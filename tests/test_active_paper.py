from __future__ import annotations

import copy
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from urllib.error import HTTPError

import pytest

from athena_trading.athena_adapter.jev import (
    JEV_ENDPOINT, JEV_MODEL, JevAdjudicator, ModelError, questions, request_json,
)
from athena_trading.paper.journal import PaperJournal
from athena_trading.paper.live import (
    ActivePaperPilot, LivePaperAPI, OllamaActor, PilotPolicy, signal_packet,
)

NOW = datetime(2026, 9, 24, 13, 46, 5, tzinfo=UTC)
OPEN = NOW.replace(minute=30, second=0)
POLICY = PilotPolicy("AAPL")


def response(choice="supported", confidence=0.9):
    return {"model": JEV_MODEL, "usage": {"input_tokens": 300, "output_tokens": 50},
            "answers": {key: {"type": "choice", "choice": choice,
                              "confidence": confidence,
                              "probabilities": {opt: 0.9 if opt == choice else 0.05
                                                for opt in ("supported", "contradicted", "insufficient")}}
                        for key in questions()}}


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
            assert self.journal.last("entry_intent"), "reservation must be durable before send"
            self.parent = {**payload, "id": "entry-1", "status": "new", "filled_qty": "0"}
            self.state["orders"] = [copy.deepcopy(self.parent)]
            if self.lose_submit_response:
                raise ModelError("timeout")
            return copy.deepcopy(self.parent)
        if path.startswith("/v2/orders:by_client_order_id"):
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


class Actor:
    def __init__(self):
        self.action = "LONG"
        self.after = lambda: None

    def propose(self, packet):
        self.after()
        return {"action": self.action, "rationale": "The close broke above the opening range.",
                "invalidation": "The breakout fails.", "actor_model": "fixture", "actor_digest": "0" * 64}


@pytest.fixture
def setup(tmp_path):
    journal = PaperJournal(tmp_path / "paper.sqlite")
    broker, actor = Broker(), Actor()
    broker.journal = journal
    adjudicator = JevAdjudicator("fixture-key", transport=lambda *a, **k: response())
    pilot = ActivePaperPilot(broker, actor, adjudicator, journal, POLICY)
    yield pilot, broker, actor, journal
    journal.close()


def test_active_jev_veto_prevents_paper_order(setup):
    pilot, broker, _, journal = setup
    pilot.jev = JevAdjudicator("fixture-key", transport=lambda *a, **k: response("contradicted"))
    assert pilot.tick() == "JEV_VETO"
    assert not any(method == "POST" for method, _, _ in broker.calls)
    assert journal.last("adjudication")["receipt"]["accepted"] is False


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
    recovered = ActivePaperPilot(broker, actor, pilot.jev, journal, POLICY)
    assert recovered.tick() == "ENTRY_WORKING"
    assert sum(m == "POST" for m, _, _ in broker.calls) == 1


def test_model_error_never_places_order_or_repeats_same_bar(setup):
    pilot, broker, _, journal = setup

    def unavailable(*args, **kwargs):
        raise ModelError("HTTP 429")

    pilot.jev = JevAdjudicator("fixture-key", transport=unavailable)
    with pytest.raises(ModelError):
        pilot.tick()
    assert pilot.tick() == "ALREADY_REVIEWED"
    assert not journal.last("entry_intent")
    assert not any(m == "POST" for m, _, _ in broker.calls)


def test_actor_abstention_never_calls_jev(setup):
    pilot, broker, actor, journal = setup
    actor.action = "NO_TRADE"
    assert pilot.tick() == "ACTOR_NO_TRADE"
    assert not journal.last("adjudication")
    assert not any(m == "POST" for m, _, _ in broker.calls)


@pytest.mark.parametrize("change,expected", [
    (lambda s: s["clock"].update(timestamp=(NOW + timedelta(minutes=3)).isoformat()), "STALE_AFTER_ADJUDICATION"),
    (lambda s: s["clock"].update(is_open=False), "SESSION_BLOCKED"),
    (lambda s: s["account"].update(buying_power="1"), "RISK_CHANGED"),
    (lambda s: s["account"].update(trading_blocked=True), "ACCOUNT_BLOCKED"),
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
    ("supported", .9, True), ("supported", .1, False),
    ("insufficient", .9, False), ("contradicted", .9, False),
])
def test_jev_gates_and_exact_http_contract(choice, confidence, accepted):
    calls = []

    def transport(method, endpoint, headers, payload, *, timeout):
        calls.append((method, endpoint, payload, timeout))
        return response(choice, confidence)

    judge = JevAdjudicator("fixture-key", transport=transport)
    result = judge.adjudicate({"observations": {"breakout": "above"}}, {"action": "LONG"})
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
    lambda r: r["answers"].pop("rationale_support"),
    lambda r: r["answers"]["rationale_support"].update(type="noul"),
    lambda r: r["answers"]["rationale_support"].update(choice="contradicted"),
    lambda r: r["answers"]["rationale_support"].update(confidence=float("nan")),
    lambda r: r["answers"]["rationale_support"].update(confidence=True),
    lambda r: r["answers"]["rationale_support"]["probabilities"].update(supported=1.5),
    lambda r: r["answers"]["rationale_support"]["probabilities"].update(supported=.5),
    lambda r: r["answers"]["rationale_support"]["probabilities"].update(other=0),
    lambda r: r["usage"].update(input_tokens=-1),
])
def test_invalid_jev_response_is_never_approval(mutate):
    raw = response()
    mutate(raw)
    judge = JevAdjudicator("fixture-key", transport=lambda *a, **k: raw)
    with pytest.raises(ModelError):
        judge.adjudicate({"observations": {}}, {"action": "LONG"})


def test_actor_requires_local_model_digest_and_bounded_action():
    calls = []

    def transport(method, url, headers, payload=None, **kwargs):
        calls.append(url)
        if url.endswith("/api/tags"):
            return {"models": [{"name": "qwen2.5:7b", "digest": "a" * 64}]}
        return {"model": "qwen2.5:7b", "done": True, "message": {"content": json.dumps({
            "action": "SHORT", "rationale": "text", "invalidation": "text"})}}

    actor = OllamaActor(transport=transport)
    with pytest.raises(ModelError, match="direction"):
        actor.propose({"candidate_direction": "LONG"})
    assert len(calls) == 3


def test_current_data_ignores_incomplete_bar_and_excludes_today_daily():
    minute, daily = market_bars()
    expected = signal_packet(minute, daily, now=NOW, session_open=OPEN, policy=POLICY)
    minute.append({**minute[-1], "t": NOW.replace(second=0).isoformat(), "c": 999, "h": 999})
    daily.append({**daily[-1], "t": OPEN.isoformat(), "h": 999})
    actual = signal_packet(minute, daily, now=NOW, session_open=OPEN, policy=POLICY)
    assert actual == expected


@pytest.mark.parametrize("change", [
    lambda bars: bars.pop(3),
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
    with pytest.raises(ValueError, match="stale"):
        signal_packet(minute, daily, now=NOW + timedelta(minutes=3), session_open=OPEN, policy=POLICY)


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
    recovered = ActivePaperPilot(broker, None, None, journal, POLICY)
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
        broker, actor = Broker(), Actor()
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
        jev = JevAdjudicator("fixture", transport=lambda *a, **k: response())
        pilot = ActivePaperPilot(broker, actor, jev, journal, POLICY)
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
