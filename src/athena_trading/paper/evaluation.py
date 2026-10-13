"""Fresh-evidence comparison. It does not read or rewrite consumed holdouts.

A decision that needs a bar's close cannot fill at that same close. A stop that
the next bar gaps through is filled at the open. Drawdown follows time, and the
uncertainty band resamples whole sessions so one day's trades are not treated
as independent. Nothing here is a claim of trading edge.
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Opportunity:
    """One underlying setup used by every compared policy."""

    opportunity_id: str
    session: str
    symbol: str
    at: str
    decision_close: float
    next_open: float
    next_close: float
    stop: float
    target: float
    direction: str
    jev_without_memory: str
    jev_with_memory: str


def _side(direction: str) -> int:
    if direction == "LONG":
        return 1
    if direction == "SHORT":
        return -1
    raise ValueError("direction must be LONG or SHORT")


def fill_from_next_bar(item: Opportunity) -> dict[str, Any]:
    """Fill at the next open. The decision close is not an executable price."""
    side = _side(item.direction)
    entry = item.next_open
    stop_gap = (item.stop - entry) * side
    if stop_gap >= 0:
        # The next open is already through the stop. The loss is the gap.
        exit_price = entry
        reason = "GAP_THROUGH_STOP"
    elif (item.target - entry) * side <= 0:
        exit_price = entry
        reason = "GAP_THROUGH_TARGET"
    else:
        # The bar's close is the only later price this record carries.
        favorable = (item.next_close - entry) * side
        adverse = (item.stop - entry) * side
        if favorable >= (item.target - entry) * side and adverse < 0:
            exit_price = item.target
            reason = "TARGET"
        elif (item.next_close - item.stop) * side <= 0:
            exit_price = item.next_close
            reason = "STOP_OR_CLOSE"
        else:
            exit_price = item.next_close
            reason = "CLOSE"
    signed = (exit_price - entry) * side
    risk = abs(item.decision_close - item.stop)
    return {
        "entry": entry, "exit": exit_price, "reason": reason,
        "gross": signed, "gross_r": None if risk <= 0 else signed / risk,
        "gapped_through_stop": reason == "GAP_THROUGH_STOP",
        "decision_close_used_as_fill": False,
    }


def adverse_excursion(item: Opportunity) -> float:
    """The worst signed move from the next open, including a gap through the stop."""
    side = _side(item.direction)
    gap = (item.stop - item.next_open) * side
    if gap >= 0:
        return (item.next_open - item.decision_close) * side
    close_move = (item.next_close - item.next_open) * side
    return min(0.0, close_move, (item.stop - item.next_open) * side)


def chronological_drawdown(rows: list[dict[str, Any]]) -> float:
    """Peak-to-trough of gross P&L in time order. Identity hashes are not time."""
    ordered = sorted(rows, key=lambda row: (str(row["at"]), str(row["opportunity_id"])))
    equity = peak = worst = 0.0
    for row in ordered:
        equity += float(row["gross"])
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def session_uncertainty(
    values: list[tuple[str, float]], *, resamples: int, confidence: float, seed: int,
) -> dict[str, float]:
    """Resample sessions, keeping every opportunity inside a session together."""
    if not values:
        return {"n": 0, "sessions": 0, "lower": 0.0, "mean": 0.0}
    grouped: dict[str, list[float]] = defaultdict(list)
    for session, value in values:
        grouped[session].append(value)
    sessions = sorted(grouped)
    flat = [value for session in sessions for value in grouped[session]]
    mean = sum(flat) / len(flat)
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        drawn = [rng.choice(sessions) for _ in sessions]
        sample = [value for session in drawn for value in grouped[session]]
        means.append(sum(sample) / len(sample))
    means.sort()
    index = max(0, int((1.0 - confidence) * resamples) - 1)
    return {"n": len(flat), "sessions": len(sessions), "lower": means[index], "mean": mean}


def _policy_takes(item: Opportunity, policy: str) -> bool:
    if policy == "deterministic":
        return True
    if policy == "jev_without_memory":
        return item.jev_without_memory == "TAKE_TRADE"
    if policy == "jev_with_memory":
        return item.jev_with_memory == "TAKE_TRADE"
    raise ValueError("unknown evaluation policy")


def compare_policies(
    opportunities: list[Opportunity], *, cost_per_share: float = 0.0, resamples: int = 200,
    confidence: float = 0.95, seed: int = 7,
) -> dict[str, Any]:
    """Same opportunities, three policies. Costs are scenarios, not a measured edge."""
    reports = {}
    for policy in ("deterministic", "jev_without_memory", "jev_with_memory"):
        taken = []
        skipped = []
        for item in opportunities:
            if _policy_takes(item, policy):
                fill = fill_from_next_bar(item)
                gross = float(fill["gross"]) - cost_per_share
                taken.append({
                    "opportunity_id": item.opportunity_id, "at": item.at, "session": item.session,
                    "symbol": item.symbol, "direction": item.direction, "gross": gross,
                    "reason": fill["reason"],
                    "gapped_through_stop": fill["gapped_through_stop"],
                    "adverse_excursion": adverse_excursion(item),
                })
            else:
                skipped.append(item.opportunity_id)
        grosses = [row["gross"] for row in taken]
        expectancy = sum(grosses) / len(opportunities) if opportunities else None
        by_direction = {}
        for direction in ("LONG", "SHORT"):
            subset = [row for row in taken if row["direction"] == direction]
            values = [row["gross"] for row in subset]
            by_direction[direction] = {
                "taken": len(subset),
                "expectancy_per_taken": (sum(values) / len(values)) if values else None,
                "worst_loss": min(values) if values else None,
            }
        reports[policy] = {
            "taken": len(taken), "skipped": skipped,
            "expectancy_per_opportunity": expectancy,
            "drawdown": chronological_drawdown(taken),
            "worst_loss": min(grosses) if grosses else None,
            "gap_through_stop_count": sum(row["gapped_through_stop"] for row in taken),
            "turnover": len(taken),
            "by_direction": by_direction,
            "uncertainty": session_uncertainty(
                [(row["session"], row["gross"]) for row in taken],
                resamples=resamples, confidence=confidence, seed=seed,
            ),
        }
    return {
        "policies": reports,
        "cost_per_share": cost_per_share,
        "fill_assumption": "next bar open; decision close is not a fill",
        "paper_fill_limitation": "broker paper fills are not this simulation",
        "jev_confidence": "action probability is not a calibrated probability of profit",
        "performance": "unverified",
        "holdouts": "not read",
    }
