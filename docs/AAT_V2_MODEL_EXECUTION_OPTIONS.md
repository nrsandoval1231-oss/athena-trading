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

## Decision boundary

The recommended next step is a bounded hosted-Qwen compatibility benchmark or
DeepSeek API pilot, not the full run. No provider switch, RunPod account charge,
model call, or holdout operation is authorized by this document.
