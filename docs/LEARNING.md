# Paper outcome learning

Owner direction: Jev is the sole active model. Evaluate every trade, retain experience, review the day after after-hours, and improve decisions through evidence. The implementation is `paper/learning.py`, using the existing Athena adapter identity contracts. This is trading-specific policy, not a new general-purpose Core.

## What runs

1. Strategy code builds a bounded candidate and computes its features. The runner retrieves prior experience and active validated rules for the same account, symbol, direction, policy and Jev question identity.
2. Code enforces any applicable learned abstention rule. Otherwise Jev chooses TAKE_TRADE, NO_TRADE or INSUFFICIENT_EVIDENCE. Deterministic risk still authorizes the exact entry.
3. Broker-confirmed flatness makes the episode eligible for outcome evaluation. Entry and exit quantities must balance, the reserved entry must be present, prices must be finite, and the decision receipt must match the reservation. Nested bracket legs are part of that history. A repeated order id still blocks the outcome. Missing/unbalanced history blocks learning rather than fabricating a result.
4. The experience store records gross realized paper P&L, return relative to planned stop risk, fill quantity, exit reason, decision receipt and source hashes. Sampled broker unrealized P&L supplies sampled MFE/MAE when available; it is not the complete market path. An earnings sample counts only from that trade's entry through the observation time. Samples from another trade in the same symbol, and samples after the observation, are excluded. Fees/net P&L remain unmeasured. No-fill and partial-fill episodes are retained but excluded from rule training.
5. Verified experiences become available to subsequent eligible decisions immediately, using their actual observation time. Retrieval includes up to five matching experiences. Intraday availability does not increase the pilot's one-entry-per-account/day limit.
6. After **20:15 America/New_York** the supervisor reviews the day. This is after the usual 20:00 after-hours close and also conservatively later on early-close days. It uses timezone-aware conversion, catches up after downtime and defers promotions while exposure is unresolved. Overnight trading is not enabled.
7. The nightly report records reviewed candidates, Jev acceptances, learned-veto observations, entry/flat state, outcomes and the candidate/validation/promotion ledger. It evaluates the recorded trading day; it does not claim to ingest every after-hours news item or reconstruct a complete market replay.

## Initial adaptive policy

This first study can learn only: **temporarily skip this account/symbol/direction/policy cohort**. It cannot increase size, widen stops, add leverage, change strategy code, modify model weights or enable real-money trading.

| Stage | Fixed requirement |
|---|---|
| Eligible episode | One-share reconciled closed trade, matching current Jev question identity and policy, accepted decision bound to the entry. A journaled fallback entry is stored and is not eligible, even when it reuses an earlier accepted receipt |
| Discovery | First 20 eligible episodes in the cohort; one fixed look |
| Candidate support | Mean gross return ≤ −0.1R and the 95% Wilson lower bound on the fraction of losing trades > 0.5 |
| Prospective validation | First 20 additional eligible trades whose entries occur strictly after candidate registration |
| Promotion | The same support criteria pass on that separate validation sample |
| Comparison | Actual observed trade returns versus hypothetical zero trade P&L from skipping; hypothetical results remain labeled |
| Allowed action | NO_TRADE only, enforced before another model call or entry |
| Expiry | 14 calendar days after promotion; expiry automatically removes the veto |
| Repeated testing | One candidate/validation attempt per exact cohort in protocol v1; rejected hypotheses are retained |

The 20/20 sample sizes and thresholds are explicit **initial paper operating policy**, not demonstrated statistical sufficiency for financial markets. Wilson checks are screening heuristics; trade dependence, changing selection and market regimes limit statistical interpretation. Passing this gate does not prove future improvement or a profitable strategy. Separate prospective evaluation is needed before expanding the adaptive surface.

At one trade per session, a promoted rule will take substantial time to earn. The system still captures and retrieves experience before that. A single win or loss never becomes an established rule. Positive discovery results and failed validation remain recorded without changing policy.

## State, recovery and operation

- Session journals remain the broker evidence source. `learning/events.sqlite` stores versioned protocol, experience, candidate, validation, promotion and daily-review events.
- `learning/YYYY-MM-DD.json` is a derived report, not execution authority. Restart reconstructs a missing report from the committed review. Import and review are idempotent.
- Each SQLite journal has a separate `.checkpoint` file. Chain verification plus that anchor detects row edits, tail deletion and a missing anchor. The single-row DB-commit/checkpoint interruption window is recoverable. An old journal establishes its first anchor when migrated; that cannot prove it was never truncated before migration. A privileged writer able to rewrite both database and anchor remains outside this local integrity guarantee.
- Back up the complete state directory with the service stopped and broker exposure reconciled. Never delete state to clear a veto/halt or run two restored hosts against one account.
- A bad learning store blocks new decisions. Broker recovery is prioritized and does not require the store or Jev to be healthy. The supervisor retries deferred outcome import after the broker lifecycle is complete.
- Earnings Adaptive writes closed trades into this same store with `arm=earnings`. Those episodes are ineligible for this study. Discovery, validation, promotion, and intraday context retrieval use only `arm=intraday` (the arm on episodes recorded before this field existed). An earnings loss cannot create or fill an intraday cohort. The nightly report lists them under `earnings_episodes`, separate from `episodes`. There is no earnings promotion rule. A few earnings wins do not create one.
- Both books journal an `opportunity` row for outcomes they can see: eligible, temporarily ineligible, rejected, model-vetoed, budget-limited, unfilled, fallback, and executed. Rejected and unfilled names stay in that journal. They are not dropped to improve a later study.
- Use `systemctl stop athena-paper.service` to suspend future activity. No automatic promotion can override the deterministic risk policy.

The trade-to-memory-to-candidate-to-prospective-validation-to-veto path is exercised with fixtures, including a test that a promoted rule changes the actual pilot's behavior. **No live paper session or measured performance improvement has yet been observed.** The general Athena Core service integration, richer strategy lessons, full replay counterfactuals and automatic code/model training remain incomplete.

## Fixture self-improve lab

`paper/self_improve.py` is a separate offline toy. It does not register candidates, promote NO_TRADE rules, or write the experience store. See [FIXTURE_SELF_IMPROVE.md](FIXTURE_SELF_IMPROVE.md).

References: [Alpaca session/order semantics](https://docs.alpaca.markets/us/docs/orders-at-alpaca), [Jev limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13), [canon](ATHENA_TRADING_CANON.md), [deployment](CLOUD_DEPLOYMENT.md).
