from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .config import load_config
from .errors import PipelineError
from .pipeline import inspect_input, run_pipeline
from .providers import create_provider


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(
        description="Generate and validate benchmark task candidates from frozen fragment JSONL."
    )
    parser.add_argument("--input", type=Path, required=True, help="Fragment JSONL input")
    parser.add_argument("--config", type=Path, default=root / "configs" / "default.toml")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--apply", action="store_true", help="Run models and write a new output directory")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = project_root()
    try:
        config = load_config(args.config.resolve())
        if not args.apply:
            if args.output_dir is not None:
                raise PipelineError("dry-run does not accept --output-dir")
            result = inspect_input(args.input.resolve(), config, root)
        else:
            if args.output_dir is None:
                raise PipelineError("--apply requires --output-dir")
            provider = create_provider(config, root)
            result = run_pipeline(
                input_path=args.input.resolve(),
                output_dir=args.output_dir.resolve(),
                project_root=root,
                config=config,
                provider=provider,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        print(f"SUMMARY={result['status']}")
        return 0
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("SUMMARY=FAIL", file=sys.stderr)
        return 2

