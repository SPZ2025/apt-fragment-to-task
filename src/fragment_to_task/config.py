from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import PipelineError


@dataclass(frozen=True)
class RunConfig:
    batch_size: int = 6
    leakage_batch_size: int = 18
    candidates_per_fragment: int = 3
    include_borderline: bool = True
    transport_retries: int = 2
    timeout_seconds: int = 900
    min_task_words: int = 20
    max_task_words: int = 180


@dataclass(frozen=True)
class ProviderConfig:
    kind: str = "codex-cli"
    executable: str = "codex"
    model: str | None = None
    command: tuple[str, ...] = ()
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class FewShotConfig:
    generation_enabled: bool = False
    generation_file: Path | None = None
    examples_per_batch: int = 4


@dataclass(frozen=True)
class ProjectConfig:
    run: RunConfig
    provider: ProviderConfig
    few_shot: FewShotConfig = FewShotConfig()


def _integer(table: dict[str, Any], key: str, default: int, minimum: int = 1) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PipelineError(f"config run.{key} must be an integer >= {minimum}")
    return value


def load_config(path: Path) -> ProjectConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PipelineError(f"Could not load config {path}: {exc}") from exc
    run_raw = raw.get("run", {})
    provider_raw = raw.get("provider", {})
    few_shot_raw = raw.get("few_shot", {})
    generation_raw = few_shot_raw.get("generation", {}) if isinstance(few_shot_raw, dict) else None
    if not all(isinstance(table, dict) for table in (run_raw, provider_raw, few_shot_raw, generation_raw)):
        raise PipelineError(
            "config [run], [provider], and [few_shot.generation] must be TOML tables"
        )
    candidates = _integer(run_raw, "candidates_per_fragment", 3)
    if candidates > 3:
        raise PipelineError("config run.candidates_per_fragment must be 1--3")
    minimum = _integer(run_raw, "min_task_words", 20)
    maximum = _integer(run_raw, "max_task_words", 180)
    if minimum > maximum:
        raise PipelineError("config min_task_words cannot exceed max_task_words")
    include_borderline = run_raw.get("include_borderline", True)
    if not isinstance(include_borderline, bool):
        raise PipelineError("config run.include_borderline must be a boolean")
    run = RunConfig(
        batch_size=_integer(run_raw, "batch_size", 6),
        leakage_batch_size=_integer(run_raw, "leakage_batch_size", 18),
        candidates_per_fragment=candidates,
        include_borderline=include_borderline,
        transport_retries=_integer(run_raw, "transport_retries", 2),
        timeout_seconds=_integer(run_raw, "timeout_seconds", 900),
        min_task_words=minimum,
        max_task_words=maximum,
    )
    kind = provider_raw.get("kind", "codex-cli")
    if kind not in {"codex-cli", "command"}:
        raise PipelineError("config provider.kind must be 'codex-cli' or 'command'")
    command = provider_raw.get("command", [])
    extra_args = provider_raw.get("extra_args", [])
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise PipelineError("config provider.command must be an array of strings")
    if not isinstance(extra_args, list) or not all(isinstance(item, str) for item in extra_args):
        raise PipelineError("config provider.extra_args must be an array of strings")
    if kind == "command" and not command:
        raise PipelineError("command provider requires provider.command")
    model = provider_raw.get("model")
    if model is not None and not isinstance(model, str):
        raise PipelineError("config provider.model must be a string")
    provider = ProviderConfig(
        kind=kind,
        executable=str(provider_raw.get("executable", "codex")),
        model=model or None,
        command=tuple(command),
        extra_args=tuple(extra_args),
    )
    generation_enabled = generation_raw.get("enabled", False)
    if not isinstance(generation_enabled, bool):
        raise PipelineError("config few_shot.generation.enabled must be a boolean")
    generation_file_raw = generation_raw.get("file")
    if generation_file_raw is not None and not isinstance(generation_file_raw, str):
        raise PipelineError("config few_shot.generation.file must be a string")
    examples_per_batch = _integer(generation_raw, "examples_per_batch", 4)
    if examples_per_batch > 6:
        raise PipelineError(
            "config few_shot.generation.examples_per_batch must be 1--6"
        )
    few_shot = FewShotConfig(
        generation_enabled=generation_enabled,
        generation_file=Path(generation_file_raw) if generation_file_raw else None,
        examples_per_batch=examples_per_batch,
    )
    return ProjectConfig(run=run, provider=provider, few_shot=few_shot)

