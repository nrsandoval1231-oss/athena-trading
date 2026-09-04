"""Create an explicit hash-bound TRAINING lesson inventory."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..athena_adapter.contracts import canonical_hash
from .readiness import verified_receipt
from .training import AATValidationError


def build_lesson_inventory(outcome_receipt_path: str | Path) -> dict[str, Any]:
    receipt = verified_receipt(Path(outcome_receipt_path), hash_field="result_hash")
    if receipt.get("partition") != "TRAINING" or receipt.get("contains_sealed_holdout") is not False:
        raise AATValidationError("lesson source is not TRAINING-only")
    if receipt.get("adaptation_evidence") is not True:
        raise AATValidationError("lesson source is diagnostic-only")
    inventory = {
        "schema_version": "aat-v1-training-lesson-inventory-v1",
        "source_outcome_result_hash": receipt["result_hash"],
        "disposition": "NO_PROMOTED_LESSONS",
        "lessons": [],
        "negative_knowledge": [
            "No lesson promotion rule has been registered; empty memory is required."
        ],
        "partition": "TRAINING",
        "contains_sealed_holdout": False,
        "execution_authority": "ABSENT",
    }
    inventory["inventory_hash"] = canonical_hash(inventory)
    return inventory
