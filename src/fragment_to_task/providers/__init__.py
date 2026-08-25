from __future__ import annotations

from pathlib import Path

from ..config import ProjectConfig
from .base import ModelProvider
from .codex_cli import CodexCliProvider
from .command import CommandProvider


def create_provider(config: ProjectConfig, project_root: Path) -> ModelProvider:
    if config.provider.kind == "codex-cli":
        return CodexCliProvider(
            executable=config.provider.executable,
            model=config.provider.model,
            extra_args=config.provider.extra_args,
            timeout_seconds=config.run.timeout_seconds,
            project_root=project_root,
        )
    return CommandProvider(
        command=config.provider.command,
        timeout_seconds=config.run.timeout_seconds,
        project_root=project_root,
    )

