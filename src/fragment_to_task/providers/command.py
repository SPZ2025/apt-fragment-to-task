from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..errors import PipelineError
from .base import ProviderResponse
from .process import run_process


class CommandProvider:
    """Run an arbitrary adapter command without a shell.

    The prompt is sent on stdin. Command arguments may contain the placeholders
    ``{stage}``, ``{schema_file}``, ``{output_file}``, and ``{work_dir}``.
    The adapter may write JSON to ``{output_file}`` or print JSON to stdout.
    """

    name = "command"

    def __init__(self, *, command: tuple[str, ...], timeout_seconds: int, project_root: Path) -> None:
        if not command:
            raise PipelineError("Command provider requires a non-empty command")
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.project_root = project_root

    def generate(self, *, stage: str, prompt: str, schema_path: Path, work_dir: Path) -> ProviderResponse:
        output_path = work_dir / f".{stage}.{uuid.uuid4().hex}.json"
        replacements = {
            "stage": stage,
            "schema_file": str(schema_path),
            "output_file": str(output_path),
            "work_dir": str(work_dir),
        }
        try:
            command = [part.format(**replacements) for part in self.command]
        except KeyError as exc:
            raise PipelineError(f"Unknown command placeholder: {exc}") from exc
        try:
            result = run_process(command, prompt=prompt, cwd=self.project_root, timeout_seconds=self.timeout_seconds)
        except OSError as exc:
            raise PipelineError(f"Could not start command provider: {exc}") from exc
        try:
            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")
            if result.returncode != 0:
                raise PipelineError(f"Command provider exited with {result.returncode}: {stderr[-2000:]}")
            raw = output_path.read_text(encoding="utf-8") if output_path.is_file() else stdout
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise PipelineError("Command provider output must be a JSON object")
            return ProviderResponse(payload=payload, return_code=result.returncode, stdout=stdout, stderr=stderr)
        except json.JSONDecodeError as exc:
            raise PipelineError(f"Command provider returned invalid JSON: {exc}") from exc
        finally:
            output_path.unlink(missing_ok=True)

