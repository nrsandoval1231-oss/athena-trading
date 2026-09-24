"""Read-only operator report from the durable ledger store.

No broker calls. No LIVE authority. Requires an on-disk store for truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from astra_desk.accounting import CapitalBooks
from astra_desk.contract import load_contract
from astra_desk.errors import LedgerStoreError
from astra_desk.kill_switch import KillSwitch
from astra_desk.ledger import AppendOnlyLedger
from astra_desk.store import LedgerStore
from astra_desk.types import Position

REPORT_KIND = "operator_report"
STORE_REQUIRED_REASON = (
    "report requires --store <dir> (durable ledger is the source of truth; "
    "no in-memory demo)"
)


@dataclass(frozen=True)
class OperatorReport:
    kind: str
    store_path: str
    books: Mapping[str, Any]
    ledger_entry_count: int
    closed_trade_count: int
    ledger_verified: bool
    kill_switch: Mapping[str, Any]
    weights: Mapping[str, Any]
    phase_progress: Mapping[str, Any]
    live_trading_authority: bool
    reenable: str
    kill_switch_action: str
    human_summary: str


def _unknown_phase(*, reason: str = "not_in_store") -> dict[str, Any]:
    return {"status": "unknown", "reason": reason}


def _phase_progress(
    ledger: AppendOnlyLedger, *, contract: Any
) -> dict[str, Any]:
    latest: dict[str, Mapping[str, Any]] = {}
    for entry in ledger.entries:
        payload = entry.payload
        if not isinstance(payload, Mapping):
            continue
        kind = payload.get("kind")
        if isinstance(kind, str):
            latest[kind] = payload

    phase_0 = _unknown_phase()
    walk = latest.get("walk_forward_summary")
    if isinstance(walk, Mapping) and isinstance(walk.get("exit_criteria"), Mapping):
        criteria = walk["exit_criteria"]
        phase_0 = {
            "status": "derived",
            "source": "walk_forward_summary",
            "exit_criteria_met": bool(criteria.get("met")),
            "rule": criteria.get("rule"),
            "reasons": list(criteria.get("reasons") or []),
        }

    phase_1 = _unknown_phase()
    paper = latest.get("paper_period_summary")
    if isinstance(paper, Mapping):
        phase_1 = {
            "status": "derived",
            "source": "paper_period_summary",
            "days_completed": paper.get("days_completed"),
            "days_required": paper.get("days_required", contract.paper_trading_period_days),
            "duration_complete": paper.get("duration_complete"),
        }

    phase_2 = _unknown_phase()
    check = latest.get("human_checkpoint_summary")
    if isinstance(check, Mapping):
        phase_2 = {
            "status": "derived",
            "source": "human_checkpoint_summary",
            "trades_completed": check.get("trades_completed"),
            "trades_required": check.get("trades_required", contract.human_checkpoint_trades),
            "trade_count_complete": check.get("trade_count_complete"),
        }

    phase_3 = _unknown_phase()
    auto = latest.get("autonomous_paper_summary")
    if isinstance(auto, Mapping):
        reqs = auto.get("requirements") if isinstance(auto.get("requirements"), Mapping) else {}
        phase_3 = {
            "status": "derived",
            "source": "autonomous_paper_summary",
            "rounds_processed": auto.get("rounds_processed"),
            "rounds_requested": auto.get("rounds_requested"),
            "requirements_met": reqs.get("met"),
            "halted": auto.get("halted"),
        }

    return {
        "phase_0": phase_0,
        "phase_1": phase_1,
        "phase_2": phase_2,
        "phase_3": phase_3,
        "targets": {
            "paper_trading_period_days": contract.paper_trading_period_days,
            "human_checkpoint_trades": contract.human_checkpoint_trades,
            "min_sample_live_setups": contract.min_sample_live_setups,
        },
    }


def _weights_block(snapshot: Mapping[str, Any], *, contract: Any) -> dict[str, Any]:
    baseline = {str(k): float(v) for k, v in contract.gate.conviction_weights.items()}
    raw = snapshot.get("conviction_weights")
    if isinstance(raw, Mapping) and raw:
        working = {str(k): float(v) for k, v in raw.items()}
        source = "snapshot"
    else:
        working = dict(baseline)
        source = "contract_baseline"
    seats = snapshot.get("disabled_seats")
    disabled = {str(k): str(v) for k, v in seats.items()} if isinstance(seats, Mapping) else {}
    return {
        "contract": baseline,
        "working": working,
        "working_equals_contract": working == baseline,
        "disabled_seats": disabled,
        "source": source,
    }


def format_human_summary(payload: Mapping[str, Any]) -> str:
    books = payload.get("books") if isinstance(payload.get("books"), Mapping) else {}
    ks = payload.get("kill_switch") if isinstance(payload.get("kill_switch"), Mapping) else {}
    weights = payload.get("weights") if isinstance(payload.get("weights"), Mapping) else {}
    phases = payload.get("phase_progress") if isinstance(payload.get("phase_progress"), Mapping) else {}
    lines = [
        "Astra Desk operator report",
        f"store: {payload.get('store_path')}",
        (
            f"equity: {books.get('equity')}  realized: {books.get('realized_pnl')}  "
            f"unrealized: {books.get('unrealized_pnl')}  "
            f"max_dd_pct: {books.get('max_drawdown_pct')}  "
            f"exposure_pct: {books.get('exposure_pct')}"
        ),
        (
            f"ledger: {payload.get('ledger_entry_count')} entries, "
            f"{payload.get('closed_trade_count')} closed trades, "
            f"verified={payload.get('ledger_verified')}"
        ),
        (
            f"kill switch: armed={ks.get('armed')} halted={ks.get('halted')} "
            f"reason={ks.get('reason')} reenable={payload.get('reenable')}"
        ),
        (
            f"weights: working_equals_contract={weights.get('working_equals_contract')} "
            f"source={weights.get('source')}"
        ),
        (
            "phase progress: "
            + ", ".join(
                f"{name}={block.get('status')}"
                for name, block in (
                    ("phase_0", phases.get("phase_0") if isinstance(phases.get("phase_0"), Mapping) else {}),
                    ("phase_1", phases.get("phase_1") if isinstance(phases.get("phase_1"), Mapping) else {}),
                    ("phase_2", phases.get("phase_2") if isinstance(phases.get("phase_2"), Mapping) else {}),
                    ("phase_3", phases.get("phase_3") if isinstance(phases.get("phase_3"), Mapping) else {}),
                )
            )
        ),
        f"live_trading_authority: {payload.get('live_trading_authority')}",
    ]
    return "\n".join(lines) + "\n"


def build_operator_report(store_path: str | Path) -> OperatorReport:
    """Load a durable store read-only and summarize books, ledger, and kill switch."""
    path = Path(store_path)
    try:
        store = LedgerStore.open(path, new=False)
    except LedgerStoreError as exc:
        if "not found" in str(exc):
            raise LedgerStoreError(
                f"ledger store not found at {path}; report is read-only and will not create it"
            ) from exc
        raise
    ledger, snapshot = store.load_verified_chain()
    verified, _reason = ledger.verify()
    if not verified:
        raise LedgerStoreError("ledger chain failed verification")

    contract = load_contract()
    books = CapitalBooks(contract.starting_equity, contract.currency)
    books_raw = snapshot.get("books")
    if isinstance(books_raw, Mapping):
        positions = tuple(
            Position(
                symbol=str(item["symbol"]),
                notional_usd=float(item["notional_usd"]),
                correlation_group=str(item["correlation_group"]),
                side=str(item.get("side") or "long"),
            )
            for item in (books_raw.get("positions") or [])
            if isinstance(item, Mapping)
        )
        books.restore_snapshot(
            realized_pnl=float(books_raw.get("realized_pnl") or 0.0),
            unrealized_pnl=float(books_raw.get("unrealized_pnl") or 0.0),
            peak_equity=float(books_raw.get("peak_equity") or contract.starting_equity),
            max_drawdown_pct=float(books_raw.get("max_drawdown_pct") or 0.0),
            day_start_equity=float(
                books_raw.get("day_start_equity") or contract.starting_equity
            ),
            closed_pnls=list(books_raw.get("closed_pnls") or []),
            positions=positions,
        )
    snap = books.snapshot()
    ks = KillSwitch(
        drawdown_pct=contract.kill_switch.drawdown_pct,
        daily_loss_limit_pct=contract.hard_limits.daily_loss_limit_pct,
    )
    ks_raw = snapshot.get("kill_switch")
    if isinstance(ks_raw, Mapping):
        ks.restore_snapshot(
            halted=bool(ks_raw.get("halted")),
            reason=ks_raw.get("reason"),
            armed=bool(ks_raw.get("armed", True)),
        )
        ks_status = "snapshot"
    else:
        ks_status = "unknown"
    ks_state = ks.state()
    books_fields = {
        **snap.ledger_fields(),
        "currency": snap.currency,
        "starting_equity": snap.starting_equity,
        "closed_trade_count": snap.closed_trade_count,
    }
    payload = {
        "kind": REPORT_KIND,
        "store_path": str(store.path),
        "books": books_fields,
        "ledger_entry_count": len(ledger),
        "closed_trade_count": snap.closed_trade_count,
        "ledger_verified": verified,
        "kill_switch": {
            "armed": ks_state.armed,
            "halted": ks_state.halted,
            "reason": ks_state.reason,
            "source": ks_status,
        },
        "weights": _weights_block(snapshot, contract=contract),
        "phase_progress": _phase_progress(ledger, contract=contract),
        "live_trading_authority": False,
        "reenable": KillSwitch.REENABLE,
        "kill_switch_action": KillSwitch.ACTION,
    }
    human = format_human_summary(payload)
    return OperatorReport(
        kind=REPORT_KIND,
        store_path=str(store.path),
        books=books_fields,
        ledger_entry_count=len(ledger),
        closed_trade_count=snap.closed_trade_count,
        ledger_verified=verified,
        kill_switch=payload["kill_switch"],
        weights=payload["weights"],
        phase_progress=payload["phase_progress"],
        live_trading_authority=False,
        reenable=KillSwitch.REENABLE,
        kill_switch_action=KillSwitch.ACTION,
        human_summary=human,
    )


def report_to_dict(report: OperatorReport) -> dict[str, Any]:
    return {
        "kind": report.kind,
        "store_path": report.store_path,
        "books": dict(report.books),
        "equity": report.books.get("equity"),
        "realized_pnl": report.books.get("realized_pnl"),
        "unrealized_pnl": report.books.get("unrealized_pnl"),
        "max_drawdown_pct": report.books.get("max_drawdown_pct"),
        "exposure_pct": report.books.get("exposure_pct"),
        "ledger_entry_count": report.ledger_entry_count,
        "closed_trade_count": report.closed_trade_count,
        "ledger_verified": report.ledger_verified,
        "kill_switch": dict(report.kill_switch),
        "weights": dict(report.weights),
        "phase_progress": dict(report.phase_progress),
        "live_trading_authority": False,
        "reenable": report.reenable,
        "kill_switch_action": report.kill_switch_action,
        "human_summary": report.human_summary,
        "read_only": True,
        "broker_calls": False,
    }
