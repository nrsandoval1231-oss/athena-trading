# Working in Athena Trading

Nick's explicit instructions govern the task. The owner-supplied dual-strategy diagram and docs/ATHENA_TRADING_CANON.md define product direction.

Read, in order:
1. docs/ATHENA_TRADING_CANON.md
2. docs/IMPLEMENTATION_STATUS.md
3. docs/BUILD_PLAN.md

## Architecture rules

- Build Earnings Adaptive and Athena Predictive Intraday on one shared decision/context/risk/execution/APDE/outcome workflow.
- The LLM proposes decisions. Deterministic risk controls authorize execution. Broker events establish observed state.
- ORB-M is an intraday component and baseline. AAT-V1/V2 and Astra Desk are research references, not competing product architectures.
- Athena Core remains domain-neutral. Reuse its contracts through athena_adapter; do not create a second Core or generic orchestration framework here.
- Preserve the canonical APDE states: EXPLORE, PROTECT, HARVEST, DEFEND, EXIT.
- The diagram is target architecture, not evidence of implemented capability or trading performance.

## Scope and simplification

- Work on the earliest incomplete slice in BUILD_PLAN.md relevant to the owner's task.
- Extend existing modules when practical. Add a strategy package or shared contract only when it contains real behavior for the current slice.
- Do not add empty directories, parallel runtime stacks, new provider matrices, or another governing roadmap.
- Research files carry experiment-specific restrictions. Old provider/runbook next steps do not override the product build sequence.
- Do not rename or move frozen research code, manifests, datasets, identities, receipts, or referenced paths solely to make the tree cleaner. Any later retirement requires a dependency/reference check and preserved provenance.
- Preserve consumed-holdout status and frozen hashes. Canon restoration does not authorize changing historical evidence.
- Current execution authority remains PAPER/ABSENT. Existing spend and external-action boundaries still apply.

## Verification and handoff

- Keep fixtures/simulation separate from actual provider or broker evidence.
- Test changed behavior with meaningful cases and run the relevant existing suite.
- Root pytest excludes research/astra-desk/tests. Report that boundary; follow IMPLEMENTATION_STATUS.md for the separate suite.
- Report existing gate failures separately from regressions. Do not relabel a partial test pass as a clean repository.
- Update IMPLEMENTATION_STATUS.md with the tested commit, commands, observed results, and remaining limitations.
- Use one clear PR explaining the problem, resulting behavior, verification, and material limits.
