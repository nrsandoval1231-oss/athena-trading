# Athena ORB-Momentum Strategy v1.0

Status: **RECONSTRUCTED BASELINE — freeze before empirical tuning**

This document reconstructs the intended aggressive opening-momentum strategy from prior Athena Trading research. Values marked as reconstructed are hypotheses to validate prospectively, not claims of proven edge.

## Objective

Capture the high-velocity portion of an opening breakout, deliberately realize part of that edge early, retain a runner for exceptional trends, and permit a separately risked continuation re-entry after a controlled retracement.

## 1. Opening range

- U.S. regular session.
- Opening range: first 15 minutes, 09:30–09:45 ET.
- Freeze Opening Range High (ORH) and Opening Range Low (ORL) at 09:45.
- ORH/ORL do not move after the range closes.

## 2. Setup qualification

A crossing of ORH/ORL is not sufficient. Compute a Breakout Quality / confidence assessment from only information available at decision time, including:

- breakout structure
- relative/expanding volume
- price momentum/acceleration
- VWAP relationship
- gap and premarket context
- market alignment
- sector alignment
- volatility quality
- extension risk
- false-breakout risk

Reconstructed action bands:

- confidence < 0.55: PASS
- 0.55–0.70: ENTER_REDUCED
- 0.70–0.85: ENTER_STANDARD
- > 0.85: ENTER_HIGH_CONVICTION

These thresholds are frozen starting hypotheses until a registered experiment changes them.

## 3. Entry and initial risk

- Long: qualified breakout above ORH.
- Short: qualified breakdown below ORL.
- Every entry requires a deterministic invalidation/stop before exposure is accepted.
- Initial risk derives from breakout structure and volatility/ATR rather than an unconditional percentage stop.
- Express downstream performance and management in R multiples.
- Position size is risk-budget-first; confidence may modify exposure only inside deterministic hard limits.

## 4. Adaptive Position Defense Engine

Canonical post-entry states:

`EXPLORE → PROTECT → HARVEST → DEFEND → EXIT`

This is consistent with the historical Athena APDE contract. Risk authority remains deterministic. An LLM/Athena policy may recommend actions but cannot override hard risk constraints.

## 5. Early momentum capture

The first approximately 8 minutes after a qualified breakout are treated as an opening-impulse observation/harvest window.

Track at minimum:

- MFE and MAE
- acceleration/deceleration
- volume expansion/collapse
- VWAP separation/reclaim/loss
- distance from breakout level
- new-high/new-low persistence
- market and sector confirmation/divergence

Reconstructed management behavior:

- around +0.5R: transition toward PROTECT and reduce allowable loss when structure supports it.
- around +1.0R: first partial harvest, initially 20–30% of position.
- unusually strong early velocity may trigger another 20–30% momentum harvest during the opening impulse.

The purpose is to monetize the statistically valuable impulse rather than require the full position to survive a later retracement.

## 6. Runner and dynamic staircase trail

The remaining position becomes the runner. It must not use a naive fixed-percent trailing stop.

Conceptual staircase:

1. RISK — original structural invalidation.
2. PROTECT — after meaningful favorable excursion, reduce open risk.
3. FREE TRADE — near ~+1R, move protection toward breakeven or valid supporting structure.
4. PROFIT LOCK — after stronger excursion, lock a meaningful fraction of profit.
5. TREND RIDE — use structure, volatility/ATR and VWAP/trend context to ratchet the trail.

The protective boundary may ratchet toward profit but never loosen simply to avoid an exit.

## 7. Peak-profit retention

Historical research direction: target roughly **65% retention of peak favorable R** once a trade is sufficiently profitable.

Example: peak MFE of +3R implies a conceptual retained-profit objective near +1.95R.

This is not a tick-by-tick fixed 65% trailing stop. It is an input to the dynamic staircase/structure-aware exit policy and must allow ordinary market noise.

## 8. Deterioration / exit pressure

Exit pressure may increase from independent evidence such as:

- failed continuation/new high or low
- adverse lower-high/higher-low structure
- VWAP loss/reclaim against the trade
- momentum deceleration
- volume collapse
- sector divergence
- broad-market reversal
- abnormal adverse bar
- volatility/regime shift
- failed breakout retest

Multiple independent deterioration signals should matter more than one noisy indicator.

## 9. Retracement re-entry

A harvested breakout may create a second, independent continuation opportunity.

Sequence:

`IMPULSE → HARVEST → CONTROLLED RETRACEMENT → SUPPORT/REQUALIFICATION → MOMENTUM RE-EXPANSION → NEW ENTRY`

Historical reconstructed geometry:

- very high-confidence impulses may accept shallow ~15–20% retracements;
- lower-confidence setups require deeper/cleaner reset and stronger requalification.

Every re-entry is a new Decision Episode/trade with a new risk budget. It is not an extension of the prior position.

## 10. Hard boundaries

- no overnight position for this strategy
- no look-ahead data
- deterministic risk veto
- explicit transaction costs/slippage in evaluation
- no parameter changes based on sealed holdout results
- baseline parameters versioned and immutable within a registered experiment

## 11. Athena adaptation surface

Athena may eventually propose/learn bounded changes to:

- breakout-quality weighting/thresholds
- confidence calibration
- bounded size multiplier
- harvest timing and fraction
- re-entry qualification
- trail aggressiveness
- deterioration weighting
- regime-specific behavior
- setup abandonment

Athena may not mutate hard capital/risk authority inside the same learning loop.

## 12. Primary research question

Given the same strong deterministic ORB constitution and repeated unseen market episodes, can Athena produce a prospectively validated improvement in risk-adjusted realized value without violating hard risk constraints or overfitting protected evidence?
