"""Capped marketable limits for intraday paper entries. Fixtures only."""
from __future__ import annotations

from datetime import UTC, timedelta
from decimal import Decimal

import pytest

from athena_trading.athena_adapter.contracts import canonical_hash
from athena_trading.athena_adapter.jev import JevDecisionEngine, ModelError
from athena_trading.execution.authority import AuthorityError, parse_authority
from athena_trading.paper.journal import PaperJournal
from athena_trading.paper.live import (
    ENTRY_LIMIT_OFFSET,
    ENTRY_LIMIT_OFFSET_MAX,
    MAX_ENTRY_SLIPPAGE,
    MAX_LIMIT_CONCESSION,
    QUOTE_MAX_AGE_SECONDS,
    ActivePaperPilot,
    EntryNotPriced,
    LivePaperAPI,
    PilotPolicy,
    assess_quote,
    price_marketable_entry,
    signal_packet,
)
from test_active_paper import NOW, OPEN, POLICY, Broker, market_bars, response, scored_response

OCT15_CLOSE = Decimal("765.21")
OCT15_BID = Decimal("765.15")
OCT15_ASK = Decimal("765.18")


def test_pricing_constants_and_authority_are_unchanged():
    assert ENTRY_LIMIT_OFFSET == Decimal("0.01")
    assert ENTRY_LIMIT_OFFSET_MAX == Decimal("0.02")
    assert MAX_ENTRY_SLIPPAGE == Decimal("0.05")
    assert MAX_LIMIT_CONCESSION == MAX_ENTRY_SLIPPAGE + ENTRY_LIMIT_OFFSET_MAX
    assert QUOTE_MAX_AGE_SECONDS == 15
    policy = PilotPolicy("SPY")
    assert policy.version == "athena-jev-paper-pilot-v3"
    assert policy.max_notional == 1000
    assert policy.max_stop_risk == 10
    with pytest.raises(AuthorityError):
        parse_authority("LIVE")


def test_oct_15_sell_formula_is_bid_minus_one_cent():
    limit, slippage = price_marketable_entry(
        side="sell", signal_close=OCT15_CLOSE, bid=OCT15_BID, ask=OCT15_ASK,
        stop=Decimal("767.42"), target=Decimal("760.79"),
    )
    assert limit == Decimal("765.14")
    assert slippage == Decimal("0.07")
    assert abs(limit - Decimal("767.42")) == Decimal("2.28")
    assert Decimal("760.79") < limit < Decimal("767.42")


def test_two_cent_offset_is_allowed_and_three_cents_is_not():
    limit, slippage = price_marketable_entry(
        side="sell", signal_close=Decimal("100.00"), bid=Decimal("100.00"),
        ask=Decimal("100.01"), stop=Decimal("101.00"), target=Decimal("98.00"),
        offset=Decimal("0.02"),
    )
    assert limit == Decimal("99.98")
    assert slippage == Decimal("0.02")
    with pytest.raises(EntryNotPriced) as raised:
        price_marketable_entry(
            side="buy", signal_close=Decimal("100"), bid=Decimal("100"),
            ask=Decimal("100"), stop=Decimal("99"), target=Decimal("102"),
            offset=Decimal("0.03"),
        )
    assert raised.value.reason == "ENTRY_OFFSET_INVALID"


def test_slippage_cap_skips_when_the_midpoint_runs_more_than_five_cents():
    with pytest.raises(EntryNotPriced) as raised:
        price_marketable_entry(
            side="sell", signal_close=Decimal("100.00"), bid=Decimal("99.94"),
            ask=Decimal("99.94"), stop=Decimal("101.00"), target=Decimal("98.00"),
        )
    assert raised.value.reason == "ENTRY_SLIPPAGE_CAP"
    # Exactly five cents of midpoint move is still inside the cap.
    limit, _slippage = price_marketable_entry(
        side="sell", signal_close=Decimal("100.00"), bid=Decimal("99.95"),
        ask=Decimal("99.95"), stop=Decimal("101.00"), target=Decimal("98.00"),
    )
    assert limit == Decimal("99.94")


def test_limit_concession_above_seven_cents_is_skipped():
    """A wide spread can keep the midpoint inside five cents and still chase too far."""
    with pytest.raises(EntryNotPriced) as raised:
        price_marketable_entry(
            side="sell", signal_close=Decimal("100.00"), bid=Decimal("99.92"),
            ask=Decimal("99.98"), stop=Decimal("101.00"), target=Decimal("98.00"),
        )
    assert raised.value.reason == "ENTRY_SLIPPAGE_CAP"
    assert raised.value.details["limit_price"] == "99.91"
    assert raised.value.details["slippage_vs_close"] == "0.09"


def test_quote_missing_stale_crossed_and_nanosecond_time():
    with pytest.raises(EntryNotPriced) as missing:
        assess_quote(None, now=NOW)
    assert missing.value.reason == "QUOTE_MISSING"
    with pytest.raises(EntryNotPriced) as empty:
        assess_quote({}, now=NOW)
    assert empty.value.reason == "QUOTE_MISSING"
    with pytest.raises(EntryNotPriced) as invalid:
        assess_quote({"bp": "0", "ap": "1", "t": NOW.isoformat()}, now=NOW)
    assert invalid.value.reason == "QUOTE_INVALID"
    with pytest.raises(EntryNotPriced) as crossed:
        assess_quote({"bp": "101.20", "ap": "101.10", "t": NOW.isoformat()}, now=NOW)
    assert crossed.value.reason == "QUOTE_CROSSED"
    stale_at = (NOW - timedelta(seconds=QUOTE_MAX_AGE_SECONDS + 1)).isoformat()
    with pytest.raises(EntryNotPriced) as stale:
        assess_quote({"bp": "101.00", "ap": "101.01", "t": stale_at}, now=NOW)
    assert stale.value.reason == "QUOTE_STALE"
    edge = (NOW - timedelta(seconds=QUOTE_MAX_AGE_SECONDS)).isoformat()
    bid, ask, _when = assess_quote({"bp": "101.00", "ap": "101.01", "t": edge}, now=NOW)
    assert bid == Decimal("101.00") and ask == Decimal("101.01")
    nanos = NOW.strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
    _bid, _ask, parsed = assess_quote({"bid": "10.00", "ask": "10.01", "t": nanos}, now=NOW)
    assert parsed.microsecond == 123456


def test_latest_quote_uses_the_configured_feed():
    seen = []

    def transport(method, url, headers, payload=None, **kwargs):
        seen.append((method, url))
        return {"symbol": "SPY", "quote": {"bp": 765.15, "ap": 765.18, "t": NOW.isoformat()}}

    api = LivePaperAPI("key", "secret", transport=transport)
    quote = api.latest_quote("SPY", "iex")
    assert quote["bp"] == 765.15 and quote["ap"] == 765.18
    assert seen == [(
        "GET",
        "https://data.alpaca.markets/v2/stocks/SPY/quotes/latest?feed=iex",
    )]
    with pytest.raises(ValueError, match="unusable"):
        LivePaperAPI("key", "secret", transport=lambda *args, **kwargs: {"quote": None}).latest_quote(
            "SPY", "sip",
        )


def _spy_short_bars():
    minute = [{
        "t": (OPEN + timedelta(minutes=i)).isoformat(),
        "o": 767.00, "h": 767.42, "l": 766.10, "c": 766.80, "v": 1000,
    } for i in range(15)]
    minute.append({
        "t": (OPEN + timedelta(minutes=15)).isoformat(),
        "o": 766.00, "h": 766.20, "l": 765.10, "c": 765.21, "v": 1000,
    })
    daily = [{
        "t": (OPEN - timedelta(days=i)).isoformat(),
        "o": 760, "h": 763, "l": 760, "c": 761, "v": 2_000_000,
    } for i in range(20, 0, -1)]
    return minute, daily


def _pilot(tmp_path, minute, daily, quote, *, symbol="AAPL", buying_power="100000", equity="100000"):
    journal = PaperJournal(tmp_path / "paper.sqlite")
    broker = Broker()
    broker.journal = journal
    broker.state["account"]["equity"] = equity
    broker.state["account"]["last_equity"] = equity
    broker.state["account"]["buying_power"] = buying_power

    def bars(symbol_name, start, end, timeframe, feed):
        return minute if timeframe == "1Min" else daily

    broker.bars = bars
    broker.latest_quote = lambda symbol_name, feed: quote
    pilot = ActivePaperPilot(
        broker, JevDecisionEngine("fixture-key", transport=lambda *args, **kwargs: response()),
        journal, PilotPolicy(symbol),
    )
    return pilot, broker, journal


def _posts(broker):
    return [payload for method, path, payload in broker.calls if method == "POST" and path == "/v2/orders"]


def test_oct_15_short_submits_a_marketable_limit_and_keeps_the_bracket(tmp_path):
    """Replay of 15 October 2026: sell limit 765.21 never reached a 765.15 bid."""
    minute, daily = _spy_short_bars()
    provisional = signal_packet(minute, daily, now=NOW, session_open=OPEN, policy=PilotPolicy("SPY"))
    assert provisional is not None
    _packet, reference = provisional
    assert reference["limit_price"] == "765.21"
    assert reference["stop_loss"]["stop_price"] == "767.42"
    assert reference["take_profit"]["limit_price"] == "760.79"
    quote = {
        "t": NOW.isoformat(), "bp": "765.15", "ap": "765.18",
    }
    pilot, broker, journal = _pilot(tmp_path, minute, daily, quote, symbol="SPY")
    assert pilot.tick() == "ENTRY_SUBMITTED"
    intent = journal.last("entry_intent")
    order = intent["order"]
    assert order["type"] == "limit"
    assert order["side"] == "sell"
    assert order["limit_price"] == "765.14"
    assert order["stop_loss"] == reference["stop_loss"]
    assert order["take_profit"] == reference["take_profit"]
    assert order["time_in_force"] == "day" and order["extended_hours"] is False
    stop_risk = abs(Decimal(order["limit_price"]) - Decimal(order["stop_loss"]["stop_price"]))
    assert stop_risk == Decimal("2.28")
    assert Decimal(order["take_profit"]["limit_price"]) < Decimal(order["limit_price"])
    assert Decimal(order["limit_price"]) < Decimal(order["stop_loss"]["stop_price"])
    assert intent["quote"] == {
        "bid": "765.15", "ask": "765.18", "at": NOW.isoformat(),
    }
    assert intent["signal_close"] == "765.21"
    assert intent["slippage_vs_close"] == "0.07"
    assert intent["midpoint_vs_close"] == "0.0450"
    assert journal.last("entry_skipped") is None
    assert journal.last("halt") is None
    posted = _posts(broker)
    assert len(posted) == 1
    assert posted[0]["type"] == "limit" and posted[0]["limit_price"] == "765.14"
    assert "market" not in {posted[0]["type"]}
    day = NOW.astimezone(UTC).date().isoformat()
    assert order["client_order_id"] == "athp-" + canonical_hash({
        "account": "paper-account", "session": day,
    })[:40]
    assert pilot.tick() == "ENTRY_WORKING"
    assert len(_posts(broker)) == 1
    path = tmp_path / "paper.sqlite"
    journal.close()
    reopened = PaperJournal(path)
    try:
        restored = reopened.last("entry_intent")["order"]
        assert restored["limit_price"] == "765.14"
        assert restored["client_order_id"] == order["client_order_id"]
    finally:
        reopened.close()


def test_oct_14_buy_is_priced_through_the_ask(tmp_path):
    """14 October filled a passive buy only after price dipped back to the close.

    The investigation did not publish that session's prints. This fixture is the
    same rule on a long breakout whose ask is four cents above the signal close.
    """
    minute, daily = market_bars()
    reference = signal_packet(minute, daily, now=NOW, session_open=OPEN, policy=POLICY)[1]
    assert reference["side"] == "buy"
    assert reference["limit_price"] == "101.50"
    assert reference["stop_loss"]["stop_price"] == "99.00"
    quote = {"t": NOW.isoformat(), "bp": "101.52", "ap": "101.54"}
    pilot, broker, journal = _pilot(tmp_path, minute, daily, quote)
    assert pilot.tick() == "ENTRY_SUBMITTED"
    order = journal.last("entry_intent")["order"]
    assert order["type"] == "limit" and order["side"] == "buy"
    assert order["limit_price"] == "101.55"
    assert order["stop_loss"] == reference["stop_loss"]
    assert order["take_profit"] == reference["take_profit"]
    assert Decimal(order["stop_loss"]["stop_price"]) < Decimal(order["limit_price"])
    assert Decimal(order["limit_price"]) < Decimal(order["take_profit"]["limit_price"])
    intent = journal.last("entry_intent")
    assert intent["quote"]["bid"] == "101.52" and intent["quote"]["ask"] == "101.54"
    assert intent["signal_close"] == "101.50"
    assert intent["slippage_vs_close"] == "0.05"
    assert _posts(broker)[0]["limit_price"] == "101.55"
    assert _posts(broker)[0]["type"] == "limit"
    journal.close()


def test_favorable_buy_still_submits_inside_the_original_bracket(tmp_path):
    minute, daily = market_bars()
    quote = {"t": NOW.isoformat(), "bp": "101.38", "ap": "101.40"}
    pilot, broker, journal = _pilot(tmp_path, minute, daily, quote)
    assert pilot.tick() == "ENTRY_SUBMITTED"
    order = journal.last("entry_intent")["order"]
    assert order["limit_price"] == "101.41"
    assert journal.last("entry_intent")["slippage_vs_close"] == "-0.09"
    assert order["stop_loss"]["stop_price"] == "99.00"
    assert float(order["stop_loss"]["stop_price"]) < float(order["limit_price"])
    journal.close()


@pytest.mark.parametrize("quote,reason", [
    (None, "QUOTE_MISSING"),
    ({}, "QUOTE_MISSING"),
    ({"bp": "101.60", "ap": "101.40", "t": NOW.isoformat()}, "QUOTE_CROSSED"),
    ({"bp": "101.49", "t": NOW.isoformat()}, "QUOTE_INVALID"),
    ({
        "bp": "101.49", "ap": "101.50",
        "t": (NOW - timedelta(seconds=30)).isoformat(),
    }, "QUOTE_STALE"),
])
def test_bad_quotes_skip_without_an_order_or_a_halt(tmp_path, quote, reason):
    minute, daily = market_bars()
    pilot, broker, journal = _pilot(tmp_path, minute, daily, quote)
    assert pilot.tick() == "ENTRY_SKIPPED"
    assert journal.last("halt") is None
    assert journal.last("entry_intent") is None
    assert _posts(broker) == []
    skipped = journal.last("entry_skipped")
    assert skipped["reason"] == reason
    assert journal.last("opportunity")["stage"] == "rejected"
    assert journal.last("opportunity")["reason"] == reason
    assert pilot.tick() == "ALREADY_REVIEWED"
    assert len([event for event in journal.events if event["kind"] == "entry_skipped"]) == 1
    journal.close()


def test_quote_transport_failure_skips_instead_of_halting(tmp_path):
    minute, daily = market_bars()
    pilot, broker, journal = _pilot(tmp_path, minute, daily, {})

    def latest_quote(symbol, feed):
        raise ModelError("HTTP 503", status=503)

    broker.latest_quote = latest_quote
    assert pilot.tick() == "ENTRY_SKIPPED"
    assert journal.last("entry_skipped")["reason"] == "QUOTE_MISSING"
    assert journal.last("halt") is None
    assert _posts(broker) == []
    journal.close()


def test_slippage_cap_skip_journals_the_refused_limit(tmp_path):
    minute, daily = market_bars()
    quote = {"t": NOW.isoformat(), "bp": "101.70", "ap": "101.72"}
    pilot, broker, journal = _pilot(tmp_path, minute, daily, quote)
    assert pilot.tick() == "ENTRY_SKIPPED"
    skipped = journal.last("entry_skipped")
    assert skipped["reason"] == "ENTRY_SLIPPAGE_CAP"
    assert skipped["signal_close"] == "101.50"
    assert skipped["quote"]["bid"] == "101.70"
    assert skipped["quote"]["ask"] == "101.72"
    assert skipped["limit_price"] == "101.73"
    assert Decimal(skipped["slippage_vs_close"]) > MAX_ENTRY_SLIPPAGE
    assert journal.last("entry_intent") is None
    assert journal.last("halt") is None
    assert _posts(broker) == []
    journal.close()


def test_notional_and_stop_risk_bind_on_the_new_limit(tmp_path):
    minute, daily = market_bars()
    quote = {"t": NOW.isoformat(), "bp": "101.53", "ap": "101.54"}
    pilot, broker, journal = _pilot(
        tmp_path / "notional", minute, daily, quote, buying_power="101.52",
    )
    assert pilot.tick() == "RISK_CHANGED"
    assert journal.last("decision_started")
    assert journal.last("entry_intent") is None
    assert journal.last("halt") is None
    assert _posts(broker) == []
    journal.close()

    wide = []
    for index in range(15):
        wide.append({
            "t": (OPEN + timedelta(minutes=index)).isoformat(),
            "o": 95, "h": 100.00, "l": 91.54, "c": 96, "v": 1000,
        })
    wide.append({
        "t": (OPEN + timedelta(minutes=15)).isoformat(),
        "o": 100.2, "h": 101.80, "l": 100.10, "c": 101.50, "v": 1000,
    })
    daily = [{
        "t": (OPEN - timedelta(days=index)).isoformat(),
        "o": 100, "h": 112, "l": 100, "c": 106, "v": 2_000_000,
    } for index in range(20, 0, -1)]
    reference = signal_packet(wide, daily, now=NOW, session_open=OPEN, policy=POLICY)[1]
    assert Decimal(reference["limit_price"]) - Decimal(reference["stop_loss"]["stop_price"]) <= 10
    pilot, broker, journal = _pilot(tmp_path / "stop", wide, daily, quote)
    assert pilot.tick() == "RISK_CHANGED"
    assert journal.last("decision_started")
    assert journal.last("entry_intent") is None
    assert journal.last("halt") is None
    assert _posts(broker) == []
    new_risk = Decimal("101.55") - Decimal(reference["stop_loss"]["stop_price"])
    assert new_risk > 10
    journal.close()


def test_bracket_geometry_skip_does_not_send_an_order(tmp_path):
    minute, daily = market_bars()
    quote = {"t": NOW.isoformat(), "bp": "98.88", "ap": "98.90"}
    pilot, broker, journal = _pilot(tmp_path, minute, daily, quote)
    assert pilot.tick() == "ENTRY_SKIPPED"
    assert journal.last("entry_skipped")["reason"] == "BRACKET_GEOMETRY"
    assert journal.last("halt") is None
    assert _posts(broker) == []
    journal.close()


def test_a_market_order_is_not_sent(tmp_path):
    minute, daily = market_bars()
    quote = {"t": NOW.isoformat(), "bp": "101.49", "ap": "101.50"}
    pilot, broker, journal = _pilot(tmp_path, minute, daily, quote)
    packet, order = signal_packet(minute, daily, now=NOW, session_open=OPEN, policy=POLICY)
    order = {**order, "type": "market"}
    status = pilot._commit_order(
        order, packet, session_day=NOW.date().isoformat(), account_id="paper-account",
        observed_at=NOW, verdict_hash="fixture", allow_stale=False, fallback=None,
    )
    assert status == "ENTRY_SKIPPED"
    assert journal.last("entry_skipped")["reason"] == "BRACKET_GEOMETRY"
    assert _posts(broker) == []
    journal.close()


def test_fallback_submission_uses_the_live_quote(tmp_path):
    minute, daily = market_bars()
    journal = PaperJournal(tmp_path / "paper.sqlite")
    broker = Broker()
    broker.journal = journal

    def bars(symbol, start, end, timeframe, feed):
        return minute if timeframe == "1Min" else daily

    broker.bars = bars

    def latest_quote(symbol, feed):
        return {"t": broker.state["clock"]["timestamp"], "bp": "101.52", "ap": "101.53"}

    broker.latest_quote = latest_quote
    policy = PilotPolicy("AAPL", fallback_after_open_minutes=17)
    pilot = ActivePaperPilot(
        broker,
        JevDecisionEngine(
            "fixture-key", transport=lambda *args, **kwargs: scored_response(0.55, 0.9),
        ),
        journal, policy,
    )
    assert pilot.tick() == "JEV_VETO"
    broker.state["clock"]["timestamp"] = (OPEN + timedelta(minutes=17, seconds=5)).isoformat()
    assert pilot.tick() == "ENTRY_SUBMITTED"
    order = journal.last("entry_intent")["order"]
    assert order["limit_price"] == "101.54"
    assert order["stop_loss"]["stop_price"] == "99.00"
    assert order["type"] == "limit"
    assert journal.last("entry_intent")["fallback"] is True
    assert journal.last("entry_intent")["slippage_vs_close"] == "0.04"
    assert len(_posts(broker)) == 1
    journal.close()
