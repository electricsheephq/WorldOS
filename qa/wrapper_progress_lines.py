"""qa-side accessor for the wrapper progress-heartbeat lines (#749).

The ONE python source of truth is ``servers/engine/wrapper_progress.py`` (the engine's
memory filters import it directly; the engine must keep filtering even if it were ever
deployed without qa/). This shim loads that module by file path — qa scripts run under
the system ``python3`` with no engine on ``sys.path`` — and re-exports its names so
stdlib-only qa tools (``qa/dm_narration_fallback.py``) share the exact same constants.

``tests/test_wrapper_progress_sync.py`` pins this re-export (and the jsx/sh copies)
byte-identical to the canonical module.
"""

from __future__ import annotations

import importlib.util
import os

_CANONICAL = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "servers", "engine", "wrapper_progress.py",
    )
)

_spec = importlib.util.spec_from_file_location("_worldos_wrapper_progress", _CANONICAL)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

WRAPPER_OPENING_PROGRESS_LINE = _mod.WRAPPER_OPENING_PROGRESS_LINE
WRAPPER_MOVE_PROGRESS_LINES = _mod.WRAPPER_MOVE_PROGRESS_LINES
WRAPPER_PROGRESS_LINES = _mod.WRAPPER_PROGRESS_LINES
is_wrapper_progress_line = _mod.is_wrapper_progress_line
