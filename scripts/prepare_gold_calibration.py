from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fragment_to_task.errors import PipelineError
from fragment_to_task.utils import atomic_write, json_bytes, jsonl_bytes, read_jsonl, sha256_file
from fragment_to_task.validation import CATEGORIES, normalize_fragments


def _category_map(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        fragment_id = row.get("fragment_id")
        primary = row.get("primary_category")
        secondary = row.get("secondary_category")
        if not isinstance(fragment_id, str) or not fragment_id:
            raise PipelineError("category annotation has invalid fragment_id")
        if fragment_id in result:
            raise PipelineError(f"duplicate category annotation: {fragment_id}")
        if primary not in CATEGORIES:
            raise PipelineError(f"{fragment_id}: invalid primary_category")
        if secondary is not None and secondary not in CATEGORIES:
            raise PipelineError(f"{fragment_id}: invalid secondary_category")
        result[fragment_id] = dict(row)
    return result


def select_rows(
    fragments: list[dict[str, Any]],
    categories: dict[str, dict[str, Any]],
    sample_size: int,
) -> list[dict[str, Any]]:
    buckets = {category: [] for category in CATEGORIES}
    for fragment in sorted(fragments, key=lambda row: row["fragment_id"]):
        annotation = categories.get(fragment["fragment_id"])
        if annotation is not None:
            buckets[annotation["primary_category"]].append(fragment)
    selected: list[dict[str, Any]] = []
    offset = 0
    while len(selected) < sample_size:
        added = False
        for category in CATEGORIES:
            if offset < len(buckets[category]):
                selected.append(buckets[category][offset])
                added = True
                if len(selected) == sample_size:
                    break
        if not added:
            break
        offset += 1
    return selected


def _draft(
    fragment: dict[str, Any], annotation: dict[str, Any], domain: str, index: int
) -> dict[str, Any]:
    return {
        "gold_example_id": f"{domain}_gold_draft_{index:03d}",
        "domain": domain,
        "source_fragment_id": fragment["fragment_id"],
        "primary_category": annotation["primary_category"],
        "secondary_category": annotation["secondary_category"],
        "task_variant_mode": "TO_REVIEW",
        "allowed_given_information": ["TO_REVIEW"],
        "answer_core_to_withhold": ["TO_REVIEW"],
        "task_prompt": "TO_REVIEW",
        "gold_status": "draft",
        "calibration_note": "Complete and independently review before approval.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministically prepare a domain gold calibration review pack."
    )
    parser.add_argument("--fragments", type=Path, required=True)
    parser.add_argument("--categories", type=Path, required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--sample-size", type=int, default=16)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        if args.sample_size < 1:
            raise PipelineError("--sample-size must be >= 1")
        if not args.domain.strip():
            raise PipelineError("--domain must be non-empty")
        if args.apply and args.output_dir is None:
            raise PipelineError("--apply requires --output-dir")
        if not args.apply and args.output_dir is not None:
            raise PipelineError("dry-run does not accept --output-dir")
        fragment_path = args.fragments.resolve()
        category_path = args.categories.resolve()
        fragments = normalize_fragments(read_jsonl(fragment_path))
        categories = _category_map(category_path)
        selected = select_rows(fragments, categories, args.sample_size)
        if not selected:
            raise PipelineError("no fragments matched category annotations")
        ids = [row["fragment_id"] for row in selected]
        summary = {
            "schema": "gold_calibration_preparation_summary_v1",
            "status": "PASS",
            "mode": "apply" if args.apply else "dry-run",
            "domain": args.domain,
            "requested_sample_size": args.sample_size,
            "selected": len(selected),
            "selected_fragment_ids": ids,
            "category_distribution": dict(
                sorted(collections.Counter(categories[value]["primary_category"] for value in ids).items())
            ),
            "input_sha256": {
                "fragments": sha256_file(fragment_path),
                "categories": sha256_file(category_path),
            },
            "writes": 3 if args.apply else 0,
            "llm_calls": 0,
        }
        if args.apply:
            output_dir = args.output_dir.resolve()
            if output_dir.exists():
                raise PipelineError(f"refusing to overwrite existing output directory: {output_dir}")
            output_dir.mkdir(parents=True)
            review_rows = [
                {
                    "fragment_id": row["fragment_id"],
                    "text": row["text"],
                    "text_sha256": row["text_sha256"],
                    "metadata": row["metadata"],
                    "primary_category": categories[row["fragment_id"]]["primary_category"],
                    "secondary_category": categories[row["fragment_id"]]["secondary_category"],
                }
                for row in selected
            ]
            drafts = [
                _draft(row, categories[row["fragment_id"]], args.domain, index)
                for index, row in enumerate(selected, start=1)
            ]
            atomic_write(output_dir / "gold_calibration_fragments.jsonl", jsonl_bytes(review_rows))
            atomic_write(output_dir / "generation_gold_drafts.jsonl", jsonl_bytes(drafts))
            atomic_write(output_dir / "preparation_summary.json", json_bytes(summary))
            for name in (
                "gold_calibration_fragments.jsonl",
                "generation_gold_drafts.jsonl",
                "preparation_summary.json",
            ):
                if not (output_dir / name).is_file():
                    raise PipelineError(f"post-write validation missing {name}")
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        print("SUMMARY=PASS")
        return 0
    except (OSError, ValueError, PipelineError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("SUMMARY=FAIL", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
