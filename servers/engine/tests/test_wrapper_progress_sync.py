"""#749 SYNC TEST — the wrapper progress-heartbeat lines must be IDENTICAL everywhere.

The wrapper-authored heartbeat (#743) emits a small rotation of "the scene is arriving"
lines from the play/QA bash wrappers, and FOUR other surfaces must agree on those exact
strings or the repair silently regresses:

  - ``servers/engine/wrapper_progress.py``  — the ONE python source of truth; the engine's
    memory filters (recap / FTS ledger / lean re-ground tail) exact-match against it.
  - ``qa/wrapper_progress_lines.py``        — the qa-side accessor (re-export) used by
    ``qa/dm_narration_fallback.py`` (a stdlib-only standalone script).
  - ``viewer/openworlds/screen-table.jsx``  — ``_WRAPPER_PROGRESS_LINES`` (sanitize drops
    these from rendering; app.jsx flips the live-progress state on them at /events ingest).
  - ``qa/lib_beat_driver.sh``               — ``CLAWDND_OPENING_PROGRESS_TEXT`` +
    ``CLAWDND_MOVE_PROGRESS_TEXTS`` (the claude-DM wrappers' emit rotation).
  - ``scripts/play_codex_dm.sh``            — ``OPENING_PROGRESS_TEXT`` +
    ``MOVE_PROGRESS_TEXTS`` (the codex-DM wrapper's emit rotation).

If ANY side drifts (a reworded teaser, an added rotation line), the heartbeat would either
render as canned prose to the player or leak into engine memory — so this test parses the
jsx and both shell files with regexes and FAILS on the first divergent byte.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import wrapper_progress

ENGINE_DIR = Path(__file__).resolve().parents[1]   # servers/engine
REPO_ROOT = ENGINE_DIR.parents[1]                  # repo root
QA_SHIM = REPO_ROOT / "qa" / "wrapper_progress_lines.py"
SCREEN_TABLE = REPO_ROOT / "viewer" / "openworlds" / "screen-table.jsx"
LIB_BEAT_DRIVER = REPO_ROOT / "qa" / "lib_beat_driver.sh"
PLAY_CODEX_DM = REPO_ROOT / "scripts" / "play_codex_dm.sh"


def _quoted_strings(block: str) -> list[str]:
    """All double-quoted string literals in a source block, in order."""
    return [m.group(1) for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', block)]


def _jsx_lines() -> list[str]:
    src = SCREEN_TABLE.read_text(encoding="utf-8")
    m = re.search(r"_WRAPPER_PROGRESS_LINES\s*=\s*new Set\(\[(.*?)\]\)", src, re.S)
    assert m, "screen-table.jsx no longer defines _WRAPPER_PROGRESS_LINES = new Set([...])"
    return _quoted_strings(m.group(1))


def _sh_rotation(path: Path, opening_var: str, moves_var: str) -> tuple[str, list[str]]:
    src = path.read_text(encoding="utf-8")
    mo = re.search(rf'^{opening_var}="([^"]*)"', src, re.M)
    assert mo, f"{path.name} no longer defines {opening_var}"
    mm = re.search(rf"^{moves_var}=\((.*?)^\)", src, re.M | re.S)
    assert mm, f"{path.name} no longer defines {moves_var}=( ... )"
    moves = _quoted_strings(mm.group(1))
    assert moves, f"{path.name}: empty {moves_var} rotation"
    return mo.group(1), moves


# --- the python canonical is sane ---------------------------------------------------------


def test_python_canonical_shape():
    assert wrapper_progress.WRAPPER_PROGRESS_LINES[0] == wrapper_progress.WRAPPER_OPENING_PROGRESS_LINE
    assert (
        tuple(wrapper_progress.WRAPPER_PROGRESS_LINES[1:])
        == tuple(wrapper_progress.WRAPPER_MOVE_PROGRESS_LINES)
    )
    # No duplicates — a duplicate would silently shrink the jsx Set comparison.
    assert len(set(wrapper_progress.WRAPPER_PROGRESS_LINES)) == len(wrapper_progress.WRAPPER_PROGRESS_LINES)


def test_is_wrapper_progress_line_exact_trim_match():
    line = wrapper_progress.WRAPPER_OPENING_PROGRESS_LINE
    assert wrapper_progress.is_wrapper_progress_line(line)
    assert wrapper_progress.is_wrapper_progress_line(f"  {line}\n")   # trim, like the jsx .trim()
    assert not wrapper_progress.is_wrapper_progress_line(line + " More prose.")  # exact, not substring
    assert not wrapper_progress.is_wrapper_progress_line("You step into the warren.")
    assert not wrapper_progress.is_wrapper_progress_line("")
    assert not wrapper_progress.is_wrapper_progress_line(None)


# --- cross-surface sync -------------------------------------------------------------------


def test_jsx_set_matches_python():
    assert set(_jsx_lines()) == set(wrapper_progress.WRAPPER_PROGRESS_LINES), (
        "screen-table.jsx _WRAPPER_PROGRESS_LINES drifted from servers/engine/wrapper_progress.py"
    )


def test_jsx_exports_shared_window_constant():
    src = SCREEN_TABLE.read_text(encoding="utf-8")
    assert "window.isWrapperProgressLine" in src, (
        "screen-table.jsx must export window.isWrapperProgressLine (app.jsx's /events ingest uses it)"
    )
    assert "window.WRAPPER_PROGRESS_LINES" in src, (
        "screen-table.jsx must export window.WRAPPER_PROGRESS_LINES (the shared constant)"
    )


def test_lib_beat_driver_rotation_matches_python():
    opening, moves = _sh_rotation(
        LIB_BEAT_DRIVER, "CLAWDND_OPENING_PROGRESS_TEXT", "CLAWDND_MOVE_PROGRESS_TEXTS"
    )
    assert opening == wrapper_progress.WRAPPER_OPENING_PROGRESS_LINE
    assert moves == list(wrapper_progress.WRAPPER_MOVE_PROGRESS_LINES), (
        "qa/lib_beat_driver.sh CLAWDND_MOVE_PROGRESS_TEXTS drifted from wrapper_progress.py "
        "(order matters: the emit rotation indexes by beat)"
    )


def test_play_codex_dm_rotation_matches_python():
    opening, moves = _sh_rotation(PLAY_CODEX_DM, "OPENING_PROGRESS_TEXT", "MOVE_PROGRESS_TEXTS")
    assert opening == wrapper_progress.WRAPPER_OPENING_PROGRESS_LINE
    assert moves == list(wrapper_progress.WRAPPER_MOVE_PROGRESS_LINES), (
        "scripts/play_codex_dm.sh MOVE_PROGRESS_TEXTS drifted from wrapper_progress.py"
    )


def test_qa_shim_reexports_canonical():
    spec = importlib.util.spec_from_file_location("qa_wrapper_progress_lines", QA_SHIM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert tuple(mod.WRAPPER_PROGRESS_LINES) == tuple(wrapper_progress.WRAPPER_PROGRESS_LINES)
    assert mod.is_wrapper_progress_line(wrapper_progress.WRAPPER_OPENING_PROGRESS_LINE)
    assert not mod.is_wrapper_progress_line("You step into the warren.")
