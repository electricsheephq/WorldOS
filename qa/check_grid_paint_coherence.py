#!/usr/bin/env python3
"""check_grid_paint_coherence.py — the ABSOLUTE grid<->paint coherence gate (#1462 / #1491).

The failure the owner walked onto (the painted sarcophagus): the ENGINE cells were legal, but the PAINT
sat ~3/4 cell off the grid footprint (least-squares rms ~76px, per-prop non-rigid drift — see the
player-alignment lane on #1491/#1462, PR #1505). The actor stood on the authored impassable cell; the
painted prop was elsewhere; the player saw a character standing INSIDE a sarcophagus.

Why check_plate_drift.py does NOT catch this: that gate is RELATIVE — it asserts a regenerated plate
keeps each prop where a KNOWN-GOOD baseline plate had it (drift across regens). A prop that has ALWAYS
been 3/4 cell off the grid passes the drift gate forever (no baseline to drift from). This gate is
ABSOLUTE: it asserts the painted prop sits on the grid's OWN impassable footprint — the cells the engine
keys pathing/collision to — with no reference plate at all.

The measurement, per authored prop (from qa/room_manifests/<room>.cells.json):
  1. The grid's structural signature is REGENERATED from the manifest geometry (props' cells + kind) via
     the SAME contract greybox rig the plate is img2img-conditioned on (greybox_render_headless — the
     #1396 recipe). This greybox IS the authored grid: each prop's box is drawn at exactly its authored
     cells. The greybox depth sidecar (greybox_sidecars_headless) is the same correspondence prior; the
     shaded greybox render is used here because its silhouette EDGES cross-register with a painterly plate
     (the brightness-robust edge criterion the alignment lane pinned: qa/evidence/1469 build_overlays.py).
  2. For each prop a structural template is the greybox silhouette EDGES over the prop's full projected
     box (floor->height), mean-subtracted + L2-normalised (the check_plate_drift fingerprint, reused so
     the two gates share one NCC definition). Edges are the modality-invariant bridge greybox<->paint.
  3. The template is localised in the PLATE's edge map over a +/- search window; the OFFSET of the peak
     normalised cross-correlation from the authored position is the paint's displacement. Converted to
     CELLS via the near-centre column pitch. Any prop whose displacement exceeds MAX_OFFSET_CELLS (0.5)
     -> INCOHERENT (fail loud). A prop whose structure cannot be located near its authored footprint at
     all (peak NCC below CONF_MIN) is also a coherence failure (the paint put nothing grid-shaped there).

SCOPE / RELIABILITY: the edge cross-correlation localises HARD-SILHOUETTE props (pillars, sarcophagi,
walls, altars — the crypt/interior class the sarcophagus incident belongs to) reliably. TALL ORGANIC
props (tree foliage, tan open canopy) present a poor match to a box silhouette, so their peak NCC is
low and their measured offset is LOW-CONFIDENCE — the `check`/`gate-recipes` CLI reports them (offset +
NCC) as a diagnostic, but the blocking CI contribution is the deterministic self-contained TEST SUITE
(qa/test_grid_paint_coherence.py: synthetic-aligned PASS + synthetic-shift CAUGHT + the current-crypt
reality anchor), not a `gate-recipes` sweep over organic-heavy live plates.

DETERMINISTIC, no LLM. Pillow + numpy (the qa image lane; shares ci.yml's paint-drift-gate venv). Read-
only: never mutates engine state, plates, seeds, or manifests.

  python3 qa/check_grid_paint_coherence.py check <plate.png> <manifest.cells.json>
  python3 qa/check_grid_paint_coherence.py check <plate.png> <manifest.cells.json> --greybox <depth.png>
  python3 qa/check_grid_paint_coherence.py gate-recipes    # gate every room with a manifest + local plate
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageFilter

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

# One camera rig, one fingerprint definition — shared with the drift gate so the two can never disagree.
from check_plate_drift import (  # noqa: E402
    FP_GRID, col_pitch_px, fingerprint, load_manifest,
)
from greybox_render_headless import (  # noqa: E402
    PX_H, PX_W, cell_to_world, render as render_greybox, world_to_screen,
    _spec_for_kind,
)

# ── Calibration constants (qa/test_grid_paint_coherence.py pins the separation these encode) ─────────
MAX_OFFSET_CELLS = 0.5   # painted prop displaced more than half a cell off its authored footprint == the
                         # sarcophagus class of defect (actor on the cell, paint elsewhere) -> fail.
SEARCH_CELL_FRAC = 1.25  # localise the prop within +/- this many CELLS of its authored box. Wide enough
                         # to MEASURE a ~3/4-cell drift (the crypt), narrow enough that a totally-absent
                         # prop can't borrow an unrelated match from across the room.
SEARCH_STEP_PX = 3       # search stride (px). Coarse is fine — the pass/fail gap (0 vs ~1 cell) is wide.
CONF_MIN = 0.20          # peak NCC below this near the authored footprint == the prop's grid-shaped
                         # structure isn't there at all -> UNLOCATED (also a coherence failure).
EDGE_THRESHOLD = 24      # FIND_EDGES binarise threshold — the exact value the #1469/#1470 registration
                         # overlays used for the greybox<->plate edge-recall metric.


# ── Projection: the prop's FULL box footprint (floor->height), the grid's structural extent ─────────
def prop_box_screen_bbox(cells: list, kind: str, cols: int, rows: int) -> list:
    """Screen [x0,y0,x1,y1] bounding box of a prop's full greybox BOX (all 8 corners, floor y=0 to
    y=height), reproducing greybox_render_headless's per-prop box (centre + padded half-extent + the
    kind's height). This is the prop's on-grid structural silhouette extent — the region a correctly
    registered plate paints the prop into. Wider than check_plate_drift's floor-only bbox on purpose:
    the whole silhouette localises far more reliably than the thin floor sliver."""
    height, half, _ = _spec_for_kind(kind)
    xs_w = [cell_to_world(c, r, cols, rows)[0] for (c, r) in cells]
    zs_w = [cell_to_world(c, r, cols, rows)[2] for (c, r) in cells]
    cx, cz = (min(xs_w) + max(xs_w)) / 2.0, (min(zs_w) + max(zs_w)) / 2.0
    half_x = max(half, (max(xs_w) - min(xs_w)) / 2.0 + half)
    half_z = max(half, (max(zs_w) - min(zs_w)) / 2.0 + half)
    hh = max(half_x, half_z)
    xs: list = []
    ys: list = []
    for (dx, dz) in ((-hh, -hh), (hh, -hh), (hh, hh), (-hh, hh)):
        for wy in (0.0, height):
            sx, sy = world_to_screen(cx + dx, wy, cz + dz)
            xs.append(sx)
            ys.append(sy)
    return [min(xs), min(ys), max(xs), max(ys)]


# ── Edge map (the modality-invariant greybox<->plate bridge; the alignment lane's criterion) ────────
def edge_luma(path_or_im, threshold: int = EDGE_THRESHOLD) -> np.ndarray:
    """PIL FIND_EDGES -> binarised float32 edge map at the contract frame. Both the greybox and the
    painterly plate are reduced to edges so cross-correlation compares SHAPE, not albedo/lighting."""
    im = path_or_im if isinstance(path_or_im, Image.Image) else Image.open(path_or_im)
    edges = im.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda p: 255 if p > threshold else 0)
    return np.asarray(edges, dtype=np.float32)


def greybox_edges_from_manifest(manifest: dict) -> np.ndarray:
    """Regenerate the authored grid's greybox render from the manifest geometry (props' cells + kind),
    at the contract rig, and return its edge map. Self-contained: no committed greybox artifact needed —
    the grid truth is the manifest, exactly as build_room_manifest.py derives the bboxes from the seeds.
    Walls are omitted (only props are gated); the floor + per-prop boxes reproduce the img2img control."""
    grid = manifest.get("grid", {})
    geo = {
        "cols": int(grid.get("cols", 0)),
        "rows": int(grid.get("rows", 0)),
        "walls": [],
        "props": [{"kind": p.get("kind", "prop"), "cells": p.get("cells", [])}
                  for p in manifest.get("props", [])],
    }
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "greybox.png"
        render_greybox(geo, str(out))
        return edge_luma(out)


# ── Localiser: the offset of the best structural match (the check_plate_drift search, returning WHERE) ─
def _locate_offset(template: np.ndarray, plate_edges: np.ndarray, bbox: list,
                   search_px: int) -> tuple:
    """Best (ncc, dx, dy) of `template` (already normalised) against the plate edge map over a
    +/-search_px window around `bbox`. Returns the peak NCC and the pixel offset at which it occurs —
    the paint's displacement from the authored footprint. Mirrors check_plate_drift._peak_ncc, but
    keeps the argmax offset instead of only the peak value."""
    if not np.any(template):
        return 0.0, 0, 0
    best, bdx, bdy = -1.0, 0, 0
    for dy in range(-search_px, search_px + 1, SEARCH_STEP_PX):
        for dx in range(-search_px, search_px + 1, SEARCH_STEP_PX):
            shifted = [bbox[0] + dx, bbox[1] + dy, bbox[2] + dx, bbox[3] + dy]
            cand = fingerprint(plate_edges, shifted)
            if np.any(cand):
                ncc = float(np.dot(template, cand))
                if ncc > best:
                    best, bdx, bdy = ncc, dx, dy
    return best, bdx, bdy


# ── Result ──────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class CoherenceResult:
    passed: bool
    room: str
    props: list = field(default_factory=list)      # per-prop {id, offset_cells, offset_px, ncc, status}
    reasons: list = field(default_factory=list)
    checked: int = 0
    skipped: int = 0

    def as_dict(self) -> dict:
        return {"passed": self.passed, "room": self.room, "max_offset_cells": MAX_OFFSET_CELLS,
                "checked": self.checked, "skipped": self.skipped,
                "props": self.props, "reasons": self.reasons}

    def summary(self) -> str:
        verdict = "COHERENT" if self.passed else "INCOHERENT"
        worst = max((p.get("offset_cells", 0.0) for p in self.props
                     if p.get("status") in ("PASS", "DRIFT")), default=0.0)
        return (f"[check_grid_paint_coherence] {self.room}: {verdict} "
                f"({self.checked} checked, worst offset {worst:.2f} cell, threshold {MAX_OFFSET_CELLS})"
                + ("" if self.passed else " — " + "; ".join(self.reasons)))


# ── The gate ──────────────────────────────────────────────────────────────────────────────────────
def check_grid_paint_coherence(plate_path: str | Path, manifest: dict, *,
                               greybox_path: Optional[str | Path] = None,
                               max_offset_cells: float = MAX_OFFSET_CELLS) -> CoherenceResult:
    """Gate one plate against its room manifest for ABSOLUTE grid<->paint coherence. Localises every
    authored prop's grid silhouette in the plate and fails if any painted prop sits more than
    `max_offset_cells` off its authored footprint (or cannot be located there at all)."""
    grid = manifest.get("grid", {})
    cols, rows = int(grid.get("cols", 0)), int(grid.get("rows", 0))
    room = str(manifest.get("room", "?"))
    plate_im = Image.open(plate_path)
    if plate_im.size != (PX_W, PX_H):
        return CoherenceResult(False, room, reasons=[
            f"plate {Path(plate_path).name} is {plate_im.size[0]}x{plate_im.size[1]}, "
            f"expected the contract {PX_W}x{PX_H} (bboxes are computed in that frame)"])

    plate_edges = edge_luma(plate_im)
    grey_edges = edge_luma(greybox_path) if greybox_path else greybox_edges_from_manifest(manifest)
    pitch = col_pitch_px(cols, rows)
    search_px = int(round(SEARCH_CELL_FRAC * pitch))
    result = CoherenceResult(True, room)

    for prop in manifest.get("props", []):
        pid = str(prop.get("id", "?"))
        cells = prop.get("cells")
        kind = prop.get("kind", "prop")
        if not (isinstance(cells, list) and cells):
            result.props.append({"id": pid, "status": "SKIP", "reason": "no cells"})
            result.skipped += 1
            continue
        bbox = prop_box_screen_bbox([tuple(c) for c in cells], kind, cols, rows)
        template = fingerprint(grey_edges, bbox)
        if not np.any(template):
            result.props.append({"id": pid, "status": "SKIP", "reason": "empty grid template"})
            result.skipped += 1
            continue
        ncc, dx, dy = _locate_offset(template, plate_edges, bbox, search_px)
        offset_px = math.hypot(dx, dy)
        offset_cells = offset_px / pitch
        result.checked += 1
        if ncc < CONF_MIN:
            status = "UNLOCATED"
            result.passed = False
            result.reasons.append(
                f"{pid} not locatable near authored cells {cells} (peak NCC {ncc:.3f} < {CONF_MIN}) "
                f"— paint put no grid-shaped structure on the footprint")
        elif offset_cells > max_offset_cells:
            status = "DRIFT"
            result.passed = False
            result.reasons.append(
                f"{pid} painted {offset_cells:.2f} cell off authored cells {cells} "
                f"(> {max_offset_cells}; {offset_px:.0f}px, NCC {ncc:.3f}) — actor sits on the cell, "
                f"paint sits elsewhere")
        else:
            status = "PASS"
        result.props.append({"id": pid, "status": status, "offset_cells": round(offset_cells, 3),
                             "offset_px": round(offset_px, 1), "ncc": round(ncc, 4),
                             "peak_offset_px": [dx, dy]})
    if result.checked == 0 and not result.reasons:
        result.reasons.append("no gate-able props in manifest")
        result.passed = False
    return result


# ── The room_recipes.json gate (mirrors check_plate_drift.gate_room_recipes) ────────────────────────
_RECIPES_PATH = _QA_DIR.parent / "extensions" / "renderers" / "shared" / "room_recipes.json"
_MANIFESTS_DIR = _QA_DIR / "room_manifests"
_PLATES_DIRS = [
    _QA_DIR / "evidence" / "plate-audit",
    _QA_DIR / "native_palette",
    _QA_DIR / "screenshot_baselines",
]


def _resolve_plate(name: str, plates_dirs: list) -> Optional[Path]:
    stem = Path(name).stem
    for d in plates_dirs:
        for cand in (d / name, d / f"{stem}.png", d / f"{stem}.jpg"):
            if cand.is_file():
                return cand
    return None


def _find_manifest_for_recipe(recipe_key: str, plate: str, manifests_dir: Path) -> Optional[Path]:
    if not manifests_dir.is_dir():
        return None
    for mp in sorted(manifests_dir.glob("*.cells.json")):
        try:
            m = load_manifest(mp)
        except (OSError, ValueError):
            continue
        if m.get("recipe_key") == recipe_key:
            return mp
    twin = manifests_dir / f"{Path(plate).stem}.cells.json"
    return twin if twin.is_file() else None


def gate_room_recipes(recipes_path: str | Path = _RECIPES_PATH,
                      manifests_dir: str | Path = _MANIFESTS_DIR,
                      plates_dirs: Optional[list] = None) -> dict:
    """Gate every room in room_recipes.json that has a canonical_plate + a committed manifest + a
    locally-available plate. Rooms whose plate lives only on the box are reported 'no-plate' and skipped
    (never a failure on absence) — the gate covers whatever is locally verifiable, same as the drift gate."""
    manifests_dir = Path(manifests_dir)
    plates_dirs = [Path(p) for p in plates_dirs] if plates_dirs else _PLATES_DIRS
    recipes = json.loads(Path(recipes_path).read_text(encoding="utf-8"))
    report = {"passed": True, "rooms": []}
    for key, room in (recipes.get("rooms") or {}).items():
        plate = room.get("canonical_plate") if isinstance(room, dict) else None
        if not plate:
            continue
        manifest_path = _find_manifest_for_recipe(key, plate, manifests_dir)
        if manifest_path is None:
            report["rooms"].append({"recipe_key": key, "status": "no-manifest", "plate": plate})
            continue
        plate_path = _resolve_plate(plate, plates_dirs)
        if plate_path is None:
            report["rooms"].append({"recipe_key": key, "status": "no-plate", "plate": plate,
                                    "manifest": manifest_path.name})
            continue
        res = check_grid_paint_coherence(plate_path, load_manifest(manifest_path))
        report["rooms"].append({"recipe_key": key, "plate": plate_path.name,
                                "manifest": manifest_path.name, **res.as_dict()})
        if not res.passed:
            report["passed"] = False
    return report


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="gate one plate against one room manifest")
    c.add_argument("plate")
    c.add_argument("manifest")
    c.add_argument("--greybox", default=None, help="explicit greybox depth/render sidecar (1344x768) to "
                                                  "use as the correspondence prior; default regenerates "
                                                  "it from the manifest geometry")
    c.add_argument("--max-offset-cells", type=float, default=MAX_OFFSET_CELLS)
    sub.add_parser("gate-recipes", help="gate every room_recipes.json plate with a manifest + local plate")
    args = ap.parse_args(argv)

    if args.cmd == "check":
        res = check_grid_paint_coherence(args.plate, load_manifest(args.manifest),
                                         greybox_path=args.greybox,
                                         max_offset_cells=args.max_offset_cells)
        print(res.summary())
        print(json.dumps(res.as_dict(), indent=2))
        return 0 if res.passed else 1

    report = gate_room_recipes()
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
