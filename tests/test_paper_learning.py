from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta

import pytest

from athena_trading.athena_adapter.contracts import canonical_hash
from athena_trading.athena_adapter.jev import questions
from athena_trading.paper.journal import PaperJournal
from athena_trading.paper.learning import LearningStore, NY, maintain, outcome
from athena_trading.paper.live import ActivePaperPilot, PilotPolicy

POLICY = PilotPolicy("AAPL")
START = datetime(2026, 7, 1, 15, tzinfo=UTC)


def completed(directory, index=0, pnl=-2.0, qty="1", policy=POLICY, side="buy"):
    at = START + timedelta(days=index)
    journal = PaperJournal(directory / (at.date().isoformat() + ".sqlite"))
    journal.append("binding", binding={"account_id": "paper-account", "policy": asdict(policy)})
    order = {"client_order_id": f"fixture-{index}", "symbol": "AAPL", "side": side,
             "limit_price": "100", "stop_loss": {"stop_price": "98"}}
    receipt = {"accepted": True, "question_hash": canonical_hash(questions())}
    journal.append("adjudication", receipt=receipt)
    journal.append("entry_intent", session=at.date().isoformat(), at=at, order=order,
                   verdict_hash=canonical_hash(receipt))
    parent = {**order, "id": "parent", "qty": qty, "filled_qty": qty,
              "filled_avg_price": "100", "status": "filled" if qty != "0" else "canceled"}
    history = [parent]
    if qty != "0":
        exit_price = 100 + (pnl / float(qty)) * (1 if side == "buy" else -1)
        history.append({"id": "exit", "symbol": "AAPL", "side": "sell" if side == "buy" else "buy",
                        "qty": qty, "filled_qty": qty, "filled_avg_price": str(exit_price),
                        "status": "filled"})
    journal.append("flat_confirmed", parent=parent, order_history=history,
                   snapshot={"clock": {"timestamp": (at + timedelta(hours=1)).isoformat()},
                             "positions": [], "orders": []})
    return journal, at + timedelta(hours=2)


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_outcome_uses_reconciled_broker_cashflows_for_long_and_short(tmp_path, side):
    source, now = completed(tmp_path, pnl=3, side=side)
    episode = outcome(source, now)
    assert episode["gross_pnl"] == "3.0" and episode["gross_r"] == 1.5
    assert episode["net_pnl"] is None and episode["costs"] == "UNMEASURED"
    assert episode["eligible"]
    source.close()


@pytest.mark.parametrize("qty", ["0", "0.5"])
def test_no_fill_and_partial_fill_are_recorded_but_excluded_from_rule_training(tmp_path, qty):
    source, now = completed(tmp_path, qty=qty)
    episode = outcome(source, now)
    assert episode["eligible"] is False
    assert episode["status"] == ("NO_FILL" if qty == "0" else "CLOSED")
    source.close()


@pytest.mark.parametrize("fault", ["missing_exit", "duplicate", "wrong_symbol", "nan", "future"])
def test_invalid_outcomes_cannot_train_rules(tmp_path, fault):
    source, now = completed(tmp_path)
    flat = source.last("flat_confirmed")
    if fault == "missing_exit":
        flat["order_history"].pop()
    elif fault == "duplicate":
        flat["order_history"].append(flat["order_history"][0])
    elif fault == "wrong_symbol":
        flat["order_history"][1]["symbol"] = "MSFT"
    elif fault == "nan":
        flat["order_history"][1]["filled_avg_price"] = "NaN"
    else:
        now = START
    with pytest.raises(ValueError):
        outcome(source, now)
    source.close()


def test_intraday_experience_is_idempotent_and_point_in_time(tmp_path):
    source, now = completed(tmp_path)
    store = LearningStore(tmp_path)
    store.ingest(source, now)
    store.ingest(source, now + timedelta(seconds=1))
    assert len(store.experiences()) == 1
    assert not store.context("paper-account", "AAPL", "LONG", now)["recent_experiences"]
    later = now + timedelta(seconds=1)
    assert len(store.context("paper-account", "AAPL", "LONG", later)["recent_experiences"]) == 1
    assert not store.context("other-account", "AAPL", "LONG", later)["recent_experiences"]
    assert not store.context("paper-account", "MSFT", "LONG", later)["recent_experiences"]
    assert not store.context("paper-account", "AAPL", "SHORT", later)["recent_experiences"]
    assert not store.context("paper-account", "AAPL", "LONG", later,
                             asdict(PilotPolicy("AAPL", feed="sip")))["recent_experiences"]
    source.last("flat_confirmed")["order_history"][1]["filled_avg_price"] = "99"
    with pytest.raises(ValueError, match="changed"):
        store.ingest(source, later)
    source.close()
    store.close()


def test_receipt_substitution_blocks_learning(tmp_path):
    source, now = completed(tmp_path)
    source.last("adjudication")["receipt"]["accepted"] = False
    with pytest.raises(ValueError, match="receipt"):
        outcome(source, now)
    source.close()


def test_observed_experience_reaches_the_actual_jev_request(tmp_path):
    from test_active_paper import Broker, response
    from athena_trading.athena_adapter.jev import JevDecisionEngine

    source, now = completed(tmp_path, index=83)
    store = LearningStore(tmp_path)
    store.ingest(source, now)
    source.close()
    calls = []

    def transport(method, endpoint, headers, payload, **kwargs):
        calls.append(payload)
        assert payload["state"]["context"]["recent_experiences"][0]["result"] == "LOSS"
        return response("NO_TRADE")

    journal = PaperJournal(tmp_path / "current.sqlite")
    broker = Broker()
    broker.journal = journal
    pilot = ActivePaperPilot(broker, JevDecisionEngine("fixture", transport=transport),
                             journal, POLICY, learning=store)
    assert pilot.tick() == "JEV_VETO"
    assert len(calls) == 1 and "proposal" not in calls[0]["state"]
    assert not any(m == "POST" for m, _, _ in broker.calls)
    store.close()
    journal.close()


def add_experiences(store, directory, start, stop, pnl=-2):
    for index in range(start, stop):
        source, now = completed(directory, index, pnl=pnl)
        store.ingest(source, now)
        source.close()
    return now


def night(now):
    return now.astimezone(NY).replace(hour=20, minute=15, second=0)


def test_learning_requires_new_prospective_outcomes_then_expires(tmp_path):
    store = LearningStore(tmp_path)
    now = add_experiences(store, tmp_path, 0, 20)
    review_at = night(now)
    assert store.review(review_at.date(), review_at - timedelta(seconds=1)) is None
    store.review(review_at.date(), review_at)
    assert store.journal.last("candidate")["eligible"]
    assert not store.journal.last("promoted")
    assert store.review(review_at.date(), review_at + timedelta(minutes=1)) is None
    now = add_experiences(store, tmp_path, 20, 39)
    store.review(night(now).date(), night(now))
    assert not store.journal.last("validation")
    now = add_experiences(store, tmp_path, 39, 40)
    review_at = night(now)
    store.review(review_at.date(), review_at)
    validation = store.journal.last("validation")
    assert validation["disposition"] == "PROMOTED"
    assert not set(validation["source_ids"]) & set(store.journal.last("candidate")["source_ids"])
    context = store.context("paper-account", "AAPL", "LONG", review_at + timedelta(seconds=1), asdict(POLICY))
    assert context["validated_rules"][0]["action"] == "NO_TRADE"
    assert not store.context("paper-account", "AAPL", "LONG", review_at + timedelta(days=14),
                             asdict(POLICY))["validated_rules"]
    store.write_reports()
    assert json.loads((tmp_path / "learning" / f"{review_at.date()}.json").read_text())["session"] == str(review_at.date())
    store.close()
    resumed = LearningStore(tmp_path)
    assert resumed.journal.last("validation") == validation
    resumed.close()


def test_retroactive_split_cannot_pass_as_prospective_validation(tmp_path):
    store = LearningStore(tmp_path)
    now = add_experiences(store, tmp_path, 0, 40)
    store.review(night(now).date(), night(now))
    store.review((night(now) + timedelta(days=1)).date(), night(now) + timedelta(days=1))
    assert store.journal.last("candidate")
    assert not store.journal.last("validation")
    store.close()


def test_negative_discovery_with_positive_validation_is_rejected(tmp_path):
    store = LearningStore(tmp_path)
    now = add_experiences(store, tmp_path, 0, 20)
    store.review(night(now).date(), night(now))
    now = add_experiences(store, tmp_path, 20, 40, pnl=2)
    store.review(night(now).date(), night(now))
    assert store.journal.last("validation")["disposition"] == "REJECTED"
    assert not store.journal.last("promoted")
    store.close()


def test_unresolved_exposure_defers_nightly_review(tmp_path):
    store = LearningStore(tmp_path)
    now = night(START)
    assert store.review(now.date(), now, unresolved=True) is None
    assert not store.journal.last("nightly_review")
    store.close()


def test_scheduler_recovers_missed_review_and_derived_report(tmp_path):
    source, now = completed(tmp_path)
    source.close()
    now = night(now) + timedelta(days=1)
    maintain(tmp_path, now)
    path = tmp_path / "learning" / (str(START.date()) + ".json")
    assert path.exists()
    path.unlink()
    maintain(tmp_path, now)
    assert path.exists()
    store = LearningStore(tmp_path)
    assert len(store.experiences()) == 1
    assert sum(e["kind"] == "nightly_review" for e in store.journal.events) == 1
    store.close()


def test_journal_checkpoint_detects_tail_deletion_and_missing_anchor(tmp_path):
    path = tmp_path / "ledger.sqlite"
    journal = PaperJournal(path)
    journal.append("one")
    journal.append("two")
    journal.close()
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM events WHERE seq=2")
    with pytest.raises(ValueError, match="truncated"):
        PaperJournal(path)
    path.with_suffix(".sqlite.checkpoint").unlink()
    with pytest.raises(ValueError, match="missing"):
        PaperJournal(path)


def test_journal_recovers_commit_before_checkpoint_crash(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite"
    journal = PaperJournal(path)

    def fail():
        raise OSError("fixture interrupted checkpoint")

    monkeypatch.setattr(journal, "_checkpoint", fail)
    with pytest.raises(OSError):
        journal.append("committed")
    journal.close()
    recovered = PaperJournal(path)
    assert recovered.last("committed")
    recovered.close()


def test_nightly_schedule_uses_eastern_daylight_saving_time(tmp_path):
    store = LearningStore(tmp_path)
    # 20:15 ET is 00:15 UTC next day in summer, 01:15 UTC next day in winter.
    assert store.review(date(2026, 7, 1), datetime(2026, 7, 2, 0, 14, tzinfo=UTC)) is None
    assert store.review(date(2026, 7, 1), datetime(2026, 7, 2, 0, 15, tzinfo=UTC))
    assert store.review(date(2026, 12, 1), datetime(2026, 12, 2, 1, 14, tzinfo=UTC)) is None
    assert store.review(date(2026, 12, 1), datetime(2026, 12, 2, 1, 15, tzinfo=UTC))
    store.close()


def test_promoted_rule_changes_actual_pilot_behavior(tmp_path):
    from test_active_paper import Broker, NOW

    store = LearningStore(tmp_path)
    # Use dates that leave the rule active at the pilot fixture's September session.
    now = add_experiences(store, tmp_path, 44, 64)
    store.review(night(now).date(), night(now))
    now = add_experiences(store, tmp_path, 64, 84)
    store.review(night(now).date(), night(now))
    journal = PaperJournal(tmp_path / "pilot.sqlite")
    broker = Broker()
    broker.journal = journal
    assert store.context("paper-account", "AAPL", "LONG", NOW, asdict(POLICY))["validated_rules"]
    # A sentinel fails if the model is called: the learned rule is enforced by code.
    pilot = ActivePaperPilot(broker, object(), journal, POLICY, learning=store)
    assert pilot.tick() == "LEARNED_VETO"
    assert journal.last("learning_veto")
    assert not any(m == "POST" for m, _, _ in broker.calls)
    journal.close()
    store.close()
