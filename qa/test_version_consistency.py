#!/usr/bin/env python3
"""Versioning Phase-1 consistency tests.

The HARD check: the repo-root ``VERSION`` file MUST equal ``servers/engine/__version__.py``'s
``__version__`` (the single source of truth) — so the two can never silently drift. The git-tag
alignment is a SOFT check: it only emits a note (never fails) when the latest tag lags the
declared version, because a tag legitimately lags between "bump VERSION" and "cut the tag".

Run:
    uv run --directory servers/engine python -m pytest ../../qa/test_version_consistency.py -q -p no:xdist
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = REPO_ROOT / "servers" / "engine"
VERSION_FILE = REPO_ROOT / "VERSION"


def _load_engine_version() -> str:
    """Import servers/engine/__version__.py without importing the whole engine package."""
    sys.path.insert(0, str(ENGINE_DIR))
    import __version__ as engine_version  # noqa: E402

    return engine_version.__version__


def test_version_file_exists():
    assert VERSION_FILE.exists(), f"repo-root VERSION file missing at {VERSION_FILE}"


def test_engine_version_is_semver_like():
    v = _load_engine_version()
    assert re.match(r"^\d+\.\d+\.\d+", v), f"__version__ {v!r} is not semver-like (X.Y.Z…)"


def test_version_file_matches_engine_dunder():
    """THE hard gate: VERSION == servers/engine/__version__.__version__."""
    file_version = VERSION_FILE.read_text(encoding="utf-8").strip()
    engine_version = _load_engine_version()
    assert file_version == engine_version, (
        f"VERSION file ({file_version!r}) != servers/engine/__version__.__version__ "
        f"({engine_version!r}) — the single source of truth is __version__.py; "
        f"mirror it into VERSION"
    )


def test_version_info_tuple_parses():
    sys.path.insert(0, str(ENGINE_DIR))
    import __version__ as engine_version  # noqa: E402

    assert engine_version.__version_info__[:3] == tuple(
        int(p) for p in engine_version.__version__.split(".")[:3]
    )


def test_latest_git_tag_alignment_soft():
    """SOFT check: never fails. Note (not fail) when the latest tag lags VERSION — a tag
    legitimately lags between bumping VERSION and cutting the tag."""
    engine_version = _load_engine_version()
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "tag", "--list", "--sort=-v:refname"],
            capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("git unavailable — tag alignment is a soft check")
        return
    if proc.returncode != 0:
        pytest.skip("git tag listing failed — tag alignment is a soft check")
        return
    tags = [t.strip() for t in proc.stdout.splitlines() if t.strip()]
    if not tags:
        pytest.skip("no git tags yet — nothing to align against")
        return
    latest = tags[0]
    normalized = latest.lstrip("v").split("-")[0]  # vX.Y.Z-rcN -> X.Y.Z
    if normalized != engine_version:
        # Deliberately a print + soft assert(True): observable in -s output, never red.
        print(
            f"[soft] latest git tag {latest!r} (→ {normalized}) does not match VERSION "
            f"{engine_version!r}; remember to tag the release after the bump."
        )
    assert True
