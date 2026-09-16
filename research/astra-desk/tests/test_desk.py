from dataclasses import replace

import pytest

from astra_desk.desk import TradingDesk
from astra_desk.errors import LiveTradingDisabled
from astra_desk.fixtures import (
    deploy_fixture,
    ev_refuse_fixture,
    full_size_fixture,
    refuse_fixture,
    risk_veto_fixture,
    skeptic_blocks_fixture,
)
from astra_desk.ledger import GENESIS_HASH
from astra_desk.types import DeskMode, RoundDecision


def test_fixture_round_deploys_and_logs() -> None:
    desk = TradingDesk()
    result = desk.run_round(deploy_fixture())
    assert result.decision is RoundDecision.DEPLOY
    assert result.executed is True
    assert result.final_size_usd == 20.0  # 40 * 0.5 band
    assert result.gate.total_weighted_conviction == 3.5
    assert result.front_man is not None
    assert result.front_man.decision == "deploy"
    assert result.front_man.gate_inputs
    assert len(result.front_man.rationale_hash) == 64
    assert result.accounting.starting_equity == 500.0
    assert result.accounting.currency == "USD"
    assert result.accounting.exposure_pct == 4.0
    assert result.live_trading_authority is False
    assert result.regime.value == "trending"
    assert "456_scout" in result.agent_outputs
    assert len(desk.ledger) == 1


def test_desk_refuses_below_threshold() -> None:
    result = TradingDesk().run_round(refuse_fixture())
    assert result.decision is RoundDecision.REFUSE
    assert result.executed is False
    assert result.final_size_usd == 0.0


def test_skeptic_blocks_deploy() -> None:
    result = TradingDesk().run_round(skeptic_blocks_fixture())
    assert result.gate.total_weighted_conviction == pytest.approx(2.7)
    assert result.decision is RoundDecision.REFUSE
    assert result.executed is False


def test_ev_zero_is_refused() -> None:
    result = TradingDesk().run_round(ev_refuse_fixture())
    assert result.gate.reason == "ev_not_positive"
    assert result.executed is False


def test_risk_officer_veto_on_desk_round() -> None:
    result = TradingDesk().run_round(risk_veto_fixture())
    assert result.gate.deployed is True
    assert result.risk is not None
    assert result.risk.approved is False
    assert result.decision is RoundDecision.VETO
    assert result.executed is False
    assert result.accounting.open_positions == ()


def test_full_size_band_and_regime_accuracy_hooks() -> None:
    fixture = replace(
        full_size_fixture(),
        resolved_correct_by_agent={"456_scout": True, "067_reader": False},
    )
    desk = TradingDesk(use_stubs=True)
    result = desk.run_round(fixture)
    assert result.final_size_usd == 40.0
    assert desk.accuracy.agent_accuracy()["456_scout"] == 1.0
    assert desk.accuracy.agent_accuracy()["067_reader"] == 0.0
    stats = desk.accuracy.regime_stats()
    assert "trending" in stats
    assert stats["trending"]["456_scout"]["n"] == 1


def test_live_mode_fail_closed() -> None:
    with pytest.raises(LiveTradingDisabled):
        TradingDesk(live_broker=True)


def test_autonomous_mode_is_paper_not_live() -> None:
    desk = TradingDesk(mode=DeskMode.AUTONOMOUS)
    result = desk.run_round(deploy_fixture())
    assert result.mode is DeskMode.AUTONOMOUS
    assert result.live_trading_authority is False
    assert result.executed is True
    assert result.decision is RoundDecision.DEPLOY


def test_checkpoint_mode_is_paper_not_live() -> None:
    desk = TradingDesk(mode=DeskMode.LIVE_WITH_HUMAN_CHECKPOINT)
    result = desk.run_round(deploy_fixture())
    assert result.mode is DeskMode.LIVE_WITH_HUMAN_CHECKPOINT
    assert result.live_trading_authority is False
    assert result.executed is False
    assert result.decision is RoundDecision.REFUSE


def test_ledger_is_append_only_hash_chain() -> None:
    desk = TradingDesk()
    first = desk.run_round(deploy_fixture())
    second = desk.run_round(refuse_fixture())
    entries = desk.ledger.entries
    assert entries[0].prev_hash == GENESIS_HASH
    assert entries[1].prev_hash == entries[0].entry_hash
    assert first.ledger_entry_hash == entries[0].entry_hash
    assert second.ledger_entry_hash == entries[1].entry_hash
    assert not hasattr(desk.ledger, "rewrite")
    assert not hasattr(desk.ledger, "delete")


def test_paper_mode_allowed() -> None:
    desk = TradingDesk(mode=DeskMode.PAPER_TRADING)
    result = desk.run_round(deploy_fixture())
    assert result.mode is DeskMode.PAPER_TRADING
    assert result.executed is True
