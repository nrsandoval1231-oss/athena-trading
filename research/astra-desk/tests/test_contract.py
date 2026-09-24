from __future__ import annotations

import copy
from pathlib import Path

import pytest

from astra_desk.contract import load_contract, load_schema, load_yaml, validate_contract_data
from astra_desk.errors import ContractError
from astra_desk.types import Regime

REPO = Path(__file__).resolve().parents[1]
CONTRACT = REPO / "contracts" / "astra-desk.v1.yaml"
SCHEMA = REPO / "contracts" / "astra-desk.schema.json"


def test_committed_contract_validates_against_schema() -> None:
    contract = load_contract(CONTRACT, SCHEMA)
    assert contract.name == "astra-desk"
    assert contract.version == "1.0.0"
    assert contract.starting_equity == 500.0
    assert contract.currency == "USD"
    assert contract.gate.quorum_required == 5
    assert contract.gate.conviction_weights["666_skeptic"] == -1.0
    assert contract.gate.conviction_weights["067_reader"] == 1.5
    assert contract.kill_switch.reenable == "manual_human_only"
    assert contract.paper_trading_period_days == 21
    assert contract.raw["rollout"]["phase_1"]["duration_days"] == 21
    assert contract.human_checkpoint_trades == 25
    assert contract.min_sample_live_setups == 200
    assert contract.calibration.eviction_bind_sample == 200
    assert contract.raw["rollout"]["phase_2"]["trades"] == 25
    assert contract.raw["rollout"]["phase_2"]["mode"] == "live_with_human_checkpoint"
    assert contract.raw["rollout"]["phase_3"]["mode"] == "autonomous"
    assert contract.phase_3_requires == ("kill_switch_armed", "ledger_verified")
    assert contract.calibration.weights_update == "nightly_from_outcomes"
    assert contract.calibration.correlation_monitor is True
    assert "rolling 50" in contract.calibration.eviction_rule
    assert contract.raw["regime_classifier"]["agent_accuracy_tracked_per_regime"] is True
    assert set(contract.agent_ids()) == {
        "456_scout",
        "067_reader",
        "218_broker",
        "240_exit",
        "001_ledger",
        "666_skeptic",
        "risk_officer",
    }
    assert contract.regimes == (
        Regime.TRENDING,
        Regime.MEAN_REVERTING,
        Regime.HIGH_VOL,
        Regime.NEWS_EVENT,
    )


def test_schema_rejects_wrong_api_version() -> None:
    data = copy.deepcopy(load_yaml(CONTRACT))
    schema = load_schema(SCHEMA)
    data["apiVersion"] = "desk/v0"
    with pytest.raises(ContractError, match="schema validation"):
        validate_contract_data(data, schema)


def test_schema_rejects_missing_gate() -> None:
    data = copy.deepcopy(load_yaml(CONTRACT))
    schema = load_schema(SCHEMA)
    del data["gate"]
    with pytest.raises(ContractError, match="schema validation"):
        validate_contract_data(data, schema)


def test_duplicate_agent_ids_fail_closed() -> None:
    data = copy.deepcopy(load_yaml(CONTRACT))
    schema = load_schema(SCHEMA)
    data["agents"][1]["id"] = data["agents"][0]["id"]
    with pytest.raises(ContractError, match="duplicate agent ids"):
        validate_contract_data(data, schema)
