"""Fixtures for the cross-service contract suite.

Two import strategies, picked per server by what the *engine* venv can load
in-process (the suite runs under the engine venv, per the sprint spec):

  * **voice** — imported IN-PROCESS. The voice server's only hard dep is ``mcp``,
    which the engine venv has, so we import its ``server`` module directly and
    force the NULL TTS backend (no PyTorch / Kokoro / audio). Lightest +
    deterministic.

  * **rules** — driven through ONE long-lived SUBPROCESS. The rules server imports
    ``rapidfuzz`` at module top level and that wheel is NOT installed in the
    engine venv, so an in-process import is not feasible here. Instead a single
    worker process is launched in the *rules* venv (which has rapidfuzz),
    imports the rules ``server`` once, and answers JSON line-protocol requests
    over stdin/stdout. One process for the whole session keeps it fast
    (~0.3s cold, then near-instant) and single-process from the host's view —
    no xdist, no per-test spawn. ``WORLDOS_RULES_OFFLINE=1`` is set in its env
    exactly as the rules suite does, so it never touches the network.

The engine is the SOLE WRITER of state and the player facade is READ-ONLY; this
suite only *reads* across the MCP boundaries (lookups + speak), so it is
gateway-free and never mutates game state, Eva, or any global config.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repo / server layout (absolute paths — cwd is not assumed).
# ---------------------------------------------------------------------------
SERVERS_DIR = Path(__file__).resolve().parent.parent          # .../servers
REPO_ROOT = SERVERS_DIR.parent                                # repo root
ENGINE_DIR = SERVERS_DIR / "engine"
RULES_DIR = SERVERS_DIR / "rules"
VOICE_DIR = SERVERS_DIR / "voice"

RULES_VENV_PY = RULES_DIR / ".venv" / "bin" / "python"


# ---------------------------------------------------------------------------
# voice — in-process import under the engine venv, NULL backend forced.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def voice_server(monkeypatch_session):
    """The voice MCP ``server`` module, imported in-process with the silent
    NULL TTS backend so no audio / PyTorch is ever touched (CI-safe)."""
    # Force the null backend before the module (and any lazy backend build) runs.
    monkeypatch_session.setenv("WORLDOS_TTS_BACKEND", "null")
    monkeypatch_session.setenv("WORLDOS_TTS_BACKEND", "null")
    # The voice package uses `pythonpath = ["."]`; mirror that so `import server`,
    # `import registry`, `from _env import ...`, `from interface import ...`
    # resolve to the voice package, not the engine one.
    if str(VOICE_DIR) not in sys.path:
        sys.path.insert(0, str(VOICE_DIR))
    import importlib

    # Import (or re-import) cleanly so the forced env var is in effect.
    for name in ("server", "registry", "interface", "stt", "_env"):
        sys.modules.pop(name, None)
    mod = importlib.import_module("server")
    # Sanity: we really are on the null backend, not Kokoro.
    assert mod._backend_name() == "null"
    return mod


@pytest.fixture(scope="session")
def monkeypatch_session():
    """A session-scoped monkeypatch (the built-in one is function-scoped)."""
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


# ---------------------------------------------------------------------------
# rules — one long-lived subprocess worker in the rules venv (offline).
# ---------------------------------------------------------------------------
# Worker program: import the rules `server` once, then loop reading one JSON
# request per line {"fn": "<name>", "args": [...]} and emit one JSON reply per
# line {"ok": true, "result": ...} / {"ok": false, "error": "..."}. stdout is
# kept clean (JSON only); the _env deprecation banner goes to stderr. The rules
# dir is passed via argv[1] (NOT str.format) so the program needs no brace
# escaping and stays readable.
_RULES_WORKER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import server as s

# Whitelisted read-only entrypoints exposed across the boundary.
_FNS = {
    "find_condition": s.find_condition,
    "find_spell": s.find_spell,
    "find_monster": s.find_monster,
    "find_rule": s.find_rule,
    "find_item": s.find_item,
    "find_feat": s.find_feat,
    "find_class": s.find_class,
    "find_background": s.find_background,
    "find_species": s.find_species,
    "search": s.search,
    "lookup_spell": s.lookup_spell,
    "lookup_monster": s.lookup_monster,
    "lookup_condition": s.lookup_condition,
    "sizes": lambda: {
        "conditions": len(s._CONDITIONS), "spells": len(s._SPELLS),
        "monsters": len(s._MONSTERS), "rules": len(s._RULES),
    },
}

sys.stdout.write("READY\n")
sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
        if req.get("fn") == "__quit__":
            break
        fn = _FNS[req["fn"]]
        result = fn(*req.get("args", []), **req.get("kwargs", {}))
        sys.stdout.write(json.dumps({"ok": True, "result": result}) + "\n")
    except Exception as exc:  # report, don't die — keeps the worker alive
        sys.stdout.write(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
    sys.stdout.flush()
"""


class RulesClient:
    """Thin JSON-line client over the long-lived rules worker subprocess."""

    def __init__(self, proc: subprocess.Popen):
        self._proc = proc

    def call(self, fn: str, *args, **kwargs):
        req = json.dumps({"fn": fn, "args": list(args), "kwargs": kwargs})
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(req + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("rules worker closed unexpectedly")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise RuntimeError(f"rules worker error: {reply.get('error')}")
        return reply["result"]

    # Convenience wrappers mirroring the rules server's public API.
    def find_spell(self, name):
        return self.call("find_spell", name)

    def find_monster(self, name):
        return self.call("find_monster", name)

    def find_condition(self, name):
        return self.call("find_condition", name)

    def find_rule(self, name):
        return self.call("find_rule", name)

    def find_item(self, name):
        return self.call("find_item", name)

    def search(self, query, category=None):
        return self.call("search", query, **({"category": category} if category else {}))

    def lookup_spell(self, name):
        return self.call("lookup_spell", name)

    def sizes(self):
        return self.call("sizes")


@pytest.fixture(scope="session")
def rules_client():
    """A session-scoped, single subprocess client for the rules server.

    Launched in the rules venv (has rapidfuzz) with the network disabled. One
    process serves the whole module — no parallel workers, no per-test spawn.
    """
    if not RULES_VENV_PY.exists():
        pytest.skip(f"rules venv python not found at {RULES_VENV_PY}")
    env = dict(os.environ)
    env["WORLDOS_RULES_OFFLINE"] = "1"   # offline, exactly as the rules suite sets it
    env["WORLDOS_RULES_OFFLINE"] = "1"   # canonical name too (no deprecation noise)
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [str(RULES_VENV_PY), "-c", _RULES_WORKER, str(RULES_DIR)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,  # swallow the _env deprecation banner
        text=True,
        cwd=str(RULES_DIR),
        env=env,
    )
    # Wait for the worker's READY handshake (bounded so a broken import fails fast).
    try:
        assert proc.stdout is not None
        ready = proc.stdout.readline().strip()
        if ready != "READY":
            proc.kill()
            raise RuntimeError(f"rules worker failed to start (got {ready!r})")
    except Exception:
        proc.kill()
        raise
    client = RulesClient(proc)
    yield client
    # Teardown: ask it to quit, then ensure it's gone.
    try:
        if proc.stdin is not None and proc.poll() is None:
            proc.stdin.write(json.dumps({"fn": "__quit__"}) + "\n")
            proc.stdin.flush()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
