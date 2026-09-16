from __future__ import annotations

import json

import pytest

from astra_desk.accounting import CapitalBooks
from astra_desk.broker import (
    PAPER_API_BASE,
    AlpacaPaperBroker,
    FixtureBroker,
    build_paper_broker,
    require_paper_base_url,
)
from astra_desk.cli import main
from astra_desk.desk import TradingDesk
from astra_desk.errors import BrokerCredentialsError, BrokerError, LiveTradingDisabled
from astra_desk.fixtures import deploy_fixture
from astra_desk.kill_switch import KillSwitch
from astra_desk.types import Position


def test_fixture_submit_cancel_track() -> None:
    broker = FixtureBroker(fill_on_track=False)
    submitted = broker.submit(symbol="PAPER-TEST", side="long", notional_usd=20.0)
    assert submitted.paper is True
    assert submitted.live_trading_authority is False
    assert submitted.broker == "fixture"
    assert submitted.status == "accepted"
    tracked = FixtureBroker(fill_on_track=True)
    filled_src = tracked.submit(symbol="PAPER-TEST", side="long", notional_usd=20.0)
    filled = tracked.track(filled_src.order_id)
    assert filled.status == "filled"
    canceled = broker.cancel(submitted.order_id)
    assert canceled.status == "canceled"
    assert broker.track(submitted.order_id).status == "canceled"


def test_fixture_cannot_cancel_filled() -> None:
    broker = FixtureBroker()
    order = broker.submit(symbol="PAPER-TEST", side="long", notional_usd=10.0)
    broker.track(order.order_id)
    with pytest.raises(BrokerError, match="cannot cancel filled"):
        broker.cancel(order.order_id)


def test_alpaca_from_env_fails_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    with pytest.raises(BrokerCredentialsError, match="ALPACA_API_KEY"):
        AlpacaPaperBroker.from_env({})
    with pytest.raises(BrokerCredentialsError):
        build_paper_broker("alpaca-paper", environ={})


def test_non_paper_url_rejected() -> None:
    with pytest.raises(LiveTradingDisabled, match="live Alpaca host rejected"):
        require_paper_base_url("https://api.alpaca.markets")
    with pytest.raises(LiveTradingDisabled, match="rejected"):
        AlpacaPaperBroker(
            api_key="test-key",
            api_secret="test-secret",
            base_url="https://api.alpaca.markets",
        )
    with pytest.raises(LiveTradingDisabled):
        AlpacaPaperBroker(
            api_key="test-key",
            api_secret="test-secret",
            base_url="http://paper-api.alpaca.markets",
        )
    with pytest.raises(LiveTradingDisabled):
        AlpacaPaperBroker.from_env(
            {
                "ALPACA_API_KEY": "test-key",
                "ALPACA_API_SECRET": "test-secret",
                "ALPACA_BASE_URL": "https://api.alpaca.markets",
            }
        )
    assert require_paper_base_url("https://paper-api.alpaca.markets/") == PAPER_API_BASE


def test_live_mode_still_raises() -> None:
    with pytest.raises(LiveTradingDisabled):
        TradingDesk(live_broker=True)
    with pytest.raises(LiveTradingDisabled):
        build_paper_broker("live")


def test_desk_round_hits_fixture_broker() -> None:
    desk = TradingDesk(broker_kind="fixture")
    result = desk.run_round(deploy_fixture())
    assert result.executed is True
    assert result.broker_order is not None
    assert result.broker_order.broker == "fixture"
    assert result.broker_order.status == "filled"
    assert result.broker_order.notional_usd == 20.0
    assert result.broker_order.live_trading_authority is False
    assert result.live_trading_authority is False


def test_kill_switch_cancels_working_and_flattens_books() -> None:
    broker = FixtureBroker(fill_on_track=False)
    books = CapitalBooks(500.0, "USD")
    books.open_position(
        Position(symbol="PAPER-TEST", notional_usd=20.0, correlation_group="index-beta")
    )
    working = broker.submit(symbol="PAPER-TEST", side="long", notional_usd=20.0)
    assert working.status == "accepted"
    ks = KillSwitch(drawdown_pct=8.0, daily_loss_limit_pct=5.0)
    books.apply_drawdown_mark(8.0)
    state = ks.poll(books, broker=broker)
    assert state.halted is True
    assert books.positions == ()
    assert broker.track(working.order_id).status == "canceled"


def test_alpaca_submit_cancel_track_with_fake_transport() -> None:
    store: dict[str, dict] = {}

    def transport(method: str, url: str, payload: dict | None, headers: dict[str, str]):
        assert url.startswith(PAPER_API_BASE)
        assert "APCA-API-KEY-ID" in headers
        assert "APCA-API-SECRET-KEY" in headers
        path = url[len(PAPER_API_BASE) :]
        if method == "POST" and path == "/v2/orders":
            assert payload is not None
            order = {
                "id": "alp-1",
                "symbol": payload["symbol"],
                "side": payload["side"],
                "notional": payload["notional"],
                "status": "accepted",
                "client_order_id": payload.get("client_order_id"),
                "filled_qty": "0",
            }
            store[order["id"]] = order
            return 200, order
        if method == "GET" and path.startswith("/v2/orders/"):
            order_id = path.rsplit("/", 1)[-1]
            current = dict(store[order_id])
            if current["status"] == "accepted":
                current["status"] = "filled"
                current["filled_qty"] = "1"
                store[order_id] = current
            return 200, current
        if method == "DELETE" and path.startswith("/v2/orders/"):
            order_id = path.rsplit("/", 1)[-1]
            store[order_id]["status"] = "canceled"
            return 204, {}
        if method == "GET" and path == "/v2/account":
            return 200, {"status": "ACTIVE", "trading_blocked": False, "account_blocked": False}
        raise AssertionError(f"unexpected {method} {path}")

    broker = AlpacaPaperBroker(
        api_key="test-key",
        api_secret="test-secret",
        transport=transport,
    )
    submitted = broker.submit(symbol="AAPL", side="long", notional_usd=20.0, client_order_id="r1")
    assert submitted.order_id == "alp-1"
    assert submitted.status == "accepted"
    assert submitted.paper is True
    filled = broker.track(submitted.order_id)
    assert filled.status == "filled"

    store["alp-1"]["status"] = "accepted"
    canceled = broker.cancel("alp-1")
    assert canceled.status == "canceled"

    report = broker.preflight()
    assert report["paper"] is True
    assert report["live_trading_authority"] is False
    assert report["base_url"] == PAPER_API_BASE


def test_cli_round_uses_fixture_broker(capsys) -> None:
    code = main(["round", "--broker", "fixture"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["broker_order"]["broker"] == "fixture"
    assert payload["broker_order"]["status"] == "filled"
    assert payload["live_trading_authority"] is False


def test_cli_alpaca_paper_fails_closed_without_keys(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    code = main(["round", "--broker", "alpaca-paper"])
    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.out)
    assert payload["refused"] is True
    assert "ALPACA_API_KEY" in payload["reason"]
    assert payload["live_trading_authority"] is False

    code = main(["broker-preflight", "--broker", "alpaca-paper"])
    captured = capsys.readouterr()
    assert code == 1
    pre = json.loads(captured.out)
    assert pre["refused"] is True


def test_cli_broker_preflight_fixture(capsys) -> None:
    code = main(["broker-preflight", "--broker", "fixture"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["broker"] == "fixture"
    assert payload["paper"] is True
    assert payload["live_trading_authority"] is False


def test_cli_rejects_live_broker_choice() -> None:
    with pytest.raises(SystemExit):
        main(["round", "--broker", "live"])
    with pytest.raises(SystemExit):
        main(["broker-preflight", "--broker", "live"])
