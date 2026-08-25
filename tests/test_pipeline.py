from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fragment_to_task.config import ProjectConfig, ProviderConfig, RunConfig, load_config
from fragment_to_task.errors import PipelineError
from fragment_to_task.pipeline import inspect_input, run_pipeline
from fragment_to_task.providers.base import ProviderResponse
from fragment_to_task.providers.command import CommandProvider
from fragment_to_task.utils import sha256_file, sha256_text
from fragment_to_task.validation import normalize_fragments, validate_candidate


def prompt_payload(prompt: str) -> dict:
    return json.loads(prompt.split("<INPUT_JSON>\n", 1)[1].split("\n</INPUT_JSON>", 1)[0])


class FakeProvider:
    name = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, *, stage: str, prompt: str, schema_path: Path, work_dir: Path) -> ProviderResponse:
        self.calls.append(stage)
        data = prompt_payload(prompt)
        if stage == "category":
            payload = {
                "schema": "fragment_category_annotations_batch_v1",
                "prompt_version": "category_v1",
                "annotations": [
                    {
                        "fragment_id": row["fragment_id"], "primary_category": "Method",
                        "secondary_category": None, "mixed_category": False,
                        "confidence": 0.9, "short_reason": "The fragment explains a mechanism."
                    }
                    for row in data["fragments"]
                ],
            }
        elif stage == "eligibility":
            payload = {
                "schema": "fragment_task_eligibility_batch_v1",
                "prompt_version": "eligibility_v1",
                "annotations": [
                    {
                        "fragment_id": row["fragment_id"], "primary_category": "Method",
                        "secondary_category": None, "task_eligibility": "keep",
                        "exclusion_reason": None, "borderline_reason": None,
                        "confidence": 0.9,
                        "taskability_dimensions": {
                            "problem_reconstructable": True, "answer_leakage_avoidable": True,
                            "requires_reasoning": True, "self_contained_task_feasible": True,
                            "persona_discrimination_potential": True,
                        },
                        "latent_task_operation": "design", "short_reason": "Supports a design task."
                    }
                    for row in data["fragments"]
                ],
            }
        elif stage == "generation":
            prompts = [
                "Design a coordination protocol for a replicated service that must preserve committed operations during leader changes. State the information participants retain, the safety invariants, and how recovery proceeds after communication stabilizes.",
                "Diagnose a replicated service that returns inconsistent histories after a network partition. Identify the evidence you would collect, reason about the likely protocol failure, and propose targeted tests that distinguish competing causes.",
                "Compare two recovery strategies for a replicated service operating under intermittent communication. Establish evaluation criteria for safety and progress, analyze their tradeoffs, and recommend one strategy under clearly stated operating assumptions.",
            ]
            operations = ["design", "diagnose", "compare"]
            payload = {"schema": "task_reverse_generation_batch_v1", "fragments": []}
            for row in data["fragments"]:
                candidates = []
                for index in row["requested_candidate_indexes"]:
                    candidates.append(
                        {
                            "candidate_index": index,
                            "task_variant_mode": ["constraint_design", "failure_diagnosis", "tradeoff_comparison"][index - 1],
                            "task_spec": {
                                "structure_type": "Method",
                                "fields": [{"name": "problem", "items": ["Replicated service coordination"]}],
                                "requested_operation": operations[index - 1],
                            },
                            "answer_core_to_withhold": ["A quorum intersection preserves every committed operation across coordinator changes."],
                            "task_prompt": prompts[index - 1],
                            "generation_checks": {
                                "self_contained": True, "source_agnostic": True,
                                "literal_leakage": False, "paraphrase_leakage": False,
                                "causal_leakage": False, "distinct_from_other_candidates": True,
                            },
                            "generation_confidence": 0.9,
                            "short_note": "Distinct reasoning frame.",
                        }
                    )
                payload["fragments"].append(
                    {"fragment_id": row["fragment_id"], "task_candidates": candidates, "failure_reasons": []}
                )
        elif stage == "leakage":
            payload = {
                "schema": "task_leakage_validation_batch_v1",
                "validations": [
                    {
                        "batch_item_index": row["batch_item_index"],
                        "candidate_index": row["candidate_index"],
                        "validation": "pass", "failure_types": [],
                        "short_reason": "The central mechanism remains to be derived."
                    }
                    for row in data["items"]
                ],
            }
        else:  # pragma: no cover
            raise AssertionError(stage)
        return ProviderResponse(payload=payload, return_code=0, stdout="", stderr="")


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ProjectConfig(
            run=RunConfig(batch_size=2, leakage_batch_size=6, candidates_per_fragment=3),
            provider=ProviderConfig(kind="command", command=("unused",)),
        )

    def test_default_config_loads(self) -> None:
        config = load_config(PROJECT_ROOT / "configs" / "default.toml")
        self.assertEqual(config.provider.kind, "codex-cli")
        self.assertEqual(config.run.candidates_per_fragment, 3)

    def test_normalize_accepts_cs_aliases_and_preserves_metadata(self) -> None:
        rows = [{"final_fragment_id": "f1", "text": "Exact text.", "scholar_id": "s1"}]
        result = normalize_fragments(rows)
        self.assertEqual(result[0]["fragment_id"], "f1")
        self.assertEqual(result[0]["metadata"], {"scholar_id": "s1"})

    def test_normalize_rejects_hash_mismatch(self) -> None:
        with self.assertRaises(PipelineError):
            normalize_fragments([{"fragment_id": "f1", "text": "Exact text.", "text_sha256": "0" * 64}])

    def test_candidate_detects_literal_leakage(self) -> None:
        candidate = {
            "candidate_index": 1,
            "task_variant_mode": "design",
            "task_spec": {
                "structure_type": "Method",
                "fields": [{"name": "problem", "items": ["system"]}],
                "requested_operation": "design",
            },
            "answer_core_to_withhold": ["majority intersection preserves every previously committed operation"],
            "task_prompt": "Design a robust system where majority intersection preserves every previously committed operation while nodes change leadership and communication remains unreliable for extended periods.",
            "generation_checks": {
                "self_contained": True, "source_agnostic": True, "literal_leakage": False,
                "paraphrase_leakage": False, "causal_leakage": False,
                "distinct_from_other_candidates": True,
            },
            "generation_confidence": 0.8,
            "short_note": "test",
        }
        result = validate_candidate(
            {"identity_markers": []}, "Method", candidate, min_words=5, max_words=180
        )
        self.assertIn("literal_leakage", result["reason_codes"])

    def test_dry_run_is_zero_write_and_zero_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "fragments.jsonl"
            input_path.write_text('{"fragment_id":"f1","text":"A sufficiently substantive frozen fragment for testing."}\n', encoding="utf-8")
            before = set(Path(temporary).iterdir())
            result = inspect_input(input_path, self.config, PROJECT_ROOT)
            self.assertEqual(result["writes"], 0)
            self.assertEqual(result["llm_calls"], 0)
            self.assertEqual(before, set(Path(temporary).iterdir()))

    def test_apply_pipeline_writes_valid_outputs_and_preserves_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            input_path = temporary_path / "fragments.jsonl"
            input_path.write_text(
                json.dumps({"fragment_id": "f1", "text": "A coordination mechanism records terms, reconciles histories, and commits operations after replication."}) + "\n",
                encoding="utf-8",
            )
            before = sha256_file(input_path)
            provider = FakeProvider()
            output_dir = temporary_path / "run"
            result = run_pipeline(
                input_path=input_path, output_dir=output_dir, project_root=PROJECT_ROOT,
                config=self.config, provider=provider,
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["counts"]["accepted_task_candidates"], 3)
            self.assertEqual(provider.calls, ["category", "eligibility", "generation", "leakage"])
            self.assertEqual(before, sha256_file(input_path))
            for name in (
                "category_annotations.jsonl", "eligibility_annotations.jsonl",
                "task_candidates.jsonl", "fragment_results.jsonl", "run_summary.json",
                "output_manifest.json", "manifest.sha256",
            ):
                self.assertTrue((output_dir / name).is_file(), name)

    def test_apply_refuses_existing_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "fragments.jsonl"
            input_path.write_text('{"fragment_id":"f1","text":"Frozen text."}\n', encoding="utf-8")
            output_dir = root / "existing"
            output_dir.mkdir()
            with self.assertRaises(PipelineError):
                run_pipeline(
                    input_path=input_path, output_dir=output_dir, project_root=PROJECT_ROOT,
                    config=self.config, provider=FakeProvider(),
                )

    def test_command_provider_uses_stdin_and_output_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "adapter.py"
            script.write_text(
                "import json, pathlib, sys\n"
                "prompt = sys.stdin.read()\n"
                "pathlib.Path(sys.argv[1]).write_text(json.dumps({'received': bool(prompt)}), encoding='utf-8')\n",
                encoding="utf-8",
            )
            provider = CommandProvider(
                command=(sys.executable, str(script), "{output_file}"),
                timeout_seconds=30,
                project_root=root,
            )
            response = provider.generate(
                stage="category", prompt="hello", schema_path=PROJECT_ROOT / "schemas" / "category_output_v1.schema.json", work_dir=root,
            )
            self.assertEqual(response.payload, {"received": True})

    def test_sha256_is_stable(self) -> None:
        self.assertEqual(sha256_text("same"), sha256_text("same"))


if __name__ == "__main__":
    unittest.main()

