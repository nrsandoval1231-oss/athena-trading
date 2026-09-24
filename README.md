# Athena Trading

**Two Arms. One Mind. Continuous Wisdom.**

Athena Trading combines **Earnings Adaptive** and **Athena Predictive Intraday** through a shared LLM Decision Actor, Athena Core context and learning, Adaptive Position Defense (APDE), deterministic risk controls, and broker-confirmed execution.

[Read the canonical architecture](docs/ATHENA_TRADING_CANON.md) · [Current implementation](docs/IMPLEMENTATION_STATUS.md) · [Build sequence](docs/BUILD_PLAN.md)

![Athena Trading — owner-supplied dual-strategy architecture](docs/assets/athena-trading-canon.jpg)

## Start here

| Question | Source |
|---|---|
| What are we building? | [ATHENA_TRADING_CANON.md](docs/ATHENA_TRADING_CANON.md) |
| What works and what is missing? | [IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) |
| What should the builder do next? | [BUILD_PLAN.md](docs/BUILD_PLAN.md) |
| Which existing code can be reused? | [Repository map](docs/IMPLEMENTATION_STATUS.md#repository-map) |
| What are the old experiments? | [Research index](research/README.md) |
| What did the latest audit find? | [24 September audit](docs/audits/2026-09-24.md) |

## Current reality

The strategy/simulation and research infrastructure is substantial. The two canonical strategy arms and shared learning workflow are **not complete**. The current paper path submits entries but still lacks a reconciled broker position lifecycle.

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

The root suite does not cover the separately packaged Astra Desk tests. Current lint and imported-suite failures are recorded in [implementation status](docs/IMPLEMENTATION_STATUS.md#verification-baseline); a passing root count is not a complete repository gate.
