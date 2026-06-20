#!/usr/bin/env python3
"""pack_sheet.py — tile baked frames into the renderer's sprite sheet + manifest (#1062).

Stage 3 of the WorldOS GT2 final-art pipeline (meshy_gen.py -> bake_sprites.py -> THIS).

Reads the per-(facing,anim,frame) PNGs produced by bake_sprites.py and packs them into the
EXACT atlas layout the Godot renderer consumes (CharacterToken.gd / ISO-PROJECTION.md):

    ROWS = 8 facings, order S,SE,E,NE,N,NW,W,SW   (row index == facing index)
    COLS = idle(4) + walk(8) + attack(6) + cast(6) = 24
    cell = 128  =>  sheet is 3072 x 1024

It then writes a sheet.json manifest IDENTICAL IN SHAPE to the committed CC0 placeholder
manifest (godot/assets/characters/aubree/sheet.json), but with the finals' provenance:
    source      = "meshy-blender-render"
    license     = "proprietary-owner-generated (Meshy AI)"
    attribution = notes the prompt + Meshy + Blender bake
The foot `anchor` comes from the bake's anchor.json.

Usage:
    python3 pack_sheet.py --frames <frames_dir> --scope sprite-aubree-iso8 \
        --out <assets_dir> [--out <assets_dir2> ...]   # may pass --out multiple times

If a frame PNG is missing it is filled transparent (so the sheet is always well-formed),
and the count of missing frames is reported.
"""

from __future__ import annotations

import argparse
import json
import os

from PIL import Image

# Locked layout (mirrors gen_placeholder_sheet.py / CharacterToken.gd).
CELL = 128
FACING_ORDER = ["S", "SE", "E", "NE", "N", "NW", "W", "SW"]
ANIMS = [("idle", 4, True), ("walk", 8, False), ("attack", 6, False), ("cast", 6, False)]
# NOTE: loop flags below MATCH the committed placeholder manifest exactly.
ANIM_LOOP = {"idle": True, "walk": True, "attack": False, "cast": False}
TOTAL_COLS = sum(c for _, c, _ in ANIMS)  # 24
FPS = 10


def _load_anchor(frames_dir: str) -> dict:
    path = os.path.join(frames_dir, "anchor.json")
    if not os.path.isfile(path):
        return {"x": CELL // 2, "y": int(CELL * 0.9)}
    with open(path) as f:
        doc = json.load(f)
    return {"x": int(doc.get("x", CELL // 2)), "y": int(doc.get("y", int(CELL * 0.9)))}


def _build_sheet(frames_dir: str, cell: int) -> tuple:
    width = TOTAL_COLS * cell
    height = len(FACING_ORDER) * cell
    sheet = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    missing = 0
    for row, facing in enumerate(FACING_ORDER):
        col = 0
        for anim, count, _loop in ANIMS:
            for frame in range(count):
                fname = "%s_%s_%d.png" % (facing, anim, frame)
                fpath = os.path.join(frames_dir, fname)
                if os.path.isfile(fpath):
                    cellimg = Image.open(fpath).convert("RGBA")
                    if cellimg.size != (cell, cell):
                        cellimg = cellimg.resize((cell, cell), Image.LANCZOS)
                else:
                    cellimg = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
                    missing += 1
                sheet.paste(cellimg, (col * cell, row * cell))
                col += 1
    return sheet, missing


def _manifest(scope: str, anchor: dict, prompt: str, cell: int) -> dict:
    animations = {}
    start = 0
    for anim, count, _loop in ANIMS:
        animations[anim] = {"start": start, "count": count, "loop": ANIM_LOOP[anim]}
        start += count
    return {
        "manifest_version": 1,
        "scope_key": scope,
        "kind": "character",
        "projection": "dimetric-2to1",
        "image": "sheet.png",
        "frame": {"w": cell, "h": cell},
        "sheet": {"cols": TOTAL_COLS, "rows": len(FACING_ORDER)},
        "facings": len(FACING_ORDER),
        "facing_order": FACING_ORDER,
        "anchor": {"x": int(anchor["x"]), "y": int(anchor["y"])},
        "fps": FPS,
        "animations": animations,
        "source": "meshy-blender-render",
        "license": "proprietary-owner-generated (Meshy AI)",
        "attribution": (
            "WorldOS final render — Meshy AI text-to-3d (prompt: \"%s\") baked to 8-facing "
            "dimetric-2to1 frames via godot/tools/bake_sprites.py (Blender headless), packed "
            "by godot/tools/pack_sheet.py." % prompt
        ),
    }


def _write_outputs(sheet: Image.Image, manifest: dict, out_dirs: list) -> None:
    for d in out_dirs:
        os.makedirs(d, exist_ok=True)
        sheet.save(os.path.join(d, "sheet.png"))
        with open(os.path.join(d, "sheet.json"), "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print("[pack_sheet] wrote sheet.png + sheet.json -> %s" % d)


def main() -> None:
    ap = argparse.ArgumentParser(description="Pack baked frames into the renderer sprite sheet.")
    ap.add_argument("--frames", required=True, help="dir of <facing>_<anim>_<i>.png + anchor.json")
    ap.add_argument("--scope", default="sprite-aubree-iso8", help="manifest scope_key")
    ap.add_argument("--out", required=True, action="append",
                    help="output assets dir (repeatable: writes sheet.png+sheet.json to each)")
    ap.add_argument("--prompt", default="(prompt unrecorded)",
                    help="the Meshy prompt, for attribution provenance")
    ap.add_argument("--cell", type=int, default=CELL, help="cell size (default %d)" % CELL)
    args = ap.parse_args()

    frames_dir = os.path.abspath(args.frames)
    if not os.path.isdir(frames_dir):
        raise SystemExit("[pack_sheet] ERROR: frames dir not found: %s" % frames_dir)

    # Prefer the prompt recorded by meshy_gen.py (meshy_meta.json) if --prompt not given.
    prompt = args.prompt
    meta_candidates = [
        os.path.join(frames_dir, "meshy_meta.json"),
        os.path.join(os.path.dirname(frames_dir), "meshy_meta.json"),
    ]
    if prompt == "(prompt unrecorded)":
        for mc in meta_candidates:
            if os.path.isfile(mc):
                try:
                    with open(mc) as f:
                        prompt = json.load(f).get("prompt", prompt)
                    break
                except Exception:
                    pass

    anchor = _load_anchor(frames_dir)
    sheet, missing = _build_sheet(frames_dir, args.cell)
    manifest = _manifest(args.scope, anchor, prompt, args.cell)
    _write_outputs(sheet, manifest, args.out)

    print("[pack_sheet] sheet dims=%s anchor=(%d,%d) missing_frames=%d" % (
        str(sheet.size), anchor["x"], anchor["y"], missing))
    expected = (TOTAL_COLS * args.cell, len(FACING_ORDER) * args.cell)
    assert sheet.size == expected, "sheet must be %s, got %s" % (expected, sheet.size)
    if missing:
        print("[pack_sheet] WARNING: %d frames were missing and filled transparent" % missing)
    print("[pack_sheet] OK")


if __name__ == "__main__":
    main()
