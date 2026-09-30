# Athena Trading

**Two Arms. One Mind. Continuous Wisdom.**

Athena Trading combines **Earnings Adaptive** and **Athena Predictive Intraday** through a shared TypeSafe Jev decision model, Athena Core context and learning, Adaptive Position Defense (APDE), deterministic risk controls, and broker-confirmed execution.

[Read the canonical architecture](docs/ATHENA_TRADING_CANON.md) · [Current implementation](docs/IMPLEMENTATION_STATUS.md) · [Build sequence](docs/BUILD_PLAN.md)

![Athena Trading — owner-supplied dual-strategy architecture](docs/assets/athena-trading-canon.jpg)

## Start here

| Question | Source |
|---|---|
| What are we building? | [ATHENA_TRADING_CANON.md](docs/ATHENA_TRADING_CANON.md) |
| How does it stay online in the cloud? | [CLOUD_DEPLOYMENT.md](docs/CLOUD_DEPLOYMENT.md) |
| How does it learn from trades? | [LEARNING.md](docs/LEARNING.md) |
| How do I start active paper trading? | [LIVE_PAPER.md](docs/LIVE_PAPER.md) |
| What works and what is missing? | [IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) |
| What should the builder do next? | [BUILD_PLAN.md](docs/BUILD_PLAN.md) |
| Which existing code can be reused? | [Repository map](docs/IMPLEMENTATION_STATUS.md#repository-map) |
| What are the old experiments? | [Research index](research/README.md) |
| What did the latest audit find? | [Jev and learning audit, 24 September 2026](docs/audits/2026-09-24-jev-learning.md). The earlier inventory remains [2026-09-24](docs/audits/2026-09-24.md). |

## Current reality

The bounded intraday paper path connects current Alpaca data → strategy candidate → **Jev decision** → deterministic risk → protected paper order and broker-confirmed exit. Jev is the only active model; Terra and Ollama are removed from this path.

Completed trades become verified experience available to later decisions. The cloud supervisor reviews each day after 20:15 Eastern; only prospectively validated, temporary abstention rules can be promoted. See [learning](docs/LEARNING.md) and [cloud deployment](docs/CLOUD_DEPLOYMENT.md).

The implementation is locally verified but **not deployed**: cloud access and server-side Alpaca/TypeSafe credentials remain unavailable. The complete Earnings arm, full APDE, comprehensive forecasting and general Athena Core integration are incomplete. Learning from observations is implemented; improved trading performance is not established.

ORB-M is a reusable intraday component and baseline. AAT experiments are research history. The existing Ollama B/C/D runner uses identical empty-memory requests; it does not yet demonstrate Athena learning.

**Execution remains PAPER or ABSENT. LIVE is rejected.** The consumed AAT-V1 holdout remains INVALID_EVIDENCE and must not be reused.

## Local verification and demonstrations

From the repository root, in a Python 3.11+ environment:

```bash
python -m pip install pytest ruff==0.15.8 .
python -m pytest -q
python scripts/run_orb_m_paper.py --walk-bars
```

The bar walk is a fixture demonstration. It is not the completed dual-strategy bot.

The root suite does not cover the separately packaged Astra Desk tests. The repaired consolidated gate and historical findings are recorded in [implementation status](docs/IMPLEMENTATION_STATUS.md#verification); run both packages for a complete local gate.
