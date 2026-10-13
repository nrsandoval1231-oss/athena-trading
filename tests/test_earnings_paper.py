"""Earnings Adaptive paper book. Fixtures use Alpaca-shaped payloads; nothing is sent."""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest

from athena_trading.athena_adapter.jev import JEV_MODEL, JevDecisionEngine, ModelError, questions
from athena_trading.execution.authority import AuthorityError, ExecutionAuthority, parse_authority
from athena_trading.paper.earnings import (
    EarningsBook, EarningsPolicy, bracket_order, earnings_client_order_id, earnings_questions,
    entry_session, hold_action, load_universe, plan_entry, policy_from_environment,
)
from athena_trading.paper.earnings_calendar import EarningsReport, parse_nasdaq_calendar
from athena_trading.paper.journal import PaperJournal
from athena_trading.paper.ownership import EARNINGS_ORDER_PREFIX, INTRADAY_ORDER_PREFIX

NOW = datetime(2026, 10, 13, 13, 40, 5, tzinfo=UTC)
STOP_ID = "8f6c1d2e-1b3a-4c5d-9e7f-0a1b2c3d4e5f"
TAKE_ID = "7e5b0c1d-0a29-4b4c-8d6e-9f0a1b2c3d4e"


def jev_response(choice="TAKE_TRADE", confidence=0.9):
    return {"model": JEV_MODEL, "usage": {"input_tokens": 300, "output_tokens": 50},
            "answers": {"entry_decision": {
                "type": "choice", "choice": choice, "confidence": confidence,
                "probabilities": {opt: 0.9 if opt == choice else 0.05
                                  for opt in ("TAKE_TRADE", "NO_TRADE", "INSUFFICIENT_EVIDENCE")},
            }}}


def calendar_rows():
    return [
        {"date": "2026-10-09", "open": "09:30", "close": "16:00"},
        {"date": "2026-10-12", "open": "09:30", "close": "16:00"},
        {"date": "2026-10-13", "open": "09:30", "close": "16:00"},
        {"date": "2026-10-14", "open": "09:30", "close": "16:00"},
        {"date": "2026-10-15", "open": "09:30", "close": "16:00"},
        {"date": "2026-10-16", "open": "09:30", "close": "16:00"},
    ]


def minute_bars(close=104.0):
    start = datetime(2026, 10, 13, 13, 30, tzinfo=UTC)
    return [{
        "t": (start + timedelta(minutes=i)).isoformat(),
        "o": close - 0.4, "h": close + 0.2, "l": close - 0.6, "c": close, "v": 12000,
    } for i in range(10)]


def nasdaq(day_has_deck=True):
    def transport(method, url, headers, payload=None, timeout=5):
        assert method == "GET"
        assert "api.nasdaq.com/api/calendar/earnings" in url
        assert "Mozilla" in headers["User-Agent"]
        if "date=2026-10-13" in url and day_has_deck:
            return {"data": {"asOf": "Tue, 13 Oct 2026 00:00:00 -0400", "rows": [
                {"symbol": "DECK", "name": "Deckers Outdoor Corporation", "time": "time-pre-market",
                 "marketCap": "$15,200,000,000", "fiscalQuarterEnding": "Sep/2026",
                 "epsForecast": "$1.40", "noOfEsts": "12", "lastYearRptDt": "10/24/2025",
                 "lastYearEPS": "$1.25"},
                {"symbol": "ETSY", "name": "Etsy Inc.", "time": "time-not-supplied",
                 "marketCap": "$6,000,000,000", "epsForecast": "$0.50", "noOfEsts": "8"},
                {"symbol": "BRK/B", "name": "Berkshire", "time": "time-pre-market"},
            ]}, "message": None, "status": {"rCode": 200, "bCodeMessage": None}}
        return {"data": {"calendar": {"rows": None}}, "status": {"rCode": 200}}
    return transport


class BookBroker:
    """In-memory Alpaca paper. Payloads follow the nested bracket shape."""

    def __init__(self, close=104.0):
        self.close = close
        self.state = {
            "clock": {"timestamp": NOW.isoformat(), "is_open": True,
                      "next_close": "2026-10-13T20:00:00Z"},
            "account": {"id": "paper-account", "equity": "100000", "last_equity": "100000",
                        "buying_power": "100000", "status": "ACTIVE", "trading_blocked": False,
                        "account_blocked": False, "trade_suspended_by_user": False},
            "positions": [], "orders": [],
        }
        self.calls = []
        self.parents = {}
        self.lose_submit = False
        self.drop_submit = False
        self.hidden = set()
        self.fail_get = None

    def snapshot(self):
        return copy.deepcopy(self.state)

    def bars(self, symbol, start, end, timeframe, feed):
        if timeframe == "1Day":
            return [{"t": "2026-10-12T20:00:00Z", "o": 100, "h": 103, "l": 99, "c": 100,
                     "v": 2_500_000}]
        return minute_bars(self.close)

    def call(self, method, path, payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if path.startswith("/v2/calendar"):
            return calendar_rows()
        if path.startswith("/v2/assets/"):
            return {"id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415", "class": "us_equity",
                    "exchange": "NASDAQ", "symbol": path.rsplit("/", 1)[-1], "status": "active",
                    "tradable": True, "marginable": True, "shortable": True, "easy_to_borrow": True,
                    "fractionable": True}
        if method == "POST" and path == "/v2/orders":
            if self.drop_submit:
                raise ModelError("timeout")
            order = _accepted_bracket(payload)
            self.parents[payload["client_order_id"]] = order
            self.state["orders"] = [copy.deepcopy(order)]
            if self.lose_submit:
                raise ModelError("timeout")
            return copy.deepcopy(order)
        if path.startswith("/v2/orders:by_client_order_id"):
            client_order_id = path.split("client_order_id=", 1)[1]
            if self.fail_get is not None:
                raise self.fail_get
            if client_order_id in self.hidden or client_order_id not in self.parents:
                raise ModelError("HTTP 404", status=404)
            return copy.deepcopy(self.parents[client_order_id])
        if method == "DELETE" and path.startswith("/v2/orders/"):
            return None
        if method == "DELETE" and path.startswith("/v2/positions/"):
            symbol = path.rsplit("/", 1)[-1]
            return {
                "id": "c0ffee00-0000-4000-8000-000000000001",
                "client_order_id": "broker-close-" + symbol,
                "symbol": symbol, "qty": "4", "filled_qty": "0", "side": "sell",
                "type": "market", "order_class": "simple", "time_in_force": "day",
                "status": "new", "extended_hours": False,
            }
        if path.startswith("/v2/orders?"):
            return [copy.deepcopy(order) for order in self.parents.values()]
        raise AssertionError((method, path))


def _accepted_bracket(payload):
    """Shape returned by POST /v2/orders for a new bracket, before either leg is held."""
    return {
        "id": "61e69015-8549-4bfd-b9c3-a2d9e0f0e0a1",
        "client_order_id": payload["client_order_id"],
        "created_at": "2026-10-13T13:40:01.102282Z",
        "updated_at": "2026-10-13T13:40:01.102282Z",
        "submitted_at": "2026-10-13T13:40:01.098000Z",
        "filled_at": None, "expired_at": None, "canceled_at": None, "failed_at": None,
        "replaced_at": None, "replaced_by": None, "replaces": None,
        "asset_id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
        "symbol": payload["symbol"], "asset_class": "us_equity", "notional": None,
        "qty": payload["qty"], "filled_qty": "0", "filled_avg_price": None,
        "order_class": payload["order_class"], "order_type": payload["type"],
        "type": payload["type"], "side": payload["side"],
        "time_in_force": payload["time_in_force"], "limit_price": payload["limit_price"],
        "stop_price": None, "status": "new", "extended_hours": payload["extended_hours"],
        "legs": [], "trail_percent": None, "trail_price": None, "hwm": None,
    }


def _leg(client_order_id, **overrides):
    leg = {
        "id": TAKE_ID, "client_order_id": client_order_id,
        "created_at": "2026-10-13T13:40:05.100000Z", "updated_at": "2026-10-13T13:40:05.100000Z",
        "submitted_at": "2026-10-13T13:40:05.100000Z", "filled_at": None, "expired_at": None,
        "canceled_at": None, "failed_at": None, "replaced_at": None, "replaced_by": None,
        "replaces": None, "symbol": "DECK", "asset_class": "us_equity", "notional": None,
        "qty": "19", "filled_qty": "0", "filled_avg_price": None, "order_class": "bracket",
        "order_type": "limit", "type": "limit", "side": "sell", "time_in_force": "gtc",
        "limit_price": "108.00", "stop_price": None, "status": "new", "extended_hours": False,
        "legs": None,
    }
    leg.update(overrides)
    return leg


def fill_bracket(broker, client_order_id, *, stop_status="held"):
    parent = broker.parents[client_order_id]
    qty = parent["qty"]
    parent.update(status="filled", filled_qty=qty, filled_avg_price=parent["limit_price"],
                  filled_at="2026-10-13T13:40:04.880000Z",
                  updated_at="2026-10-13T13:40:04.880000Z")
    take = _leg(client_order_id + "-tp", id=TAKE_ID, qty=qty, status="new", type="limit",
                order_type="limit", limit_price="108.00", stop_price=None)
    stop = _leg(client_order_id + "-stop", id=STOP_ID, qty=qty, status=stop_status, type="stop",
                order_type="stop", limit_price=None, stop_price="102.00",
                filled_at="2026-10-13T13:40:10Z" if stop_status == "filled" else None)
    parent["legs"] = [take, stop]
    broker.state["orders"] = [copy.deepcopy(take)]
    broker.state["positions"] = [{
        "asset_id": "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415",
        "symbol": "DECK", "exchange": "NASDAQ", "asset_class": "us_equity",
        "avg_entry_price": parent["limit_price"], "qty": qty, "side": "long",
        "market_value": "1976.00", "cost_basis": "1976.00", "unrealized_pl": "0",
        "unrealized_plpc": "0", "current_price": parent["limit_price"],
        "lastday_price": "100.00", "change_today": "0.04", "qty_available": qty,
    }]


def _book(tmp_path, monkeypatch, *, close=104.0, choice="TAKE_TRADE", policy=None, names=("DECK", "CROX"),
         folder=None):
    monkeypatch.delenv("ATHENA_EARNINGS_STATE_DIR", raising=False)
    root = folder or tmp_path
    root.mkdir(parents=True, exist_ok=True)
    universe = root / "universe_75.txt"
    universe.write_text("\n".join(names) + "\n")
    journal = PaperJournal(root / "book.sqlite")
    broker = BookBroker(close=close)
    calls = {"n": 0}

    def transport(*args, **kwargs):
        calls["n"] += 1
        return jev_response(choice)

    jev = JevDecisionEngine("fixture-key", transport=transport)
    policy = policy or EarningsPolicy(str(universe))
    book = EarningsBook(broker, jev, journal, policy, calendar_transport=nasdaq())
    return book, broker, journal, calls


def _posts(broker):
    return [payload for method, path, payload in broker.calls if method == "POST" and path == "/v2/orders"]


def test_live_authority_is_rejected_and_absent_cannot_submit(tmp_path, monkeypatch):
    with pytest.raises(AuthorityError):
        parse_authority("LIVE")
    book, broker, journal, _calls = _book(tmp_path, monkeypatch)
    book.authority = ExecutionAuthority.ABSENT
    with pytest.raises(AuthorityError):
        book._commit(
            {}, {}, {"price": 1, "shares": 1, "side": "buy"},
            EarningsReport("DECK", "2026-10-13", "BMO"),
            account_id="paper-account", observed_at=NOW, session_day="2026-10-13", verdict_hash="x",
        )
    assert _posts(broker) == []
    journal.close()


def test_gap_band_stop_target_and_risk_caps():
    kwargs = dict(
        open_risk=0, open_notional=0, equity=100_000, buying_power=100_000,
        per_trade_risk=50, total_risk=150, max_notional_per_trade=2000, max_notional_total=6000,
        gap_low=0.04, gap_high=0.12, stop_gap_fraction=0.5, target_r=2,
    )
    long = plan_entry(price=104, prior_close=100, **kwargs)
    assert long["direction"] == "LONG" and long["shares"] == 19
    assert long["stop"] == 102 and long["target"] == 108 and long["risk"] == 38
    assert plan_entry(price=103.99, prior_close=100, **kwargs) is None
    assert plan_entry(price=112, prior_close=100, **kwargs)["gap_fraction"] == pytest.approx(0.12)
    assert plan_entry(price=112.02, prior_close=100, **kwargs) is None
    short = plan_entry(price=96, prior_close=100, **kwargs)
    assert short["direction"] == "SHORT" and short["stop"] == 98 and short["target"] == 92
    assert plan_entry(price=3000, prior_close=2880, **kwargs) is None
    blocked = plan_entry(price=104, prior_close=100, **{**kwargs, "open_risk": 150})
    assert blocked is None
    order = bracket_order("DECK", long)
    assert order["time_in_force"] == "gtc" and order["extended_hours"] is False
    assert order["order_class"] == "bracket" and order["qty"] == "19"


def test_hold_and_entry_session_follow_the_report_clock():
    sessions = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
    bmo = EarningsReport("DECK", "2026-10-13", "BMO")
    amc = EarningsReport("DECK", "2026-10-13", "AMC")
    assert entry_session(bmo, sessions) == "2026-10-13"
    assert entry_session(amc, sessions) == "2026-10-14"
    close_at = datetime(2026, 10, 15, 20, 0, tzinfo=UTC)
    early = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    late = datetime(2026, 10, 15, 19, 50, tzinfo=UTC)
    assert hold_action("2026-10-13", "2026-10-14", sessions, 3, is_open=True, now=early,
                       close_at=close_at, close_buffer_minutes=15) == "HOLD"
    assert hold_action("2026-10-13", "2026-10-15", sessions, 3, is_open=True, now=early,
                       close_at=close_at, close_buffer_minutes=15) == "HOLD"
    assert hold_action("2026-10-13", "2026-10-15", sessions, 3, is_open=True, now=late,
                       close_at=close_at, close_buffer_minutes=15) == "FLATTEN"
    missed = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)
    assert hold_action("2026-10-13", "2026-10-16", sessions, 3, is_open=True, now=missed,
                       close_at=datetime(2026, 10, 16, 20, 0, tzinfo=UTC),
                       close_buffer_minutes=15) == "FLATTEN"
    assert hold_action("2026-10-13", "2026-10-16", sessions, 3, is_open=False, now=missed,
                       close_at=datetime(2026, 10, 16, 20, 0, tzinfo=UTC),
                       close_buffer_minutes=15) == "WAIT"


def test_client_order_id_is_stable_and_prefixed():
    first = earnings_client_order_id("paper-account", "DECK", "2026-10-13")
    assert first == earnings_client_order_id("paper-account", "DECK", "2026-10-13")
    assert first.startswith(EARNINGS_ORDER_PREFIX) and not first.startswith(INTRADAY_ORDER_PREFIX)
    assert first != earnings_client_order_id("paper-account", "DECK", "2026-10-14")
    assert len(first) <= 48


def test_nasdaq_calendar_parses_both_shapes_and_skips_unknown_time():
    payload = {"data": {"rows": [
        {"symbol": "deck", "time": "time-pre-market"},
        {"symbol": "NKE", "time": "time-after-hours"},
        {"symbol": "ETSY", "time": "time-not-supplied"},
        {"symbol": "BRK/B", "time": "TIME-PRE-MARKET"},
        {"symbol": "", "time": "time-pre-market"},
    ]}}
    reports, skipped = parse_nasdaq_calendar(payload, report_date="2026-10-13")
    assert [(item.symbol, item.when) for item in reports] == [
        ("DECK", "BMO"), ("NKE", "AMC"), ("BRK.B", "BMO"),
    ]
    assert {item["reason"] for item in skipped} >= {"TIME_UNKNOWN", "BAD_SYMBOL"}
    nested, _skipped = parse_nasdaq_calendar(
        {"data": {"calendar": {"rows": [{"symbol": "CROX", "time": "time-after-hours"}]}}},
        report_date="2026-10-12",
    )
    assert nested == [EarningsReport("CROX", "2026-10-12", "AMC")]
    assert parse_nasdaq_calendar({"data": {"rows": None}}, report_date="2026-10-13") == ([], [])
    assert parse_nasdaq_calendar({"data": {}}, report_date="2026-10-13") == ([], [])
    with pytest.raises(ValueError):
        parse_nasdaq_calendar({"data": {"rows": "nope"}}, report_date="2026-10-13")


def test_universe_file_normalizes_and_rejects_an_oversized_list(tmp_path):
    path = tmp_path / "universe_75.txt"
    path.write_text("# screener output\nDECK\nbrk-b\nDECK\n")
    assert load_universe(path) == ["DECK", "BRK.B"]
    path.write_text("\n".join(f"A{i:02d}" for i in range(76)) + "\n")
    with pytest.raises(ValueError, match="75"):
        load_universe(path)


def test_policy_rejects_a_wider_band_and_live_sized_caps(tmp_path, monkeypatch):
    path = str(tmp_path / "universe_75.txt")
    with pytest.raises(ValueError):
        EarningsPolicy(path, gap_high=0.2)
    with pytest.raises(ValueError):
        EarningsPolicy(path, per_trade_risk=500)
    monkeypatch.setenv("ATHENA_EARNINGS_PER_TRADE_RISK", "40")
    monkeypatch.setenv("ATHENA_JEV_MIN_PTAKE", "0.7")
    monkeypatch.setenv("ATHENA_SYMBOL", "spy")
    policy = policy_from_environment(path, "iex")
    assert policy.per_trade_risk == 40 and policy.support_min == 0.7
    assert policy.excluded_symbol == "SPY" and policy.gap_low == 0.04 and policy.gap_high == 0.12


def test_supported_gap_submits_one_gtc_bracket_and_a_restart_does_not(tmp_path, monkeypatch):
    book, broker, journal, calls = _book(tmp_path, monkeypatch)
    assert book.tick() == "ENTRY_SUBMITTED"
    posted = _posts(broker)
    assert len(posted) == 1
    order = posted[0]
    assert order["symbol"] == "DECK" and order["qty"] == "19"
    assert order["type"] == "limit" and order["time_in_force"] == "gtc"
    assert order["extended_hours"] is False and order["order_class"] == "bracket"
    assert order["limit_price"] == "104.00"
    assert order["stop_loss"] == {"stop_price": "102.00"}
    assert order["take_profit"] == {"limit_price": "108.00"}
    assert order["client_order_id"].startswith("athe-")
    assert journal.last("entry_ack")["order"]["status"] == "new"
    assert journal.last("entry_ack")["order"]["filled_qty"] == "0"
    ledger = json.loads((tmp_path / "ownership.json").read_text())
    assert ledger["prefix"] == "athe-" and ledger["symbols"] == ["DECK"]
    assert any(event["reason"] == "TIME_UNKNOWN" for event in journal.events if event["kind"] == "calendar_skip")
    fill_bracket(broker, order["client_order_id"])
    assert book.tick() == "POSITION_OPEN"
    assert journal.last("entry_intent")["apde_state"] == "EXPLORE"
    restarted = EarningsBook(
        broker, book.jev, journal, book.policy, calendar_transport=nasdaq(),
    )
    broker.state["clock"]["timestamp"] = "2026-10-14T13:50:00Z"
    broker.state["clock"]["next_close"] = "2026-10-14T20:00:00Z"
    assert restarted.tick() == "POSITION_OPEN"
    assert len(_posts(broker)) == 1
    assert calls["n"] == 1
    journal.close()


def test_held_stop_counts_as_protection_and_a_missing_stop_flattens_only_that_symbol(tmp_path, monkeypatch):
    book, broker, journal, calls = _book(tmp_path, monkeypatch)
    book.tick()
    client_order_id = _posts(broker)[0]["client_order_id"]
    fill_bracket(broker, client_order_id, stop_status="canceled")
    broker.state["orders"].append({
        "id": "athp-stop-id", "client_order_id": "athp-aapl-stop", "symbol": "AAPL",
        "qty": "1", "filled_qty": "0", "side": "sell", "type": "stop", "status": "held",
        "stop_price": "99", "time_in_force": "day", "order_class": "bracket",
    })
    calls["n"] = 0
    book.jev = JevDecisionEngine("fixture-key", transport=lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("protective exit must not call Jev")))
    assert book.tick() == "FLATTENING"
    assert journal.last("trade_exit")["reason"] == "PROTECTION_MISSING"
    deleted = [path for method, path, _ in broker.calls if method == "DELETE"]
    assert deleted
    assert all("AAPL" not in path and "athp-stop-id" not in path for path in deleted)
    assert calls["n"] == 0
    journal.close()


def test_max_hold_flattens_on_the_third_session_and_survives_the_book_file(tmp_path, monkeypatch):
    book, broker, journal, _calls = _book(tmp_path, monkeypatch)
    book.tick()
    client_order_id = _posts(broker)[0]["client_order_id"]
    fill_bracket(broker, client_order_id)
    broker.state["clock"]["timestamp"] = "2026-10-15T19:50:00Z"
    broker.state["clock"]["next_close"] = "2026-10-15T20:00:00Z"
    assert book.tick() == "FLATTENING"
    assert journal.last("trade_exit")["reason"] == "MAX_HOLD"
    for leg in broker.parents[client_order_id]["legs"]:
        leg["status"] = "canceled"
    broker.state["orders"] = []
    assert book.tick() == "FLATTENING"
    assert any(path == "/v2/positions/DECK" for method, path, _ in broker.calls if method == "DELETE")
    broker.state["positions"] = []
    assert book.tick() == "FLATTENING"
    assert book.tick() == "NO_CANDIDATE"
    assert journal.last("position_closed")["client_order_id"] == client_order_id
    assert journal.last("earnings_experience")["eligible_for_intraday_study"] is False
    ledger = json.loads((tmp_path / "ownership.json").read_text())
    assert ledger["symbols"] == []
    journal.close()


def test_lost_submit_and_a_second_process_do_not_resend(tmp_path, monkeypatch):
    book, broker, journal, _calls = _book(tmp_path, monkeypatch)
    broker.lose_submit = True
    with pytest.raises(ModelError):
        book.tick()
    assert len(_posts(broker)) == 1
    broker.lose_submit = False
    recovered = EarningsBook(broker, book.jev, journal, book.policy, calendar_transport=nasdaq())
    assert recovered.tick() == "ENTRY_WORKING"
    assert len(_posts(broker)) == 1
    journal.close()


def test_two_flat_404s_close_the_reservation_without_a_new_order(tmp_path, monkeypatch):
    book, broker, journal, _calls = _book(tmp_path, monkeypatch)
    broker.drop_submit = True
    with pytest.raises(ModelError):
        book.tick()
    assert journal.last("entry_ack") is None
    assert len(_posts(broker)) == 1
    broker.drop_submit = False
    assert book.tick() == "ORDER_NOT_FOUND_RECHECK"
    assert book.tick() == "ENTRY_NOT_CREATED"
    assert len(_posts(broker)) == 1
    assert journal.last("entry_not_created")["reason"] == "BROKER_ORDER_NOT_FOUND"
    journal.close()


def test_jev_veto_and_a_bad_answer_do_not_order_or_flatten(tmp_path, monkeypatch):
    book, broker, journal, _calls = _book(tmp_path, monkeypatch, choice="NO_TRADE")
    assert book.tick() == "JEV_VETO"
    assert journal.last("adjudication")["receipt"]["accepted"] is False
    assert _posts(broker) == []
    journal.close()

    universe = tmp_path / "universe_75.txt"
    journal = PaperJournal(tmp_path / "bad.sqlite")
    broker = BookBroker()

    def bad(*args, **kwargs):
        payload = jev_response()
        payload["answers"]["entry_decision"]["probabilities"] = {
            "TAKE_TRADE": 0.9, "NO_TRADE": 0.9, "INSUFFICIENT_EVIDENCE": 0.9,
        }
        return payload

    book = EarningsBook(
        broker, JevDecisionEngine("fixture-key", transport=bad), journal,
        EarningsPolicy(str(universe)), calendar_transport=nasdaq(),
    )
    assert book.tick() == "JEV_VETO"
    assert journal.last("jev_veto")["accepted"] is False
    assert _posts(broker) == []
    assert journal.last("trade_exit") is None
    journal.close()


def test_model_outage_does_not_flatten_an_open_earnings_position(tmp_path, monkeypatch):
    universe = tmp_path / "universe_75.txt"
    universe.write_text("DECK\nCROX\n")
    journal = PaperJournal(tmp_path / "book.sqlite")
    broker = BookBroker()
    calls = {"n": 0}

    def transport(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return jev_response()
        raise ModelError("HTTP 503", status=503)

    def calendar(method, url, headers, payload=None, timeout=5):
        body = nasdaq()(method, url, headers, payload, timeout)
        if "date=2026-10-13" in url:
            body["data"]["rows"].append(
                {"symbol": "CROX", "time": "time-pre-market", "name": "Crocs", "epsForecast": "$2"},
            )
        return body

    policy = EarningsPolicy(str(universe), max_new_entries_per_day=2)
    book = EarningsBook(
        broker, JevDecisionEngine("fixture-key", transport=transport), journal, policy,
        calendar_transport=calendar,
    )
    assert book.tick() == "ENTRY_SUBMITTED"
    fill_bracket(broker, _posts(broker)[0]["client_order_id"])
    assert book.tick() == "JEV_UNAVAILABLE"
    assert broker.state["positions"][0]["symbol"] == "DECK"
    assert not any(method == "DELETE" and "DECK" in path for method, path, _ in broker.calls)
    journal.close()


def test_read_timeout_while_open_is_not_a_flatten(tmp_path, monkeypatch):
    book, broker, journal, _calls = _book(tmp_path, monkeypatch)
    book.tick()
    fill_bracket(broker, _posts(broker)[0]["client_order_id"])
    broker.fail_get = ModelError("request failed: TimeoutError")
    with pytest.raises(ModelError, match="TimeoutError"):
        book.tick()
    assert broker.state["positions"]
    assert not any(method == "DELETE" for method, _path, _payload in broker.calls)
    journal.close()


def test_partial_fill_and_quantity_mismatch_do_not_adopt_the_position(tmp_path, monkeypatch):
    book, broker, journal, _calls = _book(tmp_path, monkeypatch)
    book.tick()
    client_order_id = _posts(broker)[0]["client_order_id"]
    parent = broker.parents[client_order_id]
    parent.update(status="partially_filled", filled_qty="1")
    broker.state["positions"] = [{
        "symbol": "DECK", "qty": "1", "side": "long", "asset_class": "us_equity",
        "avg_entry_price": "104.00",
    }]
    broker.state["orders"] = [copy.deepcopy(parent)]
    assert book.tick() == "FLATTENING"
    assert journal.last("trade_exit")["reason"] == "PARTIAL_ENTRY"
    journal.close()

    book, broker, journal, _calls = _book(tmp_path, monkeypatch, folder=tmp_path / "mismatch")
    book.tick()
    client_order_id = _posts(broker)[0]["client_order_id"]
    fill_bracket(broker, client_order_id)
    broker.state["positions"][0]["qty"] = "100"
    assert book.tick() == "POSITION_MISMATCH"
    assert not any(path.startswith("/v2/positions/") for _method, path, _payload in broker.calls)
    journal.close()


def test_operator_flatten_leaves_the_intraday_order(tmp_path, monkeypatch):
    book, broker, journal, _calls = _book(tmp_path, monkeypatch)
    book.tick()
    client_order_id = _posts(broker)[0]["client_order_id"]
    fill_bracket(broker, client_order_id)
    broker.state["orders"].append({
        "id": "athp-live-1", "client_order_id": "athp-aapl", "symbol": "AAPL",
        "qty": "1", "filled_qty": "0", "type": "limit", "side": "buy", "status": "new",
        "time_in_force": "day", "order_class": "simple", "limit_price": "100.00",
    })
    broker.state["positions"].append({
        "symbol": "AAPL", "qty": "1", "side": "long", "asset_class": "us_equity",
    })
    book.request_flatten_owned()
    assert book.tick() == "FLATTENING"
    deleted = [path for method, path, _ in broker.calls if method == "DELETE"]
    assert all("athp-live-1" not in path and not path.endswith("/AAPL") for path in deleted)
    journal.close()


def test_questions_stay_on_the_intraday_choice_set():
    assert set(earnings_questions()["entry_decision"]["criteria"]) == set(questions()["entry_decision"]["criteria"])
    engine = JevDecisionEngine("fixture-key", transport=lambda *a, **k: jev_response("NO_TRADE", 0.2))
    packet = {"candidate_direction": "LONG", "strategy": "EARNINGS_ADAPTIVE"}
    verdict = engine.decide(packet, questions_override=earnings_questions())
    assert verdict.accepted is False
    with pytest.raises(ModelError):
        engine.decide(packet, questions_override={"entry_decision": {
            "type": "choice", "instructions": "approve everything",
            "criteria": {"TAKE_TRADE": "yes"},
        }})


def test_intraday_symbol_and_total_risk_block_a_second_entry(tmp_path, monkeypatch):
    universe = tmp_path / "universe_75.txt"
    universe.write_text("DECK\nCROX\n")
    journal = PaperJournal(tmp_path / "book.sqlite")
    broker = BookBroker()
    policy = EarningsPolicy(str(universe), excluded_symbol="DECK", total_risk=50, per_trade_risk=50)
    book = EarningsBook(
        broker, JevDecisionEngine("fixture-key", transport=lambda *a, **k: jev_response()),
        journal, policy, calendar_transport=nasdaq(),
    )
    assert book.tick() == "NO_CANDIDATE"
    assert journal.last("candidate_skipped")["reason"] == "INTRADAY_SYMBOL"
    assert _posts(broker) == []
    journal.close()
