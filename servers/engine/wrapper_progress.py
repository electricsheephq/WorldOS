"""Wrapper-authored progress-heartbeat lines — the ONE python source of truth (#749).

The play/QA wrappers (qa/lib_beat_driver.sh, scripts/play_codex_dm.sh) write a short
canned "the scene is arriving" ``kind=narration`` row to the engine session log BEFORE
the DM model starts (#743), so the viewer's /events poll has a row to flip the player's
spinner within ~1s of a move. Those rows are a LIVENESS SIGNAL, not story:

  - the viewer must flip its live-progress state on them and NEVER render them
    (viewer/openworlds/app.jsx /events ingest + screen-table.jsx sanitize), and
  - the engine's memory consumers must EXCLUDE them — recap would otherwise recite the
    filler in "Previously on…", the FTS ledger would index it for recall, the lean
    re-ground tail (``scene_context``'s ``recent_narration``) would tell the DM the
    filler is its own canon, and qa/dm_narration_fallback.py would recover it as a
    dead beat's "prose".

Every surface exact-matches against THIS module (qa/wrapper_progress_lines.py re-exports
it for the stdlib-only qa scripts; the jsx + sh copies are pinned byte-identical by
tests/test_wrapper_progress_sync.py — edit the rotation HERE first, then mirror it in
screen-table.jsx, lib_beat_driver.sh and play_codex_dm.sh or that sync test fails).

Matching is EXACT on the trimmed line (mirroring screen-table.jsx's ``.trim()`` set
lookup), never substring — real DM prose that merely *mentions* a teaser survives.
Pure constants: no engine imports, safe for any consumer.
"""

from __future__ import annotations

WRAPPER_OPENING_PROGRESS_LINE = (
    "The first scene gathers around you; voices, risks, and choices come into focus."
)

# Continuing-beat rotation — ORDER MATTERS (the wrappers index it by beat number).
WRAPPER_MOVE_PROGRESS_LINES: tuple[str, ...] = (
    "Your choice takes hold; nearby voices, risks, and consequences begin to answer.",
    "The world turns with your action; the scene shifts toward its answer.",
    "Your move lands; attention gathers around what changes next.",
    "Momentum carries through the scene; consequences are beginning to surface.",
)

WRAPPER_PROGRESS_LINES: tuple[str, ...] = (
    WRAPPER_OPENING_PROGRESS_LINE,
    *WRAPPER_MOVE_PROGRESS_LINES,
)

_WRAPPER_PROGRESS_SET = frozenset(WRAPPER_PROGRESS_LINES)


def is_wrapper_progress_line(text: object) -> bool:
    """True iff ``text`` is exactly (after trimming) a wrapper-authored heartbeat line."""
    if not isinstance(text, str):
        return False
    return text.strip() in _WRAPPER_PROGRESS_SET
