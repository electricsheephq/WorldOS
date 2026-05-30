"""Tests for qa/dm_narration_fallback.py — the empty-DM-narration fallback (issue #357).

The play/QA resolver loop writes the DM turn's FINAL reply text to the chat panel. When a DM
turn ends on a tool call (e.g. its last act was log_event/roll) or a bare 3rd-person status
line, that final reply is EMPTY even though the engine logged real player-facing prose via
log_event(kind="narration"/"dialogue"). The fallback recovers the most recent player-facing
prose from the engine's per-session log so the chat is never blank on a resolved beat.

This test writes the session log via the engine's OWN writer (store.append_log +
models.SessionLogEntry), so it validates against the EXACT on-disk JSONL format the engine
produces — then runs the real fallback script as a subprocess (the same way the bash harness
invokes it) and asserts what it recovers.

Stdlib + pytest only. Run with the engine venv:
    uv run --directory servers/engine python -m pytest tests/test_dm_narration_fallback.py -p no:xdist
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import store
from models import SessionLogEntry

ENGINE_DIR = Path(__file__).resolve().parents[1]          # servers/engine
REPO_ROOT = ENGINE_DIR.parents[1]                          # repo root
FALLBACK = REPO_ROOT / "qa" / "dm_narration_fallback.py"


def _run(snapshot_path: Path) -> str:
    """Invoke the fallback exactly as qa/lib_beat_driver.sh does: python3 <script> <snapshot>."""
    out = subprocess.run(
        [sys.executable, str(FALLBACK), str(snapshot_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


def _seed(tmp_path: Path, monkeypatch, *, campaign_id: str, session_id: str,
          entries: list[SessionLogEntry], snapshot_extra: dict | None = None) -> Path:
    """Write a snapshot.json + the session log (via the engine's real writer) under a tmp state
    dir, and return the snapshot path. monkeypatch points the engine store at the tmp dir."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAWDND_STATE_DIR", str(tmp_path))
    camp_dir = tmp_path / "campaigns" / campaign_id
    camp_dir.mkdir(parents=True, exist_ok=True)
    snap = {"id": campaign_id, "active_session_id": session_id, "day": 1}
    if snapshot_extra:
        snap.update(snapshot_extra)
    snap_path = camp_dir / "snapshot.json"
    snap_path.write_text(json.dumps(snap), encoding="utf-8")
    for e in entries:
        store.append_log(campaign_id, session_id, e)   # the engine's real on-disk writer
    return snap_path


def test_recovers_trailing_prose_block(tmp_path, monkeypatch):
    # An older beat, then a roll (bookkeeping that breaks the trailing block), then THIS beat's
    # narration + a tagged dialogue line. Only the trailing block is recovered.
    entries = [
        SessionLogEntry(t=1.0, kind="system", text="Session started."),
        SessionLogEntry(t=2.0, kind="narration", text="An older beat — must NOT be recovered."),
        SessionLogEntry(t=3.0, kind="roll", text="Insight check: 13 vs DC 16 — FAIL"),
        SessionLogEntry(t=4.0, kind="narration",
                        text="You step into the Heapside warren; the reek of tallow closes over you."),
        SessionLogEntry(t=5.0, kind="dialogue", text="Mind your purse, stranger.", speaker="Osk"),
    ]
    snap = _seed(tmp_path, monkeypatch, campaign_id="camp_a", session_id="sess_a", entries=entries)
    out = _run(snap)
    assert out == (
        "You step into the Heapside warren; the reek of tallow closes over you.\n\n"
        "Osk: Mind your purse, stranger."
    )
    assert "older beat" not in out          # the roll broke the block; the old narration is excluded
    assert "Insight check" not in out       # roll bookkeeping is never surfaced as player prose


def test_non_prose_only_log_recovers_nothing(tmp_path, monkeypatch):
    # A beat where the DM only emitted bookkeeping (roll/system/combat) and never logged prose.
    entries = [
        SessionLogEntry(t=1.0, kind="system", text="Session started."),
        SessionLogEntry(t=2.0, kind="roll", text="d20=7"),
        SessionLogEntry(t=3.0, kind="combat", text="Goblin moves to E4."),
    ]
    snap = _seed(tmp_path, monkeypatch, campaign_id="camp_b", session_id="sess_b", entries=entries)
    assert _run(snap) == ""


def test_falls_back_to_last_session_id_when_no_active(tmp_path, monkeypatch):
    # active_session_id absent → use session_ids[-1], mirroring the viewer's resolution.
    entries = [SessionLogEntry(t=1.0, kind="narration", text="Prose from the last session id.")]
    snap = _seed(
        tmp_path, monkeypatch, campaign_id="camp_c", session_id="sess_c", entries=entries,
        snapshot_extra={"active_session_id": None, "session_ids": ["old", "sess_c"]},
    )
    assert _run(snap) == "Prose from the last session id."


def test_missing_session_log_recovers_nothing(tmp_path, monkeypatch):
    # Snapshot names a session whose log file was never written → graceful empty (no crash).
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    camp_dir = tmp_path / "campaigns" / "camp_d"
    camp_dir.mkdir(parents=True, exist_ok=True)
    snap = camp_dir / "snapshot.json"
    snap.write_text(json.dumps({"id": "camp_d", "active_session_id": "sess_d"}), encoding="utf-8")
    assert _run(snap) == ""


def test_path_traversal_session_id_is_rejected(tmp_path, monkeypatch):
    # A hostile/buggy session id that escapes the sessions dir must be refused (safety) → empty.
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    camp_dir = tmp_path / "campaigns" / "camp_e"
    camp_dir.mkdir(parents=True, exist_ok=True)
    snap = camp_dir / "snapshot.json"
    snap.write_text(
        json.dumps({"id": "camp_e", "active_session_id": "../../etc/passwd"}), encoding="utf-8"
    )
    assert _run(snap) == ""


def test_multiparagraph_block_is_bounded(tmp_path, monkeypatch):
    # A fat beat with many prose rows is capped (the chat must not get the whole log dumped).
    entries = [SessionLogEntry(t=float(i), kind="narration", text=f"Para {i}.") for i in range(1, 11)]
    snap = _seed(tmp_path, monkeypatch, campaign_id="camp_f", session_id="sess_f", entries=entries)
    out = _run(snap)
    # Only the last 6 prose rows survive; the earliest are dropped.
    assert "Para 10." in out and "Para 5." in out
    assert "Para 4." not in out and "Para 1." not in out
    assert out.count("\n\n") == 5           # 6 rows joined by a blank line → 5 separators


def test_malformed_snapshot_recovers_nothing(tmp_path, monkeypatch):
    # A half-written / corrupt snapshot.json must not crash the fallback.
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    camp_dir = tmp_path / "campaigns" / "camp_g"
    camp_dir.mkdir(parents=True, exist_ok=True)
    snap = camp_dir / "snapshot.json"
    snap.write_text("{not valid json", encoding="utf-8")
    assert _run(snap) == ""


# ── #357 re-scope (nb3): never recover a 3rd-person setup brief / game-system notation ──────

def test_coldopen_setup_brief_is_not_recovered(tmp_path, monkeypatch):
    # The EXACT nb3 failure: on the cold open the DM logged ONLY a 3rd-person setup brief in
    # game-system notation (a leading ALLCAPS label + a "(tiefling wizard, PC)" sheet tag) and
    # ended its turn with empty reply text. Recovering that brief showed the player developer
    # notation, not a scene. The fallback must now recover NOTHING here (blank > notation).
    entries = [
        SessionLogEntry(t=1.0, kind="system", text="Session 1 began: Winter After the War"),
        SessionLogEntry(
            t=2.0, kind="narration",
            text=("COLD OPEN — ARRIVAL: Rolan (tiefling wizard, PC) walks toward Sorcerous "
                  "Sundries to pick up reagents. A new Flaming Fist checkpoint blocks the lane "
                  "near Siltwharf Rise. Rolan joins the back of the queue."),
        ),
    ]
    snap = _seed(tmp_path, monkeypatch, campaign_id="camp_h", session_id="sess_h", entries=entries)
    out = _run(snap)
    assert out == ""
    assert "COLD OPEN" not in out
    assert "(tiefling wizard, PC)" not in out


def test_setup_brief_breaks_block_but_real_scene_after_survives(tmp_path, monkeypatch):
    # A setup brief followed by the REAL 2nd-person opening scene: the brief is excluded
    # (treated like bookkeeping that breaks the trailing block); only the real scene is recovered.
    entries = [
        SessionLogEntry(t=1.0, kind="narration",
                        text="SETUP: Mara (PC) is a Harper agent newly arrived in the Lower City."),
        SessionLogEntry(
            t=2.0, kind="narration",
            text=("You stand at the mouth of Siltwharf Rise, the morning fog clinging to the "
                  "cobblestones, a Flaming Fist checkpoint blocking the lane ahead."),
        ),
        SessionLogEntry(t=3.0, kind="dialogue", text="Papers. Now.", speaker="Fist Sergeant"),
    ]
    snap = _seed(tmp_path, monkeypatch, campaign_id="camp_i", session_id="sess_i", entries=entries)
    out = _run(snap)
    assert out == (
        "You stand at the mouth of Siltwharf Rise, the morning fog clinging to the "
        "cobblestones, a Flaming Fist checkpoint blocking the lane ahead.\n\n"
        "Fist Sergeant: Papers. Now."
    )
    assert "SETUP:" not in out and "(PC)" not in out


def test_real_2nd_person_prose_with_innocent_parens_survives(tmp_path, monkeypatch):
    # GUARD against over-matching: real 2nd-person prose with an in-fiction parenthetical that
    # does NOT carry a PC/NPC/level token must be recovered untouched (no false positive).
    entries = [
        SessionLogEntry(
            t=1.0, kind="narration",
            text=("You duck beneath the awning (or what's left of it) as the rain hammers the "
                  "tin roofs of Heapside, and a hooded figure waits by the well."),
        ),
    ]
    snap = _seed(tmp_path, monkeypatch, campaign_id="camp_j", session_id="sess_j", entries=entries)
    out = _run(snap)
    assert out.startswith("You duck beneath the awning")
    assert "hooded figure" in out
