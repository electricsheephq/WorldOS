#!/usr/bin/env python3
"""Red-first units for the STATIC walkability gate + the repo-wide CI enforcement (epic #1581).

The last test validates the ACTUAL repo (manifest + every committed geometry) — so a PR that
reintroduces the ortho-only-pin bug, ships a manifest entry without its plate/sidecar, drifts an
ortho, blocks a door landing, or authors an orphan pocket goes RED **pre-merge, forever, with no
player**. This is the engine-integrated half of the walkability gate (VISION.md).

Run: python3 -m pytest qa/test_walk_static.py -q -p no:xdist
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import walk_static as WS  # noqa: E402


# --- fit_ortho: the third ortho source must reproduce every shipped pin exactly --------------------
def test_fit_ortho_reproduces_shipped_pins():
    assert abs(WS.fit_ortho(16, 12) - 11.7851) < 0.001   # crypt v3.6
    assert abs(WS.fit_ortho(14, 11) - 10.5224) < 0.001   # tavern v2
    assert abs(WS.fit_ortho(13, 10) - 9.6806) < 0.001    # shop


# --- manifest lint (red-first) ----------------------------------------------------------------------
def test_missing_ortho_is_the_2026_07_15_bug_class(tmp_path):
    fails = WS.lint_manifest_entry("x", {"plate": "nope.png", "cameraPin": {}}, tmp_path)
    assert any("ortho missing" in f for f in fails)


def test_off_contract_pitch_yaw_fails(tmp_path):
    fails = WS.lint_manifest_entry("x", {"cameraPin": {"ortho": 10.0, "pitch": 25, "yaw": 45}}, tmp_path)
    assert any("pitch" in f for f in fails)


def test_sidecar_ortho_mismatch_fails(tmp_path):
    (tmp_path / "b.json").write_text(json.dumps({"ortho": 13.0, "boxes": []}))
    entry = {"cameraPin": {"ortho": 10.5}, "boxes": "b.json"}
    fails = WS.lint_manifest_entry("x", entry, tmp_path)
    assert any("sidecar ortho" in f for f in fails)


def test_missing_sidecar_is_the_latent_zero_occluder_path(tmp_path):
    entry = {"cameraPin": {"ortho": 10.5}, "boxes": "gone.json"}
    assert any("sidecar missing" in f for f in WS.lint_manifest_entry("x", entry, tmp_path))


def test_clean_entry_passes(tmp_path):
    (tmp_path / "p.png").write_bytes(b"\x89PNG")
    (tmp_path / "b.json").write_text(json.dumps({"ortho": 10.5224, "boxes": [
        {"name": "pillar", "kind": "pillar", "center": [0, 3, 0], "size": [1, 6, 1]}]}))
    entry = {"plate": "p.png", "cameraPin": {"ortho": 10.5224, "pitch": 30, "yaw": 45}, "boxes": "b.json"}
    assert WS.lint_manifest_entry("x", entry, tmp_path) == []


# --- ortho triple-check -----------------------------------------------------------------------------
def test_ortho_triple_catches_drift():
    geo = {"cols": 16, "rows": 12, "camera_fit": True}
    entry = {"cameraPin": {"ortho": 13.0}}   # a camera-fit room wearing the contract-13 pin
    assert WS.check_ortho_triple("x", entry, geo)
    entry["cameraPin"]["ortho"] = 11.7851
    assert WS.check_ortho_triple("x", entry, geo) == []


# --- geometry checks (red-first) --------------------------------------------------------------------
def _geo(cols, rows, walls, doors):
    return {"cols": cols, "rows": rows, "walls": [list(w) for w in walls],
            "door_cells": [list(d) for d in doors]}


def test_blocked_door_landing_fails():
    walls = [(c, 0) for c in range(5)] + [(2, 1)]   # wall row + a prop ON the landing of door (2,0)
    fails = WS.check_geometry("g", _geo(5, 5, walls, [(2, 0)]))
    assert any("landing" in f for f in fails)


def test_orphan_pocket_fails():
    walls = [(2, r) for r in range(5)]   # full bisecting wall -> far side unreachable from the door
    fails = WS.check_geometry("g", _geo(5, 5, walls + [(0, 2)], [(0, 2)]))
    assert any("orphan" in f for f in fails)


def test_interior_door_fails():
    assert any("perimeter" in f for f in WS.check_geometry("g", _geo(5, 5, [], [(2, 2)])))


def test_clean_geometry_passes():
    walls = ([(c, 0) for c in range(5)] + [(c, 4) for c in range(5)]
             + [(0, r) for r in (1, 2, 3)] + [(4, r) for r in (1, 2, 3)])
    geo = _geo(5, 5, [w for w in walls if w != (2, 0)], [(2, 0)])
    assert WS.check_geometry("g", geo) == []


# --- world reciprocity ------------------------------------------------------------------------------
def test_one_way_door_fails():
    rooms = [("a", [((1, 0), "b")]), ("b", [])]
    assert any("NO reciprocal" in f for f in WS.validate_world(rooms))


def test_reciprocal_world_passes():
    rooms = [("a", [((1, 0), "b")]), ("b", [((3, 0), "a")])]
    assert WS.validate_world(rooms) == []


# --- codex-review hardening (#1598, all six red-first) ----------------------------------------------
def test_plateless_entry_fails(tmp_path):
    """LoadPlateManifest SKIPS plate-less entries — the room keeps the previous backdrop."""
    fails = WS.lint_manifest_entry("x", {"cameraPin": {"ortho": 10.0}}, tmp_path)
    assert any("no `plate`" in f for f in fails)


def test_boxless_sidecar_fails(tmp_path):
    """An ortho-only sidecar degrades the runtime to footprint proxies — must fail loud."""
    (tmp_path / "p.png").write_bytes(b"\x89PNG")
    (tmp_path / "b.json").write_text(json.dumps({"ortho": 10.5}))
    entry = {"plate": "p.png", "cameraPin": {"ortho": 10.5}, "boxes": "b.json"}
    assert any("NO occluder volumes" in f for f in WS.lint_manifest_entry("x", entry, tmp_path))


# --- ROOMS-ARE-THE-SCENE live-room keys (#1793 Day 3, red-first) -------------------------------------
def test_unknown_live_room_mode_fails():
    """A misspelled mode falls through to "visible" in the client — a room gated as an occluder would
    then ship as the picture. Fail it in CI instead."""
    fails = WS.lint_live_room("crypt", {"liveRoom": "crypt", "liveRoomMode": "occluders"})
    assert any("liveRoomMode" in f for f in fails)


def test_live_room_keys_without_live_room_fail():
    assert any("without `liveRoom`" in f
               for f in WS.lint_live_room("x", {"liveRoomMode": "occluder"}))


def test_live_room_entry_passes():
    assert WS.lint_live_room("crypt", {"liveRoom": "crypt", "liveRoomMode": "occluder"}) == []
    assert WS.lint_live_room("crypt", {"liveRoom": "crypt"}) == []
    assert WS.lint_live_room("crypt", {}) == []


def test_prop_on_door_landing_fails():
    """Prop footprints count as blocked even when the geometry does NOT fold them into walls
    (the generate_town convention) — a barrel on the landing must go red."""
    walls = [(c, 0) for c in range(5) if c != 2]
    geo = _geo(5, 5, walls, [(2, 0)])
    geo["props"] = [{"id": "barrel", "kind": "barrel", "cells": [[2, 1]]}]
    assert any("landing" in f for f in WS.check_geometry("g", geo))


def test_prop_partition_makes_orphans():
    geo = _geo(5, 5, [(0, 2)], [(0, 2)])
    geo["props"] = [{"id": "wall_of_crates", "kind": "supply_crates",
                     "cells": [[2, r] for r in range(5)]}]
    assert any("orphan" in f for f in WS.check_geometry("g", geo))


def test_duplicate_door_cells_fail():
    fails = WS.check_geometry("g", _geo(5, 5, [], [(2, 0), (2, 0)]))
    assert any("duplicate door cells" in f.lower() for f in fails)


def test_mapped_but_missing_geometry_fails(tmp_path):
    (tmp_path / "m.json").write_text(json.dumps({"plates": {"crypt": {
        "plate": "p.png", "cameraPin": {"ortho": 11.7851}}}}))
    (tmp_path / "p.png").write_bytes(b"\x89PNG")
    fails = WS.validate_repo(manifest_path=tmp_path / "m.json", unity_dir=tmp_path,
                             geo_dir=tmp_path)  # crypt is mapped in GEOMETRY_OF; file absent here
    assert any("MISSING" in f and "triple-check" in f for f in fails)


def test_unwired_authored_door_fails_unless_allowed():
    geo = {"cols": 5, "rows": 5, "door_cells": [[2, 0], [4, 2]], "walls": []}
    rooms = [("tavern", [((2, 0), "crypt")])]
    fails = WS.validate_seed_doors(rooms, {"tavern": geo})
    assert any("UNWIRED" in f for f in fails)
    assert WS.validate_seed_doors(rooms, {"tavern": geo},
                                  allowed_unwired={("tavern", (4, 2))}) == []


def test_wired_but_unauthored_door_fails():
    geo = {"cols": 5, "rows": 5, "door_cells": [[2, 0]], "walls": []}
    fails = WS.validate_seed_doors([("x", [((3, 0), "y")])], {"x": geo})
    assert any("never authored" in f for f in fails)


# --- THE CI ENFORCEMENT: the actual repo must be GREEN ----------------------------------------------
def test_repo_is_statically_walkable():
    fails = WS.validate_repo()
    assert fails == [], "\n".join(fails)
