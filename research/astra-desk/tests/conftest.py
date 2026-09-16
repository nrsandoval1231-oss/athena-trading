from astra_desk.contract import load_contract
from astra_desk.desk import TradingDesk
from astra_desk.fixtures import deploy_fixture


def pytest_configure(config):  # noqa: ARG001
    load_contract()


def make_desk() -> TradingDesk:
    return TradingDesk()
