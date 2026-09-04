"""Write an explicit, hash-bound TRAINING lesson inventory."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from athena_trading.aat_v1.lessons import build_lesson_inventory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outcome-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite lesson inventory")
    inventory = build_lesson_inventory(args.outcome_receipt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
