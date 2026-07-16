#!/usr/bin/env python3
"""Red-first proof that the WALKABILITY gate has teeth (epic #1581, issue #1582).

The live green proof comes after the camera-rig fix + box rebuild (issue #1583, Step 4). THIS test
proves — deterministically, with no rebuild and no image analysis — that walk_test's assertion logic
FAILS on the shipped-2026-07-15 broken camera pose and PASSES on the build_room_unified contract pose.
A gate that can't go red is not a gate.

Run: python3 -m pytest qa/test_walk_test.py -q -p no:xdist
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import walk_test as W  # noqa: E402

CRYPT_ORTHO = 11.7851


def _contract_snapshot(ortho=CRYPT_ORTHO):
    """The pose ApplyPlate must reproduce: Euler(30,45,0), pos=-fwd*80, aim at origin, pinned ortho."""
    px, py, pz = W.contract_cam_pos()
    return {"ok": True, "camOrtho": ortho, "camRx": 30.0, "camRy": 45.0, "camRz": 0.0,
            "camPx": px, "camPy": py, "camPz": pz, "originVX": 0.5, "originVY": 0.5}


def test_contract_pose_passes():
    """A camera that matches build_room_unified's rig has ZERO failures — the room is projectable."""
    assert W.check_camera_pose(_contract_snapshot(), CRYPT_ORTHO) == []


def test_broken_aim_fails():
    """The actual 2026-07-15 bug: ortho pinned but position never set → camera keeps a stale aim, so
    world origin projects OFF viewport-center. The gate MUST catch this."""
    snap = _contract_snapshot()
    snap["originVX"], snap["originVY"] = 0.72, 0.34  # origin far from center → offset plate
    snap["camPx"] += 18.0  # stale/previous-room position
    fails = W.check_camera_pose(snap, CRYPT_ORTHO)
    assert fails, "broken aim must fail the camera gate"
    assert any("origin" in f for f in fails)


def test_wrong_ortho_fails():
    snap = _contract_snapshot(ortho=13.0)  # inherited a different room's ortho
    assert any("camOrtho" in f for f in W.check_camera_pose(snap, CRYPT_ORTHO))


def test_rotated_rig_fails():
    snap = _contract_snapshot()
    snap["camRy"] = 60.0  # camera re-angled off the frozen dimetric contract
    assert any("camRy" in f for f in W.check_camera_pose(snap, CRYPT_ORTHO))


def test_missing_camera_fields_is_loud_not_silent():
    """An old player build without the /debug camera extension must FAIL loud (never silently green)."""
    fails = W.check_camera_pose({"ok": True, "enq": 3, "last": "cell(8,9)"}, CRYPT_ORTHO)
    assert fails and "unavailable" in fails[0]


def test_walkmask_from_surface():
    """Engine truth: cellDefault=floor is walkable; listed wall/prop cells are blocked; doors walkable."""
    surf = {
        "grid": {"cols": 4, "rows": 3, "cellDefault": {"type": "floor", "walkable": True},
                 "cells": [{"c": 0, "r": 0, "walkable": False}, {"c": 1, "r": 1, "walkable": False},
                           {"c": 2, "r": 0, "type": "door", "walkable": True}]},
        "doors": [{"cell": [2, 0], "to": "next"}],
    }
    m = W.walkmask_from_surface(surf)
    assert (1, 1) in m["blocked"] and (0, 0) in m["blocked"]
    assert (2, 0) in m["doors"] and (2, 0) in m["walkable"]  # a door is walkable
    assert (3, 2) in m["walkable"]  # default floor


def test_contract_cam_pos_aims_back_and_up():
    x, y, z = W.contract_cam_pos()
    assert y > 0 and x < 0 and z < 0  # pulled back-and-up along -forward (Euler 30/45)
    assert abs((x * x + y * y + z * z) ** 0.5 - 80.0) < 1e-6  # exactly PULLBACK units from origin


def _mask(cols, rows, blocked):
    walkable = {(c, r) for r in range(rows) for c in range(cols)} - set(blocked)
    return {"cols": cols, "rows": rows, "walkable": walkable, "blocked": set(blocked), "doors": set()}


def test_orphan_pocket_detected():
    """A wall bisecting the room leaves an orphan pocket — the unreachable-paint/seed-defect class.
    Red-first: the gate MUST flag it."""
    wall = [(2, r) for r in range(5)]  # full vertical wall at c=2 in a 5x5 room
    m = _mask(5, 5, wall)
    orphans = W.orphan_cells(m, start=(0, 0))
    assert orphans, "bisected room must yield orphans"
    assert all(c > 2 for (c, r) in [tuple(o) for o in orphans]), "orphans are the far side of the wall"


def test_no_orphans_in_connected_room():
    m = _mask(5, 5, [(2, 2)])  # one prop, room stays connected
    assert W.orphan_cells(m, start=(0, 0)) == []


def test_path_through_a_table_flagged():
    """The owner's ACTUAL failure: destination legal, but the route crosses a prop. Red-first."""
    m = _mask(6, 3, [(3, 1)])  # a table at (3,1)
    bad = W.path_violations([[1, 1], [2, 1], [3, 1], [4, 1]], m)
    assert bad == [[3, 1]]


def test_clean_path_passes():
    m = _mask(6, 3, [(3, 1)])
    assert W.path_violations([[1, 1], [2, 1], [2, 0], [3, 0], [4, 0], [4, 1]], m) == []
    assert W.path_violations([], m) == []          # no path recorded = nothing to flag
    assert W.path_violations(None, m) == []
