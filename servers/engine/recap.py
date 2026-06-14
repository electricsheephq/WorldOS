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

# F07-1 (issue #772): a kind="combat" row can be EITHER a narrative beat (the DM's
# prose: "the ogre roared") OR an engine bookkeeping row written by _log_combat_event,
# which stamps this schema into payload. The mechanical rows ("Tough 1 takes 5 force
# damage", "Turn advances to Tough 2") are not story — a "Previously on..." recap that
# recites them reads as a damage log. We keep narrative combat (payload None or lacking
# this schema) and drop only the schema-stamped rows. Distinct from #749/#763, which
# exact-matched only the wrapper-progress heartbeat.
_COMBAT_EVENT_SCHEMA = "clawdnd.combat_event.v1"


def _is_combat_bookkeeping(entry: SessionLogEntry) -> bool:
    """True iff this is an engine-authored combat-event row (schema-stamped payload),
    i.e. mechanical bookkeeping rather than a narrative combat beat."""
    if entry.kind != "combat":
        return False
    payload = entry.payload
    return isinstance(payload, dict) and payload.get("schema") == _COMBAT_EVENT_SCHEMA


_INTRO = "Previously on your adventure..."
_EMPTY = "This is the start of a new adventure. The story has yet to be written."

# SYN-08 / F07-5 / F14-16 (issue #805): the recap was COUNT-bounded (max_entries=12)
# but NOT SIZE-bounded — a verbatim full-text join, which reproduced 48,631B live
# every cold open when beats were fat (12 x ~4KB). These DEFAULTED budgets bound the
# bytes the per-beat recap surface carries WITHOUT dropping recency: each beat is
# soft-capped to a sentence boundary, and the whole recap to a total budget, trimming
# OLDEST-first so the newest beats (what the gates read) always survive.
# Source: docs/audits/ENGINE-AUDIT-2026-06-11.md (F07-5, F14-16, SYN-08).
_DEFAULT_MAX_ENTRY_CHARS = 400  # per-beat soft cap (prefers a sentence boundary)
_DEFAULT_MAX_CHARS = 6000  # total recap byte budget (~6KB), oldest-first trim


def _clean(s: str) -> str:
    """Collapse whitespace/newlines and neutralize embedded double-quotes so a log
    entry can't break the recap's quoting or inject spurious narration."""
    return " ".join(s.split()).replace('"', "'")


def _soft_truncate(text: str, max_chars: int) -> str:
    """Trim `text` to at most ~`max_chars`, preferring the LAST sentence boundary
    (``. ! ?``) at or before the cap so the recap reads as prose, not a mid-word
    cut. Falls back to the last word boundary, then a hard slice with an ellipsis.
    Short text (already within the cap) is returned unchanged (byte-identical)."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    window = text[:max_chars]
    # Prefer a sentence boundary; require it to land past the halfway mark so we
    # don't collapse a long beat down to its first tiny clause.
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut >= max_chars // 2:
        return window[: cut + 1]  # keep the terminal punctuation
    space = window.rfind(" ")
    if space >= max_chars // 2:
        return window[:space].rstrip() + "…"
    return window.rstrip() + "…"


def _beat(entry: SessionLogEntry, max_entry_chars: int = 0) -> str:
    """Render a single log entry as one recap line. ``max_entry_chars`` (>0) soft-
    caps the beat's own text at a sentence boundary BEFORE it is wrapped in the
    dialogue quoting, so the rendered line stays bounded (SYN-08)."""
    text = _clean(entry.text or "")
    if not text:
        return ""
    if max_entry_chars > 0:
        text = _soft_truncate(text, max_entry_chars)
    if entry.kind == "dialogue":
        speaker = _clean(entry.speaker or "")
        if speaker:
            return f'{speaker} said, "{text}"'
        return f'A voice said, "{text}"'
    # narration / combat (and any other story kind): present the text as-is.
    return text


def format_recap(
    entries: list[SessionLogEntry],
    max_entries: int = 12,
    max_chars: int = _DEFAULT_MAX_CHARS,
    max_entry_chars: int = _DEFAULT_MAX_ENTRY_CHARS,
) -> str:
    """Build a "Previously on your adventure..." recap from recent log entries.

    Prefers narration, dialogue, and combat beats from the *most recent*
    `max_entries` story entries (in chronological order); dice rolls and system
    messages are ignored. An empty log (or a log with no story beats) yields a
    sensible new-adventure message.

    SYN-08 / F07-5 / F14-16: the recap is BYTE-bounded as well as count-bounded.
    Each beat is soft-capped to ``max_entry_chars`` (sentence-boundary preferred)
    and the whole recap to a ``max_chars`` total budget, dropping OLDEST beats
    first so recency — the story memory the gates read — is preserved. Both caps
    default to sane budgets and are no-ops for the common short-beat case (set
    either to ``0`` to disable). No LLM summarization: this only trims, never
    paraphrases (engine reports, the DM narrates).
    """
    if max_entries < 1:
        max_entries = 1

    # Keep only story beats, then take the most recent `max_entries` of them.
    # #749: the wrapper progress heartbeat ("Your move lands; attention gathers…") is a
    # liveness signal the QA/play wrappers log mid-turn, not story — reciting it in a
    # "Previously on…" recap reads as canned filler. Exact-match excluded.
    # F07-1 (#772): schema-stamped combat-event rows are engine bookkeeping, not story —
    # excluded here while narrative combat beats stay.
    story = [
        e for e in entries
        if e.kind in _STORY_KINDS
        and not is_wrapper_progress_line(e.text)
        and not _is_combat_bookkeeping(e)
    ]
    recent = story[-max_entries:]

    lines = [b for b in (_beat(e, max_entry_chars) for e in recent) if b]
    if not lines:
        return _EMPTY

    # Total-budget trim (SYN-08): keep the NEWEST lines that fit, oldest-first drop.
    # The budget is measured against the body (intro is fixed overhead). With caps
    # disabled or a generous budget this keeps every line (byte-identical to before).
    if max_chars > 0:
        lines = _fit_budget(lines, max_chars)

    body = " ".join(lines)
    return f"{_INTRO} {body}"


def _fit_budget(lines: list[str], max_chars: int) -> list[str]:
    """Keep the most-recent suffix of `lines` whose joined length fits `max_chars`,
    always keeping at least the single newest line (a lone fat beat is already
    per-entry capped). Returns lines in their original (chronological) order."""
    kept_rev: list[str] = []
    used = 0
    for line in reversed(lines):  # newest first
        extra = len(line) + (1 if kept_rev else 0)  # +1 for the joining space
        if kept_rev and used + extra > max_chars:
            break
        kept_rev.append(line)
        used += extra
    return list(reversed(kept_rev))


def recap_from_store(campaign_id: str, session_id: str) -> str:
    """Read a session's log from the store and format a recap from it."""
    import store

    entries = store.read_log(campaign_id, session_id)
    return format_recap(entries)
