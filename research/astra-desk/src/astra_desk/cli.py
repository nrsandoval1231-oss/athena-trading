"""CLI: paper-session, report, walk-forward, paper-period, checkpointed-paper, autonomous-paper, calibrate, tape.

No live broker.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from pathlib import Path

from astra_desk.autonomous import (
    AutonomousPaperLoop,
    limit_autonomous_rounds,
    load_autonomous_fixture,
)
from astra_desk.calibration import (
    CalibrationEngine,
    CalibrationReport,
    ingest_calibration_setups,
    load_calibration_fixture,
)
from astra_desk.broker import build_paper_broker
from astra_desk.desk import TradingDesk
from astra_desk.errors import (
    AutonomousError,
    BrokerCredentialsError,
    BrokerError,
    CalibrationError,
    LedgerStoreError,
    LiveTradingDisabled,
    MarketDataError,
    WalkForwardError,
)
from astra_desk.fixtures import SCENARIOS, deploy_fixture
from astra_desk.human_checkpoint import (
    HumanCheckpointSequence,
    limit_checkpoint_trades,
    load_checkpoint_fixture,
)
from astra_desk.paper_session import run_paper_session
from astra_desk.paper_trading import (
    PaperPeriod,
    limit_paper_days,
    load_paper_fixture,
)
from astra_desk.report import STORE_REQUIRED_REASON, build_operator_report, report_to_dict
from astra_desk.store import attach_store, default_store_path
from astra_desk.market_data import AlpacaDataFeed
from astra_desk.tape import (
    DERIVED_FEATURE_KEYS,
    EXPLICIT_FEATURE_KEYS,
    apply_fetched_bars,
    load_first_setup_raw,
    run_tape_round,
    run_tape_walk,
)
from astra_desk.types import (
    AutonomousReport,
    DeskMode,
    HumanCheckpointReport,
    PaperPeriodReport,
    PaperSessionReport,
    RoundResult,
    WalkForwardReport,
)
from astra_desk.walk_forward import WalkForwardReplay, load_walk_fixture, parse_walk_setup


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def result_to_dict(result: RoundResult) -> dict[str, Any]:
    return _jsonable(result)


def walk_report_to_dict(report: WalkForwardReport) -> dict[str, Any]:
    payload = _jsonable(report)
    payload["rounds"] = [
        {
            "round_id": item["round_id"],
            "setup_id": item["setup_id"],
            "regime": item["regime"],
            "decision": item["decision"],
            "executed": item["executed"],
            "final_size_usd": item["final_size_usd"],
            "ledger_entry_hash": item["ledger_entry_hash"],
        }
        for item in payload["rounds"]
    ]
    payload["accounting"] = {
        **report.accounting.ledger_fields(),
        "currency": report.accounting.currency,
        "starting_equity": report.accounting.starting_equity,
        "closed_trade_count": report.accounting.closed_trade_count,
    }
    return payload


def autonomous_report_to_dict(report: AutonomousReport) -> dict[str, Any]:
    payload = _jsonable(report)
    payload["progress"] = f"{report.rounds_processed}/{report.rounds_requested}"
    payload["kill_switch_armed"] = report.requirements.kill_switch_armed
    payload["ledger_verified"] = report.requirements.ledger_verified
    payload["reenable"] = "manual_human_only"
    payload["accounting"] = {
        **report.accounting.ledger_fields(),
        "currency": report.accounting.currency,
        "starting_equity": report.accounting.starting_equity,
        "closed_trade_count": report.accounting.closed_trade_count,
    }
    return payload


def checkpoint_report_to_dict(report: HumanCheckpointReport) -> dict[str, Any]:
    payload = _jsonable(report)
    payload["progress"] = f"{report.trades_completed}/{report.trades_required}"
    payload["reenable"] = "manual_human_only"
    payload["accounting"] = {
        **report.accounting.ledger_fields(),
        "currency": report.accounting.currency,
        "starting_equity": report.accounting.starting_equity,
        "closed_trade_count": report.accounting.closed_trade_count,
    }
    return payload


def paper_report_to_dict(report: PaperPeriodReport) -> dict[str, Any]:
    payload = _jsonable(report)
    payload["progress"] = f"{report.days_completed}/{report.days_required}"
    payload["reenable"] = "manual_human_only"
    payload["accounting"] = {
        **report.accounting.ledger_fields(),
        "currency": report.accounting.currency,
        "starting_equity": report.accounting.starting_equity,
        "closed_trade_count": report.accounting.closed_trade_count,
    }
    return payload


def paper_session_report_to_dict(report: PaperSessionReport) -> dict[str, Any]:
    payload = _jsonable(report)
    payload["kind"] = report.kind
    payload["live_trading_authority"] = False
    payload["reenable"] = report.reenable
    payload["kill_switch_action"] = report.kill_switch_action
    payload["books"] = {
        **report.accounting.ledger_fields(),
        "currency": report.accounting.currency,
        "starting_equity": report.accounting.starting_equity,
        "closed_trade_count": report.accounting.closed_trade_count,
    }
    payload["accounting"] = payload["books"]
    return payload


def calibrate_report_to_dict(report: CalibrationReport) -> dict[str, Any]:
    payload = _jsonable(report)
    payload["live_trading_authority"] = False
    payload["weights_update"] = report.weights_update
    return payload


def _tape_meta(tape) -> dict[str, Any]:
    derived = sorted(
        {key for item in tape.setups for key in item.derived_keys}
    )
    return {
        "source": tape.source,
        "fixture_path": str(tape.path) if tape.path else None,
        "derived_keys": derived or list(DERIVED_FEATURE_KEYS),
        "explicit_keys": list(EXPLICIT_FEATURE_KEYS),
        "live_trading_authority": False,
        "broker_port": False,
    }


def _refuse_payload(exc: Exception) -> dict[str, Any]:
    return {
        "refused": True,
        "reason": str(exc),
        "live_trading_authority": False,
    }


def _run_tape(args: argparse.Namespace) -> int:
    path = Path(args.fixture) if args.fixture else None
    try:
        if args.source == "alpaca":
            symbol = (args.symbol or "").strip()
            if not symbol:
                raise MarketDataError("--symbol is required with --source alpaca")
            feed = AlpacaDataFeed.from_env()
            bars = feed.fetch_bars(symbol)
            overlay = load_first_setup_raw(path)
            overlay["symbol"] = symbol
            explicit = overlay.get("explicit_features")
            if isinstance(explicit, dict):
                overlay["explicit_features"] = {
                    key: explicit[key]
                    for key in EXPLICIT_FEATURE_KEYS
                    if key in explicit
                }
            materialized = apply_fetched_bars(overlay, bars)
            walk = parse_walk_setup(materialized)
            result = TradingDesk(mode=DeskMode.WALK_FORWARD_REPLAY).run_round(
                walk.fixture
            )
            payload = {
                "kind": "tape_round",
                "source": "alpaca",
                "symbol": symbol,
                "derived_keys": list(DERIVED_FEATURE_KEYS),
                "explicit_keys": list(EXPLICIT_FEATURE_KEYS),
                "live_trading_authority": False,
                "broker_port": False,
                "round": result_to_dict(result),
            }
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")
            if result.decision.value == "halt":
                return 2
            return 0 if result.executed else 1
        if args.tape_round:
            tape, result = run_tape_round(path)
            payload = {
                "kind": "tape_round",
                **_tape_meta(tape),
                "round": result_to_dict(result),
            }
            json.dump(payload, sys.stdout, indent=2)
            sys.stdout.write("\n")
            if result.decision.value == "halt":
                return 2
            return 0 if result.executed else 1
        tape, report = run_tape_walk(path)
        payload = {
            "kind": "tape_walk",
            **_tape_meta(tape),
            "walk": walk_report_to_dict(report),
        }
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        if report.halted:
            return 2
        if report.exit_criteria.met:
            return 0
        return 1
    except (
        MarketDataError,
        BrokerCredentialsError,
        LiveTradingDisabled,
        WalkForwardError,
        BrokerError,
    ) as exc:
        json.dump(_refuse_payload(exc), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="astra-desk",
        description=(
            "Astra Desk Phase 0–3 — paper-session, report, walk-forward, paper-period, "
            "human-checkpoint, autonomous paper, nightly calibration, paper broker, "
            "and paper tape. No live broker, no secrets, no live trading authority. "
            "Phase 3 is autonomous paper under armed kill switch + verified ledger, "
            "not live trading."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    round_p = sub.add_parser("round", help="Run one fixture desk round")
    round_p.add_argument(
        "--scenario",
        default="deploy",
        choices=sorted(SCENARIOS),
        help="Built-in deterministic fixture (default: deploy)",
    )
    round_p.add_argument(
        "--mode",
        default=DeskMode.WALK_FORWARD_REPLAY.value,
        choices=[DeskMode.WALK_FORWARD_REPLAY.value, DeskMode.PAPER_TRADING.value],
        help="Phase 0 modes only. Live modes are rejected.",
    )
    round_p.add_argument(
        "--broker",
        default="fixture",
        choices=["fixture", "alpaca-paper"],
        help=(
            "Paper broker port. Default fixture is in-memory (no network, no keys). "
            "alpaca-paper is opt-in and pinned to https://paper-api.alpaca.markets."
        ),
    )
    walk_p = sub.add_parser(
        "walk-forward",
        help="Replay a fixture sequence (phase_0 walk_forward_replay)",
    )
    walk_p.add_argument(
        "--fixture",
        default=None,
        help="Path to a walk-forward JSON fixture (default: shipped sample)",
    )
    walk_p.add_argument(
        "--mode",
        default=DeskMode.WALK_FORWARD_REPLAY.value,
        choices=[DeskMode.WALK_FORWARD_REPLAY.value],
        help="Phase 0 walk-forward only. Live modes are rejected.",
    )
    paper_p = sub.add_parser(
        "paper-period",
        help="Simulate phase_1 paper_trading session days (duration_days=21)",
    )
    paper_p.add_argument(
        "--fixture",
        default=None,
        help="Path to a paper-period JSON fixture (default: shipped sample)",
    )
    paper_p.add_argument(
        "--days",
        type=int,
        default=None,
        help="Simulate only the first N session days (duration still requires 21)",
    )
    paper_p.add_argument(
        "--mode",
        default=DeskMode.PAPER_TRADING.value,
        choices=[DeskMode.PAPER_TRADING.value],
        help="Phase 1 paper_trading only. Live / phase_3 modes are rejected.",
    )
    check_p = sub.add_parser(
        "human-checkpoint",
        help=(
            "Simulate phase_2 live_with_human_checkpoint as checkpointed PAPER "
            "deploys (human_checkpoint_trades=25). Not a live broker."
        ),
    )
    check_p.add_argument(
        "--fixture",
        default=None,
        help="Path to a human-checkpoint JSON fixture (default: shipped sample)",
    )
    check_p.add_argument(
        "--trades",
        type=int,
        default=None,
        help="Simulate only the first N setups (count still requires 25)",
    )
    check_p.add_argument(
        "--approver",
        default=None,
        help=(
            "Simulated human identity. Binds a structured approval "
            "(approved_by, approved_at, setup_id, rationale_hash) to each "
            "proposed deploy. A boolean flag is not accepted. Omit to refuse "
            "unapproved deploys."
        ),
    )
    check_p.add_argument(
        "--mode",
        default=DeskMode.LIVE_WITH_HUMAN_CHECKPOINT.value,
        choices=[DeskMode.LIVE_WITH_HUMAN_CHECKPOINT.value],
        help="Phase 2 checkpointed paper only. Autonomous paper uses the autonomous command.",
    )
    auto_p = sub.add_parser(
        "autonomous",
        help=(
            "Simulate phase_3 autonomous PAPER rounds when kill_switch_armed "
            "and ledger_verified. Not a live broker."
        ),
    )
    auto_p.add_argument(
        "--fixture",
        default=None,
        help="Path to an autonomous JSON fixture (default: shipped sample)",
    )
    auto_p.add_argument(
        "--rounds",
        type=int,
        default=None,
        help="Run only the first N fixture rounds",
    )
    auto_p.add_argument(
        "--mode",
        default=DeskMode.AUTONOMOUS.value,
        choices=[DeskMode.AUTONOMOUS.value],
        help="Phase 3 autonomous paper only. Live broker modes are rejected.",
    )
    auto_p.add_argument(
        "--broker",
        default="paper",
        choices=["paper"],
        help="Paper books only. Live brokers are rejected.",
    )
    cal_p = sub.add_parser(
        "calibrate",
        aliases=["nightly"],
        help=(
            "Nightly calibration from ledger outcomes: update gate weights, "
            "report rolling-50 per-regime accuracy, propose eviction (binds at "
            "min_sample_live_setups=200), and flag correlated errors. Paper/fixture only."
        ),
    )
    cal_p.add_argument(
        "--fixture",
        default=None,
        help="Path to a calibration JSON fixture (default: shipped nightly sample)",
    )
    cal_p.add_argument(
        "--apply",
        action="store_true",
        help="Write proposed weights onto the working gate (logged, reversible in-process)",
    )
    cal_p.add_argument(
        "--evict",
        action="store_true",
        help=(
            "Apply eviction if n>=min_sample_live_setups (200). Rolling-50 "
            "accuracy is still reported when n>=50. Fail closed below the bind "
            "sample; never invents accuracy."
        ),
    )
    cal_p.add_argument(
        "--revert",
        action="store_true",
        help="Restore the last in-process weight snapshot (fails closed if none)",
    )
    cal_p.add_argument(
        "--mode",
        default=DeskMode.PAPER_TRADING.value,
        choices=[DeskMode.PAPER_TRADING.value, DeskMode.WALK_FORWARD_REPLAY.value],
        help="Paper / walk-forward only. Live modes are rejected.",
    )
    cal_p.add_argument(
        "--broker",
        default="paper",
        choices=["paper"],
        help="Paper books only. Live brokers are rejected.",
    )
    cal_p.add_argument(
        "--store",
        default=None,
        help=(
            "Read persisted ledger outcomes from this store directory instead of "
            "ingesting the calibration fixture."
        ),
    )
    broker_p = sub.add_parser(
        "broker-preflight",
        help=(
            "Ping the paper broker port. Default fixture is in-memory. "
            "alpaca-paper requires ALPACA_API_KEY and ALPACA_API_SECRET and "
            "only talks to https://paper-api.alpaca.markets."
        ),
    )
    broker_p.add_argument(
        "--broker",
        default="fixture",
        choices=["fixture", "alpaca-paper"],
        help="Paper broker port. Live hosts are rejected.",
    )
    tape_p = sub.add_parser(
        "tape",
        help=(
            "Build setups from OHLCV bars. Default is the shipped fixture tape "
            "(no network). Optional --source alpaca fetches paper/sandbox bars "
            "when keys exist; CI never requires it."
        ),
    )
    tape_p.add_argument(
        "--fixture",
        default=None,
        help="Path to a tape JSON fixture (default: fixtures/tape/sample.json)",
    )
    tape_p.add_argument(
        "--round",
        action="store_true",
        dest="tape_round",
        help="Run only the first setup as one desk round (no walk settlement)",
    )
    tape_p.add_argument(
        "--source",
        default="fixture",
        choices=["fixture", "alpaca"],
        help=(
            "fixture reads committed bars. alpaca fetches data-host bars when "
            "ALPACA_API_KEY / ALPACA_API_SECRET exist (fail closed otherwise). "
            "Not the broker port; no orders."
        ),
    )
    tape_p.add_argument(
        "--symbol",
        default=None,
        help="Required with --source alpaca. Bars are fetched for this symbol.",
    )
    session_p = sub.add_parser(
        "paper-session",
        help=(
            "Operator paper loop: fixture tape → rule agents → gate → risk / "
            "kill switch → paper broker → ledger. Default is fully offline."
        ),
    )
    session_p.add_argument(
        "--fixture",
        default=None,
        help="Path to a tape JSON fixture (default: fixtures/tape/sample.json)",
    )
    session_p.add_argument(
        "--broker",
        default="fixture",
        choices=["fixture", "alpaca-paper"],
        help=(
            "Paper broker port. Default fixture is in-memory (no network, no keys). "
            "alpaca-paper is opt-in and pinned to https://paper-api.alpaca.markets."
        ),
    )
    session_p.add_argument(
        "--store",
        default=None,
        help=(
            "Directory for the append-only ledger store (chain.jsonl + snapshot). "
            "Default when creating/resuming: data/ledger (gitignored). "
            "Omit to keep the ledger in memory only."
        ),
    )
    session_p.add_argument(
        "--new-store",
        action="store_true",
        help="Create an empty ledger store. Fails closed if a chain already exists.",
    )
    report_p = sub.add_parser(
        "report",
        help=(
            "Read-only operator summary of a durable ledger store. "
            "Requires --store. No broker calls."
        ),
    )
    report_p.add_argument(
        "--store",
        nargs="?",
        const="",
        default=None,
        help=(
            "Ledger store directory (required). Pass --store with no path to use "
            "data/ledger. Without --store the command refuses; there is no "
            "in-memory demo."
        ),
    )
    report_p.add_argument(
        "--human",
        action="store_true",
        help="Also print a short text summary to stderr. JSON still goes to stdout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "round":
        try:
            desk = TradingDesk(mode=DeskMode(args.mode), broker_kind=args.broker)
        except (BrokerCredentialsError, LiveTradingDisabled, BrokerError) as exc:
            json.dump(
                {
                    "refused": True,
                    "reason": str(exc),
                    "live_trading_authority": False,
                },
                sys.stdout,
                indent=2,
            )
            sys.stdout.write("\n")
            return 1
        fixture = SCENARIOS.get(args.scenario, deploy_fixture)()
        if args.scenario == "halt":
            desk.books.apply_drawdown_mark(8.0)
        result = desk.run_round(fixture)
        json.dump(result_to_dict(result), sys.stdout, indent=2)
        sys.stdout.write("\n")
        if result.decision.value in {"halt"}:
            return 2
        if result.executed:
            return 0
        return 1
    if args.command == "walk-forward":
        desk = TradingDesk(mode=DeskMode(args.mode))
        path = Path(args.fixture) if args.fixture else None
        report = WalkForwardReplay(desk).run(load_walk_fixture(path))
        json.dump(walk_report_to_dict(report), sys.stdout, indent=2)
        sys.stdout.write("\n")
        if report.halted:
            return 2
        if report.exit_criteria.met:
            return 0
        return 1
    if args.command == "paper-period":
        desk = TradingDesk(mode=DeskMode(args.mode))
        path = Path(args.fixture) if args.fixture else None
        fixture = load_paper_fixture(path)
        if args.days is not None:
            fixture = limit_paper_days(fixture, args.days)
        report = PaperPeriod(desk).run(fixture)
        json.dump(paper_report_to_dict(report), sys.stdout, indent=2)
        sys.stdout.write("\n")
        if report.halted:
            return 2
        if report.duration_complete:
            return 0
        return 1
    if args.command == "human-checkpoint":
        desk = TradingDesk(mode=DeskMode(args.mode))
        path = Path(args.fixture) if args.fixture else None
        fixture = load_checkpoint_fixture(path)
        if args.trades is not None:
            fixture = limit_checkpoint_trades(fixture, args.trades)
        report = HumanCheckpointSequence(
            desk, approver=(args.approver.strip() if args.approver else None)
        ).run(fixture)
        json.dump(checkpoint_report_to_dict(report), sys.stdout, indent=2)
        sys.stdout.write("\n")
        if report.halted:
            return 2
        if report.trade_count_complete:
            return 0
        return 1
    if args.command == "autonomous":
        if args.broker != "paper":
            raise LiveTradingDisabled("live broker is not implemented")
        desk = TradingDesk(mode=DeskMode(args.mode), live_broker=False)
        path = Path(args.fixture) if args.fixture else None
        fixture = load_autonomous_fixture(path)
        if args.rounds is not None:
            fixture = limit_autonomous_rounds(fixture, args.rounds)
        try:
            report = AutonomousPaperLoop(desk).run(fixture)
        except AutonomousError as exc:
            json.dump(
                {
                    "refused": True,
                    "reason": str(exc),
                    "live_trading_authority": False,
                    "reenable": "manual_human_only",
                },
                sys.stdout,
                indent=2,
            )
            sys.stdout.write("\n")
            return 1
        json.dump(autonomous_report_to_dict(report), sys.stdout, indent=2)
        sys.stdout.write("\n")
        if report.halted:
            return 2
        return 0
    if args.command in {"calibrate", "nightly"}:
        if getattr(args, "broker", "paper") != "paper":
            raise LiveTradingDisabled("live broker is not implemented")
        desk = TradingDesk(mode=DeskMode(args.mode), live_broker=False)
        try:
            if args.revert:
                report = CalibrationEngine().nightly_from_outcomes(desk, revert=True)
            elif getattr(args, "store", None):
                if args.fixture:
                    raise CalibrationError(
                        "calibrate --store reads persisted outcomes; omit --fixture"
                    )
                attach_store(desk, args.store, new=False)
                report = CalibrationEngine().nightly_from_outcomes(
                    desk,
                    apply=bool(args.apply),
                    evict=True if args.evict else None,
                )
            else:
                path = Path(args.fixture) if args.fixture else None
                fixture = load_calibration_fixture(path)
                ingest_calibration_setups(desk, fixture.setups)
                report = CalibrationEngine().nightly_from_outcomes(
                    desk,
                    apply=bool(args.apply),
                    evict=True if args.evict else None,
                )
        except (CalibrationError, LedgerStoreError) as exc:
            json.dump(
                {
                    "refused": True,
                    "reason": str(exc),
                    "live_trading_authority": False,
                },
                sys.stdout,
                indent=2,
            )
            sys.stdout.write("\n")
            return 1
        json.dump(calibrate_report_to_dict(report), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.command == "broker-preflight":
        try:
            port = build_paper_broker(args.broker)
            report = port.preflight()
        except (BrokerCredentialsError, LiveTradingDisabled, BrokerError) as exc:
            json.dump(
                {
                    "refused": True,
                    "reason": str(exc),
                    "live_trading_authority": False,
                },
                sys.stdout,
                indent=2,
            )
            sys.stdout.write("\n")
            return 1
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if args.command == "tape":
        return _run_tape(args)
    if args.command == "paper-session":
        path = Path(args.fixture) if args.fixture else None
        try:
            report = run_paper_session(
                broker_kind=args.broker,
                fixture_path=path,
                store_path=args.store,
                new_store=bool(args.new_store),
            )
        except (
            BrokerCredentialsError,
            LiveTradingDisabled,
            BrokerError,
            MarketDataError,
            WalkForwardError,
            LedgerStoreError,
        ) as exc:
            json.dump(_refuse_payload(exc), sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 1
        json.dump(paper_session_report_to_dict(report), sys.stdout, indent=2)
        sys.stdout.write("\n")
        if report.halted:
            return 2
        return 0
    if args.command == "report":
        if args.store is None:
            json.dump(
                _refuse_payload(LedgerStoreError(STORE_REQUIRED_REASON)),
                sys.stdout,
                indent=2,
            )
            sys.stdout.write("\n")
            return 1
        store_path = default_store_path() if args.store == "" else Path(args.store)
        try:
            report = build_operator_report(store_path)
        except (LedgerStoreError, LiveTradingDisabled) as exc:
            json.dump(_refuse_payload(exc), sys.stdout, indent=2)
            sys.stdout.write("\n")
            return 1
        json.dump(report_to_dict(report), sys.stdout, indent=2)
        sys.stdout.write("\n")
        if args.human:
            sys.stderr.write(report.human_summary)
        return 0
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
