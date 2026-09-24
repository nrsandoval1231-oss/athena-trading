# Continuous cloud paper operation

Deploy the existing active paper runner on one DigitalOcean Droplet. The service stays online across market sessions and reboots; entries remain restricted to the broker's market session and the bounded policy in [LIVE_PAPER.md](LIVE_PAPER.md). This deployment does not complete the Earnings arm, full APDE, or overnight learning.

## Selected setup

| Component | Choice |
|---|---|
| Host | DigitalOcean Basic, Regular CPU, 1 vCPU, 2 GiB RAM, 50 GiB disk; $12/month base cap |
| OS and location | Ubuntu 24.04 LTS, available US East region |
| Decision Actor | OpenAI `gpt-5.6-terra` through the Responses API, low reasoning, strict structured output |
| Adjudicator | TypeSafe `jev-1.13.0`; required active acceptance before risk can authorize an entry |
| Broker | Dedicated Alpaca paper account; IEX unless SIP is explicitly subscribed/configured |
| Runtime | One systemd service, one session child; no local model or GPU |
| Persistent state | `/var/lib/athena/YYYY-MM-DD.sqlite`, retained across restarts and days |
| Secrets | Root-owned `/etc/athena/paper.env`, mode 0600; injected by systemd |
| Access | SSH key; inbound SSH restricted to the operator; no public application port required |

The $12 is the server charge, not an all-in trading budget. API usage, optional backups, taxes, and any paid market-data entitlement are separate. Each session permits at most 20 candidate reviews, each with at most one Actor and one Jev call. The Actor caps input at 24 KB and requested output at 1,024 tokens; failed inference is not retried automatically. These are usage bounds, not a guaranteed dollar ceiling. Set provider spending controls for the intended account budget.

A Droplet fits the existing SQLite runtime. DigitalOcean App Platform has ephemeral local filesystems and does not support persistent volumes; using it would require moving state to another service. Begin with one host and measure memory/disk growth before increasing resources. This is an initial sizing choice, not a load-test result or uptime guarantee.

## Install a reviewed revision

Create the configured Droplet in the owner's account or use an existing dedicated host. Authorize the displayed recurring charge before creation. Do not place credentials in cloud-init, the repository, command arguments, or chat. Use the owner's existing SSH access. Enable host monitoring and configure backups under the chosen budget.

Clone `nrsandoval1231-oss/athena-trading` into `/opt/athena-trading` using the owner's authorized repository access. Select the full reviewed commit SHA from PR #18 or its eventual merge. As root on that host:

```bash
athena_revision=FULL_REVIEWED_40_CHARACTER_COMMIT_SHA
git -C /opt/athena-trading checkout --detach "$athena_revision"
bash /opt/athena-trading/deploy/install.sh "$athena_revision"
sudoedit /etc/athena/paper.env
```

The installer rejects a dirty or mismatched checkout and an already active service. It installs the package and service definition, creates a restricted runtime user and state directory, and leaves the service stopped. Populate all four provider credentials and the explicitly chosen `ATHENA_SYMBOL` in the environment file. No symbol or secret is supplied by the installer.

The default Actor is hosted Terra. The old local Ollama Actor remains available for explicit local runs and for recognizing old journal bindings; it is not installed or called by this cloud setup.

## Preflight and activate

Run the no-order preflight under the service identity, with systemd loading the environment file:

```bash
systemd-run --wait --pipe --collect --unit=athena-paper-preflight \
  --property=User=athena \
  --property=WorkingDirectory=/opt/athena-trading \
  --property=EnvironmentFile=/etc/athena/paper.env \
  /bin/sh -c 'exec /opt/athena-trading/.venv/bin/python scripts/run_athena_paper.py --symbol "$ATHENA_SYMBOL" --feed "$ATHENA_FEED" --actor openai --authority PAPER --journal /var/lib/athena/preflight.sqlite --preflight'
```

Require `ok: true`, the intended model/symbol/feed, and a flat dedicated paper account. This verifies authentication/model availability and account reads; it does not perform inference or submit an order. Market-data freshness, asset eligibility and risk are checked in the active session.

Then start the authorized active paper service and follow its first market session:

```bash
systemctl enable --now athena-paper.service
systemctl status athena-paper.service --no-pager
journalctl -u athena-paper.service -f
cat /var/lib/athena/health.json
```

Record the installed commit, successful provider preflight, startup status and actual broker results separately from tests. A running process or heartbeat alone does not prove a trade, confirmed flatness, or adjudication quality. An eligible session can legitimately produce no trade.

## Daily operation and recovery

The supervisor checks broker session state, sleeps between checks, and starts the same paper CLI when the market is open. A fresh session gets a dated journal. A finished or halted day is not restarted for more entries. Old journals are retained and verified; an unresolved entry takes priority over today's session, including outside market hours. Multiple unresolved sessions or a corrupt journal block new entries.

Recovery uses the original journal's symbol, feed and Actor identity. A reserved entry can reconcile and close without OpenAI or TypeSafe credentials. It still needs the broker and writable state. The service never restores a backup automatically, creates a fresh journal to bypass a halt, or blindly resends a reserved entry. Broker client IDs remain stable per account/session.

The child begins flattening 15 minutes before the broker-reported close, including early closes. A missed close or date rollover ends an empty cloud session. An unresolved position continues recovery rather than authorizing the next day. A per-session halt without exposure permits a fresh journal on the next market day; to suspend all future sessions, stop the service.

```bash
systemctl stop athena-paper.service
```

SIGTERM requests the child's existing halt and cleanup path. The service allows up to 210 seconds for shutdown. If the broker cannot confirm flat, exposure remains unresolved and must be inspected in the paper account. Confirm positions and orders before host upgrades, shutdown, backup restore or replacement. A restored disk may omit recent events: reconcile against the broker before activation, and never run the restored host alongside the original.

`health.json` reports the supervisor state and timestamp; journald contains child transitions and failures. Neither is an external alert. Configure monitoring for service failure, stale heartbeat, unresolved exposure and low disk space before treating the first deployment as unattended. Completed journals grow over time and currently remain on disk; no retention deletion is automated. Broker outages, a host failure or a trading halt can defeat pre-close liquidation.

## Verified state and references

As of 24 September 2026: **648 root tests pass**, including 28 additional cloud/hosted-Actor cases. Changed Python passes Ruff 0.15.8 and the installer passes `bash -n`. These are fixture/local checks; installation and systemd startup on the target Droplet have not occurred. The tested source revision is recorded in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

The DigitalOcean console returned “Site Unavailable” in this workspace browser on the initial visit and one reload. No DigitalOcean credential was found in the connected credential catalog. Alpaca, OpenAI and TypeSafe runtime keys are absent here. **No server has been provisioned, no service deployed, and no provider inference or paper order executed.**

Primary documentation checked on the status date:

- [DigitalOcean Droplet pricing](https://www.digitalocean.com/pricing/droplets), [creation](https://docs.digitalocean.com/products/droplets/how-to/create/) and [App Platform filesystem limits](https://docs.digitalocean.com/products/app-platform/details/limits/).
- [GPT-5.6 Terra model](https://developers.openai.com/api/docs/models/gpt-5.6-terra) and [Responses structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
- Broker and Jev references are maintained in [LIVE_PAPER.md](LIVE_PAPER.md#verified-api-references).
