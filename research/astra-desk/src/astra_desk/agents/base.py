"""Typed agent interfaces. Stub implementations live in stubs.py."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from astra_desk.accounting import CapitalBooks
from astra_desk.contract import DeskContract
from astra_desk.ledger import AccuracyTracker
from astra_desk.types import Regime, RoundFixture


@runtime_checkable
class Agent(Protocol):
    id: str
    role: str

    def evaluate(self, ctx: AgentContext) -> Any: ...


class AgentContext:
    def __init__(
        self,
        *,
        regime: Regime,
        fixture: RoundFixture,
        books: CapitalBooks,
        contract: DeskContract,
        accuracy: AccuracyTracker,
        proposed_notional: float | None = None,
    ) -> None:
        self.regime = regime
        self.fixture = fixture
        self.books = books
        self.contract = contract
        self.accuracy = accuracy
        self.proposed_notional = proposed_notional
