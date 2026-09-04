"""Deterministic full-session ORB-M research runner.

The signal definitions below are implementation decisions frozen for the V1
research campaign. They use only completed bars from the same instrument; no
sector or market proxy is inferred when one is unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
from collections.abc import Sequence

from .constitution import Constitution, ManagementParameters
from .entry import AttemptBudget, BreakoutSignal, OpeningRange, detect_breakout
from .execution import ExecutionResult, run_position
from .management import exit_pressure
from .market import Bar, session_vwap


@dataclass(frozen=True)
class SessionEpisode:
    direction: str
    is_reentry: bool
    signal_minute: int
    result: ExecutionResult


@dataclass(frozen=True)
class SessionResult:
    episodes: tuple[SessionEpisode, ...]
    net_r: float
    initial_attempts: int
    reentries: int


def deterioration_by_minute(
    bars: Sequence[Bar], direction: str, opening: OpeningRange,
    parameters: ManagementParameters,
) -> dict[int, float]:
    """Compute only same-instrument, completed-bar deterioration signals."""
    ordered = sorted((bar for bar in bars if bar.minute >= opening.frozen_at_minute), key=lambda bar: bar.minute)
    pressure: dict[int, float] = {}
    seen: list[Bar] = []
    peak = None
    for bar in ordered:
        signals: list[str] = []
        prior = seen[-1] if seen else None
        prior5 = seen[-5:]
        if len(seen) >= 6 and fmean(x.volume for x in seen[-3:]) < fmean(x.volume for x in seen[-6:-3]):
            signals.append("declining_volume")
        if prior is not None:
            if direction == "LONG" and bar.high < prior.high:
                signals.append("lower_high")
            if direction == "SHORT" and bar.low > prior.low:
                signals.append("lower_high")
            current_move = abs(bar.close - bar.open)
            prior_move = abs(prior.close - prior.open)
            if prior_move > 0 and current_move < prior_move * 0.5:
                signals.append("momentum_deceleration")
            if (direction == "LONG" and bar.close < bar.open) or (direction == "SHORT" and bar.close > bar.open):
                signals.append("adverse_candle")
        vwap = session_vwap([item for item in bars if 0 <= item.minute <= bar.minute])
        if vwap is not None and ((direction == "LONG" and bar.close < vwap) or (direction == "SHORT" and bar.close > vwap)):
            signals.append("vwap_loss")
        favorable = bar.high if direction == "LONG" else -bar.low
        if peak is not None and favorable <= peak:
            signals.append("failed_new_high")
        peak = favorable if peak is None else max(peak, favorable)
        if prior5 and (bar.high - bar.low) > 2 * fmean(x.high - x.low for x in prior5):
            signals.append("volatility_regime_shift")
        if (direction == "LONG" and bar.close < opening.high) or (direction == "SHORT" and bar.close > opening.low):
            signals.append("failed_retest")
        pressure[bar.minute] = exit_pressure(signals, parameters)
        seen.append(bar)
    return pressure


def _exit_minute(result: ExecutionResult) -> int:
    return result.entry_minute + result.bars_held


def _reentry_signal(
    bars: Sequence[Bar], prior: SessionEpisode, opening: OpeningRange,
    parameters: ManagementParameters, constitution: Constitution,
) -> BreakoutSignal | None:
    result = prior.result
    frame = result.position.risk_frame
    peak_price = frame.price_at_r(max(0.0, result.peak_r))
    denominator = abs(peak_price - result.entry_price)
    if denominator <= 0:
        return None
    reset_seen = False
    for bar in sorted(bars, key=lambda item: item.minute):
        if bar.minute <= _exit_minute(result) or bar.minute >= constitution.session_deadline_minute:
            continue
        retracement = (
            (peak_price - bar.low) / denominator if prior.direction == "LONG"
            else (bar.high - peak_price) / denominator
        )
        if retracement >= parameters.moderate_setup_retracement_min:
            reset_seen = True
        if not reset_seen:
            continue
        if prior.direction == "LONG" and bar.close > opening.high:
            return BreakoutSignal("LONG", bar.minute, bar.close, opening, True)
        if prior.direction == "SHORT" and bar.close < opening.low:
            return BreakoutSignal("SHORT", bar.minute, bar.close, opening, True)
    return None


def run_full_session(
    opening: OpeningRange, bars: Sequence[Bar], atr14: float,
    constitution: Constitution, parameters: ManagementParameters,
) -> SessionResult:
    """Run both directions sequentially, including one qualified re-entry each."""
    budget = AttemptBudget(constitution)
    episodes: list[SessionEpisode] = []
    cursor = opening.frozen_at_minute
    while len(episodes) < 4:
        candidates: list[BreakoutSignal] = []
        for direction in ("LONG", "SHORT"):
            prior = next((episode for episode in reversed(episodes) if episode.direction == direction), None)
            if prior is None and budget.may_attempt(direction, is_reentry=False):
                signal = detect_breakout(bars, opening, constitution, after_minute=cursor, direction=direction)
            elif prior is not None and not prior.is_reentry and parameters.reentry_enabled and budget.may_attempt(direction, is_reentry=True):
                signal = _reentry_signal(bars, prior, opening, parameters, constitution)
                if signal is not None and signal.signal_minute < cursor:
                    signal = None
            else:
                signal = None
            if signal is not None:
                candidates.append(signal)
        if not candidates:
            break
        signal = min(candidates, key=lambda item: (item.signal_minute, item.direction))
        budget.record(signal.direction, is_reentry=signal.is_reentry)
        result = run_position(
            signal, bars, atr14, constitution, parameters,
            deterioration=deterioration_by_minute(bars, signal.direction, opening, parameters),
        )
        if result is None:
            break
        episode = SessionEpisode(signal.direction, signal.is_reentry, signal.signal_minute, result)
        episodes.append(episode)
        cursor = _exit_minute(result) + 1
        if cursor >= constitution.session_deadline_minute:
            break
    return SessionResult(
        tuple(episodes), sum(episode.result.net_r for episode in episodes),
        sum(not episode.is_reentry for episode in episodes),
        sum(episode.is_reentry for episode in episodes),
    )
