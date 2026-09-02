#!/usr/bin/env python3
"""Red-first units for qa_sandbox's kit-scene contamination detector (#1690 C# half).

The detector is the independent check that a .app does not carry the QA kit rooms build_room_kit.cs
assembles in the open scene. Three of these tests fail against the pre-fix detector:

  * helper-only match  -> the `or found` fallback re-flagged the very names it had just excluded, so a
    clean app whose light rig left KitRoom_Fire bytes behind failed the sandbox with no kit room in it.
  * sharedassets/.resS -> the scan only globbed `level*`, so a contaminated app whose objects serialized
    into the shared-asset archives read CLEAN.
  * chunk boundary     -> the chunked (bounded) read must not lose a name split across two chunks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_sandbox  # noqa: E402


def _app(tmp_path: Path, files: dict) -> Path:
    """Build a synthetic .app whose Data/ holds the given {filename: bytes}."""
    data = tmp_path / "WorldOSPlayer.app" / "Contents" / "Resources" / "Data"
    data.mkdir(parents=True)
    for name, blob in files.items():
        (data / name).write_bytes(blob)
    return tmp_path / "WorldOSPlayer.app"


# a plausible slice of a Unity level file: names sit in the byte stream amid binary noise
def _lvl(*names: str) -> bytes:
    out = b"\x00\x13UnityFS\x00" + bytes(range(64))
    for n in names:
        out += b"\x1a\x00\x00\x00" + n.encode() + b"\x00\x00m_Name\x00"
    return out + bytes(range(64))


def test_clean_app_reports_nothing(tmp_path):
    app = _app(tmp_path, {"level0": _lvl(), "sharedassets0.assets": _lvl()})
    assert qa_sandbox._qa_roots_in_app(app) == set()


def test_kit_root_in_level_is_contamination(tmp_path):
    app = _app(tmp_path, {"level0": _lvl("KitRoom_crypt", "KitRoom_Fire")})
    assert qa_sandbox._qa_roots_in_app(app) == {"KitRoom_crypt"}


def test_helper_lights_alone_are_not_contamination(tmp_path):
    """RED pre-fix: `... or found` returned the helper names it had just filtered out."""
    app = _app(tmp_path, {"level0": _lvl("KitRoom_Fire", "KitRoom_TombGlow", "KitRoom_CoolKey")})
    assert qa_sandbox._qa_roots_in_app(app) == set()


@pytest.mark.parametrize("fname", ["sharedassets0.assets", "sharedassets2.resource", "level0.resS"])
def test_scan_covers_shared_asset_archives(tmp_path, fname):
    """RED pre-fix: a level-only glob read a contaminated app as clean."""
    app = _app(tmp_path, {"level0": _lvl(), fname: _lvl("KitRoom_tavern")})
    assert qa_sandbox._qa_roots_in_app(app) == {"KitRoom_tavern"}


def test_name_split_across_chunk_boundary_still_matches(tmp_path, monkeypatch):
    """The read is bounded/chunked; the overlap must not drop a name straddling two chunks."""
    monkeypatch.setattr(qa_sandbox, "_SCAN_CHUNK", 32)
    app = _app(tmp_path, {"level0": b"\x00" * 28 + b"KitRoom_bosshall" + b"\x00" * 40})
    assert qa_sandbox._qa_roots_in_app(app) == {"KitRoom_bosshall"}


def test_scan_is_bounded_by_the_byte_budget(tmp_path, monkeypatch):
    """A pathological build must not hang the gate: past the budget the scan stops reading."""
    monkeypatch.setattr(qa_sandbox, "_SCAN_BUDGET", 16)
    app = _app(tmp_path, {"level0": b"\x00" * 4096 + b"KitRoom_crypt"})
    assert qa_sandbox._qa_roots_in_app(app) == set()


def test_missing_data_dir_is_not_an_error(tmp_path):
    (tmp_path / "Empty.app").mkdir()
    assert qa_sandbox._qa_roots_in_app(tmp_path / "Empty.app") == set()


def test_helper_allowlist_is_parsed_from_the_builder_cs():
    """Single source of truth: the allowlist comes from the C# that creates the helper objects."""
    parsed = qa_sandbox._kit_helper_names()
    assert qa_sandbox._KIT_BUILDER_CS.exists(), qa_sandbox._KIT_BUILDER_CS
    assert parsed == frozenset(qa_sandbox._KIT_HELPER_FALLBACK), (
        "build_room_kit.cs KitHelper* constants drifted from the qa_sandbox fallback tuple; "
        f"parsed={sorted(parsed)}")
    # and every parsed name is really constructed in that file
    cs = qa_sandbox._KIT_BUILDER_CS.read_text()
    for name in parsed:
        const = "KitHelper" + name.removeprefix("KitRoom_")
        assert f"new GameObject({const})" in cs, f"{const} declared but not used to create {name}"


def test_helper_allowlist_falls_back_when_the_cs_is_absent(tmp_path):
    assert qa_sandbox._kit_helper_names(tmp_path / "nope.cs") == frozenset(
        qa_sandbox._KIT_HELPER_FALLBACK)
