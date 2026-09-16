"""Fixture tape → features the rule agents already consume.

OHLCV can derive volume_z, volume_lead_bars, price_change_pct, spread_widen_pct,
invalidation_price, structure_quality, and a failed_breakout proxy.
cancel_rate, trade_to_quote_ratio, unique_participant_z, crowding_z, and
take_profit_r_multiples must be supplied explicitly — they are not in bars.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Mapping

from astra_desk.errors import AgentFeatureError, LiveTradingDisabled, MarketDataError
from astra_desk.features import (
    DERIVED_FEATURE_KEYS,
    EXPLICIT_FEATURE_KEYS,
    MIN_BARS_FOR_DERIVATION,
    SetupFeatures,
    has_complete_features,
    parse_features,
)
from astra_desk.market_data import parse_bar
from astra_desk.types import Bar, DeskMode, RoundFixture

TAPE_SCHEMA = "astra-desk/tape-v1"
SHIPPED_TAPE_NAME = "sample.json"
LIVE_TAPE_MODES = frozenset({"live", "live_broker", DeskMode.AUTONOMOUS.value})
QUIET_PRICE_PCT = 0.01
STRONG_EXPLICIT_FEATURES: dict[str, Any] = {
    "cancel_rate": 0.0,
    "trade_to_quote_ratio": 0.55,
    "unique_participant_z": 1.8,
    "crowding_z": 0.2,
    "take_profit_r_multiples": [1.0],
}


@dataclass(frozen=True)
class TapeSetup:
    fixture: RoundFixture
    bars: tuple[Bar, ...]
    derived_keys: tuple[str, ...]
    explicit_keys: tuple[str, ...]
    settlement_pnl: float | None = None


@dataclass(frozen=True)
class TapeFixture:
    setups: tuple[TapeSetup, ...]
    path: Path | None = None
    source: str = "fixture"


def default_tape_path() -> Path:
    env = os.environ.get("ASTRA_DESK_TAPE_FIXTURE")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "fixtures" / "tape" / SHIPPED_TAPE_NAME,
        Path.cwd() / "fixtures" / "tape" / SHIPPED_TAPE_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise MarketDataError(
        "cannot locate fixtures/tape/sample.json; set ASTRA_DESK_TAPE_FIXTURE"
    )


def _bar_pct(bar: Bar) -> float:
    if bar.open <= 0:
        return 0.0
    return (bar.close - bar.open) / bar.open


def features_from_bars(
    bars: tuple[Bar, ...],
    *,
    explicit: Mapping[str, Any],
    setup_id: str,
    side: str = "long",
    price: float | None = None,
) -> SetupFeatures:
    """Build SetupFeatures from OHLCV plus explicit microstructure fields."""
    if len(bars) < MIN_BARS_FOR_DERIVATION:
        raise MarketDataError(
            f"{setup_id}: need at least {MIN_BARS_FOR_DERIVATION} bars to derive features "
            f"(got {len(bars)}); do not invent volume_z"
        )
    missing = [key for key in EXPLICIT_FEATURE_KEYS if key not in explicit]
    if missing:
        raise MarketDataError(
            f"{setup_id}: bars cannot supply {missing}; provide explicit_features"
        )

    last = bars[-1]
    lookback = bars[:-1]
    volumes = [bar.volume for bar in lookback]
    vol_mean = mean(volumes)
    vol_std = pstdev(volumes) if len(volumes) > 1 else 0.0
    if vol_std == 0.0:
        volume_z = 0.0 if last.volume == vol_mean else 3.0
    else:
        volume_z = (last.volume - vol_mean) / vol_std

    threshold = vol_mean + vol_std if vol_std > 0 else vol_mean * 1.5
    lead = 0.0
    for bar in reversed(lookback):
        if bar.volume > threshold and abs(_bar_pct(bar)) < QUIET_PRICE_PCT:
            lead += 1.0
        else:
            break

    price_change_pct = _bar_pct(last)
    ranges = [(bar.high - bar.low) / bar.close for bar in lookback if bar.close > 0]
    last_range = (last.high - last.low) / last.close if last.close > 0 else 0.0
    median_range = sorted(ranges)[len(ranges) // 2] if ranges else last_range
    spread_widen_pct = max(0.0, last_range - median_range)

    entry = float(price) if price is not None else last.close
    recent = bars[-5:]
    if side == "short":
        invalidation = max(bar.high for bar in recent)
        if invalidation <= entry:
            raise MarketDataError(
                f"{setup_id}: derived short invalidation_price must be above price"
            )
    else:
        invalidation = min(bar.low for bar in recent)
        if invalidation >= entry:
            raise MarketDataError(
                f"{setup_id}: derived long invalidation_price must be below price"
            )

    wide = sum(1 for bar in bars if bar.close > 0 and (bar.high - bar.low) / bar.close > 0.05)
    structure_quality = max(0.4, 1.0 - 0.1 * wide)

    if "failed_breakout" in explicit:
        failed_breakout = bool(explicit["failed_breakout"])
    elif len(lookback) >= 2:
        prior_high = max(bar.high for bar in lookback)
        failed_breakout = last.high > prior_high and last.close < prior_high
    else:
        failed_breakout = False

    merged: dict[str, Any] = {
        "volume_z": float(volume_z),
        "volume_lead_bars": float(lead),
        "price_change_pct": float(price_change_pct),
        "spread_widen_pct": float(spread_widen_pct),
        "invalidation_price": float(explicit.get("invalidation_price", invalidation)),
        "structure_quality": float(explicit.get("structure_quality", structure_quality)),
        "failed_breakout": failed_breakout,
        "cancel_rate": explicit["cancel_rate"],
        "trade_to_quote_ratio": explicit["trade_to_quote_ratio"],
        "unique_participant_z": explicit["unique_participant_z"],
        "crowding_z": explicit["crowding_z"],
        "take_profit_r_multiples": explicit["take_profit_r_multiples"],
    }
    try:
        return parse_features(merged, setup_id=setup_id)
    except AgentFeatureError as exc:
        raise MarketDataError(str(exc)) from exc


def bar_to_dict(bar: Bar) -> dict[str, Any]:
    return {
        "ts": bar.ts,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def _parse_bars(raw: Any, setup_id: str) -> tuple[Bar, ...]:
    if not isinstance(raw, list) or not raw:
        raise MarketDataError(f"{setup_id}: bars must be a non-empty list")
    bars: list[Bar] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise MarketDataError(f"{setup_id}: bar must be an object")
        bars.append(parse_bar(item, setup_id))
    return tuple(bars)


def materialize_setup(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Fill features from bars when the authored feature blob is incomplete."""
    setup_id = str(raw.get("setup_id") or "tape")
    payload = dict(raw)
    bars_raw = raw.get("bars") or []
    bars = _parse_bars(bars_raw, setup_id) if isinstance(bars_raw, list) and bars_raw else ()
    features_raw = raw.get("features")
    explicit = raw.get("explicit_features") if isinstance(raw.get("explicit_features"), Mapping) else {}
    if has_complete_features(features_raw):
        return payload
    if not bars:
        raise MarketDataError(
            f"{setup_id}: provide a complete features blob or bars + explicit_features"
        )
    side = str(raw.get("side") or "long")
    price = float(raw["price"]) if "price" in raw else bars[-1].close
    overlay: dict[str, Any] = {}
    if isinstance(features_raw, Mapping):
        overlay.update(features_raw)
    overlay.update(dict(explicit))
    derived = features_from_bars(
        bars, explicit=overlay, setup_id=setup_id, side=side, price=price
    )
    payload["features"] = derived.as_dict()
    payload["price"] = price
    return payload


def load_first_setup_raw(path: Path | None = None) -> dict[str, Any]:
    """Read the first setup mapping without deriving features (Alpaca overlay)."""
    fixture_path = path or default_tape_path()
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketDataError(f"failed to read tape fixture {fixture_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MarketDataError("tape fixture must be a JSON object")
    setups_raw = data.get("setups")
    if not isinstance(setups_raw, list) or not setups_raw:
        raise MarketDataError("tape fixture requires a non-empty setups list")
    first = setups_raw[0]
    if not isinstance(first, Mapping):
        raise MarketDataError("each tape setup must be a mapping")
    return dict(first)


def load_tape_fixture(path: Path | None = None) -> TapeFixture:
    from astra_desk.walk_forward import parse_walk_setup

    fixture_path = path or default_tape_path()
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketDataError(f"failed to read tape fixture {fixture_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MarketDataError("tape fixture must be a JSON object")
    schema = data.get("schema")
    if schema not in {TAPE_SCHEMA, "astra-desk/walk-forward-v1"}:
        raise MarketDataError(f"unsupported tape fixture schema: {schema!r}")
    mode = data.get("mode", DeskMode.WALK_FORWARD_REPLAY.value)
    if mode in LIVE_TAPE_MODES:
        raise LiveTradingDisabled(f"tape fixture mode={mode} is not allowed")
    if mode != DeskMode.WALK_FORWARD_REPLAY.value:
        raise MarketDataError(
            f"tape fixture mode must be walk_forward_replay, got {mode}"
        )
    setups_raw = data.get("setups")
    if not isinstance(setups_raw, list) or not setups_raw:
        raise MarketDataError("tape fixture requires a non-empty setups list")
    setups: list[TapeSetup] = []
    for item in setups_raw:
        if not isinstance(item, Mapping):
            raise MarketDataError("each tape setup must be a mapping")
        materialized = materialize_setup(item)
        walk = parse_walk_setup(materialized)
        had_complete = has_complete_features(item.get("features"))
        setups.append(
            TapeSetup(
                fixture=walk.fixture,
                bars=walk.bars,
                derived_keys=() if had_complete else DERIVED_FEATURE_KEYS,
                explicit_keys=EXPLICIT_FEATURE_KEYS,
                settlement_pnl=walk.settlement_pnl,
            )
        )
    return TapeFixture(setups=tuple(setups), path=fixture_path, source="fixture")


def tape_to_walk_fixture(path: Path | None = None):
    from astra_desk.walk_forward import WalkFixture, WalkSetup

    tape = load_tape_fixture(path)
    setups = tuple(
        WalkSetup(
            fixture=item.fixture,
            bars=item.bars,
            settlement_pnl=item.settlement_pnl,
        )
        for item in tape.setups
    )
    return WalkFixture(setups=setups, path=tape.path)


def load_tape_as_walk(path: Path | str | None = None):
    """Load the shipped (or given) tape fixture as a walk-forward document."""
    return tape_to_walk_fixture(Path(path) if path else None)


def apply_fetched_bars(raw: Mapping[str, Any], bars: tuple[Bar, ...]) -> dict[str, Any]:
    """Replace lookback bars with a fetched series. Strip authored features so OHLCV wins."""
    payload = dict(raw)
    payload["bars"] = [bar_to_dict(bar) for bar in bars]
    payload.pop("features", None)
    payload.pop("forward_bars", None)
    return materialize_setup(payload)


def run_tape_walk(path: Path | None = None):
    from astra_desk.desk import TradingDesk
    from astra_desk.walk_forward import WalkFixture, WalkForwardReplay, WalkSetup

    tape = load_tape_fixture(path)
    walk = WalkFixture(
        setups=tuple(
            WalkSetup(
                fixture=item.fixture,
                bars=item.bars,
                settlement_pnl=item.settlement_pnl,
            )
            for item in tape.setups
        ),
        path=tape.path,
    )
    report = WalkForwardReplay(TradingDesk(mode=DeskMode.WALK_FORWARD_REPLAY)).run(walk)
    return tape, report


def run_tape_round(path: Path | None = None):
    from astra_desk.desk import TradingDesk

    tape = load_tape_fixture(path)
    result = TradingDesk(mode=DeskMode.WALK_FORWARD_REPLAY).run_round(tape.setups[0].fixture)
    return tape, result
