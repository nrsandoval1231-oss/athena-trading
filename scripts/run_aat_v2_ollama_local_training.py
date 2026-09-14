"""Fail-closed local Ollama AAT-V2 TRAINING runner.

Defaults to a tiny dry-run smoke against the TRAINING-shaped fixture index.
Does not contact DeepSeek. Does not start the 116,935-call run.

Smoke (offline, default)::

    python scripts/run_aat_v2_ollama_local_training.py

Live warmup on Nick's desktop (still not the full plan)::

    python scripts/run_aat_v2_ollama_local_training.py \\
      --allow-live-ollama --limit-opportunities 2 \\
      --opportunity-index <TRAINING JSONL> --snapshot <macro snapshot JSONL>

Overnight full in-sample TRAINING (start tonight; prior local estimate ~93h)::

    python scripts/run_aat_v2_ollama_local_training.py \\
      --authorize-local-training --allow-live-ollama \\
      --opportunity-index <TRAINING JSONL> --snapshot <macro snapshot JSONL> \\
      --opportunity-receipt <receipt> --snapshot-receipt <receipt> \\
      --output-dir artifacts/aat_v2_ollama_local_training

Resume an interrupted overnight run with the same flags plus ``--resume``.

This authorizes **local Ollama TRAINING execution plumbing** under Nick's
direction. ``execution_authority`` stays ``ABSENT``. The Terra protocol stays
``REGISTERED_NOT_AUTHORIZED``. V1/V2 holdout is not consumed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from athena_trading.aat_v2.ollama_identity import (
    CLI_AUTHORIZATION_FLAG,
    DEFAULT_MODEL_ID,
    AuthorizationError,
    IdentityError,
)
from athena_trading.aat_v2.ollama_training import (
    DEFAULT_SMOKE_LIMIT,
    OllamaTrainingError,
    fixture_transport,
    forbid_output_path,
    run_ollama_local_training,
)
from athena_trading.aat_v2.ollama_transport import OllamaTransportError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = ROOT / "configs" / "aat_v2_training_plan_v2.json"
DEFAULT_PROTOCOL = ROOT / "configs" / "aat_v2_registered_protocol_v1.json"
DEFAULT_ASSIGNMENT = ROOT / "configs" / "aat_v2_dataset_assignment_v1.json"
DEFAULT_AMENDMENT = ROOT / "configs" / "aat_v2_e_arm_compact_schema_amendment_v1.json"
DEFAULT_IDENTITY = ROOT / "configs" / "aat_v2_ollama_qwen_actor_identity_v1.json"
DEFAULT_AUTHORIZATION = ROOT / "configs" / "aat_v2_ollama_local_training_authorization_v1.json"
DEFAULT_FIXTURE = ROOT / "configs" / "fixtures" / "ollama_qwen_training_v1.json"
DEFAULT_INDEX = ROOT / "configs" / "fixtures" / "aat_v2_training_opportunities_smoke_v1.jsonl"
DEFAULT_SNAPSHOT = ROOT / "configs" / "fixtures" / "aat_v2_training_snapshot_smoke_v1.jsonl"
DEFAULT_OUTPUT = ROOT / "artifacts" / "aat_v2_ollama_local_training_smoke"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--assignment", type=Path, default=DEFAULT_ASSIGNMENT)
    parser.add_argument("--amendment", type=Path, default=DEFAULT_AMENDMENT)
    parser.add_argument("--identity", type=Path, default=DEFAULT_IDENTITY)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    parser.add_argument("--opportunity-index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--opportunity-receipt", type=Path)
    parser.add_argument("--snapshot-receipt", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument(
        "--model-id",
        default=DEFAULT_MODEL_ID,
        help="must match the hashed identity (default qwen3-coder:30b)",
    )
    parser.add_argument(
        "--limit-opportunities",
        type=int,
        default=None,
        help="smoke/warmup cap; omit with --authorize-local-training for the full 16,705",
    )
    parser.add_argument(
        "--authorize-local-training",
        action="store_true",
        help="Nick's explicit local-TRAINING flag; required for the full plan",
    )
    parser.add_argument(
        "--allow-live-ollama",
        action="store_true",
        help="POST to loopback Ollama; default is mocked fixture transport",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--include-deliberation",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args(argv)

    try:
        forbid_output_path(args.output_dir)
        if args.authorize_local_training and args.limit_opportunities is None:
            limit = None
        elif args.limit_opportunities is not None:
            limit = args.limit_opportunities
        else:
            limit = DEFAULT_SMOKE_LIMIT
        if args.allow_live_ollama:
            transport = None
        else:
            transport = fixture_transport(args.fixture)
        result = run_ollama_local_training(
            training_plan_path=args.training_plan,
            protocol_path=args.protocol,
            assignment_path=args.assignment,
            opportunity_index_path=args.opportunity_index,
            snapshot_path=args.snapshot,
            amendment_path=args.amendment,
            identity_path=args.identity,
            authorization_path=args.authorization,
            opportunity_receipt_path=args.opportunity_receipt,
            snapshot_receipt_path=args.snapshot_receipt,
            output_dir=args.output_dir,
            limit_opportunities=limit,
            authorize_local_training=args.authorize_local_training,
            allow_live=args.allow_live_ollama,
            transport=transport,
            model_id=args.model_id,
            include_deliberation=args.include_deliberation,
            resume=args.resume,
            base_url=args.base_url,
        )
    except (OllamaTrainingError, OllamaTransportError, IdentityError, AuthorizationError) as exc:
        print(json.dumps(_error_payload(exc), indent=2, sort_keys=True))
        return 1

    payload = dict(result)
    payload.pop("records", None)
    payload["ok"] = True
    payload["ledger_path"] = str(args.output_dir / "ledger.jsonl")
    payload["receipt_path"] = str(args.output_dir / "receipt.json")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if result["status"] != "LOCAL_TRAINING_INCOMPLETE" else 2


def _error_payload(exc: BaseException) -> dict[str, object]:
    return {
        "ok": False,
        "error": str(exc),
        "execution_authority": "ABSENT",
        "sealed_holdout_consumed": False,
        "live_calls": getattr(exc, "live_calls", False),
        "authorize_local_training": getattr(exc, "authorize_local_training", False),
        "completed_calls": getattr(exc, "completed_calls", 0),
        "attempted_calls": getattr(exc, "attempted_calls", 0),
        "status": getattr(exc, "status", "LOCAL_TRAINING_SMOKE"),
        "authorization_cli_flag": CLI_AUTHORIZATION_FLAG,
        "registration_status": "REGISTERED_NOT_AUTHORIZED",
    }


if __name__ == "__main__":
    raise SystemExit(main())
