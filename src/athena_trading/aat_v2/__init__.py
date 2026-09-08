"""AAT-V2: the exogenous-evidence adaptation experiment.

Proposed in ``docs/AAT_V2_VISION.md``, sequenced in ``docs/AAT_V2_PLAN.md``.
This package holds the experiment's machinery only. It contains no registered
protocol, no drawn assignment, no frozen contract and no evidence, and
``execution_authority`` is ``ABSENT`` throughout.

AAT-V1 is untouched. V2 is a separate experiment with its own dataset
assignment and its own, separately authorized holdout.
"""

from .arms import (
    ACTOR_ARMS,
    ARMS,
    DELIBERATION_ARMS,
    EXOGENOUS_ARMS,
    MEMORY_ARMS,
    ArmError,
    arm_definitions,
    assert_equal_token_budget,
    assert_treatment_asymmetry,
    calls_actor,
    deliberates,
    receives_exogenous,
    receives_memory,
)
from .assignment import (
    AssignmentError,
    assert_linked_symbols_share_a_partition,
    assert_partitions_disjoint,
    draw_assignment,
    filter_opportunities,
    load_universe,
    partition_for,
    write_assignment,
)
from .contract import ContractError, freeze_claim_contract, load_contract, write_contract
from .packet import (
    assert_control_arm_never_saw_treatment,
    build_packet,
    ledger_projection,
    packet_hash,
)

__all__ = [
    "ACTOR_ARMS", "ARMS", "DELIBERATION_ARMS", "EXOGENOUS_ARMS", "MEMORY_ARMS",
    "ArmError", "AssignmentError", "ContractError", "arm_definitions",
    "assert_control_arm_never_saw_treatment", "assert_equal_token_budget",
    "assert_linked_symbols_share_a_partition", "assert_partitions_disjoint", "assert_treatment_asymmetry", "build_packet",
    "calls_actor", "deliberates", "draw_assignment", "filter_opportunities",
    "freeze_claim_contract", "ledger_projection", "load_contract", "load_universe",
    "packet_hash", "partition_for", "receives_exogenous", "receives_memory",
    "write_assignment", "write_contract",
]
