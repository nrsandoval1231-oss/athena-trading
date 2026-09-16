"""Rule-based voting agents. Deterministic; no LLM, no network."""

from __future__ import annotations

from astra_desk.agents.base import AgentContext
from astra_desk.contract import EXIT_ID, READER_ID, SCOUT_ID, SKEPTIC_ID
from astra_desk.errors import AgentFeatureError
from astra_desk.features import SetupFeatures
from astra_desk.types import ExitOutput, ReaderOutput, ScoutOutput, SkepticOutput

SKEPTIC_FAILED_BREAKOUT_WEIGHT = 0.4
SKEPTIC_CROWDING_WEIGHT = 0.4
SKEPTIC_CROWDING_THRESHOLD = 1.5


def _clip01(value: float) -> float:
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return float(value)


def require_features(ctx: AgentContext) -> SetupFeatures:
    if ctx.regime is None:
        raise AgentFeatureError("market_regime is required")
    features = ctx.fixture.features
    if features is None:
        raise AgentFeatureError(
            f"{ctx.fixture.setup_id}: voting agents require setup features"
        )
    return features


def scout_conviction(features: SetupFeatures) -> float:
    vol_score = _clip01(features.volume_z / 2.0)
    lead_score = _clip01(features.volume_lead_bars / 2.0)
    lag_score = _clip01(1.0 - abs(features.price_change_pct) / 0.03)
    return _clip01(0.45 * vol_score + 0.35 * lead_score + 0.20 * lag_score)


def reader_scores(features: SetupFeatures) -> tuple[float, float]:
    p_organic = _clip01(
        0.30 * (1.0 - _clip01(features.cancel_rate))
        + 0.25 * _clip01(features.trade_to_quote_ratio / 0.4)
        + 0.25 * _clip01(features.unique_participant_z / 1.5)
        + 0.20 * (1.0 - _clip01(features.spread_widen_pct / 0.015))
    )
    return p_organic, p_organic


class VolumeLeadScout:
    id = SCOUT_ID
    role = "volume_lead_detection"

    def evaluate(self, ctx: AgentContext) -> ScoutOutput:
        features = require_features(ctx)
        lo, hi = ctx.contract.agent(self.id).raw["conviction_range"]
        conviction = min(float(hi), max(float(lo), scout_conviction(features)))
        rationale = (
            f"regime={ctx.regime.value} volume_z={features.volume_z:.3f} "
            f"lead_bars={features.volume_lead_bars:.2f} "
            f"price_change_pct={features.price_change_pct:.4f} "
            f"→ volume_precedes_price conviction={conviction:.3f}"
        )
        return ScoutOutput(conviction=conviction, rationale=rationale)


class DemandAuthenticityReader:
    id = READER_ID
    role = "demand_authenticity"

    def evaluate(self, ctx: AgentContext) -> ReaderOutput:
        features = require_features(ctx)
        p_organic, conviction = reader_scores(features)
        rationale = (
            f"regime={ctx.regime.value} cancel_rate={features.cancel_rate:.3f} "
            f"trade_to_quote={features.trade_to_quote_ratio:.3f} "
            f"unique_z={features.unique_participant_z:.3f} "
            f"spread_widen_pct={features.spread_widen_pct:.4f} "
            f"→ p_organic={p_organic:.3f}"
        )
        return ReaderOutput(
            conviction=conviction,
            rationale=rationale,
            p_organic=p_organic,
        )


class InvalidationExit:
    id = EXIT_ID
    role = "invalidation"

    def evaluate(self, ctx: AgentContext) -> ExitOutput:
        features = require_features(ctx)
        price = float(ctx.fixture.price)
        stop = float(features.invalidation_price)
        side = ctx.fixture.side
        if price <= 0:
            raise AgentFeatureError(f"{ctx.fixture.setup_id}: price must be > 0")
        if side == "long" and stop >= price:
            raise AgentFeatureError(
                f"{ctx.fixture.setup_id}: long invalidation_price must be below price"
            )
        if side == "short" and stop <= price:
            raise AgentFeatureError(
                f"{ctx.fixture.setup_id}: short invalidation_price must be above price"
            )
        risk = abs(price - stop)
        sign = 1.0 if side == "long" else -1.0
        take_profits = tuple(
            price + sign * multiple * risk
            for multiple in features.take_profit_r_multiples
        )
        distance_pct = risk / price * 100.0
        quality = _clip01(features.structure_quality)
        if distance_pct < 0.2 or distance_pct > 15.0:
            conviction = _clip01(quality * 0.3)
        else:
            conviction = quality
        return ExitOutput(
            stop_price=stop,
            take_profit_levels=take_profits,
            conviction=conviction,
        )


class AdversarialSkeptic:
    id = SKEPTIC_ID
    role = "adversarial_debate"

    def evaluate(self, ctx: AgentContext) -> SkepticOutput:
        features = require_features(ctx)
        flags: list[tuple[float, str, str, str]] = []
        if features.failed_breakout:
            flags.append(
                (
                    SKEPTIC_FAILED_BREAKOUT_WEIGHT,
                    "failed_breakout",
                    "A nearby failed breakout is the base rate; this print is the same shape.",
                    "Price reclaims the other side of the range and the stop is the next print.",
                )
            )
        if features.crowding_z >= SKEPTIC_CROWDING_THRESHOLD:
            flags.append(
                (
                    SKEPTIC_CROWDING_WEIGHT,
                    "crowding",
                    "Crowding_z is elevated; late capacity is likely already in the trade.",
                    "A crowded unwind gaps through invalidation before the thesis can adapt.",
                )
            )
        conviction = _clip01(sum(weight for weight, _name, _c, _f in flags))
        if flags:
            flags_sorted = sorted(flags, key=lambda item: item[0], reverse=True)
            counterarguments = tuple(item[2] for item in flags_sorted)
            failure_scenarios = tuple(item[3] for item in flags_sorted)
            strongest = flags_sorted[0][2]
        else:
            counterarguments = (
                "No dominant opposing evidence in the fixture tape; "
                "residual risk that the volume lead is coincidence.",
            )
            failure_scenarios = (
                "Immediate mean reversion through invalidation with no follow-through.",
            )
            strongest = counterarguments[0]
        return SkepticOutput(
            counterarguments=counterarguments,
            failure_scenarios=failure_scenarios,
            strongest_opposing_case=strongest,
            conviction=conviction,
        )
