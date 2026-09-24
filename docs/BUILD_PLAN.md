# Build plan
Status: **ACTIVE PRODUCT IMPLEMENTATION SEQUENCE**, restored by Nick on 24 September 2026.
Authority: [Athena Trading Canon](ATHENA_TRADING_CANON.md).
Baseline and defects: [Implementation status](IMPLEMENTATION_STATUS.md).

Build the shared workflow once, connect Predictive Intraday first using existing market/ORB-M assets, then add Earnings Adaptive through the same contracts. This is an implementation sequence; both strategy arms remain canonical. Do not extend the former AAT/provider roadmap by default.

## One finish line

**Owner-directed priority:** integrate TypeSafe Jev as an active adjudicator and start live-data broker paper trading. No shadow stage is required. The first bounded intraday pilot is implemented in the existing paper/adapter packages; [LIVE_PAPER.md](LIVE_PAPER.md) is its launch procedure. Credentials and an operating host are still required. This slice does not finish the complete Actor, risk, APDE, strategy, or learning requirements below.

An observed market opportunity becomes a timestamped world state, relevant Athena context, an LLM proposal, active Jev adjudication, a deterministic risk verdict, a broker-confirmed paper lifecycle managed by APDE, and an attributed Decision Episode. A supported lesson is then versioned and retrieved in a later decision. Earnings and intraday both use this same path. Observed, simulated, and counterfactual outcomes remain distinguishable.

## Sequential slices

1. **Restore a usable development gate.** Reconcile maintained versus isolated research test coverage and dependencies; fix AT-06 without reintroducing a separate product stack. Preserve frozen research hashes and evidence. Record the actual pass/fail results.
2. **Implement the shared decision/episode boundary.** Define the smallest versioned world-state, context, proposed-action, risk-verdict, execution-event, and outcome records required by the canon. Extend athena_adapter and existing modules. Include strategy, session, account/environment, timestamps, provenance, and input/version identity. Do not build a generic orchestration framework.
3. **Implement trustworthy state and recovery.** Freeze input identities, preserve ordered evidence, and reject drift/corruption on resume. Reuse research utilities only after their applicable AT-01/02 defects are repaired. Implement only the persistence needed by the shared workflow; do not adopt Astra's AT-05 snapshot behavior.
4. **Connect the LLM Decision Actor.** Feed point-in-time state plus versioned context; produce bounded, schema-validated proposals with direction/action, horizon, sizing request, stop/invalidation, expected outcome, and reasoning. Handle abstention and malformed output explicitly. Keep risk enforcement outside the Actor.
5. **Complete the risk/execution foundation.** Use exact-intent authorization, deterministic risk policy, account/exposure checks, session-safe order identities, broker fill/partial-fill/reject/cancel handling, and reconciliation. Set and persist halt before attempting broker cleanup. Repair AT-03/07 and applicable AT-04 behavior before broker-connected operation.
6. **Deliver Predictive Intraday through the shared workflow.** Assemble the supported live/replay input set, use ORB-M as one reusable signal/baseline, and produce a defined short-horizon forecast and trade/no-trade proposal. Missing data and uncalibrated probabilities remain explicit. Deliver one complete session lifecycle before adding more signals.
7. **Complete shared APDE and session close.** Drive EXPLORE/PROTECT/HARVEST/DEFEND/EXIT from arrived evidence and confirmed exposure. Enforce protective orders, partial exits, risk limits, pre-close flattening, restart, and exception recovery. Test disconnects, rejected exits, and positions still open after a close request.
8. **Close the first learning loop with Athena Core.** Emit complete episodes, replay with a temporal firewall, extract a bounded candidate lesson, and validate it against a frozen baseline and meaningfully distinct control treatment. Store/promote through the Core boundary. Demonstrate the promoted version being retrieved in a later decision. Log rejection/negative knowledge when it fails; successful promotion is not required for honest completion.
9. **Add Earnings Adaptive.** Integrate trustworthy calendar/estimate/event inputs, guidance/sentiment and historical reactions, scenario distribution, thesis/invalidation, and proposal generation using the same workflow. Begin with supported equity instruments and permitted sessions. Options/multi-leg execution and Kelly sizing require separate implemented data/risk support; they remain part of the target canon.
10. **Prove continuous paper operation.** Run both arms against reconciled broker state with durable recovery, scheduled pre-close flattening, overnight replay, and an operator report showing proposals, decisions, exposure, P&L attribution, and knowledge changes. Set a prospective evaluation window and acceptance criteria before observing outcomes.
11. **Evaluate before expanding authority.** Confirm dataset sufficiency and temporal validity; evaluate frozen candidates on fresh, authorized held-out evidence. Report expectancy, risk, calibration, slippage, and uncertainty. Keep LIVE disabled unless the owner explicitly authorizes a separately reviewed implementation.

## Working constraints

12. **One slice at a time.** Finish and verify a useful behavior, update status, then continue. An API adapter, enum, mock, or long model-call run alone does not complete a strategy or the learning loop.
13. **Reuse selectively.** Keep useful market, ORB-M, APDE, risk, and adapter components. Historical AAT ablation arms B/C/D/E are experiment labels, not additional product strategy arms. Astra stays isolated until a specific tested component is adopted.
14. **Keep implementation choices small.** One primary Actor/provider path and one shared runtime are sufficient initially. Add alternatives only to solve a measured current limitation. Model benchmarking and GPU scaling do not lead the product plan.
15. **Separate capability from authority.** A diagram or build task does not grant live trading, paid calls, consumed-holdout reuse, or a policy bypass. Preserve the owner's existing permissions and the current operational boundary.

Numeric thresholds, broker deployment, evaluation windows, and probability calibration must be explicit implementation/evaluation policy. The illustration does not supply their values. Reuse safe current policy where applicable; keep unsupported capabilities disabled rather than inventing settings.
