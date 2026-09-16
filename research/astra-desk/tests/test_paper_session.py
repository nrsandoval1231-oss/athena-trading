from __future__ import annotations

from pathlib import Path

import pytest

from astra_desk.cli import main
from astra_desk.desk import TradingDesk
from astra_desk.errors import BrokerCredentialsError, LiveTradingDisabled
from astra_desk.kill_switch import KillSwitch
from astra_desk.paper_session import run_paper_session
from astra_desk.types import RoundDecision

REPO = Path(__file__).resolve().parents[1]
TAPE = REPO / "fixtures" / "tape" / "sample.json"


def test_offline_paper_session_on_shipped_tape() -> None:
    report = run_paper_session(broker_kind="fixture", fixture_path=TAPE)
    assert report.kind == "paper_session"
    assert report.tape_source == "fixture"
    assert report.broker == "fixture"
    assert report.live_trading_authority is False
    assert report.reenable == KillSwitch.REENABLE
    assert report.kill_switch_action == KillSwitch.ACTION
    assert report.halted is False
    assert report.ledger_verified is True
    assert report.setups_total == 3
    assert report.setups_processed == 3
    assert report.deploys == 1
    assert report.refuses == 2
    assert report.vetoes == 0
    assert report.halts == 0
    assert report.fills == 1
    assert [item.decision for item in report.rounds] == [
        RoundDecision.DEPLOY.value,
        RoundDecision.REFUSE.value,
        RoundDecision.REFUSE.value,
    ]
    filled = report.rounds[0]
    assert filled.executed is True
    assert filled.broker == "fixture"
    assert filled.broker_status == "filled"
    assert filled.settlement == "take_profit"
    assert filled.realized_pnl is not None and filled.realized_pnl > 0
    assert report.accounting.closed_trade_count == 1
    assert report.accounting.starting_equity == 500.0
    assert report.ledger_entry_count >= 3


def test_alpaca_paper_without_keys_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    with pytest.raises(BrokerCredentialsError, match="ALPACA_API_KEY"):
        run_paper_session(broker_kind="alpaca-paper", fixture_path=TAPE)


def test_live_broker_still_raises() -> None:
    with pytest.raises(LiveTradingDisabled):
        TradingDesk(live_broker=True)
    with pytest.raises(LiveTradingDisabled):
        run_paper_session(desk=TradingDesk(live_broker=True))


def test_offline_session_does_not_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("paper-session default path must not call urlopen")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    report = run_paper_session(broker_kind="fixture")
    assert report.fills == 1
    assert report.live_trading_authority is False
    code = main(["paper-session", "--broker", "fixture"])
    assert code == 0


def test_kill_switch_halts_session_and_flattens() -> None:
    desk = TradingDesk(broker_kind="fixture")
    desk.books.apply_drawdown_mark(8.0)
    report = run_paper_session(desk=desk, fixture_path=TAPE)
    assert report.halted is True
    assert report.halts == 1
    assert report.fills == 0
    assert desk.books.positions == ()
    assert report.reenable == "manual_human_only"
    assert report.live_trading_authority is False
