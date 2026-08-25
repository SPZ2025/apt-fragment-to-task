from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fragment_to_task.errors import PipelineError
from fragment_to_task.gold import validate_gold_rows
from fragment_to_task.utils import read_jsonl, sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a generation gold JSONL file.")
    parser.add_argument("--gold-file", type=Path, required=True)
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="Validate draft/rejected records too; approved-only is the default.",
    )
    args = parser.parse_args()
    try:
        path = args.gold_file.resolve()
        rows = validate_gold_rows(
            read_jsonl(path), require_approved=not args.allow_draft
        )
        summary = {
            "status": "PASS",
            "gold_file": str(path),
            "sha256": sha256_file(path),
            "records": len(rows),
            "domains": dict(sorted(collections.Counter(row["domain"] for row in rows).items())),
            "primary_categories": dict(
                sorted(collections.Counter(row["primary_category"] for row in rows).items())
            ),
            "statuses": dict(
                sorted(collections.Counter(row["gold_status"] for row in rows).items())
            ),
            "writes": 0,
            "llm_calls": 0,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        print("SUMMARY=PASS")
        return 0
    except (OSError, ValueError, PipelineError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("SUMMARY=FAIL", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
