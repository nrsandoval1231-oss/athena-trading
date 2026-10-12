from __future__ import annotations

import copy
import importlib.util
import sqlite3
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError

import pytest

from athena_trading.athena_adapter.jev import (
    JEV_ENDPOINT, JEV_MODEL, JevDecisionEngine, ModelError, questions, request_json,
)
from athena_trading.execution.authority import (
    AuthorityError, ExecutionAuthority, parse_authority,
)
from athena_trading.paper import cloud
from athena_trading.paper.journal import PaperJournal
from athena_trading.paper.live import (
    ActivePaperPilot, DataDeferral, LivePaperAPI, PilotPolicy, policy_from_environment,
    signal_packet,
)

NOW = datetime(2026, 9, 24, 13, 46, 5, tzinfo=UTC)
OPEN = NOW.replace(minute=30, second=0)
POLICY = PilotPolicy("AAPL")


def response(choice="TAKE_TRADE", confidence=0.9):
    return {"model": JEV_MODEL, "usage": {"input_tokens": 300, "output_tokens": 50},
            "answers": {key: {"type": "choice", "choice": choice,
                              "confidence": confidence,
                              "probabilities": {opt: 0.9 if opt == choice else 0.05
                                                for opt in ("TAKE_TRADE", "NO_TRADE", "INSUFFICIENT_EVIDENCE")}}
                        for key in questions()}}


def scored_response(take, confidence, choice="TAKE_TRADE"):
    names = ("TAKE_TRADE", "NO_TRADE", "INSUFFICIENT_EVIDENCE")
    rest = (1.0 - take) / 2.0
    probabilities = {name: take if name == choice else rest for name in names}
    return {"model": JEV_MODEL, "usage": {"input_tokens": 300, "output_tokens": 50},
            "answers": {"entry_decision": {
                "type": "choice", "choice": choice, "confidence": confidence,
                "probabilities": probabilities,
            }}}


def market_bars():
    minute = [{"t": (OPEN + timedelta(minutes=i)).isoformat(), "o": 100, "h": 101,
               "l": 99, "c": 100, "v": 1000} for i in range(15)]
    minute.append({"t": (OPEN + timedelta(minutes=15)).isoformat(), "o": 100.5,
                   "h": 101.8, "l": 100.2, "c": 101.5, "v": 1000})
    daily = [{"t": (OPEN - timedelta(days=i)).isoformat(), "o": 100, "h": 102,
              "l": 98, "c": 100, "v": 2_000_000} for i in range(20, 0, -1)]
    return minute, daily


def snapshot():
    return {"clock": {"timestamp": NOW.isoformat(), "is_open": True,
                      "next_close": NOW.replace(hour=20, minute=0, second=0).isoformat()},
            "account": {"id": "paper-account", "equity": "100000", "last_equity": "100000",
                        "buying_power": "100000", "status": "ACTIVE", "trading_blocked": False},
            "positions": [], "orders": []}


class Broker:
    def __init__(self):
        self.state = snapshot()
        self.calls = []
        self.parent = None
        self.lose_submit_response = False
        self.reject_submit = False
        self.hide_order = False
        self.journal = None

    def snapshot(self):
        return copy.deepcopy(self.state)

    def bars(self, symbol, start, end, timeframe, feed):
        return market_bars()[0 if timeframe == "1Min" else 1]

    def call(self, method, path, payload=None):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if path.startswith("/v2/calendar"):
            return [{"date": "2026-09-23", "open": "09:30", "close": "16:00"},
                    {"date": "2026-09-24", "open": "09:30", "close": "16:00"}]
        if path.startswith("/v2/assets/"):
            return {"status": "active", "tradable": True, "class": "us_equity",
                    "shortable": True, "easy_to_borrow": True}
        if method == "POST":
            if self.reject_submit:
                raise ModelError("HTTP 422", status=422)
            assert self.journal.last("entry_intent"), "reservation must be durable before send"
            self.parent = {**payload, "id": "entry-1", "status": "new", "filled_qty": "0"}
            self.state["orders"] = [copy.deepcopy(self.parent)]
            if self.lose_submit_response:
                raise ModelError("timeout")
            return copy.deepcopy(self.parent)
        if path.startswith("/v2/orders:by_client_order_id"):
            if self.hide_order or self.parent is None:
                raise ModelError("HTTP 404", status=404)
            return copy.deepcopy(self.parent)
        if path.startswith("/v2/orders?"):
            return [copy.deepcopy(self.parent)] if self.parent else []
        if method == "DELETE":
            assert self.journal.last("halt"), "halt must precede any cleanup side effect"
            if path.startswith("/v2/positions/"):
                result = {"id": "close-1", "client_order_id": "broker-close", "symbol": "AAPL",
                          "qty": "1", "side": "sell", "type": "market", "order_class": "simple",
                          "status": "new", "filled_qty": "0"}
                self.state["orders"] = [result]
                return result
            # A cancel acknowledgement deliberately does NOT update broker state in this fixture.
            return None
        raise AssertionError((method, path, payload))


class DecisionHook:
    def __init__(self):
        self.action = "TAKE_TRADE"
        self.after = lambda: None

    def transport(self, *args, **kwargs):
        self.after()
        return response(self.action)


@pytest.fixture
def setup(tmp_path):
    journal = PaperJournal(tmp_path / "paper.sqlite")
    broker, actor = Broker(), DecisionHook()
    broker.journal = journal
    adjudicator = JevDecisionEngine("fixture-key", transport=actor.transport)
    pilot = ActivePaperPilot(broker, adjudicator, journal, POLICY)
    yield pilot, broker, actor, journal
    journal.close()


def test_active_jev_veto_prevents_paper_order(setup):
    pilot, broker, _, journal = setup
    pilot.jev = JevDecisionEngine("fixture-key", transport=lambda *a, **k: response("NO_TRADE"))
    assert pilot.tick() == "JEV_VETO"
    assert not any(method == "POST" for method, _, _ in broker.calls)
    assert journal.last("adjudication")["receipt"]["accepted"] is False


@pytest.mark.parametrize("next_day", [False, True])
def test_cloud_session_stops_if_close_was_missed_or_day_changed(setup, next_day):
    pilot, broker, _, journal = setup
    pilot.session_date = date(2026, 9, 24)
    broker.state["clock"]["is_open"] = next_day
    broker.state["clock"]["timestamp"] = "2026-09-25T13:46:05Z" if next_day else "2026-09-24T20:01:00Z"
    assert pilot.tick() == "COMPLETE"
    assert journal.last("decision_started") is None
    assert not any(method == "POST" for method, _, _ in broker.calls)


def test_supported_jev_order_is_protected_reserved_and_not_a_fill(setup):
    pilot, broker, _, journal = setup
    assert pilot.tick() == "ENTRY_SUBMITTED"
    assert pilot.tick() == "ENTRY_WORKING"
    assert not broker.state["positions"]
    order = journal.last("entry_intent")["order"]
    assert order["qty"] == "1" and order["order_class"] == "bracket"
    assert order["type"] == "limit" and order["extended_hours"] is False
    assert order["stop_loss"]["stop_price"] == "99.00"
    assert sum(m == "POST" for m, _, _ in broker.calls) == 1


def test_lost_submit_response_recovers_without_resubmission(setup):
    pilot, broker, actor, journal = setup
    broker.lose_submit_response = True
    with pytest.raises(ModelError):
        pilot.tick()
    recovered = ActivePaperPilot(broker, pilot.jev, journal, POLICY)
    assert recovered.tick() == "ENTRY_WORKING"
    assert sum(m == "POST" for m, _, _ in broker.calls) == 1


def test_model_error_never_places_order_or_repeats_same_bar(setup):
    pilot, broker, _, journal = setup

    def unavailable(*args, **kwargs):
        raise ModelError("HTTP 429")

    pilot.jev = JevDecisionEngine("fixture-key", transport=unavailable)
    with pytest.raises(ModelError):
        pilot.tick()
    assert pilot.tick() == "ALREADY_REVIEWED"
    assert not journal.last("entry_intent")
    assert not any(m == "POST" for m, _, _ in broker.calls)


def test_jev_abstention_never_submits_an_order(setup):
    pilot, broker, actor, journal = setup
    actor.action = "NO_TRADE"
    assert pilot.tick() == "JEV_VETO"
    assert journal.last("adjudication")["receipt"]["accepted"] is False
    assert not any(m == "POST" for m, _, _ in broker.calls)


@pytest.mark.parametrize("change,expected", [
    (lambda s: s["clock"].update(timestamp=(NOW + timedelta(minutes=3)).isoformat()), "STALE_AFTER_ADJUDICATION"),
    (lambda s: s["clock"].update(is_open=False), "SESSION_BLOCKED"),
    (lambda s: s["account"].update(buying_power="1"), "RISK_CHANGED"),
    (lambda s: s["account"].update(trading_blocked=True), "ACCOUNT_BLOCKED"),
    (lambda s: s["account"].update(status="INACTIVE"), "ACCOUNT_BLOCKED"),
    (lambda s: s["account"].update(equity="99000"), "COMPLETE"),
])
def test_risk_rechecks_after_jev_acceptance(setup, change, expected):
    pilot, broker, actor, journal = setup
    actor.after = lambda: change(broker.state)
    assert pilot.tick() == expected
    assert not journal.last("entry_intent")
    assert not any(m == "POST" for m, _, _ in broker.calls)


def test_preclose_waits_for_cancel_then_fill_and_confirmed_flat(setup):
    pilot, broker, _, journal = setup
    pilot.tick()
    broker.parent.update(status="filled", filled_qty="1")
    broker.state["positions"] = [{"symbol": "AAPL", "qty": "1"}]
    broker.state["orders"] = [{"id": "stop", "symbol": "AAPL", "type": "stop", "side": "sell", "qty": "1"}]
    broker.state["clock"]["timestamp"] = NOW.replace(hour=19, minute=46).isoformat()
    assert pilot.tick() == "FLATTENING"
    assert not any(path.startswith("/v2/positions/") for _, path, _ in broker.calls)
    assert not journal.last("flat_confirmed")
    broker.state["orders"] = []  # broker, not DELETE acknowledgement, confirms cancellation
    assert pilot.tick() == "FLATTENING"
    assert broker.state["positions"]  # close acknowledgement is not a fill either
    assert pilot.tick() == "FLATTENING"
    assert sum(path.startswith("/v2/positions/") for _, path, _ in broker.calls) == 1
    broker.state.update(positions=[], orders=[])
    assert pilot.tick() == "FLATTENING"
    assert pilot.tick() == "COMPLETE"
    assert journal.last("flat_confirmed")


def test_partial_fill_halts_and_is_not_counted_as_full_position(setup):
    pilot, broker, _, journal = setup
    pilot.tick()
    broker.parent.update(status="partially_filled", filled_qty="0.5")
    broker.state["orders"] = [copy.deepcopy(broker.parent)]
    broker.state["positions"] = [{"symbol": "AAPL", "qty": "0.5"}]
    assert pilot.tick() == "FLATTENING"
    assert journal.last("halt")["reason"] == "PARTIAL_ENTRY"
    assert not journal.last("flat_confirmed")


def test_missing_protection_halts_independently_of_jev(setup):
    pilot, broker, _, journal = setup
    pilot.tick()
    broker.parent.update(status="filled", filled_qty="1")
    broker.state.update(orders=[], positions=[{"symbol": "AAPL", "qty": "1"}])
    assert pilot.tick() == "FLATTENING"
    assert journal.last("halt")["reason"] == "PROTECTION_MISSING"


def test_existing_unowned_exposure_is_not_touched(setup):
    pilot, broker, _, journal = setup
    broker.state["positions"] = [{"symbol": "MSFT", "qty": "1"}]
    with pytest.raises(ValueError, match="dedicated"):
        pilot.tick()
    assert not broker.calls
    assert not journal.last("binding")


def test_cleanup_refuses_different_account_even_with_matching_symbol(setup):
    pilot, broker, _, journal = setup
    pilot.tick()
    broker.state["account"]["id"] = "other-account"
    broker.state["positions"] = [{"symbol": "AAPL", "qty": "1"}]
    before = len(broker.calls)
    with pytest.raises(ValueError, match="different account"):
        pilot.flatten_step(broker.snapshot())
    assert len(broker.calls) == before


def test_partially_filled_stop_does_not_protect_its_original_quantity(setup):
    pilot, broker, _, journal = setup
    pilot.tick()
    broker.parent.update(status="filled", filled_qty="1")
    broker.state["positions"] = [{"symbol": "AAPL", "qty": "1"}]
    broker.state["orders"] = [{"id": "stop", "symbol": "AAPL", "type": "stop", "side": "sell",
                               "qty": "1", "filled_qty": "0.5"}]
    assert pilot.tick() == "FLATTENING"
    assert journal.last("halt")["reason"] == "PROTECTION_MISSING"


def test_halt_survives_cleanup_failure(setup):
    pilot, broker, _, journal = setup
    pilot.tick()
    pilot.halt("OPERATOR_STOP")
    original = broker.call

    def broken(method, path, payload=None):
        if method == "DELETE":
            raise ModelError("network down")
        return original(method, path, payload)

    broker.call = broken
    with pytest.raises(ModelError):
        pilot.tick()
    assert journal.last("halt")["reason"] == "OPERATOR_STOP"
    assert not journal.last("flat_confirmed")


def test_no_duplicate_on_next_tick_after_complete(setup):
    pilot, broker, _, journal = setup
    pilot.tick()
    broker.parent.update(status="canceled")
    broker.state["orders"] = []
    assert pilot.tick() == "FLATTENING"
    assert pilot.tick() == "COMPLETE"
    assert pilot.tick() == "COMPLETE"
    assert sum(m == "POST" for m, _, _ in broker.calls) == 1


@pytest.mark.parametrize("choice,confidence,accepted", [
    ("TAKE_TRADE", .9, True), ("TAKE_TRADE", .1, False),
    ("INSUFFICIENT_EVIDENCE", .9, False), ("NO_TRADE", .9, False),
])
def test_jev_gates_and_exact_http_contract(choice, confidence, accepted):
    calls = []

    def transport(method, endpoint, headers, payload, *, timeout):
        calls.append((method, endpoint, payload, timeout))
        return response(choice, confidence)

    judge = JevDecisionEngine("fixture-key", transport=transport)
    result = judge.decide({"candidate_direction": "LONG", "observations": {"breakout": "above"}})
    assert result.accepted is accepted
    assert result.receipt["execution_authorized"] is False
    assert calls[0][0:2] == ("POST", JEV_ENDPOINT)
    assert calls[0][2]["model"] == JEV_MODEL
    assert calls[0][3] == 4
    assert "fixture-key" not in result.receipt_json
    receipt = result.receipt
    receipt["accepted"] = not accepted
    assert result.receipt["accepted"] is accepted  # immutable detached receipt


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(model="jev-latest"),
    lambda r: r["answers"].pop("entry_decision"),
    lambda r: r["answers"]["entry_decision"].update(type="noul"),
    lambda r: r["answers"]["entry_decision"].update(choice="NO_TRADE"),
    lambda r: r["answers"]["entry_decision"].update(confidence=float("nan")),
    lambda r: r["answers"]["entry_decision"].update(confidence=True),
    lambda r: r["answers"]["entry_decision"]["probabilities"].update(TAKE_TRADE=1.5),
    lambda r: r["answers"]["entry_decision"]["probabilities"].update(TAKE_TRADE=.5),
    lambda r: r["answers"]["entry_decision"]["probabilities"].update(other=0),
    lambda r: r["usage"].update(input_tokens=-1),
])
def test_invalid_jev_response_is_never_approval(mutate):
    raw = response()
    mutate(raw)
    judge = JevDecisionEngine("fixture-key", transport=lambda *a, **k: raw)
    with pytest.raises(ModelError):
        judge.decide({"candidate_direction": "LONG", "observations": {}})


def test_current_data_ignores_incomplete_bar_and_excludes_today_daily():
    minute, daily = market_bars()
    expected = signal_packet(minute, daily, now=NOW, session_open=OPEN, policy=POLICY)
    minute.append({**minute[-1], "t": NOW.replace(second=0).isoformat(), "c": 999, "h": 999})
    daily.append({**daily[-1], "t": OPEN.isoformat(), "h": 999})
    actual = signal_packet(minute, daily, now=NOW, session_open=OPEN, policy=POLICY)
    assert actual == expected


@pytest.mark.parametrize("change", [
    lambda bars: bars.append(copy.deepcopy(bars[0])),
    lambda bars: bars[-1].update(c=float("nan")),
])
def test_invalid_bar_input_blocks_signal(change):
    minute, daily = market_bars()
    change(minute)
    with pytest.raises(ValueError):
        signal_packet(minute, daily, now=NOW, session_open=OPEN, policy=POLICY)


def test_stale_bar_is_not_replayed_as_current_signal():
    minute, daily = market_bars()
    with pytest.raises(DataDeferral, match="STALE_BAR"):
        signal_packet(minute, daily, now=NOW + timedelta(minutes=3), session_open=OPEN, policy=POLICY)


def test_stale_bar_waits_without_halt_or_order(setup):
    pilot, broker, _, journal = setup
    broker.state["clock"]["timestamp"] = (NOW + timedelta(minutes=3)).isoformat()
    assert pilot.tick() == "STALE_BAR"
    assert pilot.tick() == "STALE_BAR"
    assert not journal.last("halt")
    assert not journal.last("entry_intent")
    assert not any(method == "POST" for method, _, _ in broker.calls)


def test_missing_opening_minute_waits_without_halt_or_order(setup):
    pilot, broker, _, journal = setup
    minute, daily = market_bars()
    gapped = [bar for index, bar in enumerate(minute) if index != 4]

    def bars(symbol, start, end, timeframe, feed):
        return gapped if timeframe == "1Min" else daily

    broker.bars = bars
    assert pilot.tick() == "OPENING_RANGE_INCOMPLETE"
    assert pilot.tick() == "OPENING_RANGE_INCOMPLETE"
    assert not journal.last("halt")
    assert not journal.last("entry_intent")
    assert not any(method == "POST" for method, _, _ in broker.calls)


def test_daily_loss_still_halts_without_an_order(setup):
    pilot, broker, actor, journal = setup
    actor.after = lambda: broker.state["account"].update(equity="99000")
    assert pilot.tick() == "COMPLETE"
    assert journal.last("halt")["reason"] == "DAILY_LOSS_LIMIT"
    assert not journal.last("entry_intent")
    assert not any(method == "POST" for method, _, _ in broker.calls)


def _posts(broker):
    return sum(method == "POST" for method, _, _ in broker.calls)


def test_two_flat_404s_end_a_never_created_order_without_resubmit(setup):
    pilot, broker, _, journal = setup
    broker.reject_submit = True
    broker.hide_order = True
    with pytest.raises(ModelError, match="HTTP 422"):
        pilot.tick()
    assert journal.last("entry_intent")
    assert not journal.last("entry_ack")
    assert _posts(broker) == 1
    assert pilot.tick() == "ORDER_NOT_FOUND_RECHECK"
    assert journal.last("order_lookup_miss")["reason"] == "BROKER_ORDER_NOT_FOUND"
    assert not journal.last("entry_not_created")
    assert pilot.tick() == "ENTRY_NOT_CREATED"
    assert journal.last("entry_not_created")["reason"] == "BROKER_ORDER_NOT_FOUND"
    reserved = journal.last("entry_intent")["order"]["client_order_id"]
    assert journal.last("entry_not_created")["client_order_id"] == reserved
    again = ActivePaperPilot(broker, pilot.jev, journal, POLICY)
    assert again.tick() == "ENTRY_NOT_CREATED"
    assert _posts(broker) == 1
    assert not journal.last("flat_confirmed")


def test_404_with_a_position_stays_unresolved_and_does_not_resubmit(setup):
    pilot, broker, _, journal = setup
    broker.reject_submit = True
    broker.hide_order = True
    with pytest.raises(ModelError, match="HTTP 422"):
        pilot.tick()
    broker.state["positions"] = [{"symbol": "AAPL", "qty": "1"}]
    with pytest.raises(ValueError, match="exposure remains"):
        pilot.tick()
    assert not journal.last("entry_not_created")
    assert _posts(broker) == 1


def test_acknowledged_order_404_is_not_dropped_or_resent(setup):
    pilot, broker, _, journal = setup
    assert pilot.tick() == "ENTRY_SUBMITTED"
    broker.hide_order = True
    broker.state["orders"] = []
    with pytest.raises(ValueError, match="acknowledged entry disappeared"):
        pilot.tick()
    assert journal.last("entry_ack")
    assert not journal.last("entry_not_created")
    assert _posts(broker) == 1


def test_journal_detects_edits_and_enforces_single_writer(tmp_path):
    path = tmp_path / "paper.sqlite"
    journal = PaperJournal(path)
    journal.append("halt", reason="test")
    with pytest.raises(ValueError, match="another paper runner"):
        PaperJournal(path)
    journal.close()
    db = sqlite3.connect(path)
    db.execute("UPDATE events SET payload=? WHERE seq=1", ('{"kind":"approved"}',))
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="integrity"):
        PaperJournal(path)


def test_broker_requests_pinned_endpoints_and_paginates_data():
    calls = []

    def transport(method, url, headers, payload=None, **kwargs):
        calls.append(url)
        if "data.alpaca.markets" in url:
            return {"bars": {"AAPL": [{"t": len(calls)}]},
                    "next_page_token": "page2" if "page_token" not in url else None}
        return {}

    broker = LivePaperAPI("key", "secret", transport=transport)
    broker.call("POST", "/v2/orders", {"symbol": "AAPL"})
    assert len(broker.bars("AAPL", OPEN, NOW, "1Min", "iex")) == 2
    assert calls[0] == "https://paper-api.alpaca.markets/v2/orders"
    assert "feed=iex" in calls[1] and "page_token=page2" in calls[2]


def test_http_errors_never_echo_provider_body_or_secret(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise HTTPError(request.full_url, 401, "sensitive-key", {}, None)

    monkeypatch.setattr("athena_trading.athena_adapter.jev.build_opener", lambda *a: Opener())
    with pytest.raises(ModelError) as error:
        request_json("GET", JEV_ENDPOINT, {"Authorization": "Bearer sensitive-key"})
    assert str(error.value) == "HTTP 401"


def test_restart_can_exit_without_either_model(setup):
    pilot, broker, _, journal = setup
    pilot.tick()
    recovered = ActivePaperPilot(broker, None, journal, POLICY)
    broker.state["clock"]["timestamp"] = NOW.replace(hour=19, minute=46).isoformat()
    assert recovered.tick() == "FLATTENING"
    assert journal.last("halt")["reason"] == "SESSION_CLOSE"
    assert any(m == "DELETE" for m, _, _ in broker.calls)


def test_rejected_entry_never_becomes_position(setup):
    pilot, broker, _, journal = setup
    pilot.tick()
    broker.parent.update(status="rejected")
    broker.state["orders"] = []
    assert pilot.tick() == "FLATTENING"
    assert pilot.tick() == "COMPLETE"
    assert not broker.state["positions"]
    assert journal.last("halt")["reason"] == "ENTRY_TERMINATED"


def test_close_partial_fill_does_not_trigger_second_close(setup):
    pilot, broker, _, journal = setup
    pilot.tick()
    broker.parent.update(status="filled", filled_qty="1")
    broker.state.update(orders=[], positions=[{"symbol": "AAPL", "qty": "1"}])
    pilot.halt("OPERATOR_STOP")
    assert pilot.tick() == "FLATTENING"
    broker.state["orders"][0].update(status="partially_filled", filled_qty="0.5")
    broker.state["positions"][0]["qty"] = "0.5"
    assert pilot.tick() == "FLATTENING"
    assert sum(p.startswith("/v2/positions/") for _, p, _ in broker.calls) == 1
    assert not journal.last("flat_confirmed")


def test_corrupt_recovered_order_never_causes_resubmission(setup):
    pilot, broker, _, _ = setup
    pilot.tick()
    broker.parent["symbol"] = "MSFT"
    with pytest.raises(ValueError, match="reserved entry"):
        pilot.tick()
    assert sum(m == "POST" for m, _, _ in broker.calls) == 1


def test_prior_daily_data_must_include_previous_broker_session(setup):
    pilot, broker, _, journal = setup
    pilot.daily = market_bars()[1][:-1]
    with pytest.raises(ValueError, match="daily data is stale"):
        pilot.tick()
    assert not journal.last("decision_started")


def test_client_order_id_changes_with_session_date(tmp_path):
    ids = []
    for offset in (0, 1):
        journal = PaperJournal(tmp_path / f"day-{offset}.sqlite")
        broker = Broker()
        broker.journal = journal
        shifted_now = NOW + timedelta(days=offset)
        broker.state["clock"]["timestamp"] = shifted_now.isoformat()
        broker.state["clock"]["next_close"] = shifted_now.replace(hour=20).isoformat()
        original_call = broker.call

        def call(method, path, payload=None, *, day_offset=offset, original=original_call):
            if path.startswith("/v2/calendar"):
                return [{"date": (NOW + timedelta(days=day_offset - i)).date().isoformat(),
                         "open": "09:30", "close": "16:00"} for i in (1, 0)]
            return original(method, path, payload)

        def bars(symbol, start, end, timeframe, feed, *, day_offset=offset):
            values = market_bars()[0 if timeframe == "1Min" else 1]
            for item in values:
                item["t"] = (datetime.fromisoformat(item["t"]) + timedelta(days=day_offset)).isoformat()
            return values

        broker.call, broker.bars = call, bars
        jev = JevDecisionEngine("fixture", transport=lambda *a, **k: response())
        pilot = ActivePaperPilot(broker, jev, journal, POLICY)
        assert pilot.tick() == "ENTRY_SUBMITTED"
        ids.append(journal.last("entry_intent")["order"]["client_order_id"])
        journal.close()
    assert ids[0] != ids[1]


def test_short_candidate_has_correct_bracket_direction():
    minute, daily = market_bars()
    minute[-1].update(o=99.5, h=99.8, l=98.2, c=98.5)
    packet, order = signal_packet(minute, daily, now=NOW, session_open=OPEN, policy=POLICY)
    assert packet["candidate_direction"] == "SHORT"
    assert order["side"] == "sell"
    assert float(order["take_profit"]["limit_price"]) < float(order["limit_price"]) < float(order["stop_loss"]["stop_price"])


def _runner():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_athena_paper.py"
    spec = importlib.util.spec_from_file_location("run_athena_paper_resilience", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _flat_snapshot():
    return {"positions": [], "orders": [], "clock": {"is_open": True}, "account": {}}


def test_typesafe_startup_failure_keeps_a_flat_day_runnable(tmp_path, monkeypatch):
    script = _runner()
    day = date(2026, 9, 24)
    journal_path = tmp_path / f"{day.isoformat()}.sqlite"
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "paper-secret")
    monkeypatch.setenv("TYPESAFE_API_KEY", "model-key")

    class FlatBroker:
        def snapshot(self):
            return _flat_snapshot()

    def unavailable(*args, **kwargs):
        raise ModelError("HTTP 503", status=503)

    monkeypatch.setattr(script.LivePaperAPI, "from_env", lambda: FlatBroker())
    monkeypatch.setattr(script, "request_json", unavailable)
    monkeypatch.setattr(script.time, "sleep", lambda *_args: None)
    code = script.main([
        "--symbol", "AAPL", "--authority", "PAPER", "--feed", "iex",
        "--journal", str(journal_path), "--run",
    ])
    assert code == 2
    journal = PaperJournal(journal_path)
    try:
        assert journal.last("halt") is None
        assert journal.last("entry_intent") is None
    finally:
        journal.close()
    task = cloud.next_session(tmp_path, day)
    assert task == cloud.SessionTask(journal_path)


def test_startup_failure_with_visible_exposure_still_halts(tmp_path, monkeypatch):
    script = _runner()
    day = date(2026, 9, 24)
    journal_path = tmp_path / f"{day.isoformat()}.sqlite"
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "paper-secret")
    monkeypatch.setenv("TYPESAFE_API_KEY", "model-key")

    class ExposedBroker:
        def snapshot(self):
            return {"positions": [{"symbol": "AAPL", "qty": "1"}], "orders": [],
                    "clock": {"is_open": True}, "account": {}}

    def unavailable(*_args, **_kwargs):
        raise ModelError("HTTP 503", status=503)

    monkeypatch.setattr(script.LivePaperAPI, "from_env", lambda: ExposedBroker())
    monkeypatch.setattr(script, "request_json", unavailable)
    monkeypatch.setattr(script.time, "sleep", lambda *_args: None)
    assert script.main([
        "--symbol", "AAPL", "--authority", "PAPER", "--journal", str(journal_path), "--run",
    ]) == 2
    journal = PaperJournal(journal_path)
    try:
        assert journal.last("halt")["reason"] == "RUNNER_ERROR"
        assert journal.last("entry_intent") is None
    finally:
        journal.close()
    assert cloud.next_session(tmp_path, day) is None


def test_failure_after_reservation_halts_and_does_not_resubmit(tmp_path, monkeypatch):
    script = _runner()
    day = date(2026, 9, 24)
    journal_path = tmp_path / f"{day.isoformat()}.sqlite"
    journal = PaperJournal(journal_path)
    journal.append("binding", binding={
        "account_id": "paper-account", "policy": asdict(POLICY),
    })
    journal.append("entry_intent", session=day.isoformat(), order={
        "client_order_id": "athp-reserved", "symbol": "AAPL", "side": "buy", "qty": "1",
    })
    journal.close()
    calls = []

    class Broker:
        def snapshot(self):
            return {"positions": [], "orders": [], "account": {"id": "paper-account"},
                    "clock": {"timestamp": NOW.isoformat(), "is_open": True,
                              "next_close": NOW.replace(hour=20).isoformat()}}

        def call(self, method, path, payload=None):
            calls.append(method)
            raise ModelError("HTTP 404", status=404)

    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "paper-secret")
    monkeypatch.setenv("TYPESAFE_API_KEY", "model-key")
    monkeypatch.setattr(script.LivePaperAPI, "from_env", lambda: Broker())
    def fail_tick(self):
        raise RuntimeError("broker down during recovery")

    clock = {"now": 0}

    def monotonic():
        clock["now"] += 1_000
        return clock["now"]

    monkeypatch.setattr(script.ActivePaperPilot, "tick", fail_tick)
    monkeypatch.setattr(script.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(script.time, "monotonic", monotonic)
    assert script.main([
        "--symbol", "AAPL", "--authority", "PAPER", "--journal", str(journal_path), "--run",
    ]) == 2
    journal = PaperJournal(journal_path)
    try:
        assert journal.last("halt")["reason"] == "RUNNER_ERROR"
        assert not journal.last("entry_not_created")
    finally:
        journal.close()
    assert "POST" not in calls
    task = cloud.next_session(tmp_path, day)
    assert task.recovery and task.path == journal_path


def test_jev_thresholds_default_and_read_environment(monkeypatch, tmp_path):
    policy = PilotPolicy("SPY")
    assert (policy.support_min, policy.confidence_min, policy.fallback_after_open_minutes) == (0.6, 0.5, 120)
    packet = {"candidate_direction": "LONG", "observations": {}}

    def accepted(take, confidence, **overrides):
        engine = JevDecisionEngine(
            "fixture-key", transport=lambda *args, **kwargs: scored_response(take, confidence),
            **overrides,
        )
        return engine.decide(packet).accepted

    # Reported best on 2026-10-12. No bar or adjudication fixture for that session is in the repo.
    assert accepted(0.74, 0.61) is True
    assert accepted(0.74, 0.61, support_min=0.8, confidence_min=0.6) is False
    assert accepted(0.6, 0.5) is True
    assert accepted(0.59, 0.5) is False
    assert accepted(0.6, 0.49) is False

    monkeypatch.setenv("ATHENA_JEV_MIN_PTAKE", "0.74")
    monkeypatch.setenv("ATHENA_JEV_MIN_CONFIDENCE", "0.61")
    monkeypatch.setenv("ATHENA_FALLBACK_AFTER_OPEN_MINUTES", "90")
    loaded = policy_from_environment("SPY", "iex")
    assert (loaded.support_min, loaded.confidence_min, loaded.fallback_after_open_minutes) == (0.74, 0.61, 90)
    monkeypatch.setenv("ATHENA_JEV_MIN_PTAKE", "nope")
    with pytest.raises(ValueError, match="ATHENA_JEV_MIN_PTAKE"):
        policy_from_environment("SPY")
    monkeypatch.setenv("ATHENA_JEV_MIN_PTAKE", "0.6")
    monkeypatch.setenv("ATHENA_FALLBACK_AFTER_OPEN_MINUTES", "0")
    with pytest.raises(ValueError, match="fallback cutoff"):
        policy_from_environment("SPY")

    script = _runner()
    seen = {}
    monkeypatch.setenv("ALPACA_API_KEY", "paper-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "paper-secret")
    monkeypatch.setenv("TYPESAFE_API_KEY", "model-key")
    monkeypatch.setenv("ATHENA_JEV_MIN_PTAKE", "0.62")
    monkeypatch.setenv("ATHENA_JEV_MIN_CONFIDENCE", "0.51")
    monkeypatch.delenv("ATHENA_FALLBACK_AFTER_OPEN_MINUTES", raising=False)

    class FlatBroker:
        def snapshot(self):
            return _flat_snapshot()

    def capture(api_key, *, support_min, confidence_min):
        seen["support_min"] = support_min
        seen["confidence_min"] = confidence_min
        return object()

    monkeypatch.setattr(script.LivePaperAPI, "from_env", lambda: FlatBroker())
    monkeypatch.setattr(script, "JevDecisionEngine", capture)
    monkeypatch.setattr(script, "request_json", lambda *args, **kwargs: {"models": ["jev-1.13.0"]})
    assert script.main([
        "--symbol", "SPY", "--authority", "PAPER", "--feed", "iex",
        "--journal", str(tmp_path / "preflight.sqlite"), "--preflight",
    ]) == 0
    assert seen == {"support_min": 0.62, "confidence_min": 0.51}


def _fallback_pilot(tmp_path, transport, policy, minute=None):
    journal = PaperJournal(tmp_path / "paper.sqlite")
    broker = Broker()
    broker.journal = journal
    if minute is not None:
        daily = market_bars()[1]

        def bars(symbol, start, end, timeframe, feed):
            return minute if timeframe == "1Min" else daily

        broker.bars = bars
    pilot = ActivePaperPilot(
        broker, JevDecisionEngine("fixture-key", transport=transport), journal, policy,
    )
    return pilot, broker, journal


def test_fallback_submits_the_highest_take_once(tmp_path):
    minute, _daily = market_bars()
    planned = [(0.55, 0.90), (0.59, 0.85)]
    calls = {"n": 0}

    def transport(*args, **kwargs):
        take, confidence = planned[calls["n"]]
        calls["n"] += 1
        return scored_response(take, confidence)

    policy = PilotPolicy("AAPL", fallback_after_open_minutes=30)
    pilot, broker, journal = _fallback_pilot(tmp_path, transport, policy, minute)
    assert pilot.tick() == "JEV_VETO"
    minute.append({
        "t": (OPEN + timedelta(minutes=16)).isoformat(),
        "o": 101.6, "h": 102.4, "l": 101.4, "c": 102.2, "v": 1000,
    })
    broker.state["clock"]["timestamp"] = (OPEN + timedelta(minutes=17, seconds=5)).isoformat()
    assert pilot.tick() == "JEV_VETO"
    broker.state["clock"]["timestamp"] = (OPEN + timedelta(minutes=31)).isoformat()
    assert pilot.tick() == "ENTRY_SUBMITTED"
    assert pilot.tick() == "ENTRY_WORKING"
    assert _posts(broker) == 1
    assert calls["n"] == 2
    fallbacks = [event for event in journal.events if event["kind"] == "fallback_entry"]
    assert len(fallbacks) == 1
    assert fallbacks[0]["reason"] == "NO_ORDER_BY_CUTOFF"
    assert fallbacks[0]["signal_at"] == (OPEN + timedelta(minutes=16)).isoformat()
    order = journal.last("entry_intent")["order"]
    assert order["limit_price"] == "102.20"
    assert order["qty"] == "1" and order["order_class"] == "bracket"
    assert order["extended_hours"] is False and order["time_in_force"] == "day"
    assert journal.last("entry_intent")["fallback"] is True
    journal.close()


def test_fallback_is_skipped_after_a_trade(tmp_path):
    minute, _daily = market_bars()
    minute.append({
        "t": (OPEN + timedelta(minutes=18)).isoformat(),
        "o": 101.6, "h": 102.4, "l": 101.4, "c": 102.2, "v": 1000,
    })
    policy = PilotPolicy("AAPL", fallback_after_open_minutes=20)
    pilot, broker, journal = _fallback_pilot(
        tmp_path, lambda *args, **kwargs: response(), policy, minute,
    )
    broker.state["clock"]["timestamp"] = (OPEN + timedelta(minutes=19, seconds=20)).isoformat()
    assert pilot.tick() == "ENTRY_SUBMITTED"
    broker.state["clock"]["timestamp"] = (OPEN + timedelta(minutes=20, seconds=10)).isoformat()
    assert pilot.tick() == "ENTRY_WORKING"
    assert journal.last("fallback_entry") is None
    assert journal.last("fallback_candidate") is None
    assert _posts(broker) == 1
    journal.close()


def test_fallback_does_not_promote_no_trade(tmp_path):
    policy = PilotPolicy("AAPL", fallback_after_open_minutes=30)
    pilot, broker, journal = _fallback_pilot(
        tmp_path, lambda *args, **kwargs: response("NO_TRADE"), policy,
    )
    assert pilot.tick() == "JEV_VETO"
    broker.state["clock"]["timestamp"] = (OPEN + timedelta(minutes=31)).isoformat()
    assert pilot.tick() == "STALE_BAR"
    assert journal.last("fallback_candidate") is None
    assert journal.last("fallback_entry") is None
    assert _posts(broker) == 0
    journal.close()


def test_adjudication_budget_waits_for_the_daily_fallback(tmp_path):
    minute, _daily = market_bars()
    calls = {"n": 0}

    def transport(*args, **kwargs):
        calls["n"] += 1
        return scored_response(0.55, 0.9)

    policy = PilotPolicy("AAPL", fallback_after_open_minutes=30, max_adjudications=1)
    pilot, broker, journal = _fallback_pilot(tmp_path, transport, policy, minute)
    assert pilot.tick() == "JEV_VETO"
    minute.append({
        "t": (OPEN + timedelta(minutes=16)).isoformat(),
        "o": 101.6, "h": 102.4, "l": 101.4, "c": 102.2, "v": 1000,
    })
    broker.state["clock"]["timestamp"] = (OPEN + timedelta(minutes=17, seconds=5)).isoformat()
    assert pilot.tick() == "ADJUDICATION_BUDGET"
    assert calls["n"] == 1
    broker.state["clock"]["timestamp"] = (OPEN + timedelta(minutes=31)).isoformat()
    assert pilot.tick() == "ENTRY_SUBMITTED"
    assert calls["n"] == 1
    assert _posts(broker) == 1
    assert journal.last("fallback_entry")["reason"] == "NO_ORDER_BY_CUTOFF"
    assert journal.last("entry_intent")["order"]["limit_price"] == "101.50"
    journal.close()


def test_journaled_take_before_this_field_still_falls_back_once(tmp_path):
    minute, daily = market_bars()
    packet, _order = signal_packet(minute, daily, now=NOW, session_open=OPEN, policy=POLICY)
    engine = JevDecisionEngine(
        "fixture-key", transport=lambda *args, **kwargs: scored_response(0.55, 0.9),
    )
    verdict = engine.decide(packet)
    assert verdict.accepted is False
    journal = PaperJournal(tmp_path / "paper.sqlite")
    journal.append(
        "decision_started", session="2026-09-24", signal_at=packet["signal_at"],
        packet=packet, market_inputs={"bars": minute, "daily": daily},
    )
    journal.append("adjudication", receipt=verdict.receipt)
    broker = Broker()
    broker.journal = journal
    broker.state["clock"]["timestamp"] = (OPEN + timedelta(minutes=31)).isoformat()

    def transport(*args, **kwargs):
        raise AssertionError("fallback reuses the journaled adjudication")

    policy = PilotPolicy("AAPL", fallback_after_open_minutes=30)
    pilot = ActivePaperPilot(
        broker, JevDecisionEngine("fixture-key", transport=transport), journal, policy,
    )
    assert pilot.tick() == "ENTRY_SUBMITTED"
    assert pilot.tick() == "ENTRY_WORKING"
    assert _posts(broker) == 1
    assert journal.last("fallback_entry")["reason"] == "NO_ORDER_BY_CUTOFF"
    assert journal.last("entry_intent")["order"]["qty"] == "1"
    assert journal.last("entry_intent")["fallback"] is True
    journal.close()


def test_saved_policy_without_fallback_field_still_resumes(tmp_path):
    saved = asdict(PilotPolicy("AAPL"))
    saved.update(support_min=0.8, confidence_min=0.6, version="athena-jev-paper-pilot-v2")
    del saved["fallback_after_open_minutes"]
    restored = PilotPolicy(**saved)
    assert (restored.support_min, restored.confidence_min) == (0.8, 0.6)
    assert restored.fallback_after_open_minutes == 120
    journal = PaperJournal(tmp_path / "paper.sqlite")
    journal.append("binding", binding={"account_id": "paper-account", "policy": saved})
    broker = Broker()
    broker.journal = journal
    pilot = ActivePaperPilot(
        broker, JevDecisionEngine("fixture-key", transport=lambda *args, **kwargs: response("NO_TRADE")),
        journal, restored,
    )
    assert pilot.tick() == "JEV_VETO"
    journal.close()


def test_fallback_and_runner_stay_paper_only(tmp_path):
    with pytest.raises(AuthorityError, match="LIVE"):
        parse_authority("LIVE")
    assert "LIVE" not in {item.value for item in ExecutionAuthority}
    policy = PilotPolicy("AAPL", fallback_after_open_minutes=30)
    pilot, broker, journal = _fallback_pilot(
        tmp_path, lambda *args, **kwargs: scored_response(0.55, 0.9), policy,
    )
    assert pilot.tick() == "JEV_VETO"
    pilot.authority = ExecutionAuthority.ABSENT
    broker.state["clock"]["timestamp"] = (OPEN + timedelta(minutes=31)).isoformat()
    with pytest.raises(AuthorityError, match="ABSENT"):
        pilot.tick()
    assert _posts(broker) == 0
    assert journal.last("entry_intent") is None
    assert journal.last("fallback_entry") is None
    journal.close()
    script = _runner()
    with pytest.raises(SystemExit) as raised:
        script.main([
            "--symbol", "AAPL", "--authority", "LIVE",
            "--journal", str(tmp_path / "live.sqlite"), "--run",
        ])
    assert raised.value.code == 2
