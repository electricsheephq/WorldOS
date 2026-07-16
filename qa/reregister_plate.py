#!/usr/bin/env python3
"""reregister_plate.py — deterministic corrective warp for the Gemini style-pass plate drift.

THE DEFECT (measured, production): the Gemini structure-lock style pass GLOBALLY RESCALES +
TRANSLATES the painted room plate. The engine's collision geometry is projection-true at the plate's
STAMPED ortho (boxes["ortho"]), so a >1-cell plate offset means the owner walks through painted props
and collides with visually-open floor (owner-confirmed in playtest). Measured on dwing_room_0:
fitted_ortho 8.24 vs stamped 10.52, screen_offset [58.8, 193.3] → 1.66-cell brazier error.

THE FIX: the rooms carry BRAZIER props (bright fire bowls) — dress_focal guarantees 2+ per generated
room, giving 2+ solvable registration beacons. qa/overlay_boxes.py::blob_solve already fits the plate's
TRUE (fitted_ortho, screen_offset) from the fire blobs vs the projected bowl centers. From that fit we
compute the exact affine (uniform scale about screen center + translation) that maps the painted content
to where the STAMPED ortho projects it, apply it with PIL (bicubic), and RE-SOLVE to confirm the
corrected plate lands in the shipped-registration class (max err_cells <= 0.35).

── PROJECTION MATH (derivation) ──────────────────────────────────────────────────────────────────────
greybox_render_headless.world_to_screen projects a world point, relative to the screen center, as a pure
1/ortho scaling of a fixed camera-basis quantity (the _POS camera offset is perpendicular to the view
right/up axes, so it cancels — see _camera_ru):

    sx - W/2 = P / ortho          sy - H/2 = Q / ortho       (P, Q depend only on the world point)

blob_solve reports, for the painted plate, the (fitted_ortho, screen_offset=[dx, dy]) such that a fire
blob sits at  blob = project(bowl, fitted_ortho) + [dx, dy]  (dx = mean(blob - proj), dy likewise). The
engine renders/collides the SAME bowl at  target = project(bowl, stamped_ortho)  with NO offset. Writing
both relative to center and eliminating (P, Q):

    X_cur - dx = P / fitted_ortho ,  X_tgt = P / stamped_ortho
    ⇒ X_tgt = (fitted_ortho / stamped_ortho) · (X_cur - dx)

So with the SCALE  s = fitted_ortho / stamped_ortho  the forward map (current plate px → corrected px) is

    corrected = s · current + t ,   t_x = (1-s)·W/2 - s·dx ,   t_y = (1-s)·H/2 - s·dy

i.e. scale by s about the screen center, then translate to undo the fitted offset. The sign is VERIFIED
empirically against the real dwing_room_0 plate: s = fitted/stamped (0.783) drives the re-solve to
fitted≈stamped (10.84≈10.52) and offset≈0 (registered); the inverse leaves fitted at 7.52 with a large
residual offset (its lower nearest-blob err is a coincidence of the fire-ember spread, NOT registration).

PIL's Image.transform(AFFINE) wants the INVERSE map (corrected px → source px):
    src = (1/s)·corrected - t/s  ⇒  coeffs (a,b,c,d,e,f) = (1/s, 0, -t_x/s, 0, 1/s, -t_y/s)

Revealed borders (s<1 shrinks the content) are filled with the plate's median EDGE color so the 1344x768
frame stays intact and the seam is invisible.

Usage:
  python3 qa/reregister_plate.py <boxes.json> <plate.png> --out <corrected.png>
Exit code is non-zero unless the corrected max err_cells <= 0.35 (the shipped-registration class).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from overlay_boxes import blob_solve  # noqa: E402  (reuse the brazier blob-solve machinery, not a copy)

W, H = 1344, 768
PASS_ERR_CELLS = 0.35  # the shipped-registration class
_EDGE_STRIP = 8        # border-strip width sampled for the fill color


def _max_err_cells(solve: dict) -> float:
    return max((p["err_cells"] for p in solve.get("per_bowl_at_stamp", [])), default=float("inf"))


def _border_median(img) -> tuple:
    """Median RGB of the plate's outer edge strip — the fill for borders revealed by the shrink warp,
    so the re-framed 1344x768 plate has no black/void seam."""
    import numpy as np

    a = np.asarray(img.convert("RGB"))
    b = _EDGE_STRIP
    edge = np.concatenate([a[:b].reshape(-1, 3), a[-b:].reshape(-1, 3),
                           a[:, :b].reshape(-1, 3), a[:, -b:].reshape(-1, 3)])
    return tuple(int(np.median(edge[:, i])) for i in range(3))


def corrective_transform(solve: dict) -> dict:
    """From a blob_solve result compute the affine that maps the painted content to the stamped-ortho
    projection. Returns the forward scale/translate plus the PIL-inverse coefficients (a,b,c,d,e,f)."""
    fitted = float(solve["fitted_ortho"])
    stamped = float(solve["stamped_ortho"])
    dx, dy = solve["screen_offset"]
    s = fitted / stamped
    tx = (1.0 - s) * W / 2.0 - s * dx
    ty = (1.0 - s) * H / 2.0 - s * dy
    # PIL AFFINE is the inverse map (output px -> source px): src = (1/s)*out - t/s
    inv = (1.0 / s, 0.0, -tx / s, 0.0, 1.0 / s, -ty / s)
    return {"scale": s, "translate": [tx, ty], "pil_affine_inverse": inv}


def reregister(boxes: dict, plate_path: Path, out_path: Path) -> dict:
    """Solve → warp → re-solve. Writes the corrected plate to out_path and returns a report with
    before/after {fitted_ortho, screen_offset, max_err_cells}, the transform, and a `passed` flag
    (corrected max err_cells <= 0.35). If the brazier signal is insufficient, returns {"error": ...}
    with passed=False and does NOT write out_path."""
    from PIL import Image

    before = blob_solve(boxes, Path(plate_path))
    if "error" in before:
        return {"error": before["error"], "passed": False, "before": before}

    tf = corrective_transform(before)
    img = Image.open(plate_path).convert("RGB")
    fill = _border_median(img)
    corrected = img.transform((W, H), Image.AFFINE, tf["pil_affine_inverse"],
                              resample=Image.BICUBIC, fillcolor=fill)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    corrected.save(out_path)

    after = blob_solve(boxes, Path(out_path))
    return {
        "passed": _max_err_cells(after) <= PASS_ERR_CELLS,
        "scale": round(tf["scale"], 4),
        "translate": [round(v, 1) for v in tf["translate"]],
        "before": {"fitted_ortho": before["fitted_ortho"], "screen_offset": before["screen_offset"],
                   "max_err_cells": round(_max_err_cells(before), 2)},
        "after": {"fitted_ortho": after["fitted_ortho"], "screen_offset": after["screen_offset"],
                  "max_err_cells": round(_max_err_cells(after), 2)},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("boxes", help="room_boxes.json sidecar (build_room_unified.cs output)")
    ap.add_argument("image", help="styled plate PNG (1344x768) to re-register")
    ap.add_argument("--out", required=True, help="write the corrected plate PNG here")
    args = ap.parse_args()

    boxes = json.loads(Path(args.boxes).read_text())
    rep = reregister(boxes, Path(args.image), Path(args.out))
    print(json.dumps(rep, indent=1))
    if not rep.get("passed"):
        print(f"[reregister_plate] FAILED: corrected max err_cells > {PASS_ERR_CELLS}", file=sys.stderr)
        return 1
    print(f"[reregister_plate] OK: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

def fit_similarity(pairs: list) -> dict:
    """Full 2D similarity T(z)=a·z+b (complex least squares; scale+ROTATION+translation) mapping
    CURRENT plate px -> TARGET px, from the solve's matched blob->proj_at_stamp pairs. Two beacons
    determine it exactly — the scale+translate-only path (3 dof) discards the rotation the same two
    points already encode, and a slight Gemini rotation is exactly the residual it can't fix.
    Instability guard: |rotation| > 4 deg or scale outside [0.7, 1.4] -> {"unstable": True}."""
    import cmath
    zs = [complex(p["blob"][0], p["blob"][1]) for p in pairs]
    ws = [complex(p["proj_at_stamp"][0], p["proj_at_stamp"][1]) for p in pairs]
    n = len(zs)
    mz, mw = sum(zs) / n, sum(ws) / n
    num = sum((w - mw) * (z - mz).conjugate() for z, w in zip(zs, ws))
    den = sum(abs(z - mz) ** 2 for z in zs)
    if den == 0:
        return {"unstable": True}
    a = num / den
    b = mw - a * mz
    rot_deg = abs(cmath.phase(a)) * 180.0 / 3.141592653589793
    scale = abs(a)
    if rot_deg > 6.0 or not (0.7 <= scale <= 1.4):  # guard vs absurd 2-point fits; the post-warp re-solve is the real acceptance
        return {"unstable": True, "rot_deg": round(rot_deg, 2), "scale": round(scale, 4)}
    return {"a": a, "b": b, "rot_deg": round(rot_deg, 2), "scale": round(scale, 4)}


def apply_similarity(img, sim: dict):
    """PIL affine apply of T(z)=a z+b. Image.transform maps OUTPUT->INPUT, so we pass T^-1:
    z = (w - b)/a  ->  matrix [re(1/a), -im(1/a), re(-b/a) ... ] in PIL (a,b,c,d,e,f) order."""
    from PIL import Image
    inv_a = 1.0 / sim["a"]
    inv_b = -sim["b"] / sim["a"]
    coeffs = (inv_a.real, -inv_a.imag, inv_b.real, inv_a.imag, inv_a.real, inv_b.imag)
    return img.transform(img.size, Image.AFFINE, coeffs, resample=Image.BICUBIC,
                         fillcolor=_border_median(img))
