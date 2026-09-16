from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_desk.cli import main
from astra_desk.desk import TradingDesk
from astra_desk.errors import LedgerStoreError
from astra_desk.ledger import outcomes_from_ledger
from astra_desk.paper_session import run_paper_session
from astra_desk.store import CHAIN_NAME, DEFAULT_DATA_DIR, LedgerStore, attach_store

REPO = Path(__file__).resolve().parents[1]


def test_write_reload_verify(tmp_path: Path) -> None:
    store_dir = tmp_path / "ledger"
    first = run_paper_session(store_path=store_dir, new_store=True)
    assert first.persisted is True
    assert first.ledger_verified is True
    assert first.fills == 1
    assert first.accounting.closed_trade_count == 1
    first_count = first.ledger_entry_count
    assert first_count > 0
    ok, reason = TradingDesk().ledger.verify()
    assert ok and reason == "ledger_verified"

    second = run_paper_session(store_path=store_dir, new_store=False)
    assert second.persisted is True
    assert second.ledger_verified is True
    assert second.ledger_entry_count > first_count
    assert second.accounting.closed_trade_count == 2
    assert second.accounting.realized_pnl == pytest.approx(first.accounting.realized_pnl * 2)

    desk = TradingDesk()
    store = LedgerStore.open(store_dir, new=False)
    store.attach(desk)
    ok, reason = desk.ledger.verify()
    assert ok, reason
    assert len(desk.ledger) == second.ledger_entry_count
    assert desk.books.snapshot().closed_trade_count == 2
    assert len(outcomes_from_ledger(desk.ledger)) >= 6


def test_corrupt_chain_refused(tmp_path: Path) -> None:
    store_dir = tmp_path / "ledger"
    run_paper_session(store_path=store_dir, new_store=True)
    chain = store_dir / CHAIN_NAME
    lines = chain.read_text(encoding="utf-8").splitlines()
    assert lines
    tampered = json.loads(lines[0])
    tampered["payload"]["decision"] = "forged"
    lines[0] = json.dumps(tampered)
    chain.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(LedgerStoreError, match="verification"):
        attach_store(TradingDesk(), store_dir, new=False)


def test_new_store_does_not_clobber(tmp_path: Path) -> None:
    store_dir = tmp_path / "ledger"
    run_paper_session(store_path=store_dir, new_store=True)
    with pytest.raises(LedgerStoreError, match="already exists"):
        LedgerStore.open(store_dir, new=True)
    with pytest.raises(LedgerStoreError, match="not found"):
        LedgerStore.open(tmp_path / "missing", new=False)


def test_default_path_gitignored() -> None:
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "/data/" in text
    assert str(DEFAULT_DATA_DIR).startswith("data")


def test_cli_two_invocations_then_calibrate(tmp_path: Path, capsys) -> None:
    store = tmp_path / "ledger"
    code1 = main(["paper-session", "--new-store", "--store", str(store)])
    out1 = capsys.readouterr().out
    assert code1 == 0
    first = json.loads(out1)
    assert first["persisted"] is True
    assert first["ledger_verified"] is True
    code2 = main(["paper-session", "--store", str(store)])
    out2 = capsys.readouterr().out
    assert code2 == 0
    second = json.loads(out2)
    assert second["ledger_entry_count"] > first["ledger_entry_count"]
    assert second["books"]["closed_trade_count"] == 2
    code3 = main(["calibrate", "--store", str(store)])
    out3 = capsys.readouterr().out
    assert code3 == 0
    cal = json.loads(out3)
    assert cal["sample_setups"] >= 6
    assert cal["ledger_verified"] is True
    assert cal["live_trading_authority"] is False


def test_cli_corrupt_store_refused(tmp_path: Path, capsys) -> None:
    store = tmp_path / "ledger"
    assert main(["paper-session", "--new-store", "--store", str(store)]) == 0
    capsys.readouterr()
    chain = store / CHAIN_NAME
    chain.write_text(chain.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    code = main(["paper-session", "--store", str(store)])
    captured = capsys.readouterr()
    assert code == 1
    assert '"refused": true' in captured.out
    assert '"live_trading_authority": false' in captured.out
