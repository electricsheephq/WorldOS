#!/usr/bin/env python3
"""STATIC walkability + registry checks — pure functions of repo artifacts, run in CI on every PR.

Epic #1581 (sidecar round-3 adoption): most walkability defects don't need a running player to catch.
This module is the STATIC half of the gate — `qa/walk_test.py` is the LIVE half — and it runs three
places: (1) CI via qa/test_walk_static.py (every PR — the spawn-in-a-barrel / ortho-drift / orphan
classes become pre-merge-impossible), (2) seed time (seed scripts call validate_world and REFUSE to
seed an invalid world — "some of it can just be the engine itself"), (3) qa/room_pipeline.py stages.

Checks (all CU-free, player-free, deterministic):
- MANIFEST LINT: every plates entry has cameraPin.ortho > 0; the plate file exists; pitch/yaw, when
  present, equal the frozen dimetric contract (30/45); a `boxes` sidecar, when named, exists and its
  stamped ortho matches the manifest pin. The ortho-only-pin bug class (the 2026-07-15 root cause)
  and the wrong-sidecar class become structurally unreintroducible.
- ORTHO TRIPLE-CHECK: manifest ortho == boxes-sidecar ortho == _fit_ortho_size(cols, rows) recomputed
  from the room's committed geometry. Three independent sources, one number — any disagreement is an
  instant red before a single click (the wrong-plate/stale-ortho class the live camera check can't see).
- GEOMETRY: door cells sit on the perimeter; each door's interior landing cell is walkable; the
  walkable interior has ZERO orphan pockets (flood fill from each door landing).
- WORLD (seed-level): door_cells[i] <-> connections[i] lengths agree and every wired door has a
  RECIPROCAL door back (the cross_door contract, servers/engine/server.py).

Usage:
  python3 qa/walk_static.py            # validate the whole repo (manifest + all mapped geometries)
  python3 -m pytest qa/test_walk_static.py -q    # the CI entry point (red-first units + repo check)
"""
from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
UNITY = REPO / "extensions" / "renderers" / "unity"
MANIFEST = UNITY / "plates_manifest.json"
GEO_DIR = HERE / "room_geometries"

# manifest room key -> committed geometry file (rooms without an entry get manifest-only lint).
GEOMETRY_OF = {
    "crypt": "crypt_v36_geometry.json",
    "tavern": "tavern_v2_geometry.json",
    "throne_hall": "throne_hall_geometry.json",
    "shop": "shop_geometry.json",
    "tavern_snug": "tavern_snug_geometry.json",
    "dwing_room_0": "dwing_room_0_geometry.json",
}
CONTRACT_PITCH, CONTRACT_YAW = 30.0, 45.0
# mirror greybox_render_headless (kept dependency-free so CI needs no PIL/numpy)
_ASPECT, _FILL = 1344.0 / 768.0, 0.96


def fit_ortho(cols: int, rows: int) -> float:
    """_fit_ortho_size re-derived from the contract camera — the third ortho source."""
    p, y = math.radians(CONTRACT_PITCH), math.radians(CONTRACT_YAW)
    right = (math.cos(y), 0.0, -math.sin(y))
    fwd = (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))
    up = (fwd[1] * right[2] - fwd[2] * right[1],
          fwd[2] * right[0] - fwd[0] * right[2],
          fwd[0] * right[1] - fwd[1] * right[0])
    up = tuple(-u for u in up)
    half_x, half_z = cols, rows  # (cols/2)*2, (rows/2)*2 world units
    corners = [(-half_x, 0.0, -half_z), (half_x, 0.0, -half_z),
               (half_x, 0.0, half_z), (-half_x, 0.0, half_z)]
    max_r = max(abs(c[0] * right[0] + c[1] * right[1] + c[2] * right[2]) for c in corners)
    max_u = max(abs(c[0] * up[0] + c[1] * up[1] + c[2] * up[2]) for c in corners)
    return max(max_r / _ASPECT, max_u) / _FILL


# --- pure checks (unit-tested red-first in qa/test_walk_static.py) ---------------------------------
def lint_manifest_entry(key: str, entry: dict, unity_dir: Path) -> list:
    fails = []
    pin = entry.get("cameraPin") or {}
    ortho = pin.get("ortho")
    if not ortho or float(ortho) <= 0:
        fails.append(f"{key}: cameraPin.ortho missing/invalid — the 2026-07-15 bug class")
    for axis, want in (("pitch", CONTRACT_PITCH), ("yaw", CONTRACT_YAW)):
        if axis in pin and abs(float(pin[axis]) - want) > 0.01:
            fails.append(f"{key}: cameraPin.{axis}={pin[axis]} != frozen contract {want}")
    plate = entry.get("plate")
    if not plate:
        # codex review on #1598: LoadPlateManifest SKIPS plate-less entries — the room silently keeps
        # the previous/baked backdrop. An entry that names a room must name its plate.
        fails.append(f"{key}: manifest entry has no `plate` — the runtime loader skips it and the room "
                     f"keeps the previous backdrop")
    elif not (unity_dir / plate).exists():
        fails.append(f"{key}: plate file missing: {plate}")
    boxes = entry.get("boxes")
    if boxes:
        bp = unity_dir / boxes
        if not bp.exists():
            fails.append(f"{key}: boxes sidecar missing: {boxes} (latent zero-occluder path)")
        else:
            side = json.loads(bp.read_text())
            stamped = side.get("ortho")
            if ortho and (stamped is None or abs(float(stamped) - float(ortho)) > 0.001):
                fails.append(f"{key}: sidecar ortho {stamped} != manifest ortho {ortho}")
            # codex review: an ortho-only sidecar with no volumes silently degrades the runtime to
            # footprint proxies — the room loses the exact occluders the plate was conditioned on.
            if not side.get("boxes"):
                fails.append(f"{key}: boxes sidecar has NO occluder volumes (empty/missing `boxes`)")
    return fails


def check_ortho_triple(key: str, entry: dict, geo: dict) -> list:
    """manifest == sidecar (lint above) == fit-math from geometry — the third leg."""
    pin = (entry.get("cameraPin") or {})
    if not pin.get("ortho") or not geo.get("camera_fit"):
        return []  # non-fit rooms (camp) pin the contract 13 explicitly; nothing to recompute
    want = fit_ortho(int(geo["cols"]), int(geo["rows"]))
    got = float(pin["ortho"])
    if abs(got - want) > 0.01:
        return [f"{key}: manifest ortho {got} != _fit_ortho_size({geo['cols']},{geo['rows']}) = {want:.4f}"]
    return []


def check_geometry(name: str, geo: dict) -> list:
    fails = []
    cols, rows = int(geo["cols"]), int(geo["rows"])
    doors = [tuple(d) for d in geo.get("door_cells", [])]
    # codex review: duplicate door cells break the engine's cross_door resolution
    # (door_cells_list.index((x,y)) can only ever pick the FIRST connection).
    if len(doors) != len(set(doors)):
        dupes = sorted({d for d in doors if doors.count(d) > 1})
        fails.append(f"{name}: duplicate door cells {dupes} — cross_door index() can never reach the "
                     f"second connection")
    # codex review: blocked = walls UNION every non-wall_run prop footprint — the seed path
    # (build_grid_from_geometry) blocks prop cells even when a geometry (e.g. generate_town output)
    # does NOT fold them into `walls`. Mirror the seed truth, or a barrel on a door landing passes.
    prop_cells = {tuple(c) for p in geo.get("props", []) if p.get("kind") != "wall_run"
                  for c in p.get("cells", [])}
    walls = ({tuple(c) for c in geo.get("walls", [])} | prop_cells) - set(doors)
    free = {(c, r) for r in range(rows) for c in range(cols) if (c, r) not in walls}
    landings = []
    for (dc, dr) in doors:
        if not (dc in (0, cols - 1) or dr in (0, rows - 1)):
            fails.append(f"{name}: door {dc, dr} not on the perimeter")
            continue
        inward = (dc + (1 if dc == 0 else -1 if dc == cols - 1 else 0),
                  dr + (1 if dr == 0 else -1 if dr == rows - 1 else 0))
        if inward not in free:
            fails.append(f"{name}: door {dc, dr} landing {inward} is blocked")
        else:
            landings.append(inward)
    if landings:
        seen, stack = {landings[0]}, [landings[0]]
        while stack:
            c, r = stack.pop()
            for n in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
                if n in free and n not in seen and 0 <= n[0] < cols and 0 <= n[1] < rows:
                    seen.add(n)
                    stack.append(n)
        interior = {(c, r) for (c, r) in free if 0 < c < cols - 1 and 0 < r < rows - 1}
        orphans = sorted(interior - seen)
        if orphans:
            fails.append(f"{name}: {len(orphans)} orphan walkable cells (unreachable pockets): "
                         f"{orphans[:6]}")
        for land in landings[1:]:
            if land not in seen:
                fails.append(f"{name}: door landing {land} unreachable from {landings[0]}")
    return fails


def validate_world(rooms: list) -> list:
    """Seed-level door reciprocity. `rooms` = [(room_id, [(door_cell, to_room_id), ...]), ...].
    Seed scripts call this and REFUSE to seed on failures (validation at the engine boundary)."""
    fails = []
    wired = {}
    for rid, doors in rooms:
        for cell, to in doors:
            wired.setdefault(rid, []).append(to)
    for rid, doors in rooms:
        for cell, to in doors:
            if rid not in wired.get(to, []):
                fails.append(f"{rid} door {tuple(cell)} -> {to} has NO reciprocal door back")
    return fails


def validate_seed_doors(rooms: list, geometries: dict, allowed_unwired: set = frozenset()) -> list:
    """codex review on #1598: the seed wires a SUBSET of the geometry's door cells and the rest become
    plain floor — but the PLATE paints them as doorways (the depth showed an opening), so an unwired
    authored door is a painted arch that does nothing: the paint≠world class. Fail unless each unwired
    door is EXPLICITLY allowed (a documented future seam, e.g. the shop's town door). Also fail a
    wired cell the geometry never authored (it would have no painted doorway at all).

    rooms = [(room_id, [(door_cell, to), ...]), ...]; geometries = {room_id: geometry dict};
    allowed_unwired = {(room_id, (c, r)), ...}."""
    fails = []
    for rid, doors in rooms:
        geo = geometries.get(rid)
        if geo is None:
            fails.append(f"{rid}: no geometry provided to validate seed doors against")
            continue
        authored = {tuple(d) for d in geo.get("door_cells", [])}
        wired = {tuple(cell) for cell, _to in doors}
        for cell in sorted(wired - authored):
            fails.append(f"{rid}: seed wires door {cell} the geometry never authored (no painted "
                         f"doorway there)")
        for cell in sorted(authored - wired):
            if (rid, cell) not in allowed_unwired:
                fails.append(f"{rid}: authored door {cell} is UNWIRED — the plate paints a doorway "
                             f"that does nothing (allow it explicitly if it is a future seam)")
    return fails


def validate_repo(manifest_path: Path = MANIFEST, unity_dir: Path = UNITY,
                  geo_dir: Path = GEO_DIR) -> list:
    fails = []
    plates = json.loads(manifest_path.read_text()).get("plates", {})
    for key, entry in plates.items():
        fails += lint_manifest_entry(key, entry, unity_dir)
        geof = GEOMETRY_OF.get(key)
        if geof:
            if (geo_dir / geof).exists():
                geo = json.loads((geo_dir / geof).read_text())
                fails += check_ortho_triple(key, entry, geo)
            else:
                # codex review: silently skipping disables the triple-check exactly when the
                # committed geometry source went missing (rename/delete) — fail loud instead.
                fails.append(f"{key}: mapped geometry {geof} is MISSING — the ortho triple-check "
                             f"cannot run (restore the file or update GEOMETRY_OF)")
    for geof in sorted(geo_dir.glob("*_geometry.json")):
        fails += check_geometry(geof.name, json.loads(geof.read_text()))
    return fails


def main() -> int:
    fails = validate_repo()
    if fails:
        print(f"[walk_static] RED — {len(fails)} failure(s):")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("[walk_static] GREEN — manifest lint + ortho triple-check + geometry checks all pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
