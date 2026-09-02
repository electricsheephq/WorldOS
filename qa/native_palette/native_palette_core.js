"use strict";
/*
 * native_palette_core.js — the macOS capture/input PRIMITIVES shared by:
 *   - native_palette_server.js (the MCP tool wrapper the T3 blind-player agent drives)
 *   - player_smoke_driver.js   (the #1443 deterministic post-build smoke — same primitives,
 *                                driven by a SCRIPT instead of an LLM, so bugs in this file are
 *                                caught by the FREE smoke run on every player rebuild, not just
 *                                the ~$3 T3 gate.)
 *
 * Extracted 2026-07-09 (issue #1443 — the native palette was blind across Mission Control
 * Spaces): pulling the window-lookup / capture / click plumbing into one module means the
 * cross-Space fix lives in exactly one place and both consumers get it for free.
 *
 * #1456: capture now goes through ScreenCaptureKit (native_input.swift `capture`) as the PRIMARY
 * path — it images a window on ANY Space with NO activation, so player QA never steals the user's
 * focus or switches Spaces. `screencapture -l` stays only as a fallback for on-screen windows (and
 * the activate-before-capture behavior is GONE — we never re-activate the owner).
 *
 * Nothing here talks MCP, nothing here knows about run directories beyond the paths it's given —
 * pure primitives over a target app OWNER name.
 */

const fs = require("fs");
const path = require("path");
const { execFileSync, spawnSync } = require("child_process");

const SWIFT_SRC = path.join(__dirname, "native_input.swift");

// ---- the Swift primitives helper -------------------------------------------
// Prefer a prebuilt binary (explicit override); else compile native_input.swift to <workDir>/
// native_input (fast per-call once built); else fall back to the `swift` interpreter (slower,
// ~1-2s/call but portable). #1443 force-recompile discipline: a COMPILED binary that predates an
// edit to native_input.swift is exactly the T3 bug (the in-run patch fixed the source but the
// already-compiled binary kept the old on-screen-only behavior until the server restarted) — so
// a stale-by-mtime binary is rebuilt even if a file already exists at `out`, not just an absent one.
// `swiftSrc` defaults to this module's own native_input.swift; a test may override it to point at
// a scratch copy so the mtime-staleness logic is exercisable without touching the real repo file.
function resolveHelper(workDir, explicitPath, swiftSrc) {
  const explicit = (explicitPath || "").trim();
  if (explicit && fs.existsSync(explicit)) return { bin: explicit, pre: [] };
  const src = swiftSrc || SWIFT_SRC;
  const out = path.join(workDir, "native_input");
  try {
    const srcStat = fs.statSync(src);
    let stale = true;
    if (fs.existsSync(out)) {
      const outStat = fs.statSync(out);
      stale = srcStat.mtimeMs > outStat.mtimeMs;
    }
    if (stale) execFileSync("swiftc", [src, "-o", out], { stdio: "pipe" });
    return { bin: out, pre: [] };
  } catch (_e) {
    // fall back to interpreting the source (no compiler, or compile failed)
    return { bin: "swift", pre: [src] };
  }
}

function runHelper(helperCmd, args) {
  const r = spawnSync(helperCmd.bin, [...helperCmd.pre, ...args], { encoding: "utf8", timeout: 20000 });
  if (r.status !== 0 && !r.stdout) {
    return { _error: (r.stderr || r.error || "helper failed").toString().slice(0, 300) };
  }
  try { return JSON.parse((r.stdout || "").trim().split("\n").pop()); }
  catch (_e) { return { _error: "unparseable helper output: " + (r.stdout || "").slice(0, 200) }; }
}

function haveCliclick() {
  try { execFileSync("which", ["cliclick"], { stdio: "pipe" }); return true; } catch (_e) { return false; }
}

// ---- window lookup ---------------------------------------------------------
function findWindow(helperCmd, owner) {
  const w = runHelper(helperCmd, ["winfind", owner]);
  if (w && w.found === true) {
    // winfind emits `window_id`; callers (winCache, the -l fallback) read `id`. Normalize so the
    // two names never diverge (a `screencapture -l undefined` was the latent pre-#1456 symptom).
    if (w.id === undefined && w.window_id !== undefined) w.id = w.window_id;
    return w;
  }
  return null;
}

// ScreenCaptureKit capture (#1456): image the owner's window to a PNG on ANY Space with NO
// activation. Returns the helper's JSON: {ok, window_id, x, y, w, h, px_w, px_h, scale, on_screen}
// on success, or {ok:false,error} / {_error} otherwise (older macOS -> caller falls back).
function captureSCK(helperCmd, owner, outFile) {
  return runHelper(helperCmd, ["capture", owner, outFile]);
}

// ---- pixel helpers -----------------------------------------------------------
function pixelSize(file) {
  try {
    const r = spawnSync("sips", ["-g", "pixelWidth", "-g", "pixelHeight", file], { encoding: "utf8" });
    const w = /pixelWidth:\s*(\d+)/.exec(r.stdout || "");
    const h = /pixelHeight:\s*(\d+)/.exec(r.stdout || "");
    if (w && h) return { pw: Number(w[1]), ph: Number(h[1]) };
  } catch (_e) {}
  return null;
}

function fileHash(file) {
  try {
    return String(fs.statSync(file).size) + ":" +
      require("crypto").createHash("sha1").update(fs.readFileSync(file)).digest("hex");
  } catch (_e) { return ""; }
}

// Crop a full-screen capture down to a window's bounds, in PIXELS, using a known/assumed scale
// (capture pixels per global point — 2 on every Retina display, which is the overwhelming common
// case; a caller with a better-known scale from a prior successful `-l` capture should pass it).
// Used ONLY as the fallback when `screencapture -l <id>` fails after activation (see
// captureWindow below) — sips crop, not a real re-render, so it's approximate at the pixel edges
// but perfectly adequate for click-target math (which works in the same crop's pixel space).
function fullscreenCropWindow(win, outFile, scale) {
  const full = outFile + ".full.png";
  const r = spawnSync("screencapture", ["-x", full], { encoding: "utf8" });
  if (r.status !== 0 || !fs.existsSync(full)) return false;
  const s = scale || 2;
  const offY = Math.max(0, Math.round(win.y * s));
  const offX = Math.max(0, Math.round(win.x * s));
  const h = Math.max(1, Math.round(win.h * s));
  const w = Math.max(1, Math.round(win.w * s));
  const crop = spawnSync("sips", [
    "-c", String(h), String(w), "--cropOffset", String(offY), String(offX),
    full, "--out", outFile,
  ], { encoding: "utf8" });
  try { fs.unlinkSync(full); } catch (_e) {}
  return crop.status === 0 && fs.existsSync(outFile);
}

// ---- the capture sequence (#1456: no-activation, SCK-primary) ---------------
// PRIMARY: ScreenCaptureKit (`capture`) images the window on ANY Space with NO activation — QA must
// never steal the user's focus or switch Spaces. FALLBACK (only if SCK is unavailable/declines,
// e.g. pre-macOS-14): `screencapture -l <id>` for an on-current-Space window, then a full-screen
// grab cropped to the window's bounds. We NEVER activate the owner. `state` is a small mutable
// object the caller keeps across calls — `{lastGoodScale}` — so the fallback crop can reuse the
// last real scale factor instead of guessing.
function captureWindow({ helperCmd, owner, outFile, fullscreenFallback, state }) {
  state = state || {};
  let win = findWindow(helperCmd, owner);
  let ok = false;
  let mode = "none";
  let usedFallback = false;

  // PRIMARY — ScreenCaptureKit, cross-Space, no activation.
  const sck = captureSCK(helperCmd, owner, outFile);
  if (sck && sck.ok === true && fs.existsSync(outFile) && fs.statSync(outFile).size > 0) {
    ok = true;
    mode = "sck";
    // Describe the window that was ACTUALLY imaged (geometry from SCK) so the reported bounds, the
    // captured pixels, and the scale all agree — even if winfind's largest-layer-0 pick differs.
    win = {
      id: sck.window_id, window_id: sck.window_id,
      x: sck.x, y: sck.y, w: sck.w, h: sck.h,
      on_screen: sck.on_screen,
      title: win ? win.title : "",
    };
  }

  // FALLBACK — only when SCK didn't produce a shot (older OS / declined). Never activates.
  if (!ok) {
    if (!win) {
      if (!fullscreenFallback) {
        return { ok: false, mode: "none", window: null, pixels: null, scale: 1, reason: "window not found" };
      }
      const okFS = spawnSync("screencapture", ["-x", outFile], { encoding: "utf8" }).status === 0 && fs.existsSync(outFile);
      return { ok: okFS, mode: "fullscreen", window: null, pixels: okFS ? pixelSize(outFile) : null, scale: 1 };
    }
    // `screencapture -l` only rasterizes a window on the CURRENT Space; if the player is elsewhere
    // it refuses and we crop a full-screen grab — we deliberately do NOT activate to bring it over.
    if (win.on_screen !== false) {
      const r = spawnSync("screencapture", ["-l", String(win.id), "-o", "-x", outFile], { encoding: "utf8" });
      ok = r.status === 0 && fs.existsSync(outFile) && fs.statSync(outFile).size > 0;
      if (ok) mode = "window";
    }
    if (!ok) {
      usedFallback = true;
      ok = fullscreenCropWindow(win, outFile, state.lastGoodScale);
      if (ok) mode = "fullscreen-crop";
    }
  }

  const px = ok && fs.existsSync(outFile) ? pixelSize(outFile) : null;
  let scale = state.lastGoodScale || 1;
  if (ok && !usedFallback && px && win && win.w > 0) {
    scale = px.pw / win.w;
    state.lastGoodScale = scale; // remember for a future fallback crop
  }
  return { ok, mode, window: win, pixels: px, scale, usedFallback };
}

// ---- synthetic click ---------------------------------------------------------
// #1466: when an `owner` is supplied we ALWAYS route through the swift helper's PID-targeted delivery
// (CGEvent.postToPid) — cliclick can only post global HID taps, which a no-activation player never
// receives (the T3/smoke "clicks do nothing" bug). `owner` empty/undefined keeps the legacy behavior
// (cliclick when available, else an HID-tap helper click). `activateFallback` opts into the brief
// activate->click->restore escape (off by default). Returns the helper's `delivery` for observability.
function clickAt(helperCmd, useCliclick, gx, gy, doubleClick, owner, activateFallback) {
  if (useCliclick && !owner) {
    const r = spawnSync("cliclick", [(doubleClick ? "dc:" : "c:") + Math.round(gx) + "," + Math.round(gy)], { encoding: "utf8" });
    if (r.status !== 0) return { ok: false, reason: (r.stderr || "cliclick failed").slice(0, 200) };
    return { ok: true, delivery: "cliclick" };
  }
  const args = ["click", String(gx), String(gy)];
  if (doubleClick) args.push("double");
  if (owner) args.push("--owner", String(owner));
  if (activateFallback) args.push("--activate-fallback");
  const r = runHelper(helperCmd, args);
  if (!r || r.ok !== true) return { ok: false, reason: (r && r._error) || "click helper failed" };
  return { ok: true, delivery: r.delivery };
}

// ---- #1466 QA input channel ---------------------------------------------------
// The player's in-process localhost listener (CombatSurfaceClient StartQaInput) is the ROBUST input
// path for the no-activation player: OS-synthetic mouse never reaches a background Unity window
// (HID/postToPid/brief-activation all REFUTED — see #1466). When WORLDOS_QA_INPUT=1 the driver + T3
// palette route clicks HERE and the player runs them through the SAME HandleCell rest-vs-combat +
// #1441 pre-validation + POST path a human click takes. Synchronous (curl) to match the spawnSync style.
function qaPostClick(port, payload) {
  const r = spawnSync("curl", ["-s", "-m", "3", "-X", "POST", "-H", "Content-Type: application/json",
    "-d", JSON.stringify(payload), `http://127.0.0.1:${port}/click`], { encoding: "utf8" });
  if (r.status !== 0) return { ok: false, reason: (r.stderr || "qa-click curl failed").slice(0, 200) };
  try { return { ok: JSON.parse(r.stdout || "{}").ok === true, delivery: "qa-channel" }; }
  catch { return { ok: false, reason: "qa-click bad response: " + (r.stdout || "").slice(0, 120) }; }
}
// CELL path (robust — no pixel/titlebar/aspect calibration): the caller already knows the grid cell.
function qaClickCell(port, c, r) { return qaPostClick(port, { c, r }); }
// VIEWPORT path (full raycast fidelity): vx,vy are 0..1, BOTTOM-LEFT origin (Unity screen space).
function qaClick(port, vx, vy) { return qaPostClick(port, { vx, vy }); }
// GET the player's Screen.width/height so a pixel-space caller can undo the macOS titlebar the SCK
// capture includes (captured height != Screen.height). Returns { ok, screenW, screenH } or { ok:false }.
function qaHealth(port) {
  const r = spawnSync("curl", ["-s", "-m", "3", `http://127.0.0.1:${port}/health`], { encoding: "utf8" });
  if (r.status !== 0) return { ok: false };
  try { return JSON.parse(r.stdout || "{}"); } catch { return { ok: false }; }
}

module.exports = {
  SWIFT_SRC,
  resolveHelper,
  runHelper,
  haveCliclick,
  findWindow,
  captureSCK,
  pixelSize,
  fileHash,
  fullscreenCropWindow,
  captureWindow,
  clickAt,
  qaClick,
  qaClickCell,
  qaHealth,
};
