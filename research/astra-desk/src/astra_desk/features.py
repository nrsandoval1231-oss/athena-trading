"""Per-setup tape features consumed by rule-based voting agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Mapping

from astra_desk.errors import AgentFeatureError

REQUIRED_FEATURE_KEYS = (
    "volume_z",
    "volume_lead_bars",
    "price_change_pct",
    "cancel_rate",
    "trade_to_quote_ratio",
    "unique_participant_z",
    "spread_widen_pct",
    "invalidation_price",
    "structure_quality",
    "take_profit_r_multiples",
    "failed_breakout",
    "crowding_z",
)

# OHLCV can support these. Microstructure / thesis fields cannot.
DERIVED_FEATURE_KEYS = (
    "volume_z",
    "volume_lead_bars",
    "price_change_pct",
    "spread_widen_pct",
    "invalidation_price",
    "structure_quality",
    "failed_breakout",
)
EXPLICIT_FEATURE_KEYS = (
    "cancel_rate",
    "trade_to_quote_ratio",
    "unique_participant_z",
    "crowding_z",
    "take_profit_r_multiples",
)
MIN_BARS_FOR_DERIVATION = 8


@dataclass(frozen=True)
class SetupFeatures:
    """Deterministic inputs. No live feed, no model call."""

    volume_z: float
    volume_lead_bars: float
    price_change_pct: float
    cancel_rate: float
    trade_to_quote_ratio: float
    unique_participant_z: float
    spread_widen_pct: float
    invalidation_price: float
    structure_quality: float
    take_profit_r_multiples: tuple[float, ...]
    failed_breakout: bool
    crowding_z: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "volume_z": self.volume_z,
            "volume_lead_bars": self.volume_lead_bars,
            "price_change_pct": self.price_change_pct,
            "cancel_rate": self.cancel_rate,
            "trade_to_quote_ratio": self.trade_to_quote_ratio,
            "unique_participant_z": self.unique_participant_z,
            "spread_widen_pct": self.spread_widen_pct,
            "invalidation_price": self.invalidation_price,
            "structure_quality": self.structure_quality,
            "take_profit_r_multiples": list(self.take_profit_r_multiples),
            "failed_breakout": self.failed_breakout,
            "crowding_z": self.crowding_z,
        }


def parse_features(raw: Any, *, setup_id: str) -> SetupFeatures:
    if not isinstance(raw, Mapping):
        raise AgentFeatureError(f"{setup_id}: features mapping is required")
    missing = [key for key in REQUIRED_FEATURE_KEYS if key not in raw]
    if missing:
        raise AgentFeatureError(f"{setup_id}: missing required features {missing}")
    try:
        multiples = tuple(float(x) for x in raw["take_profit_r_multiples"])
    except (TypeError, ValueError) as exc:
        raise AgentFeatureError(
            f"{setup_id}: take_profit_r_multiples must be numbers"
        ) from exc
    if not multiples or any(m <= 0 for m in multiples):
        raise AgentFeatureError(
            f"{setup_id}: take_profit_r_multiples must be positive and non-empty"
        )
    try:
        return SetupFeatures(
            volume_z=float(raw["volume_z"]),
            volume_lead_bars=float(raw["volume_lead_bars"]),
            price_change_pct=float(raw["price_change_pct"]),
            cancel_rate=float(raw["cancel_rate"]),
            trade_to_quote_ratio=float(raw["trade_to_quote_ratio"]),
            unique_participant_z=float(raw["unique_participant_z"]),
            spread_widen_pct=float(raw["spread_widen_pct"]),
            invalidation_price=float(raw["invalidation_price"]),
            structure_quality=float(raw["structure_quality"]),
            take_profit_r_multiples=multiples,
            failed_breakout=bool(raw["failed_breakout"]),
            crowding_z=float(raw["crowding_z"]),
        )
    except (TypeError, ValueError) as exc:
        raise AgentFeatureError(f"{setup_id}: invalid feature value: {exc}") from exc


def strong_tape_features(**overrides: Any) -> SetupFeatures:
    """Volume-leads-price, organic prints, clear invalidation, no skeptic flags."""
    data: dict[str, Any] = {
        "volume_z": 2.4,
        "volume_lead_bars": 3.0,
        "price_change_pct": 0.0,
        "cancel_rate": 0.0,
        "trade_to_quote_ratio": 0.55,
        "unique_participant_z": 1.8,
        "spread_widen_pct": 0.0,
        "invalidation_price": 95.0,
        "structure_quality": 1.0,
        "take_profit_r_multiples": (1.0,),
        "failed_breakout": False,
        "crowding_z": 0.2,
    }
    data.update(overrides)
    return parse_features(data, setup_id="preset-strong")


def weak_volume_features(**overrides: Any) -> SetupFeatures:
    """No volume lead — scout/reader stay well below a deployable gate."""
    return strong_tape_features(
        volume_z=0.3,
        volume_lead_bars=0.0,
        price_change_pct=0.04,
        unique_participant_z=0.2,
        trade_to_quote_ratio=0.05,
        cancel_rate=0.35,
        structure_quality=0.4,
        **overrides,
    )


def skeptic_flag_features(**overrides: Any) -> SetupFeatures:
    """Scout/reader/exit still strong; skeptic flags subtract enough to refuse."""
    return strong_tape_features(
        failed_breakout=True,
        crowding_z=1.8,
        **overrides,
    )


def has_complete_features(raw: Any) -> bool:
    return isinstance(raw, Mapping) and all(key in raw for key in REQUIRED_FEATURE_KEYS)
