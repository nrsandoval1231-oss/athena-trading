from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from astra_desk.autonomous import (
    AutonomousFixture,
    AutonomousPaperLoop,
    evaluate_phase3_requirements,
    limit_autonomous_rounds,
    load_autonomous_fixture,
)
from astra_desk.desk import TradingDesk
from astra_desk.errors import AutonomousError, CheckpointError, LiveTradingDisabled
from astra_desk.features import strong_tape_features
from astra_desk.fixtures import deploy_fixture, walk_setup_dict
from astra_desk.human_checkpoint import HumanCheckpointSequence
from astra_desk.types import DeskMode, RoundDecision
from astra_desk.walk_forward import parse_walk_setup

REPO = Path(__file__).resolve().parents[1]
SHIPPED = REPO / "fixtures" / "autonomous" / "phase3_sample.json"

TP_BAR = {
    "ts": "t-tp",
    "open": 100.2,
    "high": 105.4,
    "low": 99.8,
    "close": 105.1,
    "volume": 1,
}


def _setup(setup_id: str, *, settlement_pnl: float | None = None):
    return parse_walk_setup(
        walk_setup_dict(
            setup_id,
            features=strong_tape_features(),
            bars=[TP_BAR],
            settlement_pnl=settlement_pnl,
        )
    )


def test_refuse_if_kill_switch_not_armed() -> None:
    desk = TradingDesk(mode=DeskMode.AUTONOMOUS)
    desk.kill_switch.disarm()
    assert desk.kill_switch.armed is False
    with pytest.raises(AutonomousError, match="kill_switch_not_armed"):
        desk.run_round(deploy_fixture())
    with pytest.raises(AutonomousError, match="kill_switch_not_armed"):
        AutonomousPaperLoop(desk).run(AutonomousFixture(setups=(_setup("nope"),)))


def test_refuse_if_kill_switch_tripped() -> None:
    desk = TradingDesk(mode=DeskMode.AUTONOMOUS)
    desk.books.apply_drawdown_mark(8.0)
    desk.kill_switch.poll(desk.books)
    assert desk.kill_switch.halted is True
    assert desk.kill_switch.armed is False
    reqs = evaluate_phase3_requirements(desk)
    assert reqs.kill_switch_armed is False
    assert reqs.met is False
    with pytest.raises(AutonomousError, match="kill_switch_tripped"):
        AutonomousPaperLoop(desk).run(AutonomousFixture(setups=(_setup("nope"),)))


def test_refuse_if_ledger_integrity_fails() -> None:
    desk = TradingDesk(mode=DeskMode.AUTONOMOUS)
    desk.run_round(deploy_fixture())
    ok, reason = desk.ledger.verify()
    assert ok is True
    assert reason == "ledger_verified"
    broken = replace(desk.ledger.entries[0], entry_hash="00" * 32)
    desk.ledger._entries = (broken,) + desk.ledger.entries[1:]
    verified, why = desk.ledger.verify()
    assert verified is False
    assert why == "entry_hash_mismatch"
    with pytest.raises(AutonomousError, match="ledger_integrity_failed"):
        desk.run_round(deploy_fixture())
    with pytest.raises(AutonomousError, match="ledger_integrity_failed"):
        AutonomousPaperLoop(desk).run(AutonomousFixture(setups=(_setup("nope"),)))


def test_refuse_live_broker(tmp_path: Path) -> None:
    with pytest.raises(LiveTradingDisabled, match="live broker"):
        TradingDesk(live_broker=True)
    with pytest.raises(LiveTradingDisabled, match="live broker"):
        TradingDesk(mode=DeskMode.AUTONOMOUS, live_broker=True)
    path = tmp_path / "live.json"
    path.write_text(
        '{"schema":"astra-desk/autonomous-v1","mode":"live","setups":[{}]}',
        encoding="utf-8",
    )
    with pytest.raises(LiveTradingDisabled):
        load_autonomous_fixture(path)


def test_succeed_when_armed_and_verified() -> None:
    desk = TradingDesk(mode=DeskMode.AUTONOMOUS)
    reqs = evaluate_phase3_requirements(desk)
    assert reqs.kill_switch_armed is True
    assert reqs.ledger_verified is True
    assert reqs.met is True
    result = desk.run_round(deploy_fixture())
    assert result.executed is True
    assert result.decision is RoundDecision.DEPLOY
    assert result.live_trading_authority is False
    assert result.checkpoint is None
    fixture = load_autonomous_fixture(SHIPPED)
    report = AutonomousPaperLoop().run(fixture)
    assert report.rounds_requested == 5
    assert report.rounds_processed == 5
    assert report.halted is False
    assert report.requirements.met is True
    assert report.requirements.kill_switch_armed is True
    assert report.requirements.ledger_verified is True
    assert report.live_trading_authority is False
    assert report.reenable == "manual_human_only"
    assert report.mode is DeskMode.AUTONOMOUS
    truncated = limit_autonomous_rounds(fixture, 2)
    short = AutonomousPaperLoop().run(truncated)
    assert short.rounds_processed == 2
    assert short.rounds_requested == 2


def test_kill_switch_halts_mid_run() -> None:
    loop = AutonomousPaperLoop()
    loop.run_setup(_setup("win"))
    assert loop.desk.kill_switch.halted is False
    loop.run_setup(_setup("shock", settlement_pnl=-42.0))
    assert loop.desk.kill_switch.halted is True
    with pytest.raises(AutonomousError, match="kill switch halted"):
        loop.run_setup(_setup("never"))
    report = AutonomousPaperLoop().run(
        AutonomousFixture(
            setups=(
                _setup("win-2"),
                _setup("shock-2", settlement_pnl=-42.0),
                _setup("never-2"),
            )
        )
    )
    assert report.halted is True
    assert report.rounds_processed == 2
    assert report.live_trading_authority is False


def test_phase_2_checkpoint_still_requires_approval() -> None:
    auto = TradingDesk(mode=DeskMode.AUTONOMOUS)
    with pytest.raises(CheckpointError, match="live_with_human_checkpoint"):
        HumanCheckpointSequence(auto)
    check = TradingDesk(mode=DeskMode.LIVE_WITH_HUMAN_CHECKPOINT)
    refused = check.run_round(deploy_fixture())
    assert refused.executed is False
    assert refused.checkpoint is not None
    assert refused.checkpoint.reason == "approval_missing"


def test_empty_ledger_is_verified() -> None:
    desk = TradingDesk(mode=DeskMode.AUTONOMOUS)
    ok, reason = desk.ledger.verify()
    assert ok is True
    assert reason == "ledger_verified"
    assert desk.kill_switch.armed is True
    assert desk.kill_switch.state().reenable == "manual_human_only"
