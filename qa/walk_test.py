#!/usr/bin/env python3
"""THE automated WALKABILITY gate — drive the live player and PROVE a room is walkable.

Epic #1581, issue #1582. Beauty (the blind panel) and WALKABILITY are DIFFERENT gates: a plate can
score 8.3 on the panel and still be unwalkable (character projects off the plate, walks through walls,
no occlusion). We shipped 3 panel-8+ unwalkable rooms on 2026-07-15 because this gate did not exist as
automation. This is that gate.

It drives the live player over the QA channel (:8971 /click,/shot,/debug) and the engine surface
(:8766 /combat-surface = the ground truth) and asserts, with NO human:

  1. CAMERA POSE == the build_room_unified contract (the root-cause check). The plate was painted by a
     camera at Euler(30,45,0), pos=-(fwd)*80, aiming at world origin, at the pinned ortho. Actors AND
     occluders are world-placed and projected through Camera.main, so if the runtime camera != that
     contract, EVERYTHING projects offset from the plate (walk-on-tomb / walk-through-wall / no pillar
     masking — all one bug). The client exposes its actual camera pose on /debug; we assert it matches.
     The crisp, aspect-independent form: world origin must project to viewport CENTER (0.5,0.5).
  2. ENGINE TRUTH — every clicked reachable cell resolves the token TO that cell (token == click);
     every impassable cell REJECTS the move (token unchanged). GEOMETRY IS GROUND TRUTH.
  3. DOORS — each door cell is walkable and (cosmetically) sits on the painted arch.
  4. OCCLUSION + evidence — /shot behind occluders for a contact sheet.

GEOMETRY IS GROUND TRUTH (VISION.md): collision/occlusion come from the grid + boxes sidecar; the
plate is cosmetic. So (1) is the linchpin — once the camera reproduces the contract, actors and
occluders land on the painted masses automatically (no per-cell pixel tuning).

The camera-pose asserts and the engine asserts are CU-free (no LLM, no image analysis). /shot is only
for the human/occlusion evidence contact sheet. The pure assertion helpers below are unit-tested in
qa/test_walk_test.py (red-first: a broken camera snapshot must FAIL, a contract snapshot must PASS).

Usage:
  qa/walk_test.py --room crypt                         # gate the current room on the live player
  qa/walk_test.py --room crypt --exhaustive            # click EVERY reachable + impassable cell
  qa/walk_test.py --room crypt --out qa/evidence/walk/ # write walk_report.json + contact sheet here
Requires the owner player running with WORLDOS_QA_INPUT=1 (port 8971) + the engine on :8766.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MANIFEST = REPO / "extensions" / "renderers" / "unity" / "plates_manifest.json"
GEO_DIR = HERE / "room_geometries"

# --- the camera contract (MIRROR qa/greybox_render_headless — build_room_unified's rig) ------------
PITCH_DEG, YAW_DEG, PULLBACK = 30.0, 45.0, 80.0


def _forward() -> tuple:
    """Camera forward in the greybox/Unity convention (== Quaternion.Euler(30,45,0)*Vector3.forward)."""
    p, y = math.radians(PITCH_DEG), math.radians(YAW_DEG)
    return (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))


def contract_cam_pos() -> tuple:
    """The build_room_unified camera position: pulled back PULLBACK units along -forward, aim at origin."""
    fx, fy, fz = _forward()
    return (-fx * PULLBACK, -fy * PULLBACK, -fz * PULLBACK)


def world_to_window_px(wx: float, wy: float, wz: float, ortho: float, w: int, h: int) -> tuple:
    """Project a world point through the CONTRACT camera into WINDOW pixels at the live window size.

    Aspect-correct by construction: the ortho camera fixes the VERTICAL half-height (ortho world
    units); horizontal view = ortho * (w/h). This is the crop model — the plate billboard is sized by
    TEXTURE aspect (ApplyPlate), so a 1.6 window shows a cropped 1.75 plate with uniform px/world and
    NO stretch. The visual-registration mode below measures the actor against THESE coordinates, so a
    systematic horizontal error growing toward the room edges would refute the crop model empirically
    (red-team finding 3) — no arguing from theory required.
    """
    sys.path.insert(0, str(HERE))
    import greybox_render_headless as G  # noqa: PLC0415
    r, u = G._camera_ru(wx, wy, wz)
    aspect = w / h
    vx = 0.5 + r / (2.0 * ortho * aspect)
    vy_up = 0.5 + u / (2.0 * ortho)
    return (vx * w, (1.0 - vy_up) * h)   # image coords, y down


def cell_px(ortho: float, h: int) -> float:
    """One grid cell (2 world units) in window pixels of camera-up — the tolerance unit."""
    return h * (2.0 / (2.0 * ortho))


# --- #1585/#1647 painted-door hotspots: the pure px<->px math the client uses to route a click on the
# --- painted arch to its authored door_cell. TWIN of C# CombatSurfaceClient.WindowToPlatePx (+ its
# --- BuildDoorHotspots inverse). Same CROP model as world_to_window_px above: the ortho camera fixes the
# --- vertical half-height so the plate billboard spans the full frustum height (oh = 2*ortho), the plate
# --- is sized by TEXTURE aspect and centered horizontally -> a uniform px/px scale s = h/tex_h on BOTH
# --- axes (plate pixels are square on the billboard), cropped/centered when the window aspect != plate
# --- aspect. `screen_*` are Unity screen pixels (BOTTOM-LEFT origin); plate px is top-left (image) origin.
def plate_px_to_screen_px(px: float, py: float, w: float, h: float, tex_w: float, tex_h: float) -> tuple:
    """Plate pixel (top-left origin) -> Unity screen pixel (bottom-left origin). Inverse of
    window_px_to_plate_px; used by BuildDoorHotspots to place the arch glow marker."""
    s = h / tex_h
    sx = 0.5 * w + (px - 0.5 * tex_w) * s
    sy = h - py * s   # top-left plate py -> bottom-left screen y
    return (sx, sy)


def window_px_to_plate_px(screen_x: float, screen_y: float, w: float, h: float,
                          tex_w: float, tex_h: float) -> tuple:
    """Unity screen pixel (bottom-left origin) -> plate pixel (top-left origin). EXACT mirror of C#
    CombatSurfaceClient.WindowToPlatePx — the hit-test basis the door-hotspot check runs on."""
    s = h / tex_h
    px = 0.5 * tex_w + (screen_x - 0.5 * w) / s
    py = (h - screen_y) / s   # bottom-left screen -> top-left plate y
    return (px, py)


def door_hotspot_hit(click_px: tuple, hotspot_px: tuple, radius_px: float) -> bool:
    """The pure painted-door hit-test (mirror of the C# TryDoorHotspot distance check): a click, already
    in plate pixels, is inside the hotspot iff it lies within radius_px of the hotspot centroid."""
    dx = click_px[0] - hotspot_px[0]
    dy = click_px[1] - hotspot_px[1]
    return dx * dx + dy * dy <= radius_px * radius_px


# --- PURE assertion helpers (no I/O; unit-tested in qa/test_walk_test.py) --------------------------
def check_camera_pose(dbg: dict, ortho: float, *, deg_tol: float = 1.5,
                      vp_tol: float = 0.03, pos_tol: float = 1.5) -> list:
    """Return a list of failure strings; EMPTY == the runtime camera matches the plate's render rig.

    `dbg` is the /debug JSON (extended with the camera fields, issue #1583). If those fields are
    absent (an old player build) we return a single 'unavailable' failure so the gate is loud, never
    silently green. This is the check that turns RED on the shipped-2026-07-15 broken build and GREEN
    after the ApplyPlate camera-rig fix.
    """
    if dbg.get("camOrtho") is None:
        return ["camera pose unavailable — rebuild the player with the /debug camera extension "
                "(issue #1583); the walkability gate cannot verify projection without it"]
    fails: list = []
    # (a) orthographic size matches the pinned plate ortho
    if abs(float(dbg["camOrtho"]) - ortho) > 0.05:
        fails.append(f"camOrtho {float(dbg['camOrtho']):.4f} != pinned {ortho:.4f}")
    # (b) rotation is the frozen dimetric rig
    for axis, want in (("camRx", PITCH_DEG), ("camRy", YAW_DEG), ("camRz", 0.0)):
        got = ((float(dbg.get(axis, 0.0)) + 180.0) % 360.0) - 180.0
        w = ((want + 180.0) % 360.0) - 180.0
        if abs(got - w) > deg_tol:
            fails.append(f"{axis} {got:.2f} != {want} (deg_tol {deg_tol})")
    # (c) THE aim-at-origin check (the one broken now): world origin projects to viewport CENTER.
    ox, oy = dbg.get("originVX"), dbg.get("originVY")
    if ox is None or oy is None:
        fails.append("originVX/originVY missing from /debug (camera-aim check impossible)")
    elif abs(float(ox) - 0.5) > vp_tol or abs(float(oy) - 0.5) > vp_tol:
        fails.append(f"camera NOT aimed at world origin: origin projects to viewport "
                     f"({float(ox):.3f},{float(oy):.3f}), want (0.500,0.500) — actors+occluders "
                     f"will project offset from the painted plate")
    # (d) belt-and-suspenders: raw position matches the pulled-back contract (if reported)
    cx, cy, cz = dbg.get("camPx"), dbg.get("camPy"), dbg.get("camPz")
    if None not in (cx, cy, cz):
        wx, wy, wz = contract_cam_pos()
        if (abs(float(cx) - wx) > pos_tol or abs(float(cy) - wy) > pos_tol
                or abs(float(cz) - wz) > pos_tol):
            fails.append(f"camera position ({float(cx):.1f},{float(cy):.1f},{float(cz):.1f}) != "
                         f"contract ({wx:.1f},{wy:.1f},{wz:.1f})")
    return fails


def walkmask_from_surface(surf: dict) -> dict:
    """Derive the walkable / impassable / door sets from a /combat-surface payload (engine truth)."""
    grid = surf["grid"]
    cols, rows = int(grid["cols"]), int(grid["rows"])
    default_walkable = bool(grid.get("cellDefault", {}).get("walkable", True))
    blocked = set()
    for cell in grid.get("cells", []):
        if not cell.get("walkable", default_walkable):
            blocked.add((int(cell["c"]), int(cell["r"])))
    doors = {(int(d["cell"][0]), int(d["cell"][1])) for d in surf.get("doors", [])}
    interior = [(c, r) for r in range(rows) for c in range(cols)]
    walkable = [cell for cell in interior if cell not in blocked or cell in doors]
    return {"cols": cols, "rows": rows, "walkable": set(walkable),
            "blocked": blocked - doors, "doors": doors}


def _sample(cells: list, stride: int) -> list:
    """A deterministic representative sample (every `stride`-th cell) — bounds a big room's runtime."""
    return cells if stride <= 1 else cells[::stride]


def bfs_reachable(mask: dict, start: tuple) -> set:
    """4-neighbour BFS over walkable cells from `start` — the engine-truth reachable set."""
    walkable = mask["walkable"] if isinstance(mask["walkable"], set) else set(mask["walkable"])
    if start not in walkable:
        return set()
    seen, stack = {start}, [start]
    while stack:
        c, r = stack.pop()
        for nc, nr in ((c + 1, r), (c - 1, r), (c, r + 1), (c, r - 1)):
            if (nc, nr) in walkable and (nc, nr) not in seen:
                seen.add((nc, nr))
                stack.append((nc, nr))
    return seen


def orphan_cells(mask: dict, start: tuple) -> list:
    """Walkable cells NOT reachable from `start` — orphan pockets (a seed defect, or paint-invented
    'walkable-looking' space with no connection). A walkable room has ZERO orphans; any orphan is RED."""
    return sorted(set(mask["walkable"]) - bfs_reachable(mask, start))


def _frame_mean(img) -> float:
    """Mean RGB level of a captured frame — the black-frame detector for the visual stage."""
    import numpy as np  # noqa: PLC0415

    return float(np.asarray(img.convert("RGB"), dtype=float).mean())


def visual_diff_params(ortho: float, w: int, h: int) -> dict:
    """Scale the pixel-diff clustering constants to the LIVE window (#1672 windowed sandbox).

    The 60px cluster-merge radius and the 250px min-area were tuned at the fullscreen 2984x1634
    baseline, where one grid cell is ~126px — 60px is 0.48 cell, comfortably smaller than a hop. At
    the windowed 1280x697 the same fixed 60px is ~1.1 CELLS: the departure and arrival blobs of a
    short hop MERGE into one centroid midway between them, so a 3-cell hop measures ~half its true
    distance and a correct build reads FALSE RED. Both constants are therefore derived from the
    frame, not hard-coded.
    """
    cpx = cell_px(ortho, h)
    return {"cell_px": round(cpx, 1),
            "merge_px": max(16, round(0.45 * cpx)),
            "min_area_px": max(60, round(250 * (w * h) / (2984 * 1634)))}


def diff_blobs(img_a, img_b, *, min_area_px: int = 250, thresh: int = 60,
               merge_px: int = 60) -> list:
    """Cluster the pixel differences between two frames — the moved actor shows up as the two largest
    blobs (departure + arrival). Returns clusters as dicts {cx, cy, bottom: (x,y), area}, area-sorted.
    Pure (arrays in, dicts out) so it is unit-testable with synthetic frames. Animated fire/glow
    flicker also diffs — callers match blobs BY EXPECTED POSITION and gate on distance, so unmatched
    flicker clusters are ignored.
    """
    import numpy as np  # noqa: PLC0415

    a = np.asarray(img_a, dtype=int)
    b = np.asarray(img_b, dtype=int)
    mask = (np.abs(a - b).sum(axis=2) > thresh)
    ys, xs = np.nonzero(mask)
    clusters: list = []
    for x, y in zip(xs.tolist(), ys.tolist()):
        for c in clusters:
            if abs(x - c["sx"] / c["n"]) < merge_px and abs(y - c["sy"] / c["n"]) < merge_px:
                c["sx"] += x; c["sy"] += y; c["n"] += 1
                if y > c["maxy"]:
                    c["maxy"], c["bxs"] = y, [x]
                elif y == c["maxy"]:
                    c["bxs"].append(x)
                break
        else:
            clusters.append({"sx": float(x), "sy": float(y), "n": 1, "maxy": y, "bxs": [x]})
    out = []
    for c in clusters:
        if c["n"] < min_area_px:
            continue
        out.append({"cx": c["sx"] / c["n"], "cy": c["sy"] / c["n"],
                    "bottom": (sorted(c["bxs"])[len(c["bxs"]) // 2], c["maxy"]),
                    "area": c["n"]})
    return sorted(out, key=lambda c: -c["area"])


def nearest_blob_distance(blobs: list, expected_px: tuple) -> float:
    """Distance from an expected screen point to the nearest diff blob (centroid or feet, whichever
    is closer — the actor's centroid sits above the cell center, its feet on it). inf if no blobs."""
    best = float("inf")
    ex, ey = expected_px
    for bl in blobs:
        for px, py in ((bl["cx"], bl["cy"]), bl["bottom"]):
            d = ((px - ex) ** 2 + (py - ey) ** 2) ** 0.5
            best = min(best, d)
    return best


# --- animated-fire-VFX masking (#1525): braziers/hearths spawn flame VFX whose frame-to-frame flicker
# produces a large diff blob that can WIN the nearest-neighbour race against the actor sprite, reading
# a walkable cell as a false visual RED. These helpers only ever REMOVE candidate blobs / deprioritize
# fire-adjacent sample cells — they can never invent a pass (a case that masks to zero blobs stays a
# loud fail, same as a case with no measurable actor blob).
FIRE_KINDS = frozenset({"brazier", "campfire", "hearth"})


def fire_anchor_cells(source: dict) -> set:
    """Cells occupied by animated-fire props — read from a room-geometry (or surface) `props` list
    whose entries carry {kind, cells:[[c,r],...]}. Empty set if the source has no such props."""
    out: set = set()
    for p in (source or {}).get("props", []):
        if p.get("kind") in FIRE_KINDS:
            for cr in p.get("cells", []):
                out.add((int(cr[0]), int(cr[1])))
    return out


def mask_fire_blobs(blobs: list, fire_px: list, radius_px: float) -> list:
    """Drop any diff blob whose CENTROID lies within `radius_px` of a fire-anchor screen position.
    Additive-only: returns a (possibly empty) subset of `blobs`, never a new blob."""
    if not fire_px:
        return list(blobs)
    kept = []
    for bl in blobs:
        cx, cy = bl["cx"], bl["cy"]
        if any(((cx - fx) ** 2 + (cy - fy) ** 2) ** 0.5 <= radius_px for fx, fy in fire_px):
            continue
        kept.append(bl)
    return kept


def _cheby_far(cell, fire_cells, min_cheby: int) -> bool:
    """True iff `cell` is ≥ min_cheby chebyshev-distance from EVERY fire-anchor cell (vacuously true
    when there are no fire cells)."""
    return all(max(abs(cell[0] - fc[0]), abs(cell[1] - fc[1])) >= min_cheby for fc in fire_cells)


def select_visual_cells(pool: list, n: int, fire_cells, *, min_cheby: int = 2) -> list:
    """Pick up to `n` visual-registration cells, PREFERRING cells ≥ min_cheby from any fire anchor
    (their pixel-diff is polluted by brazier/hearth flicker). Falls back to fire-adjacent cells only
    to top the sample up to `n` when the far pool is too small. Deterministic (strided sample of the
    far tier, then in-order fill). `pool` must already exclude the current/zero-hop cell."""
    if not pool or n <= 0:
        return []
    # HARD exclusion first: a cell within the fire-MASK radius (< 2 chebyshev of a fire anchor) is
    # UNMEASURABLE BY CONSTRUCTION — the mask that suppresses flicker also swallows the actor's own
    # diff there (measured: n_blobs=0 with 4 masked -> guaranteed false fail on a tiny-pool room).
    # An honestly smaller sample beats sampling cells that cannot pass. The room-level
    # zero-measurable protection still applies if nothing measurable remains.
    measurable = [c for c in pool if _cheby_far(c, fire_cells, 2)]
    far = [c for c in measurable if _cheby_far(c, fire_cells, max(min_cheby, 3))]
    ordered = _sample(far, max(1, len(far) // max(1, n))) if far else []
    for src in (far, measurable):  # top up: far tier first, then measurable-but-nearer
        for c in src:
            if len(ordered) >= n:
                break
            if c not in ordered:
                ordered.append(c)
    return ordered[:n]


def _path_cell_cr(cell) -> list:
    """Normalize a path cell ([c,r] list or {c,r} dict — the two shapes path_violations accepts)."""
    if isinstance(cell, dict):
        return [int(cell["c"]), int(cell["r"])]
    return [int(cell[0]), int(cell[1])]


def path_violations(path, mask: dict) -> list:
    """Cells in an engine `lastPath` that are NOT walkable — the owner's actual 'walked through the
    table' failure class: the DESTINATION can be legal while the route crosses a prop. Empty == clean."""
    if not path:
        return []
    walkable = mask["walkable"] if isinstance(mask["walkable"], set) else set(mask["walkable"])
    bad = []
    for cell in path:
        cr = (int(cell[0]), int(cell[1])) if not isinstance(cell, dict) else (int(cell["c"]), int(cell["r"]))
        if cr not in walkable:
            bad.append(list(cr))
    return bad


# --- tri-state classification (PURE; unit-tested) --------------------------------------------------
# A harness/infrastructure defect must NEVER read as a walkability verdict, in either direction. We
# split every failure into a WALKABILITY class (a real room defect the gate exists to catch) vs a
# HARNESS class (player/engine unreachable, /debug missing the camera fields, /shot capture failure,
# drive-error exceptions). Ambiguous → classify as the side that can never certify a broken room green.
def is_drive_error(landed) -> bool:
    """A _drive_and_check result whose `landed` is a 'drive-error:<exc>' sentinel = a HARNESS error
    (click/engine POST threw), NOT the room refusing/mis-resolving a move. Timeouts are NOT drive
    errors — they stay walkability failures (they guard vacuous greens)."""
    return isinstance(landed, str) and landed.startswith("drive-error:")


def classify_camera_fails(dbg: dict, cam_fails: list) -> tuple:
    """Split check_camera_pose failures → (walkability_fails, harness_errors). A wholly-absent camera
    extension (camOrtho None) or an unreachable /debug (dbg carries `_error`) is a HARNESS error; any
    other camera failure is a real pose MISMATCH — wrong ortho/rotation/aim — which is the 2026-07-15
    root-cause class and a genuine walkability RED."""
    if not cam_fails:
        return [], []
    if dbg.get("_error") is not None or dbg.get("camOrtho") is None:
        return [], list(cam_fails)
    return list(cam_fails), []


def classify_pose_observation(dbg: dict, ortho) -> tuple:
    """Classify a door-cross camera re-assert observation → (walkability_fails, harness_errors).
    /debug unreachable = HARNESS; a real ortho/aim/rotation mismatch = walkability RED (the camera-
    contract regression the gate exists to catch); no pinned ortho for the room = neither (skip)."""
    if dbg.get("_error") is not None:
        return [], [f"door-cross /debug unreachable: {dbg['_error']}"]
    if ortho is None:
        return [], []
    return classify_camera_fails(dbg, check_camera_pose(dbg, ortho))


def classify_verdict(report: dict) -> tuple:
    """Pure verdict/exit-code decision → (verdict, exit_code). A WALKABILITY failure (reachable/
    impassable/path/door/visual/orphan, OR a camera-pose mismatch, OR a door-cross pose mismatch)
    → RED/1 — a real room fail WINS even when harness errors are also present. No walkability failure
    but harness_errors non-empty → ERROR/2 (a harness/infra defect, never a room verdict). Clean →
    GREEN/0. The impassable timeout-fail and visual zero-measurable-case fails are walkability fails
    by construction (they live in the fail counters), so they correctly win over harness."""
    walk = bool(
        report.get("camera", {}).get("pose_mismatch")
        or report.get("reachable", {}).get("fail")
        or report.get("impassable", {}).get("fail")
        or report.get("doors", {}).get("fail")
        or report.get("orphans")
        or report.get("path", {}).get("fail")
        or report.get("visual", {}).get("fail")
        or report.get("door_pose_fail")
    )
    if walk:
        return "RED", 1
    if report.get("harness_errors"):
        return "ERROR", 2
    return "GREEN", 0


# --- provenance stamps (self-describing report; closes the #1607 cert traceability loop) -----------
def _repo_sha():
    import subprocess  # noqa: PLC0415
    try:
        r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _manifest_sha256():
    import hashlib  # noqa: PLC0415
    try:
        return hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    except Exception:  # noqa: BLE001
        return None


def _utc_now_iso() -> str:
    from datetime import datetime, timezone  # noqa: PLC0415
    return datetime.now(timezone.utc).isoformat()


def _init_report(room: str, ortho: float, scene: str, mask: dict, engine: str, qa: str) -> dict:
    """Build the base walk_report dict — provenance stamps + zeroed sub-structures. Factored out so
    the provenance keys are unit-testable without a live player."""
    return {
        "schema_version": 1,
        "repo_sha": _repo_sha(),
        "manifest_sha256": _manifest_sha256(),
        "ts": _utc_now_iso(),
        "engine_url": engine, "qa_url": qa,
        "room": room, "ortho": ortho, "sceneId": scene,
        "grid": {"cols": mask["cols"], "rows": mask["rows"]},
        "camera": {}, "orphans": [], "path": {"pass": 0, "fail": 0, "violations": []},
        "reachable": {"pass": 0, "fail": 0, "cases": []},
        "impassable": {"pass": 0, "fail": 0, "cases": []},
        "doors": {"pass": 0, "fail": 0, "cases": []},
        "visual": {"pass": 0, "fail": 0, "cases": []},
        "harness_errors": [], "door_pose_fail": [],
        "shots": [], "verdict": "PENDING",
    }


# --- live I/O helpers ------------------------------------------------------------------------------
def _get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post(url: str, body: dict, timeout: float = 5.0):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _location(surf: dict):
    loc = surf.get("location")
    return loc.get("id") if isinstance(loc, dict) else loc


def _debug_or_error(qa: str) -> dict:
    """POST /debug, returning the JSON or {'_error': str} — never raises (so a pose re-assert on an
    unreachable player is classified as a HARNESS error, not a walkability RED)."""
    try:
        return _post(f"{qa}/debug", {})
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def _check_door_cross(qa: str, engine: str, cell, target, home, settle: float, timeout: float,
                      *, dest_ortho=None, home_ortho=None):
    """Click a door cell → assert the party CROSSES to `target` → return to `home` via the reciprocal
    door. Verifies cross_door works both ways without stranding the party in the wrong room. After the
    cross-arrival is confirmed, re-fetch /debug and record the camera pose against the DESTINATION
    room's pinned ortho; after the return-home leg, record it against the HOME room's ortho — a pose
    mismatch there is the door-cross camera-contract regression (a RED); /debug unreachable is harness.
    The raw pose observations ride `detail['pose']`; run_gate classifies them (keeps this fn I/O-only)."""
    c, r = cell
    try:
        _post(f"{qa}/click", {"c": c, "r": r})
    except Exception as e:  # noqa: BLE001
        return False, {"error": f"click:{e}"}
    deadline = time.time() + timeout
    crossed = None
    while time.time() < deadline:
        time.sleep(settle)
        try:
            loc = _location(_get(f"{engine}/combat-surface"))
        except Exception:  # noqa: BLE001
            continue
        if loc and loc != home:
            crossed = loc
            break
    ok = (crossed == target) if target else bool(crossed)
    pose: dict = {}
    # re-assert the DESTINATION room's camera contract — ONLY when arrival at `target` is CONFIRMED
    # (crossed != home only proves we left, not that we reached `target`) and the target room has a
    # pinned ortho. An unconfirmed cross is already counted by `ok`; do not double-report as a pose
    # fail, and never /debug an unpinned-ortho leg (there is nothing to assert it against).
    if crossed:
        if target and crossed == target and dest_ortho is not None:
            pose["dest"] = {"ortho": dest_ortho, "dbg": _debug_or_error(qa)}
        # return home via the target room's door back to `home`
        try:
            back = next((d for d in _get(f"{engine}/combat-surface").get("doors", [])
                         if d.get("to") == home), None)
            if back:
                _post(f"{qa}/click", {"c": back["cell"][0], "r": back["cell"][1]})
                bdl = time.time() + timeout
                returned_home = False
                while time.time() < bdl:
                    time.sleep(settle)
                    if _location(_get(f"{engine}/combat-surface")) == home:
                        returned_home = True
                        break
                # re-assert the HOME room's camera contract — ONLY when the return CONFIRMED home (a
                # timed-out return strands the party in the target room; asserting HOME's ortho there
                # compares the wrong room = false RED) and home has a pinned ortho.
                if returned_home and home_ortho is not None:
                    pose["home"] = {"ortho": home_ortho, "dbg": _debug_or_error(qa)}
        except Exception:  # noqa: BLE001
            pass
    detail = {"crossed_to": crossed, "target": target}
    if pose:
        detail["pose"] = pose
    return ok, detail


def _token_cell(surf: dict):
    # combat surfaces carry the cast in top-level `tokens`; a REST surface (the registered walkable
    # world) carries it under `stage.tokens` — check both (mirrors CombatSurfaceClient's cast pick).
    toks = surf.get("tokens") or (surf.get("stage") or {}).get("tokens") or []
    if not toks:
        return None
    t = toks[0]
    return (int(t["x"]), int(t["y"]))


def _room_ortho(room: str) -> float:
    man = json.loads(MANIFEST.read_text())
    plates = man.get("plates", man)
    entry = plates.get(room)
    if not entry:
        raise SystemExit(f"[walk_test] room '{room}' not in {MANIFEST} (have: {sorted(plates)[:8]})")
    return float(entry["cameraPin"]["ortho"])


def _room_ortho_opt(room):
    """Pinned ortho for a room, or None if the room isn't in the manifest (door targets can point at
    rooms with no pinned plate — a missing ortho means the door-cross pose check is skipped, not RED)."""
    if not room:
        return None
    try:
        return _room_ortho(room)
    except SystemExit:
        return None


def _load_room_geometry(room: str) -> dict:
    """Best-effort load of a room's geometry JSON (for fire-anchor masking, #1525). Returns {} when no
    geometry file is found — fire masking is additive, never fatal. Resolves via walk_static's
    GEOMETRY_OF map, then a `{room}_geometry.json` fallback (generated rooms land there)."""
    candidates = []
    try:
        sys.path.insert(0, str(HERE))
        import walk_static as WS  # noqa: PLC0415
        g = WS.GEOMETRY_OF.get(room)
        if g:
            candidates.append(GEO_DIR / g)
    except Exception:  # noqa: BLE001
        pass
    candidates.append(GEO_DIR / f"{room}_geometry.json")
    for c in candidates:
        try:
            if c.is_file():
                return json.loads(c.read_text())
        except Exception:  # noqa: BLE001
            continue
    return {}


def _visual_registration(qa: str, engine: str, mask: dict, ortho: float, cells: list,
                         out: Path, settle: float, move_timeout: float, fire_cells=frozenset()) -> dict:
    """MEASURE the actor's rendered screen position with NO client instrumentation (sidecar synthesis,
    adopted): walk a chain of cells; pixel-diff consecutive /shots; the diff blobs are the departure +
    arrival actor sprites. Assert each expected cell projection (world_to_window_px at the LIVE window
    aspect from /health) has a blob within tolerance (1.25 cell-equivalents — sprite centroid/feet +
    glow noise; the broken-build offsets were 3-5 cells). Works on ANY build — this is also the
    empirical answer to the aspect question: a crop model passes; a stretch model fails wide cells."""
    from PIL import Image  # noqa: PLC0415

    res = {"pass": 0, "fail": 0, "cases": [], "window": None}
    try:
        health = _get(f"{qa}/health")
        w, h = int(health["screenW"]), int(health["screenH"])
    except Exception as e:  # noqa: BLE001
        return {"pass": 0, "fail": 0, "cases": [], "window": None, "error": f"health: {e}"}
    res["window"] = [w, h]
    cols, rows = mask["cols"], mask["rows"]

    def _proj_win(cell):
        wx = (cell[0] - (cols - 1) / 2.0) * 2.0
        wz = ((rows - 1) / 2.0 - cell[1]) * 2.0
        return world_to_window_px(wx, 0.6, wz, ortho, w, h)   # 0.6 up = lower-torso/feet band

    # --- #1672 capture preflight: three ways this stage can silently invent a verdict, each now a
    # --- NAMED harness error instead of a mysterious per-cell RED. Windowing the sandbox player made
    # --- all three reachable: a 2x HiDPI backbuffer, a minimized/occluded window, and a window whose
    # --- aspect crops sample cells out of frame.
    calib = _capture_shot(qa, out, "vis_calib")
    if not calib:
        return {**res, "error": "calibration capture FAILED — /shot returned no usable frame"}
    with Image.open(calib) as _im:
        iw, ih = _im.size
        arr_mean = _frame_mean(_im)
    if (iw, ih) == (w, h):
        scale = 1.0
    elif (iw, ih) == (2 * w, 2 * h):
        scale = 2.0                        # macRetinaSupport=1 — capture is the backing buffer
    else:
        return {**res, "error": f"capture geometry {iw}x{ih} is neither 1x nor 2x the reported "
                                f"window {w}x{h} — the projection math cannot be calibrated"}
    res["capture_scale"] = scale
    if arr_mean < 8:
        return {**res, "error": "capture is BLACK — the QA window is minimized/offscreen/occluded; "
                                "ScreenCapture returned no presented frame (mean luminance "
                                f"{arr_mean:.2f} < 8). Fix the window, do not trust this run."}

    def _proj(cell):
        px, py = _proj_win(cell)
        return (px * scale, py * scale)

    outside = [list(c) for c in cells
               if not (0 <= _proj(c)[0] < iw and 0 <= _proj(c)[1] < ih)]
    if outside:
        return {**res, "error": f"cells project OUTSIDE the {iw}x{ih} capture: {outside} — the "
                                f"window aspect ({w}/{h}) crops them out of the ortho frame; widen "
                                f"the window (WORLDOS_PLAYER_WIN_W) or reduce the sample set"}

    tol = 1.25 * cell_px(ortho, ih)
    res["diff_params"] = visual_diff_params(ortho, iw, ih)
    merge_px, min_area_px = res["diff_params"]["merge_px"], res["diff_params"]["min_area_px"]

    # brazier/hearth flame VFX flicker sits ON the fire cell — mask any diff blob within ~1.5 cells of
    # a fire-anchor screen position so it cannot win the nearest-neighbour race against the actor.
    fire_px = [_proj(fc) for fc in fire_cells]
    fire_radius = 1.5 * cell_px(ortho, ih)
    res["fire_cells"] = [list(fc) for fc in sorted(fire_cells)]

    prev = _token_cell(_get(f"{engine}/combat-surface"))
    # Chain the sample nearest-neighbour from the current cell: SHORT hops. The engine token flips
    # immediately but the CLIENT GLIDES the sprite over ~0.3-0.4s/cell — a /shot fired at
    # engine-confirm captures a mid-glide sprite 2-4 cells off (measured proof2 run: settled cells
    # register at ~0.16 cells; mid-glide arrivals read 155-727 px). Short hops + a glide-proportional
    # wait give settled frames.
    todo = [tuple(c) for c in cells if tuple(c) != prev]   # a zero-hop target has nothing to diff
    chain: list = []
    cur = prev or (todo[0] if todo else None)
    while todo:
        nxt = min(todo, key=lambda p: abs(p[0] - cur[0]) + abs(p[1] - cur[1]))
        chain.append(nxt)
        todo.remove(nxt)
        cur = nxt
    shot_prev = _capture_shot(qa, out, "vis_start")
    for i, cell in enumerate(chain):
        ok_move, landed, _p = _drive_and_check(qa, engine, cell[0], cell[1], settle, move_timeout,
                                               expect_move=True)
        hop = (abs(cell[0] - prev[0]) + abs(cell[1] - prev[1])) if prev else 3
        time.sleep(1.2 + 0.45 * hop)   # let the glide finish before measuring
        shot_new = _capture_shot(qa, out, f"vis_{i}_c{cell[0]}r{cell[1]}")
        case = {"cell": list(cell), "moved": ok_move, "dist_px": None, "tol_px": round(tol, 1), "ok": False}
        if ok_move and shot_prev and shot_new:
            raw = diff_blobs(Image.open(shot_prev).convert("RGB"), Image.open(shot_new).convert("RGB"),
                             min_area_px=min_area_px, merge_px=merge_px)
            blobs = mask_fire_blobs(raw, fire_px, fire_radius)   # drop brazier-flicker blobs first
            d_new = nearest_blob_distance(blobs, _proj(cell))    # inf when all blobs were fire-masked
            d_prev = nearest_blob_distance(blobs, _proj(prev)) if prev else 0.0
            case["dist_px"] = round(d_new, 1)
            case["dist_prev_px"] = round(d_prev, 1)
            case["n_blobs"] = len(blobs)
            case["blob_area_max"] = max((b["area"] for b in blobs), default=0)
            case["n_blobs_masked"] = len(raw) - len(blobs)
            case["ok"] = d_new <= tol
        res["cases"].append(case)
        res["pass" if case["ok"] else "fail"] += 1
        if ok_move:
            prev, shot_prev = tuple(cell), shot_new
    return res


def run_gate(room: str, engine: str, qa: str, *, stride: int, out: Path,
             settle: float, move_timeout: float, visual: int = 0) -> dict:
    """Drive the live player through `room` and return a walk_report dict. Never raises on a cell
    failure — records it — so the report is complete; the caller decides the exit code."""
    ortho = _room_ortho(room)
    surf = _get(f"{engine}/combat-surface")
    scene = surf.get("grid", {}).get("sceneId", "")
    if room not in scene:
        print(f"[walk_test] WARNING: live sceneId '{scene}' does not name room '{room}' — the player "
              f"may be in a different room; cross a door first or pass the matching --room.")
    mask = walkmask_from_surface(surf)
    # fire-anchor cells (brazier/hearth VFX) from the room geometry and/or the surface props — used to
    # mask animated-flame diff blobs in visual mode and to prefer fire-distant visual sample cells.
    fire_cells = fire_anchor_cells(_load_room_geometry(room)) | fire_anchor_cells(surf)
    report = _init_report(room, ortho, scene, mask, engine, qa)

    # 1) CAMERA POSE — the root-cause gate. A pose MISMATCH (wrong ortho/rotation/aim) is a
    # walkability RED; a wholly-absent camera extension or an unreachable /debug is a HARNESS error.
    try:
        dbg = _post(f"{qa}/debug", {})
    except Exception as e:  # noqa: BLE001
        dbg = {"_error": str(e)}
    cam_fails = check_camera_pose(dbg, ortho)
    cam_walk, cam_harness = classify_camera_fails(dbg, cam_fails)
    report["camera"] = {"dbg": dbg, "fails": cam_fails, "ok": not cam_fails,
                        "pose_mismatch": bool(cam_walk)}
    report["harness_errors"].extend(cam_harness)

    # 1b) REACHABILITY / ORPHAN check (pure engine-truth, no driving): every walkable cell must be
    # BFS-reachable from the party's current cell. An orphan pocket = a seed defect or paint-invented
    # "walkable-looking" space with no connection — the unreachable-paint class. Any orphan is RED.
    start = _token_cell(surf)
    if start:
        report["orphans"] = [list(c) for c in orphan_cells(mask, start)]
    reachable_set = bfs_reachable(mask, start) if start else set(mask["walkable"])

    interior = [(c, r) for (c, r) in reachable_set
                if 0 < c < mask["cols"] - 1 and 0 < r < mask["rows"] - 1]
    interior.sort()
    doors = sorted(mask["doors"])
    blocked = sorted(mask["blocked"])

    # 2) REACHABLE — click each sampled reachable cell; token must resolve TO it (engine truth), AND
    # the engine's lastPath must cross ONLY walkable cells (the owner's real "walked through the
    # table" failure class: a legal destination via an illegal route).
    for (c, r) in _sample(interior, stride):
        ok, landed, path = _drive_and_check(qa, engine, c, r, settle, move_timeout, expect_move=True)
        report["reachable"]["cases"].append({"cell": [c, r], "landed": landed, "ok": ok})
        # a drive-error exception is a HARNESS error, not the room mis-resolving a reachable move
        if is_drive_error(landed):
            report["harness_errors"].append(f"reachable ({c},{r}): {landed}")
            continue
        report["reachable"]["pass" if ok else "fail"] += 1
        # audit only THIS move's route: the path's endpoint must be the clicked cell (a stale
        # lastWalkPath from a previous move is skipped, never mis-attributed to this click)
        route = path if (path and _path_cell_cr(path[-1]) == [c, r]) else None
        bad = path_violations(route, mask)
        if bad:
            report["path"]["fail"] += 1
            report["path"]["violations"].append({"to": [c, r], "illegal_cells": bad})
        elif route:
            report["path"]["pass"] += 1

    # 3) IMPASSABLE — click each sampled blocked cell; token must NOT move onto it.
    for (c, r) in _sample(blocked, max(1, stride)):
        ok, landed, _p = _drive_and_check(qa, engine, c, r, settle, move_timeout, expect_move=False)
        report["impassable"]["cases"].append({"cell": [c, r], "landed": landed, "ok": ok})
        # a drive-error exception is HARNESS; a timeout (token never became (c,r)) stays a walkability
        # PASS and a token that DID move onto the blocked cell stays a walkability FAIL (unchanged).
        if is_drive_error(landed):
            report["harness_errors"].append(f"impassable ({c},{r}): {landed}")
            continue
        report["impassable"]["pass" if ok else "fail"] += 1

    # 4) DOORS — clicking a door CROSSES to the linked room (cross_door), it does NOT leave the token
    # on the door cell. Assert the cross lands in the door's `to` target, then return home.
    home = _location(surf) or room
    home_ortho = _room_ortho_opt(home)
    for d in surf.get("doors", []):
        cell = tuple(d["cell"])
        target = d.get("to")
        ok, detail = _check_door_cross(qa, engine, cell, target, home, settle, move_timeout,
                                       dest_ortho=_room_ortho_opt(target), home_ortho=home_ortho)
        report["doors"]["cases"].append({"cell": list(cell), "to": target, "ok": ok, "detail": detail})
        # a door click that threw = HARNESS (player unreachable); a genuine cross pass/fail counts as
        # before (existing counting preserved).
        if isinstance(detail.get("error"), str) and detail["error"].startswith("click:"):
            report["harness_errors"].append(f"door {list(cell)}: {detail['error']}")
            continue
        report["doors"]["pass" if ok else "fail"] += 1
        # classify the door-cross camera re-assert: a pose mismatch = RED (walkability), /debug
        # unreachable = harness.
        for leg in ("dest", "home"):
            obs = detail.get("pose", {}).get(leg)
            if not obs:
                continue
            pose_walk, pose_harness = classify_pose_observation(obs["dbg"], obs["ortho"])
            if pose_walk:
                report["door_pose_fail"].append({"door": list(cell), "leg": leg, "fails": pose_walk})
            report["harness_errors"].extend(f"door {list(cell)} {leg}: {m}" for m in pose_harness)

    # 5) VISUAL REGISTRATION (optional, --visual N): pixel-diff actor localization at the LIVE window
    # aspect — the client-instrumentation-free measurement of "does the actor RENDER where the plate
    # says the cell is". Gating when run.
    report["visual"] = {"pass": 0, "fail": 0, "cases": []}
    if visual > 0 and interior:
        # codex review on #1600: exclude the CURRENT token cell from the pool (a zero-hop target has
        # nothing to diff) and TOP the sample back up to N from the remaining reachable interior, so
        # the gate always measures the requested count when enough cells exist.
        cur = _token_cell(_get(f"{engine}/combat-surface"))
        pool = [c for c in interior if c != cur]
        # prefer cells ≥2 chebyshev from any brazier/hearth (their diff is polluted by flame VFX
        # flicker); fall back to fire-adjacent cells only to fill the requested N.
        vis_cells = select_visual_cells(pool, visual, fire_cells)
        report["visual"] = _visual_registration(qa, engine, mask, ortho, vis_cells, out,
                                                settle, move_timeout, fire_cells=fire_cells)
        # a requested visual gate that measured NOTHING must fail loud, never read as a vacuous GREEN
        if not report["visual"]["cases"]:
            err = report["visual"].get("error")
            if err:  # preflight/harness fault, not a room verdict
                report["harness_errors"].append(f"visual: {err}")
            else:
                report["visual"]["fail"] += 1
                report["visual"]["error"] = "visual registration requested but produced no measurable cases"

    # 6) OCCLUSION evidence — a /shot near an occluder for the human contact sheet (non-gating here).
    shot = _capture_shot(qa, out, f"{room}_final")
    if shot:
        report["shots"].append(shot)

    # 7) RETURN HOME (sidecar adoption): leave the party where the gate found it — the campaign under
    # test is not a scratchpad. Best-effort: only when still in the starting room.
    if start and _location(_get(f"{engine}/combat-surface")) == (_location(surf) or room):
        _drive_and_check(qa, engine, start[0], start[1], settle, move_timeout, expect_move=True)

    report["verdict"], _ = classify_verdict(report)
    return report


def _drive_and_check(qa: str, engine: str, c: int, r: int, settle: float, timeout: float,
                     *, expect_move: bool):
    """Click (c,r); poll the engine token. expect_move: token must reach (c,r), else must not.
    Returns (ok, landed, lastPath) — lastPath is the engine's route for the move (path-cell audit)."""
    try:
        before = _token_cell(_get(f"{engine}/combat-surface"))
        _post(f"{qa}/click", {"c": c, "r": r})
    except Exception as e:  # noqa: BLE001
        return False, f"drive-error:{e}", None
    deadline = time.time() + timeout
    landed, path = before, None
    any_surface = False   # did ANY poll after the click actually read the surface?
    while time.time() < deadline:
        time.sleep(settle)
        try:
            surf = _get(f"{engine}/combat-surface")
        except Exception:  # noqa: BLE001
            continue
        any_surface = True
        landed = _token_cell(surf)
        # combat moves ride lastPath; rest walks ride the additive lastWalkPath (#1582 — before it,
        # rest-mode path audits were silently vacuous because the surface's lastPath is combat-only)
        path = surf.get("lastPath") or surf.get("lastWalkPath") or path
        if expect_move and landed == (c, r):
            return True, list(landed), path
        if not expect_move and landed == (c, r):
            return False, list(landed), path  # moved onto an impassable cell — a real failure
    # the engine died mid-probe (ZERO successful surface reads after the click) → a HARNESS error, not
    # a verdict: an impassable check would otherwise false-PASS on the stale `before` cell and a
    # reachable check would false-RED. Partial outages (≥1 good poll) keep the normal timeout semantics.
    if not any_surface:
        return False, f"drive-error:engine surface unreachable after click ({c},{r})", None
    if expect_move:
        return (landed == (c, r)), (list(landed) if landed else None), path
    # impassable: pass iff the token never became (c,r)
    return (landed != (c, r)), (list(landed) if landed else None), path


def _capture_shot(qa: str, out: Path, label: str, timeout: float = 6.0):
    """POST /shot and poll the RETURNED path for existence + size-stability — race-free with the
    numbered-shot client (#1582: a new wos_shot_<id>.png appears only when written) and correct on
    the legacy fixed-path client too (stability guards a half-written overwrite)."""
    try:
        resp = _post(f"{qa}/shot", {})
        src = Path(resp.get("path", "")) if resp.get("path") else None
        if not src:
            return None
        deadline = time.time() + timeout
        last = -1
        while time.time() < deadline:
            time.sleep(0.4)
            if src.exists():
                size = src.stat().st_size
                if size > 0 and size == last:
                    out.mkdir(parents=True, exist_ok=True)
                    dst = out / f"shot_{label}.png"
                    shutil.copy(src, dst)
                    return str(dst)
                last = size
    except Exception:  # noqa: BLE001
        pass
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--room", required=True, help="manifest room key (crypt/tavern/throne_hall/…)")
    ap.add_argument("--engine", default="http://127.0.0.1:8766")
    ap.add_argument("--qa", default="http://127.0.0.1:8971")
    ap.add_argument("--exhaustive", action="store_true", help="click EVERY cell (default: stride sample)")
    ap.add_argument("--stride", type=int, default=3, help="sample every Nth cell unless --exhaustive")
    ap.add_argument("--settle", type=float, default=0.6, help="poll interval while a move resolves")
    ap.add_argument("--move-timeout", type=float, default=6.0)
    ap.add_argument("--visual", type=int, default=0, metavar="N",
                    help="ALSO measure actor screen position on N sampled cells via pixel-diff "
                         "localization (client-instrumentation-free; gating when run)")
    ap.add_argument("--out", default=str(HERE / "evidence" / "walk"))
    args = ap.parse_args(argv)

    out = Path(args.out) / args.room
    stride = 1 if args.exhaustive else max(1, args.stride)
    report = run_gate(args.room, args.engine, args.qa, stride=stride, out=out,
                      settle=args.settle, move_timeout=args.move_timeout, visual=args.visual)
    out.mkdir(parents=True, exist_ok=True)
    (out / "walk_report.json").write_text(json.dumps(report, indent=2) + "\n")

    verdict, exit_code = classify_verdict(report)
    cam = report["camera"]
    print(f"\n=== WALK_TEST {args.room} — {verdict} ===")
    print(f"camera: {'OK' if cam['ok'] else 'FAIL'}"
          + ("" if cam["ok"] else "".join(f"\n    - {m}" for m in cam["fails"])))
    for k in ("reachable", "impassable", "doors", "path", "visual"):
        s = report.get(k) or {"pass": 0, "fail": 0}
        print(f"{k:11s}: {s['pass']} pass / {s['fail']} fail")
    print(f"orphans    : {len(report['orphans'])}"
          + (f" — {report['orphans'][:6]}" if report["orphans"] else ""))
    if report.get("door_pose_fail"):
        print(f"door pose  : {len(report['door_pose_fail'])} mismatch — {report['door_pose_fail'][:3]}")
    if report.get("harness_errors"):
        print(f"HARNESS ({len(report['harness_errors'])}) — NOT a walkability verdict:"
              + "".join(f"\n    - {m}" for m in report["harness_errors"][:8]))
    print(f"report: {out / 'walk_report.json'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
