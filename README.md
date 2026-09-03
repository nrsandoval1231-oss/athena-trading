# Athena Trading

Athena Trading is the dedicated trading research and strategy domain built on top of the domain-neutral Athena adaptive-intelligence architecture.

## Boundary

**Athena is not a trading algorithm.** Athena is the evidence-governed adaptive decision-learning system. Arena and Gauntlet belong to Athena Core.

**Athena Trading** owns trading-specific concerns: market data, strategies, position management, execution simulation, trading risk, backtesting, paper trading, and trading-domain adapters.

## Initial strategy program

1. **ORB-Momentum** — 15-minute opening-range breakout with breakout qualification, early momentum harvesting, adaptive position defense, dynamic trailing, peak-profit protection, and retracement/re-entry research.
2. **Predictive Intraday** — future Athena-guided predictive intraday strategy arm.
3. **Earnings Adaptive** — future event-driven earnings strategy arm.

## Architecture

```text
Athena Core
  ├── Arena
  ├── Gauntlet
  └── Domain Adapter Contract
          │
          ▼
Athena Trading
  ├── strategies/
  ├── market_data/
  ├── risk/
  ├── position_management/
  ├── execution/
  ├── backtesting/
  ├── paper/
  └── athena_adapter/
```

Trading is Athena's first major proving ground, not Athena's identity.

## Migration rule

Trading-specific code and documentation may be migrated from the historical `Athena` repository only after classification. Do not remove or rewrite historical Athena material until the source item is classified as `ATHENA_CORE`, `ARENA_GAUNTLET`, `TRADING_DOMAIN`, or `HISTORICAL`.

See `docs/ATHENA_TRADING_CANON.md` and `docs/ORB_MOMENTUM_V1.md`.
