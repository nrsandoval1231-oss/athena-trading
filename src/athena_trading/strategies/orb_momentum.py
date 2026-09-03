from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Side(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class OrbLevels:
    high: float
    low: float

    def __post_init__(self) -> None:
        if self.high <= self.low:
            raise ValueError("opening-range high must exceed low")


def close_breakout(*, close: float, levels: OrbLevels) -> Side | None:
    """V1 trigger: a completed 1-minute close outside the frozen 15-minute range."""
    if close > levels.high:
        return Side.LONG
    if close < levels.low:
        return Side.SHORT
    return None


def initial_stop(*, side: Side, entry: float, atr: float, levels: OrbLevels, min_distance: float) -> float:
    """Freeze initial R using the tighter of OR structure and 1x ATR, with a minimum distance."""
    if atr <= 0 or min_distance <= 0:
        raise ValueError("atr and min_distance must be positive")
    if side is Side.LONG:
        raw = max(levels.low, entry - atr)
        return min(raw, entry - min_distance)
    raw = min(levels.high, entry + atr)
    return max(raw, entry + min_distance)


def retracement_fraction(*, side: Side, entry: float, peak: float, current: float) -> float:
    """Retracement is measured from actual entry to peak favorable excursion."""
    impulse = (peak - entry) if side is Side.LONG else (entry - peak)
    if impulse <= 0:
        raise ValueError("peak must represent positive favorable excursion")
    giveback = (peak - current) if side is Side.LONG else (current - peak)
    return max(0.0, giveback / impulse)
