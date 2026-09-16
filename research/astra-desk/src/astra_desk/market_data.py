"""Paper market-data port: fixture tape plus optional Alpaca data fetch.

This is data in, not orders out. The broker port stays separate.
LIVE trading hosts are rejected. CI uses the fixture tape only.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from astra_desk.errors import (
    BrokerCredentialsError,
    LiveTradingDisabled,
    MarketDataError,
)
from astra_desk.types import Bar

ENV_API_KEY = "ALPACA_API_KEY"
ENV_API_SECRET = "ALPACA_API_SECRET"
DATA_SANDBOX_BASE = "https://data.sandbox.alpaca.markets"
DATA_PROD_BASE = "https://data.alpaca.markets"
ALLOWED_DATA_HOSTS = frozenset({"data.sandbox.alpaca.markets", "data.alpaca.markets"})
LIVE_TRADE_HOSTS = frozenset({"api.alpaca.markets", "paper-api.alpaca.markets"})
HTTP_TIMEOUT_SEC = 10.0

Transport = Callable[[str, str, dict[str, Any] | None, dict[str, str]], tuple[int, Any]]


def require_data_base_url(url: str) -> str:
    """Fail closed unless the URL is an Alpaca *data* host (not a trading host)."""
    raw = (url or "").strip()
    if not raw:
        raise LiveTradingDisabled("market-data base URL is required")
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host.endswith(":443"):
        host = host[:-4]
    if parsed.scheme != "https":
        raise LiveTradingDisabled(f"market-data URL must be https: {raw}")
    if host in LIVE_TRADE_HOSTS or host.startswith("api.alpaca.markets"):
        raise LiveTradingDisabled(
            f"trading host rejected for market data (broker is a separate port): {host}"
        )
    if host not in ALLOWED_DATA_HOSTS:
        raise LiveTradingDisabled(f"non-paper market-data host rejected: {host}")
    path = parsed.path or ""
    if path not in {"", "/"}:
        raise LiveTradingDisabled(f"market-data path rejected: {path}")
    if parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise LiveTradingDisabled("market-data URL must be the bare data host")
    return f"https://{host}"


class AlpacaDataFeed:
    """Optional Alpaca bars fetch. Paper/sandbox data default. No order routing."""

    name = "alpaca_data"

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = DATA_SANDBOX_BASE,
        transport: Transport | None = None,
    ) -> None:
        if not (api_key or "").strip() or not (api_secret or "").strip():
            raise BrokerCredentialsError(
                f"{ENV_API_KEY} and {ENV_API_SECRET} are required for alpaca data"
            )
        self.base_url = require_data_base_url(base_url)
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._transport = transport or _urllib_transport

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        transport: Transport | None = None,
    ) -> AlpacaDataFeed:
        env = environ if environ is not None else os.environ
        key = str(env.get(ENV_API_KEY) or "").strip()
        secret = str(env.get(ENV_API_SECRET) or "").strip()
        if not key or not secret:
            raise BrokerCredentialsError(
                f"{ENV_API_KEY} and {ENV_API_SECRET} are required for alpaca data"
            )
        base = str(env.get("ALPACA_DATA_URL") or DATA_SANDBOX_BASE).strip()
        return cls(api_key=key, api_secret=secret, base_url=base, transport=transport)

    def fetch_bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1Min",
        limit: int = 50,
    ) -> tuple[Bar, ...]:
        if not symbol.strip():
            raise MarketDataError("symbol is required")
        query = urllib.parse.urlencode(
            {"timeframe": timeframe, "limit": int(limit), "adjustment": "raw"}
        )
        path = f"/v2/stocks/{urllib.parse.quote(symbol)}/bars?{query}"
        _status, body = self._request("GET", path)
        raw_bars = body.get("bars") if isinstance(body, Mapping) else None
        if not isinstance(raw_bars, list) or not raw_bars:
            raise MarketDataError(f"{symbol}: alpaca data returned no bars")
        bars: list[Bar] = []
        for item in raw_bars:
            if not isinstance(item, Mapping):
                raise MarketDataError(f"{symbol}: bar must be an object")
            try:
                bars.append(parse_bar(item, symbol))
            except MarketDataError:
                raise
            except (KeyError, TypeError, ValueError) as exc:
                raise MarketDataError(f"{symbol}: invalid alpaca bar: {exc}") from exc
        if not bars:
            raise MarketDataError(f"{symbol}: alpaca data returned no bars")
        return tuple(bars)

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._api_key,
            "APCA-API-SECRET-KEY": self._api_secret,
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        try:
            status, body = self._transport(method, url, None, self._headers())
        except (MarketDataError, LiveTradingDisabled, BrokerCredentialsError):
            raise
        except Exception as exc:
            raise MarketDataError(f"alpaca data request failed: {method} {path.split('?', 1)[0]}") from exc
        if status >= 400:
            raise MarketDataError(
                f"alpaca data request failed: {method} {path.split('?', 1)[0]} status={status}"
            )
        return status, body


def _urllib_transport(
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
) -> tuple[int, Any]:
    parsed = urlparse(url)
    host = parsed.netloc.lower().split(":")[0]
    if parsed.scheme != "https" or host not in ALLOWED_DATA_HOSTS:
        raise LiveTradingDisabled(f"refusing non-data transport URL: {parsed.netloc}")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SEC) as response:
            raw = response.read()
            status = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raise MarketDataError(
            f"alpaca data request failed: {method} {parsed.path} status={status}"
        ) from None
    if not raw:
        return status, {}
    try:
        return status, json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise MarketDataError("alpaca data response was not JSON") from exc


def parse_bar(raw: Mapping[str, Any], setup_id: str) -> Bar:
    try:
        bar = Bar(
            ts=str(raw["ts"] if "ts" in raw else raw.get("t") or ""),
            open=float(raw["open"] if "open" in raw else raw["o"]),
            high=float(raw["high"] if "high" in raw else raw["h"]),
            low=float(raw["low"] if "low" in raw else raw["l"]),
            close=float(raw["close"] if "close" in raw else raw["c"]),
            volume=float(raw.get("volume", raw.get("v", 0.0))),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketDataError(f"{setup_id}: invalid bar: {exc}") from exc
    if bar.high < bar.low:
        raise MarketDataError(f"{setup_id}: invalid bar {bar.ts}: high < low")
    if bar.open <= 0 or bar.close <= 0:
        raise MarketDataError(f"{setup_id}: bar prices must be > 0")
    return bar
