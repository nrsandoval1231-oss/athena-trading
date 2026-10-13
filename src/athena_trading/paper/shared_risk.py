"""One paper risk view for both arms.

Each arm keeps its own approved caps. This gate adds only the account facts
both books can already see: a symbol cannot be reserved twice, and combined
reserved notional cannot exceed buying power. Correlation, concentration,
weekly and monthly loss, portfolio drawdown, and overnight stress have no
approved threshold. Those checks are listed and do not authorize exposure.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..athena_adapter.contracts import canonical_hash
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
    acknowledged: bool = False


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


# A filled or otherwise finished parent releases shares that never filled.
_TERMINAL_RELEASE = {"filled", "canceled", "expired", "rejected", "replaced", "done_for_day"}
_WORKING = {
    "new", "accepted", "pending_new", "accepted_for_bidding", "partially_filled",
    "pending_cancel", "pending_replace", "held", "stopped", "suspended", "calculated",
}


class SharedExposureUnreadable(ValueError):
    """Peer exposure cannot be proven. New entries stop; open protection does not."""


def _malformed(detail: str) -> SharedExposureUnreadable:
    return SharedExposureUnreadable(f"malformed exposure: {detail}")


def _leg_fills(order: dict[str, Any], parent: dict[str, Any]) -> float:
    exited = 0.0
    legs = parent.get("legs") or []
    if not isinstance(legs, list):
        raise _malformed("legs")
    for leg in legs:
        if not isinstance(leg, dict) or leg.get("side") == order.get("side"):
            continue
        if leg.get("side") not in {"buy", "sell"}:
            raise _malformed("leg side")
        if leg.get("filled_qty") in {None, ""}:
            filled = 0.0
        else:
            filled = _number(leg.get("filled_qty"))
            if filled is None or filled < 0:
                raise _malformed("leg fill")
        exited += filled
    return exited


def _remaining_qty(order: dict[str, Any], parent: dict[str, Any] | None) -> float:
    """Working size is the unfilled remainder plus shares still held.

    Capacity is released only when the broker order is terminal. A zero fill on
    a live order reserves the whole quantity. Missing fill evidence is not zero.
    """
    reserved = _number(order.get("qty"))
    if reserved is None or reserved < 0:
        raise _malformed("quantity")
    if parent is None:
        return reserved
    if parent.get("filled_qty") in {None, ""}:
        raise _malformed("fill")
    entered = _number(parent.get("filled_qty"))
    if entered is None or entered < 0 or entered > reserved:
        raise _malformed("fill")
    exited = _leg_fills(order, parent)
    if exited > entered:
        raise _malformed("exit exceeds fill")
    status = parent.get("status")
    if status in _TERMINAL_RELEASE or (status in {None, ""} and entered == reserved):
        return entered - exited
    if status not in _WORKING:
        raise _malformed("status")
    return reserved - exited


def _acknowledged_ids(events: list[dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    for event in events:
        if event.get("kind") != "entry_ack":
            continue
        identity = event.get("client_order_id")
        if isinstance(identity, str):
            found.add(identity)
        order = event.get("order")
        if isinstance(order, dict) and isinstance(order.get("client_order_id"), str):
            found.add(order["client_order_id"])
    return found


def _exposures_from_events(events: list[dict[str, Any]], arm: str) -> list[OpenExposure]:
    """Open reservations in one event stream. Malformed evidence fails closed."""
    closed = {
        event.get("client_order_id") for event in events
        if event.get("kind") in {"position_closed", "entry_not_created", "flat_confirmed"}
        and isinstance(event.get("client_order_id"), str)
    }
    flat = next((event for event in reversed(events) if event.get("kind") == "flat_confirmed"), None)
    if flat and arm == "intraday":
        closed.update(
            event["order"]["client_order_id"] for event in events
            if event.get("kind") == "entry_intent" and isinstance(event.get("order"), dict)
        )
    parents: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("kind") != "entry_observation":
            continue
        identity = event.get("client_order_id")
        order = event.get("order")
        if isinstance(identity, str) and isinstance(order, dict):
            parents[identity] = order
    acknowledged = _acknowledged_ids(events)
    open_rows: list[OpenExposure] = []
    seen: set[str] = set()
    for event in events:
        if event.get("kind") != "entry_intent":
            continue
        order = event.get("order")
        if not isinstance(order, dict):
            raise _malformed("entry")
        identity = order.get("client_order_id")
        if not isinstance(identity, str) or identity in closed or identity in seen:
            continue
        seen.add(identity)
        qty = _remaining_qty(order, parents.get(identity))
        price = _number(order.get("limit_price"))
        stop_loss = order.get("stop_loss") or {}
        stop = _number(stop_loss.get("stop_price") if isinstance(stop_loss, dict) else None)
        if price is None or stop is None or price <= 0:
            raise _malformed("price")
        if qty == 0:
            continue
        open_rows.append(OpenExposure(
            arm=arm, symbol=str(order.get("symbol") or ""), notional=price * qty,
            stop_risk=abs(price - stop) * qty, overnight=arm == "earnings",
            client_order_id=identity, acknowledged=identity in acknowledged,
        ))
    return open_rows


def exposures_from_journal(journal: PaperJournal, arm: str) -> list[OpenExposure]:
    """Open reservations in one journal. A finished fill keeps only residual shares."""
    return _exposures_from_events(journal.events, arm)


class SharedBookBusy(ValueError):
    """The other arm's journal is locked. Exposure is unknown, so new entries stop."""


def _read_events(path: Path) -> list[dict[str, Any]]:
    """Read a committed hash chain without the owning runner's writer lock.

    The sqlite file is opened read-only. This does not create the lock file,
    write a checkpoint, or change permissions. An unreadable or broken chain
    fails closed instead of looking like an empty book.
    """
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True, timeout=1.0)
    except sqlite3.Error as exc:
        raise SharedExposureUnreadable(f"peer exposure is unreadable: {path}") from exc
    try:
        try:
            rows = list(db.execute(
                "SELECT seq, payload, previous, digest FROM events ORDER BY seq"
            ))
        except sqlite3.Error as exc:
            raise SharedExposureUnreadable(f"peer exposure is unreadable: {path}") from exc
    finally:
        db.close()
    events: list[dict[str, Any]] = []
    head = "0" * 64
    for seq, payload, previous, digest in rows:
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SharedExposureUnreadable(f"peer exposure is unreadable: {path}") from exc
        expected = canonical_hash({"seq": seq, "event": event, "previous": previous})
        if seq != len(events) + 1 or previous != head or digest != expected:
            raise SharedExposureUnreadable(f"peer exposure hash chain failed: {path}")
        if not isinstance(event, dict):
            raise SharedExposureUnreadable(f"peer exposure is unreadable: {path}")
        events.append(event)
        head = digest
    return events


def _read_journal(path: Path, arm: str) -> list[OpenExposure]:
    if not path.exists():
        return []
    return _exposures_from_events(_read_events(path), arm)


def reflected_exposure(snapshot: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Client order ids and position symbols already inside one broker snapshot."""
    ids: set[str] = set()

    def walk(order: Any) -> None:
        if not isinstance(order, dict):
            return
        identity = order.get("client_order_id")
        if isinstance(identity, str) and identity:
            ids.add(identity)
        legs = order.get("legs") or []
        if isinstance(legs, list):
            for leg in legs:
                walk(leg)

    orders = snapshot.get("orders") or []
    if isinstance(orders, list):
        for order in orders:
            walk(order)
    symbols: set[str] = set()
    positions = snapshot.get("positions") or []
    if isinstance(positions, list):
        for position in positions:
            if isinstance(position, dict) and isinstance(position.get("symbol"), str):
                symbols.add(position["symbol"])
    return ids, symbols


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


def _already_in_buying_power(
    item: OpenExposure, *, reflected_client_order_ids: set[str], reflected_symbols: set[str],
) -> bool:
    """Broker buying power already deducts an acknowledged order visible in that snapshot."""
    if not item.acknowledged:
        return False
    return item.client_order_id in reflected_client_order_ids or (
        bool(item.symbol) and item.symbol in reflected_symbols
    )


def authorize_entry(
    *, arm: str, symbol: str, notional: float, stop_risk: float, overnight: bool,
    buying_power: float, open_exposure: list[OpenExposure],
    reflected_client_order_ids: set[str] | None = None,
    reflected_symbols: set[str] | None = None,
) -> SharedDecision:
    """Refuse a new entry that spends capacity the other arm already reserved.

    ``buying_power`` is the broker figure from one snapshot. Acknowledged orders
    visible in that same snapshot are not added again. A local reservation the
    snapshot does not show, including a stale snapshot or a concurrent entry,
    still counts. Unresolved portfolio checks stay inactive.
    """
    if arm not in {"intraday", "earnings"}:
        return SharedDecision(False, "UNKNOWN_ARM")
    if overnight and arm != "earnings":
        return SharedDecision(False, "OVERNIGHT_NOT_AUTHORIZED")
    if notional < 0 or stop_risk < 0 or buying_power < 0:
        return SharedDecision(False, "INVALID_EXPOSURE")
    if any(item.symbol == symbol and item.symbol for item in open_exposure):
        return SharedDecision(False, "SYMBOL_ALREADY_RESERVED")
    reflected_ids = reflected_client_order_ids or set()
    visible_symbols = reflected_symbols or set()
    used = sum(
        item.notional for item in open_exposure
        if not _already_in_buying_power(
            item, reflected_client_order_ids=reflected_ids, reflected_symbols=visible_symbols,
        )
    )
    if used + notional > buying_power:
        return SharedDecision(False, "BUYING_POWER")
    authority = "earnings-paper-exception" if arm == "earnings" and overnight else "flat-by-close"
    return SharedDecision(True, "AUTHORIZED", overnight_authority=authority)
