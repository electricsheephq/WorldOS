"""Session recaps: turn a session log into a "Previously on..." narrative.

Pure module (no MCP, no I/O of its own except the thin store-backed convenience
wrapper). `format_recap` condenses the tail of a session log into a short prose
recap a DM can read aloud when play resumes after compaction or a break; it
keeps the story beats (narration, dialogue, combat) and drops the bookkeeping
noise (dice rolls, system messages). `recap_from_store` is the persistence-aware
front door used by the server's recap tool.
"""

from __future__ import annotations

from models import SessionLogEntry
from wrapper_progress import is_wrapper_progress_line

# Kinds that carry the story. Rolls and system messages are bookkeeping noise we
# leave out of a "Previously on..." recap.
_STORY_KINDS = frozenset({"narration", "dialogue", "combat"})

_INTRO = "Previously on your adventure..."
_EMPTY = "This is the start of a new adventure. The story has yet to be written."


def _clean(s: str) -> str:
    """Collapse whitespace/newlines and neutralize embedded double-quotes so a log
    entry can't break the recap's quoting or inject spurious narration."""
    return " ".join(s.split()).replace('"', "'")


def _beat(entry: SessionLogEntry) -> str:
    """Render a single log entry as one recap line."""
    text = _clean(entry.text or "")
    if not text:
        return ""
    if entry.kind == "dialogue":
        speaker = _clean(entry.speaker or "")
        if speaker:
            return f'{speaker} said, "{text}"'
        return f'A voice said, "{text}"'
    # narration / combat (and any other story kind): present the text as-is.
    return text


def format_recap(entries: list[SessionLogEntry], max_entries: int = 12) -> str:
    """Build a "Previously on your adventure..." recap from recent log entries.

    Prefers narration, dialogue, and combat beats from the *most recent*
    `max_entries` story entries (in chronological order); dice rolls and system
    messages are ignored. An empty log (or a log with no story beats) yields a
    sensible new-adventure message.
    """
    if max_entries < 1:
        max_entries = 1

    # Keep only story beats, then take the most recent `max_entries` of them.
    # #749: the wrapper progress heartbeat ("Your move lands; attention gathers…") is a
    # liveness signal the QA/play wrappers log mid-turn, not story — reciting it in a
    # "Previously on…" recap reads as canned filler. Exact-match excluded.
    story = [
        e for e in entries
        if e.kind in _STORY_KINDS and not is_wrapper_progress_line(e.text)
    ]
    recent = story[-max_entries:]

    lines = [b for b in (_beat(e) for e in recent) if b]
    if not lines:
        return _EMPTY

    body = " ".join(lines)
    return f"{_INTRO} {body}"


def recap_from_store(campaign_id: str, session_id: str) -> str:
    """Read a session's log from the store and format a recap from it."""
    import store

    entries = store.read_log(campaign_id, session_id)
    return format_recap(entries)
