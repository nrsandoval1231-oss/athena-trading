from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
STATUS = REPO / "docs" / "STATUS.md"


def test_ci_workflow_installs_and_runs_pytest() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pip install -e" in text
    assert ".[dev]" in text
    assert "pytest" in text
    assert "ruff" not in text
    assert "pull_request" in text
    assert "main" in text


def test_status_handoff_states_current_baseline() -> None:
    text = STATUS.read_text(encoding="utf-8")
    assert "live_trading_authority" in text
    assert "manual_human_only" in text
    assert "min_sample_live_setups" in text
    assert "200" in text
    assert "rolling" in text.lower()
    assert "no live broker" in text.lower()
    assert "walk-forward" in text
    assert "paper-period" in text
    assert "human-checkpoint" in text
    assert "autonomous" in text
    assert "calibrate" in text
    assert "paper-api.alpaca.markets" in text
    assert "ALPACA_API_KEY" in text
    assert "python -m astra_desk tape" in text
    assert "python -m astra_desk paper-session" in text
    assert "python -m astra_desk report" in text
    assert "data/ledger" in text
    assert "append-only" in text.lower()
    assert "explicit" in text.lower()
    assert "data.sandbox.alpaca.markets" in text
