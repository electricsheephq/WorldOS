#!/usr/bin/env python3
"""Generate the committed CC0 placeholder sprite sheets + manifests for #1054.

This is the REPRODUCIBLE source for every PNG under godot/assets/. The art is our
OWN original placeholder (no third-party download) so the committed tree has zero
license ambiguity — finals (AI / Blender / Hormelz) drop in later at the SAME
manifest layout.

LAYOUT (LOCKED — the renderer in CharacterToken.gd depends on it; see
ISO-PROJECTION.md):

  Character atlas  (godot/assets/characters/aubree/sheet.png)
    cell 128x128
    ROWS  = 8 facings, order S,SE,E,NE,N,NW,W,SW  (row 0 = S; row index = facing index)
    COLS  = animation frames concatenated:
              idle(4) + walk(8) + attack(6) + cast(6) = 24 columns
    => atlas is 3072 x 1024  (24*128 x 8*128)

  Prop atlas  (godot/assets/props/pillar/sheet.png)
    1 facing x 1 frame, 128x192  (a pillar/crate occluder, base at cell-bottom)

Each character cell draws a simple but UNAMBIGUOUSLY DIRECTIONAL token: a body
ellipse centered horizontally with its feet near the cell bottom (foot anchor at
~y=116), plus a facing wedge/triangle pointing in that row's compass direction.
Per-animation variation makes playback visible:
  idle   = gentle vertical bob
  walk   = bob + horizontal stride offset cycling
  attack = wedge thrusts outward (forward in facing dir) mid-anim
  cast   = a small orb appears above the body mid-anim

Everything is DETERMINISTIC (no randomness). Re-run to regenerate:

    python3 godot/tools/gen_placeholder_sheet.py
"""

from __future__ import annotations

import json
import math
import os

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Paths (resolved relative to the repo's godot/ dir, so the script works from
# any CWD: `python3 godot/tools/gen_placeholder_sheet.py`).
# ---------------------------------------------------------------------------
_THIS = os.path.abspath(__file__)
GODOT_DIR = os.path.dirname(os.path.dirname(_THIS))          # .../godot
ASSETS_DIR = os.path.join(GODOT_DIR, "assets")
CHAR_DIR = os.path.join(ASSETS_DIR, "characters", "aubree")
PROP_DIR = os.path.join(ASSETS_DIR, "props", "pillar")

# ---------------------------------------------------------------------------
# Locked layout constants.
# ---------------------------------------------------------------------------
CELL = 128
FACING_ORDER = ["S", "SE", "E", "NE", "N", "NW", "W", "SW"]
FACINGS = len(FACING_ORDER)  # 8

# Animation column layout: (name, count, loop). Order defines column packing.
ANIMS = [
    ("idle", 4, True),
    ("walk", 8, True),
    ("attack", 6, False),
    ("cast", 6, False),
]
TOTAL_COLS = sum(count for _, count, _ in ANIMS)  # 24
FPS = 10

# Foot anchor inside a character cell (the node origin sits here → it is the
# Y-sort depth key). Centered horizontally, near the bottom.
ANCHOR_X = 64
ANCHOR_Y = 116

# Per-facing screen-space unit direction the wedge points toward. Mirrors the
# FacingResolver octant geometry: screen +X right, +Y DOWN, S = toward camera
# (down). Index = facing index.
#   S=down, SE=down-right, E=right, NE=up-right, N=up, NW=up-left, W=left, SW=down-left
_DIAG = 1.0 / math.sqrt(2.0)
FACING_DIR = {
    "S":  (0.0, 1.0),
    "SE": (_DIAG, _DIAG),
    "E":  (1.0, 0.0),
    "NE": (_DIAG, -_DIAG),
    "N":  (0.0, -1.0),
    "NW": (-_DIAG, -_DIAG),
    "W":  (-1.0, 0.0),
    "SW": (-_DIAG, _DIAG),
}

# Aubree's stable body color (RGBA). A muted ranger green.
AUBREE_BODY = (86, 132, 92, 255)
AUBREE_BODY_DARK = (58, 92, 64, 255)   # outline / shaded base
WEDGE_COLOR = (236, 222, 150, 255)     # bright facing wedge (unambiguous)
ORB_COLOR = (150, 206, 255, 255)       # cast orb (cool blue)

# Body geometry (relative to the foot anchor). The body is an upright ellipse
# whose BOTTOM (the feet) sits at the anchor; the head is above it.
BODY_W = 44          # ellipse width
BODY_H = 64          # ellipse height (foot -> head)
HEAD_R = 13          # head circle radius


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _draw_character_cell(draw: ImageDraw.ImageDraw, ox: int, oy: int,
                         facing: str, anim: str, frame: int, count: int) -> None:
    """Draw one 128x128 character cell at top-left (ox, oy).

    Deterministic: every value is a pure function of (facing, anim, frame).
    """
    dirx, diry = FACING_DIR[facing]

    # Normalized phase through the animation [0,1).
    phase = (frame / count) if count > 0 else 0.0
    # A smooth 0->1->0 triangle for "mid-anim" emphasis (attack thrust / cast orb).
    mid = 1.0 - abs(2.0 * phase - 1.0)

    # --- per-animation body offset (so playback is visibly different) ---
    bob = 0.0
    stride = 0.0
    if anim == "idle":
        # gentle vertical bob
        bob = -2.0 * math.sin(phase * math.tau)
    elif anim == "walk":
        # bob + horizontal stride offset cycling (along the facing's screen X)
        bob = -3.0 * abs(math.sin(phase * math.tau))
        stride = 5.0 * math.sin(phase * math.tau) * (1.0 if dirx >= 0 else -1.0)
    elif anim == "attack":
        # body leans slightly into the strike at mid-anim
        bob = -1.0 * mid
    elif anim == "cast":
        # subtle settle while channeling
        bob = -1.5 * mid

    # Foot anchor position in cell px (with a small horizontal stride sway).
    fx = ox + ANCHOR_X + stride
    fy = oy + ANCHOR_Y + bob

    # --- shadow ellipse at the feet (grounds the token; foot contact) ---
    sh_w, sh_h = 38, 12
    draw.ellipse(
        [fx - sh_w / 2, oy + ANCHOR_Y - sh_h / 2 + 3,
         fx + sh_w / 2, oy + ANCHOR_Y + sh_h / 2 + 3],
        fill=(0, 0, 0, 70),
    )

    # --- body ellipse: bottom at the (bobbed) foot, head above ---
    body_top = fy - BODY_H
    body_box = [fx - BODY_W / 2, body_top, fx + BODY_W / 2, fy]
    draw.ellipse(body_box, fill=AUBREE_BODY, outline=AUBREE_BODY_DARK, width=3)

    # --- head circle above the body ---
    head_cy = body_top - HEAD_R + 4
    draw.ellipse(
        [fx - HEAD_R, head_cy - HEAD_R, fx + HEAD_R, head_cy + HEAD_R],
        fill=AUBREE_BODY, outline=AUBREE_BODY_DARK, width=3,
    )

    # --- facing wedge: a bright triangle pointing in the facing direction,
    #     anchored at the body's vertical center so it reads as "looking that way".
    cx = fx
    cy = body_top + BODY_H * 0.42  # body mid-ish (chest height)

    # Attack thrusts the wedge outward (forward) at mid-anim.
    thrust = 0.0
    if anim == "attack":
        thrust = 18.0 * mid

    base_len = 26.0 + thrust   # tip distance from center
    half_w = 12.0              # base half-width

    # Tip of the wedge in the facing direction.
    tx = cx + dirx * base_len
    ty = cy + diry * base_len
    # Perpendicular (screen) to spread the base.
    px, py = -diry, dirx
    # Base center sits a little out from the body center so the wedge isn't buried.
    bx = cx + dirx * 8.0
    by = cy + diry * 8.0
    p1 = (bx + px * half_w, by + py * half_w)
    p2 = (bx - px * half_w, by - py * half_w)
    draw.polygon([p1, (tx, ty), p2], fill=WEDGE_COLOR, outline=AUBREE_BODY_DARK)

    # --- cast orb: a small glowing orb appears above the body mid-anim ---
    if anim == "cast" and mid > 0.15:
        orb_r = _lerp(2.0, 8.0, mid)
        orb_cx = cx + dirx * 10.0
        orb_cy = head_cy - HEAD_R - 14.0
        a = int(_lerp(60, 230, mid))
        draw.ellipse(
            [orb_cx - orb_r, orb_cy - orb_r, orb_cx + orb_r, orb_cy + orb_r],
            fill=(ORB_COLOR[0], ORB_COLOR[1], ORB_COLOR[2], a),
            outline=(255, 255, 255, a),
        )


def gen_character_atlas() -> tuple[int, int]:
    """Render the full 3072x1024 character atlas + write its manifest."""
    width = TOTAL_COLS * CELL
    height = FACINGS * CELL
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")

    for row, facing in enumerate(FACING_ORDER):
        col = 0
        for anim, count, _loop in ANIMS:
            for frame in range(count):
                ox = col * CELL
                oy = row * CELL
                _draw_character_cell(draw, ox, oy, facing, anim, frame, count)
                col += 1

    os.makedirs(CHAR_DIR, exist_ok=True)
    out_png = os.path.join(CHAR_DIR, "sheet.png")
    img.save(out_png)

    # Manifest v1 — column ranges within a facing row.
    animations = {}
    start = 0
    for anim, count, loop in ANIMS:
        animations[anim] = {"start": start, "count": count, "loop": loop}
        start += count

    manifest = {
        "manifest_version": 1,
        "scope_key": "sprite-aubree-iso8",
        "kind": "character",
        "projection": "dimetric-2to1",
        "image": "sheet.png",
        "frame": {"w": CELL, "h": CELL},
        "sheet": {"cols": TOTAL_COLS, "rows": FACINGS},
        "facings": FACINGS,
        "facing_order": FACING_ORDER,
        "anchor": {"x": ANCHOR_X, "y": ANCHOR_Y},
        "fps": FPS,
        "animations": animations,
        "source": "worldos-placeholder",
        "license": "CC0-1.0",
        "attribution": (
            "WorldOS original placeholder (CC0) — regenerate via "
            "godot/tools/gen_placeholder_sheet.py"
        ),
    }
    with open(os.path.join(CHAR_DIR, "sheet.json"), "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return img.size


def gen_prop_atlas() -> tuple[int, int]:
    """Render the 128x192 pillar prop atlas + write its manifest."""
    pw, ph = 128, 192
    img = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")

    base_y = 184          # base/foot at cell-bottom (the anchor y)
    cx = 64
    col_w = 46            # pillar width
    top_y = 28

    # Stone pillar colors.
    stone = (122, 120, 130, 255)
    stone_dark = (78, 76, 86, 255)
    stone_light = (158, 156, 166, 255)

    # Ground shadow at the foot.
    draw.ellipse([cx - 40, base_y - 9, cx + 40, base_y + 9], fill=(0, 0, 0, 90))

    # Shaft (a tall rounded rectangle).
    draw.rounded_rectangle(
        [cx - col_w / 2, top_y, cx + col_w / 2, base_y],
        radius=8, fill=stone, outline=stone_dark, width=3,
    )
    # A vertical highlight stripe for a little form.
    draw.rectangle([cx - col_w / 2 + 8, top_y + 6, cx - col_w / 2 + 16, base_y - 6],
                   fill=stone_light)

    # Capital (top) + base (foot) slabs.
    draw.rounded_rectangle([cx - 32, top_y - 12, cx + 32, top_y + 12],
                           radius=4, fill=stone_light, outline=stone_dark, width=3)
    draw.rounded_rectangle([cx - 34, base_y - 16, cx + 34, base_y],
                           radius=4, fill=stone_dark, outline=stone_dark, width=2)

    os.makedirs(PROP_DIR, exist_ok=True)
    out_png = os.path.join(PROP_DIR, "sheet.png")
    img.save(out_png)

    manifest = {
        "manifest_version": 1,
        "scope_key": "prop-pillar-iso8",
        "kind": "prop",
        "projection": "dimetric-2to1",
        "image": "sheet.png",
        "frame": {"w": pw, "h": ph},
        "sheet": {"cols": 1, "rows": 1},
        "facings": 1,
        "facing_order": ["S"],
        "anchor": {"x": 64, "y": 184},
        "fps": FPS,
        "animations": {"idle": {"start": 0, "count": 1, "loop": True}},
        "source": "worldos-placeholder",
        "license": "CC0-1.0",
        "attribution": (
            "WorldOS original placeholder (CC0) — regenerate via "
            "godot/tools/gen_placeholder_sheet.py"
        ),
    }
    with open(os.path.join(PROP_DIR, "sheet.json"), "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return img.size


def main() -> None:
    char_size = gen_character_atlas()
    prop_size = gen_prop_atlas()
    print("[gen_placeholder_sheet] character atlas %s -> %s" % (
        char_size, os.path.join(CHAR_DIR, "sheet.png")))
    print("[gen_placeholder_sheet] prop atlas      %s -> %s" % (
        prop_size, os.path.join(PROP_DIR, "sheet.png")))
    assert char_size == (3072, 1024), "character atlas must be 3072x1024"
    assert prop_size == (128, 192), "prop atlas must be 128x192"
    print("[gen_placeholder_sheet] OK")


if __name__ == "__main__":
    main()
