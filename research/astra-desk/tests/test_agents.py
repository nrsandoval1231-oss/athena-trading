from __future__ import annotations

from dataclasses import replace

import pytest

from astra_desk.agents.base import AgentContext
from astra_desk.agents.rules import (
    AdversarialSkeptic,
    DemandAuthenticityReader,
    InvalidationExit,
    VolumeLeadScout,
)
from astra_desk.agents.stubs import build_stub_agents
from astra_desk.contract import load_contract
from astra_desk.desk import TradingDesk
from astra_desk.errors import AgentFeatureError
from astra_desk.features import (
    skeptic_flag_features,
    strong_tape_features,
    weak_volume_features,
)
from astra_desk.fixtures import _base, deploy_fixture, full_size_fixture, refuse_fixture
from astra_desk.ledger import AccuracyTracker
from astra_desk.types import RoundDecision


def _ctx(fixture) -> AgentContext:
    contract = load_contract()
    return AgentContext(
        regime=fixture.regime,
        fixture=fixture,
        books=TradingDesk(contract=contract).books,
        contract=contract,
        accuracy=AccuracyTracker(),
    )


def test_missing_features_fail_closed() -> None:
    fixture = replace(deploy_fixture(), features=None)
    with pytest.raises(AgentFeatureError, match="require setup features"):
        VolumeLeadScout().evaluate(_ctx(fixture))


def test_wrong_side_invalidation_fails_closed() -> None:
    fixture = _base(
        setup_id="bad-stop",
        features=strong_tape_features(invalidation_price=105.0),
    )
    with pytest.raises(AgentFeatureError, match="below price"):
        InvalidationExit().evaluate(_ctx(fixture))


def test_feature_sets_change_gate_without_hand_set_convictions() -> None:
    deploy = TradingDesk().run_round(
        _base(setup_id="feat-deploy", features=strong_tape_features())
    )
    refuse = TradingDesk().run_round(
        _base(setup_id="feat-refuse", features=weak_volume_features())
    )
    blocked = TradingDesk().run_round(
        _base(setup_id="feat-skeptic", features=skeptic_flag_features())
    )
    assert deploy.decision is RoundDecision.DEPLOY
    assert deploy.gate.total_weighted_conviction == pytest.approx(3.5)
    assert deploy.agent_outputs["456_scout"].conviction == pytest.approx(1.0)
    assert refuse.decision is RoundDecision.REFUSE
    assert refuse.gate.total_weighted_conviction < 3.5
    assert blocked.decision is RoundDecision.REFUSE
    assert blocked.agent_outputs["666_skeptic"].conviction == pytest.approx(0.8)
    assert blocked.gate.total_weighted_conviction == pytest.approx(2.7)


def test_rule_agents_ignore_leftover_stub_convictions() -> None:
    poisoned = replace(
        refuse_fixture(),
        scout_conviction=1.0,
        reader_conviction=1.0,
        exit_conviction=1.0,
        skeptic_conviction=0.0,
    )
    result = TradingDesk().run_round(poisoned)
    assert result.decision is RoundDecision.REFUSE


def test_stubs_are_explicit_doubles_only() -> None:
    result = TradingDesk(use_stubs=True).run_round(full_size_fixture())
    assert result.gate.size_multiplier == 1.0
    assert result.final_size_usd == 40.0


def test_scout_reader_exit_skeptic_shapes() -> None:
    ctx = _ctx(deploy_fixture())
    scout = VolumeLeadScout().evaluate(ctx)
    reader = DemandAuthenticityReader().evaluate(ctx)
    exit_out = InvalidationExit().evaluate(ctx)
    skeptic = AdversarialSkeptic().evaluate(ctx)
    assert scout.conviction == pytest.approx(1.0)
    assert "volume_precedes_price" in scout.rationale
    assert "regime=trending" in scout.rationale
    assert reader.p_organic == pytest.approx(1.0)
    assert reader.conviction == pytest.approx(1.0)
    assert exit_out.stop_price == 95.0
    assert exit_out.take_profit_levels == pytest.approx((105.0,))
    assert skeptic.conviction == pytest.approx(0.0)
    assert skeptic.counterarguments
    assert skeptic.strongest_opposing_case


def test_default_roster_is_not_stubs() -> None:
    desk = TradingDesk()
    assert type(desk.agents["456_scout"]).__name__ == "VolumeLeadScout"
    stub_desk = TradingDesk(use_stubs=True)
    assert type(stub_desk.agents["456_scout"]).__name__ == "ScoutAgent"
    assert set(build_stub_agents(load_contract())) == set(desk.agents)
