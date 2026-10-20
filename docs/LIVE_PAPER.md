# Active paper launch

Nick requested active Jev adjudication and actual broker paper execution, with current market data. There is no shadow mode in this runner. The [canon](ATHENA_TRADING_CANON.md) now specifies the owner-approved Astra 6 + Jev target; this runbook describes the existing Jev-only runner on main `5708946`.

## What this existing slice runs

`python scripts/run_athena_paper.py` uses current Alpaca minute bars, the latest bid/ask on the same feed, existing ORB opening-range/risk functions, **TypeSafe Jev as the only decision model**, deterministic limits and Alpaca paper bracket orders. There is no Terra, OpenAI or Ollama dependency in the active path.

Strategy code supplies a candidate direction and computed observations. Jev selects TAKE_TRADE, NO_TRADE or INSUFFICIENT_EVIDENCE. A normal entry requires p(TAKE_TRADE) at least 0.6 and confidence at least 0.5. Override those floors with `ATHENA_JEV_MIN_PTAKE` and `ATHENA_JEV_MIN_CONFIDENCE`. They are paper operating thresholds, not measured trading calibration. Full questions, inputs, distributions, policy and broker observations are journaled. Arithmetic, sizing, stops and final authority stay in code.

Each option probability must be finite and in [0, 1], and every choice label must be present. When those values sum to within 0.02 of 1, the runner scales them to sum to exactly 1 before the gate and the fallback score. The receipt keeps the raw values and a `renormalized` flag. The raw Jev response is journaled before that check. A sum farther from 1, a negative, a non-finite value, or a missing label vetoes that one setup. The journal records `jev_veto` with the reason and `accepted` false. That veto is not an approval, does not halt a flat day, and does not remove other TAKE_TRADE labels from the daily fallback. A transport failure before any response still stops the process; it is not treated as a decision.

If the session still has no reserved order `ATHENA_FALLBACK_AFTER_OPEN_MINUTES` after the broker open (default 120), the runner looks again at the fresh completed bar. It submits the highest-scoring remembered TAKE_TRADE only when that bar is still the same signal and the same direction, and only after the same notional, stop, buying-power, account, learned-veto, and shared-book checks. The score is confidence times the renormalized p(TAKE_TRADE). An earlier label wins a tie. The journal records `fallback_entry` with reason `NO_ORDER_BY_CUTOFF`. A stale bar records `fallback_no_trade` and does not send the old order. A fresh bar in the other direction does not send the remembered order either; that new setup is adjudicated on its own, and `fallback_no_trade` records `DIRECTION_CHANGED` when the old order is dropped. NO_TRADE and INSUFFICIENT_EVIDENCE are not eligible. A normal entry skips the fallback. The review budget counts distinct opening-range setups. One setup may be reassessed once. Further equivalent bars do not spend another slot, so a later different direction can still be reviewed while a slot remains. The budget is not removed. A missing, malformed, or failed model response is still not an approval. A session that never receives a TAKE_TRADE label can still finish with no order.

Reconciled completed trades enter the experience store. Relevant prior experiences inform later Jev decisions; prospectively validated temporary abstention rules are enforced in code. [LEARNING.md](LEARNING.md) defines the exact feedback and nightly validation policy.

| Pilot policy | Initial bound |
|---|---|
| Account | Dedicated Alpaca paper account, initially flat; one runner/journal |
| Universe | One explicitly supplied US equity symbol; price at least $5, ATR at least $0.50 |
| Data | IEX by default; SIP only if subscribed, no silent feed substitution |
| Entry | One whole share; at most one entry per account/session via stable broker client ID |
| Exposure | At most $1,000 and 1% of account equity; buying power checked again before submission |
| Initial stop risk | At most $10 and 0.1% of account equity; stop orders do not guarantee execution price |
| Account daily loss | Stop new entries and flatten at 0.5% versus broker `last_equity` |
| Orders | DAY limit entry priced from the latest quote, broker-held stop-market, and the original 2R target; no market orders and no extended-hours orders |
| Entry timeout | Cancel/close after 60 seconds; partial entry triggers cleanup |
| Session close | Begin flattening 15 minutes before broker-reported close, including early closes |
| Model budget | At most 20 distinct opening-range setups per session, plus one reassessment of a setup already reviewed. Equivalent later bars do not consume another slot. No inference retries. Probability sums within 0.02 of 1 are renormalized. A bad answer vetoes that setup and is not an approval |
| Daily fallback | At the cutoff (default 120 minutes after the open, or `ATHENA_FALLBACK_AFTER_OPEN_MINUTES` from 1 to 300), submit the remembered TAKE_TRADE only if the fresh bar is still that setup. A stale bar or a direction change records `fallback_no_trade` and does not send the old order. Same one-share bracket and risk limits |
| Shared account | Refuse a new entry when the earnings book already reserves the symbol, when combined reserved notional exceeds buying power, or when the shared entry coordinator is busy/unavailable or peer exposure cannot be verified. The peer journal remains read-only and may keep its writer lock. Portfolio correlation, concentration, weekly and monthly loss, drawdown, and overnight stress stay inactive until a number is approved |
| Data age | Completed bar no more than 90 seconds old; rechecked after model calls. The entry quote must be no more than 15 seconds old at submission |

At submission the runner reads the latest Alpaca quote on the configured feed (`iex` or `sip`, the same feed as the bars). A buy limit is the ask plus `ENTRY_LIMIT_OFFSET` (one cent). A sell limit is the bid minus that offset. The offset cannot exceed two cents (`ENTRY_LIMIT_OFFSET_MAX`). The order stays a limit. A market order is not used. The stop stays at the signal `risk_stop`. The target stays the original 2R price from the signal close. Notional and initial stop-risk are checked again against the limit that would be sent, and those caps stay $1,000 / 1% and $10 / 0.1%.

The quote midpoint may not already be more than five cents (`MAX_ENTRY_SLIPPAGE`) worse than the signal close. The limit itself may not be more than seven cents worse (`MAX_LIMIT_CONCESSION`, the five-cent cap plus the two-cent offset bound). That is what makes the 15 October 2026 short marketable: close 765.21, bid 765.15, ask 765.18, sell limit 765.14. The midpoint is 4.5 cents under the close, and the limit is exactly seven cents under it. A wider move is skipped. A missing, stale, crossed, or otherwise invalid quote is skipped. The journal records `entry_skipped` with the reason, and, when a price was computed, the bid, ask, refused limit, and slippage versus the signal close. No order is sent and a flat account is not halted. A submitted entry records the same quote, the limit, `slippage_vs_close`, and `midpoint_vs_close` on `entry_intent`. The client order id is still the account and session hash. The 60-second entry timeout is unchanged. This pricing is not deployed.

This uses the ORB trigger as one intraday component. It is not the full historical ORB-M qualification/campaign, continuously updated predictive engine, Earnings Adaptive arm, full APDE ratchet, or complete general Athena Core integration. The initial bounded paper learning loop is implemented; its observed live results remain unverified. No profit or prediction-accuracy claim is made.

## Hybrid implementation and activation gate

The owner selected **Astra 6** to produce evidence-linked theses/scenarios/plans, with Jev judging original evidence plus the proposal and code owning numbers, validated forecasts, freshness, risk and execution. This capability is not in the current runner. Astra 6's exact provider deployment/API model ID, access and supported interface are unresolved; do not add guessed credentials/flags, substitute a model, or assume an existing chat model is a deployable API.

Before any future hybrid activation, complete the [build acceptance gates](BUILD_PLAN.md) and [contract](HYBRID_MODEL_CONTRACT.md): source and plan validation, expiry/invalidation, no new exposure on either model's failure, bounded event-driven updates, shared portfolio risk and deterministic APDE transitions. Critical protection, mandatory exits and recovery must not wait for either model. The current daily fallback is not a hybrid acceptance rule; it cannot bypass missing/expired proposals or Jev vetoes. No risk limit or order policy changes in this docs PR.

[PR #29](https://github.com/nrsandoval1231-oss/athena-trading/pull/29) remains unmerged. Its `docs/EARNINGS_PAPER.md` is the earnings book's runbook, not a launch procedure for the intraday service and not an Astra 6 deployment. Nick confirmed no overnight positions on 13 October 2026, superseding its earlier multi-day proposal. Both arms begin closing before the actual regular-session close. Do not launch a second book from this runbook; unresolved exposure during a halt or outage is not a confirmed flat account.

## Configure the existing runner

Use a Linux host with persistent storage and Python 3.11+. [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md) is the installation and continuous-operation procedure. The active host example is a Hostinger VPS. A DigitalOcean Droplet remains an optional path with the same `install.sh`, systemd unit, and `paper.env` pattern. Configure only these server-side environment variables through the host's secret settings:

- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`
- `TYPESAFE_API_KEY`

Optional paper policy, also read by the runner when unset values are left blank. These are not secrets:

- `ATHENA_JEV_MIN_PTAKE` (default `0.6`)
- `ATHENA_JEV_MIN_CONFIDENCE` (default `0.5`)
- `ATHENA_FALLBACK_AFTER_OPEN_MINUTES` (default `120`)

Use paper-account keys. The order endpoint is fixed to `https://paper-api.alpaca.markets`; there is no real-money endpoint switch. Do not put keys in Git, command arguments, reports, or chat. No keys are required for the test suite.

Choose an existing persistent writable state directory. Use the same journal path on restart. One journal represents one pilot session. The cloud supervisor creates dated journals, retains earlier records, and prioritizes unresolved entries before a new day. For a manual run, use the same journal through broker-confirmed completion and select a new dated path for a later session. The broker client ID includes account/session identity to prevent duplicate same-day submissions even if a new local journal is accidentally used. Do not delete state to bypass a halt. Manual orders that use neither `athp-` nor `athe-` remain outside the ownership contract.

The intraday runner can share the paper account with the earnings book. It ignores an order whose client id starts with `athe-`, and a position whose symbol is on that order or listed in `/var/lib/athena-earnings/ownership.json`. It does not ignore its own symbol. Any other symbol still blocks startup and cleanup. See [EARNINGS_PAPER.md](EARNINGS_PAPER.md).

The following example uses AAPL only as a command example; the operator supplies the intended symbol:

```bash
python -m pip install .
python scripts/run_athena_paper.py --symbol AAPL --authority PAPER --feed iex --journal /var/lib/athena/paper.sqlite --preflight
python scripts/run_athena_paper.py --symbol AAPL --authority PAPER --feed iex --journal /var/lib/athena/paper.sqlite --run
```

Preflight authenticates the broker and TypeSafe's read-only model-list endpoint without an inference or order. It is a connectivity check; current bar availability, asset eligibility, account state, and risk are checked in the active run. `--run` waits for the market and a qualifying fresh signal, then makes actual Jev calls and actual **paper** broker submissions. A session with no TAKE_TRADE label can still finish with no order. Keep the first session attended.

## Opt-in high-frequency paper profile

`ATHENA_PAPER_PROFILE=high-frequency` (or `--profile high-frequency`) selects a separate paper loop. The default one-trade ORB pilot, its Jev floors, and the earnings book stay as they are when the variable is unset or `pilot`. Do not run both profiles on the same account. A journal that already bound `high-frequency` keeps that profile on recovery, including when the environment is later flipped back.

The profile trades a short-horizon momentum re-entry on liquid names. The default universe is SPY, QQQ, IWM, DIA, AAPL, MSFT, NVDA, and AMZN (`ATHENA_HF_SYMBOLS`, 4 to 12 uppercase names). It may hold 4 positions at once (`ATHENA_HF_MAX_CONCURRENT`, 1 to 4). Each name waits 25 minutes after a filled round trip, holds at most 8 minutes, and does not enter during the first 10 minutes or the last 30 minutes. Flatten still starts 15 minutes before the broker close, including an early close. The session stops new entries at 140 round trips. The operating point of the offline session is about 100, not a quota the broker is required to fill.

Jev is not on this entry path. `ATHENA_HF_JEV=off` (the default) does not call it. `advisory` writes `hf_jev_bypassed` and still does not call it. `TYPESAFE_API_KEY` and `ATHENA_SYMBOL` are not required for this profile. A TypeSafe outage cannot reduce the trade count, and it also cannot approve an entry.

Entries stay one-share day limit brackets. The limit is still the ask plus one cent, or the bid minus one cent. This profile allows the midpoint to be up to 15 cents worse than the signal close and the limit up to 17 cents worse than it. A wider quote is skipped. A market order is not used for entry. The bracket is submitted with the stop and target a full stop-cap away from that limit, so a better fill cannot print through the stop. When the fill price is known, the runner replaces both legs: the stop and target sit at the planned risk around the actual fill, at least eight cents, and never on the wrong side of it. Until that replace lands, the wide stop is the protection. Exits may delete the position, the same way the existing flatten path does. Per-position ceilings stay $1,000 and 1% of equity, and $10 and 0.1% of equity for the stop. Gross reserved notional stays at $4,000. The daily loss halt stays 0.5% versus broker `last_equity`. New entries also stop when the paper call budget reaches 150 requests in a rolling minute, or for 30 seconds after HTTP 429.

A working order is unowned only when it is not an earnings order, not a current symbol, and not a parent or leg of an order this profile sent. A pending-cancel sibling of a just-closed bracket stays in that lineage. If the only suspicious orders are on a symbol closed in the last 15 seconds, the runner reads the book once more before `UNOWNED_EXPOSURE`. An order that is still there, or an order on any other symbol, still halts.

Startup and every tick refuse a broker whose endpoint is not exactly `https://paper-api.alpaca.markets`. `parse_authority("LIVE")` still raises. An HTTP 403 whose body matches a pattern-day-trading phrase records `hf_pdt_restricted`, stops new entries, and keeps managing and flattening what is already open. The response body is not logged. A paper account under $25,000 can hit that restriction after a few day trades. A name whose one share exceeds the notional cap is skipped. Earnings symbols and `athe-` orders are left alone.

High-frequency events use `hf_*` kinds. They are not the one-trade study's `entry_intent` rows, so nightly learning does not ingest them. Client order ids still start with `athp-` and are unique per sequence. `ATHENA_SHARED_RISK_DIR` must be an absolute path. The shared book sees `hf_entry_intent` until `hf_position_closed` or `hf_session_flat`.

```bash
python scripts/run_athena_paper.py --profile high-frequency --authority PAPER --feed iex \
  --journal /var/lib/athena/hf-preflight.sqlite --preflight
```

On the VPS, set `ATHENA_PAPER_PROFILE=high-frequency` in `/etc/athena/paper.env` and leave the one-trade service as the only `athena-paper.service`. Do not enable it while the default pilot is also submitting on that account. This profile is not deployed by this change.

## Recovery and exits

The journal reserves a client order ID before submission. A lost acknowledgement never causes a blind resend; recovery queries the broker by that ID. NEW and accepted orders are not fills. Confirmed positions and open orders drive cleanup; cancel/close acknowledgements are not sufficient to declare flat.

A missing opening-range minute or a completed bar older than 90 seconds waits in the session (`OPENING_RANGE_INCOMPLETE` or `STALE_BAR`). A TypeSafe or startup error with no reserved order and a flat account exits without a halt, and the supervisor starts that same journal again. A halt is still written when an order id is reserved, a position or open order is visible, or the daily-loss cap hits. Operator stop still halts.

If the reserved id was never acknowledged and Alpaca answers HTTP 404 while the account is flat, the runner records `order_lookup_miss` and looks once more. A second flat 404 records `entry_not_created` with reason `BROKER_ORDER_NOT_FOUND` and ends recovery. The id is not submitted again. A 404 while a position or open order remains, or a 404 after an `entry_ack`, stays unresolved and is still not resent.

Restart the same `--run` command with the same journal and Alpaca credentials to recover. An existing reserved entry does not require Jev, the TypeSafe key, or healthy learning state to recover and exit. The CLI reconstructs its complete saved policy binding, including legacy model labels, without restoring old model code. Ctrl-C/SIGTERM latches a halt and attempts cancellation and position closure. The runner observes flat positions, no working orders, and a terminal entry twice before recording `flat_confirmed`. Broker observations retain fill quantities/prices; no simulated P&L is used.

If cleanup cannot finish, it prints `UNRESOLVED_PAPER_EXPOSURE` and exits nonzero. Inspect the paper account and close remaining exposure. A host crash, broker outage, trading halt, or rejected close can prevent flatness; a bracket does not guarantee pre-close liquidation. Model failures never authorize new exposure and never serve as a prerequisite for recovery.

## Verification and launch status

**Current summary, 13 October 2026.** The [held-bracket incident](IMPLEMENTATION_STATUS.md#held-bracket-stop--13-october-2026) records a filled bracket followed by protective flatten. Earlier installation/waiting/no-fill descriptions below are dated history. This documentation review does not establish the host's present health, deployed SHA or exposure, and does not demonstrate performance improvement. Hybrid entry/management and full APDE/Core learning remain incomplete.


The Jev-only/learning revision of 24 September 2026 passed **657 root tests and 158 Astra tests**, with repo-wide Ruff 0.15.8 clean. Tests exercise typed decisions, malformed responses, rechecked risk, broker recovery, checkpoint corruption, point-in-time experience, prospective validation, expiry and learned vetoes. See the immutable revision evidence in [implementation status](IMPLEMENTATION_STATUS.md).

**Status correction, 10 October 2026.** Astra Desk is no longer in this repository. The audit of main `d36164ef7843d135dbfeea3684be598566abcf33` is [paper-readiness](audits/2026-10-10-paper-readiness.md). GitHub Actions on that push succeeded (run `38073284465`). The recorded root gate on the lab revision is 672 passed; Astra's suite runs in its own repository. A missing opening minute, a stale bar, or a flat startup error no longer finishes the day by itself. A reserved id Alpaca never created is closed out as `BROKER_ORDER_NOT_FOUND` and is not resent. Read the recovery section above before the first attended session.

**Hostinger paper pilot, 10 October 2026.** Nick installed this runner on a Hostinger VPS. `athena-paper.service` is enabled. The host `paper.env` holds the Alpaca paper and TypeSafe keys; those values are not in Git. The service was last observed in `WAITING_FOR_MARKET` for Monday open. Pre-Monday fixture, preflight, and health checks passed that day. That is install and connectivity evidence, not a fill. **LIVE remains rejected.** Test results and the fixture lab are not observed broker or semantic-accuracy results. The old synthetic demo command now supports fixtures and broker preflight only; it cannot submit an Alpaca demo order. Use this active runner for broker paper operation.

**Paper gate, 12 October 2026.** New sessions use p(TAKE_TRADE) >= 0.6 and confidence >= 0.5, overridable with the environment variables above. The once-per-day fallback is journaled and stays inside the same bracket, risk checks, one-position cap, and PAPER authority. `parse_authority("LIVE")` still raises. A journal that already bound a policy keeps that policy's floors; a missing fallback field resumes at 120 minutes. There is no market-data or adjudication fixture for the 12 October 2026 SPY session in this repository, so that session was not replayed. The reported best label from that day, confidence 0.61 and p(TAKE_TRADE) 0.74, clears the new default gate and would be a normal entry. The previous 0.8 / 0.6 gate rejects it. That comparison is not a broker fill.

**Probability mass, 12 October 2026.** A Jev distribution that sums to within 0.02 of 1 is scaled to exactly 1 before the gate. The raw values and a renormalized flag stay in the receipt, and the raw response is journaled first. Sums farther away, negatives, non-finite values, and missing labels veto that setup with a reason. The runner stays up. The veto is not an approval and does not block the daily fallback from using other TAKE_TRADE labels. **LIVE remains rejected.**

## Verified API references

Primary documentation checked 24 September 2026:

- [TypeSafe HTTP API](https://docs.typesafe.ai/api), [models](https://docs.typesafe.ai/models), [confidence](https://docs.typesafe.ai/confidence), and [Jev limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13).
- [Alpaca order lifecycle and bracket rules](https://docs.alpaca.markets/us/docs/orders-at-alpaca), [current/historical stock bars](https://docs.alpaca.markets/us/reference/stockbars), [latest stock quote](https://docs.alpaca.markets/us/reference/stocklatestquotesingle-1), [client-order-ID lookup](https://docs.alpaca.markets/us/reference/getorderbyclientorderid), and [position closure](https://docs.alpaca.markets/us/reference/deleteopenposition-1).

Jev's documented weaknesses include numeric/date operations and adversarial content. Arithmetic, time comparisons, and execution stay in code. Instruction wording is not a proof of resistance to malicious evidence; the initial pilot sends code-derived market observations, not scraped instructions or arbitrary news text. Actual adjudication quality remains to be measured during paper operation.
