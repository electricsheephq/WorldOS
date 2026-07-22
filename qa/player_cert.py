#!/usr/bin/env python3
"""player_cert.py — THE standing player-certification suite (the LIVE half). Charter #1651.

The 2026-07-22 process audit's single highest-leverage anti-regression item: fold every CERTIFIED
runtime property a shipped build must hold into ONE tri-state command that asserts them against the
live sandbox player, so a regression (a vanished silhouette, a spawn under painted furniture, a broken
build swapped under a certified one) turns the suite RED instead of decaying silently.

This module is the LIVE-half SLICE (build order, charter): the tri-state SKELETON + assertion REGISTRY
+ the two shared PRIMITIVES the #83 3D spike consumes as exit criteria — silhouette-per-submesh and
spawn-centroid/coherence-open. The full roster-complete suite (camera contract, collision accept/reject,
door+arch hotspots cross, cast-token presence, the version-stamp diff) aggregates these later.

Architecture (mirrors qa/adventure_walk.py + qa/walk_test.py — REUSED, not re-invented):
  * TRI-STATE discipline: GREEN/RED/ERROR -> exit 0/1/2. A harness/infra defect (engine or player
    unreachable, /shot capture failure, no occluder to stand behind) is NEVER a certification verdict —
    it classifies ERROR, never a silent GREEN and never a false RED. A REAL property failure wins.
  * ASSERTION REGISTRY: each assertion = id + roster-SCOPE + a probe fn + the endpoints it NEEDS. The
    runner probes endpoint reachability, runs each applicable assertion, and aggregates a tri-state
    report with per-assertion rows + provenance (repo sha, app path, campaign, player build stamp).
  * PURE cores, unit-tested (qa/test_player_cert.py, red-first): the tri-state classifiers, the
    tall-occluder cell picker, the silhouette diff verdict, and the spawn/coherence-open verdict are
    all pure (JSON in, verdict out) — the box/player is only the I/O the tests monkeypatch.

Engine = SOLE WRITER (VISION.md): this drives the player and reads engine surfaces + frames only; it
never mutates engine state.

Usage (bring the sandbox up FIRST — qa/qa_sandbox.py provisions engine :8866 + player :8972):
  WORLDOS_PLAYER_APP=/tmp/WorldOSPlayer_usertruth.app qa/qa_sandbox.py up --run certlane \\
      --campaign adventure_demo_v1 \\
      --seed-cmd "uv run --directory servers/engine python /ABS/qa/seed_adventure_demo.py {state} crypt"
  qa/player_cert.py --live --run certlane --out qa/evidence/player_cert/
  qa/qa_sandbox.py down --run certlane
Engine-only (Primitive B; no player needed): qa/player_cert.py --engine http://127.0.0.1:8866
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))

import walk_test as W  # noqa: E402 — transport + occluder/diff/projection machinery + tri-state (reused)

UNITY = REPO / "extensions" / "renderers" / "unity"
DEFAULT_ENGINE = "http://127.0.0.1:8866"
DEFAULT_QA = "http://127.0.0.1:8972"
DEFAULT_CAMPAIGN = "adventure_demo_v1"

# A box counts as a TALL occluder (fully covers a standing actor -> the walk-behind silhouette is the
# only thing that keeps the actor visible) when it is at least this many world units high and is NOT
# floor/grout. An actor stands ~2 units; a pillar/wall at 5-7 units masks it completely.
OCCLUDER_MIN_HEIGHT = 3.0
NON_OCCLUDER_KINDS = frozenset({"floor"})

# Per-room VERIFIED fully-occluding cells (grid c,r), EMPIRICALLY calibrated (2026-07-22): with an actor
# on one of these the head-band diff density reads ~0 with the actor hidden — the whole actor + its
# ground ring vanish. The boxes-sidecar occlusion geometry OVER-predicts occlusion for thin pillars
# (it flagged crypt (5,3) as behind pillar_nw, but the live render leaves the actor's head visible), so
# the probe PREFERS a calibrated cell and only falls back to the ray-geometry picker for uncalibrated
# rooms. Calibrated against the certified crypt plate; recalibrate if the plate/camera is re-pinned.
VERIFIED_OCCLUDER_CELLS = {
    "crypt": [(13, 3), (13, 2), (14, 2), (14, 1)],   # NE corner, behind pillar_ne + the east wall
}


# ── cell<->world geometry (the build_room_unified contract, mirror walk_test._visual_registration) ──
def cell_to_world(cell: tuple, cols: int, rows: int) -> tuple:
    """Grid (c,r) -> world (wx, wz) at the pinned cell size (2 world units/cell). Inverse of the
    projection walk_test uses: wx=(c-(cols-1)/2)*2 ; wz=((rows-1)/2 - r)*2."""
    c, r = cell
    return ((c - (cols - 1) / 2.0) * 2.0, ((rows - 1) / 2.0 - r) * 2.0)


def world_to_cell(wx: float, wz: float, cols: int, rows: int) -> tuple:
    """World (wx, wz) -> nearest grid (c,r). Inverse of cell_to_world."""
    c = round(wx / 2.0 + (cols - 1) / 2.0)
    r = round((rows - 1) / 2.0 - wz / 2.0)
    return (int(c), int(r))


def camera_grid_pos(cols: int, rows: int) -> tuple:
    """The contract camera's position expressed in (fractional) grid coordinates — used to decide which
    side of an occluder is 'behind' (further from the camera). The camera is pulled back along -forward
    at Euler(30,45), so it sits off the low-c / high-r corner; a cell is BEHIND an occluder when it lies
    further from this point along the camera->occluder ray."""
    cx, _cy, cz = W.contract_cam_pos()
    return (cx / 2.0 + (cols - 1) / 2.0, (rows - 1) / 2.0 - cz / 2.0)


# ── Primitive A helpers: pick a tall occluder + a walkable cell BEHIND it (PURE; unit-tested) ───────
def find_tall_occluders(boxes: dict, *, min_height: float = OCCLUDER_MIN_HEIGHT) -> list:
    """From a boxes sidecar (the #1649/greybox collision+occlusion truth), return the tall occluders as
    [{name, kind, height, cell}] — a box qualifies when height (size[1]) >= min_height and its kind is
    not floor. `cell` is the box centre mapped to the grid. Tallest first (the surest full cover)."""
    cols, rows = int(boxes.get("cols", 0)), int(boxes.get("rows", 0))
    out = []
    for b in boxes.get("boxes", []):
        kind = b.get("kind", "")
        size = b.get("size") or [0, 0, 0]
        height = float(size[1]) if len(size) > 1 else 0.0
        if kind in NON_OCCLUDER_KINDS or height < min_height:
            continue
        center = [float(v) for v in (b.get("center") or [0, 0, 0])]
        out.append({"name": b.get("name", "?"), "kind": kind, "height": round(height, 3),
                    "center": center, "size": [float(v) for v in size],
                    "cell": list(world_to_cell(center[0], center[2], cols, rows))})
    return sorted(out, key=lambda o: -o["height"])


def _ray_hits_box(cam: tuple, target: tuple, bmin: list, bmax: list, *, eps: float = 1e-3) -> bool:
    """Slab test: does the SEGMENT camera->target enter the world AABB [bmin,bmax] BEFORE reaching the
    target? (i.e. is the target occluded by the box along this view ray). Occlusion is a 3D property —
    a 2D screen-bbox test admits false positives (a cell whose screen position overlaps the column but
    whose view ray passes beside the box in depth), so the certification uses the true ray test."""
    d = [target[i] - cam[i] for i in range(3)]
    tmin, tmax = 0.0, 1.0
    for i in range(3):
        if abs(d[i]) < 1e-9:
            if cam[i] < bmin[i] or cam[i] > bmax[i]:
                return False
        else:
            t1 = (bmin[i] - cam[i]) / d[i]
            t2 = (bmax[i] - cam[i]) / d[i]
            if t1 > t2:
                t1, t2 = t2, t1
            tmin = max(tmin, t1)
            tmax = min(tmax, t2)
            if tmin > tmax:
                return False
    return tmin < 1.0 - eps and tmax > eps      # the box lies between the camera and the target


def _aabbs(occluders: list) -> list:
    """[(bmin, bmax)] world AABBs for a list of occluder dicts."""
    out = []
    for o in occluders:
        cx, cy, cz = o["center"]
        sx, sy, sz = o["size"]
        out.append(([cx - sx / 2, cy - sy / 2, cz - sz / 2],
                    [cx + sx / 2, cy + sy / 2, cz + sz / 2]))
    return out


def _occluded_by_any(cam: tuple, target: tuple, aabbs: list) -> bool:
    return any(_ray_hits_box(cam, target, bmin, bmax) for (bmin, bmax) in aabbs)


def cell_head_masked(cell: tuple, cols: int, rows: int, cam: tuple, aabbs: list) -> bool:
    """True iff an actor's UPPER BODY + HEAD (world y ≈ 1.0..2.3) on `cell` is hidden by the UNION of
    the tall occluders. The head band is the detection region: with the head behind a tall column, the
    silhouette-OFF frame shows only unchanged column there, while silhouette-ON paints the tint — a
    clean separation that the ground move/selection RING (down at y≈0, well below the head band) cannot
    pollute. This deliberately does NOT require the ground/ring point occluded — a thin pillar hides the
    head even when the ring peeks, and the head-band diff density (not the ring) is what the verdict
    reads (measured 2026-07-22: pillar_nw/(5,3) hides the head; the ring peeked but sits ~1.5 cells below
    the head window)."""
    wx, wz = cell_to_world(cell, cols, rows)
    pts = [(wx, 1.0, wz), (wx, 1.7, wz), (wx, 2.3, wz)]
    return all(_occluded_by_any(cam, p, aabbs) for p in pts)


def head_window_diff_density(img_a, img_b, center_px: tuple, radius: int, thresh: int) -> float:
    """Fraction of pixels in a square window around `center_px` whose per-pixel colour changed by more
    than `thresh` (sum of abs channel diffs) between two frames. The silhouette-detection signal: a
    non-trivial density in the head-band window means the actor's upper body renders on the column (the
    walk-behind silhouette); a near-zero density means the column is unchanged (the actor vanished —
    WORLDOS_SILHOUETTE=0). Pure (arrays in, float out) — unit-testable with synthetic frames."""
    import numpy as np  # noqa: PLC0415
    a = np.asarray(img_a, dtype=int)
    b = np.asarray(img_b, dtype=int)
    h, w = a.shape[0], a.shape[1]
    x, y = int(center_px[0]), int(center_px[1])
    ya, yb = max(0, y - radius), min(h, y + radius)
    xa, xb = max(0, x - radius), min(w, x + radius)
    if ya >= yb or xa >= xb:
        return 0.0
    pa, pb = a[ya:yb, xa:xb], b[ya:yb, xa:xb]
    changed = np.abs(pa - pb).sum(axis=2) > thresh
    return float(changed.mean())


def choose_occluded_cell(occluders: list, walkable: set, cols: int, rows: int, cam: tuple) -> Optional[dict]:
    """Pick a walkable INTERIOR cell whose actor AND ground ring are fully hidden by the UNION of the
    tall occluders (cell_fully_masked), and report the primary (nearest tall) occluder. Prefers the cell
    closest to any occluder (the actor stands just behind it). Returns {cell, occluder} or None when no
    fully-masked walkable cell exists (the caller then classifies the probe ERROR — never a false RED on
    an un-occludable actor).

    Occlusion is tested against the WHOLE occluder set, not one box: a corner cell is masked by a pillar
    AND a wall together (measured 2026-07-22 — a single thin pillar hides the body but the ring peeks;
    the corner cell (13,3), backed by pillar + east wall, hides everything and gives a clean RED)."""
    aabbs = _aabbs(occluders)
    occ_cells = [tuple(o["cell"]) for o in occluders]
    best, best_key = None, None
    for c in range(1, cols - 1):
        for r in range(1, rows - 1):
            cell = (c, r)
            if cell in occ_cells or cell not in walkable:
                continue
            if not cell_head_masked(cell, cols, rows, cam, aabbs):
                continue
            ring = min(abs(c - oc[0]) + abs(r - oc[1]) for oc in occ_cells) if occ_cells else 0
            if best_key is None or ring < best_key:
                best, best_key = cell, ring
    if best is None:
        return None
    primary = min(occluders, key=lambda o: abs(best[0] - o["cell"][0]) + abs(best[1] - o["cell"][1]))
    return {"cell": best, "occluder": primary}


def silhouette_verdict(rec: dict) -> str:
    """PURE tri-state for the silhouette probe. ERROR (harness — never a certification verdict) when the
    probe could not be set up or measured: no tall occluder, no head-masked walkable cell behind one,
    the move to the occluded cell failed, or the frames are frozen (no diff anywhere — a /shot capture /
    render-infra failure). Otherwise the verdict is the HEAD-BAND diff density at the occluded cell: with
    the head behind a tall column, a non-trivial density means the walk-behind SILHOUETTE renders on the
    column (GREEN); a near-zero density means the occluded actor vanished (RED — the #1572/#1545
    regression, exactly what WORLDOS_SILHOUETTE=0 reproduces). The head band is used because the ground
    move/selection RING can peek below a thin column and would pollute a feet-height test, but never
    reaches the head band."""
    if rec.get("harness_errors"):
        return "ERROR"
    if rec.get("no_occluder") or rec.get("no_behind_cell") or not rec.get("moved"):
        return "ERROR"
    density, min_density = rec.get("head_density"), rec.get("min_density")
    if density is None or min_density is None:
        return "ERROR"
    if rec.get("frames_identical"):
        return "ERROR"      # two identical frames — no evidence about the silhouette
    return "GREEN" if density >= min_density else "RED"


# ── Primitive B helpers: spawn/arrival cells reachable, prop-clear, coherence-open (PURE) ───────────
def _actor_tokens(surface: dict) -> list:
    """Every actor token on a surface (combat `tokens` and/or REST `stage.tokens`), as
    [{name, team, cell:(c,r)}]. Skips tokens with no locatable cell."""
    pools = (surface.get("tokens") or []) + ((surface.get("stage") or {}).get("tokens") or [])
    out = []
    for t in pools:
        if "x" in t and "y" in t:
            cell = (int(t["x"]), int(t["y"]))
        else:
            cr = t.get("stage_cell") or t.get("cell")
            if not cr:
                continue
            cell = (int(cr[0]), int(cr[1]))
        out.append({"name": t.get("name") or t.get("label") or t.get("id") or "?",
                    "team": (t.get("team") or t.get("side") or "").lower(), "cell": cell})
    return out


def spawn_state_results(surface: dict, cell_verdicts: Optional[dict] = None) -> dict:
    """PURE spawn/arrival assert against a LIVE engine surface (no player). Every actor token (party +
    staged NPCs/monsters = the seed's spawn + arrival cells) must be: (a) PROP-CLEAR — on a walkable
    grid cell, never a wall/prop footprint; (b) REACHABLE — BFS-connected to the party anchor (no
    orphan pocket); (c) COHERENCE-OPEN — where a paint_coherence report exists, on a cell the player
    SEES as open/ambiguous, never one classified `covered` (under painted furniture, #1647). Returns
    per-token rows + a tri-state verdict. This is qa/test_seed_spawns.py's placement discipline asserted
    as LIVE STATE rather than duplicated."""
    mask = W.walkmask_from_surface(surface)
    walkable = mask["walkable"] if isinstance(mask["walkable"], set) else set(mask["walkable"])
    tokens = _actor_tokens(surface)
    anchor = W._token_cell(surface)
    reachable = W.bfs_reachable(mask, anchor) if anchor else walkable
    rows, harness = [], []
    for tok in tokens:
        cell = tok["cell"]
        prop_clear = cell not in mask["blocked"]
        reachable_ok = cell in reachable
        verdict = cell_verdicts.get(cell) if cell_verdicts else None
        coherence_ok = verdict != "covered"   # open / ambiguous / unclassified all pass; covered fails
        ok = prop_clear and reachable_ok and coherence_ok
        rows.append({"name": tok["name"], "team": tok["team"], "cell": list(cell),
                     "prop_clear": prop_clear, "reachable": reachable_ok,
                     "coherence": verdict, "coherence_ok": coherence_ok, "ok": ok})
    party = [t for t in tokens if t["team"] in ("party", "pc", "player", "ally", "")]
    # No actors at all, or no party, means the seed placed nothing certifiable — a real RED spawn
    # failure, never a vacuous GREEN. (An empty surface is a seed defect, not a harness error.)
    if not tokens:
        return {"tokens": [], "verdict": "RED", "detail": "no actor tokens on the surface",
                "harness_errors": harness}
    if not party:
        return {"tokens": rows, "verdict": "RED", "detail": "no PARTY token on the surface",
                "harness_errors": harness}
    verdict = "RED" if any(not r["ok"] for r in rows) else "GREEN"
    return {"tokens": rows, "anchor": list(anchor) if anchor else None, "verdict": verdict,
            "harness_errors": harness,
            "detail": {"n_tokens": len(rows), "n_bad": sum(1 for r in rows if not r["ok"])}}


# ── the tri-state certification aggregate (PURE; walk_test/adventure_walk discipline) ───────────────
def classify_cert_verdict(report: dict) -> tuple:
    """Overall (verdict, exit_code). Any RED assertion -> RED/1 (a real property failure wins even
    beside harness noise). Else any ERROR assertion or top-level harness_errors -> ERROR/2. Else, IFF at
    least one assertion actually ran GREEN -> GREEN/0. An all-SKIP run (nothing verified) is ERROR/2 —
    a certification that asserted nothing must never read GREEN."""
    rows = report.get("assertions", [])
    if any(a.get("verdict") == "RED" for a in rows):
        return "RED", 1
    if any(a.get("verdict") == "ERROR" for a in rows) or report.get("harness_errors"):
        return "ERROR", 2
    if any(a.get("verdict") == "GREEN" for a in rows):
        return "GREEN", 0
    return "ERROR", 2


# ── live drive (I/O; monkeypatched in tests via walk_test._get/_post) ───────────────────────────────
def _room_of(surface: dict) -> Optional[str]:
    return W._location(surface)


def _boxes_for_room(room: str) -> Optional[dict]:
    """Load the boxes sidecar for `room` from the manifest entry (or a {room}_boxes.json fallback).
    None if the room has no boxes sidecar on disk (the silhouette probe then classifies ERROR)."""
    try:
        man = json.loads(W.MANIFEST.read_text())
        entry = man.get("plates", man).get(room, {})
        boxes_rel = entry.get("boxes")
    except Exception:  # noqa: BLE001
        boxes_rel = None
    candidates = []
    if boxes_rel:
        candidates.append(UNITY / boxes_rel)
    candidates.append(UNITY / "boxes" / f"{room}_boxes.json")
    for c in candidates:
        try:
            if c.is_file():
                return json.loads(c.read_text())
        except Exception:  # noqa: BLE001
            continue
    return None


def probe_silhouette(ctx: dict) -> dict:
    """PRIMITIVE A — drive the party actor BEHIND a tall occluder and prove the walk-behind silhouette
    still renders (#1572 per-submesh clone). Captures a baseline /shot at the party's open cell, moves
    the party to the occluded cell, /shots again, and pixel-diffs the two frames (reusing walk_test's
    diff_blobs / projection). The ARRIVAL blob near the occluded cell's projection == the silhouette is
    rendered; its ABSENCE == the actor vanished behind the occluder (RED — the exact WORLDOS_SILHOUETTE=0
    regression). No LLM / no image model — deterministic and red-first."""
    engine, qa, out = ctx["engine"], ctx["qa"], ctx["out"]
    settle, timeout = ctx["settle"], ctx["move_timeout"]
    rec = {"harness_errors": [], "moved": False, "no_occluder": False, "no_behind_cell": False}
    try:
        surf = W._get(f"{engine}/combat-surface")
    except Exception as e:  # noqa: BLE001
        rec["harness_errors"].append(f"surface: {e}")
        return rec
    room = _room_of(surf) or ctx.get("room") or ""
    rec["room"] = room
    boxes = _boxes_for_room(room)
    if not boxes:
        rec["no_occluder"] = True
        rec["detail"] = f"no boxes sidecar for room '{room}' — cannot pick an occluder"
        return rec
    mask = W.walkmask_from_surface(surf)
    walkable = mask["walkable"] if isinstance(mask["walkable"], set) else set(mask["walkable"])
    cols, rows = mask["cols"], mask["rows"]
    start = W._token_cell(surf)
    reachable = W.bfs_reachable(mask, start) if start else walkable

    # window size + ortho (walk_test convention: /health screenW/screenH) — needed to pick a
    # SCREEN-masked behind-cell, so fetch it before choosing the occluder.
    try:
        health = W._get(f"{qa}/health")
        w, h = int(health["screenW"]), int(health["screenH"])
    except Exception as e:  # noqa: BLE001
        rec["harness_errors"].append(f"health: {e}")
        return rec
    try:
        ortho = float(boxes.get("ortho") or W._room_ortho(room))
    except Exception:  # noqa: BLE001
        ortho = float(boxes.get("ortho") or 11.0)
    rec["window"] = [w, h]

    def _torso(cell):       # ON the occluder column, ABOVE the visible floor + its move/selection ring
        wx, wz = cell_to_world(cell, cols, rows)
        return W.world_to_window_px(wx, 1.8, wz, ortho, w, h)

    cam = W.contract_cam_pos()
    occluders = find_tall_occluders(boxes)
    avail = (reachable - {start}) if start else reachable
    # PREFER an empirically-calibrated fully-occluding cell for this room; fall back to the ray-geometry
    # picker for uncalibrated rooms (the boxes geometry over-predicts occlusion for thin pillars).
    verified = [c for c in VERIFIED_OCCLUDER_CELLS.get(room, []) if c in avail]
    if verified:
        target = verified[0]
        chosen = min(occluders, key=lambda o: abs(target[0] - o["cell"][0]) + abs(target[1] - o["cell"][1])) \
            if occluders else {"name": "calibrated", "height": None}
        pick = {"cell": target, "occluder": chosen, "source": "calibrated"}
    else:
        pick = choose_occluded_cell(occluders, avail, cols, rows, cam) if occluders else None
    if not pick:
        rec["no_occluder"] = bool(not occluders)
        rec["no_behind_cell"] = bool(occluders)
        rec["occluders"] = occluders[:6]
        rec["detail"] = "no tall occluder with a fully-masked walkable cell behind it"
        return rec
    rec["cell_source"] = pick.get("source", "geometry")
    chosen, target = pick["occluder"], pick["cell"]
    rec["occluder"] = chosen
    rec["behind_cell"] = list(target)
    rec["start_cell"] = list(start) if start else None

    # HEAD-band detection: measure the fraction of changed pixels in a small window at the occluded
    # cell's head projection (world y≈2.4, ON the tall column, well ABOVE the ground move/selection ring).
    # With the head behind the column, silhouette-ON paints the tint there (high density) and silhouette-
    # OFF leaves unchanged column (near-zero) — a clean separation the ground ring cannot pollute.
    def _head(cell):
        wx, wz = cell_to_world(cell, cols, rows)
        return W.world_to_window_px(wx, 2.4, wz, ortho, w, h)

    radius = max(6, int(round(0.55 * W.cell_px(ortho, h))))
    rec["head_px"] = [round(v) for v in _head(target)]
    rec["head_radius_px"] = radius
    rec["min_density"] = 0.045      # ≥ this fraction changed in the head window == silhouette rendered

    shot_start = W._capture_shot(qa, out, "sil_baseline")
    # the calibrated occluded cell can be far from the party's spawn — a ~10-cell path + glide takes
    # well over the default 8s move budget (measured 2026-07-22: an 8s timeout read a still-gliding
    # party as moved=False -> ERROR). Give the placement move a generous timeout scaled by distance.
    hop = (abs(target[0] - start[0]) + abs(target[1] - start[1])) if start else 6
    place_timeout = max(timeout, 6.0 + 1.5 * hop)
    ok_move, landed, _p = W._drive_and_check(qa, engine, target[0], target[1], settle, place_timeout,
                                             expect_move=True)
    if W.is_drive_error(landed):
        rec["harness_errors"].append(f"move to behind-cell {list(target)}: {landed}")
        return rec
    rec["moved"] = bool(ok_move)
    rec["landed"] = landed
    time.sleep(1.5 + 0.45 * hop)     # let the client glide finish before the occluded /shot
    shot_behind = W._capture_shot(qa, out, "sil_behind")
    rec["frames"] = {"baseline": shot_start, "behind": shot_behind}
    if not (shot_start and shot_behind):
        rec["harness_errors"].append("shot capture failed (baseline or behind frame missing)")
        return rec
    from PIL import Image  # noqa: PLC0415
    ba = Image.open(shot_start).convert("RGB")
    bh = Image.open(shot_behind).convert("RGB")
    # a lowered threshold (28) so the semi-transparent (~0.45 alpha) silhouette tint over stone registers.
    rec["head_density"] = round(head_window_diff_density(ba, bh, _head(target), radius, thresh=28), 4)
    # frozen-frame guard: if the WHOLE frame is byte-identical the /shot pipeline is stuck (no evidence).
    import numpy as np  # noqa: PLC0415
    rec["frames_identical"] = bool(np.array_equal(np.asarray(ba), np.asarray(bh)))
    # hygiene: walk the party back to its start cell so a subsequent assert/run reads the seed spawn
    # state, not a token parked behind an occluder (best-effort — never a verdict).
    if start and rec.get("moved"):
        try:
            W._drive_and_check(qa, engine, start[0], start[1], settle, place_timeout, expect_move=True)
        except Exception:  # noqa: BLE001
            pass
    return rec


def _silhouette_row(ctx: dict) -> dict:
    rec = probe_silhouette(ctx)
    verdict = silhouette_verdict(rec)
    detail = rec.get("detail")
    if verdict in ("GREEN", "RED"):
        detail = (f"occluder={rec.get('occluder', {}).get('name')} h={rec.get('occluder', {}).get('height')} "
                  f"behind={rec.get('behind_cell')} head_density={rec.get('head_density')} "
                  f"min={rec.get('min_density')} head_px={rec.get('head_px')}")
    return {"verdict": verdict, "detail": detail, "record": rec,
            "harness_errors": rec.get("harness_errors", [])}


def _spawn_row(ctx: dict) -> dict:
    engine = ctx["engine"]
    try:
        surf = W._get(f"{engine}/combat-surface")
    except Exception as e:  # noqa: BLE001
        return {"verdict": "ERROR", "detail": f"engine surface unreachable: {e}",
                "harness_errors": [f"spawn: surface: {e}"], "record": {}}
    room = _room_of(surf) or ctx.get("room")
    cell_verdicts = None
    try:
        from seed_gfx_town import load_cell_verdicts  # noqa: PLC0415
        reports = REPO / "qa" / "evidence" / "paint-coherence"
        cell_verdicts = load_cell_verdicts(reports, room) if room else None
    except Exception:  # noqa: BLE001
        cell_verdicts = None
    res = spawn_state_results(surf, cell_verdicts)
    res["room"] = room
    res["coherence_report"] = bool(cell_verdicts)
    return {"verdict": res["verdict"], "detail": res.get("detail"), "record": res,
            "harness_errors": res.get("harness_errors", [])}


# ── assertion registry ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Assertion:
    id: str
    scope: str                       # roster-scope: "engine_state" | "live_party" (extends to "roster")
    needs: tuple                     # endpoints required: ("engine",) or ("engine", "player")
    probe: Callable[[dict], dict]    # (ctx) -> {verdict, detail, record, harness_errors}
    doc: str


REGISTRY: list = [
    Assertion(
        id="spawn_coherence_open", scope="engine_state", needs=("engine",), probe=_spawn_row,
        doc="Primitive B — party spawn + arrival cells are prop-clear, BFS-reachable, and (where a "
            "coherence report exists) on cells the player sees as open, never `covered` (#1584/#1647)."),
    Assertion(
        id="silhouette_behind_occluder", scope="live_party", needs=("engine", "player"),
        probe=_silhouette_row,
        doc="Primitive A — the party actor placed BEHIND a tall occluder still renders its walk-behind "
            "silhouette (#1572 per-submesh clone; WORLDOS_SILHOUETTE=0 turns this RED)."),
]


def _reachable(url: str, post: bool) -> bool:
    try:
        if post:
            W._post(url, {})
        else:
            W._get(url)
        return True
    except Exception:  # noqa: BLE001
        return False


def _player_build_stamp(qa: str) -> Optional[dict]:
    """Best-effort build/version stamp the player self-reports on /debug or /health — the seed of the
    #1651 version-stamp diff (certified build != installed build). None if not exposed by this build."""
    for path, post in ((f"{qa}/health", False), (f"{qa}/debug", True)):
        try:
            j = W._post(path, {}) if post else W._get(path)
            stamp = {k: j[k] for k in ("build", "version", "buildStamp", "commit", "appVersion")
                     if k in j}
            if stamp:
                return stamp
        except Exception:  # noqa: BLE001
            continue
    return None


def init_report(engine: str, qa: str, campaign: str, app: str, live: bool) -> dict:
    return {
        "schema_version": 1,
        "suite": "player_cert",
        "repo_sha": W._repo_sha(),
        "manifest_sha256": W._manifest_sha256(),
        "ts": W._utc_now_iso(),
        "engine_url": engine, "qa_url": qa, "campaign": campaign,
        "app_path": app, "live": live,
        "player_build": None,
        "assertions": [], "harness_errors": [], "verdict": "PENDING",
    }


def run_cert(engine: str, qa: str, out: Path, *, campaign: str, app: str, live: bool,
             settle: float, move_timeout: float, only: Optional[set] = None) -> dict:
    """Run the applicable registry assertions against the live sandbox and aggregate a tri-state report.
    Assertions whose endpoints aren't reachable — or player-dependent assertions in a non-live run —
    record SKIP (reported, never gating); an assertion asked for under --live whose endpoint is down
    records ERROR (harness)."""
    out.mkdir(parents=True, exist_ok=True)
    report = init_report(engine, qa, campaign, app, live)
    engine_up = _reachable(f"{engine}/combat-surface", post=False)
    player_up = _reachable(f"{qa}/debug", post=True)
    report["endpoints"] = {"engine_up": engine_up, "player_up": player_up}
    if player_up:
        report["player_build"] = _player_build_stamp(qa)
    ctx = {"engine": engine, "qa": qa, "out": out, "campaign": campaign,
           "settle": settle, "move_timeout": move_timeout}

    for a in REGISTRY:
        if only and a.id not in only:
            continue
        row = {"id": a.id, "scope": a.scope, "needs": list(a.needs), "doc": a.doc}
        needs_player = "player" in a.needs
        # policy: player-dependent assertions run ONLY under --live (the live half); engine-only
        # assertions run whenever the engine is up. An unmet NEEDED endpoint under --live is ERROR.
        if needs_player and not live:
            row.update(verdict="SKIP", detail="player probe not run (pass --live to drive the sandbox player)")
            report["assertions"].append(row)
            continue
        missing = [ep for ep in a.needs
                   if (ep == "engine" and not engine_up) or (ep == "player" and not player_up)]
        if missing:
            row.update(verdict="ERROR", detail=f"required endpoint(s) unreachable: {missing}")
            report["harness_errors"].append(f"{a.id}: endpoint(s) down: {missing}")
            report["assertions"].append(row)
            continue
        res = a.probe(ctx)
        row.update(verdict=res["verdict"], detail=res.get("detail"), record=res.get("record"))
        report["assertions"].append(row)
        report["harness_errors"].extend(f"{a.id}: {m}" for m in res.get("harness_errors", []))

    report["verdict"], _ = classify_cert_verdict(report)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true",
                    help="drive the sandbox PLAYER (:8972) — runs the player-dependent assertions "
                         "(silhouette). Without it, only engine-state assertions run (spawn/coherence).")
    ap.add_argument("--engine", default=DEFAULT_ENGINE, help="sandbox engine base (default :8866)")
    ap.add_argument("--qa", default=DEFAULT_QA, help="sandbox player QA channel base (default :8972)")
    ap.add_argument("--run", default=None,
                    help="qa_sandbox run name — read the live endpoints from its sandbox.json if present")
    ap.add_argument("--campaign", default=DEFAULT_CAMPAIGN)
    ap.add_argument("--app", default=os.environ.get("WORLDOS_PLAYER_APP", ""),
                    help="player .app path (provenance: the build being certified)")
    ap.add_argument("--out", default=str(HERE / "evidence" / "player_cert"))
    ap.add_argument("--settle", type=float, default=0.6, help="poll interval while a move resolves")
    ap.add_argument("--move-timeout", type=float, default=8.0)
    ap.add_argument("--only", nargs="*", default=None, help="run only these assertion ids")
    args = ap.parse_args(argv)

    engine, qa = args.engine, args.qa
    if args.run:
        sb = Path("/tmp/worldos-qa-sandbox") / args.run / "sandbox.json"
        if sb.is_file():
            m = json.loads(sb.read_text())
            engine, qa = m.get("engine", engine), m.get("qa", qa)

    out = Path(args.out)
    report = run_cert(engine, qa, out, campaign=args.campaign, app=args.app, live=args.live,
                      settle=args.settle, move_timeout=args.move_timeout,
                      only=set(args.only) if args.only else None)
    out.mkdir(parents=True, exist_ok=True)
    (out / "player_cert_report.json").write_text(json.dumps(report, indent=2) + "\n")

    verdict, exit_code = classify_cert_verdict(report)
    print(f"\n=== PLAYER_CERT — {verdict} ===")
    print(f"repo {report['repo_sha']} · campaign {report['campaign']} · live={report['live']} · "
          f"engine_up={report['endpoints']['engine_up']} player_up={report['endpoints']['player_up']}")
    if report.get("player_build"):
        print(f"player build stamp: {report['player_build']}")
    for a in report["assertions"]:
        print(f"  {a['verdict']:5s} {a['id']:26s} {a.get('detail') or ''}")
    if report["harness_errors"]:
        print(f"HARNESS ({len(report['harness_errors'])}) — NOT a certification verdict:"
              + "".join(f"\n    - {m}" for m in report["harness_errors"][:8]))
    print(f"report: {out / 'player_cert_report.json'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
