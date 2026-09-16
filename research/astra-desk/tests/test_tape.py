from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_desk.cli import main
from astra_desk.desk import TradingDesk
from astra_desk.errors import BrokerCredentialsError, LiveTradingDisabled, MarketDataError
from astra_desk.features import (
    DERIVED_FEATURE_KEYS,
    EXPLICIT_FEATURE_KEYS,
    MIN_BARS_FOR_DERIVATION,
)
from astra_desk.market_data import (
    DATA_SANDBOX_BASE,
    AlpacaDataFeed,
    require_data_base_url,
)
from astra_desk.tape import (
    STRONG_EXPLICIT_FEATURES,
    apply_fetched_bars,
    features_from_bars,
    load_tape_fixture,
    materialize_setup,
    run_tape_round,
    run_tape_walk,
)
from astra_desk.types import Bar, RoundDecision
from astra_desk.walk_forward import WalkForwardReplay, load_walk_fixture, parse_walk_setup

REPO = Path(__file__).resolve().parents[1]
SHIPPED = REPO / "fixtures" / "tape" / "sample.json"
PHASE0 = REPO / "fixtures" / "walk_forward" / "phase0_sample.json"


def _bar(i: int, volume: float, *, close: float = 100.0, high: float | None = None) -> Bar:
    hi = close + 0.1 if high is None else high
    return Bar(
        ts=f"t-{i:02d}",
        open=close,
        high=hi,
        low=close - 0.1,
        close=close,
        volume=volume,
    )


def test_shipped_tape_deploy_refuse_skeptic() -> None:
    tape = load_tape_fixture(SHIPPED)
    assert len(tape.setups) == 3
    desk = TradingDesk()
    decisions = [desk.run_round(item.fixture).decision for item in tape.setups]
    assert decisions == [
        RoundDecision.DEPLOY,
        RoundDecision.REFUSE,
        RoundDecision.REFUSE,
    ]
    derived = tape.setups[0]
    assert derived.derived_keys == DERIVED_FEATURE_KEYS
    assert derived.explicit_keys == EXPLICIT_FEATURE_KEYS
    features = derived.fixture.features
    assert features is not None
    assert features.volume_z >= 2.0
    assert features.volume_lead_bars >= 2.0
    assert abs(features.price_change_pct) < 0.01


def test_shipped_tape_walk_and_round() -> None:
    tape, report = run_tape_walk(SHIPPED)
    assert tape.source == "fixture"
    assert report.setups_processed == 3
    assert report.halted is False
    assert report.live_trading_authority is False
    assert [item.decision for item in report.rounds] == [
        RoundDecision.DEPLOY,
        RoundDecision.REFUSE,
        RoundDecision.REFUSE,
    ]
    assert report.rounds[0].executed is True
    assert report.settlements[0] is not None
    assert report.settlements[0].reason == "take_profit"
    assert report.exit_criteria.met is True

    tape_round, result = run_tape_round(SHIPPED)
    assert tape_round.setups[0].fixture.setup_id == "tape-001-volume-lead"
    assert result.decision is RoundDecision.DEPLOY
    assert result.executed is True
    assert result.live_trading_authority is False


def test_walk_forward_loads_tape_schema() -> None:
    walk = load_walk_fixture(SHIPPED)
    report = WalkForwardReplay(TradingDesk()).run(walk)
    assert report.setups_total == 3
    assert report.rounds[0].decision is RoundDecision.DEPLOY
    phase0 = load_walk_fixture(PHASE0)
    assert len(phase0.setups) == 4


def test_missing_explicit_keys_fail_closed() -> None:
    bars = tuple(_bar(i, 100.0) for i in range(MIN_BARS_FOR_DERIVATION))
    with pytest.raises(MarketDataError, match="explicit_features"):
        features_from_bars(
            bars,
            explicit={"cancel_rate": 0.0},
            setup_id="missing-explicit",
        )


def test_too_few_bars_fail_closed() -> None:
    bars = tuple(_bar(i, 400.0) for i in range(MIN_BARS_FOR_DERIVATION - 1))
    with pytest.raises(MarketDataError, match="do not invent volume_z"):
        features_from_bars(
            bars,
            explicit=STRONG_EXPLICIT_FEATURES,
            setup_id="short-tape",
        )


def test_materialize_requires_bars_or_features() -> None:
    with pytest.raises(MarketDataError, match="complete features blob or bars"):
        materialize_setup(
            {
                "setup_id": "empty",
                "regime": "trending",
                "ev_estimate": 0.1,
                "price": 100.0,
                "broker": {"size_units": 40.0},
                "explicit_features": STRONG_EXPLICIT_FEATURES,
            }
        )


def test_fixture_path_does_not_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("fixture tape must not call urllib.request.urlopen")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    load_tape_fixture(SHIPPED)
    main(["tape", "--round"])
    main(["walk-forward", "--fixture", str(SHIPPED)])


def test_alpaca_from_env_fails_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    with pytest.raises(BrokerCredentialsError, match="ALPACA_API_KEY"):
        AlpacaDataFeed.from_env({})


def test_trading_host_rejected_for_market_data() -> None:
    with pytest.raises(LiveTradingDisabled, match="trading host"):
        require_data_base_url("https://api.alpaca.markets")
    with pytest.raises(LiveTradingDisabled, match="trading host"):
        require_data_base_url("https://paper-api.alpaca.markets")
    with pytest.raises(LiveTradingDisabled):
        AlpacaDataFeed(
            api_key="test-key",
            api_secret="test-secret",
            base_url="https://api.alpaca.markets",
        )
    with pytest.raises(LiveTradingDisabled):
        AlpacaDataFeed.from_env(
            {
                "ALPACA_API_KEY": "test-key",
                "ALPACA_API_SECRET": "test-secret",
                "ALPACA_DATA_URL": "https://paper-api.alpaca.markets",
            }
        )
    assert require_data_base_url("https://data.sandbox.alpaca.markets/") == DATA_SANDBOX_BASE


def test_live_broker_still_raises() -> None:
    with pytest.raises(LiveTradingDisabled):
        TradingDesk(live_broker=True)


def test_alpaca_fetch_with_fake_transport_deploys() -> None:
    shipped = json.loads(SHIPPED.read_text(encoding="utf-8"))
    lookback = shipped["setups"][0]["bars"]

    def transport(method: str, url: str, payload: dict | None, headers: dict[str, str]):
        assert method == "GET"
        assert url.startswith(DATA_SANDBOX_BASE)
        assert "data.sandbox.alpaca.markets" in url
        assert "paper-api.alpaca.markets" not in url
        assert "APCA-API-KEY-ID" in headers
        alpaca_bars = [
            {
                "t": item["ts"],
                "o": item["open"],
                "h": item["high"],
                "l": item["low"],
                "c": item["close"],
                "v": item["volume"],
            }
            for item in lookback
        ]
        return 200, {"bars": alpaca_bars}

    feed = AlpacaDataFeed(
        api_key="test-key",
        api_secret="test-secret",
        transport=transport,
    )
    bars = feed.fetch_bars("AAPL")
    assert len(bars) == len(lookback)
    overlay = dict(shipped["setups"][0])
    materialized = apply_fetched_bars(overlay, bars)
    walk = parse_walk_setup(materialized)
    result = TradingDesk().run_round(walk.fixture)
    assert result.decision is RoundDecision.DEPLOY
    assert result.executed is True
    assert result.live_trading_authority is False


def test_tape_live_mode_rejected(tmp_path: Path) -> None:
    payload = json.loads(SHIPPED.read_text(encoding="utf-8"))
    payload["mode"] = "live"
    path = tmp_path / "live-tape.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LiveTradingDisabled):
        load_tape_fixture(path)
