"""WorldOS env-var resolution (``WORLDOS_*`` only).

Every reader and writer uses the ``WORLDOS_*`` prefix.

Stdlib-only, zero project imports, so it's safe to import from any module (including ones
used at import time). Mirrored per server package because each ``servers/<pkg>`` is an
isolated ``uv`` workspace with ``pythonpath = ["."]``.

Usage::

    from _env import env_var
    raw = env_var("STATE_DIR")                  # WORLDOS_STATE_DIR
    backend = env_var("TTS_BACKEND", "kokoro")  # with a default

``env_var_legacy`` reads a FULL env name as-is — used for genuinely external vars that
have no ``WORLDOS_`` prefix, e.g. ``OPENCLAW_GATEWAY_TOKEN``.
"""

from __future__ import annotations

import os
from typing import Optional

#: The canonical (and only) prefix.
WORLDOS_PREFIX = "WORLDOS_"


def env_var(suffix: str, default: Optional[str] = None) -> Optional[str]:
    """Resolve a WorldOS env var by its suffix (the part after the prefix).

    Reads ``WORLDOS_<suffix>``; returns ``default`` when unset. ``suffix`` is given
    WITHOUT a prefix, e.g. ``env_var("STATE_DIR")`` reads ``WORLDOS_STATE_DIR``.
    """
    return os.environ.get(WORLDOS_PREFIX + suffix, default)


def env_var_legacy(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read a FULL env name as-is (e.g. ``OPENCLAW_GATEWAY_TOKEN``, ``WORLDOS_*``).

    Kept for call sites that hold a complete env-var name rather than a suffix.
    """
    return os.environ.get(name, default)
