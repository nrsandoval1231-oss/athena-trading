# Athena Trading

Canonical trading-domain repository powered by Athena Core.

Athena itself is domain-neutral adaptive intelligence. This repository owns market data, strategies, trading risk, position management/APDE, fills/execution simulation, backtesting, paper trading, and trading-domain experiments.

## Current baseline

`ORB-Momentum V1`: 15-minute opening range, 1-minute close confirmation, both directions independently live, ATR/structure initial R, early momentum harvest, APDE, dynamic trailing, peak-profit retention, and separately risked continuation re-entry.

## Layout

- `src/athena_trading/strategies/` — trading strategies
- `src/athena_trading/market_data/` — provider adapters
- `src/athena_trading/position_management/` — APDE/trailing/harvest logic
- `configs/` — trading-only risk/research configuration
- `docs/` — canon, strategy specs, provenance and migration record
- `tests/` — trading-domain tests

## Migration status

Forward ownership has migrated from the historical trading Lab inside `Athena`. Historical copies remain there only where required to reproduce old Arena/Gauntlet experiments and receipts. New trading work belongs here.

See `docs/MIGRATION_COMPLETE.md` and `docs/MIGRATION_MATRIX_V1.md`.
