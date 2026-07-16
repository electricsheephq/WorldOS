"""Regression for the multi-room TOWN generator gate (tools/generate_town.py + tools/dungen_to_fixtures.py).

The generator crops a DunGen layout export into per-room greybox geometries for the registered-plate
pipeline. Running it on the committed DunGen spike layout (rooms room_0/room_1/room_2) used to emit
geometries that FAIL the static walkability gate (qa/walk_static.check_geometry) in three ways — the
three DunGen defect classes this suite pins as fixed:

  1. BOUNDARY DOORS — a doorway projects onto the room-tile boundary, landing one cell inside the crop's
     padded wall ring (on the floor edge) instead of ON the grid perimeter. The door-perimeter-snap
     moves it outward to the adjacent perimeter cell; the old floor-edge cell becomes the landing. An
     already-on-perimeter door is left untouched (room_1's west door (0,3)).
  2. BLOCKED LANDINGS — DunGen prop crates occupy a door's interior landing cell. Landing clearance
     drops the prop cell(s) on every door's landing so it stays walkable.
  3. FLAT-INTERIOR CLASS (#1588) — every interior mass is a low crate (height 1.4 < 2.6); the paint
     stage drifts. dress_tall_anchors authors two `pillar` anchors when a room has no tall interior mass.

Plus a red-first assertion that the committed rooms now pass check_geometry with ZERO failures, and that
the door-perimeter-snap preserves door COUNT (never drops/merges a door — the door_cells[i] <->
connections[i] world/seed contract depends on it).

Deterministic, offline, stdlib + pytest only — NO live services (build the geometries in-process via
convert()/build_geometry()/dress_tall_anchors()).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_QA_DIR = Path(__file__).resolve().parent
_ROOT = _QA_DIR.parent
for _p in (_ROOT / "tools", _QA_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import dungen_to_fixtures as d2f  # noqa: E402
from author_room_geometry import _perimeter_wall_run_props  # noqa: E402
from walk_static import check_geometry  # noqa: E402

# the same command the RUNBOOK / dispatch drives, run in-process below.
_LAYOUT = _QA_DIR / "evidence" / "dungen-spike" / "dungen_basic_layout.json"
_ROOMS = ["room_0", "room_1", "room_2"]
_TOWN = "dwing"


def _stamp_room(geo: dict, *, material: str = "stone", wall_height: float = 5.0) -> dict:
    """Mirror generate_town._stamp_room WITHOUT importing generate_town (its greybox_render_headless
    import pulls in PIL, absent from the servers/engine test venv): perimeter wall runs split at doors,
    walls/impassable recomputed, camera_fit/wall_height stamped."""
    cols, rows = geo["cols"], geo["rows"]
    doors = [tuple(d) for d in geo.get("door_cells", [])]
    runs = _perimeter_wall_run_props(cols, rows, door_cells=doors)
    wall_cells = {tuple(c) for (_id, _kind, cells) in runs for c in cells}
    props = [{"id": rid, "kind": kind, "cells": [list(c) for c in cells]} for rid, kind, cells in runs]
    props += [p for p in geo.get("props", []) if p.get("kind") != "wall_run"]
    prop_cells = {tuple(c) for p in props for c in p["cells"] if p["kind"] != "wall_run"}
    geo["props"] = props
    geo["walls"] = sorted(wall_cells)
    geo["impassable"] = sorted(wall_cells | prop_cells - {tuple(d) for d in doors})
    geo["material"] = material
    geo["camera_fit"] = True
    geo["wall_height"] = wall_height
    return geo


def _build_town_geometries() -> dict:
    import json  # noqa: PLC0415
    layout = json.loads(_LAYOUT.read_text(encoding="utf-8"))
    ctx = d2f.convert(layout, name=_TOWN, upc=2.0, material="stone")
    geos = {}
    for rid in _ROOMS:
        geo = _stamp_room(d2f.build_geometry(ctx, room=rid))
        geo = d2f.dress_focal(geo, name=f"{_TOWN}_{rid}")
        geo = d2f.dress_tall_anchors(geo, name=f"{_TOWN}_{rid}")
        geo["location"] = f"{_TOWN}_{rid}"
        geos[rid] = geo
    return geos


@pytest.fixture(scope="module")
def town():
    return _build_town_geometries()


# ── (a) red-first regression: the committed rooms now pass the static gate ────────────────────────────
def test_committed_rooms_pass_static_gate(town):
    for rid, geo in town.items():
        fails = check_geometry(f"{_TOWN}_{rid}", geo)
        assert fails == [], f"{rid} must pass check_geometry with zero failures, got: {fails}"


# ── (b) every generated room has a tall interior mass (flat-interior class fixed) ─────────────────────
def test_every_room_has_a_tall_prop(town):
    for rid, geo in town.items():
        interior = [p for p in geo["props"] if p.get("kind") != "wall_run"]
        tallest = max((d2f._KIND_HEIGHT.get(p["kind"], 0.0) for p in interior), default=0.0)
        assert tallest >= d2f._ANCHOR_MIN_TALL, (
            f"{rid} tallest interior mass {tallest} < {d2f._ANCHOR_MIN_TALL} (flat-interior class)")


def test_flat_rooms_get_pillar_anchors(town):
    # room_0 and room_1 are all-crate DunGen rooms -> the anchor pass must author AT LEAST ONE
    # pillar anchor each (with the focal pass running first, brazier/altar occupancy can leave room
    # for only one connectivity-safe pair — one tall anchor still clears the flat-interior bar);
    # room_2 already carries a cylinder->pillar, so it must NOT get anchors.
    for rid in ("room_0", "room_1"):
        anchors = [p for p in town[rid]["props"] if p.get("id") in ("anchor_a", "anchor_b")]
        # >=1 is the HARD bar (one tall mass clears the flat-interior class; check_dressing_bars +
        # test_every_room_has_a_tall_prop enforce it end-to-end). Two anchors is BEST-EFFORT under
        # focal occupancy: dress_focal runs first and may consume the second connectivity-safe pair,
        # so pinning ==2 here would make the suite flake on legitimate focal layouts (evaOS P3).
        assert len(anchors) >= 1, f"{rid} needs at least one pillar anchor"
        for p in anchors:
            assert p["kind"] == "pillar"
            (c0, r0), (c1, r1) = p["cells"]
            assert c0 == c1 and abs(r0 - r1) == 1, "each anchor is two vertically-adjacent cells"
    assert not [p for p in town["room_2"]["props"] if p.get("id", "").startswith("anchor")], \
        "room_2 already has a tall pillar and must not get anchors"


# ── (c) an on-perimeter door is not moved by the snap ─────────────────────────────────────────────────
def test_on_perimeter_door_not_moved(town):
    # room_1's west door (0,3) is already on the perimeter -> snap must leave it untouched.
    assert [0, 3] in town["room_1"]["door_cells"], "on-perimeter door (0,3) must survive the snap"
    # and the off-perimeter east door was snapped outward to the right edge.
    assert [11, 3] in town["room_1"]["door_cells"], "off-perimeter door (10,3) must snap to (11,3)"


def test_snapped_doors_land_on_perimeter(town):
    for rid, geo in town.items():
        cols, rows = geo["cols"], geo["rows"]
        for (c, r) in (tuple(d) for d in geo["door_cells"]):
            assert d2f._on_perimeter(c, r, cols, rows), f"{rid} door {(c, r)} not on perimeter after snap"


# ── (d) every door's landing is walkable (not impassable) ─────────────────────────────────────────────
def test_every_door_landing_is_walkable(town):
    for rid, geo in town.items():
        cols, rows = geo["cols"], geo["rows"]
        impassable = {tuple(c) for c in geo["impassable"]}
        for door in (tuple(d) for d in geo["door_cells"]):
            landing = d2f._door_landing(door, cols, rows)
            assert landing not in impassable, f"{rid} door {door} landing {landing} is blocked"


# ── door count preserved (snap never drops/merges a door) ─────────────────────────────────────────────
def test_snap_preserves_door_count():
    import json  # noqa: PLC0415
    layout = json.loads(_LAYOUT.read_text(encoding="utf-8"))
    ctx = d2f.convert(layout, name=_TOWN, upc=2.0, material="stone")
    for rid in _ROOMS:
        # count the raw doorway cells that fall in this room's crop window (pre-snap intent)
        room_floor = ctx["_room_cells"][rid]
        cs = [c for (c, r) in room_floor]
        rs = [r for (c, r) in room_floor]
        c0, c1, r0, r1 = min(cs) - 1, max(cs) + 1, min(rs) - 1, max(rs) + 1
        window = {(c, r) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)}
        raw = {cr for cr in ctx["_door_set"] if cr in window}
        geo = d2f.build_geometry(ctx, room=rid)
        assert len(geo["door_cells"]) == len(raw), (
            f"{rid}: snap changed door count {len(raw)} -> {len(geo['door_cells'])}")
        # door cells stay sorted by row then col
        rc = [(r, c) for (c, r) in (tuple(d) for d in geo["door_cells"])]
        assert rc == sorted(rc), f"{rid} door_cells not sorted by (row, col)"


# ── the generator self-gate exits 0 on the committed layout (integration, no live services) ───────────
def test_generator_self_gate_passes(tmp_path):
    # generate_town's main builds the plates fragment via greybox_render_headless (needs PIL/numpy);
    # skip the end-to-end run where those render deps are absent (e.g. the minimal engine test venv).
    pytest.importorskip("PIL")
    cmd = [sys.executable, str(_ROOT / "tools" / "generate_town.py"), str(_LAYOUT),
           "--rooms", ",".join(_ROOMS), "--town-id", _TOWN, "--out-dir", str(tmp_path),
           "--material", "stone"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"generator self-gate failed:\n{proc.stderr}"
    assert "STATIC GATE RED" not in proc.stderr


def test_every_generated_room_has_a_narrative_focal(town):
    """Beauty-floor dressing (dwing panel lesson): pure-clutter rooms panel at 'clean but generic —
    no narrative focal point' (6 vs 9, measured 2/2). Every generated room must carry at least one
    focal kind, placed clear of door landings, with the static gate still green."""
    for rid, geo in town.items():
        kinds = {p["kind"] for p in geo["props"]}
        assert kinds & d2f._FOCAL_KINDS, f"{rid}: no focal kind in {sorted(kinds)}"
        cols, rows = geo["cols"], geo["rows"]
        landings = {d2f._door_landing(tuple(d), cols, rows) for d in geo["door_cells"]}
        focal_cells = {tuple(c) for p in geo["props"] if p["kind"] in d2f._FOCAL_KINDS
                       for c in p["cells"]}
        assert not (focal_cells & landings), f"{rid}: focal on a door landing"
        assert check_geometry(f"{_TOWN}_{rid}", geo) == [], (
            f"{rid}: static gate regressed after focal dressing")


def test_plates_fragment_pins_carry_the_full_camera_contract(tmp_path):
    """Sidecar round-4 catch: ortho-only pins were the 2026-07-15 camera-bug SHAPE. The client now
    defaults pitch/yaw (#1591) so they're safe — but every emitter must still stamp the full
    contract (provenance; walk_static lints pitch/yaw==30/45 when present)."""
    pytest.importorskip("PIL")  # generate_town imports greybox_render_headless -> PIL; skip in the
    # minimal engine venv exactly like test_generator_self_gate_passes does for the same subprocess.
    import json
    import subprocess
    out = tmp_path / "frag"
    out.mkdir()
    r = subprocess.run(
        [sys.executable, str(_ROOT / "tools" / "generate_town.py"), str(_LAYOUT),
         "--rooms", "room_0", "--town-id", "pin", "--out-dir", str(out), "--material", "stone"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    frag = json.loads((out / "pin_plates_fragment.json").read_text())
    for rid, entry in frag["plates"].items():
        pin = entry["cameraPin"]
        assert pin.get("pitch") == 30 and pin.get("yaw") == 45, f"{rid}: ortho-only pin {pin}"


def test_dressing_bars_fail_loud_when_a_pass_places_nothing():
    """codex P2 pair (#1611): a narrow/dense crop can defeat dress_focal (returns silently) or leave
    dress_tall_anchors no connectivity-safe pair AFTER focal placement — either silent miss re-ships
    the exact drift/generic class the dressing exists to fix. check_dressing_bars is the emit-time
    enforcement generate_town folds into its self-gate (escape hatch: --allow-undressed)."""
    base = {"cols": 5, "rows": 5, "door_cells": [[0, 2]], "walls": [],
            "props": [{"id": "w", "kind": "wall_run", "cells": [[0, 0]]}]}
    both_missing = d2f.check_dressing_bars(dict(base, props=list(base["props"])), name="bare")
    assert len(both_missing) == 2 and any("tall" in f for f in both_missing)         and any("focal" in f for f in both_missing)
    tall_only = dict(base, props=base["props"] + [
        {"id": "a", "kind": "pillar", "cells": [[2, 2], [2, 3]]}])
    fails = d2f.check_dressing_bars(tall_only, name="tallonly")
    assert len(fails) == 1 and "focal" in fails[0]
    dressed = dict(base, props=tall_only["props"] + [
        {"id": "b", "kind": "brazier", "cells": [[3, 2]]}])
    assert d2f.check_dressing_bars(dressed, name="ok") == []
