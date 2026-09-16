from __future__ import annotations

from pathlib import Path

import pytest

from astra_desk.desk import TradingDesk
from astra_desk.errors import CheckpointError, LiveTradingDisabled
from astra_desk.features import strong_tape_features, weak_volume_features
from astra_desk.fixtures import deploy_fixture, walk_setup_dict
from astra_desk.human_checkpoint import (
    CheckpointFixture,
    CheckpointSetup,
    HumanCheckpointSequence,
    bind_approval,
    limit_checkpoint_trades,
    load_checkpoint_fixture,
    make_approval,
    parse_approval,
)
from astra_desk.types import DeskMode, RoundDecision
from astra_desk.walk_forward import parse_walk_setup

REPO = Path(__file__).resolve().parents[1]
SHIPPED = REPO / "fixtures" / "checkpoint" / "phase2_sample.json"

TP_BAR = {
    "ts": "t-tp",
    "open": 100.2,
    "high": 105.4,
    "low": 99.8,
    "close": 105.1,
    "volume": 1,
}

FORGED_HASH = "ab" * 32


def _walk(setup_id: str, *, settlement_pnl: float | None = None, weak: bool = False):
    return parse_walk_setup(
        walk_setup_dict(
            setup_id,
            features=weak_volume_features() if weak else strong_tape_features(),
            bars=[TP_BAR],
            settlement_pnl=settlement_pnl,
        )
    )


def _item(setup_id: str, **kwargs) -> CheckpointSetup:
    return CheckpointSetup(setup=_walk(setup_id, **kwargs))


def test_refuse_without_approval() -> None:
    desk = TradingDesk(mode=DeskMode.LIVE_WITH_HUMAN_CHECKPOINT)
    result = desk.run_round(deploy_fixture())
    assert result.executed is False
    assert result.decision is RoundDecision.REFUSE
    assert result.live_trading_authority is False
    assert result.checkpoint is not None
    assert result.checkpoint.approved is False
    assert result.checkpoint.reason == "approval_missing"
    assert result.accounting.open_positions == ()


def test_accept_with_valid_approval() -> None:
    desk = TradingDesk(mode=DeskMode.LIVE_WITH_HUMAN_CHECKPOINT)
    item = _item("ok-001")
    approval = bind_approval(
        desk,
        item.setup,
        approved_by="operator",
        approved_at="2024-05-01T12:00:00+00:00",
    )
    result = desk.run_round(item.setup.fixture, approval=approval)
    assert result.executed is True
    assert result.decision is RoundDecision.DEPLOY
    assert result.checkpoint is not None
    assert result.checkpoint.approved is True
    assert result.front_man is not None
    assert result.front_man.rationale_hash == approval.rationale_hash
    assert result.live_trading_authority is False


def test_boolean_approval_is_not_enough() -> None:
    with pytest.raises(CheckpointError, match="boolean approval"):
        parse_approval(True)
    with pytest.raises(CheckpointError, match="boolean approval"):
        parse_approval({"approved": True})
    with pytest.raises(CheckpointError, match="missing fields"):
        parse_approval({"approved": True, "approved_by": "operator"})


def test_forged_or_incomplete_approval_refused() -> None:
    desk = TradingDesk(mode=DeskMode.LIVE_WITH_HUMAN_CHECKPOINT)
    item = _item("forge-001")
    genuine = bind_approval(
        desk,
        item.setup,
        approved_by="operator",
        approved_at="2024-05-01T12:00:00+00:00",
    )
    forged = make_approval(
        approved_by=genuine.approved_by,
        approved_at=genuine.approved_at,
        setup_id=genuine.setup_id,
        rationale_hash=FORGED_HASH,
    )
    result = desk.run_round(item.setup.fixture, approval=forged)
    assert result.executed is False
    assert result.checkpoint is not None
    assert result.checkpoint.reason == "rationale_hash_mismatch"

    wrong_setup = make_approval(
        approved_by=genuine.approved_by,
        approved_at=genuine.approved_at,
        setup_id="other-setup",
        rationale_hash=genuine.rationale_hash,
    )
    result = desk.run_round(item.setup.fixture, approval=wrong_setup)
    assert result.executed is False
    assert result.checkpoint is not None
    assert result.checkpoint.reason == "setup_id_mismatch"


def test_approval_replay_refused() -> None:
    desk = TradingDesk(mode=DeskMode.LIVE_WITH_HUMAN_CHECKPOINT)
    first = _item("replay-a")
    approval = bind_approval(
        desk,
        first.setup,
        approved_by="operator",
        approved_at="2024-05-01T12:00:00+00:00",
    )
    accepted = desk.run_round(first.setup.fixture, approval=approval)
    assert accepted.executed is True
    # Same record cannot authorize a second deploy, even of an identical tape.
    second = _item("replay-a")
    replayed = desk.run_round(second.setup.fixture, approval=approval)
    assert replayed.executed is False
    assert replayed.checkpoint is not None
    assert replayed.checkpoint.reason == "approval_replay"


def test_count_completes_at_25_and_stops() -> None:
    seq = HumanCheckpointSequence(approver="operator")
    extra = tuple(_item(f"cp-{i:03d}") for i in range(1, 27))
    report = seq.run(CheckpointFixture(setups=extra))
    assert report.trades_required == 25
    assert report.trades_completed == 25
    assert report.trade_count_complete is True
    assert report.setups_processed == 25
    assert report.halted is False
    assert report.live_trading_authority is False
    with pytest.raises(CheckpointError, match="already complete"):
        seq.run_setup(_item("cp-026"))


def test_incomplete_before_25() -> None:
    seq = HumanCheckpointSequence(approver="operator")
    report = seq.run(CheckpointFixture(setups=tuple(_item(f"n-{i:03d}") for i in range(24))))
    assert report.trades_completed == 24
    assert report.trade_count_complete is False


def test_unapproved_sequence_does_not_count() -> None:
    seq = HumanCheckpointSequence()
    report = seq.run(CheckpointFixture(setups=tuple(_item(f"u-{i:03d}") for i in range(5))))
    assert report.trades_completed == 0
    assert report.trade_count_complete is False
    assert report.setups_processed == 5
    assert seq.desk.books.positions == ()


def test_kill_switch_aborts_checkpoint_sequence() -> None:
    seq = HumanCheckpointSequence(approver="operator")
    seq.run_setup(_item("win"))
    assert seq.progress().trades_completed == 1
    progress_before = seq.progress()
    assert progress_before.halted is False
    seq.run_setup(_item("shock", settlement_pnl=-42.0))
    progress = seq.progress()
    assert progress.halted is True
    assert progress.trade_count_complete is False
    with pytest.raises(CheckpointError, match="kill switch halted"):
        seq.run_setup(_item("never"))


def test_gate_refuse_does_not_need_approval_and_does_not_count() -> None:
    seq = HumanCheckpointSequence()
    result = seq.run_setup(_item("weak", weak=True))
    assert result.executed is False
    assert result.decision is RoundDecision.REFUSE
    assert result.checkpoint is not None
    assert result.checkpoint.reason == "not_a_deploy"
    assert seq.progress().trades_completed == 0


def test_phase_3_does_not_bypass_checkpoint() -> None:
    auto = TradingDesk(mode=DeskMode.AUTONOMOUS)
    with pytest.raises(CheckpointError, match="live_with_human_checkpoint"):
        HumanCheckpointSequence(auto)
    paper = TradingDesk(mode=DeskMode.PAPER_TRADING)
    with pytest.raises(CheckpointError, match="live_with_human_checkpoint"):
        HumanCheckpointSequence(paper)
    with pytest.raises(LiveTradingDisabled):
        TradingDesk(live_broker=True)


def test_autonomous_fixture_rejected(tmp_path: Path) -> None:
    path = tmp_path / "live.json"
    path.write_text(
        '{"schema":"astra-desk/human-checkpoint-v1","mode":"autonomous","setups":[{}]}',
        encoding="utf-8",
    )
    with pytest.raises(LiveTradingDisabled):
        load_checkpoint_fixture(path)


def test_shipped_fixture_completes_with_approver() -> None:
    fixture = load_checkpoint_fixture(SHIPPED)
    assert len(fixture.setups) == 25
    refused = HumanCheckpointSequence().run(fixture)
    assert refused.trades_completed == 0
    assert refused.trade_count_complete is False
    truncated = limit_checkpoint_trades(fixture, 5)
    short = HumanCheckpointSequence(approver="operator").run(truncated)
    assert short.trades_completed == 5
    assert short.trade_count_complete is False
    complete = HumanCheckpointSequence(approver="operator").run(fixture)
    assert complete.trades_completed == 25
    assert complete.trade_count_complete is True
    assert complete.mode is DeskMode.LIVE_WITH_HUMAN_CHECKPOINT
    assert complete.live_trading_authority is False
    assert complete.reenable == "manual_human_only"
