"""Phase 1 paper-trading duration tracking. Fixture/sim only. No live broker."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

from astra_desk.contract import EXIT_ID, load_contract
from astra_desk.desk import TradingDesk
from astra_desk.errors import LiveTradingDisabled, PaperTradingError, WalkForwardError
from astra_desk.types import (
    DeskMode,
    ExitOutput,
    PaperPeriodReport,
    Phase1Progress,
    RoundDecision,
    RoundResult,
    WalkSettlement,
)
from astra_desk.walk_forward import (
    PHASE0_EXIT_RULE,
    WalkSetup,
    evaluate_phase0_exit,
    parse_walk_setup,
    resolve_settlement,
)

PAPER_SCHEMA = "astra-desk/paper-period-v1"
SHIPPED_FIXTURE_NAME = "phase1_sample.json"
LIVE_FIXTURE_MODES = frozenset(
    {
        DeskMode.LIVE_WITH_HUMAN_CHECKPOINT.value,
        DeskMode.AUTONOMOUS.value,
        "live",
    }
)


@dataclass(frozen=True)
class PaperDay:
    session_date: str
    setups: tuple[WalkSetup, ...]


@dataclass(frozen=True)
class PaperFixture:
    days: tuple[PaperDay, ...]
    path: Path | None = None


def default_paper_fixture_path() -> Path:
    env = os.environ.get("ASTRA_DESK_PAPER_FIXTURE")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "fixtures" / "paper" / SHIPPED_FIXTURE_NAME,
        Path.cwd() / "fixtures" / "paper" / SHIPPED_FIXTURE_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise PaperTradingError(
        "cannot locate fixtures/paper/phase1_sample.json; set ASTRA_DESK_PAPER_FIXTURE"
    )


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PaperTradingError(f"{label} must be a mapping")
    return value


def _parse_iso_date(value: str, *, label: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise PaperTradingError(f"{label} must be YYYY-MM-DD") from exc


def _expand_session_dates(start: str, count: int) -> tuple[str, ...]:
    if count < 1:
        raise PaperTradingError("session_days must be >= 1")
    origin = date.fromisoformat(start)
    return tuple((origin + timedelta(days=offset)).isoformat() for offset in range(count))


def _parse_day(raw: Mapping[str, Any]) -> PaperDay:
    session_date = _parse_iso_date(str(raw.get("session_date") or ""), label="session_date")
    setups_raw = raw.get("setups") or []
    if not isinstance(setups_raw, list):
        raise PaperTradingError(f"{session_date}: setups must be a list")
    try:
        setups = tuple(parse_walk_setup(_require_mapping(item, "setup")) for item in setups_raw)
    except WalkForwardError as exc:
        raise PaperTradingError(str(exc)) from exc
    return PaperDay(session_date=session_date, setups=setups)


def load_paper_fixture(path: Path | None = None) -> PaperFixture:
    fixture_path = path or default_paper_fixture_path()
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperTradingError(f"failed to read paper fixture {fixture_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PaperTradingError("paper fixture must be a JSON object")
    if data.get("schema") != PAPER_SCHEMA:
        raise PaperTradingError(f"unsupported paper fixture schema: {data.get('schema')!r}")
    mode = data.get("mode", DeskMode.PAPER_TRADING.value)
    if mode in LIVE_FIXTURE_MODES:
        raise LiveTradingDisabled(f"paper fixture mode={mode} is not allowed")
    if mode != DeskMode.PAPER_TRADING.value:
        raise PaperTradingError(f"paper fixture mode must be paper_trading, got {mode}")

    if data.get("days"):
        days_raw = data["days"]
        if not isinstance(days_raw, list) or not days_raw:
            raise PaperTradingError("paper fixture days must be a non-empty list")
        days = tuple(_parse_day(_require_mapping(item, "day")) for item in days_raw)
        return PaperFixture(days=days, path=fixture_path)

    start = _parse_iso_date(str(data.get("start_date") or ""), label="start_date")
    try:
        session_days = int(data["session_days"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PaperTradingError("session_days is required when days is omitted") from exc
    daily_raw = data.get("daily_setups")
    if not isinstance(daily_raw, list):
        raise PaperTradingError("daily_setups must be a list")
    try:
        template = tuple(parse_walk_setup(_require_mapping(item, "setup")) for item in daily_raw)
    except WalkForwardError as exc:
        raise PaperTradingError(str(exc)) from exc
    days = tuple(
        PaperDay(session_date=session, setups=template)
        for session in _expand_session_dates(start, session_days)
    )
    return PaperFixture(days=days, path=fixture_path)


def limit_paper_days(fixture: PaperFixture, count: int) -> PaperFixture:
    if count < 1:
        raise PaperTradingError("--days must be >= 1")
    return PaperFixture(days=fixture.days[:count], path=fixture.path)


def _play_setups(
    desk: TradingDesk, setups: tuple[WalkSetup, ...]
) -> tuple[list[RoundResult], list[WalkSettlement | None], bool, str | None]:
    rounds: list[RoundResult] = []
    settlements: list[WalkSettlement | None] = []
    for setup in setups:
        result = desk.run_round(setup.fixture)
        rounds.append(result)
        if result.decision is RoundDecision.HALT or result.kill_switch.halted:
            settlements.append(None)
            return rounds, settlements, True, result.kill_switch.reason or result.gate.reason
        exit_out = result.agent_outputs.get(EXIT_ID)
        stop_price = exit_out.stop_price if isinstance(exit_out, ExitOutput) else None
        take_profits = (
            exit_out.take_profit_levels if isinstance(exit_out, ExitOutput) else None
        )
        try:
            settlement = resolve_settlement(
                setup,
                executed=result.executed,
                size_usd=result.final_size_usd,
                stop_price=stop_price,
                take_profit_levels=take_profits,
            )
        except WalkForwardError as exc:
            raise PaperTradingError(str(exc)) from exc
        settlements.append(settlement)
        if settlement is not None:
            desk.settle_paper(
                round_id=result.round_id,
                realized_pnl=settlement.realized_pnl,
                detail={
                    "setup_id": setup.fixture.setup_id,
                    "reason": settlement.reason,
                    "exit_price": settlement.exit_price,
                    "session": True,
                },
            )
            if desk.kill_switch.halted:
                return rounds, settlements, True, desk.kill_switch.state().reason
    return rounds, settlements, False, None


class PaperPeriod:
    """Count unique paper session days toward contract duration_days (21)."""

    def __init__(self, desk: TradingDesk | None = None) -> None:
        self.desk = desk or TradingDesk(mode=DeskMode.PAPER_TRADING)
        if self.desk.mode.is_live:
            raise LiveTradingDisabled(
                f"paper period refuses live mode={self.desk.mode.value}"
            )
        if self.desk.mode is not DeskMode.PAPER_TRADING:
            raise PaperTradingError(
                "paper period requires mode=paper_trading; "
                f"got {self.desk.mode.value}"
            )
        required = int(self.desk.contract.paper_trading_period_days)
        phase1_days = int(self.desk.contract.raw["rollout"]["phase_1"]["duration_days"])
        if required != phase1_days:
            raise PaperTradingError("contract phase_1 duration drifted from evaluation days")
        self.days_required = required
        self._session_dates: list[str] = []
        self._halt_reason: str | None = None

    def progress(self) -> Phase1Progress:
        halted = self.desk.kill_switch.halted
        days = len(self._session_dates)
        return Phase1Progress(
            days_completed=days,
            days_required=self.days_required,
            duration_complete=days >= self.days_required and not halted,
            halted=halted,
            halt_reason=self.desk.kill_switch.state().reason or self._halt_reason,
            session_dates=tuple(self._session_dates),
        )

    def advance(
        self, session_date: str, setups: tuple[WalkSetup, ...] = ()
    ) -> Phase1Progress:
        """Simulate one paper session day through the existing desk."""
        session_date = _parse_iso_date(session_date, label="session_date")
        if self.desk.kill_switch.halted:
            raise PaperTradingError("kill switch halted; reenable is manual_human_only")
        if session_date in self._session_dates:
            raise PaperTradingError(f"session_date {session_date} already recorded")
        if self._session_dates and session_date < self._session_dates[-1]:
            raise PaperTradingError("session dates must be non-decreasing")
        self.desk.books.begin_session_day()
        _rounds, _settlements, halted, reason = _play_setups(self.desk, setups)
        self._session_dates.append(session_date)
        if halted:
            self._halt_reason = reason
        self.desk.ledger.append(
            f"paper-day-{session_date}",
            {
                "kind": "paper_day",
                "session_date": session_date,
                "setups": len(setups),
                "rounds": len(_rounds),
                "halted": halted,
                "days_completed": len(self._session_dates),
                "days_required": self.days_required,
                "live_trading_authority": False,
            },
        )
        return self.progress()

    def run(self, fixture: PaperFixture) -> PaperPeriodReport:
        for day in fixture.days:
            if self.desk.kill_switch.halted:
                break
            self.advance(day.session_date, day.setups)
        snap = self.desk.books.snapshot()
        phase0 = evaluate_phase0_exit(self.desk.books, rule=PHASE0_EXIT_RULE)
        progress = self.progress()
        summary = {
            "kind": "paper_period_summary",
            "mode": self.desk.mode.value,
            "days_required": progress.days_required,
            "days_completed": progress.days_completed,
            "duration_complete": progress.duration_complete,
            "halted": progress.halted,
            "halt_reason": progress.halt_reason,
            "session_dates": list(progress.session_dates),
            "phase_0_exit_criteria": {
                "met": phase0.met,
                "rule": phase0.rule,
                "expectancy_per_setup": phase0.expectancy_per_setup,
                "max_drawdown_pct": phase0.max_drawdown_pct,
                "closed_trade_count": phase0.closed_trade_count,
                "reasons": list(phase0.reasons),
            },
            "accounting": snap.ledger_fields(),
            "live_trading_authority": False,
        }
        self.desk.ledger.append("paper-period-summary", summary)
        return PaperPeriodReport(
            mode=self.desk.mode,
            fixture_path=str(fixture.path) if fixture.path else None,
            days_required=progress.days_required,
            days_completed=progress.days_completed,
            duration_complete=progress.duration_complete,
            halted=progress.halted,
            halt_reason=progress.halt_reason,
            session_dates=progress.session_dates,
            days_processed=len(progress.session_dates),
            accounting=snap,
            phase_0_exit_criteria=phase0,
            ledger_entry_count=len(self.desk.ledger),
            live_trading_authority=False,
        )


def run_shipped_paper_period() -> PaperPeriodReport:
    load_contract()
    return PaperPeriod().run(load_paper_fixture())
