"""Fail-closed Ollama AAT-V2 TRAINING runner.

Defaults to a tiny dry-run smoke against the TRAINING-shaped fixture index.
Does not contact DeepSeek or RunPod. Does not start the 116,935-call run.

Desktop smoke (offline, default ``qwen3.5:9b``)::

    python scripts/run_aat_v2_ollama_local_training.py

RunPod / remote-Ollama smoke (offline mocked ``qwen2.5:14b``)::

    python scripts/run_aat_v2_ollama_local_training.py \\
      --identity configs/aat_v2_ollama_qwen_14b_runpod_actor_identity_v1.json \\
      --authorization configs/aat_v2_ollama_runpod_training_authorization_v1.json \\
      --base-url http://127.0.0.1:11434

Live RunPod warmup (14 calls). Point ``--base-url`` at the pod or an SSH tunnel::

    python scripts/run_aat_v2_ollama_local_training.py \\
      --identity configs/aat_v2_ollama_qwen_14b_runpod_actor_identity_v1.json \\
      --authorization configs/aat_v2_ollama_runpod_training_authorization_v1.json \\
      --allow-live-ollama --limit-opportunities 2 \\
      --base-url http://<pod>:11434 \\
      --opportunity-index <TRAINING JSONL> --snapshot <macro snapshot JSONL>

Overnight full in-sample TRAINING on RunPod (still ABSENT; not Terra)::

    python scripts/run_aat_v2_ollama_local_training.py \\
      --identity configs/aat_v2_ollama_qwen_14b_runpod_actor_identity_v1.json \\
      --authorization configs/aat_v2_ollama_runpod_training_authorization_v1.json \\
      --authorize-runpod-training --allow-live-ollama \\
      --base-url http://<pod>:11434 \\
      --opportunity-index <TRAINING JSONL> --snapshot <macro snapshot JSONL> \\
      --opportunity-receipt <receipt> --snapshot-receipt <receipt> \\
      --output-dir artifacts/aat_v2_ollama_runpod_training

Resume with the same flags plus ``--resume``. Download artifacts before
terminating the pod. Chunked ``ledger.jsonl`` is the durable record.

``--authorize-runpod-training`` does not authorize Terra or desktop
``qwen3.5:9b``. ``execution_authority`` stays ``ABSENT``. Holdout stays unused.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from athena_trading.aat_v2.ollama_identity import (
    CLI_AUTHORIZATION_FLAG,
    DEFAULT_MODEL_ID,
    RUNPOD_CLI_AUTHORIZATION_FLAG,
    RUNPOD_MODEL_ID,
    AuthorizationError,
    IdentityError,
)
from athena_trading.aat_v2.ollama_training import (
    DEFAULT_MAX_CONCURRENT_CALLS,
    DEFAULT_SMOKE_LIMIT,
    MAX_CONCURRENT_CALLS_CEILING,
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
DEFAULT_FIXTURE = ROOT / "configs" / "fixtures" / "ollama_qwen_training_v1.json"
RUNPOD_FIXTURE = ROOT / "configs" / "fixtures" / "ollama_qwen_14b_runpod_training_v1.json"
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
    parser.add_argument(
        "--authorization",
        type=Path,
        default=None,
        help="defaults to the authorization artifact matching --identity",
    )
    parser.add_argument("--opportunity-index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--opportunity-receipt", type=Path)
    parser.add_argument("--snapshot-receipt", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--fixture", type=Path, default=None)
    parser.add_argument(
        "--model-id",
        default=None,
        help="must match the hashed identity (desktop default qwen3.5:9b; "
        "RunPod identity default qwen2.5:14b)",
    )
    parser.add_argument(
        "--limit-opportunities",
        type=int,
        default=None,
        help="smoke/warmup cap; omit with the matching authorize flag for the full 16,705",
    )
    parser.add_argument(
        "--authorize-local-training",
        action="store_true",
        help="Nick's explicit local qwen3.5:9b TRAINING flag; required for the full desktop plan",
    )
    parser.add_argument(
        "--authorize-runpod-training",
        action="store_true",
        help="Nick's explicit RunPod qwen2.5:14b TRAINING flag; does not authorize Terra or 9b",
    )
    parser.add_argument(
        "--allow-live-ollama",
        action="store_true",
        help="POST to Ollama; default is mocked fixture transport",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--include-deliberation",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:11434",
        help="Ollama native /api/chat origin. Loopback is the desktop/SSH-tunnel "
        "default. Pass http://<pod>:11434 for a reachable RunPod GPU.",
    )
    parser.add_argument(
        "--max-concurrent-calls",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_CALLS,
        help=(
            f"opportunity-level concurrency (default {DEFAULT_MAX_CONCURRENT_CALLS}, "
            f"max {MAX_CONCURRENT_CALLS_CEILING}). Ollama on one GPU serializes "
            "inference; 64-wide blasts are refused. Arm E stays sequential per row."
        ),
    )
    args = parser.parse_args(argv)

    try:
        forbid_output_path(args.output_dir)
        if (
            args.authorize_local_training or args.authorize_runpod_training
        ) and args.limit_opportunities is None:
            limit = None
        elif args.limit_opportunities is not None:
            limit = args.limit_opportunities
        else:
            limit = DEFAULT_SMOKE_LIMIT
        identity_model = _identity_model_id(args.identity)
        model_id = args.model_id
        if args.allow_live_ollama:
            transport = None
        else:
            fixture = args.fixture
            if fixture is None:
                fixture = RUNPOD_FIXTURE if identity_model == RUNPOD_MODEL_ID else DEFAULT_FIXTURE
            transport = fixture_transport(fixture, model_id=identity_model)
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
            authorize_runpod_training=args.authorize_runpod_training,
            allow_live=args.allow_live_ollama,
            transport=transport,
            model_id=model_id,
            include_deliberation=args.include_deliberation,
            resume=args.resume,
            base_url=args.base_url,
            max_concurrent_calls=args.max_concurrent_calls,
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


def _identity_model_id(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_MODEL_ID
    model_id = payload.get("model_id")
    return model_id if isinstance(model_id, str) and model_id else DEFAULT_MODEL_ID


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
        "runpod_authorization_cli_flag": RUNPOD_CLI_AUTHORIZATION_FLAG,
        "registration_status": "REGISTERED_NOT_AUTHORIZED",
    }


if __name__ == "__main__":
    raise SystemExit(main())
