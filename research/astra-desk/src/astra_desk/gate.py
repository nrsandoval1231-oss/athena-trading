"""Weighted threshold gate. Fail closed on missing inputs or non-positive EV."""

from __future__ import annotations

from typing import Mapping

from astra_desk.errors import GateError
from astra_desk.types import ConvictionBand, GateDecision


def weighted_conviction(
    convictions: Mapping[str, float],
    weights: Mapping[str, float],
) -> float:
    missing = sorted(agent_id for agent_id in weights if agent_id not in convictions)
    if missing:
        raise GateError(f"missing convictions for weighted agents: {missing}")
    return sum(float(weights[agent_id]) * float(convictions[agent_id]) for agent_id in weights)


def band_multiplier(
    total: float,
    bands: tuple[ConvictionBand, ...],
) -> float | None:
    for band in sorted(bands, key=lambda b: b.min, reverse=True):
        if total >= band.min:
            return band.size_multiplier
    return None


def evaluate_gate(
    *,
    convictions: Mapping[str, float],
    weights: Mapping[str, float],
    bands: tuple[ConvictionBand, ...],
    quorum_have: int,
    quorum_required: int,
    ev_estimate: float,
) -> GateDecision:
    inputs = {
        "convictions": dict(convictions),
        "weights": dict(weights),
        "ev_estimate": ev_estimate,
        "quorum_have": quorum_have,
        "quorum_required": quorum_required,
    }
    quorum_met = quorum_have >= quorum_required
    try:
        total = weighted_conviction(convictions, weights)
    except GateError as exc:
        return GateDecision(
            deployed=False,
            total_weighted_conviction=0.0,
            size_multiplier=0.0,
            ev_estimate=ev_estimate,
            quorum_met=quorum_met,
            quorum_have=quorum_have,
            quorum_required=quorum_required,
            reason=str(exc),
            inputs=inputs,
        )
    inputs = {**inputs, "total_weighted_conviction": total}

    if not quorum_met:
        return GateDecision(
            deployed=False,
            total_weighted_conviction=total,
            size_multiplier=0.0,
            ev_estimate=ev_estimate,
            quorum_met=False,
            quorum_have=quorum_have,
            quorum_required=quorum_required,
            reason="quorum_not_met",
            inputs=inputs,
        )
    if not (ev_estimate > 0):
        return GateDecision(
            deployed=False,
            total_weighted_conviction=total,
            size_multiplier=0.0,
            ev_estimate=ev_estimate,
            quorum_met=True,
            quorum_have=quorum_have,
            quorum_required=quorum_required,
            reason="ev_not_positive",
            inputs=inputs,
        )
    multiplier = band_multiplier(total, bands)
    if multiplier is None:
        return GateDecision(
            deployed=False,
            total_weighted_conviction=total,
            size_multiplier=0.0,
            ev_estimate=ev_estimate,
            quorum_met=True,
            quorum_have=quorum_have,
            quorum_required=quorum_required,
            reason="below_conviction_threshold",
            inputs=inputs,
        )
    return GateDecision(
        deployed=True,
        total_weighted_conviction=total,
        size_multiplier=multiplier,
        ev_estimate=ev_estimate,
        quorum_met=True,
        quorum_have=quorum_have,
        quorum_required=quorum_required,
        reason="deploy_rule_satisfied",
        inputs=inputs,
    )
