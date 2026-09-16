import pytest

from astra_desk.cli import main, result_to_dict
from astra_desk.desk import TradingDesk
from astra_desk.fixtures import deploy_fixture


def test_cli_round_deploy(capsys) -> None:
    code = main(["round", "--scenario", "deploy"])
    captured = capsys.readouterr()
    assert code == 0
    assert '"decision": "deploy"' in captured.out
    assert '"live_trading_authority": false' in captured.out


def test_cli_round_refuse(capsys) -> None:
    code = main(["round", "--scenario", "refuse"])
    captured = capsys.readouterr()
    assert code == 1
    assert '"decision": "refuse"' in captured.out


def test_cli_walk_forward_shipped(capsys) -> None:
    code = main(["walk-forward"])
    captured = capsys.readouterr()
    assert code == 0
    assert '"met": true' in captured.out
    assert '"live_trading_authority": false' in captured.out
    assert '"starting_equity": 500.0' in captured.out
    assert "wf-001-volume-lead" in captured.out


def test_cli_paper_period_incomplete(capsys) -> None:
    code = main(["paper-period", "--days", "5"])
    captured = capsys.readouterr()
    assert code == 1
    assert '"duration_complete": false' in captured.out
    assert '"days_completed": 5' in captured.out
    assert '"days_required": 21' in captured.out
    assert '"progress": "5/21"' in captured.out
    assert '"halted": false' in captured.out
    assert '"live_trading_authority": false' in captured.out
    assert '"reenable": "manual_human_only"' in captured.out


def test_cli_paper_period_complete(capsys) -> None:
    code = main(["paper-period"])
    captured = capsys.readouterr()
    assert code == 0
    assert '"duration_complete": true' in captured.out
    assert '"days_completed": 21' in captured.out
    assert '"progress": "21/21"' in captured.out
    assert '"halted": false' in captured.out
    assert '"live_trading_authority": false' in captured.out


def test_cli_paper_period_rejects_live_modes() -> None:
    with pytest.raises(SystemExit):
        main(["paper-period", "--mode", "autonomous"])
    with pytest.raises(SystemExit):
        main(["paper-period", "--mode", "live_with_human_checkpoint"])
    with pytest.raises(SystemExit):
        main(["walk-forward", "--mode", "paper_trading"])
    with pytest.raises(SystemExit):
        main(["human-checkpoint", "--mode", "autonomous"])
    with pytest.raises(SystemExit):
        main(["autonomous", "--mode", "live_with_human_checkpoint"])
    with pytest.raises(SystemExit):
        main(["autonomous", "--broker", "live"])
    with pytest.raises(SystemExit):
        main(["calibrate", "--mode", "autonomous"])
    with pytest.raises(SystemExit):
        main(["calibrate", "--broker", "live"])
    with pytest.raises(SystemExit):
        main(["round", "--broker", "live"])
    with pytest.raises(SystemExit):
        main(["broker-preflight", "--broker", "live"])
    with pytest.raises(SystemExit):
        main(["tape", "--source", "live"])
    with pytest.raises(SystemExit):
        main(["paper-session", "--broker", "live"])


def test_cli_autonomous_paper(capsys) -> None:
    code = main(["autonomous"])
    captured = capsys.readouterr()
    assert code == 0
    assert '"rounds_processed": 5' in captured.out
    assert '"progress": "5/5"' in captured.out
    assert '"kill_switch_armed": true' in captured.out
    assert '"ledger_verified": true' in captured.out
    assert '"live_trading_authority": false' in captured.out
    assert '"halted": false' in captured.out


def test_cli_autonomous_rounds(capsys) -> None:
    code = main(["autonomous", "--rounds", "2"])
    captured = capsys.readouterr()
    assert code == 0
    assert '"rounds_processed": 2' in captured.out
    assert '"progress": "2/2"' in captured.out
    assert '"live_trading_authority": false' in captured.out


def test_cli_human_checkpoint_unapproved(capsys) -> None:
    code = main(["human-checkpoint", "--trades", "5"])
    captured = capsys.readouterr()
    assert code == 1
    assert '"trade_count_complete": false' in captured.out
    assert '"trades_completed": 0' in captured.out
    assert '"progress": "0/25"' in captured.out
    assert '"live_trading_authority": false' in captured.out


def test_cli_human_checkpoint_incomplete(capsys) -> None:
    code = main(["human-checkpoint", "--approver", "operator", "--trades", "5"])
    captured = capsys.readouterr()
    assert code == 1
    assert '"trade_count_complete": false' in captured.out
    assert '"trades_completed": 5' in captured.out
    assert '"progress": "5/25"' in captured.out
    assert '"halted": false' in captured.out
    assert '"reenable": "manual_human_only"' in captured.out


def test_cli_human_checkpoint_complete(capsys) -> None:
    code = main(["human-checkpoint", "--approver", "operator"])
    captured = capsys.readouterr()
    assert code == 0
    assert '"trade_count_complete": true' in captured.out
    assert '"trades_completed": 25' in captured.out
    assert '"progress": "25/25"' in captured.out
    assert '"live_trading_authority": false' in captured.out


def test_result_dict_has_ledger_fields() -> None:
    result = TradingDesk().run_round(deploy_fixture())
    payload = result_to_dict(result)
    fields = payload["accounting"]
    for key in (
        "equity",
        "realized_pnl",
        "unrealized_pnl",
        "max_drawdown_pct",
        "exposure_pct",
        "sharpe",
        "sortino",
        "win_rate",
        "expectancy_per_setup",
    ):
        assert key in fields


def test_cli_tape_walk(capsys) -> None:
    code = main(["tape"])
    captured = capsys.readouterr()
    assert code == 0
    assert '"kind": "tape_walk"' in captured.out
    assert '"live_trading_authority": false' in captured.out
    assert '"broker_port": false' in captured.out
    assert "tape-001-volume-lead" in captured.out
    assert "volume_z" in captured.out
    assert "cancel_rate" in captured.out


def test_cli_tape_round(capsys) -> None:
    code = main(["tape", "--round"])
    captured = capsys.readouterr()
    assert code == 0
    assert '"kind": "tape_round"' in captured.out
    assert '"decision": "deploy"' in captured.out
    assert '"live_trading_authority": false' in captured.out


def test_cli_walk_forward_accepts_tape_fixture(capsys) -> None:
    code = main(["walk-forward", "--fixture", "fixtures/tape/sample.json"])
    captured = capsys.readouterr()
    assert code == 0
    assert "tape-001-volume-lead" in captured.out
    assert '"live_trading_authority": false' in captured.out


def test_cli_tape_alpaca_fails_without_keys(capsys, monkeypatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    code = main(["tape", "--source", "alpaca", "--symbol", "AAPL"])
    captured = capsys.readouterr()
    assert code == 1
    assert '"refused": true' in captured.out
    assert "ALPACA_API_KEY" in captured.out
    assert '"live_trading_authority": false' in captured.out


def test_cli_paper_session_offline(capsys) -> None:
    code = main(["paper-session"])
    captured = capsys.readouterr()
    assert code == 0
    assert '"kind": "paper_session"' in captured.out
    assert '"broker": "fixture"' in captured.out
    assert '"deploys": 1' in captured.out
    assert '"refuses": 2' in captured.out
    assert '"fills": 1' in captured.out
    assert '"halted": false' in captured.out
    assert '"live_trading_authority": false' in captured.out
    assert '"reenable": "manual_human_only"' in captured.out
    assert '"kill_switch_action": "flatten_all_and_halt"' in captured.out
    assert '"ledger_verified": true' in captured.out
    assert "tape-001-volume-lead" in captured.out
    assert '"closed_trade_count": 1' in captured.out


def test_cli_paper_session_alpaca_without_keys(capsys, monkeypatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    code = main(["paper-session", "--broker", "alpaca-paper"])
    captured = capsys.readouterr()
    assert code == 1
    assert '"refused": true' in captured.out
    assert "ALPACA_API_KEY" in captured.out
    assert '"live_trading_authority": false' in captured.out
