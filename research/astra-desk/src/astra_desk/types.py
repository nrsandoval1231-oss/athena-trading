"""Shared types for the Phase 0 desk. No live broker types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from astra_desk.features import SetupFeatures


class Regime(str, Enum):
    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"
    HIGH_VOL = "high_vol"
    NEWS_EVENT = "news_event"


class DeskMode(str, Enum):
    WALK_FORWARD_REPLAY = "walk_forward_replay"
    PAPER_TRADING = "paper_trading"
    # Checkpointed paper deploys. Not a live broker. live_trading_authority stays false.
    LIVE_WITH_HUMAN_CHECKPOINT = "live_with_human_checkpoint"
    # Autonomous *paper* loop. Not a live broker. live_trading_authority stays false.
    AUTONOMOUS = "autonomous"

    @property
    def is_live(self) -> bool:
        """True when a live broker would be required. No implemented mode is live."""
        return False

    @property
    def is_autonomous(self) -> bool:
        return self is DeskMode.AUTONOMOUS

    @property
    def requires_human_checkpoint(self) -> bool:
        return self is DeskMode.LIVE_WITH_HUMAN_CHECKPOINT


LIVE_MODES = frozenset()
CHECKPOINT_MODES = frozenset({DeskMode.LIVE_WITH_HUMAN_CHECKPOINT})
AUTONOMOUS_MODES = frozenset({DeskMode.AUTONOMOUS})
PAPER_MODES = frozenset({DeskMode.WALK_FORWARD_REPLAY, DeskMode.PAPER_TRADING})


class RoundDecision(str, Enum):
    DEPLOY = "deploy"
    REFUSE = "refuse"
    VETO = "veto"
    HALT = "halt"


@dataclass(frozen=True)
class ScoutOutput:
    conviction: float
    rationale: str


@dataclass(frozen=True)
class ReaderOutput:
    conviction: float
    rationale: str
    p_organic: float


@dataclass(frozen=True)
class BrokerOutput:
    size_units: float
    kelly_fraction: float


@dataclass(frozen=True)
class ExitOutput:
    stop_price: float
    take_profit_levels: tuple[float, ...]
    # Gate-facing invalidation quality in [0, 1]. Not a contract output field;
    # the weighted gate still requires a conviction for agent 240_exit.
    conviction: float


@dataclass(frozen=True)
class LedgerAgentOutput:
    agent_accuracy: Mapping[str, float]
    regime_stats: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class SkepticOutput:
    counterarguments: tuple[str, ...]
    failure_scenarios: tuple[str, ...]
    strongest_opposing_case: str
    # Opposition strength in [0, 1]; gate weight is negative.
    conviction: float


@dataclass(frozen=True)
class RiskOfficerOutput:
    approved: bool
    exposure_after: float
    veto_reason: str | None = None


@dataclass(frozen=True)
class Position:
    symbol: str
    notional_usd: float
    correlation_group: str
    side: str = "long"


@dataclass(frozen=True)
class AccountingSnapshot:
    currency: str
    starting_equity: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    max_drawdown_pct: float
    exposure_pct: float
    daily_loss_pct: float
    sharpe: float | None
    sortino: float | None
    win_rate: float | None
    expectancy_per_setup: float | None
    open_positions: tuple[Position, ...]
    closed_trade_count: int

    def ledger_fields(self) -> dict[str, float | None]:
        return {
            "equity": self.equity,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "max_drawdown_pct": self.max_drawdown_pct,
            "exposure_pct": self.exposure_pct,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "win_rate": self.win_rate,
            "expectancy_per_setup": self.expectancy_per_setup,
        }


@dataclass(frozen=True)
class ConvictionBand:
    min: float
    size_multiplier: float


@dataclass(frozen=True)
class GateDecision:
    deployed: bool
    total_weighted_conviction: float
    size_multiplier: float
    ev_estimate: float
    quorum_met: bool
    quorum_have: int
    quorum_required: int
    reason: str
    inputs: Mapping[str, Any]


@dataclass(frozen=True)
class FrontManLog:
    decision: str
    gate_inputs: Mapping[str, Any]
    rationale_hash: str


@dataclass(frozen=True)
class BrokerOrder:
    """Paper-broker order snapshot. Never a live/LIVE authority object."""

    order_id: str
    symbol: str
    side: str
    notional_usd: float
    status: str
    broker: str
    client_order_id: str | None = None
    filled_qty: float = 0.0
    paper: bool = True
    live_trading_authority: bool = False


@dataclass(frozen=True)
class KillSwitchState:
    halted: bool
    reason: str | None
    action: str | None
    reenable: str = "manual_human_only"
    armed: bool = True


@dataclass(frozen=True)
class RoundFixture:
    """One setup. Rule agents read `features`; stubs may read conviction fields."""

    setup_id: str
    symbol: str
    regime: Regime
    ev_estimate: float
    price: float
    features: SetupFeatures | None = None
    broker_size_units: float = 40.0
    broker_kelly_fraction: float = 0.20
    correlation_group: str = "default"
    side: str = "long"
    resolved_correct_by_agent: Mapping[str, bool] = field(default_factory=dict)
    # Explicit stub doubles only. Default path ignores these.
    scout_conviction: float | None = None
    scout_rationale: str | None = None
    reader_conviction: float | None = None
    reader_rationale: str | None = None
    reader_p_organic: float | None = None
    exit_stop_price: float | None = None
    exit_take_profit_levels: tuple[float, ...] = ()
    exit_conviction: float | None = None
    skeptic_counterarguments: tuple[str, ...] = ()
    skeptic_failure_scenarios: tuple[str, ...] = ()
    skeptic_strongest_opposing_case: str = ""
    skeptic_conviction: float | None = None


@dataclass(frozen=True)
class Bar:
    """One replay bar. Fixture data only — no live feed."""

    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class WalkSettlement:
    realized_pnl: float
    exit_price: float | None
    reason: str


@dataclass(frozen=True)
class ExitCriteriaResult:
    met: bool
    rule: str
    expectancy_per_setup: float | None
    max_drawdown_pct: float
    closed_trade_count: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class HumanApproval:
    """Structured human approval. A boolean flag is not sufficient."""

    approved_by: str
    approved_at: str
    setup_id: str
    rationale_hash: str
    decision: str
    record_hash: str


@dataclass(frozen=True)
class CheckpointOutcome:
    required: bool
    approved: bool
    reason: str | None
    proposed_rationale_hash: str | None
    approval_record_hash: str | None = None


@dataclass(frozen=True)
class RoundResult:
    round_id: str
    setup_id: str
    mode: DeskMode
    regime: Regime
    decision: RoundDecision
    executed: bool
    final_size_usd: float
    gate: GateDecision
    risk: RiskOfficerOutput | None
    kill_switch: KillSwitchState
    front_man: FrontManLog | None
    accounting: AccountingSnapshot
    ledger_entry_hash: str
    agent_outputs: Mapping[str, Any]
    live_trading_authority: bool = False
    checkpoint: CheckpointOutcome | None = None
    broker_order: BrokerOrder | None = None


@dataclass(frozen=True)
class WalkForwardReport:
    mode: DeskMode
    fixture_path: str | None
    setups_total: int
    setups_processed: int
    halted: bool
    halt_reason: str | None
    rounds: tuple[RoundResult, ...]
    settlements: tuple[WalkSettlement | None, ...]
    accounting: AccountingSnapshot
    exit_criteria: ExitCriteriaResult
    ledger_entry_count: int
    live_trading_authority: bool = False


@dataclass(frozen=True)
class PaperSessionRound:
    """One setup in the operator paper session. Compact, not a second gate."""

    round_id: str
    setup_id: str
    decision: str
    executed: bool
    final_size_usd: float
    broker: str | None
    broker_status: str | None
    settlement: str | None
    realized_pnl: float | None


@dataclass(frozen=True)
class PaperSessionReport:
    """End-to-end paper session: tape → agents → gate → risk → broker → ledger."""

    kind: str
    tape_source: str
    broker: str
    mode: DeskMode
    fixture_path: str | None
    setups_total: int
    setups_processed: int
    deploys: int
    refuses: int
    vetoes: int
    halts: int
    fills: int
    halted: bool
    halt_reason: str | None
    rounds: tuple[PaperSessionRound, ...]
    accounting: AccountingSnapshot
    ledger_entry_count: int
    ledger_verified: bool
    live_trading_authority: bool = False
    reenable: str = "manual_human_only"
    kill_switch_action: str = "flatten_all_and_halt"
    store_path: str | None = None
    persisted: bool = False


@dataclass(frozen=True)
class Phase1Progress:
    days_completed: int
    days_required: int
    duration_complete: bool
    halted: bool
    halt_reason: str | None
    session_dates: tuple[str, ...]


@dataclass(frozen=True)
class Phase2Progress:
    trades_completed: int
    trades_required: int
    trade_count_complete: bool
    halted: bool
    halt_reason: str | None


@dataclass(frozen=True)
class Phase3Requirements:
    kill_switch_armed: bool
    ledger_verified: bool
    met: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AutonomousReport:
    mode: DeskMode
    fixture_path: str | None
    rounds_requested: int
    rounds_processed: int
    halted: bool
    halt_reason: str | None
    requirements: Phase3Requirements
    accounting: AccountingSnapshot
    ledger_entry_count: int
    live_trading_authority: bool = False
    reenable: str = "manual_human_only"


@dataclass(frozen=True)
class HumanCheckpointReport:
    mode: DeskMode
    fixture_path: str | None
    trades_required: int
    trades_completed: int
    trade_count_complete: bool
    halted: bool
    halt_reason: str | None
    setups_total: int
    setups_processed: int
    accounting: AccountingSnapshot
    ledger_entry_count: int
    live_trading_authority: bool = False
    reenable: str = "manual_human_only"


@dataclass(frozen=True)
class PaperPeriodReport:
    mode: DeskMode
    fixture_path: str | None
    days_required: int
    days_completed: int
    duration_complete: bool
    halted: bool
    halt_reason: str | None
    session_dates: tuple[str, ...]
    days_processed: int
    accounting: AccountingSnapshot
    phase_0_exit_criteria: ExitCriteriaResult
    ledger_entry_count: int
    live_trading_authority: bool = False
