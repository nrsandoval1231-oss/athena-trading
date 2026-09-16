"""Operator-facing paper session. Reuses tape, desk, walk-forward, broker, ledger.

Pipeline: fixture bars → features → rule agents → weighted gate → risk /
kill switch → paper broker → append-only ledger.

Default is fully offline (fixture tape + fixture broker). Alpaca paper is
opt-in and fail-closed without keys. No LIVE authority.
"""

from __future__ import annotations

from pathlib import Path

from astra_desk.desk import TradingDesk
from astra_desk.errors import LiveTradingDisabled
from astra_desk.kill_switch import KillSwitch
from astra_desk.store import LedgerStore, attach_store
from astra_desk.tape import load_tape_fixture
from astra_desk.types import (
    DeskMode,
    PaperSessionReport,
    PaperSessionRound,
    RoundDecision,
)
from astra_desk.walk_forward import WalkFixture, WalkForwardReplay, WalkSetup

SESSION_KIND = "paper_session"


def _broker_name(desk: TradingDesk) -> str:
    return str(getattr(desk.broker, "name", "fixture"))


def run_paper_session(
    *,
    broker_kind: str = "fixture",
    fixture_path: Path | None = None,
    desk: TradingDesk | None = None,
    store_path: Path | str | None = None,
    new_store: bool = False,
) -> PaperSessionReport:
    """Run one paper session over the shipped (or given) tape fixture."""
    if desk is None:
        desk = TradingDesk(
            mode=DeskMode.WALK_FORWARD_REPLAY,
            broker_kind=broker_kind,
        )
    if desk.mode.is_live:
        raise LiveTradingDisabled(
            f"paper-session refuses live mode={desk.mode.value}"
        )
    store: LedgerStore | None = None
    if store_path is not None or new_store:
        store = attach_store(desk, store_path, new=new_store)
    tape = load_tape_fixture(fixture_path)
    walk = WalkFixture(
        setups=tuple(
            WalkSetup(
                fixture=item.fixture,
                bars=item.bars,
                settlement_pnl=item.settlement_pnl,
            )
            for item in tape.setups
        ),
        path=tape.path,
    )
    walk_report = WalkForwardReplay(desk).run(walk)

    rounds: list[PaperSessionRound] = []
    deploys = refuses = vetoes = halts = fills = 0
    for result, settlement in zip(
        walk_report.rounds, walk_report.settlements, strict=True
    ):
        if result.decision is RoundDecision.DEPLOY:
            deploys += 1
        elif result.decision is RoundDecision.REFUSE:
            refuses += 1
        elif result.decision is RoundDecision.VETO:
            vetoes += 1
        elif result.decision is RoundDecision.HALT:
            halts += 1
        order = result.broker_order
        if order is not None and order.status == "filled":
            fills += 1
        rounds.append(
            PaperSessionRound(
                round_id=result.round_id,
                setup_id=result.setup_id,
                decision=result.decision.value,
                executed=result.executed,
                final_size_usd=result.final_size_usd,
                broker=order.broker if order is not None else None,
                broker_status=order.status if order is not None else None,
                settlement=settlement.reason if settlement is not None else None,
                realized_pnl=settlement.realized_pnl if settlement is not None else None,
            )
        )

    verified, _reason = desk.ledger.verify()
    summary = {
        "kind": SESSION_KIND,
        "tape_source": tape.source,
        "broker": _broker_name(desk),
        "mode": desk.mode.value,
        "setups_total": walk_report.setups_total,
        "setups_processed": walk_report.setups_processed,
        "deploys": deploys,
        "refuses": refuses,
        "vetoes": vetoes,
        "halts": halts,
        "fills": fills,
        "halted": walk_report.halted,
        "halt_reason": walk_report.halt_reason,
        "ledger_verified": verified,
        "accounting": desk.books.snapshot().ledger_fields(),
        "live_trading_authority": False,
        "reenable": KillSwitch.REENABLE,
        "kill_switch_action": KillSwitch.ACTION,
        "store_path": str(store.path) if store is not None else None,
        "persisted": store is not None,
    }
    desk.ledger.append("paper-session-summary", summary)
    snap = desk.books.snapshot()
    verified, _reason = desk.ledger.verify()
    return PaperSessionReport(
        kind=SESSION_KIND,
        tape_source=tape.source,
        broker=_broker_name(desk),
        mode=desk.mode,
        fixture_path=str(tape.path) if tape.path else None,
        setups_total=walk_report.setups_total,
        setups_processed=walk_report.setups_processed,
        deploys=deploys,
        refuses=refuses,
        vetoes=vetoes,
        halts=halts,
        fills=fills,
        halted=walk_report.halted,
        halt_reason=walk_report.halt_reason,
        rounds=tuple(rounds),
        accounting=snap,
        ledger_entry_count=len(desk.ledger),
        ledger_verified=verified,
        live_trading_authority=False,
        reenable=KillSwitch.REENABLE,
        kill_switch_action=KillSwitch.ACTION,
        store_path=str(store.path) if store is not None else None,
        persisted=store is not None,
    )
