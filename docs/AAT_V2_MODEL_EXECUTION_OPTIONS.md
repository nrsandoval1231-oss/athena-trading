# AAT-V2 Model Execution Options

Status: planning evidence only. No full TRAINING execution has been authorized.
The registered protocol remains `REGISTERED_NOT_AUTHORIZED` and
`execution_authority` remains `ABSENT`. DeepSeek Flash is a `CANDIDATE_PILOT`
identity with a bounded fixture pilot; that path is not TRAINING.

## Workload

The full macro-only TRAINING plan contains 16,705 opportunities. Arm A is the
frozen deterministic control and makes no model calls. Arms B, C, and D make
one call per opportunity. Arm E has four deliberation turns per opportunity,
with 64 output tokens per turn so its total output allowance equals one 256
-token single call. Those E-arm turns now require a compact closed-enum JSON
object rather than the five-field Actor schema. The 4 x 64 = 256 output
ceiling is unchanged, so TRAINING spend is not raised.

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
- E failed because 64 tokens truncated the required five-field Actor JSON
  (`done_reason: length`);
- observed throughput: 0.348 calls/second;
- extrapolated full-run time: approximately 93.2 hours.

The local model was stopped after the pilot. Its ledger remains in disposable
ignored temp storage. That E configuration required unbounded
`horizon` / `invalidation` / `rationale` strings on every 64-token turn. A
non-authorizing compact-schema amendment now binds closed-enum E-arm turns
that fit the same 64-token budget. TRAINING remains unauthorized; the
registered protocol is not rewritten.

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

Changing from Terra to DeepSeek changes the Actor identity. DeepSeek Flash is
declared as its own provider-scoped candidate identity
(`configs/aat_v2_deepseek_flash_actor_identity_v1.json`, status
`CANDIDATE_PILOT`). It is not bound to the registered Terra protocol. A
bounded fixture pilot exists; that pilot is not TRAINING. Live spend for an
actual API run is separately unauthorized.

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

These figures exclude storage and setup time. A faster GPU does not by itself
fix E-arm JSON truncation. The compact closed-enum schema in
`configs/aat_v2_e_arm_compact_schema_amendment_v1.json` is the E redesign:
worst-case valid turns fit the registered 64-token budget under the
conservative byte/4 proxy. It is a `NON_AUTHORIZING_PROTOCOL_NOTE`. It does
not raise TRAINING spend, does not rewrite the registered protocol, and does
not make E optional.

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
  not implied; the CLI injects the fixture transport. The hard PREFLIGHT spend
  ceiling is $1.00 (Nick-authorized for preflight only; not TRAINING). CLI
  `--preflight` stays fixture/dry by default. Live library calls still need
  `allow_live=True` plus `DEEPSEEK_API_KEY`.
- **Structured Actor JSON.** Preflight payloads send `thinking: {type:
  disabled}` so Flash reasoning tokens cannot consume `max_tokens` and leave
  `content` empty. The system prompt requires exactly the Actor keys (`action`,
  `confidence`, `horizon`, `invalidation`, `rationale`) and legal actions
  (PASS / ENTER_REDUCED / ENTER_STANDARD / ENTER_HIGH_CONVICTION). Single-call
  preflight output is 512 tokens so complete JSON can land once thinking is
  off; the registered TRAINING protocol remains 256. `response_format:
  json_object` is set. Arm E does **not** reuse the five-field Actor schema
  on 64-token turns. Compact E-arm JSON is `turn` plus closed enums
  (`stance`/`claim` on BULL/BEAR/TRADER; scored `action` on RISK) with no
  free-text fields. Fixture preflight `--include-deliberation` replays those
  compact turns from
  `configs/fixtures/deepseek_flash_preflight_v1.json` against
  `configs/aat_v2_e_arm_compact_schema_amendment_v1.json`. `execution_authority`
  stays `ABSENT`.
- **Hard refusals.** `execution_authority` stays `ABSENT`. The Terra protocol
  is consulted and not rewritten. DeepSeek Flash is a `CANDIDATE_PILOT`
  identity (`configs/aat_v2_deepseek_flash_actor_identity_v1.json`) and must
  not reuse the Terra identity. Holdout paths and the consumed AAT-V1
  registry are forbidden. The superseded 66,820-call v1 plan is refused.

Library entry points: `plan_deepseek_flash_dry_run` and
`run_deepseek_flash_preflight` in `src/athena_trading/aat_v2/deepseek_preflight.py`.
`allow_live=True` exists only as an explicit library opt-in on top of the same
tiny ceilings and still cannot flip authority or consume a holdout.

## DeepSeek Flash candidate identity and bounded pilot

Nick authorized a **bounded DeepSeek Flash pilot with a new provider-scoped
identity**. That is not TRAINING authorization and it does not freeze a new
claim contract.

The identity is declared in
`configs/aat_v2_deepseek_flash_actor_identity_v1.json`. Provider, model,
prompt, and inference are hashed separately from Terra. The five-field Actor
decision schema is shared (same `action_schema_hash`) because it is the arm
decision contract, not a vendor. Compact E turns bind `deliberation_hash`
from the non-authorizing amendment. V1 `registered_providers()` remains
`("openai",)`. The registered V2 protocol remains OpenAI / `gpt-5.6-terra`.

`scripts/run_aat_v2_deepseek_flash_pilot.py` is the fail-closed entrypoint.
The library function is `run_deepseek_flash_pilot` in
`src/athena_trading/aat_v2/deepseek_pilot.py`. Receipts carry
`PILOT_NOT_TRAINING` and `execution_authority: ABSENT`.

- **CLI default.** Fixture / dry. One synthetic opportunity, B/C/D Actor JSON
  plus four compact E turns (7 calls), `$1.00` invocation cap. Requires
  `DEEPSEEK_API_KEY` even for the fixture so a missing credential fails
  closed. Does not open a network transport.
- **Live.** Requires `--allow-live`, `DEEPSEEK_API_KEY`, and explicit
  `--call-ceiling` plus `--spend-ceiling-usd`. Hard caps fail closed above
  20 opportunities, 140 calls, or `$5.00`. CI must not pass `--allow-live`.
- **Payload rules.** Thinking disabled. B/C/D use the Actor JSON schema at
  512 output tokens. E uses compact closed-enum turns at 64 tokens. Do not
  reintroduce five-field Actor JSON on E.

### Recommended first live knobs (not authorization)

Live spend is still Nick's to set. These are the documented starting values
for a later authorized API run against synthetic fixture packets. They are
not TRAINING, not a holdout sample, and not a protocol switch.

| Knob | Dry default | Recommended first live | Hard ceiling |
| --- | ---: | ---: | ---: |
| Opportunity sample | 1 | 10 | 20 |
| Call ceiling | 7 | 70 (10 × (3 Actor + 4 E)) | 140 |
| Spend ceiling (USD) | 1.00 | 2.00 | 5.00 |

Peak cache-miss worst case for the recommended 10-opportunity / 70-call
shape is well under $2 given Flash prices and the conservative byte/4 input
proxy. Nick can raise or lower the invocation ceiling **inside the hard
caps** when authorizing live spend. A TRAINING-sized ceiling is refused.

## Decision boundary

The DeepSeek candidate identity and fixture pilot are in-tree. The next
authorized step is a live DeepSeek API pilot under the knobs above, or a
hosted-Qwen compatibility benchmark — not the full 116,935-call run. Arm E's
JSON truncation is addressed at schema level (compact closed-enum turns at
the registered 4 x 64 budget) without raising TRAINING spend. No provider
switch into the registered protocol, RunPod account charge, TRAINING
execution, or holdout operation is authorized by this document.
