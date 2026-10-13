"""Shared account gate and fresh-evidence comparison. No broker and no holdout rerun."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from athena_trading.athena_adapter.contracts import canonical_hash
from athena_trading.paper.evaluation import (
    Opportunity, adverse_excursion, chronological_drawdown, compare_policies,
    fill_from_next_bar, session_uncertainty,
)
from athena_trading.paper.funnel import record_opportunity
from athena_trading.paper.journal import PaperJournal
from athena_trading.paper.learning import LearningStore
from athena_trading.paper.shared_risk import (
    UNRESOLVED_SHARED_POLICY, OpenExposure, SharedBookBusy, authorize_entry,
    exposures_from_journal, other_arm_exposure,
)


def test_shared_gate_blocks_a_second_reservation_and_lists_inactive_checks():
    open_book = [OpenExposure("earnings", "DECK", 1500.0, 40.0, True, "athe-deck")]
    same = authorize_entry(
        arm="intraday", symbol="DECK", notional=100.0, stop_risk=1.0, overnight=False,
        buying_power=100_000.0, open_exposure=open_book,
    )
    assert same.allowed is False and same.reason == "SYMBOL_ALREADY_RESERVED"
    assert same.unresolved == UNRESOLVED_SHARED_POLICY
    crowded = authorize_entry(
        arm="intraday", symbol="AAPL", notional=200.0, stop_risk=1.0, overnight=False,
        buying_power=1600.0, open_exposure=open_book,
    )
    assert crowded.reason == "BUYING_POWER"
    overnight = authorize_entry(
        arm="intraday", symbol="AAPL", notional=100.0, stop_risk=1.0, overnight=True,
        buying_power=100_000.0, open_exposure=[],
    )
    assert overnight.reason == "OVERNIGHT_NOT_AUTHORIZED"
    earnings = authorize_entry(
        arm="earnings", symbol="AAPL", notional=100.0, stop_risk=1.0, overnight=True,
        buying_power=100_000.0, open_exposure=[],
    )
    assert earnings.allowed is True
    assert earnings.overnight_authority == "earnings-paper-exception"
    assert "correlation_limit" in earnings.unresolved


def test_partial_fill_keeps_only_the_residual_shares(tmp_path):
    journal = PaperJournal(tmp_path / "book.sqlite")
    journal.append("entry_intent", order={
        "client_order_id": "athe-deck", "symbol": "DECK", "side": "buy", "qty": "19",
        "limit_price": "100", "stop_loss": {"stop_price": "98"},
    })
    journal.append("entry_observation", client_order_id="athe-deck", order={
        "client_order_id": "athe-deck", "symbol": "DECK", "side": "buy", "qty": "19",
        "filled_qty": "19", "legs": [
            {"side": "sell", "filled_qty": "5"},
            {"side": "sell", "filled_qty": "0"},
        ],
    })
    rows = exposures_from_journal(journal, "earnings")
    assert len(rows) == 1
    assert rows[0].notional == pytest.approx(1400.0)
    assert rows[0].stop_risk == pytest.approx(28.0)
    journal.append("position_closed", client_order_id="athe-deck")
    assert exposures_from_journal(journal, "earnings") == []
    journal.close()


def test_locked_other_journal_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "earnings"
    monkeypatch.setenv("ATHENA_EARNINGS_STATE_DIR", str(root))
    held = PaperJournal(root / "book.sqlite")
    held.append("binding", binding={"arm": "earnings"})
    with pytest.raises(SharedBookBusy):
        other_arm_exposure("intraday")
    held.close()
    assert other_arm_exposure("intraday") == []


def test_funnel_rejects_an_unknown_stage_and_does_not_repeat(tmp_path):
    journal = PaperJournal(tmp_path / "paper.sqlite")
    now = datetime(2026, 10, 13, 14, tzinfo=UTC)
    record_opportunity(journal, stage="model_vetoed", reason="JEV_VETO", at=now, signal_at="t1")
    record_opportunity(journal, stage="model_vetoed", reason="JEV_VETO", at=now, signal_at="t1")
    assert sum(event["kind"] == "opportunity" for event in journal.events) == 1
    with pytest.raises(ValueError, match="unknown opportunity stage"):
        record_opportunity(journal, stage="guessed", reason="NO", at=now, signal_at="t1")
    journal.close()


def _item(**overrides):
    base = dict(
        opportunity_id="z-late", session="2026-01-02", symbol="DECK", at="2026-01-02T15:00:00Z",
        decision_close=100.0, next_open=101.0, next_close=103.0, stop=99.0, target=104.0,
        direction="LONG", jev_without_memory="TAKE_TRADE", jev_with_memory="NO_TRADE",
    )
    base.update(overrides)
    return Opportunity(**base)


def test_fill_uses_the_next_open_and_a_gap_through_the_stop():
    gapped = _item(
        decision_close=100.0, next_open=97.0, next_close=96.0, stop=99.0, target=104.0,
    )
    fill = fill_from_next_bar(gapped)
    assert fill["entry"] == 97.0
    assert fill["decision_close_used_as_fill"] is False
    assert fill["gapped_through_stop"] is True
    assert fill["gross"] == 0.0
    assert adverse_excursion(gapped) == pytest.approx(-3.0)


def test_drawdown_follows_time_and_sessions_stay_together():
    rows = [
        {"at": "2026-01-02T15:00:00Z", "opportunity_id": "a", "gross": 20.0},
        {"at": "2026-01-03T15:00:00Z", "opportunity_id": "m", "gross": -10.0},
        {"at": "2026-01-01T15:00:00Z", "opportunity_id": "z", "gross": -5.0},
    ]
    assert chronological_drawdown(rows) == pytest.approx(10.0)
    band = session_uncertainty(
        [("A", 10.0), ("A", 10.0), ("B", -5.0)], resamples=40, confidence=0.95, seed=3,
    )
    assert band["sessions"] == 2
    assert band["n"] == 3
    assert band["mean"] == pytest.approx(5.0)
    # A session draw is AA, BB, or one of each. An independent draw can land elsewhere.
    assert band["lower"] in {-5.0, 5.0, 10.0}


def test_comparison_keeps_three_policies_and_does_not_claim_an_edge():
    rows = [
        _item(opportunity_id="1", at="2026-01-01T15:00:00Z", session="2026-01-01",
              direction="LONG", jev_without_memory="TAKE_TRADE", jev_with_memory="TAKE_TRADE"),
        _item(opportunity_id="2", at="2026-01-02T15:00:00Z", session="2026-01-02",
              direction="SHORT", decision_close=100.0, next_open=101.0, next_close=99.0,
              stop=102.0, target=96.0, jev_without_memory="NO_TRADE", jev_with_memory="TAKE_TRADE"),
    ]
    report = compare_policies(rows, cost_per_share=0.01)
    assert set(report["policies"]) == {"deterministic", "jev_without_memory", "jev_with_memory"}
    assert report["policies"]["deterministic"]["taken"] == 2
    assert report["policies"]["jev_without_memory"]["taken"] == 1
    assert report["policies"]["jev_with_memory"]["skipped"] == []
    assert report["policies"]["deterministic"]["by_direction"]["SHORT"]["taken"] == 1
    assert report["performance"] == "unverified"
    assert report["holdouts"] == "not read"
    assert "not a calibrated" in report["jev_confidence"]
    assert report["fill_assumption"].startswith("next bar open")


def test_restart_still_sees_the_other_book_until_it_closes(tmp_path, monkeypatch):
    root = tmp_path / "earnings"
    monkeypatch.setenv("ATHENA_EARNINGS_STATE_DIR", str(root))
    book = PaperJournal(root / "book.sqlite")
    book.append("entry_intent", order={
        "client_order_id": "athe-deck", "symbol": "DECK", "side": "buy", "qty": "10",
        "limit_price": "100", "stop_loss": {"stop_price": "98"},
    })
    book.close()
    restarted = PaperJournal(root / "book.sqlite")
    assert exposures_from_journal(restarted, "earnings")[0].notional == pytest.approx(1000.0)
    restarted.close()
    seen = other_arm_exposure("intraday")
    assert [row.symbol for row in seen] == ["DECK"]
    closed = PaperJournal(root / "book.sqlite")
    closed.append("position_closed", client_order_id="athe-deck")
    closed.close()
    assert other_arm_exposure("intraday") == []


def test_outcome_ingestion_keeps_earnings_out_of_the_intraday_study(tmp_path):
    """Offline stand-in for a reconciled close. It is not a broker session."""
    source = PaperJournal(tmp_path / "2026-07-01.sqlite")
    at = datetime(2026, 7, 1, 15, tzinfo=UTC)
    source.append("binding", binding={"account_id": "paper-account", "policy": {"symbol": "AAPL"}})
    order = {
        "client_order_id": "fixture-0", "symbol": "AAPL", "side": "buy",
        "limit_price": "100", "stop_loss": {"stop_price": "98"},
    }
    receipt = {"accepted": True, "question_hash": "q"}
    source.append(
        "entry_intent", session="2026-07-01", at=at, order=order,
        verdict_hash=canonical_hash(receipt),
    )
    source.append("adjudication", receipt=receipt)
    parent = {**order, "id": "parent", "qty": "1", "filled_qty": "1",
              "filled_avg_price": "100", "status": "filled"}
    source.append("flat_confirmed", parent=parent, order_history=[
        parent,
        {"id": "exit", "symbol": "AAPL", "side": "sell", "qty": "1", "filled_qty": "1",
         "filled_avg_price": "98", "status": "filled"},
    ], snapshot={"clock": {"timestamp": "2026-07-01T16:00:00+00:00"}, "positions": [], "orders": []})
    store = LearningStore(tmp_path / "learning")
    store.ingest(source, datetime(2026, 7, 1, 17, tzinfo=UTC))
    stored = store.experiences()
    assert len(stored) == 1
    assert stored[0]["gross_pnl"] == "-2"
    assert stored[0]["result"] == "LOSS"
    assert stored[0].get("arm") is None
    source.close()
    store.journal.close()
