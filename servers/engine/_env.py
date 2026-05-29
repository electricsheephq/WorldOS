"""Backward-compatible env-var resolution for the WorldOS rename (issue #295, W0-E).

The project was renamed ClawDnD -> WorldOS. Env vars are migrating from the
``CLAWDND_*`` prefix to ``WORLDOS_*``. This is a NON-breaking layer: every read
site prefers ``WORLDOS_<X>`` but still falls back to the legacy ``CLAWDND_<X>``,
emitting a ONE-TIME stderr deprecation warning per legacy var. The legacy names
keep working for v1.x (the running app, QA scripts, and the macOS
``RepositoryLocator`` still set ``CLAWDND_*``); they are removed at v2.0.

Stdlib-only, zero project imports, so it's safe to import from any module
(including ones used at import time). Mirrored per server package because each
``servers/<pkg>`` is an isolated ``uv`` workspace with ``pythonpath = ["."]``.

Usage::

    from _env import env_var
    raw = env_var("STATE_DIR")                  # WORLDOS_STATE_DIR or CLAWDND_STATE_DIR
    backend = env_var("TTS_BACKEND", "kokoro")  # with a default

Or, when a call site already holds the full legacy name as a string/constant::

    token = env_var_legacy("CLAWDND_OPENCLAW_GATEWAY_TOKEN", "")
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Set

#: New canonical prefix.
WORLDOS_PREFIX = "WORLDOS_"
#: Legacy prefix (deprecated, warn-only fallback for v1.x).
LEGACY_PREFIX = "CLAWDND_"

# Legacy var names we've already warned about, so each emits its deprecation
# notice at most once per process (avoids spamming stderr on hot read paths).
_warned: Set[str] = set()


def _warn_once(legacy_name: str, worldos_name: str) -> None:
    if legacy_name in _warned:
        return
    _warned.add(legacy_name)
    print(
        f"[worldos] DEPRECATION: env var {legacy_name} is renamed to {worldos_name}; "
        f"the old name still works for v1.x but will be removed in v2.0.",
        file=sys.stderr,
    )


def env_var(suffix: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve a WorldOS env var by its suffix (the part after the prefix).

    Prefers ``WORLDOS_<suffix>``; falls back to the legacy ``CLAWDND_<suffix>``
    (warning once to stderr). Returns ``default`` when neither is set.

    ``suffix`` is given WITHOUT a prefix, e.g. ``env_var("STATE_DIR")`` reads
    ``WORLDOS_STATE_DIR`` then ``CLAWDND_STATE_DIR``.
    """
    worldos_name = WORLDOS_PREFIX + suffix
    legacy_name = LEGACY_PREFIX + suffix
    return _resolve(worldos_name, legacy_name, default)


def env_var_legacy(legacy_name: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve from a full legacy name (e.g. an existing ``CLAWDND_*`` constant).

    Derives the new name by swapping the ``CLAWDND_`` prefix for ``WORLDOS_`` and
    then behaves exactly like :func:`env_var`. Names without the legacy prefix are
    read as-is (no aliasing) — used for genuinely external vars like
    ``OPENCLAW_GATEWAY_TOKEN``.
    """
    if legacy_name.startswith(LEGACY_PREFIX):
        worldos_name = WORLDOS_PREFIX + legacy_name[len(LEGACY_PREFIX):]
        return _resolve(worldos_name, legacy_name, default)
    # Not a renamed var (e.g. OPENCLAW_*); read it straight, no alias/warn.
    return os.environ.get(legacy_name, default)


def _resolve(worldos_name: str, legacy_name: str, default: Optional[str]) -> Optional[str]:
    val = os.environ.get(worldos_name)
    if val is not None:
        return val
    val = os.environ.get(legacy_name)
    if val is not None:
        _warn_once(legacy_name, worldos_name)
        return val
    return default
