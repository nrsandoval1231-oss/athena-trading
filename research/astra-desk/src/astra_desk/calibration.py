"""Calibration from the contract: nightly weights, rolling-50 stats, bind-at-200 eviction.

Paper/fixture only. Weight updates are explicit, logged, and reversible.
Rolling-50 per-regime accuracy is reported when the window is full.
Seat eviction binds only at capital.evaluation.min_sample_live_setups (200)
and does not delete ledger history. Does not invent accuracy below the
rolling window.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Any
from collections.abc import Mapping

from astra_desk.contract import GATING_AGENT_IDS, SKEPTIC_ID, load_contract
from astra_desk.errors import CalibrationError, LiveTradingDisabled
from astra_desk.ledger import AccuracyTracker, SetupOutcome, outcomes_from_ledger
from astra_desk.types import DeskMode

ROLLING_SETUPS = 50
EVICTION_RULE = "lowest per-regime-calibrated accuracy over rolling 50 setups"
WEIGHTS_UPDATE = "nightly_from_outcomes"
CORRELATION_THRESHOLD = 0.8
CORRELATION_MIN_OVERLAP = 10
CALIBRATION_SCHEMA = "astra-desk/calibration-v1"
SHIPPED_FIXTURE_NAME = "nightly_sample.json"
LIVE_FIXTURE_MODES = frozenset({"live", "live_broker", DeskMode.AUTONOMOUS.value})


@dataclass(frozen=True)
class EvictionProposal:
    agent_id: str | None
    calibrated_accuracy: float | None
    sample_setups: int
    eligible: bool
    reason: str
    scores: Mapping[str, float]
    rolling_window: int = ROLLING_SETUPS
    bind_sample: int = 200


@dataclass(frozen=True)
class CorrelationFlag:
    agent_a: str
    agent_b: str
    comovement: float
    overlap: int


@dataclass(frozen=True)
class CalibrationReport:
    weights_from: Mapping[str, float]
    weights_proposed: Mapping[str, float]
    weights_applied: bool
    weights_update: str
    eviction: EvictionProposal
    eviction_applied: bool
    disabled_seats: Mapping[str, str]
    per_regime_accuracy: Mapping[str, Mapping[str, Mapping[str, float | int]]]
    calibrated_accuracy: Mapping[str, float]
    correlation_flags: tuple[CorrelationFlag, ...]
    sample_setups: int
    rolling_window: int
    ledger_verified: bool
    live_trading_authority: bool = False
    reverted: bool = False


@dataclass(frozen=True)
class CalibrationFixture:
    setups: tuple[Any, ...]
    path: Path | None = None


def default_calibration_fixture_path() -> Path:
    env = os.environ.get("ASTRA_DESK_CALIBRATION_FIXTURE")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "fixtures" / "calibration" / SHIPPED_FIXTURE_NAME,
        Path.cwd() / "fixtures" / "calibration" / SHIPPED_FIXTURE_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise CalibrationError(
        "cannot locate fixtures/calibration/nightly_sample.json; "
        "set ASTRA_DESK_CALIBRATION_FIXTURE"
    )


def load_calibration_fixture(path: Path | None = None) -> CalibrationFixture:
    from astra_desk.errors import WalkForwardError
    from astra_desk.walk_forward import parse_walk_setup

    fixture_path = path or default_calibration_fixture_path()
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"failed to read calibration fixture {fixture_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CalibrationError("calibration fixture must be a JSON object")
    if data.get("schema") != CALIBRATION_SCHEMA:
        raise CalibrationError(f"unsupported calibration fixture schema: {data.get('schema')!r}")
    mode = data.get("mode", DeskMode.PAPER_TRADING.value)
    if mode in LIVE_FIXTURE_MODES:
        raise LiveTradingDisabled(f"calibration fixture mode={mode} is not allowed")
    if mode not in {DeskMode.PAPER_TRADING.value, DeskMode.WALK_FORWARD_REPLAY.value}:
        raise CalibrationError(f"calibration fixture mode must be paper/walk-forward, got {mode}")
    setups_raw = data.get("setups")
    if not isinstance(setups_raw, list) or not setups_raw:
        raise CalibrationError("calibration fixture requires a non-empty setups list")
    setups: list[Any] = []
    for item in setups_raw:
        if not isinstance(item, Mapping):
            raise CalibrationError("each calibration setup must be a mapping")
        try:
            setups.append(parse_walk_setup(item))
        except WalkForwardError as exc:
            raise CalibrationError(str(exc)) from exc
    return CalibrationFixture(setups=tuple(setups), path=fixture_path)


def per_regime_stats(
    outcomes: tuple[SetupOutcome, ...], agent_id: str
) -> dict[str, dict[str, float | int]]:
    buckets: dict[str, list[bool]] = {}
    for outcome in outcomes:
        if agent_id not in outcome.correct_by_agent:
            continue
        buckets.setdefault(outcome.regime, []).append(bool(outcome.correct_by_agent[agent_id]))
    stats: dict[str, dict[str, float | int]] = {}
    for regime, flags in buckets.items():
        hits = sum(1 for flag in flags if flag)
        n = len(flags)
        stats[regime] = {"hits": hits, "n": n, "accuracy": hits / n if n else 0.0}
    return stats


def calibrated_accuracy(outcomes: tuple[SetupOutcome, ...], agent_id: str) -> float | None:
    """Mean of per-regime accuracies. None if this agent has no outcomes (do not invent)."""
    stats = per_regime_stats(outcomes, agent_id)
    accuracies = [float(slot["accuracy"]) for slot in stats.values() if int(slot["n"]) > 0]
    if not accuracies:
        return None
    return mean(accuracies)


def propose_weights(
    baseline: Mapping[str, float],
    outcomes: tuple[SetupOutcome, ...],
    *,
    gating_ids: tuple[str, ...] = GATING_AGENT_IDS,
) -> dict[str, float]:
    """Scale |weight| by (0.5 + accuracy). Skeptic stays negative."""
    proposed = {str(k): float(v) for k, v in baseline.items()}
    if not outcomes:
        raise CalibrationError("insufficient_sample_for_weights")
    for agent_id in gating_ids:
        acc = calibrated_accuracy(outcomes, agent_id)
        if acc is None:
            continue
        base = float(baseline.get(agent_id, 0.0))
        magnitude = abs(base) * (0.5 + acc)
        signed = -magnitude if (agent_id == SKEPTIC_ID or base < 0) else magnitude
        proposed[agent_id] = round(signed, 6)
    return proposed


def propose_eviction(
    outcomes: tuple[SetupOutcome, ...],
    *,
    window: int = ROLLING_SETUPS,
    bind_sample: int = 200,
    gating_ids: tuple[str, ...] = GATING_AGENT_IDS,
    disabled: Mapping[str, str] | None = None,
) -> EvictionProposal:
    """Rolling-50 scores are visible when n>=window. Seat eviction binds at bind_sample."""
    sample = len(outcomes)
    disabled = disabled or {}
    if sample < window:
        return EvictionProposal(
            agent_id=None,
            calibrated_accuracy=None,
            sample_setups=sample,
            eligible=False,
            reason="insufficient_sample_for_rolling_50",
            scores={},
            rolling_window=window,
            bind_sample=bind_sample,
        )
    rolling = outcomes[-window:]
    scores: dict[str, float] = {}
    for agent_id in gating_ids:
        if agent_id in disabled:
            continue
        acc = calibrated_accuracy(rolling, agent_id)
        if acc is None:
            continue
        scores[agent_id] = acc
    if not scores:
        return EvictionProposal(
            agent_id=None,
            calibrated_accuracy=None,
            sample_setups=sample,
            eligible=False,
            reason="no_gating_agent_has_outcomes",
            scores={},
            rolling_window=window,
            bind_sample=bind_sample,
        )
    loser = min(scores.items(), key=lambda item: (item[1], item[0]))
    if sample < bind_sample:
        return EvictionProposal(
            agent_id=loser[0],
            calibrated_accuracy=loser[1],
            sample_setups=sample,
            eligible=False,
            reason="insufficient_sample_for_min_live_setups",
            scores=scores,
            rolling_window=window,
            bind_sample=bind_sample,
        )
    return EvictionProposal(
        agent_id=loser[0],
        calibrated_accuracy=loser[1],
        sample_setups=sample,
        eligible=True,
        reason=EVICTION_RULE,
        scores=scores,
        rolling_window=window,
        bind_sample=bind_sample,
    )


def _comovement(xs: list[int], ys: list[int]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    if xs == ys:
        return 1.0 if any(xs) else None
    mx = mean(xs)
    my = mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def correlation_flags(
    outcomes: tuple[SetupOutcome, ...],
    *,
    gating_ids: tuple[str, ...] = GATING_AGENT_IDS,
    threshold: float = CORRELATION_THRESHOLD,
    min_overlap: int = CORRELATION_MIN_OVERLAP,
) -> tuple[CorrelationFlag, ...]:
    flags: list[CorrelationFlag] = []
    ids = list(gating_ids)
    for i, agent_a in enumerate(ids):
        for agent_b in ids[i + 1 :]:
            errors_a: list[int] = []
            errors_b: list[int] = []
            for outcome in outcomes:
                if agent_a not in outcome.correct_by_agent or agent_b not in outcome.correct_by_agent:
                    continue
                errors_a.append(0 if outcome.correct_by_agent[agent_a] else 1)
                errors_b.append(0 if outcome.correct_by_agent[agent_b] else 1)
            if len(errors_a) < min_overlap:
                continue
            score = _comovement(errors_a, errors_b)
            if score is None or score < threshold:
                continue
            flags.append(
                CorrelationFlag(
                    agent_a=agent_a,
                    agent_b=agent_b,
                    comovement=score,
                    overlap=len(errors_a),
                )
            )
    return tuple(flags)


def _empty_eviction(sample: int, *, bind_sample: int = 200) -> EvictionProposal:
    return EvictionProposal(
        agent_id=None,
        calibrated_accuracy=None,
        sample_setups=sample,
        eligible=False,
        reason="insufficient_sample_for_rolling_50",
        scores={},
        rolling_window=ROLLING_SETUPS,
        bind_sample=bind_sample,
    )


class CalibrationEngine:
    def nightly_from_outcomes(
        self,
        desk: Any,
        *,
        apply: bool = False,
        evict: bool | None = None,
        revert: bool = False,
    ) -> CalibrationReport:
        if desk.mode.is_live:
            raise LiveTradingDisabled("calibration refuses live broker")
        verified, _ = desk.ledger.verify()
        if not verified:
            raise CalibrationError("ledger_integrity_failed")
        if revert:
            return self._revert(desk, ledger_verified=verified)

        tracker = AccuracyTracker()
        tracker.load_from_ledger(desk.ledger)
        outcomes = tracker.history
        if not outcomes:
            outcomes = desk.accuracy.history
        sample = len(outcomes)
        window_outcomes = outcomes[-ROLLING_SETUPS:] if outcomes else ()
        baseline = dict(desk.conviction_weights)
        bind_sample = int(desk.contract.min_sample_live_setups)
        if sample == 0:
            if apply or evict is True:
                raise CalibrationError("insufficient_sample_for_weights")
            return CalibrationReport(
                weights_from=baseline,
                weights_proposed=baseline,
                weights_applied=False,
                weights_update=WEIGHTS_UPDATE,
                eviction=_empty_eviction(0, bind_sample=bind_sample),
                eviction_applied=False,
                disabled_seats=dict(desk.disabled_seats),
                per_regime_accuracy={},
                calibrated_accuracy={},
                correlation_flags=(),
                sample_setups=0,
                rolling_window=ROLLING_SETUPS,
                ledger_verified=verified,
                live_trading_authority=False,
            )

        proposed = propose_weights(desk.contract.gate.conviction_weights, outcomes)
        eviction = propose_eviction(
            outcomes,
            window=ROLLING_SETUPS,
            bind_sample=bind_sample,
            disabled=desk.disabled_seats,
        )
        if evict is True and not eviction.eligible:
            raise CalibrationError(eviction.reason)
        flags = correlation_flags(window_outcomes)
        per_regime = {
            agent_id: per_regime_stats(window_outcomes, agent_id) for agent_id in GATING_AGENT_IDS
        }
        calibrated: dict[str, float] = {}
        for agent_id in GATING_AGENT_IDS:
            acc = calibrated_accuracy(window_outcomes, agent_id)
            if acc is not None:
                calibrated[agent_id] = acc

        eviction_applied = False
        weights_applied = False
        if apply:
            desk.push_weights(proposed, source=WEIGHTS_UPDATE)
            weights_applied = True
            if eviction.eligible and eviction.agent_id and evict is True:
                desk.disable_seat(eviction.agent_id, reason=EVICTION_RULE)
                eviction_applied = True
            desk.ledger.append(
                "calibration-nightly",
                {
                    "kind": "calibration_nightly",
                    "weights_from": baseline,
                    "weights_to": dict(desk.conviction_weights),
                    "effective_weights": desk.effective_weights(),
                    "eviction": {
                        "agent_id": eviction.agent_id,
                        "eligible": eviction.eligible,
                        "applied": eviction_applied,
                        "reason": eviction.reason,
                        "calibrated_accuracy": eviction.calibrated_accuracy,
                        "scores": dict(eviction.scores),
                        "rolling_window": eviction.rolling_window,
                        "bind_sample": eviction.bind_sample,
                    },
                    "correlation_flags": [
                        {
                            "agent_a": flag.agent_a,
                            "agent_b": flag.agent_b,
                            "comovement": flag.comovement,
                            "overlap": flag.overlap,
                        }
                        for flag in flags
                    ],
                    "sample_setups": sample,
                    "live_trading_authority": False,
                },
            )

        return CalibrationReport(
            weights_from=baseline,
            weights_proposed=proposed,
            weights_applied=weights_applied,
            weights_update=WEIGHTS_UPDATE,
            eviction=eviction,
            eviction_applied=eviction_applied,
            disabled_seats=dict(desk.disabled_seats),
            per_regime_accuracy=per_regime,
            calibrated_accuracy=calibrated,
            correlation_flags=flags,
            sample_setups=sample,
            rolling_window=ROLLING_SETUPS,
            ledger_verified=verified,
            live_trading_authority=False,
        )

    def _revert(self, desk: Any, *, ledger_verified: bool) -> CalibrationReport:
        previous = dict(desk.conviction_weights)
        restored = desk.revert_weights()
        desk.ledger.append(
            "calibration-revert",
            {
                "kind": "calibration_revert",
                "weights_from": previous,
                "weights_to": dict(restored),
                "disabled_seats": dict(desk.disabled_seats),
                "live_trading_authority": False,
            },
        )
        sample = len(outcomes_from_ledger(desk.ledger))
        return CalibrationReport(
            weights_from=previous,
            weights_proposed=dict(restored),
            weights_applied=False,
            weights_update=WEIGHTS_UPDATE,
            eviction=_empty_eviction(
                sample, bind_sample=int(desk.contract.min_sample_live_setups)
            ),
            eviction_applied=False,
            disabled_seats=dict(desk.disabled_seats),
            per_regime_accuracy={},
            calibrated_accuracy={},
            correlation_flags=(),
            sample_setups=len(desk.accuracy.history),
            rolling_window=ROLLING_SETUPS,
            ledger_verified=ledger_verified,
            live_trading_authority=False,
            reverted=True,
        )


def ingest_calibration_setups(desk: Any, setups: tuple[Any, ...]) -> None:
    """Play setups onto the desk so outcomes hit the ledger. Flatten paper seats between rounds."""
    for setup in setups:
        desk.run_round(setup.fixture)
        if desk.books.positions:
            desk.books.flatten_all()


def run_shipped_calibration(
    *,
    apply: bool = False,
    evict: bool | None = None,
    fixture_path: Path | None = None,
    revert: bool = False,
) -> CalibrationReport:
    from astra_desk.desk import TradingDesk

    load_contract()
    desk = TradingDesk(mode=DeskMode.PAPER_TRADING)
    if revert:
        return CalibrationEngine().nightly_from_outcomes(desk, revert=True)
    fixture = load_calibration_fixture(fixture_path)
    ingest_calibration_setups(desk, fixture.setups)
    return CalibrationEngine().nightly_from_outcomes(desk, apply=apply, evict=evict)
