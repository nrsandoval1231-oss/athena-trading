# Architecture
The authoritative architecture is [Athena Trading Canon](ATHENA_TRADING_CANON.md), restored from the [owner-supplied diagram](assets/athena-trading-canon.jpg).

Both **Earnings Adaptive** and **Athena Predictive Intraday** share:
- Point-in-time world state and validated Athena context.
- TypeSafe Jev as the sole active decision model.
- Deterministic risk authorization.
- Execution, APDE, and broker reconciliation.
- Decision Episodes, replay, validation, and versioned learning through Athena Core.

[Implementation status and repository map](IMPLEMENTATION_STATUS.md) · [Active build sequence](BUILD_PLAN.md)

This file is a navigation entry point. Keep architectural requirements in the canon.
