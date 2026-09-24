> **RESEARCH REFERENCE — isolated component.** Governing product architecture: [Athena Trading Canon](../../../docs/ATHENA_TRADING_CANON.md). This historical scaffold does not establish current product or broker readiness; see the repository implementation status and audit.

# Current baseline

This is a handoff snapshot of what Astra Desk **can do now**. It is paper /
fixture scaffolding, not a live desk and not a performance claim.

## Authority

- **No live broker / no LIVE authority.** `live_trading_authority` is **false**
  on every round, report, calibration write, and broker order.
- Paper orders go through a broker port. **Default is the in-memory fixture**
  (no network, no keys). **Alpaca paper is opt-in** and pinned to
  `https://paper-api.alpaca.markets`. Any other host is rejected.
- Market data is a **separate port**. Default is the shipped fixture tape.
  Optional Alpaca **data** fetch uses `https://data.sandbox.alpaca.markets`
  (or `https://data.alpaca.markets`). Trading hosts are rejected. Missing keys
  fail closed. CI never uses the network path.
- Kill-switch action is `flatten_all_and_halt` (cancel working broker orders
  and mark books flat). Reenable is **`manual_human_only`**.
- Optional **local ledger store** (`data/ledger/`, gitignored). Append-only
  JSONL plus a books/kill-switch snapshot. No cloud DB. Fail closed on a
  corrupt hash chain. `paper-session --store` writes; `calibrate --store`
  and `report --store` read. `report` is read-only and requires `--store`.

## What is in

| Slice | Command | What it actually is |
| --- | --- | --- |
| Paper session | `python -m astra_desk paper-session` | Operator loop: fixture tape → agents → gate → risk / kill switch → paper broker → ledger. Default fully offline. |
| Durable ledger | `python -m astra_desk paper-session --new-store --store data/ledger` | Local append-only `chain.jsonl` + snapshot. Resume with `--store`. `calibrate --store` reads it back. Corrupt chain fails closed. |
| Operator report | `python -m astra_desk report --store data/ledger` | Read-only JSON of books, ledger, kill switch, weights. Requires `--store`. Fail closed if missing/corrupt. Optional `--human`. |
| Phase 0 | `python -m astra_desk walk-forward` | Fixture replay. Exit criteria: positive expectancy and max_dd < 15% (null expectancy fails closed). |
| Phase 1 | `python -m astra_desk paper-period` | 21 session-day paper sim. Completeness is duration, not a books pass. |
| Phase 2 | `python -m astra_desk human-checkpoint` | 25 **checkpointed paper** deploys. Mode name is `live_with_human_checkpoint`; it is not a live broker. Structured approval required. |
| Phase 3 | `python -m astra_desk autonomous` | Autonomous **paper** when `kill_switch_armed` and `ledger_verified`. Not Alpaca, not live money. |
| Calibration | `python -m astra_desk calibrate` | Nightly weights from ledger outcomes, rolling-50 per-regime accuracy, correlation monitor. Alias: `nightly`. |
| Paper broker | `python -m astra_desk round --broker fixture` | In-memory submit/cancel/track. Default. Also used by `paper-session --broker fixture`. |
| Alpaca paper (opt-in) | `python -m astra_desk paper-session --broker alpaca-paper` | Requires `ALPACA_API_KEY` / `ALPACA_API_SECRET`. Paper host only. Fail closed without keys. |
| Paper tape / market data | `python -m astra_desk tape` | Fixture OHLCV → rule-agent features. `tape --round` is one desk round. Walk-forward accepts `fixtures/tape/sample.json`. |
| Alpaca data (opt-in) | `python -m astra_desk tape --source alpaca --symbol AAPL` | Data host only (`data.sandbox.alpaca.markets` / `data.alpaca.markets`). Fail closed without keys. Not order routing. |

Voting agents (`456_scout`, `067_reader`, `240_exit`, `666_skeptic`) are
deterministic feature rules. Gate weights start at contract baseline
(scout 1.0, reader 1.5, exit 1.0, skeptic **−1.0**). `--apply` updates
**working** weights only; the committed YAML is not rewritten. Skeptic stays
a negative weight.

OHLCV bars can fill `volume_z`, `volume_lead_bars`, `price_change_pct`,
`spread_widen_pct`, `invalidation_price`, `structure_quality`, and a
`failed_breakout` proxy. `cancel_rate`, `trade_to_quote_ratio`,
`unique_participant_z`, `crowding_z`, and `take_profit_r_multiples` must be
supplied explicitly.

## Sample sizes (honest)

These two numbers are **not the same**:

| Contract field | Value | What it means in code |
| --- | --- | --- |
| `calibration.eviction_rule` rolling window | **50** | Measurement window. Per-regime calibrated accuracy and a lowest-seat *candidate* are reported once n≥50. Below 50, accuracy is not invented (`insufficient_sample_for_rolling_50`). |
| `capital.evaluation.min_sample_live_setups` | **200** | **Seat-eviction bind.** `--evict` disables a seat only when n≥200. At 50≤n<200, rolling-50 scores stay visible but eviction is ineligible (`insufficient_sample_for_min_live_setups`). |

Weight updates may run on smaller samples. Eviction does not delete ledger
history; it marks the seat and zeroes its effective gate weight.

Shipped fixtures are tiny (walk-forward 4 setups, tape 3 setups, paper 21 days,
checkpoint 25, autonomous 5, calibration 8). They exercise the machinery. They
are not a track record.

## What is not in

- Live orders, live Alpaca (`api.alpaca.markets`), committed credentials, or a LIVE authority flip
- LLM agent calls
- A ruff/mypy lint stack (CI is install + pytest)

## CI

GitHub Actions (`.github/workflows/ci.yml`) installs `pip install -e ".[dev]"`
and runs `pytest` on Python 3.11 and 3.12, on pull requests and on `main`.
