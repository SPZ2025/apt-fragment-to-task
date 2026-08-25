from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..errors import PipelineError
from .base import ProviderResponse
from .process import run_process


class CodexCliProvider:
    name = "codex-cli"

    def __init__(
        self,
        *,
        executable: str = "codex",
        model: str | None = None,
        extra_args: tuple[str, ...] = (),
        timeout_seconds: int = 900,
        project_root: Path,
    ) -> None:
        self.executable = executable
        self.model = model
        self.extra_args = extra_args
        self.timeout_seconds = timeout_seconds
        self.project_root = project_root

    def generate(self, *, stage: str, prompt: str, schema_path: Path, work_dir: Path) -> ProviderResponse:
        output_path = work_dir / f".{stage}.{uuid.uuid4().hex}.json"
        command = [
            self.executable,
            "exec",
            "--ephemeral",
            "-C",
            str(self.project_root),
            "-s",
            "read-only",
            "--output-schema",
            str(schema_path),
            "-o",
            str(output_path),
        ]
        if self.model:
            command.extend(["-m", self.model])
        command.extend(self.extra_args)
        command.append("-")
        try:
            result = run_process(command, prompt=prompt, cwd=self.project_root, timeout_seconds=self.timeout_seconds)
        except OSError as exc:
            raise PipelineError(f"Could not start Codex CLI: {exc}") from exc
        try:
            if result.returncode != 0:
                detail = result.stderr.decode("utf-8", errors="replace")[-2000:]
                raise PipelineError(f"Codex CLI exited with {result.returncode}: {detail}")
            if not output_path.is_file():
                raise PipelineError("Codex CLI did not create its structured output file")
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise PipelineError("Codex CLI output must be a JSON object")
            return ProviderResponse(
                payload=payload,
                return_code=result.returncode,
                stdout=result.stdout.decode("utf-8", errors="replace"),
                stderr=result.stderr.decode("utf-8", errors="replace"),
            )
        except json.JSONDecodeError as exc:
            raise PipelineError(f"Codex CLI returned invalid JSON: {exc}") from exc
        finally:
            output_path.unlink(missing_ok=True)

