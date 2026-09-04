from datetime import UTC, datetime, timedelta

from athena_trading.orb_m.constitution import Constitution, ManagementParameters
from athena_trading.orb_m.entry import OpeningRange
from athena_trading.orb_m.market import Bar
from athena_trading.orb_m.session import deterioration_by_minute, run_full_session

BASE = datetime(2026, 3, 2, 14, 30, tzinfo=UTC)


def _bar(minute, open_, high, low, close, volume=1000):
    return Bar(BASE + timedelta(minutes=minute), minute, open_, high, low, close, volume)


def _session():
    bars = [_bar(minute, 100, 100.5, 99.5, 100) for minute in range(15)]
    bars += [
        _bar(15, 100.5, 102.2, 100.4, 102.0),
        _bar(16, 102.0, 102.2, 101.8, 102.0),
        _bar(17, 102.0, 102.1, 98.5, 99.0),
        _bar(18, 99.0, 99.2, 97.8, 98.0),
        _bar(19, 98.0, 98.2, 97.5, 98.0),
    ]
    bars += [_bar(minute, 98, 98.2, 97.8, 98) for minute in range(20, 390)]
    return bars


def test_deterioration_is_prefix_stable():
    opening = OpeningRange(101, 99, 15000, 15)
    parameters = ManagementParameters()
    bars = _session()
    before = deterioration_by_minute(bars[:18], "LONG", opening, parameters)[17]
    after = deterioration_by_minute(bars, "LONG", opening, parameters)[17]
    assert before == after


def test_full_session_keeps_both_directions_live():
    result = run_full_session(
        OpeningRange(101, 99, 15000, 15), _session(), 2.0,
        Constitution(), ManagementParameters(),
    )
    assert {episode.direction for episode in result.episodes} == {"LONG", "SHORT"}
    assert result.initial_attempts == 2
    assert result.reentries <= 2
    assert all(episode.result.exit_reason not in {"OPEN", "DATA_EXHAUSTED"} for episode in result.episodes)
