#!/usr/bin/env python3
"""W1 — schematic -> a presentable stylized world map PNG. Pure PIL + numpy.

Consumes the artifacts `generate_overworld.py` wrote (height_map.png for relief,
biome_map.png for the tint, overworld.json for the vectors) and composites:
  * a HILLSHADE from the heightfield (directional lighting -> readable relief),
  * the biome tint modulated by that hillshade,
  * river vectors as blue strokes (width from the vector's own width_cells),
  * road vectors as tan strokes with a darker casing,
  * settlement markers + labels (name-hint slot) and bridge ticks.

No LLM, no procedural re-generation — it is a pure presentation pass over the
schematic so the map and the data can never disagree.

Run: python render_worldmap.py --in <dir> [--out <dir/worldmap.png>]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def hillshade(elev: np.ndarray, azimuth: float = 315.0, altitude: float = 45.0,
              z: float = 4.0) -> np.ndarray:
    """Classic Horn hillshade in 0..1. `z` exaggerates relief so a low-contrast
    schematic heightfield still reads as terrain."""
    az = np.radians(360.0 - azimuth + 90.0)
    alt = np.radians(altitude)
    gy, gx = np.gradient(elev * z)
    slope = np.pi / 2.0 - np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    shaded = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    return np.clip((shaded + 1.0) / 2.0, 0.0, 1.0)


def _load_font(size: int):
    for name in ("Arial.ttf", "DejaVuSans.ttf", "Helvetica.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render(in_dir: str | Path, out_path: str | Path | None = None) -> Path:
    in_dir = Path(in_dir)
    ow = json.loads((in_dir / "overworld.json").read_text())
    cs = ow["grid"]["cell_size_m"]

    elev = np.asarray(Image.open(in_dir / "height_map.png").convert("L"), dtype=np.float64) / 255.0
    biome = np.asarray(Image.open(in_dir / "biome_map.png").convert("RGB"), dtype=np.float64)

    hs = hillshade(elev)[..., None]
    # Ocean should stay flat/tinted; only shade land so water reads as water.
    land = (elev >= ow["grid"]["sea_level"])[..., None]
    lit = np.where(land, biome * (0.55 + 0.6 * hs), biome * 0.92)
    img = Image.fromarray(np.clip(lit, 0, 255).astype(np.uint8), mode="RGB")

    up = 2  # supersample the vector overlay for crisper strokes
    img = img.resize((img.width * up, img.height * up), Image.NEAREST)
    draw = ImageDraw.Draw(img)

    def _px(pt):
        return (pt[0] / cs * up, pt[1] / cs * up)

    # Rivers first (under roads), width from the vector's own log-flow width.
    for rv in ow["rivers"]:
        pts = [_px(p) for p in rv["points"]]
        w = max(1, int(round(rv["width_cells"] * up)))
        draw.line(pts, fill=(58, 108, 168), width=w, joint="curve")

    # Roads: dark casing then tan fill (a drawn-map road look).
    for rd in ow["roads"]:
        pts = [_px(p) for p in rd["points"]]
        draw.line(pts, fill=(60, 44, 28), width=4 * up, joint="curve")
        draw.line(pts, fill=(214, 182, 120), width=2 * up, joint="curve")

    # Bridges: a small perpendicular tick.
    for b in ow["bridges"]:
        x, y = _px(b["at"])
        draw.line([(x - 4 * up, y), (x + 4 * up, y)], fill=(40, 26, 16), width=2 * up)

    # Settlements: a filled marker sized by habitability + the name-hint slot label.
    font = _load_font(11 * up)
    for s in ow["settlements"]:
        x, y = _px((s["x"], s["y"]))
        r = (3 + 4 * float(s["habitability"])) * up
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(232, 226, 208), outline=(30, 24, 18), width=up)
        draw.text((x + r + 2 * up, y - 6 * up), s["name_hint"], fill=(24, 18, 12), font=font)

    if out_path is None:
        out_path = in_dir / "worldmap.png"
    img.save(out_path)
    return Path(out_path)


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Render a stylized world map from a schematic")
    ap.add_argument("--in", dest="in_dir", type=str, required=True)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args(argv)
    out = render(args.in_dir, args.out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
