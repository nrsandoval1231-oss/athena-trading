# AAT-V1 training planner

`run_aat_v1_training.py` validates a separately materialized `TRAINING` JSONL
projection and emits a deterministic dry-run plan. It does not invoke an
Actor, read a mixed dataset, score outcomes, contact a broker, or grant
authority. The projection receipt must bind `dataset_hash`, `assignment_hash`,
`protocol_hash`, row count, and an exact SHA-256 of the JSONL file.

`compile_aat_v1_opportunities.py` is the only bridge from Athena's raw bar
projection to this boundary. It verifies the complete source hash and explicit
absence of sealed content, then emits the first completed close breakout after
the frozen 15-minute ORB-M range. Its features are available at signal time and
contain neither outcomes nor symbol/date identifiers. It does not implement or
approximate the still-unbuilt Breakout Quality Score.

The initial canary is capped at $20 total spend. It may contain at most ten
opportunities and therefore at most 30 Actor calls: deterministic Arm A makes
no model request, while Arms B, C, and D each make one. No request may occur
until a separate immutable pricing record makes the transport bound explicit.

The command requires explicit call, token, and spend ceilings and refuses to
overwrite its output. The transport canary may consume this boundary only
under its explicit configuration; an adaptation or outcome-scoring runner
remains separately gated.

## Pre-canary validation evidence

Validated locally on 3 September 2026 against Athena projection
`aef846f2b26bac428e8de48845bd4ce129eaa2c00a62e4b17272dd522e9113cc`:

- source rows verified: 1,165,277;
- opportunity rows emitted: 10;
- opportunity rows SHA-256:
  `1ac694b3b25b6f7c8e49c09c01b1abe5b46de08ec66f03cb9498b312d9f39c38`;
- dry plan: 30 Actor calls and 11,730 tokens under the original $10 ceiling;
- full repository tests: 158 passed;
- spend estimate: `UNPRICED`, therefore no Actor request is permitted;
- at validation time, API calls made: 0 and spend incurred: $0;
- execution authority: `ABSENT`.

## Terra transport canary

The authorized transport canary ran on 3 September 2026 using the official
Terra rates frozen in `configs/openai_gpt_5_6_terra_pricing_2026-09-03.json`:
$2.00 per million input tokens and $12.00 per million output tokens. The source
is the official OpenAI model page recorded in that configuration.

This run is explicitly `TRANSPORT_CANARY_NOT_ADAPTATION`. No lesson inventory
exists yet, so it cannot support a comparison among memoryless, naive-memory,
and Athena-memory behavior. Two pre-ledger diagnostic calls are counted against
the 30-call ceiling and assigned a conservative cost upper bound rather than an
invented exact cost.

- total calls including diagnostics: 30;
- durably recorded calls: 28;
- accepted structured responses: 27;
- rejected responses: 1 (`RESPONSE_NOT_COMPLETED`), with no retry;
- full repository tests after runner implementation: 164 passed;
- recorded usage: 7,986 input tokens and 5,679 output tokens;
- recorded cost: $0.08412;
- corrected total cost upper bound including diagnostics: $0.095888;
- ledger SHA-256:
  `0f60516bd3df904e46c6f05592df81aed96088ab9aa4cb374ec9d1bd69a139ff`;
- sealed holdout consumed: false; execution authority: `ABSENT`.

The original ignored result used the mean planned request size for its two-call
diagnostic allowance and reported `$0.0958772`. The immutable amendment
`artifacts/aat-v1/terra-canary-amendment.json` replaces only that bound with
the maximum-request calculation above; it does not alter the ledger or any
Actor output.
