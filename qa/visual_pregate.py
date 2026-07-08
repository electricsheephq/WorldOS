#!/usr/bin/env python3
"""Deterministic, cheap PRE-GATES for the visual-critic loop — catch what the eye (and an
LLM critic) misses, BEFORE spending an LLM call. The visual analogue of the engine's
deterministic behavioral gate: numbers, not vibes.

WHY THIS EXISTS
---------------
The visual loop plateaued at 4/10 partly because the LLM critic scored *holistically* and
*softly* — "actors read pasted on" is a real defect but it never became a NUMBER the loop
could gate on or watch for regression. This module turns three illusion-breakers into hard
deterministic checks that run in <1s with stdlib (+ optional Pillow / NumPy):

  G1  FRAME-LIT      — mean luminance + variance: is the frame actually rendered, not black /
                       blown-out / flat? (guards the URP-decal / -batchmode black-render bug.)
  G2  OCCUPANCY      — when a SceneGrid + a per-cell render tint is available: does the rendered
                       walkable/blocked tint match the engine ground-truth mask? (tactical-readability,
                       numerically.)
  G3  FLOOR-CONTACT  — each actor's rendered screen-feet-Y vs the projected floor-plane Y at its
                       cell: feet floating above / clipping below the floor = CRITICAL. (grounding,
                       the #1 "pasted-on" tell.)
  G4  SCREEN-SCALE   — each actor's rendered pixel-height vs the spec-expected height at its cell
                       depth: too big/small = a pasted-on scale break. (cohesion, numerically.)
  G6  LUMA-STAGING   — greyscale histogram stats (near-black / lit fractions + median L) vs the
                       measured PoE staging-law bands: is the plate a dramatic chiaroscuro (small
                       hot pools of light in a mostly-dark scene), or an evenly-lit "museum wash"?
                       (staging, numerically — runs BEFORE the panel so scorer tokens are never
                       spent on a mis-staged candidate.)

A pre-gate is CRITICAL/HIGH/MED with an objective delta. A CRITICAL pre-gate SHORT-CIRCUITS the
LLM panel: there is no point asking five subagents to admire brushwork when the hero's feet float
0.4 floor-cells above the stone. Fix the deterministic defect first, re-render, then critique.

DEPENDENCIES (graceful degradation — never a hard import error):
  - G1 needs pixels: tries Pillow, else falls back to a stdlib PNG decode of a downscaled grid.
  - G2/G3/G4 are pure geometry/math on the SceneGrid + camera + measured actor boxes; stdlib only.
    The actor pixel boxes are supplied by the caller (the Unity side emits them, or an AUDIT-mode
    segmentation pass produces them); this module does NOT do CV detection — it does the MATH that
    turns measured boxes + the spec into a pass/fail with a numeric delta.

CAMERA / PROJECTION (the locked dimetric registration authority — must match the PROVEN Unity combat
  renderer, i.e. extensions/renderers/unity/scripts/paint_combat_v1.cs + paint_3d_spike.cs):
  orthographic, orthoSize=13, aspect=1920/1097 (~1.75), pitch(x)=30deg, yaw(y)=45deg corner-iso,
  roll=0, position = -(Euler(30,45,0)*forward)*80 = (-48.99, 40.0, -48.99) — the camera is pulled
  back 80 world units along its own forward axis and LOOKS AT the world origin; near 0.3 / far 500.
  Grid 14x11; cellToWorld(c,r) = ((c-6.5)*2.0, 0, (5.0-r)*2.0), cell_size 2.0. World->screen below
  is derived from exactly this. If the Unity camera changes, update CameraSpec (one place) — the
  registration stays single-authority.

USAGE
-----
    from visual_pregate import run_pregates, CameraSpec, load_scenegrid
    spec   = load_scenegrid("fixtures/tavern.scenegrid.json")
    result = run_pregates(
        render_png="/tmp/frame.png",
        scenegrid=spec,
        camera=CameraSpec.LOCKED,           # the locked dimetric camera (default)
        actors=[                            # measured rendered boxes (px), from the render side
            {"id":"hero","cell":[7,8],"feet_px":[672,690],"px_height":214,"head_px":[672,476]},
            {"id":"goblin","cell":[6,2],"feet_px":[300,250],"px_height":150,"head_px":[300,160]},
        ],
        occupancy_tint=None,                # optional {"[c,r]": "walkable"|"blocked"} sampled from render
    )
    print(result["verdict"])                # PASS | FLAG (any CRITICAL/HIGH) | SKIPPED
    # result["gates"] -> list of {gate, severity, metric, value, threshold, detail}

    # Spec-facing CLI (future capture harness):
    python qa/visual_pregate.py /tmp/frame.png /tmp/manifest.json \
        --baseline /tmp/empty-plate.png --json-out /tmp/report.json

    # Legacy visual-critic CLI:
    python qa/visual_pregate.py --render /tmp/frame.png --scenegrid fixtures/tavern.scenegrid.json \
        --actors @/tmp/actors.json --json

Exit codes: spec CLI 0 = PASS, 2 = FAIL; legacy CLI 0 = PASS / SKIPPED, 2 = FLAG
(a CRITICAL or HIGH pre-gate fired) — so the loop / CI can gate.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Tunable thresholds (the noise floor for the deterministic gates). Conservative on purpose:
# a pre-gate should fire only on a DEFINITE defect, never on antialiasing jitter.
# ---------------------------------------------------------------------------
# G1 frame-lit
MEAN_LUM_DARK = 0.06          # mean luminance below this => effectively black render (CRITICAL)
MEAN_LUM_BLOWN = 0.97         # mean luminance above this => blown-out white (CRITICAL)
LUM_VARIANCE_FLAT = 0.0015    # luminance variance below this => flat / no content (HIGH)
# G3 floor-contact (in FLOOR-CELL units along the projected ground plane)
FLOAT_CELL_HIGH = 0.20        # feet this many cells ABOVE the floor plane => HIGH (visibly floating)
FLOAT_CELL_CRIT = 0.45        # ... this far => CRITICAL (the classic "pasted-on" hover)
CLIP_CELL_HIGH = 0.20         # feet this many cells BELOW (sunk into) the floor => HIGH
CLIP_CELL_CRIT = 0.45
# G4 screen-scale (relative error vs spec-expected pixel height at that cell's depth)
SCALE_REL_MED = 0.18          # |measured-expected|/expected over this => MED (scale break starts to read)
SCALE_REL_HIGH = 0.32         # ... over this => HIGH (clearly a different scale than the world)
# G2 occupancy
OCC_MISMATCH_MED = 0.10       # >10% of cells whose rendered tint disagrees with the mask => MED
OCC_MISMATCH_HIGH = 0.22      # >22% => HIGH (tactical space is unreadable)
# G6 luma staging-law (Rec.709 luma L on a 0-255 greyscale; L<26 = near-black, L>60 = lit).
# Bands are the MEASURED real-PoE staging law (2026-07-01 staging-law campaign; see
# extensions/renderers/unity/scripts/atelier_luma_gate.py and generate_room.py's
# _staging_law_distance — this module must not invent new bands, only cite/apply them).
# near_black_frac: PASS 0.66-0.85, WARN 0.50-0.66 (below 0.50 or above 0.85 => FAIL).
# lit_frac:        PASS 0.02-0.05, WARN 0.05-0.20 (above 0.20 or below 0.02 => FAIL).
# median_L:        PASS 0-15,      WARN 15-40      (above 40 => FAIL; a wash, not chiaroscuro).
LUMA_NEAR_BLACK_L = 26         # Rec.709 luma threshold for "near-black" (matches atelier_luma_gate.py)
LUMA_LIT_L = 60                # Rec.709 luma threshold for "lit"        (matches atelier_luma_gate.py)
LUMA_NEAR_BLACK_PASS = (0.66, 0.85)
LUMA_NEAR_BLACK_WARN = (0.50, 0.66)
LUMA_LIT_PASS = (0.02, 0.05)
LUMA_LIT_WARN = (0.05, 0.20)
LUMA_MEDIAN_PASS = (0, 15)
LUMA_MEDIAN_WARN = (15, 40)
# G5 motion-liveness (objective inter-frame deltas over a render REEL; 0..1 normalized luminance).
# A FROZEN idle (no inter-frame change across the idle frames) is the #1 "static billboard, not a
# living 3D actor" tell — now a number. A MOVE that produces no walk-centroid displacement is an
# actor sliding/teleporting without locomotion.
FROZEN_IDLE_DELTA = 0.0005    # mean abs inter-frame luminance delta below this over the idle frames
                              # => CRITICAL "frozen idle" (the reel is static; nothing is animating)
MOVE_CENTROID_MIN_PX = 2.0    # a beat tagged as a MOVE must shift the bright-mass centroid at least
                              # this many px between its frames; below => HIGH "no locomotion displacement"


# ---------------------------------------------------------------------------
# Camera — the locked dimetric registration authority (mirror of the PROVEN Unity combat renderer:
# extensions/renderers/unity/scripts/paint_combat_v1.cs + paint_3d_spike.cs).
#
# Unity contract (paint_combat_v1.cs lines 11-12):
#     cam.orthographic=true; cam.orthographicSize=13f; nearClip=0.3; farClip=500;
#     Quaternion _crot=Quaternion.Euler(30f,45f,0f);
#     cam.transform.rotation=_crot; cam.transform.position=-(_crot*Vector3.forward)*80f;
# i.e. pitch(x)=30deg, yaw(y)=45deg, the camera pulled back DIST=80 world units along its own
# forward axis so it LOOKS AT the world origin. Capture is 1920x1097 (aspect ~1.75).
#
# The world_to_screen basis (fwd/right/up) below is derived analytically from pitch+yaw and was
# verified to match Unity's Quaternion.Euler(30,45,0) transform basis to <1e-3 (fwd=(0.612,-0.5,
# 0.612), right=(0.707,0,-0.707), up=(0.354,0.866,0.354)). pos is the same -(fwd)*DIST pullback.
# ---------------------------------------------------------------------------
CAM_DIST = 80.0   # world units the camera is pulled back along -forward (renderer: *80f)


@dataclass(frozen=True)
class CameraSpec:
    ortho_size: float = 13.0
    aspect: float = 1920.0 / 1097.0
    pitch_deg: float = 30.0   # Unity Euler x — elevation; asin(0.5)=30deg true 2:1 screen foreshortening
    yaw_deg: float = 45.0     # Unity Euler y — corner-iso azimuth
    px_w: int = 1920
    px_h: int = 1097
    # pos defaults to None -> derived in __post_init__ as -(forward)*CAM_DIST (the renderer's pullback,
    # camera looking at the world origin). Pass an explicit pos only to model a non-origin-looking rig.
    pos: Optional[tuple[float, float, float]] = None

    def __post_init__(self):
        if self.pos is None:
            fwd = self._forward()
            # -(rot*forward)*DIST: pull the camera back along -forward so it looks at world origin.
            object.__setattr__(self, "pos", tuple(-fwd[i] * CAM_DIST for i in range(3)))

    def _forward(self) -> tuple[float, float, float]:
        """Camera forward (into the scene) from pitch (about X) + yaw (about Y). Matches Unity's
        Quaternion.Euler(pitch, yaw, 0) * Vector3.forward to <1e-3 (verified)."""
        p = math.radians(self.pitch_deg)
        y = math.radians(self.yaw_deg)
        return (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))

    def world_to_screen(self, wx: float, wy: float, wz: float) -> tuple[float, float]:
        """Project a world point to pixel coords under the locked ortho dimetric camera.
        Screen origin top-left, +y DOWN (image convention). Pure ortho => linear, no perspective."""
        # Camera basis from pitch (about X) + yaw (about Y). yaw=45 in the locked rig (corner-iso).
        p = math.radians(self.pitch_deg)
        y = math.radians(self.yaw_deg)
        # Forward (into scene), looking down by pitch + around by yaw:
        fwd = self._forward()
        right = (math.cos(y), 0.0, -math.sin(y))
        # Up = fwd x right  (matches Unity's camera basis: verified against Unity
        # WorldToViewportPoint ground truth — the old right x fwd negated up, which
        # FLIPPED the depth->screen-Y axis so far cells projected below near cells).
        up = (
            fwd[1] * right[2] - fwd[2] * right[1],
            fwd[2] * right[0] - fwd[0] * right[2],
            fwd[0] * right[1] - fwd[1] * right[0],
        )
        dx, dy, dz = wx - self.pos[0], wy - self.pos[1], wz - self.pos[2]
        cam_r = dx * right[0] + dy * right[1] + dz * right[2]
        cam_u = dx * up[0] + dy * up[1] + dz * up[2]
        # Ortho mapping: vertical half-extent = ortho_size, horizontal = ortho_size*aspect.
        half_h = self.ortho_size
        half_w = self.ortho_size * self.aspect
        sx = (cam_r / half_w) * (self.px_w / 2.0) + self.px_w / 2.0
        sy = self.px_h / 2.0 - (cam_u / half_h) * (self.px_h / 2.0)   # +cam_u -> up -> smaller sy
        return sx, sy

    def floor_px_per_cell_y(self, scenegrid: "SceneGrid") -> float:
        """Vertical screen pixels spanned by one floor cell of depth, near frame center.
        Used to convert a floor-Y screen delta into floor-CELL units (the G3 unit)."""
        # Two adjacent floor cells along +Z (depth) at the same X:
        wz0 = scenegrid.cell_world_z(scenegrid.rows // 2)
        wz1 = scenegrid.cell_world_z(scenegrid.rows // 2 + 1)
        wx = scenegrid.cell_world_x(scenegrid.cols // 2)
        _, sy0 = self.world_to_screen(wx, 0.0, wz0)
        _, sy1 = self.world_to_screen(wx, 0.0, wz1)
        return abs(sy1 - sy0) or 1.0


CameraSpec.LOCKED = CameraSpec()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# SceneGrid — the engine ground-truth (mirror of fixtures/*.scenegrid.json)
# ---------------------------------------------------------------------------
@dataclass
class SceneGrid:
    cols: int
    rows: int
    cell_size_ft: float
    cells: dict[tuple[int, int], dict]   # (c,r) -> cell dict (only non-default cells)
    props: list[dict]
    spawns: dict
    lighting: dict
    cell_default: dict = field(default_factory=lambda: {"type": "floor", "walkable": True})
    scene_id: str = ""
    kind: str = ""

    # --- world-space cell centers. Convention: the painted floor lays the back wall (row 0) at
    # the TOP of the frame; ClosedLoopBuilder row-flips on the DISPLAY side via CellZ(). We mirror
    # that here so screen-space math matches the rendered frame. Cells are cell_size_ft on a side. ---
    def cell_world_x(self, c: int) -> float:
        # Mirror of ClosedLoopBuilder.CellX: OriginX = -(cols*cell)/2, X = OriginX + (c+0.5)*cell.
        origin_x = -(self.cols * self.cell_size_ft) / 2.0
        return origin_x + (c + 0.5) * self.cell_size_ft

    def cell_world_z(self, r: int) -> float:
        # Mirror of ClosedLoopBuilder.CellZ EXACTLY: OriginZ = 0, the painted back wall (row 0)
        # sits at the FAR Z (top of frame), the entrance (rows-1) at NEAR Z (bottom). The room
        # spans world z 0..rows*cell, NOT centered at origin — this is what Unity actually renders
        # and what the plate registers to (verified against Unity WorldToViewportPoint ground truth).
        return (self.rows - r - 0.5) * self.cell_size_ft

    def is_walkable(self, c: int, r: int) -> bool:
        cell = self.cells.get((c, r))
        if cell is None:
            return bool(self.cell_default.get("walkable", True))
        return bool(cell.get("walkable", True))

    def cell_kind(self, c: int, r: int) -> str:
        cell = self.cells.get((c, r))
        return (cell or self.cell_default).get("type", "floor")


def load_scenegrid(path: str | Path) -> SceneGrid:
    raw = json.loads(Path(path).read_text())
    g = raw["grid"]
    cells = {(int(cc["c"]), int(cc["r"])): cc for cc in raw.get("cells", [])}
    return SceneGrid(
        cols=int(g["cols"]),
        rows=int(g["rows"]),
        cell_size_ft=float(g.get("cell_size_ft", 5)),
        cells=cells,
        props=raw.get("props", []),
        spawns=raw.get("spawns", {}),
        lighting=raw.get("lighting", {}),
        cell_default=raw.get("cell_default", {"type": "floor", "walkable": True}),
        scene_id=raw.get("scene_id", ""),
        kind=raw.get("kind", ""),
    )


# ---------------------------------------------------------------------------
# G1 frame-lit — luminance mean + variance from the PNG.
# ---------------------------------------------------------------------------
def _lum_stats(png_path: str | Path) -> Optional[tuple[float, float]]:
    """Return (mean_luminance, variance) in 0..1, or None if we can't decode. Tries Pillow
    (fast, downscaled), then a stdlib PNG decode of a coarse pixel sample."""
    p = Path(png_path)
    if not p.exists():
        return None
    try:
        from PIL import Image  # type: ignore
        im = Image.open(p).convert("L")
        im.thumbnail((128, 128))
        px = list(im.tobytes())
        n = len(px) or 1
        vals = [v / 255.0 for v in px]
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        return mean, var
    except Exception:
        pass
    # stdlib fallback: decode PNG, sample a coarse grid of luminance.
    try:
        return _stdlib_png_lum(p)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Shared stdlib PNG decoder — chunk-parse + scanline-unfilter, used by every stdlib fallback
# below (G1 _stdlib_png_lum, G5 _stdlib_png_lum_grid, G6 _stdlib_png_luma_stats). Previously each
# had its own copy of this logic (triplicated); this is the single decode, callers each do their
# own (cheap) luminance reduction / sampling on top of the returned raw pixel buffer.
# ---------------------------------------------------------------------------
def _decode_png_pixels(p: Path) -> tuple[bytearray, int, int, int]:
    """Minimal PNG decoder: 8-bit greyscale/RGB/RGBA (color_type 0, 2, 6), no interlace, no palette.
    Returns (pixels, width, height, channels) where ``pixels`` is the unfiltered scanline buffer
    (row-major, ``channels`` bytes/pixel — 1 for greyscale, 3 for RGB, 4 for RGBA) and every caller
    reduces it to luminance itself (greyscale: use the channel directly; RGB/RGBA: Rec.709 weights).
    stdlib only (zlib + struct). Raises ValueError for anything unsupported (bit depth != 8,
    interlaced, or palette-indexed color_type 3 — palette needs a PLTE lookup this decoder doesn't
    do; Pillow handles those cases when installed)."""
    data = p.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    off = 8
    width = height = bit_depth = color_type = 0
    idat = bytearray()
    while off < len(data):
        ln = struct.unpack(">I", data[off:off + 4])[0]
        ctype = data[off + 4:off + 8]
        body = data[off + 8:off + 8 + ln]
        if ctype == b"IHDR":
            width, height, bit_depth, color_type = (*struct.unpack(">IIBB", body[:10]),)
        elif ctype == b"IDAT":
            idat += body
        elif ctype == b"IEND":
            break
        off += 12 + ln
    if bit_depth != 8 or color_type not in (0, 2, 6):
        raise ValueError(f"unsupported PNG bit_depth={bit_depth} color_type={color_type}")
    channels = {0: 1, 2: 3, 6: 4}[color_type]
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    # Un-filter scanlines (PNG filter types 0-4).
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            x = line[i]
            if ftype == 1:
                line[i] = (x + a) & 0xFF
            elif ftype == 2:
                line[i] = (x + b) & 0xFF
            elif ftype == 3:
                line[i] = (x + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (x + pr) & 0xFF
        out += line
        prev = line
    return out, width, height, channels


def _pixel_luma(pixels: bytearray, base: int, channels: int) -> float:
    """Rec.709 luma (0-255) of the pixel at byte offset ``base``. Greyscale (channels==1) is
    already luma; RGB/RGBA (channels 3/4) use the standard weights (alpha ignored, matches G1/G5/G6
    pre-refactor behavior which also dropped alpha)."""
    if channels == 1:
        return float(pixels[base])
    return 0.2126 * pixels[base] + 0.7152 * pixels[base + 1] + 0.0722 * pixels[base + 2]


def _stdlib_png_lum(p: Path) -> tuple[float, float]:
    """Coarse luminance mean+variance via the shared decoder. stdlib only."""
    out, width, height, channels = _decode_png_pixels(p)
    stride = width * channels
    # Coarse luminance sample (every Nth pixel to stay cheap).
    step = max(1, (width * height) // 16384)
    vals = []
    for i in range(0, width * height, step):
        base = (i // width) * stride + (i % width) * channels
        vals.append(_pixel_luma(out, base, channels) / 255.0)
    n = len(vals) or 1
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    return mean, var


def gate_frame_lit(png_path: str | Path) -> list[dict]:
    stats = _lum_stats(png_path)
    if stats is None:
        return [{"gate": "G1_frame_lit", "severity": "SKIPPED", "metric": "decode",
                 "value": None, "threshold": None,
                 "detail": "could not decode PNG (no Pillow + non-trivial PNG); G1 skipped"}]
    mean, var = stats
    gates: list[dict] = []
    if mean < MEAN_LUM_DARK:
        gates.append({"gate": "G1_frame_lit", "severity": "CRITICAL", "metric": "mean_luminance",
                      "value": round(mean, 4), "threshold": MEAN_LUM_DARK,
                      "detail": f"frame is effectively black (mean lum {mean:.3f} < {MEAN_LUM_DARK}); "
                                "URP-decal / -batchmode / missing-plate black render — fix BEFORE any LLM scoring"})
    elif mean > MEAN_LUM_BLOWN:
        gates.append({"gate": "G1_frame_lit", "severity": "CRITICAL", "metric": "mean_luminance",
                      "value": round(mean, 4), "threshold": MEAN_LUM_BLOWN,
                      "detail": f"frame is blown out (mean lum {mean:.3f} > {MEAN_LUM_BLOWN}); exposure / no plate"})
    elif var < LUM_VARIANCE_FLAT:
        gates.append({"gate": "G1_frame_lit", "severity": "HIGH", "metric": "luminance_variance",
                      "value": round(var, 5), "threshold": LUM_VARIANCE_FLAT,
                      "detail": f"frame is flat (lum variance {var:.5f} < {LUM_VARIANCE_FLAT}); likely a "
                                "solid fill / fog-only / no rendered content"})
    if not gates:
        gates.append({"gate": "G1_frame_lit", "severity": "PASS", "metric": "mean_luminance",
                      "value": round(mean, 4), "threshold": None,
                      "detail": f"lit (mean {mean:.3f}, var {var:.5f})"})
    return gates


# ---------------------------------------------------------------------------
# G3 floor-contact + G4 screen-scale — geometry on measured actor boxes vs the spec/camera.
# ---------------------------------------------------------------------------
# Spec-expected actor world height (matches ClosedLoopBuilder ACTOR_TARGET_H=5.2 units == one
# adult ~6ft; tune per actor kind in the actor dict via "world_height_ft").
DEFAULT_ACTOR_WORLD_H = 5.2


def gate_floor_contact_and_scale(scenegrid: SceneGrid, camera: CameraSpec, actors: list[dict]) -> list[dict]:
    gates: list[dict] = []
    if not actors:
        return [{"gate": "G3_floor_contact", "severity": "SKIPPED", "metric": "actors",
                 "value": 0, "threshold": None,
                 "detail": "no measured actor boxes supplied; G3/G4 skipped (supply actors=[{id,cell,feet_px,px_height}])"}]
    px_per_cell_y = camera.floor_px_per_cell_y(scenegrid)
    for a in actors:
        aid = a.get("id", "?")
        _cell = a.get("cell")
        # Validate cell is a 2-element sequence before unpacking.
        if not (isinstance(_cell, (list, tuple)) and len(_cell) == 2):
            gates.append({"gate": "G3_floor_contact", "severity": "SKIPPED", "metric": "input",
                          "value": None, "threshold": None,
                          "detail": f"actor {aid}: malformed or missing cell (expected [c,r]); skipped"})
            continue
        try:
            c, r = int(_cell[0]), int(_cell[1])
        except (TypeError, ValueError):
            gates.append({"gate": "G3_floor_contact", "severity": "SKIPPED", "metric": "input",
                          "value": None, "threshold": None,
                          "detail": f"actor {aid}: cell values must be integers; skipped (got {_cell!r})"})
            continue
        feet_px = a.get("feet_px")            # [sx, sy] measured screen feet (bottom of the actor)
        px_height = a.get("px_height")        # measured rendered pixel height (feet->head)
        if feet_px is None:
            gates.append({"gate": "G3_floor_contact", "severity": "SKIPPED", "metric": "input",
                          "value": None, "threshold": None,
                          "detail": f"actor {aid}: missing cell or feet_px; skipped"})
            continue
        # Expected floor-plane screen-Y at this actor's cell (y=0 world):
        wx = scenegrid.cell_world_x(c)
        wz = scenegrid.cell_world_z(r)
        _, floor_sy = camera.world_to_screen(wx, 0.0, wz)
        # measured feet Y minus expected floor Y, in pixels then in floor cells.
        # +delta_px (feet LOWER on screen than the floor plane) => feet sunk BELOW floor (clip);
        # -delta_px (feet HIGHER on screen) => feet ABOVE floor (floating).
        delta_px = feet_px[1] - floor_sy
        delta_cells = delta_px / px_per_cell_y
        if delta_cells < -FLOAT_CELL_CRIT:
            sev = "CRITICAL"
        elif delta_cells < -FLOAT_CELL_HIGH:
            sev = "HIGH"
        elif delta_cells > CLIP_CELL_CRIT:
            sev = "CRITICAL"
        elif delta_cells > CLIP_CELL_HIGH:
            sev = "HIGH"
        else:
            sev = "PASS"
        verb = "floating above" if delta_cells < 0 else "clipping below"
        gates.append({"gate": "G3_floor_contact", "severity": sev, "metric": "feet_vs_floor_cells",
                      "value": round(delta_cells, 3), "threshold": FLOAT_CELL_HIGH,
                      "detail": (f"actor {aid} @cell[{c},{r}] feet {verb} the floor plane by "
                                 f"{abs(delta_cells):.2f} cells ({delta_px:+.0f}px)" if sev != "PASS"
                                 else f"actor {aid} @cell[{c},{r}] grounded ({delta_cells:+.2f} cells)")})
        # G4 scale: expected pixel height = actor world height projected at this depth.
        # Reuse floor_sy from G3 (same floor-plane point) instead of recomputing.
        if px_height is not None:
            world_h = float(a.get("world_height_ft", DEFAULT_ACTOR_WORLD_H))
            _, sy_head = camera.world_to_screen(wx, world_h, wz)
            sy_feet = floor_sy   # floor_sy computed above for G3 — same cell, y=0
            expected_px = abs(sy_head - sy_feet)
            rel = abs(px_height - expected_px) / (expected_px or 1.0)
            if rel > SCALE_REL_HIGH:
                ssev = "HIGH"
            elif rel > SCALE_REL_MED:
                ssev = "MED"
            else:
                ssev = "PASS"
            gates.append({"gate": "G4_screen_scale", "severity": ssev, "metric": "px_height_rel_err",
                          "value": round(rel, 3), "threshold": SCALE_REL_MED,
                          "detail": (f"actor {aid} rendered {px_height:.0f}px vs expected {expected_px:.0f}px "
                                     f"({rel*100:.0f}% off) — reads {'too big' if px_height>expected_px else 'too small'} "
                                     "for its world depth" if ssev != "PASS"
                                     else f"actor {aid} scale ok ({rel*100:.0f}% of expected {expected_px:.0f}px)")})
    return gates


# ---------------------------------------------------------------------------
# G2 occupancy — rendered walkable/blocked tint vs the engine mask.
# ---------------------------------------------------------------------------
def gate_occupancy(scenegrid: SceneGrid, occupancy_tint: Optional[dict]) -> list[dict]:
    """occupancy_tint: {"c,r": "walkable"|"blocked"} sampled from the rendered grid overlay (the
    render side supplies it). Compares to the engine mask. SKIPPED if no overlay is rendered/sampled."""
    if not occupancy_tint:
        return [{"gate": "G2_occupancy", "severity": "SKIPPED", "metric": "tint",
                 "value": None, "threshold": None,
                 "detail": "no rendered occupancy tint supplied; G2 skipped (only meaningful when the "
                           "render draws a walkable/blocked overlay, e.g. tactical mode)"}]
    total = 0
    mismatch = 0
    skipped_keys = 0
    examples: list[str] = []
    for key, rendered in occupancy_tint.items():
        try:
            parts = str(key).replace(" ", "").split(",")
            if len(parts) != 2:
                raise ValueError("not 2 parts")
            c, r = int(parts[0]), int(parts[1])
        except (ValueError, TypeError):
            skipped_keys += 1
            continue
        truth = "walkable" if scenegrid.is_walkable(c, r) else "blocked"
        total += 1
        if rendered != truth:
            mismatch += 1
            if len(examples) < 6:
                examples.append(f"[{c},{r}] render={rendered} truth={truth}")
    frac = mismatch / (total or 1)
    if frac > OCC_MISMATCH_HIGH:
        sev = "HIGH"
    elif frac > OCC_MISMATCH_MED:
        sev = "MED"
    else:
        sev = "PASS"
    skip_note = f"; {skipped_keys} key(s) skipped (malformed 'c,r' format)" if skipped_keys else ""
    return [{"gate": "G2_occupancy", "severity": sev, "metric": "cell_mismatch_frac",
             "value": round(frac, 3), "threshold": OCC_MISMATCH_MED,
             "detail": (f"{mismatch}/{total} cells' rendered tint disagree with the walk-mask "
                        f"({frac*100:.0f}%): {', '.join(examples)}{skip_note}" if sev != "PASS"
                        else f"occupancy tint matches mask ({mismatch}/{total} mismatch){skip_note}")}]


# ---------------------------------------------------------------------------
# G6 luma staging-law — greyscale histogram stats vs the measured PoE staging-law bands.
# ---------------------------------------------------------------------------
# PRE-GATE, run BEFORE staging a panel: a candidate whose luma histogram sits outside the WARN
# band is a "museum wash," not a dramatic chiaroscuro plate — no point spending scorer tokens on
# it. Bands are the MEASURED real-PoE staging law (2026-07-01 staging-law campaign); this is a
# straight port of the same math already proven in two places — do NOT invent new bands here:
#   - extensions/renderers/unity/scripts/atelier_luma_gate.py (the beauty-pass CLI gate: dark_ok =
#     60-85% of pixels L<26, lit_ok = 2-5% L>60, Rec.709 luma)
#   - extensions/renderers/godot/tools/generate_room.py's _staging_law_distance (the pass-1 sample
#     selector: target_near_black=0.73, target_lit=0.03, target_median=8, called by
#     _pick_best_pass1_sample)
# This gate adds a WARN band around the same PASS band (near_black 0.50-0.66 warns below the 0.66
# PASS floor; lit 0.05-0.20 warns above the 0.05 PASS ceiling; median 15-40 warns above the 0-15
# PASS ceiling) so a borderline candidate isn't hard-blocked but the stats must still be quoted.
#
# PERF: this gate's stats are FRACTIONS (near_black/lit pixel share) + a MEDIAN, not exact per-pixel
# output — downsampling to a fixed grid before computing them is statistically safe (histogram shape
# survives a representative sample) and avoids an O(width*height) full-res pass + sort (~2.1M px on a
# 1920x1097 capture). G1 downsamples to a 128px thumbnail (mean/variance) and G5 to _G5_GRID=64
# (inter-frame deltas); G6 uses a coarser _G6_GRID=256 grid (finer than G1/G5 since fraction/median
# accuracy benefits from more samples than a mean does, but still ~65K px vs ~2.1M — >30x cheaper).
_G6_GRID = 256


def _luma_stats(png_path: str | Path) -> Optional[tuple[float, float, float]]:
    """Return (near_black_frac, lit_frac, median_L) on a 0-255 Rec.709 greyscale, sampled from a
    <=_G6_GRID working-size downscale, or None if the PNG can't be decoded. Tries Pillow (thumbnail,
    matches atelier_luma_gate.py's math up to the downsample), else falls back to the stdlib PNG
    decoder (same graceful degradation as G1)."""
    p = Path(png_path)
    if not p.exists():
        return None
    try:
        from PIL import Image  # type: ignore
        im = Image.open(p).convert("RGB")
        im.thumbnail((_G6_GRID, _G6_GRID))
        px = list(im.getdata())
        n = len(px) or 1
        lums = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in px]
        near_black = sum(1 for v in lums if v < LUMA_NEAR_BLACK_L) / n
        lit = sum(1 for v in lums if v > LUMA_LIT_L) / n
        median_L = sorted(lums)[n // 2]
        return near_black, lit, median_L
    except Exception:
        pass
    try:
        return _stdlib_png_luma_stats(p)
    except Exception:
        return None


def _stdlib_png_luma_stats(p: Path) -> tuple[float, float, float]:
    """(near_black_frac, lit_frac, median_L) on a 0-255 Rec.709 greyscale, sampled from a
    <=_G6_GRID nearest-sample downscale via the shared decoder (see the PERF note above
    gate_luma_staging_law — fractions/median tolerate downsampling, unlike G3/G4's exact geometry)."""
    out, width, height, channels = _decode_png_pixels(p)
    stride = width * channels
    # Nearest-sample downscale to <= _G6_GRID on the long edge (same scheme as _stdlib_png_lum_grid).
    scale = max(1, max(width, height) // _G6_GRID)
    gw = max(1, width // scale)
    gh = max(1, height // scale)
    lums: list[float] = []
    for gy in range(gh):
        sy = min(height - 1, gy * scale)
        for gx in range(gw):
            sx = min(width - 1, gx * scale)
            base = sy * stride + sx * channels
            lums.append(_pixel_luma(out, base, channels))
    n = len(lums) or 1
    near_black = sum(1 for v in lums if v < LUMA_NEAR_BLACK_L) / n
    lit = sum(1 for v in lums if v > LUMA_LIT_L) / n
    median_L = sorted(lums)[n // 2]
    return near_black, lit, median_L


def _band_label(value: float, pass_band: tuple[float, float], warn_band: tuple[float, float]) -> str:
    """Classify a stat as PASS / WARN / FAIL against its (pass_band, warn_band) — used only for the
    human-readable per-stat label in the detail string. warn_band is one-sided per stat (see the
    callers below); outside both bands is FAIL. The gate's actual pre-gate SEVERITY (returned by
    gate_luma_staging_law) maps this into the module's shared CRITICAL/HIGH/MED/PASS vocabulary so
    it composes correctly with run_pregates' blocking/verdict logic (_SEV_RANK)."""
    lo, hi = pass_band
    if lo <= value <= hi:
        return "PASS"
    wlo, whi = warn_band
    if wlo <= value <= whi:
        return "WARN"
    return "FAIL"


def gate_luma_staging_law(png_path: str | Path) -> list[dict]:
    """G6: greyscale histogram stats (near-black / lit fractions + median L) vs the measured PoE
    staging-law bands. FAIL = do not spend scorer tokens (fix staging first) -> mapped to HIGH so it
    blocks the panel like the other pre-gates; WARN = the panel is allowed but the stats must be
    quoted alongside the verdict -> mapped to MED (visible, non-blocking); PASS = solidly in-band."""
    stats = _luma_stats(png_path)
    if stats is None:
        return [{"gate": "G6_luma_staging_law", "severity": "SKIPPED", "metric": "decode",
                 "value": None, "threshold": None,
                 "detail": "could not decode PNG (no Pillow + non-trivial PNG); G6 skipped"}]
    near_black, lit, median_L = stats

    # near_black: WARN band is below the PASS floor only (0.50-0.66); above 0.85 is a straight FAIL
    # (an over-dark/near-black plate has no WARN band above PASS — it just fails).
    nb_label = _band_label(near_black, LUMA_NEAR_BLACK_PASS, LUMA_NEAR_BLACK_WARN)
    # lit: WARN band is above the PASS ceiling only (0.05-0.20); below 0.02 is a straight FAIL
    # (too little light at all reads as underlit / no key, not "too washy" — no WARN band below PASS).
    lit_label = _band_label(lit, LUMA_LIT_PASS, LUMA_LIT_WARN)
    # median_L: WARN band is above the PASS ceiling only (15-40); above 40 (outside WARN) is FAIL.
    med_label = _band_label(median_L, LUMA_MEDIAN_PASS, LUMA_MEDIAN_WARN)

    worst_label = max((nb_label, lit_label, med_label), key=lambda s: {"PASS": 0, "WARN": 1, "FAIL": 2}[s])
    severity = {"PASS": "PASS", "WARN": "MED", "FAIL": "HIGH"}[worst_label]

    # G6 stats are ALWAYS emitted in the detail (numbers not vibes), regardless of verdict.
    stat_str = (f"near_black={near_black*100:.1f}% (band {LUMA_NEAR_BLACK_PASS[0]*100:.0f}-"
                f"{LUMA_NEAR_BLACK_PASS[1]*100:.0f}% PASS / {LUMA_NEAR_BLACK_WARN[0]*100:.0f}-"
                f"{LUMA_NEAR_BLACK_PASS[0]*100:.0f}% WARN) [{nb_label}], "
                f"lit={lit*100:.1f}% (band {LUMA_LIT_PASS[0]*100:.0f}-{LUMA_LIT_PASS[1]*100:.0f}% PASS / "
                f"{LUMA_LIT_PASS[1]*100:.0f}-{LUMA_LIT_WARN[1]*100:.0f}% WARN) [{lit_label}], "
                f"median_L={median_L:.1f} (band {LUMA_MEDIAN_PASS[0]}-{LUMA_MEDIAN_PASS[1]} PASS / "
                f"{LUMA_MEDIAN_PASS[1]}-{LUMA_MEDIAN_WARN[1]} WARN) [{med_label}]")
    if worst_label == "FAIL":
        detail = f"FAIL: plate is outside the PoE staging-law band — {stat_str}"
    elif worst_label == "WARN":
        detail = f"WARN: plate is borderline vs the PoE staging-law band — {stat_str}"
    else:
        detail = f"PASS: plate matches the PoE staging-law band — {stat_str}"
    return [{"gate": "G6_luma_staging_law", "severity": severity, "metric": "staging_law_stats",
             "value": {"near_black_frac": round(near_black, 4), "lit_frac": round(lit, 4),
                        "median_L": round(median_L, 2)},
             "threshold": {"near_black_pass": LUMA_NEAR_BLACK_PASS, "lit_pass": LUMA_LIT_PASS,
                           "median_pass": LUMA_MEDIAN_PASS},
             "detail": detail}]


# ---------------------------------------------------------------------------
# G5 motion-liveness — objective inter-frame deltas over a render REEL.
# ---------------------------------------------------------------------------
# G5 is the motion analogue of G1: instead of "is ONE frame lit?", it asks "does the REEL actually
# MOVE?" It reads a small downscaled luminance grid from each reel frame (Pillow if present, else a
# stdlib PNG decode — same graceful degradation as G1) and computes two objective signals:
#   * mean abs inter-frame luminance delta over the IDLE frames -> a FROZEN idle is CRITICAL.
#   * bright-mass centroid displacement over a frame-pair tagged as a MOVE -> no shift is HIGH.
# When no reel is supplied (the common still-only round), G5 SKIPS — additive, empty == today.

# Downscale target for the per-frame luminance grid (cheap; matches G1's 128px thumbnail budget).
_G5_GRID = 64


def _lum_grid(png_path: str | Path) -> Optional[tuple[list[float], int, int]]:
    """Return (luminance[], width, height) of a downscaled (<= _G5_GRID) luminance grid in 0..1,
    or None if the PNG can't be decoded. Tries Pillow (fast), else reuses the stdlib PNG decoder.
    Used by G5 for inter-frame delta + centroid math (G1 only needs the scalar stats)."""
    p = Path(png_path)
    if not p.exists():
        return None
    try:
        from PIL import Image  # type: ignore
        im = Image.open(p).convert("L")
        im.thumbnail((_G5_GRID, _G5_GRID))
        w, h = im.size
        vals = [v / 255.0 for v in im.tobytes()]
        return vals, w, h
    except Exception:
        pass
    # stdlib fallback: decode the full PNG, then box-sample down to a coarse grid.
    try:
        return _stdlib_png_lum_grid(p, _G5_GRID)
    except Exception:
        return None


def _stdlib_png_lum_grid(p: Path, target: int) -> tuple[list[float], int, int]:
    """<=target luminance grid via the shared decoder. stdlib only. Nearest-sample downscale
    (cheap + deterministic); good enough for delta/centroid signals."""
    out, width, height, channels = _decode_png_pixels(p)
    stride = width * channels
    # Nearest-sample downscale to <= target on the long edge.
    scale = max(1, max(width, height) // target)
    gw = max(1, width // scale)
    gh = max(1, height // scale)
    vals: list[float] = []
    for gy in range(gh):
        sy = min(height - 1, gy * scale)
        for gx in range(gw):
            sx = min(width - 1, gx * scale)
            base = sy * stride + sx * channels
            vals.append(_pixel_luma(out, base, channels) / 255.0)
    return vals, gw, gh


def _mean_abs_delta(a: list[float], b: list[float]) -> Optional[float]:
    """Mean absolute per-pixel luminance delta between two equal-length grids (0..1). None if the
    grids differ in length (mismatched frame sizes — can't compare)."""
    if not a or not b or len(a) != len(b):
        return None
    # lengths are guaranteed equal here (guarded above) -> strict=True is safe + correct.
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


def _bright_centroid(vals: list[float], w: int, h: int) -> Optional[tuple[float, float]]:
    """Luminance-weighted centroid (x,y) in grid px of the bright mass, or None if the frame has no
    bright mass. Used to detect whether a MOVE actually displaced the actor."""
    total = sum(vals)
    if total <= 0:
        return None
    cx = sum(vals[i] * (i % w) for i in range(len(vals))) / total
    cy = sum(vals[i] * (i // w) for i in range(len(vals))) / total
    return cx, cy


def gate_motion_liveness(reel: Optional[list[dict]]) -> list[dict]:
    """G5: objective inter-frame deltas over a render REEL.

    ``reel`` is an ordered list of frame dicts (the qa/motion_reel.py sidecar's ``frames``):
        [{"frame": "<png path>", "label": "idle"|"walk"|"attack"|..., "is_move": bool, ...}, ...]
    Each frame must carry a decodable PNG ``frame`` path. ``label`` (or ``anim``) classifies the
    beat; a frame with ``is_move`` true (or label in the MOVE set) participates in the displacement
    check. SKIPPED when no reel is supplied (additive — the still-only round is unchanged).

    Checks:
      * FROZEN IDLE (CRITICAL) — over the IDLE frames (label/anim contains "idle"), the mean abs
        inter-frame luminance delta < FROZEN_IDLE_DELTA. Nothing is animating: a static billboard.
      * NO LOCOMOTION DISPLACEMENT (HIGH) — for a MOVE pair, the bright-mass centroid shifts <
        MOVE_CENTROID_MIN_PX. The engine said the actor moved but the render didn't displace it.
    """
    if not reel:
        return [{"gate": "G5_motion_liveness", "severity": "SKIPPED", "metric": "reel",
                 "value": None, "threshold": None,
                 "detail": "no render reel supplied; G5 skipped (only meaningful with a motion reel — "
                           "build one with qa/motion_reel.py and pass its frames)"}]

    # Decode each frame's grid once (graceful: undecodable frames are dropped + noted).
    grids: list[tuple[Optional[tuple[list[float], int, int]], dict]] = []
    for fr in reel:
        path = fr.get("frame") or fr.get("path")
        grids.append((_lum_grid(path) if path else None, fr))
    decoded = [(g, fr) for g, fr in grids if g is not None]
    if len(decoded) < 2:
        return [{"gate": "G5_motion_liveness", "severity": "SKIPPED", "metric": "frames",
                 "value": len(decoded), "threshold": 2,
                 "detail": f"reel has <2 decodable frames ({len(decoded)}); G5 needs >=2 to measure motion"}]

    def _label(fr: dict) -> str:
        return str(fr.get("label") or fr.get("anim") or "").lower()

    _MOVE_LABELS = ("walk", "run", "move", "locomot", "step", "approach", "charge")

    gates: list[dict] = []

    # --- FROZEN IDLE: mean abs inter-frame delta across consecutive IDLE frames. ---
    idle = [(g, fr) for g, fr in decoded if "idle" in _label(fr)]
    if len(idle) >= 2:
        deltas = []
        for (ga, _), (gb, _) in zip(idle, idle[1:]):
            d = _mean_abs_delta(ga[0], gb[0])
            if d is not None:
                deltas.append(d)
        if deltas:
            mean_delta = sum(deltas) / len(deltas)
            if mean_delta < FROZEN_IDLE_DELTA:
                gates.append({"gate": "G5_motion_liveness", "severity": "CRITICAL",
                              "metric": "idle_interframe_delta", "value": round(mean_delta, 6),
                              "threshold": FROZEN_IDLE_DELTA,
                              "detail": (f"FROZEN idle: mean inter-frame luminance delta {mean_delta:.6f} "
                                         f"< {FROZEN_IDLE_DELTA} over {len(idle)} idle frames — the actor is "
                                         "a static billboard, not a living 3D actor; animate the idle BEFORE scoring")})
            else:
                gates.append({"gate": "G5_motion_liveness", "severity": "PASS",
                              "metric": "idle_interframe_delta", "value": round(mean_delta, 6),
                              "threshold": FROZEN_IDLE_DELTA,
                              "detail": f"idle is alive (mean inter-frame delta {mean_delta:.6f} >= {FROZEN_IDLE_DELTA})"})

    # --- NO LOCOMOTION DISPLACEMENT: a MOVE beat must shift the bright-mass centroid. ---
    # A frame is a MOVE if is_move is truthy OR its label is in the move set. We use the centroid
    # SPREAD — the MAX pairwise displacement across ALL move frames — NOT just first-vs-last net
    # displacement. A valid out-and-back move (A->B->A) nets ~0 first-vs-last but clearly travels;
    # spread captures that the actor reached B. Only frames whose grid size matches the first move
    # frame's are compared (the frame-size guard); centroid-less frames (no bright mass) are skipped.
    moves = [(g, fr) for g, fr in decoded
             if fr.get("is_move") or any(m in _label(fr) for m in _MOVE_LABELS)]
    if len(moves) >= 2:
        (g0, w0, h0) = moves[0][0]
        centroids: list[tuple[float, float]] = []
        for (gi, wi, hi), _ in moves:
            if (wi, hi) != (w0, h0):
                continue  # frame-size guard: only compare same-size frames
            ci = _bright_centroid(gi, wi, hi)
            if ci is not None:
                centroids.append((ci[0], ci[1]))
        if len(centroids) >= 2:
            # Max pairwise displacement (spread). Cheap O(n^2) — reels are short (a handful of frames).
            disp = max(math.hypot(cx2 - cx1, cy2 - cy1)
                       for i, (cx1, cy1) in enumerate(centroids)
                       for (cx2, cy2) in centroids[i + 1:])
            if disp < MOVE_CENTROID_MIN_PX:
                gates.append({"gate": "G5_motion_liveness", "severity": "HIGH",
                              "metric": "move_centroid_px", "value": round(disp, 3),
                              "threshold": MOVE_CENTROID_MIN_PX,
                              "detail": (f"a MOVE beat's bright-mass centroid spread only {disp:.2f}px "
                                         f"(< {MOVE_CENTROID_MIN_PX}px) across {len(moves)} move frames — the "
                                         "actor slides/teleports without locomotion; check the walk cycle / root motion")})
            else:
                gates.append({"gate": "G5_motion_liveness", "severity": "PASS",
                              "metric": "move_centroid_px", "value": round(disp, 3),
                              "threshold": MOVE_CENTROID_MIN_PX,
                              "detail": f"move beat displaced the actor {disp:.2f}px (>= {MOVE_CENTROID_MIN_PX}px, max spread)"})

    if not gates:
        gates.append({"gate": "G5_motion_liveness", "severity": "SKIPPED", "metric": "beats",
                      "value": len(decoded), "threshold": None,
                      "detail": "reel decoded but no idle pair / move beat to measure; tag frames with "
                                "label='idle' and/or is_move=true (or a walk/run label) to enable G5 checks"})
    return gates


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
_SEV_RANK = {"CRITICAL": 4, "HIGH": 3, "MED": 2, "LOW": 1, "PASS": 0, "SKIPPED": 0}


def run_pregates(
    render_png: str | Path,
    scenegrid: Optional[SceneGrid] = None,
    camera: CameraSpec = CameraSpec.LOCKED,  # type: ignore[attr-defined]
    actors: Optional[list[dict]] = None,
    occupancy_tint: Optional[dict] = None,
    reel: Optional[list[dict]] = None,
) -> dict:
    """Run all available pre-gates. Returns {verdict, blocking, gates[]}.
    verdict = FLAG if any CRITICAL/HIGH fired (the loop must fix the deterministic defect and
    re-render BEFORE the LLM panel); else PASS; SKIPPED only if literally nothing could run.

    G1 frame-lit and G6 luma-staging-law always run (need only the PNG). G6 FAIL maps to HIGH
    (blocks the panel, same as the other hard pre-gates) and WARN maps to MED (visible in the
    result, non-blocking) — see gate_luma_staging_law.

    ``reel`` (optional) is the ordered list of motion-reel frame dicts (qa/motion_reel.py's sidecar
    ``frames``); when supplied, G5 motion-liveness also runs. Omitting it == today's behavior (G5
    SKIPS) — additive, no still-only round changes."""
    gates: list[dict] = []
    gates += gate_frame_lit(render_png)
    gates += gate_luma_staging_law(render_png)
    if scenegrid is not None:
        gates += gate_floor_contact_and_scale(scenegrid, camera, actors or [])
        gates += gate_occupancy(scenegrid, occupancy_tint)
    gates += gate_motion_liveness(reel)
    worst = max((_SEV_RANK[g["severity"]] for g in gates), default=0)
    blocking = [g for g in gates if g["severity"] in ("CRITICAL", "HIGH")]
    ran = [g for g in gates if g["severity"] not in ("SKIPPED",)]
    if not ran:
        verdict = "SKIPPED"
    elif worst >= _SEV_RANK["HIGH"]:
        verdict = "FLAG"
    else:
        verdict = "PASS"
    return {
        "verdict": verdict,
        "blocking": blocking,
        "gates": gates,
        "summary": _summary(verdict, gates),
    }


def _summary(verdict: str, gates: list[dict]) -> str:
    lines = [f"PRE-GATE {verdict}"]
    for g in gates:
        lines.append(f"  [{g['severity']:8s}] {g['gate']:18s} {g.get('metric','')}={g.get('value')} :: {g['detail']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Spec-facing manifest runner
# ---------------------------------------------------------------------------
def _manifest_check(manifest: dict, name: str) -> dict:
    checks = manifest.get("checks", {})
    cfg = checks.get(name, {}) if isinstance(checks, dict) else {}
    return cfg if isinstance(cfg, dict) else {}


def _rgb_tuple(pixels: bytearray, base: int, channels: int) -> tuple[int, int, int]:
    if channels == 1:
        v = int(pixels[base])
        return v, v, v
    return int(pixels[base]), int(pixels[base + 1]), int(pixels[base + 2])


def _bbox_from_actor(actor: dict) -> Optional[list[int]]:
    raw = actor.get("screen_bbox", actor.get("bbox", actor.get("box")))
    if raw is None:
        return None
    if isinstance(raw, dict):
        if all(k in raw for k in ("x", "y", "w", "h")):
            vals = [raw["x"], raw["y"], raw["x"] + raw["w"], raw["y"] + raw["h"]]
        elif all(k in raw for k in ("left", "top", "right", "bottom")):
            vals = [raw["left"], raw["top"], raw["right"], raw["bottom"]]
        else:
            return None
    elif isinstance(raw, (list, tuple)) and len(raw) == 4:
        vals = list(raw)
    else:
        return None
    try:
        x0, y0, x1, y1 = [int(round(float(v))) for v in vals]
    except (TypeError, ValueError):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if x1 == x0 or y1 == y0:
        return None
    return [x0, y0, x1, y1]


def _frame_lit_manifest_check(frame_png: str | Path, manifest: dict) -> tuple[dict, tuple[int, int] | None]:
    cfg = _manifest_check(manifest, "frame_lit")
    min_mean = float(cfg.get("min_mean_luma", cfg.get("min", MEAN_LUM_DARK)))
    max_mean = float(cfg.get("max_mean_luma", cfg.get("max", MEAN_LUM_BLOWN)))
    max_single = float(cfg.get("max_single_color_frac", cfg.get("max_single_color", 0.90)))
    try:
        pixels, width, height, channels = _decode_png_pixels(Path(frame_png))
    except Exception as exc:
        return ({
            "check": "frame-lit", "status": "FAIL", "metric": "decode",
            "value": None, "threshold": None,
            "detail": f"could not decode frame PNG: {exc}",
        }, None)

    stride = width * channels
    total = width * height
    step = max(1, total // 65536)
    luma_sum = 0.0
    colors: dict[tuple[int, int, int], int] = {}
    samples = 0
    for i in range(0, total, step):
        base = (i // width) * stride + (i % width) * channels
        color = _rgb_tuple(pixels, base, channels)
        colors[color] = colors.get(color, 0) + 1
        luma_sum += (0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2]) / 255.0
        samples += 1
    mean = luma_sum / (samples or 1)
    single_frac = max(colors.values(), default=0) / (samples or 1)
    failures = []
    if mean < min_mean:
        failures.append(f"mean luma {mean:.3f} < {min_mean:.3f}")
    if mean > max_mean:
        failures.append(f"mean luma {mean:.3f} > {max_mean:.3f}")
    if single_frac > max_single:
        failures.append(f"dominant color {single_frac:.1%} > {max_single:.0%}")
    status = "FAIL" if failures else "PASS"
    return ({
        "check": "frame-lit", "status": status, "metric": "mean_luma",
        "value": {"mean_luma": round(mean, 4), "single_color_frac": round(single_frac, 4)},
        "threshold": {
            "min_mean_luma": min_mean,
            "max_mean_luma": max_mean,
            "max_single_color_frac": max_single,
        },
        "detail": "; ".join(failures) if failures else
                  f"frame lit: mean luma {mean:.3f}, dominant color {single_frac:.1%}",
    }, (width, height))


def _diff_bboxes(frame_png: str | Path, baseline_png: str | Path, manifest: dict) -> list[list[int]]:
    cfg = _manifest_check(manifest, "diff")
    threshold = float(cfg.get("threshold", 20))
    min_area = int(cfg.get("min_area_px", cfg.get("min_area", 16)))
    fp, fw, fh, fc = _decode_png_pixels(Path(frame_png))
    bp, bw, bh, bc = _decode_png_pixels(Path(baseline_png))
    if (fw, fh) != (bw, bh):
        raise ValueError(f"baseline dimensions {bw}x{bh} do not match frame {fw}x{fh}")
    fstride = fw * fc
    bstride = bw * bc
    total = fw * fh
    mask = bytearray(total)
    for i in range(total):
        fx, fy = i % fw, i // fw
        fr, fg, fb = _rgb_tuple(fp, fy * fstride + fx * fc, fc)
        br, bg, bb = _rgb_tuple(bp, fy * bstride + fx * bc, bc)
        if max(abs(fr - br), abs(fg - bg), abs(fb - bb)) > threshold:
            mask[i] = 1

    seen = bytearray(total)
    bboxes: list[list[int]] = []
    for start, changed in enumerate(mask):
        if not changed or seen[start]:
            continue
        stack = [start]
        seen[start] = 1
        min_x = max_x = start % fw
        min_y = max_y = start // fw
        area = 0
        while stack:
            idx = stack.pop()
            area += 1
            x, y = idx % fw, idx // fw
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            if x > 0:
                ni = idx - 1
                if mask[ni] and not seen[ni]:
                    seen[ni] = 1
                    stack.append(ni)
            if x + 1 < fw:
                ni = idx + 1
                if mask[ni] and not seen[ni]:
                    seen[ni] = 1
                    stack.append(ni)
            if y > 0:
                ni = idx - fw
                if mask[ni] and not seen[ni]:
                    seen[ni] = 1
                    stack.append(ni)
            if y + 1 < fh:
                ni = idx + fw
                if mask[ni] and not seen[ni]:
                    seen[ni] = 1
                    stack.append(ni)
        if area >= min_area:
            bboxes.append([min_x, min_y, max_x + 1, max_y + 1])
    return sorted(bboxes, key=lambda b: (b[0], b[1], -(b[2] - b[0]) * (b[3] - b[1])))


def _grid_expected_floor_y(actor: dict, manifest: dict) -> Optional[float]:
    if "floor_y_px" in actor:
        return float(actor["floor_y_px"])
    if "floor_y_px" in manifest:
        return float(manifest["floor_y_px"])
    grid = manifest.get("grid")
    cell = actor.get("expected_cell", actor.get("cell"))
    if not isinstance(grid, dict) or not (isinstance(cell, (list, tuple)) and len(cell) == 2):
        return None
    origin = grid.get("origin", [0, 0])
    cell_px = grid.get("cell_px", 1)
    if not (isinstance(origin, (list, tuple)) and len(origin) >= 2):
        return None
    if isinstance(cell_px, (int, float)):
        cell_h = float(cell_px)
    elif isinstance(cell_px, (list, tuple)) and len(cell_px) >= 2:
        cell_h = float(cell_px[1])
    else:
        return None
    try:
        row = int(cell[1])
        origin_y = float(origin[1])
    except (TypeError, ValueError):
        return None
    return origin_y + (row + 1) * cell_h


def _occupancy_manifest_check(expected_count: int, bboxes: list[list[int]]) -> dict:
    found = len(bboxes)
    status = "PASS" if found >= expected_count else "FAIL"
    return {
        "check": "occupancy", "status": status, "metric": "actor_bbox_count",
        "value": {"expected": expected_count, "found": found},
        "threshold": expected_count,
        "detail": f"found {found}/{expected_count} expected actor bbox(es)",
    }


def _floor_contact_manifest_check(actors: list[dict], actor_bboxes: list[tuple[dict, list[int]]],
                                  manifest: dict) -> dict:
    cfg = _manifest_check(manifest, "floor_contact")
    tolerance = float(cfg.get("tolerance_px", cfg.get("tolerance", 6)))
    entries = []
    failures = []
    for actor, bbox in actor_bboxes:
        name = str(actor.get("name") or actor.get("id") or "?")
        floor_y = _grid_expected_floor_y(actor, manifest)
        if floor_y is None:
            entries.append({"actor": name, "status": "SKIP", "detail": "no floor_y_px or grid projection"})
            continue
        bottom_y = float(bbox[3])
        delta = bottom_y - floor_y
        if abs(delta) <= tolerance:
            status = "PASS"
            detail = f"{name} grounded: bbox bottom {bottom_y:.1f}px within {tolerance:.1f}px of floor {floor_y:.1f}px"
        else:
            status = "FAIL"
            mode = "floating above" if delta < 0 else "clipping below"
            detail = f"{name} {mode}: bbox bottom {bottom_y:.1f}px vs floor {floor_y:.1f}px ({delta:+.1f}px)"
            failures.append(detail)
        entries.append({"actor": name, "status": status, "delta_px": round(delta, 2), "detail": detail})
    if not actor_bboxes and actors:
        return {
            "check": "floor-contact", "status": "SKIP", "metric": "feet_vs_floor_px",
            "value": [], "threshold": tolerance,
            "detail": "no actor bbox available; occupancy check owns the missing-actor failure",
        }
    status = "FAIL" if failures else "PASS"
    return {
        "check": "floor-contact", "status": status, "metric": "feet_vs_floor_px",
        "value": entries, "threshold": tolerance,
        "detail": "; ".join(failures) if failures else f"{len(entries)} actor bbox(es) grounded",
    }


def _screen_scale_manifest_check(actor_bboxes: list[tuple[dict, list[int]]], manifest: dict,
                                 frame_size: tuple[int, int] | None) -> dict:
    cfg = _manifest_check(manifest, "screen_scale")
    min_frac = float(cfg.get("min_height_frac", cfg.get("min", 0.04)))
    max_frac = float(cfg.get("max_height_frac", cfg.get("max", 0.40)))
    if frame_size is None:
        return {
            "check": "screen-scale", "status": "SKIP", "metric": "bbox_height_frac",
            "value": [], "threshold": {"min": min_frac, "max": max_frac},
            "detail": "frame dimensions unavailable; screen-scale skipped",
        }
    _, frame_h = frame_size
    entries = []
    failures = []
    for actor, bbox in actor_bboxes:
        name = str(actor.get("name") or actor.get("id") or "?")
        height = max(0, bbox[3] - bbox[1])
        frac = height / (frame_h or 1)
        if min_frac <= frac <= max_frac:
            status = "PASS"
            detail = f"{name} height {height}px ({frac:.1%}) within {min_frac:.0%}-{max_frac:.0%}"
        else:
            status = "FAIL"
            detail = f"{name} height {height}px ({frac:.1%}) outside {min_frac:.0%}-{max_frac:.0%}"
            failures.append(detail)
        entries.append({"actor": name, "status": status, "height_px": height,
                        "height_frac": round(frac, 4), "detail": detail})
    if not actor_bboxes:
        return {
            "check": "screen-scale", "status": "SKIP", "metric": "bbox_height_frac",
            "value": [], "threshold": {"min": min_frac, "max": max_frac},
            "detail": "no actor bbox available; occupancy check owns the missing-actor failure",
        }
    status = "FAIL" if failures else "PASS"
    return {
        "check": "screen-scale", "status": status, "metric": "bbox_height_frac",
        "value": entries, "threshold": {"min": min_frac, "max": max_frac},
        "detail": "; ".join(failures) if failures else f"{len(entries)} actor bbox(es) within scale band",
    }


def _pose_uprightness_manifest_check(actor_bboxes: list[tuple[dict, list[int]]],
                                     manifest: dict) -> dict:
    """G7 / #1397 pose-uprightness pre-gate — a torso-verticality proxy from each actor's bbox
    ASPECT RATIO (height/width). This is the "binding felt defect" tripwire: a skinned actor
    rendered PRONE/TILTED (bind-pose desync, missing Animator default state, or an import-axis
    mismatch — see the #1397 probe ladder) casts a WIDE, SHORT silhouette (aspect << 1) instead
    of the TALL, NARROW one a standing humanoid casts under the locked dimetric camera (aspect
    ~2+ — e.g. paint_combat_replay_v1.cs's screen_bbox synthesis: half-width = 0.22 * px_height
    => aspect ~2.27). Deliberately a bbox-shape proxy, not a skeletal torso-vector: it needs no
    new Unity-side manifest fields (screen_bbox is already required) and is exercised by the SAME
    actor_bboxes (manifest-declared OR baseline-diff-detected) floor-contact/screen-scale use, so
    it stays truthful to real pixel geometry when bboxes come from the baseline-diff path.
    Mirrors floor-contact/screen-scale's structure exactly (per-actor entries + failures list,
    SKIP when no bboxes are available)."""
    cfg = _manifest_check(manifest, "pose_uprightness")
    min_aspect = float(cfg.get("min_aspect_ratio", cfg.get("min", 1.3)))
    entries = []
    failures = []
    for actor, bbox in actor_bboxes:
        name = str(actor.get("name") or actor.get("id") or "?")
        width = max(1e-6, bbox[2] - bbox[0])
        height = max(0.0, bbox[3] - bbox[1])
        aspect = height / width
        if aspect >= min_aspect:
            status = "PASS"
            detail = f"{name} upright: bbox aspect {aspect:.2f} (h/w) >= {min_aspect:.2f}"
        else:
            status = "FAIL"
            detail = (f"{name} prone/tilted: bbox aspect {aspect:.2f} (h/w) < {min_aspect:.2f} "
                      "(wide/short silhouette, not a standing humanoid)")
            failures.append(detail)
        entries.append({"actor": name, "status": status, "aspect_ratio": round(aspect, 3), "detail": detail})
    if not actor_bboxes:
        return {
            "check": "pose-uprightness", "status": "SKIP", "metric": "bbox_aspect_ratio",
            "value": [], "threshold": min_aspect,
            "detail": "no actor bbox available; occupancy check owns the missing-actor failure",
        }
    status = "FAIL" if failures else "PASS"
    return {
        "check": "pose-uprightness", "status": status, "metric": "bbox_aspect_ratio",
        "value": entries, "threshold": min_aspect,
        "detail": "; ".join(failures) if failures else f"{len(entries)} actor bbox(es) upright",
    }


def run_manifest_pregate(frame_png: str | Path, manifest_json: str | Path,
                         baseline_png: str | Path | None = None) -> dict:
    """Run the spec-facing deterministic visual pre-gate.

    Manifest shape:
        {
          "actors": [{"name": "Hero", "expected_cell": [c, r], "screen_bbox": [x0,y0,x1,y1]}],
          "grid": {"origin": [x,y], "cell_px": [w,h], "rows": N, "cols": M},
          "floor_y_px": 80,
          "checks": {"floor_contact": {"tolerance_px": 6}, "pose_uprightness": {"min_aspect_ratio": 1.3}, ...}
        }

    Bboxes come either from actor.screen_bbox (preferred capture-harness path) or from
    ``baseline_png`` diff clusters when the manifest omits bboxes.
    """
    manifest = json.loads(Path(manifest_json).read_text())
    actors = manifest.get("actors", [])
    if not isinstance(actors, list):
        actors = []

    checks: list[dict] = []
    frame_check, frame_size = _frame_lit_manifest_check(frame_png, manifest)
    checks.append(frame_check)

    manifest_actor_bboxes = []
    for actor in actors:
        bbox = _bbox_from_actor(actor)
        if bbox is not None:
            manifest_actor_bboxes.append((actor, bbox))
    manifest_bboxes = [bbox for _, bbox in manifest_actor_bboxes]
    bbox_source = "manifest"
    if baseline_png is not None:
        try:
            bboxes = _diff_bboxes(frame_png, baseline_png, manifest)
        except Exception as exc:
            bboxes = []
            checks.append({
                "check": "bbox-detection", "status": "FAIL", "metric": "baseline_diff",
                "value": None, "threshold": None,
                "detail": f"could not compute diff-vs-baseline actor bboxes: {exc}",
            })
        bbox_source = "baseline-diff"
        actor_bboxes = list(zip(actors, bboxes))
    else:
        bboxes = manifest_bboxes
        actor_bboxes = manifest_actor_bboxes

    checks.append(_occupancy_manifest_check(len(actors), bboxes))
    checks.append(_floor_contact_manifest_check(actors, actor_bboxes, manifest))
    checks.append(_screen_scale_manifest_check(actor_bboxes, manifest, frame_size))
    checks.append(_pose_uprightness_manifest_check(actor_bboxes, manifest))

    failed = [c for c in checks if c["status"] == "FAIL"]
    verdict = "FAIL" if failed else "PASS"
    return {
        "schema": "worldos.visual_pregate.v1",
        "verdict": verdict,
        "bbox_source": bbox_source,
        "frame": str(frame_png),
        "manifest": str(manifest_json),
        "baseline": str(baseline_png) if baseline_png else None,
        "bboxes": [{"bbox": b, "source": bbox_source} for b in bboxes],
        "checks": checks,
        "failures": failed,
        "summary": _manifest_summary(verdict, checks),
    }


def _manifest_summary(verdict: str, checks: list[dict]) -> str:
    lines = [f"VISUAL PRE-GATE {verdict}"]
    for c in checks:
        lines.append(f"  [{c['status']:4s}] {c['check']:14s} {c.get('metric','')}={c.get('value')} :: {c['detail']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_actors(arg: Optional[str]) -> list[dict]:
    if not arg:
        return []
    if arg.startswith("@"):
        return json.loads(Path(arg[1:]).read_text())
    return json.loads(arg)


def _load_reel(arg: Optional[str]) -> Optional[list[dict]]:
    """Load the motion-reel frames for G5. Accepts a bare list of frame dicts OR a motion_reel.py
    sidecar object with a top-level 'frames' key. Returns None on empty/unrecognized input."""
    if not arg:
        return None
    raw = Path(arg[1:]).read_text() if arg.startswith("@") else arg
    obj = json.loads(raw)
    if isinstance(obj, dict):
        obj = obj.get("frames")
    return obj if isinstance(obj, list) else None


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic visual pre-gates for the visual-critic loop")
    ap.add_argument("positional", nargs="*", metavar="ARG",
                    help="spec CLI: <frame.png> <manifest.json>; legacy mode uses --render")
    ap.add_argument("--render", help="legacy: path to the rendered PNG")
    ap.add_argument("--scenegrid", help="path to the *.scenegrid.json (enables G2/G3/G4)")
    ap.add_argument("--actors", help="measured actor boxes JSON or @file.json")
    ap.add_argument("--occupancy", help="rendered occupancy tint JSON or @file.json")
    ap.add_argument("--baseline", help="spec CLI: empty-plate PNG for diff-vs-baseline actor bbox mode")
    ap.add_argument("--json-out", help="write machine-readable JSON report to this path")
    ap.add_argument("--reel", help="motion-reel frames JSON or @file.json (enables G5); accepts a "
                                   "bare list of frame dicts OR a qa/motion_reel.py sidecar object "
                                   "with a top-level 'frames' key")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    if args.positional:
        if len(args.positional) != 2:
            ap.error("spec CLI expects exactly: <frame.png> <manifest.json>")
        frame_png, manifest_json = args.positional
        res = run_manifest_pregate(frame_png, manifest_json, baseline_png=args.baseline)
        if args.json_out:
            Path(args.json_out).write_text(json.dumps(res, indent=2) + "\n")
        if args.json or not args.json_out:
            print(json.dumps(res, indent=2) if args.json else res["summary"])
        return 2 if res["verdict"] == "FAIL" else 0

    if not args.render:
        ap.error("--render is required in legacy option mode")

    sg = load_scenegrid(args.scenegrid) if args.scenegrid else None
    actors = _load_actors(args.actors)
    occ = _load_actors(args.occupancy) if args.occupancy else None
    if occ is not None and not isinstance(occ, dict):
        import sys as _sys
        print(f"WARNING: --occupancy input is not a dict (got {type(occ).__name__}); G2 will be SKIPPED", file=_sys.stderr)
        occ = None
    reel = _load_reel(args.reel) if args.reel else None
    res = run_pregates(args.render, scenegrid=sg, actors=actors, occupancy_tint=occ, reel=reel)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(res, indent=2) + "\n")
    if args.json:
        print(json.dumps(res, indent=2))
    elif not args.json_out:
        print(res["summary"])
    return 2 if res["verdict"] == "FLAG" else 0


if __name__ == "__main__":
    raise SystemExit(main())
