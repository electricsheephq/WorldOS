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


def diff_blobs(img_a, img_b, *, min_area_px: int = 250, thresh: int = 60) -> list:
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
            if abs(x - c["sx"] / c["n"]) < 60 and abs(y - c["sy"] / c["n"]) < 60:
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


def _check_door_cross(qa: str, engine: str, cell, target, home, settle: float, timeout: float):
    """Click a door cell → assert the party CROSSES to `target` → return to `home` via the reciprocal
    door. Verifies cross_door works both ways without stranding the party in the wrong room."""
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
    # return home via the target room's door back to `home`
    if crossed:
        try:
            back = next((d for d in _get(f"{engine}/combat-surface").get("doors", [])
                         if d.get("to") == home), None)
            if back:
                _post(f"{qa}/click", {"c": back["cell"][0], "r": back["cell"][1]})
                bdl = time.time() + timeout
                while time.time() < bdl:
                    time.sleep(settle)
                    if _location(_get(f"{engine}/combat-surface")) == home:
                        break
        except Exception:  # noqa: BLE001
            pass
    return ok, {"crossed_to": crossed, "target": target}


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


def _visual_registration(qa: str, engine: str, mask: dict, ortho: float, cells: list,
                         out: Path, settle: float, move_timeout: float) -> dict:
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
    tol = 1.25 * cell_px(ortho, h)
    cols, rows = mask["cols"], mask["rows"]

    def _proj(cell):
        wx = (cell[0] - (cols - 1) / 2.0) * 2.0
        wz = ((rows - 1) / 2.0 - cell[1]) * 2.0
        return world_to_window_px(wx, 0.6, wz, ortho, w, h)   # 0.6 up = lower-torso/feet band

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
            blobs = diff_blobs(Image.open(shot_prev).convert("RGB"), Image.open(shot_new).convert("RGB"))
            d_new = nearest_blob_distance(blobs, _proj(cell))
            d_prev = nearest_blob_distance(blobs, _proj(prev)) if prev else 0.0
            case["dist_px"] = round(d_new, 1)
            case["dist_prev_px"] = round(d_prev, 1)
            case["n_blobs"] = len(blobs)
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
    report = {"room": room, "ortho": ortho, "sceneId": scene,
              "grid": {"cols": mask["cols"], "rows": mask["rows"]},
              "camera": {}, "orphans": [], "path": {"pass": 0, "fail": 0, "violations": []},
              "reachable": {"pass": 0, "fail": 0, "cases": []},
              "impassable": {"pass": 0, "fail": 0, "cases": []},
              "doors": {"pass": 0, "fail": 0, "cases": []}, "shots": [], "verdict": "PENDING"}

    # 1) CAMERA POSE — the root-cause gate.
    try:
        dbg = _post(f"{qa}/debug", {})
    except Exception as e:  # noqa: BLE001
        dbg = {"_error": str(e)}
    cam_fails = check_camera_pose(dbg, ortho)
    report["camera"] = {"dbg": dbg, "fails": cam_fails, "ok": not cam_fails}

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
        report["impassable"]["pass" if ok else "fail"] += 1

    # 4) DOORS — clicking a door CROSSES to the linked room (cross_door), it does NOT leave the token
    # on the door cell. Assert the cross lands in the door's `to` target, then return home.
    home = _location(surf) or room
    for d in surf.get("doors", []):
        cell = tuple(d["cell"])
        target = d.get("to")
        ok, detail = _check_door_cross(qa, engine, cell, target, home, settle, move_timeout)
        report["doors"]["cases"].append({"cell": list(cell), "to": target, "ok": ok, "detail": detail})
        report["doors"]["pass" if ok else "fail"] += 1

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
        vis_cells = _sample(pool, max(1, len(pool) // max(1, visual)))[:visual]
        for c in pool:
            if len(vis_cells) >= visual:
                break
            if c not in vis_cells:
                vis_cells.append(c)
        report["visual"] = _visual_registration(qa, engine, mask, ortho, vis_cells, out,
                                                settle, move_timeout)
        # a requested visual gate that measured NOTHING must fail loud, never read as a vacuous GREEN
        if not report["visual"]["cases"]:
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

    hard_fail = bool(cam_fails) or report["reachable"]["fail"] or report["impassable"]["fail"] \
        or report["doors"]["fail"] or report["orphans"] or report["path"]["fail"] \
        or report["visual"]["fail"]
    report["verdict"] = "RED" if hard_fail else "GREEN"
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
    while time.time() < deadline:
        time.sleep(settle)
        try:
            surf = _get(f"{engine}/combat-surface")
        except Exception:  # noqa: BLE001
            continue
        landed = _token_cell(surf)
        # combat moves ride lastPath; rest walks ride the additive lastWalkPath (#1582 — before it,
        # rest-mode path audits were silently vacuous because the surface's lastPath is combat-only)
        path = surf.get("lastPath") or surf.get("lastWalkPath") or path
        if expect_move and landed == (c, r):
            return True, list(landed), path
        if not expect_move and landed == (c, r):
            return False, list(landed), path  # moved onto an impassable cell — a real failure
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

    cam = report["camera"]
    print(f"\n=== WALK_TEST {args.room} — {report['verdict']} ===")
    print(f"camera: {'OK' if cam['ok'] else 'FAIL'}"
          + ("" if cam["ok"] else "".join(f"\n    - {m}" for m in cam["fails"])))
    for k in ("reachable", "impassable", "doors", "path", "visual"):
        s = report.get(k) or {"pass": 0, "fail": 0}
        print(f"{k:11s}: {s['pass']} pass / {s['fail']} fail")
    print(f"orphans    : {len(report['orphans'])}"
          + (f" — {report['orphans'][:6]}" if report["orphans"] else ""))
    print(f"report: {out / 'walk_report.json'}")
    return 0 if report["verdict"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
