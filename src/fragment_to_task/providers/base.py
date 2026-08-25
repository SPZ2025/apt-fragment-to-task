from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderResponse:
    payload: dict[str, Any]
    return_code: int
    stdout: str
    stderr: str


class ModelProvider(Protocol):
    name: str

    def generate(
        self,
        *,
        stage: str,
        prompt: str,
        schema_path: Path,
        work_dir: Path,
    ) -> ProviderResponse: ...

