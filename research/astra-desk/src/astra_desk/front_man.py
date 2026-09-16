"""Front-man executor. Logs required fields; no unilateral veto."""

from __future__ import annotations

from typing import Any, Mapping

from astra_desk.errors import FrontManLogError
from astra_desk.ledger import canonical_hash
from astra_desk.types import FrontManLog, RoundDecision


REQUIRED_LOG_FIELDS = ("decision", "gate_inputs", "rationale_hash")


class FrontMan:
    role = "executor"

    def log(
        self,
        *,
        decision: RoundDecision,
        gate_inputs: Mapping[str, Any],
        rationale_payload: Mapping[str, Any],
    ) -> FrontManLog:
        if not decision:
            raise FrontManLogError("decision is required")
        if gate_inputs is None:
            raise FrontManLogError("gate_inputs is required")
        rationale_hash = canonical_hash(rationale_payload)
        record = FrontManLog(
            decision=decision.value,
            gate_inputs=dict(gate_inputs),
            rationale_hash=rationale_hash,
        )
        for field_name in REQUIRED_LOG_FIELDS:
            if not getattr(record, field_name):
                raise FrontManLogError(f"front-man must_log missing {field_name}")
        return record

    def execute(
        self,
        *,
        decision: RoundDecision,
        gate_inputs: Mapping[str, Any],
        rationale_payload: Mapping[str, Any],
        size_usd: float,
    ) -> tuple[FrontManLog, float]:
        """Carry out the gate+risk decision. Cannot flip deploy to refuse."""
        record = self.log(
            decision=decision,
            gate_inputs=gate_inputs,
            rationale_payload=rationale_payload,
        )
        if decision is RoundDecision.DEPLOY:
            return record, size_usd
        return record, 0.0
