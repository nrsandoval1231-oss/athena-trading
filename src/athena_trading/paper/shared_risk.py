"""One paper risk view for both arms.

Each arm keeps its own approved caps. This gate adds only the account facts
both books can already see: a symbol cannot be reserved twice, and combined
reserved notional cannot exceed buying power. Correlation, concentration,
weekly and monthly loss, portfolio drawdown, and overnight stress have no
approved threshold. Those checks are listed and do not authorize exposure.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .journal import PaperJournal

UNRESOLVED_SHARED_POLICY = (
    "correlation_limit",
    "concentration_limit",
    "weekly_loss_limit",
    "monthly_loss_limit",
    "portfolio_drawdown_limit",
    "overnight_stress_limit",
)


@dataclass(frozen=True)
class OpenExposure:
    arm: str
    symbol: str
    notional: float
    stop_risk: float
    overnight: bool
    client_order_id: str


@dataclass(frozen=True)
class SharedDecision:
    allowed: bool
    reason: str
    unresolved: tuple[str, ...] = UNRESOLVED_SHARED_POLICY
    overnight_authority: str = "inactive"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _remaining_qty(order: dict[str, Any], parent: dict[str, Any] | None) -> float | None:
    """Reserved shares, or entry fills minus opposite-side fills when a parent is known."""
    reserved = _number(order.get("qty"))
    if parent is None:
        return reserved
    entered = _number(parent.get("filled_qty"))
    if entered is None:
        return reserved
    exited = 0.0
    for leg in parent.get("legs") or []:
        if not isinstance(leg, dict) or leg.get("side") == order.get("side"):
            continue
        if leg.get("side") not in {"buy", "sell"}:
            return None
        filled = _number(leg.get("filled_qty") or 0)
        if filled is None:
            return None
        exited += filled
    if exited > entered:
        return None
    return entered - exited


def exposures_from_journal(journal: PaperJournal, arm: str) -> list[OpenExposure]:
    """Open reservations in one journal. A partial exit keeps only the residual shares."""
    closed = {
        event.get("client_order_id") for event in journal.events
        if event["kind"] in {"position_closed", "entry_not_created", "flat_confirmed"}
        and isinstance(event.get("client_order_id"), str)
    }
    if journal.last("flat_confirmed") and arm == "intraday":
        closed.update(
            event["order"]["client_order_id"] for event in journal.events
            if event["kind"] == "entry_intent" and isinstance(event.get("order"), dict)
        )
    parents: dict[str, dict[str, Any]] = {}
    for event in journal.events:
        if event["kind"] != "entry_observation":
            continue
        identity = event.get("client_order_id")
        order = event.get("order")
        if isinstance(identity, str) and isinstance(order, dict):
            parents[identity] = order
    open_rows: list[OpenExposure] = []
    seen: set[str] = set()
    for event in journal.events:
        if event["kind"] != "entry_intent":
            continue
        order = event.get("order")
        if not isinstance(order, dict):
            continue
        identity = order.get("client_order_id")
        if not isinstance(identity, str) or identity in closed or identity in seen:
            continue
        seen.add(identity)
        qty = _remaining_qty(order, parents.get(identity))
        price = _number(order.get("limit_price"))
        stop = _number((order.get("stop_loss") or {}).get("stop_price"))
        if qty is None or price is None or stop is None or qty < 0 or price <= 0:
            continue
        if qty == 0:
            continue
        open_rows.append(OpenExposure(
            arm=arm, symbol=str(order.get("symbol") or ""), notional=price * qty,
            stop_risk=abs(price - stop) * qty, overnight=arm == "earnings",
            client_order_id=identity,
        ))
    return open_rows


class SharedBookBusy(ValueError):
    """The other arm's journal is locked. Exposure is unknown, so new entries stop."""


def _read_journal(path: Path, arm: str) -> list[OpenExposure]:
    if not path.exists():
        return []
    try:
        journal = PaperJournal(path)
    except ValueError as exc:
        if "another paper runner holds" in str(exc):
            raise SharedBookBusy(str(path)) from exc
        raise
    try:
        return exposures_from_journal(journal, arm)
    finally:
        journal.close()


def other_arm_exposure(arm: str, *, today: date | None = None) -> list[OpenExposure]:
    """The other book. A missing directory is an arm that is not running."""
    if arm == "earnings":
        root = Path(os.environ.get("ATHENA_STATE_DIR", ""))
        if not os.environ.get("ATHENA_STATE_DIR", "").strip() or not root.is_dir():
            return []
        rows: list[OpenExposure] = []
        paths = sorted(root.glob("*.sqlite"))
        if today is not None:
            current = root / f"{today.isoformat()}.sqlite"
            paths = [current] if current in paths else []
        for path in paths:
            rows.extend(_read_journal(path, "intraday"))
        return rows
    root = Path(os.environ.get("ATHENA_EARNINGS_STATE_DIR", ""))
    if not os.environ.get("ATHENA_EARNINGS_STATE_DIR", "").strip() or not root.is_dir():
        return []
    return _read_journal(root / "book.sqlite", "earnings")


def authorize_entry(
    *, arm: str, symbol: str, notional: float, stop_risk: float, overnight: bool,
    buying_power: float, open_exposure: list[OpenExposure],
) -> SharedDecision:
    """Refuse a new entry that spends capacity the other arm already reserved.

    Unresolved portfolio checks stay inactive. They are never an approval.
    """
    if arm not in {"intraday", "earnings"}:
        return SharedDecision(False, "UNKNOWN_ARM")
    if overnight and arm != "earnings":
        return SharedDecision(False, "OVERNIGHT_NOT_AUTHORIZED")
    if notional < 0 or stop_risk < 0 or buying_power < 0:
        return SharedDecision(False, "INVALID_EXPOSURE")
    if any(item.symbol == symbol and item.symbol for item in open_exposure):
        return SharedDecision(False, "SYMBOL_ALREADY_RESERVED")
    used = sum(item.notional for item in open_exposure)
    if used + notional > buying_power:
        return SharedDecision(False, "BUYING_POWER")
    authority = "earnings-paper-exception" if arm == "earnings" and overnight else "flat-by-close"
    return SharedDecision(True, "AUTHORIZED", overnight_authority=authority)
