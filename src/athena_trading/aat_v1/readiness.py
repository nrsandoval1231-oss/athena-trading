"""Fail-closed readiness audit for an AAT adaptation run."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..athena_adapter.contracts import canonical_hash
from .training import AATValidationError, load_registered_protocol, load_training_projection

_SNAPSHOT = re.compile(r"^gpt-5\.6-terra-\d{4}-\d{2}-\d{2}$")


def _verified_receipt(path: Path, *, hash_field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AATValidationError(f"receipt is unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise AATValidationError(f"receipt is malformed: {path.name}")
    payload = dict(value)
    claimed = payload.pop(hash_field, None)
    if claimed != canonical_hash(payload):
        raise AATValidationError(f"receipt hash mismatch: {path.name}")
    rows_name, rows_hash = value.get("rows_path"), value.get("rows_sha256")
    if not isinstance(rows_name, str) or not isinstance(rows_hash, str):
        raise AATValidationError(f"row binding is missing: {path.name}")
    rows_path = path.parent / rows_name
    if not rows_path.is_file() or hashlib.sha256(rows_path.read_bytes()).hexdigest() != rows_hash:
        raise AATValidationError(f"row hash mismatch: {path.name}")
    return value


def audit_adaptation_readiness(
    protocol_path: str | Path,
    opportunity_receipt_path: str | Path,
    outcome_receipt_path: str | Path,
    *,
    lesson_inventory_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return reproducible negative knowledge; never run an Actor or holdout."""
    protocol_file = Path(protocol_path)
    registered = load_registered_protocol(protocol_file)
    protocol = registered["protocol"]
    opportunity_receipt, opportunities = load_training_projection(
        opportunity_receipt_path, protocol_file)
    outcome_receipt = _verified_receipt(Path(outcome_receipt_path), hash_field="result_hash")
    if outcome_receipt.get("opportunity_projection_hash") != opportunity_receipt.get("projection_hash"):
        raise AATValidationError("outcome/opportunity projection mismatch")
    if outcome_receipt.get("partition") != "TRAINING" or outcome_receipt.get("contains_sealed_holdout") is not False:
        raise AATValidationError("outcome evidence is not TRAINING-only")
    if outcome_receipt.get("execution_authority") != "ABSENT":
        raise AATValidationError("execution authority must be ABSENT")

    blockers: list[dict[str, Any]] = []

    def block(code: str, observed: Any, required: Any) -> None:
        blockers.append({"code": code, "observed": observed, "required": required})

    actor = protocol.get("actor", {})
    model_id = actor.get("model_id") if isinstance(actor, dict) else None
    if not isinstance(model_id, str) or _SNAPSHOT.fullmatch(model_id) is None:
        block("ACTOR_MODEL_NOT_IMMUTABLE_SNAPSHOT", model_id, "gpt-5.6-terra-YYYY-MM-DD")
    minimum_pairs = protocol.get("minimum_pairs")
    if not isinstance(minimum_pairs, int) or minimum_pairs < 300:
        block("REGISTERED_MINIMUM_PAIRS_BELOW_FLOOR", minimum_pairs, 300)
    if len(opportunities) < 300:
        block("TRAINING_OPPORTUNITIES_BELOW_FLOOR", len(opportunities), 300)
    if outcome_receipt.get("row_count") != len(opportunities):
        block("OUTCOME_PAIR_COUNT_MISMATCH", outcome_receipt.get("row_count"), len(opportunities))
    if outcome_receipt.get("adaptation_evidence") is not True:
        block("OUTCOME_EVALUATOR_DIAGNOSTIC_ONLY", outcome_receipt.get("adaptation_evidence"), True)
    evaluator = outcome_receipt.get("evaluator")
    if evaluator == "ORB_M_FIRST_BREAKOUT_NO_DETERIORATION_DIAGNOSTIC_V1":
        block("FULL_SESSION_REENTRY_AND_DETERIORATION_ABSENT", evaluator, "FROZEN_FULL_ORB_M_EVALUATOR")
    if outcome_receipt.get("scoring_hash") != protocol.get("scoring_hash"):
        block("SCORING_SEMANTICS_NOT_BOUND", outcome_receipt.get("scoring_hash"), protocol.get("scoring_hash"))

    lessons = None
    if lesson_inventory_path is not None:
        lesson_file = Path(lesson_inventory_path)
        if lesson_file.is_file():
            lessons = json.loads(lesson_file.read_text(encoding="utf-8"))
    if not isinstance(lessons, dict):
        block("TRAINING_LESSON_INVENTORY_ABSENT", None, "HASH_BOUND_TRAINING_INVENTORY")
    else:
        rows = lessons.get("lessons")
        promoted = [row for row in rows if isinstance(row, dict) and row.get("disposition") == "PROMOTED"] if isinstance(rows, list) else []
        if not promoted:
            block("PROMOTED_TRAINING_LESSONS_ABSENT", 0, ">=1")

    report = {
        "schema_version": "aat-v1-adaptation-readiness-v1",
        "status": "READY" if not blockers else "BLOCKED_NEGATIVE_KNOWLEDGE",
        "protocol_hash": registered["protocol_hash"],
        "opportunity_projection_hash": opportunity_receipt["projection_hash"],
        "outcome_result_hash": outcome_receipt["result_hash"],
        "blocker_count": len(blockers),
        "blockers": blockers,
        "sealed_holdout_consumed": False,
        "actor_called": False,
        "execution_authority": "ABSENT",
    }
    report["report_hash"] = canonical_hash(report)
    return report
