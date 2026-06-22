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

A pre-gate is CRITICAL/HIGH/MED with an objective delta. A CRITICAL pre-gate SHORT-CIRCUITS the
LLM panel: there is no point asking five subagents to admire brushwork when the hero's feet float
0.4 floor-cells above the stone. Fix the deterministic defect first, re-render, then critique.

DEPENDENCIES (graceful degradation — never a hard import error):
  - G1 needs pixels: tries Pillow, else falls back to a stdlib PNG decode of a downscaled grid.
  - G2/G3/G4 are pure geometry/math on the SceneGrid + camera + measured actor boxes; stdlib only.
    The actor pixel boxes are supplied by the caller (the Unity side emits them, or an AUDIT-mode
    segmentation pass produces them); this module does NOT do CV detection — it does the MATH that
    turns measured boxes + the spec into a pass/fail with a numeric delta.

CAMERA / PROJECTION (the locked dimetric registration authority — must match ClosedLoopBuilder.cs):
  orthographic, orthoSize=18, aspect=1344/756 (16:9), pitch=atan(0.5)=26.565deg (dimetric 2:1),
  yaw=0, roll=0, position=(0, 40.25, -55.5). World->screen below is derived from exactly this.
  If the Unity camera changes, update CameraSpec (one place) — the registration stays single-authority.

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

    # CLI:
    python qa/visual_pregate.py --render /tmp/frame.png --scenegrid fixtures/tavern.scenegrid.json \
        --actors @/tmp/actors.json --json

Exit codes: 0 = PASS / SKIPPED, 2 = FLAG (a CRITICAL or HIGH pre-gate fired) — so the loop / CI can gate.
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


# ---------------------------------------------------------------------------
# Camera — the locked dimetric registration authority (mirror of ClosedLoopBuilder.cs LockCamera)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CameraSpec:
    ortho_size: float = 18.0
    aspect: float = 1344.0 / 756.0
    pitch_deg: float = math.degrees(math.atan(0.5))   # 26.565 — true dimetric 2:1
    yaw_deg: float = 0.0
    pos: tuple[float, float, float] = (0.0, 40.25, -55.5)
    px_w: int = 1344
    px_h: int = 756

    def world_to_screen(self, wx: float, wy: float, wz: float) -> tuple[float, float]:
        """Project a world point to pixel coords under the locked ortho dimetric camera.
        Screen origin top-left, +y DOWN (image convention). Pure ortho => linear, no perspective."""
        # Camera basis from pitch (about X) + yaw (about Y). yaw=0 in the locked rig, but kept general.
        p = math.radians(self.pitch_deg)
        y = math.radians(self.yaw_deg)
        # Forward (into scene), looking down by pitch:
        fwd = (math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))
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


def _stdlib_png_lum(p: Path) -> tuple[float, float]:
    """Minimal PNG decoder (8-bit RGB/RGBA, no interlace) -> coarse luminance stats. stdlib only."""
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
    if bit_depth != 8 or color_type not in (2, 6):
        raise ValueError(f"unsupported PNG bit_depth={bit_depth} color_type={color_type}")
    channels = 4 if color_type == 6 else 3
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    # Un-filter scanlines (PNG filter types 0-4).
    out = bytearray()
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ftype = raw[pos]; pos += 1
        line = bytearray(raw[pos:pos + stride]); pos += stride
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
    # Coarse luminance sample (every Nth pixel to stay cheap).
    step = max(1, (width * height) // 16384)
    vals = []
    for i in range(0, width * height, step):
        base = (i // width) * stride + (i % width) * channels
        r, g, b = out[base], out[base + 1], out[base + 2]
        vals.append((0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0)
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
        c, r = int(_cell[0]) if _cell[0] is not None else None, int(_cell[1]) if _cell[1] is not None else None
        feet_px = a.get("feet_px")            # [sx, sy] measured screen feet (bottom of the actor)
        px_height = a.get("px_height")        # measured rendered pixel height (feet->head)
        if c is None or r is None or feet_px is None:
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
# Orchestrator
# ---------------------------------------------------------------------------
_SEV_RANK = {"CRITICAL": 4, "HIGH": 3, "MED": 2, "LOW": 1, "PASS": 0, "SKIPPED": 0}


def run_pregates(
    render_png: str | Path,
    scenegrid: Optional[SceneGrid] = None,
    camera: CameraSpec = CameraSpec.LOCKED,  # type: ignore[attr-defined]
    actors: Optional[list[dict]] = None,
    occupancy_tint: Optional[dict] = None,
) -> dict:
    """Run all available pre-gates. Returns {verdict, blocking, gates[]}.
    verdict = FLAG if any CRITICAL/HIGH fired (the loop must fix the deterministic defect and
    re-render BEFORE the LLM panel); else PASS; SKIPPED only if literally nothing could run."""
    gates: list[dict] = []
    gates += gate_frame_lit(render_png)
    if scenegrid is not None:
        gates += gate_floor_contact_and_scale(scenegrid, camera, actors or [])
        gates += gate_occupancy(scenegrid, occupancy_tint)
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
# CLI
# ---------------------------------------------------------------------------
def _load_actors(arg: Optional[str]) -> list[dict]:
    if not arg:
        return []
    if arg.startswith("@"):
        return json.loads(Path(arg[1:]).read_text())
    return json.loads(arg)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic visual pre-gates for the visual-critic loop")
    ap.add_argument("--render", required=True, help="path to the rendered PNG")
    ap.add_argument("--scenegrid", help="path to the *.scenegrid.json (enables G2/G3/G4)")
    ap.add_argument("--actors", help="measured actor boxes JSON or @file.json")
    ap.add_argument("--occupancy", help="rendered occupancy tint JSON or @file.json")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    sg = load_scenegrid(args.scenegrid) if args.scenegrid else None
    actors = _load_actors(args.actors)
    occ = _load_actors(args.occupancy) if args.occupancy else None
    if occ is not None and not isinstance(occ, dict):
        import sys as _sys
        print(f"WARNING: --occupancy input is not a dict (got {type(occ).__name__}); G2 will be SKIPPED", file=_sys.stderr)
        occ = None
    res = run_pregates(args.render, scenegrid=sg, actors=actors, occupancy_tint=occ)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(res["summary"])
    return 2 if res["verdict"] == "FLAG" else 0


if __name__ == "__main__":
    raise SystemExit(main())
