"""Which paper positions and orders belong to which arm.

The intraday runner and the earnings runner can share one Alpaca paper account.
Intraday client order ids start with ``athp-``. Earnings ids start with ``athe-``.
A position is earnings-owned when an earnings order (or a leg of one) names its
symbol, or when the earnings ledger still lists that symbol. The intraday arm
never treats its own symbol as foreign: a collision stays visible and blocks
account-wide cleanup.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

EARNINGS_ORDER_PREFIX = "athe-"
INTRADAY_ORDER_PREFIX = "athp-"
OWNERSHIP_FILENAME = "ownership.json"


def earnings_state_dir() -> Path | None:
    """Directory the earnings arm uses. Unset means the ledger is not consulted."""
    raw = os.environ.get("ATHENA_EARNINGS_STATE_DIR", "").strip()
    if not raw:
        return None
    return Path(raw)


def _prefixed(order: dict[str, Any], prefix: str) -> bool:
    client_order_id = order.get("client_order_id")
    return isinstance(client_order_id, str) and client_order_id.startswith(prefix)


def symbols_for_prefix(orders: Any, prefix: str) -> set[str]:
    """Symbols on orders with this prefix, including nested legs of those parents."""
    found: set[str] = set()

    def walk(order: Any, inherited: bool) -> None:
        if not isinstance(order, dict):
            return
        owned = inherited or _prefixed(order, prefix)
        symbol = order.get("symbol")
        if owned and isinstance(symbol, str) and symbol:
            found.add(symbol)
        legs = order.get("legs") or []
        if isinstance(legs, list):
            for leg in legs:
                walk(leg, owned)

    if isinstance(orders, list):
        for order in orders:
            walk(order, False)
    return found


def load_ownership_symbols(state_dir: Path | None) -> set[str]:
    """Symbols the earnings arm says it still holds. A missing file is an empty book.

    A present but unreadable file fails closed. Ignoring it could let the other
    arm flatten those positions.
    """
    if state_dir is None:
        return set()
    path = state_dir / OWNERSHIP_FILENAME
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("earnings ownership ledger is unreadable") from exc
    if not isinstance(payload, dict):
        raise ValueError("earnings ownership ledger is unreadable")
    if payload.get("arm") != "earnings" or payload.get("prefix") != EARNINGS_ORDER_PREFIX:
        raise ValueError("earnings ownership ledger is not the earnings arm")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or any(type(symbol) is not str or not symbol for symbol in symbols):
        raise ValueError("earnings ownership ledger symbols are invalid")
    return set(symbols)


def write_ownership_ledger(state_dir: Path, symbols: set[str], orders: list[dict[str, str]]) -> None:
    """Atomically publish the symbols and client order ids the earnings arm owns."""
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "arm": "earnings",
        "prefix": EARNINGS_ORDER_PREFIX,
        "symbols": sorted(symbols),
        "orders": orders,
    }
    path = state_dir / OWNERSHIP_FILENAME
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n")
    temporary.replace(path)


def intraday_book(
    positions: Any, orders: Any, *, own_symbol: str, ledger_symbols: set[str],
) -> tuple[list[Any], list[Any]]:
    """Drop earnings-owned positions and orders. Keep every other symbol.

    ``own_symbol`` is never dropped. If both arms touch it, the intraday arm
    still sees the exposure and refuses to flatten the account.
    """
    if not isinstance(positions, list) or not isinstance(orders, list):
        raise ValueError("broker positions and orders are incomplete")
    foreign = symbols_for_prefix(orders, EARNINGS_ORDER_PREFIX) | set(ledger_symbols)
    foreign.discard(own_symbol)
    kept_positions = [item for item in positions if item.get("symbol") not in foreign]
    kept_orders = [item for item in orders if item.get("symbol") not in foreign]
    return kept_positions, kept_orders
