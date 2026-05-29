"""Backward-compat env-var resolution for the WorldOS rename (issue #295, W0-E).

Asserts the NON-breaking contract at every read site routed through _env:
  - WORLDOS_<X> is preferred over CLAWDND_<X>
  - CLAWDND_<X> alone still resolves AND emits exactly one stderr deprecation warning
  - neither set -> the provided default
  - the legacy CLAWDND_* names keep working end-to-end (store.state_dir)
  - the new ~/.worldos home is preferred when no override is set, else ~/.clawdnd
"""

import importlib

import pytest

import _env
import store


@pytest.fixture(autouse=True)
def _reset_warn_cache():
    """The one-time-warn cache is module-global; clear it so each test starts clean."""
    _env._warned.clear()
    yield
    _env._warned.clear()


def test_worldos_wins_over_clawdnd(monkeypatch, capsys):
    monkeypatch.setenv("WORLDOS_STATE_DIR", "/tmp/new")
    monkeypatch.setenv("CLAWDND_STATE_DIR", "/tmp/old")
    assert _env.env_var("STATE_DIR") == "/tmp/new"
    # The new name wins outright — no legacy hit, so NO deprecation warning.
    assert "DEPRECATION" not in capsys.readouterr().err


def test_clawdnd_alone_still_works_and_warns_once(monkeypatch, capsys):
    monkeypatch.delenv("WORLDOS_STATE_DIR", raising=False)
    monkeypatch.setenv("CLAWDND_STATE_DIR", "/tmp/legacy")
    # Resolves the legacy value (non-breaking)...
    assert _env.env_var("STATE_DIR") == "/tmp/legacy"
    # ...and reading it AGAIN still resolves but does NOT re-warn (one-time).
    assert _env.env_var("STATE_DIR") == "/tmp/legacy"
    err = capsys.readouterr().err
    warnings = [ln for ln in err.splitlines() if "DEPRECATION" in ln]
    assert len(warnings) == 1
    assert "CLAWDND_STATE_DIR" in warnings[0]
    assert "WORLDOS_STATE_DIR" in warnings[0]


def test_neither_set_returns_default(monkeypatch, capsys):
    monkeypatch.delenv("WORLDOS_TTS_BACKEND", raising=False)
    monkeypatch.delenv("CLAWDND_TTS_BACKEND", raising=False)
    assert _env.env_var("TTS_BACKEND", "kokoro") == "kokoro"
    assert _env.env_var("TTS_BACKEND") is None  # default default is None
    # Nothing set -> nothing deprecated.
    assert "DEPRECATION" not in capsys.readouterr().err


def test_env_var_legacy_swaps_prefix(monkeypatch, capsys):
    # A full CLAWDND_* constant resolves its WORLDOS_* alias first...
    monkeypatch.setenv("WORLDOS_OPENCLAW_GATEWAY_TOKEN", "new-tok")
    monkeypatch.setenv("CLAWDND_OPENCLAW_GATEWAY_TOKEN", "old-tok")
    assert _env.env_var_legacy("CLAWDND_OPENCLAW_GATEWAY_TOKEN", "") == "new-tok"
    assert "DEPRECATION" not in capsys.readouterr().err
    # ...and falls back to the legacy value (with a warning) when only it is set.
    monkeypatch.delenv("WORLDOS_OPENCLAW_GATEWAY_TOKEN", raising=False)
    assert _env.env_var_legacy("CLAWDND_OPENCLAW_GATEWAY_TOKEN", "") == "old-tok"
    assert "DEPRECATION" in capsys.readouterr().err


def test_env_var_legacy_non_clawdnd_passthrough(monkeypatch, capsys):
    # External vars (e.g. OpenClaw's own) are read straight — never aliased, never warned.
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "oc-tok")
    assert _env.env_var_legacy("OPENCLAW_GATEWAY_TOKEN", "") == "oc-tok"
    assert "DEPRECATION" not in capsys.readouterr().err


def test_store_state_dir_honors_legacy_env(monkeypatch):
    # The load-bearing integration: store.state_dir() still respects the legacy var
    # (the running app + QA scripts set CLAWDND_STATE_DIR), so nothing breaks.
    monkeypatch.delenv("WORLDOS_STATE_DIR", raising=False)
    monkeypatch.setenv("CLAWDND_STATE_DIR", "/tmp/legacy-state")
    assert store.state_dir() == store.Path("/tmp/legacy-state")
    # And the new name takes precedence when both are set.
    monkeypatch.setenv("WORLDOS_STATE_DIR", "/tmp/new-state")
    assert store.state_dir() == store.Path("/tmp/new-state")


def test_store_state_dir_home_fallback(monkeypatch, tmp_path):
    # No override: prefer ~/.worldos/state when that home exists, else ~/.clawdnd/state.
    monkeypatch.delenv("WORLDOS_STATE_DIR", raising=False)
    monkeypatch.delenv("CLAWDND_STATE_DIR", raising=False)
    fake_home = tmp_path
    monkeypatch.setattr(store.Path, "home", classmethod(lambda cls: fake_home))

    # ~/.worldos absent -> legacy ~/.clawdnd/state.
    assert store.state_dir() == fake_home / ".clawdnd" / "state"

    # Create ~/.worldos -> now preferred.
    (fake_home / ".worldos").mkdir()
    assert store.state_dir() == fake_home / ".worldos" / "state"
