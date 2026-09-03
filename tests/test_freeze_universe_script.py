"""The universe script must load .env before it checks for credentials.

Regression: the credential guard originally ran before anything read .env, so
a correctly populated file still aborted the pull. The Massive adapter does its
own dotenv load, but only once a download is already in flight.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "freeze_orb_m_universe.py"


def _module():
    spec = importlib.util.spec_from_file_location("freeze_universe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    return monkeypatch


def test_loads_the_key_from_a_dotenv_file(tmp_path, clean_env):
    env = tmp_path / ".env"
    env.write_text("# comment\nMASSIVE_API_KEY=abc123\nOTHER=x\n", encoding="utf-8")
    assert _module().load_env_file(env) is True
    assert os.environ["MASSIVE_API_KEY"] == "abc123"


def test_a_real_export_is_not_clobbered_by_the_file(tmp_path, clean_env):
    clean_env.setenv("MASSIVE_API_KEY", "from-shell")
    env = tmp_path / ".env"
    env.write_text("MASSIVE_API_KEY=from-file\n", encoding="utf-8")
    _module().load_env_file(env)
    assert os.environ["MASSIVE_API_KEY"] == "from-shell"


def test_surrounding_quotes_are_stripped(tmp_path, clean_env):
    env = tmp_path / ".env"
    env.write_text('MASSIVE_API_KEY="quoted"\n', encoding="utf-8")
    _module().load_env_file(env)
    assert os.environ["MASSIVE_API_KEY"] == "quoted"


def test_missing_file_reports_absence_without_raising(tmp_path, clean_env):
    assert _module().load_env_file(tmp_path / "absent.env") is False


def test_comment_lines_are_ignored(tmp_path, clean_env):
    env = tmp_path / ".env"
    env.write_text("#MASSIVE_API_KEY=commented\n", encoding="utf-8")
    _module().load_env_file(env)
    assert "MASSIVE_API_KEY" not in os.environ
