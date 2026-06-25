#!/usr/bin/env python3
"""pack_sheet.py — tile baked frames into the renderer's sprite sheet + manifest (#1062).

Stage 3 of the WorldOS GT2 final-art pipeline (meshy_gen.py -> bake_sprites.py -> THIS).

Reads the per-(facing,anim,frame) PNGs produced by bake_sprites.py and packs them into the
EXACT atlas layout the Godot renderer consumes (CharacterToken.gd / ISO-PROJECTION.md):

    ROWS = 8 facings, order S,SE,E,NE,N,NW,W,SW   (row index == facing index)
    COLS = idle(4) + walk(8) + attack(6) + cast(6) = 24
    cell = 128  =>  sheet is 3072 x 1024

It then writes a sheet.json manifest IDENTICAL IN SHAPE to the committed CC0 placeholder
manifest (extensions/renderers/godot/assets/characters/aubree/sheet.json), but with the finals' provenance:
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
import time

# NOTE: Pillow (PIL) is imported lazily inside _build_sheet so the descriptor/manifest helpers
# (_descriptor / _manifest / _is_private_finals_dir) are importable + unit-testable WITHOUT the
# Pillow dependency (it is not in the engine/viewer test venvs). Only the pixel-tiling path needs it.

# The viewer descriptor the /image bridge resolves. To serve the baked atlas via
# /image?scope=<scope_key> (the render-profile contract: "served via the existing /image bridge
# unchanged"), the viewer's _serve_image looks for content/worlds/_private/<world>/images/<scope>/
# wiki_ingest.json and serves the PNG it points at. Without this descriptor a baked sheet.png is
# orphaned (/image?scope=... 404s, no-art). Mirrors tools/ingest/wiki_images.py write_descriptor
# (the canonical descriptor writer the viewer reads); this is #1063's serve-side enablement.
DESCRIPTOR_FILENAME = "wiki_ingest.json"

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
    from PIL import Image  # lazy: only the pixel-tiling path needs Pillow (see top-of-file note)

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
            "dimetric-2to1 frames via extensions/renderers/godot/tools/bake_sprites.py (Blender headless), packed "
            "by extensions/renderers/godot/tools/pack_sheet.py." % prompt
        ),
    }


def _descriptor(scope: str, sheet_path: str, manifest: dict) -> dict:
    """The viewer descriptor (wiki_ingest.json) that makes the baked atlas servable via
    /image?scope=<scope>. Shape matches tools/ingest/wiki_images.py write_descriptor (what
    _serve_image / _latest_descriptor read). `path` is absolute (the viewer anchors it to the
    descriptor's sibling dir by basename); pixels are served from `path` (no external source_url)."""
    return {
        "scope": scope,
        "path": os.path.abspath(sheet_path),
        "mime_type": "image/png",
        "source_url": "",  # baked locally — _serve_image serves `path`, not a remote URL.
        "license": manifest.get("license", ""),
        "attribution": manifest.get("attribution", ""),
        "ingested_at": time.time(),
    }


def _is_private_finals_dir(d: str) -> bool:
    """True only for the served finals tree (content/worlds/_private/...), where wiki_ingest.json is
    gitignored. We NEVER drop a descriptor (which embeds an absolute local path) into a committed
    dir such as the res:// CC0 placeholder — that path would be wrong on every other machine."""
    return (os.sep + "_private" + os.sep) in (os.path.abspath(d) + os.sep)


def _write_outputs(sheet: Image.Image, manifest: dict, out_dirs: list,
                   emit_descriptor: bool = True) -> None:
    for d in out_dirs:
        os.makedirs(d, exist_ok=True)
        sheet_path = os.path.join(d, "sheet.png")
        sheet.save(sheet_path)
        with open(os.path.join(d, "sheet.json"), "w") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
            f.write("\n")
        wrote_desc = ""
        if emit_descriptor and _is_private_finals_dir(d):
            with open(os.path.join(d, DESCRIPTOR_FILENAME), "w") as f:
                json.dump(_descriptor(manifest["scope_key"], sheet_path, manifest),
                          f, indent=2, ensure_ascii=False)
                f.write("\n")
            wrote_desc = " + %s" % DESCRIPTOR_FILENAME
        elif emit_descriptor:
            print("[pack_sheet] (no %s: %s is not under _private — committed dir, descriptor skipped)"
                  % (DESCRIPTOR_FILENAME, d))
        print("[pack_sheet] wrote sheet.png + sheet.json%s -> %s" % (wrote_desc, d))


def main() -> None:
    ap = argparse.ArgumentParser(description="Pack baked frames into the renderer sprite sheet.")
    ap.add_argument("--frames", required=True, help="dir of <facing>_<anim>_<i>.png + anchor.json")
    ap.add_argument("--scope", default="sprite-aubree-iso8", help="manifest scope_key")
    ap.add_argument("--out", required=True, action="append",
                    help="output assets dir (repeatable: writes sheet.png+sheet.json to each)")
    ap.add_argument("--prompt", default="(prompt unrecorded)",
                    help="the Meshy prompt, for attribution provenance")
    ap.add_argument("--cell", type=int, default=CELL, help="cell size (default %d)" % CELL)
    ap.add_argument("--emit-descriptor", dest="emit_descriptor", action="store_true", default=True,
                    help="emit the viewer wiki_ingest.json descriptor into _private finals dirs so "
                         "the atlas is served via /image?scope=<scope> (default on; #1063)")
    ap.add_argument("--no-emit-descriptor", dest="emit_descriptor", action="store_false",
                    help="skip the viewer descriptor (committed/non-_private dirs skip it anyway)")
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
    _write_outputs(sheet, manifest, args.out, emit_descriptor=args.emit_descriptor)

    print("[pack_sheet] sheet dims=%s anchor=(%d,%d) missing_frames=%d" % (
        str(sheet.size), anchor["x"], anchor["y"], missing))
    expected = (TOTAL_COLS * args.cell, len(FACING_ORDER) * args.cell)
    assert sheet.size == expected, "sheet must be %s, got %s" % (expected, sheet.size)
    if missing:
        print("[pack_sheet] WARNING: %d frames were missing and filled transparent" % missing)
    print("[pack_sheet] OK")


if __name__ == "__main__":
    main()
