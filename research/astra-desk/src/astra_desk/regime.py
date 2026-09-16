"""Regime classifier enum. Phase 0 uses the fixture-supplied regime."""

from __future__ import annotations

from astra_desk.errors import ContractError
from astra_desk.types import Regime, RoundFixture


class RegimeClassifier:
    """Feeds the same regime to every agent. No silent default."""

    def __init__(self, allowed: tuple[Regime, ...]):
        self.allowed = allowed

    def classify(self, fixture: RoundFixture) -> Regime:
        if fixture.regime not in self.allowed:
            raise ContractError(f"regime {fixture.regime} is not in contract")
        return fixture.regime
