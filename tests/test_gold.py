from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fragment_to_task.config import FewShotConfig, ProjectConfig, ProviderConfig, RunConfig, load_config
from fragment_to_task.gold import load_generation_gold, prompt_view, select_generation_examples
from fragment_to_task.pipeline import _generation_stage, _load_resources, inspect_input
from fragment_to_task.providers.base import ProviderResponse


def prompt_payload(prompt: str) -> dict:
    return json.loads(prompt.split("<INPUT_JSON>\n", 1)[1].split("\n</INPUT_JSON>", 1)[0])


class CapturingProvider:
    name = "capturing"

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def generate(self, *, stage: str, prompt: str, schema_path: Path, work_dir: Path) -> ProviderResponse:
        self.payloads.append(prompt_payload(prompt))
        rows = []
        for fragment in self.payloads[-1]["fragments"]:
            rows.append(
                {
                    "fragment_id": fragment["fragment_id"],
                    "task_candidates": [
                        {
                            "candidate_index": 1,
                            "task_variant_mode": "constraint_driven_design",
                            "task_spec": {
                                "structure_type": "Method",
                                "fields": [{"name": "problem", "items": ["A distributed coordination problem"]}],
                                "requested_operation": "design",
                            },
                            "answer_core_to_withhold": [
                                "A quorum intersection preserves committed operations."
                            ],
                            "task_prompt": (
                                "Design a distributed coordination protocol under intermittent communication. "
                                "State its operating assumptions, required state, safety objective, and recovery "
                                "procedure, while leaving the central preservation mechanism to be derived."
                            ),
                            "generation_checks": {
                                "self_contained": True,
                                "source_agnostic": True,
                                "literal_leakage": False,
                                "paraphrase_leakage": False,
                                "causal_leakage": False,
                            },
                            "generation_confidence": 0.9,
                            "short_note": "A distinct design task.",
                        }
                    ],
                    "failure_reasons": [],
                }
            )
        return ProviderResponse(
            payload={"schema": "task_reverse_generation_batch_v1", "fragments": rows},
            return_code=0,
            stdout="",
            stderr="",
        )


class GoldTests(unittest.TestCase):
    def test_default_and_cs_configs(self) -> None:
        default = load_config(PROJECT_ROOT / "configs" / "default.toml")
        cs = load_config(PROJECT_ROOT / "configs" / "computer_science.toml")
        self.assertEqual(default.run.candidates_per_fragment, 1)
        self.assertEqual(cs.run.candidates_per_fragment, 1)
        self.assertFalse(default.few_shot.generation_enabled)
        self.assertTrue(cs.few_shot.generation_enabled)
        self.assertEqual(cs.few_shot.examples_per_batch, 4)

    def test_cs_gold_loads_and_prompt_view_strips_provenance(self) -> None:
        config = load_config(PROJECT_ROOT / "configs" / "computer_science.toml")
        gold = load_generation_gold(PROJECT_ROOT, config.few_shot)
        self.assertEqual(len(gold.rows), 22)
        view = prompt_view(gold.rows[0])
        self.assertNotIn("source_fragment_id", view)
        self.assertNotIn("source_scholar_id", view)
        self.assertNotIn("calibration_note", view)
        self.assertEqual(view["example_id"], "gold_task_reverse_v1_001")

    def test_selection_is_deterministic_and_category_aware(self) -> None:
        config = load_config(PROJECT_ROOT / "configs" / "computer_science.toml")
        rows = load_generation_gold(PROJECT_ROOT, config.few_shot).rows
        batch = [{"fragment_id": "f1"}, {"fragment_id": "f2"}]
        categories = {
            "f1": {"primary_category": "Method"},
            "f2": {"primary_category": "Conclusion"},
        }
        first = select_generation_examples(batch, categories, rows, 4)
        second = select_generation_examples(batch, categories, rows, 4)
        self.assertEqual(first, second)
        self.assertEqual([row["primary_category"] for row in first[:2]], ["Method", "Conclusion"])
        self.assertEqual(len({row["example_id"] for row in first}), 4)

    def test_cs_dry_run_validates_gold_without_writes(self) -> None:
        config = load_config(PROJECT_ROOT / "configs" / "computer_science.toml")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "fragments.jsonl"
            input_path.write_text(
                '{"fragment_id":"f1","text":"A substantive frozen fragment used only for validation."}\n',
                encoding="utf-8",
            )
            before = set(root.iterdir())
            result = inspect_input(input_path, config, PROJECT_ROOT)
            self.assertEqual(result["few_shot"]["examples_available"], 22)
            self.assertEqual(result["writes"], 0)
            self.assertEqual(result["llm_calls"], 0)
            self.assertEqual(before, set(root.iterdir()))

    def test_generation_receives_prompt_views_only(self) -> None:
        few_shot = FewShotConfig(
            generation_enabled=True,
            generation_file=Path("goldsets/examples/computer_science/generation_gold_v1.jsonl"),
            examples_per_batch=3,
        )
        config = ProjectConfig(
            run=RunConfig(batch_size=1, candidates_per_fragment=1, min_task_words=10),
            provider=ProviderConfig(kind="command", command=("unused",)),
            few_shot=few_shot,
        )
        gold = load_generation_gold(PROJECT_ROOT, few_shot)
        fragment = {
            "fragment_id": "f1",
            "text": "A distributed protocol records decisions and coordinates recovery.",
            "text_sha256": "unused",
            "metadata": {},
            "identity_markers": [],
        }
        categories = [
            {
                "fragment_id": "f1",
                "primary_category": "Method",
                "secondary_category": None,
            }
        ]
        eligibility = [{"fragment_id": "f1", "task_eligibility": "keep"}]
        provider = CapturingProvider()
        with tempfile.TemporaryDirectory() as temporary:
            _generation_stage(
                [fragment],
                categories,
                eligibility,
                _load_resources(PROJECT_ROOT),
                provider,
                config,
                Path(temporary),
                [],
                gold,
            )
        examples = provider.payloads[0]["few_shot_examples"]
        self.assertEqual(
            provider.payloads[0]["fragments"][0]["requested_candidate_indexes"],
            [1],
        )
        self.assertEqual(len(examples), 3)
        self.assertEqual(examples[0]["primary_category"], "Method")
        self.assertNotIn("source_fragment_id", examples[0])
        self.assertNotIn("source_scholar_id", examples[0])
        self.assertEqual(provider.payloads[0]["task_language_policy"]["mode"], "auto")
        self.assertEqual(
            provider.payloads[0]["task_language_policy"]["chinese_character_range"],
            [40, 320],
        )


if __name__ == "__main__":
    unittest.main()
