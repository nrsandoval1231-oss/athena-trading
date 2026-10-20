"""Opt-in high-frequency paper profile. The one-trade pilot is not this path."""
from __future__ import annotations

import importlib.util
import json
import signal
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from athena_trading.athena_adapter.jev import ModelError, PatternDayTradeError, pattern_day_trade_message
from athena_trading.execution.authority import AuthorityError, ExecutionAuthority, parse_authority
from athena_trading.paper import cloud
from athena_trading.paper.alpaca import PAPER_API_BASE
from athena_trading.paper.high_frequency import (
    DEFAULT_SYMBOLS,
    CallBudget,
    EntryBlocked,
    HighFrequencyPilot,
    HighFrequencyPolicy,
    MinuteBar,
    anchor_protection,
    assert_limit_bracket,
    high_frequency_policy_from_environment,
    plan_bracket,
    require_paper_endpoint,
    simulate_session,
    synthetic_regular_session,
)
from athena_trading.paper.journal import PaperJournal
from athena_trading.paper.learning import maintain
from athena_trading.paper.live import NY, EntryNotPriced, price_marketable_entry, policy_from_environment
from athena_trading.paper.ownership import write_ownership_ledger
from athena_trading.paper.shared_risk import exposures_from_journal

NOW = datetime(2026, 10, 19, 14, 30, tzinfo=UTC)
OPEN = datetime(2026, 10, 19, 13, 30, tzinfo=UTC)


class QuoteJev:
    def __init__(self) -> None:
        self.calls = 0

    def decide(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("Jev is not on the high-frequency entry path")


class FakeBroker:
    def __init__(self) -> None:
        self._base = PAPER_API_BASE
        self.now = NOW
        self.next_close = "2026-10-19T20:00:00+00:00"
        self.open = True
        self.equity = "100000"
        self.last_equity = "100000"
        self.buying_power = "200000"
        self.positions: list[dict] = []
        self.orders: list[dict] = []
        self.posts: list[dict] = []
        self.patches: list[tuple[str, dict]] = []
        self.deletes: list[str] = []
        self.snapshot_count = 0
        self.clear_orders_after_snapshot: int | None = None
        self.fail_post: Exception | None = None
        self.lookup: dict[str, dict] = {}
        self.quotes = {
            "SPY": {"bp": "581.49", "ap": "581.51", "t": NOW.isoformat()},
        }
        # Named apart from bars() so the method stays callable.
        self.bar_rows = {"SPY": _ramped_bars()}
        for symbol, price in (("QQQ", 490), ("IWM", 220), ("DIA", 430),
                              ("AAPL", 230), ("MSFT", 420), ("NVDA", 140), ("AMZN", 190)):
            self.bar_rows[symbol] = _flat_bars(price)
            self.quotes[symbol] = {"bp": f"{price - 0.01:.2f}", "ap": f"{price + 0.01:.2f}", "t": NOW.isoformat()}

    def snapshot(self):
        self.snapshot_count += 1
        if (
            self.clear_orders_after_snapshot is not None
            and self.snapshot_count > self.clear_orders_after_snapshot
        ):
            self.orders = []
        return {
            "clock": {
                "timestamp": self.now.isoformat(), "is_open": self.open, "next_close": self.next_close,
                "next_open": "2026-10-20T13:30:00+00:00",
            },
            "positions": list(self.positions),
            "orders": list(self.orders),
            "account": {
                "id": "paper-account", "equity": self.equity, "last_equity": self.last_equity,
                "buying_power": self.buying_power, "status": "ACTIVE",
                "trading_blocked": False, "account_blocked": False, "trade_suspended_by_user": False,
            },
        }

    def bars(self, symbol, start, end, timeframe, feed):
        return list(self.bar_rows.get(symbol, []))

    def latest_quote(self, symbol, feed):
        return dict(self.quotes[symbol])

    def call(self, method, path, payload=None):
        if method == "GET" and path.startswith("/v2/calendar"):
            return [
                {"date": "2026-10-16", "open": "09:30", "close": "16:00"},
                {"date": "2026-10-19", "open": "09:30", "close": "16:00"},
            ]
        if method == "GET" and path.startswith("/v2/assets/"):
            return {
                "status": "active", "tradable": True, "class": "us_equity",
                "shortable": True, "easy_to_borrow": True,
            }
        if method == "GET" and "orders:by_client_order_id" in path:
            client_id = path.split("client_order_id=", 1)[-1]
            if client_id not in self.lookup:
                raise ModelError("HTTP 404", status=404)
            return self.lookup[client_id]
        if method == "POST" and path == "/v2/orders":
            if self.fail_post is not None:
                raise self.fail_post
            self.posts.append(payload)
            ack = {"id": "ord-" + str(len(self.posts)), "status": "accepted", **payload}
            self.lookup[payload["client_order_id"]] = ack
            self.orders.append(ack)
            return ack
        if method == "PATCH" and path.startswith("/v2/orders/"):
            self.patches.append((path, payload or {}))
            return {"id": path.rsplit("/", 1)[-1], "status": "accepted", **(payload or {})}
        if method == "DELETE":
            self.deletes.append(path)
            if path.startswith("/v2/positions/"):
                symbol = path.rsplit("/", 1)[-1]
                self.positions = [row for row in self.positions if row.get("symbol") != symbol]
            return {"id": "close", "status": "accepted"}
        raise AssertionError(f"unexpected {method} {path}")


def _flat_bars(price: float) -> list[dict]:
    rows = []
    for index in range(60):
        start = OPEN + timedelta(minutes=index)
        rows.append({
            "t": start.isoformat().replace("+00:00", "Z"),
            "o": price, "h": price + 0.05, "l": price - 0.05, "c": price, "v": 1000,
        })
    return rows


def _ramped_bars() -> list[dict]:
    rows = _flat_bars(580.0)
    for index in range(54, 60):
        price = 580.0 + (index - 53) * 0.25
        rows[index].update(o=price - 0.05, h=price + 0.08, l=price - 0.12, c=price)
    return rows


def _pilot(tmp_path, broker, monkeypatch, **policy):
    monkeypatch.setenv("ATHENA_SHARED_RISK_DIR", str(tmp_path / "risk"))
    journal = PaperJournal(tmp_path / "2026-10-19.sqlite")
    pilot = HighFrequencyPilot(
        broker, journal, HighFrequencyPolicy(**policy), session_date=date(2026, 10, 19),
        jev=QuoteJev(),
    )
    return pilot, journal


def test_profile_flag_does_not_change_the_one_trade_policy(monkeypatch):
    monkeypatch.setenv("ATHENA_PAPER_PROFILE", "high-frequency")
    monkeypatch.setenv("ATHENA_HF_SYMBOLS", "SPY,QQQ,IWM,DIA")
    policy = policy_from_environment("SPY")
    assert policy.symbol == "SPY"
    assert policy.max_adjudications == 20
    assert policy.max_notional == 1000
    assert policy.version == "athena-jev-paper-pilot-v3"


def test_ceilings_cannot_be_raised_by_construction_or_environment(monkeypatch):
    with pytest.raises(ValueError, match="1,000"):
        HighFrequencyPolicy(max_notional=1001)
    with pytest.raises(ValueError, match="concurrent"):
        HighFrequencyPolicy(max_concurrent=5)
    with pytest.raises(ValueError, match="0.5%"):
        HighFrequencyPolicy(max_daily_loss_fraction=0.01)
    with pytest.raises(ValueError, match="stop"):
        HighFrequencyPolicy(max_stop_risk=11)
    with pytest.raises(ValueError, match="opt-in"):
        HighFrequencyPolicy(profile="pilot")
    with pytest.raises(ValueError, match="fifteen cents"):
        HighFrequencyPolicy(max_entry_slippage="0.50")
    monkeypatch.setenv("ATHENA_HF_MAX_CONCURRENT", "8")
    with pytest.raises(ValueError, match="concurrent"):
        high_frequency_policy_from_environment()
    monkeypatch.delenv("ATHENA_HF_MAX_CONCURRENT")
    monkeypatch.setenv("ATHENA_HF_JEV", "required")
    with pytest.raises(ValueError, match="ATHENA_HF_JEV"):
        high_frequency_policy_from_environment()


def test_live_authority_and_non_paper_endpoint_are_refused():
    with pytest.raises(AuthorityError):
        parse_authority("LIVE")
    broker = FakeBroker()
    broker._base = "https://api.alpaca.markets"
    with pytest.raises(ValueError, match="paper-api.alpaca.markets"):
        require_paper_endpoint(broker)
    with pytest.raises(ValueError, match="paper-api.alpaca.markets"):
        HighFrequencyPilot(broker, journal=None, policy=HighFrequencyPolicy())
    assert ExecutionAuthority.PAPER.value == "PAPER"


def test_default_slippage_cap_is_unchanged_and_high_frequency_cap_is_still_a_cap():
    with pytest.raises(EntryNotPriced, match="ENTRY_SLIPPAGE_CAP"):
        price_marketable_entry(
            side="buy", signal_close=Decimal("100.00"), bid=Decimal("100.05"),
            ask=Decimal("100.07"), stop=Decimal("99.00"), target=Decimal("102.00"),
        )
    limit, _slippage = price_marketable_entry(
        side="buy", signal_close=Decimal("100.00"), bid=Decimal("100.08"),
        ask=Decimal("100.10"), stop=Decimal("99.00"), target=Decimal("102.00"),
        max_slippage=Decimal("0.15"), max_concession=Decimal("0.17"),
    )
    assert limit == Decimal("100.11")
    with pytest.raises(EntryNotPriced, match="ENTRY_SLIPPAGE_CAP"):
        price_marketable_entry(
            side="buy", signal_close=Decimal("100.00"), bid=Decimal("100.20"),
            ask=Decimal("100.22"), stop=Decimal("99.00"), target=Decimal("102.00"),
            max_slippage=Decimal("0.15"), max_concession=Decimal("0.17"),
        )


def test_plan_rejects_oversized_notional_and_stop_and_never_builds_a_market_order():
    policy = HighFrequencyPolicy()
    now = NOW
    with pytest.raises(EntryBlocked, match="NOTIONAL_CAP"):
        plan_bracket(
            symbol="SPY", direction="LONG", close=2000, high=2000.2, low=1999.8,
            bid=1999.99, ask=2000.01, quote_at=now, now=now, policy=policy,
            equity=100_000, buying_power=200_000, open_notional=0,
        )
    with pytest.raises(EntryBlocked, match="STOP_RISK_CAP"):
        plan_bracket(
            symbol="SPY", direction="LONG", close=100, high=100.2, low=80,
            bid=99.99, ask=100.01, quote_at=now, now=now, policy=policy,
            equity=100_000, buying_power=200_000, open_notional=0,
        )
    order, _pricing = plan_bracket(
        symbol="SPY", direction="LONG", close=581.5, high=581.58, low=581.38,
        bid=581.49, ask=581.51, quote_at=now, now=now, policy=policy,
        equity=100_000, buying_power=200_000, open_notional=0,
    )
    assert_limit_bracket(order)
    assert order["type"] == "limit"
    assert order["order_class"] == "bracket"
    assert order["extended_hours"] is False
    assert "market" not in order.values()


def test_warmup_ignores_a_push_in_the_first_ten_minutes(tmp_path, monkeypatch):
    broker = FakeBroker()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    session_open = datetime(2026, 10, 19, 9, 30, tzinfo=NY)
    bars = []
    for index in range(9):
        price = 580.0 + index * 0.25
        bars.append(MinuteBar(
            session_open + timedelta(minutes=index),
            price - 0.05, price + 0.08, price - 0.12, price,
        ))
    now = session_open + timedelta(minutes=9)
    close_at = datetime(2026, 10, 19, 16, 0, tzinfo=NY)
    assert pilot._signal("SPY", bars, now, close_at, session_open) is None
    journal.close()


def test_entry_is_a_limit_bracket_and_does_not_call_jev(tmp_path, monkeypatch):
    broker = FakeBroker()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    jev = pilot.jev
    assert pilot.tick() == "ENTRY_SUBMITTED"
    assert jev.calls == 0
    assert len(broker.posts) == 1
    order = broker.posts[0]
    assert_limit_bracket(order)
    assert order["symbol"] == "SPY"
    assert order["side"] == "buy"
    assert order["client_order_id"].startswith("athp-")
    assert len(order["client_order_id"]) <= 48
    assert journal.last("hf_entry_intent")["order"]["type"] == "limit"
    assert journal.last("entry_intent") is None
    journal.close()


def test_protection_is_anchored_to_the_fill_and_never_the_wrong_side():
    # 20 Oct: AMZN long filled 264.20 with a stop at 264.24, above the fill.
    # QQQ short filled 711.41 with a stop at 711.42, one cent away.
    amzn_stop, amzn_target = anchor_protection(
        side="buy", fill=Decimal("264.20"), risk=Decimal("0.08"), min_distance=Decimal("0.08"),
    )
    assert amzn_stop == Decimal("264.12")
    assert amzn_target == Decimal("264.28")
    assert amzn_stop < Decimal("264.20") < amzn_target
    qqq_stop, qqq_target = anchor_protection(
        side="sell", fill=Decimal("711.41"), risk=Decimal("0.08"), min_distance=Decimal("0.08"),
    )
    assert qqq_stop == Decimal("711.49")
    assert qqq_target == Decimal("711.33")
    assert qqq_target < Decimal("711.41") < qqq_stop


def test_submitted_stop_stays_beyond_a_better_fill_until_the_fill_is_known():
    policy = HighFrequencyPolicy()
    order, pricing = plan_bracket(
        symbol="AMZN", direction="LONG", close=264.30, high=264.36, low=264.22,
        bid=264.30, ask=264.31, quote_at=NOW, now=NOW, policy=policy,
        equity=100_000, buying_power=200_000, open_notional=0,
    )
    limit = Decimal(order["limit_price"])
    stop = Decimal(order["stop_loss"]["stop_price"])
    target = Decimal(order["take_profit"]["limit_price"])
    assert limit == Decimal("264.32")
    assert limit - stop == Decimal("10.00")
    assert target - limit == Decimal("10.00")
    # The reported 264.20 fill was through a stop priced off the quote. It stays above this one.
    assert stop < Decimal("264.20") < limit
    assert Decimal(pricing["planned_risk"]) >= Decimal("0.08")


def test_filled_bracket_is_replaced_from_the_actual_fill(tmp_path, monkeypatch):
    broker = FakeBroker()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    assert pilot.tick() == "ENTRY_SUBMITTED"
    submitted = broker.posts[0]
    client_id = submitted["client_order_id"]
    fill = "581.20"
    broker.lookup[client_id] = {
        "id": "parent-1", "client_order_id": client_id, "symbol": "SPY", "side": "buy",
        "status": "filled", "qty": "1", "filled_qty": "1", "filled_avg_price": fill,
        "type": "limit",
        "legs": [
            {
                "id": "stop-1", "symbol": "SPY", "side": "sell", "type": "stop", "status": "held",
                "stop_price": submitted["stop_loss"]["stop_price"], "qty": "1", "filled_qty": "0",
            },
            {
                "id": "tp-1", "symbol": "SPY", "side": "sell", "type": "limit", "status": "held",
                "limit_price": submitted["take_profit"]["limit_price"], "qty": "1", "filled_qty": "0",
            },
        ],
    }
    broker.positions = [{"symbol": "SPY", "qty": "1", "side": "long", "avg_entry_price": fill}]
    assert pilot.tick() == "POSITION_OPEN"
    prices = {path.rsplit("/", 1)[-1]: payload for path, payload in broker.patches}
    assert Decimal(prices["stop-1"]["stop_price"]) == Decimal("581.08")
    assert Decimal(prices["tp-1"]["limit_price"]) == Decimal("581.32")
    assert Decimal(prices["stop-1"]["stop_price"]) <= Decimal(fill) - Decimal("0.08")
    anchored = journal.last("hf_protection_anchored")
    assert anchored["fill"] == fill
    assert journal.last("halt") is None
    journal.close()


def test_pending_sibling_after_stop_fill_is_not_unowned(tmp_path, monkeypatch):
    """The 20 Oct IWM halt: the same snapshot still showed the canceled-pending leg."""
    broker = FakeBroker()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    assert pilot.tick() == "ENTRY_SUBMITTED"
    client_id = broker.posts[0]["client_order_id"]
    parent = {
        "id": "parent-iwm", "client_order_id": client_id, "symbol": "SPY", "side": "buy",
        "status": "filled", "qty": "1", "filled_qty": "1", "filled_avg_price": "581.40",
        "type": "limit",
        "legs": [
            {
                "id": "stop-1", "symbol": "SPY", "side": "sell", "type": "stop", "status": "filled",
                "stop_price": "571.52", "qty": "1", "filled_qty": "1",
            },
            {
                "id": "tp-1", "symbol": "SPY", "side": "sell", "type": "limit", "status": "pending_cancel",
                "limit_price": "591.52", "qty": "1", "filled_qty": "0", "parent_id": "parent-iwm",
            },
        ],
    }
    broker.lookup[client_id] = parent
    broker.orders = [parent]
    broker.positions = []
    status = pilot.tick()
    assert journal.last("halt") is None
    assert status == "WAITING_FOR_SIGNAL"
    assert journal.last("hf_position_closed")["reason"] in {"STOP_FILLED", "POSITION_FLAT"}
    assert broker.deletes == []
    journal.close()


def test_vanishing_sibling_is_rechecked_before_a_halt(tmp_path, monkeypatch):
    broker = FakeBroker()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    assert pilot.tick() == "ENTRY_SUBMITTED"
    client_id = broker.posts[0]["client_order_id"]
    journal.append(
        "hf_position_closed", client_order_id=client_id, symbol="SPY", reason="STOP_FILLED",
        filled=True, at=broker.now.isoformat(),
    )
    broker.orders = [{
        "id": "mystery-leg", "symbol": "SPY", "status": "pending_cancel", "type": "limit",
        "side": "sell", "client_order_id": "broker-generated", "qty": "1",
    }]
    broker.positions = []
    broker.clear_orders_after_snapshot = broker.snapshot_count + 1
    status = pilot.tick()
    assert journal.last("halt") is None
    assert journal.last("hf_unowned_recheck")["symbols"] == ["SPY"]
    assert status == "WAITING_FOR_SIGNAL"
    journal.close()


def test_persistent_stray_on_a_closed_symbol_still_halts(tmp_path, monkeypatch):
    broker = FakeBroker()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    assert pilot.tick() == "ENTRY_SUBMITTED"
    client_id = broker.posts[0]["client_order_id"]
    journal.append(
        "hf_position_closed", client_order_id=client_id, symbol="SPY", reason="STOP_FILLED",
        filled=True, at=broker.now.isoformat(),
    )
    broker.orders = [{
        "id": "manual-spy", "symbol": "SPY", "status": "new", "type": "limit", "side": "buy",
        "client_order_id": "manual-1", "qty": "1",
    }]
    broker.positions = []
    pilot.tick()
    assert journal.last("halt")["reason"] == "UNOWNED_EXPOSURE"
    assert journal.last("hf_unowned_recheck") is not None
    journal.close()


def test_real_unowned_order_halts_without_waiting(tmp_path, monkeypatch):
    broker = FakeBroker()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    assert pilot.tick() == "ENTRY_SUBMITTED"
    broker.orders.append({
        "id": "manual-tsla", "symbol": "TSLA", "status": "new", "type": "limit", "side": "buy",
        "client_order_id": "manual-tsla", "qty": "1",
    })
    pilot.tick()
    assert journal.last("halt")["reason"] == "UNOWNED_EXPOSURE"
    assert journal.last("hf_unowned_recheck") is None
    journal.close()


def test_advisory_jev_is_recorded_and_still_not_called(tmp_path, monkeypatch):
    broker = FakeBroker()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch, jev_mode="advisory")
    assert pilot.tick() == "ENTRY_SUBMITTED"
    assert pilot.jev.calls == 0
    assert journal.last("hf_jev_bypassed")["reason"] == "entry does not wait for Jev"
    assert len(broker.posts) == 1
    journal.close()


def test_pattern_day_trade_rejection_stops_new_entries(tmp_path, monkeypatch):
    assert pattern_day_trade_message(403, "potential pattern day trading protection")
    assert pattern_day_trade_message(403, "day trading buying power is insufficient")
    assert not pattern_day_trade_message(403, "asset is not tradable")
    assert not pattern_day_trade_message(401, "pattern day trading protection")
    broker = FakeBroker()
    broker.fail_post = PatternDayTradeError()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    assert pilot.tick() == "PDT_RESTRICTED"
    assert broker.posts == []
    assert journal.last("hf_pdt_restricted") is not None
    assert "pattern day trade" in journal.last("hf_pdt_restricted")["reason"]
    broker.fail_post = None
    assert pilot.tick() == "PDT_RESTRICTED"
    assert broker.posts == []
    journal.close()


def test_rate_budget_and_broker_429_do_not_send_another_entry(tmp_path, monkeypatch):
    broker = FakeBroker()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    pilot.budget = CallBudget(limit=4)
    assert pilot.tick() == "RATE_LIMIT"
    assert broker.posts == []
    pilot.budget = CallBudget()
    broker.fail_post = ModelError("HTTP 429", status=429)
    assert pilot.tick() == "RATE_LIMIT"
    assert broker.posts == []
    broker.fail_post = None
    pilot._backoff_until = pilot._clock() + 60
    assert pilot.tick() == "RATE_LIMIT"
    assert broker.posts == []
    journal.close()


def test_daily_loss_halts_and_does_not_enter(tmp_path, monkeypatch):
    broker = FakeBroker()
    broker.equity = "99000"
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    assert pilot.tick() == "FLATTENING"
    assert broker.posts == []
    assert journal.last("halt")["reason"] == "DAILY_LOSS_LIMIT"
    assert pilot.tick() == "COMPLETE"
    assert journal.last("hf_session_flat")["reason"] == "DAILY_LOSS_FLATTEN"
    journal.close()


def test_earnings_symbol_is_not_traded_or_flattened(tmp_path, monkeypatch):
    earnings = tmp_path / "earnings"
    write_ownership_ledger(earnings, {"SPY"}, [])
    monkeypatch.setenv("ATHENA_EARNINGS_STATE_DIR", str(earnings))
    broker = FakeBroker()
    broker.positions = [{"symbol": "SPY", "qty": "3", "side": "long"}]
    broker.orders = [{"id": "earn", "client_order_id": "athe-owned", "symbol": "SPY", "status": "new"}]
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    assert pilot.tick() == "WAITING_FOR_SIGNAL"
    assert broker.posts == []
    assert broker.deletes == []
    assert broker.positions[0]["qty"] == "3"
    journal.close()


def test_missing_protection_flattens_that_position(tmp_path, monkeypatch):
    broker = FakeBroker()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    assert pilot.tick() == "ENTRY_SUBMITTED"
    client_id = broker.posts[0]["client_order_id"]
    broker.lookup[client_id] = {
        "id": "parent-1", "client_order_id": client_id, "symbol": "SPY", "side": "buy",
        "status": "filled", "qty": "1", "filled_qty": "1", "type": "limit", "legs": [],
    }
    broker.positions = [{"symbol": "SPY", "qty": "1", "side": "long"}]
    assert pilot.tick() == "POSITION_OPEN"
    assert any(path.endswith("/SPY") for path in broker.deletes)
    assert broker.positions == []
    assert not any(order.get("type") == "market" for order in broker.posts)
    journal.close()


def test_time_stop_closes_with_a_position_delete_not_a_market_entry(tmp_path, monkeypatch):
    broker = FakeBroker()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    assert pilot.tick() == "ENTRY_SUBMITTED"
    client_id = broker.posts[0]["client_order_id"]
    broker.lookup[client_id] = {
        "id": "parent-1", "client_order_id": client_id, "symbol": "SPY", "side": "buy",
        "status": "filled", "qty": "1", "filled_qty": "1", "type": "limit",
        "legs": [{
            "id": "stop-1", "symbol": "SPY", "side": "sell", "type": "stop", "status": "held",
            "stop_price": "580.00", "qty": "1", "filled_qty": "0",
        }],
    }
    broker.positions = [{"symbol": "SPY", "qty": "1", "side": "long"}]
    assert pilot.tick() == "POSITION_OPEN"
    assert broker.deletes == []
    broker.now = NOW + timedelta(minutes=9)
    broker.quotes["SPY"]["t"] = broker.now.isoformat()
    pilot.tick()
    assert any(path == "/v2/positions/SPY" for path in broker.deletes)
    assert all(order.get("type") == "limit" for order in broker.posts)
    journal.close()


def test_flatten_before_close_cancels_entries_and_does_not_open_new_ones(tmp_path, monkeypatch):
    broker = FakeBroker()
    pilot, journal = _pilot(tmp_path, broker, monkeypatch)
    assert pilot.tick() == "ENTRY_SUBMITTED"
    broker.now = datetime(2026, 10, 19, 19, 50, tzinfo=UTC)
    broker.quotes["SPY"]["t"] = broker.now.isoformat()
    status = pilot.tick()
    assert status == "FLATTENING"
    assert journal.last("halt")["reason"] == "SESSION_CLOSE"
    assert len(broker.posts) == 1
    assert any(path.startswith("/v2/orders/") for path in broker.deletes)
    journal.close()


def test_shared_exposure_reserves_and_releases_high_frequency_intents(tmp_path):
    journal = PaperJournal(tmp_path / "book.sqlite")
    order = {
        "client_order_id": "athp-hf", "symbol": "QQQ", "qty": "1", "limit_price": "490.02",
        "time_in_force": "day", "stop_loss": {"stop_price": "489.50"},
    }
    journal.append("hf_entry_intent", order=order)
    assert [row.symbol for row in exposures_from_journal(journal, "intraday")] == ["QQQ"]
    journal.append("hf_position_closed", client_order_id="athp-hf", symbol="QQQ", filled=True)
    assert exposures_from_journal(journal, "intraday") == []
    journal.append("hf_entry_intent", order={**order, "client_order_id": "athp-hf-2", "symbol": "IWM"})
    journal.append("hf_session_flat", reason="SESSION_FLATTEN")
    assert exposures_from_journal(journal, "intraday") == []
    journal.close()


def test_high_frequency_journal_does_not_enter_the_one_trade_learning_study(tmp_path):
    journal = PaperJournal(tmp_path / "2026-10-19.sqlite")
    journal.append("binding", binding={
        "account_id": "paper-account",
        "policy": {"profile": "high-frequency", "symbols": list(DEFAULT_SYMBOLS), "feed": "iex"},
    })
    journal.append("hf_entry_intent", session="2026-10-19", order={
        "client_order_id": "athp-hf", "symbol": "SPY", "qty": "1", "limit_price": "580",
        "stop_loss": {"stop_price": "579"},
    })
    journal.append("hf_session_flat", reason="SESSION_FLATTEN")
    journal.append("session_complete", status="COMPLETE")
    journal.close()
    maintain(tmp_path, datetime(2026, 10, 19, 22, 0, tzinfo=UTC))
    task = cloud.next_session(tmp_path, date(2026, 10, 19))
    assert task is None


def test_unresolved_high_frequency_session_is_recovered_without_a_symbol_flag(tmp_path):
    path = tmp_path / "2026-10-19.sqlite"
    journal = PaperJournal(path)
    journal.append("binding", binding={
        "account_id": "paper-account",
        "policy": {"profile": "high-frequency", "feed": "iex", "symbols": ["SPY", "QQQ", "IWM", "DIA"]},
    })
    journal.append("hf_entry_intent", order={"client_order_id": "athp-hf", "symbol": "SPY"})
    journal.close()
    task = cloud.next_session(tmp_path, date(2026, 10, 20))
    assert task is not None and task.recovery and task.path == path
    command = cloud.session_command(task, cloud.PilotPolicy("MSFT"), Path("runner.py"))
    assert "--profile" in command and "high-frequency" in command
    assert "--symbol" not in command


def test_cloud_high_frequency_does_not_require_a_model_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ATHENA_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ATHENA_PAPER_PROFILE", "high-frequency")
    monkeypatch.delenv("ATHENA_SYMBOL", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    started = []
    handlers = {}

    class Broker:
        def call(self, *args):
            return {"timestamp": "2026-10-19T14:30:00+00:00", "is_open": True}

    class Child:
        returncode = 0
        pid = 7

        def poll(self):
            return 0

    def spawn(command):
        started.append(command)
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return Child()

    monkeypatch.setattr(cloud.LivePaperAPI, "from_env", lambda: Broker())
    monkeypatch.setattr(cloud.subprocess, "Popen", spawn)
    monkeypatch.setattr(cloud.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(cloud.signal, "signal", lambda sig, handler: handlers.update({sig: handler}))
    assert cloud.main() == 0
    assert started
    assert "--profile" in started[0] and "high-frequency" in started[0]
    assert "TYPESAFE" not in json.dumps(started[0])


def test_typical_session_generates_about_one_hundred_round_trips():
    policy = HighFrequencyPolicy()
    counts = {}
    for seed in (20261019, 7, 11, 42, 99, 123, 256):
        report = simulate_session(synthetic_regular_session(seed), policy)
        counts[seed] = report.round_trips
        assert report.entries == report.limit_brackets == report.round_trips
        assert report.open_at_end == 0
        assert report.max_concurrent <= policy.max_concurrent
        assert report.daily_loss_halt is False
        assert report.round_trip_cap_hit is False
        assert set(report.exits_by_reason) <= {"stop", "target", "time", "flatten", "daily_loss"}
        assert 80 <= report.round_trips <= 130
    assert counts[20261019] == 97, counts
    assert min(counts.values()) >= 90
    assert max(counts.values()) <= 110


def test_runner_requires_a_symbol_only_for_the_default_profile(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_athena_paper.py"
    spec = importlib.util.spec_from_file_location("run_athena_paper_hf", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(SystemExit):
        module.main(["--authority", "PAPER", "--journal", str(tmp_path / "j.sqlite"), "--preflight"])
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "paper-secret")
    monkeypatch.setenv("ATHENA_SHARED_RISK_DIR", str(tmp_path / "risk"))

    class Broker(FakeBroker):
        pass

    broker = Broker()
    monkeypatch.setattr(
        "athena_trading.paper.high_frequency.LivePaperAPI.from_env", lambda: broker,
    )
    code = module.main([
        "--profile", "high-frequency", "--authority", "PAPER",
        "--journal", str(tmp_path / "preflight.sqlite"), "--preflight",
    ])
    assert code == 0
