# Athena ORB-M v1.0 — status

Updated 3 September 2026.

## What exists

The entry and management halves of the engine are implemented and tested
(`src/athena_trading/orb_m/`, 148 tests). Nothing has been run against real
market data.

| Module | Responsibility | State |
| --- | --- | --- |
| `constitution.py` | Immutable strategy definition; 27-field adaptive surface | Complete |
| `market.py` | Point-in-time features, Wilder true range, VWAP, RVOL, gap | Complete |
| `entry.py` | Range freeze, close-based trigger, frozen R, attempt budget | Complete |
| `management.py` | APDE stages, staircase trail, peak retention, exit pressure | Complete |
| `execution.py` | Bar-walking fill simulator | Complete |
| `universe.py` | Point-in-time selection, immutable manifest | Complete |
| Breakout Quality Score | Nine-component confidence score | **Not built** |
| Deterioration detectors | Computing signals from bars | **Not built** (mechanism exists) |
| Session runner | Both directions plus the re-entry branch | **Not built** |

`action_for()` already maps a confidence value onto PASS / REDUCED / STANDARD /
HIGH_CONVICTION, and `exit_pressure()` already weighs named deterioration
signals against a threshold. What is missing in both cases is the code that
produces the inputs.

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

Two guards the specification did not cover:

- **Minimum stop distance.** `max(ORL, entry - ATR)` places the stop inside the
  noise when the opening range is narrow, and a tiny R denominator both inflates
  position size and makes every downstream R threshold meaningless. Risk per
  share is floored at `min_stop_atr_fraction` (0.15) of ATR. Frames hitting the
  floor record `stop_basis == "ATR_FLOOR"` so they stay identifiable in analysis.
- **Intra-bar ordering.** See below.

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

- **Survivorship bias.** `configs/orb_m_candidate_pool.txt` is 277 names
  assembled from model knowledge of currently liquid equities. Companies
  delisted or acquired during a 2024–2025 window cannot appear. The freeze
  records `carries_survivorship_bias: true` and warns. Replacing this with a
  December 2023 constituent or liquidity export is the single highest-value
  improvement available to the campaign's credibility.
- **Adaptive surface breadth.** 27 adaptable fields searched simultaneously
  against ~1,000 episodes will find noise. Freeze all but one, promote or
  reject, then unfreeze the next.
- **`peak_retention` is recollected, not measured.** The 0.65 default is a
  starting point to be fitted. Building toward it would fit the author's memory
  rather than the data.

## Running the universe freeze

Requires `MASSIVE_API_KEY` and network access to the provider.

```text
python scripts/freeze_orb_m_universe.py stats \
  --pool configs/orb_m_candidate_pool.txt \
  --lookback-end 2023-12-29 --campaign-start 2024-01-02 \
  --campaign-end 2025-12-31 \
  --output data/universe/stats.json --data-root data

python scripts/freeze_orb_m_universe.py freeze \
  --stats data/universe/stats.json \
  --output data/universe/manifest.json \
  --provenance CURRENT_SNAPSHOT
```

Both outputs are write-once. `--provenance POINT_IN_TIME` is only honest if the
pool genuinely reflects December 2023 listings.

A three-symbol smoke run is worth doing first; the last one caught two real
defects in the statistics code (sessions keyed on UTC rather than exchange-local
dates, and opening volume measured over premarket bars rather than the opening
range).

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

## Environment

The universe pull cannot run from a Claude Code remote session: the network
policy denies `CONNECT api.massive.com:443`, and no key is present. It runs from
the author's Windows machine, or from a remote session once the host is
allowlisted and `MASSIVE_API_KEY` is added as an environment secret. Doing the
latter would let the campaign iterate without a human relaying every result.

## Unfinished business at handoff

- **The smoke run has not been repeated since the statistics fixes.** The last
  three-symbol run reported `sessions_observed` of 92 and an AAPL
  `median_opening_volume` of 16,680, both wrong. After the fix the expected
  figures are roughly 82 sessions and an opening volume in the millions.
  Re-run it and confirm before spending hours on 277 symbols. Output paths are
  write-once, so use a new filename.
- **An existing local dataset may make the pull unnecessary.** The author
  reports market data already collected at
  `C:\Users\NickSandoval\Nick OS\02_Projects\Trading`. It was never
  inventoried. Before pulling, establish four things: granularity (one-minute
  bars are required; daily is useless for a 15-minute opening range), whether
  extended-hours bars are included, symbol and date coverage, and provenance —
  which provider, adjusted or unadjusted, and whether retrieval dates and
  content hashes were recorded. Data without recorded provenance is usable for
  research but weakens the immutable-evidence property the Gauntlet path
  depends on, and the manifest must say so.
- **The candidate pool is unverified against the provider.** A few of its 277
  symbols may no longer resolve; the stats command logs and skips those rather
  than aborting.

## Next

1. Inventory the existing local dataset before pulling anything.
2. Breakout Quality Score, calibrated against real feature distributions rather
   than invented weights — which is why the data step precedes it.
3. Deterioration signal detection from bars.
4. Session runner covering both directions and the re-entry branch.
5. Freeze partitions, then run the campaign.
