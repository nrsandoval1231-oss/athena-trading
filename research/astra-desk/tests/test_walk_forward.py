from __future__ import annotations

from pathlib import Path

import pytest

from astra_desk.desk import TradingDesk
from astra_desk.errors import LiveTradingDisabled, WalkForwardError
from astra_desk.features import skeptic_flag_features, strong_tape_features, weak_volume_features
from astra_desk.fixtures import walk_setup_dict
from astra_desk.types import Bar, DeskMode, RoundDecision
from astra_desk.walk_forward import (
    WalkFixture,
    WalkForwardReplay,
    evaluate_phase0_exit,
    load_walk_fixture,
    parse_walk_setup,
    replay_bars,
)

REPO = Path(__file__).resolve().parents[1]
SHIPPED = REPO / "fixtures" / "walk_forward" / "phase0_sample.json"

TP_BAR = {
    "ts": "t-tp",
    "open": 100.2,
    "high": 105.4,
    "low": 99.8,
    "close": 105.1,
    "volume": 1,
}


def _setup(
    setup_id: str,
    *,
    skeptic: bool = False,
    weak: bool = False,
    ev_estimate: float = 0.1,
    bars: list[dict] | None = None,
    settlement_pnl: float | None = None,
    regime: str = "trending",
    price: float = 100.0,
) -> dict:
    if weak:
        features = weak_volume_features()
    elif skeptic:
        features = skeptic_flag_features(
            invalidation_price=95.0 if price == 100.0 else price - 5.0
        )
    else:
        features = strong_tape_features(
            invalidation_price=95.0 if price == 100.0 else price - 5.0
        )
    return walk_setup_dict(
        setup_id,
        features=features,
        regime=regime,
        ev_estimate=ev_estimate,
        price=price,
        bars=bars,
        settlement_pnl=settlement_pnl,
    )


def _walk(setups: list[dict]) -> WalkFixture:
    return WalkFixture(setups=tuple(parse_walk_setup(item) for item in setups), path=None)


def test_shipped_walk_meets_exit_criteria() -> None:
    walk = load_walk_fixture(SHIPPED)
    desk = TradingDesk()
    report = WalkForwardReplay(desk).run(walk)
    assert report.setups_total == 4
    assert report.setups_processed == 4
    assert report.halted is False
    assert [r.decision for r in report.rounds] == [
        RoundDecision.DEPLOY,
        RoundDecision.REFUSE,
        RoundDecision.DEPLOY,
        RoundDecision.DEPLOY,
    ]
    assert [r.regime.value for r in report.rounds] == [
        "trending",
        "high_vol",
        "mean_reverting",
        "news_event",
    ]
    assert report.rounds[1].agent_outputs["666_skeptic"].conviction == pytest.approx(0.8)
    assert report.accounting.starting_equity == 500.0
    assert report.exit_criteria.expectancy_per_setup is not None
    assert report.exit_criteria.expectancy_per_setup > 0
    assert report.exit_criteria.max_drawdown_pct < 15.0
    assert report.exit_criteria.met is True
    assert report.live_trading_authority is False
    assert report.ledger_entry_count == 8
    assert desk.ledger.entries[-1].payload["kind"] == "walk_forward_summary"


def test_multi_setup_walk_drives_each_fixture() -> None:
    walk = _walk(
        [
            _setup("a-deploy", bars=[TP_BAR], regime="trending"),
            _setup("b-refuse", skeptic=True, bars=[TP_BAR], regime="high_vol"),
            _setup("c-deploy", bars=[TP_BAR], regime="mean_reverting"),
        ]
    )
    report = WalkForwardReplay().run(walk)
    assert [r.setup_id for r in report.rounds] == ["a-deploy", "b-refuse", "c-deploy"]
    assert report.rounds[0].executed is True
    assert report.rounds[1].executed is False
    assert report.rounds[2].executed is True
    assert report.rounds[0].gate.total_weighted_conviction == pytest.approx(3.5)
    assert report.rounds[1].gate.total_weighted_conviction == pytest.approx(2.7)
    assert report.settlements[1] is None
    assert report.settlements[0] is not None
    assert report.settlements[0].reason == "take_profit"
    assert report.settlements[0].realized_pnl == pytest.approx(1.0)


def test_exit_criteria_met_from_books() -> None:
    walk = _walk(
        [
            _setup("w1", bars=[TP_BAR]),
            _setup("w2", bars=[TP_BAR]),
        ]
    )
    desk = TradingDesk()
    report = WalkForwardReplay(desk).run(walk)
    snap = desk.books.snapshot()
    assert snap.expectancy_per_setup == report.exit_criteria.expectancy_per_setup
    assert snap.max_drawdown_pct == report.exit_criteria.max_drawdown_pct
    assert snap.expectancy_per_setup == pytest.approx(1.0)
    assert snap.max_drawdown_pct < 15.0
    assert report.exit_criteria.met is True


def test_exit_criteria_unmet_when_drawdown_breaches() -> None:
    walk = _walk([_setup("gap-loss", settlement_pnl=-80.0)])
    report = WalkForwardReplay().run(walk)
    assert report.rounds[0].executed is True
    assert report.accounting.max_drawdown_pct == pytest.approx(16.0)
    assert report.exit_criteria.met is False
    assert "max_dd_gte_15" in report.exit_criteria.reasons
    assert report.exit_criteria.expectancy_per_setup == pytest.approx(-80.0)


def test_exit_criteria_unmet_when_expectancy_null() -> None:
    walk = _walk(
        [
            _setup("r1", skeptic=True, bars=[TP_BAR]),
            _setup("r2", skeptic=True, bars=[TP_BAR]),
        ]
    )
    report = WalkForwardReplay().run(walk)
    assert all(not r.executed for r in report.rounds)
    assert report.exit_criteria.expectancy_per_setup is None
    assert report.exit_criteria.met is False
    assert "expectancy_null_insufficient_sample" in report.exit_criteria.reasons


def test_kill_switch_halts_mid_walk() -> None:
    walk = _walk(
        [
            _setup("win", bars=[TP_BAR]),
            _setup("shock", settlement_pnl=-42.0),
            _setup("never", bars=[TP_BAR]),
        ]
    )
    report = WalkForwardReplay().run(walk)
    assert report.halted is True
    assert report.setups_processed == 2
    assert report.setups_total == 3
    assert report.rounds[0].decision is RoundDecision.DEPLOY
    assert report.rounds[1].decision is RoundDecision.DEPLOY
    assert report.halt_reason is not None
    assert "never" not in {r.setup_id for r in report.rounds}


def test_live_modes_still_raise() -> None:
    with pytest.raises(LiveTradingDisabled):
        TradingDesk(live_broker=True)
    auto = TradingDesk(mode=DeskMode.AUTONOMOUS)
    with pytest.raises(WalkForwardError, match="walk_forward_replay"):
        WalkForwardReplay(auto)
    checkpoint = TradingDesk(mode=DeskMode.LIVE_WITH_HUMAN_CHECKPOINT)
    with pytest.raises(WalkForwardError, match="walk_forward_replay"):
        WalkForwardReplay(checkpoint)
    paper = TradingDesk(mode=DeskMode.PAPER_TRADING)
    with pytest.raises(WalkForwardError, match="walk_forward_replay"):
        WalkForwardReplay(paper)


def test_live_fixture_mode_rejected(tmp_path: Path) -> None:
    path = tmp_path / "live.json"
    path.write_text(
        '{"schema":"astra-desk/walk-forward-v1","mode":"autonomous","setups":[{}]}',
        encoding="utf-8",
    )
    with pytest.raises(LiveTradingDisabled):
        load_walk_fixture(path)


def test_hand_set_convictions_rejected(tmp_path: Path) -> None:
    payload = _setup("bad", bars=[TP_BAR])
    payload["agents"] = {"456_scout": {"conviction": 1.0}}
    path = tmp_path / "conv.json"
    import json

    path.write_text(
        json.dumps(
            {
                "schema": "astra-desk/walk-forward-v1",
                "mode": "walk_forward_replay",
                "setups": [payload],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(WalkForwardError, match="do not set agents"):
        load_walk_fixture(path)


def test_replay_bars_stop_wins_same_bar() -> None:
    bar = Bar(ts="x", open=100.0, high=106.0, low=94.0, close=104.0)
    settlement = replay_bars(
        entry=100.0,
        notional=20.0,
        side="long",
        stop_price=95.0,
        take_profit_levels=(105.0,),
        bars=(bar,),
    )
    assert settlement.reason == "stop"
    assert settlement.realized_pnl == pytest.approx(-1.0)


def test_replay_bars_gap_stop_fills_open() -> None:
    bar = Bar(ts="gap", open=90.0, high=91.0, low=88.0, close=89.0)
    settlement = replay_bars(
        entry=100.0,
        notional=20.0,
        side="long",
        stop_price=95.0,
        take_profit_levels=(105.0,),
        bars=(bar,),
    )
    assert settlement.reason == "stop"
    assert settlement.exit_price == 90.0
    assert settlement.realized_pnl == pytest.approx(-2.0)


def test_evaluate_phase0_exit_rejects_unknown_rule() -> None:
    desk = TradingDesk()
    with pytest.raises(WalkForwardError, match="unsupported"):
        evaluate_phase0_exit(desk.books, rule="invented")
