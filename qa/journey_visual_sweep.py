#!/usr/bin/env python3
"""journey_visual_sweep.py — the VISUAL JOURNEY instrument (#1540, milestone M-ALIGN).

The one-layer-up sibling of qa/journey_click_sweep.py (#1537). #1537 drives the engine's OWN move
validation surface and reports ENGINE truth ("did the move get accepted/rejected as authored"). It
reported 133 targets / 0 defects clean the SAME night the owner walked a real player through painted
tavern tables and a drifted crypt sarcophagus — because engine-truth is BLIND to PAINT truth (the same
recall lesson as qa/evidence/journey-eval-first-run/RECALL.md's honest 0/4 table, one layer up: an
instrument only catches the defect class it deliberately probes). A room whose engine cells are all
legal can still SHOW the hero standing inside a painted coffin, or offer a walkable floor cell the
plate painted a bench onto, or dump the party across the room from the door they must use to return.

This instrument drives the walkslice 3-room world (crypt hub <-> camp, crypt <-> tavern; the same seed
qa/seed_gfx_walkslice.py), and at every room + every door round-trip it VERIFIES PAINT, deterministically:

  1. HERO-POSITION check — project the hero's engine cell to screen via the SAME contract camera the
     client renders against (greybox_render_headless.cell_to_world/world_to_screen, orthoSize=13 /
     pitch=30 / yaw=45, 1344x768, verified <1e-3 vs Unity's Quaternion.Euler; the plates are registered
     to it) and assert the projected feet sit inside the hero cell's floor quad AND that the hero is not
     standing on a cell the inverse-coherence pass flagged as a painted object (the sarcophagus/woodpile
     "actor inside the coffin" incident, qa/check_grid_paint_coherence.py's motivating defect).

  2. RECIPROCAL-DOOR check — after each A->B cross, the hero must arrive within Chebyshev-2 of B's door
     cell whose connection maps back to A. Today the engine drops the party at B's rest spawn, which is
     across the room from the return door => this FAILS on every crossing. That is #1541's red test; this
     instrument REPORTS it as a finding, it does NOT fix the engine.

  3. INVERSE-COHERENCE check — a painted-object detector on the registered plate. Edge-density
     (PIL FIND_EDGES thresholded at 24, the qa/plate_overlays.py registration threshold) is sampled over
     each authored-WALKABLE, non-prop cell's projected floor quad; a robust per-room baseline (median +
     MAD of that room's floor cells) is calibrated so the known-clean camp floor cells never flag, and any
     walkable cell whose painted edge density is a strong outlier above it => "painted object on an
     authored-walkable cell" FLAG. This is the pass that catches the tavern's invented benches / furniture
     the manifest never authored.

  4. A per-step composite FRAME (the registered plate + the hero marker at its projected feet + the
     floor quad + every flagged cell, checks annotated) -> a human gallery.html, plus a machine
     report.json and a per-room CLEAN% table (CLEAN% = passing (steps + inverse-coherence cells) / total —
     the owner's 95% bar).

CAPTURE PATH (flagged deviation, not hidden): #1540 asks to drive the packaged player and screenshot via
the #1466 QA `/click` channel. ship-morning (qa/evidence/ship-morning/SMOKE_RESULT.md) captured that way
by BUILDING and running WorldOSPlayer.app with WORLDOS_QA_INPUT=1 — a native macOS window. journey_click_
sweep.py already documented why a scratch run must NOT drive a second native player: macOS window lookup
by owner name (CGWindowList / findWindow "WorldOSPlayer") cannot disambiguate two WorldOSPlayer windows,
so a scratch capture risks grabbing the OWNER'S live :8766 window. #1540 explicitly permits "the headless
capture path if the packaged player can't screenshot": this instrument therefore composites the frame
HEADLESSLY from the SAME registered plate the player renders as its backdrop, plus the hero drawn at the
contract-camera projection of its engine cell. What this exercises is EXACTLY the paint-vs-grid contract
(is the authored-walkable floor actually painted as clear floor; does the hero's cell project onto clear
floor; does the arrival land near the return door) — the three defect classes #1540 names — with zero
risk to the owner's live session. The one thing a headless composite CANNOT independently verify is
whether the shipped CLIENT's own projection matches this contract camera; that is a separate client-
render check (wire the #1466 QA channel's screen-position report when a scratch player build can be
driven safely) and is called out per-step in the report as `hero_render_source: "contract-projection"`.

Engine = SOLE WRITER: the driver only POSTs the same rest-mode /move intents a real click makes (via
qa/journey_click_sweep.py's proven helpers) against a SCRATCH viewer on a non-8766 port; it never writes
campaign state and never touches the owner's :8766 / /tmp/walkslice_owner / the LaunchAgents.

  python3 qa/journey_visual_sweep.py run --state-dir <scratch> --rundir qa/evidence/1540
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

# Reuse #1537's proven core + IO helpers verbatim (this instrument EXTENDS journey_click_sweep, per #1540).
from journey_click_sweep import (  # noqa: E402
    OWNER_LIVE_PORT, _SEED_SCRIPT, _VIEWER, _ROOT,
    manifest_from_surface, _get, _post_move, _rest_token, _free_port, _wait_ready,
)
# The contract camera + edge threshold — ONE definition shared with the greybox/coherence lane so this
# instrument can never disagree with the rig the plates are registered to.
from greybox_render_headless import PX_W, PX_H, cell_to_world, world_to_screen  # noqa: E402
from plate_overlays import edge_mask  # noqa: E402  (shared FIND_EDGES->binary primitive)


# ── Plate resolution: engine location.id -> the registered 1344x768 plate on disk ───────────────────
_PLATES_MANIFEST = _ROOT / "extensions" / "renderers" / "unity" / "plates_manifest.json"
# Dirs the committed plate artifacts live in (the box carries plates/*.png; the repo carries the same
# painted plates under qa/evidence/*). Searched in order; first hit wins.
_PLATE_SEARCH_DIRS = [
    _QA_DIR / "evidence" / "plate-audit",
    _QA_DIR / "evidence" / "tavern-fit2",  # the adopted density-law tavern plate (tavern_fit2_v1.png)
    _QA_DIR / "evidence" / "new-tavern",
    _QA_DIR / "evidence" / "crypt-replicate" / "refs",
    _QA_DIR / "native_palette",
    _QA_DIR / "screenshot_baselines",
    _ROOT / "plates",
    _ROOT / "extensions" / "renderers" / "unity" / "plates",
]
# The canonical crypt plate (plates/crypt_armb_iter3_v1.png) ships only on the GEX44 box; the adopted
# incumbent reference committed in-repo is the SAME painted plate at the contract 1344x768 (the
# crypt-replicate incumbent). Alias so a repo-only run resolves it.
_PLATE_ALIASES = {"crypt_armb_iter3_v1": "incumbent_crypt_armb_iter3"}


def load_plate_registry(manifest_path: Path = _PLATES_MANIFEST) -> dict:
    """Map engine ``location.id`` -> plate basename (no dir), straight off the runtime plate registry the
    client keys on (``plates_manifest.json``; keys ARE ``surface.location.id``)."""
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    out: dict = {}
    for loc_id, entry in (data.get("plates") or {}).items():
        plate = entry.get("plate") if isinstance(entry, dict) else None
        if plate:
            out[loc_id] = Path(plate).name
    return out


def resolve_plate(loc_id: str, registry: dict,
                  search_dirs: Optional[list] = None) -> Optional[Path]:
    """Locate the registered plate PNG/JPG for a room on disk. Returns None (never raises) when the plate
    lives only on the box — the caller reports that room `no-plate` and skips inverse-coherence for it,
    exactly as qa/check_grid_paint_coherence.gate_room_recipes handles a box-only plate (absence is never
    a failure)."""
    basename = registry.get(loc_id)
    if not basename:
        return None
    stem = Path(basename).stem
    stems = [stem]
    if stem in _PLATE_ALIASES:
        stems.append(_PLATE_ALIASES[stem])
    dirs = [Path(d) for d in search_dirs] if search_dirs else _PLATE_SEARCH_DIRS
    for d in dirs:
        for s in stems:
            for ext in (".png", ".jpg", ".jpeg"):
                cand = d / f"{s}{ext}"
                if cand.is_file():
                    return cand
    return None


# Inverse-coherence uses a HARD edge threshold, deliberately much higher than qa/plate_overlays.EDGE_THR
# (24). 24 is calibrated for greybox<->plate REGISTRATION recall (it must catch every soft painterly
# edge so a firelit plate still registers); on a dense painterly plate it floods — the whole plank/stone
# floor reads as edges and furniture cannot separate (empirically: at 24 the tavern's furniture cells sit
# at robust-z ~1.2, indistinguishable from textured floor; qa/evidence/1540 first-run notes). 128 keeps
# only HARD, high-contrast silhouette edges (an object's outline / a cast-shadow boundary), dropping the
# soft floor grain, at which the tavern furniture separates to z 3-5 and camp clean floor stays at z<1.5.
IC_EDGE_THR = 128


def load_plate_edges(plate_path: str | Path, thr: int = IC_EDGE_THR) -> Image.Image:
    """Open a plate, force it to the contract 1344x768 frame (all quads are computed there), and return
    its binary HARD-edge mask (shared qa/plate_overlays.edge_mask, FIND_EDGES @ `thr`)."""
    im = Image.open(plate_path).convert("RGB")
    if im.size != (PX_W, PX_H):
        im = im.resize((PX_W, PX_H))
    return edge_mask(im, thr)


# ── PURE geometry: the contract camera projected to floor quads (unit-tested, no engine/HTTP) ───────
def feet_screen(c: int, r: int, cols: int, rows: int) -> tuple:
    """Screen (sx, sy) of the hero's FEET for engine cell (c, r): the cell centre at floor y=0, through
    the contract camera. This is where a correctly-registered client draws the actor's feet."""
    wx, wy, wz = cell_to_world(c, r, cols, rows)
    return world_to_screen(wx, wy, wz)


def cell_floor_quad(c: int, r: int, cols: int, rows: int) -> list:
    """The 4 screen corners of cell (c, r)'s floor quad (its 2.0-world-unit square at y=0), in winding
    order. A cell centre is at cell_to_world(c,r); the square spans +/-1.0 world unit in x and z (the
    isotropic 2.0 cell). Convex under the iso projection."""
    cx, _, cz = cell_to_world(c, r, cols, rows)
    corners_world = [(cx - 1.0, 0.0, cz - 1.0), (cx + 1.0, 0.0, cz - 1.0),
                     (cx + 1.0, 0.0, cz + 1.0), (cx - 1.0, 0.0, cz + 1.0)]
    return [world_to_screen(*w) for w in corners_world]


def cell_silhouette_quad(c: int, r: int, cols: int, rows: int,
                         up0: float = 0.3, up1: float = 2.2) -> list:
    """The screen quad of a cell's STANDING-SILHOUETTE band — its footprint square lifted between `up0`
    and `up1` world units of height. Under the iso projection an object standing ON a cell paints its
    body ABOVE that cell's floor quad (up-and-back); sampling this band (not just the y=0 floor quad,
    which on a dense painterly plate is dominated by plank/stone grain) is where invented FURNITURE
    actually shows as hard edges. Convex; corners in winding order."""
    cx, _, cz = cell_to_world(c, r, cols, rows)
    corners_world = [(cx - 1.0, up0, cz - 1.0), (cx + 1.0, up0, cz - 1.0),
                     (cx + 1.0, up1, cz + 1.0), (cx - 1.0, up1, cz + 1.0)]
    return [world_to_screen(*w) for w in corners_world]


def point_in_quad(pt: tuple, quad: list) -> bool:
    """Is `pt` inside the convex `quad` (4 screen points, winding order)? Same-sign cross-product test —
    on any edge counts as inside (a feet point on the cell boundary is still on that cell's floor)."""
    px, py = pt
    sign = 0
    n = len(quad)
    for i in range(n):
        ax, ay = quad[i]
        bx, by = quad[(i + 1) % n]
        cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        if cross == 0:
            continue
        s = 1 if cross > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


def _quad_bbox(quad: list) -> tuple:
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return (min(xs), min(ys), max(xs), max(ys))


def cell_edge_density(edge_img: Image.Image, quad: list) -> float:
    """Fraction of pixels inside a cell's floor quad that are structural EDGES on the plate. Clean painted
    floor -> low (a flat texture); a painted object (bench/coffin/crate) sitting on the cell -> high (its
    silhouette + interior detail). Deterministic: rasterise the quad to a mask in its own screen bbox,
    AND with the plate's edge mask, count. Returns 0.0 for a fully off-frame / degenerate quad."""
    x0, y0, x1, y1 = _quad_bbox(quad)
    ix0, iy0 = max(0, int(x0)), max(0, int(y0))
    ix1, iy1 = min(PX_W, int(x1) + 1), min(PX_H, int(y1) + 1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    w, h = ix1 - ix0, iy1 - iy0
    local_quad = [(px - ix0, py - iy0) for (px, py) in quad]
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(local_quad, fill=255)
    edges_crop = edge_img.crop((ix0, iy0, ix1, iy1))
    md, ed = mask.getdata(), edges_crop.getdata()
    inside = hit = 0
    for m, e in zip(md, ed):
        if m:
            inside += 1
            if e:
                hit += 1
    return hit / inside if inside else 0.0


# ── Check 3: inverse-coherence (the painted-object detector) ────────────────────────────────────────
# A cell is FLAGGED when its hard-edge silhouette density is a robust OUTLIER above the room's own floor
# baseline: density > median + Z_CUT * 1.4826 * MAD (a MAD-scaled robust z-score; 1.4826 makes MAD a
# std estimate), AND above ABS_FLOOR (so a near-edge-free room can't flag on noise). Z_CUT=3.0 and the
# hard IC_EDGE_THR were co-calibrated on the CAMP known-clean floor cells (they must stay unflagged: their
# robust-z sits < 1.5) against the tavern's visible invented furniture (which separates to z 3-5) — see
# qa/evidence/1540 first-run notes. The baseline is PER-ROOM (each plate's floor texture sets its own
# median), the only cross-texture-safe choice: a single global camp-density threshold applied to the
# busier tavern plate would flood it — so "calibrate on camp clean cells" is honored as calibrating the
# Z_CUT/threshold the camp clean floor must sit below, not a global density level.
Z_CUT = 3.0
ABS_FLOOR = 0.02
# Camp cells that are unambiguously open painted floor (mid clearing, off the fire / firewood / crates /
# shelter / ruin footprints) — the #1540 calibration anchor: these must NOT flag.
CAMP_CLEAN_CELLS = [(4, 3), (5, 3), (6, 3), (4, 4), (5, 4), (6, 4), (5, 5), (6, 5)]


@dataclass
class InverseCoherenceResult:
    room: str
    baseline_median: float
    mad: float
    threshold: float
    n_cells: int
    flagged: list = field(default_factory=list)          # [{cell, density, z, ratio}]
    densities: dict = field(default_factory=dict)         # "c,r" -> density (for the frame overlay)

    def as_dict(self) -> dict:
        return {"room": self.room, "edge_thr": IC_EDGE_THR, "z_cut": Z_CUT,
                "baseline_median": round(self.baseline_median, 4), "mad": round(self.mad, 4),
                "threshold": round(self.threshold, 4), "n_cells": self.n_cells, "flagged": self.flagged}


def _robust_z(v: float, med: float, mad: float) -> float:
    return (v - med) / (1.4826 * mad) if mad > 0 else 0.0


def inverse_coherence_flags(edge_img: Image.Image, walkable: list, prop_cells: set,
                            cols: int, rows: int, room: str = "?", *,
                            z_cut: float = Z_CUT, abs_floor: float = ABS_FLOOR) -> InverseCoherenceResult:
    """Flag every authored-WALKABLE, non-prop cell whose hard-edge STANDING-SILHOUETTE density is a robust
    outlier above the room's own floor baseline (robust-z >= z_cut AND density >= abs_floor) — i.e. the
    plate painted an object where the grid authored clear floor (invented furniture; the tavern-benches /
    the actor-inside-the-coffin class). `edge_img` is the plate's HARD-edge mask (load_plate_edges @
    IC_EDGE_THR). Self-calibrating per room; the camp clean cells only pin that z_cut is high enough."""
    cells = [(int(c), int(r)) for (c, r) in walkable if (int(c), int(r)) not in prop_cells]
    densities: dict = {}
    for (c, r) in cells:
        densities[(c, r)] = cell_edge_density(edge_img, cell_silhouette_quad(c, r, cols, rows))
    vals = list(densities.values())
    if not vals:
        return InverseCoherenceResult(room, 0.0, 0.0, abs_floor, 0)
    med = statistics.median(vals)
    mad = statistics.median([abs(v - med) for v in vals]) or 0.0
    threshold = max(med + z_cut * 1.4826 * mad, abs_floor)
    flagged = []
    for (c, r), d in sorted(densities.items(), key=lambda kv: -kv[1]):
        if d > threshold and d >= abs_floor:
            flagged.append({"cell": [c, r], "density": round(d, 4),
                            "z": round(_robust_z(d, med, mad), 2),
                            "ratio": round(d / med, 2) if med > 0 else None})
    return InverseCoherenceResult(
        room=room, baseline_median=med, mad=mad, threshold=threshold, n_cells=len(vals),
        flagged=flagged, densities={f"{c},{r}": d for (c, r), d in densities.items()})


# ── Check 2: reciprocal-door landing ────────────────────────────────────────────────────────────────
def chebyshev(a: tuple, b: tuple) -> int:
    return max(abs(int(a[0]) - int(b[0])), abs(int(a[1]) - int(b[1])))


def reciprocal_door_check(arrival_cell: Optional[tuple], dest_doors: list, origin_loc_id: str,
                          *, max_cheb: int = 2) -> dict:
    """After crossing origin -> dest, the party must arrive within `max_cheb` (Chebyshev) of the dest
    door whose connection maps back to origin — otherwise the player is dumped across the room from the
    only door home. Fails (a) if dest has no door back to origin at all, or (b) if the nearest such door
    is > max_cheb from the arrival cell. PURE (dest_doors is the dest surface's own _combat_doors)."""
    back = [d for d in (dest_doors or []) if str(d.get("to") or "") == str(origin_loc_id)]
    if arrival_cell is None:
        return {"pass": False, "reason": "no arrival cell (hero token missing after cross)",
                "reciprocal_doors": [d.get("cell") for d in back]}
    if not back:
        return {"pass": False, "reason": f"dest has NO door back to origin {origin_loc_id!r} — "
                "one-way room (reciprocal door missing)", "arrival": list(arrival_cell),
                "reciprocal_doors": []}
    dists = [(chebyshev(arrival_cell, tuple(d["cell"])), d) for d in back if d.get("cell")]
    best_cheb, best_door = min(dists, key=lambda t: t[0])
    passed = best_cheb <= max_cheb
    return {"pass": passed, "arrival": list(arrival_cell), "max_cheb": max_cheb,
            "nearest_door": best_door.get("cell"), "cheb": best_cheb,
            "reason": (None if passed else
                       f"arrived {best_cheb} cells (Chebyshev) from the return door "
                       f"{best_door.get('cell')} (> {max_cheb}) — dumped across the room from the way back")}


# ── Check 1: hero position ──────────────────────────────────────────────────────────────────────────
def hero_feet_check(hero_cell: Optional[tuple], cols: int, rows: int, flagged_cells: set) -> dict:
    """Feet-in-quad (the contract camera projects the cell centre inside the cell's OWN floor quad — a
    registration-regression guard on the projection basis) AND not-on-a-flagged-cell (the hero must not
    stand on a cell the inverse-coherence pass flagged as a painted object: the 'actor inside the coffin'
    incident). PURE."""
    if hero_cell is None:
        return {"pass": False, "reason": "no hero token on the surface (party not projected)"}
    c, r = int(hero_cell[0]), int(hero_cell[1])
    quad = cell_floor_quad(c, r, cols, rows)
    feet = feet_screen(c, r, cols, rows)
    in_quad = point_in_quad(feet, quad)
    on_flagged = (c, r) in flagged_cells
    passed = in_quad and not on_flagged
    reason = None
    if not in_quad:
        reason = f"projected feet {tuple(round(v,1) for v in feet)} fall OUTSIDE cell {[c,r]}'s floor quad"
    elif on_flagged:
        reason = (f"hero stands on cell {[c,r]} the plate painted an object onto "
                  "(inverse-coherence flag) — actor-inside-the-object")
    return {"pass": passed, "cell": [c, r], "feet": [round(feet[0], 1), round(feet[1], 1)],
            "feet_in_quad": in_quad, "on_flagged_cell": on_flagged, "reason": reason}


# ── The per-step composite frame (headless capture) ─────────────────────────────────────────────────
def _draw_quad(draw, quad, outline, width=2, fill=None):
    draw.polygon(quad, outline=outline, fill=fill, width=width)


def composite_frame(plate_path: Optional[str | Path], hero_cell: Optional[tuple], cols: int, rows: int,
                    flagged_cells: list, doors: list, out_path: Path, *,
                    label: str = "", checks: Optional[list] = None,
                    reciprocal_target: Optional[list] = None) -> None:
    """Render the human evidence frame: the registered plate (or a grey placeholder if box-only) + the
    hero marker at its projected feet + the hero cell's floor quad (green pass / red on-flagged) + every
    inverse-coherence flagged cell (orange) + door cells (blue) + the reciprocal return door (cyan) +
    a text annotation of the checks. This is what the gallery shows per step."""
    if plate_path and Path(plate_path).is_file():
        base = Image.open(plate_path).convert("RGB")
        if base.size != (PX_W, PX_H):
            base = base.resize((PX_W, PX_H))
    else:
        base = Image.new("RGB", (PX_W, PX_H), (30, 30, 36))
    im = base.copy()
    draw = ImageDraw.Draw(im, "RGBA")

    for d in doors or []:
        cell = d.get("cell") if isinstance(d, dict) else d
        if cell:
            _draw_quad(draw, cell_floor_quad(int(cell[0]), int(cell[1]), cols, rows),
                       (70, 140, 255, 255), width=2)
    if reciprocal_target:
        _draw_quad(draw, cell_floor_quad(int(reciprocal_target[0]), int(reciprocal_target[1]), cols, rows),
                   (0, 220, 220, 255), width=3)
    for cell in flagged_cells or []:
        _draw_quad(draw, cell_floor_quad(int(cell[0]), int(cell[1]), cols, rows),
                   (255, 140, 0, 255), width=2, fill=(255, 140, 0, 55))

    if hero_cell is not None:
        c, r = int(hero_cell[0]), int(hero_cell[1])
        on_flagged = [c, r] in [list(x) for x in (flagged_cells or [])]
        col = (255, 40, 40, 255) if on_flagged else (40, 230, 90, 255)
        _draw_quad(draw, cell_floor_quad(c, r, cols, rows), col, width=3)
        fx, fy = feet_screen(c, r, cols, rows)
        draw.ellipse([fx - 9, fy - 9, fx + 9, fy + 9], outline=col, width=3)
        draw.line([fx - 13, fy, fx + 13, fy], fill=col, width=2)
        draw.line([fx, fy - 13, fx, fy + 13], fill=col, width=2)

    lines = [label] + (checks or [])
    y = 10
    for ln in lines:
        if not ln:
            continue
        draw.rectangle([8, y - 2, 8 + 7 * len(ln) + 8, y + 16], fill=(0, 0, 0, 170))
        draw.text((12, y), ln, fill=(240, 240, 240, 255))
        y += 20

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)


# ── The journey driver: every room + every door round-trip, a frame + checks per step ───────────────
def _hero_cell(surface: dict, hero_id: str) -> Optional[tuple]:
    tok = _rest_token(surface, hero_id)
    return (int(tok["x"]), int(tok["y"])) if tok else None


def run_journey(get_surface: Callable[[], dict], cross_door: Callable[[dict], dict], hero_id: str,
                registry: dict, rundir: Path, *, plate_dirs: Optional[list] = None,
                max_cheb: int = 2) -> dict:
    """Drive the whole door graph from wherever `get_surface()` starts (the crypt hub): check the start
    room, then for every door cross into the neighbour (reciprocal-door + hero + inverse-coherence),
    capture a frame per step, and cross back. Star topology (crypt hub, camp/tavern leaves), so a single
    hub-and-spoke pass visits every room and rides every door BOTH ways. Callables injected — the
    traversal is unit-tested with stubs; here they hit the live scratch viewer.

    Returns the full report dict (rooms + transitions + findings + CLEAN%), also written to report.json."""
    frames_dir = rundir / "frames"
    rooms: dict = {}           # loc_id -> per-room record (inverse-coherence + spawn hero check)
    steps: list = []           # ordered per-step records (each has a frame)
    transitions: list = []     # per-crossing reciprocal-door records
    step_no = 0

    def _plate_for(loc_id: str) -> Optional[Path]:
        return resolve_plate(loc_id, registry, plate_dirs)

    def _grid(surface: dict) -> tuple:
        g = surface.get("grid") or {}
        return int(g.get("cols", 0)), int(g.get("rows", 0))

    def _visit_room(surface: dict) -> dict:
        """Idempotent per-room inverse-coherence + spawn hero-position (runs once per distinct room)."""
        loc_id = str((surface.get("location") or {}).get("id") or "")
        if loc_id in rooms:
            return rooms[loc_id]
        cols, rows = _grid(surface)
        manifest = manifest_from_surface(surface)
        walkable = manifest.get("walkable", [])
        prop_cells = {(int(c), int(r)) for p in manifest.get("props", []) for (c, r) in p.get("footprint", [])}
        plate = _plate_for(loc_id)
        if plate is not None:
            ic = inverse_coherence_flags(load_plate_edges(plate), walkable, prop_cells, cols, rows, loc_id)
            ic_dict = ic.as_dict()
            flagged_cells = [f["cell"] for f in ic.flagged]
            plate_status = "resolved"
        else:
            ic_dict = {"room": loc_id, "status": "no-plate",
                       "reason": "plate ships box-only; inverse-coherence skipped (absence != failure)"}
            flagged_cells = []
            plate_status = "no-plate"
        # camp calibration receipt: the designated known-clean cells must NOT be among the flags.
        calib = None
        if loc_id == "camp_clearing_night" and plate is not None:
            clean_set = {tuple(x) for x in CAMP_CLEAN_CELLS}
            clean_flagged = [c for c in flagged_cells if tuple(c) in clean_set]
            calib = {"clean_cells": [list(c) for c in CAMP_CLEAN_CELLS],
                     "clean_cells_flagged": [list(c) for c in clean_flagged],
                     "ok": not clean_flagged}
        rec = {"room": loc_id, "cols": cols, "rows": rows, "plate": str(plate) if plate else None,
               "plate_status": plate_status, "inverse_coherence": ic_dict,
               "flagged_cells": flagged_cells, "n_walkable_floor": len(
                   [1 for (c, r) in walkable if (int(c), int(r)) not in prop_cells]),
               "calibration": calib, "hero_checks": []}
        rooms[loc_id] = rec
        return rec

    def _capture_step(kind: str, surface: dict, *, label: str, reciprocal: Optional[dict] = None) -> dict:
        nonlocal step_no
        loc_id = str((surface.get("location") or {}).get("id") or "")
        cols, rows = _grid(surface)
        rec = rooms.get(loc_id) or _visit_room(surface)
        flagged = rec.get("flagged_cells", [])
        hero = _hero_cell(surface, hero_id)
        hero_chk = hero_feet_check(hero, cols, rows, {tuple(c) for c in flagged})
        checks_txt = [f"room={loc_id}  hero_cell={hero_chk.get('cell')}  "
                      f"hero_pos={'PASS' if hero_chk['pass'] else 'FAIL'}"]
        if reciprocal is not None:
            checks_txt.append(f"reciprocal-door={'PASS' if reciprocal['pass'] else 'FAIL'} "
                              f"({reciprocal.get('reason') or 'within Chebyshev-'+str(max_cheb)})")
        if rec.get("plate_status") == "resolved":
            checks_txt.append(f"inverse-coherence flags={len(flagged)} "
                              f"(walkable floor cells={rec['n_walkable_floor']})")
        recip_target = reciprocal.get("nearest_door") if reciprocal else None
        frame_name = f"step{step_no:02d}_{kind}_{loc_id}.png"
        composite_frame(rec.get("plate"), hero, cols, rows, flagged,
                        surface.get("doors") or [], frames_dir / frame_name,
                        label=f"[{step_no}] {label}", checks=checks_txt, reciprocal_target=recip_target)
        step = {"step": step_no, "kind": kind, "room": loc_id, "label": label, "frame": f"frames/{frame_name}",
                "hero_check": hero_chk, "reciprocal": reciprocal}
        steps.append(step)
        if kind in ("spawn", "arrive"):
            rec["hero_checks"].append({"step": step_no, **hero_chk})
        step_no += 1
        return step

    # 1) The hub, at spawn.
    start = get_surface()
    hub_id = str((start.get("location") or {}).get("id") or "")
    _visit_room(start)
    _capture_step("spawn", start, label=f"spawn in {hub_id}")

    # 2) Ride every door out of the hub and back (star topology => visits every room, both ways).
    hub_doors = list(start.get("doors") or [])
    for door in hub_doors:
        dest_id = str(door.get("to") or "")
        outcome = cross_door(door)
        if not outcome.get("ok"):
            transitions.append({"from": hub_id, "to": dest_id, "crossed": False,
                                "reason": outcome.get("reason"), "reciprocal": None})
            continue
        dest_surface = get_surface()
        arrival = _hero_cell(dest_surface, hero_id)
        recip = reciprocal_door_check(arrival, dest_surface.get("doors") or [], hub_id, max_cheb=max_cheb)
        transitions.append({"from": hub_id, "to": dest_id, "crossed": True, "arrival":
                            list(arrival) if arrival else None, "reciprocal": recip})
        _visit_room(dest_surface)
        _capture_step("arrive", dest_surface, label=f"crossed {hub_id} -> {dest_id}", reciprocal=recip)

        # cross back to the hub (its own reciprocal check: the leaf's door home vs the hub arrival).
        back_door = next((d for d in (dest_surface.get("doors") or []) if str(d.get("to") or "") == hub_id), None)
        if back_door is None:
            transitions.append({"from": dest_id, "to": hub_id, "crossed": False,
                                "reason": "leaf has no door back to hub", "reciprocal": None})
            continue
        back_outcome = cross_door(back_door)
        if not back_outcome.get("ok"):
            transitions.append({"from": dest_id, "to": hub_id, "crossed": False,
                                "reason": back_outcome.get("reason"), "reciprocal": None})
            continue
        hub_surface = get_surface()
        hub_arrival = _hero_cell(hub_surface, hero_id)
        # reciprocal on the way back: the hub's door that maps back to the leaf we came from.
        back_recip = reciprocal_door_check(hub_arrival, hub_surface.get("doors") or [], dest_id, max_cheb=max_cheb)
        transitions.append({"from": dest_id, "to": hub_id, "crossed": True,
                            "arrival": list(hub_arrival) if hub_arrival else None, "reciprocal": back_recip})
        _capture_step("return", hub_surface, label=f"returned {dest_id} -> {hub_id}", reciprocal=back_recip)

    return build_report(list(rooms.values()), steps, transitions, hub_id)


# ── Report + CLEAN% ─────────────────────────────────────────────────────────────────────────────────
def build_report(room_recs: list, steps: list, transitions: list, hub_id: str) -> dict:
    """Aggregate rooms + steps + transitions into report.json with a per-room CLEAN% table + finding
    counts by class. CLEAN% for a room = (passing hero-position steps in it + non-flagged walkable floor
    cells) / (its hero-position steps + its walkable floor cells) — the owner's 95% bar folds the two
    paint-truth surfaces (does the actor stand right; is the floor clear) into one number."""
    per_room = []
    n_furniture = n_hero = n_recip = 0
    for rec in room_recs:
        loc = rec["room"]
        hero_checks = rec.get("hero_checks", [])
        step_ids = [s for s in steps if s["room"] == loc and s["kind"] in ("spawn", "arrive")]
        hero_pass = sum(1 for s in step_ids if s["hero_check"].get("pass"))
        hero_total = len(step_ids)
        n_hero += hero_total - hero_pass
        n_floor = rec.get("n_walkable_floor", 0)
        n_flag = len(rec.get("flagged_cells", []))
        n_furniture += n_flag
        clean_cells = n_floor - n_flag
        num = hero_pass + clean_cells
        den = hero_total + n_floor
        clean_pct = round(100.0 * num / den, 1) if den else None
        per_room.append({"room": loc, "plate_status": rec.get("plate_status"),
                         "hero_steps": hero_total, "hero_pass": hero_pass,
                         "walkable_floor_cells": n_floor, "inverse_coherence_flags": n_flag,
                         "clean_pct": clean_pct, "meets_95": (clean_pct is not None and clean_pct >= 95.0)})
    for t in transitions:
        recip = t.get("reciprocal")
        if t.get("crossed") and recip is not None and not recip.get("pass"):
            n_recip += 1
    tot_num = sum((r["hero_pass"] + (r["walkable_floor_cells"] - r["inverse_coherence_flags"]))
                  for r in per_room if r["clean_pct"] is not None)
    tot_den = sum((r["hero_steps"] + r["walkable_floor_cells"])
                  for r in per_room if r["clean_pct"] is not None)
    overall = round(100.0 * tot_num / tot_den, 1) if tot_den else None
    findings = {"invented_furniture_flags": n_furniture, "reciprocal_door_failures": n_recip,
                "hero_position_failures": n_hero}
    return {"hub": hub_id, "overall_clean_pct": overall, "per_room": per_room,
            "findings_by_class": findings, "rooms": room_recs, "transitions": transitions, "steps": steps}


# ── gallery.html ────────────────────────────────────────────────────────────────────────────────────
def write_gallery(report: dict, out_path: Path) -> None:
    """A standalone gallery: the per-room CLEAN% table + finding counts, then one card per step (its
    composite frame + the checks that fired). References the frame PNGs by relative path."""
    rows_html = "\n".join(
        f"<tr class='{'bad' if not r['meets_95'] and r['clean_pct'] is not None else ''}'>"
        f"<td>{r['room']}</td><td>{r['plate_status']}</td>"
        f"<td>{r['hero_pass']}/{r['hero_steps']}</td>"
        f"<td>{r['inverse_coherence_flags']} / {r['walkable_floor_cells']}</td>"
        f"<td><b>{'' if r['clean_pct'] is None else str(r['clean_pct'])+'%'}</b></td></tr>"
        for r in report["per_room"])
    f = report["findings_by_class"]
    cards = []
    for s in report["steps"]:
        hc = s["hero_check"]
        recip = s.get("reciprocal")
        badges = [f"<span class='{'ok' if hc.get('pass') else 'fail'}'>hero-pos {'PASS' if hc.get('pass') else 'FAIL'}</span>"]
        if recip is not None:
            badges.append(f"<span class='{'ok' if recip.get('pass') else 'fail'}'>reciprocal-door "
                          f"{'PASS' if recip.get('pass') else 'FAIL'}</span>")
        reasons = [x for x in [hc.get("reason"), (recip or {}).get("reason")] if x]
        cards.append(
            f"<div class='card'><h3>[{s['step']}] {s['label']} <small>({s['kind']})</small></h3>"
            f"<div class='badges'>{''.join(badges)}</div>"
            f"<img src='{s['frame']}' loading='lazy'/>"
            + ("".join(f"<p class='reason'>{r}</p>" for r in reasons)) + "</div>")
    html = f"""<!doctype html><meta charset=utf-8><title>VISUAL JOURNEY — #1540</title>
<style>
 body{{font:14px/1.5 -apple-system,sans-serif;margin:24px;background:#111;color:#eee}}
 h1{{margin:0 0 4px}} .sub{{color:#9aa;margin:0 0 18px}}
 table{{border-collapse:collapse;margin:12px 0 24px}} td,th{{border:1px solid #333;padding:6px 12px;text-align:left}}
 th{{background:#1c1c22}} tr.bad td{{background:#3a1414}}
 .kpis span{{display:inline-block;background:#1c1c22;border:1px solid #333;border-radius:6px;padding:6px 12px;margin-right:8px}}
 .cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(560px,1fr));gap:20px}}
 .card{{background:#1a1a20;border:1px solid #2a2a33;border-radius:10px;padding:12px}}
 .card img{{width:100%;border-radius:6px;border:1px solid #333}}
 .badges span{{display:inline-block;padding:2px 8px;border-radius:4px;margin:0 6px 8px 0;font-weight:600}}
 .ok{{background:#123d1e;color:#7fe0a0}} .fail{{background:#4a1414;color:#ff9a9a}}
 .reason{{color:#ffb0b0;font-size:13px;margin:4px 0 0}}
 h3{{margin:0 0 8px;font-size:15px}} small{{color:#8a8}}
</style>
<h1>VISUAL JOURNEY sweep — walkslice 3-room world</h1>
<p class=sub>#1540 · M-ALIGN · overall CLEAN% <b>{'' if report['overall_clean_pct'] is None else str(report['overall_clean_pct'])+'%'}</b>
 · hub <code>{report['hub']}</code></p>
<div class=kpis>
 <span>invented-furniture flags: <b>{f['invented_furniture_flags']}</b></span>
 <span>reciprocal-door failures: <b>{f['reciprocal_door_failures']}</b></span>
 <span>hero-position failures: <b>{f['hero_position_failures']}</b></span>
</div>
<table><tr><th>room</th><th>plate</th><th>hero-pos pass</th><th>furniture flags / floor cells</th><th>CLEAN%</th></tr>
{rows_html}</table>
<div class=cards>
{''.join(cards)}
</div>
"""
    out_path.write_text(html, encoding="utf-8")


# ── impure entry: seed + boot scratch viewer + journey + teardown ───────────────────────────────────
def run_live(state_dir: Path, rundir: Path, *, port: Optional[int] = None,
             seed_script: Optional[Path] = None, plate_dirs: Optional[list] = None) -> dict:
    """Seed the walkslice world into `state_dir`, boot a SCRATCH viewer on a non-8766 port, drive the
    visual journey, write report.json + gallery.html into `rundir`, tear the viewer down (kills EXACTLY
    the one subprocess it starts, by PID — never the owner's live :8766). Mirrors
    journey_click_sweep.run_live's boot/seed/teardown contract."""
    rundir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    seed_script = seed_script or _SEED_SCRIPT

    seed_env = {**os.environ, "WORLDOS_STATE_DIR": str(state_dir)}
    seed_proc = subprocess.run(
        ["uv", "run", "--directory", str(_ROOT / "servers" / "engine"), "python",
         str(seed_script), str(state_dir)],
        cwd=str(_ROOT), env=seed_env, capture_output=True, text=True)
    (rundir / "seed.log").write_text((seed_proc.stdout or "") + (seed_proc.stderr or ""), encoding="utf-8")
    if seed_proc.returncode != 0:
        raise RuntimeError(f"seed failed (rc={seed_proc.returncode}); see {rundir}/seed.log")
    seed_lines = [ln for ln in seed_proc.stdout.strip().splitlines() if ln.strip()]
    if not seed_lines:
        raise RuntimeError(f"seed produced no output; see {rundir}/seed.log")
    seed_out = json.loads(seed_lines[-1])
    cid, hero_id = seed_out["campaign_id"], seed_out["hero_id"]

    port = port or _free_port()
    if port == OWNER_LIVE_PORT:
        raise RuntimeError(f"refusing port {OWNER_LIVE_PORT} — the owner's live server")
    base = f"http://127.0.0.1:{port}"
    registry = load_plate_registry()

    viewer_env = {**os.environ, "WORLDOS_STATE_DIR": str(state_dir),
                  "WORLDOS_PLAYER_MOVES": str(state_dir / "player_moves.jsonl")}
    viewer_log = open(rundir / "viewer.log", "wb")
    viewer = subprocess.Popen([sys.executable, str(_VIEWER), cid, str(port)],
                              cwd=str(_ROOT), env=viewer_env, stdout=viewer_log, stderr=subprocess.STDOUT)
    try:
        _wait_ready(base, cid)

        def _get_surface() -> dict:
            return _get(base, f"/combat-surface?campaign={cid}")

        def _cross(door: dict) -> dict:
            cell = door.get("cell") or [0, 0]
            c, r = int(cell[0]), int(cell[1])
            _post_move(base, {"kind": "walk_to_cell", "character_id": hero_id, "x": c, "y": r})
            out = _post_move(base, {"kind": "cross_door", "x": c, "y": r})
            return {"ok": bool(out.get("ok")), "reason": out.get("reason")}

        report = run_journey(_get_surface, _cross, hero_id, registry, rundir, plate_dirs=plate_dirs)
    finally:
        viewer.terminate()
        try:
            viewer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            viewer.kill()
            viewer.wait(timeout=5)
        viewer_log.close()

    (rundir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_gallery(report, rundir / "gallery.html")
    return report


def _print_summary(report: dict) -> None:
    f = report["findings_by_class"]
    print(f"[journey_visual_sweep] overall CLEAN% = {report['overall_clean_pct']}  hub={report['hub']}")
    for r in report["per_room"]:
        print(f"  ROOM {r['room']}: CLEAN {r['clean_pct']}%  hero {r['hero_pass']}/{r['hero_steps']}  "
              f"furniture-flags {r['inverse_coherence_flags']}/{r['walkable_floor_cells']}  "
              f"[{r['plate_status']}]")
    print(f"  FINDINGS: invented-furniture={f['invented_furniture_flags']}  "
          f"reciprocal-door-failures={f['reciprocal_door_failures']}  "
          f"hero-position-failures={f['hero_position_failures']}")
    for t in report["transitions"]:
        recip = t.get("reciprocal")
        if t.get("crossed") and recip is not None and not recip.get("pass"):
            print(f"  RECIPROCAL-FAIL {t['from']} -> {t['to']}: {recip.get('reason')}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rn = sub.add_parser("run", help="[scratch] seed + boot a scratch viewer + visual journey + teardown")
    rn.add_argument("--state-dir", required=True)
    rn.add_argument("--rundir", required=True)
    rn.add_argument("--port", type=int, default=None)
    rn.add_argument("--seed-script", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        report = run_live(Path(args.state_dir), Path(args.rundir), port=args.port,
                          seed_script=Path(args.seed_script) if args.seed_script else None)
        _print_summary(report)
        # exit non-zero when the owner's 95% bar is missed OR any reciprocal-door failure — this is a
        # RED-FIRST instrument: on today's world it is EXPECTED to exit 1 (that proves the checks fire).
        ok = (report["overall_clean_pct"] is not None and report["overall_clean_pct"] >= 95.0
              and report["findings_by_class"]["reciprocal_door_failures"] == 0)
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
