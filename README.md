# Athena Trading

**Two Arms. One Mind. Continuous Wisdom.**

Athena Trading combines **Earnings Adaptive** and **Athena Predictive Intraday** through a shared LLM Decision Actor, active TypeSafe Jev adjudication, Athena Core context and learning, Adaptive Position Defense (APDE), deterministic risk controls, and broker-confirmed execution.

[Read the canonical architecture](docs/ATHENA_TRADING_CANON.md) · [Current implementation](docs/IMPLEMENTATION_STATUS.md) · [Build sequence](docs/BUILD_PLAN.md)

![Athena Trading — owner-supplied dual-strategy architecture](docs/assets/athena-trading-canon.jpg)

## Start here

| Question | Source |
|---|---|
| What are we building? | [ATHENA_TRADING_CANON.md](docs/ATHENA_TRADING_CANON.md) |
| How does it stay online in the cloud? | [CLOUD_DEPLOYMENT.md](docs/CLOUD_DEPLOYMENT.md) |
| How do I start active paper trading? | [LIVE_PAPER.md](docs/LIVE_PAPER.md) |
| What works and what is missing? | [IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) |
| What should the builder do next? | [BUILD_PLAN.md](docs/BUILD_PLAN.md) |
| Which existing code can be reused? | [Repository map](docs/IMPLEMENTATION_STATUS.md#repository-map) |
| What are the old experiments? | [Research index](research/README.md) |
| What did the latest audit find? | [24 September audit](docs/audits/2026-09-24.md) |

## Current reality

The first active intraday paper pilot now connects current Alpaca data → hosted GPT-5.6 Terra proposal → Jev acceptance/veto → deterministic risk → protected paper order and broker-confirmed exit. See [active paper launch](docs/LIVE_PAPER.md). It is implemented and fixture-tested, but **not launched**: DigitalOcean deployment access and server-side provider credentials are still needed. The cloud service adds reboot restart, daily scheduling and recovery before new entries; see [cloud deployment](docs/CLOUD_DEPLOYMENT.md).

The complete two-strategy product, full APDE, and shared learning loop remain incomplete. Historical fixture walkers retain their documented limitations.

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
