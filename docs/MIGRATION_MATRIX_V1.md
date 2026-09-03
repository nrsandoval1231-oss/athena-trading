# Athena → Athena Trading Migration Matrix v1

This matrix records the first separation pass. Source files remain in Athena until the extracted trading repo is independently buildable/testable; Git history is the provenance record.

| Source area/artifact | Class | Destination/action |
|---|---|---|
| `gauntlet/` | ARENA_GAUNTLET / ATHENA_CORE | Remain in Athena |
| `src/athena_arena/` and domain-neutral policy lineage | ARENA_GAUNTLET / ATHENA_CORE | Remain in Athena |
| DecisionEpisode / Evidence / WorldState abstractions | ATHENA_CORE | Remain in Athena; generalize away from `instrument` naming later |
| `docs/ATHENA_VISION_ALIGNMENT.md` | MIXED | Preserve in Athena; trading vision restated in Athena Trading canon |
| `src/athena_lab/` | MIXED | Split, not wholesale move: generic replay/evidence primitives stay Core; provider/equity/strategy code migrates Trading |
| Alpaca market-data/provider adapters | TRADING_DOMAIN | Migrate to Athena Trading |
| Massive equity acquisition/reference/liquidity/instrument-event adapters | TRADING_DOMAIN | Migrate to Athena Trading |
| Alpaca paper broker adapter | TRADING_DOMAIN | Migrate to Athena Trading |
| trading fill/slippage/timing simulation | TRADING_DOMAIN | Migrate to Athena Trading |
| ORB candidate generation and trading Actor wiring | TRADING_DOMAIN | Migrate/rebuild in Athena Trading against frozen ORB V1 |
| `ATHENA_APDE_V1.md` | TRADING_DOMAIN implementation of general authority idea | Migrated under `docs/legacy/`; ORB V1 owns current trading behavior |
| historical paper-risk config | TRADING_DOMAIN / HISTORICAL | Migrated under `configs/legacy/`; not current ORB authority |
| NVDA/session-specific observed replay results | HISTORICAL TRADING | Preserve as legacy evidence; do not use consumed holdout for new tuning |
| 500-stock trading scale configs/readiness | TRADING_DOMAIN | Migrate only after ORB V1 runner is causal and test-complete |
| root Athena command-surface UI | HISTORICAL/ATHENA PRODUCT | Remain in Athena pending separate UI cleanup |
| `Testing-Grounds/Arena` duplicate tree | HISTORICAL/RESEARCH | Do not migrate blindly; deduplicate later in Athena |

## Separation rule
Athena Core owns domain-neutral learning, evidence, policy lineage, Arena, Gauntlet and authority. Athena Trading owns markets, strategies, providers, broker semantics, fills, PnL and trading risk.

## Current migration state
Phase 1 is complete: canonical trading identity, frozen ORB-Momentum V1 decisions, legacy APDE provenance, legacy paper-risk provenance, and separation matrix are now in Athena Trading.

Phase 2 code extraction is intentionally **copy-first**. Source code must not be deleted from Athena until Athena Trading has its own package/tests and both repos pass independently.
