"""Unit tests for qa/journey_click_sweep.py — the #1523 adversarial prop-click sweep.

Covers the PURE, box-free, HTTP-free cores: converting a live /combat-surface JSON into the
{grid, props, walkable, doors} manifest contract, deriving every adversarial target from a manifest
(footprint-reject / ring-accept / door-cross / random-accept, with unreachable props surfaced not
dropped), the door-graph DFS traversal (stub callables, no engine), and findings aggregation. The live
HTTP driving (_drive_target / _cross_door / run_live) is exercised on the box against a real booted
viewer — deliberately not unit-tested here, mirroring qa/test_journey_eval.py's own split.
"""
from __future__ import annotations

import sys
from pathlib import Path

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

import journey_click_sweep as jcs  # noqa: E402


# ── manifest_from_surface ────────────────────────────────────────────────────────────────────────────
def _surface(cols=6, rows=6, cells=None, doors=None, cell_default_walkable=True):
    return {
        "grid": {"cols": cols, "rows": rows, "cells": cells or [],
                 "cellDefault": {"walkable": cell_default_walkable}},
        "doors": doors or [],
    }


def test_manifest_from_surface_groups_prop_footprints_and_computes_walkable():
    cells = [
        {"c": 0, "r": 0, "type": "wall", "walkable": False},
        {"c": 2, "r": 2, "type": "prop", "walkable": False, "prop_ref": "urn"},
        {"c": 2, "r": 3, "type": "prop", "walkable": False, "prop_ref": "urn"},
        {"c": 5, "r": 0, "type": "door", "walkable": True},
    ]
    m = jcs.manifest_from_surface(_surface(cells=cells))
    assert m["grid"] == {"cols": 6, "rows": 6}
    assert m["props"] == [{"id": "urn", "footprint": [[2, 2], [2, 3]]}]
    walk = {tuple(c) for c in m["walkable"]}
    assert (0, 0) not in walk        # wall
    assert (2, 2) not in walk and (2, 3) not in walk   # prop footprint
    assert (5, 0) in walk            # door — walkable, no prop_ref
    assert len(walk) == 6 * 6 - 3    # every other cell defaults to walkable floor


def test_manifest_from_surface_carries_doors_verbatim():
    doors = [{"cell": [5, 0], "to": "camp_clearing_night", "toName": "Campfire Clearing"}]
    m = jcs.manifest_from_surface(_surface(doors=doors))
    assert m["doors"] == [{"cell": [5, 0], "to": "camp_clearing_night", "toName": "Campfire Clearing"}]


# ── build_adversarial_targets ───────────────────────────────────────────────────────────────────────
def _manifest_with_one_prop(cols=8, rows=8, footprint=None, doors=None):
    footprint = footprint or [[3, 3], [4, 3]]
    fp = {tuple(c) for c in footprint}
    walkable = [[c, r] for r in range(rows) for c in range(cols) if (c, r) not in fp]
    return {"grid": {"cols": cols, "rows": rows},
           "props": [{"id": "crate", "footprint": footprint}],
           "walkable": walkable, "doors": doors or []}


def test_every_footprint_cell_becomes_a_reject_target():
    m = _manifest_with_one_prop(footprint=[[3, 3], [4, 3], [3, 4]])
    plan = jcs.build_adversarial_targets(m, random_count=0)
    reject_cells = {t.cell for t in plan.targets if t.kind == "footprint_reject"}
    assert reject_cells == {(3, 3), (4, 3), (3, 4)}
    assert all(t.expect == "reject" and t.prop_id == "crate"
              for t in plan.targets if t.kind == "footprint_reject")


def test_one_ring_cell_per_prop_lands_adjacent_never_on_footprint():
    m = _manifest_with_one_prop(footprint=[[3, 3], [4, 3]])
    plan = jcs.build_adversarial_targets(m, random_count=0)
    rings = [t for t in plan.targets if t.kind == "ring_accept"]
    assert len(rings) == 1
    r = rings[0]
    assert r.expect == "accept_adjacent"
    assert r.cell not in {(3, 3), (4, 3)}
    # Chebyshev distance 1 from at least one footprint cell
    assert any(max(abs(r.cell[0] - fc[0]), abs(r.cell[1] - fc[1])) == 1 for fc in [(3, 3), (4, 3)])
    assert set(r.forbidden_cells) == {(3, 3), (4, 3)}


def test_prop_with_no_walkable_ring_cell_is_recorded_unreachable_not_dropped():
    # a 1x1 grid: the prop occupies the only cell -> no in-bounds neighbour exists at all.
    m = {"grid": {"cols": 1, "rows": 1}, "props": [{"id": "pinned", "footprint": [[0, 0]]}],
        "walkable": [], "doors": []}
    plan = jcs.build_adversarial_targets(m, random_count=0)
    assert not any(t.kind == "ring_accept" for t in plan.targets)
    assert plan.unreachable == [{"id": "pinned", "cells": [[0, 0]], "reason": "no walkable Chebyshev-1 ring cell"}]


def test_every_door_cell_becomes_a_door_cross_target_with_expected_room():
    m = _manifest_with_one_prop(doors=[{"cell": [7, 0], "to": "camp", "toName": "Camp"},
                                       {"cell": [0, 7], "to": "tavern", "toName": "Tavern"}])
    plan = jcs.build_adversarial_targets(m, random_count=0)
    doors = {(t.cell, t.expected_room) for t in plan.targets if t.kind == "door_cross"}
    assert doors == {((7, 0), "camp"), ((0, 7), "tavern")}


def test_random_targets_are_bounded_disjoint_and_deterministic():
    m = _manifest_with_one_prop()
    plan_a = jcs.build_adversarial_targets(m, random_count=4, rng_seed=42)
    plan_b = jcs.build_adversarial_targets(m, random_count=4, rng_seed=42)
    randoms_a = [t.cell for t in plan_a.targets if t.kind == "random_accept"]
    randoms_b = [t.cell for t in plan_b.targets if t.kind == "random_accept"]
    assert len(randoms_a) == 4
    assert randoms_a == randoms_b, "same seed must reproduce the same random targets"
    footprint = {(3, 3), (4, 3)}
    ring_cells = {t.cell for t in plan_a.targets if t.kind == "ring_accept"}
    assert not (set(randoms_a) & footprint), "a random target must never be a footprint cell"
    assert not (set(randoms_a) & ring_cells), "a random target must never duplicate a ring target"


# ── dfs_sweep (stub callables — a fake 3-room star graph: crypt <-> camp, crypt <-> tavern) ─────────
def _fake_world():
    """A tiny in-memory 3-room graph mirroring the walkslice topology: crypt is the hub, camp and
    tavern each have exactly ONE door straight back to crypt. Returns (get_surface, cross_door, calls)."""
    doors = {
        "crypt": [{"cell": [6, 0], "to": "camp"}, {"cell": [13, 4], "to": "tavern"}],
        "camp": [{"cell": [5, 0], "to": "crypt"}],
        "tavern": [{"cell": [0, 0], "to": "crypt"}],
    }
    state = {"current": "crypt"}
    calls = {"checked_rooms": [], "crossed": []}

    def get_surface():
        return {"location": {"id": state["current"]}, "doors": doors[state["current"]]}

    def run_room_checks(loc_id, surface):
        calls["checked_rooms"].append(loc_id)
        return {"room": loc_id, "targets": []}

    def cross_door(door):
        calls["crossed"].append((state["current"], tuple(door["cell"]), door["to"]))
        state["current"] = door["to"]
        return {"ok": True}

    return get_surface, run_room_checks, cross_door, calls


def test_dfs_sweep_visits_every_room_exactly_once():
    get_surface, run_room_checks, cross_door, calls = _fake_world()
    results = jcs.dfs_sweep(get_surface, run_room_checks, cross_door)
    assert sorted(r["room"] for r in results) == ["camp", "crypt", "tavern"]
    assert calls["checked_rooms"].count("crypt") == 1
    assert calls["checked_rooms"].count("camp") == 1
    assert calls["checked_rooms"].count("tavern") == 1


def test_dfs_sweep_exercises_every_door_including_ones_back_to_a_visited_room():
    get_surface, run_room_checks, cross_door, calls = _fake_world()
    jcs.dfs_sweep(get_surface, run_room_checks, cross_door)
    crossed_pairs = {(frm, to) for (frm, _cell, to) in calls["crossed"]}
    # every door in the star topology gets crossed: crypt->camp, camp->crypt, crypt->tavern, tavern->crypt
    assert crossed_pairs == {("crypt", "camp"), ("camp", "crypt"), ("crypt", "tavern"), ("tavern", "crypt")}


def test_dfs_sweep_records_a_failed_door_crossing_without_crashing():
    get_surface, run_room_checks, _cross_door, calls = _fake_world()

    def flaky_cross(door):
        calls["crossed"].append(("attempt", tuple(door["cell"]), door["to"]))
        if door["to"] == "tavern":
            return {"ok": False, "reason": "not a doorway"}
        return _cross_door(door)

    results = jcs.dfs_sweep(get_surface, run_room_checks, flaky_cross)
    rooms = {r["room"] for r in results}
    assert "tavern" not in rooms, "a failed crossing must never fabricate a visit to the destination"
    crypt_result = next(r for r in results if r["room"] == "crypt")
    door_checks = crypt_result["door_checks"]
    tavern_check = next(d for d in door_checks if d["expected_room"] == "tavern")
    assert tavern_check["ok"] is False


# ── build_findings ───────────────────────────────────────────────────────────────────────────────────
def test_build_findings_flags_any_failing_check_and_tables_per_room():
    room_results = [
        {"room": "crypt", "targets": [
            {"id": "footprint_x_1_1", "kind": "footprint_reject", "pass": True},
            {"id": "ring_x", "kind": "ring_accept", "pass": False, "defect": "landed on the prop"},
        ], "spawn_check": {"id": "spawn_position", "kind": "spawn_position", "pass": True},
         "door_checks": [{"cell": [6, 0], "expected_room": "camp", "ok": True, "pass": True}]},
        {"room": "camp", "targets": [{"id": "r1", "kind": "random_accept", "pass": True}],
         "spawn_check": {"id": "spawn_position", "kind": "spawn_position", "pass": True},
         "unreachable_props": [{"id": "pinned", "cells": [[0, 0]], "reason": "no ring cell"}]},
    ]
    findings = jcs.build_findings(room_results)
    assert findings["passed"] is False
    assert findings["targets_checked"] == 6   # 2 crypt targets + spawn + door, 1 camp target + spawn
    assert findings["targets_with_defects"] == 1
    assert findings["defects"][0]["id"] == "ring_x"
    assert findings["unreachable_props"] == [{"room": "camp", "id": "pinned", "cells": [[0, 0]],
                                              "reason": "no ring cell"}]
    per_room = {r["room"]: r for r in findings["per_room"]}
    assert per_room["crypt"] == {"room": "crypt", "checked": 4, "passed": 3, "failed": 1}
    assert per_room["camp"] == {"room": "camp", "checked": 2, "passed": 2, "failed": 0}


def test_build_findings_fails_on_zero_checks():
    findings = jcs.build_findings([])
    assert findings["passed"] is False
    assert findings["reasons"]
