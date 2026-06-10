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

#357 RE-SCOPE (nb3 cold-open): the recovered prose must be a PLAYER-FACING 2nd-person scene,
never a 3rd-person setup brief / game-system notation. On the cold open the DM did its setup
silently via tools and logged only a 3rd-person brief ("COLD OPEN — ARRIVAL: Rolan (tiefling
wizard, PC) walks toward…") via log_event(kind="narration"), then ended its turn with EMPTY
reply text -- so this fallback (then) recovered that brief and the player saw developer/GM
notation instead of an opening scene. We now REJECT the high-confidence setup-brief shapes (a
leading ALLCAPS label header; a (… PC)/(… NPC)/(level N …) character-sheet tag) so a brief is
treated like bookkeeping and never reaches the chat; a real 2nd-person scene always survives.

It lives as a standalone file (not a heredoc inside `$(...)`) because the macOS system bash
(3.2.57) mis-parses a quoted heredoc nested in command substitution -- it is invoked by path
from `clawdnd_dm_narration_or_fallback` in qa/lib_beat_driver.sh.

Usage: python3 dm_narration_fallback.py <snapshot.json>
Prints the recovered prose to stdout (empty when there is nothing player-facing to recover).
"""
import json
import os
import re
import sys

# #749: the wrapper progress heartbeat ("Your move lands; attention gathers…") is a canned
# liveness row the play/QA wrappers log BEFORE the DM turn — never recoverable prose. The
# shared constants live in servers/engine/wrapper_progress.py (re-exported by the sibling
# qa/wrapper_progress_lines.py). This script is best-effort plumbing in the beat path, so a
# missing shim degrades to "no wrapper filtering" rather than a crash; the engine test suite
# (tests/test_dm_narration_fallback.py) proves the import works in-repo.
try:
    from wrapper_progress_lines import is_wrapper_progress_line
except Exception:  # pragma: no cover - only on a broken checkout
    def is_wrapper_progress_line(_text):
        return False

# Engine session-log kinds (SessionLogEntry.kind): narration | dialogue | roll | system | combat.
# Only narration + dialogue are player-facing prose; the rest are bookkeeping the player never reads.
PROSE_KINDS = {"narration", "dialogue"}

# Cap the recovered block so one fat multi-paragraph beat can't dump the whole log into the chat.
MAX_PROSE_ROWS = 6

# #357 re-scope (nb3): a narration row can ALSO be a 3rd-person SETUP BRIEF in game-system
# notation that the DM logged via log_event during silent cold-open setup -- e.g.
#   "COLD OPEN — ARRIVAL: Rolan (tiefling wizard, PC) walks toward Sorcerous Sundries…"
# That is the DM's scratchpad, NOT a scene the player can read and respond to. Recovering it
# (what #360 did) is WORSE than recovering nothing: the player sees developer/GM notation
# instead of prose. So before a narration row qualifies as recoverable player-facing prose we
# REJECT the high-confidence setup-brief / system-notation shapes. HIGH-CONFIDENCE ONLY -- a
# real 2nd-person scene ("You step into the Heapside warren…") must always survive.

# A leading ALLCAPS structural LABEL followed by ':' or ' — ' — the chronicle/brief header the
# DM writes for itself ("COLD OPEN — ARRIVAL:", "SETUP:", "BRIEF —", "CHRONICLE:"). Two+ caps
# words so an in-fiction shout ("HELP!") or a single proper noun never trips it.
_SETUP_LABEL = re.compile(r"^\s*[A-Z][A-Z'’]+(?:[ \-—–][A-Z][A-Z'’]+){0,5}\s*(?::|—|–|-\s)")
# A parenthetical CHARACTER-SHEET tag — "(tiefling wizard, PC)", "(PC)", "(NPC)",
# "(level 3 fighter)". The PC/NPC/level/class role annotation is pure game-system notation;
# in-fiction parentheticals ("(or so the rumor went)") don't carry these tokens.
_SHEET_TAG = re.compile(
    r"\((?:[^)]*\b(?:PC|NPC)\b[^)]*|[^)]*\blevel\s*\d+[^)]*)\)",
    re.IGNORECASE,
)


def _is_system_notation(text):
    """True when a narration row is a 3rd-person setup brief / game-system notation rather than
    a player-facing scene (#357). Conservative: only the high-confidence shapes -- a leading
    ALLCAPS label header, or a (… PC)/(… NPC)/(level N …) character-sheet tag."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_SETUP_LABEL.match(t) or _SHEET_TAG.search(t))


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
                # A 3rd-person setup brief / system-notation NARRATION row (#357) is the DM's
                # scratchpad, not a scene — treat it like bookkeeping: it breaks the trailing
                # block and is never recovered (showing the player notation is worse than blank).
                # Dialogue rows are always a quoted character line, so they're never system-notation.
                # #749: the wrapper progress heartbeat gets the SAME treatment — it is canned
                # liveness filler logged BEFORE the DM turn. Crucially it BREAKS the block (it is
                # not transparently skipped): a heartbeat-only (dead) beat must recover NOTHING,
                # because stitching the PRIOR beat's stale prose under a fresh heartbeat would
                # mask the dead beat as 'resolved'.
                if kind == "narration" and text and (
                    _is_system_notation(text) or is_wrapper_progress_line(text)
                ):
                    block = []
                    continue
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
