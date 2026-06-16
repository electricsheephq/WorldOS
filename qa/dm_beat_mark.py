#!/usr/bin/env python3
"""Pre-beat session-log mark + post-beat NEW-prose check (SYN-01, issues #757/#745).

The #357 empty-narration fallback (qa/dm_narration_fallback.py) recovers the engine-logged
prose when a DM turn ends with empty reply text. That recovery is GENUINE only when the DM
logged NEW player-facing prose THIS beat and then died before its final reply. When the beat
was fully dead, the only recoverable prose is the PREVIOUS beat's — recycling it masks the
dead beat as "resolved" (audit F12-14): the chat row dedups as already-logged, gets stamped
``engine_logged``, and the client hides it, so the player sees nothing while the harness
counts a resolved turn.

This script is the discriminator:

  mark  <state_dir> <mark_file>   — BEFORE a DM beat's first attempt: record the active
                                    session log file + its current line count (append-only,
                                    so "lines past the mark" == "rows logged this beat").
  check <state_dir> <mark_file>   — AFTER the beat: exit 0 iff at least one NEW player-facing
                                    prose row (narration | dialogue; wrapper heartbeats and
                                    setup-brief system-notation excluded — the exact filters
                                    the #357 fallback itself applies) was logged past the
                                    mark; exit 1 when everything recoverable predates the beat.

FAIL-OPEN DISCIPLINE: this is best-effort plumbing on the beat path. A missing/corrupt mark,
an unreadable log, or ANY internal failure exits 0 ("assume genuine") so a broken checkout can
only ever degrade to today's pre-SYN-01 behavior — it must never fail a healthy recovery.

Session-log resolution mirrors dm_narration_fallback._recover (active_session_id, else the
last session_ids entry, with the same bare-filename safety check); snapshot selection mirrors
clawdnd_snapshot_path in qa/lib_beat_driver.sh (the LARGEST non-empty snapshot). It lives as a
standalone file (not a heredoc inside ``$(...)``) because the macOS system bash 3.2 mis-parses
a quoted heredoc nested in command substitution — invoked by path from clawdnd_dm_prebeat_mark
/ clawdnd_dm_logged_new_prose in qa/lib_beat_driver.sh.
"""
import json
import os
import sys

# Reuse the #357 fallback's own notion of "player-facing prose" (same dir, same python3) so
# the two can never drift; degrade to kind-filter-only on a broken checkout (fail-open: a
# wrapper line would then count as prose, which can only WIDEN "genuine" — never fail a beat).
try:
    from dm_narration_fallback import (
        PROSE_KINDS,
        _is_system_notation,
        is_wrapper_progress_line,
    )
except Exception:  # pragma: no cover - only on a broken checkout
    PROSE_KINDS = {"narration", "dialogue"}

    def _is_system_notation(_text):
        return False

    def is_wrapper_progress_line(_text):
        return False


def _snapshot_path(state_dir):
    """The LARGEST non-empty snapshot under <state_dir>/campaigns — mirrors the shell-side
    clawdnd_snapshot_path (find -size +1c | ls -S | head -1)."""
    best, best_size = "", 1  # >1 byte, matching find's -size +1c
    root = os.path.join(state_dir, "campaigns")
    try:
        names = os.listdir(root)
    except OSError:
        return ""
    for name in names:
        p = os.path.join(root, name, "snapshot.json")
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if size > best_size:
            best, best_size = p, size
    return best


def _session_log_path(snap_path):
    """The ACTIVE session log for a snapshot — mirrors dm_narration_fallback._recover."""
    try:
        with open(snap_path, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return ""
    if not isinstance(snap, dict):
        return ""
    sid = snap.get("active_session_id")
    if not sid:
        ids = snap.get("session_ids")
        if isinstance(ids, list) and ids:
            sid = ids[-1]
    if not isinstance(sid, str) or not sid or sid != os.path.basename(sid) or sid in (".", ".."):
        return ""
    return os.path.join(os.path.dirname(snap_path), "sessions", sid + ".jsonl")


def _line_count(path):
    n = 0
    try:
        with open(path, encoding="utf-8") as f:
            for _ in f:
                n += 1
    except OSError:
        return 0
    return n


def _is_new_prose(row):
    """The same player-facing-prose filter the #357 fallback applies: narration|dialogue with
    non-empty text, excluding wrapper heartbeats + setup-brief system notation."""
    if not isinstance(row, dict):
        return False
    kind = str(row.get("kind") or "narration").strip().lower()
    text = str(row.get("text") or "").strip()
    if kind not in PROSE_KINDS or not text:
        return False
    if kind == "narration" and (_is_system_notation(text) or is_wrapper_progress_line(text)):
        return False
    return True


def cmd_mark(state_dir, mark_file, first=None):
    snap = _snapshot_path(state_dir)
    log_path = _session_log_path(snap) if snap else ""
    lines = _line_count(log_path) if log_path and os.path.isfile(log_path) else 0
    payload = {"session": os.path.abspath(log_path) if log_path else "", "lines": lines}
    # FIX 2(a) (#623): record the beat's first/cold-open signal so cmd_check can tell a TRUE
    # cold open (first=1: no prior session legitimately existed) from a CONTINUING beat whose
    # mark came back EMPTY (first=0: a mark-write bug — the baseline was lost, so any "later"
    # prose the #357 fallback recovers is the PREVIOUS beat's, recycled). Only "0"/"1" are
    # recorded; anything else (or absent) leaves the legacy fail-open behavior in cmd_check.
    if first in ("0", "1"):
        payload["first"] = first
    with open(mark_file, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return 0


def cmd_check(state_dir, mark_file):
    try:
        with open(mark_file, encoding="utf-8") as f:
            mark = json.load(f)
        marked_session = str(mark.get("session") or "")
        marked_lines = int(mark.get("lines") or 0)
        marked_first = str(mark.get("first") or "")
    except Exception:
        return 0  # unreadable mark -> fail OPEN (assume genuine; legacy behavior)
    snap = _snapshot_path(state_dir)
    if not snap:
        return 1  # nothing recoverable exists at all
    cur = _session_log_path(snap)
    if not cur or not os.path.isfile(cur):
        return 1
    # FIX 2(a) (#623): an EMPTY marked_session on a CONTINUING beat (first=0) is a mark-write
    # bug — no baseline was captured, so scanning from line 0 would match the PREVIOUS beat's
    # prose as "new" and stamp a recycled (dead) beat fallback_recovered:true. Force-fail (1 =
    # NOT genuine) here. We require BOTH an empty mark AND the recorded first=0 signal so a TRUE
    # first-prose-then-die cold open (first=1, where no session legitimately existed at mark
    # time) is NOT wrongly failed — it keeps the legacy scan-from-0 path below. A mark WITHOUT a
    # recorded first signal (legacy/external callers) also keeps the legacy fail-open path.
    if not marked_session and cur and marked_first == "0":
        return 1
    # A DIFFERENT session file than the marked one (the beat started a new session, or no
    # session existed at mark time) means every row in it is new — scan from line 0.
    skip = marked_lines if os.path.abspath(cur) == marked_session else 0
    try:
        with open(cur, encoding="utf-8") as f:
            for i, raw in enumerate(f):
                if i < skip:
                    continue
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if _is_new_prose(row):
                    return 0  # NEW player-facing prose logged this beat -> genuine
    except OSError:
        return 0  # unreadable log -> fail OPEN
    return 1  # nothing new -> anything recovered is recycled pre-beat prose


def main(argv):
    if len(argv) < 4 or argv[1] not in ("mark", "check"):
        print("usage: dm_beat_mark.py mark|check <state_dir> <mark_file> [first]", file=sys.stderr)
        return 0  # never fail a beat over a usage error
    try:
        if argv[1] == "mark":
            # FIX 2(a) (#623): optional 5th arg = the beat's first/cold-open signal ("1"|"0").
            first = argv[4] if len(argv) > 4 else None
            return cmd_mark(argv[2], argv[3], first)
        return cmd_check(argv[2], argv[3])
    except Exception:
        return 0  # any internal failure fails OPEN


if __name__ == "__main__":
    sys.exit(main(sys.argv))
