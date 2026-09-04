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

The initial canary is capped at $10 total spend. It may contain at most ten
opportunities and therefore at most 30 Actor calls: deterministic Arm A makes
no model request, while Arms B, C, and D each make one. No request may occur
while the plan remains `UNPRICED`.

The command requires explicit call, token, and spend ceilings and refuses to
overwrite its output. A future live training runner must be separately
authorized and may only consume this validated boundary.

## Validation evidence

Validated locally on 3 September 2026 against Athena projection
`aef846f2b26bac428e8de48845bd4ce129eaa2c00a62e4b17272dd522e9113cc`:

- source rows verified: 1,165,277;
- opportunity rows emitted: 10;
- opportunity rows SHA-256:
  `1ac694b3b25b6f7c8e49c09c01b1abe5b46de08ec66f03cb9498b312d9f39c38`;
- dry plan: 30 Actor calls and 11,730 tokens under the $10 ceiling;
- full repository tests: 158 passed;
- spend estimate: `UNPRICED`, therefore no Actor request is permitted;
- API calls made: 0; spend incurred: $0; execution authority: `ABSENT`.
