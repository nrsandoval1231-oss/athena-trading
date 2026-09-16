from __future__ import annotations

import json
from pathlib import Path

from astra_desk.cli import main
from astra_desk.paper_session import run_paper_session
from astra_desk.report import STORE_REQUIRED_REASON, build_operator_report
from astra_desk.store import CHAIN_NAME


def test_report_happy_path_from_store(tmp_path: Path) -> None:
    store = tmp_path / "ledger"
    session = run_paper_session(store_path=store, new_store=True)
    report = build_operator_report(store)
    assert report.kind == "operator_report"
    assert report.live_trading_authority is False
    assert report.ledger_verified is True
    assert report.ledger_entry_count == session.ledger_entry_count
    assert report.closed_trade_count == 1
    assert report.books["equity"] == session.accounting.equity
    assert report.books["realized_pnl"] == session.accounting.realized_pnl
    assert report.books["unrealized_pnl"] == session.accounting.unrealized_pnl
    assert report.books["max_drawdown_pct"] == session.accounting.max_drawdown_pct
    assert report.books["exposure_pct"] == session.accounting.exposure_pct
    assert report.kill_switch["halted"] is False
    assert report.kill_switch["armed"] is True
    assert report.weights["working_equals_contract"] is True
    assert report.phase_progress["phase_0"]["status"] == "derived"
    assert report.phase_progress["phase_1"]["status"] == "unknown"
    assert report.phase_progress["phase_2"]["status"] == "unknown"
    assert report.phase_progress["phase_3"]["status"] == "unknown"
    assert "live_trading_authority: False" in report.human_summary


def test_cli_report_store(tmp_path: Path, capsys) -> None:
    store = tmp_path / "ledger"
    assert main(["paper-session", "--new-store", "--store", str(store)]) == 0
    capsys.readouterr()
    code = main(["report", "--store", str(store)])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["kind"] == "operator_report"
    assert payload["read_only"] is True
    assert payload["broker_calls"] is False
    assert payload["live_trading_authority"] is False
    assert payload["ledger_verified"] is True
    assert payload["closed_trade_count"] == 1
    assert "equity" in payload
    assert "max_drawdown_pct" in payload
    assert payload["kill_switch"]["armed"] is True
    assert payload["weights"]["contract"]["456_scout"] == 1.0


def test_cli_report_human(tmp_path: Path, capsys) -> None:
    store = tmp_path / "ledger"
    assert main(["paper-session", "--new-store", "--store", str(store)]) == 0
    capsys.readouterr()
    code = main(["report", "--store", str(store), "--human"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["kind"] == "operator_report"
    assert "Astra Desk operator report" in captured.err
    assert "live_trading_authority" in captured.err


def test_cli_report_requires_store(capsys) -> None:
    code = main(["report"])
    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.out)
    assert payload["refused"] is True
    assert payload["live_trading_authority"] is False
    assert "requires --store" in payload["reason"]
    assert STORE_REQUIRED_REASON.split("(")[0].strip() in payload["reason"]


def test_cli_report_missing_store(tmp_path: Path, capsys) -> None:
    code = main(["report", "--store", str(tmp_path / "missing")])
    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.out)
    assert payload["refused"] is True
    assert "not found" in payload["reason"]
    assert payload["live_trading_authority"] is False


def test_cli_report_corrupt_store(tmp_path: Path, capsys) -> None:
    store = tmp_path / "ledger"
    assert main(["paper-session", "--new-store", "--store", str(store)]) == 0
    capsys.readouterr()
    chain = store / CHAIN_NAME
    chain.write_text(chain.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    code = main(["report", "--store", str(store)])
    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.out)
    assert payload["refused"] is True
    assert payload["live_trading_authority"] is False
    assert "corrupt" in payload["reason"] or "verification" in payload["reason"]
