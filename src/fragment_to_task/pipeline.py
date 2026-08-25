from __future__ import annotations

import collections
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .config import ProjectConfig
from .errors import PipelineError
from .gold import GoldSet, load_generation_gold, select_generation_examples
from .providers.base import ModelProvider
from .utils import atomic_write, chunks, json_bytes, jsonl_bytes, read_jsonl, sha256_file, sha256_text, utc_now
from .validation import (
    normalize_fragments,
    validate_candidate,
    validate_category_payload,
    validate_eligibility_payload,
    validate_generation_payload,
    validate_leakage_payload,
)

PROMPT_FILES = {
    "category": "category_v1.md",
    "eligibility": "eligibility_v1.md",
    "generation": "task_generation_v1.md",
    "leakage": "leakage_validation_v1.md",
}
SCHEMA_FILES = {
    "category": "category_output_v1.schema.json",
    "eligibility": "eligibility_output_v1.schema.json",
    "generation": "task_generation_output_v1.schema.json",
    "leakage": "leakage_output_v1.schema.json",
}
CORE_OUTPUTS = (
    "category_annotations.jsonl",
    "eligibility_annotations.jsonl",
    "task_candidates.jsonl",
    "rejected_task_candidates.jsonl",
    "fragment_results.jsonl",
    "run_summary.json",
)


def _load_resources(project_root: Path) -> dict[str, dict[str, Any]]:
    resources: dict[str, dict[str, Any]] = {}
    for stage in PROMPT_FILES:
        prompt_path = project_root / "prompts" / PROMPT_FILES[stage]
        schema_path = project_root / "schemas" / SCHEMA_FILES[stage]
        if not prompt_path.is_file() or not schema_path.is_file():
            raise PipelineError(f"missing prompt/schema resource for stage {stage}")
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PipelineError(f"invalid JSON schema {schema_path}: {exc}") from exc
        resources[stage] = {
            "instruction": prompt_path.read_text(encoding="utf-8"),
            "prompt_path": prompt_path,
            "schema_path": schema_path,
            "schema": schema,
        }
    return resources


def build_prompt(instruction: str, input_payload: Mapping[str, Any]) -> str:
    return (
        instruction.rstrip()
        + "\n\n<INPUT_JSON>\n"
        + json.dumps(input_payload, ensure_ascii=False, indent=2)
        + "\n</INPUT_JSON>\n"
    )


def inspect_input(input_path: Path, config: ProjectConfig, project_root: Path) -> dict[str, Any]:
    if not input_path.is_file():
        raise PipelineError(f"input JSONL does not exist: {input_path}")
    try:
        fragments = normalize_fragments(read_jsonl(input_path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PipelineError(f"could not read input JSONL: {exc}") from exc
    resources = _load_resources(project_root)
    gold = load_generation_gold(project_root, config.few_shot)
    return {
        "mode": "dry-run",
        "status": "PASS",
        "input_path": str(input_path.resolve()),
        "input_sha256": sha256_file(input_path),
        "fragments": len(fragments),
        "provider": config.provider.kind,
        "model": config.provider.model,
        "candidates_per_fragment": config.run.candidates_per_fragment,
        "include_borderline": config.run.include_borderline,
        "known_planned_calls": {
            "category": math.ceil(len(fragments) / config.run.batch_size),
            "eligibility": math.ceil(len(fragments) / config.run.batch_size),
        },
        "model_dependent_calls": ["generation", "leakage"],
        "few_shot": {
            **gold.summary(project_root),
            "examples_per_batch": config.few_shot.examples_per_batch,
        },
        "prompt_sha256": {
            stage: sha256_file(value["prompt_path"]) for stage, value in resources.items()
        },
        "schema_sha256": {
            stage: sha256_file(value["schema_path"]) for stage, value in resources.items()
        },
        "writes": 0,
        "llm_calls": 0,
    }


def _call_batch(
    *,
    provider: ModelProvider,
    stage: str,
    batch_number: int,
    instruction: str,
    schema_path: Path,
    input_payload: Mapping[str, Any],
    output_dir: Path,
    retries: int,
    validator: Callable[[Mapping[str, Any]], list[dict[str, Any]]],
    call_log: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prompt = build_prompt(instruction, input_payload)
    batch_dir = output_dir / "batches"
    prefix = f"{stage}_{batch_number:04d}"
    atomic_write(batch_dir / f"{prefix}.input.json", json_bytes(input_payload))
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            response = provider.generate(
                stage=stage,
                prompt=prompt,
                schema_path=schema_path,
                work_dir=batch_dir,
            )
            validated = validator(response.payload)
            atomic_write(batch_dir / f"{prefix}.output.json", json_bytes(response.payload))
            call_log.append(
                {
                    "stage": stage,
                    "batch_number": batch_number,
                    "attempt": attempt,
                    "provider": provider.name,
                    "return_code": response.return_code,
                    "output_sha256": sha256_text(json.dumps(response.payload, ensure_ascii=False, sort_keys=True)),
                    "status": "passed",
                }
            )
            return validated
        except PipelineError as exc:
            last_error = str(exc)
            call_log.append(
                {
                    "stage": stage,
                    "batch_number": batch_number,
                    "attempt": attempt,
                    "provider": provider.name,
                    "status": "failed",
                    "error": last_error[-2000:],
                }
            )
    raise PipelineError(f"{stage} batch {batch_number} failed after {retries} attempts: {last_error}")


def _category_stage(
    fragments: list[dict[str, Any]], resources: Mapping[str, Any], provider: ModelProvider,
    config: ProjectConfig, output_dir: Path, call_log: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for number, batch in enumerate(chunks(fragments, config.run.batch_size), start=1):
        payload = {
            "schema": "fragment_category_input_batch_v1",
            "prompt_version": "category_v1",
            "fragments": [{"fragment_id": row["fragment_id"], "text": row["text"]} for row in batch],
        }
        ids = [row["fragment_id"] for row in batch]
        results.extend(
            _call_batch(
                provider=provider, stage="category", batch_number=number,
                instruction=resources["category"]["instruction"], schema_path=resources["category"]["schema_path"],
                input_payload=payload, output_dir=output_dir, retries=config.run.transport_retries,
                validator=lambda value, ids=ids: validate_category_payload(value, ids), call_log=call_log,
            )
        )
    return results


def _eligibility_stage(
    fragments: list[dict[str, Any]], categories: list[dict[str, Any]], resources: Mapping[str, Any],
    provider: ModelProvider, config: ProjectConfig, output_dir: Path, call_log: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    category_map = {row["fragment_id"]: row for row in categories}
    results: list[dict[str, Any]] = []
    for number, batch in enumerate(chunks(fragments, config.run.batch_size), start=1):
        payload = {
            "schema": "fragment_task_eligibility_input_batch_v1",
            "prompt_version": "eligibility_v1",
            "fragments": [
                {
                    "fragment_id": row["fragment_id"],
                    "text": row["text"],
                    "primary_category": category_map[row["fragment_id"]]["primary_category"],
                    "secondary_category": category_map[row["fragment_id"]]["secondary_category"],
                }
                for row in batch
            ],
        }
        ids = [row["fragment_id"] for row in batch]
        batch_results = _call_batch(
            provider=provider, stage="eligibility", batch_number=number,
            instruction=resources["eligibility"]["instruction"], schema_path=resources["eligibility"]["schema_path"],
            input_payload=payload, output_dir=output_dir, retries=config.run.transport_retries,
            validator=lambda value, ids=ids: validate_eligibility_payload(value, ids), call_log=call_log,
        )
        for item in batch_results:
            category = category_map[item["fragment_id"]]
            if item["primary_category"] != category["primary_category"] or item["secondary_category"] != category["secondary_category"]:
                raise PipelineError(f"eligibility/category mismatch for {item['fragment_id']}")
        results.extend(batch_results)
    return results


def _generation_stage(
    fragments: list[dict[str, Any]], categories: list[dict[str, Any]], eligibility: list[dict[str, Any]],
    resources: Mapping[str, Any], provider: ModelProvider, config: ProjectConfig,
    output_dir: Path, call_log: list[dict[str, Any]], gold: GoldSet,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    category_map = {row["fragment_id"]: row for row in categories}
    eligibility_map = {row["fragment_id"]: row for row in eligibility}
    allowed = {"keep", "borderline"} if config.run.include_borderline else {"keep"}
    selected = [row for row in fragments if eligibility_map[row["fragment_id"]]["task_eligibility"] in allowed]
    outputs: list[dict[str, Any]] = []
    raw_fragments: list[dict[str, Any]] = []
    for number, batch in enumerate(chunks(selected, config.run.batch_size), start=1):
        payload = {
            "schema": "task_reverse_generation_input_batch_v1",
            "prompt_version": "task_generation_v1",
            "few_shot_examples": select_generation_examples(
                batch, category_map, gold.rows,
                config.few_shot.examples_per_batch,
            ),
            "fragments": [
                {
                    "fragment_id": row["fragment_id"],
                    "fragment_text": row["text"],
                    "primary_category": category_map[row["fragment_id"]]["primary_category"],
                    "secondary_category": category_map[row["fragment_id"]]["secondary_category"],
                    "source_task_eligibility": eligibility_map[row["fragment_id"]]["task_eligibility"],
                    "requested_candidate_indexes": list(range(1, config.run.candidates_per_fragment + 1)),
                }
                for row in batch
            ],
        }
        ids = [row["fragment_id"] for row in batch]
        batch_outputs = _call_batch(
            provider=provider, stage="generation", batch_number=number,
            instruction=resources["generation"]["instruction"], schema_path=resources["generation"]["schema_path"],
            input_payload=payload, output_dir=output_dir, retries=config.run.transport_retries,
            validator=lambda value, ids=ids: validate_generation_payload(value, ids, config.run.candidates_per_fragment),
            call_log=call_log,
        )
        raw_fragments.extend(batch_outputs)
    raw_map = {row["fragment_id"]: row for row in raw_fragments}
    seen_prompt_hashes: set[str] = set()
    fragment_map = {row["fragment_id"]: row for row in fragments}
    for row in selected:
        fragment_id = row["fragment_id"]
        category = category_map[fragment_id]["primary_category"]
        siblings: list[dict[str, Any]] = []
        for candidate in raw_map[fragment_id]["task_candidates"]:
            validation = validate_candidate(
                fragment_map[fragment_id], category, candidate,
                min_words=config.run.min_task_words, max_words=config.run.max_task_words,
                prior_candidates=siblings,
            )
            if validation["normalized_task_prompt_sha256"] in seen_prompt_hashes:
                validation["valid"] = False
                validation["reason_codes"] = sorted(set(validation["reason_codes"] + ["exact_task_prompt_duplicate_global"]))
            if validation["valid"]:
                siblings.append(candidate)
                seen_prompt_hashes.add(validation["normalized_task_prompt_sha256"])
            outputs.append(
                {
                    "fragment_id": fragment_id,
                    "candidate": candidate,
                    "deterministic_validation": validation,
                }
            )
    return outputs, raw_fragments


def _leakage_stage(
    fragments: list[dict[str, Any]], proposals: list[dict[str, Any]], resources: Mapping[str, Any],
    provider: ModelProvider, config: ProjectConfig, output_dir: Path, call_log: list[dict[str, Any]],
) -> None:
    fragment_map = {row["fragment_id"]: row for row in fragments}
    valid = [row for row in proposals if row["deterministic_validation"]["valid"]]
    for number, batch in enumerate(chunks(valid, config.run.leakage_batch_size), start=1):
        items = []
        expected: list[tuple[int, int]] = []
        for batch_index, proposal in enumerate(batch, start=1):
            candidate = proposal["candidate"]
            items.append(
                {
                    "batch_item_index": batch_index,
                    "candidate_index": candidate["candidate_index"],
                    "fragment_text": fragment_map[proposal["fragment_id"]]["text"],
                    "task_prompt": candidate["task_prompt"],
                    "answer_core_to_withhold": candidate["answer_core_to_withhold"],
                }
            )
            expected.append((batch_index, candidate["candidate_index"]))
        payload = {
            "schema": "task_leakage_validation_input_batch_v1",
            "prompt_version": "leakage_validation_v1",
            "items": items,
        }
        validations = _call_batch(
            provider=provider, stage="leakage", batch_number=number,
            instruction=resources["leakage"]["instruction"], schema_path=resources["leakage"]["schema_path"],
            input_payload=payload, output_dir=output_dir, retries=config.run.transport_retries,
            validator=lambda value, expected=expected: validate_leakage_payload(value, expected), call_log=call_log,
        )
        for proposal, validation in zip(batch, validations):
            proposal["semantic_leakage_validation"] = validation
    for proposal in proposals:
        if not proposal["deterministic_validation"]["valid"]:
            proposal["semantic_leakage_validation"] = {
                "validation": "not_run",
                "failure_types": [],
                "short_reason": "Deterministic validation failed before semantic leakage review.",
            }


def _build_outputs(
    fragments: list[dict[str, Any]], categories: list[dict[str, Any]], eligibility: list[dict[str, Any]],
    proposals: list[dict[str, Any]], raw_generation: list[dict[str, Any]], config: ProjectConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    category_map = {row["fragment_id"]: row for row in categories}
    eligibility_map = {row["fragment_id"]: row for row in eligibility}
    raw_map = {row["fragment_id"]: row for row in raw_generation}
    fragment_map = {row["fragment_id"]: row for row in fragments}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    by_fragment: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for proposal in proposals:
        fragment_id, candidate = proposal["fragment_id"], proposal["candidate"]
        leakage = proposal["semantic_leakage_validation"]
        is_valid = proposal["deterministic_validation"]["valid"] and leakage["validation"] == "pass"
        task_hash = proposal["deterministic_validation"]["task_prompt_sha256"]
        base = {
            "task_candidate_id": f"taskcand_{fragment_id}_{candidate['candidate_index']}_{task_hash[:12]}",
            "fragment_id": fragment_id,
            "fragment_text_sha256": fragment_map[fragment_id]["text_sha256"],
            "metadata": fragment_map[fragment_id]["metadata"],
            "primary_category": category_map[fragment_id]["primary_category"],
            "secondary_category": category_map[fragment_id]["secondary_category"],
            "source_task_eligibility": eligibility_map[fragment_id]["task_eligibility"],
            **candidate,
            "deterministic_validation": proposal["deterministic_validation"],
            "semantic_leakage_validation": leakage,
        }
        (accepted if is_valid else rejected).append(base)
        by_fragment[fragment_id].append(base)
    allowed = {"keep", "borderline"} if config.run.include_borderline else {"keep"}
    results: list[dict[str, Any]] = []
    for fragment in fragments:
        fragment_id = fragment["fragment_id"]
        decision = eligibility_map[fragment_id]["task_eligibility"]
        if decision not in allowed:
            status = "excluded"
        else:
            count = sum(
                row["deterministic_validation"]["valid"] and row["semantic_leakage_validation"]["validation"] == "pass"
                for row in by_fragment[fragment_id]
            )
            status = "generated" if count == config.run.candidates_per_fragment else "partial" if count else "failed"
        results.append(
            {
                "fragment_id": fragment_id,
                "task_eligibility": decision,
                "generation_status": status,
                "accepted_candidate_ids": [
                    row["task_candidate_id"]
                    for row in by_fragment[fragment_id]
                    if row["deterministic_validation"]["valid"] and row["semantic_leakage_validation"]["validation"] == "pass"
                ],
                "model_failure_reasons": raw_map.get(fragment_id, {}).get("failure_reasons", []),
            }
        )
    return accepted, rejected, results


def _write_manifest(output_dir: Path) -> dict[str, Any]:
    files = []
    for path in sorted(path for path in output_dir.rglob("*") if path.is_file() and path.name not in {"output_manifest.json", "manifest.sha256"}):
        files.append({"path": path.relative_to(output_dir).as_posix(), "size": path.stat().st_size, "sha256": sha256_file(path)})
    manifest = {"schema": "fragment_to_task_output_manifest_v1", "files": files}
    atomic_write(output_dir / "output_manifest.json", json_bytes(manifest))
    checksum_paths = [output_dir / item["path"] for item in files] + [output_dir / "output_manifest.json"]
    lines = [f"{sha256_file(path)}  {path.relative_to(output_dir).as_posix()}" for path in checksum_paths]
    atomic_write(output_dir / "manifest.sha256", ("\n".join(lines) + "\n").encode("utf-8"))
    for item in manifest["files"]:
        path = output_dir / item["path"]
        if path.stat().st_size != item["size"] or sha256_file(path) != item["sha256"]:
            raise PipelineError(f"post-write validation failed: {item['path']}")
    return manifest


def run_pipeline(
    *, input_path: Path, output_dir: Path, project_root: Path,
    config: ProjectConfig, provider: ModelProvider,
) -> dict[str, Any]:
    if output_dir.exists():
        raise PipelineError(f"refusing to overwrite existing output directory: {output_dir}")
    if not input_path.is_file():
        raise PipelineError(f"input JSONL does not exist: {input_path}")
    input_hash_before = sha256_file(input_path)
    try:
        fragments = normalize_fragments(read_jsonl(input_path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PipelineError(f"could not read input JSONL: {exc}") from exc
    resources = _load_resources(project_root)
    gold = load_generation_gold(project_root, config.few_shot)
    output_dir.mkdir(parents=True)
    call_log: list[dict[str, Any]] = []
    started = utc_now()
    categories = _category_stage(fragments, resources, provider, config, output_dir, call_log)
    atomic_write(output_dir / "category_annotations.jsonl", jsonl_bytes(categories))
    eligibility = _eligibility_stage(fragments, categories, resources, provider, config, output_dir, call_log)
    atomic_write(output_dir / "eligibility_annotations.jsonl", jsonl_bytes(eligibility))
    proposals, raw_generation = _generation_stage(
        fragments, categories, eligibility, resources, provider, config, output_dir, call_log, gold,
    )
    _leakage_stage(fragments, proposals, resources, provider, config, output_dir, call_log)
    accepted, rejected, results = _build_outputs(
        fragments, categories, eligibility, proposals, raw_generation, config,
    )
    atomic_write(output_dir / "task_candidates.jsonl", jsonl_bytes(accepted))
    atomic_write(output_dir / "rejected_task_candidates.jsonl", jsonl_bytes(rejected))
    atomic_write(output_dir / "fragment_results.jsonl", jsonl_bytes(results))
    atomic_write(output_dir / "model_calls.jsonl", jsonl_bytes(call_log))
    input_hash_after = sha256_file(input_path)
    if input_hash_after != input_hash_before:
        raise PipelineError("input file changed during the run")
    summary = {
        "schema": "fragment_to_task_run_summary_v1",
        "tool_version": "0.1.0",
        "started_at_utc": started,
        "completed_at_utc": utc_now(),
        "status": "PASS",
        "provider": provider.name,
        "model": config.provider.model,
        "few_shot": {
            **gold.summary(project_root),
            "examples_per_batch": config.few_shot.examples_per_batch,
            "selection_rule": "batch_primary_category_then_variant_diversity_then_example_id",
            "stage": "generation_only",
        },
        "input": {
            "path": str(input_path.resolve()),
            "sha256_before": input_hash_before,
            "sha256_after": input_hash_after,
            "unchanged": True,
            "fragments": len(fragments),
        },
        "counts": {
            "category_annotations": len(categories),
            "eligibility_annotations": len(eligibility),
            "eligible_fragments": sum(row["generation_status"] != "excluded" for row in results),
            "accepted_task_candidates": len(accepted),
            "rejected_task_candidates": len(rejected),
            "model_calls": len(call_log),
        },
        "eligibility_distribution": dict(sorted(collections.Counter(row["task_eligibility"] for row in eligibility).items())),
        "generation_status_distribution": dict(sorted(collections.Counter(row["generation_status"] for row in results).items())),
        "config": {
            "batch_size": config.run.batch_size,
            "leakage_batch_size": config.run.leakage_batch_size,
            "candidates_per_fragment": config.run.candidates_per_fragment,
            "include_borderline": config.run.include_borderline,
            "min_task_words": config.run.min_task_words,
            "max_task_words": config.run.max_task_words,
        },
        "prompt_sha256": {stage: sha256_file(value["prompt_path"]) for stage, value in resources.items()},
        "schema_sha256": {stage: sha256_file(value["schema_path"]) for stage, value in resources.items()},
    }
    atomic_write(output_dir / "run_summary.json", json_bytes(summary))
    manifest = _write_manifest(output_dir)
    missing = [name for name in CORE_OUTPUTS if not (output_dir / name).is_file()]
    if missing:
        raise PipelineError(f"post-write validation missing outputs: {missing}")
    summary["output_manifest_files"] = len(manifest["files"])
    return summary

