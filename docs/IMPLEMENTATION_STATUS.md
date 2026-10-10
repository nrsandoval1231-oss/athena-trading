# Implementation status

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

**Implemented and locally verified; not deployed.** No broker/model execution or improved trading performance has been observed. Cloud access and server-side Alpaca/TypeSafe credentials remain unavailable.

## Canon-to-code map

| Capability | Current implementation | Remaining boundary |
|---|---|---|
| Jev decision model | athena_adapter/jev.py: one typed TAKE_TRADE / NO_TRADE / INSUFFICIENT_EVIDENCE decision; active threshold gate | Semantic trading quality unmeasured; no generated thesis |
| Predictive Intraday | Existing ORB trigger/risk frame feeds paper/live.py | Bounded opening-range component, not full multi-source forecasting |
| Earnings Adaptive | research/earnings-universe input research | Integrated earnings strategy remains incomplete |
| Risk and execution | Dedicated paper account; one share/entry/day; rechecked limits; bracket, partial fills, cancellation and broker reconciliation | Full portfolio weekly/monthly/correlation policy remains incomplete |
| APDE | Shared vocabulary and historical strategy management; broker protection/cleanup in active pilot | Full EXPLORE/PROTECT/HARVEST/DEFEND ratcheting not integrated |
| Persistence | Single-writer SQLite events, verified chain and separate durable checkpoint | Privileged rewrites of both DB/anchor or lost disk require independent recovery evidence |
| Outcome feedback | paper/learning.py reconciles fills, stores gross outcomes/provenance and retrieves prior experience with a temporal boundary | Net fees and complete market-path excursions unmeasured |
| Learning validation | Fixed discovery sample, prospectively registered candidate, fresh validation sample, recorded rejection/promotion, expiring NO_TRADE rule | Narrow initial paper policy; no demonstrated future improvement or model training |
| Cloud scheduling | paper/cloud.py starts sessions, prioritizes recovery and reviews after 20:15 Eastern; service/installer/runbook prepared | Host installation, credentials, external alerting and backup restore not verified |
| Athena Core | Shared adapter identity contracts reused; trading-specific experience policy implemented | General Core service/context/replay/promotion integration remains incomplete |

## Active sources

- [LIVE_PAPER.md](LIVE_PAPER.md): the one active broker runner and operating bounds.
- [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md): host setup, persistent service, preflight and activation.
- [LEARNING.md](LEARNING.md): exact learning schedule, evidence, validation and adaptive limits.
- [BUILD_PLAN.md](BUILD_PLAN.md): the single product build sequence.
- [Latest repository audit](audits/2026-09-24-jev-learning.md): scope, repairs, retained defects and cleanup decisions.
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
