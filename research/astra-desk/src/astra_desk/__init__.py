"""Astra Desk Phase 0–3 — walk-forward / paper / checkpointed-paper / autonomous-paper scaffold.

LIVE TRADING AUTHORITY IS NOT GRANTED. Phase 3 is autonomous paper only.
"""

from __future__ import annotations

LIVE_TRADING_AUTHORITY = False
PHASE = 0
__version__ = "1.0.0"

from astra_desk.autonomous import AutonomousPaperLoop
from astra_desk.calibration import CalibrationEngine
from astra_desk.broker import AlpacaPaperBroker, FixtureBroker, PAPER_API_BASE
from astra_desk.contract import load_contract
from astra_desk.desk import TradingDesk
from astra_desk.errors import LiveTradingDisabled
from astra_desk.human_checkpoint import HumanCheckpointSequence
from astra_desk.market_data import AlpacaDataFeed
from astra_desk.paper_session import run_paper_session
from astra_desk.paper_trading import PaperPeriod
from astra_desk.report import build_operator_report
from astra_desk.store import LedgerStore, attach_store
from astra_desk.tape import features_from_bars, load_tape_fixture
from astra_desk.types import DeskMode, Regime
from astra_desk.walk_forward import WalkForwardReplay

__all__ = [
    "LIVE_TRADING_AUTHORITY",
    "PHASE",
    "AlpacaDataFeed",
    "AlpacaPaperBroker",
    "AutonomousPaperLoop",
    "CalibrationEngine",
    "DeskMode",
    "FixtureBroker",
    "HumanCheckpointSequence",
    "LedgerStore",
    "LiveTradingDisabled",
    "PAPER_API_BASE",
    "PaperPeriod",
    "Regime",
    "TradingDesk",
    "WalkForwardReplay",
    "attach_store",
    "build_operator_report",
    "features_from_bars",
    "load_contract",
    "load_tape_fixture",
    "run_paper_session",
    "__version__",
]
