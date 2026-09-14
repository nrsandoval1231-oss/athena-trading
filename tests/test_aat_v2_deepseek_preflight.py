import importlib.util
import json
from pathlib import Path

import pytest

from athena_trading.athena_adapter.contracts import canonical_hash
from athena_trading.aat_v1.providers import registered_providers
from athena_trading.aat_v2.deepseek_preflight import (
    MODEL_ID,
    PREFLIGHT_CALL_CEILING,
    DeepSeekPreflightError,
    MissingDeepSeekCredentialsError,
    api_key_from_env,
    forbid_output_path,
    load_macro_only_training_plan,
    plan_deepseek_flash_dry_run,
    run_deepseek_flash_preflight,
)
from athena_trading.aat_v2 import deepseek_preflight as deepseek_mod


ROOT = Path(__file__).parents[1]
PLAN = ROOT / "configs/aat_v2_training_plan_v2.json"
PRICING = ROOT / "configs/deepseek_flash_pricing_2026-09-14.json"
PROTOCOL = ROOT / "configs/aat_v2_registered_protocol_v1.json"
FIXTURE = ROOT / "configs/fixtures/deepseek_flash_preflight_v1.json"
SCRIPT = ROOT / "scripts/plan_aat_v2_deepseek_flash.py"
FIXTURE_KEY = "fixture-key-not-serialized"


def _script():
    spec = importlib.util.spec_from_file_location("plan_aat_v2_deepseek_flash", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _plan():
    return plan_deepseek_flash_dry_run(
        training_plan_path=PLAN, pricing_path=PRICING, protocol_path=PROTOCOL,
    )


def _fixture_transport(payload, key):
    assert key == FIXTURE_KEY
    assert payload["model"] == MODEL_ID
    assert payload["stream"] is False
    envelope = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return envelope["response"]


def test_dry_run_matches_the_documented_deepseek_estimate():
    plan = _plan()
    assert plan["status"] == "DRY_RUN_NOT_TRAINING"
    assert plan["dry_run"] is True
    assert plan["execution_authorized"] is False
    assert plan["claim_authorized"] is False
    assert plan["sealed_holdout_consumed"] is False
    assert plan["execution_authority"] == "ABSENT"
    assert plan["live_calls"] is False
    assert plan["actor_called"] is False
    assert plan["terra_protocol_not_reused"] is True
    assert plan["actor_identity_status"] == "UNREGISTERED_CANDIDATE"
    assert plan["planned_calls"] == 116935
    assert round(plan["off_peak_cost_usd"], 2) == 32.68
    assert round(plan["peak_cost_usd"], 2) == 65.35
    assert plan["worst_case_cost_usd"] == plan["peak_cost_usd"]
    assert plan["source_document"] == "docs/AAT_V2_MODEL_EXECUTION_OPTIONS.md"


def test_dry_run_does_not_require_a_deepseek_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    plan = _plan()
    assert plan["execution_authority"] == "ABSENT"


def test_superseded_v1_plan_is_refused():
    with pytest.raises(DeepSeekPreflightError, match="corrected v2"):
        load_macro_only_training_plan(ROOT / "configs/aat_v2_training_plan_v1.json")


def test_tampered_training_plan_hash_is_refused(tmp_path):
    payload = json.loads(PLAN.read_text(encoding="utf-8"))
    payload["output_token_ceiling"] = 1
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DeepSeekPreflightError, match="plan_hash"):
        plan_deepseek_flash_dry_run(
            training_plan_path=path, pricing_path=PRICING, protocol_path=PROTOCOL,
        )


def test_non_absent_authority_is_refused(tmp_path):
    payload = json.loads(PLAN.read_text(encoding="utf-8"))
    payload["execution_authority"] = "PAPER"
    payload.pop("plan_hash")
    payload["plan_hash"] = canonical_hash(payload)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DeepSeekPreflightError, match="ABSENT"):
        plan_deepseek_flash_dry_run(
            training_plan_path=path, pricing_path=PRICING, protocol_path=PROTOCOL,
        )


def test_deepseek_cannot_reuse_the_terra_protocol_identity(tmp_path):
    payload = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    payload["protocol"]["actor_identity"]["provider"] = "deepseek"
    payload["protocol"]["actor_identity"]["model_id"] = MODEL_ID
    body = dict(payload)
    body.pop("protocol_hash")
    payload["protocol_hash"] = canonical_hash(body)
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DeepSeekPreflightError, match="Terra"):
        plan_deepseek_flash_dry_run(
            training_plan_path=PLAN, pricing_path=PRICING, protocol_path=path,
        )


def test_missing_api_key_fails_closed_before_preflight(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(MissingDeepSeekCredentialsError, match="DEEPSEEK_API_KEY"):
        api_key_from_env()
    called = False

    def transport(payload, key):
        nonlocal called
        called = True
        return _fixture_transport(payload, key)

    with pytest.raises(MissingDeepSeekCredentialsError, match="DEEPSEEK_API_KEY"):
        run_deepseek_flash_preflight(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            call_ceiling=3, spend_ceiling_usd=0.05, transport=transport,
        )
    assert called is False


def test_fixture_preflight_stays_absent_and_does_not_consume_a_holdout():
    result = run_deepseek_flash_preflight(
        fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
        call_ceiling=3, spend_ceiling_usd=0.05, api_key=FIXTURE_KEY,
        transport=_fixture_transport,
    )
    assert result["status"] == "PREFLIGHT_NOT_TRAINING"
    assert result["request_count"] == 3
    assert result["accepted_count"] == 3
    assert result["execution_authority"] == "ABSENT"
    assert result["sealed_holdout_consumed"] is False
    assert result["execution_authorized"] is False
    assert result["live_calls"] is False
    assert result["terra_protocol_not_reused"] is True
    assert {row["arm"] for row in result["records"]} == {
        "B_MEMORYLESS", "C_NAIVE_MEMORY", "D_ATHENA",
    }
    dumped = json.dumps(result)
    assert FIXTURE_KEY not in dumped
    assert all(row["execution_authority"] == "ABSENT" for row in result["records"])


def test_preflight_refuses_a_training_sized_call_ceiling():
    with pytest.raises(DeepSeekPreflightError, match="call ceiling"):
        run_deepseek_flash_preflight(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            call_ceiling=116935, spend_ceiling_usd=0.05, api_key=FIXTURE_KEY,
            transport=_fixture_transport,
        )


def test_preflight_refuses_spend_above_the_tiny_ceiling():
    with pytest.raises(DeepSeekPreflightError, match="spend ceiling"):
        run_deepseek_flash_preflight(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            call_ceiling=3, spend_ceiling_usd=1.00, api_key=FIXTURE_KEY,
            transport=_fixture_transport,
        )


def test_preflight_without_allow_live_never_opens_a_network_transport():
    with pytest.raises(DeepSeekPreflightError, match="not authorized"):
        run_deepseek_flash_preflight(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            call_ceiling=3, spend_ceiling_usd=0.05, api_key=FIXTURE_KEY,
        )


def test_injected_live_transport_still_requires_allow_live():
    with pytest.raises(DeepSeekPreflightError, match="not authorized"):
        run_deepseek_flash_preflight(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            call_ceiling=3, spend_ceiling_usd=0.05, api_key=FIXTURE_KEY,
            transport=deepseek_mod._live_transport,
        )


def test_allow_live_without_a_key_fails_closed(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(MissingDeepSeekCredentialsError, match="DEEPSEEK_API_KEY"):
        run_deepseek_flash_preflight(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            call_ceiling=3, spend_ceiling_usd=0.05, allow_live=True,
        )


def test_holdout_paths_are_forbidden(tmp_path):
    bad = tmp_path / "sealed_holdout.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(DeepSeekPreflightError, match="holdout"):
        plan_deepseek_flash_dry_run(
            training_plan_path=bad, pricing_path=PRICING, protocol_path=PROTOCOL,
        )


def test_output_cannot_overwrite_the_consumed_holdout_registry(tmp_path):
    with pytest.raises(DeepSeekPreflightError, match="holdout"):
        forbid_output_path(ROOT / "configs/aat_consumed_holdouts_v1.json")
    with pytest.raises(DeepSeekPreflightError, match="registered or consumed"):
        forbid_output_path(ROOT / "configs/aat_v2_registered_protocol_v1.json")
    existing = tmp_path / "out.json"
    existing.write_text("{}\n", encoding="utf-8")
    with pytest.raises(DeepSeekPreflightError, match="overwrite"):
        forbid_output_path(existing)


def test_invalid_decisions_are_rejected_not_accepted():
    envelope = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = (
        ({"action": "NOPE", "confidence": 0.5, "horizon": "H", "invalidation": "I",
          "rationale": "R"}, "Actor action is not allowed"),
        ({"action": "PASS", "confidence": "high", "horizon": "H", "invalidation": "I",
          "rationale": "R"}, "Actor confidence is invalid"),
        ({"action": "PASS", "confidence": 1.5, "horizon": "H", "invalidation": "I",
          "rationale": "R"}, "Actor confidence is invalid"),
        ({"action": "PASS", "confidence": 0.5, "horizon": "", "invalidation": "I",
          "rationale": "R"}, "Actor text fields are invalid"),
    )

    for decision, reason in cases:
        response = json.loads(json.dumps(envelope["response"]))
        response["choices"][0]["message"]["content"] = json.dumps(decision)

        def transport(payload, key, *, _response=response):
            return _response

        result = run_deepseek_flash_preflight(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            call_ceiling=3, spend_ceiling_usd=0.05, api_key=FIXTURE_KEY,
            transport=transport,
        )
        assert result["accepted_count"] == 0
        assert result["rejected_count"] == 3
        assert result["records"][0]["rejection_reason"] == reason


def test_deepseek_is_not_registered_as_a_v1_provider():
    assert registered_providers() == ("openai",)
    assert PREFLIGHT_CALL_CEILING == 7
    assert registered_providers() == ("openai",)
    assert PREFLIGHT_CALL_CEILING == 7


def test_documented_plan_only_command(capsys):
    code = _script().main([])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["execution_authority"] == "ABSENT"
    assert payload["live_calls"] is False


def test_documented_preflight_fails_closed_without_key(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    code = _script().main(["--preflight"])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "DEEPSEEK_API_KEY" in payload["error"]
    assert payload["execution_authority"] == "ABSENT"
    assert payload["sealed_holdout_consumed"] is False


def test_documented_preflight_with_key_uses_the_fixture_not_the_network(
    monkeypatch, capsys,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FIXTURE_KEY)
    code = _script().main(["--preflight"])
    assert code == 0
    captured = capsys.readouterr().out
    payload = json.loads(captured)
    assert payload["ok"] is True
    assert payload["status"] == "PREFLIGHT_NOT_TRAINING"
    assert payload["live_calls"] is False
    assert payload["execution_authority"] == "ABSENT"
    assert payload["request_count"] == 3
    assert FIXTURE_KEY not in captured
