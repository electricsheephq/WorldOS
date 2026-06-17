"""Env-var resolution contract for ``servers/engine/_env.py`` (``WORLDOS_*`` only).

These assert:
  - ``env_var(<suffix>)`` reads ``WORLDOS_<suffix>``, else the provided default
  - ``env_var_legacy(<full name>)`` reads the name as-is (for external vars like
    ``OPENCLAW_GATEWAY_TOKEN``)
  - the load-bearing integration: ``store.state_dir()`` resolves ``WORLDOS_STATE_DIR``
    and defaults to ``~/.worldos/state``
"""

import _env
import store


def test_env_var_reads_worldos(monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", "/tmp/new")
    assert _env.env_var("STATE_DIR") == "/tmp/new"


def test_env_var_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("WORLDOS_TTS_BACKEND", raising=False)
    assert _env.env_var("TTS_BACKEND", "kokoro") == "kokoro"
    assert _env.env_var("TTS_BACKEND") is None  # default default is None


def test_env_var_legacy_reads_full_name_as_is(monkeypatch):
    # A full external env name (no WORLDOS_ prefix) is read straight.
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "oc-tok")
    assert _env.env_var_legacy("OPENCLAW_GATEWAY_TOKEN", "") == "oc-tok"
    # A full WORLDOS_* name is likewise read as-is.
    monkeypatch.setenv("WORLDOS_OPENCLAW_GATEWAY_TOKEN", "w-tok")
    assert _env.env_var_legacy("WORLDOS_OPENCLAW_GATEWAY_TOKEN", "") == "w-tok"
    # Unset -> the provided default.
    monkeypatch.delenv("WORLDOS_NOT_SET", raising=False)
    assert _env.env_var_legacy("WORLDOS_NOT_SET", "fallback") == "fallback"


def test_store_state_dir_honors_worldos_env(monkeypatch):
    # The load-bearing integration: store.state_dir() resolves WORLDOS_STATE_DIR.
    monkeypatch.setenv("WORLDOS_STATE_DIR", "/tmp/new-state")
    assert store.state_dir() == store.Path("/tmp/new-state")


def test_store_state_dir_defaults_to_worldos_home(monkeypatch, tmp_path):
    # No override -> ~/.worldos/state.
    monkeypatch.delenv("WORLDOS_STATE_DIR", raising=False)
    monkeypatch.setattr(store.Path, "home", classmethod(lambda cls: tmp_path))
    assert store.state_dir() == tmp_path / ".worldos" / "state"
