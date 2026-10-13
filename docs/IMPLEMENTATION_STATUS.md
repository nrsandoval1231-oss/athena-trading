# Implementation status

## Earnings Adaptive paper book — 13 October 2026

The earnings arm is a second PAPER book. It reads a free Nasdaq earnings calendar and an operator copy of the screener's `universe_75.txt`. It submits a GTC bracket only when the live gap is inside the screener's 4–12% band, Jev accepts it on the same floors as the intraday arm, and the dollar caps allow the share count. `parse_authority("LIVE")` still raises. Client order ids start with `athe-`. The book file is separate from the dated intraday journals. A restart reconciles those ids and does not resubmit them. SIGTERM does not flatten. The intraday runner ignores `athe-` orders and symbols in the earnings ledger, and it still refuses any other foreign symbol.

The calendar has no SLA, no surprise or guidance fields, and skips `time-not-supplied`. Options, multi-leg orders, Kelly sizing, and PROTECT/HARVEST/DEFEND ratchets are not in this slice. On `--run`, the TypeSafe model list is probed only when the book is about to seek a new entry. A restart still reconciles open brackets, replaces a missing stop, and exits at max hold while that probe is failing; the failure is journaled `JEV_UNAVAILABLE` and blocks new entries. `--preflight` still probes Jev and reports `decision_model`.

Closed earnings trades are copied into the shared learning store with `arm=earnings`, including fill quantity, gross P&L when entry and exit cashflows balance, the decision receipt, and source hashes. They are not eligible for the intraday negative-experience study, and that study's context, discovery, and validation ignore the earnings arm. Alpaca news headlines since the prior close are journaled and passed into the earnings Jev packet. A successful snapshot is reused for fifteen minutes and then refreshed through the entry window. A failed or rate-limited news read does not stop the book.

A partial take-profit stays under management: the open quantity must match entry fills minus opposite-side fills, and a quantity the legs cannot explain still halts. A broker close id already stored in `close_ack` is waited on. It is not treated as an unowned order and it is not sent again. A tighter risk or notional cap is stored as `policy_tightened` and used for later entries; a looser cap or any other configuration change refuses startup. The entry window and bar age are checked again after Jev returns, so a late answer does not submit. A daily-loss halt blocks only the session that recorded it. An operator halt stays in force. Nested bracket legs are part of an intraday outcome, and a repeated order id still blocks it. A fallback entry is stored and is not eligible for the intraday study. Nothing here was deployed, and nothing here is a broker fill or a performance result. **LIVE remains rejected.**

Observed local checks, Python 3.12. The baseline on this branch at `15a614196ea82f63e4c0183c9801a1c6fea3e636` was 760 passed and 0 failed. No pre-existing failures were present. This change adds 8 tests.

```bash
python -m pytest -q                 # 768 passed in 5.45s
python -m ruff check .              # clean, ruff 0.15.8
bash -n deploy/install.sh           # clean
git diff --check                    # clean
```

Root pytest does not include Astra Desk; that suite lives in its own repository. Tested revision: `71364f0cf7b994335c83debc5e9b86aef7ec4b66`. This sentence is a docs-only follow-up; the residual-position, close-id, restart-cap, entry-deadline, daily-loss, news-refresh, nested-outcome, and fallback-study fixes, and the checks above, are that commit.

## Transient paper reads — 13 October 2026

Broker GET calls, including minute bars, retry twice with backoff and jitter after a timeout, HTTP 429, or a 5xx response. Order POSTs are not retried. A single transient tick failure no longer flattens an open paper trade. The runner halts `RUNNER_ERROR` only after 3 consecutive transient failures or 60 seconds. A DELETE that returns 404 or 422 for an already finished or canceled leg is a successful cancel. A take-profit or stop fill still halts `POSITION_EXITED` and is also journaled as `TAKE_PROFIT_FILLED` or `STOP_FILLED`. Flat confirmation reads order history with `nested=true`. `parse_authority("LIVE")` still raises. **LIVE remains rejected.** This is not a broker fill and not evidence of trading performance.

Observed local checks, Python 3.12. The baseline on `main` `46c0754016c62ab8f381b52b9862298fc119316e` was 716 passed and 0 failed. No pre-existing failures were present. This change adds 14 tests.

```bash
python -m pytest -q                 # 730 passed in 4.73s
python -m ruff check .              # clean, ruff 0.15.8
bash -n deploy/install.sh           # clean
git diff --check                    # clean
```

Root pytest does not include Astra Desk; that suite lives in its own repository. Tested revision: `59e8fff6333858f7f7940c3d54f512445cb8b4ae`. This sentence is a docs-only follow-up; the runner, tests, and the checks above are that commit.

## Held bracket stop — 13 October 2026

On 13 October 2026 the first filled bracket was flattened about five seconds after the fill with halt `PROTECTION_MISSING`. Alpaca keeps the bracket stop leg in status `held`, and `GET /v2/orders?status=open&nested=false` does not return that leg. The post-fill check treated the missing row as no protection and `flatten_step` canceled and market-closed the position.

A filled position stays open when the tracked parent order's legs include an opposite-side `stop` or `stop_limit` in `held`, `new`, `accepted`, or `pending_new`, with a stop price and remaining quantity covering the position. A missing, canceled, rejected, or expired stop leg still halts `PROTECTION_MISSING`. Open orders are requested with `nested=true`, and nested legs are included in the snapshot, flatten/cancel, and not-flat checks. Flatten cancels the working parent rather than each of its legs. The per-check status line includes `halt_reason` when a halt is recorded. `parse_authority("LIVE")` still raises. **LIVE remains rejected.** This is not a broker fill and not evidence of trading performance.

Observed local checks, Python 3.12. The baseline on `main` `3db8edca34a6c110086d0ff064a3fa9ea4b013eb` was 704 passed and 0 failed. No pre-existing failures were present. This change adds 12 tests.

```bash
python -m pytest -q                 # 716 passed in 4.53s
python -m ruff check .              # clean, ruff 0.15.8
bash -n deploy/install.sh           # clean
git diff --check                    # clean
```

Root pytest does not include Astra Desk; that suite lives in its own repository. Tested revision: `74a1017f7bde5f43715eaca93108566b2279c981`. This sentence is a docs-only follow-up; the runner, tests, and the checks above are that commit.

## Jev probability mass — 12 October 2026

On 12 October 2026 at 08:58 America/Chicago the paper runner raised `ModelError: Jev probabilities do not sum to one` from `JevDecisionEngine.decide`. The sum had to match 1 within `1e-6`. The process exited after `decision_started`, and the restart then returned `ALREADY_REVIEWED` for that setup.

A distribution that is finite, in [0, 1], labeled with the three choices, and within 0.02 of 1 is scaled to sum to exactly 1 before the gate and the fallback score. The receipt keeps the raw values and `probability_checks.entry_decision.renormalized`. The raw response is journaled as `jev_response` before validation. A sum outside that tolerance, a negative, a non-finite value, or a missing label is `jev_veto` with `accepted` false and a reason. That veto does not submit an order, does not halt a flat account, and does not remove other TAKE_TRADE labels from the daily fallback. A transport failure before a response still propagates. `parse_authority("LIVE")` still raises. **LIVE remains rejected.** This is not a broker fill and not evidence of trading performance.

Observed local checks, Python 3.12. The baseline on `main` `1c47639df662b26d48d25d949327a7e4f7a26471` was 689 passed and 0 failed. No pre-existing failures were present. This change adds 15 tests.

```bash
python -m pytest -q                 # 704 passed in 4.67s
python -m ruff check .              # clean, ruff 0.15.8
bash -n deploy/install.sh           # clean
git diff --check                    # clean
```

Root pytest does not include Astra Desk; that suite lives in its own repository. Tested revision: `fa37740f50d7a8572beef3cfd9e6d7307d1fc62d`. This sentence is a docs-only follow-up; the runner, tests, and the checks above are that commit.

## Paper daily entry — 12 October 2026

New sessions admit a TAKE_TRADE label when p(TAKE_TRADE) is at least 0.6 and confidence is at least 0.5. `ATHENA_JEV_MIN_PTAKE` and `ATHENA_JEV_MIN_CONFIDENCE` override those floors. If no order is reserved by `ATHENA_FALLBACK_AFTER_OPEN_MINUTES` after the open (default 120), the runner submits the highest-scoring TAKE_TRADE label seen that day, once, and journals `fallback_entry` / `NO_ORDER_BY_CUTOFF`. The score is confidence times p(TAKE_TRADE). The same one-share bracket, notional, stop, buying-power, learned-veto, and one-position limits apply. `LIVE` still raises. A model error is not an approval. A bound journal keeps its saved floors; a missing fallback field resumes at 120 minutes. The 20-review cap waits for that fallback instead of ending the day.

The repository has no 2026-10-12 SPY bars or adjudication journal. That session was not replayed. The reported best label, confidence 0.61 and p(TAKE_TRADE) 0.74, is accepted by the new default gate and rejected by the previous 0.8 / 0.6 gate. That is a threshold check, not a broker fill and not evidence of trading performance. **LIVE remains rejected.**

Observed local checks, Python 3.12. The baseline on `main` `edd80a27a936b5724aba5ca6076d74e21ede7ebb` was 681 passed and 0 failed. No pre-existing failures were present.

```bash
python -m pytest -q                 # 689 passed in 10.10s
python -m ruff check .              # clean, ruff 0.15.8
bash -n deploy/install.sh           # clean
git diff --check                    # clean
```

Root pytest does not include Astra Desk; that suite lives in its own repository. Tested revision: `8f868bb16a203940b83d5534fa268c7b7e9d0152`. This sentence is a docs-only follow-up; the runner, tests, and the checks above are that commit.

## Hostinger paper pilot — 10 October 2026

Nick installed the paper runner on a Hostinger VPS. `athena-paper.service` is enabled. `/etc/athena/paper.env` on that host holds the Alpaca paper and TypeSafe keys. The file is not in git, and its contents are not recorded here. The service was last observed in `WAITING_FOR_MARKET` for Monday open. Pre-Monday fixture, preflight, and health checks passed that day. Those checks are not a broker fill. No broker session has traded, and improved trading performance is not established. **LIVE remains rejected.** The fixture lab is not paper evidence and must not auto-promote. DigitalOcean remains an optional host in [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md). It is not the host of this install. External alerting and backup restore are still not verified. Pilot bounds in [LIVE_PAPER.md](LIVE_PAPER.md) are unchanged.

This section is a docs-only status correction. It does not change the runner, supervisor, journal, learning store, broker pin, or installer. The session-resilience behavior below is unchanged. Checked against runtime commit `5c574e53a1a417cefbd3735bcb071475dbf4999d`. `git diff --check` was clean. No Python or installer files changed, so pytest and Ruff were not re-run. Root pytest still excludes Astra Desk; that suite lives in its own repository.

## Paper session resilience — 10 October 2026

A flat account with no reserved order no longer gets a day-ending `RUNNER_ERROR` halt for a missing opening minute, a stale bar, or a TypeSafe/startup failure. The supervisor retries that journal. A reserved id that Alpaca answers with HTTP 404 twice, while the account stays flat and no `entry_ack` was stored, is journaled as `entry_not_created` / `BROKER_ORDER_NOT_FOUND` and is not submitted again. A 404 with a position or open order, or after an acknowledgement, stays unresolved. Halt remains for a reserved order, visible exposure, daily loss, and operator stop. LIVE remains rejected. The fixture lab is not on this path.

Local checks, Python 3.12, on `bcf3f446704a0a8f58a713bc86a723574c240e63`. This sentence is a docs-only follow-up; the runtime and tests are that commit.

```bash
python -m pytest -q                 # 681 passed in 11.57s
python -m ruff check .              # clean, ruff 0.15.8
bash -n deploy/install.sh           # clean
git diff --check                    # clean
```

Root pytest does not include Astra Desk; that suite lives in its own repository. These results are not a broker session.

## Paper-readiness audit — 10 October 2026

Audited main `d36164ef7843d135dbfeea3684be598566abcf33` (PR #21). The paper runner, supervisor, journal, learning store, broker pin, and installer are unchanged since `733a1c441dbed15799397192cf6470b46c370d45`. The audit recorded that the pilot was not deployed and that server-side credentials were unavailable. That deployment claim is superseded by the Hostinger install above. LIVE remains rejected. The fixture lab is not paper evidence and must not auto-promote. Full findings and the owner checklist: [paper-readiness audit](audits/2026-10-10-paper-readiness.md).

GitHub Actions on that main push succeeded (run `38073284465`). This documentation revision does not change runtime behavior.

Local checks on audit commit `4a75582617cf4e1ee1a801507f7cefc794632de6`, Python 3.12. Runtime files are that commit. This follow-up only records the commands and points the audit at them.

```bash
python -m pytest -q                 # 672 passed in 11.49s
python -m ruff check .              # clean, ruff 0.15.8
bash -n deploy/install.sh           # clean
git diff --check                    # clean
```

A one-off on that tree removed one opening-range minute from the active-paper fixture. `signal_packet` raised `ValueError: opening window needs 15 bars, found 14`. `parse_authority("LIVE")` raised `AuthorityError`. `ExecutionAuthority` was `ABSENT` and `PAPER` only. Root pytest does not include Astra Desk; that suite lives in its own repository.

## Fixture self-improve lab — 10 October 2026

Offline evolutionary toy in `paper/self_improve.py` and `scripts/run_fixture_self_improve.py`. Sol's role is a `MutationScope` over scalar `ManagementParameters` fields. A local mutator applies one field through `evolve()`. The default command runs three generations. Each generation seeds from the best search survivor and reuses the sealed search partition. An Astra-style PASS/FAIL/UNKNOWN gate uses a precommitted policy on the walk-forward selection fold. The paper walker runs only under `ExecutionAuthority.ABSENT`; simulated episode R comes from `run_symbol_session`. The frozen holdout is hashed before search and opened once after the last generation. Its verdicts are not fed back. `report.md` is the skim; the JSON receipts stay the machine record. Nothing is promoted into the paper pilot. See [FIXTURE_SELF_IMPROVE.md](FIXTURE_SELF_IMPROVE.md).

This is not broker evidence, LIVE authority, or trading-performance evidence. The lab does not call Alpaca or TypeSafe.

Tested revision: `99bf935cf4a594758726229ecacde25ab5fdc7ef`. This paragraph is a docs-only follow-up; the lab, script, and tests are that revision.

Observed local checks, Python 3.12:

```bash
python -m ruff check .    # clean, ruff 0.15.8
python -m pytest -q       # 672 passed
git diff --check          # clean
```

The previous recorded root gate on this branch was 671 passed. The additional test covers the default three-generation seed, the sealed holdout opening once, and the readable report. Root pytest does not include Astra Desk; that suite lives in its own repository. A clean local gate is not deployment or trading-performance evidence. A default CLI run (`--generations` 3) was also executed offline; stdout is the markdown report and stderr was empty.

## Repository split — 7 October 2026

Astra Desk has been independently extracted to private [`nrsandoval1231-oss/astra-desk`](https://github.com/nrsandoval1231-oss/astra-desk) through its [merged PR #1](https://github.com/nrsandoval1231-oss/astra-desk/pull/1). The migration reconstructed 64 tracked files and all three source-directory snapshots with exact Git tree SHA matches. Standalone GitHub Actions passed 158 tests and Ruff on Python 3.11 and 3.12. This repository no longer contains `research/astra-desk/` or installs its test dependencies; Athena has its own CI gate. See [extraction receipt](ASTRA_DESK_EXTRACTION.md).

**Historical note:** The dated September verification commands and bundled-Astra descriptions below document the pre-extraction repository. Run the present CI/README commands instead; historical research audits are retained unchanged.


Status date: 24 September 2026. Product authority: [Athena Trading Canon v3.0](ATHENA_TRADING_CANON.md). Nick directed Jev-only decisions, cloud paper operation, repository cleanup and outcome learning.

**Status as of 24 September 2026:** implemented and locally verified, and not deployed on that date. Cloud access and server-side Alpaca/TypeSafe credentials were unavailable then. Deployment status after 10 October 2026 is the Hostinger section above. No broker session has traded, and improved trading performance has not been observed.

## Canon-to-code map

| Capability | Current implementation | Remaining boundary |
|---|---|---|
| Jev decision model | athena_adapter/jev.py: one typed TAKE_TRADE / NO_TRADE / INSUFFICIENT_EVIDENCE decision; active threshold gate | Semantic trading quality unmeasured; no generated thesis |
| Predictive Intraday | Existing ORB trigger/risk frame feeds paper/live.py | Bounded opening-range component, not full multi-source forecasting |
| Earnings Adaptive | paper/earnings.py: Nasdaq calendar, screener universe file, 4–12% post-earnings GTC bracket, Jev gate, Alpaca headlines as context, shared learning store tagged arm=earnings, separate paper service | Options, guidance/sentiment models, Kelly, and full APDE ratchet remain incomplete. Not deployed |
| Risk and execution | Dedicated paper account; one share/entry/day; rechecked limits; bracket, partial fills, cancellation and broker reconciliation | Full portfolio weekly/monthly/correlation policy remains incomplete |
| APDE | Shared vocabulary and historical strategy management; broker protection/cleanup in active pilot | Full EXPLORE/PROTECT/HARVEST/DEFEND ratcheting not integrated |
| Persistence | Single-writer SQLite events, verified chain and separate durable checkpoint | Privileged rewrites of both DB/anchor or lost disk require independent recovery evidence |
| Outcome feedback | paper/learning.py reconciles fills, stores gross outcomes/provenance and retrieves prior experience with a temporal boundary | Net fees and complete market-path excursions unmeasured |
| Learning validation | Fixed discovery sample, prospectively registered candidate, fresh validation sample, recorded rejection/promotion, expiring NO_TRADE rule | Narrow initial paper policy; no demonstrated future improvement or model training |
| Cloud scheduling | paper/cloud.py starts sessions, prioritizes recovery and reviews after 20:15 Eastern; Hostinger VPS service enabled 10 October 2026, last observed `WAITING_FOR_MARKET` | External alerting and backup restore not verified; LIVE remains rejected; no traded session observed |
| Athena Core | Shared adapter identity contracts reused; trading-specific experience policy implemented | General Core service/context/replay/promotion integration remains incomplete |

## Active sources

- [LIVE_PAPER.md](LIVE_PAPER.md): the one active broker runner and operating bounds.
- [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md): host setup, persistent service, preflight and activation.
- [LEARNING.md](LEARNING.md): exact learning schedule, evidence, validation and adaptive limits.
- [BUILD_PLAN.md](BUILD_PLAN.md): the single product build sequence.
- [Paper-readiness audit, 10 October 2026](audits/2026-10-10-paper-readiness.md): findings from that review, including how the fixture lab stays off the paper path. The host and credential finding in that audit is superseded by the Hostinger install above.
- [Jev and learning audit, 24 September 2026](audits/2026-09-24-jev-learning.md): scope, repairs, retained defects and cleanup decisions.
- [Research index](../research/README.md): retained historical experiments, not product entry points.

## Repository map

Strategy/market components remain in strategies/, orb_m/, market_data/ and exogenous/. Trading integration belongs in athena_adapter/, risk/, execution/, position_management/ and paper/. The active path is run_athena_paper.py under the cloud supervisor. Terra/Ollama classes and related active CLI/environment configuration were removed. The unused backtesting placeholder was deleted after a reference check.

AAT-V1/V2 source, frozen config/data identities, original source illustration and historical entry-point snapshots remain byte-identical to the prior candidate (77 protected files checked). Astra received lint-only maintenance and its test now targets the consolidated workflow. Its execution/persistence defects remain isolated; no Astra broker code is adopted into the active path. The old ORB demo CLI retains fixtures and broker preflight but refuses broker submission.

## Verification

The baseline for this update is remote PR #18 commit `2476e6b5dee38f9ac7ce5b65c9a2e6d7a702362f`. Initial repository inventory: 303 tracked files, 205 Python files parsed. New learning/runtime tests are added in this revision.

Tested revision: `733a1c441dbed15799397192cf6470b46c370d45`, tree `017e42988162fe612e98572901052c08e474e4a9`. The publication follow-up changes only this evidence line; tested runtime, deployment, research maintenance and test files are identical.

Observed local checks:

```bash
# Repository root, Python 3.12 with both packages' dependencies
python -m pytest -q                              # 657 passed
uv tool run --from ruff==0.15.8 ruff check .      # clean
bash -n deploy/install.sh                       # clean

git diff --check                               # clean
# Separately packaged Astra suite
cd research/astra-desk
python -m pytest -o addopts='' -q                # 158 passed
```

The existing root workflow now installs both packages and runs both suites. No new self-hosted runner is required. The three UP042 modernization warnings are narrowly suppressed for Astra enums because replacing Enum with StrEnum changes historical string rendering; the rest of the prior lint findings are repaired. A clean local gate is not evidence that the remote CI platform or target cloud host ran successfully.

Tests cover Jev-only decisions, malformed output, risk changes during inference, account-safe cleanup, remaining stop coverage, recovery without models, deleted journal tails, checkpoint interruption, reconciled outcomes, duplicate imports, future-data exclusion, discovery/validation separation, rule rejection/expiry, DST scheduling and a promoted rule changing the real pilot's decision path.

## Safe cleanup verification

Status addition: 30 September 2026. Safe cleanup only: deleted unreferenced `configs/orb_m_smoke_pool.txt` and unused `demo_bar_session()`, `bar_session_to_payload()`, and its private helper `_bar_to_payload()`. Stale docs were edited in place. Review-only configs and unused Alpaca corporate-action helpers were left for the owner. No dependency was removed; the supplied audit did not name one.

Tested revision: `ac4a4212a80ebd8258f46a1bf4c3d5a51708f97b`, tree `1b1478a20eb5ec5a07efd84b759229a7bc2bce24`. This evidence paragraph is a docs-only follow-up; the tested runtime files are that revision.

Observed local checks, Python 3.12:

```bash
python -m pip install pytest ruff==0.15.8 . './research/astra-desk[dev]'
python -m pytest -q                              # 657 passed
python -m ruff check .                           # clean, ruff 0.15.8
bash -n deploy/install.sh                        # clean
git diff --check                                 # clean
cd research/astra-desk
python -m pytest -o addopts='' -q                # 158 passed
```

Root pytest still excludes `research/astra-desk/tests`. A clean local gate is not cloud deployment or trading-performance evidence.

## Historical evidence and remaining defects

Earlier first integration: `9d83069e9ae53b01aee688aa68960eb8cc89ace6`, tree `aac92fd779c110c10836ddd6c0636d79f9945666`, 620 root tests. Cloud extension: `ca7a1ace2513af52474527bb7203dac9a2104ac9`, tree `3948f1e6048f468de3ac235ef45d6f3605808528`, 648 root tests. Those versions used an Actor; they are not the current runtime.

The original [audit](audits/2026-09-24.md) of `15fe4938241931078680cff79010ca09ce70feab` recorded 574 main tests, 31 lint findings, and 157/158 Astra tests. AT-06 (local gate) is now repaired. AT-01/02 (historical research receipt/resume integrity) and AT-04/05 (Astra execution/snapshots) remain open before those components can be adopted. AT-03/07 remain limitations of the historical library walker; its broker-submit CLI route is retired, and the active runner uses broker state and dated IDs. AT-08 was addressed by the canonical documentation reset.

AAT-V1 remains consumed / INVALID_EVIDENCE. The registered V2 protocol remains REGISTERED_NOT_AUTHORIZED; no holdout was rerun or reclassified. Real-money LIVE remains disabled. The current paper learning study has its own versioned policy and does not consume those research outcomes.
