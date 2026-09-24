# Implementation status
Status date: 24 September 2026. Product authority: [Athena Trading Canon](ATHENA_TRADING_CANON.md).

The owner restored the supplied two-strategy architecture after the audit of main at 15fe4938241931078680cff79010ca09ce70feab. This document tracks implementation against that canon. The reset changed direction/documentation. Nick subsequently requested active Jev adjudication and live-data broker paper trading; the implementation below adds a bounded intraday pilot. It has not been launched against external services.

## Canon-to-code map

| Canon capability | Existing implementation | Honest state / next gap |
|---|---|---|
| Earnings Adaptive | research/earnings-universe screening research | Research input only. No integrated earnings intelligence → scenarios → Actor → execution arm. |
| Predictive Intraday | strategies/orb_momentum.py; orb_m/ engine | Reusable signals, simulation, and baseline. No complete continuously updated prediction/Actor arm. |
| Point-in-time world state | market_data/; exogenous/; ORB-M market features | Useful components. No shared canonical account/market/context snapshot across both arms. |
| LLM Decision Actor | paper/live.py OllamaActor; historical aat_v1/ and aat_v2/ | Pilot proposes candidate direction or NO_TRADE with rationale/invalidation, recording installed qwen2.5:7b digest. Full canonical proposal service remains incomplete. |
| Active Jev adjudicator | athena_adapter/jev.py, called by paper/live.py | Pinned jev-1.13.0, two Choice checks, strict response validation, active entry veto, recorded distributions/identity/policy. External calls and semantic accuracy not yet observed. |
| Athena context and learning | athena_adapter/contracts.py; research lessons/evaluation code | Hash/contract utilities and research machinery. A production context/replay/promotion integration with Athena Core is not established. |
| Shared APDE | position_management/apde.py; orb_m/management.py | APDE vocabulary and strategy-specific management exist. Shared broker-aware management for both arms is incomplete. |
| Deterministic risk | risk/halt.py; ORB-M constitution/sizing | Partial ceilings and authority gating. Shared account/exposure/session lifecycle needs implementation. |
| Broker execution and reconciliation | paper/live.py extends Alpaca paper adapter; paper/journal.py | Bounded pilot submits one-share protected bracket, tracks broker fills/positions, cancels then closes, recovers reserved entries without resend, and confirms flat. Fixture-tested only. Historical session/walk still has AT-03. Full account/exposure/APDE lifecycle remains incomplete. |
| Decision Episodes/outcomes | athena_adapter/; AAT research receipts/outcomes | Reusable pieces. No canonical end-to-end episode for both strategies. |
| Replay → extract → validate → promote | AAT V1/V2 research; Astra scaffold | Research-only. Current V2 runner does not demonstrate a meaningful learned-treatment comparison. |
| Scheduled operation and reporting | scripts/run_athena_paper.py | Active, attended first-session CLI with preflight and shutdown cleanup. Not deployed, no credentials present, no verified unattended service or overnight learning. |

“Exists” means code was inspected. It does not imply broker readiness or a profitable/adaptive result.

## Repository map

| Location | Role after the reset |
|---|---|
| docs/ATHENA_TRADING_CANON.md + docs/assets/athena-trading-canon.jpg | Governing written canon and original visual source |
| docs/BUILD_PLAN.md | The one active implementation sequence |
| docs/IMPLEMENTATION_STATUS.md | Verified state, reuse map, gaps |
| src/athena_trading/strategies/ | Strategy integration home; currently ORB-M only |
| src/athena_trading/market_data/ + exogenous/ | Inputs for both arms, with availability/provenance checks |
| src/athena_trading/athena_adapter/ | Domain boundary to Athena Core |
| src/athena_trading/position_management/ | Shared APDE vocabulary; extend with actual behavior |
| src/athena_trading/risk/ + execution/ + paper/ | Shared operational boundaries to complete |
| src/athena_trading/orb_m/ | Reusable intraday engine/baseline; historical experiment semantics remain pinned |
| src/athena_trading/aat_v1/ + aat_v2/ | Retained experiment code; not the default product runtime |
| research/astra-desk/ | Isolated research scaffold; no automatic promotion into the product |
| research/earnings-universe/ | Candidate earnings research input; validate before adoption |
| configs/ + data/ + scripts/ | Existing research/runtime assets; retain stable paths and frozen identities |
| research/history/ | Superseded product entry-point snapshots |
| docs/audits/ | Dated evidence, including the pre-reset audit |

No empty strategy/framework tree is added. Create or split runtime files when the next real implementation slice needs them.

## Verification baseline

Audit of 15fe4938241931078680cff79010ca09ce70feab:

- Main package: **574 passed** on Python 3.12.
- Ruff **0.15.8**: **31 existing errors**, mostly in imported research files.
- Astra Desk: **157 passed, 1 failed**; the failure asserts a removed standalone workflow exists.
- Root pytest testpaths=["tests"] omits the Astra suite.
- Latest inspected GitHub CI failed before any steps, with no assigned runner. Its platform failure cause was unavailable.
- All 283 original tracked files were checked against remote blob hashes.

Run main-package checks from the root. Run Astra checks from research/astra-desk in an environment with its dependencies (PyYAML, jsonschema, and pytest). Keep those results separate until the consolidated gate is repaired.

Canon-reset verification: **574 main-package tests passed**. All **246 runtime/config/data/script/test/workflow files** in the protected audit scope remain byte-identical to the audited main commit. Whitespace and active-document local links pass. The source diagram is byte-identical to the supplied JPEG (SHA-256 fe9a2da6a8de6ae56bb3c3ba864a1efed2fc1fb9eb7aa9a01d1ae0638c54b8ef). This reset does not repair the existing CI or runtime findings.

## Active Jev paper slice

Owner direction: **active paper, no shadow requirement**. Base main: `2382cce97b4b4b1dd4e096a4e501f6989f0b3126`. Exact integration source is the associated PR head; no historical research artifacts or existing runtime modules were rewritten.

- New path: current Alpaca minute/daily data → bounded Ollama Actor → active Jev acceptance/veto → rechecked deterministic risk → one-share limit/bracket paper entry → broker reconciliation/cleanup.
- Initial bounds: one account/symbol, one entry per session, $1,000/1% notional cap, $10/0.1% initial stop-risk cap, 0.5% daily account-loss cap, 20 model reviews, flatten 15 minutes before broker close.
- Jev model/question/result/policy and original market inputs are recorded. Confidence is semantic output uncertainty, not trading profit probability or a Kelly input.
- SQLite events are content-verified and single-writer locked. The entry is reserved before send. Model outages do not prevent recovery of a reserved paper position.
- **46 new tests; 620 root tests passed** using `python -m pytest -q`. Changed Python files pass Ruff **0.15.8**. Existing root lint/Astra findings remain open; the Astra code is unchanged and was not re-run for this slice.
- CLI preflight was attempted and exited 2 for missing Alpaca environment credentials, with zero orders submitted. TypeSafe credentials are also absent. No operating host is configured. There is **no live broker/provider result or trading-accuracy evidence** from this change.
- Launch procedure and exact remaining setup are in [LIVE_PAPER.md](LIVE_PAPER.md). No automatic shadow mode exists.

This is a usable integration slice, not completion of the dual strategy, full APDE, account-wide weekly/monthly risk policy, predictive model, or Core learning loop. The historical fixture walker remains a fixture; use the new active command for live paper.

## Outstanding defects

See [the full audit](audits/2026-09-24.md) for counterexamples and source lines.

| ID | Finding | Where it matters |
|---|---|---|
| AT-01 | Full-run and snapshot inputs are not consistently bound to verified bytes | Before reusing research evidence |
| AT-02 | Resume accepts changed inputs/decisions without validating the complete history | Before any trusted resumable evidence run |
| AT-03 | Paper risk state follows simulated exits instead of broker fills/exits | Before unattended broker paper operation |
| AT-04 | Astra flatten omits position-close operations; broker errors prevent halt latch | Before adopting Astra execution code |
| AT-05 | Astra snapshot changes can pass alongside a verified chain | Before adopting Astra persistence |
| AT-06 | Consolidated lint/test gate is incomplete | First build slice |
| AT-07 | Client order IDs repeat across session dates | Before multi-session operation |
| AT-08 | Competing current-status documents | Addressed by the canon reset and explicit historical labels |

The 24 September PR #16 fixes are present: qwen2.5:7b identity, deterministic subsample receipt comparison, and subsample index byte verification. Adjacent findings above remain open.

## Evidence and authority

- AAT-V1 holdout is consumed / INVALID_EVIDENCE (249 versus required 300). It cannot be repurposed for the restored product.
- The recorded V2 Terra protocol remains REGISTERED_NOT_AUTHORIZED; candidate Qwen research is not that registered experiment.
- Frozen V2 full data and snapshot bytes were not available in the repo audit. Their actual external contents are unverified here.
- Real-money LIVE remains disabled. The later owner instruction authorizes bounded active paper operation and its Jev calls; it does not reopen historical experiments or authorize real-money orders.
- Update this page after each real slice with observed evidence and remaining limits. Do not mark diagram boxes complete from file existence or fixture-only demonstrations.
