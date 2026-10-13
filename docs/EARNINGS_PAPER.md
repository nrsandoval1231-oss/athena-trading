# Earnings Adaptive paper book

PAPER only. `parse_authority("LIVE")` raises `AuthorityError`. The order endpoint stays `https://paper-api.alpaca.markets`. This arm does not grant real-money authority, and it does not rerun the frozen earnings-universe study.

The screener in `research/earnings-universe/universe_screener.py` ranks about 75 names that historically move 4–12% from the close before a report to the close after it. This book does not import that script. It trades a name from the operator's copy of `universe_75.txt` only when the live gap, on the first regular session after a known report, lands inside that same band.

## Rule set

| Rule | Value |
|---|---|
| Direction | Sign of the gap from the prior regular close to the latest completed minute inside the entry window |
| Gap band | 4% to 12% absolute, inclusive. Outside the band is no trade. The band is not an environment setting |
| Entry window | 5 to 30 minutes after the regular open, on the first session the report is public |
| Before the open (BMO) | Entry session is the report date |
| After the close (AMC) | Entry session is the next regular session |
| Unknown report time | Skipped. The book does not guess |
| Order | GTC limit bracket, whole shares, no extended hours. Stop is halfway back toward the prior close. Target is 2R |
| Max hold | 3 regular sessions, including the entry day. Exit starts 15 minutes before that session's close. A later session flattens at the next open |
| Unfilled entry | Canceled when the entry window ends. The client order id is not reused |
| Partial fill or missing stop | Close that symbol. Jev is not called |
| Quantity the book does not recognize | `POSITION_MISMATCH`. No market close |
| New entries | At most 1 per day and 3 open names, unless the environment sets a tighter cap |
| Jev | Same floors as the intraday arm: p(TAKE_TRADE) >= 0.6 and confidence >= 0.5. A veto or a bad answer skips that report. It is not an approval |
| Model outage | No new order. Open brackets stay. A transport failure is not a flatten |

APDE labels on the book are `EXPLORE` while the bracket is working and `EXIT` when the book starts to close it. PROTECT, HARVEST, and DEFEND are not driven yet.

## Size

Default paper caps, which the environment may tighten but not raise past the ceiling:

| Cap | Default | Ceiling |
|---|---|---|
| Risk per trade | $50 | $100 |
| Open stop risk | $150 | $300 |
| Notional per trade | $2,000, and 2% of equity | $5,000 |
| Open notional | $6,000 | $10,000 |
| Open positions | 3 | 5 |
| Hold | 3 sessions | 5 |

Buying power is checked again before the order is sent. One share that would exceed the risk or notional cap is skipped. Sizing is not fractional Kelly.

A 0.5% drop versus broker `last_equity` stops new entries. It does not flatten brackets that already have a stop.

## Calendar

Free source: `https://api.nasdaq.com/api/calendar/earnings?date=YYYY-MM-DD`. No API key. The request sends a browser User-Agent because the host often refuses a bare client.

Limits:

- The endpoint is undocumented and has no availability promise. A failed or unusable payload blocks new entries. It does not invent a calendar.
- One call is one calendar day. The book reads today and the previous regular session.
- `time-pre-market` is before the open. `time-after-hours` is after the close. `time-not-supplied` and any other token are skipped.
- Rows do not include a surprise, guidance, options skew, or the historical reaction. EPS text is ignored.
- The list can change after publication. This book does not detect revisions.
- Symbols are normalized to Alpaca form (`BRK/B` and `BRK-B` become `BRK.B`).

Session dates come from Alpaca's paper calendar, not from Nasdaq.

## Universe

`ATHENA_EARNINGS_UNIVERSE` is a text file, one ticker per line, at most 75 names. Produce it with the research screener and copy `universe_75.txt`. The book ranks candidates in file order and takes the first name that clears the gap, the asset check, and Jev. It will not enter `ATHENA_SYMBOL`, a symbol that already has an `athp-` order, or a symbol that already has a position or order the book did not reserve.

## State, restarts, and the other arm

Client order ids start with `athe-` and are a hash of account, symbol, and report date. The id is journaled before the POST. A lost response looks the id up. It is not sent again. Two flat HTTP 404s, with no acknowledgement and no exposure in that symbol, record `entry_not_created`.

The book file is `/var/lib/athena-earnings/book.sqlite`. It is not a dated intraday journal. A restart loads the same file, republishes `ownership.json`, and reconciles open ids before it looks for a new entry. A partial take-profit leaves the unsold shares under management: the position must match entry fills minus opposite-side fills. A quantity those legs cannot explain still halts. A close Alpaca created under its own client order id is waited on when that id is already in `close_ack`. It is not canceled and it is not sent again. SIGTERM stops the process and leaves GTC brackets at the broker. It does not flatten. `--flatten-owned` closes earnings symbols only.

Intraday orders start with `athp-`. The intraday runner's flat check and `flatten_step` ignore `athe-` orders and symbols listed in the earnings ledger. They still flatten `athp-` exposure, and they still refuse a symbol that belongs to neither arm. The earnings book will not cancel an `athp-` order.

`athena-paper.service` reads `ATHENA_EARNINGS_STATE_DIR=/var/lib/athena-earnings`. `athena-earnings.service` uses `/etc/athena/earnings.env`. It writes its book under `/var/lib/athena-earnings` and appends closed trades under `/var/lib/athena/learning`. It does not write the intraday session journals. Enabling the earnings unit does not restart the intraday unit. This repository does not enable either unit.

Closed earnings trades are copied into the shared learning store at `ATHENA_STATE_DIR/learning` (default `/var/lib/athena/learning`) with `arm=earnings`. They keep fill quantity, gross P&L when the entry and exit cashflows balance, the decision receipt, and source hashes. `eligible` is false. The intraday discovery, validation, promotion, and context retrieval ignore `arm=earnings`, so these fills do not train or skew that study. A locked store is retried; it does not block a protective exit.

Before the open, and again until the entry window ends, the book calls Alpaca `GET /v1beta1/news` with the paper keys for that day's candidates, starting at the prior regular close. A successful snapshot is kept for fifteen minutes and then refreshed; each snapshot records the time it was available. Headlines and summaries are journaled as `news_context` and, when a name is adjudicated, as `news_fed` inside the Jev packet. A headline after the prior close and no later than the regular open that reads like a report marks `news=confirmed`. That flag does not authorize the trade. The Nasdaq calendar remains the schedule. Rate limits use the same GET retry as other broker reads. If news is down or unusable, the book journals `news_unavailable` and continues with `news=unavailable`.

The entry window and the age of the minute bar are checked again after Jev returns, immediately before the order is reserved. A decision that finishes after minute 30, or on a stale bar, does not submit.

A 0.5% daily loss blocks new entries for that broker session only. The event stays in the journal. The next session evaluates equity again. An operator flatten halt stays in force until the book is replaced.

On restart, risk and notional caps may be tightened. A looser cap, or a change to the feed, universe, or any other setting, refuses startup. Existing reservations keep the order that was already journaled. The tightened caps are stored as `policy_tightened` and apply to later entries.

## Run

Do not put keys in git. Copy `deploy/earnings.env.example` to `/etc/athena/earnings.env` (mode 0600). The example file has empty key fields.

```bash
python scripts/run_athena_earnings.py --authority PAPER --journal /var/lib/athena-earnings/book.sqlite --preflight
python scripts/run_athena_earnings.py --authority PAPER --journal /var/lib/athena-earnings/book.sqlite --run
```

`--preflight` checks the broker, the TypeSafe model list, and the universe file. It does not submit. The cloud supervisor is `python -m athena_trading.paper.earnings_cloud`. `deploy/install.sh` installs `athena-earnings.service` and leaves it stopped.

A passed test suite is not a broker fill and not evidence of trading performance.
