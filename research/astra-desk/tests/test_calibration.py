from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from astra_desk.calibration import (
    CalibrationEngine,
    correlation_flags,
    ingest_calibration_setups,
    load_calibration_fixture,
    propose_eviction,
    propose_weights,
)
from astra_desk.cli import main
from astra_desk.contract import (
    EXIT_ID,
    READER_ID,
    SCOUT_ID,
    SKEPTIC_ID,
    load_contract,
    load_schema,
    load_yaml,
    validate_contract_data,
)
from astra_desk.desk import TradingDesk
from astra_desk.errors import CalibrationError, ContractError, LiveTradingDisabled
from astra_desk.fixtures import deploy_fixture
from astra_desk.ledger import SetupOutcome, outcomes_from_ledger
from astra_desk.types import DeskMode, Regime

REPO = Path(__file__).resolve().parents[1]
CONTRACT = REPO / "contracts" / "astra-desk.v1.yaml"
SCHEMA = REPO / "contracts" / "astra-desk.schema.json"


def _outcome(
    setup_id: str,
    *,
    regime: str = "trending",
    correct: dict[str, bool],
) -> SetupOutcome:
    return SetupOutcome(setup_id=setup_id, regime=regime, correct_by_agent=correct)


def _gating(
    *,
    scout: bool = True,
    reader: bool = True,
    exit_ok: bool = True,
    skeptic: bool = True,
) -> dict[str, bool]:
    return {
        SCOUT_ID: scout,
        READER_ID: reader,
        EXIT_ID: exit_ok,
        SKEPTIC_ID: skeptic,
    }


def seed_outcomes(
    desk: TradingDesk,
    n: int,
    correct: dict[str, bool],
    *,
    regime: str = "trending",
    prefix: str = "seed",
) -> None:
    for index in range(n):
        setup_id = f"{prefix}-{index:03d}"
        desk.ledger.append(
            setup_id,
            {
                "setup_id": setup_id,
                "regime": regime,
                "outcomes": correct,
                "live_trading_authority": False,
            },
        )
        desk.accuracy.record_setup(setup_id, Regime(regime), correct)


def test_contract_calibration_spec() -> None:
    contract = load_contract()
    assert contract.calibration.weights_update == "nightly_from_outcomes"
    assert contract.calibration.correlation_monitor is True
    assert "rolling 50" in contract.calibration.eviction_rule
    assert contract.calibration.rolling_window == 50
    assert contract.calibration.eviction_bind_sample == 200
    assert contract.min_sample_live_setups == 200
    assert contract.calibration.agent_accuracy_tracked_per_regime is True
    assert contract.raw["regime_classifier"]["agent_accuracy_tracked_per_regime"] is True


def test_schema_rejects_live_weight_update() -> None:
    data = copy.deepcopy(load_yaml(CONTRACT))
    schema = load_schema(SCHEMA)
    data["calibration"]["weights_update"] = "live_from_broker"
    with pytest.raises(ContractError, match="schema validation"):
        validate_contract_data(data, schema)


def test_schema_rejects_correlation_monitor_off() -> None:
    data = copy.deepcopy(load_yaml(CONTRACT))
    schema = load_schema(SCHEMA)
    data["calibration"]["correlation_monitor"] = False
    with pytest.raises(ContractError, match="schema validation"):
        validate_contract_data(data, schema)


def test_weights_move_from_outcomes() -> None:
    contract = load_contract()
    outcomes = tuple(
        _outcome(f"w-{i}", correct=_gating(skeptic=False)) for i in range(8)
    )
    proposed = propose_weights(contract.gate.conviction_weights, outcomes)
    assert proposed[SCOUT_ID] == pytest.approx(1.5)
    assert proposed[READER_ID] == pytest.approx(2.25)
    assert proposed[EXIT_ID] == pytest.approx(1.5)
    assert proposed[SKEPTIC_ID] == pytest.approx(-0.5)
    assert proposed[SKEPTIC_ID] < 0


def test_nightly_apply_moves_working_weights_and_logs() -> None:
    desk = TradingDesk(mode=DeskMode.PAPER_TRADING)
    seed_outcomes(desk, 8, _gating(scout=True, reader=True, exit_ok=True, skeptic=False))
    report = CalibrationEngine().nightly_from_outcomes(desk, apply=True)
    assert report.weights_applied is True
    assert report.live_trading_authority is False
    assert report.eviction.eligible is False
    assert report.eviction.reason == "insufficient_sample_for_rolling_50"
    assert desk.conviction_weights[SCOUT_ID] == pytest.approx(1.5)
    assert desk.conviction_weights[SKEPTIC_ID] == pytest.approx(-0.5)
    assert desk.conviction_weights[SKEPTIC_ID] < 0
    kinds = [entry.payload.get("kind") for entry in desk.ledger.entries]
    assert "calibration_nightly" in kinds
    nightly = next(e for e in desk.ledger.entries if e.payload.get("kind") == "calibration_nightly")
    assert nightly.payload["weights_from"][SCOUT_ID] == 1.0
    assert nightly.payload["live_trading_authority"] is False


def test_rolling_50_stats_visible_but_eviction_does_not_bind() -> None:
    desk = TradingDesk(mode=DeskMode.PAPER_TRADING)
    seed_outcomes(desk, 50, _gating(skeptic=False))
    report = CalibrationEngine().nightly_from_outcomes(desk, apply=True)
    assert report.sample_setups == 50
    assert report.eviction.eligible is False
    assert report.eviction.reason == "insufficient_sample_for_min_live_setups"
    assert report.eviction.bind_sample == 200
    assert report.eviction.agent_id == SKEPTIC_ID
    assert report.eviction.scores[SKEPTIC_ID] == pytest.approx(0.0)
    assert report.eviction.scores[SCOUT_ID] == pytest.approx(1.0)
    assert report.weights_applied is True
    assert desk.disabled_seats == {}
    with pytest.raises(CalibrationError, match="insufficient_sample_for_min_live_setups"):
        CalibrationEngine().nightly_from_outcomes(desk, apply=True, evict=True)
    assert desk.disabled_seats == {}


def test_seat_eviction_binds_only_at_min_sample_live_setups() -> None:
    desk = TradingDesk(mode=DeskMode.PAPER_TRADING)
    seed_outcomes(desk, 199, _gating(skeptic=False))
    visible = CalibrationEngine().nightly_from_outcomes(desk, apply=True)
    assert visible.sample_setups == 199
    assert visible.eviction.eligible is False
    assert visible.eviction.scores
    assert desk.disabled_seats == {}
    with pytest.raises(CalibrationError, match="insufficient_sample_for_min_live_setups"):
        CalibrationEngine().nightly_from_outcomes(desk, apply=True, evict=True)

    seed_outcomes(desk, 1, _gating(skeptic=False), prefix="bind")
    bound = CalibrationEngine().nightly_from_outcomes(desk, apply=True, evict=True)
    assert bound.sample_setups == 200
    assert bound.eviction.eligible is True
    assert bound.eviction.agent_id == SKEPTIC_ID
    assert bound.eviction.bind_sample == 200
    assert bound.eviction.rolling_window == 50
    assert bound.eviction_applied is True
    assert SKEPTIC_ID in desk.disabled_seats
    assert desk.effective_weights()[SKEPTIC_ID] == 0.0
    assert desk.agents[SKEPTIC_ID] is not None


def test_evict_true_with_insufficient_sample_fails_closed() -> None:
    desk = TradingDesk(mode=DeskMode.PAPER_TRADING)
    seed_outcomes(desk, 8, _gating(skeptic=False))
    with pytest.raises(CalibrationError, match="insufficient_sample_for_rolling_50"):
        CalibrationEngine().nightly_from_outcomes(desk, apply=True, evict=True)
    assert desk.disabled_seats == {}
    assert desk.conviction_weights[SCOUT_ID] == 1.0


def test_correlation_flag_on_comoving_errors() -> None:
    desk = TradingDesk(mode=DeskMode.PAPER_TRADING)
    seed_outcomes(
        desk,
        50,
        _gating(scout=False, reader=False, exit_ok=True, skeptic=True),
    )
    report = CalibrationEngine().nightly_from_outcomes(desk)
    pairs = {(flag.agent_a, flag.agent_b) for flag in report.correlation_flags}
    assert (SCOUT_ID, READER_ID) in pairs
    flagged = next(flag for flag in report.correlation_flags if flag.agent_a == SCOUT_ID)
    assert flagged.comovement == pytest.approx(1.0)
    assert flagged.overlap == 50


def test_correlation_does_not_invent_flag_below_overlap() -> None:
    outcomes = tuple(
        _outcome(
            f"c-{i}",
            correct=_gating(scout=False, reader=False, exit_ok=True, skeptic=True),
        )
        for i in range(5)
    )
    assert correlation_flags(outcomes) == ()
    proposal = propose_eviction(outcomes)
    assert proposal.eligible is False
    assert proposal.reason == "insufficient_sample_for_rolling_50"
    assert proposal.scores == {}
    assert proposal.bind_sample == 200


def test_live_broker_still_raises() -> None:
    with pytest.raises(LiveTradingDisabled):
        TradingDesk(live_broker=True)


def test_revert_restores_previous_weights() -> None:
    desk = TradingDesk(mode=DeskMode.PAPER_TRADING)
    seed_outcomes(desk, 8, _gating(skeptic=False))
    CalibrationEngine().nightly_from_outcomes(desk, apply=True)
    assert desk.conviction_weights[SCOUT_ID] == pytest.approx(1.5)
    report = CalibrationEngine().nightly_from_outcomes(desk, revert=True)
    assert report.reverted is True
    assert desk.conviction_weights[SCOUT_ID] == pytest.approx(1.0)
    assert desk.conviction_weights[SKEPTIC_ID] == pytest.approx(-1.0)
    kinds = [entry.payload.get("kind") for entry in desk.ledger.entries]
    assert "calibration_revert" in kinds


def test_disabled_seat_keeps_ledger_history() -> None:
    desk = TradingDesk(mode=DeskMode.PAPER_TRADING)
    seed_outcomes(desk, 200, _gating(skeptic=False))
    before = outcomes_from_ledger(desk.ledger)
    assert len(before) == 200
    CalibrationEngine().nightly_from_outcomes(desk, apply=True, evict=True)
    after = outcomes_from_ledger(desk.ledger)
    assert after == before
    assert SKEPTIC_ID in desk.disabled_seats
    result = desk.run_round(deploy_fixture())
    assert SKEPTIC_ID in result.agent_outputs
    assert desk.effective_weights()[SKEPTIC_ID] == 0.0


def test_round_writes_outcomes_and_per_regime_history() -> None:
    fixture = replace(
        deploy_fixture(),
        resolved_correct_by_agent={SCOUT_ID: True, READER_ID: False},
    )
    desk = TradingDesk()
    desk.run_round(fixture)
    history = desk.accuracy.history
    assert len(history) == 1
    assert history[0].regime == "trending"
    assert history[0].correct_by_agent[SCOUT_ID] is True
    ledger_outcomes = outcomes_from_ledger(desk.ledger)
    assert ledger_outcomes == history
    stats = desk.accuracy.regime_stats()
    assert stats["trending"][SCOUT_ID]["n"] == 1
    assert stats["trending"][READER_ID]["accuracy"] == 0.0


def test_shipped_fixture_dry_run_proposes_without_eviction() -> None:
    desk = TradingDesk(mode=DeskMode.PAPER_TRADING)
    fixture = load_calibration_fixture()
    ingest_calibration_setups(desk, fixture.setups)
    report = CalibrationEngine().nightly_from_outcomes(desk)
    assert report.sample_setups == 8
    assert report.weights_applied is False
    assert report.eviction.eligible is False
    assert report.weights_proposed[SCOUT_ID] == pytest.approx(1.5)
    assert report.weights_proposed[SKEPTIC_ID] == pytest.approx(-0.5)
    assert "trending" in report.per_regime_accuracy[SCOUT_ID]
    assert report.live_trading_authority is False
    assert desk.conviction_weights[SCOUT_ID] == 1.0


def test_cli_calibrate_and_nightly_alias(capsys) -> None:
    code = main(["calibrate"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["live_trading_authority"] is False
    assert payload["weights_applied"] is False
    assert payload["sample_setups"] == 8
    assert payload["eviction"]["eligible"] is False
    assert payload["weights_proposed"][SCOUT_ID] == pytest.approx(1.5)
    assert payload["weights_update"] == "nightly_from_outcomes"

    code = main(["nightly"])
    captured = capsys.readouterr()
    assert code == 0
    alias = json.loads(captured.out)
    assert alias["sample_setups"] == 8


def test_cli_calibrate_evict_fails_closed(capsys) -> None:
    code = main(["calibrate", "--evict"])
    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.out)
    assert payload["refused"] is True
    assert "insufficient_sample_for_rolling_50" in payload["reason"]
    assert payload["live_trading_authority"] is False


def test_cli_calibrate_rejects_live_modes() -> None:
    with pytest.raises(SystemExit):
        main(["calibrate", "--mode", "autonomous"])
    with pytest.raises(SystemExit):
        main(["calibrate", "--mode", "live_with_human_checkpoint"])
    with pytest.raises(SystemExit):
        main(["calibrate", "--broker", "live"])


def test_cli_calibrate_apply(capsys) -> None:
    code = main(["calibrate", "--apply"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["weights_applied"] is True
    assert payload["eviction_applied"] is False
    assert payload["live_trading_authority"] is False
