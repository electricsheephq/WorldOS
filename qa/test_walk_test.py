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


def test_world_to_window_px_origin_is_center():
    """The contract camera aims at world origin → origin projects to the window centre at ANY aspect
    (the crop model — a 1.6 window crops the 1.75 plate, nothing stretches)."""
    for w, h in ((1344, 768), (1280, 800), (1920, 1080)):
        x, y = W.world_to_window_px(0, 0, 0, ortho=11.7851, w=w, h=h)
        assert abs(x - w / 2) < 1e-6 and abs(y - h / 2) < 1e-6


def test_diff_blobs_finds_a_moved_square():
    """Synthetic red-first: a 30x30 'actor' moves from (100,100) to (300,200) between frames —
    diff_blobs must report exactly the departure + arrival blobs, bottom-centres on the squares."""
    import numpy as np
    a = np.zeros((400, 500, 3), dtype=np.uint8)
    b = np.zeros((400, 500, 3), dtype=np.uint8)
    a[100:130, 100:130] = 255   # actor at old position in frame A
    b[200:230, 300:330] = 255   # actor at new position in frame B
    blobs = W.diff_blobs(a, b, min_area_px=200)
    assert len(blobs) == 2
    centers = sorted((round(bl["cx"]), round(bl["cy"])) for bl in blobs)
    assert centers == [(114, 114), (314, 214)]     # 100..129 → centre ~114.5
    assert W.nearest_blob_distance(blobs, (315, 229)) < 6      # feet of the arrival square
    assert W.nearest_blob_distance(blobs, (50, 350)) > 100     # far point matches nothing


def test_diff_blobs_ignores_flicker_noise():
    """Small flicker (animated fire) diffs below min_area must not produce blobs."""
    import numpy as np
    a = np.zeros((200, 200, 3), dtype=np.uint8)
    b = a.copy()
    b[10:14, 10:14] = 255   # 16 px flicker
    assert W.diff_blobs(a, b, min_area_px=200) == []


def test_path_cell_cr_normalizes_both_shapes():
    """#1582: the route-endpoint staleness guard must read both cell shapes path_violations accepts."""
    from walk_test import _path_cell_cr
    assert _path_cell_cr([3, 4]) == [3, 4]
    assert _path_cell_cr((3, 4)) == [3, 4]
    assert _path_cell_cr({"c": 3, "r": 4}) == [3, 4]


# --- tri-state verdict classification (GREEN / RED / ERROR) -----------------------------------------
def _base_report(**over):
    """A clean report skeleton carrying only the fields classify_verdict reads."""
    r = {"camera": {"pose_mismatch": False}, "reachable": {"fail": 0}, "impassable": {"fail": 0},
         "doors": {"fail": 0}, "orphans": [], "path": {"fail": 0}, "visual": {"fail": 0},
         "door_pose_fail": [], "harness_errors": []}
    r.update(over)
    return r


def test_classify_verdict_clean_is_green():
    assert W.classify_verdict(_base_report()) == ("GREEN", 0)


def test_classify_verdict_harness_only_is_error():
    """A harness/infra defect with NO walkability failure = ERROR/2 — never a room verdict."""
    r = _base_report(harness_errors=["reachable (3,4): drive-error:conn refused"])
    assert W.classify_verdict(r) == ("ERROR", 2)


def test_classify_verdict_walkability_fail_is_red():
    assert W.classify_verdict(_base_report(reachable={"fail": 1})) == ("RED", 1)


def test_classify_verdict_real_fail_wins_over_harness():
    """A genuine walkability failure present ALONGSIDE harness errors → RED/1 (real fail wins), so a
    partial harness outage can never downgrade a proven-broken room to a mere ERROR."""
    r = _base_report(impassable={"fail": 2}, harness_errors=["door [4,0]: click:timeout"])
    assert W.classify_verdict(r) == ("RED", 1)


def test_classify_verdict_camera_pose_mismatch_is_red():
    assert W.classify_verdict(_base_report(camera={"pose_mismatch": True})) == ("RED", 1)


def test_classify_verdict_door_pose_mismatch_is_red():
    r = _base_report(door_pose_fail=[{"door": [1, 2], "leg": "dest", "fails": ["camOrtho ..."]}])
    assert W.classify_verdict(r) == ("RED", 1)


def test_classify_verdict_orphans_and_visual_are_walkability():
    """The visual zero-measurable-case fail and orphan pockets are walkability RED (guard vacuous
    greens), never reclassified as harness."""
    assert W.classify_verdict(_base_report(orphans=[[7, 3]])) == ("RED", 1)
    assert W.classify_verdict(_base_report(visual={"fail": 1})) == ("RED", 1)


def test_is_drive_error_only_matches_the_sentinel():
    """A drive-error string is harness; a settled cell list or a plain None (timeout) is not."""
    assert W.is_drive_error("drive-error:HTTPError") is True
    assert W.is_drive_error([4, 5]) is False
    assert W.is_drive_error(None) is False
    assert W.is_drive_error("cell(4,5)") is False


# --- provenance stamps -----------------------------------------------------------------------------
def test_init_report_carries_provenance_stamps():
    """The report self-describes its provenance (closes the #1607 cert traceability loop)."""
    r = W._init_report("crypt", CRYPT_ORTHO, "crypt_scene",
                       {"cols": 5, "rows": 4}, "http://engine:8766", "http://qa:8971")
    assert r["schema_version"] == 1
    assert "T" in r["ts"]                       # ISO8601 UTC
    assert r["engine_url"] == "http://engine:8766" and r["qa_url"] == "http://qa:8971"
    assert "repo_sha" in r and "manifest_sha256" in r   # present (value may be None if unavailable)
    assert r["manifest_sha256"] and len(r["manifest_sha256"]) == 64   # real sha of the on-disk manifest
    assert r["harness_errors"] == [] and r["door_pose_fail"] == []
    assert r["verdict"] == "PENDING" and r["ortho"] == CRYPT_ORTHO


# --- camera-fail + door-cross pose classification (walkability RED vs harness) ----------------------
def test_classify_camera_fails_missing_extension_is_harness():
    """A player build with NO /debug camera fields → harness (never a walkability verdict)."""
    dbg = {"ok": True, "enq": 3}                 # no camOrtho
    fails = W.check_camera_pose(dbg, CRYPT_ORTHO)
    walk, harness = W.classify_camera_fails(dbg, fails)
    assert walk == [] and harness and "unavailable" in harness[0]


def test_classify_camera_fails_pose_mismatch_is_walkability():
    """Wrong ortho/rotation/aim (fields present) = the 2026-07-15 root-cause class = walkability RED."""
    snap = _contract_snapshot()
    snap["originVX"], snap["originVY"] = 0.72, 0.34
    fails = W.check_camera_pose(snap, CRYPT_ORTHO)
    walk, harness = W.classify_camera_fails(snap, fails)
    assert walk and harness == []


def test_classify_pose_observation_debug_unreachable_is_harness():
    walk, harness = W.classify_pose_observation({"_error": "connection refused"}, CRYPT_ORTHO)
    assert walk == [] and harness and "unreachable" in harness[0]


def test_classify_pose_observation_mismatch_is_walkability_red():
    snap = _contract_snapshot()
    snap["camRy"] = 60.0                          # destination camera off the frozen dimetric contract
    walk, harness = W.classify_pose_observation(snap, CRYPT_ORTHO)
    assert walk and harness == []


def test_classify_pose_observation_no_ortho_skips():
    """A door target with no pinned ortho asserts nothing — not RED, not harness."""
    assert W.classify_pose_observation(_contract_snapshot(), None) == ([], [])


def test_classify_pose_observation_contract_pose_is_clean():
    assert W.classify_pose_observation(_contract_snapshot(), CRYPT_ORTHO) == ([], [])


# --- Fix 3: poll-time engine outage in _drive_and_check → harness, not a false verdict --------------
def test_drive_and_check_total_poll_outage_is_harness(monkeypatch):
    """Engine alive at click, then dies for EVERY poll → harness sentinel (an impassable check would
    otherwise false-PASS on the stale `before` cell; a reachable check would false-RED)."""
    calls = {"n": 0}

    def _get_stub(url, timeout=5.0):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"tokens": [{"x": 0, "y": 0}]}          # the pre-click `before` read succeeds
        raise ConnectionError("engine died mid-probe")       # every poll after the click fails

    monkeypatch.setattr(W, "_get", _get_stub)
    monkeypatch.setattr(W, "_post", lambda url, body, timeout=5.0: {"ok": True})
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    ok, landed, path = W._drive_and_check("q", "e", 3, 4, 0.001, 0.03, expect_move=False)
    assert ok is False and W.is_drive_error(landed)          # NOT a false impassable PASS


def test_drive_and_check_partial_outage_keeps_verdict(monkeypatch):
    """A single failed poll among good ones keeps normal timeout semantics — only a TOTAL outage is
    harness."""
    seq = [{"tokens": [{"x": 0, "y": 0}]}, ConnectionError("blip"), {"tokens": [{"x": 3, "y": 4}]}]

    def _get_stub(url, timeout=5.0):
        v = seq.pop(0) if seq else {"tokens": [{"x": 3, "y": 4}]}
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(W, "_get", _get_stub)
    monkeypatch.setattr(W, "_post", lambda url, body, timeout=5.0: {"ok": True})
    monkeypatch.setattr(W.time, "sleep", lambda s: None)
    ok, landed, path = W._drive_and_check("q", "e", 3, 4, 0.001, 5.0, expect_move=True)
    assert ok is True and landed == [3, 4]                   # a good poll saw arrival → real PASS


# --- Fix 2 / 4b: door-cross pose is captured only on a CONFIRMED leg, and never on an unpinned ortho -
class _FakeWorld:
    """A minimal live-player+engine fake: click 1 crosses to `crossed_to`; click 2 returns home iff
    `return_home`. /combat-surface reports the current location + a back-door to home; /debug returns
    a contract snapshot."""
    def __init__(self, home, target, crossed_to, return_home):
        self.home, self.target, self.crossed_to, self.return_home = home, target, crossed_to, return_home
        self.loc, self.clicks, self.debug_calls = home, 0, 0

    def get(self, url, timeout=5.0):
        return {"location": self.loc, "doors": [{"cell": [1, 1], "to": self.home}]}

    def post(self, url, body=None, timeout=5.0):
        if url.endswith("/click"):
            self.clicks += 1
            if self.clicks == 1:
                self.loc = self.crossed_to
            elif self.clicks == 2 and self.return_home:
                self.loc = self.home
            return {"ok": True}
        if url.endswith("/debug"):
            self.debug_calls += 1
            return dict(_contract_snapshot())
        return {}


def _wire(monkeypatch, fw):
    monkeypatch.setattr(W, "_get", fw.get)
    monkeypatch.setattr(W, "_post", fw.post)
    monkeypatch.setattr(W.time, "sleep", lambda s: None)


def test_door_home_pose_skipped_on_timed_out_return(monkeypatch):
    """The party crosses to target but the return leg TIMES OUT (still in target room). The home-leg
    pose must NOT be recorded — asserting HOME's ortho against the target room = a false RED."""
    fw = _FakeWorld("home_room", "target_room", crossed_to="target_room", return_home=False)
    _wire(monkeypatch, fw)
    ok, detail = W._check_door_cross("q", "e", (2, 0), "target_room", "home_room", 0.001, 0.03,
                                     dest_ortho=CRYPT_ORTHO, home_ortho=CRYPT_ORTHO)
    assert "dest" in detail["pose"]                # arrival at target confirmed → dest pose recorded
    assert "home" not in detail.get("pose", {})    # unconfirmed return → NO home pose


def test_door_home_pose_recorded_on_confirmed_return(monkeypatch):
    fw = _FakeWorld("home_room", "target_room", crossed_to="target_room", return_home=True)
    _wire(monkeypatch, fw)
    ok, detail = W._check_door_cross("q", "e", (2, 0), "target_room", "home_room", 0.001, 0.5,
                                     dest_ortho=CRYPT_ORTHO, home_ortho=CRYPT_ORTHO)
    assert "dest" in detail["pose"] and "home" in detail["pose"]


def test_door_dest_pose_skipped_when_not_arrived_at_target(monkeypatch):
    """`crossed` != home only proves we LEFT; it does not prove we reached `target`. A cross to the
    wrong room must NOT capture a dest pose (it would assert the wrong room's ortho)."""
    fw = _FakeWorld("home_room", "roomB", crossed_to="roomX", return_home=False)
    _wire(monkeypatch, fw)
    ok, detail = W._check_door_cross("q", "e", (2, 0), "roomB", "home_room", 0.001, 0.03,
                                     dest_ortho=CRYPT_ORTHO, home_ortho=CRYPT_ORTHO)
    assert ok is False                             # crossed to the wrong room
    assert "dest" not in detail.get("pose", {})


def test_door_unpinned_ortho_skips_debug_fetch(monkeypatch):
    """codex-P2: when a leg's room has no pinned ortho, skip the /debug fetch ENTIRELY — no pose work,
    and no spurious harness error from a /debug that we would never assert against."""
    fw = _FakeWorld("home_room", "target_room", crossed_to="target_room", return_home=True)
    _wire(monkeypatch, fw)
    ok, detail = W._check_door_cross("q", "e", (2, 0), "target_room", "home_room", 0.001, 0.5,
                                     dest_ortho=None, home_ortho=None)
    assert fw.debug_calls == 0                      # no /debug fetched for an unpinned leg
    assert detail.get("pose", {}) == {}


# --- Addendum: animated-fire-VFX masking (#1525) ---------------------------------------------------
def test_fire_anchor_cells_from_geometry():
    geo = {"props": [{"kind": "brazier", "cells": [[5, 1]]},
                     {"kind": "wall_run", "cells": [[0, 0]]},
                     {"kind": "hearth", "cells": [[10, 2], [10, 3]]}]}
    assert W.fire_anchor_cells(geo) == {(5, 1), (10, 2), (10, 3)}
    assert W.fire_anchor_cells({}) == set()


def test_fire_mask_removes_flicker_blob_and_selects_actor():
    """A brazier-flicker blob nearer to the actor's expected cell is masked, so the nearest-neighbour
    selection resolves to the ACTOR blob instead of losing the race to the flame VFX."""
    fire_blob = {"cx": 100.0, "cy": 100.0, "bottom": (100, 110), "area": 5000}
    actor_blob = {"cx": 300.0, "cy": 300.0, "bottom": (300, 320), "area": 900}
    kept = W.mask_fire_blobs([fire_blob, actor_blob], [(100.0, 100.0)], radius_px=30.0)
    assert kept == [actor_blob]
    assert W.nearest_blob_distance(kept, (300, 320)) < 25


def test_fire_mask_all_excluded_is_empty_not_a_pass():
    """If every blob is fire-masked the case has ZERO measurable blobs → the caller fails it loud
    (nearest_blob_distance is inf). Masking never invents a pass."""
    blobs = [{"cx": 100.0, "cy": 100.0, "bottom": (100, 110), "area": 5000}]
    assert W.mask_fire_blobs(blobs, [(100.0, 100.0)], 30.0) == []
    assert W.nearest_blob_distance([], (300, 320)) == float("inf")


def test_fire_mask_noop_without_fire():
    blobs = [{"cx": 1.0, "cy": 1.0, "bottom": (1, 1), "area": 1}]
    assert W.mask_fire_blobs(blobs, [], 30.0) == blobs


def test_select_visual_cells_deprioritizes_fire_but_fills_to_n():
    fire = {(5, 5)}
    pool = [(5, 5), (5, 6), (6, 5), (1, 1), (2, 2), (3, 3), (8, 8)]   # first three are fire-adjacent
    picked = W.select_visual_cells(pool, 4, fire, min_cheby=2)
    assert len(picked) == 4
    assert set(picked) <= {(1, 1), (2, 2), (3, 3), (8, 8)}           # no fire-adjacent cell chosen


def test_select_visual_cells_falls_back_to_fire_adjacent_when_needed():
    fire = {(5, 5)}
    pool = [(5, 5), (5, 6), (1, 1)]                                  # only ONE far cell; need 3
    picked = W.select_visual_cells(pool, 3, fire, min_cheby=2)
    assert len(picked) == 3 and (1, 1) in picked                     # fills to N incl. fire-adjacent
