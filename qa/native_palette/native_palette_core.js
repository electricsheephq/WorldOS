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
 * Spaces): pulling the window-lookup / activate / capture / click plumbing into one module means
 * the cross-Space fix lives in exactly one place and both consumers get it for free.
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

// ---- window lookup + cross-Space activation --------------------------------
function findWindow(helperCmd, owner) {
  const w = runHelper(helperCmd, ["winfind", owner]);
  if (w && w.found === true) return w;
  return null;
}

// Bring `owner` to the CURRENT Space (macOS switches Spaces to follow an activated app's window,
// same as clicking its Dock icon). Two mechanisms, in order: NSRunningApplication-style `open -a`
// activation via `osascript ... activate` (works for a bundled .app), falling back to nothing
// louder than a logged no-op if the app isn't running — the caller's poll loop below is what
// actually decides whether activation worked, not this call's exit code.
function activateOwner(owner) {
  spawnSync("osascript", ["-e", `tell application "${owner}" to activate`], { encoding: "utf8", timeout: 5000 });
}

// Poll winfind until the window reports on_screen:true (i.e. the Space switch landed and
// `screencapture -l` will work) or the timeout elapses. Returns the last window info seen
// (possibly still on_screen:false) so the caller can decide on a fallback.
function waitForOnScreen(helperCmd, owner, timeoutMs, pollMs) {
  const deadline = Date.now() + (timeoutMs || 3000);
  let last = null;
  for (;;) {
    last = findWindow(helperCmd, owner);
    if (last && last.on_screen !== false) return last; // found + on-screen (or legacy helper w/o the field)
    if (Date.now() >= deadline) return last;
    spawnSync("sleep", [String((pollMs || 150) / 1000)]);
  }
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

// ---- the capture sequence (#1443 core fix) ----------------------------------
// Find the window; if it's on a DIFFERENT Space, activate the owner + wait for it to land on the
// current Space (screencapture -l only works on the current Space on macOS 15); capture via
// `-l <id>`; if that still fails (activation didn't land, or -l refused anyway), fall back to a
// full-screen grab cropped to the window's bounds. `state` is a small mutable object the caller
// keeps across calls — `{lastGoodScale}` — so the fallback crop can reuse the last real scale
// factor observed from a successful `-l` capture instead of guessing.
function captureWindow({ helperCmd, owner, outFile, fullscreenFallback, state, activateTimeoutMs }) {
  state = state || {};
  let win = findWindow(helperCmd, owner);
  let usedFallback = false;
  let activated = false;
  if (win && win.on_screen === false) {
    activateOwner(owner);
    activated = true;
    win = waitForOnScreen(helperCmd, owner, activateTimeoutMs || 3000, 150) || win;
  }
  if (!win) {
    if (!fullscreenFallback) {
      return { ok: false, mode: "none", window: null, pixels: null, scale: 1, reason: "window not found" };
    }
    const ok = spawnSync("screencapture", ["-x", outFile], { encoding: "utf8" }).status === 0 && fs.existsSync(outFile);
    return { ok, mode: "fullscreen", window: null, pixels: ok ? pixelSize(outFile) : null, scale: 1 };
  }
  let ok = false;
  if (win.on_screen !== false) {
    const r = spawnSync("screencapture", ["-l", String(win.id), "-o", "-x", outFile], { encoding: "utf8" });
    ok = r.status === 0 && fs.existsSync(outFile) && fs.statSync(outFile).size > 0;
  }
  if (!ok) {
    // -l refused (still not on this Space, or macOS declined anyway) — crop a full-screen grab.
    usedFallback = true;
    ok = fullscreenCropWindow(win, outFile, state.lastGoodScale);
  }
  const px = ok && fs.existsSync(outFile) ? pixelSize(outFile) : null;
  let scale = state.lastGoodScale || 1;
  if (!usedFallback && px && win.w > 0) {
    scale = px.pw / win.w;
    state.lastGoodScale = scale; // remember for a future fallback crop
  }
  return {
    ok, mode: usedFallback ? "fullscreen-crop" : "window", window: win, pixels: px, scale,
    activated, usedFallback,
  };
}

// ---- synthetic click ---------------------------------------------------------
function clickAt(helperCmd, useCliclick, gx, gy, doubleClick) {
  if (useCliclick) {
    const r = spawnSync("cliclick", [(doubleClick ? "dc:" : "c:") + Math.round(gx) + "," + Math.round(gy)], { encoding: "utf8" });
    if (r.status !== 0) return { ok: false, reason: (r.stderr || "cliclick failed").slice(0, 200) };
    return { ok: true };
  }
  const args = ["click", String(gx), String(gy)];
  if (doubleClick) args.push("double");
  const r = runHelper(helperCmd, args);
  if (!r || r.ok !== true) return { ok: false, reason: (r && r._error) || "click helper failed" };
  return { ok: true };
}

module.exports = {
  SWIFT_SRC,
  resolveHelper,
  runHelper,
  haveCliclick,
  findWindow,
  activateOwner,
  waitForOnScreen,
  pixelSize,
  fileHash,
  fullscreenCropWindow,
  captureWindow,
  clickAt,
};
