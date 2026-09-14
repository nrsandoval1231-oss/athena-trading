import importlib.util
import json
from pathlib import Path

import pytest

from athena_trading.aat_v1.providers import registered_providers
from athena_trading.aat_v2.deepseek_pilot import (
    DEFAULT_PILOT_CALL_CEILING,
    DEFAULT_PILOT_OPPORTUNITIES,
    DEFAULT_PILOT_SPEND_CEILING_USD,
    PILOT_CALL_CEILING,
    PILOT_OPPORTUNITY_CEILING,
    PILOT_SPEND_CEILING_USD,
    PILOT_STATUS,
    RECOMMENDED_LIVE_CALL_CEILING,
    RECOMMENDED_LIVE_OPPORTUNITIES,
    RECOMMENDED_LIVE_SPEND_CEILING_USD,
    DeepSeekPilotError,
    run_deepseek_flash_pilot,
)
from athena_trading.aat_v2.deepseek_preflight import (
    RESPONSE_FORMAT,
    SINGLE_CALL_OUTPUT_TOKENS,
    THINKING,
    MissingDeepSeekCredentialsError,
    _live_transport,
)
from athena_trading.aat_v2.deliberation import DELIBERATION_OUTPUT_TOKENS, DELIBERATION_PROMPT
from athena_trading.aat_v2.identity import PILOT_PROMPT, declared_deepseek_flash_identity


ROOT = Path(__file__).parents[1]
PRICING = ROOT / "configs/deepseek_flash_pricing_2026-09-14.json"
PROTOCOL = ROOT / "configs/aat_v2_registered_protocol_v1.json"
AMENDMENT = ROOT / "configs/aat_v2_e_arm_compact_schema_amendment_v1.json"
IDENTITY = ROOT / "configs/aat_v2_deepseek_flash_actor_identity_v1.json"
FIXTURE = ROOT / "configs/fixtures/deepseek_flash_preflight_v1.json"
SCRIPT = ROOT / "scripts/run_aat_v2_deepseek_flash_pilot.py"
FIXTURE_KEY = "fixture-key-not-serialized"
IDENTITY_HASH = declared_deepseek_flash_identity()["identity_hash"]


def _script():
    spec = importlib.util.spec_from_file_location("run_aat_v2_deepseek_flash_pilot", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fixture_transport(payload, key):
    from athena_trading.aat_v2 import deepseek_preflight as deepseek_mod

    assert key == FIXTURE_KEY
    assert payload["model"] == "deepseek-flash"
    assert payload["thinking"] == THINKING
    assert payload["response_format"] == RESPONSE_FORMAT
    return deepseek_mod.fixture_transport(FIXTURE)(payload, key)


def test_fixture_pilot_accepts_compact_e_and_actor_json():
    seen = []

    def transport(payload, key):
        seen.append(payload)
        return _fixture_transport(payload, key)

    result = run_deepseek_flash_pilot(
        fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
        identity_path=IDENTITY, amendment_path=AMENDMENT,
        opportunity_count=1, call_ceiling=7, spend_ceiling_usd=1.00,
        api_key=FIXTURE_KEY, transport=transport, include_deliberation=True,
    )
    assert result["status"] == PILOT_STATUS == "PILOT_NOT_TRAINING"
    assert result["execution_authority"] == "ABSENT"
    assert result["execution_authorized"] is False
    assert result["claim_authorized"] is False
    assert result["sealed_holdout_consumed"] is False
    assert result["live_calls"] is False
    assert result["dry_run"] is True
    assert result["terra_protocol_not_reused"] is True
    assert result["pilot_is_not_training"] is True
    assert result["actor_identity_status"] == "CANDIDATE_PILOT"
    assert result["actor_identity_hash"] == IDENTITY_HASH
    assert result["request_count"] == 7
    assert result["accepted_count"] == 7
    assert result["rejected_count"] == 0
    assert result["opportunity_count"] == 1
    assert [payload["max_tokens"] for payload in seen] == [512, 512, 512, 64, 64, 64, 64]
    assert all(payload["thinking"] == THINKING for payload in seen)
    actor_payloads = seen[:3]
    e_payloads = seen[3:]
    assert all(payload["messages"][0]["content"] == PILOT_PROMPT for payload in actor_payloads)
    assert all(payload["messages"][0]["content"] == DELIBERATION_PROMPT for payload in e_payloads)
    assert "PILOT_NOT_TRAINING" in PILOT_PROMPT
    e_rows = [row for row in result["records"] if row["arm"] == "E_DELIBERATION"]
    actor_rows = [row for row in result["records"] if row["arm"] != "E_DELIBERATION"]
    assert [row["turn"] for row in e_rows] == ["BULL", "BEAR", "TRADER", "RISK"]
    assert all(row["accepted"] for row in e_rows)
    assert all(set(row["decision"]) == {
        "action", "confidence", "horizon", "invalidation", "rationale",
    } for row in actor_rows)
    assert e_rows[-1]["decision"]["action"] == "PASS"
    assert all(row["actor_identity_hash"] == IDENTITY_HASH for row in result["records"])
    assert FIXTURE_KEY not in json.dumps(result)
    assert SINGLE_CALL_OUTPUT_TOKENS == 512
    assert DELIBERATION_OUTPUT_TOKENS == 64


def test_two_opportunity_fixture_pilot_stays_absent():
    result = run_deepseek_flash_pilot(
        fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
        identity_path=IDENTITY, amendment_path=AMENDMENT,
        opportunity_count=2, call_ceiling=14, spend_ceiling_usd=1.00,
        api_key=FIXTURE_KEY, transport=_fixture_transport, include_deliberation=True,
    )
    assert result["opportunity_count"] == 2
    assert result["request_count"] == 14
    assert result["accepted_count"] == 14
    assert result["status"] == "PILOT_NOT_TRAINING"
    assert result["execution_authority"] == "ABSENT"
    ids = {row["opportunity_id"] for row in result["records"]}
    assert ids == {"preflight-fixture-1-1", "preflight-fixture-1-2"}


def test_pilot_without_allow_live_never_opens_a_network_transport():
    with pytest.raises(DeepSeekPilotError, match="not authorized"):
        run_deepseek_flash_pilot(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            identity_path=IDENTITY, amendment_path=AMENDMENT,
            opportunity_count=1, call_ceiling=7, spend_ceiling_usd=1.00,
            api_key=FIXTURE_KEY,
        )


def test_injected_live_transport_still_requires_allow_live():
    with pytest.raises(DeepSeekPilotError, match="not authorized"):
        run_deepseek_flash_pilot(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            identity_path=IDENTITY, amendment_path=AMENDMENT,
            opportunity_count=1, call_ceiling=7, spend_ceiling_usd=1.00,
            api_key=FIXTURE_KEY, transport=_live_transport,
        )


def test_allow_live_without_a_key_fails_closed(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(MissingDeepSeekCredentialsError, match="DEEPSEEK_API_KEY"):
        run_deepseek_flash_pilot(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            identity_path=IDENTITY, amendment_path=AMENDMENT,
            opportunity_count=1, call_ceiling=7, spend_ceiling_usd=1.00,
            allow_live=True,
        )


def test_missing_api_key_fails_closed_before_pilot(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    called = False

    def transport(payload, key):
        nonlocal called
        called = True
        return _fixture_transport(payload, key)

    with pytest.raises(MissingDeepSeekCredentialsError, match="DEEPSEEK_API_KEY"):
        run_deepseek_flash_pilot(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            identity_path=IDENTITY, amendment_path=AMENDMENT,
            opportunity_count=1, call_ceiling=7, spend_ceiling_usd=1.00,
            transport=transport,
        )
    assert called is False


def test_pilot_refuses_training_sized_ceilings():
    with pytest.raises(DeepSeekPilotError, match="call ceiling"):
        run_deepseek_flash_pilot(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            identity_path=IDENTITY, amendment_path=AMENDMENT,
            opportunity_count=1,             call_ceiling=116935, spend_ceiling_usd=1.00,
            api_key=FIXTURE_KEY, transport=_fixture_transport,
        )
    with pytest.raises(DeepSeekPilotError, match="call ceiling"):
        run_deepseek_flash_pilot(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            identity_path=IDENTITY, amendment_path=AMENDMENT,
            opportunity_count=1, call_ceiling=51, spend_ceiling_usd=5.00,
            api_key=FIXTURE_KEY, transport=_fixture_transport,
        )
    with pytest.raises(DeepSeekPilotError, match="spend ceiling"):
        run_deepseek_flash_pilot(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            identity_path=IDENTITY, amendment_path=AMENDMENT,
            opportunity_count=1, call_ceiling=7, spend_ceiling_usd=5.01,
            api_key=FIXTURE_KEY, transport=_fixture_transport,
        )
    with pytest.raises(DeepSeekPilotError, match="opportunity count"):
        run_deepseek_flash_pilot(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            identity_path=IDENTITY, amendment_path=AMENDMENT,
            opportunity_count=16705, call_ceiling=7, spend_ceiling_usd=1.00,
            api_key=FIXTURE_KEY, transport=_fixture_transport,
        )


def test_pilot_refuses_a_call_ceiling_below_the_arm_sequence():
    with pytest.raises(DeepSeekPilotError, match="call ceiling"):
        run_deepseek_flash_pilot(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            identity_path=IDENTITY, amendment_path=AMENDMENT,
            opportunity_count=2, call_ceiling=7, spend_ceiling_usd=1.00,
            api_key=FIXTURE_KEY, transport=_fixture_transport,
        )


def test_e_arm_pilot_requires_the_non_authorizing_amendment():
    with pytest.raises(DeepSeekPilotError, match="compact-schema amendment"):
        run_deepseek_flash_pilot(
            fixture_path=FIXTURE, pricing_path=PRICING, protocol_path=PROTOCOL,
            identity_path=IDENTITY, amendment_path=None,
            opportunity_count=1, call_ceiling=7, spend_ceiling_usd=1.00,
            api_key=FIXTURE_KEY, transport=_fixture_transport,
            include_deliberation=True,
        )


def test_holdout_fixture_paths_are_forbidden(tmp_path):
    bad = tmp_path / "sealed_holdout.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(DeepSeekPilotError, match="holdout"):
        run_deepseek_flash_pilot(
            fixture_path=bad, pricing_path=PRICING, protocol_path=PROTOCOL,
            identity_path=IDENTITY, amendment_path=AMENDMENT,
            opportunity_count=1, call_ceiling=7, spend_ceiling_usd=1.00,
            api_key=FIXTURE_KEY, transport=_fixture_transport,
        )


def test_recommended_knobs_stay_inside_hard_caps():
    assert DEFAULT_PILOT_OPPORTUNITIES == 1
    assert DEFAULT_PILOT_CALL_CEILING == 7
    assert DEFAULT_PILOT_SPEND_CEILING_USD == 1.00
    assert RECOMMENDED_LIVE_OPPORTUNITIES == 7
    assert RECOMMENDED_LIVE_CALL_CEILING == 50
    assert RECOMMENDED_LIVE_SPEND_CEILING_USD == 5.00
    assert RECOMMENDED_LIVE_OPPORTUNITIES <= PILOT_OPPORTUNITY_CEILING == 20
    assert RECOMMENDED_LIVE_CALL_CEILING <= PILOT_CALL_CEILING == 50
    assert RECOMMENDED_LIVE_SPEND_CEILING_USD <= PILOT_SPEND_CEILING_USD == 5.00
    assert registered_providers() == ("openai",)


def test_cli_default_is_fixture_dry(monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FIXTURE_KEY)
    code = _script().main([])
    assert code == 0
    captured = capsys.readouterr().out
    payload = json.loads(captured)
    assert payload["ok"] is True
    assert payload["status"] == "PILOT_NOT_TRAINING"
    assert payload["live_calls"] is False
    assert payload["dry_run"] is True
    assert payload["execution_authority"] == "ABSENT"
    assert payload["request_count"] == 7
    assert payload["accepted_count"] == 7
    assert payload["actor_identity_hash"] == IDENTITY_HASH
    assert FIXTURE_KEY not in captured


def test_cli_fails_closed_without_key(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    code = _script().main([])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "DEEPSEEK_API_KEY" in payload["error"]
    assert payload["execution_authority"] == "ABSENT"
    assert payload["sealed_holdout_consumed"] is False


def test_cli_allow_live_requires_explicit_ceilings(monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", FIXTURE_KEY)
    code = _script().main(["--allow-live"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "explicit --call-ceiling" in payload["error"]
    assert payload["live_calls"] is False
    assert payload["execution_authority"] == "ABSENT"


def test_cli_allow_live_without_key_fails_closed(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    code = _script().main([
        "--allow-live", "--call-ceiling", "7", "--spend-ceiling-usd", "1",
    ])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "DEEPSEEK_API_KEY" in payload["error"]
    assert payload["live_calls"] is False
