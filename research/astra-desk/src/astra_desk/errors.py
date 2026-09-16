"""Fail-closed error types for Astra Desk Phase 0."""

from __future__ import annotations


class AstraDeskError(Exception):
    """Base error for the desk scaffold."""


class ContractError(AstraDeskError):
    """Contract file missing, malformed, or schema-invalid."""


class LiveTradingDisabled(AstraDeskError):
    """v1 has no live trading authority. Fail closed."""


class KillSwitchError(AstraDeskError):
    """Kill switch refused an operation (including non-human reenable)."""


class FrontManLogError(AstraDeskError):
    """Executor cannot act without the required log fields."""


class GateError(AstraDeskError):
    """Gate is missing required inputs and must refuse."""


class WalkForwardError(AstraDeskError):
    """Walk-forward fixture or replay failed closed."""


class AgentFeatureError(AstraDeskError):
    """Required setup features are missing or invalid. Fail closed."""


class PaperTradingError(AstraDeskError):
    """Phase 1 paper-period simulation failed closed."""


class CheckpointError(AstraDeskError):
    """Phase 2 human-checkpoint simulation failed closed."""


class AutonomousError(AstraDeskError):
    """Phase 3 autonomous paper loop failed closed."""


class CalibrationError(AstraDeskError):
    """Calibration failed closed (insufficient sample, live broker, or invalid apply)."""


class BrokerError(AstraDeskError):
    """Paper broker submit/cancel/track failed closed."""


class BrokerCredentialsError(BrokerError):
    """Alpaca paper credentials missing. Fail closed; do not invent keys."""


class MarketDataError(AstraDeskError):
    """Paper market-data / tape feed failed closed."""


class LedgerStoreError(AstraDeskError):
    """Durable ledger store failed closed (missing, corrupt, or clobber)."""
