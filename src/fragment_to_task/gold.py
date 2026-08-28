from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import FewShotConfig
from .errors import PipelineError
from .utils import read_jsonl, sha256_file
from .validation import (
    CATEGORIES,
    SOURCE_REFERENCE_RE,
    answer_core_literal_leakage,
    count_cjk_characters,
    count_english_words,
    detect_text_language,
)


@dataclass(frozen=True)
class GoldSet:
    enabled: bool
    path: Path | None
    sha256: str | None
    rows: tuple[dict[str, Any], ...]

    def summary(self, project_root: Path) -> dict[str, Any]:
        relative_path: str | None = None
        if self.path is not None:
            try:
                relative_path = self.path.relative_to(project_root).as_posix()
            except ValueError:
                relative_path = str(self.path)
        return {
            "enabled": self.enabled,
            "file": relative_path,
            "sha256": self.sha256,
            "examples_available": len(self.rows),
        }


REQUIRED_GOLD_FIELDS = {
    "gold_example_id",
    "domain",
    "primary_category",
    "secondary_category",
    "task_variant_mode",
    "allowed_given_information",
    "answer_core_to_withhold",
    "task_prompt",
    "gold_status",
}


def _nonempty_strings(value: Any, label: str, example_id: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise PipelineError(f"{example_id}: {label} must be a non-empty string array")
    return list(value)


def validate_gold_rows(
    rows: Sequence[Mapping[str, Any]], *, require_approved: bool = True
) -> list[dict[str, Any]]:
    if not rows:
        raise PipelineError("gold set contains no examples")
    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for position, row in enumerate(rows, start=1):
        missing = REQUIRED_GOLD_FIELDS - set(row)
        if missing:
            raise PipelineError(
                f"gold row {position}: missing fields {sorted(missing)}"
            )
        example_id = row["gold_example_id"]
        if not isinstance(example_id, str) or not example_id.strip():
            raise PipelineError(f"gold row {position}: invalid gold_example_id")
        if example_id in seen_ids:
            raise PipelineError(f"duplicate gold_example_id: {example_id}")
        seen_ids.add(example_id)
        domain = row["domain"]
        if not isinstance(domain, str) or not domain.strip():
            raise PipelineError(f"{example_id}: domain must be non-empty")
        primary = row["primary_category"]
        secondary = row["secondary_category"]
        if primary not in CATEGORIES:
            raise PipelineError(f"{example_id}: invalid primary_category")
        if secondary is not None and secondary not in CATEGORIES:
            raise PipelineError(f"{example_id}: invalid secondary_category")
        if secondary == primary:
            raise PipelineError(f"{example_id}: secondary category duplicates primary")
        mode = row["task_variant_mode"]
        if not isinstance(mode, str) or not mode.strip():
            raise PipelineError(f"{example_id}: task_variant_mode must be non-empty")
        _nonempty_strings(
            row["allowed_given_information"], "allowed_given_information", example_id
        )
        withheld = _nonempty_strings(
            row["answer_core_to_withhold"], "answer_core_to_withhold", example_id
        )
        task_prompt = row["task_prompt"]
        if not isinstance(task_prompt, str) or not task_prompt.strip():
            raise PipelineError(f"{example_id}: task_prompt must be non-empty")
        if detect_text_language(task_prompt) == "zh":
            if count_cjk_characters(task_prompt) > 320:
                raise PipelineError(
                    f"{example_id}: task_prompt exceeds 320 Chinese characters"
                )
        elif count_english_words(task_prompt) > 180:
            raise PipelineError(f"{example_id}: task_prompt exceeds 180 English words")
        if SOURCE_REFERENCE_RE.search(task_prompt):
            raise PipelineError(f"{example_id}: task_prompt is source-dependent")
        if answer_core_literal_leakage(task_prompt, withheld):
            raise PipelineError(f"{example_id}: task_prompt literally leaks withheld core")
        status = row["gold_status"]
        if status not in {"draft", "approved", "rejected"}:
            raise PipelineError(f"{example_id}: invalid gold_status")
        if require_approved and status != "approved":
            raise PipelineError(f"{example_id}: only approved gold may be used for generation")
        validated.append(dict(row))
    return validated


def load_generation_gold(project_root: Path, config: FewShotConfig) -> GoldSet:
    if not config.generation_enabled:
        return GoldSet(enabled=False, path=None, sha256=None, rows=())
    if config.generation_file is None:
        raise PipelineError("generation few-shot is enabled but no file is configured")
    path = config.generation_file
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    if not path.is_file():
        raise PipelineError(f"generation gold file does not exist: {path}")
    try:
        rows = validate_gold_rows(read_jsonl(path), require_approved=True)
    except (OSError, ValueError) as exc:
        raise PipelineError(f"could not load generation gold: {exc}") from exc
    return GoldSet(
        enabled=True,
        path=path,
        sha256=sha256_file(path),
        rows=tuple(rows),
    )


def prompt_view(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "example_id": row["gold_example_id"],
        "primary_category": row["primary_category"],
        "secondary_category": row["secondary_category"],
        "task_variant_mode": row["task_variant_mode"],
        "allowed_given_information": list(row["allowed_given_information"]),
        "answer_core_to_withhold": list(row["answer_core_to_withhold"]),
        "task_prompt": row["task_prompt"],
    }


def select_generation_examples(
    batch_rows: Sequence[Mapping[str, Any]],
    category_map: Mapping[str, Mapping[str, Any]],
    gold_rows: Sequence[Mapping[str, Any]],
    count: int,
) -> list[dict[str, Any]]:
    if not gold_rows or count <= 0:
        return []
    ordered_categories: list[str] = []
    for row in batch_rows:
        category = str(category_map[str(row["fragment_id"])]["primary_category"])
        if category not in ordered_categories:
            ordered_categories.append(category)

    ordered_gold = sorted(gold_rows, key=lambda row: str(row["gold_example_id"]))
    selected: list[Mapping[str, Any]] = []
    used_ids: set[str] = set()
    used_modes: set[str] = set()

    def add(row: Mapping[str, Any]) -> None:
        selected.append(row)
        used_ids.add(str(row["gold_example_id"]))
        used_modes.add(str(row["task_variant_mode"]))

    for category in ordered_categories:
        match = next(
            (
                row
                for row in ordered_gold
                if row["primary_category"] == category
                and str(row["gold_example_id"]) not in used_ids
            ),
            None,
        )
        if match is not None and len(selected) < count:
            add(match)

    while len(selected) < min(count, len(ordered_gold)):
        remaining = [
            row
            for row in ordered_gold
            if str(row["gold_example_id"]) not in used_ids
        ]
        if not remaining:
            break
        remaining.sort(
            key=lambda row: (
                0 if row["primary_category"] in ordered_categories else 1,
                0 if str(row["task_variant_mode"]) not in used_modes else 1,
                str(row["gold_example_id"]),
            )
        )
        add(remaining[0])

    return [prompt_view(row) for row in selected]
