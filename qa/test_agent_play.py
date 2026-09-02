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
import time
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


def _session_path(sandbox) -> Path:
    return sandbox["tmp"] / "runs" / sandbox["run"] / "session.json"


def _patch_session(sandbox, **updates) -> dict:
    path = _session_path(sandbox)
    session = json.loads(path.read_text())
    session.update(updates)
    path.write_text(json.dumps(session, indent=2) + "\n")
    return session


def _pid_lstart(pid: int) -> str:
    return subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=True,
    ).stdout.strip()


def _pid_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                          text=True, check=False).stdout.strip()
    return bool(stat) and "Z" not in stat


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


def test_start_dry_run_prints_the_exact_claude_command_without_auth_secret(sandbox, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "unit-test-secret-must-not-be-logged")
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
    assert "unit-test-secret-must-not-be-logged" not in cmd


def test_start_seeds_the_quest_trace(sandbox):
    proc = _start(sandbox)
    assert proc.returncode == 0, proc.stderr
    trace = sandbox["tmp"] / "runs" / sandbox["run"] / f"{sandbox['run']}.quest_trace.json"
    assert trace.exists(), proc.stdout
    assert json.loads(trace.read_text())["quest_status"] == "active"
    assert "quest=active" in proc.stdout


def test_start_positions_serve_cursor_after_preexisting_chat(sandbox):
    chat = sandbox["state"] / "chat.jsonl"
    chat.parent.mkdir(parents=True, exist_ok=True)
    chat.write_text("".join(
        json.dumps({"role": "player" if i % 2 else "dm", "text": f"old-{i}"}) + "\n"
        for i in range(6)
    ))
    assert _start(sandbox).returncode == 0

    proc = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--max-beats", "1", "--dry-run")
    assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
    assert "beats served: 0" in proc.stdout
    assert len((sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log")
               .read_text().splitlines()) == 1


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


def test_say_queues_without_running_a_beat_when_serve_pid_is_live(sandbox):
    assert _start(sandbox).returncode == 0
    fake_serve = subprocess.Popen(["sleep", "30"])
    try:
        _patch_session(sandbox, serve_pid=str(fake_serve.pid), serve_lstart=_pid_lstart(fake_serve.pid))
        proc = _run(sandbox["tmp"], "say", "--run", sandbox["run"], "--dry-run", "I listen.")
        assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
        assert "queued for serve" in proc.stdout
        assert [r["role"] for r in _chat_rows(sandbox["state"])] == ["dm"]
        queued = [json.loads(line) for line in (sandbox["state"] / "player_moves.jsonl").read_text().splitlines()]
        assert queued == [{"role": "player", "kind": "say", "text": "I listen."}]
        assert json.loads(_session_path(sandbox).read_text())["beats_used"] == "0"
        assert len((sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log")
                   .read_text().splitlines()) == 1
    finally:
        fake_serve.terminate()
        fake_serve.wait(timeout=5)


def test_say_refuses_player_line_when_beat_budget_is_exhausted(sandbox):
    assert _start(sandbox).returncode == 0
    _patch_session(sandbox, beats_used="4", beats="4")
    before = _chat_rows(sandbox["state"])
    proc = _run(sandbox["tmp"], "say", "--run", sandbox["run"], "--dry-run", "One more action.")
    assert proc.returncode == 2
    assert "beat budget exhausted (4/4) — extend with --beats or start a new run" in proc.stderr
    assert _chat_rows(sandbox["state"]) == before


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
    assert "[say] I raise my shield." in cmds[1]
    assert "[say] I call out to the dark." in cmds[2]

    roles = [r["role"] for r in _chat_rows(sandbox["state"])]
    assert roles == ["dm", "player", "player", "dm", "dm"], roles

    # RESTART with nothing new: the persisted cursor means no line is answered twice.
    again = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--max-beats", "1", "--dry-run")
    assert again.returncode == 0, again.stderr
    assert "beats served: 0" in again.stdout
    assert len((sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log")
               .read_text().splitlines()) == 3


def test_serve_consumes_viewer_move_intent_exactly_once(sandbox, monkeypatch):
    moves = sandbox["state"] / "owner-player-moves.json"
    monkeypatch.setenv("WORLDOS_PLAYER_MOVES", str(moves))
    assert _start(sandbox).returncode == 0
    moves.write_text(json.dumps({"role": "player", "kind": "walk_to_cell", "x": 3, "y": 4}) + "\n")

    proc = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--max-beats", "1", "--dry-run")
    assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
    assert "walk_to_cell" in (sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log").read_text()
    assert json.loads(_session_path(sandbox).read_text())["move_cursor"] == "1"
    move_rows = [row for row in _chat_rows(sandbox["state"]) if row.get("move_id")]
    assert len(move_rows) == 1 and move_rows[0]["text"] == "[walk_to_cell] walks to (3,4)"
    assert len((sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log")
               .read_text().splitlines()) == 2

    again = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--max-beats", "1", "--dry-run")
    assert again.returncode == 0, again.stderr
    assert "beats served: 0" in again.stdout
    assert len([row for row in _chat_rows(sandbox["state"]) if row.get("move_id")]) == 1


def test_move_is_durably_consumed_before_a_crash_and_not_replayed(sandbox, monkeypatch):
    assert _start(sandbox).returncode == 0
    moves = sandbox["state"] / "player_moves.jsonl"
    moves.write_text(json.dumps({"role": "player", "kind": "do", "text": "raise the portcullis"}) + "\n")
    monkeypatch.setenv("WORLDOS_AGENT_PLAY_TEST_CRASH_AFTER_CONSUME", "1")
    crashed = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--max-beats", "1", "--dry-run")
    assert crashed.returncode == -9, f"{crashed.stderr}\n{crashed.stdout}"
    session = json.loads(_session_path(sandbox).read_text())
    assert session["move_cursor"] == "1"
    player_rows = [row for row in _chat_rows(sandbox["state"]) if row["role"] == "player"]
    assert len(player_rows) == 1 and player_rows[0].get("move_id")

    monkeypatch.delenv("WORLDOS_AGENT_PLAY_TEST_CRASH_AFTER_CONSUME")
    replay = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--max-beats", "1", "--dry-run")
    assert replay.returncode == 0, replay.stderr
    assert "beats served: 0" in replay.stdout
    assert len((sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log").read_text().splitlines()) == 1
    assert len([row for row in _chat_rows(sandbox["state"]) if row.get("move_id")]) == 1


def test_structured_move_kind_reaches_dm_without_say_wrapper(sandbox):
    assert _start(sandbox).returncode == 0
    moves = sandbox["state"] / "player_moves.jsonl"
    moves.write_text(json.dumps({"role": "player", "kind": "attack", "target_id": "goblin-1", "weapon": "sword"}) + "\n")
    proc = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--max-beats", "1", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    prompt = (sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log").read_text().splitlines()[-1]
    assert "attack" in prompt and "goblin-1" in prompt and "sword" in prompt
    assert "[say] [attack]" not in prompt


def test_set_seed_param_payload_is_preserved_for_dm(sandbox):
    assert _start(sandbox).returncode == 0
    moves = sandbox["state"] / "player_moves.jsonl"
    moves.write_text(json.dumps({"role": "player", "kind": "set_seed_param", "param": "difficulty",
                                 "value": "hard", "force": True}) + "\n")
    proc = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--max-beats", "1", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    prompt = (sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log").read_text().splitlines()[-1]
    for value in ("set_seed_param", "difficulty", "hard", "force"):
        assert value in prompt
    assert "[set_seed_param] acts" not in prompt


def test_live_say_and_viewer_moves_share_one_arrival_order(sandbox):
    assert _start(sandbox).returncode == 0
    moves = sandbox["state"] / "player_moves.jsonl"
    moves.write_text(json.dumps({"role": "player", "kind": "do", "text": "first move"}) + "\n")
    fake_serve = subprocess.Popen(["sleep", "30"])
    try:
        _patch_session(sandbox, serve_pid=str(fake_serve.pid), serve_lstart=_pid_lstart(fake_serve.pid))
        queued = _run(sandbox["tmp"], "say", "--run", sandbox["run"], "--dry-run", "second say")
        assert queued.returncode == 0 and "queued for serve" in queued.stdout
    finally:
        fake_serve.terminate()
        fake_serve.wait(timeout=5)
    _patch_session(sandbox, serve_pid="", serve_lstart="")

    proc = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--max-beats", "2", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    prompts = (sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log").read_text().splitlines()[1:]
    assert len(prompts) == 2 and "first move" in prompts[0] and "second say" in prompts[1]


def test_clarify_answers_without_spending_a_beat_or_ticking_time(sandbox):
    assert _start(sandbox).returncode == 0
    snapshot = sandbox["state"] / "campaigns" / sandbox["cid"] / "snapshot.json"
    before = snapshot.read_bytes()
    moves = sandbox["state"] / "player_moves.jsonl"
    moves.write_text(json.dumps({"role": "player", "kind": "clarify", "text": "How far is the door?"}) + "\n")
    proc = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--max-beats", "1", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(_session_path(sandbox).read_text())["beats_used"] == "0"
    assert snapshot.read_bytes() == before
    assert "beats served: 0" in proc.stdout
    assert "How far is the door?" in (sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log").read_text()


def test_serve_caps_consecutive_clarifications_and_resets_after_an_action(sandbox):
    assert _start(sandbox).returncode == 0
    moves = sandbox["state"] / "player_moves.jsonl"
    intents = [
        {"role": "player", "kind": "clarify", "text": f"Question {index}?"}
        for index in range(1, 5)
    ] + [
        {"role": "player", "kind": "do", "text": "open the door"},
        {"role": "player", "kind": "clarify", "text": "What is beyond it?"},
    ]
    moves.write_text("".join(json.dumps(intent) + "\n" for intent in intents))

    proc = _run(sandbox["tmp"], "serve", "--run", sandbox["run"],
                "--max-beats", "2", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    cmds = (sandbox["tmp"] / "runs" / sandbox["run"] / "dryrun_cmds.log").read_text().splitlines()
    assert len(cmds) == 6, cmds  # open + 3 clarifies + one action + reset clarify
    assert not any("Question 4?" in cmd for cmd in cmds)
    assert "What is beyond it?" in cmds[-1]
    assert any(row.get("system") and "questions this turn" in row["text"]
               for row in _chat_rows(sandbox["state"]))
    session = json.loads(_session_path(sandbox).read_text())
    assert session["beats_used"] == "1" and session["clarifies_used"] == "1"


def test_serve_surfaces_budget_exhaustion_as_dm_system_row(sandbox):
    assert _start(sandbox).returncode == 0
    _patch_session(sandbox, beats_used="4", beats="4")
    chat = sandbox["state"] / "chat.jsonl"
    with chat.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"role": "player", "text": "One more action."}) + "\n")

    proc = _run(sandbox["tmp"], "serve", "--run", sandbox["run"], "--dry-run")
    assert proc.returncode == 2
    assert "beat budget exhausted (4/4) — extend with --beats or start a new run" in proc.stderr
    row = _chat_rows(sandbox["state"])[-1]
    assert row["role"] == "dm" and row.get("system") is True
    assert "beat budget exhausted (4/4)" in row["text"]


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


def test_stop_terminates_recorded_serve_and_writes_bounded_closeout(sandbox):
    assert _start(sandbox).returncode == 0
    fake_serve = subprocess.Popen(["sleep", "30"])
    _patch_session(sandbox, serve_pid=str(fake_serve.pid), serve_lstart=_pid_lstart(fake_serve.pid))
    try:
        proc = _run(sandbox["tmp"], "stop", "--run", sandbox["run"])
        assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
        fake_serve.wait(timeout=2)
        session = json.loads(_session_path(sandbox).read_text())
        closeout_path = Path(session["closeout_path"])
        closeout = json.loads(closeout_path.read_text())
        assert set(closeout) == {"beats", "chat_path", "quest_status", "spend_usd", "stamps", "stopped_at"}
        assert closeout["beats"] == {"used": 0, "limit": 4}
        assert closeout["chat_path"] == str(sandbox["state"] / "chat.jsonl")
        assert isinstance(closeout["stamps"], list) and closeout["quest_status"] == "active"
        assert closeout["spend_usd"] == 0.0
        assert "KeepAlive=false" in RUNNER.read_text()
        assert "closeout" in (REPO / "docs" / "RUNBOOK-INDEX.md").read_text().splitlines()[22]
    finally:
        if fake_serve.poll() is None:
            fake_serve.terminate()
            fake_serve.wait(timeout=5)


def test_stop_terminates_a_term_resistant_dm_orphan_and_clears_heartbeat(sandbox):
    assert _start(sandbox).returncode == 0
    child_file = sandbox["tmp"] / "dm-child.pid"
    fake_serve = subprocess.Popen([
        "bash", "-c",
        f"bash -c 'trap \"\" TERM; echo $$ > {child_file}; while :; do sleep 1; done' & "
        "trap 'exit 0' TERM; wait",
    ])
    try:
        deadline = time.monotonic() + 2
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert child_file.exists(), "fake DM child never started"
        child_pid = int(child_file.read_text().strip())
        _patch_session(sandbox, serve_pid=str(fake_serve.pid), serve_lstart=_pid_lstart(fake_serve.pid))
        heartbeat = sandbox["tmp"] / "runs" / sandbox["run"] / "serve.heartbeat"
        heartbeat.touch()
        proc = _run(sandbox["tmp"], "stop", "--run", sandbox["run"], timeout=10)
        assert proc.returncode == 0, f"{proc.stderr}\n{proc.stdout}"
        fake_serve.wait(timeout=2)
        deadline = time.monotonic() + 2
        while _pid_live(child_pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _pid_live(child_pid), f"DM child {child_pid} survived stop"
        assert not heartbeat.exists(), "forced stop left a stale serving marker"
    finally:
        if fake_serve.poll() is None:
            fake_serve.kill()
            fake_serve.wait(timeout=5)


def test_recycled_pid_identity_neither_defers_say_nor_gets_signaled_by_stop(sandbox):
    assert _start(sandbox).returncode == 0
    unrelated = subprocess.Popen(["sleep", "30"])
    try:
        _patch_session(sandbox, serve_pid=str(unrelated.pid), serve_lstart="definitely-not-this-process")
        say = _run(sandbox["tmp"], "say", "--run", sandbox["run"], "--dry-run", "I act now.")
        assert say.returncode == 0, say.stderr
        assert "queued for serve" not in say.stdout
        assert json.loads(_session_path(sandbox).read_text())["beats_used"] == "1"

        _patch_session(sandbox, serve_pid=str(unrelated.pid), serve_lstart="definitely-not-this-process")
        stop = _run(sandbox["tmp"], "stop", "--run", sandbox["run"])
        assert stop.returncode == 0, stop.stderr
        assert unrelated.poll() is None, "stop signaled an unrelated recycled PID"
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
