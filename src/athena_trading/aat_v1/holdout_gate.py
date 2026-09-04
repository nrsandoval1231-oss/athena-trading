"""Fail-closed authorization and scoring boundaries for the one-time holdout."""
from __future__ import annotations

import random
import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from statistics import fmean
from typing import Any
from pathlib import Path

from ..athena_adapter.contracts import canonical_hash
from .training import AATValidationError


def audit_holdout_preflight(
    contract_path: str | Path, authorization_path: str | Path,
    opportunity_receipt_path: str | Path,
) -> dict[str, Any]:
    """End fail-closed before Actor calls when frozen claim evidence is insufficient."""
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    authorization = json.loads(Path(authorization_path).read_text(encoding="utf-8"))
    contract_hash = canonical_hash(contract)
    validate_holdout_authorization(authorization, contract_hash=contract_hash, prior_receipts=[])
    receipt_path = Path(opportunity_receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload = dict(receipt)
    claimed = payload.pop("projection_hash", None)
    rows_path = receipt_path.parent / receipt.get("rows_path", "")
    if (
        claimed != canonical_hash(payload)
        or receipt.get("partition") != "SEALED_HOLDOUT"
        or receipt.get("contains_sealed_holdout") is not True
        or receipt.get("contains_outcomes") is not False
        or receipt.get("execution_authority") != "ABSENT"
        or not rows_path.is_file()
        or hashlib.sha256(rows_path.read_bytes()).hexdigest() != receipt.get("rows_sha256")
    ):
        raise AATValidationError("sealed-holdout opportunity evidence drift")
    count = receipt.get("row_count")
    if not isinstance(count, int):
        raise AATValidationError("sealed-holdout opportunity count is invalid")
    blockers = []
    if count < contract["minimum_opportunity_count"]:
        blockers.append("QUALIFIED_OPPORTUNITIES_BELOW_FROZEN_MINIMUM")
    result = {
        "schema_version": "aat-v1-sealed-holdout-preflight-v1",
        "status": "INVALID_EVIDENCE" if blockers else "READY_FOR_ONE_TIME_ACTOR_EVALUATION",
        "contract_hash": contract_hash,
        "authorization_id": authorization["authorization_id"],
        "authorization_hash": canonical_hash(authorization),
        "opportunity_projection_hash": receipt["projection_hash"],
        "qualified_opportunity_count": count,
        "minimum_opportunity_count": contract["minimum_opportunity_count"],
        "blockers": blockers,
        "actor_called": False,
        "claim_authorized": False,
        "sealed_holdout_consumed": True,
        "execution_authority": "ABSENT",
    }
    result["result_hash"] = canonical_hash(result)
    return result


def validate_holdout_authorization(
    authorization: Mapping[str, Any], *, contract_hash: str, prior_receipts: Sequence[Mapping[str, Any]],
) -> None:
    """Require a distinct, contract-bound authorization and prove single use."""
    required = {
        "schema_version": "aat-v1-sealed-holdout-authorization-v1",
        "contract_hash": contract_hash,
        "scope": "ONE_TIME_SEALED_HOLDOUT_EVALUATION",
        "single_use": True,
        "execution_authority": "ABSENT",
    }
    if any(authorization.get(key) != value for key, value in required.items()):
        raise AATValidationError("sealed-holdout authorization drift")
    if not all(
        isinstance(authorization.get(key), str) and authorization[key].strip()
        for key in ("authorization_id", "authorized_by", "authorized_at_utc")
    ):
        raise AATValidationError("sealed-holdout authorization identity is missing")
    if any(row.get("authorization_id") == authorization["authorization_id"] for row in prior_receipts):
        raise AATValidationError("sealed-holdout authorization was already consumed")


def _lower_bound(values: list[float], *, resamples: int, confidence: float, seed: int) -> float:
    rng = random.Random(seed)
    size = len(values)
    means = sorted(
        fmean(values[rng.randrange(size)] for _ in range(size))
        for _ in range(resamples)
    )
    return means[max(0, int((1.0 - confidence) * resamples) - 1)]


def _drawdown(values: Sequence[float]) -> float:
    equity = peak = worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def evaluate_claim(contract: Mapping[str, Any], episodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Apply only the frozen claim gates to already-produced sealed episodes."""
    arms = (
        contract["primary_comparator"], contract["secondary_comparator"], contract["decision_arm"]
    )
    grouped: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in episodes:
        if (
            row.get("partition") != contract["required_partition"]
            or row.get("execution_authority") != "ABSENT"
            or row.get("arm") not in arms
        ):
            raise AATValidationError("sealed-holdout episode boundary mismatch")
        opportunity_id = row.get("opportunity_id")
        if not isinstance(opportunity_id, str) or row["arm"] in grouped[opportunity_id]:
            raise AATValidationError("sealed-holdout episode identity mismatch")
        grouped[opportunity_id][row["arm"]] = row
    if (
        len(grouped) < contract["minimum_opportunity_count"]
        or any(set(rows) != set(arms) for rows in grouped.values())
    ):
        raise AATValidationError("sealed-holdout matched-pair evidence is incomplete")
    regimes = {name: 0 for name in contract["required_regimes"]}
    for rows in grouped.values():
        observed = {row.get("regime") for row in rows.values()}
        if len(observed) != 1 or next(iter(observed)) not in regimes:
            raise AATValidationError("sealed-holdout regime evidence mismatch")
        regimes[next(iter(observed))] += 1
    if any(count < contract["minimum_regime_support"] for count in regimes.values()):
        raise AATValidationError("sealed-holdout regime support is incomplete")

    decision = contract["decision_arm"]
    comparisons = {}
    all_pass = True
    for index, comparator in enumerate(arms[:2]):
        ordered = [grouped[key] for key in sorted(grouped)]
        effects = [float(rows[comparator]["regret_r"]) - float(rows[decision]["regret_r"]) for rows in ordered]
        drawdown_regression = _drawdown(
            [float(rows[decision]["score_r"]) for rows in ordered]
        ) - _drawdown([float(rows[comparator]["score_r"]) for rows in ordered])
        mean_effect = fmean(effects)
        lower_bound = _lower_bound(
            effects, resamples=contract["bootstrap_resamples"],
            confidence=contract["confidence_level"], seed=contract["bootstrap_seed"] + index,
        )
        passed = (
            mean_effect >= contract["minimum_effect_r"]
            and lower_bound > 0
            and drawdown_regression <= contract["maximum_drawdown_regression_r"]
        )
        all_pass = all_pass and passed
        comparisons[comparator] = {
            "mean_paired_regret_improvement_r": mean_effect,
            "lower_confidence_bound_r": lower_bound,
            "drawdown_regression_r": drawdown_regression,
            "passed": passed,
        }
    result = {
        "schema_version": "aat-v1-sealed-holdout-claim-result-v1",
        "status": contract["supported_outcome"] if all_pass else contract["unsuccessful_outcome"],
        "contract_hash": canonical_hash(contract),
        "opportunity_count": len(grouped),
        "regime_support": regimes,
        "comparisons": comparisons,
        "sealed_holdout_consumed": True,
        "execution_authority": "ABSENT",
    }
    result["result_hash"] = canonical_hash(result)
    return result
