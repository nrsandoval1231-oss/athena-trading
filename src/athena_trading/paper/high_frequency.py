"""Opt-in high-frequency paper profile.

The default runner stays one ORB entry per session. This profile is selected
with ``ATHENA_PAPER_PROFILE=high-frequency`` and is paper-only: startup refuses
any broker whose endpoint is not ``https://paper-api.alpaca.markets``.

Entries are capped marketable limit brackets. The submitted stop and target
start a full stop-cap away from the limit so a better fill cannot cross them.
Once the fill is known, both legs are replaced around that fill, at least the
minimum distance and never on the wrong side. Exits may close a position the
way the existing flatten path does. Jev is not on the entry path, so model
latency cannot cap the trade count. Per-position notional and stop ceilings
match the one-trade pilot. Concurrency, a session round-trip cap, the 0.5%
daily-loss halt, and the pre-close flatten are what keep the faster cadence
inside a paper book. A leftover bracket leg from an order this profile sent
is not unowned exposure.
"""
from __future__ import annotations

import json
import os
import random
import re
import signal
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from ..athena_adapter.contracts import canonical_hash
from ..athena_adapter.jev import ModelError, PatternDayTradeError
from ..execution.authority import ExecutionAuthority, parse_authority, require_paper
from .alpaca import PAPER_API_BASE
from .journal import PaperJournal
from .live import (
    ENTRY_LIMIT_OFFSET,
    EntryNotPriced,
    LivePaperAPI,
    NY,
    _cents,
    _decimal_price,
    _money,
    assess_quote,
    bracket_exit_reason,
    number,
    price_marketable_entry,
    protective_stop,
    timestamp,
    working_orders,
)
from .ownership import (
    EARNINGS_ORDER_PREFIX,
    earnings_state_dir,
    load_ownership_symbols,
    symbols_for_prefix,
)
from .shared_risk import (
    SharedBookBusy,
    SharedExposureUnreadable,
    authorize_entry,
    entry_coordination,
    other_arm_exposure,
    reflected_exposure,
)

PROFILE_HIGH_FREQUENCY = "high-frequency"
DEFAULT_SYMBOLS = ("SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "AMZN")
REGULAR_SESSION_MINUTES = 390
TERMINAL = {"filled", "canceled", "expired", "rejected", "replaced"}
ROUND_TRIP_REASONS = {
    "STOP_FILLED", "TAKE_PROFIT_FILLED", "TIME_STOP", "SESSION_FLATTEN",
    "DAILY_LOSS_FLATTEN", "PROTECTION_MISSING", "POSITION_FLAT",
}
# Alpaca's published paper trading cap is 200 requests per minute. Stay under it.
CALL_BUDGET_PER_MINUTE = 150
# A just-closed bracket can still show a pending-cancel sibling in the same snapshot.
UNOWNED_RECHECK_SECONDS = 15
_SYMBOL = r"[A-Z][A-Z0-9.\-]{0,9}"
_TYPICAL_STARTS = {
    "SPY": 580.0, "QQQ": 490.0, "IWM": 220.0, "DIA": 430.0,
    "AAPL": 230.0, "MSFT": 420.0, "NVDA": 140.0, "AMZN": 190.0,
}
_VOL_SCALE = {
    "SPY": 0.8, "DIA": 0.8, "QQQ": 1.0, "AAPL": 1.0, "MSFT": 1.0,
    "AMZN": 1.0, "IWM": 1.3, "NVDA": 1.3,
}


class EntryBlocked(Exception):
    """This signal is not sent. A flat book is not halted."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def require_paper_endpoint(broker: Any) -> None:
    """Refuse to run unless this process is pinned to the Alpaca paper host."""
    base = getattr(broker, "_base", None)
    if not isinstance(base, str) or base.strip().rstrip("/") != PAPER_API_BASE:
        raise ValueError(
            "high-frequency mode refuses to start unless the broker endpoint is "
            "https://paper-api.alpaca.markets"
        )


def require_shared_risk_dir() -> None:
    raw = os.environ.get("ATHENA_SHARED_RISK_DIR", "").strip()
    if not raw or not os.path.isabs(raw):
        raise ValueError("ATHENA_SHARED_RISK_DIR must name an absolute shared directory")


@dataclass(frozen=True)
class HighFrequencyPolicy:
    """Paper cadence sized for about 100 round trips. Ceilings are not overridable."""

    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    feed: str = "iex"
    profile: str = PROFILE_HIGH_FREQUENCY
    max_notional: float = 1000.0
    max_notional_fraction: float = 0.01
    max_stop_risk: float = 10.0
    max_stop_risk_fraction: float = 0.001
    max_daily_loss_fraction: float = 0.005
    max_concurrent: int = 4
    max_round_trips: int = 140
    max_gross_notional: float = 4000.0
    lookback_bars: int = 5
    momentum_threshold: float = 0.0008
    min_stop: float = 0.08
    target_r: float = 1.0
    max_hold_minutes: int = 8
    cooldown_minutes: int = 25
    warmup_minutes: int = 10
    entry_cutoff_minutes: int = 30
    close_buffer_minutes: int = 15
    max_bar_age_seconds: int = 90
    entry_timeout_seconds: int = 45
    jev_mode: str = "off"
    max_entry_slippage: str = "0.15"
    max_limit_concession: str = "0.17"
    version: str = "athena-hf-paper-v1"

    def __post_init__(self) -> None:
        symbols = self.symbols
        if isinstance(symbols, str) or not isinstance(symbols, (tuple, list)):
            raise ValueError("high-frequency symbols must be a list of tickers")
        cleaned = tuple(symbols)
        if any(type(symbol) is not str for symbol in cleaned):
            raise ValueError("high-frequency symbols must be a list of tickers")
        object.__setattr__(self, "symbols", cleaned)
        if not 4 <= len(cleaned) <= 12 or len(set(cleaned)) != len(cleaned):
            raise ValueError("high-frequency mode needs 4 to 12 distinct symbols")
        if any(re.fullmatch(_SYMBOL, symbol) is None for symbol in cleaned):
            raise ValueError("high-frequency symbols must be explicit uppercase equities")
        if self.profile != PROFILE_HIGH_FREQUENCY:
            raise ValueError("high-frequency profile is opt-in")
        if self.feed not in {"iex", "sip"}:
            raise ValueError("invalid feed")
        if self.version != "athena-hf-paper-v1":
            raise ValueError("unknown high-frequency policy version")
        if not 0 < number(self.max_notional) <= 1000:
            raise ValueError("per-position notional cap is at most $1,000")
        if not 0 < number(self.max_notional_fraction) <= 0.01:
            raise ValueError("per-position notional fraction is at most 1%")
        if not 0 < number(self.max_stop_risk) <= 10:
            raise ValueError("per-position stop risk is at most $10")
        if not 0 < number(self.max_stop_risk_fraction) <= 0.001:
            raise ValueError("per-position stop fraction is at most 0.1%")
        if not 0 < number(self.max_daily_loss_fraction) <= 0.005:
            raise ValueError("daily account loss cap must be at most 0.5%")
        if number(self.max_gross_notional) != 4000:
            raise ValueError("gross notional cap is $4,000")
        if type(self.max_concurrent) is not int or not 1 <= self.max_concurrent <= 4:
            raise ValueError("at most 4 concurrent paper positions")
        if type(self.max_round_trips) is not int or not 1 <= self.max_round_trips <= 160:
            raise ValueError("session round-trip cap must be 1 to 160")
        if type(self.lookback_bars) is not int or not 2 <= self.lookback_bars <= 20:
            raise ValueError("invalid momentum lookback")
        if not 0 < number(self.momentum_threshold) <= 0.02:
            raise ValueError("invalid momentum threshold")
        if not 0.02 <= number(self.min_stop) <= number(self.max_stop_risk):
            raise ValueError("invalid minimum stop")
        if number(self.target_r) != 1:
            raise ValueError("high-frequency target is 1R")
        if type(self.max_hold_minutes) is not int or not 1 <= self.max_hold_minutes <= 30:
            raise ValueError("invalid max hold")
        if type(self.cooldown_minutes) is not int or not 1 <= self.cooldown_minutes <= 60:
            raise ValueError("invalid cooldown")
        if type(self.warmup_minutes) is not int or not self.lookback_bars <= self.warmup_minutes <= 30:
            raise ValueError("invalid warmup")
        if type(self.entry_cutoff_minutes) is not int or not (
            self.close_buffer_minutes <= self.entry_cutoff_minutes <= 90
        ):
            raise ValueError("entry cutoff must end before the flatten buffer")
        if type(self.close_buffer_minutes) is not int or self.close_buffer_minutes < 15:
            raise ValueError("flatten buffer must be at least 15 minutes")
        if type(self.max_bar_age_seconds) is not int or not 1 <= self.max_bar_age_seconds <= 90:
            raise ValueError("invalid bar age")
        if type(self.entry_timeout_seconds) is not int or not 15 <= self.entry_timeout_seconds <= 60:
            raise ValueError("invalid entry timeout")
        if self.jev_mode not in {"off", "advisory"}:
            raise ValueError("ATHENA_HF_JEV must be off or advisory")
        slip = _decimal_price(self.max_entry_slippage)
        concession = _decimal_price(self.max_limit_concession)
        if slip != Decimal("0.15") or concession != Decimal("0.17"):
            raise ValueError("high-frequency entry concession is capped at fifteen cents")


def high_frequency_policy_from_environment(feed: str = "iex") -> HighFrequencyPolicy:
    """Build the opt-in profile. Environment cannot raise the risk ceilings."""
    raw_symbols = os.environ.get("ATHENA_HF_SYMBOLS", "").strip()
    symbols: tuple[str, ...] | list[str]
    if raw_symbols:
        symbols = tuple(part.strip().upper() for part in raw_symbols.split(",") if part.strip())
    else:
        symbols = DEFAULT_SYMBOLS
    raw_concurrent = os.environ.get("ATHENA_HF_MAX_CONCURRENT", "").strip()
    concurrent = 4
    if raw_concurrent:
        try:
            concurrent = int(raw_concurrent)
        except ValueError as exc:
            raise ValueError("ATHENA_HF_MAX_CONCURRENT must be a whole number") from exc
    jev_mode = os.environ.get("ATHENA_HF_JEV", "off").strip().lower() or "off"
    chosen_feed = feed.strip() or "iex"
    return HighFrequencyPolicy(
        symbols=symbols, feed=chosen_feed, max_concurrent=concurrent, jev_mode=jev_mode,
    )


class CallBudget:
    """Count broker calls so entries pause before Alpaca's per-minute cap."""

    def __init__(self, limit: int = CALL_BUDGET_PER_MINUTE, window: float = 60.0) -> None:
        if type(limit) is not int or limit < 1 or window <= 0:
            raise ValueError("invalid call budget")
        self.limit = limit
        self.window = window
        self.marks: list[float] = []

    def add(self, now: float, count: int = 1) -> None:
        self.marks.extend([now] * count)
        self._trim(now)

    def used(self, now: float) -> int:
        self._trim(now)
        return len(self.marks)

    def allow_entry(self, now: float) -> bool:
        return self.used(now) + 6 <= self.limit

    def _trim(self, now: float) -> None:
        cutoff = now - self.window
        if self.marks and self.marks[0] < cutoff:
            self.marks = [mark for mark in self.marks if mark >= cutoff]


def momentum_direction(
    closes: list[float], *, lookback: int, threshold: float, last_open: float,
) -> str | None:
    """Long a completed up-push, short a completed down-push. Otherwise wait."""
    if len(closes) < lookback + 1:
        return None
    previous, last = closes[-(lookback + 1)], closes[-1]
    if previous <= 0 or last <= 0:
        return None
    change = (last - previous) / previous
    if change >= threshold and last > last_open:
        return "LONG"
    if change <= -threshold and last < last_open:
        return "SHORT"
    return None


def position_caps(
    equity: float, buying_power: float, policy: HighFrequencyPolicy,
) -> tuple[float, float]:
    if equity <= 0 or buying_power < 0:
        raise EntryBlocked("ACCOUNT")
    notional = min(policy.max_notional, equity * policy.max_notional_fraction, buying_power)
    stop = min(policy.max_stop_risk, equity * policy.max_stop_risk_fraction)
    return notional, stop


def anchor_protection(
    *, side: str, fill: Decimal, risk: Decimal, min_distance: Decimal,
) -> tuple[Decimal, Decimal]:
    """Stop and target measured from the actual fill, on the correct side of it.

    A long stop is below the fill and a short stop is above it, each by at least
    ``min_distance``. The target is the same distance on the other side. This is
    the distance the runner tightens to after a fill. It is not a market order.
    """
    if side not in {"buy", "sell"}:
        raise EntryBlocked("ANCHOR_SIDE")
    fill_d = _cents(fill)
    distance = max(_cents(risk), _cents(min_distance))
    if fill_d <= 0 or distance <= 0:
        raise EntryBlocked("ANCHOR_SIDE")
    if side == "buy":
        stop = _cents(fill_d - distance)
        target = _cents(fill_d + distance)
        if stop <= 0 or not stop < fill_d < target:
            raise EntryBlocked("ANCHOR_SIDE")
    else:
        stop = _cents(fill_d + distance)
        target = _cents(fill_d - distance)
        if target <= 0 or not target < fill_d < stop:
            raise EntryBlocked("ANCHOR_SIDE")
    return stop, target


def _absorb_order_ids(order: Any, ids: set[str], client_ids: set[str]) -> None:
    """Collect a parent and its nested legs. A loose sibling still carries one of these ids."""
    if not isinstance(order, dict):
        return
    if isinstance(order.get("id"), str) and order["id"]:
        ids.add(order["id"])
    client_id = order.get("client_order_id")
    if isinstance(client_id, str) and client_id:
        client_ids.add(client_id)
    legs = order.get("legs")
    if isinstance(legs, list):
        for leg in legs:
            _absorb_order_ids(leg, ids, client_ids)


def plan_bracket(
    *,
    symbol: str,
    direction: str,
    close: float,
    high: float,
    low: float,
    bid: float,
    ask: float,
    quote_at: datetime,
    now: datetime,
    policy: HighFrequencyPolicy,
    equity: float,
    buying_power: float,
    open_notional: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Capped marketable limit bracket. Never a market entry."""
    if direction not in {"LONG", "SHORT"}:
        raise EntryBlocked("DIRECTION")
    close_d = _cents(_decimal_price(close))
    high_d = _cents(_decimal_price(high))
    low_d = _cents(_decimal_price(low))
    if low_d <= 0 or low_d > close_d or high_d < close_d:
        raise EntryBlocked("BAR_GEOMETRY")
    raw_risk = close_d - low_d if direction == "LONG" else high_d - close_d
    risk = max(_decimal_price(policy.min_stop), raw_risk)
    side = "buy" if direction == "LONG" else "sell"
    bid_d, ask_d, _quote_at = assess_quote(
        {"bp": bid, "ap": ask, "t": quote_at.isoformat()}, now=now,
    )
    preview = _cents((ask_d if side == "buy" else bid_d) + (
        ENTRY_LIMIT_OFFSET if side == "buy" else -ENTRY_LIMIT_OFFSET
    ))
    reward = _cents(_decimal_price(policy.target_r) * risk)
    if side == "buy":
        stop = _cents(preview - risk)
        target = _cents(preview + reward)
    else:
        stop = _cents(preview + risk)
        target = _cents(preview - reward)
    limit, slippage = price_marketable_entry(
        side=side, signal_close=close_d, bid=bid_d, ask=ask_d, stop=stop, target=target,
        max_slippage=_decimal_price(policy.max_entry_slippage),
        max_concession=_decimal_price(policy.max_limit_concession),
    )
    notional_cap, stop_cap = position_caps(equity, buying_power, policy)
    limit_f, stop_f = float(limit), float(stop)
    if limit_f > notional_cap:
        raise EntryBlocked("NOTIONAL_CAP")
    if abs(limit_f - stop_f) > stop_cap:
        raise EntryBlocked("STOP_RISK_CAP")
    gross_cap = min(
        policy.max_gross_notional,
        equity * policy.max_notional_fraction * policy.max_concurrent,
    )
    if open_notional + limit_f > gross_cap or open_notional + limit_f > buying_power:
        raise EntryBlocked("GROSS_CAP")
    # The submitted stop is the full stop cap away from the limit, which is the
    # worst fill. A better fill cannot cross it unless price improves by the
    # whole cap. The runner replaces both legs from the actual fill.
    stop_cap_d = min(_decimal_price(policy.max_stop_risk), _cents(_decimal_price(stop_cap)))
    wide_stop, wide_target = anchor_protection(
        side=side, fill=limit, risk=stop_cap_d, min_distance=_decimal_price(policy.min_stop),
    )
    if abs(limit - wide_stop) > stop_cap_d:
        raise EntryBlocked("STOP_RISK_CAP")
    order = {
        "symbol": symbol, "qty": "1", "side": side, "type": "limit",
        "limit_price": _money(limit), "time_in_force": "day", "order_class": "bracket",
        "extended_hours": False,
        "stop_loss": {"stop_price": _money(wide_stop)},
        "take_profit": {"limit_price": _money(wide_target)},
    }
    pricing = {
        "signal_close": _money(close_d),
        "planned_risk": _money(risk),
        "slippage_vs_close": _money(slippage),
        "quote": {"bid": _money(bid_d), "ask": _money(ask_d), "at": quote_at.isoformat()},
    }
    return order, pricing


def assert_limit_bracket(order: dict[str, Any]) -> None:
    if (
        order.get("type") != "limit"
        or order.get("order_class") != "bracket"
        or order.get("extended_hours") is not False
        or order.get("time_in_force") != "day"
        or order.get("qty") != "1"
        or order.get("side") not in {"buy", "sell"}
        or not isinstance(order.get("stop_loss"), dict)
        or not isinstance(order.get("take_profit"), dict)
    ):
        raise ValueError("high-frequency entry must be a one-share day limit bracket")


@dataclass(frozen=True)
class MinuteBar:
    start: datetime
    open: float
    high: float
    low: float
    close: float


def parse_minute_bars(
    raw_bars: list[dict[str, Any]], *, session_open: datetime, now: datetime,
) -> list[MinuteBar]:
    """Completed, aligned minutes. An unfinished bar is ignored."""
    bars: list[MinuteBar] = []
    seen: set[datetime] = set()
    for raw in raw_bars:
        start = timestamp(raw["t"])
        if start in seen:
            raise ValueError("duplicate market bar")
        seen.add(start)
        if start < session_open or start + timedelta(minutes=1) > now:
            continue
        if start.second or start.microsecond:
            raise ValueError("unaligned market bar")
        bars.append(MinuteBar(
            start,
            number(raw["o"], positive=True), number(raw["h"], positive=True),
            number(raw["l"], positive=True), number(raw["c"], positive=True),
        ))
    bars.sort(key=lambda bar: bar.start)
    return bars


def _cent_price(value: float) -> float:
    return float(_cents(_decimal_price(value)))


def synthetic_regular_session(
    seed: int, symbols: tuple[str, ...] | None = None,
) -> dict[str, list[tuple[float, float, float, float]]]:
    """Seeded minute bars for one regular session.

    This is a volatility sketch of liquid names, not a market recording and not
    evidence of an edge. OHLC tuples are ``(open, high, low, close)``.
    """
    chosen = symbols or DEFAULT_SYMBOLS
    missing = [symbol for symbol in chosen if symbol not in _TYPICAL_STARTS]
    if missing:
        raise ValueError("synthetic session only covers the default liquid universe")
    rng = random.Random(seed)
    bars: dict[str, list[tuple[float, float, float, float]]] = {}
    for symbol in chosen:
        sigma = 0.0009 * _VOL_SCALE[symbol]
        price = _TYPICAL_STARTS[symbol]
        shock = 0.0
        series: list[tuple[float, float, float, float]] = []
        for _minute in range(REGULAR_SESSION_MINUTES):
            shock = 0.25 * shock + rng.gauss(0, sigma)
            open_ = price
            close = max(1.0, open_ * (1 + shock))
            wick = abs(rng.gauss(0, sigma * 0.55)) * open_
            high = max(open_, close) + wick
            low = max(0.01, min(open_, close) - wick)
            o, h, l, c = (_cent_price(open_), _cent_price(high), _cent_price(low), _cent_price(close))
            high = max(h, o, c)
            low = min(l, o, c)
            if low <= 0:
                low = min(o, c)
            series.append((o, high, low, c))
            price = c
        bars[symbol] = series
    return bars


@dataclass(frozen=True)
class SessionSimulation:
    round_trips: int
    entries: int
    exits_by_reason: dict[str, int]
    max_concurrent: int
    daily_loss_halt: bool
    round_trip_cap_hit: bool
    open_at_end: int
    limit_brackets: int
    skipped: int


def simulate_session(
    bars: dict[str, list[tuple[float, float, float, float]]],
    policy: HighFrequencyPolicy,
    *,
    equity: float = 100_000.0,
    session_open: datetime | None = None,
) -> SessionSimulation:
    """Walk one regular session with the same bracket planner the runner uses."""
    minutes = REGULAR_SESSION_MINUTES
    for symbol in policy.symbols:
        series = bars.get(symbol)
        if series is None or len(series) != minutes:
            raise ValueError("each symbol needs one regular session of minute bars")
    if session_open is None:
        session_open = datetime(2026, 10, 19, 9, 30, tzinfo=NY)
    flatten_at = minutes - policy.close_buffer_minutes
    entry_last = minutes - policy.entry_cutoff_minutes
    open_positions: dict[str, dict[str, Any]] = {}
    cooldown_until = {symbol: -1 for symbol in policy.symbols}
    exits: list[str] = []
    entries = 0
    skipped = 0
    limit_brackets = 0
    max_concurrent = 0
    loss_halt = False
    cap_hit = False
    realized = 0.0

    def mark(minute: int) -> float:
        unrealized = 0.0
        for symbol, position in open_positions.items():
            close = bars[symbol][minute][3]
            sign = 1.0 if position["side"] == "buy" else -1.0
            unrealized += sign * (close - position["entry"])
        return equity + realized + unrealized

    def close_position(symbol: str, minute: int, price: float, reason: str) -> None:
        nonlocal realized
        position = open_positions.pop(symbol)
        sign = 1.0 if position["side"] == "buy" else -1.0
        realized += sign * (price - position["entry"])
        exits.append(reason)
        cooldown_until[symbol] = minute + policy.cooldown_minutes

    for minute in range(minutes):
        for symbol in list(open_positions):
            position = open_positions[symbol]
            if minute < position["entry_minute"]:
                continue
            _open, high, low, close = bars[symbol][minute]
            held = minute - position["entry_minute"]
            if position["side"] == "buy":
                stop_hit = low <= position["stop"]
                target_hit = high >= position["target"]
            else:
                stop_hit = high >= position["stop"]
                target_hit = low <= position["target"]
            if stop_hit:
                close_position(symbol, minute, position["stop"], "stop")
            elif target_hit:
                close_position(symbol, minute, position["target"], "target")
            elif held >= policy.max_hold_minutes:
                close_position(symbol, minute, close, "time")
        max_concurrent = max(max_concurrent, len(open_positions))
        if loss_halt or mark(minute) <= equity * (1 - policy.max_daily_loss_fraction):
            loss_halt = True
            for symbol in list(open_positions):
                close_position(symbol, minute, bars[symbol][minute][3], "daily_loss")
            continue
        if minute >= flatten_at:
            for symbol in list(open_positions):
                close_position(symbol, minute, bars[symbol][minute][3], "flatten")
            continue
        if minute < policy.warmup_minutes or minute >= entry_last or minute + 1 >= minutes:
            continue
        if len(exits) >= policy.max_round_trips:
            cap_hit = True
            continue
        slots = policy.max_concurrent - len(open_positions)
        if slots <= 0:
            continue
        gross = sum(position["entry"] for position in open_positions.values())
        candidates = []
        for symbol in policy.symbols:
            if symbol in open_positions or minute <= cooldown_until[symbol]:
                continue
            series = bars[symbol]
            closes = [bar[3] for bar in series[: minute + 1]]
            bar = series[minute]
            direction = momentum_direction(
                closes, lookback=policy.lookback_bars,
                threshold=policy.momentum_threshold, last_open=bar[0],
            )
            if direction is None:
                continue
            previous = closes[-(policy.lookback_bars + 1)]
            strength = abs(closes[-1] - previous) / previous
            candidates.append((strength, symbol, direction, bar))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        now = session_open + timedelta(minutes=minute + 1)
        for _strength, symbol, direction, bar in candidates:
            if slots <= 0 or len(exits) >= policy.max_round_trips:
                break
            try:
                order, _pricing = plan_bracket(
                    symbol=symbol, direction=direction, close=bar[3], high=bar[1], low=bar[2],
                    bid=bar[3] - 0.01, ask=bar[3] + 0.01, quote_at=now, now=now, policy=policy,
                    equity=mark(minute), buying_power=mark(minute), open_notional=gross,
                )
            except (EntryNotPriced, EntryBlocked):
                skipped += 1
                continue
            assert_limit_bracket(order)
            limit_brackets += 1
            entries += 1
            fill = _decimal_price(order["limit_price"])
            anchored_stop, anchored_target = anchor_protection(
                side=order["side"], fill=fill, risk=_decimal_price(_pricing["planned_risk"]),
                min_distance=_decimal_price(policy.min_stop),
            )
            open_positions[symbol] = {
                "side": order["side"],
                "entry": float(fill),
                "stop": float(anchored_stop),
                "target": float(anchored_target),
                "entry_minute": minute + 1,
            }
            gross += float(order["limit_price"])
            slots -= 1
    reasons = dict(Counter(exits))
    return SessionSimulation(
        round_trips=len(exits), entries=entries, exits_by_reason=reasons,
        max_concurrent=max_concurrent, daily_loss_halt=loss_halt, round_trip_cap_hit=cap_hit,
        open_at_end=len(open_positions), limit_brackets=limit_brackets, skipped=skipped,
    )


def _qty(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return number(value)


def client_order_id(account_id: str, session: str, seq: int) -> str:
    digest = canonical_hash({
        "profile": PROFILE_HIGH_FREQUENCY, "account": account_id, "session": session, "seq": seq,
    })
    return "athp-" + digest[:40]


class HighFrequencyPilot:
    """Multi-symbol paper loop. Protection and flatten do not wait on a model."""

    def __init__(
        self,
        broker: Any,
        journal: PaperJournal,
        policy: HighFrequencyPolicy,
        *,
        session_date: date | None = None,
        authority: ExecutionAuthority = ExecutionAuthority.PAPER,
        jev: Any = None,
        budget: CallBudget | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        require_paper(authority)
        require_paper_endpoint(broker)
        self.broker = broker
        self.journal = journal
        self.policy = policy
        self.session_date = session_date
        self.authority = authority
        self.jev = jev
        self.budget = budget or CallBudget()
        self._clock = clock
        self._backoff_until = 0.0
        self._assets: dict[str, dict[str, Any]] = {}
        self._bars: dict[str, tuple[datetime, list[MinuteBar]]] = {}
        self._calendar_day: date | None = None
        self._calendar_row: dict[str, Any] | None = None
        self._flat_reads = 0

    def halt(self, reason: str) -> None:
        if not self.journal.last("halt"):
            self.journal.append("halt", reason=reason, at=datetime.now(NY).isoformat())

    def tick(self) -> str:
        require_paper(self.authority)
        require_paper_endpoint(self.broker)
        snapshot = self._snapshot()
        self._bind(snapshot)
        now = timestamp(snapshot["clock"]["timestamp"])
        if self.journal.last("hf_session_flat"):
            if self._open_intents() or self._our_positions(snapshot) or self._our_working(snapshot):
                self.halt("EXPOSURE_AFTER_FLAT")
            else:
                return "COMPLETE"
        self._reconcile(snapshot, now)
        unexpected = self._confirm_unowned(snapshot, now)
        if unexpected:
            self.halt("UNOWNED_EXPOSURE")
            if not self._open_intents() and not self._our_positions(snapshot):
                return "COMPLETE"
        if self._past_flatten(snapshot, now) or not snapshot["clock"]["is_open"]:
            if self._needs_flatten(snapshot):
                self.halt("SESSION_CLOSE")
                return self._settle(snapshot, now, "SESSION_FLATTEN")
            if self.session_date is not None and (
                now.astimezone(NY).date() != self.session_date or not snapshot["clock"]["is_open"]
            ):
                return "COMPLETE"
            return "MARKET_CLOSED" if not snapshot["clock"]["is_open"] else "COMPLETE"
        if self._loss_hit(snapshot):
            self.halt("DAILY_LOSS_LIMIT")
            return self._settle(snapshot, now, "DAILY_LOSS_FLATTEN")
        if self.journal.last("halt"):
            return self._settle(snapshot, now, self.journal.last("halt")["reason"])
        if self._pdt():
            return "PDT_RESTRICTED"
        return self._seek_entry(snapshot, now)

    def _charge(self, count: int = 1) -> None:
        self.budget.add(self._clock(), count)

    def _snapshot(self) -> dict[str, Any]:
        self._charge(4)
        snapshot = self.broker.snapshot()
        view = dict(snapshot)
        view["orders"] = working_orders(snapshot.get("orders"))
        return view

    def _bind(self, snapshot: dict[str, Any]) -> None:
        account_id = snapshot["account"]["id"]
        if not isinstance(account_id, str) or not account_id:
            raise ValueError("paper account id is missing")
        previous = self.journal.last("binding")
        if previous:
            stored = previous["binding"]
            if stored.get("account_id") != account_id:
                raise ValueError("journal belongs to a different account")
            try:
                self.policy = HighFrequencyPolicy(**stored["policy"])
            except (TypeError, ValueError) as exc:
                raise ValueError("journal is not a high-frequency paper session") from exc
            return
        if self._unexpected(snapshot) or self._unowned_positions(snapshot):
            raise ValueError("high-frequency session requires no unowned positions or orders")
        self.journal.append("binding", binding={"account_id": account_id, "policy": asdict(self.policy)})

    def _events(self, kind: str) -> list[dict[str, Any]]:
        return [event for event in self.journal.events if event.get("kind") == kind]

    def _open_intents(self) -> list[dict[str, Any]]:
        closed = {
            event.get("client_order_id") for event in self._events("hf_position_closed")
            if isinstance(event.get("client_order_id"), str)
        }
        return [
            event for event in self._events("hf_entry_intent")
            if event["order"]["client_order_id"] not in closed
        ]

    def _owned_symbols(self) -> set[str]:
        return {event["order"]["symbol"] for event in self._open_intents()}

    def _foreign_symbols(self, snapshot: dict[str, Any]) -> set[str]:
        return symbols_for_prefix(snapshot.get("orders"), EARNINGS_ORDER_PREFIX) | load_ownership_symbols(
            earnings_state_dir(),
        )

    def _unowned_positions(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        foreign = self._foreign_symbols(snapshot)
        owned = self._owned_symbols()
        found = []
        for position in snapshot.get("positions") or []:
            symbol = position.get("symbol")
            if symbol in owned or (symbol in foreign and symbol not in owned):
                continue
            found.append(position)
        return found

    def _lineage(self) -> tuple[set[str], set[str]]:
        ids: set[str] = set()
        client_ids: set[str] = set()
        for event in self._events("hf_entry_intent"):
            _absorb_order_ids(event.get("order"), ids, client_ids)
        for kind in ("hf_entry_ack", "hf_entry_observation", "hf_protection_anchored"):
            for event in self._events(kind):
                _absorb_order_ids(event.get("order"), ids, client_ids)
                if isinstance(event.get("client_order_id"), str):
                    client_ids.add(event["client_order_id"])
        return ids, client_ids

    def _unexpected(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        foreign = self._foreign_symbols(snapshot)
        owned = self._owned_symbols()
        known_ids, known_clients = self._lineage()
        orders = snapshot.get("orders") or []
        for order in orders:
            for leg in order.get("legs") or []:
                if isinstance(leg, dict) and isinstance(leg.get("id"), str):
                    known_ids.add(leg["id"])
        unexpected = []
        for order in orders:
            order_id = order.get("id") if isinstance(order.get("id"), str) else ""
            client_id = order.get("client_order_id") if isinstance(order.get("client_order_id"), str) else ""
            parent_id = order.get("parent_id") if isinstance(order.get("parent_id"), str) else ""
            if not parent_id and isinstance(order.get("parent_order_id"), str):
                parent_id = order["parent_order_id"]
            if order_id and order_id in known_ids:
                continue
            if client_id and (client_id in known_clients or client_id.startswith(EARNINGS_ORDER_PREFIX)):
                continue
            if parent_id and parent_id in known_ids:
                continue
            symbol = order.get("symbol")
            if symbol in owned or (symbol in foreign and symbol not in owned):
                continue
            unexpected.append(order)
        return unexpected

    def _recently_closed_symbols(self, now: datetime) -> set[str]:
        found: set[str] = set()
        window = timedelta(seconds=UNOWNED_RECHECK_SECONDS)
        for event in self._events("hf_position_closed"):
            symbol = event.get("symbol")
            closed_at = event.get("at")
            if not isinstance(symbol, str) or not isinstance(closed_at, str):
                continue
            try:
                when = timestamp(closed_at)
            except (TypeError, ValueError):
                continue
            if timedelta(0) <= now - when <= window:
                found.add(symbol)
        return found

    def _confirm_unowned(self, snapshot: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
        """Ignore our own leftover legs. Recheck a just-closed symbol once before halting."""
        unexpected = self._unexpected(snapshot)
        if not unexpected:
            return []
        symbols = {order.get("symbol") for order in unexpected if isinstance(order.get("symbol"), str)}
        if not symbols or not symbols <= self._recently_closed_symbols(now):
            return unexpected
        signature = sorted(
            f"{order.get('symbol')}|{order.get('id') or ''}|{order.get('client_order_id') or ''}"
            for order in unexpected
        )
        if any(event.get("signature") == signature for event in self._events("hf_unowned_recheck")):
            return unexpected
        self.journal.append(
            "hf_unowned_recheck", signature=signature, at=now.isoformat(),
            symbols=sorted(symbols),
        )
        return self._unexpected(self._snapshot())

    def _our_positions(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        owned = self._owned_symbols()
        return [
            position for position in snapshot.get("positions") or []
            if position.get("symbol") in owned
        ]

    def _our_working(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        known = {event["order"]["client_order_id"] for event in self._events("hf_entry_intent")}
        return [
            order for order in snapshot.get("orders") or []
            if order.get("client_order_id") in known
        ]

    def _needs_flatten(self, snapshot: dict[str, Any]) -> bool:
        return bool(self._open_intents() or self._our_positions(snapshot) or self._our_working(snapshot))

    def _position(self, snapshot: dict[str, Any], symbol: str) -> dict[str, Any] | None:
        matches = [
            position for position in snapshot.get("positions") or []
            if position.get("symbol") == symbol
        ]
        if len(matches) > 1:
            raise ValueError("duplicate broker position")
        return matches[0] if matches else None

    def _loss_hit(self, snapshot: dict[str, Any]) -> bool:
        equity = number(snapshot["account"]["equity"], positive=True)
        last_equity = number(snapshot["account"]["last_equity"], positive=True)
        return equity <= last_equity * (1 - self.policy.max_daily_loss_fraction)

    def _past_flatten(self, snapshot: dict[str, Any], now: datetime) -> bool:
        close_at = timestamp(snapshot["clock"]["next_close"])
        return now >= close_at - timedelta(minutes=self.policy.close_buffer_minutes)

    def _pdt(self) -> bool:
        return self.journal.last("hf_pdt_restricted") is not None

    def _round_trips(self) -> int:
        return sum(
            1 for event in self._events("hf_position_closed")
            if event.get("filled") is True and event.get("reason") in ROUND_TRIP_REASONS
        )

    def _cooldown_until(self, symbol: str) -> datetime | None:
        last = None
        for event in self._events("hf_position_closed"):
            if event.get("symbol") == symbol and event.get("filled") is True:
                last = event
        if last is None or not isinstance(last.get("at"), str):
            return None
        return timestamp(last["at"]) + timedelta(minutes=self.policy.cooldown_minutes)

    def _lookup(self, client_id: str) -> tuple[dict[str, Any] | None, bool]:
        self._charge(1)
        try:
            parent = self.broker.call(
                "GET", "/v2/orders:by_client_order_id?" + urlencode({"client_order_id": client_id}),
            )
        except ModelError as exc:
            if getattr(exc, "status", None) == 404 or str(exc) == "HTTP 404":
                return None, True
            raise
        if not isinstance(parent, dict):
            raise ValueError("broker order payload is unusable")
        return parent, False

    def _observe(self, client_id: str, parent: dict[str, Any]) -> None:
        fingerprint = {"status": parent.get("status"), "filled_qty": parent.get("filled_qty")}
        previous = None
        for event in self._events("hf_entry_observation"):
            if event.get("client_order_id") == client_id:
                previous = event
        if previous is not None and previous.get("fingerprint") == fingerprint:
            return
        self.journal.append(
            "hf_entry_observation", client_order_id=client_id, order=parent, fingerprint=fingerprint,
        )

    def _finish(
        self, client_id: str, symbol: str, reason: str, *, filled: bool, now: datetime,
    ) -> None:
        if any(event.get("client_order_id") == client_id for event in self._events("hf_position_closed")):
            return
        self.journal.append(
            "hf_position_closed", client_order_id=client_id, symbol=symbol, reason=reason,
            filled=filled, at=now.isoformat(),
        )

    def _miss(self, client_id: str, symbol: str, snapshot: dict[str, Any], now: datetime) -> None:
        if self._position(snapshot, symbol) is not None:
            self._request_close(client_id, symbol, "ENTRY_UNCERTAIN", None, now)
            return
        misses = sum(
            1 for event in self._events("hf_lookup_miss") if event.get("client_order_id") == client_id
        )
        if misses < 1:
            self.journal.append(
                "hf_lookup_miss", client_order_id=client_id, reason="BROKER_ORDER_NOT_FOUND",
                at=now.isoformat(),
            )
            return
        self._finish(client_id, symbol, "ENTRY_ABSENT", filled=False, now=now)

    def _delete_order(self, order_id: str) -> None:
        self._charge(1)
        try:
            self.broker.call("DELETE", "/v2/orders/" + order_id)
        except ModelError as exc:
            if getattr(exc, "status", None) not in {404, 422}:
                raise

    def _request_close(
        self, client_id: str, symbol: str, reason: str, parent: dict[str, Any] | None, now: datetime,
    ) -> None:
        attempts = [
            event for event in self._events("hf_close_requested")
            if event.get("client_order_id") == client_id
        ]
        if len(attempts) >= 3:
            self.halt("CLOSE_EXHAUSTED")
            raise ValueError("close attempts exhausted; exposure remains unresolved")
        self.journal.append(
            "hf_close_requested", client_order_id=client_id, symbol=symbol, reason=reason,
            at=now.isoformat(),
        )
        if parent and parent.get("status") not in TERMINAL and isinstance(parent.get("id"), str):
            self._delete_order(parent["id"])
        self._charge(1)
        try:
            self.broker.call("DELETE", "/v2/positions/" + symbol)
        except ModelError as exc:
            if getattr(exc, "status", None) not in {404, 422}:
                raise

    def _filled_at(self, client_id: str, now: datetime) -> datetime:
        for event in self._events("hf_filled"):
            if event.get("client_order_id") == client_id and isinstance(event.get("at"), str):
                return timestamp(event["at"])
        return now

    def _note_fill(self, client_id: str, parent: dict[str, Any], now: datetime) -> None:
        if _qty(parent.get("filled_qty")) <= 0:
            return
        if any(event.get("client_order_id") == client_id for event in self._events("hf_filled")):
            return
        self.journal.append("hf_filled", client_order_id=client_id, at=now.isoformat())

    def _planned_risk(self, intent: dict[str, Any]) -> Decimal:
        raw = intent.get("planned_risk")
        try:
            risk = _decimal_price(raw) if raw not in (None, "") else _decimal_price(self.policy.min_stop)
        except (TypeError, ValueError):
            risk = _decimal_price(self.policy.min_stop)
        risk = max(risk, _decimal_price(self.policy.min_stop))
        return min(risk, _decimal_price(self.policy.max_stop_risk))

    def _fill_price(self, parent: dict[str, Any], position: dict[str, Any] | None) -> Decimal | None:
        for source in (parent, position or {}):
            raw = source.get("filled_avg_price")
            if raw in (None, ""):
                raw = source.get("avg_entry_price")
            if raw in (None, ""):
                continue
            try:
                price = _cents(_decimal_price(raw))
            except (TypeError, ValueError):
                continue
            if price > 0:
                return price
        return None

    def _bracket_legs(
        self, parent: dict[str, Any], snapshot: dict[str, Any], symbol: str,
    ) -> list[dict[str, Any]]:
        """Live stop and target legs for this parent. The entry order itself is not a leg."""
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        parent_id = parent.get("id") if isinstance(parent.get("id"), str) else ""
        nested = parent.get("legs") if isinstance(parent.get("legs"), list) else []
        nested_ids = {
            leg.get("id") for leg in nested if isinstance(leg, dict) and isinstance(leg.get("id"), str)
        }

        def add(order: Any) -> None:
            if not isinstance(order, dict) or order.get("status") in TERMINAL:
                return
            order_id = order.get("id") if isinstance(order.get("id"), str) else ""
            if order_id and (order_id == parent_id or order_id in seen):
                return
            if order_id:
                seen.add(order_id)
            found.append(order)

        for leg in nested:
            add(leg)
        for order in snapshot.get("orders") or []:
            if not isinstance(order, dict) or order.get("symbol") != symbol:
                continue
            order_id = order.get("id") if isinstance(order.get("id"), str) else ""
            parent_link = order.get("parent_id") if isinstance(order.get("parent_id"), str) else ""
            if not parent_link and isinstance(order.get("parent_order_id"), str):
                parent_link = order["parent_order_id"]
            if order_id in nested_ids or (parent_id and parent_link == parent_id):
                add(order)
        return found

    def _replace_leg(self, leg_id: str, payload: dict[str, Any]) -> bool:
        self._charge(1)
        try:
            self.broker.call("PATCH", "/v2/orders/" + leg_id, payload)
        except ModelError:
            return False
        return True

    def _anchor_protection(
        self, parent: dict[str, Any], snapshot: dict[str, Any], intent: dict[str, Any],
        position: dict[str, Any], now: datetime,
    ) -> None:
        """Move the live stop and target onto the fill. A wrong-side stop is not left in place."""
        fill = self._fill_price(parent, position)
        if fill is None:
            return
        side = intent["order"]["side"]
        symbol = intent["order"]["symbol"]
        try:
            stop, target = anchor_protection(
                side=side, fill=fill, risk=self._planned_risk(intent),
                min_distance=_decimal_price(self.policy.min_stop),
            )
        except EntryBlocked:
            return
        legs = self._bracket_legs(parent, snapshot, symbol)
        stop_leg = next((leg for leg in legs if leg.get("type") in {"stop", "stop_limit"}), None)
        target_leg = next((leg for leg in legs if leg.get("type") == "limit"), None)
        if stop_leg is None or not isinstance(stop_leg.get("id"), str):
            return
        if self._leg_price(stop_leg, "stop_price") == stop and (
            target_leg is None or self._leg_price(target_leg, "limit_price") == target
        ):
            self._note_anchor(intent, fill, stop, target, now)
            return
        stop_body = {"qty": "1", "time_in_force": "day", "stop_price": _money(stop)}
        if not self._replace_leg(stop_leg["id"], stop_body):
            return
        stop_leg["stop_price"] = _money(stop)
        if target_leg is not None and isinstance(target_leg.get("id"), str):
            target_body = {"qty": "1", "time_in_force": "day", "limit_price": _money(target)}
            if not self._replace_leg(target_leg["id"], target_body):
                return
            target_leg["limit_price"] = _money(target)
        self._note_anchor(intent, fill, stop, target, now)

    def _note_anchor(
        self, intent: dict[str, Any], fill: Decimal, stop: Decimal, target: Decimal, now: datetime,
    ) -> None:
        client_id = intent["order"]["client_order_id"]
        previous = next(
            (event for event in reversed(self._events("hf_protection_anchored"))
             if event.get("client_order_id") == client_id),
            None,
        )
        current = {"fill": _money(fill), "stop": _money(stop), "target": _money(target)}
        if previous is not None and all(previous.get(key) == value for key, value in current.items()):
            return
        self.journal.append(
            "hf_protection_anchored", client_order_id=client_id, symbol=intent["order"]["symbol"],
            at=now.isoformat(), **current,
        )

    def _leg_price(self, leg: dict[str, Any], field: str) -> Decimal | None:
        raw = leg.get(field)
        if raw in (None, ""):
            return None
        try:
            return _cents(_decimal_price(raw))
        except (TypeError, ValueError):
            return None

    def _stop_covers_fill(
        self, parent: dict[str, Any], snapshot: dict[str, Any], intent: dict[str, Any],
        position: dict[str, Any],
    ) -> bool:
        """False when the live stop is through the fill or closer than the minimum."""
        fill = self._fill_price(parent, position)
        if fill is None:
            return True
        symbol = intent["order"]["symbol"]
        side = intent["order"]["side"]
        legs = self._bracket_legs(parent, snapshot, symbol)
        stop_leg = next((leg for leg in legs if leg.get("type") in {"stop", "stop_limit"}), None)
        if stop_leg is None:
            return True
        stop = self._leg_price(stop_leg, "stop_price")
        if stop is None:
            return False
        minimum = _decimal_price(self.policy.min_stop)
        if side == "buy":
            return fill - stop >= minimum
        if side == "sell":
            return stop - fill >= minimum
        return False

    def _protected(
        self, parent: dict[str, Any], snapshot: dict[str, Any], symbol: str, side: str, qty: float,
    ) -> bool:
        legs = parent.get("legs") if isinstance(parent.get("legs"), list) else []
        orders = list(snapshot.get("orders") or [])
        return any(
            protective_stop(order, symbol=symbol, entry_side=side, position_qty=qty)
            for order in (*orders, *legs)
        )

    def _exit_reason(self, parent: dict[str, Any], intent: dict[str, Any], client_id: str) -> str:
        for event in reversed(self._events("hf_close_requested")):
            if event.get("client_order_id") == client_id and isinstance(event.get("reason"), str):
                return event["reason"]
        reason = bracket_exit_reason(parent, intent["order"]["side"])
        return reason or "POSITION_FLAT"

    def _reconcile(self, snapshot: dict[str, Any], now: datetime) -> None:
        for intent in self._open_intents():
            order = intent["order"]
            client_id, symbol, side = order["client_order_id"], order["symbol"], order["side"]
            parent, missing = self._lookup(client_id)
            if missing or parent is None:
                self._miss(client_id, symbol, snapshot, now)
                continue
            self._observe(client_id, parent)
            self._note_fill(client_id, parent, now)
            status = parent.get("status")
            filled = _qty(parent.get("filled_qty"))
            position = self._position(snapshot, symbol)
            if filled <= 0 and status in TERMINAL:
                self._finish(client_id, symbol, "ENTRY_UNFILLED", filled=False, now=now)
                continue
            age = (now - timestamp(intent["at"])).total_seconds()
            if filled <= 0 and age > self.policy.entry_timeout_seconds:
                if isinstance(parent.get("id"), str) and status not in TERMINAL:
                    self._delete_order(parent["id"])
                continue
            position_qty = _qty(position.get("qty")) if position else 0.0
            if filled > 1 or abs(position_qty) > 1:
                self.halt("POSITION_MISMATCH")
                self._request_close(client_id, symbol, "POSITION_MISMATCH", parent, now)
                continue
            if position is not None:
                self._anchor_protection(parent, snapshot, intent, position, now)
                if not self._protected(parent, snapshot, symbol, side, position_qty):
                    self._request_close(client_id, symbol, "PROTECTION_MISSING", parent, now)
                    continue
                if not self._stop_covers_fill(parent, snapshot, intent, position):
                    self._request_close(client_id, symbol, "PROTECTION_MISSING", parent, now)
                    continue
                if now - self._filled_at(client_id, now) >= timedelta(minutes=self.policy.max_hold_minutes):
                    self._request_close(client_id, symbol, "TIME_STOP", parent, now)
                continue
            if filled > 0:
                self._finish(
                    client_id, symbol, self._exit_reason(parent, intent, client_id),
                    filled=True, now=now,
                )

    def _cancel_ours(self, snapshot: dict[str, Any]) -> None:
        for order in self._our_working(snapshot):
            if isinstance(order.get("id"), str):
                self._delete_order(order["id"])

    def _settle(self, snapshot: dict[str, Any], now: datetime, reason: str) -> str:
        self._cancel_ours(snapshot)
        for intent in list(self._open_intents()):
            order = intent["order"]
            symbol = order["symbol"]
            if self._position(snapshot, symbol) is not None:
                parent, missing = self._lookup(order["client_order_id"])
                self._request_close(
                    order["client_order_id"], symbol, reason, None if missing else parent, now,
                )
            elif not self._our_working(snapshot):
                filled = any(
                    event.get("client_order_id") == order["client_order_id"]
                    for event in self._events("hf_filled")
                )
                self._finish(
                    order["client_order_id"], symbol, reason if filled else "ENTRY_UNFILLED",
                    filled=filled, now=now,
                )
        if self._needs_flatten(snapshot):
            self._flat_reads = 0
            return "FLATTENING"
        self._flat_reads += 1
        if self._flat_reads < 2:
            return "FLATTENING"
        if self.journal.last("hf_session_flat") is None:
            self.journal.append("hf_session_flat", at=now.isoformat(), reason=reason)
        return "COMPLETE"

    def _session_open(self, now: datetime) -> datetime:
        day = now.astimezone(NY).date()
        if self._calendar_day != day or self._calendar_row is None:
            self._charge(1)
            rows = self.broker.call("GET", "/v2/calendar?" + urlencode({
                "start": (day - timedelta(days=10)).isoformat(), "end": day.isoformat(),
            }))
            if not isinstance(rows, list):
                raise ValueError("broker calendar unavailable")
            current = [row for row in rows if isinstance(row, dict) and row.get("date") == day.isoformat()]
            if len(current) != 1 or not isinstance(current[0].get("open"), str):
                raise ValueError("broker calendar unavailable")
            self._calendar_day = day
            self._calendar_row = current[0]
        return datetime.fromisoformat(day.isoformat() + "T" + self._calendar_row["open"]).replace(tzinfo=NY)

    def _minute_bars(self, symbol: str, session_open: datetime, now: datetime) -> list[MinuteBar]:
        minute = now.astimezone(NY).replace(second=0, microsecond=0)
        cached = self._bars.get(symbol)
        if cached is not None and cached[0] == minute:
            return cached[1]
        self._charge(1)
        raw = self.broker.bars(symbol, session_open, now, "1Min", self.policy.feed)
        if not isinstance(raw, list):
            raise ValueError("minute bars are unusable")
        parsed = parse_minute_bars(raw, session_open=session_open, now=now)
        self._bars[symbol] = (minute, parsed)
        return parsed

    def _asset(self, symbol: str) -> dict[str, Any]:
        cached = self._assets.get(symbol)
        if cached is not None:
            return cached
        self._charge(1)
        asset = self.broker.call("GET", "/v2/assets/" + symbol)
        if not isinstance(asset, dict):
            raise ValueError("asset payload is unusable")
        self._assets[symbol] = asset
        return asset

    def _skip(self, symbol: str, reason: str, signal_at: str, now: datetime) -> None:
        if any(
            event.get("symbol") == symbol and event.get("signal_at") == signal_at
            and event.get("reason") == reason
            for event in self._events("hf_entry_skipped")
        ):
            return
        self.journal.append(
            "hf_entry_skipped", symbol=symbol, reason=reason, signal_at=signal_at, at=now.isoformat(),
        )

    def _note_pdt(self, now: datetime) -> None:
        if self.journal.last("hf_pdt_restricted") is None:
            self.journal.append(
                "hf_pdt_restricted", at=now.isoformat(),
                reason="pattern day trade restriction; new entries stopped; open brackets stay managed",
            )

    def _signal(
        self, symbol: str, bars: list[MinuteBar], now: datetime, close_at: datetime,
        session_open: datetime,
    ) -> tuple[str, MinuteBar] | None:
        if len(bars) < self.policy.lookback_bars + 1:
            return None
        last = bars[-1]
        if (last.start - session_open).total_seconds() < self.policy.warmup_minutes * 60:
            return None
        age = (now - (last.start + timedelta(minutes=1))).total_seconds()
        if age > self.policy.max_bar_age_seconds:
            return None
        if now >= close_at - timedelta(minutes=self.policy.entry_cutoff_minutes):
            return None
        direction = momentum_direction(
            [bar.close for bar in bars], lookback=self.policy.lookback_bars,
            threshold=self.policy.momentum_threshold, last_open=last.open,
        )
        if direction is None:
            return None
        return direction, last

    def _seen_signal(self, symbol: str, signal_at: str) -> bool:
        for kind in ("hf_entry_intent", "hf_entry_skipped"):
            for event in self._events(kind):
                event_symbol = event.get("symbol") or event.get("order", {}).get("symbol")
                if event_symbol == symbol and event.get("signal_at") == signal_at:
                    return True
        return False

    def _seek_entry(self, snapshot: dict[str, Any], now: datetime) -> str:
        account = snapshot["account"]
        if account.get("status") != "ACTIVE" or any(
            account.get(flag) for flag in ("trading_blocked", "account_blocked", "trade_suspended_by_user")
        ):
            return "ACCOUNT_BLOCKED"
        if self._round_trips() >= self.policy.max_round_trips:
            return "ROUND_TRIP_CAP"
        if len(self._open_intents()) >= self.policy.max_concurrent:
            return "POSITION_OPEN"
        if not self.budget.allow_entry(self._clock()) or self._clock() < self._backoff_until:
            return "RATE_LIMIT"
        close_at = timestamp(snapshot["clock"]["next_close"])
        if now >= close_at - timedelta(minutes=self.policy.entry_cutoff_minutes):
            return "ENTRY_CUTOFF"
        session_open = self._session_open(now)
        equity = number(account["equity"], positive=True)
        buying_power = number(account["buying_power"])
        open_notional = sum(float(event["order"]["limit_price"]) for event in self._open_intents())
        foreign = self._foreign_symbols(snapshot)
        owned = self._owned_symbols()
        candidates = []
        for symbol in self.policy.symbols:
            if symbol in owned or symbol in foreign:
                continue
            cooldown = self._cooldown_until(symbol)
            if cooldown is not None and now < cooldown:
                continue
            try:
                bars = self._minute_bars(symbol, session_open, now)
            except (TypeError, ValueError, KeyError):
                self._skip(symbol, "BARS_UNUSABLE", now.isoformat(), now)
                continue
            signal = self._signal(symbol, bars, now, close_at, session_open)
            if signal is None:
                continue
            direction, bar = signal
            signal_at = bar.start.isoformat()
            if self._seen_signal(symbol, signal_at):
                continue
            previous = bars[-(self.policy.lookback_bars + 1)].close
            strength = abs(bar.close - previous) / previous if previous else 0.0
            candidates.append((strength, symbol, direction, bar, signal_at))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        if not candidates:
            return "POSITION_OPEN" if self._open_intents() else "WAITING_FOR_SIGNAL"
        for _strength, symbol, direction, bar, signal_at in candidates:
            if self.policy.jev_mode == "advisory" and not any(
                event.get("symbol") == symbol and event.get("signal_at") == signal_at
                for event in self._events("hf_jev_bypassed")
            ):
                self.journal.append(
                    "hf_jev_bypassed", symbol=symbol, signal_at=signal_at, at=now.isoformat(),
                    reason="entry does not wait for Jev",
                )
            asset = self._asset(symbol)
            side = "buy" if direction == "LONG" else "sell"
            if (
                asset.get("status") != "active" or asset.get("tradable") is not True
                or asset.get("class") != "us_equity"
            ):
                self._skip(symbol, "ASSET_BLOCKED", signal_at, now)
                continue
            if side == "sell" and (not asset.get("shortable") or not asset.get("easy_to_borrow")):
                self._skip(symbol, "SHORT_BLOCKED", signal_at, now)
                continue
            self._charge(1)
            try:
                quote = self.broker.latest_quote(symbol, self.policy.feed)
                bid = float(quote.get("bp", quote.get("bid")))
                ask = float(quote.get("ap", quote.get("ask")))
                quote_at = timestamp(str(quote.get("t", quote.get("at"))))
            except (ModelError, TypeError, ValueError, KeyError):
                self._skip(symbol, "QUOTE_MISSING", signal_at, now)
                continue
            try:
                order, pricing = plan_bracket(
                    symbol=symbol, direction=direction, close=bar.close, high=bar.high, low=bar.low,
                    bid=bid, ask=ask, quote_at=quote_at, now=now, policy=self.policy,
                    equity=equity, buying_power=buying_power, open_notional=open_notional,
                )
            except EntryNotPriced as skipped:
                self._skip(symbol, skipped.reason, signal_at, now)
                continue
            except EntryBlocked as blocked:
                self._skip(symbol, blocked.reason, signal_at, now)
                continue
            status = self._submit(snapshot, now, symbol, order, pricing, signal_at)
            if status == "ENTRY_SUBMITTED":
                return status
            if status in {"PDT_RESTRICTED", "RATE_LIMIT", "SHARED_RISK_BLOCKED", "SHARED_RISK_DEFERRED"}:
                return status
        return "WAITING_FOR_SIGNAL"

    def _submit(
        self, snapshot: dict[str, Any], now: datetime, symbol: str, order: dict[str, Any],
        pricing: dict[str, Any], signal_at: str,
    ) -> str:
        account_id = snapshot["account"]["id"]
        session = now.astimezone(NY).date().isoformat()
        seq = len(self._events("hf_entry_intent")) + 1
        reserved = dict(order)
        reserved["client_order_id"] = client_order_id(account_id, session, seq)
        assert_limit_bracket(reserved)
        require_paper(self.authority)
        require_paper_endpoint(self.broker)
        try:
            with entry_coordination(self.journal, account_id):
                fresh = self._snapshot()
                fresh_now = timestamp(fresh["clock"]["timestamp"])
                if (
                    not fresh["clock"]["is_open"]
                    or self._past_flatten(fresh, fresh_now)
                    or self._loss_hit(fresh)
                    or fresh["account"]["id"] != account_id
                ):
                    self._skip(symbol, "SESSION_BLOCKED", signal_at, fresh_now)
                    return "SESSION_BLOCKED"
                if self._position(fresh, symbol) is not None:
                    self._skip(symbol, "SYMBOL_ALREADY_RESERVED", signal_at, fresh_now)
                    return "SYMBOL_ALREADY_RESERVED"
                try:
                    other = other_arm_exposure(
                        "intraday", today=fresh_now.astimezone(NY).date(), account_id=account_id,
                    )
                except SharedBookBusy:
                    self._skip(symbol, "SHARED_BOOK_BUSY", signal_at, fresh_now)
                    return "SHARED_RISK_DEFERRED"
                except SharedExposureUnreadable:
                    self._skip(symbol, "SHARED_EXPOSURE_UNREADABLE", signal_at, fresh_now)
                    return "SHARED_RISK_BLOCKED"
                reflected_ids, reflected_symbols = reflected_exposure(fresh)
                decision = authorize_entry(
                    arm="intraday", symbol=symbol, notional=float(reserved["limit_price"]),
                    stop_risk=abs(
                        float(reserved["limit_price"]) - float(reserved["stop_loss"]["stop_price"]),
                    ),
                    overnight=False, buying_power=number(fresh["account"]["buying_power"]),
                    open_exposure=other, reflected_client_order_ids=reflected_ids,
                    reflected_symbols=reflected_symbols,
                )
                if not decision.allowed:
                    self._skip(symbol, decision.reason, signal_at, fresh_now)
                    return "SHARED_RISK_BLOCKED"
                self.journal.append(
                    "hf_entry_intent", session=session, at=fresh_now.isoformat(), signal_at=signal_at,
                    symbol=symbol, order=reserved, **pricing,
                )
                self._charge(1)
                try:
                    ack = self.broker.call("POST", "/v2/orders", reserved)
                except PatternDayTradeError:
                    self._note_pdt(fresh_now)
                    return "PDT_RESTRICTED"
                except ModelError as exc:
                    if getattr(exc, "status", None) == 429:
                        self._backoff_until = self._clock() + 30
                        return "RATE_LIMIT"
                    self.journal.append(
                        "hf_entry_rejected", client_order_id=reserved["client_order_id"],
                        status=getattr(exc, "status", None), at=fresh_now.isoformat(),
                    )
                    return "ENTRY_REJECTED"
                if not isinstance(ack, dict):
                    raise ValueError("broker acknowledgement is unusable")
                self.journal.append(
                    "hf_entry_ack", client_order_id=reserved["client_order_id"], order=ack,
                )
                return "ENTRY_SUBMITTED"
        except SharedBookBusy:
            self._skip(symbol, "SHARED_BOOK_BUSY", signal_at, now)
            return "SHARED_RISK_DEFERRED"
        except SharedExposureUnreadable:
            self._skip(symbol, "SHARED_EXPOSURE_UNREADABLE", signal_at, now)
            return "SHARED_RISK_BLOCKED"

    def unresolved(self) -> bool:
        if self.journal.last("hf_session_flat") is not None:
            return False
        return bool(self._events("hf_entry_intent"))


def run_high_frequency(args: Any) -> int:
    """Active high-frequency paper session. LIVE is not a choice on this parser."""
    require_paper(parse_authority(args.authority))
    require_shared_risk_dir()
    missing = [
        name for name in ("ALPACA_API_KEY", "ALPACA_API_SECRET")
        if not os.environ.get(name, "").strip()
    ]
    if missing:
        print(json.dumps({"ok": False, "missing_environment": missing, "orders_submitted": 0}))
        return 2
    feed = args.feed or "iex"
    policy = high_frequency_policy_from_environment(feed)
    broker = LivePaperAPI.from_env()
    require_paper_endpoint(broker)
    if args.preflight:
        snapshot = broker.snapshot()
        print(json.dumps({
            "ok": True, "mode": "PAPER", "profile": PROFILE_HIGH_FREQUENCY,
            "market_open": bool(snapshot["clock"]["is_open"]),
            "positions": len(snapshot["positions"]), "open_orders": len(snapshot["orders"]),
            "symbols": list(policy.symbols), "max_concurrent": policy.max_concurrent,
            "max_round_trips": policy.max_round_trips,
            "max_notional": policy.max_notional, "max_stop_risk": policy.max_stop_risk,
            "daily_loss_fraction": policy.max_daily_loss_fraction,
            "close_buffer_minutes": policy.close_buffer_minutes, "jev": policy.jev_mode,
            "orders_submitted": 0,
        }))
        return 0
    journal = PaperJournal(args.journal)
    pilot: HighFrequencyPilot | None = None
    stopped = False

    def stop_requested(signum, frame):
        nonlocal stopped
        stopped = True

    try:
        stored = journal.last("binding")
        if stored:
            try:
                policy = HighFrequencyPolicy(**stored["binding"]["policy"])
            except (TypeError, ValueError) as exc:
                raise ValueError("journal is not a high-frequency paper session") from exc
        pilot = HighFrequencyPilot(
            broker, journal, policy, session_date=args.session_date,
        )
        signal.signal(signal.SIGTERM, stop_requested)
        signal.signal(signal.SIGINT, stop_requested)
        previous = None
        while not stopped:
            status = pilot.tick()
            if status != previous:
                report = {"mode": "PAPER", "profile": PROFILE_HIGH_FREQUENCY, "status": status}
                halt = journal.last("halt")
                if halt is not None:
                    report["halt_reason"] = halt["reason"]
                print(json.dumps(report), flush=True)
                previous = status
            if status == "COMPLETE":
                journal.append("session_complete", at=datetime.now(NY).isoformat(), status=status)
                return 0
            time.sleep(5)
        pilot.halt("OPERATOR_STOP")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if pilot.tick() == "COMPLETE":
                journal.append("session_complete", at=datetime.now(NY).isoformat(), status="COMPLETE")
                return 0
            time.sleep(5)
    except Exception as exc:
        print(json.dumps({
            "ok": False, "error_type": type(exc).__name__, "error": str(exc)[:250],
        }), file=sys.stderr, flush=True)
        if pilot is not None and journal.last("halt") is None and pilot.unresolved():
            try:
                pilot.halt("RUNNER_ERROR")
            except Exception:
                pass
        return 2
    finally:
        if pilot is not None and pilot.unresolved():
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                try:
                    if pilot.tick() == "COMPLETE":
                        print(json.dumps({
                            "status": "FLAT_CONFIRMED", "mode": "PAPER",
                            "profile": PROFILE_HIGH_FREQUENCY,
                        }), flush=True)
                        break
                except Exception:
                    pass
                time.sleep(5)
            else:
                print(json.dumps({
                    "ok": False, "status": "UNRESOLVED_PAPER_EXPOSURE",
                    "action": "Inspect the Alpaca paper account and close remaining exposure.",
                }), file=sys.stderr, flush=True)
        journal.close()
    return 2
