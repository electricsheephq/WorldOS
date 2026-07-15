#!/usr/bin/env python3
"""check_plate_drift.py — the DETERMINISTIC paint-drift gate (W6.3, #1462).

The eval the owner said "should have caught this": when a painted `canonical_plate` is regenerated
(room_recipes.json) or a room-class artifact is promoted (tools/library/promote.py), the painted set
pieces must still sit on the SAME authored logical cells they did before. img2img is composition-pinned
(low strength) precisely so the dimetric layout holds while diffusion re-lights — but nothing MACHINE-
CHECKED that it actually held. A prop that slid two cells is exactly the class of drift that produces
"actors walking over the logs" downstream (the engine's impassable set is keyed to the AUTHORED cells,
not to wherever the paint ended up).

The check, per room manifest (qa/room_manifests/<room>.cells.json, built by build_room_manifest.py):
  1. Each authored prop carries its `screen_bbox` — the authored logical cells reprojected under the
     CONTRACT camera (greybox_render_headless.py's verified ortho=13 / Euler(30,45,0) / cellToWorld
     rig; the #1396 reprojection recipe). This is the durable, versioned successor to the one-off
     incident dirs (qa/evidence/1397, 1408).
  2. A per-prop REFERENCE FINGERPRINT — a small mean-subtracted, L2-normalised luma grid captured from
     the KNOWN-GOOD plate at that bbox (embedded in the manifest, so the gate is self-contained), OR
     captured on the fly from an explicit `--baseline` plate (the current canonical, for a replacement).
  3. On a CANDIDATE plate we template-match the fingerprint back into the candidate within a sub-cell
     search window and take the peak normalised cross-correlation (NCC). A prop still on its authored
     cell peaks high (identity ~1.0); a prop that drifted ~2 cells leaves floor/other content at the
     authored bbox and the peak collapses (calibrated: known-good camp plate min 0.96 vs synthetic
     2-cell shift max 0.60 — see qa/test_plate_drift_gate.py). Any prop below NCC_MIN → DRIFT (fail loud).

DETERMINISTIC, no LLM. Needs Pillow + numpy (the qa image lane; the engine venv is intentionally free
of them, so this and its tests run under the plain interpreter — see the ci.yml `paint-drift-gate` job).
Read-only: never mutates engine state, plates, or the manifest.

  python3 qa/check_plate_drift.py check <plate.png> <manifest.cells.json> [--baseline <known_good.png>]
  python3 qa/check_plate_drift.py gate-recipes   # gate every room_recipes.json canonical_plate that
                                                 # has a committed manifest + a locally-available plate
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

# Reuse the ONE verified camera rig (ortho=13, Euler(30,45,0), pull-back 80, 1344x768, cellToWorld
# centred on (cols-1)/2) — the same transform that renders the greybox the plate is img2img'd from.
from greybox_render_headless import ORTHO_SIZE, PX_H, PX_W, cell_to_world, world_to_screen  # noqa: E402

# ── Calibration constants (qa/test_plate_drift_gate.py pins the separation these encode) ───────────
FP_GRID = 20            # fingerprint = FP_GRID x FP_GRID downsample of the bbox luma (shape-normalised)
NCC_MIN = 0.75          # peak NCC below this at the authored bbox == DRIFT. Known-good plate: >=0.96;
                        # synthetic 2-cell shift: <=0.60. 0.75 sits in the clean gap (margin both sides).
SEARCH_CELL_FRAC = 0.6  # search +/- this many CELLS around the authored bbox — absorbs sub-cell img2img
                        # jitter without ever recovering a ~2-cell drift (which lands outside the window).
SEARCH_STEP_PX = 3      # search stride (px). Coarse is fine: the pass/fail gap is wide.

_RECIPES_PATH = _QA_DIR.parent / "extensions" / "renderers" / "shared" / "room_recipes.json"
_MANIFESTS_DIR = _QA_DIR / "room_manifests"


# ── Camera reprojection (the #1396 recipe, at the contract greybox rig) ───────────────────────────
def project_cell_bbox(cells: list, cols: int, rows: int, *, ortho: Optional[float] = None) -> list:
    """Reproject one prop's authored logical cells to a screen-space [x0,y0,x1,y1] bbox: the bounding
    box of every cell's 4 FLOOR-plane corners (world y=0) through the contract camera. Floor-plane
    (not prop-height) keeps the bbox anchored to the authored CELL — the invariant the engine's
    impassable set shares — independent of however tall the paint renders the prop.

    `ortho` (M-ALIGN camera_fit-awareness): None ⇒ the fixed contract ORTHO_SIZE (13) — byte-identical
    for every non-fit room and every legacy caller. A camera_fit room (crypt_fresh @10.5224,
    tavern_fit2 @9.2597) is PAINTED at its fitted ortho, so its manifest bboxes must project at the
    SAME ortho or the whole grid samples ~0.71-0.81x shrunk toward centre (the QA-stack drift #M-ALIGN
    fixes)."""
    o = ORTHO_SIZE if ortho is None else ortho
    xs: list = []
    ys: list = []
    for (c, r) in cells:
        for dc in (-0.5, 0.5):
            for dr in (-0.5, 0.5):
                wx, wy, wz = cell_to_world(c + dc, r + dr, cols, rows)
                sx, sy = world_to_screen(wx, wy, wz, o)
                xs.append(sx)
                ys.append(sy)
    return [min(xs), min(ys), max(xs), max(ys)]


def col_pitch_px(cols: int, rows: int, *, ortho: Optional[float] = None) -> float:
    """Screen px spanned by one +column step near grid centre — the ruler that turns SEARCH_CELL_FRAC
    (cells) into a pixel search window. `ortho` None ⇒ the fixed ORTHO_SIZE (back-compat); a camera_fit
    room passes its fitted ortho so the pixel window scales with the room's own paint."""
    o = ORTHO_SIZE if ortho is None else ortho
    c = cols // 2
    r = rows // 2
    x0, y0 = world_to_screen(*cell_to_world(c, r, cols, rows), o)
    x1, y1 = world_to_screen(*cell_to_world(c + 1, r, cols, rows), o)
    return math.hypot(x1 - x0, y1 - y0) or 1.0


# ── Fingerprint (shared by the generator and the checker, so they can never disagree) ─────────────
def load_luma(path: str | Path) -> np.ndarray:
    """Plate -> float32 luma array (PIL 'L'). PNG or JPG; read-only."""
    return np.asarray(Image.open(path).convert("L"), dtype=np.float32)


def fingerprint(arr: np.ndarray, bbox: list) -> np.ndarray:
    """A FP_GRID x FP_GRID, mean-subtracted, L2-normalised luma vector for `bbox`. Shape-normalised
    (bilinear resize) so props of any bbox size share a comparable template; mean-sub + L2 make the
    dot product a normalised cross-correlation (tolerant to a global lighting scale/bias shift, which
    is exactly what a re-light does — we want to catch a MOVE, not a re-light)."""
    h, w = arr.shape
    x0, y0, x1, y1 = (int(round(v)) for v in bbox)
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(w, x1)
    y1 = min(h, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return np.zeros(FP_GRID * FP_GRID, dtype=np.float32)
    patch = Image.fromarray(arr[y0:y1, x0:x1]).resize((FP_GRID, FP_GRID), Image.BILINEAR)
    v = np.asarray(patch, dtype=np.float32).ravel()
    v = v - v.mean()
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _peak_ncc(template: np.ndarray, arr: np.ndarray, bbox: list, search_px: int) -> float:
    """Best NCC of `template` against `arr` over a +/-search_px window around `bbox` (the sub-cell
    search). `template` is already normalised; each candidate window is fingerprinted the same way."""
    if not np.any(template):
        return 0.0
    best = -1.0
    for dy in range(-search_px, search_px + 1, SEARCH_STEP_PX):
        for dx in range(-search_px, search_px + 1, SEARCH_STEP_PX):
            shifted = [bbox[0] + dx, bbox[1] + dy, bbox[2] + dx, bbox[3] + dy]
            cand = fingerprint(arr, shifted)
            if np.any(cand):
                best = max(best, float(np.dot(template, cand)))
    return best


# ── Result ────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class DriftResult:
    passed: bool
    room: str
    props: list = field(default_factory=list)      # per-prop {id, ncc, status}
    reasons: list = field(default_factory=list)     # human-readable fail/skip reasons
    checked: int = 0
    skipped: int = 0

    def as_dict(self) -> dict:
        return {"passed": self.passed, "room": self.room, "checked": self.checked,
                "skipped": self.skipped, "props": self.props, "reasons": self.reasons}

    def summary(self) -> str:
        verdict = "PASS" if self.passed else "DRIFT"
        return (f"[check_plate_drift] {self.room}: {verdict} "
                f"({self.checked} checked, {self.skipped} no-baseline)"
                + ("" if self.passed else " — " + "; ".join(self.reasons)))


# ── The gate ──────────────────────────────────────────────────────────────────────────────────────
def check_plate_drift(plate_path: str | Path, manifest: dict, *,
                      baseline: Optional[str | Path] = None,
                      ncc_min: float = NCC_MIN) -> DriftResult:
    """Gate one candidate plate against its room manifest. A prop's reference fingerprint comes from
    (a) the manifest's embedded `ref_fp` (self-contained), else (b) an explicit `baseline` plate
    (the current canonical, for a replacement). A prop with neither is SKIPPED (reported, not failed —
    a manifest can ship authored geometry before a known-good plate exists, e.g. the crypt). Any
    fingerprinted prop whose peak NCC at its authored bbox is below `ncc_min` is DRIFT → passed=False."""
    grid = manifest.get("grid", {})
    cols, rows = int(grid.get("cols", 0)), int(grid.get("rows", 0))
    room = str(manifest.get("room", "?"))
    cand = load_luma(plate_path)
    if cand.shape != (PX_H, PX_W):
        # A plate that is not the contract 1344x768 cannot be reprojection-checked — the bboxes were
        # computed in that pixel frame. Fail loud rather than silently mis-aligning.
        return DriftResult(False, room, reasons=[
            f"plate {Path(plate_path).name} is {cand.shape[1]}x{cand.shape[0]}, "
            f"expected the contract {PX_W}x{PX_H}"])

    base_arr = load_luma(baseline) if baseline is not None else None
    # M-ALIGN: a camera_fit manifest stamps its fitted ortho — size the sub-cell search window with the
    # SAME ortho the screen_bboxes were projected at (None ⇒ the fixed rig for every legacy manifest).
    room_ortho = float(manifest["ortho"]) if manifest.get("camera_fit") and manifest.get("ortho") else None
    search_px = int(round(SEARCH_CELL_FRAC * col_pitch_px(cols, rows, ortho=room_ortho)))
    result = DriftResult(True, room)
    for prop in manifest.get("props", []):
        pid = str(prop.get("id", "?"))
        bbox = prop.get("screen_bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            result.props.append({"id": pid, "status": "SKIP", "reason": "no screen_bbox"})
            result.skipped += 1
            continue
        ref = prop.get("ref_fp")
        if isinstance(ref, list) and len(ref) == FP_GRID * FP_GRID:
            template = np.asarray(ref, dtype=np.float32)
        elif base_arr is not None:
            template = fingerprint(base_arr, bbox)
        else:
            result.props.append({"id": pid, "status": "SKIP", "reason": "no ref_fp and no --baseline"})
            result.skipped += 1
            continue
        ncc = _peak_ncc(template, cand, bbox, search_px)
        ok = ncc >= ncc_min
        result.props.append({"id": pid, "status": "PASS" if ok else "DRIFT", "ncc": round(ncc, 4)})
        result.checked += 1
        if not ok:
            result.passed = False
            result.reasons.append(f"{pid} drifted (NCC {ncc:.3f} < {ncc_min}) — painted prop no longer "
                                  f"on authored cell(s) {prop.get('cells')}")
    if result.checked == 0 and not result.reasons:
        result.reasons.append("no fingerprintable props (manifest carries geometry only; "
                              "pass --baseline or seed ref_fp to gate)")
    return result


def load_manifest(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ── The room_recipes.json canonical_plate gate ────────────────────────────────────────────────────
def _resolve_plate(name: str, plates_dirs: list) -> Optional[Path]:
    """Locate a canonical_plate PNG/JPG among the known plate dirs (plates live on the box; only a
    subset is committed for CI). Tries the exact name, then a .jpg twin (v2 plates ship as .jpg)."""
    stem = Path(name).stem
    for d in plates_dirs:
        for cand in (d / name, d / f"{stem}.png", d / f"{stem}.jpg"):
            if cand.is_file():
                return cand
    return None


def gate_room_recipes(recipes_path: str | Path = _RECIPES_PATH,
                      manifests_dir: str | Path = _MANIFESTS_DIR,
                      plates_dirs: Optional[list] = None) -> dict:
    """Gate EVERY room in room_recipes.json that has a `canonical_plate`, a committed manifest, AND a
    locally-available plate. This is the guard for a canonical_plate REPLACEMENT: a swap that drifted
    the paint off the authored cells fails here. Rooms without a committed plate (crypt) are reported
    'no-plate' and skipped — the gate covers whatever is locally verifiable, never fails on absence."""
    manifests_dir = Path(manifests_dir)
    plates_dirs = [Path(p) for p in plates_dirs] if plates_dirs else [
        _QA_DIR / "evidence" / "plate-audit",
        _QA_DIR / "native_palette",
        _QA_DIR / "screenshot_baselines",
    ]
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
        manifest = load_manifest(manifest_path)
        if plate_path is None:
            report["rooms"].append({"recipe_key": key, "status": "no-plate", "plate": plate,
                                    "manifest": str(manifest_path.name)})
            continue
        res = check_plate_drift(plate_path, manifest)
        report["rooms"].append({"recipe_key": key, "plate": plate_path.name,
                                "manifest": manifest_path.name, **res.as_dict()})
        if not res.passed:
            report["passed"] = False
    return report


def _find_manifest_for_recipe(recipe_key: str, plate: str, manifests_dir: Path) -> Optional[Path]:
    """A manifest declares its `recipe_key`; match on that, else fall back to a <plate-stem>.cells.json
    filename convention."""
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


# ── CLI ───────────────────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="gate one plate against one room manifest")
    c.add_argument("plate")
    c.add_argument("manifest")
    c.add_argument("--baseline", default=None, help="known-good plate to fingerprint if the manifest "
                                                    "carries geometry only")
    c.add_argument("--ncc-min", type=float, default=NCC_MIN)
    sub.add_parser("gate-recipes", help="gate every room_recipes.json canonical_plate with a manifest+plate")
    args = ap.parse_args(argv)

    if args.cmd == "check":
        res = check_plate_drift(args.plate, load_manifest(args.manifest),
                                baseline=args.baseline, ncc_min=args.ncc_min)
        print(res.summary())
        print(json.dumps(res.as_dict(), indent=2))
        return 0 if res.passed else 1

    report = gate_room_recipes()
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
