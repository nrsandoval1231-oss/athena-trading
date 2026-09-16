"""Phase 3 autonomous *paper* loop.

Runs only when kill_switch_armed and ledger_verified both hold. Not a live
broker, not live money. live_trading_authority stays false. Phase 2
human-checkpoint remains a separate path.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from astra_desk.contract import EXIT_ID, PHASE3_REQUIRES, load_contract
from astra_desk.desk import TradingDesk
from astra_desk.errors import AutonomousError, LiveTradingDisabled, WalkForwardError
from astra_desk.types import (
    AutonomousReport,
    DeskMode,
    ExitOutput,
    Phase3Requirements,
    RoundDecision,
    RoundResult,
    WalkSettlement,
)
from astra_desk.walk_forward import WalkSetup, parse_walk_setup, resolve_settlement

AUTONOMOUS_SCHEMA = "astra-desk/autonomous-v1"
SHIPPED_FIXTURE_NAME = "phase3_sample.json"
LIVE_FIXTURE_MODES = frozenset({"live", "live_broker"})


@dataclass(frozen=True)
class AutonomousFixture:
    setups: tuple[WalkSetup, ...]
    path: Path | None = None


def default_autonomous_fixture_path() -> Path:
    env = os.environ.get("ASTRA_DESK_AUTONOMOUS_FIXTURE")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "fixtures" / "autonomous" / SHIPPED_FIXTURE_NAME,
        Path.cwd() / "fixtures" / "autonomous" / SHIPPED_FIXTURE_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise AutonomousError(
        "cannot locate fixtures/autonomous/phase3_sample.json; "
        "set ASTRA_DESK_AUTONOMOUS_FIXTURE"
    )


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AutonomousError(f"{label} must be a mapping")
    return value


def evaluate_phase3_requirements(desk: TradingDesk) -> Phase3Requirements:
    reasons: list[str] = []
    spec = desk.contract.kill_switch
    armed, armed_reason = desk.kill_switch.armed_status()
    if spec.action != "flatten_all_and_halt":
        armed = False
        armed_reason = "kill_switch_action_not_flatten_all_and_halt"
    if spec.reenable != "manual_human_only":
        armed = False
        armed_reason = "kill_switch_reenable_not_manual_human_only"
    if not armed:
        reasons.append(armed_reason)
    verified, verified_reason = desk.ledger.verify()
    if not verified:
        reasons.append(f"ledger_integrity_failed:{verified_reason}")
    if tuple(desk.contract.phase_3_requires) != PHASE3_REQUIRES:
        reasons.append("phase_3_requires_drifted")
        armed = False
        verified = False
    return Phase3Requirements(
        kill_switch_armed=armed,
        ledger_verified=verified,
        met=not reasons,
        reasons=tuple(reasons),
    )


def load_autonomous_fixture(path: Path | None = None) -> AutonomousFixture:
    fixture_path = path or default_autonomous_fixture_path()
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutonomousError(f"failed to read autonomous fixture {fixture_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AutonomousError("autonomous fixture must be a JSON object")
    if data.get("schema") != AUTONOMOUS_SCHEMA:
        raise AutonomousError(f"unsupported autonomous fixture schema: {data.get('schema')!r}")
    mode = data.get("mode", DeskMode.AUTONOMOUS.value)
    if mode in LIVE_FIXTURE_MODES:
        raise LiveTradingDisabled(f"autonomous fixture mode={mode} is not allowed")
    if mode != DeskMode.AUTONOMOUS.value:
        raise AutonomousError(f"autonomous fixture mode must be autonomous, got {mode}")

    if data.get("setups"):
        setups_raw = data["setups"]
        if not isinstance(setups_raw, list) or not setups_raw:
            raise AutonomousError("autonomous fixture setups must be a non-empty list")
        try:
            setups = tuple(
                parse_walk_setup(_require_mapping(item, "setup")) for item in setups_raw
            )
        except WalkForwardError as exc:
            raise AutonomousError(str(exc)) from exc
        return AutonomousFixture(setups=setups, path=fixture_path)

    try:
        round_count = int(data["round_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AutonomousError("round_count is required when setups is omitted") from exc
    if round_count < 1:
        raise AutonomousError("round_count must be >= 1")
    template = _require_mapping(data.get("setup_template"), "setup_template")
    prefix = str(data.get("setup_id_prefix") or "auto")
    expanded: list[WalkSetup] = []
    for index in range(1, round_count + 1):
        item = dict(template)
        item["setup_id"] = f"{prefix}-{index:03d}"
        try:
            expanded.append(parse_walk_setup(item))
        except WalkForwardError as exc:
            raise AutonomousError(str(exc)) from exc
    return AutonomousFixture(setups=tuple(expanded), path=fixture_path)


def limit_autonomous_rounds(fixture: AutonomousFixture, count: int) -> AutonomousFixture:
    if count < 1:
        raise AutonomousError("--rounds must be >= 1")
    return AutonomousFixture(setups=fixture.setups[:count], path=fixture.path)


def _settle(desk: TradingDesk, setup: WalkSetup, result: RoundResult) -> WalkSettlement | None:
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
        raise AutonomousError(str(exc)) from exc
    if settlement is not None:
        desk.settle_paper(
            round_id=result.round_id,
            realized_pnl=settlement.realized_pnl,
            detail={
                "setup_id": setup.fixture.setup_id,
                "reason": settlement.reason,
                "exit_price": settlement.exit_price,
                "autonomous": True,
            },
        )
    return settlement


class AutonomousPaperLoop:
    """Autonomous paper rounds. No per-trade human approval. No live broker."""

    def __init__(self, desk: TradingDesk | None = None) -> None:
        self.desk = desk or TradingDesk(mode=DeskMode.AUTONOMOUS)
        if self.desk.mode.is_live:
            raise LiveTradingDisabled(
                f"autonomous paper refuses live broker mode={self.desk.mode.value}"
            )
        if self.desk.mode is not DeskMode.AUTONOMOUS:
            raise AutonomousError(
                "autonomous paper requires mode=autonomous; "
                f"got {self.desk.mode.value}"
            )
        self._halt_reason: str | None = None

    def requirements(self) -> Phase3Requirements:
        return evaluate_phase3_requirements(self.desk)

    def _refuse_unless_ready(self) -> Phase3Requirements:
        reqs = self.requirements()
        if not reqs.met:
            raise AutonomousError(
                "phase_3 refused: " + ", ".join(reqs.reasons) if reqs.reasons else "phase_3 refused"
            )
        return reqs

    def run_setup(self, setup: WalkSetup) -> RoundResult:
        if self.desk.kill_switch.halted:
            raise AutonomousError("kill switch halted; reenable is manual_human_only")
        self._refuse_unless_ready()
        result = self.desk.run_round(setup.fixture)
        if result.decision is RoundDecision.HALT or result.kill_switch.halted:
            self._halt_reason = result.kill_switch.reason or result.gate.reason
            return result
        _settle(self.desk, setup, result)
        if self.desk.kill_switch.halted:
            self._halt_reason = self.desk.kill_switch.state().reason
        return result

    def run(self, fixture: AutonomousFixture) -> AutonomousReport:
        reqs = self._refuse_unless_ready()
        processed = 0
        for setup in fixture.setups:
            if self.desk.kill_switch.halted:
                break
            self.run_setup(setup)
            processed += 1
            if self.desk.kill_switch.halted:
                break
        snap = self.desk.books.snapshot()
        reqs = self.requirements()
        summary = {
            "kind": "autonomous_paper_summary",
            "mode": self.desk.mode.value,
            "rounds_requested": len(fixture.setups),
            "rounds_processed": processed,
            "halted": self.desk.kill_switch.halted,
            "halt_reason": self.desk.kill_switch.state().reason or self._halt_reason,
            "requirements": {
                "kill_switch_armed": reqs.kill_switch_armed,
                "ledger_verified": reqs.ledger_verified,
                "met": reqs.met,
                "reasons": list(reqs.reasons),
            },
            "accounting": snap.ledger_fields(),
            "live_trading_authority": False,
            "reenable": "manual_human_only",
            "note": (
                "Phase 3 is autonomous paper under armed kill switch + verified "
                "ledger. No live broker and no live trading authority."
            ),
        }
        self.desk.ledger.append("autonomous-paper-summary", summary)
        return AutonomousReport(
            mode=self.desk.mode,
            fixture_path=str(fixture.path) if fixture.path else None,
            rounds_requested=len(fixture.setups),
            rounds_processed=processed,
            halted=self.desk.kill_switch.halted,
            halt_reason=self.desk.kill_switch.state().reason or self._halt_reason,
            requirements=self.requirements(),
            accounting=snap,
            ledger_entry_count=len(self.desk.ledger),
            live_trading_authority=False,
            reenable="manual_human_only",
        )


def run_shipped_autonomous() -> AutonomousReport:
    load_contract()
    return AutonomousPaperLoop().run(load_autonomous_fixture())
