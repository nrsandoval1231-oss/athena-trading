import pytest

from astra_desk.accounting import CapitalBooks
from astra_desk.desk import TradingDesk
from astra_desk.errors import KillSwitchError
from astra_desk.fixtures import deploy_fixture, halt_drawdown_fixture
from astra_desk.kill_switch import KillSwitch
from astra_desk.types import RoundDecision


def test_kill_switch_trips_on_drawdown() -> None:
    books = CapitalBooks(500.0, "USD")
    ks = KillSwitch(drawdown_pct=8.0, daily_loss_limit_pct=5.0)
    books.apply_drawdown_mark(8.0)
    state = ks.poll(books)
    assert state.halted is True
    assert state.action == "flatten_all_and_halt"
    assert "drawdown_pct >= 8.0" in (state.reason or "")
    assert books.positions == ()


def test_kill_switch_trips_on_daily_loss() -> None:
    books = CapitalBooks(500.0, "USD")
    ks = KillSwitch(drawdown_pct=8.0, daily_loss_limit_pct=5.0)
    books.apply_daily_loss_mark(5.0)
    state = ks.poll(books)
    assert state.halted is True
    assert "daily_loss_limit_breach" in (state.reason or "")


def test_kill_switch_reenable_is_manual_human_only() -> None:
    books = CapitalBooks(500.0, "USD")
    ks = KillSwitch(drawdown_pct=8.0, daily_loss_limit_pct=5.0)
    books.apply_drawdown_mark(8.0)
    ks.poll(books)
    with pytest.raises(KillSwitchError, match="manual_human_only"):
        ks.reenable(human_confirmed=False)
    ks.reenable(human_confirmed=True)
    assert ks.halted is False


def test_desk_halts_before_agents_when_drawdown_breached() -> None:
    desk = TradingDesk()
    desk.books.apply_drawdown_mark(8.0)
    result = desk.run_round(halt_drawdown_fixture())
    assert result.decision is RoundDecision.HALT
    assert result.executed is False
    assert result.kill_switch.halted is True
    assert result.live_trading_authority is False
    # A follow-up round stays halted even with a deployable fixture.
    blocked = desk.run_round(deploy_fixture())
    assert blocked.decision is RoundDecision.HALT

    desk.reenable(human_confirmed=True)
    still_underwater = desk.run_round(deploy_fixture())
    assert still_underwater.decision is RoundDecision.HALT

    desk.reenable(human_confirmed=True)
    # Recover the marked loss so current drawdown and daily loss are inside limits.
    desk.books.apply_realized(-desk.books.realized_pnl)
    resumed = desk.run_round(deploy_fixture())
    assert resumed.decision is RoundDecision.DEPLOY
