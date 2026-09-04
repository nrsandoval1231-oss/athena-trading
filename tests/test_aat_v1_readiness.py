import json

import pytest

from athena_trading.aat_v1 import AATValidationError
from athena_trading.aat_v1.opportunities import compile_training_opportunities
from athena_trading.aat_v1.outcomes import evaluate_training_outcomes
from athena_trading.aat_v1.readiness import audit_adaptation_readiness
from test_aat_v1_opportunities import _multi_session_source


def _evidence(tmp_path):
    source_receipt, source_rows = _multi_session_source(tmp_path)
    opportunities, opportunity_receipt = tmp_path / "opportunities.jsonl", tmp_path / "opportunities.json"
    compile_training_opportunities(
        source_receipt, source_rows, opportunities, opportunity_receipt,
        maximum_opportunities=1, require_atr14=True,
    )
    protocol = tmp_path / "protocol.json"
    protocol.write_text(json.dumps({
        "schema_version": "athena-aat-v1-registered-protocol-v1",
        "execution_authority": "ABSENT", "protocol_hash": "p" * 64,
        "protocol": {
            "dataset_hash": "d" * 64, "assignment_hash": "a" * 64,
            "minimum_pairs": 1, "scoring_hash": "s" * 64,
            "actor": {"model_id": "gpt-5.6-terra"},
        },
    }), encoding="utf-8")
    outcomes, outcome_receipt = tmp_path / "outcomes.jsonl", tmp_path / "outcomes.json"
    evaluate_training_outcomes(
        source_receipt, source_rows, opportunity_receipt, protocol,
        outcomes, outcome_receipt,
    )
    return protocol, opportunity_receipt, outcome_receipt


def test_current_shape_is_durable_negative_knowledge(tmp_path):
    protocol, opportunities, outcomes = _evidence(tmp_path)
    report = audit_adaptation_readiness(protocol, opportunities, outcomes)
    codes = {blocker["code"] for blocker in report["blockers"]}
    assert report["status"] == "BLOCKED_NEGATIVE_KNOWLEDGE"
    assert report["actor_called"] is False
    assert report["sealed_holdout_consumed"] is False
    assert report["execution_authority"] == "ABSENT"
    assert {
        "ACTOR_MODEL_NOT_IMMUTABLE_SNAPSHOT",
        "REGISTERED_MINIMUM_PAIRS_BELOW_FLOOR",
        "TRAINING_OPPORTUNITIES_BELOW_FLOOR",
        "OUTCOME_EVALUATOR_DIAGNOSTIC_ONLY",
        "FULL_SESSION_REENTRY_AND_DETERIORATION_ABSENT",
        "SCORING_SEMANTICS_NOT_BOUND",
        "TRAINING_LESSON_INVENTORY_ABSENT",
    }.issubset(codes)


def test_tampered_outcome_receipt_is_rejected(tmp_path):
    protocol, opportunities, outcomes = _evidence(tmp_path)
    value = json.loads(outcomes.read_text(encoding="utf-8"))
    value["row_count"] = 999
    outcomes.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(AATValidationError, match="receipt hash mismatch"):
        audit_adaptation_readiness(protocol, opportunities, outcomes)
