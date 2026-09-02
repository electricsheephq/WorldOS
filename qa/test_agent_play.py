"""Offline unit tests for qa/agent_play.sh — the DM-only beat loop an external player drives.

Everything here runs with ``--dry-run``: the seed + session file + MCP wiring + chat.jsonl format +
quest telemetry + the serve cursor are all exercised for real, and only the ``claude -p`` call is
replaced by printing the exact command it would have run. No LLM, no budget.

Single-process (the engine is not fork-safe under xdist):
    uv run --directory servers/engine python -m pytest qa/test_agent_play.py -p no:xdist
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

QA = Path(__file__).resolve().parent
REPO = QA.parent
SEEDER = QA / "seed_adventure_demo.py"
RUNNER = QA / "agent_play.sh"


def _seed(state_dir: Path) -> str:
    """Seed the adventure fixture into ``state_dir`` via the real seeder; return the campaign id."""
    proc = subprocess.run(
        [sys.executable, str(SEEDER), str(state_dir)],
        cwd=str(REPO), env={**os.environ, "WORLDOS_STATE_DIR": str(state_dir)},
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"seed failed: {proc.stderr}\n{proc.stdout}"
    return proc.stdout.strip().splitlines()[-1]


def _run(tmp_path: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    env = {**os.environ, "WORLDOS_AGENT_PLAY_ROOT": str(tmp_path / "runs")}
    return subprocess.run(["bash", str(RUNNER), *args], cwd=str(REPO), env=env,
                          capture_output=True, text=True, errors="replace", timeout=timeout)


@pytest.fixture()
def sandbox(tmp_path):
    """A seeded state dir + the run id/args every subcommand needs (no engine, no LLM)."""
    state = tmp_path / "state"
    cid = _seed(state)
    return {"state": state, "cid": cid, "run": "utest", "tmp": tmp_path}


def _chat_rows(state: Path) -> list[dict]:
    path = state / "chat.jsonl"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _start(sandbox) -> subprocess.CompletedProcess:
    return _run(sandbox["tmp"], "start", "--run", sandbox["run"], "--engine", "http://127.0.0.1:9",
                "--state", str(sandbox["state"]), "--campaign", sandbox["cid"],
                "--beats", "4", "--dry-run")


# ── start: session file + chat.jsonl format + the exact dry-run command ────────────────────────
def test_start_writes_session_and_a_dm_chat_row(sandbox):
    proc = _start(sandbox)
    assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
    session = json.loads((sandbox["tmp"] / "runs" / sandbox["run"] / "session.json").read_text())
    assert session["campaign_id"] == sandbox["cid"]
    assert session["state_dir"] == str(sandbox["state"])
    assert session["beats"] == "4" and session["beats_used"] == "0"
    assert session["chat_path"] == str(sandbox["state"] / "chat.jsonl")
    assert session["dm_session_id"]

    rows = _chat_rows(sandbox["state"])
    assert len(rows) == 1, rows
    # The viewer's two-sided format: one JSON object per line, role + text (nothing else required).
    assert rows[0]["role"] == "dm"
    assert isinstance(rows[0]["text"], str) and rows[0]["text"].strip()
    assert set(rows[0]) <= {"role", "text", "engine_logged", "fallback_recovered"}


def test_start_dry_run_prints_the_exact_claude_command(sandbox):
    proc = _start(sandbox)
    assert proc.returncode == 0, proc.stderr
    cmds = (sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log").read_text().splitlines()
    assert len(cmds) == 1, cmds
    cmd = cmds[0]
    # The cold open is the model-pinned, hermetic, engine-bound invocation run_adventure makes.
    for needle in ("claude", "-p", "--mcp-config", "--strict-mcp-config", "--model",
                   "--permission-mode bypassPermissions", "--output-format stream-json",
                   "CLAUDE_CONFIG_DIR=", "--session-id"):
        assert needle.replace(" ", " ") in cmd.replace("\\", ""), f"{needle!r} missing from: {cmd}"
    assert "--max-budget-usd" in cmd


def test_start_seeds_the_quest_trace(sandbox):
    proc = _start(sandbox)
    assert proc.returncode == 0, proc.stderr
    trace = sandbox["tmp"] / "runs" / sandbox["run"] / f"{sandbox['run']}.quest_trace.json"
    assert trace.exists(), proc.stdout
    assert json.loads(trace.read_text())["quest_status"] == "active"
    assert "quest=active" in proc.stdout


# ── say: the player row lands, one beat is spent, one DM row is appended ───────────────────────
def test_say_appends_player_then_dm_and_spends_one_beat(sandbox):
    assert _start(sandbox).returncode == 0
    proc = _run(sandbox["tmp"], "say", "--run", sandbox["run"], "--dry-run",
                "I look around the crypt and ready my blade.")
    assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
    rows = _chat_rows(sandbox["state"])
    assert [r["role"] for r in rows] == ["dm", "player", "dm"], rows
    assert rows[1]["text"] == "I look around the crypt and ready my blade."
    session = json.loads((sandbox["tmp"] / "runs" / sandbox["run"] / "session.json").read_text())
    assert session["beats_used"] == "1"
    assert "[agent-play] beat 1 |" in proc.stdout and "combat_active=" in proc.stdout
    # The continuing beat is a --resume/lean turn, never a second cold open.
    cmds = (sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log").read_text().splitlines()
    assert len(cmds) == 2, cmds
    assert "The player does" in cmds[1] or "player" in cmds[1]


def test_say_without_a_session_fails_loudly(sandbox):
    proc = _run(sandbox["tmp"], "say", "--run", "nosuch", "--dry-run", "hello")
    assert proc.returncode == 2
    assert "no session" in proc.stderr


# ── serve: one beat per NEW player line, and the cursor never re-answers one ───────────────────
def test_serve_answers_each_new_player_line_exactly_once(sandbox):
    assert _start(sandbox).returncode == 0
    chat = sandbox["state"] / "chat.jsonl"
    with chat.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"role": "player", "text": "I raise my shield."}) + "\n")
        fh.write(json.dumps({"role": "player", "text": "I call out to the dark."}) + "\n")

    proc = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--max-beats", "2", "--dry-run")
    assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
    assert "[agent-play] beat 1 |" in proc.stdout
    assert "[agent-play] beat 2 |" in proc.stdout
    assert "beats served: 2" in proc.stdout
    cmds = (sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log").read_text().splitlines()
    assert len(cmds) == 3, cmds   # the cold open + one beat per player line

    roles = [r["role"] for r in _chat_rows(sandbox["state"])]
    assert roles == ["dm", "player", "player", "dm", "dm"], roles

    # RESTART with nothing new: the persisted cursor means no line is answered twice.
    again = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--max-beats", "1", "--dry-run")
    assert again.returncode == 0, again.stderr
    assert "beats served: 0" in again.stdout
    assert len((sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log")
               .read_text().splitlines()) == 3


# ── status / stop ──────────────────────────────────────────────────────────────────────────────
def test_status_and_stop(sandbox):
    assert _start(sandbox).returncode == 0
    st = _run(sandbox["tmp"], "status", "--run", sandbox["run"])
    assert st.returncode == 0, st.stderr
    assert f"campaign={sandbox['cid']}" in st.stdout
    assert "beats=0/4" in st.stdout and "quest=active" in st.stdout and "spend_usd=" in st.stdout

    stop = _run(sandbox["tmp"], "stop", "--run", sandbox["run"])
    assert stop.returncode == 0, stop.stderr
    session = json.loads((sandbox["tmp"] / "runs" / sandbox["run"] / "session.json").read_text())
    assert session["stopped"], session
