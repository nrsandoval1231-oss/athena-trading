"""Deterministic stub agents that return the contract output shapes."""

from __future__ import annotations

from astra_desk.agents.base import AgentContext
from astra_desk.contract import (
    BROKER_ID,
    EXIT_ID,
    LEDGER_ID,
    READER_ID,
    RISK_ID,
    SCOUT_ID,
    SKEPTIC_ID,
    DeskContract,
)
from astra_desk.errors import AgentFeatureError
from astra_desk.risk import evaluate_hard_limits
from astra_desk.types import (
    BrokerOutput,
    ExitOutput,
    LedgerAgentOutput,
    ReaderOutput,
    RiskOfficerOutput,
    ScoutOutput,
    SkepticOutput,
)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class ScoutAgent:
    id = SCOUT_ID
    role = "volume_lead_detection"

    def evaluate(self, ctx: AgentContext) -> ScoutOutput:
        _ = ctx.regime
        if ctx.fixture.scout_conviction is None:
            raise AgentFeatureError("stub 456_scout requires scout_conviction")
        lo, hi = ctx.contract.agent(self.id).raw["conviction_range"]
        return ScoutOutput(
            conviction=_clamp(float(ctx.fixture.scout_conviction), float(lo), float(hi)),
            rationale=ctx.fixture.scout_rationale or "stub scout",
        )


class ReaderAgent:
    id = READER_ID
    role = "demand_authenticity"

    def evaluate(self, ctx: AgentContext) -> ReaderOutput:
        _ = ctx.regime
        if ctx.fixture.reader_conviction is None or ctx.fixture.reader_p_organic is None:
            raise AgentFeatureError("stub 067_reader requires conviction and p_organic")
        return ReaderOutput(
            conviction=float(ctx.fixture.reader_conviction),
            rationale=ctx.fixture.reader_rationale or "stub reader",
            p_organic=float(ctx.fixture.reader_p_organic),
        )


class BrokerAgent:
    id = BROKER_ID
    role = "position_sizing"

    def evaluate(self, ctx: AgentContext) -> BrokerOutput:
        _ = ctx.regime
        constraints = ctx.contract.broker_constraints
        kelly = float(ctx.fixture.broker_kelly_fraction)
        if constraints.never_full_kelly and kelly >= 1.0:
            kelly = constraints.max_kelly_fraction
        kelly = min(kelly, constraints.max_kelly_fraction)
        if kelly < 0:
            kelly = 0.0
        return BrokerOutput(
            size_units=float(ctx.fixture.broker_size_units),
            kelly_fraction=kelly,
        )


class ExitAgent:
    id = EXIT_ID
    role = "invalidation"

    def evaluate(self, ctx: AgentContext) -> ExitOutput:
        _ = ctx.regime
        if ctx.fixture.exit_stop_price is None or ctx.fixture.exit_conviction is None:
            raise AgentFeatureError("stub 240_exit requires stop_price and conviction")
        return ExitOutput(
            stop_price=float(ctx.fixture.exit_stop_price),
            take_profit_levels=tuple(float(x) for x in ctx.fixture.exit_take_profit_levels),
            conviction=_clamp(float(ctx.fixture.exit_conviction), 0.0, 1.0),
        )


class LedgerMemoryAgent:
    id = LEDGER_ID
    role = "memory_outcomes"

    def evaluate(self, ctx: AgentContext) -> LedgerAgentOutput:
        _ = ctx.regime
        return LedgerAgentOutput(
            agent_accuracy=ctx.accuracy.agent_accuracy(),
            regime_stats=ctx.accuracy.regime_stats(),
        )


class SkepticAgent:
    id = SKEPTIC_ID
    role = "adversarial_debate"

    def evaluate(self, ctx: AgentContext) -> SkepticOutput:
        _ = ctx.regime
        if ctx.fixture.skeptic_conviction is None:
            raise AgentFeatureError("stub 666_skeptic requires skeptic_conviction")
        return SkepticOutput(
            counterarguments=tuple(ctx.fixture.skeptic_counterarguments),
            failure_scenarios=tuple(ctx.fixture.skeptic_failure_scenarios),
            strongest_opposing_case=ctx.fixture.skeptic_strongest_opposing_case
            or "stub skeptic",
            conviction=_clamp(float(ctx.fixture.skeptic_conviction), 0.0, 1.0),
        )


class RiskOfficerAgent:
    id = RISK_ID
    role = "portfolio_risk"

    def evaluate(self, ctx: AgentContext) -> RiskOfficerOutput:
        _ = ctx.regime
        proposed = 0.0 if ctx.proposed_notional is None else ctx.proposed_notional
        return evaluate_hard_limits(
            books=ctx.books,
            limits=ctx.contract.hard_limits,
            proposed_notional=proposed,
            correlation_group=ctx.fixture.correlation_group,
        )


def build_stub_agents(contract: DeskContract) -> dict[str, object]:
    agents = [
        ScoutAgent(),
        ReaderAgent(),
        BrokerAgent(),
        ExitAgent(),
        LedgerMemoryAgent(),
        SkepticAgent(),
        RiskOfficerAgent(),
    ]
    by_id = {a.id: a for a in agents}
    missing = sorted(set(contract.agent_ids()) - set(by_id))
    if missing:
        raise RuntimeError(f"stub agents missing contract ids: {missing}")
    return by_id
