from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from astra_desk.desk import TradingDesk
from astra_desk.errors import LiveTradingDisabled, PaperTradingError
from astra_desk.features import strong_tape_features
from astra_desk.fixtures import walk_setup_dict
from astra_desk.paper_trading import (
    PaperDay,
    PaperPeriod,
    limit_paper_days,
    load_paper_fixture,
)
from astra_desk.types import DeskMode
from astra_desk.walk_forward import parse_walk_setup

REPO = Path(__file__).resolve().parents[1]
SHIPPED = REPO / "fixtures" / "paper" / "phase1_sample.json"

TP_BAR = {
    "ts": "t-tp",
    "open": 100.2,
    "high": 105.4,
    "low": 99.8,
    "close": 105.1,
    "volume": 1,
}


def _setup(setup_id: str, *, settlement_pnl: float | None = None) -> dict:
    return walk_setup_dict(
        setup_id,
        features=strong_tape_features(),
        bars=[TP_BAR],
        settlement_pnl=settlement_pnl,
    )


def _day(session_date: str, *setups: dict) -> PaperDay:
    return PaperDay(
        session_date=session_date,
        setups=tuple(parse_walk_setup(item) for item in setups),
    )


def _dates(n: int, start: str = "2024-06-01") -> list[str]:
    origin = date.fromisoformat(start)
    return [(origin + timedelta(days=i)).isoformat() for i in range(n)]


def test_paper_period_incomplete_before_21_days() -> None:
    period = PaperPeriod()
    progress = None
    for session in _dates(20):
        progress = period.advance(
            session, (_day(session, _setup(f"d-{session}")).setups[0],)
        )
    assert progress is not None
    assert progress.days_completed == 20
    assert progress.days_required == 21
    assert progress.duration_complete is False
    assert progress.halted is False
    from astra_desk.walk_forward import PHASE0_EXIT_RULE, evaluate_phase0_exit

    phase0 = evaluate_phase0_exit(period.desk.books, rule=PHASE0_EXIT_RULE)
    assert phase0.met is True
    assert progress.duration_complete is False


def test_paper_period_complete_at_21_days() -> None:
    period = PaperPeriod()
    last = None
    for session in _dates(21):
        last = period.advance(
            session, (_day(session, _setup(f"d-{session}")).setups[0],)
        )
    assert last is not None
    assert last.days_completed == 21
    assert last.days_required == 21
    assert last.duration_complete is True
    assert last.halted is False
    from astra_desk.walk_forward import PHASE0_EXIT_RULE, evaluate_phase0_exit

    phase0 = evaluate_phase0_exit(period.desk.books, rule=PHASE0_EXIT_RULE)
    assert phase0.expectancy_per_setup is not None
    assert phase0.max_drawdown_pct < 15.0


def test_kill_switch_aborts_paper_period() -> None:
    period = PaperPeriod()
    period.advance("2024-07-01", (_day("2024-07-01", _setup("win")).setups[0],))
    shock = _day("2024-07-02", _setup("shock", settlement_pnl=-42.0))
    progress = period.advance("2024-07-02", shock.setups)
    assert progress.halted is True
    assert progress.duration_complete is False
    assert progress.days_completed == 2
    with pytest.raises(PaperTradingError, match="kill switch halted"):
        period.advance("2024-07-03", (_day("2024-07-03", _setup("never")).setups[0],))


def test_halt_on_day_21_is_not_duration_complete() -> None:
    """21 session days still fail duration if the kill switch tripped."""
    period = PaperPeriod()
    dates = _dates(21)
    for session in dates[:20]:
        period.advance(session, (_day(session, _setup(f"d-{session}")).setups[0],))
    shock = _day(dates[20], _setup("shock", settlement_pnl=-42.0))
    progress = period.advance(dates[20], shock.setups)
    assert progress.days_completed == 21
    assert progress.days_required == 21
    assert progress.halted is True
    assert progress.duration_complete is False


def test_live_modes_still_raise() -> None:
    with pytest.raises(LiveTradingDisabled):
        TradingDesk(live_broker=True)
    auto = TradingDesk(mode=DeskMode.AUTONOMOUS)
    with pytest.raises(PaperTradingError, match="paper_trading"):
        PaperPeriod(auto)
    walk = TradingDesk(mode=DeskMode.WALK_FORWARD_REPLAY)
    with pytest.raises(PaperTradingError, match="paper_trading"):
        PaperPeriod(walk)
    checkpoint = TradingDesk(mode=DeskMode.LIVE_WITH_HUMAN_CHECKPOINT)
    with pytest.raises(PaperTradingError, match="paper_trading"):
        PaperPeriod(checkpoint)


def test_live_paper_fixture_rejected(tmp_path: Path) -> None:
    path = tmp_path / "live.json"
    path.write_text(
        '{"schema":"astra-desk/paper-period-v1","mode":"autonomous","days":[]}',
        encoding="utf-8",
    )
    with pytest.raises(LiveTradingDisabled):
        load_paper_fixture(path)


def test_shipped_paper_fixture_meets_duration() -> None:
    fixture = load_paper_fixture(SHIPPED)
    assert len(fixture.days) == 21
    report = PaperPeriod().run(fixture)
    assert report.days_required == 21
    assert report.days_completed == 21
    assert report.duration_complete is True
    assert report.halted is False
    assert report.live_trading_authority is False
    assert report.mode is DeskMode.PAPER_TRADING
    assert report.phase_0_exit_criteria.met is True
    truncated = limit_paper_days(fixture, 5)
    short = PaperPeriod().run(truncated)
    assert short.days_completed == 5
    assert short.duration_complete is False


def test_duplicate_session_date_fails_closed() -> None:
    period = PaperPeriod()
    setups = _day("2024-08-01", _setup("a")).setups
    period.advance("2024-08-01", setups)
    with pytest.raises(PaperTradingError, match="already recorded"):
        period.advance("2024-08-01", setups)
    assert period.progress().days_completed == 1
