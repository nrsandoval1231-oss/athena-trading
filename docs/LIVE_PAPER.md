# Active paper launch

Nick requested active Jev adjudication and actual broker paper execution, with current market data. There is no shadow mode in this runner. Product authority remains [the canon](ATHENA_TRADING_CANON.md), including its Jev amendment.

## What this first slice runs

`python scripts/run_athena_paper.py` uses Alpaca's current minute bars, the existing ORB opening-range/risk functions, hosted OpenAI `gpt-5.6-terra`, TypeSafe `jev-1.13.0`, deterministic limits, and Alpaca paper bracket orders. A veto stops the order. The runner never substitutes fixture data or a simulated fill for broker state.

The Actor selects the observed breakout direction or NO_TRADE and explains its rationale/invalidation. Jev evaluates rationale support and direction consistency. Both must select `supported`, with support probability at least 0.8 and confidence at least 0.6. These are initial paper operating thresholds, **not measured trading calibration**. Full distributions, model identity, questions, policy, inputs, proposals, responses, and broker observations are journaled.

| Pilot policy | Initial bound |
|---|---|
| Account | Dedicated Alpaca paper account, initially flat; one runner/journal |
| Universe | One explicitly supplied US equity symbol; price at least $5, ATR at least $0.50 |
| Data | IEX by default; SIP only if subscribed, no silent feed substitution |
| Entry | One whole share; at most one entry per account/session via stable broker client ID |
| Exposure | At most $1,000 and 1% of account equity; buying power checked again before submission |
| Initial stop risk | At most $10 and 0.1% of account equity; stop orders do not guarantee execution price |
| Account daily loss | Stop new entries and flatten at 0.5% versus broker `last_equity` |
| Orders | DAY limit entry with broker-held stop-market and 2R target bracket; no extended-hours orders |
| Entry timeout | Cancel/close after 60 seconds; partial entry triggers cleanup |
| Session close | Begin flattening 15 minutes before broker-reported close, including early closes |
| Model budget | At most 20 candidate reviews per session; no inference retries or fallback approval |
| Data age | Completed bar no more than 90 seconds old; rechecked after model calls |

This uses the ORB trigger as one intraday component. It is not the full historical ORB-M qualification/campaign, continuously updated predictive engine, Earnings Adaptive arm, full APDE ratchet, or Athena learning loop. The context explicitly contains no promoted lessons. No profit or prediction-accuracy claim is made.

## Configure the runner

Use a Linux host with persistent storage and Python 3.11+. [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md) is the DigitalOcean installation and continuous-operation procedure. Hosted Terra is the default Actor; no local model or GPU is needed. Requests use strict structured output, a 25-second timeout, no retries, and `store: false`. The journal records the reported model, request configuration hash, request/response hashes and usage. The configuration hash does not identify hosted model weights. The optional `--actor ollama` path retains `qwen2.5:7b` for existing local use, with its installed digest checked.

Configure these **server-side environment variables** through the host's secret settings:

- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`
- `TYPESAFE_API_KEY`
- `OPENAI_API_KEY` (hosted Actor)

Use paper-account keys. The order endpoint is fixed to `https://paper-api.alpaca.markets`; there is no real-money endpoint switch. Do not put keys in Git, command arguments, reports, or chat. No keys are required for the test suite.

Choose an existing persistent writable state directory. Use the same journal path on restart. One journal represents one pilot session. The cloud supervisor creates dated journals, retains earlier records, and prioritizes unresolved entries before a new day. For a manual run, use the same journal through broker-confirmed completion and select a new dated path for a later session. The broker client ID includes account/session identity to prevent duplicate same-day submissions even if a new local journal is accidentally used. Do not delete state to bypass a halt. Keep the account dedicated to this runner; manual/concurrent orders are outside its ownership contract.

The following example uses AAPL only as a command example; the operator supplies the intended symbol:

```bash
python -m pip install .
python scripts/run_athena_paper.py --symbol AAPL --authority PAPER --feed iex --journal /var/lib/athena/paper.sqlite --preflight
python scripts/run_athena_paper.py --symbol AAPL --authority PAPER --feed iex --journal /var/lib/athena/paper.sqlite --run
```

Preflight authenticates the broker, the configured Actor model, and TypeSafe's read-only model-list endpoint without an inference or order. It is a connectivity check; current bar availability, asset eligibility, account state, and risk are checked in the active run. `--run` waits for the market and a qualifying fresh signal, then makes actual Jev calls and actual **paper** broker submissions. A signal or accepted proposal is not guaranteed; a legitimate session may produce no trade. Keep the first session attended.

## Recovery and exits

The journal reserves a client order ID before submission. A lost acknowledgement never causes a blind resend; recovery queries the broker by that ID. If the broker cannot resolve it, the runner stays halted and reports the uncertainty. NEW and accepted orders are not fills. Confirmed positions and open orders drive cleanup; cancel/close acknowledgements are not sufficient to declare flat.

Restart the same `--run` command with the same journal and Alpaca credentials to recover. An existing reserved entry does not require either model or the TypeSafe key to recover and exit. Ctrl-C/SIGTERM latches a halt and attempts cancellation and position closure. The runner observes flat positions, no working orders, and a terminal entry twice before recording `flat_confirmed`. Broker observations retain fill quantities/prices; no simulated P&L is used.

If cleanup cannot finish, it prints `UNRESOLVED_PAPER_EXPOSURE` and exits nonzero. Inspect the paper account and close remaining exposure. A host crash, broker outage, trading halt, or rejected close can prevent flatness; a bracket does not guarantee pre-close liquidation. Model failures never authorize new exposure and never serve as a prerequisite for recovery.

## Verification and launch status

On 24 September 2026, the first integration passed **46 new deterministic tests** and **620 main-package tests total**, at commit `9d83069e9ae53b01aee688aa68960eb8cc89ace6`. The cloud extension adds **28 tests**, bringing the root total to **648**, with Ruff 0.15.8 clean for changed code. The tests cover active Jev vetoes, malformed distributions/identities, Actor abstention, risk/freshness changes during inference, submission timeouts/recovery, partial fills, rejects, cancellation acknowledgements, model-independent cleanup, journal corruption/locking, and session-specific order IDs. They are transport fixtures, not observed broker/provider execution or semantic-accuracy evaluation.

The local launch preflight exited 2 with missing Alpaca credentials and zero orders submitted. TypeSafe credentials were also absent from the workspace and the inspected connected credential catalog. The cloud target is now a DigitalOcean Droplet, with hosted Terra and a systemd supervisor prepared. The console is unavailable in this browser and server/provider credentials remain absent. **Not launched.** Follow [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md) to install, preflight and activate the service. The old repository lint/Astra failures remain separately recorded in [implementation status](IMPLEMENTATION_STATUS.md).

## Verified API references

Primary documentation checked 24 September 2026:

- [TypeSafe HTTP API](https://docs.typesafe.ai/api), [models](https://docs.typesafe.ai/models), [confidence](https://docs.typesafe.ai/confidence), and [Jev limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13).
- [Alpaca order lifecycle and bracket rules](https://docs.alpaca.markets/us/docs/orders-at-alpaca), [current/historical stock bars](https://docs.alpaca.markets/us/reference/stockbars), [client-order-ID lookup](https://docs.alpaca.markets/us/reference/getorderbyclientorderid), and [position closure](https://docs.alpaca.markets/us/reference/deleteopenposition-1).

Jev's documented weaknesses include numeric/date operations and adversarial content. Arithmetic, time comparisons, and execution stay in code. Instruction wording is not a proof of resistance to malicious evidence; the initial pilot sends code-derived market observations, not scraped instructions or arbitrary news text. Actual adjudication quality remains to be measured during paper operation.
