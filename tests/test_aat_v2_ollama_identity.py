"""Local Ollama / Qwen is a hashed V2 TRAINING candidate, not a Terra substitute."""
import json
from pathlib import Path

import pytest

from athena_trading.aat_v1.actor_canary import ACTION_SCHEMA, INFERENCE, PROMPT
from athena_trading.aat_v1.providers import OLLAMA, OPENAI, provider_identity, registered_providers
from athena_trading.aat_v2.deliberation import deliberation_plan
from athena_trading.aat_v2.identity import (
    TERRA_INFERENCE_HASH,
    TERRA_MODEL_HASH,
    TERRA_MODEL_ID,
    TERRA_PROMPT_HASH,
    TERRA_PROTOCOL_HASH,
)
from athena_trading.aat_v2.ollama_identity import (
    AUTHORIZATION_STATUS,
    CLI_AUTHORIZATION_FLAG,
    DEFAULT_MODEL_ID,
    IDENTITY_STATUS,
    PROVIDER,
    RUNPOD_CLI_AUTHORIZATION_FLAG,
    RUNPOD_IDENTITY_STATUS,
    RUNPOD_MODEL_ID,
    RUNPOD_TRAINING_INFERENCE,
    RUNPOD_TRAINING_PROMPT,
    TRAINING_INFERENCE,
    TRAINING_PROMPT,
    AuthorizationError,
    IdentityError,
    assert_full_run_authorized,
    declared_local_training_authorization,
    declared_ollama_qwen_identity,
    declared_ollama_runpod_identity,
    declared_runpod_training_authorization,
    load_local_training_authorization,
    load_ollama_qwen_identity,
    load_ollama_runpod_identity,
    load_runpod_training_authorization,
)
from athena_trading.athena_adapter.contracts import canonical_hash


ROOT = Path(__file__).parents[1]
IDENTITY = ROOT / "configs/aat_v2_ollama_qwen_actor_identity_v1.json"
AUTHORIZATION = ROOT / "configs/aat_v2_ollama_local_training_authorization_v1.json"
RUNPOD_IDENTITY = ROOT / "configs/aat_v2_ollama_qwen_14b_runpod_actor_identity_v1.json"
RUNPOD_AUTHORIZATION = ROOT / "configs/aat_v2_ollama_runpod_training_authorization_v1.json"
PROTOCOL = ROOT / "configs/aat_v2_registered_protocol_v1.json"
TERRA_IDENTITY = ROOT / "configs/aat_v2_actor_identity_v1.json"
DEEPSEEK_IDENTITY = ROOT / "configs/aat_v2_deepseek_flash_actor_identity_v1.json"

OLLAMA_MODEL_HASH = "ff4bbdf6b84f4e40806630bcbb5a507e4770d72a4456e3f94cb22b2409c36f1a"
OLLAMA_PROMPT_HASH = "fb00f2c6ba3b6484450db2a1476bdfbcbe0b66c01a0bf5b88faa1a03b1869aa4"
OLLAMA_INFERENCE_HASH = "5c8c8eadeaed77446a8b60f150995b760bb171bca794fd5949c7d2bc3aba384c"
OLLAMA_IDENTITY_HASH = "343df3a40ed2c8f521085afde9caaadf611889f6eb5872e8fb0bc0d9123745de"
OLLAMA_AUTHORIZATION_HASH = "bd95a58bc20e136a4a6e50098885623ca035157139509117635074cb2f7c9218"
RUNPOD_MODEL_HASH = "19204b642392edcb03cb727d8aeed3bd66ce3155b8a578104d5070ee98961a5f"
RUNPOD_PROMPT_HASH = "107876a12794b1314d5d7dd87a65d1e35f88462abc11b7554708864ba762b6d3"
RUNPOD_INFERENCE_HASH = "2929d0e27a92d85bae12aa4731203bebc2f9cd1ad31035a88ae99bcfaf3ef2f1"
RUNPOD_IDENTITY_HASH = "4f9178080ae3d0cf04ff296d6e2f5e259233c36e75ce79448b751d249af6966f"
RUNPOD_AUTHORIZATION_HASH = "6af1733ef877658ee8b9227312f1c9724aa673a0642fd8e7a908342f308251a4"
SHARED_ACTION_SCHEMA_HASH = "852c32a80b5f4187ea6629270bfaf14162779e1a742690716a337eb107a26b3c"


def test_declared_identity_is_provider_scoped_and_not_terra():
    identity = declared_ollama_qwen_identity()
    terra = provider_identity(
        OPENAI, model_id=TERRA_MODEL_ID, prompt=PROMPT, inference=INFERENCE,
        action_schema=ACTION_SCHEMA,
    )
    assert identity["provider"] == PROVIDER == "ollama"
    assert identity["model_id"] == DEFAULT_MODEL_ID == "qwen3.5:9b"
    assert identity["identity_status"] == IDENTITY_STATUS == "LOCAL_TRAINING_CANDIDATE"
    assert identity["bound_to_registered_protocol"] is False
    assert identity["rewrites_registered_protocol"] is False
    assert identity["execution_authorized"] is False
    assert identity["claim_authorized"] is False
    assert identity["execution_authority"] == "ABSENT"
    assert identity["sealed_holdout_consumed"] is False
    assert identity["local_training_requires_explicit_authorization"] is True
    assert identity["authorization_cli_flag"] == CLI_AUTHORIZATION_FLAG
    assert identity["terra_protocol_not_reused"] is True
    assert identity["deepseek_identity_not_reused"] is True
    assert identity["runtime_model_swap_forbidden"] is True
    assert identity["consulted_terra_protocol_hash"] == TERRA_PROTOCOL_HASH
    assert identity["model_hash"] == OLLAMA_MODEL_HASH
    assert identity["prompt_hash"] == OLLAMA_PROMPT_HASH
    assert identity["inference_hash"] == OLLAMA_INFERENCE_HASH
    assert identity["identity_hash"] == OLLAMA_IDENTITY_HASH
    assert identity["model_hash"] != terra["model_hash"] == TERRA_MODEL_HASH
    assert identity["prompt_hash"] != terra["prompt_hash"] == TERRA_PROMPT_HASH
    assert identity["inference_hash"] != terra["inference_hash"] == TERRA_INFERENCE_HASH
    assert identity["action_schema_hash"] == terra["action_schema_hash"] == SHARED_ACTION_SCHEMA_HASH
    assert identity["deliberation_hash"] == deliberation_plan()["deliberation_hash"]
    assert identity["inference"] == TRAINING_INFERENCE
    assert identity["prompt"] == TRAINING_PROMPT
    assert identity["inference"]["think"] is False
    assert identity["inference"]["max_output_tokens"] == 256
    assert identity["inference"]["base_url"] == "http://127.0.0.1:11434"


def test_committed_identity_matches_declaration_and_consults_terra():
    loaded = load_ollama_qwen_identity(IDENTITY, protocol_path=PROTOCOL)
    declared = declared_ollama_qwen_identity()
    assert loaded == declared
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    terra = json.loads(TERRA_IDENTITY.read_text(encoding="utf-8"))
    deepseek = json.loads(DEEPSEEK_IDENTITY.read_text(encoding="utf-8"))
    assert protocol["protocol_hash"] == TERRA_PROTOCOL_HASH
    assert protocol["protocol"]["actor_identity"]["provider"] == "openai"
    assert protocol["protocol"]["registration_status"] == "REGISTERED_NOT_AUTHORIZED"
    assert terra["provider"] == "openai"
    assert loaded["provider"] != terra["provider"]
    assert loaded["provider"] != deepseek["provider"]
    assert loaded["model_hash"] != terra["model_hash"]
    assert loaded["identity_hash"] != deepseek["identity_hash"]
    assert registered_providers() == ("openai",)
    assert OLLAMA.model_hash(DEFAULT_MODEL_ID) == loaded["model_hash"]


def test_authorization_artifact_requires_the_cli_flag_and_does_not_flip_terra():
    identity = load_ollama_qwen_identity(IDENTITY, protocol_path=PROTOCOL)
    auth = load_local_training_authorization(
        AUTHORIZATION, identity=identity, protocol_path=PROTOCOL,
    )
    assert auth == declared_local_training_authorization(identity)
    assert auth["authorization_hash"] == OLLAMA_AUTHORIZATION_HASH
    assert auth["authorization_status"] == AUTHORIZATION_STATUS
    assert auth["nick_authorized_plumbing"] is True
    assert auth["nick_authorized_local_training_execution"] is True
    assert auth["execution_requires_cli_flag"] is True
    assert auth["execution_authorized"] is False
    assert auth["authorizes_terra_protocol"] is False
    assert auth["rewrites_registered_protocol"] is False
    assert auth["execution_authority"] == "ABSENT"
    assert auth["sealed_holdout_consumed"] is False
    assert auth["actor_identity_hash"] == OLLAMA_IDENTITY_HASH


def test_identity_hash_tamper_is_refused(tmp_path):
    payload = json.loads(IDENTITY.read_text(encoding="utf-8"))
    payload["prompt"] = "not the declared prompt"
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IdentityError, match="identity_hash"):
        load_ollama_qwen_identity(path, protocol_path=PROTOCOL)


def test_runtime_model_swap_is_refused():
    with pytest.raises(IdentityError, match="model id"):
        load_ollama_qwen_identity(
            IDENTITY, protocol_path=PROTOCOL, expected_model_id="qwen3-coder:30b",
        )


def test_previous_30b_tag_is_a_different_actor():
    previous = declared_ollama_qwen_identity(model_id="qwen3-coder:30b")
    current = declared_ollama_qwen_identity()
    assert previous["model_id"] == "qwen3-coder:30b"
    assert current["model_id"] == "qwen3.5:9b"
    assert previous["identity_hash"] != current["identity_hash"] == OLLAMA_IDENTITY_HASH
    assert previous["model_hash"] != current["model_hash"]


def test_identity_refuses_a_terra_protocol_swap(tmp_path):
    payload = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    payload["protocol"]["actor_identity"]["provider"] = "ollama"
    payload["protocol"]["actor_identity"]["model_id"] = DEFAULT_MODEL_ID
    body = dict(payload)
    body.pop("protocol_hash")
    payload["protocol_hash"] = canonical_hash(body)
    path = tmp_path / "protocol.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IdentityError, match="Terra"):
        load_ollama_qwen_identity(IDENTITY, protocol_path=path)


def test_holdout_identity_paths_are_forbidden(tmp_path):
    bad = tmp_path / "sealed_holdout_identity.json"
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(IdentityError, match="holdout"):
        load_ollama_qwen_identity(bad, protocol_path=PROTOCOL)


def test_authorization_refuses_a_silent_execution_flip(tmp_path):
    identity = load_ollama_qwen_identity(IDENTITY, protocol_path=PROTOCOL)
    payload = json.loads(AUTHORIZATION.read_text(encoding="utf-8"))
    payload["execution_authorized"] = True
    body = dict(payload)
    body.pop("authorization_hash")
    payload["authorization_hash"] = canonical_hash(body)
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AuthorizationError, match="drifted"):
        load_local_training_authorization(path, identity=identity, protocol_path=PROTOCOL)


def test_runpod_14b_identity_is_a_different_actor_from_desktop_9b():
    identity = declared_ollama_runpod_identity()
    local = declared_ollama_qwen_identity()
    terra = json.loads(TERRA_IDENTITY.read_text(encoding="utf-8"))
    deepseek = json.loads(DEEPSEEK_IDENTITY.read_text(encoding="utf-8"))
    assert identity["provider"] == PROVIDER == "ollama"
    assert identity["model_id"] == RUNPOD_MODEL_ID == "qwen2.5:14b"
    assert identity["identity_status"] == RUNPOD_IDENTITY_STATUS
    assert identity["runpod_training_candidate"] is True
    assert identity["local_training_candidate"] is False
    assert identity["local_9b_identity_not_reused"] is True
    assert identity["runtime_base_url_not_identity"] is True
    assert identity["authorization_cli_flag"] == RUNPOD_CLI_AUTHORIZATION_FLAG
    assert identity["execution_authority"] == "ABSENT"
    assert identity["execution_authorized"] is False
    assert identity["rewrites_registered_protocol"] is False
    assert identity["inference"] == RUNPOD_TRAINING_INFERENCE
    assert identity["prompt"] == RUNPOD_TRAINING_PROMPT
    assert identity["model_hash"] == RUNPOD_MODEL_HASH
    assert identity["prompt_hash"] == RUNPOD_PROMPT_HASH
    assert identity["inference_hash"] == RUNPOD_INFERENCE_HASH
    assert identity["identity_hash"] == RUNPOD_IDENTITY_HASH
    assert identity["identity_hash"] != local["identity_hash"] == OLLAMA_IDENTITY_HASH
    assert identity["model_hash"] != local["model_hash"]
    assert identity["model_id"] != DEFAULT_MODEL_ID
    assert identity["model_hash"] != terra["model_hash"]
    assert identity["identity_hash"] != deepseek["identity_hash"]
    assert identity["action_schema_hash"] == SHARED_ACTION_SCHEMA_HASH
    assert "Admin_i" not in identity["prompt"]
    assert "ENTER_HIGH_CONVICTION" in identity["prompt"]
    assert identity["inference"]["base_url"] == "runtime"
    assert identity["inference"]["base_url_policy"] == "explicit_cli"


def test_committed_runpod_identity_matches_declaration():
    loaded = load_ollama_runpod_identity(RUNPOD_IDENTITY, protocol_path=PROTOCOL)
    assert loaded == declared_ollama_runpod_identity()
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    assert protocol["protocol"]["registration_status"] == "REGISTERED_NOT_AUTHORIZED"
    assert loaded["consulted_terra_protocol_hash"] == TERRA_PROTOCOL_HASH


def test_runpod_authorization_does_not_authorize_terra_or_local_9b():
    identity = load_ollama_runpod_identity(RUNPOD_IDENTITY, protocol_path=PROTOCOL)
    auth = load_runpod_training_authorization(
        RUNPOD_AUTHORIZATION, identity=identity, protocol_path=PROTOCOL,
    )
    assert auth == declared_runpod_training_authorization(identity)
    assert auth["authorization_hash"] == RUNPOD_AUTHORIZATION_HASH
    assert auth["authorization_status"] == AUTHORIZATION_STATUS
    assert auth["nick_authorized_runpod_training_execution"] is True
    assert auth["nick_authorized_local_training_execution"] is False
    assert auth["authorizes_terra_protocol"] is False
    assert auth["authorizes_local_9b_identity"] is False
    assert auth["execution_authorized"] is False
    assert auth["execution_authority"] == "ABSENT"
    assert auth["authorization_cli_flag"] == RUNPOD_CLI_AUTHORIZATION_FLAG
    assert auth["actor_identity_hash"] == RUNPOD_IDENTITY_HASH


def test_swapping_14b_tag_without_matching_identity_fails_closed():
    with pytest.raises(IdentityError, match="model id"):
        load_ollama_runpod_identity(
            RUNPOD_IDENTITY, protocol_path=PROTOCOL, expected_model_id="qwen3.5:9b",
        )
    with pytest.raises(IdentityError, match="model id"):
        load_ollama_runpod_identity(
            RUNPOD_IDENTITY, protocol_path=PROTOCOL, expected_model_id="qwen3:14b",
        )
    other = declared_ollama_runpod_identity(model_id="qwen3:14b")
    current = declared_ollama_runpod_identity()
    assert other["identity_hash"] != current["identity_hash"] == RUNPOD_IDENTITY_HASH
    assert other["model_id"] == "qwen3:14b"


def test_runpod_flag_does_not_authorize_local_9b_and_local_flag_does_not_authorize_14b():
    local_auth = load_local_training_authorization(
        AUTHORIZATION,
        identity=load_ollama_qwen_identity(IDENTITY, protocol_path=PROTOCOL),
        protocol_path=PROTOCOL,
    )
    runpod_auth = load_runpod_training_authorization(
        RUNPOD_AUTHORIZATION,
        identity=load_ollama_runpod_identity(RUNPOD_IDENTITY, protocol_path=PROTOCOL),
        protocol_path=PROTOCOL,
    )
    with pytest.raises(AuthorizationError, match="RunPod"):
        assert_full_run_authorized(
            authorization=local_auth, authorize_runpod_training=True,
        )
    with pytest.raises(AuthorizationError, match="local TRAINING flag"):
        assert_full_run_authorized(
            authorization=runpod_auth, authorize_local_training=True,
        )
    with pytest.raises(AuthorizationError, match="cannot both"):
        assert_full_run_authorized(
            authorization=runpod_auth,
            authorize_local_training=True,
            authorize_runpod_training=True,
        )
    assert_full_run_authorized(
        authorization=runpod_auth, authorize_runpod_training=True,
    )
