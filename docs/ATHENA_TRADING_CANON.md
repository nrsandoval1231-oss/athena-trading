# Athena Trading Canon
Version: 3.0 — Jev-only decisions and outcome-learning amendment, 24 September 2026.
Status: **AUTHORITATIVE PRODUCT ARCHITECTURE**.
Owner: Nick Sandoval.

Athena Trading is a dual-strategy adaptive trading system: **Earnings Adaptive** and **Athena Predictive Intraday**, sharing one Athena Core, one decision workflow, one risk layer, one execution lifecycle, and one continuous learning loop.

**Two Arms. One Mind. Continuous Wisdom.**

This document transcribes and operationalizes Nick's supplied architecture diagram. The [original diagram](assets/athena-trading-canon.jpg) is the visual source. This document is its governing written specification. If they conflict, resolve the text to the diagram and Nick's latest explicit direction. Implementation status is recorded separately in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md); the execution sequence is [BUILD_PLAN.md](BUILD_PLAN.md). Historical experiments describe their own scope and never redefine this product.

## Canonical requirements

1. **Two product strategy arms.** Earnings Adaptive is event-driven and thesis-based. Predictive Intraday is signal-driven and prediction-based. Both use the shared components below. ORB-M can supply an intraday signal, baseline, or comparison; it does not replace either arm.
2. **Jev is the active Decision Model.** Nick explicitly replaced the separate generative LLM Actor. Strategy code constructs bounded candidate actions and point-in-time features; Jev selects a permitted action or abstains using supplied evidence and relevant experience. Structured assessments replace generated theses in the current pilot. Original earnings-scenario and prediction ambitions remain target capabilities; Jev is not a general-purpose text generator.
3. **Decision vocabulary.** LONG, SHORT, NO_TRADE, HOLD, ADD, REDUCE, PROTECT, TRAIL, and EXIT are the conceptual actions. A strategy may use a bounded subset. Every action becomes a structured proposal; none directly calls a broker.
4. **Point-in-time world state.** A decision reads market data, signals/features, predictions, positions, and account/risk state frozen as of its decision time. Record event time and availability time. Missing or stale information is visible, never invented.
5. **Athena context.** Retrieve relevant validated experiences, similar situations, lessons/rules, failure modes, and regime context with provenance and version identity. Future outcomes and unvalidated lessons cannot enter a decision packet.
6. **Earnings intelligence.** Use earnings calendars/estimates, guidance/sentiment, options skew and implied-volatility context, historical reactions, catalysts, and risk mapping. Each source must meet the point-in-time data contract.
7. **Earnings thesis/scenario engine.** Form beat/meet/miss scenarios, reaction distributions, expected moves/ranges, confidence and edge estimates, and a trade plan. Confidence and probabilities are measured or explicitly labeled estimates.
8. **Earnings execution/positioning.** Express pre-event, post-event, or intraday entry logic, position sizing, instrument structure, and correlation/risk requirements through the shared proposal and execution path. Stocks, options, and multi-leg trades are target capabilities; supported instruments/windows are explicit implementation policy.
9. **Intraday market intelligence.** Use multi-timeframe data, technicals/order flow/VWAP, volume/volatility regimes, news/social/macro evidence, correlations, and sector context where trustworthy data exists.
10. **Intraday prediction engine.** Produce a short-horizon forecast, probability of a defined target, expected MFE/MAE/duration, path forecast, confidence, and invalidation; update as fresh evidence arrives. The diagram's +1% is an example target, not a universal objective or guarantee.
11. **Intraday execution/positioning.** Propose entry signals/confluence, size, leverage/instrument choice, stops, and management through the same shared risk and execution workflow as earnings.
12. **Shared APDE.** Both arms manage positions through EXPLORE → PROTECT → HARVEST → DEFEND → EXIT. Respectively: allow the thesis room to develop; confirm/reduce risk; let winners run; tighten/protect profit; exit when the thesis breaks or edge disappears. State transitions require evidence and remain subject to risk limits.
13. **Deterministic risk is final authority.** Enforce position/exposure/leverage limits, correlation limits, daily/weekly/monthly limits, maximum drawdown, stop requirements, kill switches, circuit breakers, and applicable instrument/session constraints. The Actor cannot change these limits or approve its own exception.
14. **Risk-first sizing.** Fractional Kelly is a target sizing method only when the needed probabilities/payoff estimates are validated and the risk policy enables it. Until then use conservative, explicitly configured risk sizing. An uncalibrated model confidence is not a Kelly input.
15. **Execution confirms reality.** The execution engine routes authorized intents, manages orders, controls slippage, and confirms fills. Broker acknowledgements, rejects, partial fills, cancellations, positions, stops, exits, and reconciliation drive operational state. Simulated fills stay in simulation.
16. **Session and emergency safety.** Current policy requires pre-close flattening for the applicable trading session and no overnight positions. Monitoring earnings outside market hours does not authorize overnight exposure. Halt blocks new entries first; unresolved exposure remains visible until broker-confirmed flat. This canon does not enable LIVE authority.
17. **Exit and review.** Each arm supports planned/adaptive exits, P&L attribution, and a Decision Episode connecting the original state, context, proposal, risk verdict, execution, management transitions, and realized outcome.
18. **Reality/outcome is the evidence.** Capture P&L, MFE/MAE, fill quality/slippage, prediction accuracy, time in trade, and opportunity cost. Store observed and simulated outcomes distinctly. No LLM response is proof of an executed trade or a successful result.
19. **Replay engine.** Replay episodes with a temporal firewall and controlled counterfactual simulation. Freeze data, policies, model identities, and evaluation inputs so changes can be attributed.
20. **Wisdom extraction.** Identify candidate lessons through outcome attribution and pattern discovery. Preserve rejected hypotheses and failure modes as negative knowledge. Candidate lessons remain separate from validated knowledge.
21. **Validation lab.** Evaluate candidate changes using suitable walk-forward, out-of-sample, and statistical checks against frozen baselines and meaningful control treatments. Bound the adaptive surface. Sample insufficiency means insufficient evidence.
22. **Versioned knowledge store.** Promote only supported lessons/rules/models, with source episodes, evaluation receipts, versions, and rollback history. Later decisions retrieve the promoted version; promotion cannot rewrite historical evidence.
23. **Continuous wisdom loop.** Observe market → generate thesis/prediction → execute an authorized trade → manage with APDE → exit → replay → extract wisdom → validate/promote → inform later decisions. Overnight replay is part of the target operating rhythm; a completed trade alone does not establish learning.
24. **Performance principles and intended outcomes.** Probability over prediction, expectancy over win rate, risk first, continuous adaptation, and disciplined use of data. Seek stronger risk management, better decisions, consistent process, and accumulated validated knowledge. Returns and improvement are objectives to measure, not promises.
25. **Core ownership.** Athena Core remains domain-neutral and owns general episode/context/lesson/validation concepts. Athena Trading owns both strategies, market/broker semantics, APDE policy, trading risk, and trading outcomes. Integrate through athena_adapter; do not copy another independent Core into this repo.
26. **Keep one product workflow.** Share contracts, state, risk, execution, replay integration, and reporting across both arms. Provider choice, research ablations, and deployment vendors are implementation choices, not additional product architectures.
27. **One model decision boundary.** Jev selects TAKE_TRADE, NO_TRADE, or INSUFFICIENT_EVIDENCE for the supplied candidate; only a threshold-qualified TAKE_TRADE can proceed to deterministic risk. Record model, question version/hash, inputs, distribution, confidence, verdict and policy. Do not call the same model twice and describe that as independent verification. Code owns calculations, sizing, stops, permissions and reconciliation. Answer confidence is not a measured probability of trading profit.
28. **Active paper is the immediate operating target.** Nick explicitly requested current market data and actual broker paper orders, with Jev in the active entry path; a shadow stage is not required. Missing, malformed, uncertain, contradictory, or unavailable adjudication cannot approve new exposure. Protective management and emergency/session exits do not depend on Jev being available. Real-money LIVE authority remains disabled. Start with the bounded intraday paper slice described in implementation status, then extend through the same workflow.

29. **Cloud operation is the deployment target.** Nick requested an always-on cloud runtime independent of his local computer. Persist decision and broker state, restart after interruption, follow broker market sessions, and reconcile unfinished exposure before new entries. Being online continuously does not authorize trading outside the configured session, overnight exposure, real-money execution, or unvalidated learning. DigitalOcean and the model API are implementation choices described in the deployment runbook.

30. **Evaluate every completed trade.** Reconcile actual broker fills, record attributable outcomes and preserve failed/no-fill episodes. Make verified observed experience available to subsequent eligible decisions, with account/strategy identity, provenance and availability timestamps. Unmeasured quantities remain unknown.
31. **Review the day after after-hours.** The cloud supervisor runs the daily review at or after 20:15 America/New_York and catches up after downtime. Record candidates, rejected hypotheses, prospective validation results, promotions and unresolved data. Defer promotion while exposure or outcome integrity is unresolved.
32. **Improve through measured, bounded changes.** Intraday experience retrieval is permitted; policy changes need separate prospective evidence. Initial automatic promotion can only add a temporary NO_TRADE rule, never increase exposure or change model weights/code. Preserve fixed study criteria, expiry and rejection history. Every trade supplies information; no trade guarantees an improvement.

## Shared decision flow

| Stage | Responsibility | Output |
|---|---|---|
| World state | Assemble only available evidence and reconciled account state | Frozen state reference |
| Athena context | Retrieve relevant validated knowledge | Versioned context reference |
| Strategy code | Construct supported earnings/intraday candidate actions and computed features | Bounded candidate and evidence |
| Jev decision model | Select the candidate or abstain under versioned criteria | Recorded typed decision; no execution authority |
| Deterministic risk | Authorize, constrain, or refuse under policy | Exact authorized intent or refusal |
| Execution + APDE | Route and manage, then reconcile broker events | Observed execution/management events |
| Outcome + Core learning | Attribute, replay, extract, validate, and promote | Decision Episode and versioned knowledge |

Strategy code supplies candidates, Jev decides whether to take them, and risk determines what can be executed. Both strategy arms share this boundary. The original visual is preserved; requirements 2 and 27–32 record Nick's subsequent explicit amendments.

## Present scope and change control

The restoration itself did not grant operational authority. Nick's subsequent instruction authorizes implementing and starting active broker paper trading with Jev adjudication. The first pilot bounds exposure and inference calls explicitly. This does not authorize real-money trading, reopen an experiment, or alter frozen research evidence. Actual activation still requires configured paper/model credentials and a running process; code and test fixtures are not proof of a launched session.

Use the implementation map to reuse useful code. AAT-V1/V2, ORB-M campaign documents, provider runbooks, and Astra Desk retain their historical evidence and restrictions. Their old next-step instructions are superseded for product planning by BUILD_PLAN.md.

Make product changes here; update status with verification evidence. Do not create competing “vNext,” experiment, or provider roadmaps as the default product plan.
