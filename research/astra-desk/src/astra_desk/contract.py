"""Load and validate TradingDeskContract YAML against the committed JSON Schema."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import jsonschema
import yaml

from astra_desk.errors import ContractError
from astra_desk.types import ConvictionBand, Regime

CONTRACT_YAML_NAME = "astra-desk.v1.yaml"
SCHEMA_JSON_NAME = "astra-desk.schema.json"

# Contract agent ids — keep in lockstep with contracts/astra-desk.v1.yaml.
SCOUT_ID = "456_scout"
READER_ID = "067_reader"
BROKER_ID = "218_broker"
EXIT_ID = "240_exit"
LEDGER_ID = "001_ledger"
SKEPTIC_ID = "666_skeptic"
RISK_ID = "risk_officer"

GATING_AGENT_IDS = (SCOUT_ID, READER_ID, EXIT_ID, SKEPTIC_ID)
PHASE3_REQUIRES = ("kill_switch_armed", "ledger_verified")
CALIBRATION_WEIGHTS_UPDATE = "nightly_from_outcomes"
CALIBRATION_EVICTION_RULE = "lowest per-regime-calibrated accuracy over rolling 50 setups"
CALIBRATION_ROLLING_SETUPS = 50


@dataclass(frozen=True)
class AgentSpec:
    id: str
    role: str
    mandate: str
    output: Mapping[str, Any]
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class HardLimits:
    max_total_exposure_pct: float
    max_correlated_positions: int
    max_single_position_pct: float
    daily_loss_limit_pct: float


@dataclass(frozen=True)
class BrokerConstraints:
    max_kelly_fraction: float
    never_full_kelly: bool


@dataclass(frozen=True)
class GateSpec:
    type: str
    quorum_required: int
    conviction_weights: Mapping[str, float]
    deploy_rule: str
    sizing_rule: str
    conviction_bands: tuple[ConvictionBand, ...]
    front_man_must_log: tuple[str, ...]


@dataclass(frozen=True)
class KillSwitchSpec:
    trigger: str
    action: str
    reenable: str
    drawdown_pct: float = 8.0


@dataclass(frozen=True)
class CalibrationSpec:
    eviction_rule: str
    weights_update: str
    correlation_monitor: bool
    rolling_window: int = CALIBRATION_ROLLING_SETUPS
    agent_accuracy_tracked_per_regime: bool = True
    eviction_bind_sample: int = 200


@dataclass(frozen=True)
class DeskContract:
    raw: Mapping[str, Any]
    path: Path
    schema_path: Path
    name: str
    version: str
    currency: str
    starting_equity: float
    ledger_fields: tuple[str, ...]
    agents: tuple[AgentSpec, ...]
    gate: GateSpec
    kill_switch: KillSwitchSpec
    hard_limits: HardLimits
    broker_constraints: BrokerConstraints
    regimes: tuple[Regime, ...]
    paper_trading_period_days: int
    human_checkpoint_trades: int
    min_sample_live_setups: int
    phase_3_requires: tuple[str, ...]
    calibration: CalibrationSpec

    def agent_ids(self) -> tuple[str, ...]:
        return tuple(a.id for a in self.agents)

    def agent(self, agent_id: str) -> AgentSpec:
        for spec in self.agents:
            if spec.id == agent_id:
                return spec
        raise ContractError(f"unknown agent id: {agent_id}")


def default_contracts_dir() -> Path:
    env = os.environ.get("ASTRA_DESK_CONTRACTS")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "contracts",
        Path.cwd() / "contracts",
    ]
    for candidate in candidates:
        if (candidate / CONTRACT_YAML_NAME).is_file():
            return candidate
    raise ContractError(
        "cannot locate contracts/astra-desk.v1.yaml; set ASTRA_DESK_CONTRACTS"
    )


def load_schema(schema_path: Path | None = None) -> dict[str, Any]:
    path = schema_path or default_contracts_dir() / SCHEMA_JSON_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"failed to read schema {path}: {exc}") from exc


def load_yaml(yaml_path: Path | None = None) -> dict[str, Any]:
    path = yaml_path or default_contracts_dir() / CONTRACT_YAML_NAME
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ContractError(f"failed to read contract {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError("contract YAML must be a mapping")
    return data


def validate_contract_data(data: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    try:
        jsonschema.Draft7Validator(schema).validate(data)
    except jsonschema.ValidationError as exc:
        raise ContractError(f"contract failed schema validation: {exc.message}") from exc
    _enforce_unique_agent_ids(data)


def _enforce_unique_agent_ids(data: Mapping[str, Any]) -> None:
    """uniqueItemProperties is not a draft-07 keyword; enforce ids in-process."""
    agents = data.get("agents") or []
    ids = [agent.get("id") for agent in agents]
    seen: set[str] = set()
    duplicates: list[str] = []
    for agent_id in ids:
        if not isinstance(agent_id, str):
            raise ContractError("every agent must have a string id")
        if agent_id in seen:
            duplicates.append(agent_id)
        seen.add(agent_id)
    if duplicates:
        raise ContractError(f"duplicate agent ids: {sorted(set(duplicates))}")


def _parse_contract(data: Mapping[str, Any], yaml_path: Path, schema_path: Path) -> DeskContract:
    metadata = data.get("metadata") or {}
    capital = data["capital"]
    agents_raw = data["agents"]
    gate_raw = data["gate"]
    ks_raw = data["kill_switch"]
    risk_raw = next(a for a in agents_raw if a["id"] == RISK_ID)
    broker_raw = next(a for a in agents_raw if a["id"] == BROKER_ID)
    limits = risk_raw["hard_limits"]
    constraints = broker_raw["constraints"]
    bands = tuple(
        ConvictionBand(min=float(b["min"]), size_multiplier=float(b["size_multiplier"]))
        for b in gate_raw["conviction_bands"]
    )
    weights = {str(k): float(v) for k, v in gate_raw["conviction_weights"].items()}
    agent_ids = {a["id"] for a in agents_raw}
    missing_weight_ids = sorted(set(weights) - agent_ids)
    if missing_weight_ids:
        raise ContractError(f"gate weights reference unknown agents: {missing_weight_ids}")
    regimes = tuple(Regime(r) for r in data["regime_classifier"]["regimes"])
    if ks_raw.get("reenable") != "manual_human_only":
        raise ContractError("kill_switch.reenable must be manual_human_only")
    if data["kill_switch"]["action"] != "flatten_all_and_halt":
        raise ContractError("kill_switch.action must be flatten_all_and_halt")
    eval_days = int(capital["evaluation"]["paper_trading_period_days"])
    min_sample_live = int(capital["evaluation"]["min_sample_live_setups"])
    if min_sample_live < CALIBRATION_ROLLING_SETUPS:
        raise ContractError(
            "capital.evaluation.min_sample_live_setups must be >= rolling 50"
        )
    phase1 = data["rollout"]["phase_1"]
    phase1_days = int(phase1["duration_days"])
    if eval_days != phase1_days:
        raise ContractError(
            "capital.evaluation.paper_trading_period_days must match "
            "rollout.phase_1.duration_days"
        )
    if phase1.get("mode") != "paper_trading":
        raise ContractError("rollout.phase_1.mode must be paper_trading")
    if eval_days < 1:
        raise ContractError("paper_trading_period_days must be >= 1")
    eval_trades = int(capital["evaluation"]["human_checkpoint_trades"])
    phase2 = data["rollout"]["phase_2"]
    phase2_trades = int(phase2["trades"])
    if eval_trades != phase2_trades:
        raise ContractError(
            "capital.evaluation.human_checkpoint_trades must match "
            "rollout.phase_2.trades"
        )
    if phase2.get("mode") != "live_with_human_checkpoint":
        raise ContractError("rollout.phase_2.mode must be live_with_human_checkpoint")
    if eval_trades < 1:
        raise ContractError("human_checkpoint_trades must be >= 1")
    phase3 = data["rollout"]["phase_3"]
    if phase3.get("mode") != "autonomous":
        raise ContractError("rollout.phase_3.mode must be autonomous")
    requires = tuple(str(item) for item in (phase3.get("requires") or ()))
    if requires != PHASE3_REQUIRES:
        raise ContractError(
            "rollout.phase_3.requires must be [kill_switch_armed, ledger_verified]"
        )
    if data["regime_classifier"].get("agent_accuracy_tracked_per_regime") is not True:
        raise ContractError("regime_classifier.agent_accuracy_tracked_per_regime must be true")
    cal_raw = data["calibration"]
    eviction_rule = str(cal_raw.get("eviction_rule") or "")
    if eviction_rule != CALIBRATION_EVICTION_RULE:
        raise ContractError(
            "calibration.eviction_rule must be "
            "'lowest per-regime-calibrated accuracy over rolling 50 setups'"
        )
    if cal_raw.get("weights_update") != CALIBRATION_WEIGHTS_UPDATE:
        raise ContractError("calibration.weights_update must be nightly_from_outcomes")
    if cal_raw.get("correlation_monitor") is not True:
        raise ContractError("calibration.correlation_monitor must be true")
    calibration = CalibrationSpec(
        eviction_rule=eviction_rule,
        weights_update=CALIBRATION_WEIGHTS_UPDATE,
        correlation_monitor=True,
        rolling_window=CALIBRATION_ROLLING_SETUPS,
        agent_accuracy_tracked_per_regime=True,
        eviction_bind_sample=min_sample_live,
    )
    return DeskContract(
        raw=data,
        path=yaml_path,
        schema_path=schema_path,
        name=str(metadata.get("name", "astra-desk")),
        version=str(metadata.get("version", "0")),
        currency=str(capital["currency"]),
        starting_equity=float(capital["starting_equity"]),
        ledger_fields=tuple(capital["accounting_policy"]["ledger_fields"]),
        agents=tuple(
            AgentSpec(
                id=a["id"],
                role=a["role"],
                mandate=a["mandate"],
                output=a["output"],
                raw=a,
            )
            for a in agents_raw
        ),
        gate=GateSpec(
            type=str(gate_raw["type"]),
            quorum_required=int(gate_raw["quorum_required"]),
            conviction_weights=weights,
            deploy_rule=str(gate_raw["deploy_rule"]),
            sizing_rule=str(gate_raw["sizing_rule"]),
            conviction_bands=bands,
            front_man_must_log=tuple(gate_raw["front_man"]["must_log"]),
        ),
        kill_switch=KillSwitchSpec(
            trigger=str(ks_raw["trigger"]),
            action=str(ks_raw["action"]),
            reenable=str(ks_raw["reenable"]),
            drawdown_pct=8.0,
        ),
        hard_limits=HardLimits(
            max_total_exposure_pct=float(limits["max_total_exposure_pct"]),
            max_correlated_positions=int(limits["max_correlated_positions"]),
            max_single_position_pct=float(limits["max_single_position_pct"]),
            daily_loss_limit_pct=float(limits["daily_loss_limit_pct"]),
        ),
        broker_constraints=BrokerConstraints(
            max_kelly_fraction=float(constraints["max_kelly_fraction"]),
            never_full_kelly=bool(constraints["never_full_kelly"]),
        ),
        regimes=regimes,
        paper_trading_period_days=eval_days,
        human_checkpoint_trades=eval_trades,
        min_sample_live_setups=min_sample_live,
        phase_3_requires=requires,
        calibration=calibration,
    )


def load_contract(
    yaml_path: Path | None = None,
    schema_path: Path | None = None,
) -> DeskContract:
    contracts_dir = default_contracts_dir() if yaml_path is None else yaml_path.parent
    yaml_file = yaml_path or contracts_dir / CONTRACT_YAML_NAME
    schema_file = schema_path or contracts_dir / SCHEMA_JSON_NAME
    data = load_yaml(yaml_file)
    schema = load_schema(schema_file)
    validate_contract_data(data, schema)
    return _parse_contract(data, yaml_file, schema_file)
