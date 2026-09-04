# Athena Trading

Canonical trading-domain repository powered by Athena Core.

Athena itself is domain-neutral adaptive intelligence. This repository owns market data, strategies, trading risk, position management/APDE, fills/execution simulation, backtesting, paper trading, and trading-domain experiments.

## Current baseline

`ORB-Momentum V1`: 15-minute opening range, 1-minute close confirmation, both directions independently live, ATR/structure initial R, early momentum harvest, APDE, dynamic trailing, peak-profit retention, and separately risked continuation re-entry.

The point-in-time 400-name candidate pool and 50-name universe are frozen. The
full-session TRAINING evaluator and AAT-V1 readiness boundary are implemented;
the current readiness state is
`READY_TRAINING_ONLY_WITH_PROVIDER_MODEL_MUTABILITY`. No sealed holdout has
been consumed, no performance or learned-adaptation claim has been made, and
execution authority remains `ABSENT`.

A preregistered TRAINING-only gate currently promotes one abstention lesson
(PASS versus standard sizing) and retains two rejected alternatives as negative
knowledge. This is input to a future bounded four-arm comparison, not evidence
that Athena adaptation works.

## Layout

- `src/athena_trading/strategies/` — trading strategies
- `src/athena_trading/market_data/` — provider adapters
- `src/athena_trading/position_management/` — APDE/trailing/harvest logic
- `configs/` — trading-only risk/research configuration
- `docs/` — canon, strategy specs, provenance and migration record
- `tests/` — trading-domain tests

## Migration status

Forward ownership has migrated from the historical trading Lab inside `Athena`. Historical copies remain there only where required to reproduce old Arena/Gauntlet experiments and receipts. New trading work belongs here.

See `docs/MIGRATION_COMPLETE.md`, `docs/MIGRATION_MATRIX_V1.md`,
`docs/ORB_M_STATUS.md`, and `docs/AAT_V1_TRAINING_RUNNER.md`.
