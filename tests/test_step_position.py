"""Per-bar high-frequency exits. These lock the rules extracted from simulate_session."""
from __future__ import annotations

from dataclasses import asdict

from athena_trading.paper.high_frequency import (
    HighFrequencyPolicy,
    PositionExit,
    simulate_session,
    step_position,
    synthetic_regular_session,
)

# Full simulate_session reports recorded on a8a4f2e, before step_position existed.
RECORDED_SYNTHETIC_SESSIONS = {
    20261019: {
        "round_trips": 97, "entries": 97,
        "exits_by_reason": {"stop": 58, "target": 34, "time": 5},
        "max_concurrent": 3, "daily_loss_halt": False, "round_trip_cap_hit": False,
        "open_at_end": 0, "limit_brackets": 97, "skipped": 0,
    },
    7: {
        "round_trips": 97, "entries": 97,
        "exits_by_reason": {"stop": 53, "target": 41, "time": 3},
        "max_concurrent": 3, "daily_loss_halt": False, "round_trip_cap_hit": False,
        "open_at_end": 0, "limit_brackets": 97, "skipped": 0,
    },
    11: {
        "round_trips": 97, "entries": 97,
        "exits_by_reason": {"stop": 58, "target": 36, "time": 3},
        "max_concurrent": 3, "daily_loss_halt": False, "round_trip_cap_hit": False,
        "open_at_end": 0, "limit_brackets": 97, "skipped": 0,
    },
    42: {
        "round_trips": 97, "entries": 97,
        "exits_by_reason": {"target": 47, "stop": 49, "time": 1},
        "max_concurrent": 2, "daily_loss_halt": False, "round_trip_cap_hit": False,
        "open_at_end": 0, "limit_brackets": 97, "skipped": 0,
    },
    99: {
        "round_trips": 97, "entries": 97,
        "exits_by_reason": {"stop": 51, "target": 41, "time": 5},
        "max_concurrent": 4, "daily_loss_halt": False, "round_trip_cap_hit": False,
        "open_at_end": 0, "limit_brackets": 97, "skipped": 0,
    },
    123: {
        "round_trips": 99, "entries": 99,
        "exits_by_reason": {"stop": 47, "target": 48, "time": 4},
        "max_concurrent": 4, "daily_loss_halt": False, "round_trip_cap_hit": False,
        "open_at_end": 0, "limit_brackets": 99, "skipped": 0,
    },
    256: {
        "round_trips": 98, "entries": 98,
        "exits_by_reason": {"stop": 48, "target": 49, "time": 1},
        "max_concurrent": 4, "daily_loss_halt": False, "round_trip_cap_hit": False,
        "open_at_end": 0, "limit_brackets": 98, "skipped": 0,
    },
}

SYMBOLS = ("SPY", "QQQ", "IWM", "DIA")


def _position(**overrides) -> dict:
    position = {
        "side": "buy",
        "entry": 100.0,
        "stop": 99.0,
        "target": 101.0,
        "entry_minute": 10,
    }
    position.update(overrides)
    return position


def _bars(fill: tuple[float, float, float, float]) -> dict[str, list]:
    return {symbol: [fill] * 390 for symbol in SYMBOLS}


def test_target_stop_and_same_bar_tie_break():
    policy = HighFrequencyPolicy()
    cases = (
        ("buy", 99.0, 101.0, (100.0, 101.2, 99.5, 100.4), PositionExit(101.0, "target")),
        ("buy", 99.0, 101.0, (100.0, 100.4, 98.8, 99.4), PositionExit(99.0, "stop")),
        ("buy", 99.0, 101.0, (100.0, 101.4, 98.8, 100.0), PositionExit(99.0, "stop")),
        ("buy", 99.0, 101.0, (100.0, 101.0, 99.5, 100.5), PositionExit(101.0, "target")),
        ("buy", 99.0, 101.0, (100.0, 100.4, 99.0, 99.5), PositionExit(99.0, "stop")),
        ("buy", 99.0, 101.0, (100.0, 101.0, 99.0, 100.0), PositionExit(99.0, "stop")),
        ("sell", 101.0, 99.0, (100.0, 100.4, 98.8, 99.4), PositionExit(99.0, "target")),
        ("sell", 101.0, 99.0, (100.0, 101.2, 99.5, 100.4), PositionExit(101.0, "stop")),
        ("sell", 101.0, 99.0, (100.0, 101.4, 98.8, 100.0), PositionExit(101.0, "stop")),
        ("sell", 101.0, 99.0, (100.0, 100.4, 99.0, 99.5), PositionExit(99.0, "target")),
        ("sell", 101.0, 99.0, (100.0, 101.0, 99.5, 100.5), PositionExit(101.0, "stop")),
        ("sell", 101.0, 99.0, (100.0, 101.0, 99.0, 100.0), PositionExit(101.0, "stop")),
    )
    for side, stop, target, bar, expected in cases:
        position = _position(side=side, stop=stop, target=target)
        assert step_position(position, bar, policy, minute=10) == expected, (side, bar)


def test_time_exit_and_flatten_buffer_use_the_close():
    policy = HighFrequencyPolicy(max_hold_minutes=8)
    inside = (100.0, 100.4, 99.5, 100.2)
    long_position = _position()
    assert step_position(long_position, inside, policy, minute=17) is None
    assert step_position(long_position, inside, policy, minute=18) == PositionExit(100.2, "time")
    assert step_position(
        long_position, inside, policy, minute=12, flatten=True,
    ) == PositionExit(100.2, "flatten")
    short_position = _position(side="sell", stop=101.0, target=99.0)
    short_inside = (100.0, 100.4, 99.5, 99.8)
    assert step_position(short_position, short_inside, policy, minute=17) is None
    assert step_position(
        short_position, short_inside, policy, minute=18,
    ) == PositionExit(99.8, "time")
    assert step_position(
        short_position, short_inside, policy, minute=12, flatten=True,
    ) == PositionExit(99.8, "flatten")


def test_stop_target_and_time_outrank_later_exits():
    policy = HighFrequencyPolicy(max_hold_minutes=8)
    long_position = _position()
    both = (100.0, 101.4, 98.8, 100.2)
    assert step_position(
        long_position, both, policy, minute=18, flatten=True,
    ) == PositionExit(99.0, "stop")
    target_only = (100.0, 101.2, 99.5, 100.4)
    assert step_position(
        long_position, target_only, policy, minute=18, flatten=True,
    ) == PositionExit(101.0, "target")
    inside = (100.0, 100.4, 99.5, 100.2)
    assert step_position(
        long_position, inside, policy, minute=18, flatten=True,
    ) == PositionExit(100.2, "time")
    short_position = _position(side="sell", stop=101.0, target=99.0)
    short_both = (100.0, 101.4, 98.8, 100.0)
    assert step_position(
        short_position, short_both, policy, minute=18, flatten=True,
    ) == PositionExit(101.0, "stop")


def test_before_entry_minute_skips_the_bracket_and_flatten_still_closes():
    policy = HighFrequencyPolicy()
    position = _position(entry_minute=10)
    bar = (100.0, 102.0, 98.0, 100.4)
    assert step_position(position, bar, policy, minute=9) is None
    assert step_position(
        position, bar, policy, minute=9, flatten=True,
    ) == PositionExit(100.4, "flatten")


def test_exit_uses_the_stored_fill_anchored_stop_and_target():
    policy = HighFrequencyPolicy()
    # Fill 100.52 with an eight-cent anchor. A wider 99.00 level is not consulted.
    long_position = _position(entry=100.52, stop=100.44, target=100.60, entry_minute=21)
    through_anchor = (100.52, 100.55, 100.40, 100.50)
    assert step_position(
        long_position, through_anchor, policy, minute=21,
    ) == PositionExit(100.44, "stop")
    between = (100.52, 100.58, 100.50, 100.55)
    assert step_position(long_position, between, policy, minute=21) is None
    at_target = (100.52, 100.60, 100.50, 100.58)
    assert step_position(
        long_position, at_target, policy, minute=21,
    ) == PositionExit(100.60, "target")
    short_position = _position(side="sell", entry=99.98, stop=100.06, target=99.90, entry_minute=21)
    short_stop = (99.98, 100.10, 99.95, 100.00)
    assert step_position(
        short_position, short_stop, policy, minute=21,
    ) == PositionExit(100.06, "stop")
    short_target = (99.98, 100.02, 99.90, 99.95)
    assert step_position(
        short_position, short_target, policy, minute=21,
    ) == PositionExit(99.90, "target")


def test_step_position_is_deterministic_and_side_effect_free():
    policy = HighFrequencyPolicy()
    position = _position()
    original = dict(position)
    bar = (100.0, 101.4, 98.8, 100.0)
    first = step_position(position, bar, policy, minute=10)
    second = step_position(position, bar, policy, minute=10)
    assert first == second == PositionExit(99.0, "stop")
    assert position == original
    assert bar == (100.0, 101.4, 98.8, 100.0)


def test_simulate_session_matches_recorded_synthetic_sessions():
    policy = HighFrequencyPolicy()
    for seed, expected in RECORDED_SYNTHETIC_SESSIONS.items():
        report = simulate_session(synthetic_regular_session(seed), policy)
        assert asdict(report) == expected


def test_same_bar_stop_and_target_session_exits_at_the_stop():
    bars = _bars((100.0, 100.0, 100.0, 100.0))
    bars["SPY"][20] = (100.0, 100.50, 100.50, 100.50)
    bars["SPY"][21] = (100.50, 101.00, 100.00, 100.50)
    policy = HighFrequencyPolicy(symbols=SYMBOLS, max_round_trips=1)
    assert asdict(simulate_session(bars, policy)) == {
        "round_trips": 1, "entries": 1, "exits_by_reason": {"stop": 1},
        "max_concurrent": 0, "daily_loss_halt": False, "round_trip_cap_hit": True,
        "open_at_end": 0, "limit_brackets": 1, "skipped": 0,
    }


def test_short_same_bar_stop_and_target_session_exits_at_the_stop():
    bars = _bars((100.50, 100.50, 100.50, 100.50))
    bars["SPY"][20] = (100.50, 100.50, 100.00, 100.00)
    bars["SPY"][21] = (100.00, 101.00, 99.00, 100.00)
    policy = HighFrequencyPolicy(symbols=SYMBOLS, max_round_trips=1)
    assert asdict(simulate_session(bars, policy)) == {
        "round_trips": 1, "entries": 1, "exits_by_reason": {"stop": 1},
        "max_concurrent": 0, "daily_loss_halt": False, "round_trip_cap_hit": True,
        "open_at_end": 0, "limit_brackets": 1, "skipped": 0,
    }


def test_target_only_session_exits_at_the_target():
    bars = _bars((100.0, 100.0, 100.0, 100.0))
    bars["SPY"][20] = (100.0, 100.50, 100.50, 100.50)
    bars["SPY"][21] = (100.52, 100.70, 100.50, 100.60)
    policy = HighFrequencyPolicy(symbols=SYMBOLS, max_round_trips=1)
    assert asdict(simulate_session(bars, policy)) == {
        "round_trips": 1, "entries": 1, "exits_by_reason": {"target": 1},
        "max_concurrent": 0, "daily_loss_halt": False, "round_trip_cap_hit": True,
        "open_at_end": 0, "limit_brackets": 1, "skipped": 0,
    }


def test_time_exit_session_matches_the_recorded_report():
    bars = _bars((100.0, 100.0, 100.0, 100.0))
    bars["SPY"][20] = (100.0, 100.50, 100.50, 100.50)
    for minute in range(21, 40):
        bars["SPY"][minute] = (100.52, 100.55, 100.50, 100.52)
    policy = HighFrequencyPolicy(symbols=SYMBOLS, max_round_trips=1)
    assert asdict(simulate_session(bars, policy)) == {
        "round_trips": 1, "entries": 1, "exits_by_reason": {"time": 1},
        "max_concurrent": 1, "daily_loss_halt": False, "round_trip_cap_hit": True,
        "open_at_end": 0, "limit_brackets": 1, "skipped": 0,
    }


def test_flatten_buffer_session_matches_the_recorded_report():
    bars = _bars((100.0, 100.0, 100.0, 100.0))
    bars["SPY"][374] = (100.0, 100.50, 100.50, 100.50)
    for minute in range(375, 390):
        bars["SPY"][minute] = (100.50, 100.50, 100.50, 100.50)
    policy = HighFrequencyPolicy(
        symbols=SYMBOLS, max_hold_minutes=8,
        close_buffer_minutes=15, entry_cutoff_minutes=15,
    )
    assert asdict(simulate_session(bars, policy, equity=100_000.0)) == {
        "round_trips": 1, "entries": 1, "exits_by_reason": {"flatten": 1},
        "max_concurrent": 1, "daily_loss_halt": False, "round_trip_cap_hit": False,
        "open_at_end": 0, "limit_brackets": 1, "skipped": 0,
    }
