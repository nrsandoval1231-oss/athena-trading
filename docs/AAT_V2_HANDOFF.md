# AAT-V2 Progress, Status and Handoff

Written 8 September 2026. Status refreshed 14 September 2026 against current
`main` (macro-only claim contract, `REGISTERED_NOT_AUTHORIZED` protocol,
GDELT rejection, TRAINING dry-run plans, and model-execution options).

**One-line status.** The V2 experiment is built through a frozen macro-only
claim contract and a `REGISTERED_NOT_AUTHORIZED` protocol.
`execution_authority` is `ABSENT`. Adapters are not the blocker: the ALFRED
macro adapter is built, GDELT was rejected for this run after a bounded
coverage study, and news stays explicitly missing. TRAINING cannot run until
a separate authorization supplies Actor credentials and spend.

---

## 1. What this was, and what we borrowed

The starting question was how Athena Trading compares to
[TradingAgents](https://github.com/TauricResearch/TradingAgents) by Tauric
Research. (For the record, since it came up: TradingAgents is an LLM
agent-framework research project. It is not KuCoin or any other exchange, and
no exchange code, API or data was involved at any point.)

### The two projects are different kinds of thing

TradingAgents is an LLM-orchestration framework: a LangGraph graph of about
seven agent roles — fundamentals, sentiment, news and technical analysts, then
bull and bear researchers who debate, then a trader, then risk and portfolio
managers. It supports many LLM providers, splits "deep think" from "quick
think" models, and pulls a wide data surface: Yahoo Finance, StockTwits,
Reddit, global news, FRED, Alpha Vantage, Polymarket. Its own README states
that backtests will not reproduce and that it should be treated as a research
scaffold, not a strategy with a replicable return.

Athena Trading is a falsification experiment with a deterministic strategy at
the centre. ORB-M V1 is a complete, frozen, non-LLM strategy — opening range,
close confirmation, ATR/structure R, APDE trailing, bar-walking fill
simulation with slippage and commission, hard risk ceilings. The LLM Actor is
one *arm* in a controlled ablation, not the product.

The essential contrast: **TradingAgents assumes the multi-agent architecture is
the value. Athena built the machinery to test whether it is.**

### What we took

| Borrowed | What it became here | Why the change |
| --- | --- | --- |
| Analyst-role decomposition | Five deterministic compressors in `exogenous/analysts.py` | Their analysts *decide*; ours *compress*. An LLM panel with no control arm cannot be evaluated, so we kept the decomposition of the input space and discarded the decision authority. |
| Wide data surface | Six source categories in `docs/EXOGENOUS_SOURCES_V1.md` | Re-tiered for a 09:45 ET intraday decision instead of their daily horizon. |
| Multi-provider LLM support | `aat_v1/providers.py` descriptor registry | Identity is scoped by provider, so the same model string from a different vendor is a different Actor rather than a silent substitution. |
| Bull/bear debate loop | Arm E in `aat_v2/arms.py` | Demoted from the architecture to a *testable arm*, capped at the token budget of a single non-deliberating call. |

### What we deliberately did not take

- **Their agent graph as a decision architecture.** Without a deterministic
  control arm there is no way to attribute any result to the agents.
- **Their memory design.** `trading_memory.md` is appended in place, with no
  provenance and no partition discipline, so nothing distinguishes in-sample
  from out-of-sample.
- **Their posture on reproducibility.** They disclaim it. Athena's entire value
  is that it does not have to.

---

## 2. The thesis

### What was wrong with AAT-V1

The V1 four-arm comparison (A deterministic, B memoryless, C naive memory, D
Athena) was audit-passing and *structurally incapable of producing a strong
positive result*. Every arm reasoned over the same price-derived feature
vector. Arm D received one promoted lesson — "PASS versus standard sizing" —
and nothing else. There was no information in arm D's packet that arm A did not
already have.

Under those conditions the largest honest effect available is a re-weighting of
sizing under identical evidence. If arm D had beaten arm A, the most likely
explanation would have been sizing variance, not adaptation. The experiment
could produce a null, and it could produce an unattributable win. It could not
produce a defensible positive.

That is a ceiling on the experiment's design, not on Athena as an architecture.

### The V2 thesis

**Give the adaptive arms information the deterministic baseline structurally
cannot use, and the asymmetry becomes the mechanism.**

Arms B through E receive a bucketed exogenous feature vector — news, macro,
social, fundamentals, prediction markets. Arm A cannot receive it, because arm
A is the frozen ORB-M strategy and makes no model call at all. If arm D beats
arm A now, there is a named mechanism: the adaptive arm saw evidence the
control arm did not. If it does not, that is a real null about adaptation
rather than an artifact of a starved packet.

Everything else — the partitions, the receipts, the 300-pair floor, the
single-use holdout, the negative-knowledge registry, the refusal to claim — is
carried forward from V1 unchanged. The governance was never the problem.

### The intraday reframe

This is the single most important design decision in the whole effort, and it
is what makes copying TradingAgents' data surface non-trivial.

TradingAgents evaluates daily-horizon decisions. ORB-M decides at **09:45 ET**,
when the 15-minute opening range freezes, and is flat by 15:45 and hard-flat by
15:59. The relevant evidence window is **prior session close → 09:45**. That
re-ranks their entire source list:

- Overnight and pre-market catalysts are the whole game.
- A fundamentals *value* is nearly worthless at 09:45. The fundamentals
  *event* — "this name reported after yesterday's close" — is one of the
  strongest gap catalysts there is. So fundamentals is narrowed to filing
  events only, which also sidesteps the restatement problem almost entirely.
- Macro matters as a scheduled-event flag: an 08:30 print lands 75 minutes
  before the range freezes.
- Sentiment matters as *velocity into the decision*, not as a standing level.
- Polymarket has essentially no single-name equity coverage and lands in
  Tier 3, implemented as a stub that proves the record contract generalizes
  beyond equities.

### A correction I made along the way

In my first pass I told Nick that social sentiment carried the worst
point-in-time provenance of the set. **That was wrong**, and it changed the
build order, so it is recorded here rather than quietly fixed.

A Reddit or StockTwits post has an immutable `created_utc` — that *is* the
moment the content became observable, exactly the `available_at` semantics the
contract needs. Social's problems are retrieval, coverage bias and deletion
survivorship: real, but tractable and measurable.

**Fundamentals is the worst.** Most affordable fundamentals APIs serve a
*current view* — latest restated figures, silently backfilled over the original
filings. A backtest reading them sees numbers that did not exist on the
decision date, and nothing in the response says so. It is the one source that
leaks by default and looks clean while doing it.

---

## 3. What was built

Five commits on `claude/tradingagents-comparison-qi58ri`. **368 tests pass**
(228 pre-existing, 140 new), `ruff` clean.

### Phase 1 — exogenous evidence layer (`src/athena_trading/exogenous/`)

Six modules, ~1,080 lines, 61 tests. Stdlib only.

| Module | Responsibility |
| --- | --- |
| `contracts.py` | `ExogenousRecord`; `event_at` and `available_at` kept separate; visibility is `available_at <= decision_time` and nothing else |
| `buckets.py` | Frozen quantization; `ABSENT` distinct from bucket 0 |
| `analysts.py` | Five deterministic compressors → 24 frozen feature keys |
| `ingest.py` | Receipted record store; anti-backfill guard |
| `snapshot.py` | Joins features to opportunities; per-source coverage rates |
| `alfred.py` | FRED ALFRED vintage adapter; fixture-tested; no data acquired |
| `__init__.py` | Public surface |

Plus `scripts/measure_exogenous_coverage.py`.

**Design decisions worth knowing:**

- **Revisions are stored but never shown.** They stay in the store so their
  existence is auditable, but `visible_records` drops them rather than
  substituting them for originals. A replay sees the stale number the trader
  actually had — the conservative direction.
- **`ABSENT` (-1) is not bucket 0.** "No news at all" and "the fewest news
  items we bucket" are different facts. This surfaced a real bug mid-build:
  the first `bucket()` returned ABSENT for anything below the first edge,
  which on the signed macro-surprise scale collided a large miss with "no
  observation". Values below the first edge now clamp to 0; only `None` is
  ABSENT.
- **The anti-backfill guard is what makes news usable at all.**
  `assert_stable_availability` rejects a record whose `available_at` moves
  between pulls, matched on `payload.external_id`. Many news APIs re-timestamp
  a story on update or backfill, silently rewriting the one number the whole
  contract depends on. A vendor with no stable id passes unchecked, so its
  adapter must synthesize one and its provenance document must say how.
- **`session_windows` takes an optional `prior_close`.** The fallback is 16:00
  ET on the previous *calendar* day, which is wrong across weekends — a Monday
  decision would miss Friday afternoon. The fallback exists so compressors are
  testable in isolation and is documented as not good enough for a registered
  run; the snapshot builder passes the real prior close.
- **The compressors are explicitly unvalidated.** Windows, baselines and
  bucket edges are frozen so results are *reproducible*, not because any of
  them is known to be predictive. The docstrings say so, so nobody six months
  from now mistakes a frozen constant for a validated one.

**Independence is verified, not asserted.** `exogenous/` imports nothing from
`orb_m/` or `aat_v1/`. There is an AST test enforcing it, and the package was
physically deleted and the remaining 228 tests run green.

### Phase 2 — portable Actor identity (`src/athena_trading/aat_v1/`)

30 tests. The only phase that touched V1, and the riskiest.

**The pinning test was written first**, before any refactor —
`tests/test_aat_v1_identity_pinning.py` holds the four registered digests as
literals, with a docstring saying they must never be regenerated from the code
they guard. If a refactor moves one, every V1 receipt stops verifying while the
code keeps running.

```
prompt_hash        b7fa5ec9928d3ec22225bd757aa25b408ffdc9a5d3897bf8717a9d7d44bca88a
inference_hash     2c1cc91e081dd2f02283a520b0d01a226e9fc8320844e2126f95099de2b68633
action_schema_hash 852c32a80b5f4187ea6629270bfaf14162779e1a742690716a337eb107a26b3c
model_hash         9c4aaaa136ed098624bee45f5f75d0654f4d56ea526fc4cc51a1f3f3bf1ae5db
```

All four unchanged after the refactor.

- `providers.py` — descriptor carrying endpoint, payload builder, response
  readers, pricing guard. `model_hash` is provider-scoped.
- `actor_canary.py` / `four_arm.py` — take the `OPENAI` descriptor by default,
  so no V1 behaviour or hash changes. `four_arm.py` is pinned explicitly rather
  than parameterized; its run is complete and frozen, and making finished
  evidence portable is churn.

**Correction to the plan as originally written.** It said Phase 2 would make
`PROVIDER_MODEL_MUTABILITY` stop being a permanent limitation. It does not, and
must not, for V1: that limitation is recorded in a frozen protocol whose
holdout is consumed, and rewriting how spent evidence describes itself is
precisely the retroactive edit this governance exists to prevent.
`readiness.py` and `training.py` keep their literal model pin untouched. What
Phase 2 delivers is that a *V2* protocol can register a provider-scoped
identity and therefore never inherits the limitation.

**No second vendor is registered.** `registered_providers()` returns
`("openai",)` and a test asserts it. Shipping an integration that cannot be
verified against a live API would trade a named limitation for an unnamed one.

#### The non-consuming sufficiency probe

`audit_holdout_preflight()` hardcodes `sealed_holdout_consumed: True`, so
merely *asking whether the dataset is big enough* spends the authorization.
That is what cost the V1 holdout: the frozen compiler found 249 of a required
300, the gate correctly returned `INVALID_EVIDENCE`, and a single-use
authorization was spent on a question that should have been settled before it
was issued.

`probe_holdout_sufficiency()` takes **no authorization argument at all** — it
cannot consume one because it is never given one, which is a stronger guarantee
than remembering not to. A test asserts those parameter names do not exist.
Verified against the exact V1 scenario:

```json
{ "qualified_opportunity_count": 249,
  "minimum_opportunity_count": 300,
  "status": "INSUFFICIENT",
  "sealed_holdout_consumed": false }
```

It returns a count and a verdict only — no per-symbol breakdown, no
distribution — so it cannot become a tuning channel, and every call is stamped
with the assignment hash so redrawing-until-it-clears appears in the log rather
than in someone's shell history.

### Phase 3 — experiment machinery (`src/athena_trading/aat_v2/`)

Five modules, ~617 lines, 49 tests.

| Module | Responsibility |
| --- | --- |
| `arms.py` | Five arms; treatment asymmetry; arm E equal-budget guard |
| `packet.py` | V2 Actor packet; fails closed in both directions; post-hoc ledger audit |
| `assignment.py` | Symbol-level split; linked-symbol grouping |
| `contract.py` | V2 claim contract; refuses to freeze without measured coverage |

Plus `scripts/draw_aat_v2_assignment.py`.

**The asymmetry is structural, not a flag.** Arm A cannot have a packet built
for it at all — it is the frozen strategy, not an Actor with features withheld.
`build_packet` fails closed in both directions: a treated arm without the
vector would silently degrade to a V1 packet and produce a null for the wrong
reason; a control arm with it would destroy the comparison. Neither is
recoverable from a ledger afterwards, so a finished run is audited again.

**Arm E is capped at one arm-D call's output budget** (4 turns × 64 tokens ≤
256). Without that, a win for deliberation is unattributable — it could simply
be more thinking tokens.

#### The dataset assignment is drawn

`configs/aat_v2_dataset_assignment_v1.json`, seed 20260908:

```
assignment_hash  46f25ba0f64c5088add6d37a72752d6fbe156de6c33e8b8d7d4415e160f5e731
TRAINING (35)    AAPL ADBE AMAT AVGO BAC BRK.B COIN COST CRM CVX DIS INTC KO
                 LLY MA META MSFT MU NFLX NKE NVDA ORCL PANW PEP PFE PLTR
                 RIVN SMCI TMO UBER UNH V VZ WMT XOM
SEALED HOLDOUT   AMD AMZN BA C CMCSA CSCO GOOG GOOGL HD JNJ JPM PG PYPL QCOM
     (15)        TSLA
```

Split by symbol, not by opportunity: sessions of one name are correlated, and
an opportunity-level split would put February's AAPL in TRAINING and March's
AAPL in the holdout, so the holdout would measure memorization. It was drawn
while **no exogenous feature existed anywhere in the repository**, which is the
strongest available form of drawing before looking.

**The draw exposed a real defect.** The first version split by symbol alone, and
GOOG and GOOGL landing in the same partition was luck, not design. Dual-class
shares of one issuer move as one asset; a split pair would have contaminated
the holdout. `LINKED_SYMBOL_GROUPS` now binds them into a single draw unit,
**curated by hand** — a prefix-matching rule fires on V/VZ, BA/BAC and C/CVX,
which are unrelated issuers. A test checks sixty seeds. The first assignment was
deleted and redrawn under the fix.

---

## 4. Current status

| Item | State |
| --- | --- |
| Vision, plan, source spec | Written |
| Phase 1 exogenous layer | Built, 61 tests |
| Phase 2 provider registry | Built, 30 tests, V1 digests pinned and unchanged |
| Phase 2 sufficiency probe | Built, verified on the 249/300 case |
| Phase 3 arms / packet / contract | Built, 49 tests |
| Phase 3 dataset assignment | **Drawn and frozen** |
| Macro ALFRED adapter | Built and fixture-tested (`exogenous/alfred.py`). Provenance in `docs/ALFRED_MACRO_SOURCE_PROVENANCE.md`. No dump is committed. |
| GDELT news parser | Built and fixture-tested. Bounded coverage study complete. **Rejected for this run** (0.0778% `news_present` on the 16,705-opportunity TRAINING index). News stays explicitly missing. See `docs/GDELT_NEWS_SOURCE_PROVENANCE.md`. |
| Phase 3 coverage / claim contract | **Frozen, macro-only.** Required source is `macro` at floor 0.90; frozen measured coverage is macro 1.0, news 0.0. `claim_authorized` is false. Do not freeze a new contract. |
| Phase 3 protocol registration | **Registered as `REGISTERED_NOT_AUTHORIZED`.** `configs/aat_v2_registered_protocol_v1.json`. Actor identity remains OpenAI / `gpt-5.6-terra`. |
| Phase 3 TRAINING dry-run plans | Present. `configs/aat_v2_training_plan_v2.json` supersedes v1: 16,705 opportunities, **116,935** planned calls, `dry_run: true`, `execution_authorized: false`. |
| Model execution options | Documented in `docs/AAT_V2_MODEL_EXECUTION_OPTIONS.md`. DeepSeek Flash dry-run / fixture preflight planner exists; it does not authorize TRAINING. |
| Phase 3 TRAINING run | **Blocked — needs authorized TRAINING execution, Actor credentials, and spend.** Not blocked on missing adapters. |
| Phase 4 sealed holdout | Not started; needs a completed Phase 3 TRAINING run and separate authorization. The consumed AAT-V1 holdout must not be reused. |
| AAT-V1 | Frozen and closed. Holdout consumed (`INVALID_EVIDENCE`). Limitation stands. |
| Execution authority | `ABSENT` everywhere. No live-trading code exists. |

### Why the run is blocked

Not on adapters, and not on an unfrozen contract. The macro-only claim contract
is already frozen and the protocol is already registered. What remains is
**authorization**: `registration_status` is `REGISTERED_NOT_AUTHORIZED`,
`execution_authorized` is false on the TRAINING plans, and
`execution_authority` is `ABSENT`. A full run also needs a priced Actor
credential (DeepSeek Flash and hosted Qwen are planning options in
`docs/AAT_V2_MODEL_EXECUTION_OPTIONS.md`) and an explicit spend ceiling.

The earlier coverage refusal still exists as a guard, and it already fired for
news: GDELT's 0.0778% measured coverage could not meet the floor, so news was
dropped from `required_sources` rather than lowering the floor. Do not reverse
that by treating a missing adapter as the next coding task.

```
registration_status: REGISTERED_NOT_AUTHORIZED
claim_authorized: false
execution_authority: ABSENT
```

---

## 5. Handoff — what to do next

### Immediate: authorized TRAINING execution, not more adapters

The ALFRED adapter exists. GDELT was evaluated and rejected for this run. The
macro-only claim contract is frozen; the protocol is
`REGISTERED_NOT_AUTHORIZED`; the corrected dry-run plan is 116,935 calls.
Nothing below authorizes TRAINING, freezes a new contract, or touches the
consumed AAT-V1 holdout.

1. **Do not acquire a news dump for this run.** GDELT coverage was 0.0778%
   under the frozen compressor window. News remains explicitly missing unless
   a future provider supplies stable historical entity IDs and defensible
   point-in-time coverage, and that would be a *new* required-source decision
   — not a silent add to the frozen contract.
2. **Do not freeze another claim contract.** The current one already binds
   measured macro coverage 1.0 at a 0.90 floor. Lowering the floor because
   coverage disappointed is the move the contract exists to prevent.
3. **Obtain Actor credentials and an explicit spend ceiling.** The planner
   `scripts/plan_aat_v2_deepseek_flash.py` estimates DeepSeek Flash cost from
   the frozen prices in `docs/AAT_V2_MODEL_EXECUTION_OPTIONS.md` (~$32.68
   off-peak / ~$65.35 peak cache-miss worst case). Plan-only needs no key.
   `--preflight` fails closed without `DEEPSEEK_API_KEY` and dry-runs a tiny
   N-call against `configs/fixtures/deepseek_flash_preflight_v1.json`. It does
   not authorize TRAINING, does not register DeepSeek as the V2 Actor, and
   does not consume a holdout. `execution_authority` stays `ABSENT`.
4. **Treat DeepSeek Flash and hosted Qwen as candidate runtimes, not as the
   registered Actor.** Changing off Terra changes identity. It needs a
   provider adapter, a new provider-scoped identity, and a separately verified
   pilot. The Terra protocol must not be silently reused.

### Then, in order

5. Resolve the E-arm JSON truncation (redesign E, or a B/C/D-only protocol
   amendment) before any full run. The local Qwen 100-opportunity pilot
   accepted 0/400 E responses at 64 tokens. That is in the execution-options
   note; it is not TRAINING evidence.
6. Only after **explicit TRAINING authorization**: run the comparison. Audit
   it. Label it in-sample evidence, because that is what it is.
7. Compile holdout opportunities only under a future V2 authorization. If a
   sufficiency question comes up first, use `scripts/probe_holdout_sufficiency.py`
   — it takes no authorization and cannot consume one. Do not point it at the
   consumed AAT-V1 holdout.
8. Phase 4 sealed holdout requires separate, explicit authorization.

### Open decisions still outstanding

- **Which hosted runtime to authorize, if any.** DeepSeek Flash API vs
  RunPod-hosted Qwen vs staying on Terra. Cost and E-arm compatibility are in
  `docs/AAT_V2_MODEL_EXECUTION_OPTIONS.md`. None of those options is authorized
  by the dry-run planner.
- **Whether arm E earns its cost.** It is the most interesting thing
  TradingAgents does and the least likely to survive an equal-budget
  comparison. The current 64-token turn truncates required JSON. It stays
  optional until a protocol amendment says otherwise.
- **Which Tier-2 sources to adopt at all.** Social has sound timestamps but
  awkward retrieval — Pushshift has no public API since losing privileged
  ingestion after Reddit's 2023 changes; historical access now runs through
  Arctic Shift, Academic Torrents, or Reddit's researcher program, all frozen
  bulk archives. Fine for backtest, useless if this ever goes live. Not on
  the critical path for the current macro-only run.
- **A fundamentals vendor**, if fundamentals is adopted later. Must serve
  as-reported figures time-indexed to release date — Sharadar's as-reported
  dimensions, Calcbench's as-filed capture, LSEG's point-in-time product. A
  current-view REST API is disqualified regardless of price.

The coverage floor for *this* run is already decided: 0.90 on macro, measured
at 1.0. Do not reopen it after looking at a result.

### Known limitations carried forward

- `session_windows` calendar-day fallback is wrong across weekends; always
  pass `prior_close`.
- Compressor scoring semantics are frozen but unvalidated.
- Only one Actor provider is registered (`openai`). DeepSeek Flash is an
  unregistered candidate probed by the dry-run planner.
- `four_arm.py` is pinned to OpenAI by design.
- Exogenous coverage is denser for large caps and for names that later moved.
  Coverage density is emitted as a feature so the effect stays measurable, but
  it is not eliminated.
- GDELT is not adopted for the current run; news coverage is explicitly zero
  in the frozen contract.

---

## 6. Standing judgement

The governance is the asset. It is the thing TradingAgents has no equivalent
of, and the reason an eventual Athena claim would mean something where theirs
cannot. Nothing in this work weakened it: V1 stayed frozen, its digests are
pinned as literals, its holdout stays consumed, its limitation stands, and the
new contract refuses to freeze against a number nobody has measured.

The honest summary of V2 so far is that it removed the ceiling on the
experiment, froze a macro-only contract, and registered a protocol that still
cannot spend. Whether adaptation helps is still unknown. The machinery can
produce a defensible answer either way — including "it did not help" — once
TRAINING is separately authorized.

The next real risk is not a missing adapter. It is authorizing spend, or
swapping Terra for DeepSeek/Qwen, without a new provider-scoped identity; and
the temptation, once a full run is expensive or E truncates, to lower a frozen
floor or reuse the consumed V1 holdout. The contract, the protocol status, and
the consumed-holdout registry are written to make those moves visible. Keep
them that way.
