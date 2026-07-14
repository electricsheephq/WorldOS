#!/usr/bin/env python3
"""make_grid_overlay.py — WALKSLICE-CRYPT-ALIGN (#1565) before/after grid overlay.

Projects the crypt combat-grid IMPASSABLE set onto the adopted crypt_fresh plate via the SAME
contract camera the sweep/client render against (greybox_render_headless.cell_to_world/world_to_screen,
1344x768), so the alignment is visible pixel-for-pixel. BEFORE = the superseded 12-cell sarcophagus
drift blob (cols3-7 x rows6-8) with no wall-band ornaments; AFTER = seed_gfx_combat._build_crypt_grid
today (2x2 coffin + 16 fresh-plate ornament cells). Freed cells = green, new ornament cells = blue,
unchanged impassable = red.

    uv run --directory servers/engine --with pillow python ../../qa/evidence/crypt-align/make_grid_overlay.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[3]
_QA = _ROOT / "qa"
sys.path.insert(0, str(_QA))
sys.path.insert(0, str(_ROOT / "servers" / "engine"))

import server  # noqa: E402,F401  (resolves the models<->scene_grid import cycle)
import seed_gfx_combat as combat  # noqa: E402
from scene_grid import impassable_cells  # noqa: E402
from greybox_render_headless import cell_to_world, world_to_screen  # noqa: E402  (contract camera)

PLATE = _QA / "evidence" / "crypt-fresh" / "crypt_fresh_v1.png"
COLS, ROWS = combat.GRID_W, combat.GRID_H

# The superseded 12-cell sarcophagus drift blob (pre-#1565), pillars unchanged, no ornaments.
_OLD_SARCO = [(4, 6), (5, 6), (6, 6), (7, 6), (3, 7), (4, 7), (5, 7), (6, 7), (7, 7), (4, 8), (5, 8), (6, 8)]


def _quad(c: int, r: int) -> list:
    cx, _, cz = cell_to_world(c, r, COLS, ROWS)
    world = [(cx - 1.0, 0.0, cz - 1.0), (cx + 1.0, 0.0, cz - 1.0),
             (cx + 1.0, 0.0, cz + 1.0), (cx - 1.0, 0.0, cz + 1.0)]
    return [world_to_screen(*w) for w in world]


def _perimeter() -> set:
    p = set()
    for c in range(COLS):
        p |= {(c, 0), (c, ROWS - 1)}
    for r in range(ROWS):
        p |= {(0, r), (COLS - 1, r)}
    return p


def _old_impassable() -> set:
    perim = _perimeter()
    pillars = {(3, 3), (3, 4), (8, 9), (9, 9)}
    return perim | pillars | set(_OLD_SARCO)


def _draw(cells: set, fills: dict, title: str) -> Image.Image:
    im = Image.open(PLATE).convert("RGB").resize((1344, 768))
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov, "RGBA")
    for (c, r) in sorted(cells):
        col = fills.get((c, r), (220, 40, 40, 90))          # default = unchanged impassable (red)
        outline = (col[0], col[1], col[2], 255)
        d.polygon(_quad(c, r), fill=col, outline=outline, width=2)
    d.text((12, 10), title, fill=(255, 255, 255, 255))
    return Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")


def main() -> int:
    new_imp = {(x, y) for (x, y) in (tuple(p) for p in impassable_cells(
        combat._build_crypt_grid(combat.CID, "overlay"), COLS, ROWS))}
    old_imp = _old_impassable()

    freed = old_imp - new_imp            # 8 sarcophagus drift cells now walkable (green)
    added = new_imp - old_imp            # 16 fresh-plate ornament cells now impassable (blue)

    before_fills = {cell: (220, 40, 40, 95) for cell in old_imp}
    for cell in freed:
        before_fills[cell] = (60, 200, 90, 120)   # highlight what will be freed
    before = _draw(old_imp, before_fills, "BEFORE (#1505 12-cell blob, no ornaments) - green = freed by #1565")

    after_fills = {cell: (220, 40, 40, 95) for cell in new_imp}
    for cell in added:
        after_fills[cell] = (60, 120, 235, 130)   # the 16 ornament cells
    after = _draw(new_imp, after_fills, "AFTER (#1565 2x2 coffin + 16 ornaments) - blue = new ornament cells")

    outdir = _HERE.parent
    before.save(outdir / "grid_overlay_before.png")
    after.save(outdir / "grid_overlay_after.png")
    # side-by-side for the PR
    combo = Image.new("RGB", (before.width, before.height * 2 + 8), (16, 16, 16))
    combo.paste(before, (0, 0))
    combo.paste(after, (0, before.height + 8))
    combo.save(outdir / "grid_overlay_before_after.png")

    print(f"old impassable={len(old_imp)}  new impassable={len(new_imp)}  freed={len(freed)}  added={len(added)}")
    print(f"freed cells (green):  {sorted(freed)}")
    print(f"added cells (blue):   {sorted(added)}")
    print(f"wrote {outdir/'grid_overlay_before_after.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
