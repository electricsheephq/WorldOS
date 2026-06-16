#!/usr/bin/env python3
"""Tests for qa/dm_beat_mark.py — the #357 recycle discriminator (SYN-01).

Focus: FIX 2(a) (#623). An EMPTY marked_session on a CONTINUING beat (first=0) is a mark-write
bug; scanning from line 0 would match the PREVIOUS beat's prose as "new" and stamp a recycled
(dead) beat fallback_recovered:true. cmd_check must force-fail (return 1) in that case — while
NEVER wrongly failing a true first-prose-then-die COLD OPEN (first=1, where no session
legitimately existed at mark time) and keeping the legacy fail-open for marks with no recorded
first signal.

Stdlib + pytest only; self-contained.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dm_beat_mark as dbm  # noqa: E402


def _write_session(state_dir: Path, campaign: str, sid: str, rows: list[dict]) -> Path:
    """Write campaigns/<campaign>/{snapshot.json, sessions/<sid>.jsonl} and return the log path."""
    camp = state_dir / "campaigns" / campaign
    sessions = camp / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    # snapshot must be > 1 byte and select this campaign (largest non-empty snapshot wins).
    (camp / "snapshot.json").write_text(
        json.dumps({"active_session_id": sid, "session_ids": [sid]}), encoding="utf-8"
    )
    log = sessions / f"{sid}.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""), encoding="utf-8")
    return log


def _prose_row(text: str) -> dict:
    return {"kind": "narration", "text": text}


# ── cmd_mark records the first signal ────────────────────────────────────────────

def test_cmd_mark_records_first_signal(tmp_path):
    state = tmp_path / "state"
    _write_session(state, "camp1", "sess1", [_prose_row("Opening prose.")])
    mark_file = state / ".dm_prebeat_mark"
    assert dbm.cmd_mark(str(state), str(mark_file), "0") == 0
    payload = json.loads(mark_file.read_text(encoding="utf-8"))
    assert payload.get("first") == "0"
    # cold open
    assert dbm.cmd_mark(str(state), str(mark_file), "1") == 0
    assert json.loads(mark_file.read_text(encoding="utf-8")).get("first") == "1"
    # absent signal -> no "first" key (legacy fail-open behavior in cmd_check)
    assert dbm.cmd_mark(str(state), str(mark_file), None) == 0
    assert "first" not in json.loads(mark_file.read_text(encoding="utf-8"))


# ── FIX 2(a): empty mark + continuing beat -> NOT genuine ─────────────────────────

def test_check_empty_mark_continuing_beat_is_not_genuine(tmp_path):
    # The mark-write bug: a CONTINUING beat's mark came back empty (no baseline). The session log
    # holds only the PREVIOUS beat's prose. cmd_check must return 1 (recycled, NOT genuine) so the
    # dead beat is not masked.
    state = tmp_path / "state"
    _write_session(state, "camp1", "sess1", [_prose_row("Previous beat's prose.")])
    mark_file = state / ".dm_prebeat_mark"
    # Simulate the empty mark a write-bug produced, but WITH the first=0 (continuing) signal.
    mark_file.write_text(json.dumps({"session": "", "lines": 0, "first": "0"}), encoding="utf-8")
    assert dbm.cmd_check(str(state), str(mark_file)) == 1


def test_check_empty_mark_cold_open_with_new_prose_stays_genuine(tmp_path):
    # HARD CONSTRAINT: a TRUE first-prose-then-die COLD OPEN (first=1) legitimately had no session
    # at mark time. Its prose IS new (logged this beat). It must NOT be force-failed — the legacy
    # scan-from-0 path applies and finds the new prose -> genuine (0).
    state = tmp_path / "state"
    _write_session(state, "camp1", "sess1", [_prose_row("The cold open's fresh opening prose.")])
    mark_file = state / ".dm_prebeat_mark"
    mark_file.write_text(json.dumps({"session": "", "lines": 0, "first": "1"}), encoding="utf-8")
    assert dbm.cmd_check(str(state), str(mark_file)) == 0


def test_check_empty_mark_no_first_signal_is_legacy_fail_open(tmp_path):
    # A mark with NO recorded first signal (legacy / external caller) keeps the legacy fail-open
    # scan-from-0 behavior — it must NOT be force-failed just because the session is empty.
    state = tmp_path / "state"
    _write_session(state, "camp1", "sess1", [_prose_row("Some prose.")])
    mark_file = state / ".dm_prebeat_mark"
    mark_file.write_text(json.dumps({"session": "", "lines": 0}), encoding="utf-8")
    assert dbm.cmd_check(str(state), str(mark_file)) == 0


# ── A normal mark with a real baseline still works (no regression) ────────────────

def test_check_real_baseline_new_prose_after_mark_is_genuine(tmp_path):
    # Mark captured a real baseline (the session existed with N lines). A NEW prose row was logged
    # AFTER the mark -> genuine (0). This is the healthy mid-session recovery path.
    state = tmp_path / "state"
    log = _write_session(state, "camp1", "sess1", [_prose_row("Beat 1 prose (baseline).")])
    mark_file = state / ".dm_prebeat_mark"
    # Mark AT the current baseline (1 line) via the real cmd_mark on a continuing beat.
    assert dbm.cmd_mark(str(state), str(mark_file), "0") == 0
    baseline = json.loads(mark_file.read_text(encoding="utf-8"))
    assert baseline["session"] != ""  # a real baseline WAS captured
    assert baseline["lines"] == 1
    # Now append a NEW prose row past the mark.
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(_prose_row("Beat 2 prose (new this beat).")) + "\n")
    assert dbm.cmd_check(str(state), str(mark_file)) == 0


def test_check_real_baseline_no_new_prose_is_recycled(tmp_path):
    # Mark captured a real baseline; NOTHING new was logged past it -> recycled (1), the existing
    # discriminator behavior (unchanged by FIX 2(a)).
    state = tmp_path / "state"
    _write_session(state, "camp1", "sess1", [_prose_row("Beat 1 prose (baseline).")])
    mark_file = state / ".dm_prebeat_mark"
    assert dbm.cmd_mark(str(state), str(mark_file), "0") == 0
    # No append: nothing new past the mark.
    assert dbm.cmd_check(str(state), str(mark_file)) == 1


def test_check_unreadable_mark_fails_open(tmp_path):
    # A corrupt/unreadable mark (a REAL baseline that we just can't parse) keeps fail-open (0).
    state = tmp_path / "state"
    _write_session(state, "camp1", "sess1", [_prose_row("Prose.")])
    mark_file = state / ".dm_prebeat_mark"
    mark_file.write_text("{not json", encoding="utf-8")
    assert dbm.cmd_check(str(state), str(mark_file)) == 0


def test_main_mark_passes_first_arg(tmp_path):
    # The CLI front door threads the optional 5th arg (first) into cmd_mark.
    state = tmp_path / "state"
    _write_session(state, "camp1", "sess1", [_prose_row("Prose.")])
    mark_file = state / ".dm_prebeat_mark"
    rc = dbm.main(["dm_beat_mark.py", "mark", str(state), str(mark_file), "0"])
    assert rc == 0
    assert json.loads(mark_file.read_text(encoding="utf-8")).get("first") == "0"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:xdist"]))
