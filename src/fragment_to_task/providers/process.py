from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence


def run_process(
    command: Sequence[str], *, prompt: str, cwd: Path, timeout_seconds: int
) -> subprocess.CompletedProcess[bytes]:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    try:
        stdout, stderr = process.communicate(prompt.encode("utf-8"), timeout=timeout_seconds)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30,
            )
        else:  # pragma: no cover
            process.kill()
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
            stdout, stderr = b"", b""
        stderr += f"\nprovider timeout after {timeout_seconds} seconds".encode()
        return subprocess.CompletedProcess(command, 124, stdout, stderr)

