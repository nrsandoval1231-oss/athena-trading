# Athena Trading

Canonical trading-domain repository powered by Athena Core.

Athena itself is domain-neutral adaptive intelligence. This repository owns market data, strategies, trading risk, position management/APDE, fills/execution simulation, backtesting, paper trading, and trading-domain experiments.

## Current baseline

`ORB-Momentum V1`: 15-minute opening range, 1-minute close confirmation, both directions independently live, ATR/structure initial R, early momentum harvest, APDE, dynamic trailing, peak-profit retention, and separately risked continuation re-entry.

The point-in-time 400-name candidate pool and 50-name universe are frozen. The
full-session TRAINING evaluator and AAT-V1 readiness boundary are implemented;
the current readiness state is
`READY_TRAINING_ONLY_WITH_PROVIDER_MODEL_MUTABILITY`. The sealed holdout is
consumed (`INVALID_EVIDENCE`) and is not reopened here. No performance or
learned-adaptation claim has been made. AAT / TRAINING execution authority
remains `ABSENT`.

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
negative-knowledge registry that future export paths must check. The paper
path does not read, write, or reseal that registry.

Paper execution is a separate path. An operator may grant `PAPER` authority so
ORB-M decisions can be sized, halt-gated, and submitted to an Alpaca **paper**
adapter (or a fixture double). `LIVE` is not a value and is rejected. Default
authority in `configs/orb_m_paper_session_v1.json` is `ABSENT`. This is not
live trading.

## Layout

- `src/athena_trading/strategies/` — trading strategies
- `src/athena_trading/orb_m/` — ORB-M engine (constitution, entry, management, fill sim)
- `src/athena_trading/execution/` — PAPER-only authority, order intent, broker protocol
- `src/athena_trading/risk/` — constitutional halt gate (refuse before send)
- `src/athena_trading/paper/` — paper session runner, fixture broker, Alpaca paper adapter
- `src/athena_trading/market_data/` — provider adapters (Massive historical; Alpaca corporate actions)
- `src/athena_trading/position_management/` — APDE/trailing/harvest logic
- `configs/` — trading-only risk/research configuration
- `docs/` — canon, strategy specs, provenance and migration record
- `tests/` — trading-domain tests

## Paper session (not live trading)

The paper loop consumes ORB-M campaign decisions from `run_symbol_session`,
builds an entry `OrderIntent`, refuses the send if `SessionRiskState` would
breach `max_open_positions` / `max_daily_loss_r` / `max_drawdown_r`, then
submits / cancels / tracks through a `BrokerAdapter`.

It does **not** stream harvests, trails, or hard-flat exits to the broker;
those remain engine-side. It does **not** pull Massive bars. It does **not**
enable live money. It does **not** reopen the AAT holdout.

Deterministic dry-run (no network, no API keys):

```text
python scripts/run_orb_m_paper.py --dry-run
```

Halt-gate demonstration (still no network):

```text
python scripts/run_orb_m_paper.py --dry-run --halt-demo
```

Optional Alpaca paper call. Missing `ALPACA_API_KEY` / `ALPACA_API_SECRET`
fails closed. With keys, the default is a read-only preflight; `--submit-demo`
would submit the synthetic demo to paper and is never implied by `--dry-run`.

```text
python scripts/run_orb_m_paper.py --broker alpaca --authority PAPER
```

With no flags, config authority stays `ABSENT` and nothing is sent.

## Migration status

Forward ownership has migrated from the historical trading Lab inside `Athena`. Historical copies remain there only where required to reproduce old Arena/Gauntlet experiments and receipts. New trading work belongs here.

See `docs/MIGRATION_COMPLETE.md`, `docs/MIGRATION_MATRIX_V1.md`,
`docs/ORB_M_STATUS.md`, and `docs/AAT_V1_TRAINING_RUNNER.md`.
