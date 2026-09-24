"""Orchestrate one paper/walk-forward round. No live broker."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any
from collections.abc import Mapping

from astra_desk.accounting import CapitalBooks
from astra_desk.agents.base import AgentContext
from astra_desk.agents import build_default_agents, build_stub_agents
from astra_desk.broker import PaperBroker, build_paper_broker
from astra_desk.calibration import CalibrationEngine
from astra_desk.contract import (
    BROKER_ID,
    EXIT_ID,
    GATING_AGENT_IDS,
    LEDGER_ID,
    READER_ID,
    RISK_ID,
    SCOUT_ID,
    SKEPTIC_ID,
    DeskContract,
    load_contract,
)
from astra_desk.errors import (
    AutonomousError,
    BrokerError,
    CalibrationError,
    CheckpointError,
    LiveTradingDisabled,
    WalkForwardError,
)
from astra_desk.front_man import FrontMan
from astra_desk.gate import evaluate_gate
from astra_desk.kill_switch import KillSwitch
from astra_desk.ledger import AccuracyTracker, AppendOnlyLedger, canonical_hash
from astra_desk.regime import RegimeClassifier
from astra_desk.types import (
    BrokerOrder,
    BrokerOutput,
    CheckpointOutcome,
    DeskMode,
    ExitOutput,
    GateDecision,
    HumanApproval,
    Position,
    ReaderOutput,
    RoundDecision,
    RoundFixture,
    RoundResult,
    ScoutOutput,
    SkepticOutput,
)

PRE_GATE_IDS = (SCOUT_ID, READER_ID, BROKER_ID, EXIT_ID, LEDGER_ID, SKEPTIC_ID)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


class TradingDesk:
    def __init__(
        self,
        contract: DeskContract | None = None,
        mode: DeskMode = DeskMode.WALK_FORWARD_REPLAY,
        *,
        use_stubs: bool = False,
        agents: dict[str, object] | None = None,
        live_broker: bool = False,
        broker: PaperBroker | None = None,
        broker_kind: str = "fixture",
    ) -> None:
        if live_broker:
            raise LiveTradingDisabled(
                "live broker is not implemented; live_trading_authority stays false"
            )
        self.contract = contract or load_contract()
        self.mode = mode
        self.books = CapitalBooks(
            starting_equity=self.contract.starting_equity,
            currency=self.contract.currency,
        )
        self.ledger = AppendOnlyLedger()
        self.accuracy = AccuracyTracker()
        self.kill_switch = KillSwitch(
            drawdown_pct=self.contract.kill_switch.drawdown_pct,
            daily_loss_limit_pct=self.contract.hard_limits.daily_loss_limit_pct,
        )
        self.front_man = FrontMan()
        self.regime_classifier = RegimeClassifier(self.contract.regimes)
        self.calibration = CalibrationEngine()
        self.conviction_weights = dict(self.contract.gate.conviction_weights)
        self.disabled_seats: dict[str, str] = {}
        self._calibration_stack: list[dict[str, Any]] = []
        self.broker = build_paper_broker(broker_kind, broker=broker)
        if agents is not None:
            self.agents = agents
        elif use_stubs:
            self.agents = build_stub_agents(self.contract)
        else:
            self.agents = build_default_agents(self.contract)
        self._round_seq = 0
        self._spent_approvals: set[str] = set()

    def reenable(self, *, human_confirmed: bool) -> None:
        self.kill_switch.reenable(human_confirmed=human_confirmed)

    def effective_weights(self) -> dict[str, float]:
        """Working gate weights with disabled seats zeroed. Contract baseline is unchanged."""
        weights = dict(self.conviction_weights)
        for agent_id in self.disabled_seats:
            if agent_id in weights:
                weights[agent_id] = 0.0
        return weights

    def push_weights(self, weights: Mapping[str, float], *, source: str) -> dict[str, float]:
        """Replace working conviction weights. Logged by the caller; reversible via revert_weights."""
        proposed = {str(k): float(v) for k, v in weights.items()}
        if proposed.get(SKEPTIC_ID, -1.0) > 0:
            raise CalibrationError("skeptic_weight_must_stay_negative")
        self._calibration_stack.append(
            {
                "weights": dict(self.conviction_weights),
                "disabled_seats": dict(self.disabled_seats),
                "source": source,
            }
        )
        self.conviction_weights = proposed
        return dict(self.conviction_weights)

    def revert_weights(self) -> dict[str, float]:
        if not self._calibration_stack:
            raise CalibrationError("no_calibration_snapshot_to_revert")
        snap = self._calibration_stack.pop()
        self.conviction_weights = dict(snap["weights"])
        self.disabled_seats = dict(snap["disabled_seats"])
        return dict(self.conviction_weights)

    def disable_seat(self, agent_id: str, *, reason: str) -> None:
        """Mark a gating seat disabled. Does not delete ledger history or unload the agent."""
        self.disabled_seats[str(agent_id)] = reason

    def _require_phase3(self) -> None:
        """Fail closed unless kill_switch_armed and ledger_verified."""
        spec = self.contract.kill_switch
        if spec.action != "flatten_all_and_halt":
            raise AutonomousError("kill_switch action must be flatten_all_and_halt")
        if spec.reenable != "manual_human_only":
            raise AutonomousError("kill_switch reenable must be manual_human_only")
        if tuple(self.contract.phase_3_requires) != ("kill_switch_armed", "ledger_verified"):
            raise AutonomousError("contract phase_3 requires drifted")
        armed, reason = self.kill_switch.armed_status()
        if not armed:
            raise AutonomousError(f"phase_3 refused: {reason}")
        verified, reason = self.ledger.verify()
        if not verified:
            raise AutonomousError(f"phase_3 refused: ledger_integrity_failed:{reason}")

    def settle_paper(
        self,
        *,
        round_id: str,
        realized_pnl: float,
        detail: Mapping[str, Any],
    ) -> None:
        """Close the open paper position and append a settlement to the same ledger."""
        if not self.books.positions:
            raise WalkForwardError(f"no open paper position to settle for {round_id}")
        self.books.close_all_positions(realized_pnl)
        self.ledger.append(
            f"{round_id}-settle",
            {
                "kind": "paper_settlement",
                "round_id": round_id,
                "realized_pnl": realized_pnl,
                "detail": dict(detail),
                "accounting": self.books.snapshot().ledger_fields(),
                "live_trading_authority": False,
            },
        )
        self.kill_switch.poll(self.books, broker=self.broker)

    def proposal_hash(self, fixture: RoundFixture) -> str:
        """SHA-256 of the would-be deploy rationale. Does not execute a fill."""
        assessed = self._assess(fixture)
        return str(assessed["rationale_hash"])

    def _assess(self, fixture: RoundFixture) -> dict[str, Any]:
        regime = self.regime_classifier.classify(fixture)
        ks = self.kill_switch.poll(self.books, broker=self.broker)
        if ks.halted:
            gate = GateDecision(
                deployed=False,
                total_weighted_conviction=0.0,
                size_multiplier=0.0,
                ev_estimate=fixture.ev_estimate,
                quorum_met=False,
                quorum_have=0,
                quorum_required=self.contract.gate.quorum_required,
                reason="kill_switch_halted",
                inputs={"halt_reason": ks.reason},
            )
            payload = {"reason": ks.reason, "setup_id": fixture.setup_id}
            return {
                "halted": True,
                "regime": regime,
                "gate": gate,
                "risk": None,
                "outputs": {"halt": ks.reason},
                "decision": RoundDecision.HALT,
                "final_size": 0.0,
                "rationale_payload": payload,
                "rationale_hash": canonical_hash(payload),
            }

        ctx = AgentContext(
            regime=regime,
            fixture=fixture,
            books=self.books,
            contract=self.contract,
            accuracy=self.accuracy,
        )
        outputs: dict[str, Any] = {}
        for agent_id in PRE_GATE_IDS:
            outputs[agent_id] = self.agents[agent_id].evaluate(ctx)

        scout: ScoutOutput = outputs[SCOUT_ID]
        reader: ReaderOutput = outputs[READER_ID]
        broker: BrokerOutput = outputs[BROKER_ID]
        exit_out: ExitOutput = outputs[EXIT_ID]
        skeptic: SkepticOutput = outputs[SKEPTIC_ID]

        convictions = {
            SCOUT_ID: scout.conviction,
            READER_ID: reader.conviction,
            EXIT_ID: exit_out.conviction,
            SKEPTIC_ID: skeptic.conviction,
        }
        gate = evaluate_gate(
            convictions=convictions,
            weights=self.effective_weights(),
            bands=self.contract.gate.conviction_bands,
            quorum_have=len(outputs),
            quorum_required=self.contract.gate.quorum_required,
            ev_estimate=fixture.ev_estimate,
        )

        rationale_payload = {
            "setup_id": fixture.setup_id,
            "regime": regime.value,
            "scout_rationale": scout.rationale,
            "reader_rationale": reader.rationale,
            "skeptic": skeptic.strongest_opposing_case,
            "exit": {"stop_price": exit_out.stop_price, "tps": exit_out.take_profit_levels},
            "ev_estimate": fixture.ev_estimate,
            "total_weighted_conviction": gate.total_weighted_conviction,
            "gating_agents": list(GATING_AGENT_IDS),
        }

        proposed = broker.size_units * gate.size_multiplier if gate.deployed else 0.0
        risk = None
        if gate.deployed:
            risk_ctx = AgentContext(
                regime=regime,
                fixture=fixture,
                books=self.books,
                contract=self.contract,
                accuracy=self.accuracy,
                proposed_notional=proposed,
            )
            risk = self.agents[RISK_ID].evaluate(risk_ctx)
            outputs[RISK_ID] = risk

        decision = RoundDecision.REFUSE
        final_size = 0.0
        if not gate.deployed:
            decision = RoundDecision.REFUSE
        elif risk is None or not risk.approved:
            decision = RoundDecision.VETO
        else:
            decision = RoundDecision.DEPLOY
            final_size = proposed

        return {
            "halted": False,
            "regime": regime,
            "gate": gate,
            "risk": risk,
            "outputs": outputs,
            "decision": decision,
            "final_size": final_size,
            "rationale_payload": rationale_payload,
            "rationale_hash": canonical_hash(rationale_payload),
        }

    def _apply_checkpoint(
        self,
        *,
        fixture: RoundFixture,
        decision: RoundDecision,
        final_size: float,
        rationale_payload: Mapping[str, Any],
        rationale_hash: str,
        approval: HumanApproval | None,
    ) -> tuple[RoundDecision, float, dict[str, Any], CheckpointOutcome]:
        if not self.mode.requires_human_checkpoint:
            return decision, final_size, dict(rationale_payload), CheckpointOutcome(
                required=False,
                approved=False,
                reason="checkpoint_not_required",
                proposed_rationale_hash=rationale_hash,
            )
        if decision is not RoundDecision.DEPLOY:
            return decision, final_size, dict(rationale_payload), CheckpointOutcome(
                required=True,
                approved=False,
                reason="not_a_deploy",
                proposed_rationale_hash=rationale_hash,
            )
        ok, reason = validate_approval(
            approval,
            setup_id=fixture.setup_id,
            rationale_hash=rationale_hash,
            spent=self._spent_approvals,
        )
        if ok and approval is not None:
            self._spent_approvals.add(approval.record_hash)
            return decision, final_size, dict(rationale_payload), CheckpointOutcome(
                required=True,
                approved=True,
                reason="approved",
                proposed_rationale_hash=rationale_hash,
                approval_record_hash=approval.record_hash,
            )
        refused_payload = {
            **dict(rationale_payload),
            "checkpoint_refused": True,
            "checkpoint_reason": reason,
            "proposed_rationale_hash": rationale_hash,
        }
        return (
            RoundDecision.REFUSE,
            0.0,
            refused_payload,
            CheckpointOutcome(
                required=True,
                approved=False,
                reason=reason,
                proposed_rationale_hash=rationale_hash,
                approval_record_hash=approval.record_hash if approval is not None else None,
            ),
        )

    def run_round(
        self,
        fixture: RoundFixture,
        *,
        approval: HumanApproval | None = None,
    ) -> RoundResult:
        if self.mode.is_autonomous and not self.kill_switch.halted:
            self._require_phase3()
        self._round_seq += 1
        round_id = f"r{self._round_seq:04d}-{fixture.setup_id}"
        assessed = self._assess(fixture)
        regime = assessed["regime"]
        gate = assessed["gate"]
        risk = assessed["risk"]
        outputs = assessed["outputs"]
        decision = assessed["decision"]
        final_size = assessed["final_size"]
        rationale_payload = dict(assessed["rationale_payload"])
        rationale_hash = str(assessed["rationale_hash"])

        if assessed["halted"]:
            log, _ = self.front_man.execute(
                decision=RoundDecision.HALT,
                gate_inputs=gate.inputs,
                rationale_payload=rationale_payload,
                size_usd=0.0,
            )
            return self._finalize(
                round_id=round_id,
                fixture=fixture,
                regime=regime,
                decision=RoundDecision.HALT,
                executed=False,
                final_size=0.0,
                gate=gate,
                risk=None,
                front_man=log,
                agent_outputs=outputs,
                checkpoint=None,
            )

        checkpoint: CheckpointOutcome | None = None
        if self.mode.requires_human_checkpoint:
            decision, final_size, rationale_payload, checkpoint = self._apply_checkpoint(
                fixture=fixture,
                decision=decision,
                final_size=final_size,
                rationale_payload=rationale_payload,
                rationale_hash=rationale_hash,
                approval=approval,
            )
        elif approval is not None:
            raise CheckpointError(
                "human approval records are only valid in live_with_human_checkpoint"
            )

        log, executed_size = self.front_man.execute(
            decision=decision,
            gate_inputs=gate.inputs,
            rationale_payload=rationale_payload,
            size_usd=final_size,
        )
        executed = decision is RoundDecision.DEPLOY and executed_size > 0
        broker_order: BrokerOrder | None = None
        if executed:
            broker_order = self._submit_paper_order(
                fixture=fixture,
                size_usd=executed_size,
                client_order_id=round_id,
            )
            if broker_order.status in {"canceled", "rejected", "expired"}:
                executed = False
                final_size = 0.0
            else:
                self.books.open_position(
                    Position(
                        symbol=fixture.symbol,
                        notional_usd=executed_size,
                        correlation_group=fixture.correlation_group,
                        side=fixture.side,
                    )
                )
                final_size = executed_size

        ks = self.kill_switch.poll(self.books, broker=self.broker)
        if ks.halted and executed:
            decision = RoundDecision.HALT
            executed = False
            final_size = 0.0
            if broker_order is not None:
                try:
                    broker_order = self.broker.track(broker_order.order_id)
                except BrokerError:
                    pass

        return self._finalize(
            round_id=round_id,
            fixture=fixture,
            regime=regime,
            decision=decision,
            executed=executed,
            final_size=final_size,
            gate=gate,
            risk=risk,
            front_man=log,
            agent_outputs=outputs,
            checkpoint=checkpoint,
            broker_order=broker_order,
        )

    def _submit_paper_order(
        self,
        *,
        fixture: RoundFixture,
        size_usd: float,
        client_order_id: str,
    ) -> BrokerOrder:
        submitted = self.broker.submit(
            symbol=fixture.symbol,
            side=fixture.side,
            notional_usd=size_usd,
            client_order_id=client_order_id,
        )
        return self.broker.track(submitted.order_id)

    def _finalize(
        self,
        *,
        round_id: str,
        fixture: RoundFixture,
        regime,
        decision: RoundDecision,
        executed: bool,
        final_size: float,
        gate,
        risk,
        front_man,
        agent_outputs: Mapping[str, Any],
        checkpoint: CheckpointOutcome | None = None,
        broker_order: BrokerOrder | None = None,
    ) -> RoundResult:
        snap = self.books.snapshot()
        payload = {
            "round_id": round_id,
            "setup_id": fixture.setup_id,
            "mode": self.mode.value,
            "regime": regime.value,
            "decision": decision.value,
            "executed": executed,
            "final_size_usd": final_size,
            "gate": _jsonable(gate),
            "risk": _jsonable(risk),
            "kill_switch": _jsonable(self.kill_switch.state()),
            "front_man": _jsonable(front_man),
            "broker_order": _jsonable(broker_order),
            "broker": getattr(self.broker, "name", "fixture"),
            "accounting": snap.ledger_fields(),
            "agent_outputs": _jsonable(dict(agent_outputs)),
            "checkpoint": _jsonable(checkpoint),
            "live_trading_authority": False,
        }
        if fixture.resolved_correct_by_agent:
            payload["outcomes"] = {
                str(agent_id): bool(flag)
                for agent_id, flag in fixture.resolved_correct_by_agent.items()
            }
            self.accuracy.record_setup(
                fixture.setup_id, regime, fixture.resolved_correct_by_agent
            )
        entry = self.ledger.append(round_id, payload)
        return RoundResult(
            round_id=round_id,
            setup_id=fixture.setup_id,
            mode=self.mode,
            regime=regime,
            decision=decision,
            executed=executed,
            final_size_usd=final_size,
            gate=gate,
            risk=risk,
            kill_switch=self.kill_switch.state(),
            front_man=front_man,
            accounting=snap,
            ledger_entry_hash=entry.entry_hash,
            agent_outputs=dict(agent_outputs),
            live_trading_authority=False,
            checkpoint=checkpoint,
            broker_order=broker_order,
        )


def validate_approval(
    approval: HumanApproval | None,
    *,
    setup_id: str,
    rationale_hash: str,
    spent: set[str],
) -> tuple[bool, str]:
    """Fail closed: missing, incomplete, mismatched, or replayed approvals refuse."""
    if approval is None:
        return False, "approval_missing"
    if approval.decision != "approve":
        return False, "approval_decision_not_approve"
    if not approval.approved_by.strip():
        return False, "approved_by_missing"
    if not approval.approved_at.strip():
        return False, "approved_at_missing"
    if approval.setup_id != setup_id:
        return False, "setup_id_mismatch"
    if approval.rationale_hash != rationale_hash:
        return False, "rationale_hash_mismatch"
    if approval.record_hash in spent:
        return False, "approval_replay"
    return True, "approved"
