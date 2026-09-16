"""Deterministic Phase 0 round fixtures. Not live market data."""

from __future__ import annotations

from typing import Mapping

from astra_desk.features import (
    SetupFeatures,
    skeptic_flag_features,
    strong_tape_features,
    weak_volume_features,
)
from astra_desk.types import Regime, RoundFixture


def _base(**overrides: object) -> RoundFixture:
    data: dict[str, object] = dict(
        setup_id="fixture-base",
        symbol="PAPER-TEST",
        regime=Regime.TRENDING,
        ev_estimate=0.12,
        price=100.0,
        features=strong_tape_features(),
        broker_size_units=40.0,
        broker_kelly_fraction=0.20,
        correlation_group="index-beta",
        side="long",
    )
    data.update(overrides)
    return RoundFixture(**data)  # type: ignore[arg-type]


def deploy_fixture() -> RoundFixture:
    """Strong tape features → scout/reader/exit ~1.0, skeptic 0, EV > 0."""
    return _base(setup_id="fixture-deploy")


def full_size_fixture() -> RoundFixture:
    """Stub double only: reader conviction 1.667 reaches the 4.5 band."""
    return _base(
        setup_id="fixture-full-size",
        features=strong_tape_features(),
        scout_conviction=1.0,
        scout_rationale="stub full-size scout",
        reader_conviction=1.667,
        reader_rationale="stub full-size reader",
        reader_p_organic=0.82,
        broker_size_units=40.0,
        broker_kelly_fraction=0.20,
        exit_stop_price=95.0,
        exit_take_profit_levels=(105.0, 110.0),
        exit_conviction=1.0,
        skeptic_counterarguments=("stub",),
        skeptic_failure_scenarios=("stub",),
        skeptic_strongest_opposing_case="stub",
        skeptic_conviction=0.0,
    )


def refuse_fixture() -> RoundFixture:
    """Weak volume lead → weighted conviction below 3.5."""
    return _base(setup_id="fixture-refuse", features=weak_volume_features())


def skeptic_blocks_fixture() -> RoundFixture:
    """Failed breakout + crowding → skeptic 0.8, gate 2.7."""
    return _base(setup_id="fixture-skeptic-blocks", features=skeptic_flag_features())


def ev_refuse_fixture() -> RoundFixture:
    return _base(setup_id="fixture-ev-refuse", ev_estimate=0.0)


def risk_veto_fixture() -> RoundFixture:
    """0.5× band of 120 USD is 12% of 500, over max_single_position_pct 10."""
    return _base(setup_id="fixture-risk-veto", broker_size_units=120.0)


def halt_drawdown_fixture() -> RoundFixture:
    return _base(setup_id="fixture-halt-drawdown")


def walk_setup_dict(
    setup_id: str,
    *,
    features: SetupFeatures | None = None,
    regime: str = "trending",
    ev_estimate: float = 0.12,
    price: float = 100.0,
    bars: list[dict] | None = None,
    settlement_pnl: float | None = None,
    size_units: float = 40.0,
    symbol: str = "PAPER-TEST",
    resolved_correct_by_agent: Mapping[str, bool] | None = None,
) -> dict:
    """Walk-forward JSON setup driven by features, not hand-set convictions."""
    payload: dict = {
        "setup_id": setup_id,
        "symbol": symbol,
        "regime": regime,
        "ev_estimate": ev_estimate,
        "price": price,
        "side": "long",
        "correlation_group": "index-beta",
        "broker": {"size_units": size_units, "kelly_fraction": 0.2},
        "features": (features or strong_tape_features()).as_dict(),
    }
    if bars is not None:
        payload["bars"] = bars
    if settlement_pnl is not None:
        payload["settlement"] = {"realized_pnl": settlement_pnl}
    if resolved_correct_by_agent:
        payload["resolved_correct_by_agent"] = {
            str(agent_id): bool(flag) for agent_id, flag in resolved_correct_by_agent.items()
        }
    return payload


SCENARIOS = {
    "deploy": deploy_fixture,
    "full-size": full_size_fixture,
    "refuse": refuse_fixture,
    "skeptic": skeptic_blocks_fixture,
    "ev-refuse": ev_refuse_fixture,
    "veto": risk_veto_fixture,
    "halt": halt_drawdown_fixture,
}
