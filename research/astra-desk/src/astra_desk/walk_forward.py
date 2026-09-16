"""Phase 0 walk-forward replay. Fixture data only. Reuses the existing desk."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from astra_desk.accounting import CapitalBooks
from astra_desk.contract import EXIT_ID, load_contract
from astra_desk.desk import TradingDesk
from astra_desk.errors import (
    AgentFeatureError,
    LiveTradingDisabled,
    MarketDataError,
    WalkForwardError,
)
from astra_desk.features import parse_features
from astra_desk.types import (
    Bar,
    DeskMode,
    ExitCriteriaResult,
    ExitOutput,
    Regime,
    RoundDecision,
    RoundFixture,
    WalkForwardReport,
    WalkSettlement,
)

WALK_SCHEMA = "astra-desk/walk-forward-v1"
PHASE0_EXIT_RULE = "positive expectancy, max_dd < 15%"
PHASE0_MAX_DD_PCT = 15.0
SHIPPED_FIXTURE_NAME = "phase0_sample.json"
LIVE_FIXTURE_MODES = frozenset(
    {
        DeskMode.LIVE_WITH_HUMAN_CHECKPOINT.value,
        DeskMode.AUTONOMOUS.value,
        "live",
    }
)


@dataclass(frozen=True)
class WalkSetup:
    fixture: RoundFixture
    bars: tuple[Bar, ...]
    settlement_pnl: float | None


@dataclass(frozen=True)
class WalkFixture:
    setups: tuple[WalkSetup, ...]
    path: Path | None = None


def default_walk_fixture_path() -> Path:
    env = os.environ.get("ASTRA_DESK_WALK_FIXTURE")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "fixtures" / "walk_forward" / SHIPPED_FIXTURE_NAME,
        Path.cwd() / "fixtures" / "walk_forward" / SHIPPED_FIXTURE_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise WalkForwardError(
        "cannot locate fixtures/walk_forward/phase0_sample.json; "
        "set ASTRA_DESK_WALK_FIXTURE"
    )


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WalkForwardError(f"{label} must be a mapping")
    return value


def _parse_bar(raw: Mapping[str, Any], setup_id: str) -> Bar:
    from astra_desk.market_data import parse_bar

    if not isinstance(raw, Mapping):
        raise WalkForwardError(f"{setup_id}: bar must be an object")
    try:
        return parse_bar(raw, setup_id)
    except MarketDataError as exc:
        raise WalkForwardError(str(exc)) from exc


def setup_to_round_fixture(raw: Mapping[str, Any]) -> RoundFixture:
    setup_id = str(raw.get("setup_id") or "")
    if not setup_id:
        raise WalkForwardError("each setup requires setup_id")
    try:
        from astra_desk.tape import materialize_setup

        raw = materialize_setup(raw)
    except MarketDataError as exc:
        raise WalkForwardError(str(exc)) from exc
    try:
        regime = Regime(str(raw["regime"]))
    except (KeyError, ValueError) as exc:
        raise WalkForwardError(f"{setup_id}: invalid regime") from exc
    try:
        features = parse_features(raw.get("features"), setup_id=setup_id)
    except AgentFeatureError as exc:
        raise WalkForwardError(str(exc)) from exc

    broker_raw: Mapping[str, Any]
    if isinstance(raw.get("broker"), Mapping):
        broker_raw = raw["broker"]  # type: ignore[assignment]
    elif isinstance(raw.get("agents"), Mapping):
        maybe = raw["agents"].get("218_broker")  # type: ignore[union-attr]
        broker_raw = maybe if isinstance(maybe, Mapping) else {}
    else:
        broker_raw = {}
    if "size_units" not in broker_raw:
        raise WalkForwardError(f"{setup_id}: broker.size_units is required")

    voting_ids = ("456_scout", "067_reader", "240_exit", "666_skeptic")
    agents_raw = raw.get("agents")
    if isinstance(agents_raw, Mapping):
        for agent_id in voting_ids:
            block = agents_raw.get(agent_id)
            if isinstance(block, Mapping) and "conviction" in block:
                raise WalkForwardError(
                    f"{setup_id}: do not set agents.{agent_id}.conviction; "
                    "provide features for the rule-based voting path"
                )

    try:
        return RoundFixture(
            setup_id=setup_id,
            symbol=str(raw.get("symbol") or "PAPER-TEST"),
            regime=regime,
            ev_estimate=float(raw["ev_estimate"]),
            price=float(raw["price"]),
            features=features,
            broker_size_units=float(broker_raw["size_units"]),
            broker_kelly_fraction=float(broker_raw.get("kelly_fraction", 0.2)),
            correlation_group=str(raw.get("correlation_group") or "index-beta"),
            side=str(raw.get("side") or "long"),
            resolved_correct_by_agent=dict(raw.get("resolved_correct_by_agent") or {}),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise WalkForwardError(f"{setup_id}: invalid setup fields: {exc}") from exc


def parse_walk_setup(raw: Mapping[str, Any]) -> WalkSetup:
    fixture = setup_to_round_fixture(raw)
    if raw.get("forward_bars") is not None:
        bars_raw = raw.get("forward_bars")
    else:
        bars_raw = raw.get("bars") or []
    if not isinstance(bars_raw, list):
        raise WalkForwardError(f"{fixture.setup_id}: bars must be a list")
    bars = tuple(_parse_bar(b, fixture.setup_id) for b in bars_raw)
    settlement = raw.get("settlement")
    settlement_pnl: float | None = None
    if settlement is not None:
        block = _require_mapping(settlement, f"{fixture.setup_id}.settlement")
        if "realized_pnl" not in block:
            raise WalkForwardError(f"{fixture.setup_id}: settlement.realized_pnl required")
        settlement_pnl = float(block["realized_pnl"])
    return WalkSetup(fixture=fixture, bars=bars, settlement_pnl=settlement_pnl)


def load_walk_fixture(path: Path | None = None) -> WalkFixture:
    fixture_path = path or default_walk_fixture_path()
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WalkForwardError(f"failed to read walk fixture {fixture_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise WalkForwardError("walk fixture must be a JSON object")
    schema = data.get("schema")
    if schema == "astra-desk/tape-v1":
        from astra_desk.tape import tape_to_walk_fixture

        try:
            return tape_to_walk_fixture(fixture_path)
        except MarketDataError as exc:
            raise WalkForwardError(str(exc)) from exc
    if schema != WALK_SCHEMA:
        raise WalkForwardError(f"unsupported walk fixture schema: {schema!r}")
    mode = data.get("mode", DeskMode.WALK_FORWARD_REPLAY.value)
    if mode in LIVE_FIXTURE_MODES:
        raise LiveTradingDisabled(f"walk fixture mode={mode} is not allowed in Phase 0")
    if mode != DeskMode.WALK_FORWARD_REPLAY.value:
        raise WalkForwardError(f"walk fixture mode must be walk_forward_replay, got {mode}")
    setups_raw = data.get("setups")
    if not isinstance(setups_raw, list) or not setups_raw:
        raise WalkForwardError("walk fixture requires a non-empty setups list")
    setups = tuple(parse_walk_setup(_require_mapping(item, "setup")) for item in setups_raw)
    return WalkFixture(setups=setups, path=fixture_path)


def replay_bars(
    *,
    entry: float,
    notional: float,
    side: str,
    stop_price: float,
    take_profit_levels: tuple[float, ...],
    bars: tuple[Bar, ...],
) -> WalkSettlement:
    """Settle a paper fill from fixture bars. Stop wins ties (fail closed)."""
    if entry <= 0 or notional <= 0:
        raise WalkForwardError("entry and notional must be > 0")
    if not bars:
        raise WalkForwardError("cannot replay an empty bar series")
    sign = 1.0 if side == "long" else -1.0
    tps = tuple(sorted(take_profit_levels, reverse=(side != "long")))

    def pnl_at(price: float) -> float:
        return notional * sign * (price - entry) / entry

    for bar in bars:
        if bar.high < bar.low:
            raise WalkForwardError(f"invalid bar {bar.ts}: high < low")
        if side == "long":
            gapped_stop = bar.open <= stop_price
            hit_stop = gapped_stop or bar.low <= stop_price
            hit_tp = None
            for tp in tps:
                if bar.open >= tp or bar.high >= tp:
                    hit_tp = bar.open if bar.open >= tp else tp
                    break
            if hit_stop:
                fill = bar.open if gapped_stop else stop_price
                return WalkSettlement(pnl_at(fill), fill, "stop")
            if hit_tp is not None:
                return WalkSettlement(pnl_at(hit_tp), hit_tp, "take_profit")
        else:
            gapped_stop = bar.open >= stop_price
            hit_stop = gapped_stop or bar.high >= stop_price
            hit_tp = None
            for tp in tps:
                if bar.open <= tp or bar.low <= tp:
                    hit_tp = bar.open if bar.open <= tp else tp
                    break
            if hit_stop:
                fill = bar.open if gapped_stop else stop_price
                return WalkSettlement(pnl_at(fill), fill, "stop")
            if hit_tp is not None:
                return WalkSettlement(pnl_at(hit_tp), hit_tp, "take_profit")
    last = bars[-1]
    return WalkSettlement(pnl_at(last.close), last.close, "close")


def evaluate_phase0_exit(books: CapitalBooks, *, rule: str) -> ExitCriteriaResult:
    if rule != PHASE0_EXIT_RULE:
        raise WalkForwardError(f"unsupported phase_0 exit_criteria: {rule!r}")
    snap = books.snapshot()
    reasons: list[str] = []
    expectancy = snap.expectancy_per_setup
    if expectancy is None:
        reasons.append("expectancy_null_insufficient_sample")
    elif not (expectancy > 0):
        reasons.append("expectancy_not_positive")
    if snap.max_drawdown_pct >= PHASE0_MAX_DD_PCT:
        reasons.append("max_dd_gte_15")
    return ExitCriteriaResult(
        met=not reasons,
        rule=rule,
        expectancy_per_setup=expectancy,
        max_drawdown_pct=snap.max_drawdown_pct,
        closed_trade_count=snap.closed_trade_count,
        reasons=tuple(reasons),
    )


def resolve_settlement(
    setup: WalkSetup,
    *,
    executed: bool,
    size_usd: float,
    stop_price: float | None,
    take_profit_levels: tuple[float, ...] | None,
) -> WalkSettlement | None:
    if not executed:
        return None
    if setup.settlement_pnl is not None:
        return WalkSettlement(setup.settlement_pnl, None, "explicit")
    if not setup.bars:
        raise WalkForwardError(
            f"{setup.fixture.setup_id}: executed setup needs bars or settlement"
        )
    if stop_price is None or not take_profit_levels:
        raise WalkForwardError(
            f"{setup.fixture.setup_id}: executed setup missing exit agent levels"
        )
    return replay_bars(
        entry=setup.fixture.price,
        notional=size_usd,
        side=setup.fixture.side,
        stop_price=stop_price,
        take_profit_levels=take_profit_levels,
        bars=setup.bars,
    )


class WalkForwardReplay:
    """Drive a fixture sequence through the existing desk. No second gate/risk."""

    def __init__(self, desk: TradingDesk | None = None) -> None:
        self.desk = desk or TradingDesk(mode=DeskMode.WALK_FORWARD_REPLAY)
        if self.desk.mode.is_live:
            raise LiveTradingDisabled(
                f"walk-forward refuses live mode={self.desk.mode.value}"
            )
        if self.desk.mode is not DeskMode.WALK_FORWARD_REPLAY:
            raise WalkForwardError(
                "walk-forward requires mode=walk_forward_replay; "
                f"got {self.desk.mode.value}"
            )
        contract_rule = str(
            self.desk.contract.raw["rollout"]["phase_0"]["exit_criteria"]
        )
        if contract_rule != PHASE0_EXIT_RULE:
            raise WalkForwardError(
                f"contract phase_0 exit_criteria drifted: {contract_rule!r}"
            )
        phase0_mode = self.desk.contract.raw["rollout"]["phase_0"]["mode"]
        if phase0_mode != DeskMode.WALK_FORWARD_REPLAY.value:
            raise WalkForwardError(f"contract phase_0 mode is {phase0_mode}")

    def run(self, walk: WalkFixture) -> WalkForwardReport:
        rounds = []
        settlements: list[WalkSettlement | None] = []
        halted = False
        halt_reason: str | None = None
        for setup in walk.setups:
            result = self.desk.run_round(setup.fixture)
            rounds.append(result)
            if result.decision is RoundDecision.HALT or result.kill_switch.halted:
                halted = True
                halt_reason = result.kill_switch.reason or result.gate.reason
                settlements.append(None)
                break
            exit_out = result.agent_outputs.get(EXIT_ID)
            stop_price = exit_out.stop_price if isinstance(exit_out, ExitOutput) else None
            take_profits = (
                exit_out.take_profit_levels if isinstance(exit_out, ExitOutput) else None
            )
            settlement = resolve_settlement(
                setup,
                executed=result.executed,
                size_usd=result.final_size_usd,
                stop_price=stop_price,
                take_profit_levels=take_profits,
            )
            settlements.append(settlement)
            if settlement is not None:
                self.desk.settle_paper(
                    round_id=result.round_id,
                    realized_pnl=settlement.realized_pnl,
                    detail={
                        "setup_id": setup.fixture.setup_id,
                        "reason": settlement.reason,
                        "exit_price": settlement.exit_price,
                        "bars": len(setup.bars),
                    },
                )
                if self.desk.kill_switch.halted:
                    halted = True
                    halt_reason = self.desk.kill_switch.state().reason
                    break

        snap = self.desk.books.snapshot()
        exit_criteria = evaluate_phase0_exit(
            self.desk.books,
            rule=PHASE0_EXIT_RULE,
        )
        summary = {
            "kind": "walk_forward_summary",
            "mode": self.desk.mode.value,
            "setups_total": len(walk.setups),
            "setups_processed": len(rounds),
            "halted": halted,
            "halt_reason": halt_reason,
            "exit_criteria": {
                "met": exit_criteria.met,
                "rule": exit_criteria.rule,
                "expectancy_per_setup": exit_criteria.expectancy_per_setup,
                "max_drawdown_pct": exit_criteria.max_drawdown_pct,
                "closed_trade_count": exit_criteria.closed_trade_count,
                "reasons": list(exit_criteria.reasons),
            },
            "accounting": snap.ledger_fields(),
            "live_trading_authority": False,
        }
        self.desk.ledger.append("walk-forward-summary", summary)
        return WalkForwardReport(
            mode=self.desk.mode,
            fixture_path=str(walk.path) if walk.path else None,
            setups_total=len(walk.setups),
            setups_processed=len(rounds),
            halted=halted,
            halt_reason=halt_reason,
            rounds=tuple(rounds),
            settlements=tuple(settlements),
            accounting=snap,
            exit_criteria=exit_criteria,
            ledger_entry_count=len(self.desk.ledger),
            live_trading_authority=False,
        )


def run_shipped_walk() -> WalkForwardReport:
    load_contract()
    return WalkForwardReplay().run(load_walk_fixture())
