# Athena ORB-M v1.0 — status

Updated 4 September 2026.

## What exists

The entry, management, and frozen V1 session-evaluation path are implemented
and tested.

The universe path **has** now been run against real market data: a three-symbol
smoke pull returns 84 sessions and an AAPL median opening volume of 4,078,500,
cross-checked by hand against the raw bar file, and the candidate pool builder
independently arrives at the same 84 sessions from daily aggregates. Everything
downstream has been evaluated only against TRAINING evidence or synthetic
fixtures. This is neither a performance claim nor validation on a sealed
holdout.

| Module | Responsibility | State |
| --- | --- | --- |
| `constitution.py` | Immutable strategy definition; 27-field adaptive surface | Complete |
| `market.py` | Point-in-time features, Wilder true range, VWAP, RVOL, gap | Complete |
| `entry.py` | Range freeze, close-based trigger, frozen R, attempt budget | Complete |
| `management.py` | APDE stages, staircase trail, peak retention, exit pressure | Complete |
| `execution.py` | Bar-walking fill simulator (slippage + commission) | Complete |
| `universe.py` | Point-in-time selection, immutable manifest | Complete |
| `quality.py` | Breakout Quality Score (nine equal-weighted components) | Complete |
| `deterioration.py` | Detectors from bars → signals `exit_pressure` weighs | Complete |
| `session.py` | `run_full_session` (AAT) + `run_symbol_session` (BQS/ceilings) | Complete |

`action_for()` maps confidence onto PASS / REDUCED / STANDARD / HIGH_CONVICTION.
`score_breakout()` produces that confidence and **fails closed**
(`ok=False`, action PASS) when required inputs are missing. `exit_pressure()`
weighs named deterioration signals; `detect_signals` / `pressure_by_minute`
produce those signals from bars so `run_position` exits can fire.
`run_full_session()` remains the frozen AAT / TRAINING evaluator contract.
`run_symbol_session()` enforces constitutional `max_open_positions`,
`max_daily_loss_r`, and `max_drawdown_r` (not enforced by `run_position` alone).

## Confirmed specification

Settled with the strategy author, not inferred:

- Trigger is a completed one-minute **close** beyond the frozen 15-minute range.
  A wick through the level is not a signal. Opening-candle colour does not gate
  direction; it is a logged feature whose predictive value can be tested.
- Both sides stay live. A failed long can later become a valid short. Each
  direction gets one initial attempt plus one continuation re-entry.
- Initial stop is the nearer of structural and volatility invalidation:
  `max(ORL, entry - ATR)` for longs, mirrored for shorts. R is fixed at entry
  and never restated as the trail ratchets.
- Retracement for re-entry is measured from actual entry to peak MFE, not from
  the range boundary.
- Campaign target is 50 names over roughly two years. The 300-paired-episode
  promotion floor is hard: falling short means insufficient evidence, not a
  lowered bar.

## Decisions made in implementation

Additional guards and frozen V1 choices the specification did not cover:

- **Minimum stop distance.** `max(ORL, entry - ATR)` places the stop inside the
  noise when the opening range is narrow, and a tiny R denominator both inflates
  position size and makes every downstream R threshold meaningless. Risk per
  share is floored at `min_stop_atr_fraction` (0.15) of ATR. Frames hitting the
  floor record `stop_basis == "ATR_FLOOR"` so they stay identifiable in analysis.
- **Intra-bar ordering.** See below.
- **BQS weights.** Nine components are equal-weighted until real feature
  distributions from the campaign data are measured. Inventing calibrated
  weights before that measurement would dress a guess as knowledge.
- **Commission.** `commission_per_share` is applied as an adverse currency
  offset in `fill_price` on both entry and exit (preferred over dropping the
  field).
- **Massive retries.** `_request_with_retry` retries only
  `TRANSIENT_HTTP_STATUSES`; permanent 4xx fail fast.
- **Deterioration evidence.** V1 uses only completed bars from the same
  instrument: declining volume, mirrored lower high/low, momentum
  deceleration, adverse candle, VWAP loss, failed new extreme, volatility
  regime shift, and failed opening-range retest. It does not invent absent
  sector or market-proxy data.
- **Re-entry strength.** `run_full_session` (AAT) still uses the constitution's
  moderate-setup retracement floor. `run_symbol_session` applies BQS plus the
  strong/moderate retracement bands; BQS weights remain uncalibrated until
  campaign feature distributions exist.
- **Action scoring.** The TRAINING protocol maps PASS / REDUCED / STANDARD /
  HIGH_CONVICTION to 0 / 0.5 / 1 / 2 times normalized R. This makes regret
  deterministic but is an implementation decision pending empirical
  calibration.

## The intra-bar assumption

Management fires at +0.5R and +1R inside an eight-minute window, so on
one-minute bars a single bar routinely contains both a harvest level and the
stop. OHLC cannot order them. `ADVERSE_FIRST` resolves such bars against the
position and is the default.

`scripts/probe_orb_m_intrabar.py` measures what the assumption is worth:

| Bar volatility | Affected trades | Mean inflation |
| --- | --- | --- |
| Low | 0 / 300 | +0.0000 R |
| Moderate | 11 / 300 | +0.0154 R |
| High | 70 / 300 | +0.1413 R |
| Very high | 121 / 300 | +0.3411 R |

The optimistic ordering manufactures up to +0.34 R per trade on 40% of trades,
and ORB-M selects for high-RVOL opening sessions — precisely that regime.

**Any measured edge below roughly 0.14 R per trade is indistinguishable from
this assumption.** That is a floor on what counts as a result, not a caveat to
mention afterwards. Finer-grained data than one-minute bars would shrink it.

## Open risks

- **Survivorship bias — resolved, pending use.**
  `configs/orb_m_candidate_pool_point_in_time.txt` replaces the 277-name
  model-knowledge pool with 400 names built from daily grouped aggregates over
  the lookback, intersected with the provider's common-stock reference **as of
  2023-12-29**. Each grouped file lists what traded on its own day, so names
  that later delisted are present; asking the type filter for today's listings
  would have reintroduced the same bias, which is why it is asked as of the
  lookback. Measured against the grouped data through 2026-06, sixteen members
  stop appearing in the exchange record and twelve of those fall inside the
  campaign window; five the old pool could not have contained. The old pool is
  still on disk and still carries the bias — it must not be used to freeze this
  campaign.
- **Extended-hours bars in price and dollar volume.** Resolved: statistics now
  describe regular hours only. Recorded because the earlier behaviour silently
  blended premarket into medians.
- **Adaptive surface breadth.** 27 adaptable fields searched simultaneously
  against ~1,000 episodes will find noise. Freeze all but one, promote or
  reject, then unfreeze the next.
- **`peak_retention` is recollected, not measured.** The 0.65 default is a
  starting point to be fitted. Building toward it would fit the author's memory
  rather than the data.
- **The engine existed twice — resolved.** The domain split copied ORB-M into
  this repository rather than moving it, leaving a second, diverging copy in
  `nrsandoval1231-oss/Athena` that never received the statistics fixes.
  `src/athena_arena/orb_m/*`, both ORB-M scripts and the six test modules have
  been removed there (Athena `473ffe6`); its suite goes 507 to 382, exactly the
  125 ORB-M tests, with nothing else referencing them. Athena Core now carries
  `docs/ATHENA_DOMAIN_BOUNDARY.md` as the canonical statement of what lives
  where, and its README says strategy code belongs here. **This repository is
  the only home for the engine.**
- **`tzdata` is a Windows-only dependency and CI cannot see it.** `zoneinfo` has
  no tz database to read on Windows, so the exchange-local grouping raises
  `ZoneInfoNotFoundError` there. CI runs on Linux and reports green regardless.

## Running the universe freeze

Requires `MASSIVE_API_KEY` and network access to the provider.

```text
python scripts/freeze_orb_m_universe.py stats \
  --pool configs/orb_m_candidate_pool_point_in_time.txt \
  --lookback-end 2023-12-29 --campaign-start 2024-01-02 \
  --campaign-end 2025-12-31 \
  --output data/universe/stats.json --data-root data

python scripts/freeze_orb_m_universe.py freeze \
  --stats data/universe/stats.json \
  --output data/universe/manifest.json \
  --provenance POINT_IN_TIME
```

Both outputs are write-once, so choose a new path rather than deleting one.
`POINT_IN_TIME` is honest for the pool above and **not** for
`configs/orb_m_candidate_pool.txt`.

Expect roughly an hour: 400 symbols over 120 calendar days of minute bars.
`--pool-size` on the builder trims it, but not far below ~200 — `SelectionRule`
keeps 50, and a pool near that size makes the rule decorative.

A three-symbol smoke run is worth doing first. The last one caught two real
defects in the statistics code, both silent — they produced plausible numbers
rather than raising, so the suite stayed green while the ranking was computed
over premarket noise. A correct run returns 84 sessions and an AAPL median
opening volume of 4,078,500.

## Rebuilding the candidate pool

```text
python scripts/build_orb_m_pool.py \
  --grouped-root <daily grouped aggregates> \
  --lookback-end 2023-12-29 --campaign-start 2024-01-02 \
  --pool-size 400 \
  --output configs/orb_m_candidate_pool_point_in_time.txt \
  --manifest configs/orb_m_candidate_pool_point_in_time.manifest.json \
  --env-file <a dotenv holding POLYGON_API_KEY>
```

Funnel on the committed pool: 11,768 tickers seen, 9,333 with 95% session
coverage, 4,841 typed common stock, 942 clearing the frozen price and ADV
floors, top 400 by median daily dollar volume. The type filter is load-bearing —
SPY, QQQ and IWM rank 1st, 4th and 8th on raw dollar volume.

The grouped cache stores a **zero-row placeholder for every market holiday**,
32 of them. Counting those as sessions inflates any coverage denominator and
quietly relaxes the floor; the builder drops them and a test pins that.

## Where this sits relative to Athena Core

ORB-M lives here because `docs/ATHENA_DOMAIN_BOUNDARY.md` in
`nrsandoval1231-oss/Athena` assigns ORB rules, stops, harvests, trails,
re-entry, fill simulation and trading campaigns to Athena Trading. It was
originally built in `Athena/src/athena_arena/orb_m/` and moved; no copy remains
there.

`athena_adapter/contracts.py` carries a `canonical_hash` deliberately
reimplemented rather than imported, so this repository does not pin Athena Core
as a build dependency for one pure function. It must stay byte-compatible:
identity hashes recorded here travel into Athena evidence, so
`tests/test_athena_adapter.py` pins digests taken from
`athena_arena.contracts` and fails if the two diverge.

## Next

Campaign path modules (`quality`, `deterioration`, `run_symbol_session`) are
implemented. Remaining work is operational: smoke re-run after statistics
fixes, and a real freeze with provider keys — not a second pool invention.


1. Breakout Quality Score, calibrated against TRAINING feature distributions
   rather than invented weights.
2. Freeze the final claim contract. The bounded four-arm TRAINING comparison
   is complete and audit-passing, but authorizes no adaptation claim.
3. Open the one-time sealed holdout only
   under a separately authorized evaluation.

## Data

**In use.** Minute bars from Massive, unadjusted, extended hours included
(04:00–20:00 ET), pulled on demand into `data/` (gitignored, regenerable).
Daily grouped aggregates from Polygon, unadjusted, one file per session, held
outside this repository under the Trading project; the 84 files consumed by a
pool build are hashed individually into that pool's manifest.

**Evaluated and rejected.** The Polygon minute cache under the Trading project,
and its copies under `Desktop/Eggman` — including `eggbot-historical-data.zip`,
which is the same 17,544 files archived rather than a superset. It is 1-minute,
but regular-session only and covers 1,974 symbols across 651 days at a **median
of 11 days per symbol**, clustered around each symbol's earnings dates. SPY
alone has continuous coverage. It is an earnings-event cache, not a panel, and
using it would measure earnings-gap sessions exclusively. It cannot substitute
for the universe pull.

**A provider caveat.** The Polygon plan in use cannot answer *current* listing
status: `/v3/reference/tickers/{ticker}` returns 404 for tickers that plainly
still trade. Do not build delisting checks on it — measure disappearance from
the grouped aggregates instead. Querying that endpoint *with an explicit date*
works, and is what the pool builder relies on.

## Environment

The universe pull needs `MASSIVE_API_KEY` and network access to
`api.massive.com`; the pool builder needs `POLYGON_API_KEY`, currently in the
screener's `.env` under the Trading project rather than this repository's.

A Claude Code **remote** session has neither — the network policy denies CONNECT
to that host — so pulls run from the Windows machine unless the host is
allowlisted and the key added as an environment secret. A session running
**locally on the Windows machine** does have both, and the smoke run and pool
build recorded here were done that way.
