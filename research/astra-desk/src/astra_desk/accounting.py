"""Honest capital accounting from contract starting equity. Tiny-sample metrics are None."""

from __future__ import annotations

import math
from statistics import mean, pstdev

from astra_desk.types import AccountingSnapshot, Position


class CapitalBooks:
    def __init__(self, starting_equity: float, currency: str) -> None:
        if starting_equity <= 0:
            raise ValueError("starting_equity must be > 0")
        self.starting_equity = float(starting_equity)
        self.currency = currency
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.peak_equity = float(starting_equity)
        self.max_drawdown_pct = 0.0
        self.day_start_equity = float(starting_equity)
        self._positions: list[Position] = []
        self._closed_pnls: list[float] = []

    @property
    def equity(self) -> float:
        return self.starting_equity + self.realized_pnl + self.unrealized_pnl

    @property
    def exposure_notional(self) -> float:
        return sum(abs(p.notional_usd) for p in self._positions)

    @property
    def exposure_pct(self) -> float:
        eq = self.equity
        if eq <= 0:
            return 100.0 if self._positions else 0.0
        return (self.exposure_notional / eq) * 100.0

    @property
    def daily_pnl(self) -> float:
        return self.equity - self.day_start_equity

    @property
    def current_drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 100.0
        return max(0.0, (self.peak_equity - self.equity) / self.peak_equity * 100.0)

    @property
    def daily_loss_pct(self) -> float:
        if self.day_start_equity <= 0:
            return 0.0
        loss = max(0.0, -self.daily_pnl)
        return (loss / self.day_start_equity) * 100.0

    @property
    def positions(self) -> tuple[Position, ...]:
        return tuple(self._positions)

    def _refresh_drawdown(self) -> None:
        eq = self.equity
        if eq > self.peak_equity:
            self.peak_equity = eq
        if self.peak_equity <= 0:
            self.max_drawdown_pct = 100.0
            return
        dd = (self.peak_equity - eq) / self.peak_equity * 100.0
        if dd > self.max_drawdown_pct:
            self.max_drawdown_pct = dd

    def mark_unrealized(self, unrealized_pnl: float) -> None:
        self.unrealized_pnl = float(unrealized_pnl)
        self._refresh_drawdown()

    def apply_realized(self, pnl: float) -> None:
        self.realized_pnl += float(pnl)
        self._closed_pnls.append(float(pnl))
        self._refresh_drawdown()

    def apply_drawdown_mark(self, drawdown_pct: float) -> None:
        """Test/fixture helper: mark equity down from peak without inventing fills."""
        target_eq = self.peak_equity * (1.0 - float(drawdown_pct) / 100.0)
        self.unrealized_pnl = target_eq - self.starting_equity - self.realized_pnl
        self._refresh_drawdown()

    def apply_daily_loss_mark(self, daily_loss_pct: float) -> None:
        target_eq = self.day_start_equity * (1.0 - float(daily_loss_pct) / 100.0)
        self.unrealized_pnl = target_eq - self.starting_equity - self.realized_pnl
        self._refresh_drawdown()

    def open_position(self, position: Position) -> None:
        self._positions.append(position)

    def close_all_positions(self, realized_pnl: float) -> None:
        """Close paper positions and record the given realized PnL. No invented fill."""
        self._positions.clear()
        self.unrealized_pnl = 0.0
        self.apply_realized(float(realized_pnl))

    def begin_session_day(self) -> None:
        """Start a new paper session day. Resets the daily-loss baseline."""
        self.day_start_equity = self.equity

    def flatten_all(self) -> None:
        """Realize current unrealized PnL and close every paper position."""
        if self.unrealized_pnl != 0.0:
            self.apply_realized(self.unrealized_pnl)
        self.unrealized_pnl = 0.0
        self._positions.clear()
        self._refresh_drawdown()

    def restore_snapshot(
        self,
        *,
        realized_pnl: float,
        unrealized_pnl: float,
        peak_equity: float,
        max_drawdown_pct: float,
        day_start_equity: float,
        closed_pnls: tuple[float, ...] | list[float],
        positions: tuple[Position, ...] | list[Position],
    ) -> None:
        """Reload persisted books. Does not invent fills or PnL."""
        self.realized_pnl = float(realized_pnl)
        self.unrealized_pnl = float(unrealized_pnl)
        self.peak_equity = float(peak_equity)
        self.max_drawdown_pct = float(max_drawdown_pct)
        self.day_start_equity = float(day_start_equity)
        self._closed_pnls = [float(p) for p in closed_pnls]
        self._positions = list(positions)

    def _sharpe(self) -> float | None:
        if len(self._closed_pnls) < 2:
            return None
        sigma = pstdev(self._closed_pnls)
        if sigma == 0:
            return None
        return mean(self._closed_pnls) / sigma

    def _sortino(self) -> float | None:
        if len(self._closed_pnls) < 2:
            return None
        downside = [p for p in self._closed_pnls if p < 0]
        if not downside:
            return None
        downside_dev = math.sqrt(sum(p * p for p in downside) / len(self._closed_pnls))
        if downside_dev == 0:
            return None
        return mean(self._closed_pnls) / downside_dev

    def _win_rate(self) -> float | None:
        n = len(self._closed_pnls)
        if n == 0:
            return None
        wins = sum(1 for p in self._closed_pnls if p > 0)
        return wins / n

    def _expectancy(self) -> float | None:
        if not self._closed_pnls:
            return None
        return mean(self._closed_pnls)

    def snapshot(self) -> AccountingSnapshot:
        return AccountingSnapshot(
            currency=self.currency,
            starting_equity=self.starting_equity,
            equity=self.equity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            max_drawdown_pct=self.max_drawdown_pct,
            exposure_pct=self.exposure_pct,
            daily_loss_pct=self.daily_loss_pct,
            sharpe=self._sharpe(),
            sortino=self._sortino(),
            win_rate=self._win_rate(),
            expectancy_per_setup=self._expectancy(),
            open_positions=self.positions,
            closed_trade_count=len(self._closed_pnls),
        )
