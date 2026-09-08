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

A preregistered TRAINING-only gate promotes one abstention lesson (PASS versus
standard sizing) and retains two rejected alternatives as negative knowledge.
The subsequent 300-opportunity four-arm TRAINING comparison is complete and
audit-passing, but remains in-sample prompt-steering evidence—not proof that
Athena adaptation works. The final claim contract is frozen; the one-time
sealed holdout still requires separate authorization and remains untouched.
The single-use authorization gateway and deterministic claim scorer are ready.
The authorized sealed holdout has now been identity-verified and exported, so
it is consumed and must never be tuned against, resealed, or reused. No final
claim is supported: the frozen compiler found only 249 of the required 300
qualified opportunities, so evaluation ended `INVALID_EVIDENCE` before any
Actor call.
The consumed dataset assignment and authorization are recorded in a tracked
negative-knowledge registry that future export paths must check.

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

## Proposed next experiment

`docs/AAT_V2_VISION.md`, `docs/AAT_V2_PLAN.md` and
`docs/EXOGENOUS_SOURCES_V1.md` propose AAT-V2: an exogenous (non-price)
evidence layer, provider-portable Actor identity, and a non-consuming holdout
sufficiency probe.

Phases 1 and 2 of that plan are built. `src/athena_trading/exogenous/`
enforces point-in-time visibility over non-price evidence and compresses it
into a frozen, bucketed feature set; it imports nothing from `orb_m/` or
`aat_v1/`, and the strategy suite passes with the package deleted.
`aat_v1/providers.py` makes Actor identity provider-scoped without moving any
registered V1 digest, and `probe_holdout_sufficiency` answers the
is-the-dataset-big-enough question without consuming an authorization — the
gap that cost the V1 holdout at 249 of 300.

Phase 3's machinery is built in `src/athena_trading/aat_v2/`, and the V2
dataset assignment is drawn and frozen at
`configs/aat_v2_dataset_assignment_v1.json` — 35 TRAINING and 15 holdout
symbols, split by symbol, drawn before any exogenous feature existed. The
comparison itself is blocked on data acquisition: the claim contract requires a
measured exogenous coverage rate and refuses to freeze against an intended one.

No phase holds registered evidence or supports any claim. No contract is
frozen and no protocol registered. AAT-V1 remains frozen and closed, and its
`PROVIDER_MODEL_MUTABILITY` limitation stands.
