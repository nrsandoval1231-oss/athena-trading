"""Phase 2 human-checkpoint scaffolding.

Checkpointed PAPER (or simulated) deploys. Not a live broker, not Alpaca,
not live money. live_trading_authority stays false. Phase 3 autonomous
still raises.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from astra_desk.contract import EXIT_ID, load_contract
from astra_desk.desk import TradingDesk
from astra_desk.errors import CheckpointError, LiveTradingDisabled, WalkForwardError
from astra_desk.ledger import canonical_hash
from astra_desk.types import (
    DeskMode,
    ExitOutput,
    HumanApproval,
    HumanCheckpointReport,
    Phase2Progress,
    RoundDecision,
    RoundResult,
    WalkSettlement,
)
from astra_desk.walk_forward import (
    WalkSetup,
    parse_walk_setup,
    resolve_settlement,
)

CHECKPOINT_SCHEMA = "astra-desk/human-checkpoint-v1"
SHIPPED_FIXTURE_NAME = "phase2_sample.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
LIVE_FIXTURE_MODES = frozenset(
    {
        DeskMode.AUTONOMOUS.value,
        "live",
    }
)
APPROVAL_FIELDS = (
    "approved_by",
    "approved_at",
    "setup_id",
    "rationale_hash",
    "decision",
)


@dataclass(frozen=True)
class CheckpointSetup:
    setup: WalkSetup
    approval: HumanApproval | None = None


@dataclass(frozen=True)
class CheckpointFixture:
    setups: tuple[CheckpointSetup, ...]
    path: Path | None = None


def default_checkpoint_fixture_path() -> Path:
    env = os.environ.get("ASTRA_DESK_CHECKPOINT_FIXTURE")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "fixtures" / "checkpoint" / SHIPPED_FIXTURE_NAME,
        Path.cwd() / "fixtures" / "checkpoint" / SHIPPED_FIXTURE_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise CheckpointError(
        "cannot locate fixtures/checkpoint/phase2_sample.json; "
        "set ASTRA_DESK_CHECKPOINT_FIXTURE"
    )


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CheckpointError(f"{label} must be a mapping")
    return value


def _parse_approved_at(value: str) -> str:
    text = str(value).strip()
    if not text:
        raise CheckpointError("approved_at is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CheckpointError("approved_at must be timezone-aware ISO-8601") from exc
    if parsed.tzinfo is None:
        raise CheckpointError("approved_at must be timezone-aware ISO-8601")
    return parsed.isoformat()


def make_approval(
    *,
    approved_by: str,
    approved_at: str,
    setup_id: str,
    rationale_hash: str,
    decision: str = "approve",
) -> HumanApproval:
    approved_by = str(approved_by).strip()
    setup_id = str(setup_id).strip()
    rationale_hash = str(rationale_hash).strip().lower()
    decision = str(decision).strip()
    approved_at = _parse_approved_at(approved_at)
    if not approved_by:
        raise CheckpointError("approved_by is required")
    if not setup_id:
        raise CheckpointError("setup_id is required")
    if not HEX64.match(rationale_hash):
        raise CheckpointError("rationale_hash must be a 64-char sha256 hex digest")
    if decision != "approve":
        raise CheckpointError("approval.decision must be 'approve'")
    body = {
        "approved_by": approved_by,
        "approved_at": approved_at,
        "setup_id": setup_id,
        "rationale_hash": rationale_hash,
        "decision": decision,
    }
    return HumanApproval(
        approved_by=approved_by,
        approved_at=approved_at,
        setup_id=setup_id,
        rationale_hash=rationale_hash,
        decision=decision,
        record_hash=canonical_hash(body),
    )


def parse_approval(raw: Any, *, expected_setup_id: str | None = None) -> HumanApproval:
    """Parse a structured approval. A boolean `approved` flag is not enough."""
    if raw is True or raw is False:
        raise CheckpointError("boolean approval is not sufficient")
    block = _require_mapping(raw, "approval")
    if set(block.keys()) <= {"approved"}:
        raise CheckpointError("boolean approval is not sufficient")
    missing = [field for field in APPROVAL_FIELDS if not str(block.get(field) or "").strip()]
    if missing:
        raise CheckpointError(f"approval missing fields: {missing}")
    approval = make_approval(
        approved_by=str(block["approved_by"]),
        approved_at=str(block["approved_at"]),
        setup_id=str(block["setup_id"]),
        rationale_hash=str(block["rationale_hash"]),
        decision=str(block["decision"]),
    )
    if expected_setup_id is not None and approval.setup_id != expected_setup_id:
        raise CheckpointError(
            f"approval.setup_id {approval.setup_id!r} does not match {expected_setup_id!r}"
        )
    return approval


def bind_approval(
    desk: TradingDesk,
    setup: WalkSetup,
    *,
    approved_by: str,
    approved_at: str | None = None,
) -> HumanApproval:
    """Attach a structured approval to the current proposal hash (sim human)."""
    stamp = approved_at or datetime.now(timezone.utc).isoformat()
    return make_approval(
        approved_by=approved_by,
        approved_at=stamp,
        setup_id=setup.fixture.setup_id,
        rationale_hash=desk.proposal_hash(setup.fixture),
    )


def _parse_setup_item(raw: Mapping[str, Any]) -> CheckpointSetup:
    payload = dict(raw)
    approval_raw = payload.pop("approval", None)
    try:
        setup = parse_walk_setup(payload)
    except WalkForwardError as exc:
        raise CheckpointError(str(exc)) from exc
    approval = None
    if approval_raw is not None:
        approval = parse_approval(approval_raw, expected_setup_id=setup.fixture.setup_id)
    return CheckpointSetup(setup=setup, approval=approval)


def load_checkpoint_fixture(path: Path | None = None) -> CheckpointFixture:
    fixture_path = path or default_checkpoint_fixture_path()
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"failed to read checkpoint fixture {fixture_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CheckpointError("checkpoint fixture must be a JSON object")
    if data.get("schema") != CHECKPOINT_SCHEMA:
        raise CheckpointError(f"unsupported checkpoint fixture schema: {data.get('schema')!r}")
    mode = data.get("mode", DeskMode.LIVE_WITH_HUMAN_CHECKPOINT.value)
    if mode in LIVE_FIXTURE_MODES:
        raise LiveTradingDisabled(f"checkpoint fixture mode={mode} is not allowed")
    if mode != DeskMode.LIVE_WITH_HUMAN_CHECKPOINT.value:
        raise CheckpointError(
            f"checkpoint fixture mode must be live_with_human_checkpoint, got {mode}"
        )

    if data.get("setups"):
        setups_raw = data["setups"]
        if not isinstance(setups_raw, list) or not setups_raw:
            raise CheckpointError("checkpoint fixture setups must be a non-empty list")
        setups = tuple(_parse_setup_item(_require_mapping(item, "setup")) for item in setups_raw)
        return CheckpointFixture(setups=setups, path=fixture_path)

    try:
        trade_count = int(data["trade_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CheckpointError("trade_count is required when setups is omitted") from exc
    if trade_count < 1:
        raise CheckpointError("trade_count must be >= 1")
    template = _require_mapping(data.get("setup_template"), "setup_template")
    prefix = str(data.get("setup_id_prefix") or "cp")
    expanded: list[CheckpointSetup] = []
    for index in range(1, trade_count + 1):
        item = dict(template)
        item["setup_id"] = f"{prefix}-{index:03d}"
        expanded.append(_parse_setup_item(item))
    return CheckpointFixture(setups=tuple(expanded), path=fixture_path)


def limit_checkpoint_trades(fixture: CheckpointFixture, count: int) -> CheckpointFixture:
    if count < 1:
        raise CheckpointError("--trades must be >= 1")
    return CheckpointFixture(setups=fixture.setups[:count], path=fixture.path)


def _settle(
    desk: TradingDesk, setup: WalkSetup, result: RoundResult
) -> WalkSettlement | None:
    exit_out = result.agent_outputs.get(EXIT_ID)
    stop_price = exit_out.stop_price if isinstance(exit_out, ExitOutput) else None
    take_profits = exit_out.take_profit_levels if isinstance(exit_out, ExitOutput) else None
    try:
        settlement = resolve_settlement(
            setup,
            executed=result.executed,
            size_usd=result.final_size_usd,
            stop_price=stop_price,
            take_profit_levels=take_profits,
        )
    except WalkForwardError as exc:
        raise CheckpointError(str(exc)) from exc
    if settlement is not None:
        desk.settle_paper(
            round_id=result.round_id,
            realized_pnl=settlement.realized_pnl,
            detail={
                "setup_id": setup.fixture.setup_id,
                "reason": settlement.reason,
                "exit_price": settlement.exit_price,
                "checkpoint": True,
            },
        )
    return settlement


class HumanCheckpointSequence:
    """Count approved paper deploys toward contract human_checkpoint_trades (25)."""

    def __init__(
        self,
        desk: TradingDesk | None = None,
        *,
        approver: str | None = None,
    ) -> None:
        self.desk = desk or TradingDesk(mode=DeskMode.LIVE_WITH_HUMAN_CHECKPOINT)
        if self.desk.mode.is_live:
            raise LiveTradingDisabled(
                f"human checkpoint refuses live/autonomous mode={self.desk.mode.value}"
            )
        if self.desk.mode is not DeskMode.LIVE_WITH_HUMAN_CHECKPOINT:
            raise CheckpointError(
                "human checkpoint requires mode=live_with_human_checkpoint; "
                f"got {self.desk.mode.value}"
            )
        required = int(self.desk.contract.human_checkpoint_trades)
        phase2_trades = int(self.desk.contract.raw["rollout"]["phase_2"]["trades"])
        if required != phase2_trades:
            raise CheckpointError("contract phase_2 trades drifted from evaluation trades")
        self.trades_required = required
        self.approver = str(approver).strip() if approver else None
        if approver is not None and not self.approver:
            raise CheckpointError("approver identity must be a non-empty string")
        self._trades_completed = 0
        self._halt_reason: str | None = None

    def progress(self) -> Phase2Progress:
        halted = self.desk.kill_switch.halted
        return Phase2Progress(
            trades_completed=self._trades_completed,
            trades_required=self.trades_required,
            trade_count_complete=self._trades_completed >= self.trades_required and not halted,
            halted=halted,
            halt_reason=self.desk.kill_switch.state().reason or self._halt_reason,
        )

    def _resolve_approval(
        self, item: CheckpointSetup, *, approval: HumanApproval | None
    ) -> HumanApproval | None:
        if approval is not None:
            return approval
        if item.approval is not None:
            return item.approval
        if self.approver:
            return bind_approval(self.desk, item.setup, approved_by=self.approver)
        return None

    def run_setup(
        self,
        item: CheckpointSetup,
        *,
        approval: HumanApproval | None = None,
    ) -> RoundResult:
        if self.desk.kill_switch.halted:
            raise CheckpointError("kill switch halted; reenable is manual_human_only")
        if self._trades_completed >= self.trades_required:
            raise CheckpointError("phase_2 trade count already complete")
        resolved = self._resolve_approval(item, approval=approval)
        result = self.desk.run_round(item.setup.fixture, approval=resolved)
        if result.decision is RoundDecision.HALT or result.kill_switch.halted:
            self._halt_reason = result.kill_switch.reason or result.gate.reason
            return result
        settlement = _settle(self.desk, item.setup, result)
        if result.executed and result.checkpoint is not None and result.checkpoint.approved:
            self._trades_completed += 1
        if settlement is not None and self.desk.kill_switch.halted:
            self._halt_reason = self.desk.kill_switch.state().reason
        self.desk.ledger.append(
            f"checkpoint-{item.setup.fixture.setup_id}",
            {
                "kind": "human_checkpoint_trade",
                "setup_id": item.setup.fixture.setup_id,
                "executed": result.executed,
                "approved": bool(result.checkpoint and result.checkpoint.approved),
                "trades_completed": self._trades_completed,
                "trades_required": self.trades_required,
                "live_trading_authority": False,
            },
        )
        return result

    def run(self, fixture: CheckpointFixture) -> HumanCheckpointReport:
        processed = 0
        for item in fixture.setups:
            if self.desk.kill_switch.halted:
                break
            if self._trades_completed >= self.trades_required:
                break
            self.run_setup(item)
            processed += 1
        snap = self.desk.books.snapshot()
        progress = self.progress()
        summary = {
            "kind": "human_checkpoint_summary",
            "mode": self.desk.mode.value,
            "trades_required": progress.trades_required,
            "trades_completed": progress.trades_completed,
            "trade_count_complete": progress.trade_count_complete,
            "halted": progress.halted,
            "halt_reason": progress.halt_reason,
            "setups_total": len(fixture.setups),
            "setups_processed": processed,
            "accounting": snap.ledger_fields(),
            "live_trading_authority": False,
            "reenable": "manual_human_only",
            "note": (
                "Phase 2 is checkpointed paper/sim deploys. "
                "No live broker and no live trading authority."
            ),
        }
        self.desk.ledger.append("human-checkpoint-summary", summary)
        return HumanCheckpointReport(
            mode=self.desk.mode,
            fixture_path=str(fixture.path) if fixture.path else None,
            trades_required=progress.trades_required,
            trades_completed=progress.trades_completed,
            trade_count_complete=progress.trade_count_complete,
            halted=progress.halted,
            halt_reason=progress.halt_reason,
            setups_total=len(fixture.setups),
            setups_processed=processed,
            accounting=snap,
            ledger_entry_count=len(self.desk.ledger),
            live_trading_authority=False,
            reenable="manual_human_only",
        )


def run_shipped_checkpoint(*, approver: str | None = None) -> HumanCheckpointReport:
    load_contract()
    return HumanCheckpointSequence(approver=approver).run(load_checkpoint_fixture())
