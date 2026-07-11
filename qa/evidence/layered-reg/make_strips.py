#!/usr/bin/env python3
"""make_strips.py — progression strip (base->p1->p2->p3) + panel comparison strip for the PR."""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

LR = Path("qa/evidence/layered-reg")
TW = 520  # per-tile width


def _font(sz):
    for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            pass
    return ImageFont.load_default()


def strip(items, out, cols=None):
    cols = cols or len(items)
    tiles = []
    for path, label in items:
        im = Image.open(path).convert("RGB")
        h = int(im.height * TW / im.width)
        im = im.resize((TW, h), Image.LANCZOS)
        canvas = Image.new("RGB", (TW, h + 46), (18, 18, 22))
        canvas.paste(im, (0, 46))
        d = ImageDraw.Draw(canvas)
        d.text((8, 12), label, fill=(238, 238, 238), font=_font(20))
        tiles.append(canvas)
    th = max(t.height for t in tiles)
    rows = (len(tiles) + cols - 1) // cols
    out_im = Image.new("RGB", (TW * cols, th * rows), (18, 18, 22))
    for i, t in enumerate(tiles):
        out_im.paste(t, ((i % cols) * TW, (i // cols) * th))
    out_im.save(out)
    print("wrote", out, out_im.size)


strip([
    (LR / "gen_base/base_fit2.png", "BASE (reused fit2, recall 0.98)"),
    (LR / "passes/p1_material_s1111.png", "p1 MATERIAL (recall 0.93)"),
    (LR / "passes/p2_lighting_s2111.png", "p2 LIGHTING (recall 0.63 - CAMERA BROKE)"),
    (LR / "passes/p3_scatter_s3000.png", "p3 SCATTER (final, +1 IC flag)"),
], LR / "progression_strip.png", cols=2)

strip([
    (LR / "gen_base/fit2_singlepass_s1111.png", "single-pass  median 7.0  d-1.0"),
    (LR / "passes/p1_material_s1111.png", "layered p1  median 6.0  d-2.0"),
    (LR / "passes/p3_scatter_s3000.png", "layered p3  median 5.0  d-3.0 REJECT"),
    (Path("qa/evidence/new-tavern/tavern_truegrey_v1.png"), "INCUMBENT  median 7.5  d-0.5"),
], LR / "panel_strip.png", cols=2)
