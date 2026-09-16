"""Risk officer hard limits. Independent global veto; not a gate substitute."""

from __future__ import annotations

from astra_desk.accounting import CapitalBooks
from astra_desk.contract import HardLimits
from astra_desk.types import RiskOfficerOutput


def evaluate_hard_limits(
    *,
    books: CapitalBooks,
    limits: HardLimits,
    proposed_notional: float,
    correlation_group: str,
) -> RiskOfficerOutput:
    equity = books.equity
    if equity <= 0:
        return RiskOfficerOutput(
            approved=False,
            exposure_after=books.exposure_pct,
            veto_reason="non_positive_equity",
        )
    if books.daily_loss_pct >= limits.daily_loss_limit_pct:
        return RiskOfficerOutput(
            approved=False,
            exposure_after=books.exposure_pct,
            veto_reason="daily_loss_limit_pct",
        )

    proposed = abs(float(proposed_notional))
    single_pct = (proposed / equity) * 100.0
    if single_pct > limits.max_single_position_pct:
        return RiskOfficerOutput(
            approved=False,
            exposure_after=books.exposure_pct,
            veto_reason="max_single_position_pct",
        )

    exposure_after = ((books.exposure_notional + proposed) / equity) * 100.0
    if exposure_after > limits.max_total_exposure_pct:
        return RiskOfficerOutput(
            approved=False,
            exposure_after=exposure_after,
            veto_reason="max_total_exposure_pct",
        )

    correlated = sum(
        1 for pos in books.positions if pos.correlation_group == correlation_group
    )
    if correlated >= limits.max_correlated_positions:
        return RiskOfficerOutput(
            approved=False,
            exposure_after=exposure_after,
            veto_reason="max_correlated_positions",
        )

    return RiskOfficerOutput(approved=True, exposure_after=exposure_after, veto_reason=None)
