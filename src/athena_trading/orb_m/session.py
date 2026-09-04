"""Session runner — both sides, re-entry, attempt budget, portfolio ceilings.

``run_position`` manages a single episode.  Portfolio ceilings
(``max_open_positions``, ``max_daily_loss_r``, ``max_drawdown_r``) are
constitutional and are not enforced there; this module is where they bite.

One symbol-session is walked causally: freeze the range, qualify breakouts with
BQS, size nothing beyond R accounting, attach deterioration pressure, then
consider a continuation re-entry when the budget and retracement bands allow.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .constitution import BreakoutAction, Constitution, ManagementParameters
from .deterioration import DeteriorationContext, pressure_by_minute
from .entry import (
    AttemptBudget, BreakoutSignal, OpeningRange, detect_breakout,
    freeze_opening_range,
)
from .execution import ExecutionResult, run_position
from .market import Bar, PriorSession, premarket_context, session_vwap
from .quality import BreakoutQualityInputs, BreakoutQualityResult, score_breakout


@dataclass
class SessionRiskState:
    """Shared ledger for constitutional portfolio ceilings across symbols.

    Drawdown is measured from the session's peak realized R, not from zero: a
    green morning does not license an unbounded giveback.
    """

    open_positions: int = 0
    realized_r: float = 0.0
    peak_realized_r: float = 0.0

    @property
    def drawdown_r(self) -> float:
        return max(0.0, self.peak_realized_r - self.realized_r)

    def veto_reason(self, constitution: Constitution) -> str | None:
        if self.open_positions >= constitution.max_open_positions:
            return "MAX_OPEN_POSITIONS"
        if self.realized_r <= -constitution.max_daily_loss_r:
            return "MAX_DAILY_LOSS"
        if self.drawdown_r >= constitution.max_drawdown_r:
            return "MAX_DRAWDOWN"
        return None

    def register_open(self) -> None:
        self.open_positions += 1

    def register_close(self, net_r: float) -> None:
        if self.open_positions > 0:
            self.open_positions -= 1
        self.realized_r += net_r
        self.peak_realized_r = max(self.peak_realized_r, self.realized_r)


@dataclass(frozen=True)
class EpisodeRecord:
    signal: BreakoutSignal
    quality: BreakoutQualityResult
    result: ExecutionResult | None
    skipped_reason: str | None = None


@dataclass
class SymbolSessionResult:
    opening_range: OpeningRange
    episodes: list[EpisodeRecord] = field(default_factory=list)
    halt_reason: str | None = None

    @property
    def realized_r(self) -> float:
        return sum(
            episode.result.net_r for episode in self.episodes
            if episode.result is not None)


def _retracement_fraction(*, direction: str, entry: float, peak_r: float,
                          risk_per_share: float, current_price: float) -> float:
    """Retracement of post-entry impulse, matching the frozen re-entry reference."""
    if peak_r <= 0 or risk_per_share <= 0:
        return 1.0
    if direction == "LONG":
        peak_price = entry + peak_r * risk_per_share
        impulse = peak_price - entry
        giveback = peak_price - current_price
    else:
        peak_price = entry - peak_r * risk_per_share
        impulse = entry - peak_price
        giveback = current_price - peak_price
    if impulse <= 0:
        return 1.0
    return max(0.0, giveback / impulse)


def _reentry_retracement_allowed(fraction: float,
                                 parameters: ManagementParameters) -> bool:
    """Strong setups tolerate a shallow reset; moderate setups need a deeper one.

    The open interval between the two bands is intentionally not tradeable —
    that mushy middle is where whipsaw re-entries concentrate.
    """
    if fraction <= parameters.strong_setup_retracement_max:
        return True
    return fraction >= parameters.moderate_setup_retracement_min


def _regular_bars(bars: Sequence[Bar]) -> list[Bar]:
    return sorted((bar for bar in bars if bar.minute >= 0), key=lambda item: item.minute)


def _opening_volume(bars: Sequence[Bar], constitution: Constitution) -> float:
    return sum(bar.volume for bar in bars
               if 0 <= bar.minute < constitution.opening_minutes)


def _first_open(bars: Sequence[Bar]) -> float | None:
    regular = [bar for bar in bars if bar.minute == 0]
    if not regular:
        ordered = _regular_bars(bars)
        return ordered[0].open if ordered else None
    return min(regular, key=lambda item: item.event_at).open


def _quality_for(signal: BreakoutSignal, bars: Sequence[Bar], prior: PriorSession,
                 constitution: Constitution, parameters: ManagementParameters, *,
                 market_alignment: float | None,
                 sector_alignment: float | None) -> BreakoutQualityResult:
    signal_bars = [bar for bar in bars if bar.minute == signal.signal_minute]
    if not signal_bars:
        return score_breakout(None, parameters)
    signal_bar = signal_bars[-1]
    through_signal = [bar for bar in bars if 0 <= bar.minute <= signal.signal_minute]
    vwap = session_vwap(through_signal)
    first_open = _first_open(bars)
    if vwap is None or first_open is None:
        return score_breakout(None, parameters)
    try:
        inputs = BreakoutQualityInputs(
            direction=signal.direction,
            opening_range=signal.opening_range,
            signal_bar=signal_bar,
            atr14=prior.atr14,
            opening_volume=_opening_volume(bars, constitution),
            average_opening_volume=prior.average_opening_volume,
            session_vwap=vwap,
            previous_close=prior.previous_close,
            first_open=first_open,
            premarket=premarket_context(bars),
            market_alignment=market_alignment,
            sector_alignment=sector_alignment,
        )
    except ValueError:
        return score_breakout(None, parameters)
    return score_breakout(inputs, parameters)


def _next_signal(bars: Sequence[Bar], opening_range: OpeningRange,
                 constitution: Constitution, budget: AttemptBudget, *,
                 direction: str, after_minute: int,
                 is_reentry: bool) -> BreakoutSignal | None:
    if not budget.may_attempt(direction, is_reentry=is_reentry):
        return None
    found = detect_breakout(bars, opening_range, constitution,
                            after_minute=after_minute, direction=direction)
    if found is None:
        return None
    if is_reentry:
        return BreakoutSignal(
            found.direction, found.signal_minute, found.signal_close,
            found.opening_range, is_reentry=True)
    return found


def run_symbol_session(
    bars: Sequence[Bar],
    prior: PriorSession,
    constitution: Constitution,
    parameters: ManagementParameters,
    *,
    risk: SessionRiskState | None = None,
    market_alignment: float | None = None,
    sector_alignment: float | None = None,
    market_alignment_by_minute: dict[int, float] | None = None,
    sector_alignment_by_minute: dict[int, float] | None = None,
) -> SymbolSessionResult:
    """Run one symbol through one session under constitutional ceilings.

    Both directions stay live and share the attempt budget.  A failed long does
    not spend the short budget, and vice versa.  This runner never grants
    live-money authority — it only simulates episodes against supplied bars.
    """
    ledger = risk if risk is not None else SessionRiskState()
    opening_range = freeze_opening_range(bars, constitution)
    result = SymbolSessionResult(opening_range=opening_range)
    budget = AttemptBudget(constitution)
    cursor = {"LONG": opening_range.frozen_at_minute, "SHORT": opening_range.frozen_at_minute}
    last_episode: dict[str, EpisodeRecord] = {}

    while True:
        veto = ledger.veto_reason(constitution)
        if veto is not None:
            result.halt_reason = veto
            break

        candidates: list[BreakoutSignal] = []
        for direction in ("LONG", "SHORT"):
            # Initial attempt from the direction cursor.
            initial = _next_signal(
                bars, opening_range, constitution, budget, direction=direction,
                after_minute=cursor[direction], is_reentry=False)
            if initial is not None:
                candidates.append(initial)

            # Continuation re-entry only after a prior episode on this side.
            if not parameters.reentry_enabled or direction not in last_episode:
                continue
            prior_episode = last_episode[direction]
            if prior_episode.result is None or prior_episode.skipped_reason:
                continue
            if not budget.may_attempt(direction, is_reentry=True):
                continue
            exit_minute = prior_episode.result.position.fills[-1]["minute"]
            reentry = _next_signal(
                bars, opening_range, constitution, budget, direction=direction,
                after_minute=max(cursor[direction], exit_minute + 1),
                is_reentry=True)
            if reentry is None:
                continue
            frame = prior_episode.result.position.risk_frame
            # Retracement measured at the re-entry signal close.
            frac = _retracement_fraction(
                direction=direction,
                entry=prior_episode.result.entry_price,
                peak_r=prior_episode.result.peak_r,
                risk_per_share=frame.risk_per_share,
                current_price=reentry.signal_close,
            )
            if not _reentry_retracement_allowed(frac, parameters):
                cursor[direction] = reentry.signal_minute + 1
                continue
            candidates.append(reentry)

        if not candidates:
            break

        signal = min(candidates, key=lambda item: (item.signal_minute, item.direction))
        cursor[signal.direction] = signal.signal_minute + 1

        quality = _quality_for(
            signal, bars, prior, constitution, parameters,
            market_alignment=market_alignment, sector_alignment=sector_alignment)

        if not quality.ok or quality.action is BreakoutAction.PASS:
            record = EpisodeRecord(
                signal, quality, None,
                skipped_reason=quality.reason or "PASS")
            result.episodes.append(record)
            # A PASSed initial still consumes the attempt: otherwise a weak
            # print would be retried forever inside the same chop.
            if budget.may_attempt(signal.direction, is_reentry=signal.is_reentry):
                budget.record(signal.direction, is_reentry=signal.is_reentry)
            last_episode[signal.direction] = record
            continue

        veto = ledger.veto_reason(constitution)
        if veto is not None:
            result.halt_reason = veto
            result.episodes.append(EpisodeRecord(signal, quality, None, skipped_reason=veto))
            break

        if not budget.may_attempt(signal.direction, is_reentry=signal.is_reentry):
            result.episodes.append(EpisodeRecord(
                signal, quality, None, skipped_reason="ATTEMPT_BUDGET"))
            break

        budget.record(signal.direction, is_reentry=signal.is_reentry)
        ledger.register_open()

        # Entry fills on the bar after the signal; anchor detectors there.
        det_context = DeteriorationContext(
            direction=signal.direction,
            entry_minute=signal.signal_minute + 1,
            entry_price=signal.signal_close,
            opening_range=opening_range,
            market_alignment_by_minute=market_alignment_by_minute,
            sector_alignment_by_minute=sector_alignment_by_minute,
        )
        pressure = pressure_by_minute(bars, det_context, parameters)
        execution = run_position(
            signal, bars, prior.atr14, constitution, parameters,
            deterioration=pressure)

        if execution is None:
            ledger.register_close(0.0)
            record = EpisodeRecord(signal, quality, None, skipped_reason="NO_FILL")
            result.episodes.append(record)
            last_episode[signal.direction] = record
            continue

        ledger.register_close(execution.net_r)
        record = EpisodeRecord(signal, quality, execution)
        result.episodes.append(record)
        last_episode[signal.direction] = record
        cursor[signal.direction] = max(
            cursor[signal.direction],
            execution.position.fills[-1]["minute"] + 1 if execution.position.fills
            else signal.signal_minute + 1)

    return result
