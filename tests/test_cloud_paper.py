from __future__ import annotations

import copy
import json
import signal
import sqlite3
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest

from athena_trading.athena_adapter.contracts import canonical_hash
from athena_trading.athena_adapter.jev import ModelError
from athena_trading.paper import cloud
from athena_trading.paper.journal import PaperJournal
from athena_trading.paper.live import OpenAIActor, PilotPolicy


def actor_response(action="LONG"):
    return {
        "id": "resp_fixture", "model": OpenAIActor.model, "status": "completed",
        "output": [{"type": "reasoning", "summary": []}, {
            "type": "message", "content": [{"type": "output_text", "text": json.dumps({
                "action": action, "rationale": "The close broke above the opening range.",
                "invalidation": "Price returns below the range.",
            })}],
        }], "usage": {"input_tokens": 500, "output_tokens": 100},
    }


def test_hosted_actor_contract_and_receipt_exclude_credentials():
    calls = []

    def transport(method, url, headers, payload=None, **kwargs):
        calls.append((method, url, headers, copy.deepcopy(payload), kwargs))
        return {"id": OpenAIActor.model} if method == "GET" else actor_response()

    actor = OpenAIActor("fixture-secret", transport=transport)
    actor.preflight()
    proposal = actor.propose({"candidate_direction": "LONG", "observations": {"price": 101}})
    assert proposal["action"] == "LONG"
    assert proposal["actor_model"] == OpenAIActor.model
    assert calls[0][0:2] == ("GET", "https://api.openai.com/v1/models/gpt-5.6-terra")
    method, url, headers, payload, options = calls[1]
    assert (method, url) == ("POST", OpenAIActor.endpoint)
    assert headers["Authorization"] == "Bearer fixture-secret"
    assert options == {"timeout": 25}
    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["max_output_tokens"] == 1024
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["schema"]["additionalProperties"] is False
    assert "tools" not in payload
    assert actor.last_receipt["request_hash"] == canonical_hash(payload)
    assert actor.last_receipt["response_hash"] == canonical_hash(actor_response())
    assert "fixture-secret" not in json.dumps(actor.last_receipt)


@pytest.mark.parametrize("action", ["LONG", "SHORT", "NO_TRADE"])
def test_hosted_actor_accepts_only_the_candidate_or_abstention(action):
    actor = OpenAIActor("fixture", transport=lambda *a, **k: actor_response(action))
    assert actor.propose({"candidate_direction": "SHORT" if action == "SHORT" else "LONG"})["action"] == action


@pytest.mark.parametrize("fault", ["model", "incomplete", "refusal", "missing", "duplicate",
                                  "json", "extra", "direction", "text", "usage"])
def test_hosted_actor_fails_closed_on_invalid_provider_output(fault):
    raw = actor_response()
    content = raw["output"][1]["content"]
    if fault == "model":
        raw["model"] = "different-model"
    elif fault == "incomplete":
        raw["status"] = "incomplete"
    elif fault == "refusal":
        content[0] = {"type": "refusal", "refusal": "Cannot propose."}
    elif fault == "missing":
        raw["output"] = []
    elif fault == "duplicate":
        content.append(copy.deepcopy(content[0]))
    elif fault == "json":
        content[0]["text"] = "not JSON"
    elif fault == "usage":
        raw["usage"]["output_tokens"] = True
    else:
        proposal = json.loads(content[0]["text"])
        if fault == "extra":
            proposal["size"] = 100
        elif fault == "direction":
            proposal["action"] = "SHORT"
        else:
            proposal["rationale"] = ""
        content[0]["text"] = json.dumps(proposal)
    actor = OpenAIActor("fixture", transport=lambda *a, **k: raw)
    with pytest.raises(ModelError):
        actor.propose({"candidate_direction": "LONG"})
    assert actor.last_receipt is None


def test_hosted_actor_does_not_retry_timeouts_or_oversized_input():
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(args)
        raise TimeoutError("fixture timeout")

    actor = OpenAIActor("fixture", transport=unavailable)
    with pytest.raises(TimeoutError):
        actor.propose({"candidate_direction": "LONG"})
    assert len(calls) == 1
    with pytest.raises(ModelError, match="size limit"):
        actor.propose({"candidate_direction": "LONG", "text": "a" * 24_000})
    assert len(calls) == 1


def journal_at(directory, day, *, entry=False, finish=None, model=OpenAIActor.model):
    path = directory / f"{day}.sqlite"
    journal = PaperJournal(path)
    journal.append("binding", binding={"account_id": "fixture-account", "policy": asdict(
        PilotPolicy("AAPL", feed="sip", actor_model=model))})
    if entry:
        journal.append("entry_intent", session=day, order={"client_order_id": "fixture-entry"})
    if finish:
        journal.append(finish, reason="fixture")
    journal.close()
    return path


@pytest.mark.parametrize("finish", ["session_complete", "halt", "flat_confirmed"])
def test_completed_or_halted_session_does_not_restart_that_day(tmp_path, finish):
    journal_at(tmp_path, "2026-09-24", finish=finish)
    assert cloud.next_session(tmp_path, date(2026, 9, 24)) is None
    tomorrow = cloud.next_session(tmp_path, date(2026, 9, 25))
    assert tomorrow == cloud.SessionTask(tmp_path / "2026-09-25.sqlite")


def test_unfinished_entry_blocks_new_day_even_if_completion_was_recorded(tmp_path):
    old = journal_at(tmp_path, "2026-09-23", entry=True, finish="session_complete")
    journal_at(tmp_path, "2026-09-24", finish="halt")
    task = cloud.next_session(tmp_path, date(2026, 9, 24))
    assert task.recovery and task.path == old
    assert task.saved_policy["symbol"] == "AAPL"
    journal = PaperJournal(old)
    journal.append("flat_confirmed")
    journal.close()
    assert cloud.next_session(tmp_path, date(2026, 9, 24)) is None


def test_multiple_unfinished_entries_block_all_new_entries(tmp_path):
    journal_at(tmp_path, "2026-09-23", entry=True)
    journal_at(tmp_path, "2026-09-24", entry=True)
    with pytest.raises(ValueError, match="multiple unresolved"):
        cloud.next_session(tmp_path, date(2026, 9, 25))


def test_corrupt_old_journal_blocks_rollover(tmp_path):
    path = journal_at(tmp_path, "2026-09-23", finish="flat_confirmed")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE events SET digest = 'changed' WHERE seq = 1")
    with pytest.raises(ValueError, match="integrity"):
        cloud.next_session(tmp_path, date(2026, 9, 24))


def test_recovery_preserves_old_symbol_feed_and_actor_without_model_access(tmp_path):
    path = journal_at(tmp_path, "2026-09-23", entry=True, model="qwen2.5:7b")
    task = cloud.next_session(tmp_path, date(2026, 9, 24))
    command = cloud.session_command(task, PilotPolicy("MSFT"), Path("runner.py"))
    assert command[2:] == ["--authority", "PAPER", "--symbol", "AAPL", "--feed", "sip",
                           "--actor", "ollama", "--journal", str(path), "--run"]


def test_cloud_starts_new_session_with_explicit_date_and_hosted_actor(tmp_path):
    task = cloud.next_session(tmp_path, date(2026, 9, 24))
    command = cloud.session_command(task, PilotPolicy("AAPL"), Path("runner.py"))
    assert command[-2:] == ["--session-date", "2026-09-24"]
    assert command[command.index("--actor") + 1] == "openai"


def test_cloud_shutdown_between_clock_and_spawn_never_starts_session(tmp_path, monkeypatch):
    handlers, started = {}, []
    monkeypatch.setenv("ATHENA_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ATHENA_SYMBOL", "AAPL")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    monkeypatch.setenv("TYPESAFE_API_KEY", "fixture")
    monkeypatch.setattr(cloud.signal, "signal", lambda sig, handler: handlers.update({sig: handler}))
    monkeypatch.setattr(cloud.subprocess, "Popen", lambda *a: started.append(a))

    class Broker:
        def call(self, *args):
            handlers[signal.SIGTERM](signal.SIGTERM, None)
            return {"timestamp": "2026-09-24T14:00:00Z", "is_open": True}

    monkeypatch.setattr(cloud.LivePaperAPI, "from_env", lambda: Broker())
    assert cloud.main() == 0
    assert not started
    assert json.loads((tmp_path / "health.json").read_text())["status"] == "STOPPED"


def test_cloud_recovers_outside_market_hours_without_model_keys(tmp_path, monkeypatch):
    journal_at(tmp_path, "2026-09-23", entry=True)
    handlers, started = {}, []
    monkeypatch.setenv("ATHENA_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ATHENA_SYMBOL", "AAPL")
    for key in ("OPENAI_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cloud.signal, "signal", lambda sig, handler: handlers.update({sig: handler}))

    class Broker:
        def call(self, *args):
            return {"timestamp": "2026-09-24T00:00:00Z", "is_open": False}

    class Child:
        returncode = 0
        pid = 123

        def poll(self):
            return 0

    def spawn(command):
        started.append(command)
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return Child()

    monkeypatch.setattr(cloud.LivePaperAPI, "from_env", lambda: Broker())
    monkeypatch.setattr(cloud.subprocess, "Popen", spawn)
    assert cloud.main() == 0
    assert len(started) == 1
    assert "2026-09-23.sqlite" in started[0][-2]


def test_heartbeat_replaces_complete_json(tmp_path):
    cloud.heartbeat(tmp_path, "WAITING_FOR_MARKET", next_open="fixture")
    cloud.heartbeat(tmp_path, "RECOVERING", journal="previous.sqlite")
    result = json.loads((tmp_path / "health.json").read_text())
    assert result["status"] == "RECOVERING" and result["journal"] == "previous.sqlite"
    assert "next_open" not in result and result["updated_at"].endswith("+00:00")
    assert not (tmp_path / "health.json.tmp").exists()
