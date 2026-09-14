# AAT-V2 Model Execution Options

Status: planning evidence only. No full TRAINING execution has been authorized.
The registered protocol remains `REGISTERED_NOT_AUTHORIZED` and
`execution_authority` remains `ABSENT`.

## Workload

The full macro-only TRAINING plan contains 16,705 opportunities. Arm A is the
frozen deterministic control and makes no model calls. Arms B, C, and D make
one call per opportunity. Arm E has four deliberation turns per opportunity,
with 64 output tokens per turn so its total output allowance equals one 256
-token single call.

The corrected workload is:

| Arm | Calls |
| --- | ---: |
| B — memoryless | 16,705 |
| C — naive memory | 16,705 |
| D — Athena memory | 16,705 |
| E — four-turn deliberation | 66,820 |
| **Total** | **116,935** |

The earlier dry-run estimate counted E as one call and reported 66,820 calls;
that number is superseded by the corrected total above.

## Observed local Qwen result

The installed local model is `qwen3-coder:30b` (approximately 19 GB). The
machine exposed approximately 33.8 GB RAM and an NVIDIA RTX 5060 with 8 GB
VRAM. Ollama allocated the model approximately 67% GPU / 33% CPU.

A 100-opportunity, 700-call pilot completed in 2,008.8 seconds:

- B/C/D: 300/300 responses accepted;
- E: 0/400 responses accepted;
- E failed because 64 tokens truncated the required JSON (`done_reason:
  length`);
- observed throughput: 0.348 calls/second;
- extrapolated full-run time: approximately 93.2 hours.

The local model was stopped after the pilot. Its ledger remains in disposable
ignored temp storage. The full run must not use the current E configuration.

## DeepSeek V4.1 Flash API

DeepSeek's official pricing page identifies the API model as `deepseek-flash`.
At the time of measurement it listed:

- off-peak cache-miss input: $0.15 per million tokens;
- peak cache-miss input: $0.30 per million tokens;
- off-peak output: $0.60 per million tokens;
- peak output: $1.20 per million tokens.

Using the corrected workload and the repository's conservative input-size
proxy, the estimated full-run cost is approximately $32.68 off-peak or $65.35
peak. Actual billing depends on measured tokenization, cache behavior, retries,
and provider pricing changes.

DeepSeek source: <https://api-docs.deepseek.com/quick_start/pricing>

Changing from Terra to DeepSeek changes the Actor identity. It requires a
provider adapter, a new provider-scoped identity, and a separately verified
pilot. The Terra protocol must not be silently reused.

## RunPod-hosted Qwen

RunPod bills GPU compute by time; the official pricing page is dynamic. The
pricing observed during planning showed approximately $0.34/hour for a
Community RTX 4090 and approximately $0.74/hour for a Secure RTX 4090. RunPod
documents per-second compute billing and separate storage billing.

Sources:

- <https://www.runpod.io/pricing>
- <https://docs.runpod.io/pods/pricing>

A 24 GB RTX 4090 is a more suitable host for the approximately 19 GB Qwen
model than the local 8 GB GPU. Actual throughput must be benchmarked on the
selected image and inference server. Illustrative compute-only costs for the
17,105,920-token output ceiling are:

| Measured output speed | Runtime | At $0.34/hr | At $0.74/hr |
| ---: | ---: | ---: | ---: |
| 50 tokens/sec | 95.0 hr | $32.31 | $70.32 |
| 100 tokens/sec | 47.5 hr | $16.16 | $35.16 |
| 200 tokens/sec | 23.8 hr | $8.08 | $17.58 |
| 500 tokens/sec | 9.5 hr | $3.23 | $7.03 |

These figures exclude storage and setup time. A faster GPU does not fix the
current E-arm JSON truncation; the protocol still needs an E redesign or a
B/C/D-only amendment.

## DeepSeek Flash dry-run / preflight planner

`scripts/plan_aat_v2_deepseek_flash.py` turns the workload and prices in this
document into a fail-closed planning artifact. It is not TRAINING
authorization and it is not a provider switch.

- **Plan-only (default).** Reads `configs/aat_v2_training_plan_v2.json`, the
  frozen DeepSeek quote in `configs/deepseek_flash_pricing_2026-09-14.json`,
  and the registered V2 protocol. Emits off-peak / peak cache-miss estimates
  from the conservative input-byte proxy (~$32.68 / ~$65.35). No API key, no
  model call, no spend.
- **Fixture preflight (`--preflight`).** Requires `DEEPSEEK_API_KEY` and fails
  closed without it. Dry-runs at most seven calls (default three: arms B/C/D)
  against `configs/fixtures/deepseek_flash_preflight_v1.json`. Live HTTP is
  not implied; the CLI injects the fixture transport. Spend ceiling is capped
  at $0.10.
- **Hard refusals.** `execution_authority` stays `ABSENT`. The Terra protocol
  is consulted and not rewritten; DeepSeek remains an `UNREGISTERED_CANDIDATE`
  and must not reuse the Terra identity. Holdout paths and the consumed AAT-V1
  registry are forbidden. The superseded 66,820-call v1 plan is refused.

Library entry points: `plan_deepseek_flash_dry_run` and
`run_deepseek_flash_preflight` in `src/athena_trading/aat_v2/deepseek_preflight.py`.
`allow_live=True` exists only as an explicit library opt-in on top of the same
tiny ceilings and still cannot flip authority or consume a holdout.

## Decision boundary

The recommended next step remains a bounded hosted-Qwen compatibility
benchmark or DeepSeek API pilot, not the full run. The dry-run planner above
is cost and credential evidence for that decision. No provider switch, RunPod
account charge, TRAINING execution, or holdout operation is authorized by this
document.
