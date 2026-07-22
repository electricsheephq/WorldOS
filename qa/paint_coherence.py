#!/usr/bin/env python3
"""paint_coherence.py — the PAINT-COVERAGE COHERENCE instrument (epic #1647 item 4+5).

The owner-playtest gap (2026-07-22): every existing gate certifies GEOMETRY truth — walls/props reject
imp=Y, the camera contract is exact, prop paint sits on its footprint (check_grid_paint_coherence). Yet
the owner spawns "inside a bookcase" and walks "through objects." Reproduced live: the engine happily
walks to shop cell (1,1), which is grid-OPEN but sits under the PAINTED shelf; the crypt party spawns on
(7,7)/(8,7), grid-open cells that sit under the painted sarcophagus silhouette. The gap is systematic:
NOTHING certifies the USER'S VISUAL truth PER CELL.

This is that instrument. It is ORTHOGONAL to check_grid_paint_coherence (which asks "does a prop's paint
sit on its authored FOOTPRINT?" — per-prop drift). This asks the complementary question the drift gate
cannot: "is a WALKABLE cell visually OPEN FLOOR in the plate, or is it under painted furniture/props?"
A tall prop's painted silhouette rises up-screen under the dimetric projection and COVERS walkable cells
BEHIND its footprint — cells that are legitimately grid-open (not the prop's footprint) but that the
player SEES as inside furniture. The drift gate is blind to that by construction; this gate measures it.

METHOD (deterministic first, VQA only for the ambiguous band):
  1. Project every walkable cell's floor quad to plate pixels via the SAME contract projection the plate
     was img2img-conditioned on (greybox_render_headless.world_to_screen + cell_to_world, at the room's
     pinned cameraPin ortho). Uniform ortho projection ⇒ every floor cell is a congruent diamond.
  2. Per-cell pixel stats over an inset quad: mean RGB, luma texture variance, edge density (the
     modality-invariant FIND_EDGES map shared with check_grid_paint_coherence.edge_luma).
  3. A ROBUST FLOOR BASELINE from the MAJORITY class (floor is the dominant surface): channel-wise median
     over all walkable cells, then one trimmed re-estimation over the clearly-open cells. Anchoring to the
     token spawn would be WRONG here — the spawn is exactly what can be covered (the crypt bug), so a
     spawn-anchored baseline would learn furniture as "floor." The median-of-majority is spawn-independent.
  4. A composite coverage score per cell = color distance + edge excess + variance excess, each scaled by
     the baseline's own robust spread. score ≤ OPEN_T ⇒ open; ≥ COVERED_T ⇒ covered; between ⇒ ambiguous.
  5. VQA ADJUDICATOR (ambiguous cells only, ONE batched call per room): render a numbered-lattice overlay
     of the plate with every ambiguous cell outlined + labelled, and ask the journey_eval sonnet scorer
     (reused verbatim — same auth-isolation plumbing) which numbered cells are covered by furniture. The
     scorer is INJECTED, so the whole pipeline is unit-tested with a stub (no LLM, no box, no player).

GATE (tri-state discipline — a harness failure is an ERROR, NEVER a verdict, exit 2):
  - spawn cells + door-arrival cells MUST classify OPEN (a spawn/arrival that is covered/ambiguous is a
    HARD fail — you cannot spawn or arrive inside painted furniture). These are the owner-visible defects.
  - walkable-covered cells are LISTED as violations (report, exit 1 only under --fail-on-walkable): the
    fix decision — block the cell vs re-lock the plate — is the orchestrator's, per the epic.

Read-only: never mutates engine state, plates, seeds, geometry, or manifests. Pillow + numpy only for the
deterministic core (shares the qa image lane); the VQA lane shells out to journey_eval's scorer.

  qa/paint_coherence.py check <plate.png> <geometry.json> --ortho 9.6806 [--vqa] [-o report.json]
  qa/paint_coherence.py gate-rooms   # gate the five owner rooms from the plate registry (+ optional --vqa)
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

from greybox_render_headless import cell_to_world, world_to_screen, PX_W, PX_H  # noqa: E402
from check_grid_paint_coherence import edge_luma  # noqa: E402  (shared edge definition)

_ROOT = _QA_DIR.parent
_PLATES_MANIFEST = _ROOT / "extensions" / "renderers" / "unity" / "plates_manifest.json"
_PLATES_DIR = _ROOT / "extensions" / "renderers" / "unity" / "plates"
_GEO_DIR = _QA_DIR / "room_geometries"

# ── Calibration constants (qa/test_paint_coherence.py pins the separation these encode; the real-room
# distributions in qa/evidence/paint-coherence/ are the empirical anchor) ────────────────────────────
QUAD_INSET = 0.16       # shrink each cell quad toward its centroid before sampling, to keep grout lines /
                        # a neighbour's silhouette edge from bleeding into a cell's own floor sample.
OPEN_T = 1.00           # composite coverage score ≤ this ⇒ clearly OPEN floor.
COVERED_T = 1.85        # composite coverage score ≥ this ⇒ clearly COVERED by furniture/props.
ABS_COLOR_FLOOR = 16.0  # min color-distance scale (RGB units) — a plate with uniform floor (spread ≈ 0)
                        # must not make a 5-unit wobble read as "far from floor."
ABS_EDGE_FLOOR = 0.035  # min edge-density scale.
ABS_VAR_FLOOR = 7.0     # min luma-std scale.
# EDGE-DOMINANT by empirical calibration on the five owner plates (qa/evidence/paint-coherence/): the
# painterly plates carry a DRAMATIC warm/cool lighting gradient (brazier pools vs shadowed corners), so
# raw COLOR distance flags brightly-lit OPEN floor as "covered" — a lighting artefact, not furniture. The
# lighting-robust signal is STRUCTURE: furniture/props raise edge density + texture variance regardless of
# how the cell is lit. Edges lead; color is a light corroborator (a dark pillar shadow), never the driver.
W_COLOR, W_EDGE, W_VAR = 0.15, 0.70, 0.15
MIN_FLOOR_CELLS = 5     # fewer clean-floor cells than this ⇒ low-confidence baseline (flagged, not fatal).


# ── Room model: walkable / blocked / doors / spawns from the AUTHORED geometry (offline; no live surface)
@dataclass
class RoomModel:
    room: str
    cols: int
    rows: int
    walkable: list          # list[(c,r)]
    blocked: set            # walls ∪ non-wall_run prop footprints, minus doors
    doors: set              # door_cells (walkable exits/arrivals)
    spawns: list            # party + npc spawn cells (choose_spawns; may be [])


def derive_room(geo: dict) -> RoomModel:
    """The engine-truth walk model from a room geometry JSON (qa/room_geometries/*_geometry.json). Mirrors
    seed_gfx_town.build_grid_from_geometry: wall runs are render geometry (already in walls), so only
    non-wall_run prop footprints add to `blocked`; door cells are punched walkable. Spawns are reproduced
    from the SAME choose_spawns the seed path uses (imported, never re-implemented, so they can't drift)."""
    from seed_gfx_town import choose_spawns  # noqa: PLC0415  (top-level import is json/os only — safe)

    cols, rows = int(geo["cols"]), int(geo["rows"])
    doors = {(int(c), int(r)) for c, r in geo.get("door_cells", [])}
    wall_cells = {(int(c), int(r)) for c, r in geo.get("walls", [])} - doors
    prop_cells = {(int(c), int(r)) for p in geo.get("props", []) if p.get("kind") != "wall_run"
                  for c, r in p.get("cells", [])}
    blocked = wall_cells | prop_cells
    interior = [(c, r) for r in range(rows) for c in range(cols)]
    walkable = [cell for cell in interior if cell not in blocked or cell in doors]
    sp = choose_spawns(cols, rows, wall_cells | prop_cells, list(doors))
    spawns = [tuple(c) for c in (sp.get("party", []) + sp.get("npcs", []))]
    return RoomModel(room=str(geo.get("location", "?")), cols=cols, rows=rows,
                     walkable=walkable, blocked=blocked, doors=doors, spawns=spawns)


# ── Projection: a walkable cell's floor quad in plate pixels (the contract dimetric rig) ─────────────
def cell_quad_px(c: int, r: int, cols: int, rows: int, ortho: float, *, inset: float = QUAD_INSET) -> list:
    """The four plate-pixel corners of cell (c,r)'s floor square (2 world units on a side), projected
    through the plate's contract camera at `ortho`. `inset` shrinks the quad toward its centroid so the
    sample is the cell's own interior, not its grout-line border. Ortho projection ⇒ congruent for every
    cell, so the sample area is identical room-wide (no perspective bias)."""
    cx, _, cz = cell_to_world(c, r, cols, rows)
    h = 1.0 - inset            # half-extent in world units after inset (cell half is 1.0)
    corners = [(-h, -h), (h, -h), (h, h), (-h, h)]
    return [world_to_screen(cx + dx, 0.0, cz + dz, ortho) for dx, dz in corners]


def _quad_mask_slice(quad: list, w: int, h: int):
    """(y0, y1, x0, x1, mask) — the integer bbox of `quad` clamped to the image, and a boolean mask of the
    filled polygon within that bbox. Returns None if the quad falls entirely off-image (a degenerate
    projection). Rasterised with PIL so it needs no scipy/matplotlib."""
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    x0, x1 = max(0, int(np.floor(min(xs)))), min(w, int(np.ceil(max(xs))))
    y0, y1 = max(0, int(np.floor(min(ys)))), min(h, int(np.ceil(max(ys))))
    if x1 <= x0 or y1 <= y0:
        return None
    m = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(m).polygon([(px - x0, py - y0) for px, py in quad], fill=255)
    mask = np.asarray(m, dtype=bool)
    return (y0, y1, x0, x1, mask) if mask.any() else None


def cell_stats(rgb: np.ndarray, luma: np.ndarray, edges: np.ndarray, quad: list) -> Optional[dict]:
    """Per-cell pixel stats over the (already inset) floor quad: mean RGB, luma mean/std (texture
    variance), edge density (fraction of FIND_EDGES pixels). None if the quad projects off-image."""
    sl = _quad_mask_slice(quad, luma.shape[1], luma.shape[0])
    if sl is None:
        return None
    y0, y1, x0, x1, mask = sl
    lpx = luma[y0:y1, x0:x1][mask]
    epx = edges[y0:y1, x0:x1][mask]
    rpx = rgb[y0:y1, x0:x1][mask]
    if lpx.size == 0:
        return None
    return {"rgb": rpx.mean(axis=0).astype(float), "luma_mean": float(lpx.mean()),
            "luma_std": float(lpx.std()), "edge_density": float((epx > 0).mean()), "n_px": int(lpx.size)}


# ── Robust floor baseline (majority class; spawn-independent) ────────────────────────────────────────
def _color_dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.sqrt(np.sum((a - b) ** 2)))


def _robust_scales(base_rgb: np.ndarray, base_edge: float, base_std: float, cells_stats: list) -> dict:
    """Scale each coverage component by the baseline population's own robust spread (1.4826·MAD, floored
    by an absolute minimum) so the score is self-calibrating per plate yet can't blow up on a flat floor."""
    def mad(vals: list, center: float) -> float:
        return 1.4826 * statistics.median([abs(v - center) for v in vals]) if vals else 0.0
    cdists = [_color_dist(s["rgb"], base_rgb) for s in cells_stats]
    edges_over = [max(0.0, s["edge_density"] - base_edge) for s in cells_stats]
    stds_over = [max(0.0, s["luma_std"] - base_std) for s in cells_stats]
    return {
        "color": max(ABS_COLOR_FLOOR, mad(cdists, statistics.median(cdists)) if cdists else 0.0),
        "edge": max(ABS_EDGE_FLOOR, mad(edges_over, statistics.median(edges_over)) if edges_over else 0.0),
        "var": max(ABS_VAR_FLOOR, mad(stds_over, statistics.median(stds_over)) if stds_over else 0.0),
    }


def _median_baseline(cells_stats: list) -> dict:
    rgbs = np.vstack([s["rgb"] for s in cells_stats])
    return {"rgb": np.median(rgbs, axis=0),
            "edge": float(statistics.median(s["edge_density"] for s in cells_stats)),
            "luma_std": float(statistics.median(s["luma_std"] for s in cells_stats))}


def coverage_score(stats: dict, base_rgb: np.ndarray, base_edge: float, base_std: float,
                   scales: dict) -> tuple:
    """(score, components) — the composite 'distance from open floor'. Each component is one-sided (a cell
    DARKER/flatter/greyer than floor still scores by color distance, but excess edges/variance only add,
    never subtract — furniture adds structure, it doesn't remove it)."""
    color_z = _color_dist(stats["rgb"], base_rgb) / scales["color"]
    edge_z = max(0.0, stats["edge_density"] - base_edge) / scales["edge"]
    var_z = max(0.0, stats["luma_std"] - base_std) / scales["var"]
    score = W_COLOR * color_z + W_EDGE * edge_z + W_VAR * var_z
    return score, {"color_z": round(color_z, 3), "edge_z": round(edge_z, 3), "var_z": round(var_z, 3)}


def _verdict_for(score: float, open_t: float, covered_t: float) -> str:
    if score <= open_t:
        return "open"
    if score >= covered_t:
        return "covered"
    return "ambiguous"


# ── The deterministic pass: classify every walkable cell ────────────────────────────────────────────
@dataclass
class CellResult:
    cell: tuple
    verdict: str
    score: float
    components: dict
    role: str               # "walkable" | "spawn" | "arrival" (arrival/spawn take precedence)
    method: str = "deterministic"   # or "vqa" once adjudicated


def classify_cells(model: RoomModel, ortho: float, plate_im: Image.Image, *,
                   open_t: float = OPEN_T, covered_t: float = COVERED_T) -> tuple:
    """Deterministic per-cell classification. Returns (results_by_cell, baseline_meta). Two-pass baseline:
    a median over ALL walkable cells (majority ≈ floor), then re-estimated over the clearly-open subset so
    the reference is the CLEAN floor distribution, not floor-plus-a-few-furniture-outliers."""
    rgb = np.asarray(plate_im.convert("RGB"), dtype=np.float32)
    luma = np.asarray(plate_im.convert("L"), dtype=np.float32)
    edges = edge_luma(plate_im)

    stats_by_cell: dict = {}
    for (c, r) in model.walkable:
        s = cell_stats(rgb, luma, edges, cell_quad_px(c, r, model.cols, model.rows, ortho))
        if s is not None:
            stats_by_cell[(c, r)] = s
    if not stats_by_cell:
        raise HarnessError(f"{model.room}: no walkable cell projected on-image — bad ortho/geometry pin")
    # A hard-gate cell (spawn / door-arrival) that projects off-image would be silently dropped from the
    # measured set and could never fail the spawn/arrival check — a clipped room would read cleaner than it
    # is. Tri-state: an unmeasurable hard-gate cell is a HARNESS error, not a (missing) pass.
    hard_gate = (set(model.spawns) | model.doors) & set(model.walkable)
    off_frame = sorted(cell for cell in hard_gate if cell not in stats_by_cell)
    if off_frame:
        raise HarnessError(f"{model.room}: spawn/arrival cell(s) projected off-image "
                           f"(bad ortho/geometry pin): {off_frame}")

    all_stats = list(stats_by_cell.values())
    b = _median_baseline(all_stats)
    scales = _robust_scales(b["rgb"], b["edge"], b["luma_std"], all_stats)
    clean = [s for s in all_stats
             if coverage_score(s, b["rgb"], b["edge"], b["luma_std"], scales)[0] <= open_t]
    low_conf = len(clean) < MIN_FLOOR_CELLS
    if not low_conf:                       # refine on the clean-floor set only
        b = _median_baseline(clean)
        scales = _robust_scales(b["rgb"], b["edge"], b["luma_std"], clean)

    doors, spawns = model.doors, set(model.spawns)
    results: dict = {}
    for cell, s in stats_by_cell.items():
        score, comps = coverage_score(s, b["rgb"], b["edge"], b["luma_std"], scales)
        role = "arrival" if cell in doors else ("spawn" if cell in spawns else "walkable")
        results[cell] = CellResult(cell, _verdict_for(score, open_t, covered_t), round(score, 3),
                                   comps, role)
    meta = {"base_rgb": [round(float(v), 1) for v in b["rgb"]], "base_edge": round(b["edge"], 4),
            "base_luma_std": round(b["luma_std"], 2), "scales": {k: round(v, 4) for k, v in scales.items()},
            "clean_floor_cells": len(clean), "low_confidence": low_conf}
    return results, meta


# ── VQA adjudicator (ambiguous cells only; ONE batched call per room) ────────────────────────────────
VqaScorer = Callable[[str, list], dict]   # (image_path, questions) -> {flag: bool}  (journey_eval shape)


def build_lattice_overlay(plate_im: Image.Image, model: RoomModel, ortho: float,
                          ambiguous: list, out_path: str | Path) -> dict:
    """Render the plate with every ambiguous cell outlined + numbered, so ONE VQA call can adjudicate all
    of them. Returns {label -> cell} so the scorer's per-label answers map back to cells."""
    im = plate_im.convert("RGB").copy()
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("Arial.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    label_map: dict = {}
    for i, (c, r) in enumerate(ambiguous, start=1):
        quad = cell_quad_px(c, r, model.cols, model.rows, ortho, inset=0.0)
        dr.polygon(quad, outline=(255, 40, 200), width=3)
        cx = sum(p[0] for p in quad) / 4.0
        cy = sum(p[1] for p in quad) / 4.0
        dr.text((cx - 7, cy - 11), str(i), fill=(255, 255, 0), font=font,
                stroke_width=2, stroke_fill=(0, 0, 0))
        label_map[str(i)] = (c, r)
    im.save(out_path)
    return label_map


def _vqa_questions(label_map: dict) -> list:
    return [{"flag": f"cell_{lab}_covered",
             "text": (f"Cell number {lab} is outlined in magenta and labelled '{lab}' in the image. "
                      f"Is that cell COVERED or OCCUPIED by furniture, a prop, or any object "
                      f"(i.e. NOT clear, open walkable floor)? Answer YES if it is covered/occupied.")}
            for lab in label_map]


def default_vqa_scorer(image_path: str, questions: list, *, model: str = "sonnet",
                       timeout_s: int = 180) -> dict:
    """The production scorer: journey_eval._shell_scorer verbatim (one sonnet claude -p pass over the
    overlay, same auth-isolation as score.sh). Lazy-imported so unit tests never touch the subprocess."""
    from journey_eval import _shell_scorer  # noqa: PLC0415
    return _shell_scorer(image_path, questions, model=model, timeout_s=timeout_s)


def adjudicate_ambiguous(results: dict, plate_im: Image.Image, model: RoomModel, ortho: float,
                         scorer: VqaScorer, overlay_path: str | Path) -> int:
    """Resolve every ambiguous cell with ONE batched VQA call over a numbered overlay. Mutates `results`
    in place (verdict -> open|covered, method -> 'vqa'). Returns the number of cells adjudicated."""
    ambiguous = [cell for cell, cr in results.items() if cr.verdict == "ambiguous"]
    if not ambiguous:
        return 0
    label_map = build_lattice_overlay(plate_im, model, ortho, ambiguous, overlay_path)
    questions = _vqa_questions(label_map)
    flags = scorer(str(overlay_path), questions)          # {cell_<lab>_covered: bool}
    # The scorer MUST answer exactly the requested flags (mirrors journey_eval._shell_scorer): a missing
    # answer is a HARNESS error, never a silent default to "open" — an unadjudicated ambiguous spawn/arrival
    # cell must not read clean just because the scorer dropped its flag.
    want = {q["flag"] for q in questions}
    absent = sorted(want - set(flags))
    if absent:
        raise HarnessError(f"{model.room}: VQA scorer did not answer {absent} (got {sorted(flags)}) — "
                           f"a missing flag must never read as open")
    for lab, cell in label_map.items():
        covered = bool(flags.get(f"cell_{lab}_covered", False))
        cr = results[cell]
        cr.verdict = "covered" if covered else "open"
        cr.method = "vqa"
    return len(ambiguous)


# ── Report + gate ───────────────────────────────────────────────────────────────────────────────────
class HarnessError(RuntimeError):
    """A measurement could not be taken (bad plate/ortho/geometry, VQA scorer crash). Tri-state: this is
    an ERROR (CLI exit 2), NEVER a coherence verdict — a broken harness must not read green OR red."""


@dataclass
class CoherenceReport:
    room: str
    passed: bool
    cells: dict                         # "c,r" -> verdict
    violations: dict                    # walkable_covered / spawn_covered / arrival_covered
    method: dict
    cell_details: list = field(default_factory=list)
    reasons: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"room": self.room, "passed": self.passed, "cells": self.cells,
                "violations": self.violations, "method": self.method,
                "cell_details": self.cell_details, "reasons": self.reasons}

    def summary(self) -> str:
        v = self.violations
        return (f"[paint_coherence] {self.room}: {'COHERENT' if self.passed else 'INCOHERENT'} — "
                f"{self.method.get('walkable_cells', 0)} walkable, "
                f"{len(v['walkable_covered'])} walkable-covered, "
                f"spawn_covered={len(v['spawn_covered'])}, arrival_covered={len(v['arrival_covered'])}")


def build_report(model: RoomModel, results: dict, baseline_meta: dict, ortho: float, *,
                 used_vqa: bool, fail_on_walkable: bool = False,
                 deterministic_ambiguous: Optional[list] = None) -> CoherenceReport:
    """Assemble the per-room report + gate verdict. HARD fail: any spawn or door-arrival cell not OPEN
    (you cannot spawn/arrive inside painted furniture). Walkable-covered cells are listed; they fail the
    gate only under --fail-on-walkable (the block-cell-vs-relock-plate fix is the orchestrator's call)."""
    cells = {f"{c},{r}": cr.verdict for (c, r), cr in results.items()}
    details = [{"cell": [c, r], "verdict": cr.verdict, "score": cr.score, "role": cr.role,
                "method": cr.method, "components": cr.components}
               for (c, r), cr in sorted(results.items())]
    walkable_covered = sorted([c, r] for (c, r), cr in results.items()
                              if cr.role == "walkable" and cr.verdict == "covered")
    spawn_covered = sorted([c, r] for (c, r), cr in results.items()
                           if cr.role == "spawn" and cr.verdict != "open")
    arrival_covered = sorted([c, r] for (c, r), cr in results.items()
                             if cr.role == "arrival" and cr.verdict != "open")
    ambiguous = sorted([c, r] for (c, r), cr in results.items() if cr.verdict == "ambiguous")
    violations = {"walkable_covered": walkable_covered, "spawn_covered": spawn_covered,
                  "arrival_covered": arrival_covered}
    reasons = []
    if spawn_covered:
        reasons.append(f"{len(spawn_covered)} spawn cell(s) not open (spawn inside painted furniture): "
                       f"{spawn_covered}")
    if arrival_covered:
        reasons.append(f"{len(arrival_covered)} door-arrival cell(s) not open: {arrival_covered}")
    if fail_on_walkable and walkable_covered:
        reasons.append(f"{len(walkable_covered)} walkable cell(s) under painted furniture (strict)")
    passed = not spawn_covered and not arrival_covered and not (fail_on_walkable and walkable_covered)
    # `ambiguous_cells` is the POST-adjudication residue (empty after a VQA run, which resolves every
    # ambiguous cell). `deterministic_ambiguous_cells` preserves the PRE-VQA count — how large the
    # ambiguous band was before the LLM adjudicated it — so a VQA-gated report stays as auditable as a
    # deterministic one. Defaults to the deterministic residue when no VQA pass ran.
    det_ambiguous = deterministic_ambiguous if deterministic_ambiguous is not None else ambiguous
    method = {"deterministic": True, "vqa": used_vqa, "ortho": ortho,
              "walkable_cells": len(results), "ambiguous_cells": ambiguous,
              "deterministic_ambiguous_cells": det_ambiguous,
              "thresholds": {"open_t": OPEN_T, "covered_t": COVERED_T, "quad_inset": QUAD_INSET,
                             "weights": {"color": W_COLOR, "edge": W_EDGE, "var": W_VAR}},
              "baseline": baseline_meta}
    return CoherenceReport(model.room, passed, cells, violations, method, details, reasons)


# ── Orchestrator: one room end to end ───────────────────────────────────────────────────────────────
def run_room(plate_path: str | Path, geo: dict, ortho: float, *, vqa: bool = False,
             scorer: Optional[VqaScorer] = None, overlay_dir: Optional[Path] = None,
             fail_on_walkable: bool = False) -> CoherenceReport:
    """Classify one room's plate FILE and gate it. Thin wrapper over run_room_image that opens the plate.
    Raises HarnessError for any measurement failure (never returns a verdict on a broken harness)."""
    try:                                               # a missing/unreadable plate path is a HARNESS error,
        plate_im = Image.open(plate_path)              # not a coherence verdict — tri-state: exit 2, never 1
    except (OSError, ValueError) as exc:               # FileNotFoundError / UnidentifiedImageError / decode
        raise HarnessError(f"cannot open plate {plate_path}: {exc}") from exc
    return run_room_image(plate_im, geo, ortho, vqa=vqa, scorer=scorer,
                          overlay_dir=overlay_dir, fail_on_walkable=fail_on_walkable,
                          plate_name=Path(plate_path).name)


def run_room_image(plate_im: Image.Image, geo: dict, ortho: float, *, vqa: bool = False,
                   scorer: Optional[VqaScorer] = None, overlay_dir: Optional[Path] = None,
                   fail_on_walkable: bool = False, plate_name: str = "<image>",
                   open_t: float = OPEN_T, covered_t: float = COVERED_T) -> CoherenceReport:
    """Classify + gate one already-loaded plate image (the in-memory core; unit tests drive this directly
    with a synthetic plate). `scorer` overrides the default sonnet VQA scorer (injected in tests)."""
    if plate_im.size != (PX_W, PX_H):
        raise HarnessError(f"plate {plate_name} is {plate_im.size}, expected the contract "
                           f"{PX_W}x{PX_H} (projection is defined in that frame)")
    model = derive_room(geo)
    if not model.walkable:
        raise HarnessError(f"{model.room}: geometry has no walkable cells")
    results, meta = classify_cells(model, ortho, plate_im, open_t=open_t, covered_t=covered_t)
    # Snapshot the ambiguous band BEFORE VQA adjudication mutates every ambiguous cell to open/covered, so
    # the report can record how large the deterministic band was (lost otherwise on the VQA path).
    det_ambiguous = sorted([c, r] for (c, r), cr in results.items() if cr.verdict == "ambiguous")
    used_vqa = False
    if vqa:
        overlay = Path(overlay_dir or _QA_DIR) / f"vqa_overlay_{_slug(model.room)}.png"
        overlay.parent.mkdir(parents=True, exist_ok=True)
        try:
            n = adjudicate_ambiguous(results, plate_im, model, ortho,
                                     scorer or default_vqa_scorer, overlay)
        except Exception as exc:                       # a VQA crash is a HARNESS failure, not a verdict
            raise HarnessError(f"{model.room}: VQA adjudication failed: {exc}") from exc
        used_vqa = n > 0
    return build_report(model, results, meta, ortho, used_vqa=used_vqa,
                        fail_on_walkable=fail_on_walkable, deterministic_ambiguous=det_ambiguous)


def _slug(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")


# ── Registry helpers for gate-rooms (the five owner rooms) ──────────────────────────────────────────
# location.id (plate-registry key) -> geometry stem in qa/room_geometries/.
_OWNER_ROOMS = {
    "crypt": "crypt_v36", "tavern": "tavern_v2", "shop": "shop",
    "tavern_snug": "tavern_snug", "throne_hall": "throne_hall",
}


def _load_registry(path: str | Path = _PLATES_MANIFEST) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8")).get("plates", {})


def gate_owner_rooms(*, vqa: bool = False, scorer: Optional[VqaScorer] = None,
                     overlay_dir: Optional[Path] = None, fail_on_walkable: bool = False) -> dict:
    """Gate the five owner rooms named in epic #1647 from the plate registry + qa/room_geometries. A room
    whose plate/geometry/ortho is missing is reported (never silently skipped); a HarnessError on one room
    is captured as that room's 'error' and does not read as a pass or a fail for the batch."""
    registry = _load_registry()
    # Tri-state: `passed` tracks MEASURED coherence (a room that ran and failed the gate); `errored` tracks
    # INDETERMINATE outcomes (a missing artifact or a HarnessError) that must never read as pass OR fail —
    # `main` surfaces `errored` as exit 2, so a batch that couldn't measure a room can't go green (missing)
    # or be mistaken for an incoherent verdict (harness failure).
    report = {"passed": True, "errored": False, "rooms": []}
    for reg_key, geo_stem in _OWNER_ROOMS.items():
        entry = registry.get(reg_key)
        geo_path = _GEO_DIR / f"{geo_stem}_geometry.json"
        if not entry or not geo_path.is_file():
            report["rooms"].append({"room": reg_key, "status": "missing",
                                    "have_entry": bool(entry), "geometry": geo_path.name})
            report["errored"] = True
            continue
        plate_name = entry.get("plate")               # a registry entry may lack 'plate' — treat as missing,
        ortho = float((entry.get("cameraPin") or {}).get("ortho", 0) or 0)   # never a bare KeyError
        plate_path = _PLATES_DIR / Path(plate_name).name if plate_name else None
        if not plate_path or not plate_path.is_file() or ortho <= 0:
            report["rooms"].append({"room": reg_key, "status": "missing",
                                    "plate": str(plate_path) if plate_path else None, "ortho": ortho})
            report["errored"] = True
            continue
        try:
            res = run_room(plate_path, json.loads(geo_path.read_text()), ortho, vqa=vqa, scorer=scorer,
                           overlay_dir=overlay_dir, fail_on_walkable=fail_on_walkable)
        # Isolation contract: a malformed geometry JSON (ValueError), a present-but-corrupt plate (OSError),
        # or a malformed registry entry (KeyError) is THIS room's error — record it and keep gating the rest
        # of the batch, never let one bad room poison the other four.
        except (HarnessError, OSError, ValueError, KeyError) as exc:
            report["rooms"].append({"room": reg_key, "status": "error",
                                    "error": f"{type(exc).__name__}: {exc}"})
            report["errored"] = True
            continue
        report["rooms"].append({"registry_key": reg_key, "plate": plate_path.name, **res.as_dict()})
        if not res.passed:
            report["passed"] = False
    return report


# ── CLI (tri-state: 0 pass / 1 fail / 2 harness ERROR) ──────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="classify + gate one plate against one room geometry")
    c.add_argument("plate")
    c.add_argument("geometry")
    c.add_argument("--ortho", type=float, required=True, help="the room's pinned cameraPin ortho")
    c.add_argument("--vqa", action="store_true", help="adjudicate ambiguous cells with the sonnet VQA scorer")
    c.add_argument("--fail-on-walkable", action="store_true",
                   help="also fail the gate on walkable-covered cells (default: report only)")
    c.add_argument("-o", "--out", default=None, help="write the coherence_report.json here")
    c.add_argument("--overlay-dir", default=None)
    g = sub.add_parser("gate-rooms", help="gate the five owner rooms from the plate registry")
    g.add_argument("--vqa", action="store_true")
    g.add_argument("--fail-on-walkable", action="store_true")
    g.add_argument("-o", "--out", default=None)
    g.add_argument("--overlay-dir", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "check":
        try:
            res = run_room(args.plate, json.loads(Path(args.geometry).read_text()), args.ortho,
                           vqa=args.vqa, overlay_dir=Path(args.overlay_dir) if args.overlay_dir else None,
                           fail_on_walkable=args.fail_on_walkable)
        except HarnessError as exc:
            print(f"[paint_coherence] ERROR: {exc}", file=sys.stderr)
            return 2
        print(res.summary())
        payload = json.dumps(res.as_dict(), indent=2)
        if args.out:
            Path(args.out).write_text(payload, encoding="utf-8")
        else:
            print(payload)
        return 0 if res.passed else 1

    report = gate_owner_rooms(vqa=args.vqa, fail_on_walkable=args.fail_on_walkable,
                              overlay_dir=Path(args.overlay_dir) if args.overlay_dir else None)
    payload = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    if report.get("errored"):                          # missing artifact / HarnessError → INDETERMINATE
        print("[paint_coherence] ERROR: harness failure or missing owner room(s) in gate-rooms — "
              "batch is indeterminate, not a coherence verdict", file=sys.stderr)
        return 2
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
