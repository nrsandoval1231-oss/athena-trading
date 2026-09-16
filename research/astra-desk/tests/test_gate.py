import pytest

from astra_desk.contract import load_contract
from astra_desk.gate import evaluate_gate, weighted_conviction


def test_deploy_above_threshold() -> None:
    contract = load_contract()
    weights = contract.gate.conviction_weights
    convictions = {
        "456_scout": 1.0,
        "067_reader": 1.0,
        "240_exit": 1.0,
        "666_skeptic": 0.0,
    }
    assert weighted_conviction(convictions, weights) == 3.5
    decision = evaluate_gate(
        convictions=convictions,
        weights=weights,
        bands=contract.gate.conviction_bands,
        quorum_have=6,
        quorum_required=5,
        ev_estimate=0.1,
    )
    assert decision.deployed is True
    assert decision.size_multiplier == 0.5
    assert decision.reason == "deploy_rule_satisfied"


def test_full_band_when_weighted_hits_4_5() -> None:
    contract = load_contract()
    convictions = {
        "456_scout": 1.0,
        "067_reader": 1.667,
        "240_exit": 1.0,
        "666_skeptic": 0.0,
    }
    decision = evaluate_gate(
        convictions=convictions,
        weights=contract.gate.conviction_weights,
        bands=contract.gate.conviction_bands,
        quorum_have=6,
        quorum_required=5,
        ev_estimate=0.1,
    )
    assert decision.deployed is True
    assert decision.size_multiplier == 1.0
    assert decision.total_weighted_conviction >= 4.5


def test_refuse_below_threshold() -> None:
    contract = load_contract()
    convictions = {
        "456_scout": 0.4,
        "067_reader": 0.4,
        "240_exit": 0.4,
        "666_skeptic": 0.0,
    }
    decision = evaluate_gate(
        convictions=convictions,
        weights=contract.gate.conviction_weights,
        bands=contract.gate.conviction_bands,
        quorum_have=6,
        quorum_required=5,
        ev_estimate=0.5,
    )
    assert decision.deployed is False
    assert decision.size_multiplier == 0.0
    assert decision.reason == "below_conviction_threshold"


def test_skeptic_weight_subtracts() -> None:
    contract = load_contract()
    weights = contract.gate.conviction_weights
    base = {
        "456_scout": 1.0,
        "067_reader": 1.0,
        "240_exit": 1.0,
        "666_skeptic": 0.0,
    }
    opposed = {**base, "666_skeptic": 0.8}
    without = weighted_conviction(base, weights)
    with_skeptic = weighted_conviction(opposed, weights)
    assert without == 3.5
    assert with_skeptic == pytest.approx(2.7)
    blocked = evaluate_gate(
        convictions=opposed,
        weights=weights,
        bands=contract.gate.conviction_bands,
        quorum_have=6,
        quorum_required=5,
        ev_estimate=0.5,
    )
    assert blocked.deployed is False
    assert blocked.total_weighted_conviction < 3.5


def test_refuse_when_ev_not_positive() -> None:
    contract = load_contract()
    convictions = {
        "456_scout": 1.0,
        "067_reader": 1.0,
        "240_exit": 1.0,
        "666_skeptic": 0.0,
    }
    decision = evaluate_gate(
        convictions=convictions,
        weights=contract.gate.conviction_weights,
        bands=contract.gate.conviction_bands,
        quorum_have=6,
        quorum_required=5,
        ev_estimate=0.0,
    )
    assert decision.deployed is False
    assert decision.reason == "ev_not_positive"


def test_refuse_when_quorum_missing() -> None:
    contract = load_contract()
    convictions = {
        "456_scout": 1.0,
        "067_reader": 1.0,
        "240_exit": 1.0,
        "666_skeptic": 0.0,
    }
    decision = evaluate_gate(
        convictions=convictions,
        weights=contract.gate.conviction_weights,
        bands=contract.gate.conviction_bands,
        quorum_have=4,
        quorum_required=5,
        ev_estimate=1.0,
    )
    assert decision.deployed is False
    assert decision.reason == "quorum_not_met"
