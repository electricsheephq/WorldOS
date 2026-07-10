#!/usr/bin/env python3
"""tiled_space_spike.py — TILED-SPACE spike harness (epic #1508 extension: the road to towns).

The plate system paints ONE dimetric room from a 3D greybox (ControlNet depth). This spike asks
whether a space LARGER than one plate can be composed from seamlessly-pieced tiles, comparing two
architectures on the SAME greybox pair (a camp clearing that opens into a forest path):

  ARM A — SLICE-ONE-BIG:   author ONE wide greybox, render ONE wide depth control, generate ONE plate
                           at the largest width flux sustains (2048px), then SLICE it into tiles.
                           Seams are perfect BY CONSTRUCTION; the cost to measure is (a) any global-
                           coherence loss vs a single-room baseline generated from the IDENTICAL tile
                           control, and (b) the detail ceiling as coverage grows past ~2 rooms.
  ARM B — EDGE-CONTINUATION: generate tile 1 normally; generate tile 2 from ITS depth control but
                           conditioned for continuity — flux img2img with an init canvas whose left
                           OVERLAP strip is tile 1's finished paint (+ tile 2's depth as controlImage).
                           Stitch with an overlap feather; measure the seam (pixel-difference profile
                           across the boundary strip + a blind VQA/panel on the stitched pair).

THE FAIRNESS TRICK: tile controls are LEFT/RIGHT CROPS of the ONE wide depth control, so an Arm-A
sliced tile and the Arm-B / baseline tiles share identical control pixels — the only variable is the
generation width/conditioning. Nothing here calls an LLM or the paint API directly: this module builds
the controls, slices/stitches the returned plates, computes the deterministic seam metrics, and emits
the evidence gallery. GENERATION is driven by extensions/renderers/godot/tools/scenario_gen.py
(flux.1-dev depth ControlNet, + img2img for Arm B) via emit_gen_commands(); the 3-scorer blind panels
are agent-work (visual-critic recipe), mirroring qa/plate_loop.py's script/agent boundary.

Engine = SOLE WRITER; this is read-only view-layer tooling (controls + metrics + evidence only).

  # 1. build controls (offline, no API)
  python3 qa/tiled_space_spike.py build --out qa/evidence/tiled-space
  # 2. run the generations it prints (scenario_gen.py flux depth ControlNet + img2img)
  # 3. compose + metrics once the plates are downloaded
  python3 qa/tiled_space_spike.py compose --out qa/evidence/tiled-space
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Camera — the SAME verified dimetric basis as greybox_render_headless.py
# (orthoSize 13, pitch 30 / yaw 45, cam pulled back 80u, isotropic 2.0u cells),
# parameterised on px width + ortho so a WIDER frame can hold more cells. Keeping
# px_h=768 & ortho=13 fixed holds the VERTICAL density identical to the native
# 1344x768 rig, so widening trades ONLY horizontal coverage (the ceiling this
# spike measures), not per-cell paint density.
# ---------------------------------------------------------------------------
CAM_DIST = 80.0
PITCH_DEG = 30.0
YAW_DEG = 45.0
CELL_WORLD = 2.0
NATIVE_PX_H = 768


def _forward() -> tuple:
    p, y = math.radians(PITCH_DEG), math.radians(YAW_DEG)
    return (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))


_FWD = _forward()
_RIGHT = (math.cos(math.radians(YAW_DEG)), 0.0, -math.sin(math.radians(YAW_DEG)))
_UP = (
    _FWD[1] * _RIGHT[2] - _FWD[2] * _RIGHT[1],
    _FWD[2] * _RIGHT[0] - _FWD[0] * _RIGHT[2],
    _FWD[0] * _RIGHT[1] - _FWD[1] * _RIGHT[0],
)
_POS = tuple(-_FWD[i] * CAM_DIST for i in range(3))


class Cam:
    """A parameterised dimetric camera (same basis, chosen px_w/px_h/ortho)."""

    def __init__(self, px_w: int, px_h: int, ortho: float):
        self.px_w, self.px_h, self.ortho = px_w, px_h, ortho
        self.aspect = px_w / px_h

    def world_to_screen(self, wx: float, wy: float, wz: float) -> tuple:
        dx, dy, dz = wx - _POS[0], wy - _POS[1], wz - _POS[2]
        cam_r = dx * _RIGHT[0] + dy * _RIGHT[1] + dz * _RIGHT[2]
        cam_u = dx * _UP[0] + dy * _UP[1] + dz * _UP[2]
        half_h, half_w = self.ortho, self.ortho * self.aspect
        sx = (cam_r / half_w) * (self.px_w / 2.0) + self.px_w / 2.0
        sy = self.px_h / 2.0 - (cam_u / half_h) * (self.px_h / 2.0)
        return sx, sy

    def cam_depth(self, wx: float, wy: float, wz: float) -> float:
        dx, dy, dz = wx - _POS[0], wy - _POS[1], wz - _POS[2]
        return dx * _FWD[0] + dy * _FWD[1] + dz * _FWD[2]


def cell_to_world(c: float, r: float, cols: int, rows: int) -> tuple:
    cx0, cy0 = (cols - 1) / 2.0, (rows - 1) / 2.0
    return ((c - cx0) * CELL_WORLD, 0.0, (cy0 - r) * CELL_WORLD)


# kind -> (height, half-width, RGB) — same table as greybox_render_headless.py.
_KIND_SPECS = [
    (("pillar", "column"), (7.5, 0.8, (143, 140, 135))),
    (("large_tree",), (9.0, 0.9, (58, 74, 52))),
    (("stone_well",), (3.2, 0.9, (150, 148, 140))),
    (("sarcophagus", "altar", "bar", "table", "pew", "market_stall", "timber_frame"), (2.0, 0.9, (153, 148, 140))),
    (("brazier",), (2.2, 0.4, (96, 92, 86))),
    (("campfire",), (0.6, 0.55, (200, 110, 40))),
    (("bedroll",), (0.28, 0.55, (110, 96, 78))),
    (("fallen_log",), (0.8, 0.55, (90, 74, 54))),
    (("boulder",), (2.0, 0.7, (110, 112, 108))),
    (("supply_crates", "cart", "merchants_cart"), (1.5, 0.7, (115, 110, 96))),
    (("stone_wall", "rubble", "barrel", "crate"), (1.4, 0.75, (115, 110, 102))),
]
_DEFAULT_SPEC = (2.6, 0.7, (133, 128, 122))


def _spec_for_kind(kind: str) -> tuple:
    k = (kind or "").lower()
    for keys, spec in _KIND_SPECS:
        if any(key in k for key in keys):
            return spec
    return _DEFAULT_SPEC


def _shade(color: tuple, factor: float) -> tuple:
    return tuple(max(0, min(255, int(ch * factor))) for ch in color)


def _encode_normal(n: tuple) -> tuple:
    return tuple(int(round((c * 0.5 + 0.5) * 255)) for c in n)


# ---------------------------------------------------------------------------
# Geometry composition — merge room geometries side-by-side into a wide grid
# ---------------------------------------------------------------------------
def _shift(geo: dict, dcol: int) -> dict:
    """Return props+walls of geo shifted by +dcol columns."""
    walls = [[int(c) + dcol, int(r)] for (c, r) in geo.get("walls", [])]
    props = []
    for p in geo.get("props", []):
        q = dict(p)
        q["cells"] = [[int(c) + dcol, int(r)] for (c, r) in p.get("cells", [])]
        props.append(q)
    return {"walls": walls, "props": props}


def _trim_seam_zone(walls: list, props: list, seam_cols: set) -> tuple:
    """Open a mouth at the join: drop wall cells and whole props that intrude into seam_cols, so the
    boundary crosses continuous open ground (a fair seam test) rather than hiding behind a tree wall."""
    walls = [[c, r] for (c, r) in walls if c not in seam_cols]
    kept = []
    for p in props:
        if any(int(c) in seam_cols for (c, r) in p.get("cells", [])):
            continue
        kept.append(p)
    return walls, kept


def compose_geometry(blocks: list, rows: int, seam_cols: Optional[set] = None) -> dict:
    """blocks = [(geo_dict, dcol), ...]. Returns a merged {cols, rows, walls, props} grid."""
    all_walls: list = []
    all_props: list = []
    max_col = 0
    for geo, dcol in blocks:
        s = _shift(geo, dcol)
        all_walls += s["walls"]
        all_props += s["props"]
        max_col = max(max_col, dcol + int(geo["cols"]))
    if seam_cols:
        all_walls, all_props = _trim_seam_zone(all_walls, all_props, seam_cols)
    return {"cols": max_col, "rows": rows, "walls": all_walls, "props": all_props}


# ---------------------------------------------------------------------------
# Rendering — flat greybox + camera-space depth, at an arbitrary camera
# ---------------------------------------------------------------------------
def _collect_items(geo: dict, wall_height: float) -> list:
    cols, rows = int(geo["cols"]), int(geo["rows"])
    prop_cells = {(int(c), int(r)) for p in geo.get("props", []) for (c, r) in p.get("cells", [])}
    items = []  # (r_sort, cx, cz, half, height, color)
    for (c, r) in geo.get("walls", []):
        if (int(c), int(r)) in prop_cells:
            continue
        wc = cell_to_world(c, r, cols, rows)
        items.append((r, wc[0], wc[2], 1.0, wall_height, (110, 108, 104)))
    for p in geo.get("props", []):
        height, half, color = _spec_for_kind(p.get("kind", "prop"))
        pcells = p.get("cells", [])
        if not pcells:
            continue
        xs = [cell_to_world(c, r, cols, rows)[0] for (c, r) in pcells]
        zs = [cell_to_world(c, r, cols, rows)[2] for (c, r) in pcells]
        cx, cz = (min(xs) + max(xs)) / 2.0, (min(zs) + max(zs)) / 2.0
        half_x = max(half, (max(xs) - min(xs)) / 2.0 + half)
        half_z = max(half, (max(zs) - min(zs)) / 2.0 + half)
        r_avg = sum(r for (_, r) in pcells) / len(pcells)
        items.append((r_avg, cx, cz, max(half_x, half_z), height, color))
    return sorted(items, key=lambda it: it[0])


def render(geo: dict, cam: Cam, wall_height: float = 9.0) -> tuple:
    """Render (greybox_rgb, depth_L) images at cam. Depth: near=bright (ControlNet convention)."""
    cols, rows = int(geo["cols"]), int(geo["rows"])
    rgb = Image.new("RGB", (cam.px_w, cam.px_h), (13, 13, 18))
    depth = Image.new("L", (cam.px_w, cam.px_h), 0)
    d_rgb, d_depth = ImageDraw.Draw(rgb), ImageDraw.Draw(depth)

    half_x = (cols / 2.0) * CELL_WORLD
    half_z = (rows / 2.0) * CELL_WORLD
    corners = [(-half_x, 0.0, -half_z), (half_x, 0.0, -half_z),
               (half_x, wall_height, half_z), (-half_x, wall_height, half_z)]
    depths = [cam.cam_depth(*c) for c in corners]
    d_near, d_far = min(depths), max(depths)
    span = (d_far - d_near) or 1.0

    def d255(wx, wy, wz):
        t = (cam.cam_depth(wx, wy, wz) - d_near) / span
        return max(0, min(255, int(round((1.0 - t) * 255))))

    floor = [(-half_x, 0.0, -half_z), (half_x, 0.0, -half_z), (half_x, 0.0, half_z), (-half_x, 0.0, half_z)]
    d_rgb.polygon([cam.world_to_screen(*p) for p in floor], fill=(58, 58, 62))
    d_depth.polygon([cam.world_to_screen(*p) for p in floor],
                    fill=int(round(sum(d255(*p) for p in floor) / 4)))

    for (_, cx, cz, half, height, color) in _collect_items(geo, wall_height):
        cb = [(cx - half, 0.0, cz - half), (cx + half, 0.0, cz - half),
              (cx + half, 0.0, cz + half), (cx - half, 0.0, cz + half)]
        ct = [(x, height, z) for (x, _, z) in cb]
        sb = [cam.world_to_screen(*p) for p in cb]
        st = [cam.world_to_screen(*p) for p in ct]
        # right face, front face, top face (the three visible toward the -x,-z camera).
        for world_pts, screen_pts, fac in (
            ([cb[1], cb[2], ct[2], ct[1]], [sb[1], sb[2], st[2], st[1]], 0.72),
            ([cb[2], cb[3], ct[3], ct[2]], [sb[2], sb[3], st[3], st[2]], 0.58),
            (ct, st, 1.0),
        ):
            d_rgb.polygon(screen_pts, fill=_shade(color, fac))
            d_depth.polygon(screen_pts, fill=int(round(sum(d255(*p) for p in world_pts) / len(world_pts))))
    return rgb, depth


# ---------------------------------------------------------------------------
# Stitching + seam metrics
# ---------------------------------------------------------------------------
def feather_stitch(left: Image.Image, right: Image.Image, overlap: int) -> Image.Image:
    """Overlap-feather two same-height tiles. `right` overlaps `left` by `overlap` px; the shared band
    is linearly cross-faded. Output width = left.w + right.w - overlap."""
    import numpy as np
    left, right = left.convert("RGB"), right.convert("RGB")
    lw, h = left.size
    rw, _ = right.size
    out_w = lw + rw - overlap
    L = np.asarray(left, dtype=np.float64)
    R = np.asarray(right, dtype=np.float64)
    out = np.zeros((h, out_w, 3), dtype=np.float64)
    out[:, :lw] = L
    out[:, lw - overlap:] = R  # right placed so its first `overlap` cols land on left's last `overlap`
    if overlap > 0:
        alpha = np.linspace(0.0, 1.0, overlap).reshape(1, overlap, 1)  # 0 at left edge -> 1 at right
        band_l = L[:, lw - overlap:lw, :]
        band_r = R[:, :overlap, :]
        out[:, lw - overlap:lw, :] = band_l * (1.0 - alpha) + band_r * alpha
    return Image.fromarray(np.clip(out, 0, 255).astype("uint8"), "RGB")


def seam_metrics(img: Image.Image, seam_x: int, band: int = 24) -> dict:
    """Deterministic seam metrics on a stitched image at screen column seam_x.

    - grad_ratio: mean |d/dx luma| in the seam band vs the whole-image mean (1.0 == seam invisible in
      gradient terms; >>1 == a hard edge sits at the seam).
    - coldiff_seam: mean per-channel |Δ| across the exact seam column pair (0 == continuous).
    - coldiff_ref: same measure averaged over random interior column pairs (the natural texture floor).
    - seam_excess: coldiff_seam / coldiff_ref (1.0 == seam indistinguishable from ordinary texture)."""
    import numpy as np
    a = np.asarray(img.convert("RGB"), dtype=np.float64)
    h, w, _ = a.shape
    luma = a @ np.array([0.299, 0.587, 0.114])
    gx = np.abs(np.diff(luma, axis=1))  # (h, w-1)
    x0, x1 = max(1, seam_x - band), min(w - 1, seam_x + band)
    grad_seam = float(gx[:, x0:x1].mean())
    grad_all = float(gx.mean()) or 1e-6
    # exact adjacent-column colour discontinuity at the seam
    sx = min(max(seam_x, 1), w - 1)
    coldiff_seam = float(np.abs(a[:, sx, :] - a[:, sx - 1, :]).mean())
    rng = np.random.default_rng(1508)
    cols = rng.integers(1, w - 1, size=64)
    coldiff_ref = float(np.mean([np.abs(a[:, c, :] - a[:, c - 1, :]).mean() for c in cols])) or 1e-6
    return {
        "seam_x": int(seam_x), "band": band,
        "grad_ratio": round(grad_seam / grad_all, 3),
        "coldiff_seam": round(coldiff_seam, 3),
        "coldiff_ref": round(coldiff_ref, 3),
        "seam_excess": round(coldiff_seam / coldiff_ref, 3),
    }


def seam_closeup(img: Image.Image, seam_x: int, half_w: int = 96) -> Image.Image:
    """A tall crop centred on the seam for the evidence close-up."""
    w, h = img.size
    x0 = max(0, seam_x - half_w)
    x1 = min(w, seam_x + half_w)
    return img.crop((x0, 0, x1, h))


# ---------------------------------------------------------------------------
# Evidence gallery (self-contained, base64-embedded — mirrors plate_loop.py)
# ---------------------------------------------------------------------------
def _data_uri(img_or_path, max_px: int = 1400) -> Optional[str]:
    import base64
    import io
    try:
        im = img_or_path if isinstance(img_or_path, Image.Image) else Image.open(img_or_path)
        im = im.convert("RGB")
        if max_px and max(im.size) > max_px:
            im = im.copy()
            im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=86)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def render_gallery(sections: list, title: str = "TILED-SPACE spike — seam evidence") -> str:
    def block(sec):
        imgs = "".join(
            f'<figure><img src="{_data_uri(p)}" alt="{n}"><figcaption>{n}</figcaption></figure>'
            for (n, p) in sec.get("images", []) if _data_uri(p))
        meta = ""
        if sec.get("metrics"):
            meta = "<pre>" + json.dumps(sec["metrics"], indent=2) + "</pre>"
        return (f'<section><h2>{sec["name"]}</h2>'
                f'<p class="note">{sec.get("note", "")}</p>{meta}<div class="row">{imgs}</div></section>')
    body = "\n".join(block(s) for s in sections)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ background:#14141a; color:#e8e6e0; font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; margin:0; padding:24px; }}
 h1 {{ font-size:22px; }} h2 {{ font-size:17px; margin:22px 0 6px; }}
 .note {{ color:#9a978f; margin:2px 0 10px; }}
 .row {{ display:flex; flex-wrap:wrap; gap:12px; align-items:flex-start; }}
 figure {{ margin:0; background:#1d1d25; border:1px solid #2c2c38; border-radius:8px; padding:6px; }}
 figure img {{ display:block; max-width:640px; height:auto; border-radius:4px; }}
 figcaption {{ color:#b9b6ae; font-size:12px; margin-top:4px; font-family:ui-monospace,Menlo,monospace; }}
 pre {{ background:#0c0c10; border:1px solid #2c2c38; border-radius:6px; padding:10px; overflow:auto; color:#c9d8c9; font-size:12px; }}
</style></head><body>
<h1>{title}</h1>
<p class="note">Same greybox pair (camp clearing → forest path). ARM A slices one wide plate;
ARM B stitches edge-continued tiles. Lower <code>seam_excess</code> / <code>grad_ratio</code> ≈ 1.0 is
an invisible seam.</p>
{body}
</body></html>"""


# ---------------------------------------------------------------------------
# Scene config + orchestration
# ---------------------------------------------------------------------------
_QA = Path(__file__).resolve().parent
CAMP_GEO = _QA / "evidence/true-greybox/camp/camp_geometry.json"
FOREST_GEO = _QA / "evidence/plate-sprint/forest-road/forest_road_geometry.json"
_SCENARIO_GEN = _QA.parent / "extensions/renderers/godot/tools/scenario_gen.py"
_FLUX = "model_bfl-flux-1-dev"

WIDE_W, TILE_W, PX_H = 2048, 1024, 768
OVERLAP = 160          # Arm B overlap-strip width (px) shared between tiles
ROWS = 12
STYLE = ("moody hand-painted painterly environment art, Pillars of Eternity Deadfire isometric "
         "backdrop, dramatic chiaroscuro, warm firelight vs cool teal shadow, dark earthy palette, "
         "dimetric bird's-eye view, no characters, no text")
PROMPTS = {
    "wide2": "a forsaken war camp clearing at dusk opening eastward into a dark pine forest path, " + STYLE,
    "wide3": "a war camp clearing opening into a long dark pine forest path receding into deep woods, " + STYLE,
    "camp":  "a forsaken war camp clearing at dusk, campfire embers, bedrolls, supply crates, timber "
             "palisade, trampled earth, " + STYLE,
    "forest": "a dark pine forest path continuing from a camp clearing, mossy boulders, fallen logs, "
              "dense conifers flanking a trodden dirt trail, " + STYLE,
}


def _blocks_wide2():
    camp = json.loads(CAMP_GEO.read_text())
    forest = json.loads(FOREST_GEO.read_text())
    # camp cols 0-15, forest cols 16-31; open the mouth at the join (cols 14-19) for a fair seam.
    geo = compose_geometry([(camp, 0), (forest, 16)], ROWS, seam_cols={14, 15, 16, 17, 18, 19})
    return geo


def _blocks_wide3():
    camp = json.loads(CAMP_GEO.read_text())
    forest = json.loads(FOREST_GEO.read_text())
    geo = compose_geometry([(camp, 0), (forest, 16), (forest, 32)], ROWS,
                           seam_cols={14, 15, 16, 17, 18, 19, 30, 31, 32, 33, 34, 35})
    return geo


def cmd_build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    ctl = out / "controls"
    ctl.mkdir(exist_ok=True)

    # --- ARM A wide 2-room (native density: 2048/32 = 64 px/col == the single-room 1024/16) ---
    geo2 = _blocks_wide2()
    cam2 = Cam(WIDE_W, PX_H, ortho=13.0)
    rgb2, depth2 = render(geo2, cam2)
    rgb2.save(ctl / "wide2_greybox.png")
    depth2.save(ctl / "wide2_depth.png")
    # tile controls = LEFT/RIGHT crops of the SAME wide depth (the fairness trick)
    depth2.crop((0, 0, TILE_W, PX_H)).save(ctl / "tileL_depth.png")
    depth2.crop((WIDE_W - TILE_W, 0, WIDE_W, PX_H)).save(ctl / "tileR_depth.png")
    rgb2.crop((WIDE_W - TILE_W, 0, WIDE_W, PX_H)).save(ctl / "tileR_greybox.png")

    # --- ARM A escalation: 3-room squeezed into 2048 (2048/48 = 42.7 px/col ≈ 0.67x density) ---
    geo3 = _blocks_wide3()
    cam3 = Cam(WIDE_W, PX_H, ortho=19.5)
    rgb3, depth3 = render(geo3, cam3)
    rgb3.save(ctl / "wide3_greybox.png")
    depth3.save(ctl / "wide3_depth.png")

    meta = {
        "wide2": {"cols": geo2["cols"], "px": [WIDE_W, PX_H], "ortho": 13.0, "px_per_col": WIDE_W / geo2["cols"]},
        "wide3": {"cols": geo3["cols"], "px": [WIDE_W, PX_H], "ortho": 19.5, "px_per_col": WIDE_W / geo3["cols"]},
        "tile": {"px": [TILE_W, PX_H], "px_per_col": TILE_W / 16},
        "overlap_px": OVERLAP, "seam_x_wide2": WIDE_W // 2,
    }
    (out / "build_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"[build] controls -> {ctl}")
    print(json.dumps(meta, indent=2))
    print("\n" + emit_gen_commands(out, stage=1))


def cmd_initb(out: Path) -> None:
    """After tile1 (plates/tile1.png) exists: assemble Arm B's tile-2 img2img INIT canvas — tile1's
    right OVERLAP strip of finished PAINT on the left, tile2's greybox on the rest — so flux img2img
    continues tile1's brushwork across the seam while controlImage=tileR_depth locks tile2 structure."""
    ctl, plates = out / "controls", out / "plates"
    tile1 = Image.open(plates / "tile1.png").convert("RGB").resize((TILE_W, PX_H))
    grey = Image.open(ctl / "tileR_greybox.png").convert("RGB").resize((TILE_W, PX_H))
    init = grey.copy()
    strip = tile1.crop((TILE_W - OVERLAP, 0, TILE_W, PX_H))  # tile1's rightmost OVERLAP px of paint
    init.paste(strip, (0, 0))                                 # -> tile2 init's left edge
    init.save(ctl / "tile2_init.png")
    print(f"[initb] tile2 img2img init -> {ctl / 'tile2_init.png'}")
    print("\n" + emit_gen_commands(out, stage=2))


def cmd_compose(out: Path) -> None:
    plates, comp = out / "plates", out / "composites"
    comp.mkdir(parents=True, exist_ok=True)
    sections = []

    # ---- ARM A: slice the one wide plate ----
    wide = Image.open(plates / "wideA.png").convert("RGB").resize((WIDE_W, PX_H))
    seam_x = WIDE_W // 2
    a_left = wide.crop((0, 0, TILE_W, PX_H))
    a_right = wide.crop((TILE_W, 0, WIDE_W, PX_H))
    a_left.save(comp / "armA_tileL.png")
    a_right.save(comp / "armA_tileR.png")
    wide.save(comp / "armA_stitched.png")
    m_a = seam_metrics(wide, seam_x)
    seam_closeup(wide, seam_x).save(comp / "armA_seam_closeup.png")
    imgsA = [("armA_stitched (one plate, sliced)", comp / "armA_stitched.png"),
             ("armA_seam_closeup", comp / "armA_seam_closeup.png")]
    if (plates / "baseline.png").is_file():
        imgsA.append(("baseline single-room (same tileL control)", plates / "baseline.png"))
    sections.append({"name": "ARM A — SLICE-ONE-BIG", "images": imgsA, "metrics": m_a,
                     "note": "One 2048px plate sliced at x=1024. Seam perfect by construction; "
                             "seam_excess≈1.0 expected. Baseline shares tileL's control — compare "
                             "global coherence."})

    # ---- ARM B: stitch edge-continued tiles ----
    tile1 = Image.open(plates / "tile1.png").convert("RGB").resize((TILE_W, PX_H))
    tile2 = Image.open(plates / "tile2_cont.png").convert("RGB").resize((TILE_W, PX_H))
    stitched_b = feather_stitch(tile1, tile2, OVERLAP)
    seam_xb = TILE_W - OVERLAP // 2
    stitched_b.save(comp / "armB_stitched.png")
    seam_closeup(stitched_b, seam_xb).save(comp / "armB_seam_closeup.png")
    m_b = seam_metrics(stitched_b, seam_xb)
    imgsB = [("armB_stitched (edge-continued + feather)", comp / "armB_stitched.png"),
             ("armB_seam_closeup", comp / "armB_seam_closeup.png")]
    sections.append({"name": "ARM B — EDGE-CONTINUATION", "images": imgsB, "metrics": m_b,
                     "note": "tile2 = flux img2img on tile1's overlap strip + tileR depth control, "
                             "feather-blended. seam_excess→1.0 = invisible."})

    # ---- ARM B negative control: naive butt-join (no conditioning, no feather) ----
    if (plates / "tile2_naive.png").is_file():
        t2n = Image.open(plates / "tile2_naive.png").convert("RGB").resize((TILE_W, PX_H))
        naive = feather_stitch(tile1, t2n, 0)  # hard butt-join
        naive.save(comp / "armB_naive_stitched.png")
        seam_closeup(naive, TILE_W).save(comp / "armB_naive_seam_closeup.png")
        m_n = seam_metrics(naive, TILE_W)
        sections.append({"name": "ARM B negative control — NAIVE butt-join (no conditioning)",
                         "images": [("naive_stitched", comp / "armB_naive_stitched.png"),
                                    ("naive_seam_closeup", comp / "armB_naive_seam_closeup.png")],
                         "metrics": m_n,
                         "note": "Independent tiles, hard join — the seam the metric must catch."})

    # ---- Escalation (detail ceiling) ----
    if (plates / "wide3.png").is_file():
        Image.open(plates / "wide3.png").convert("RGB").save(comp / "armA_escalation_3room.png")
        sections.append({"name": "ARM A escalation — 3 rooms @ 0.67× density",
                         "images": [("wide3 (48 cols in 2048px)", comp / "armA_escalation_3room.png")],
                         "note": "2048/48 = 42.7 px/col vs 64 native. Panel scores the detail cost."})

    metrics = {"armA": m_a, "armB": m_b}
    if (plates / "tile2_naive.png").is_file():
        metrics["armB_naive"] = m_n
    (out / "seam_metrics.json").write_text(json.dumps(metrics, indent=2))
    (out / "gallery.html").write_text(render_gallery(sections))
    print(f"[compose] gallery -> {out / 'gallery.html'}")
    print(json.dumps(metrics, indent=2))


def emit_gen_commands(out: Path, stage: int) -> str:
    ctl, plates = out / "controls", out / "plates"
    py = "python3"
    sg = str(_SCENARIO_GEN)

    def cn(control, prompt_key, w, scope, extra=""):
        return (f"{py} {sg} controlnet --model-id {_FLUX} --control-modality depth "
                f"--control-strength 0.72 --num-samples 1 --width {w} --height {PX_H} --seed 1508 "
                f"--control-image {control} --out {plates} --scope {scope} "
                f'--prompt "{PROMPTS[prompt_key]}"{extra}')

    if stage == 1:
        lines = [
            "── STAGE 1 GENERATION (flux depth ControlNet) — download each into plates/ then rename ──",
            "# ARM A wide 2-room (slice-one-big):",
            "  " + cn(ctl / "wide2_depth.png", "wide2", WIDE_W, "wideA") + "   # -> plates/wideA.png",
            "# ARM A escalation 3-room (detail ceiling):",
            "  " + cn(ctl / "wide3_depth.png", "wide3", WIDE_W, "wide3") + "   # -> plates/wide3.png",
            "# Baseline single-room + ARM B tile1 (SAME tileL control):",
            "  " + cn(ctl / "tileL_depth.png", "camp", TILE_W, "tile1") + "   # -> plates/tile1.png (copy to baseline.png)",
            "# ARM B negative control — naive tile2 (no conditioning):",
            "  " + cn(ctl / "tileR_depth.png", "forest", TILE_W, "tile2_naive") + "   # -> plates/tile2_naive.png",
            "",
            "Then: python3 qa/tiled_space_spike.py initb --out " + str(out),
        ]
        return "\n".join(lines)
    # stage 2: Arm B edge-continued tile2 = img2img(init) + depth control
    b = (f"{py} {sg} controlnet --model-id {_FLUX} --control-modality depth --control-strength 0.72 "
         f"--num-samples 1 --width {TILE_W} --height {PX_H} --seed 1508 "
         f"--control-image {ctl / 'tileR_depth.png'} --init-image {ctl / 'tile2_init.png'} "
         f"--strength 0.68 --out {plates} --scope tile2_cont "
         f'--prompt "{PROMPTS["forest"]}"   # -> plates/tile2_cont.png')
    return "\n".join([
        "── STAGE 2 GENERATION (ARM B edge-continuation: img2img + depth control) ──",
        "  " + b,
        "",
        "Then: python3 qa/tiled_space_spike.py compose --out " + str(out),
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["build", "initb", "compose"])
    ap.add_argument("--out", required=True, help="evidence output dir")
    args = ap.parse_args(argv)
    out = Path(args.out)
    {"build": cmd_build, "initb": cmd_initb, "compose": cmd_compose}[args.cmd](out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
