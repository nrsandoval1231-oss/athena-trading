"""Paper broker port: in-memory fixture plus Alpaca paper-only adapter.

LIVE trading is not implemented. The Alpaca adapter is pinned to
https://paper-api.alpaca.markets and rejects any other host.
Credentials come from ALPACA_API_KEY / ALPACA_API_SECRET only.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse

from astra_desk.errors import BrokerCredentialsError, BrokerError, LiveTradingDisabled
from astra_desk.types import BrokerOrder

PAPER_API_BASE = "https://paper-api.alpaca.markets"
PAPER_API_HOST = "paper-api.alpaca.markets"
LIVE_API_HOSTS = frozenset({"api.alpaca.markets", "api.alpaca.markets."})
WORKING_STATUSES = frozenset(
    {"new", "accepted", "pending_new", "pending_cancel", "partially_filled", "held", "accepted_for_bidding"}
)
FILLED_STATUSES = frozenset({"filled"})
ENV_API_KEY = "ALPACA_API_KEY"
ENV_API_SECRET = "ALPACA_API_SECRET"
HTTP_TIMEOUT_SEC = 10.0

Transport = Callable[[str, str, dict[str, Any] | None, dict[str, str]], tuple[int, Any]]


def require_paper_base_url(url: str) -> str:
    """Fail closed unless the URL is exactly the Alpaca paper HTTPS host."""
    raw = (url or "").strip()
    if not raw:
        raise LiveTradingDisabled("broker base URL is required and must be the paper host")
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host.endswith(":443"):
        host = host[:-4]
    if parsed.scheme != "https":
        raise LiveTradingDisabled(
            f"non-paper broker URL rejected (https paper host required): {raw}"
        )
    if host in LIVE_API_HOSTS or host.startswith("api.alpaca.markets"):
        raise LiveTradingDisabled(f"live Alpaca host rejected: {host}")
    if host != PAPER_API_HOST:
        raise LiveTradingDisabled(f"non-paper broker host rejected: {host}")
    path = parsed.path or ""
    if path not in {"", "/"}:
        raise LiveTradingDisabled(f"non-paper broker path rejected: {path}")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise LiveTradingDisabled("broker URL must be the bare paper API host")
    return PAPER_API_BASE


class PaperBroker(Protocol):
    name: str

    def submit(
        self,
        *,
        symbol: str,
        side: str,
        notional_usd: float,
        client_order_id: str | None = None,
    ) -> BrokerOrder: ...

    def cancel(self, order_id: str) -> BrokerOrder: ...

    def track(self, order_id: str) -> BrokerOrder: ...

    def flatten(self) -> tuple[BrokerOrder, ...]: ...

    def preflight(self) -> dict[str, Any]: ...


class FixtureBroker:
    """In-memory paper broker. No network, no keys. Default CI path."""

    name = "fixture"

    def __init__(self, *, fill_on_track: bool = True) -> None:
        self.fill_on_track = fill_on_track
        self._orders: dict[str, BrokerOrder] = {}
        self._seq = 0

    def submit(
        self,
        *,
        symbol: str,
        side: str,
        notional_usd: float,
        client_order_id: str | None = None,
    ) -> BrokerOrder:
        if notional_usd <= 0:
            raise BrokerError("notional_usd must be > 0")
        if not symbol.strip():
            raise BrokerError("symbol is required")
        self._seq += 1
        order = BrokerOrder(
            order_id=f"fix-{self._seq:04d}",
            symbol=str(symbol),
            side=str(side or "long"),
            notional_usd=float(notional_usd),
            status="accepted",
            broker=self.name,
            client_order_id=client_order_id,
            filled_qty=0.0,
            paper=True,
            live_trading_authority=False,
        )
        self._orders[order.order_id] = order
        return order

    def cancel(self, order_id: str) -> BrokerOrder:
        order = self._require(order_id)
        if order.status in FILLED_STATUSES:
            raise BrokerError(f"cannot cancel filled order {order_id}")
        if order.status == "canceled":
            return order
        updated = replace(order, status="canceled")
        self._orders[order_id] = updated
        return updated

    def track(self, order_id: str) -> BrokerOrder:
        order = self._require(order_id)
        if self.fill_on_track and order.status in WORKING_STATUSES:
            filled = replace(order, status="filled", filled_qty=1.0)
            self._orders[order_id] = filled
            return filled
        return order

    def flatten(self) -> tuple[BrokerOrder, ...]:
        canceled: list[BrokerOrder] = []
        for order_id, order in list(self._orders.items()):
            if order.status in WORKING_STATUSES:
                canceled.append(self.cancel(order_id))
        return tuple(canceled)

    def preflight(self) -> dict[str, Any]:
        working = sum(1 for order in self._orders.values() if order.status in WORKING_STATUSES)
        return {
            "broker": self.name,
            "paper": True,
            "live_trading_authority": False,
            "base_url": "memory://fixture",
            "working_orders": working,
            "account": {"status": "ACTIVE", "trading_blocked": False},
        }

    def _require(self, order_id: str) -> BrokerOrder:
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise BrokerError(f"unknown fixture order {order_id}") from exc


class AlpacaPaperBroker:
    """Alpaca paper adapter. HTTPS paper host only. No live trading authority."""

    name = "alpaca_paper"

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = PAPER_API_BASE,
        transport: Transport | None = None,
    ) -> None:
        if not (api_key or "").strip() or not (api_secret or "").strip():
            raise BrokerCredentialsError(
                f"{ENV_API_KEY} and {ENV_API_SECRET} are required for alpaca-paper"
            )
        self.base_url = require_paper_base_url(base_url)
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._transport = transport or _urllib_transport

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        transport: Transport | None = None,
    ) -> AlpacaPaperBroker:
        env = environ if environ is not None else os.environ
        key = str(env.get(ENV_API_KEY) or "").strip()
        secret = str(env.get(ENV_API_SECRET) or "").strip()
        if not key or not secret:
            raise BrokerCredentialsError(
                f"{ENV_API_KEY} and {ENV_API_SECRET} are required for alpaca-paper"
            )
        base = str(env.get("ALPACA_BASE_URL") or PAPER_API_BASE).strip()
        return cls(api_key=key, api_secret=secret, base_url=base, transport=transport)

    def submit(
        self,
        *,
        symbol: str,
        side: str,
        notional_usd: float,
        client_order_id: str | None = None,
    ) -> BrokerOrder:
        if notional_usd <= 0:
            raise BrokerError("notional_usd must be > 0")
        payload: dict[str, Any] = {
            "symbol": symbol,
            "notional": str(notional_usd),
            "side": "buy" if str(side).lower() in {"long", "buy"} else "sell",
            "type": "market",
            "time_in_force": "day",
        }
        if client_order_id:
            payload["client_order_id"] = client_order_id
        _status, body = self._request("POST", "/v2/orders", payload)
        return self._order_from_payload(body)

    def cancel(self, order_id: str) -> BrokerOrder:
        self._request("DELETE", f"/v2/orders/{order_id}")
        return self.track(order_id)

    def track(self, order_id: str) -> BrokerOrder:
        _status, body = self._request("GET", f"/v2/orders/{order_id}")
        return self._order_from_payload(body)

    def flatten(self) -> tuple[BrokerOrder, ...]:
        _status, body = self._request("DELETE", "/v2/orders")
        if isinstance(body, list):
            return tuple(self._order_from_payload(item) for item in body if isinstance(item, Mapping))
        return ()

    def preflight(self) -> dict[str, Any]:
        _status, body = self._request("GET", "/v2/account")
        if not isinstance(body, Mapping):
            raise BrokerError("alpaca paper preflight returned a non-object")
        return {
            "broker": self.name,
            "paper": True,
            "live_trading_authority": False,
            "base_url": self.base_url,
            "account": {
                "status": body.get("status"),
                "trading_blocked": bool(body.get("trading_blocked")),
                "account_blocked": bool(body.get("account_blocked")),
                "pattern_day_trader": bool(body.get("pattern_day_trader")),
            },
        }

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        try:
            status, body = self._transport(method, url, payload, self._headers())
        except BrokerError:
            raise
        except Exception as exc:
            raise BrokerError(f"alpaca paper request failed: {method} {path}") from exc
        if status >= 400:
            raise BrokerError(f"alpaca paper request failed: {method} {path} status={status}")
        return status, body

    def _order_from_payload(self, body: Any) -> BrokerOrder:
        if not isinstance(body, Mapping):
            raise BrokerError("alpaca paper order payload must be an object")
        order_id = str(body.get("id") or "")
        if not order_id:
            raise BrokerError("alpaca paper order is missing id")
        side_raw = str(body.get("side") or "buy").lower()
        side = "long" if side_raw in {"buy", "long"} else "short"
        notional = body.get("notional")
        try:
            notional_usd = float(notional) if notional is not None else 0.0
        except (TypeError, ValueError):
            notional_usd = 0.0
        try:
            filled_qty = float(body.get("filled_qty") or 0.0)
        except (TypeError, ValueError):
            filled_qty = 0.0
        return BrokerOrder(
            order_id=order_id,
            symbol=str(body.get("symbol") or ""),
            side=side,
            notional_usd=notional_usd,
            status=str(body.get("status") or "unknown"),
            broker=self.name,
            client_order_id=(
                str(body["client_order_id"]) if body.get("client_order_id") else None
            ),
            filled_qty=filled_qty,
            paper=True,
            live_trading_authority=False,
        )


def _urllib_transport(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
) -> tuple[int, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower().split(":")[0] != PAPER_API_HOST:
        raise LiveTradingDisabled(f"refusing non-paper transport URL: {parsed.netloc}")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as response:
            raw = response.read()
            status = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read() if exc.fp is not None else b""
        body: Any = {}
        if raw:
            try:
                body = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                body = {}
        raise BrokerError(f"alpaca paper request failed: {method} {parsed.path} status={status}") from None
    if not raw:
        return status, {}
    try:
        return status, json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise BrokerError("alpaca paper response was not JSON") from exc


def build_paper_broker(
    kind: str = "fixture",
    *,
    environ: Mapping[str, str] | None = None,
    transport: Transport | None = None,
    broker: PaperBroker | None = None,
) -> PaperBroker:
    """Default is the in-memory fixture. Alpaca paper is opt-in."""
    if broker is not None:
        return broker
    name = (kind or "fixture").strip().lower().replace("_", "-")
    if name in {"live", "alpaca-live", "alpaca", "live-broker"}:
        raise LiveTradingDisabled("live broker is not implemented; live_trading_authority stays false")
    if name in {"fixture", "paper", "memory"}:
        return FixtureBroker()
    if name in {"alpaca-paper"}:
        return AlpacaPaperBroker.from_env(environ, transport=transport)
    raise BrokerError(f"unknown paper broker {kind!r}; use fixture or alpaca-paper")
