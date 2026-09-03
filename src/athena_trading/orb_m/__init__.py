"""Athena ORB-M v1.0 — deterministic 15-minute opening-range breakout engine.

The constitution is immutable and defines the strategy.  Athena adapts only the
management parameters enumerated in ``ADAPTIVE_SURFACE``; it does not invent or
alter the strategy itself.
"""
from .constitution import (
    ADAPTIVE_SURFACE, BreakoutAction, Constitution, ManagementParameters, TrailStage,
)
from .entry import (
    AttemptBudget, BreakoutSignal, OpeningRange, RiskFrame, build_risk_frame,
    detect_breakout, freeze_opening_range,
)
from .execution import ExecutionResult, run_position
from .management import (
    PositionState, action_for, exit_pressure, stage_for, trail_stop_r,
)
from .universe import (
    PoolProvenance, SelectionRule, SymbolStatistics, UniverseManifest,
    select_universe,
)
from .market import (
    Bar, PremarketContext, PriorSession, average_true_range, gap_percent,
    premarket_context, relative_volume, session_vwap, true_range,
)

__all__ = [
    "ADAPTIVE_SURFACE", "AttemptBudget", "Bar", "BreakoutAction", "BreakoutSignal",
    "Constitution", "ExecutionResult", "ManagementParameters", "OpeningRange",
    "PoolProvenance", "PositionState", "PremarketContext", "PriorSession",
    "RiskFrame", "SelectionRule", "SymbolStatistics", "TrailStage",
    "UniverseManifest",
    "action_for", "average_true_range", "build_risk_frame", "detect_breakout",
    "exit_pressure", "freeze_opening_range", "gap_percent", "premarket_context",
    "relative_volume", "run_position", "select_universe", "session_vwap",
    "stage_for", "trail_stop_r",
    "true_range",
]
