import hashlib
import json

import pytest

from athena_trading.aat_v1 import AATValidationError, load_training_projection, plan_training_run


def _fixture(tmp_path):
    rows = tmp_path / "training.jsonl"
    rows.write_text(json.dumps({"opportunity_id": "o1", "candidate_hash": "c1", "features": {"rvol": 2.0}}) + "\n", encoding="utf-8")
    protocol = tmp_path / "protocol.json"
    protocol.write_text(json.dumps({"schema_version": "athena-aat-v1-registered-protocol-v1", "execution_authority": "ABSENT", "protocol_hash": "p" * 64, "protocol": {"dataset_hash": "d" * 64, "assignment_hash": "a" * 64, "actor": {"model_id": "gpt-5.6-terra"}}}), encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"schema_version": "athena-aat-v1-training-projection-v1", "partition": "TRAINING", "execution_authority": "ABSENT", "dataset_hash": "d" * 64, "assignment_hash": "a" * 64, "protocol_hash": "p" * 64, "rows_path": rows.name, "row_count": 1, "rows_sha256": hashlib.sha256(rows.read_bytes()).hexdigest()}), encoding="utf-8")
    return receipt, protocol


def test_training_projection_and_dry_plan(tmp_path):
    receipt, protocol = _fixture(tmp_path)
    value, rows = load_training_projection(receipt, protocol)
    plan = plan_training_run(value, rows, call_ceiling=3, token_ceiling=2000, spend_ceiling_usd=1)
    assert plan["planned_calls"] == 3
    assert plan["actor_arm_count"] == 3
    assert plan["dry_run"] is True
    assert plan["execution_authority"] == "ABSENT"


@pytest.mark.parametrize("mutator", [
    lambda r: r.with_name("SEALED_HOLDOUT.json"),
])
def test_holdout_path_is_rejected(tmp_path, mutator):
    receipt, protocol = _fixture(tmp_path)
    bad = mutator(receipt)
    bad.write_text(receipt.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(AATValidationError, match="SEALED_HOLDOUT"):
        load_training_projection(bad, protocol)


def test_missing_ceiling_is_rejected(tmp_path):
    receipt, protocol = _fixture(tmp_path)
    value, rows = load_training_projection(receipt, protocol)
    with pytest.raises(AATValidationError, match="ceilings"):
        plan_training_run(value, rows, call_ceiling=4, token_ceiling=None, spend_ceiling_usd=1)


def test_leakage_field_is_rejected(tmp_path):
    receipt, protocol = _fixture(tmp_path)
    rows = tmp_path / "training.jsonl"
    rows.write_text(json.dumps({"opportunity_id": "o1", "candidate_hash": "c1", "features": {"outcome": 2}}) + "\n", encoding="utf-8")
    receipt.write_text(receipt.read_text(encoding="utf-8").replace(receipt.parent.joinpath("training.jsonl").name, rows.name).replace(json.loads(receipt.read_text())["rows_sha256"], hashlib.sha256(rows.read_bytes()).hexdigest()), encoding="utf-8")
    with pytest.raises(AATValidationError, match="forbidden"):
        load_training_projection(receipt, protocol)
