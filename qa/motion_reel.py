#!/usr/bin/env python3
"""Build a MOTION REEL — a contact-sheet PNG + a JSON sidecar — for the visual-critic L7 motion lens.

WHY THIS EXISTS
---------------
The visual-critic v2 scored a single STILL. But WorldOS is pivoting characters from flat billboards
to real-time 3D animated actors, and a still cannot tell you whether the actor is ALIVE: a
beautiful frozen pose and a beautiful walk both score the same on a still. The L7 MOTION lens (see
.claude/skills/visual-critic/SKILL.md) and the deterministic G5 motion-liveness pre-gate
(qa/visual_pregate.py) both score a REEL — an ordered sequence of frames spanning idle / locomotion
/ attack / hit-react / death. This module assembles that reel into:

  1. a CONTACT-SHEET PNG  — the N frames laid out left-to-right, top-to-bottom in a grid, in order,
     so one image (cheap to hand to an opus lens) shows the whole motion at a glance; and
  2. a JSON SIDECAR       — per-frame metadata {frame_idx, label/anim, actor centroid, t_ms, engine
     event, is_move, the frame's own PNG path} that G5 and the L7 lens read.

TWO MODES
---------
* MODE A "engine-state reel" — given a campaign id + a list of N beats, fetch successive combat-
  surface states from the engine and render each via the existing Unity render path, then assemble.
  The ENGINE-FETCH (:func:`fetch_combat_surface_states`) is still a clearly-marked HOOK (the real
  engine read is a TODO; the engine is the SOLE WRITER and Unity is the renderer — this module
  never writes game state). The UNITY-CAPTURE (:func:`render_state_via_unity`, #1415) is wired
  against the documented ``manage_camera`` pattern (``extensions/renderers/unity/BOX.md``) —
  env-gated on ``WORLDOS_UNITY_MCP_URL`` and mockable via ``mcp_call=``; the exact live ``:8080/mcp``
  round-trip shape is UNVERIFIED on this lane (no GEX44 box access) and queues for validation behind
  the next box session. The reel ASSEMBLY (contact-sheet + sidecar) from the produced PNG list is
  REAL and tested.
* MODE B "timeline reel" — given a directory of already-rendered animation frame PNGs (e.g. a
  Meshy/PixelLab/Unity timeline export), assemble the contact-sheet + sidecar directly. Fully real.

The contact-sheet uses Pillow when available (clean compositing); when Pillow is absent it falls
back to a pure-stdlib PNG tiler so the reel still assembles in the engine's uv env (which has no
Pillow). stdlib-first, no engine import, no game-state writes.

USAGE
-----
    # MODE B — assemble a reel from a directory of frame PNGs:
    python qa/motion_reel.py --mode timeline --frames-dir /tmp/walk_frames \
        --out /tmp/hero_walk_reel.png --label walk --move

    # MODE B — explicit ordered frames with per-frame labels (a JSON manifest):
    python qa/motion_reel.py --mode timeline --frames @/tmp/frames.json --out /tmp/reel.png

    # MODE A — engine-state reel (interface documented; engine-fetch is still a TODO hook, capture
    # is wired but unverified off the box — set WORLDOS_UNITY_MCP_URL to opt in):
    python qa/motion_reel.py --mode engine --campaign demo-crypt --beats 6 --out /tmp/beat_reel.png

    # From Python:
    from motion_reel import build_timeline_reel, build_engine_reel
    res = build_timeline_reel(["/tmp/f0.png", "/tmp/f1.png"], out_png="/tmp/reel.png",
                              labels=["idle", "idle"])
    print(res["contact_sheet"], res["sidecar"])

The sidecar JSON shape (also the `--reel` input qa/visual_pregate.py G5 reads — it accepts either
this whole object, keyed on `frames`, or a bare list of the frame dicts):
    {
      "schema": "worldos.motion-reel.v1",
      "mode": "timeline" | "engine",
      "scene": "<scene/campaign id or null>",
      "contact_sheet": "<path to the assembled grid PNG>",
      "cols": int, "rows": int, "cell_w": int, "cell_h": int,
      "frames": [
        {"frame_idx": 0, "frame": "<png path>", "label": "idle", "is_move": false,
         "t_ms": 0, "engine_event": null, "centroid_px": [x, y] | null}, ...
      ]
    }

Exit codes: 0 = reel built, 2 = nothing to assemble (no frames / unreadable inputs).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import sys
import zlib
from pathlib import Path
from typing import Optional

SCHEMA = "worldos.motion-reel.v1"

# Labels that count as a MOVE for the G5 displacement check + the L7 locomotion sub-dim.
MOVE_LABELS = ("walk", "run", "move", "locomot", "step", "approach", "charge")


# ===========================================================================
# PNG decode / encode — Pillow-first, stdlib fallback (mirrors qa/visual_pregate.py).
# ===========================================================================
def _read_rgb(path: str | Path) -> Optional[tuple[bytearray, int, int]]:
    """Read a PNG into (rgb_bytes, width, height) as packed 8-bit RGB (3 bytes/px). Tries Pillow,
    else a stdlib PNG decode (8-bit RGB/RGBA, non-interlaced). None if it can't be decoded."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        from PIL import Image  # type: ignore
        im = Image.open(p).convert("RGB")
        w, h = im.size
        return bytearray(im.tobytes()), w, h
    except Exception:
        pass
    try:
        return _stdlib_decode_rgb(p)
    except Exception:
        return None


def _stdlib_decode_rgb(p: Path) -> tuple[bytearray, int, int]:
    """Minimal PNG decoder (8-bit RGB/RGBA, no interlace) -> packed RGB. stdlib only."""
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
    # Strip alpha to packed RGB if needed.
    if channels == 3:
        return out, width, height
    rgb = bytearray(width * height * 3)
    for px in range(width * height):
        rgb[px * 3:px * 3 + 3] = out[px * 4:px * 4 + 3]
    return rgb, width, height


def _write_png_rgb(path: str | Path, rgb: bytes, w: int, h: int) -> None:
    """Write packed 8-bit RGB to a PNG (filter type 0 rows). stdlib only."""
    stride = w * 3
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter: None
        raw += rgb[y * stride:(y + 1) * stride]
    comp = zlib.compress(bytes(raw), 6)

    def _chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", comp) + _chunk(b"IEND", b"")
    )


def _nearest_resize(src: bytearray, sw: int, sh: int, dw: int, dh: int) -> bytearray:
    """Nearest-neighbour resize of packed RGB. Cheap + dependency-free; good enough for a contact
    sheet thumbnail (the lens reads the motion, not pixel-perfect fidelity)."""
    dst = bytearray(dw * dh * 3)
    for dy in range(dh):
        sy = min(sh - 1, dy * sh // dh)
        for dx in range(dw):
            sx = min(sw - 1, dx * sw // dw)
            s = (sy * sw + sx) * 3
            d = (dy * dw + dx) * 3
            dst[d:d + 3] = src[s:s + 3]
    return dst


def _centroid(rgb: bytearray, w: int, h: int) -> Optional[list[float]]:
    """Luminance-weighted bright-mass centroid (x,y) in source px, or None if no bright mass.
    Records the actor's screen position per frame so G5/L7 can read displacement across a move."""
    total = 0.0
    sx = 0.0
    sy = 0.0
    for i in range(w * h):
        r, g, b = rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        total += lum
        sx += lum * (i % w)
        sy += lum * (i // w)
    if total <= 0:
        return None
    return [round(sx / total, 2), round(sy / total, 2)]


# ===========================================================================
# Reel assembly — the REAL, tested core (contact-sheet grid + JSON sidecar).
# ===========================================================================
def _grid_dims(n: int) -> tuple[int, int]:
    """Choose a near-square grid (cols, rows) for n cells (cols >= rows; row-major fill)."""
    if n <= 0:
        return 0, 0
    cols = 1
    while cols * cols < n:
        cols += 1
    rows = (n + cols - 1) // cols
    return cols, rows


def assemble_contact_sheet(
    frame_paths: list[str | Path],
    out_png: str | Path,
    *,
    cell_max: int = 256,
    bg: tuple[int, int, int] = (24, 24, 28),
) -> dict:
    """Tile the ordered frame PNGs into ONE contact-sheet PNG (row-major, near-square grid).

    Each frame is read, resized to fit a <= cell_max box (aspect-preserved, centered in its cell on
    a dark bg), and pasted in order. Returns {contact_sheet, cols, rows, cell_w, cell_h, centroids,
    sizes} — centroids[i] is the per-frame bright-mass centroid in CELL px (used by the sidecar).
    Raises ValueError if no frame is decodable (the caller maps that to exit 2)."""
    decoded: list[tuple[bytearray, int, int]] = []
    for fp in frame_paths:
        d = _read_rgb(fp)
        if d is None:
            raise ValueError(f"motion_reel: cannot decode frame {fp}")
        decoded.append(d)
    if not decoded:
        raise ValueError("motion_reel: no frames to assemble")

    # Cell size = the largest fit-box across all frames, capped at cell_max.
    cell_w = min(cell_max, max(w for _, w, _ in decoded))
    cell_h = min(cell_max, max(h for _, _, h in decoded))
    cols, rows = _grid_dims(len(decoded))
    sheet_w = cols * cell_w
    sheet_h = rows * cell_h
    sheet = bytearray(bytes(bg) * (sheet_w * sheet_h))

    centroids: list[Optional[list[float]]] = []
    sizes: list[list[int]] = []
    for idx, (rgb, w, h) in enumerate(decoded):
        # Fit into the cell, aspect-preserved.
        scale = min(cell_w / w, cell_h / h, 1.0) if (w and h) else 1.0
        tw = max(1, int(w * scale))
        th = max(1, int(h * scale))
        thumb = _nearest_resize(rgb, w, h, tw, th) if (tw != w or th != h) else rgb
        sizes.append([tw, th])
        # Centroid of the thumbnail (cell-px space — the space G5 compares displacement in).
        centroids.append(_centroid(thumb, tw, th))
        # Top-left of this cell + centering offset.
        gx = (idx % cols) * cell_w + (cell_w - tw) // 2
        gy = (idx // cols) * cell_h + (cell_h - th) // 2
        for ty in range(th):
            dst = ((gy + ty) * sheet_w + gx) * 3
            src = ty * tw * 3
            sheet[dst:dst + tw * 3] = thumb[src:src + tw * 3]

    _write_png_rgb(out_png, bytes(sheet), sheet_w, sheet_h)
    return {
        "contact_sheet": str(out_png),
        "cols": cols, "rows": rows,
        "cell_w": cell_w, "cell_h": cell_h,
        "centroids": centroids,
        "sizes": sizes,
    }


def _frame_meta(
    idx: int,
    frame_path: str,
    *,
    label: str,
    is_move: Optional[bool],
    t_ms: Optional[int],
    engine_event: Optional[str],
    centroid: Optional[list[float]],
) -> dict:
    """One sidecar frame record. is_move defaults to True when the label is a move label."""
    if is_move is None:
        is_move = any(m in label.lower() for m in MOVE_LABELS)
    return {
        "frame_idx": idx,
        "frame": frame_path,
        "label": label,
        "is_move": bool(is_move),
        "t_ms": t_ms,
        "engine_event": engine_event,
        "centroid_px": centroid,
    }


def write_sidecar(sidecar: dict, out_path: str | Path) -> str:
    Path(out_path).write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")
    return str(out_path)


# ---------------------------------------------------------------------------
# MODE B — timeline reel (a directory or explicit list of frame PNGs). Fully real.
# ---------------------------------------------------------------------------
def build_timeline_reel(
    frames: list,
    out_png: str | Path,
    *,
    labels: Optional[list[str]] = None,
    moves: Optional[list[bool]] = None,
    t_ms: Optional[list[int]] = None,
    events: Optional[list[Optional[str]]] = None,
    scene: Optional[str] = None,
    cell_max: int = 256,
) -> dict:
    """Assemble a reel from an ordered list of frame PNGs (MODE B).

    ``frames`` is either a list of path strings, or a list of dicts
    ``{"frame"/"path": str, "label"?: str, "is_move"?: bool, "t_ms"?: int, "engine_event"?: str}``.
    Explicit ``labels``/``moves``/``t_ms``/``events`` lists (when given) override / supply per-frame
    metadata positionally. Writes the contact-sheet to ``out_png`` and the sidecar to
    ``out_png`` with a ``.json`` suffix; returns the sidecar dict (with ``contact_sheet`` + ``sidecar``)."""
    # Normalize frames -> (path, meta-overrides) pairs.
    norm: list[tuple[str, dict]] = []
    for i, fr in enumerate(frames):
        if isinstance(fr, dict):
            path = fr.get("frame") or fr.get("path")
            if not path:
                raise ValueError(f"motion_reel: frame {i} dict has no 'frame'/'path' key")
            norm.append((str(path), fr))
        else:
            norm.append((str(fr), {}))
    if not norm:
        raise ValueError("motion_reel: no frames provided")

    paths = [p for p, _ in norm]
    sheet = assemble_contact_sheet(paths, out_png, cell_max=cell_max)

    frame_records: list[dict] = []
    for i, (path, meta) in enumerate(norm):
        label = (labels[i] if labels and i < len(labels) else None) or meta.get("label") or meta.get("anim") or "frame"
        is_move = (moves[i] if moves and i < len(moves) else None)
        if is_move is None:
            is_move = meta.get("is_move")
        tm = (t_ms[i] if t_ms and i < len(t_ms) else None)
        if tm is None:
            tm = meta.get("t_ms")
        ev = (events[i] if events and i < len(events) else None) or meta.get("engine_event")
        frame_records.append(_frame_meta(
            i, path, label=str(label), is_move=is_move, t_ms=tm, engine_event=ev,
            centroid=sheet["centroids"][i] if i < len(sheet["centroids"]) else None,
        ))

    sidecar = {
        "schema": SCHEMA,
        "mode": "timeline",
        "scene": scene,
        "contact_sheet": sheet["contact_sheet"],
        "cols": sheet["cols"], "rows": sheet["rows"],
        "cell_w": sheet["cell_w"], "cell_h": sheet["cell_h"],
        "frames": frame_records,
    }
    sidecar_path = str(Path(out_png).with_suffix(".json"))
    write_sidecar(sidecar, sidecar_path)
    sidecar["sidecar"] = sidecar_path
    return sidecar


def _natural_key(name: str) -> list:
    """Natural-sort key: split on digit runs so '2.png' sorts before '10.png' (a plain string sort
    would order '10' before '2'). Digit chunks compare as ints, text chunks as lowercase strings."""
    return [int(tok) if tok.isdigit() else tok.lower()
            for tok in re.split(r"(\d+)", name) if tok != ""]


def _frames_from_dir(frames_dir: str | Path) -> list[str]:
    """All *.png in a directory in NATURAL frame order (so an unpadded export — frame2, frame10 —
    assembles as 2,10 not 10,2). Sorts by the file name's natural key."""
    d = Path(frames_dir)
    return [str(p) for p in sorted(d.glob("*.png"), key=lambda p: _natural_key(p.name))]


# ---------------------------------------------------------------------------
# MODE A — engine-state reel. Interface documented; capture is a clearly-marked hook.
# ---------------------------------------------------------------------------
def fetch_combat_surface_states(campaign_id: str, beats: int) -> list[dict]:
    """HOOK (TODO): fetch ``beats`` successive combat-surface states for ``campaign_id`` from the
    engine. The engine is the SOLE WRITER of game state — this is READ-ONLY (it pulls snapshots,
    never mutates). The real wiring reads the same combat-surface the Unity renderer consumes (the
    snapshot.json / engine MCP combat-surface state per beat). Returns one state dict per beat.

    Until wired, this returns ``beats`` placeholder state stubs so the interface + the reel-assembly
    path are exercisable; the assembly itself is what this module guarantees is real + tested."""
    # TODO(worldos): replace with the real engine read (e.g. snapshot per beat via the engine MCP /
    # play-state dir). MUST stay read-only — never write game state from the QA/reel side.
    return [{"campaign_id": campaign_id, "beat": i, "_stub": True} for i in range(beats)]


# Env var naming the live Unity MCP HTTP endpoint (BOX.md: "curl 127.0.0.1:8080/mcp returns 406
# (server healthy)" — the documented health-check path on the GEX44 box). Unset (the default
# everywhere except a claimed GEX44 session) means "no live host configured" -> render_state_via_
# unity returns None immediately, with NO network attempt — this is what keeps the default path
# safe to run anywhere (CI, a laptop) without risking a stray connection to whatever happens to be
# on localhost:8080.
UNITY_MCP_URL_ENV = "WORLDOS_UNITY_MCP_URL"


def _default_unity_mcp_call(tool: str, params: dict, *, url: str, timeout: float = 30.0) -> dict:
    """Real transport: POST one MCP ``tools/call`` JSON-RPC request to ``url`` (the live Unity MCP
    HTTP endpoint documented in ``extensions/renderers/unity/BOX.md``, e.g.
    ``http://127.0.0.1:8080/mcp``). stdlib-only (``urllib``), no new dependency.

    This function performs REAL network I/O and is deliberately never exercised by the test suite
    (tests inject a stub via ``render_state_via_unity(..., mcp_call=...)``) — this lane has no GEX44
    box access, so the exact wire round-trip is UNVERIFIED here; live validation queues behind the
    next box session (see the gex44-unity-host skill)."""
    import urllib.error
    import urllib.request

    payload = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool, "arguments": params},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (documented internal host)
        return json.loads(resp.read().decode("utf-8"))


def render_state_via_unity(
    state: dict,
    out_png: str | Path,
    *,
    mcp_call=None,
    screenshot_super_size: int = 2,
) -> Optional[str]:
    """Render ONE combat-surface state to ``out_png`` via the documented ``manage_camera`` capture
    (``extensions/renderers/unity/BOX.md``: ``screenshot_super_size`` 2-4, captures land in
    ``Assets/Screenshots/`` on the live Unity MCP host). Returns the copied PNG path on success, or
    ``None`` if the capture is unavailable in this environment (the common case off the GPU box) —
    this function NEVER raises; every failure mode degrades to ``None`` so ``build_engine_reel``'s
    existing ``no_render`` status handles it exactly like the old stub did.

    ``mcp_call`` is the injectable MCP transport — ``(tool_name, params_dict) -> response_dict`` —
    so this is fully unit-testable with a mocked call and NO box/network access. When omitted, the
    real transport (:func:`_default_unity_mcp_call`) is used, but ONLY if ``WORLDOS_UNITY_MCP_URL``
    is set (the documented live-box endpoint, e.g. ``http://127.0.0.1:8080/mcp``) — with no URL
    configured (every environment except a claimed GEX44 session), this returns ``None``
    immediately with zero network I/O, matching ``state`` unused by the state itself beyond
    identifying what to capture (the live camera/scene already reflects ``state`` by the time this
    is called; this hook only triggers + fetches the screenshot).

    This is deliberately UNVERIFIED against the live box on this lane (no GEX44 access this pass —
    see BOX.md's single-tenant claim discipline): the orchestration/parsing here is code-complete
    and covered by injected-stub unit tests; the real ``:8080/mcp`` round-trip shape queues for
    validation behind the next box session."""
    call = mcp_call
    if call is None:
        url = os.environ.get(UNITY_MCP_URL_ENV)
        if not url:
            return None  # no live Unity MCP host configured -> the documented off-box fallback

        def call(tool: str, params: dict) -> dict:
            return _default_unity_mcp_call(tool, params, url=url)

    try:
        resp = call("manage_camera",
                     {"action": "screenshot", "screenshot_super_size": screenshot_super_size})
    except Exception:
        return None

    result = resp.get("result") if isinstance(resp, dict) else None
    captured = None
    if isinstance(result, dict):
        captured = result.get("path") or result.get("screenshot_path")
    if not captured:
        return None

    src = Path(captured)
    if not src.exists():
        return None
    dst = Path(out_png)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return str(dst)


def build_engine_reel(
    campaign_id: str,
    beats: int,
    out_png: str | Path,
    *,
    cell_max: int = 256,
    frame_dir: Optional[str | Path] = None,
    _states: Optional[list[dict]] = None,
    _renderer=None,
) -> dict:
    """Assemble an engine-state reel (MODE A): fetch N combat-surface states, render each via Unity,
    then assemble the produced PNGs into a contact-sheet + sidecar.

    The ENGINE-FETCH (:func:`fetch_combat_surface_states`) is still a HOOK — the live engine read is
    a TODO (it needs the GPU box + a running editor). The UNITY-CAPTURE (:func:`render_state_via_unity`,
    #1415) is now wired against the documented ``manage_camera`` pattern, but its live ``:8080/mcp``
    round-trip is UNVERIFIED on this lane (no GEX44 box access) and queues behind the next box
    session. What is REAL and unit-tested here is the orchestration + the assembly: when renders ARE
    produced (live, or supplied via ``frame_dir`` of pre-rendered per-beat PNGs, or via the
    ``_renderer`` injection in tests), this builds the same contact-sheet + sidecar as MODE B,
    one frame per beat, labelled ``beat<N>`` and carrying the beat index as t_ms-equivalent ordering.

    Returns the sidecar dict (with ``contact_sheet`` + ``sidecar``), or a dict with
    ``{"status": "no_render", ...}`` when no frames could be produced (exit 2 at the CLI)."""
    states = _states if _states is not None else fetch_combat_surface_states(campaign_id, beats)
    renderer = _renderer or render_state_via_unity

    produced: list[str] = []
    out_dir = Path(out_png).parent
    for i, st in enumerate(states):
        # Prefer a supplied pre-rendered per-beat PNG (frame_dir/beat_<i>.png) if present.
        png_path: Optional[str] = None
        if frame_dir is not None:
            cand = Path(frame_dir) / f"beat_{i}.png"
            if cand.exists():
                png_path = str(cand)
        if png_path is None:
            tgt = out_dir / f"_engine_beat_{i}.png"
            png_path = renderer(st, tgt)
        if png_path and Path(png_path).exists():
            produced.append(png_path)

    if not produced:
        return {
            "schema": SCHEMA, "mode": "engine", "scene": campaign_id,
            "status": "no_render",
            "detail": ("no frames were produced — the Unity capture hook returned no PNG and no "
                       "pre-rendered frame_dir/beat_<i>.png was supplied. Wire render_state_via_unity "
                       "(GPU box) or pass --frame-dir of per-beat PNGs."),
            "frames": [],
        }

    # Assemble exactly like MODE B, with engine-beat labels.
    labels = [f"beat{i}" for i in range(len(produced))]
    sidecar = build_timeline_reel(
        produced, out_png, labels=labels, scene=campaign_id, cell_max=cell_max,
        # beat ordering doubles as a coarse t_ms so timing-sync has a monotonic axis.
        t_ms=[i * 1000 for i in range(len(produced))],
    )
    sidecar["mode"] = "engine"
    # Re-write the sidecar with the corrected mode tag.
    write_sidecar({k: v for k, v in sidecar.items() if k != "sidecar"}, sidecar["sidecar"])
    return sidecar


# ===========================================================================
# CLI
# ===========================================================================
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build a motion REEL (contact-sheet + sidecar) for the visual-critic L7 lens")
    ap.add_argument("--mode", choices=("timeline", "engine"), required=True,
                    help="timeline = assemble from rendered frame PNGs; engine = fetch+render N beats "
                         "(engine-fetch is a TODO hook; capture needs WORLDOS_UNITY_MCP_URL set)")
    ap.add_argument("--out", required=True, help="output contact-sheet PNG path (sidecar is <out>.json)")
    ap.add_argument("--cell-max", type=int, default=256, help="max per-cell thumbnail edge (default 256)")
    # MODE B
    ap.add_argument("--frames-dir", help="(timeline) directory of *.png frames, sorted by name")
    ap.add_argument("--frames", help="(timeline) explicit frames: JSON list of paths/dicts, or @file.json")
    ap.add_argument("--label", default=None, help="(timeline) apply this label to every frame (e.g. idle/walk/attack)")
    ap.add_argument("--move", action="store_true", help="(timeline) mark every frame as a MOVE beat (for the G5 displacement check)")
    ap.add_argument("--scene", default=None, help="scene/campaign id stamped into the sidecar")
    # MODE A
    ap.add_argument("--campaign", help="(engine) campaign id to fetch combat-surface states for")
    ap.add_argument("--beats", type=int, default=4, help="(engine) number of beats to reel (default 4)")
    ap.add_argument("--frame-dir", help="(engine) directory of pre-rendered per-beat PNGs (beat_<i>.png) to use instead of the live capture hook")
    args = ap.parse_args(argv)

    if args.mode == "timeline":
        if args.frames:
            raw = Path(args.frames[1:]).read_text() if args.frames.startswith("@") else args.frames
            frames = json.loads(raw)
        elif args.frames_dir:
            frames = _frames_from_dir(args.frames_dir)
        else:
            print("motion_reel: timeline mode needs --frames-dir or --frames", file=sys.stderr)
            return 2
        if not frames:
            print("motion_reel: no frames found to assemble", file=sys.stderr)
            return 2
        labels = [args.label] * len(frames) if args.label else None
        moves = [True] * len(frames) if args.move else None
        try:
            res = build_timeline_reel(frames, args.out, labels=labels, moves=moves,
                                      scene=args.scene, cell_max=args.cell_max)
        except ValueError as exc:
            print(f"motion_reel: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"contact_sheet": res["contact_sheet"], "sidecar": res["sidecar"],
                          "frames": len(res["frames"]), "cols": res["cols"], "rows": res["rows"]}, indent=2))
        return 0

    # engine mode
    if not args.campaign:
        print("motion_reel: engine mode needs --campaign", file=sys.stderr)
        return 2
    res = build_engine_reel(args.campaign, args.beats, args.out,
                            cell_max=args.cell_max, frame_dir=args.frame_dir)
    if res.get("status") == "no_render":
        print(json.dumps(res, indent=2), file=sys.stderr)
        return 2
    print(json.dumps({"contact_sheet": res["contact_sheet"], "sidecar": res["sidecar"],
                      "frames": len(res["frames"]), "mode": res["mode"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
