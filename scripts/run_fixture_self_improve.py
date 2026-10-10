"""Run the offline fixture self-improve lab.

Mutates one management knob at a time, scores sealed synthetic bars, and
writes generation receipts. Execution authority stays ABSENT. This process
does not call a broker or model client and does not read the AAT-V1 holdout.

    python scripts/run_fixture_self_improve.py --out /tmp/fixture-self-improve
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from athena_trading.execution.authority import AuthorityError
from athena_trading.paper.self_improve import HoldoutSealError, run_fixture_lab


def _mentions_live(argv: list[str]) -> bool:
    return any(token.strip().upper() == "LIVE" for token in argv)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if _mentions_live(raw):
        print("LIVE execution authority is not available", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, required=True,
        help="directory for generation and holdout receipts",
    )
    parser.add_argument(
        "--generations", type=int, default=1,
        help="search generations on the same fixture partition (default 1)",
    )
    parser.add_argument(
        "--authority", default=None, help=argparse.SUPPRESS,
    )
    args = parser.parse_args(raw)
    if args.authority is not None:
        print("this lab does not accept an execution authority", file=sys.stderr)
        return 2
    if args.generations < 1:
        print("generations must be positive", file=sys.stderr)
        return 2
    try:
        summary = run_fixture_lab(args.out, generations=args.generations)
    except (ValueError, AuthorityError, HoldoutSealError, FileExistsError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
