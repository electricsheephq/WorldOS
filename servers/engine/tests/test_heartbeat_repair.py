"""#749 heartbeat repair — engine-memory decontamination, dedup-bypass, fallback honesty.

The #743 wrapper heartbeat writes real ``kind=narration`` rows ("Your move lands; attention
gathers…") into the engine session log so /events has a row mid-turn. Those rows are a
LIVENESS SIGNAL, not story — but before #749 every engine memory consumer treated them as
canon:

  - recap recited the filler in "Previously on your adventure…",
  - the FTS ledger indexed it for recall,
  - ``scene_context``'s lean re-ground tail fed it back to the DM as its own prose,
  - and ``qa/dm_narration_fallback.py`` recovered it as a beat's "narration".

These tests seed REAL campaigns through the engine's own writers and assert each consumer
now excludes the exact wrapper lines while REAL prose still flows. They also pin the (d)
dedup interaction: ``qa/lib_beat_driver.sh``'s ``log_engine_narration`` #727 substring
guard must NOT silently swallow a cadence-aligned heartbeat (the 4-line rotation repeats
every 4 beats — a run of dead beats logs ONLY heartbeats, so beat N+4's text is always in
the last-8 tail), while still de-duping the DM's own echoed prose. And the (c) fallback
honesty contract: a chat row whose prose was RECOVERED from the engine log (not the DM's
own reply) carries ``{"fallback_recovered": true}`` so behavioral tallies can later
discount masked-dead beats.

Bash helpers run under ``/bin/bash`` exactly as the play/QA wrappers invoke them (macOS
system bash 3.2-clean), mirroring tests/test_dm_reply_engine_logged.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import recap
import server
import wrapper_progress
from models import SessionLogEntry

ENGINE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_DIR.parents[1]
LIB = REPO_ROOT / "qa" / "lib_beat_driver.sh"

OPENING = wrapper_progress.WRAPPER_OPENING_PROGRESS_LINE
MOVES = list(wrapper_progress.WRAPPER_MOVE_PROGRESS_LINES)
PROSE_1 = "You step into the Heapside warren as lamplight gutters along the brick."
PROSE_2 = "Mirelda lowers her voice; the ledger between you suddenly feels heavier."


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def cid(state):
    return server.start_adventure("cellar-rats")["campaign_id"]


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, cwd=str(REPO_ROOT)
    )


def _session_rows(state: Path, cid: str) -> list[dict]:
    rows: list[dict] = []
    for p in sorted((state / "campaigns" / cid / "sessions").glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _chat_rows(chat: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in chat.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --- (b) recap: "Previously on…" must not recite the heartbeat filler -----------------------


def test_recap_excludes_wrapper_lines_store_backed(cid):
    server.log_event(cid, "narration", OPENING)
    server.log_event(cid, "narration", PROSE_1)
    server.log_event(cid, "narration", MOVES[0])
    server.log_event(cid, "narration", PROSE_2)
    out = server.session_recap(cid)["recap"]
    assert "Heapside" in out                        # real prose surfaces
    assert "Mirelda" in out
    assert OPENING not in out
    for m in MOVES:
        assert m not in out, f"recap recited wrapper filler: {m!r}"


def test_format_recap_unit_excludes_wrapper_lines():
    entries = [
        SessionLogEntry(t=1.0, kind="narration", text=OPENING),
        SessionLogEntry(t=2.0, kind="narration", text=PROSE_1),
        SessionLogEntry(t=3.0, kind="narration", text=MOVES[1]),
        SessionLogEntry(t=4.0, kind="dialogue", text="Stay close.", speaker="Lyra"),
    ]
    out = recap.format_recap(entries)
    assert "Stay close." in out
    assert OPENING not in out and MOVES[1] not in out


def test_format_recap_only_wrapper_lines_is_new_adventure():
    entries = [
        SessionLogEntry(t=float(i), kind="narration", text=line)
        for i, line in enumerate([OPENING, *MOVES])
    ]
    out = recap.format_recap(entries)
    assert "start of a new adventure" in out.lower()


# --- (b) FTS ledger: recall must not index the heartbeat filler -----------------------------


def test_fts_recall_excludes_wrapper_lines(cid):
    server.log_event(cid, "narration", OPENING)
    server.log_event(cid, "narration", MOVES[2])
    server.log_event(cid, "narration", "The lich raised a barrow-wight from the ashen mound.")
    # A query built from the wrapper line's own words must not surface it…
    hits = server.recall(cid, "scene gathers voices risks choices focus")["hits"]
    wrapper_texts = set(wrapper_progress.WRAPPER_PROGRESS_LINES)
    assert not any(h["text"].strip() in wrapper_texts for h in hits), hits
    # …while real prose stays recallable (the index itself is intact).
    hits = server.recall(cid, "lich barrow wight")["hits"]
    assert any("lich" in h["text"].lower() for h in hits)


# --- (b) lean re-ground tail: scene_context must not feed filler back as canon --------------


def test_scene_context_recent_narration_excludes_wrapper_lines(cid):
    server.log_event(cid, "narration", OPENING)
    server.log_event(cid, "narration", PROSE_1)
    for m in MOVES:
        server.log_event(cid, "narration", m)
    server.log_event(cid, "narration", PROSE_2)
    tail = server.scene_context(cid, recent_narration=10)["recent_narration"]
    texts = [t["text"] for t in tail]
    assert texts == [PROSE_1, PROSE_2], (
        "the lean re-ground tail must carry ONLY real prose — wrapper heartbeat filler is "
        f"not the DM's canon. Got: {texts}"
    )


# --- (d) dedup interaction: the #727 guard must not swallow a cadence-aligned heartbeat -----


def _log_engine_narration_script(state_dir: Path, text: str, cid: str, times: int) -> str:
    calls = "\n".join(
        f'log_engine_narration {cid!r} {text!r} || echo "CALL_FAILED" >&2' for _ in range(times)
    )
    return (
        f'set -u; ROOT="{REPO_ROOT}"; STATE_DIR="{state_dir}"; CHAT="{state_dir}/chat.jsonl"; . "{LIB}"\n'
        f"{calls}\n"
    )


def test_dedup_guard_never_drops_a_repeated_heartbeat(state, cid):
    """A run of dead beats logs ONLY heartbeats: rotation of 4 ⇒ beat N+4 repeats beat N's
    exact text inside the #727 last-8 tail. The guard must let the repeat through (it is a
    liveness signal, not duplicated prose) — otherwise the player's spinner never flips on
    cadence-aligned beats."""
    r = _bash(_log_engine_narration_script(state, MOVES[0], cid, times=2))
    assert r.returncode == 0, r.stderr
    assert "CALL_FAILED" not in r.stderr
    rows = [e for e in _session_rows(state, cid) if e.get("text") == MOVES[0]]
    assert len(rows) == 2, (
        f"the #727 dedup guard swallowed a repeated heartbeat (got {len(rows)} rows) — "
        "cadence-aligned beats lose their liveness signal"
    )


def test_dedup_guard_still_dedups_real_prose(state, cid):
    """The #727 guard's actual job is untouched: the DM's own echoed prose logs ONCE."""
    r = _bash(_log_engine_narration_script(state, PROSE_1, cid, times=2))
    assert r.returncode == 0, r.stderr
    rows = [e for e in _session_rows(state, cid) if e.get("text") == PROSE_1]
    assert len(rows) == 1, f"real prose must still dedup (got {len(rows)} rows)"


# --- (c) fallback honesty: recovered prose is flagged on the chat row -----------------------


def _seed_fallback_campaign(state: Path, prose: str) -> None:
    """A minimal snapshot + session log shaped exactly like the engine's on-disk format,
    so worldos_dm_narration_or_fallback (via qa/dm_narration_fallback.py) recovers prose."""
    camp = state / "campaigns" / "c1"
    (camp / "sessions").mkdir(parents=True)
    (camp / "snapshot.json").write_text(
        json.dumps({"id": "c1", "active_session_id": "s1", "day": 1}), encoding="utf-8"
    )
    (camp / "sessions" / "s1.jsonl").write_text(
        json.dumps({"t": 1.0, "kind": "narration", "text": prose}) + "\n", encoding="utf-8"
    )


def test_resolve_dm_reply_flags_recovery_and_stamps_chat_row(tmp_path):
    """Empty DM reply + engine-logged prose ⇒ the resolved reply is the recovered prose AND
    the dm chat row carries fallback_recovered:true (so tallies can discount masked beats)."""
    _seed_fallback_campaign(tmp_path, PROSE_1)
    chat = tmp_path / "chat.jsonl"
    script = (
        f'set -u; ROOT="{REPO_ROOT}"; STATE_DIR="{tmp_path}"; CHAT="{chat}"; . "{LIB}"\n'
        f'worldos_resolve_dm_reply "" "$STATE_DIR"\n'
        f'echo "recovered=$CLAWDND_FALLBACK_RECOVERED"\n'
        f'worldos_chatlog_dm "$CLAWDND_DM_REPLY"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "recovered=1" in r.stdout
    rows = _chat_rows(chat)
    assert rows == [{"role": "dm", "text": PROSE_1, "fallback_recovered": True}], rows


def test_resolve_dm_reply_no_flag_when_dm_replied(tmp_path):
    """A DM that ended on its own prose is NOT flagged — the row stays byte-identical to
    the legacy {role,text} shape."""
    _seed_fallback_campaign(tmp_path, PROSE_1)
    chat = tmp_path / "chat.jsonl"
    script = (
        f'set -u; ROOT="{REPO_ROOT}"; STATE_DIR="{tmp_path}"; CHAT="{chat}"; . "{LIB}"\n'
        f'worldos_resolve_dm_reply {PROSE_2!r} "$STATE_DIR"\n'
        f'echo "recovered=$CLAWDND_FALLBACK_RECOVERED"\n'
        f'worldos_chatlog_dm "$CLAWDND_DM_REPLY"\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    assert "recovered=0" in r.stdout
    assert _chat_rows(chat) == [{"role": "dm", "text": PROSE_2}]


def test_record_dm_reply_failure_path_carries_flag_and_consumes_it(tmp_path):
    """record_dm_reply merges fallback_recovered into BOTH its branches; here the engine-log
    failure path (blank campaign id). The flag is consume-once: the next row is unflagged."""
    chat = tmp_path / "chat.jsonl"
    script = (
        f'set -u; ROOT="{REPO_ROOT}"; STATE_DIR="{tmp_path}"; CHAT="{chat}"; . "{LIB}"\n'
        f"CLAWDND_FALLBACK_RECOVERED=1\n"
        f'record_dm_reply "" {PROSE_1!r} beat\n'
        f'record_dm_reply "" {PROSE_2!r} beat\n'
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    rows = _chat_rows(chat)
    assert rows[0] == {"role": "dm", "text": PROSE_1, "fallback_recovered": True}, rows
    assert rows[1] == {"role": "dm", "text": PROSE_2}, rows


def test_record_dm_reply_success_path_carries_both_flags(state, cid):
    """SUCCESS path: recovered prose that also engine-logs carries engine_logged AND
    fallback_recovered (the client de-dups it; the tally can still discount it)."""
    chat = state / "chat.jsonl"
    script = (
        f'set -u; ROOT="{REPO_ROOT}"; STATE_DIR="{state}"; CHAT="{chat}"; . "{LIB}"\n'
        f"CLAWDND_FALLBACK_RECOVERED=1\n"
        f"record_dm_reply {cid!r} {PROSE_1!r} beat\n"
    )
    r = _bash(script)
    assert r.returncode == 0, r.stderr
    rows = _chat_rows(chat)
    assert rows == [
        {"role": "dm", "text": PROSE_1, "engine_logged": True, "fallback_recovered": True}
    ], rows
