"""Compact arm-E deliberation schema for the registered 4 x 64 token budget.

The local Qwen 100-opportunity pilot accepted 0/400 E-arm responses because
each turn required the five-field Actor object (``action``, ``confidence``,
``horizon``, ``invalidation``, ``rationale``). Those last three fields are
unbounded strings. At 64 output tokens the model hit ``done_reason: length``
before the JSON closed.

Arm E stays equal-budget with one arm-D call: four turns, 64 tokens each,
256 total. This module does not raise TRAINING spend. It replaces the per-turn
payload with closed enums so a valid object has a bounded worst-case size
that fits the 64-token ceiling under the same conservative ``utf-8 bytes / 4``
proxy used elsewhere in AAT planning.

B/C/D still emit the five-field Actor schema. Only E uses the compact turns.
The registered protocol JSON is not rewritten; the compact schema is bound
by a non-authorizing amendment. ``execution_authority`` stays ``ABSENT``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..athena_adapter.contracts import canonical_hash, canonical_json
from .arms import assert_equal_token_budget

AMENDMENT_SCHEMA = "athena-aat-v2-e-arm-compact-schema-amendment-v1"
AMENDMENT_KIND = "NON_AUTHORIZING_PROTOCOL_NOTE"
PROTOCOL_SCHEMA = "athena-aat-v2-registered-protocol-v1"

DELIBERATION_TURNS = 4
DELIBERATION_OUTPUT_TOKENS = 64
SINGLE_CALL_OUTPUT_TOKENS = 256
DELIBERATION_TURN_ROLES = ("BULL", "BEAR", "TRADER", "RISK")
STANCE_TURNS = ("BULL", "BEAR", "TRADER")
DECISION_TURN = "RISK"
STANCES = ("IN", "OUT")
CLAIMS = ("MACRO", "PRICE", "NONE")
LEGAL_ACTIONS = ("PASS", "ENTER_REDUCED", "ENTER_STANDARD", "ENTER_HIGH_CONVICTION")
STANCE_KEYS = ("turn", "stance", "confidence", "claim")
DECISION_KEYS = ("turn", "action", "confidence")
TRUNCATION_FINISH_REASONS = ("length", "max_tokens")

#: Conservative planning proxy used by AAT-V1 training plans.
BYTES_PER_TOKEN_PROXY = 4

OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"
INCOMPLETE_JSON = "INCOMPLETE_JSON"
SCHEMA_MISMATCH = "deliberation output schema mismatch"
INVALID_CONFIDENCE = "deliberation confidence is invalid"
INVALID_ACTION = "deliberation action is not allowed"

DELIBERATION_PROMPT = (
    "Fail-closed AAT-V2 arm-E turn. Return exactly one compact JSON object and "
    "nothing else: no markdown, no code fences, no commentary, no extra keys, "
    "no prose. Never claim execution_authority. This is PREFLIGHT, not TRAINING."
)


class DeliberationError(ValueError):
    """The compact E-arm schema cannot be used as specified."""


def conservative_token_count(text: str) -> int:
    """``utf-8 bytes / 4``, matching the AAT planning proxy. Minimum 1."""
    if not isinstance(text, str):
        raise DeliberationError("token estimate requires text")
    return max(1, len(text.encode("utf-8")) // BYTES_PER_TOKEN_PROXY)


def truncate_to_token_budget(text: str, *, max_output_tokens: int) -> str:
    """Cut ``text`` to the conservative byte budget for ``max_output_tokens``."""
    if max_output_tokens < 1:
        raise DeliberationError("token budgets must be positive")
    limit = max_output_tokens * BYTES_PER_TOKEN_PROXY
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")


def is_truncation_finish(response: Mapping[str, Any] | None, *, finish_reason: str | None = None) -> bool:
    """True when the provider stopped because the output budget was exhausted."""
    reason = finish_reason
    if reason is None and isinstance(response, Mapping):
        reason = _finish_reason(response)
    return reason in TRUNCATION_FINISH_REASONS


def deliberation_plan() -> dict[str, Any]:
    """Hashable compact-schema plan. Equal-budget with one 256-token call."""
    assert_equal_token_budget(
        deliberation_turns=DELIBERATION_TURNS,
        per_turn_output_tokens=DELIBERATION_OUTPUT_TOKENS,
        single_call_output_tokens=SINGLE_CALL_OUTPUT_TOKENS,
    )
    if DELIBERATION_TURN_ROLES != ("BULL", "BEAR", "TRADER", "RISK"):
        raise DeliberationError("turn roles must stay bull, bear, trader, risk")
    plan = {
        "schema_version": AMENDMENT_SCHEMA,
        "turns": DELIBERATION_TURNS,
        "turn_roles": list(DELIBERATION_TURN_ROLES),
        "per_turn_output_tokens": DELIBERATION_OUTPUT_TOKENS,
        "single_call_output_tokens": SINGLE_CALL_OUTPUT_TOKENS,
        "stance_turns": list(STANCE_TURNS),
        "decision_turn": DECISION_TURN,
        "stances": list(STANCES),
        "claims": list(CLAIMS),
        "legal_actions": list(LEGAL_ACTIONS),
        "stance_keys": list(STANCE_KEYS),
        "decision_keys": list(DECISION_KEYS),
        "unbounded_text_fields": False,
        "prompt": DELIBERATION_PROMPT,
    }
    plan["deliberation_hash"] = canonical_hash(plan)
    return plan


def turn_instruction(turn: str) -> dict[str, Any]:
    """Closed-enum emit contract for one E-arm turn. No free-text fields."""
    role = _validate_turn(turn)
    if role == DECISION_TURN:
        return {
            "turn": role,
            "required_keys": list(DECISION_KEYS),
            "action": list(LEGAL_ACTIONS),
            "confidence": "[0,1]",
        }
    return {
        "turn": role,
        "required_keys": list(STANCE_KEYS),
        "stance": list(STANCES),
        "claim": list(CLAIMS),
        "confidence": "[0,1]",
    }


def compact_user_payload(packet: Mapping[str, Any], *, turn: str) -> dict[str, Any]:
    """User-message body for one E turn: packet plus the closed emit contract."""
    return {
        "packet": dict(packet),
        "turn": _validate_turn(turn),
        "emit": turn_instruction(turn),
    }


def worst_case_compact_payloads() -> dict[str, dict[str, Any]]:
    """Largest legal compact object per turn role. Used to prove the budget."""
    payloads = {
        role: {
            "turn": role,
            "stance": "OUT",
            "confidence": 1.0,
            "claim": "PRICE",
        }
        for role in STANCE_TURNS
    }
    payloads[DECISION_TURN] = {
        "turn": DECISION_TURN,
        "action": "ENTER_HIGH_CONVICTION",
        "confidence": 1.0,
    }
    return payloads


def assert_compact_payloads_fit_budget(
    payloads: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    max_output_tokens: int = DELIBERATION_OUTPUT_TOKENS,
) -> dict[str, int]:
    """Refuse a compact schema whose worst-case serialization exceeds the budget."""
    items = payloads if payloads is not None else worst_case_compact_payloads()
    counts: dict[str, int] = {}
    for role, payload in items.items():
        serialized = canonical_json(payload)
        tokens = conservative_token_count(serialized)
        counts[str(role)] = tokens
        if tokens > max_output_tokens:
            raise DeliberationError(
                f"compact payload for {role} needs {tokens} tokens; "
                f"budget is {max_output_tokens}"
            )
    return counts


def parse_deliberation_turn(
    content: str,
    *,
    turn: str,
    finish_reason: str | None = None,
    done_reason: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Parse one E-arm turn. Truncated or unbounded Actor JSON is not accepted."""
    role = _validate_turn(turn)
    truncated = (finish_reason in TRUNCATION_FINISH_REASONS) or (
        done_reason in TRUNCATION_FINISH_REASONS
    )
    if not isinstance(content, str) or not content.strip():
        return None, OUTPUT_TRUNCATED if truncated else INCOMPLETE_JSON
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None, OUTPUT_TRUNCATED if truncated else INCOMPLETE_JSON
    if not isinstance(parsed, Mapping):
        return None, SCHEMA_MISMATCH
    expected_keys = DECISION_KEYS if role == DECISION_TURN else STANCE_KEYS
    if set(parsed) != set(expected_keys):
        return None, SCHEMA_MISMATCH
    if parsed.get("turn") != role:
        return None, SCHEMA_MISMATCH
    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        return None, INVALID_CONFIDENCE
    if role == DECISION_TURN:
        if parsed.get("action") not in LEGAL_ACTIONS:
            return None, INVALID_ACTION
    else:
        if parsed.get("stance") not in STANCES or parsed.get("claim") not in CLAIMS:
            return None, SCHEMA_MISMATCH
    return dict(parsed), None


def scored_action_from_turns(turns: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The RISK turn is the scored Actor action. Earlier turns are debate only."""
    if len(turns) != DELIBERATION_TURNS:
        raise DeliberationError("deliberation requires exactly four accepted turns")
    roles = tuple(item.get("turn") for item in turns)
    if roles != DELIBERATION_TURN_ROLES:
        raise DeliberationError("deliberation turns must be BULL, BEAR, TRADER, RISK")
    decision = turns[-1]
    return {
        "action": decision["action"],
        "confidence": float(decision["confidence"]),
        "source_turn": DECISION_TURN,
        "execution_authority": "ABSENT",
    }


def load_e_arm_amendment(path: str | Path, *, protocol_path: str | Path) -> dict[str, Any]:
    """Load the non-authorizing compact-schema note. Does not rewrite the protocol."""
    amendment = _load_json(path)
    protocol = _load_json(protocol_path)
    payload = dict(amendment)
    claimed = payload.pop("amendment_hash", None)
    if (
        amendment.get("schema_version") != AMENDMENT_SCHEMA
        or amendment.get("amendment_kind") != AMENDMENT_KIND
        or amendment.get("rewrites_registered_protocol") is not False
        or claimed != canonical_hash(payload)
    ):
        raise DeliberationError("E-arm amendment is unusable")
    if amendment.get("base_protocol_hash") != protocol.get("protocol_hash"):
        raise DeliberationError("E-arm amendment does not bind the registered protocol")
    if protocol.get("schema_version") != PROTOCOL_SCHEMA:
        raise DeliberationError("registered protocol schema is unexpected")
    if (
        amendment.get("registration_status") != "REGISTERED_NOT_AUTHORIZED"
        or amendment.get("execution_authorized") is not False
        or amendment.get("claim_authorized") is not False
        or amendment.get("execution_authority") != "ABSENT"
        or amendment.get("sealed_holdout_consumed") is not False
        or amendment.get("training_spend_unchanged") is not True
    ):
        raise DeliberationError("E-arm amendment must not authorize TRAINING")
    expected = deliberation_plan()
    bound = amendment.get("deliberation")
    if not isinstance(bound, Mapping) or bound.get("deliberation_hash") != expected["deliberation_hash"]:
        raise DeliberationError("E-arm amendment deliberation hash drifted")
    return amendment


def _validate_turn(turn: str) -> str:
    if turn not in DELIBERATION_TURN_ROLES:
        raise DeliberationError(f"unknown deliberation turn {turn!r}")
    return turn


def _finish_reason(response: Mapping[str, Any]) -> str | None:
    done = response.get("done_reason")
    if isinstance(done, str) and done:
        return done
    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        finish = choices[0].get("finish_reason")
        if isinstance(finish, str) and finish:
            return finish
    return None


def _load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    text = str(target).lower()
    if "holdout" in text or "sealed" in text:
        raise DeliberationError("holdout paths are forbidden")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliberationError("E-arm amendment input is unreadable") from exc
    if not isinstance(value, dict):
        raise DeliberationError("E-arm amendment input is not an object")
    return value
