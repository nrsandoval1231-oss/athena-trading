> **RESEARCH REFERENCE — isolated component.** Governing product architecture: [Athena Trading Canon](../../docs/ATHENA_TRADING_CANON.md). This historical scaffold does not establish current product or broker readiness; see the repository implementation status and audit.

# Astra Desk

Phase 0–3 scaffold of a declarative, machine-addressable multi-agent trading desk.
The committed Trading Desk Contract is the source of truth.

**This repository is walk-forward / paper / checkpointed-paper / autonomous-paper
only.** There is no live broker, no secrets, and no live trading authority.
Phase 2 (`live_with_human_checkpoint`) means simulated paper deploys that cannot
proceed without a structured human approval record. Phase 3 (`autonomous`) means
**autonomous paper** under an armed kill switch and a verified ledger — not
Alpaca, not live money. `live_trading_authority` stays false.

## Current baseline

See [`docs/STATUS.md`](docs/STATUS.md) for the handoff snapshot. Short version:

- Operator loop: `python -m astra_desk paper-session` (fixture tape → rule
  agents → gate → risk / kill switch → paper broker → ledger). Default is
  fully offline. Alpaca paper is `--broker alpaca-paper`. Persist across
  process runs with `--new-store --store data/ledger`. Read it back with
  `python -m astra_desk report --store data/ledger`.
- Phase 0 walk-forward, Phase 1 paper-period, Phase 2 human-checkpoint paper,
  Phase 3 autonomous paper, nightly calibration, a **paper broker port**, and a
  **paper tape / market-data port** (fixture bars → rule-agent features).
- **No live broker.** Default orders hit an in-memory fixture. Alpaca paper is
  opt-in and pinned to `https://paper-api.alpaca.markets`.
  `live_trading_authority` is false. Kill-switch reenable is
  `manual_human_only`.
- Rolling-**50** setups is the accuracy measurement window. Seat eviction
  binds only at `capital.evaluation.min_sample_live_setups` **200**.

## What Phase 0 does

1. Load `contracts/astra-desk.v1.yaml` and validate it against
   `contracts/astra-desk.schema.json`.
2. Run rule-based voting agents (`456_scout`, `067_reader`, `240_exit`,
   `666_skeptic`) that read per-setup tape features. Stub agents exist only
   as explicit test doubles (`TradingDesk(use_stubs=True)`).
3. Score a weighted gate (`456_scout` 1.0, `067_reader` 1.5, `240_exit` 1.0,
   `666_skeptic` −1.0), require quorum 5, require `ev_estimate > 0`, and apply
   conviction bands (0.5× at ≥ 3.5, 1.0× at ≥ 4.5).
4. Let the front-man execute that decision only — no unilateral veto — and
   require logs of `decision`, `gate_inputs`, and `rationale_hash`.
5. Enforce risk-officer hard limits and an independent kill switch
   (`drawdown_pct >= 8` or daily loss limit breach → `flatten_all_and_halt`,
   reenable `manual_human_only`).
6. Append every round (and each paper settlement) to an append-only ledger.
   Capital books start at $500 USD. Sharpe / Sortino / win rate / expectancy
   are `null` until the closed sample is large enough to compute. No
   performance claim is made.
7. Walk a fixture sequence in `walk_forward_replay` mode and report whether
   the contract phase_0 exit criteria hold: **positive expectancy** and
   **max_dd < 15%**, both read from the books. A null expectancy fails closed
   (criteria unmet).

## What Phase 0 does not do

- No live orders, live Alpaca host, or committed credentials. The default
  broker is an in-memory fixture. Alpaca **paper** is opt-in via
  `--broker alpaca-paper` and fails closed without env keys.
- No silent flip of LIVE authority. Calibration updates **working** gate
  weights from fixture ledger outcomes; the committed contract baseline is
  unchanged unless you pass `--apply`.
- CI and default commands never hit the network. `paper-session`, walk-forward,
  paper-period, human-checkpoint, autonomous, and calibration sequences use
  committed fixtures. The tape command defaults to `fixtures/tape/sample.json`.
  Voting agents are deterministic rules, not LLM calls. Optional Alpaca
  **data** fetch (`--source alpaca`) is fail-closed without keys and is not
  the broker. Optional Alpaca **paper** broker (`--broker alpaca-paper`) is
  fail-closed without keys and is not live trading.

## Setup

Python 3.11+ (3.12 is fine).

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Paper session (operator loop)

End-to-end on shipped fixtures. Tape (bars → features) → rule agents →
weighted gate → risk / kill switch → paper broker → append-only ledger.
Default is **fully offline** (fixture tape + fixture broker). Human-checkpoint
and Phase 3 autonomous paper remain their own commands.

```bash
python -m astra_desk paper-session
python -m astra_desk paper-session --broker fixture
python -m astra_desk paper-session --broker alpaca-paper
```

JSON includes `deploys`, `refuses`, `fills`, `halts`, per-round broker status
and settlement, `books`, `ledger_verified`, and
`live_trading_authority: false`. Kill-switch action is
`flatten_all_and_halt`; reenable is `manual_human_only`. Exit 0 if the
session finished without a halt; exit 1 if the command refused (missing
Alpaca keys, live host, bad tape); exit 2 if the kill switch halted.

`--broker alpaca-paper` requires `ALPACA_API_KEY` / `ALPACA_API_SECRET` and
only talks to `https://paper-api.alpaca.markets`. CI never uses that path.

`--fixture` points at a tape document (default `fixtures/tape/sample.json`).
The shipped sample deploys once, refuses twice, and fills the deploy on the
fixture broker.

## Durable ledger (local file store)

Paper sessions and calibration can accumulate across process runs. The
append-only chain is `chain.jsonl` under a store directory; `snapshot.json`
holds books, kill-switch flags, and working weights so a later process can
resume. Integrity still verifies after reload. Corrupt chains fail closed.
There is no cloud database and no secrets in the store.

Default directory is `data/ledger/` (gitignored). Persistence is explicit:

```bash
python -m astra_desk paper-session --new-store --store data/ledger
python -m astra_desk paper-session --store data/ledger
python -m astra_desk calibrate --store data/ledger
```

`--new-store` creates an empty store and refuses to clobber an existing chain.
`--store PATH` resumes. Omit both flags to keep the ledger in memory only
(CI default). `calibrate --store` reads persisted round outcomes; it does not
ingest the calibration fixture. Override the default directory with
`ASTRA_DESK_DATA_DIR`.

## Operator report (read-only)

Summarize a durable store: books, ledger integrity, kill switch, working vs
contract gate weights, and phase-progress hints when a matching summary is
on the chain. **Requires `--store`.** There is no in-memory demo. Missing or
corrupt chains fail closed. No broker calls. `live_trading_authority` is
always false.

```bash
python -m astra_desk report --store data/ledger
python -m astra_desk report --store
python -m astra_desk report --store data/ledger --human
```

`--store` with no path uses `data/ledger`. JSON goes to stdout. `--human`
also prints a short text summary to stderr. Phase 1–3 fields are `unknown`
unless that command wrote into the same store.

## Walk-forward replay (phase_0)

Agents → gate → risk → paper settlement from fixture bars → ledger (or halt):

```bash
python -m astra_desk walk-forward
```

Equivalent:

```bash
astra-desk walk-forward --fixture fixtures/walk_forward/phase0_sample.json
```

Output is JSON. Exit code 0 means the walk finished and phase_0 exit criteria
are met; 1 means the walk finished but criteria are unmet (including a null
expectancy); 2 means the kill switch halted the walk.

`--mode` accepts only `walk_forward_replay`. Phase 3 autonomous raises and
does not run.

The shipped sample is four setups on one desk: two take-profits, one skeptic
refuse (failed breakout + crowding features), one stop. It is fixture tape,
not a track record. Gate outcomes come from rule agents on those features,
not from hand-set convictions.

### Walk-forward fixture format

```json
{
  "schema": "astra-desk/walk-forward-v1",
  "mode": "walk_forward_replay",
  "setups": [
    {
      "setup_id": "wf-001",
      "symbol": "PAPER-ES",
      "regime": "trending",
      "ev_estimate": 0.12,
      "price": 100.0,
      "side": "long",
      "correlation_group": "index-beta",
      "broker": {"size_units": 40.0, "kelly_fraction": 0.2},
      "features": {
        "volume_z": 2.4,
        "volume_lead_bars": 3.0,
        "price_change_pct": 0.0,
        "cancel_rate": 0.0,
        "trade_to_quote_ratio": 0.55,
        "unique_participant_z": 1.8,
        "spread_widen_pct": 0.0,
        "invalidation_price": 95.0,
        "structure_quality": 1.0,
        "take_profit_r_multiples": [1.0],
        "failed_breakout": false,
        "crowding_z": 0.2
      },
      "bars": [
        {"ts": "2024-01-02T14:30:00Z", "open": 100.2, "high": 105.4, "low": 99.8, "close": 105.1, "volume": 1500}
      ]
    }
  ]
}
```

Required features (fail closed if missing):

| Agent | Reads |
| --- | --- |
| `456_scout` | `volume_z`, `volume_lead_bars`, `price_change_pct` |
| `067_reader` | `cancel_rate`, `trade_to_quote_ratio`, `unique_participant_z`, `spread_widen_pct` |
| `240_exit` | `invalidation_price`, `structure_quality`, `take_profit_r_multiples` plus `price`/`side` |
| `666_skeptic` | `failed_breakout`, `crowding_z` (opposition feeds the −1.0 gate weight) |

`218_broker` still sizes from `broker.size_units` under `max_kelly_fraction` 0.25
and `never_full_kelly`. After a deploy, paper PnL is taken from `bars` against
the **exit agent's** stop / take-profit (stop wins if both print in the same
bar). An optional `settlement.realized_pnl` overrides bar replay. Hand-set
voting `agents.*.conviction` values are rejected. A live `mode` in the file
is rejected. There is no network fetch.

## Paper tape / market-data port

Setups can be built from OHLCV bars instead of a hand-authored `features`
blob. This is **data in**, not orders out. The broker port stays separate.

```bash
python -m astra_desk tape
python -m astra_desk tape --round
python -m astra_desk walk-forward --fixture fixtures/tape/sample.json
```

`tape` (default) runs walk-forward over the shipped tape. `tape --round` runs
only the first setup as one desk round. Both reuse the existing rule agents,
gate, and paper books. Exit codes match walk-forward / round.

OHLCV can derive some features. Microstructure / thesis fields cannot and
must be supplied in `explicit_features` (the desk will not invent organic
flow):

| From bars (derived) | Must be explicit |
| --- | --- |
| `volume_z`, `volume_lead_bars`, `price_change_pct` | `cancel_rate` |
| `spread_widen_pct` | `trade_to_quote_ratio` |
| `invalidation_price` (swing vs side) | `unique_participant_z` |
| `structure_quality` (range proxy) | `crowding_z` |
| `failed_breakout` (proxy; overridable) | `take_profit_r_multiples` |

Derivation fails closed with fewer than 8 bars, or if any explicit key is
missing. `regime` and `ev_estimate` stay authored. Lookback bars are `bars`;
optional `forward_bars` are the walk-forward settlement tape after entry (so
lookback lows do not immediately stop a deploy). A complete `features` blob
still works unchanged (`fixtures/walk_forward/phase0_sample.json`).

Alpaca **data** fetch is opt-in and never required for CI. It reads
`ALPACA_API_KEY` / `ALPACA_API_SECRET`, talks only to
`https://data.sandbox.alpaca.markets` (or `https://data.alpaca.markets` via
`ALPACA_DATA_URL`), and fails closed without keys or if pointed at a trading
host (`api.alpaca.markets`, `paper-api.alpaca.markets`). Explicit
microstructure still comes from the fixture overlay — bars do not invent it:

```bash
python -m astra_desk tape --source alpaca --symbol AAPL
```

`--symbol` is required with `--source alpaca`. That path runs one round from
fetched bars. It does not submit orders.

## Paper-trading period (phase_1)

Contract source of truth: `capital.evaluation.paper_trading_period_days` and
`rollout.phase_1.duration_days` (both 21). This is a fixture simulation of
session days, not a brokerage connection.

```bash
python -m astra_desk paper-period
```

Equivalent:

```bash
astra-desk paper-period --fixture fixtures/paper/phase1_sample.json
```

The command expands the shipped daily template across 21 calendar dates,
runs each session through the **existing** desk (agents → gate → risk →
ledger / kill switch), and reports `days_completed / 21`. Exit code 0 only
when **duration_complete** (21 unique session days and not halted). Exit 1
if the sim finished short of 21 days. Exit 2 if the kill switch aborted the
period. Reenable remains `manual_human_only`.

`--days N` simulates only the first N session days; duration still requires
21, so `--days 5` reports `duration_complete: false`.

`--mode` accepts only `paper_trading`. Phase 2 checkpoint and phase 3
autonomous raise on this command.

Phase 1 does **not** claim a pass from books alone. Duration is the phase_1
rule. Phase 0 exit criteria (positive expectancy, max_dd < 15%) are still
read from the books and included on the report for chaining; they are not a
substitute for 21 days. The shipped 21-day tape is fixture data, not a
track record.

Compact fixture shape (`days` may replace `start_date` / `session_days` /
`daily_setups`):

```json
{
  "schema": "astra-desk/paper-period-v1",
  "mode": "paper_trading",
  "start_date": "2024-03-04",
  "session_days": 21,
  "daily_setups": [{ "...same setup object as walk-forward..." }]
}
```

## Human-checkpoint paper deploys (phase_2)

Contract source of truth: `capital.evaluation.human_checkpoint_trades` and
`rollout.phase_2.trades` (both 25). Mode name is `live_with_human_checkpoint`,
but **this is not live trading.** Each deploy still goes through paper books.
There is no broker API, no secrets, and `live_trading_authority` stays false.

A deploy is marked executable only after a structured approval record matches
the proposal:

- `approved_by` (non-empty identity)
- `approved_at` (timezone-aware ISO-8601)
- `setup_id` (must match the setup)
- `rationale_hash` (sha256 of the front-man deploy rationale)
- `decision` must be `"approve"`

A boolean `approved: true` is refused. Missing, forged, incomplete, or replayed
records refuse the deploy and do not count. The kill switch still flattens and
halts; reenable remains `manual_human_only`. Phase 3 `autonomous` still raises.

```bash
python -m astra_desk human-checkpoint
python -m astra_desk human-checkpoint --approver operator
```

Equivalent:

```bash
astra-desk human-checkpoint --fixture fixtures/checkpoint/phase2_sample.json --approver operator
```

Without `--approver` (and without per-setup approval records in the fixture),
gate-approved deploys are refused and `trades_completed` stays 0.
`--approver NAME` simulates a human: it binds a full approval record to each
proposal hash. That is still paper/sim.

The command reports `trades_completed / 25`. Exit code 0 only when
**trade_count_complete** (25 approved paper deploys and not halted). Exit 1
if the sim finished short of 25. Exit 2 if the kill switch aborted the
sequence.

`--trades N` simulates only the first N setups; the count still requires 25,
so `--trades 5` reports `trade_count_complete: false`. Further deploys after 25
are stopped. Phase 2 does **not** claim a pass from books alone.

`--mode` accepts only `live_with_human_checkpoint`. Autonomous paper uses the
`autonomous` command. A live broker is still refused.

Compact fixture shape (`setups` may replace `trade_count` / `setup_template`):

```json
{
  "schema": "astra-desk/human-checkpoint-v1",
  "mode": "live_with_human_checkpoint",
  "trade_count": 25,
  "setup_id_prefix": "cp",
  "setup_template": { "...same setup object as walk-forward..." }
}
```

## Autonomous paper (phase_3)

Contract source of truth: `rollout.phase_3.mode: autonomous` with
`requires: [kill_switch_armed, ledger_verified]`. **This is not live trading.**
The loop uses the existing desk (agents → gate → risk → paper books / ledger /
kill switch) with **no per-trade human approval**. There is no broker API, no
secrets, and `live_trading_authority` stays false. Phase 2 human-checkpoint
remains available as its own command.

The loop starts only when both requirements hold:

- **kill_switch_armed** — kill switch is present, not tripped, armed for
  `flatten_all_and_halt` on breach; reenable stays `manual_human_only`
- **ledger_verified** — the append-only hash chain over recorded rounds
  recomputes cleanly (an empty ledger is valid)

If either fails, autonomous mode refuses (fail closed). A live broker flag or
fixture `mode: live` still raises. The kill switch can still flatten and halt
mid-run.

```bash
python -m astra_desk autonomous
python -m astra_desk autonomous --rounds 2
```

Equivalent:

```bash
astra-desk autonomous --fixture fixtures/autonomous/phase3_sample.json
```

`--broker` accepts only `paper`. `--mode` accepts only `autonomous`. The command
reports `rounds_processed / rounds_requested`, `kill_switch_armed`,
`ledger_verified`, and `halted`. Exit 0 if the paper loop finished without a
halt; exit 1 if the phase_3 gate refused; exit 2 if the kill switch aborted the
run. The shipped 5-round tape is fixture data, not a track record.

Compact fixture shape (`setups` may replace `round_count` / `setup_template`):

```json
{
  "schema": "astra-desk/autonomous-v1",
  "mode": "autonomous",
  "round_count": 5,
  "setup_id_prefix": "auto",
  "setup_template": { "...same setup object as walk-forward..." }
}
```

## Nightly calibration (from the contract)

Contract source of truth:

- `calibration.weights_update: nightly_from_outcomes`
- `calibration.eviction_rule`: lowest per-regime-calibrated accuracy over
  rolling 50 setups
- `calibration.correlation_monitor: true`
- `regime_classifier.agent_accuracy_tracked_per_regime: true`

The command reads **append-only ledger outcomes** (not a forked ledger),
proposes gate conviction weights from those outcomes, reports per-regime
accuracy over a rolling 50 setups, can *show* an eviction candidate after 50,
and flags agents whose errors co-move. Default is a dry run.
`live_trading_authority` stays false.

Seat eviction **binds at 200** (`capital.evaluation.min_sample_live_setups`),
not at 50. Rolling-50 scores stay visible in the 50–199 range; `--evict`
fails closed until n≥200 and does not invent accuracy below 50.

```bash
python -m astra_desk calibrate
python -m astra_desk nightly
python -m astra_desk calibrate --apply
```

Equivalent:

```bash
astra-desk calibrate --fixture fixtures/calibration/nightly_sample.json
```

`--apply` writes the proposed weights onto the **working** gate and appends a
`calibration_nightly` ledger entry (previous weights included). That update
is reversible in-process via `CalibrationEngine.nightly_from_outcomes(...,
revert=True)` / `--revert`. `--evict` disables the lowest-accuracy gating
seat only when n≥`min_sample_live_setups` (200). It **fails closed** below
that bind (and does not invent accuracy below the rolling 50). A disabled
seat is marked, not deleted: agent evaluation and ledger history remain.

Weight formula: `|contract_weight| * (0.5 + calibrated_accuracy)`, using the
mean of per-regime accuracies. `666_skeptic` stays a **negative** weight.
The next gate evaluation uses `effective_weights()` (disabled seats are
zeroed). Baseline contract weights are not rewritten.

`--mode` accepts `paper_trading` or `walk_forward_replay`. `--broker` accepts
only `paper`. A live broker flag or fixture `mode: live` still raises.

The shipped sample is eight setups across four regimes. Scout is always
correct and skeptic is always incorrect so weights move; n=8 is below 50, so
rolling accuracy is not invented and eviction is ineligible. Fixture tape,
not a track record.

CI (`.github/workflows/ci.yml`) installs the package with `pip install -e ".[dev]"`
and runs `pytest` on pull requests and on `main`.

## Run one paper desk round

```bash
python -m astra_desk round
python -m astra_desk round --broker fixture
python -m astra_desk broker-preflight --broker fixture
```

Default `--broker fixture` submits/cancels/tracks in memory (no network, no
keys). The JSON round payload includes `broker_order`. Exit code 0 means the
paper round executed, 1 means refuse/veto, 2 means kill-switch halt.

Alpaca **paper** is opt-in. It reads `ALPACA_API_KEY` and `ALPACA_API_SECRET`
only, talks only to `https://paper-api.alpaca.markets`, and fails closed if
keys are missing or the host is not paper:

```bash
python -m astra_desk broker-preflight --broker alpaca-paper
python -m astra_desk round --broker alpaca-paper --mode paper_trading
```

Do not set `ALPACA_BASE_URL` to `https://api.alpaca.markets`. Live hosts are
rejected. Never commit keys. Other built-in round scenarios: `refuse`,
`skeptic`, `ev-refuse`, `veto`, `halt`, `full-size`.

## Tests

```bash
pytest
```

Coverage includes: contract schema validation, feature-driven deploy / refuse /
skeptic weight, risk veto / kill switch, multi-setup walk-forward, phase_0
exit criteria, phase_1 duration incomplete/complete, paper-period kill-switch
abort, phase_2 refuse-without-approval / accept-with-record / count-at-25 /
kill-switch abort, phase_3 refuse-if-not-armed / refuse-if-ledger-broken /
autonomous paper when armed+verified / mid-run halt, nightly weights from
outcomes, rolling-50 accuracy with eviction bind at 200, correlation flags
on co-moving errors, insufficient-sample fail-closed, revert of applied
weights, fixture broker submit/cancel/track, Alpaca paper fail-closed
without keys / non-paper URL reject, fixture tape bars → features →
deploy/refuse, Alpaca data fail-closed without keys / trading-host reject,
offline `paper-session` (tape → gate → fixture broker → ledger), Alpaca paper
session without keys fail-closed, durable ledger write → reload → verify,
corrupt chain refused, default `data/ledger` gitignored, operator `report
--store` happy path plus missing/corrupt fail-closed and refuse without
`--store`, and live-broker rejection.

## Layout

```
contracts/                       # YAML contract + JSON Schema
docs/STATUS.md                   # current baseline handoff
.github/workflows/ci.yml         # install + pytest on PRs and main
fixtures/walk_forward/           # committed replay tape (no network)
fixtures/tape/                   # OHLCV lookback → rule-agent features (no network)
fixtures/paper/                  # phase_1 paper-period template (no network)
fixtures/checkpoint/             # phase_2 checkpointed-paper template (no network)
fixtures/autonomous/             # phase_3 autonomous-paper template (no network)
fixtures/calibration/            # nightly calibration tape with per-agent outcomes
data/ledger/                     # local append-only ledger store (gitignored; not committed)
src/astra_desk/                  # desk, gate, risk, ledger, store, report, walk-forward, paper session, paper, checkpoint, autonomous, calibration, paper broker, paper tape / market data, CLI
tests/
```

Agent ids and gate weights are taken from the contract and are not renamed.
