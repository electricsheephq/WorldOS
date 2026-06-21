"""WorldOS engine — the SINGLE SOURCE OF TRUTH for the product version.

Versioning Phase-1 (toward "complete a milestone → tag/release"): the product version
lives HERE as ``__version__`` and is mirrored byte-for-byte into the repo-root ``VERSION``
file. The test ``qa/test_version_consistency.py`` asserts ``VERSION == __version__`` so the
two can never silently drift, and a soft check notes when the latest git tag lags.

This is deliberately a tiny, dependency-free module so anything in the engine (or the QA /
release tooling) can ``from __version__ import __version__`` without importing pydantic, the
MCP server, or any heavy surface. It is ADDITIVE — nothing in the engine's runtime behavior
depends on it; it is the version a release/tag links to, not a gameplay knob.

NOTE — this is the *product* version. It is distinct from:
  * ``servers/engine/pyproject.toml``'s package ``version`` (build/packaging metadata), and
  * ``Campaign.schema_version`` in ``models.py`` (the on-disk snapshot schema revision, bumped
    only on a breaking snapshot change — NOT on every product release).
"""

from __future__ import annotations

__version__ = "1.0.5"

# A parsed ``(major, minor, patch)`` tuple for callers that want to compare versions
# numerically. Best-effort: a non-numeric / pre-release suffix simply isn't included.
try:
    __version_info__ = tuple(int(p) for p in __version__.split(".")[:3])
except ValueError:  # pragma: no cover - defensive; __version__ is a hand-edited constant
    __version_info__ = ()
