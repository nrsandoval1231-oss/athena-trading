"""Cost-bounded DeepSeek Flash dry-run / preflight for AAT-V2.

Planning evidence only. Estimates the corrected 116,935-call macro-only
TRAINING workload under the frozen DeepSeek Flash prices documented in
``docs/AAT_V2_MODEL_EXECUTION_OPTIONS.md``, and can dry-run a tiny N-call
preflight against a synthetic fixture.

This module does not authorize TRAINING, freeze a claim contract, register or
rewrite a protocol, consume a holdout, or bind DeepSeek as a substitute for the
registered Terra Actor identity. ``execution_authority`` is always ``ABSENT``.
Live transport fails closed without ``DEEPSEEK_API_KEY`` and is never implied
by a plan.
"""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..athena_adapter.contracts import canonical_hash
from ..exogenous import feature_keys
from .arms import ACTOR_ARMS, B_MEMORYLESS, C_NAIVE_MEMORY, D_ATHENA, E_DELIBERATION
from .packet import build_packet

PLAN_SCHEMA = "athena-aat-v2-deepseek-flash-dry-run-v1"
PREFLIGHT_SCHEMA = "athena-aat-v2-deepseek-flash-preflight-v1"
TRAINING_PLAN_SCHEMA = "athena-aat-v2-training-plan-v2"
PRICING_SCHEMA = "deepseek-text-pricing-v1"
PROTOCOL_SCHEMA = "athena-aat-v2-registered-protocol-v1"
FIXTURE_SCHEMA = "athena-deepseek-flash-preflight-fixture-v1"

PROVIDER = "deepseek"
MODEL_ID = "deepseek-flash"
ENDPOINT = "https://api.deepseek.com/chat/completions"
PRICING_SOURCE_PREFIX = "https://api-docs.deepseek.com/"

#: One synthetic opportunity: B/C/D plus four E turns. Never a TRAINING run.
PREFLIGHT_CALL_CEILING = 7
PREFLIGHT_SPEND_CEILING_USD = 0.10
SINGLE_CALL_OUTPUT_TOKENS = 256
DELIBERATION_OUTPUT_TOKENS = 64
DELIBERATION_TURNS = 4
DEFAULT_PREFLIGHT_ARMS = (B_MEMORYLESS, C_NAIVE_MEMORY, D_ATHENA)
LEGAL_ACTIONS = ("PASS", "ENTER_REDUCED", "ENTER_STANDARD", "ENTER_HIGH_CONVICTION")

PREFLIGHT_PROMPT = (
    "You are a fail-closed AAT-V2 preflight Actor. Use only the supplied opaque "
    "features. Return one allowed entry action as JSON. Never claim execution "
    "authority. This is not TRAINING."
)

Transport = Callable[[Mapping[str, Any], str], Mapping[str, Any]]

_FORBIDDEN_PATH_TOKENS = ("holdout", "sealed")
_PROTECTED_ARTIFACTS = (
    "aat_v2_registered_protocol_v1.json",
    "aat_v2_claim_contract_v1.json",
    "aat_consumed_holdouts_v1.json",
)


class DeepSeekPreflightError(ValueError):
    """The DeepSeek dry-run or preflight cannot proceed safely."""


class MissingDeepSeekCredentialsError(DeepSeekPreflightError):
    """DEEPSEEK_API_KEY is absent. Fail closed; do not call the network."""


def api_key_from_env() -> str:
    """Return ``DEEPSEEK_API_KEY`` or fail closed without touching the network."""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise MissingDeepSeekCredentialsError(
            "DEEPSEEK_API_KEY is required for a DeepSeek Flash preflight"
        )
    return key


def load_deepseek_pricing(path: str | Path) -> dict[str, Any]:
    """Load the frozen DeepSeek Flash quote. Tests never contact the vendor."""
    payload = _load_json(path)
    off_peak = payload.get("off_peak")
    peak = payload.get("peak")
    if (
        payload.get("schema_version") != PRICING_SCHEMA
        or payload.get("provider") != PROVIDER
        or payload.get("model_id") != MODEL_ID
        or not isinstance(off_peak, Mapping)
        or not isinstance(peak, Mapping)
        or not str(payload.get("source_url", "")).startswith(PRICING_SOURCE_PREFIX)
    ):
        raise DeepSeekPreflightError("DeepSeek pricing configuration is invalid")
    for band in (off_peak, peak):
        if (
            float(band.get("input_usd_per_million_tokens", 0)) <= 0
            or float(band.get("output_usd_per_million_tokens", 0)) <= 0
        ):
            raise DeepSeekPreflightError("DeepSeek pricing rates must be positive")
    return payload


def load_macro_only_training_plan(path: str | Path) -> dict[str, Any]:
    """Load the corrected dry-run TRAINING plan. The v1 undercount is refused."""
    payload = _load_json(path)
    if payload.get("schema_version") != TRAINING_PLAN_SCHEMA:
        raise DeepSeekPreflightError(
            "DeepSeek dry-run requires the corrected v2 TRAINING plan; "
            "the 66,820-call v1 estimate is superseded"
        )
    _require_bound_hash(payload, "plan_hash")
    _assert_absent_authority(payload)
    if (
        payload.get("dry_run") is not True
        or payload.get("execution_authorized") is not False
        or payload.get("claim_authorized") is not False
        or payload.get("partition") != "TRAINING"
        or payload.get("planned_calls") != 116935
    ):
        raise DeepSeekPreflightError("TRAINING plan is not a non-authorizing dry-run")
    return payload


def load_registered_v2_protocol(path: str | Path) -> dict[str, Any]:
    """Consult the registered protocol without rewriting it."""
    payload = _load_json(path)
    protocol = payload.get("protocol")
    if (
        payload.get("schema_version") != PROTOCOL_SCHEMA
        or not isinstance(protocol, Mapping)
        or protocol.get("registration_status") != "REGISTERED_NOT_AUTHORIZED"
        or protocol.get("claim_authorized") is not False
    ):
        raise DeepSeekPreflightError("V2 protocol is not REGISTERED_NOT_AUTHORIZED")
    _require_bound_hash(payload, "protocol_hash")
    _assert_absent_authority(payload)
    _assert_absent_authority(protocol)
    actor = protocol.get("actor_identity")
    if not isinstance(actor, Mapping) or actor.get("provider") != "openai":
        raise DeepSeekPreflightError(
            "registered Actor is not the Terra identity; refusing a silent swap"
        )
    if actor.get("model_id") == MODEL_ID or actor.get("provider") == PROVIDER:
        raise DeepSeekPreflightError("DeepSeek must not reuse the Terra protocol identity")
    return payload


def plan_deepseek_flash_dry_run(
    *,
    training_plan_path: str | Path,
    pricing_path: str | Path,
    protocol_path: str | Path,
) -> dict[str, Any]:
    """Estimate full-run DeepSeek cost. Makes no model call and spends nothing."""
    plan = load_macro_only_training_plan(training_plan_path)
    pricing = load_deepseek_pricing(pricing_path)
    protocol = load_registered_v2_protocol(protocol_path)
    input_proxy = plan["input_byte_ceiling_proxy"]
    output_tokens = plan["output_token_ceiling"]
    if not isinstance(input_proxy, int) or not isinstance(output_tokens, int):
        raise DeepSeekPreflightError("TRAINING plan ceilings are malformed")
    if input_proxy < 1 or output_tokens < 1:
        raise DeepSeekPreflightError("TRAINING plan ceilings must be positive")

    off_peak = _cost_usd(input_proxy, output_tokens, pricing["off_peak"])
    peak = _cost_usd(input_proxy, output_tokens, pricing["peak"])
    result = {
        "schema_version": PLAN_SCHEMA,
        "status": "DRY_RUN_NOT_TRAINING",
        "dry_run": True,
        "execution_authorized": False,
        "claim_authorized": False,
        "sealed_holdout_consumed": False,
        "execution_authority": "ABSENT",
        "registration_status": "REGISTERED_NOT_AUTHORIZED",
        "terra_protocol_not_reused": True,
        "candidate_provider": PROVIDER,
        "candidate_model_id": MODEL_ID,
        "actor_identity_status": "UNREGISTERED_CANDIDATE",
        "protocol_hash": protocol["protocol_hash"],
        "training_plan_hash": plan["plan_hash"],
        "pricing_source_url": pricing["source_url"],
        "source_document": "docs/AAT_V2_MODEL_EXECUTION_OPTIONS.md",
        "partition": "TRAINING",
        "row_count": plan["row_count"],
        "planned_calls": plan["planned_calls"],
        "input_byte_ceiling_proxy": input_proxy,
        "output_token_ceiling": output_tokens,
        "off_peak_cost_usd": off_peak,
        "peak_cost_usd": peak,
        "worst_case_cost_usd": peak,
        "live_calls": False,
        "actor_called": False,
    }
    result["plan_hash"] = canonical_hash(result)
    return result


def build_deepseek_payload(
    packet: Mapping[str, Any], *, max_output_tokens: int,
) -> dict[str, Any]:
    """Chat-completions body for a DeepSeek Flash preflight call."""
    if max_output_tokens < 1:
        raise DeepSeekPreflightError("output token budget must be positive")
    return {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": PREFLIGHT_PROMPT},
            {
                "role": "user",
                "content": json.dumps(packet, sort_keys=True, separators=(",", ":")),
            },
        ],
        "max_tokens": max_output_tokens,
        "response_format": {"type": "json_object"},
        "stream": False,
    }


def fixture_transport(fixture_path: str | Path) -> Transport:
    """Return a transport that replays the synthetic fixture. No network."""
    envelope = _load_preflight_fixture(fixture_path)

    def _replay(payload: Mapping[str, Any], api_key: str) -> Mapping[str, Any]:
        _require_key(api_key)
        if payload.get("model") != MODEL_ID:
            raise DeepSeekPreflightError("fixture transport received a non-Flash payload")
        return dict(envelope["response"])

    return _replay


def run_deepseek_flash_preflight(
    *,
    fixture_path: str | Path,
    pricing_path: str | Path,
    protocol_path: str | Path,
    call_ceiling: int,
    spend_ceiling_usd: float,
    api_key: str | None = None,
    transport: Transport | None = None,
    allow_live: bool = False,
    include_deliberation: bool = False,
) -> dict[str, Any]:
    """Tiny N-call preflight. Fixture-safe by default; live spend is not implied.

    ``DEEPSEEK_API_KEY`` is required even when a fixture transport is injected,
    so a missing credential fails closed before any request is built. Live
    transport additionally requires ``allow_live=True`` and still cannot raise
    ``execution_authority`` or consume a holdout.
    """
    if call_ceiling < 1 or call_ceiling > PREFLIGHT_CALL_CEILING:
        raise DeepSeekPreflightError(
            f"preflight call ceiling must be in 1..{PREFLIGHT_CALL_CEILING}"
        )
    if spend_ceiling_usd < 0 or spend_ceiling_usd > PREFLIGHT_SPEND_CEILING_USD:
        raise DeepSeekPreflightError(
            f"preflight spend ceiling must be in 0..{PREFLIGHT_SPEND_CEILING_USD}"
        )
    key = api_key if api_key is not None else api_key_from_env()
    _require_key(key)
    pricing = load_deepseek_pricing(pricing_path)
    protocol = load_registered_v2_protocol(protocol_path)
    fixture = _load_preflight_fixture(fixture_path)
    sender = _resolve_transport(transport=transport, allow_live=allow_live)

    arms = _preflight_arm_sequence(include_deliberation=include_deliberation)
    if len(arms) > call_ceiling:
        raise DeepSeekPreflightError("preflight arm sequence exceeds call ceiling")

    packet = _fixture_packet(fixture)
    requests = [
        {
            "arm": arm,
            "opportunity_id": fixture["opportunity_id"],
            "max_output_tokens": (
                DELIBERATION_OUTPUT_TOKENS if arm == E_DELIBERATION
                else SINGLE_CALL_OUTPUT_TOKENS
            ),
            "payload": build_deepseek_payload(
                packet,
                max_output_tokens=(
                    DELIBERATION_OUTPUT_TOKENS if arm == E_DELIBERATION
                    else SINGLE_CALL_OUTPUT_TOKENS
                ),
            ),
        }
        for arm in arms
    ]
    worst_cost = _preflight_worst_case_usd(requests, pricing["peak"])
    if worst_cost > spend_ceiling_usd:
        raise DeepSeekPreflightError("worst-case preflight cost exceeds spend ceiling")

    records = []
    total_input = total_output = 0
    live_calls = sender is _live_transport
    for index, item in enumerate(requests, start=1):
        response = sender(item["payload"], key)
        prompt_tokens, completion_tokens = _read_usage(response)
        total_input += prompt_tokens
        total_output += completion_tokens
        actual_cost = _cost_usd(total_input, total_output, pricing["peak"])
        if actual_cost > spend_ceiling_usd:
            raise DeepSeekPreflightError("reported usage exceeds spend ceiling")
        decision, rejection = _read_decision(response)
        records.append({
            "sequence": index,
            "arm": item["arm"],
            "opportunity_id": item["opportunity_id"],
            "request_hash": canonical_hash(item["payload"]),
            "accepted": rejection is None,
            "rejection_reason": rejection,
            "decision": decision,
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "cumulative_cost_usd": actual_cost,
            "status": "PREFLIGHT_NOT_TRAINING",
            "execution_authority": "ABSENT",
        })

    result = {
        "schema_version": PREFLIGHT_SCHEMA,
        "status": "PREFLIGHT_NOT_TRAINING",
        "dry_run": True,
        "execution_authorized": False,
        "claim_authorized": False,
        "sealed_holdout_consumed": False,
        "execution_authority": "ABSENT",
        "registration_status": "REGISTERED_NOT_AUTHORIZED",
        "terra_protocol_not_reused": True,
        "candidate_provider": PROVIDER,
        "candidate_model_id": MODEL_ID,
        "actor_identity_status": "UNREGISTERED_CANDIDATE",
        "protocol_hash": protocol["protocol_hash"],
        "source_document": "docs/AAT_V2_MODEL_EXECUTION_OPTIONS.md",
        "request_count": len(records),
        "accepted_count": sum(record["accepted"] for record in records),
        "rejected_count": sum(not record["accepted"] for record in records),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "worst_case_cost_usd": worst_cost,
        "recorded_actual_cost_usd": records[-1]["cumulative_cost_usd"] if records else 0.0,
        "live_calls": live_calls,
        "actor_called": True,
        "allow_live": allow_live,
        "records": records,
    }
    result["result_hash"] = canonical_hash(
        {key: value for key, value in result.items() if key != "records"}
    )
    _assert_no_secret(result, key)
    return result


def _resolve_transport(*, transport: Transport | None, allow_live: bool) -> Transport:
    if transport is _live_transport or transport is None:
        if not allow_live:
            raise DeepSeekPreflightError(
                "live DeepSeek calls are not authorized by this planner"
            )
        return _live_transport
    return transport


def _live_transport(payload: Mapping[str, Any], api_key: str) -> dict[str, Any]:
    _require_key(api_key)
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        ENDPOINT,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:  # nosec B310: pinned HTTPS
            parsed = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise DeepSeekPreflightError(
            f"DeepSeek preflight failed closed with HTTP {exc.code}"
        ) from exc
    except URLError as exc:
        raise DeepSeekPreflightError("DeepSeek preflight request failed closed") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeepSeekPreflightError("DeepSeek response was not JSON") from exc
    if not isinstance(parsed, dict):
        raise DeepSeekPreflightError("DeepSeek response was not an object")
    return parsed


def _load_preflight_fixture(path: str | Path) -> dict[str, Any]:
    payload = _load_json(path)
    if (
        payload.get("schema_version") != FIXTURE_SCHEMA
        or payload.get("model_id") != MODEL_ID
        or payload.get("execution_authority") != "ABSENT"
        or not isinstance(payload.get("opportunity_id"), str)
        or not isinstance(payload.get("features"), Mapping)
        or not isinstance(payload.get("response"), Mapping)
    ):
        raise DeepSeekPreflightError("DeepSeek preflight fixture is unusable")
    return payload


def _fixture_packet(fixture: Mapping[str, Any]) -> dict[str, Any]:
    exogenous = {key: 0 for key in feature_keys()}
    return build_packet(
        arm=D_ATHENA,
        opportunity_id=str(fixture["opportunity_id"]),
        features=dict(fixture["features"]),
        actor_identity="deepseek-flash-unregistered-preflight",
        exogenous_features=exogenous,
    )


def _preflight_arm_sequence(*, include_deliberation: bool) -> tuple[str, ...]:
    arms: list[str] = list(DEFAULT_PREFLIGHT_ARMS)
    if include_deliberation:
        arms.extend(E_DELIBERATION for _ in range(DELIBERATION_TURNS))
    for arm in arms:
        if arm not in ACTOR_ARMS:
            raise DeepSeekPreflightError(f"preflight cannot call arm {arm}")
    return tuple(arms)


def _preflight_worst_case_usd(
    requests: Sequence[Mapping[str, Any]], peak: Mapping[str, Any],
) -> float:
    input_ceiling = sum(
        len(json.dumps(item["payload"], sort_keys=True, separators=(",", ":")).encode("utf-8"))
        for item in requests
    )
    output_ceiling = sum(int(item["max_output_tokens"]) for item in requests)
    return _cost_usd(input_ceiling, output_ceiling, peak)


def _read_usage(response: Mapping[str, Any]) -> tuple[int, int]:
    usage = response.get("usage")
    if not isinstance(usage, Mapping):
        raise DeepSeekPreflightError("DeepSeek response lacks usage evidence")
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        raise DeepSeekPreflightError("DeepSeek usage evidence is malformed")
    if prompt_tokens < 0 or completion_tokens < 0:
        raise DeepSeekPreflightError("DeepSeek usage evidence is malformed")
    return prompt_tokens, completion_tokens


def _read_decision(response: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    if response.get("model") != MODEL_ID:
        return None, "MODEL_IDENTITY_DRIFT"
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, "RESPONSE_NOT_COMPLETED"
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
        return None, "RESPONSE_NOT_COMPLETED"
    try:
        decision = json.loads(message["content"])
    except json.JSONDecodeError:
        return None, "Actor output is not JSON"
    required = {"action", "confidence", "horizon", "invalidation", "rationale"}
    if not isinstance(decision, Mapping) or set(decision) != required:
        return None, "Actor output schema mismatch"
    if decision["action"] not in LEGAL_ACTIONS:
        return None, "Actor action is not allowed"
    if (
        not isinstance(decision["confidence"], (int, float))
        or not 0 <= decision["confidence"] <= 1
    ):
        return None, "Actor confidence is invalid"
    if not all(
        isinstance(decision[key], str) and decision[key]
        for key in ("horizon", "invalidation", "rationale")
    ):
        return None, "Actor text fields are invalid"
    return dict(decision), None


def _cost_usd(input_tokens: int, output_tokens: int, rates: Mapping[str, Any]) -> float:
    return (
        input_tokens * float(rates["input_usd_per_million_tokens"])
        + output_tokens * float(rates["output_usd_per_million_tokens"])
    ) / 1_000_000


def _require_key(key: str) -> None:
    if not isinstance(key, str) or not key.strip():
        raise MissingDeepSeekCredentialsError(
            "DEEPSEEK_API_KEY is required for a DeepSeek Flash preflight"
        )


def _require_bound_hash(payload: Mapping[str, Any], field: str) -> None:
    body = dict(payload)
    claimed = body.pop(field, None)
    if not isinstance(claimed, str) or claimed != canonical_hash(body):
        raise DeepSeekPreflightError(f"{field} does not match its contents")


def _assert_absent_authority(payload: Mapping[str, Any]) -> None:
    if payload.get("execution_authority") != "ABSENT":
        raise DeepSeekPreflightError("execution authority must remain ABSENT")


def _assert_no_secret(payload: Mapping[str, Any], api_key: str) -> None:
    dumped = json.dumps(payload)
    if api_key and api_key in dumped:
        raise DeepSeekPreflightError("refusing to serialize a DeepSeek credential")


def _load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    _forbid_holdout_path(target)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeepSeekPreflightError("DeepSeek planner input is unreadable") from exc
    if not isinstance(value, dict):
        raise DeepSeekPreflightError("DeepSeek planner input is not an object")
    return value


def forbid_output_path(path: str | Path) -> Path:
    """Refuse overwrite of registered artifacts, holdouts, and existing files."""
    target = Path(path)
    _forbid_holdout_path(target)
    _forbid_protected_write_target(target, writing=True)
    if target.exists():
        raise DeepSeekPreflightError("refusing to overwrite existing output")
    return target


def _forbid_holdout_path(path: Path) -> None:
    text = str(path).lower()
    if any(token in text for token in _FORBIDDEN_PATH_TOKENS):
        raise DeepSeekPreflightError("holdout paths are forbidden")


def _forbid_protected_write_target(path: Path, *, writing: bool) -> None:
    if not writing:
        return
    name = path.name.lower()
    if name in _PROTECTED_ARTIFACTS:
        raise DeepSeekPreflightError("refusing to write a registered or consumed artifact")
