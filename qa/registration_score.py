#!/usr/bin/env python3
"""registration_score.py — the REGISTRATION instrument (#1680): per-room agreement between the PAINTED
room and its COLLISION set, the measuring stick for the project's #1 demo blocker (navigation-truth).

The owner playtest gap (2026-07-22): the party walks THROUGH painted tables and slams into INVISIBLE
WALLS — the painted furniture/walls in the plate and the engine's collision/walkmask disagree, room by
room. check_grid_paint_coherence asks "does a prop's paint sit on its footprint?"; paint_coherence asks
"is a walkable cell visually open floor?". This asks the SHIP question that subsumes both into one number:
over the room's projected area, what FRACTION of cells agree between what the player SEES as blocking and
what the engine actually BLOCKS? — and it names the two failure modes cell-by-cell:

  * invisible wall   = painted-OPEN but collision-BLOCKS  (you see floor, you can't walk there)
  * walk-through     = painted-BLOCKED but walkable        (you see a table, you walk through it)

METHOD (deterministic; reuses the calibrated coherence machinery, no LLM/box/player):
  1. COLLISION truth, projected through the contract camera (Euler(30,45,0), pos=-fwd*80, the room's
     pinned ortho — greybox_render_headless.world_to_screen + cell_to_world are the canonical math):
       (a) the walkmask — walls ∪ non-wall_run prop footprints, minus door cells (paint_coherence.
           derive_room, which mirrors seed_gfx_town.build_grid_from_geometry), and
       (b) the boxes-sidecar proxy footprints — every non-floor box volume in
           extensions/renderers/unity/boxes/<room>_boxes.json, its x/z extent mapped to the cells it
           overlaps (the EXACT conditioning volumes the plate was img2img'd on).
     collision_blocking(cell) = (a) ∪ (b) − doors.
  2. PAINTED-object mask from the plate PNG — reuse paint_coherence's per-cell coverage machinery
     (cell_quad_px + cell_stats + the robust floor baseline + coverage_score) at PER-CELL grain (the
     documented floor; sub-cell is a tracked refinement). painted_blocking(cell) = coverage score ≥
     BLOCK_T, i.e. the cell reads as a blocking object (furniture/wall) rather than open floor — the
     SAME "covered" definition paint_coherence gates on (BLOCK_T defaults to its COVERED_T).
  3. SCORE — agreement % over every cell that projects on-frame, plus the two disagreement lists.
  4. HEATMAP — the disagreement tinted on the plate (invisible walls red, walk-through cyan) + a JSON
     report {room, agreement_pct, invisible_wall_cells, walkthrough_cells, per_cell}.
  5. SHIP BAR — --bar 0.99 (owner). `score-rooms` gates every plates_manifest room and exits nonzero
     listing the rooms below bar (the honest baseline EXPECTS most/all rooms to fail — that is the
     finding). A room whose plate/geometry/boxes cannot be measured is an ERROR (exit 2), never a pass.

Read-only: never mutates engine state, plates, seeds, geometry, boxes, or manifests. Pillow + numpy only.

  qa/registration_score.py check <plate.png> <geometry.json> --ortho 11.7851 [--boxes b.json] [-o r.json]
  qa/registration_score.py score-rooms [--bar 0.99] [--evidence-dir qa/evidence/registration]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

import paint_coherence as P  # noqa: E402  (derive_room, cell_quad_px, cell_stats, coverage_score, ...)
from greybox_render_headless import cell_to_world, PX_W, PX_H  # noqa: E402
from check_grid_paint_coherence import edge_luma  # noqa: E402  (shared edge definition)

_ROOT = _QA_DIR.parent
_PLATES_MANIFEST = _ROOT / "extensions" / "renderers" / "unity" / "plates_manifest.json"
_PLATES_DIR = _ROOT / "extensions" / "renderers" / "unity" / "plates"
_BOXES_DIR = _ROOT / "extensions" / "renderers" / "unity" / "boxes"
_GEO_DIR = _QA_DIR / "room_geometries"
_EVIDENCE_DIR = _QA_DIR / "evidence" / "registration"

# A cell reads as PAINTED-BLOCKING when its coverage score ≥ this — the SAME "covered" cutoff
# paint_coherence gates on (its COVERED_T), so "painted-blocking" ≡ "paint_coherence would call this cell
# covered by furniture/wall". Exposed as a CLI knob but pinned to the calibrated default by construction.
BLOCK_T = P.COVERED_T

# plates_manifest location.id -> geometry stem in qa/room_geometries/<stem>_geometry.json. The manifest
# does not name the geometry (it names the plate + boxes); this is the explicit bridge (the geometry
# `location` fields are prose like "General Goods Shop", not the registry key, so they can't auto-map).
_GEO_STEM = {
    "crypt": "crypt_v36",
    "camp_clearing_night": "camp_clearing",
    "camp_clearing": "camp_clearing",
    "tavern": "tavern_v2",
    "throne_hall": "throne_hall",
    "shop": "shop",
    "tavern_snug": "tavern_snug",
    "dwing_room_1": "dwing_room_1",
}


class HarnessError(RuntimeError):
    """A measurement could not be taken (bad plate/ortho/geometry/boxes). Tri-state: this is an ERROR
    (CLI exit 2), NEVER a registration verdict — a broken harness must not read green OR red."""


# ── Collision truth: walkmask + boxes-sidecar footprints, projected to cells ─────────────────────────
def box_footprint_cells(boxes: list, cols: int, rows: int) -> set:
    """The set of cells any NON-floor box volume overlaps on the floor plane. A box has a world-space
    `center` [x,y,z] and `size` [sx,sy,sz]; its floor footprint is the x/z rectangle center±size/2. A cell
    (c,r) occupies the 2×2 world square centred at cell_to_world(c,r), so it is covered when that square
    overlaps the box rectangle. Floor/grout boxes (kind starting 'floor') are the ground plane, not
    collision, and are excluded."""
    covered: set = set()
    for b in boxes:
        kind = str(b.get("kind") or "").lower()
        if kind.startswith("floor"):
            continue
        center = b.get("center") or []
        size = b.get("size") or []
        if len(center) < 3 or len(size) < 3:
            continue
        cxw, _, czw = float(center[0]), float(center[1]), float(center[2])
        sx, _, sz = float(size[0]), float(size[1]), float(size[2])
        x0, x1 = cxw - sx / 2.0, cxw + sx / 2.0
        z0, z1 = czw - sz / 2.0, czw + sz / 2.0
        for r in range(rows):
            wz = cell_to_world(0, r, cols, rows)[2]     # z depends only on r
            if wz + 1.0 <= z0 or wz - 1.0 >= z1:        # cell z-span [wz-1, wz+1] disjoint from box z
                continue
            for c in range(cols):
                wx = cell_to_world(c, r, cols, rows)[0]  # x depends only on c
                if wx + 1.0 <= x0 or wx - 1.0 >= x1:
                    continue
                covered.add((c, r))
    return covered


def collision_cells(model: "P.RoomModel", box_cells: set) -> set:
    """The collision-blocking cell set = geometry walkmask-blocked (walls ∪ prop footprints) ∪ box
    footprints, minus door cells (a door is a walkable passage, never collision)."""
    return (set(model.blocked) | set(box_cells)) - set(model.doors)


# ── Painted-object mask: paint_coherence's per-cell coverage machinery over EVERY interior cell ───────
def _floor_baseline(model: "P.RoomModel", ortho: float, rgb, luma, edges) -> tuple:
    """The robust floor reference from the walkable-cell majority — the SAME two-pass estimator
    paint_coherence.classify_cells uses (median over all walkable cells, then re-estimated over the
    clearly-open subset). Returns (base_rgb, base_edge, base_std, scales). Raises HarnessError if no
    walkable cell projects on-frame (a bad ortho/geometry pin, never a silent clean read)."""
    walk_stats = []
    for (c, r) in model.walkable:
        s = P.cell_stats(rgb, luma, edges, P.cell_quad_px(c, r, model.cols, model.rows, ortho))
        if s is not None:
            walk_stats.append(s)
    if not walk_stats:
        raise HarnessError(f"{model.room}: no walkable cell projects on-image — bad ortho/geometry pin")
    b = P._median_baseline(walk_stats)
    scales = P._robust_scales(b["rgb"], b["edge"], b["luma_std"], walk_stats)
    clean = [s for s in walk_stats
             if P.coverage_score(s, b["rgb"], b["edge"], b["luma_std"], scales)[0] <= P.OPEN_T]
    if len(clean) >= P.MIN_FLOOR_CELLS:                  # refine on the clean-floor set only
        b = P._median_baseline(clean)
        scales = P._robust_scales(b["rgb"], b["edge"], b["luma_std"], clean)
    return b["rgb"], b["edge"], b["luma_std"], scales, len(clean)


def paint_scores(model: "P.RoomModel", ortho: float, plate_im: Image.Image) -> tuple:
    """Per-cell coverage score for EVERY cell that projects on-frame (walls included — a painted wall
    legitimately reads as blocking). Returns (scores_by_cell, baseline_meta). Score ≥ BLOCK_T ⇒
    painted-blocking. Cells that project off-frame are omitted (they cannot be scored)."""
    rgb = np.asarray(plate_im.convert("RGB"), dtype=np.float32)
    luma = np.asarray(plate_im.convert("L"), dtype=np.float32)
    edges = edge_luma(plate_im)
    base_rgb, base_edge, base_std, scales, n_clean = _floor_baseline(model, ortho, rgb, luma, edges)
    scores: dict = {}
    for r in range(model.rows):
        for c in range(model.cols):
            s = P.cell_stats(rgb, luma, edges, P.cell_quad_px(c, r, model.cols, model.rows, ortho))
            if s is None:
                continue
            score, _ = P.coverage_score(s, base_rgb, base_edge, base_std, scales)
            scores[(c, r)] = round(float(score), 3)
    meta = {"base_rgb": [round(float(v), 1) for v in base_rgb], "base_edge": round(base_edge, 4),
            "base_luma_std": round(base_std, 2), "clean_floor_cells": n_clean,
            "scales": {k: round(v, 4) for k, v in scales.items()}}
    return scores, meta


# ── Score: agreement + the two disagreement lists ────────────────────────────────────────────────────
@dataclass
class RegistrationReport:
    room: str
    agreement_pct: float
    scored_cells: int
    invisible_wall_cells: list          # painted-open-but-collides
    walkthrough_cells: list             # painted-blocked-but-walkable
    per_cell: list
    passed: bool
    bar: float
    box_cells: int
    baseline: dict = field(default_factory=dict)
    plate: str = ""
    ortho: float = 0.0
    block_threshold: float = BLOCK_T

    def as_dict(self) -> dict:
        return {
            "room": self.room, "plate": self.plate, "ortho": self.ortho,
            "bar": self.bar, "block_threshold": self.block_threshold,
            "agreement_pct": self.agreement_pct, "passed": self.passed,
            "scored_cells": self.scored_cells, "box_cells": self.box_cells,
            "invisible_wall_cells": self.invisible_wall_cells,
            "walkthrough_cells": self.walkthrough_cells,
            "counts": {"invisible_wall": len(self.invisible_wall_cells),
                       "walkthrough": len(self.walkthrough_cells)},
            "baseline": self.baseline, "per_cell": self.per_cell,
        }

    def summary(self) -> str:
        return (f"[registration] {self.room}: agreement {self.agreement_pct:.2f}% "
                f"({'PASS' if self.passed else 'FAIL'} @ bar {self.bar * 100:.0f}%) — "
                f"{len(self.invisible_wall_cells)} invisible-wall, "
                f"{len(self.walkthrough_cells)} walk-through / {self.scored_cells} cells")


def score_registration(model: "P.RoomModel", collision: set, paint_scores_by_cell: dict,
                       *, bar: float = 0.99, block_t: float = BLOCK_T) -> RegistrationReport:
    """The pure agreement math (unit-tested with an injected paint-score map). Over every scored cell
    (one that projects on-frame): collision_blocking vs painted_blocking(score ≥ block_t). Disagreements
    split into invisible walls (collides, painted open) and walk-through (painted blocked, walkable)."""
    invisible, walkthrough, per_cell = [], [], []
    for (c, r) in sorted(paint_scores_by_cell):
        score = paint_scores_by_cell[(c, r)]
        coll = (c, r) in collision
        painted = score >= block_t
        if coll and not painted:
            klass = "invisible_wall"
            invisible.append([c, r])
        elif painted and not coll:
            klass = "walkthrough"
            walkthrough.append([c, r])
        else:
            klass = "agree"
        per_cell.append({"cell": [c, r], "collision": coll, "painted_blocking": painted,
                         "score": score, "class": klass})
    scored = len(per_cell)
    disagree = len(invisible) + len(walkthrough)
    agreement_pct = round(100.0 * (scored - disagree) / scored, 3) if scored else 0.0
    passed = scored > 0 and (agreement_pct / 100.0) >= bar
    return RegistrationReport(room=model.room, agreement_pct=agreement_pct, scored_cells=scored,
                              invisible_wall_cells=invisible, walkthrough_cells=walkthrough,
                              per_cell=per_cell, passed=passed, bar=bar,
                              box_cells=0, block_threshold=block_t)


# ── Heatmap overlay: disagreement tinted on the plate ────────────────────────────────────────────────
_INVISIBLE_TINT = (235, 40, 40)     # red   — painted open, collision blocks (invisible wall)
_WALKTHROUGH_TINT = (40, 210, 235)  # cyan  — painted blocked, walkable      (walk-through)


def render_heatmap(plate_im: Image.Image, model: "P.RoomModel", ortho: float,
                   report: RegistrationReport, out_path: str | Path) -> None:
    """Tint each disagreement cell's floor quad on a copy of the plate (invisible walls red, walk-through
    cyan) with a legend, so the mismatch is eyeball-auditable against the painting."""
    base = plate_im.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(overlay)
    for role, cells, tint in (("inv", report.invisible_wall_cells, _INVISIBLE_TINT),
                              ("walk", report.walkthrough_cells, _WALKTHROUGH_TINT)):
        for (c, r) in cells:
            quad = P.cell_quad_px(c, r, model.cols, model.rows, ortho, inset=0.0)
            dr.polygon(quad, fill=(*tint, 110), outline=(*tint, 235))
    out = Image.alpha_composite(base, overlay).convert("RGB")
    dr2 = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("Arial.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    lines = [f"{report.room}  agreement {report.agreement_pct:.1f}%  (bar {report.bar * 100:.0f}%)",
             f"RED invisible-wall (painted open, collides): {len(report.invisible_wall_cells)}",
             f"CYAN walk-through (painted blocked, walkable): {len(report.walkthrough_cells)}"]
    for i, ln in enumerate(lines):
        dr2.text((14, 12 + i * 24), ln, fill=(255, 255, 0), font=font,
                 stroke_width=2, stroke_fill=(0, 0, 0))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path)


# ── Orchestrator: one room end to end ────────────────────────────────────────────────────────────────
def run_room(plate_path: str | Path, geo: dict, ortho: float, *, boxes: Optional[list] = None,
             bar: float = 0.99, block_t: float = BLOCK_T,
             heatmap_path: Optional[str | Path] = None) -> RegistrationReport:
    """Score one room's plate FILE against its collision truth. Raises HarnessError for any measurement
    failure (bad plate/ortho/geometry/boxes) — never returns a verdict on a broken harness."""
    try:
        plate_im = Image.open(plate_path)
        plate_im.load()
    except (OSError, ValueError) as exc:
        raise HarnessError(f"cannot open plate {plate_path}: {exc}") from exc
    if plate_im.size != (PX_W, PX_H):
        raise HarnessError(f"plate {Path(plate_path).name} is {plate_im.size}, expected the contract "
                           f"{PX_W}x{PX_H} (the projection is defined in that frame)")
    model = P.derive_room(geo)
    if not model.walkable:
        raise HarnessError(f"{model.room}: geometry has no walkable cells")
    box_cells = box_footprint_cells(boxes or [], model.cols, model.rows)
    collision = collision_cells(model, box_cells)
    scores, base_meta = paint_scores(model, ortho, plate_im)
    report = score_registration(model, collision, scores, bar=bar, block_t=block_t)
    report.box_cells = len(box_cells)
    report.baseline = base_meta
    report.plate = Path(plate_path).name
    report.ortho = ortho
    if heatmap_path is not None:
        render_heatmap(plate_im, model, ortho, report, heatmap_path)
    return report


# ── Registry helpers for score-rooms (every plates_manifest room) ────────────────────────────────────
def _load_registry(path: str | Path = _PLATES_MANIFEST) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8")).get("plates", {})


def _resolve_geometry_stem(reg_key: str, entry: dict) -> Optional[str]:
    """The geometry stem for a manifest key: the explicit bridge first, then a best-effort fallback from
    the boxes filename (strip a trailing _v<N>) or the key itself, so a NEW room dropped in the manifest
    is still scoreable if its geometry follows the stem convention."""
    if reg_key in _GEO_STEM:
        return _GEO_STEM[reg_key]
    import re  # noqa: PLC0415
    candidates = []
    boxes = entry.get("boxes")
    if boxes:
        stem = Path(boxes).stem[:-len("_boxes")] if Path(boxes).stem.endswith("_boxes") \
            else Path(boxes).stem
        candidates += [stem, re.sub(r"_v\d+$", "", stem)]
    candidates.append(reg_key)
    for stem in candidates:
        if (_GEO_DIR / f"{stem}_geometry.json").is_file():
            return stem
    return None


def score_manifest_rooms(*, bar: float = 0.99, block_t: float = BLOCK_T,
                         evidence_dir: Optional[Path] = None) -> dict:
    """Score EVERY plates_manifest room. A room whose plate/geometry/ortho/boxes cannot be measured is
    recorded as an error (never a silent skip or a false pass). Writes a <room>_registration.json report
    and a <room>_heatmap.png per room into `evidence_dir` when given."""
    registry = _load_registry()
    out_dir = Path(evidence_dir) if evidence_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    report = {"bar": bar, "block_threshold": block_t, "passed": True, "errored": False, "rooms": []}
    for reg_key, entry in registry.items():
        stem = _resolve_geometry_stem(reg_key, entry)
        geo_path = (_GEO_DIR / f"{stem}_geometry.json") if stem else None
        plate_name = entry.get("plate")
        plate_path = _PLATES_DIR / Path(plate_name).name if plate_name else None
        ortho = float((entry.get("cameraPin") or {}).get("ortho", 0) or 0)
        if not geo_path or not geo_path.is_file() or not plate_path or not plate_path.is_file() \
                or ortho <= 0:
            report["rooms"].append({"room": reg_key, "status": "missing",
                                    "geometry": geo_path.name if geo_path else None,
                                    "plate": plate_path.name if plate_path else None, "ortho": ortho})
            report["errored"] = True
            continue
        boxes_rel = entry.get("boxes")
        boxes_list = None
        if boxes_rel:
            boxes_path = _BOXES_DIR / Path(boxes_rel).name
            if boxes_path.is_file():
                boxes_list = json.loads(boxes_path.read_text(encoding="utf-8")).get("boxes", [])
        try:
            heatmap = (out_dir / f"{reg_key}_heatmap.png") if out_dir else None
            res = run_room(plate_path, json.loads(geo_path.read_text()), ortho, boxes=boxes_list,
                           bar=bar, block_t=block_t, heatmap_path=heatmap)
        except (HarnessError, OSError, ValueError, KeyError) as exc:
            report["rooms"].append({"room": reg_key, "status": "error",
                                    "error": f"{type(exc).__name__}: {exc}"})
            report["errored"] = True
            continue
        row = {"registry_key": reg_key, "boxes": bool(boxes_list), **res.as_dict()}
        report["rooms"].append(row)
        if out_dir:
            (out_dir / f"{reg_key}_registration.json").write_text(json.dumps(res.as_dict(), indent=2),
                                                                  encoding="utf-8")
        if not res.passed:
            report["passed"] = False
    return report


def _summary_table(report: dict) -> str:
    """A compact room -> agreement% table for the console + PR body (the honest baseline)."""
    rows = ["room                 agreement   inv-wall  walk-thru  boxes  verdict",
            "-------------------- ---------  --------  ---------  -----  -------"]
    for r in report["rooms"]:
        key = r.get("registry_key", r.get("room", "?"))
        if r.get("status") in ("missing", "error"):
            rows.append(f"{key:<20} {'--':>8}   {'':>8}  {'':>9}  {'':>5}  {r.get('status').upper()}")
            continue
        rows.append(f"{key:<20} {r['agreement_pct']:>7.2f}%  "
                    f"{r['counts']['invisible_wall']:>8}  {r['counts']['walkthrough']:>9}  "
                    f"{'yes' if r.get('boxes') else 'no':>5}  {'PASS' if r['passed'] else 'FAIL'}")
    return "\n".join(rows)


# ── CLI (tri-state: 0 pass / 1 below-bar / 2 harness ERROR) ──────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="score one plate against one room geometry (+ optional boxes)")
    c.add_argument("plate")
    c.add_argument("geometry")
    c.add_argument("--ortho", type=float, required=True, help="the room's pinned cameraPin ortho")
    c.add_argument("--boxes", default=None, help="boxes-sidecar JSON (the collision proxy volumes)")
    c.add_argument("--bar", type=float, default=0.99, help="ship bar as a fraction (default 0.99)")
    c.add_argument("--block-threshold", type=float, default=BLOCK_T,
                   help=f"coverage score ≥ this ⇒ painted-blocking (default {BLOCK_T}, paint_coherence's COVERED_T)")
    c.add_argument("-o", "--out", default=None, help="write the registration report JSON here")
    c.add_argument("--heatmap", default=None, help="write the disagreement heatmap PNG here")

    g = sub.add_parser("score-rooms", help="score EVERY plates_manifest room (the honest baseline)")
    g.add_argument("--bar", type=float, default=0.99)
    g.add_argument("--block-threshold", type=float, default=BLOCK_T)
    g.add_argument("--evidence-dir", default=str(_EVIDENCE_DIR),
                   help="write per-room reports + heatmaps here (default qa/evidence/registration)")
    g.add_argument("-o", "--out", default=None, help="write the batch summary JSON here")
    args = ap.parse_args(argv)

    if args.cmd == "check":
        try:
            geo = json.loads(Path(args.geometry).read_text())
            boxes = json.loads(Path(args.boxes).read_text()).get("boxes", []) if args.boxes else None
            res = run_room(args.plate, geo, args.ortho, boxes=boxes, bar=args.bar,
                           block_t=args.block_threshold, heatmap_path=args.heatmap)
        except (HarnessError, OSError, ValueError) as exc:
            print(f"[registration] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        print(res.summary())
        payload = json.dumps(res.as_dict(), indent=2)
        if args.out:
            Path(args.out).write_text(payload, encoding="utf-8")
        else:
            print(payload)
        return 0 if res.passed else 1

    # score-rooms
    report = score_manifest_rooms(bar=args.bar, block_t=args.block_threshold,
                                  evidence_dir=Path(args.evidence_dir) if args.evidence_dir else None)
    print(_summary_table(report))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    failing = [r.get("registry_key", r.get("room")) for r in report["rooms"]
               if r.get("status") not in ("missing", "error") and not r.get("passed")]
    if failing:
        print(f"[registration] BELOW BAR ({args.bar * 100:.0f}%): {failing}", file=sys.stderr)
    if report.get("errored"):
        print("[registration] ERROR: room(s) could not be measured — batch is indeterminate", file=sys.stderr)
        return 2
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
