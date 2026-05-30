#!/usr/bin/env python3
"""Empty-narration fallback for the play/QA DM resolver loop (issue #357).

The play/QA loops record the DM turn's FINAL reply text to the chat panel
(`chatlog dm "$DMSG"`). But a DM turn can end on a tool call (e.g. its last act was
`log_event`/`roll`) or a bare 3rd-person status line, leaving that final `result` text
EMPTY -- so the player-facing chat shows nothing even though the engine work happened and
the DM logged real 2nd-person prose via `log_event(kind="narration"/"dialogue")`. That prose
lands in the engine's per-session log (`campaigns/<id>/sessions/<sid>.jsonl`) and renders in
the viewer's `recentEvents`, but never reaches the chat.

This script recovers it: given the run's snapshot.json path, it locates the active session
log (mirroring the viewer's `_session_event_tail_from_dir`: `active_session_id`, else the
last `session_ids` entry, with the same bare-filename safety check) and prints the most recent
contiguous block of player-facing prose (narration | dialogue) the engine logged -- i.e. the
prose for the just-resolved beat. It is READ-ONLY on engine state (the engine stays the sole
writer); on a missing snapshot / session log / narration it prints nothing and the caller keeps
today's behavior (no regression).

It lives as a standalone file (not a heredoc inside `$(...)`) because the macOS system bash
(3.2.57) mis-parses a quoted heredoc nested in command substitution -- it is invoked by path
from `clawdnd_dm_narration_or_fallback` in qa/lib_beat_driver.sh.

Usage: python3 dm_narration_fallback.py <snapshot.json>
Prints the recovered prose to stdout (empty when there is nothing player-facing to recover).
"""
import json
import os
import sys

# Engine session-log kinds (SessionLogEntry.kind): narration | dialogue | roll | system | combat.
# Only narration + dialogue are player-facing prose; the rest are bookkeeping the player never reads.
PROSE_KINDS = {"narration", "dialogue"}

# Cap the recovered block so one fat multi-paragraph beat can't dump the whole log into the chat.
MAX_PROSE_ROWS = 6


def _recover(snap_path):
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
    # Same safety as the viewer's _safe_session_id: a session id must be a bare filename
    # component (no path separators / traversal) before we join it into a path.
    if not isinstance(sid, str) or not sid or sid != os.path.basename(sid) or sid in (".", ".."):
        return ""

    log_path = os.path.join(os.path.dirname(snap_path), "sessions", sid + ".jsonl")
    if not os.path.isfile(log_path):
        return ""

    # Walk the log; keep the TRAILING block of player-facing prose (the most recent beat the DM
    # logged). Any non-prose row (a roll/system/combat entry, e.g. a "Session started." system
    # line or a dice roll) breaks the trailing block, so we surface only the latest beat's
    # narration -- not the whole session's prose.
    block = []
    try:
        with open(log_path, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                kind = str(row.get("kind") or "narration").strip().lower()
                text = str(row.get("text") or "").strip()
                if kind in PROSE_KINDS and text:
                    speaker = str(row.get("speaker") or "").strip()
                    # A dialogue row keeps its speaker tag so a quoted line still reads as the
                    # NPC speaking; narration is the unattributed scene voice.
                    block.append("%s: %s" % (speaker, text) if (kind == "dialogue" and speaker) else text)
                else:
                    block = []
    except OSError:
        return ""

    if not block:
        return ""
    return "\n\n".join(block[-MAX_PROSE_ROWS:])


def main(argv):
    if len(argv) < 2:
        return 0
    out = _recover(argv[1])
    if out:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
