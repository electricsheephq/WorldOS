#!/usr/bin/env node
/*
 * player_smoke_driver.js — the SCRIPTED (no LLM) sequence for qa/player_smoke.sh (#1443).
 *
 * Drives the EXACT SAME primitives native_palette_server.js exposes to the T3 blind-player agent
 * (native_palette_core.js: findWindow/captureWindow/clickAt — #1456 SCK capture, no activation) but through a fixed
 * script instead of an MCP tool loop, so a post-build smoke run needs no LLM call: screenshot ->
 * click a known walkable grid cell (move) -> capture glide frames -> screenshot -> click the
 * goblin's cell (on-turn attack) -> capture glide frames -> screenshot. Assertions on the
 * resulting game state (mover's cell changed, goblin HP dropped) are done by the CALLER
 * (qa/player_smoke.sh, reading the campaign snapshot) — this driver's job is purely the
 * cross-Space capture/click plumbing + reporting inter-frame hashes for the motion-liveness check.
 *
 * Click targets are computed from GRID CELL coordinates via the locked dimetric camera projection
 * (qa/visual_pregate.py::CameraSpec is the source-of-truth authority this mirrors — orthoSize=13,
 * pitch=30deg, yaw=45deg, camera pulled back 80 world units, isotropic 2.0-world-unit cells) with
 * ASPECT DERIVED FROM THE ACTUAL CAPTURED WINDOW SIZE (not the pregate's fixed 1920x1097) since the
 * live player window can open at any resolution — an orthographic camera's horizontal half-width
 * scales with aspect, so this keeps the projection correct regardless of window size.
 *
 * Usage:
 *   node player_smoke_driver.js --rundir <dir> --owner WorldOSPlayer --cols 16 --rows 12 \
 *     --hero-cell 7,9 --goblin-cell 10,8 --walk-cell 8,9 [--helper <bin>] [--fullscreen-fallback]
 *
 * Prints ONE JSON summary line to stdout: {ok, reason?, window, screenshots:[...],
 *   glide_move:[...], glide_attack:[...], glide_move_distinct, glide_attack_distinct}
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const core = require("./native_palette_core.js");

// ---- CLI args ---------------------------------------------------------------
function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const key = a.slice(2);
    if (key === "fullscreen-fallback" || key === "activate-fallback") { out[key] = true; continue; }
    out[key] = argv[++i];
  }
  return out;
}
function cell(s) { const [c, r] = String(s).split(",").map(Number); return { c, r }; }

const args = parseArgs(process.argv.slice(2));
const RUNDIR = args.rundir || path.join(require("os").tmpdir(), "worldos-smoke-run");
const OWNER = args.owner || "WorldOSPlayer";
const COLS = Number(args.cols || 16);
const ROWS = Number(args.rows || 12);
const HERO = cell(args["hero-cell"] || "7,9");
const GOBLIN = cell(args["goblin-cell"] || "10,8");
const WALK = cell(args["walk-cell"] || "8,9");
const HELPER = args.helper || "";
const FULLSCREEN_FALLBACK = !!args["fullscreen-fallback"];
// #1466/#1483: brief activate->click->restore escape. pid-posted CGEvents deliver to the player PID but
// produce ZERO Unity input (Unity's Input samples only the FOREGROUND app), so this is the PROVEN working
// click path (sub-second activation, no Space switch — within the windowed no-hijack policy). Now ON by
// DEFAULT (the smoke lane was red on the pure-PID default since w6batch); WORLDOS_CLICK_ACTIVATE_FALLBACK=0
// opts back out to pure PID delivery (currently non-functional for Unity). The env opt-out is
// AUTHORITATIVE — it must win over the explicit --activate-fallback flag too (matches
// native_palette_server.js's CLICK_ACTIVATE_FALLBACK exactly, which has no such flag to override it).
const ACTIVATE_FALLBACK = process.env.WORLDOS_CLICK_ACTIVATE_FALLBACK !== "0";
const GLIDE_FRAMES = 4;
const GLIDE_INTERVAL_MS = 150;
const SETTLE_MS = 500;

const SHOTS = path.join(RUNDIR, "player", "screenshots");
fs.mkdirSync(SHOTS, { recursive: true });

const helperCmd = core.resolveHelper(path.join(RUNDIR, "player"), HELPER);
const captureState = {};
let seq = 0;

function sleepMs(ms) { spawnSync("sleep", [String(ms / 1000)]); }

function shoot(label) {
  seq += 1;
  const name = "smoke-" + String(seq).padStart(3, "0") + "-" + label + ".png";
  const file = path.join(SHOTS, name);
  const cap = core.captureWindow({
    helperCmd, owner: OWNER, outFile: file, fullscreenFallback: FULLSCREEN_FALLBACK, state: captureState,
  });
  return { ...cap, file, rel: path.join("player", "screenshots", name) };
}

// ---- the locked dimetric camera projection (mirrors qa/visual_pregate.py::CameraSpec) ----------
const ORTHO_SIZE = 13.0, PITCH_DEG = 30.0, YAW_DEG = 45.0, CAM_DIST = 80.0;
function cellToWorld(c, r, cols, rows) {
  const cx0 = (cols - 1) / 2.0, cy0 = (rows - 1) / 2.0;
  return { wx: (c - cx0) * 2.0, wy: 0.0, wz: (cy0 - r) * 2.0 };
}
function worldToScreen(wx, wy, wz, pxW, pxH) {
  const p = (PITCH_DEG * Math.PI) / 180, y = (YAW_DEG * Math.PI) / 180;
  const fwd = [Math.sin(y) * Math.cos(p), -Math.sin(p), Math.cos(y) * Math.cos(p)];
  const right = [Math.cos(y), 0.0, -Math.sin(y)];
  const up = [
    fwd[1] * right[2] - fwd[2] * right[1],
    fwd[2] * right[0] - fwd[0] * right[2],
    fwd[0] * right[1] - fwd[1] * right[0],
  ];
  const pos = [-fwd[0] * CAM_DIST, -fwd[1] * CAM_DIST, -fwd[2] * CAM_DIST];
  const dx = wx - pos[0], dy = wy - pos[1], dz = wz - pos[2];
  const camR = dx * right[0] + dy * right[1] + dz * right[2];
  const camU = dx * up[0] + dy * up[1] + dz * up[2];
  const halfH = ORTHO_SIZE;
  const aspect = pxW / pxH; // DYNAMIC — the actual captured window, not a fixed pregate resolution
  const halfW = ORTHO_SIZE * aspect;
  const sx = (camR / halfW) * (pxW / 2.0) + pxW / 2.0;
  const sy = pxH / 2.0 - (camU / halfH) * (pxH / 2.0);
  return { sx, sy };
}
function cellPixel(c, r, cols, rows, pxW, pxH) {
  const { wx, wy, wz } = cellToWorld(c, r, cols, rows);
  return worldToScreen(wx, wy, wz, pxW, pxH);
}

function clickCell(target, pxW, pxH, winCache, label) {
  const { sx, sy } = cellPixel(target.c, target.r, COLS, ROWS, pxW, pxH);
  // sx,sy are in the SAME capture-pixel space screenshots are saved in -> map to global points
  // exactly like native_palette_server.js's click tool does (winCache.x/y are global points).
  const gx = winCache.x + sx / winCache.scale;
  const gy = winCache.y + sy / winCache.scale;
  // #1466: pass OWNER so the click is PID-delivered to the unfocused player (a global HID tap /
  // cliclick never reaches a no-activation window's Input). ACTIVATE_FALLBACK is the opt-in escape.
  const useCli = core.haveCliclick();
  const r = core.clickAt(helperCmd, useCli, gx, gy, false, OWNER, ACTIVATE_FALLBACK);
  return { ...r, sx, sy, gx, gy, label };
}

function distinctHashes(frames) {
  const hashes = frames.map((f) => (f.ok ? core.fileHash(f.file) : ""));
  return new Set(hashes.filter(Boolean)).size;
}

function main() {
  const result = { ok: false, screenshots: [], steps: [] };
  const start = shoot("start");
  result.screenshots.push(start.rel);
  if (!start.ok || !start.window || !start.pixels) {
    result.reason = "initial screenshot/window-find failed: " + JSON.stringify({
      ok: start.ok, hasWindow: !!start.window, hasPixels: !!start.pixels,
    });
    process.stdout.write(JSON.stringify(result) + "\n");
    process.exit(1);
  }
  const winCache = { x: start.window.x, y: start.window.y, w: start.window.w, h: start.window.h, scale: start.scale || 1 };
  const pxW = start.pixels.pw, pxH = start.pixels.ph;
  result.window = { x: winCache.x, y: winCache.y, w: winCache.w, h: winCache.h, pxW, pxH, scale: winCache.scale };

  // --- MOVE: click a known walkable cell near the hero -----------------------
  const moveClick = clickCell(WALK, pxW, pxH, winCache, "move");
  result.steps.push({ action: "click_move", cell: WALK, ...moveClick });
  const glideMove = [];
  for (let i = 0; i < GLIDE_FRAMES; i++) {
    sleepMs(GLIDE_INTERVAL_MS);
    const f = shoot("glide-move-" + i);
    glideMove.push(f);
    result.screenshots.push(f.rel);
  }
  sleepMs(SETTLE_MS);
  const postMove = shoot("post-move");
  result.screenshots.push(postMove.rel);

  // --- ATTACK: click the goblin's cell ----------------------------------------
  const attackClick = clickCell(GOBLIN, pxW, pxH, winCache, "attack");
  result.steps.push({ action: "click_attack", cell: GOBLIN, ...attackClick });
  const glideAttack = [];
  for (let i = 0; i < GLIDE_FRAMES; i++) {
    sleepMs(GLIDE_INTERVAL_MS);
    const f = shoot("glide-attack-" + i);
    glideAttack.push(f);
    result.screenshots.push(f.rel);
  }
  sleepMs(SETTLE_MS);
  const postAttack = shoot("post-attack");
  result.screenshots.push(postAttack.rel);

  result.glide_move = glideMove.map((f) => f.rel);
  result.glide_attack = glideAttack.map((f) => f.rel);
  result.glide_move_distinct = distinctHashes(glideMove);
  result.glide_attack_distinct = distinctHashes(glideAttack);
  result.ok = moveClick.ok && attackClick.ok;
  process.stdout.write(JSON.stringify(result) + "\n");
  process.exit(result.ok ? 0 : 1);
}

main();
