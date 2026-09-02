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


# --- #1585/#1647 painted-door hotspot pure math (twin of C# WindowToPlatePx / TryDoorHotspot) ---------
def test_window_plate_px_round_trips():
    """screen px -> plate px -> screen px is identity at any aspect (the crop model is a bijection on the
    displayed region). Guards the C# hit-test against a sign/flip/scale regression it can't unit-test."""
    for w, h in ((1344, 768), (1280, 800), (1920, 1080)):
        for sx, sy in ((10, 10), (w / 2, h / 2), (w - 3, h - 7), (417, 605)):
            px, py = W.window_px_to_plate_px(sx, sy, w, h, 1344, 768)
            bx, by = W.plate_px_to_screen_px(px, py, w, h, 1344, 768)
            assert abs(bx - sx) < 1e-4 and abs(by - sy) < 1e-4


def test_window_to_plate_px_native_window_is_identity():
    """When the window IS the plate's native resolution, a screen click maps to the same plate pixel
    (modulo the bottom-left->top-left y flip) — the space the hotspot px are authored in."""
    px, py = W.window_px_to_plate_px(900, 768 - 275, 1344, 768, 1344, 768)
    assert abs(px - 900) < 1e-6 and abs(py - 275) < 1e-6


def test_plate_center_maps_to_window_center():
    """The plate is centered in the window, so its pixel centre projects to the screen centre at any aspect."""
    for w, h in ((1344, 768), (1600, 900), (1200, 1000)):
        sx, sy = W.plate_px_to_screen_px(672, 384, w, h, 1344, 768)
        assert abs(sx - w / 2) < 1e-6 and abs(sy - h / 2) < 1e-6


def test_door_hotspot_hit_inside_and_outside():
    """The circular hit-test: inside the radius fires, just outside does not (mirror of TryDoorHotspot)."""
    hs, rad = (900, 275), 85
    assert W.door_hotspot_hit((900, 275), hs, rad)          # dead centre
    assert W.door_hotspot_hit((900 + 60, 275 - 60), hs, rad)  # within (dist ~84.9)
    assert not W.door_hotspot_hit((900 + 61, 275 - 61), hs, rad)  # just outside (dist ~86.3)
    assert not W.door_hotspot_hit((900, 275 + 86), hs, rad)


def test_shop_arch_click_routes_to_authored_door():
    """End-to-end pure path for the shipped shop hotspot: a click ON the painted arch (screen px at the
    native window) converts to the measured plate px and lands inside the {door_cell:[12,5]} hotspot —
    while a click at the projected door-cell base (px 1037,230, the wall the paint drifted off) misses."""
    w, h, tw, th = 1344, 768, 1344, 768
    hs_px, rad = (900, 275), 85
    # click on the painted arch centroid (screen y = h - plate_py)
    click = W.window_px_to_plate_px(900, h - 275, w, h, tw, th)
    assert W.door_hotspot_hit(click, hs_px, rad)
    # a click at the authored door_cell's projected base is off the painted arch -> no hotspot
    off = W.window_px_to_plate_px(1037, h - 230, w, h, tw, th)
    assert not W.door_hotspot_hit(off, hs_px, rad)


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


def test_run_gate_visual_preflight_error_is_harness_error(monkeypatch, tmp_path):
    """A capture/preflight fault is ERROR/2, not a vacuous visual RED/1."""
    monkeypatch.setattr(W, "_room_ortho", lambda room: 1.0)
    monkeypatch.setattr(W, "_get", lambda url: {"grid": {"sceneId": "room"},
                                                 "tokens": [{"x": 0, "y": 0}],
                                                 "doors": []})
    monkeypatch.setattr(W, "_post", lambda *args, **kwargs: {})
    monkeypatch.setattr(W, "walkmask_from_surface", lambda surf: {
        "cols": 3, "rows": 3, "walkable": [(1, 1)], "doors": [], "blocked": []})
    monkeypatch.setattr(W, "check_camera_pose", lambda *args: [])
    monkeypatch.setattr(W, "classify_camera_fails", lambda *args: ([], []))
    monkeypatch.setattr(W, "orphan_cells", lambda *args: [])
    monkeypatch.setattr(W, "bfs_reachable", lambda *args: {(1, 1)})
    monkeypatch.setattr(W, "_sample", lambda *args: [])
    monkeypatch.setattr(W, "_load_room_geometry", lambda room: {})
    monkeypatch.setattr(W, "fire_anchor_cells", lambda *args: set())
    monkeypatch.setattr(W, "select_visual_cells", lambda *args: [(1, 1)])
    monkeypatch.setattr(W, "_visual_registration", lambda *args, **kwargs: {
        "pass": 0, "fail": 0, "cases": [], "error": "capture preflight failed"})
    monkeypatch.setattr(W, "_capture_shot", lambda *args, **kwargs: None)
    monkeypatch.setattr(W, "_drive_and_check", lambda *args, **kwargs: (True, (0, 0), {}))
    report = W.run_gate("room", "http://engine", "http://qa", stride=1,
                        out=tmp_path, settle=0, move_timeout=0, visual=1)
    assert report["visual"]["fail"] == 0
    assert report["verdict"] == "ERROR"
    assert report["harness_errors"] == ["visual: capture preflight failed"]


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


def test_select_visual_cells_never_samples_mask_covered_cells():
    # CONTRACT CHANGE (measured false-fail class): cells within the fire-MASK radius (< 2 chebyshev)
    # are UNMEASURABLE BY CONSTRUCTION — the mask swallows the actor's own diff (n_blobs=0 with 4
    # masked on a 6-cell room). They are EXCLUDED absolutely; the sample may honestly be smaller
    # than n. Cells at 2 (measurable, mask-border) remain a top-up tier; ≥3 preferred.
    fire = {(5, 5)}
    pool = [(5, 5), (5, 6), (7, 7), (1, 1)]
    picked = W.select_visual_cells(pool, 3, fire, min_cheby=2)
    assert (5, 5) not in picked and (5, 6) not in picked             # mask-covered: never sampled
    assert (1, 1) in picked                                          # far cell always in
    assert (7, 7) in picked                                          # chebyshev-2: measurable top-up
    assert len(picked) == 2                                          # honestly smaller than n=3


# --- #1672 windowed sandbox player: the pixel-diff constants must scale with the frame ------------
# The visual stage was tuned at the fullscreen 2984x1634 baseline. Windowing the QA player to
# 1280x697 shrinks a grid cell from ~126px to ~54px, and the HARD-CODED 60px cluster-merge radius
# (walk_test.diff_blobs) becomes ~1.1 CELLS: a short hop's departure and arrival blobs FUSE into one
# centroid midway between them, the measured distance reads about half the true hop, and a correct
# build goes FALSE RED. These are the units for that.

def _squares(*xs, y=10, size=12, w=400, h=200):
    """Two frames differing only by `size`-square blocks at the given x offsets."""
    import numpy as np
    a = np.zeros((h, w, 3), dtype=int)
    b = a.copy()
    for x in xs:
        b[y:y + size, x:x + size] = 255
    return a, b


def test_diff_blobs_merge_px_separates_close_blobs():
    a, b = _squares(10, 60)          # centres 50px apart == ~1 cell at the windowed 1280x697
    fused = W.diff_blobs(a, b, min_area_px=50, merge_px=60)
    split = W.diff_blobs(a, b, min_area_px=50, merge_px=24)
    assert len(fused) == 1, "today's fixed 60px radius fuses a 1-cell hop into a single blob"
    assert len(split) == 2, "the frame-derived radius keeps departure and arrival apart"


def test_diff_blobs_default_merge_px_is_60():
    """The default is unchanged, so every existing caller/golden is provably unaffected."""
    import inspect
    assert inspect.signature(W.diff_blobs).parameters["merge_px"].default == 60


def test_visual_diff_params_scale_with_window():
    big = W.visual_diff_params(13, 2984, 1634)     # the fullscreen baseline
    small = W.visual_diff_params(13, 1280, 697)    # the windowed sandbox rig
    assert big["merge_px"] == 57 and big["min_area_px"] == 250
    assert small["merge_px"] == 24 and small["min_area_px"] == 60   # min_area floors at 60
    for p in (big, small):
        assert abs(p["merge_px"] / p["cell_px"] - 0.45) < 0.02


# --- #1672: the three ways the visual stage could silently invent a verdict, now NAMED errors -----
# A windowed player made all three reachable: a 2x HiDPI backbuffer (projection off by 2x), a
# minimized/occluded window (ScreenCapture hands back a black frame and /shot still returns 200), and
# a window whose aspect crops sample cells out of the ortho frame. Each used to surface as a
# mysterious per-cell RED; each must now fail LOUD and never report a pass.

def _png(tmp_path, name, size, fill):
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", size, fill).save(p)
    return str(p)


def _stub_visual(monkeypatch, shot_path, *, w=1280, h=697):
    monkeypatch.setattr(W, "_get", lambda url: {"screenW": w, "screenH": h, "ok": True})
    monkeypatch.setattr(W, "_capture_shot", lambda qa, out, label, timeout=6.0: shot_path)
    monkeypatch.setattr(W, "_token_cell", lambda surf: (6, 6))
    monkeypatch.setattr(W, "_drive_and_check",
                        lambda qa, eng, c, r, s, t, expect_move=True: (True, (c, r), {}))
    monkeypatch.setattr(W.time, "sleep", lambda *_: None)


_MASK13 = {"cols": 13, "rows": 13, "walkable": []}


def _run_visual(tmp_path, cells):
    return W._visual_registration("http://qa", "http://eng", _MASK13, 13.0, cells,
                                  tmp_path / "out", 0.2, 5.0)


def test_visual_guard_rejects_uncalibratable_capture(monkeypatch, tmp_path):
    _stub_visual(monkeypatch, _png(tmp_path, "odd.png", (1000, 500), (40, 40, 40)))
    res = _run_visual(tmp_path, [(6, 7)])
    assert "neither 1x nor 2x" in res["error"] and res["pass"] == 0


def test_visual_guard_rejects_black_capture(monkeypatch, tmp_path):
    _stub_visual(monkeypatch, _png(tmp_path, "black.png", (1280, 697), (0, 0, 0)))
    res = _run_visual(tmp_path, [(6, 7)])
    assert "capture is BLACK" in res["error"] and res["pass"] == 0


def test_visual_guard_rejects_out_of_frame_cells(monkeypatch, tmp_path):
    _stub_visual(monkeypatch, _png(tmp_path, "grey.png", (1280, 697), (40, 44, 48)))
    res = _run_visual(tmp_path, [(6, 7), (60, 6)])
    assert "project OUTSIDE" in res["error"] and "[60, 6]" in res["error"]


def test_visual_guards_pass_a_normal_frame(monkeypatch, tmp_path):
    _stub_visual(monkeypatch, _png(tmp_path, "ok.png", (1280, 697), (40, 44, 48)))
    res = _run_visual(tmp_path, [(6, 7)])
    assert "error" not in res
    assert res["capture_scale"] == 1.0
    assert res["diff_params"] == {"cell_px": 53.6, "merge_px": 24, "min_area_px": 60}


def test_visual_guard_accepts_2x_hidpi_capture(monkeypatch, tmp_path):
    _stub_visual(monkeypatch, _png(tmp_path, "hidpi.png", (2560, 1394), (40, 44, 48)))
    res = _run_visual(tmp_path, [(6, 7)])
    assert "error" not in res and res["capture_scale"] == 2.0
    assert res["diff_params"]["merge_px"] == 48        # scales with the backing buffer, not /health
