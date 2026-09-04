"""Session runner — both sides, budgets, and constitutional portfolio ceilings."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta


from athena_trading.orb_m.constitution import Constitution, ManagementParameters
from athena_trading.orb_m.market import Bar, PriorSession
from athena_trading.orb_m.session import SessionRiskState, run_symbol_session

BASE = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)
PARAMS = ManagementParameters(pass_below=0.10, standard_above=0.20,
                              high_conviction_above=0.90)
PRIOR = PriorSession(previous_close=100.0, atr14=2.0,
                     average_daily_volume=5_000_000.0,
                     average_opening_volume=20_000.0)


def bar(minute, o, h, low, c, volume=2000.0):
    return Bar(BASE + timedelta(minutes=minute), minute, o, h, low, c, volume)


def opening_range_bars():
    # 15 bars: ORH=101, ORL=99
    bars = []
    for m in range(15):
        bars.append(bar(m, 100.0, 101.0 if m == 3 else 100.5,
                        99.0 if m == 4 else 99.5, 100.2, volume=2000))
    return bars


def long_breakout_session():
    bars = opening_range_bars()
    bars.append(bar(15, 100.5, 101.2, 100.4, 101.1))  # close above ORH
    bars.append(bar(16, 101.2, 101.4, 101.1, 101.3))  # entry bar
    bars.append(bar(17, 101.3, 101.5, 98.0, 98.5))    # stop out
    return bars


def test_session_runs_a_long_episode_to_a_stop():
    result = run_symbol_session(long_breakout_session(), PRIOR, Constitution(slippage_bps=0.0),
                                PARAMS)
    taken = [ep for ep in result.episodes if ep.result is not None]
    assert taken
    assert taken[0].signal.direction == "LONG"
    assert taken[0].result.exit_reason == "STOP"


def test_max_daily_loss_halts_further_entries():
    risk = SessionRiskState(realized_r=-3.0, peak_realized_r=0.0)
    result = run_symbol_session(
        long_breakout_session(), PRIOR, Constitution(slippage_bps=0.0), PARAMS, risk=risk)
    assert result.halt_reason == "MAX_DAILY_LOSS"
    assert all(ep.result is None for ep in result.episodes)


def test_max_open_positions_is_enforced_before_entry():
    risk = SessionRiskState(open_positions=3)
    result = run_symbol_session(
        long_breakout_session(), PRIOR, Constitution(slippage_bps=0.0), PARAMS, risk=risk)
    assert result.halt_reason == "MAX_OPEN_POSITIONS"


def test_both_sides_can_attempt_independently():
    bars = opening_range_bars()
    # Long breakout then stop, then short breakout.
    bars += [
        bar(15, 100.5, 101.2, 100.4, 101.1),
        bar(16, 101.2, 101.4, 101.1, 101.3),
        bar(17, 101.3, 101.5, 98.5, 98.8),   # long stop
        bar(18, 98.8, 98.9, 98.0, 98.2),     # close below ORL
        bar(19, 98.2, 98.3, 98.0, 98.1),     # short entry
        bar(20, 98.1, 100.5, 98.0, 100.2),   # short stop
    ]
    result = run_symbol_session(bars, PRIOR, Constitution(slippage_bps=0.0), PARAMS)
    directions = [ep.signal.direction for ep in result.episodes if ep.result is not None]
    assert "LONG" in directions and "SHORT" in directions


def test_attempt_budget_blocks_a_second_initial_long():
    bars = opening_range_bars()
    # Two long breakout sequences; budget allows only one initial long.
    # Re-entry is disabled here so the second print cannot spend the
    # continuation slot and mask an initial-budget leak.
    bars += [
        bar(15, 100.5, 101.2, 100.4, 101.1),
        bar(16, 101.2, 101.4, 101.1, 101.3),
        bar(17, 101.3, 101.5, 98.5, 98.8),
        # back above range later — must not take a second initial long
        bar(30, 100.0, 101.5, 99.5, 101.2),
        bar(31, 101.2, 101.4, 101.0, 101.3),
        bar(32, 101.3, 101.4, 98.5, 98.8),
    ]
    params = ManagementParameters(pass_below=0.10, standard_above=0.20,
                                  high_conviction_above=0.90, reentry_enabled=False)
    result = run_symbol_session(bars, PRIOR, Constitution(slippage_bps=0.0), params)
    longs = [ep for ep in result.episodes
             if ep.signal.direction == "LONG" and ep.result is not None]
    assert len(longs) == 1
    assert all(not ep.signal.is_reentry for ep in longs)
