#!/usr/bin/env python3
"""demo_reel.py — the STANDING walkable-loop pixel artifact (#1346 R2).

The owner's #1 visibility deliverable, made rerunnable: boot the REAL viewer on a seeded
2-room rest campaign and capture the full walkable loop as browser-level PNG frames of the
actual /openworlds/ UI — the rest scene, click-to-walk, approach-a-present-NPC, the parley
opening AT the actor, a door-cell cross, and arrival in the linked room.

ASSEMBLY (reuse, don't regress):
  - STATE + DRIVING is qa/walk_click_replay.py: its seed() (a 2-room rest campaign with a
    party PC + a present NPC + a shared doorway), its viewer boot with boot-log CAPTURE +
    one fresh-port retry, and its readiness probe are imported and reused verbatim. The
    engine is the SOLE WRITER — this script only SEEDS then drives the booted viewer.
  - PIXELS are captured at the BROWSER level by qa/demo_reel_capture.js (system Chrome via
    the existing qa/playwright install — NO new deps). It drives the loop through REAL DOM
    clicks on the rest board (the same onClick → POST /move the player fires), so the frames
    are the honest UI, not a synthetic render.

NO DM / NO LLM: walk_to_cell + parley_approach (generate_parley_options approach=True) are
engine-only; the viewer is a pure consumer.

PLATE RESOLUTION — the load-bearing gotcha (record for the next agent): the rest board paints
the location's scene plate from scope `location:<loc_id>` (build_combat_surface), which the
viewer's `_safe_scope` bridge (viewer/server.py:_scope_key) resolves by NAME-KEY: it drops the
kind prefix and matches the ingested art keyed `scene:<slug>`. So a plate renders ONLY when the
location id is a readable SLUG (e.g. canon `loc-lower-city` -> "lower-city" -> matches
`scene:lower-city`). This synthetic reel's seed (walk_click_replay) uses HASH ids
(`loc_<hex>`) which normalize to the bare hex and match NOTHING — so it renders a walkable
grid with NO plate, BY DESIGN. To capture a plate UNDER the loop, drive an ART-BACKED campaign
whose current location has a slug id + ingested plate: seed via qa/seed_canon_fixture.py
(baldurs-gate; current loc `loc-lower-city` has a servable plate + a 19x14 scene_grid), add a
present NPC on a walkable cell, and run demo_reel_capture.js directly against that booted
viewer (from the canonical checkout, where the gitignored _private art lives). The door steps
in demo_reel_capture.js are OPTIONAL — they skip cleanly when the location has no linked room.

Run:  uv run --directory servers/engine python "$PWD/qa/demo_reel.py" [--out DIR]
Exit 0 = every frame captured + verified non-black; non-zero (with a readable error) = the
first step that failed. This is a CI-adjacent artifact — it must fail loud.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

# Reuse walk_click_replay's seed + boot + readiness (STATE/DRIVING) without regressing it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import walk_click_replay as wcr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_JS = ROOT / "qa" / "demo_reel_capture.js"
DEFAULT_OUT = Path.home() / "worldos-session-notes" / "felt-frames" / "demo-reel-2026-07-08"

# The loop targets on the seeded 8x6 grid (hero starts at (1,4), NPC sits at (6,1)):
WALK_CELL = (6, 4)      # click-to-walk: a clear cell across the room
APPROACH_CELL = (6, 2)  # approach walk: a walkable cell adjacent (Chebyshev-1) to the NPC at (6,1)

CAPTIONS = {
    "01-rest-scene": "Rest scene: the party (Aldric) and a present NPC (Innkeeper Bram) stand on the walkable plate.",
    "02-walk-arrived": "Click-to-walk: the selected token glided along an engine-routed path to the clicked cell (6,4).",
    "03-approach-walk": "Approach: a second click walks Aldric up beside Bram, ready to talk.",
    "04-parley-open": "Clicking the NPC opens the parley AT the actor — the Dialogue screen, staged at Bram's cell.",
    "05-door-cell": "Back on the board: the northern doorway cell, selected for a cross into the linked room.",
    "06-arrived-linked-room": "After the door click: walked to the doorway and crossed into the linked Inner Hall — the return doorway back to the Antechamber is now offered.",
}
EXPECTED_FRAMES = list(CAPTIONS.keys())


def _die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    print(f"\nDEMO-REEL FAILED: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _png_size(p: Path) -> tuple[int, int]:
    """(w, h) from a PNG IHDR — no image deps."""
    with open(p, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        _die(f"{p.name} is not a PNG")
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def _mean_luma_sample(p: Path) -> float:
    """Cheap non-black check: decode the PNG (stdlib zlib) and average a stride sample of
    luma. A black/blank frame scores ~0; a rendered painterly board scores well above the
    threshold. Kept dependency-free (no PIL) so the reel runs anywhere the engine runs."""
    raw = p.read_bytes()
    # Concatenate all IDAT chunks, inflate, and un-filter enough rows to sample.
    pos = 8
    idat = bytearray()
    width = height = 0
    color_type = 0
    while pos + 8 <= len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        ctype = raw[pos + 4:pos + 8]
        data = raw[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, _bd, color_type = struct.unpack(">IIBB", data[:10])
        elif ctype == b"IDAT":
            idat += data
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if not idat or not width or not height:
        _die(f"{p.name}: could not read pixel data for the non-black check")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type, 4)
    stride = width * channels
    buf = zlib.decompress(bytes(idat))
    prev = bytearray(stride)
    total = 0.0
    count = 0
    for y in range(height):
        base = y * (stride + 1)
        if base + 1 + stride > len(buf):
            break
        ft = buf[base]
        row = bytearray(buf[base + 1: base + 1 + stride])
        # Un-filter (PNG filter types 0-4) so sampled bytes are true pixel values.
        for i in range(stride):
            a = row[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            x = row[i]
            if ft == 1:
                row[i] = (x + a) & 0xFF
            elif ft == 2:
                row[i] = (x + b) & 0xFF
            elif ft == 3:
                row[i] = (x + ((a + b) >> 1)) & 0xFF
            elif ft == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[i] = (x + pr) & 0xFF
        # Sample every ~37th pixel's first channel (luma proxy) to keep this cheap.
        for px in range(0, width, 37):
            total += row[px * channels]
            count += 1
        prev = row
    return (total / count) if count else 0.0


def _maybe_gif(out_dir: Path, frame_paths: list[Path]) -> bool:
    """Assemble a demo.gif if ffmpeg or ImageMagick is present; skip gracefully otherwise."""
    gif = out_dir / "demo.gif"
    if shutil.which("ffmpeg"):
        # Build a concat list so each frame holds ~1.6s.
        listing = out_dir / "_gif_frames.txt"
        listing.write_text("".join(f"file '{p.name}'\nduration 1.6\n" for p in frame_paths)
                           + f"file '{frame_paths[-1].name}'\n")
        r = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-vf", "scale=1000:-1:flags=lanczos", str(gif)],
            cwd=out_dir, capture_output=True, text=True,
        )
        listing.unlink(missing_ok=True)
        if r.returncode == 0 and gif.exists():
            return True
        print(f"  (ffmpeg gif assembly failed, skipping: {r.stderr.strip()[-200:]})", file=sys.stderr)
        return False
    magick = shutil.which("magick") or shutil.which("convert")
    if magick:
        cmd = [magick] + (["convert"] if magick.endswith("convert") is False and Path(magick).name == "magick" else [])
        r = subprocess.run(
            [magick, "-delay", "160", "-loop", "0", "-resize", "1000x"]
            + [str(p) for p in frame_paths] + [str(gif)],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and gif.exists():
            return True
        print(f"  (ImageMagick gif assembly failed, skipping: {r.stderr.strip()[-200:]})", file=sys.stderr)
        return False
    print("  (no ffmpeg/ImageMagick — skipping demo.gif)")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Capture the WorldOS walkable-loop demo reel.")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output dir for frames + captions")
    ap.add_argument("--keep-viewer-log", action="store_true", help="print the viewer boot log on success too")
    args = ap.parse_args()

    if not shutil.which("node"):
        _die("node is not on PATH — the browser capture half needs it")
    # Resolve the Playwright install: the repo-local qa/playwright/node_modules is the default, but a
    # git worktree won't have that untracked dir — WORLDOS_PLAYWRIGHT_NM (a node_modules dir) overrides
    # it so the reel runs from a worktree against a canonical checkout's install (no new deps either way).
    pw_override = os.environ.get("WORLDOS_PLAYWRIGHT_NM")
    pw_nm = Path(pw_override) if pw_override else (ROOT / "qa" / "playwright" / "node_modules")
    pw = pw_nm / "playwright"
    if not pw.exists():
        _die(f"the qa/playwright install is missing ({pw}); run its npm install once "
             f"(or set WORLDOS_PLAYWRIGHT_NM to a node_modules dir that has it) — no new deps added here")

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="demo-reel-") as td:
        state_dir = str(Path(td) / "state")
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        ids = wcr.seed(state_dir)

        env = dict(os.environ)
        env["WORLDOS_STATE_DIR"] = state_dir
        env["WORLDOS_PLAYER_MOVES"] = str(Path(td) / "moves.ndjson")  # enables the /move write path

        # Boot the viewer with output CAPTURED + one fresh-port retry (reused verbatim from
        # walk_click_replay.main so a silent import/bind failure surfaces, not a generic timeout).
        boot_log = Path(td) / "viewer-boot.log"
        proc = None
        base = ""
        for attempt in (1, 2):
            port = wcr._free_port()
            base = f"http://127.0.0.1:{port}"
            with open(boot_log, "wb") as lf:
                proc = subprocess.Popen(
                    [sys.executable, str(wcr.VIEWER), wcr.CID, str(port)],
                    env=env, stdout=lf, stderr=subprocess.STDOUT,
                )
            try:
                wcr._wait_ready(base)
                break
            except RuntimeError:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                tail = boot_log.read_bytes()[-2000:].decode(errors="replace")
                if attempt == 2:
                    _die(f"viewer never came up; boot log tail:\n{tail}")
                print(f"boot attempt {attempt} failed (bind race or slow boot) — retrying on a "
                      f"fresh port; log tail:\n{tail}", file=sys.stderr)

        try:
            print(f"viewer up on {base} (campaign {wcr.CID}) — driving the loop through the real UI")
            cap_env = dict(os.environ)
            cap_env["WORLDOS_PW_MODULE"] = str(pw)
            cap = subprocess.run(
                ["node", str(CAPTURE_JS), base, "Aldric", "Innkeeper Bram",
                 str(WALK_CELL[0]), str(WALK_CELL[1]), str(APPROACH_CELL[0]), str(APPROACH_CELL[1]),
                 str(out_dir)],
                capture_output=True, text=True, timeout=180, env=cap_env,
            )
            if cap.returncode != 0:
                _die(f"browser capture failed (exit {cap.returncode}):\n{cap.stdout}\n{cap.stderr}")
            try:
                manifest = json.loads(cap.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                _die(f"could not parse the capture manifest:\n{cap.stdout}\n{cap.stderr}")
            if manifest.get("pageErrors"):
                print(f"  note: page errors during capture: {manifest['pageErrors']}", file=sys.stderr)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            if args.keep_viewer_log:
                print(f"--- viewer boot log ---\n{boot_log.read_text(errors='replace')}")

    # ── verify every expected frame is on disk, a real PNG, non-trivial, and non-black ──────────
    frame_paths: list[Path] = []
    for name in EXPECTED_FRAMES:
        p = out_dir / f"{name}.png"
        if not p.exists():
            _die(f"expected frame {p.name} was never written")
        size = p.stat().st_size
        if size < 20_000:
            _die(f"{p.name} is only {size} bytes — trivially small (likely blank)")
        w, h = _png_size(p)
        if w < 400 or h < 300:
            _die(f"{p.name} is {w}x{h} — too small to be the real UI")
        luma = _mean_luma_sample(p)
        if luma < 8.0:
            _die(f"{p.name} mean luma {luma:.1f} — frame is black/blank")
        print(f"  OK  {p.name}  ({w}x{h}, {size // 1024} KiB, luma {luma:.0f})")
        frame_paths.append(p)

    # captions.txt — one line per frame.
    captions = out_dir / "captions.txt"
    captions.write_text("".join(f"{name}.png  —  {CAPTIONS[name]}\n" for name in EXPECTED_FRAMES))
    print(f"  captions -> {captions}")

    gif_made = _maybe_gif(out_dir, frame_paths)
    print(f"\ndemo_reel: {len(frame_paths)} frames verified + captions written to {out_dir}"
          f"{' (+ demo.gif)' if gif_made else ''} ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
