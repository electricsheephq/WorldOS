#!/usr/bin/env python3
"""Draw the LIVE collision grid onto a live player frame — the felt-truth lens for paint-vs-grid work.

Companion to qa/walk_test.py (the gate) and qa/reauthor_legacy_room.md (the method). walk_test proves
"click a walkable cell, the token lands there"; it cannot see whether that cell is painted as a table.
This renders /combat-surface's walkability onto a /shot frame so a human (or an agent reading the PNG)
can see the disagreement: RED diamond = impassable, GREEN outline = walkable, BLUE = door cell.

The cell->pixel map is the CONTRACT projection, not a per-room pixel fit: cell -> world via
greybox_render_headless.cell_to_world, world -> viewport via the frozen Euler(30,45,0) ortho camera
(the same math walk_test.world_to_window_px uses). That matters — the 2026-09-02 lens fitted an affine
from three anchor clicks and its row axis came out ~7 deg off, which is a whole cell of drift at the
grid edge and is exactly how a paint-vs-grid read goes wrong.

`--verify` proves the map instead of asserting it: it reads the actor's own viewport position
(/debug actorVX/actorVY) and the actor's cell (/combat-surface stage token) and prints the residual in
cells. Anything above ~0.25 cell means the frame and the surface disagree (wrong room, mid-glide
capture, non-contract camera) and the overlay must not be trusted.

Usage:
  qa/overlay_collision.py --frame shot.png --out overlay.png \
      --engine http://127.0.0.1:8956 --qa http://127.0.0.1:9062 --verify
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

PITCH_DEG, YAW_DEG = 30.0, 45.0


def camera_ru(wx: float, wy: float, wz: float) -> tuple:
    """(right, up) camera-axis coordinates of a world point under the frozen dimetric contract."""
    p, y = math.radians(PITCH_DEG), math.radians(YAW_DEG)
    right = (math.cos(y), 0.0, -math.sin(y))
    up = (math.sin(y) * math.sin(p), math.cos(p), math.cos(y) * math.sin(p))
    return (wx * right[0] + wy * right[1] + wz * right[2],
            wx * up[0] + wy * up[1] + wz * up[2])


def cell_to_viewport(c: float, r: float, cols: int, rows: int, ortho: float,
                     aspect: float) -> tuple:
    """Cell (c, r) -> viewport (vx, vy_up) in [0,1], bottom-left origin (the /debug actorV* frame)."""
    wx, wy, wz = (c - (cols - 1) / 2.0) * 2.0, 0.0, ((rows - 1) / 2.0 - r) * 2.0
    cr, cu = camera_ru(wx, wy, wz)
    return (0.5 + cr / (2.0 * ortho * aspect), 0.5 + cu / (2.0 * ortho))


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as fh:
        return json.loads(fh.read().decode())


def _post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as fh:
        return json.loads(fh.read().decode())


def verify(surface: dict, dbg: dict, aspect: float, ortho: float) -> dict:
    """Residual, in cells, between the actor's PREDICTED and REPORTED viewport position.

    `ortho` is the ortho the overlay will actually be DRAWN with, not necessarily the live camera's:
    verifying the live value while drawing a `--ortho` override would hand a wrong projection a GREEN
    residual, and this check is what licenses editing collision geometry off the overlay.
    """
    g = surface["grid"]
    tok = next((t for t in (surface.get("stage") or {}).get("tokens", [])
                if t.get("kind") == "player"), None)
    if tok is None or dbg.get("actorVX") is None:
        return {"ok": False, "reason": "no player token / no actorV on /debug"}
    vx, vy = cell_to_viewport(float(tok["x"]), float(tok["y"]),
                              int(g["cols"]), int(g["rows"]), ortho, aspect)
    # one cell of camera-up is 2 world units / (2*ortho) of viewport height
    per_cell = 2.0 / (2.0 * ortho)
    dx = (float(dbg["actorVX"]) - vx) * aspect / per_cell
    dy = (float(dbg["actorVY"]) - vy) / per_cell
    return {"ok": math.hypot(dx, dy) <= 0.25, "cell": [tok["x"], tok["y"]], "ortho": ortho,
            "cam_ortho": dbg.get("camOrtho"),
            "residual_cells": [round(dx, 3), round(dy, 3)],
            "predicted": [round(vx, 5), round(vy, 5)],
            "reported": [dbg["actorVX"], dbg["actorVY"]]}


def draw(frame: Path, out: Path, surface: dict, ortho: float, labels: bool = True,
         fill: bool = True) -> str:
    from PIL import Image, ImageDraw  # noqa: PLC0415  (evidence-only dependency)

    g = surface["grid"]
    cols, rows = int(g["cols"]), int(g["rows"])
    blocked, doors, refs = set(), set(), {}
    for cell in g["cells"]:
        key = (cell["c"], cell["r"])
        if cell["type"] == "door":
            doors.add(key)
        if not cell["walkable"]:
            blocked.add(key)
            refs[key] = cell.get("prop_ref") or cell["type"]
    im = Image.open(frame).convert("RGBA")
    wpx, hpx = im.size
    aspect = wpx / hpx
    ov = Image.new("RGBA", (wpx, hpx), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov)

    def px(c: float, r: float) -> tuple:
        vx, vy = cell_to_viewport(c, r, cols, rows, ortho, aspect)
        return (vx * wpx, (1.0 - vy) * hpx)

    for r in range(rows):
        for c in range(cols):
            quad = [px(c - .5, r - .5), px(c + .5, r - .5), px(c + .5, r + .5), px(c - .5, r + .5)]
            if (c, r) in doors:
                dr.polygon(quad, fill=(0, 160, 255, 95) if fill else None,
                           outline=(0, 220, 255, 255))
            elif (c, r) in blocked:
                dr.polygon(quad, fill=(255, 0, 0, 90) if fill else None,
                           outline=(255, 70, 70, 235))
            else:
                dr.polygon(quad, outline=(0, 255, 0, 150))
            if labels:
                x, y = px(c, r)
                dr.text((x - 11, y - 5), f"{c},{r}", fill=(255, 255, 90, 255))
    Image.alpha_composite(im, ov).convert("RGB").save(out)
    return (f"{out} — {cols}x{rows} scene={g.get('sceneId')} ortho={ortho} "
            f"blocked={len(blocked)} doors={sorted(doors)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frame", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--engine", default="http://127.0.0.1:8766")
    ap.add_argument("--qa", default="http://127.0.0.1:8971")
    ap.add_argument("--ortho", type=float, default=None, help="default: /debug camOrtho")
    ap.add_argument("--no-labels", action="store_true")
    ap.add_argument("--outline", action="store_true",
                    help="outline only — read the PAINT under the grid without the red tint")
    ap.add_argument("--verify", action="store_true",
                    help="print the actor-position residual in cells and FAIL over 0.25")
    args = ap.parse_args(argv)

    surface = _get(args.engine + "/combat-surface")
    dbg = _post(args.qa + "/debug", {})
    ortho = args.ortho if args.ortho is not None else float(dbg["camOrtho"])
    frame = Path(args.frame)
    from PIL import Image  # noqa: PLC0415
    with Image.open(frame) as im:
        aspect = im.size[0] / im.size[1]
    if args.verify:
        v = verify(surface, dbg, aspect, ortho)
        print("[overlay_collision] verify: " + json.dumps(v))
        if not v.get("ok"):
            print("[overlay_collision] RED — the projection does not reproduce the actor's own "
                  "viewport position; do not read this overlay", file=sys.stderr)
            return 1
    print("[overlay_collision] " + draw(frame, Path(args.out), surface, ortho,
                                        labels=not args.no_labels, fill=not args.outline))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
