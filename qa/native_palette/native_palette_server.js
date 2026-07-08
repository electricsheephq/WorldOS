#!/usr/bin/env node
/*
 * WorldOS AI playtester — the NATIVE-WINDOW PLAYER PALETTE (issue #1436 / #1322, the T3 gate).
 *
 * This is the SAME blind-player tool surface as qa/playwright/palette_server.js, but pointed at the
 * standalone Unity **WorldOSPlayer.app** window instead of a browser. The T3 gate asks a blind AI
 * playtester to complete a QUEST LOOP entirely in the RENDERED surface; that surface is a native
 * macOS window, so the palette is backed by macOS primitives rather than Playwright:
 *
 *   screenshot()   -> screencapture -l <windowid>  (the player window, found via CGWindowList)
 *   a11y_tree()    -> STUB. The native window is pixels-only (no DOM/AX tree exposed); the T3 player
 *                     persona works from screenshots. Returned so the 9-tool contract is identical.
 *   click(x,y)     -> a CGEvent left-click at window-relative PIXELS (mapped to global points), via the
 *                     committed Swift helper native_input.swift (or `cliclick` if installed).
 *   type(text)     -> synthetic unicode keystrokes into the focused field (optional Enter to submit).
 *   key(name)      -> a single key press (Return/Escape/Tab/Arrow*).
 *   wait(ms)       -> sleep (a native window has no selector to wait on; selector is a no-op note).
 *   report_bug/give_up/finish  -> IDENTICAL to the browser palette: same bugs.ndjson / status.json /
 *                     actions.ndjson shape, so qa/ui_playtest_score.py scores a native run UNCHANGED.
 *
 * Artifact layout (identical to the browser palette, so the scorer + summary read it verbatim):
 *   <RUNDIR>/bugs.ndjson                         (top-level deliverable)
 *   <RUNDIR>/player/screenshots/step-NNN-*.png
 *   <RUNDIR>/player/actions.ndjson               ({seq, action, target/x/y, ok, dead, screen, ...})
 *   <RUNDIR>/player/console.ndjson               (empty — a native window has no browser console)
 *   <RUNDIR>/player/network.ndjson               (empty — the palette does not observe the app's HTTP)
 *   <RUNDIR>/player/status.json                  (give_up / finish end signal + satisfaction)
 *
 * Env (set by qa/ui_playtest_player.sh):
 *   WORLDOS_NPT_RUNDIR       — the RUN ROOT (bugs.ndjson + status.json land here; player/ under it)
 *   WORLDOS_NPT_PERSONA      — persona slug stamped on each bug
 *   WORLDOS_NPT_WINDOW_OWNER — CGWindowList owner name of the player app (default "WorldOSPlayer")
 *   WORLDOS_NPT_HELPER       — path to a prebuilt native_input binary (else the .swift is compiled/run)
 *   WORLDOS_NPT_CLICK_TOOL   — "auto" (default) | "cliclick" | "helper"
 *   WORLDOS_NPT_SCREEN       — coarse screen label stamped on actions (default "player"; honest — the
 *                              native game view is NOT the browser SPA's "table" screen)
 *   WORLDOS_NPT_FULLSCREEN_FALLBACK — "1" to fall back to a full-screen grab when the window isn't found
 *
 * MACOS PERMISSIONS (checked at startup — the run FAILS LOUD, never silently skips, if either is
 * missing, because a missing grant is an OWNER action, not a test result):
 *   - Screen Recording  (System Settings > Privacy & Security > Screen Recording) — for screencapture
 *     AND for CGWindowList to enumerate the player's window at all (redacted without it).
 *   - Accessibility     (System Settings > Privacy & Security > Accessibility) — for synthetic input.
 *
 * Pure player-side surface: NEVER imports the engine, NEVER reads campaign state, NEVER writes
 * anything but the run-dir artifacts. The engine stays the sole writer.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

// The MCP SDK + zod live in the sibling qa/playwright workspace (the harness already installs them
// there). Resolve normally first (in case this dir grows its own node_modules), then fall back to the
// playwright workspace — no second npm install required.
function requireShared(mod) {
  const parts = mod.split("/");
  // Candidate node_modules roots, in order: an explicit override, this dir's own, the sibling
  // qa/playwright workspace (where the harness installs the SDK), and the canonical repo checkout
  // (worktrees don't get node_modules — gitignored — so fall back to the main checkout's install).
  const roots = [
    process.env.WORLDOS_NPT_NODE_MODULES,
    path.join(__dirname, "node_modules"),
    path.join(__dirname, "..", "playwright", "node_modules"),
    "/Users/lume/WorldOS/qa/playwright/node_modules",
  ].filter(Boolean);
  try {
    return require(mod);
  } catch (_e) {
    // require.resolve({paths}) honors the package's "exports" map (the SDK maps
    // "server/mcp.js" -> dist/cjs/...), which a raw path.join would bypass.
    for (const root of roots) {
      try { return require(require.resolve(mod, { paths: [root] })); } catch (_e2) {}
    }
    throw new Error(
      "cannot resolve '" + mod + "' — install the palette deps (cd qa/playwright && npm install) or " +
      "set WORLDOS_NPT_NODE_MODULES to a node_modules dir that has @modelcontextprotocol/sdk + zod. " +
      "searched: " + roots.join(", ")
    );
  }
}
const { McpServer } = requireShared("@modelcontextprotocol/sdk/server/mcp.js");
const { StdioServerTransport } = requireShared("@modelcontextprotocol/sdk/server/stdio.js");
const { z } = requireShared("zod");

// The harness always sets WORLDOS_NPT_RUNDIR; the default lands under the OS temp dir (never the
// repo) so an ad-hoc/selfcheck run can't leave a stray npt-run/ in the working tree.
const RUNDIR = process.env.WORLDOS_NPT_RUNDIR || path.join(require("os").tmpdir(), "worldos-npt-run");
const PLAYERDIR = path.join(RUNDIR, "player");
const PERSONA = (process.env.WORLDOS_NPT_PERSONA || "t3-native").trim();
const OWNER = (process.env.WORLDOS_NPT_WINDOW_OWNER || "WorldOSPlayer").trim();
const CLICK_TOOL = (process.env.WORLDOS_NPT_CLICK_TOOL || "auto").trim();
const SCREEN_LABEL = (process.env.WORLDOS_NPT_SCREEN || "player").trim();
const FULLSCREEN_FALLBACK = process.env.WORLDOS_NPT_FULLSCREEN_FALLBACK === "1";
const SELFCHECK = process.argv.includes("--selfcheck");
const MAX_WAIT_MS = 8000;

const SHOTS = path.join(PLAYERDIR, "screenshots");
const A11Y = path.join(PLAYERDIR, "a11y");
for (const d of [RUNDIR, PLAYERDIR, SHOTS, A11Y]) fs.mkdirSync(d, { recursive: true });

const BUGS = path.join(RUNDIR, "bugs.ndjson");
const ACTIONS = path.join(PLAYERDIR, "actions.ndjson");
const CONSOLE = path.join(PLAYERDIR, "console.ndjson"); // stays empty (native has no browser console)
const NETWORK = path.join(PLAYERDIR, "network.ndjson"); // stays empty (palette does not observe HTTP)
const STATUS = path.join(PLAYERDIR, "status.json");
// Touch the empty logs so the scorer's readers find them (it tolerates absent, but parity is cleaner).
for (const f of [CONSOLE, NETWORK]) { try { if (!fs.existsSync(f)) fs.writeFileSync(f, ""); } catch (_e) {} }

let seq = 0;
let winCache = null; // {x,y,w,h, id, scale} — refreshed on each screenshot

const A11Y_STUB =
  "(pixels-only surface) The native WorldOSPlayer window exposes NO accessibility tree — it is a " +
  "rendered game view. Use screenshot() to SEE, then click(x,y) at the pixel you want. Coordinates " +
  "are relative to the screenshot's top-left. The T3 player persona plays entirely from screenshots.";

function appendLine(file, obj) {
  try { fs.appendFileSync(file, JSON.stringify(obj) + "\n"); } catch (_e) {}
}
function nowIso() { return new Date().toISOString().replace(/\.\d+Z$/, "Z"); }

// ---- the Swift primitives helper (shared with player_smoke_driver.js — see #1443) -----------
// native_palette_core.js owns: resolving/compiling the swiftc helper (with a source-mtime
// staleness check so an EDITED native_input.swift is never shadowed by a stale compiled binary —
// the #1443 T3 finding: an in-run source patch didn't take effect until the server restarted),
// cross-Space window activation + polling, and the screencapture-with-fallback sequence.
const core = require("./native_palette_core.js");
const SWIFT_SRC = core.SWIFT_SRC;
let helperCmd = null; // {bin, pre} — memoized per-process (mtime check still runs each resolve)
function resolveHelper() {
  const explicit = (process.env.WORLDOS_NPT_HELPER || "").trim();
  helperCmd = core.resolveHelper(PLAYERDIR, explicit);
  return helperCmd;
}
function runHelper(args) {
  return core.runHelper(resolveHelper(), args);
}
function haveCliclick() {
  return core.haveCliclick();
}

// ---- permission gate (FAIL LOUD) -------------------------------------------
function assertPermissions() {
  const p = runHelper(["checkperms"]);
  const missing = [];
  if (!p || p.screen_recording !== true) {
    missing.push(
      "SCREEN RECORDING — open: System Settings > Privacy & Security > Screen Recording, enable the " +
      "app running this harness (Terminal/iTerm/your shell), then RESTART it. Without it the player " +
      "window cannot be enumerated or captured. (probe: " + JSON.stringify(p) + ")"
    );
  }
  if (!p || p.accessibility !== true) {
    missing.push(
      "ACCESSIBILITY — open: System Settings > Privacy & Security > Accessibility, enable the app " +
      "running this harness, then RESTART it. Without it synthetic clicks/keys are silently dropped."
    );
  }
  if (missing.length) {
    process.stderr.write(
      "\n[native-palette] FATAL: missing macOS permission(s) — this is an OWNER action, not a skip:\n" +
      missing.map((m) => "  * " + m).join("\n") + "\n\n"
    );
    process.exit(3);
  }
}

// ---- window + screenshot ----------------------------------------------------
// #1443: cross-Space capture. core.captureWindow() does the activate+poll+capture(-l)+fallback
// (fullscreen grab cropped to the window's bounds) sequence — see native_palette_core.js. This
// function stays a thin per-run wrapper: it picks the file name, threads the persistent
// `captureState` (remembers the last known-good Retina scale across calls, for the fallback
// crop), and updates `winCache` for click()'s pixel->global-point mapping exactly as before.
const captureState = {}; // {lastGoodScale} — persists across calls in this process
function findWindow() {
  return core.findWindow(resolveHelper(), OWNER);
}
function screencaptureWindow(label) {
  const name = "step-" + String(seq).padStart(3, "0") + (label ? "-" + label : "") + ".png";
  const file = path.join(SHOTS, name);
  const rel = path.join("player", "screenshots", name);
  const cap = core.captureWindow({
    helperCmd: resolveHelper(), owner: OWNER, outFile: file,
    fullscreenFallback: FULLSCREEN_FALLBACK, state: captureState,
  });
  if (!cap.window && !cap.ok) {
    return {
      ok: false, screenshot: "",
      reason: "player window '" + OWNER + "' not found (is WorldOSPlayer.app launched? set WORLDOS_NPT_FULLSCREEN_FALLBACK=1 to grab the whole screen)",
    };
  }
  winCache = cap.window ? { x: cap.window.x, y: cap.window.y, w: cap.window.w, h: cap.window.h, id: cap.window.id, scale: cap.scale } : null;
  return { ok: cap.ok, screenshot: rel, mode: cap.mode, window: cap.window, pixels: cap.pixels, scale: cap.scale };
}

// ---- bug + action logging (identical shape to the browser palette) ----------
function writeBug(rec) {
  const out = {
    ts: rec.ts || nowIso(), action_seq: seq, persona: PERSONA,
    screen: rec.screen || SCREEN_LABEL, category: rec.category || "ux",
    severity: rec.severity || "minor", title: rec.title || "(untitled)",
    expected: rec.expected || "", actual: rec.actual || "", screenshot: rec.screenshot || "",
    evidence: rec.evidence || {}, tried_alternatives: rec.tried_alternatives || [],
    blocks_progress: rec.blocks_progress === true, source: rec.source || "player",
  };
  appendLine(BUGS, out);
  return out;
}
function logAction(action, detail) {
  seq += 1;
  appendLine(ACTIONS, { ts: nowIso(), seq, action, ...detail, screen: SCREEN_LABEL });
  return seq;
}
function fileHash(file) {
  try { return String(fs.statSync(file).size) + ":" + require("crypto").createHash("sha1").update(fs.readFileSync(file)).digest("hex"); }
  catch (_e) { return ""; }
}

// ---- MCP server -------------------------------------------------------------
const server = new McpServer({ name: "worldos-nativeplayer", version: "1.0.0" });
function textResult(obj) {
  return { content: [{ type: "text", text: typeof obj === "string" ? obj : JSON.stringify(obj) }] };
}

server.registerTool(
  "screenshot",
  {
    description:
      "Take a screenshot of the WorldOS player window RIGHT NOW. Returns the saved PNG path plus the " +
      "window's pixel size — click by pixel coordinates relative to this image's top-left. This is a " +
      "pixels-only surface: there is no accessibility tree, so LOOK at the image before you act.",
    inputSchema: {},
  },
  async () => {
    const shot = screencaptureWindow("look");
    const s = logAction("screenshot", { screenshot: shot.screenshot, ok: shot.ok });
    return textResult({
      screen: SCREEN_LABEL, seq: s, screenshot: shot.screenshot, ok: shot.ok,
      window_pixels: shot.pixels ? { width: shot.pixels.pw, height: shot.pixels.ph } : null,
      note: shot.ok ? "click(x,y) uses pixels from this image (top-left origin)." : shot.reason,
    });
  }
);

server.registerTool(
  "a11y_tree",
  {
    description:
      "Read the accessibility tree of the current screen. NOTE: the native player window is pixels-only " +
      "and exposes no accessibility tree — this returns an explanation. Use screenshot() + click(x,y).",
    inputSchema: {},
  },
  async () => {
    logAction("a11y_tree", { stub: true });
    return textResult({ screen: SCREEN_LABEL, a11y: A11Y_STUB, pixels_only: true });
  }
);

server.registerTool(
  "click",
  {
    description:
      "Click inside the player window at PIXEL coordinates (x,y) from the last screenshot (top-left " +
      "origin). This is how you move your token / press an on-screen control in the rendered game. " +
      "Returns whether the click landed and whether the view changed.",
    inputSchema: {
      x: z.number().describe("X pixel from the screenshot's left edge."),
      y: z.number().describe("Y pixel from the screenshot's top edge."),
      double: z.boolean().optional().describe("Double-click (default false)."),
    },
  },
  async ({ x, y, double }) => {
    if (!winCache) screencaptureWindow("preclick"); // ensure we know the window bounds + scale
    if (!winCache) {
      const s = logAction("click", { x, y, ok: false, reason: "no window" });
      return textResult({ ok: false, seq: s, reason: "player window not located — take a screenshot first." });
    }
    // window-relative pixels -> global screen points
    const gx = winCache.x + x / winCache.scale;
    const gy = winCache.y + y / winCache.scale;
    const before = (function () { const f = screencaptureWindow("clickbefore"); return f.ok ? fileHash(path.join(RUNDIR, f.screenshot)) : ""; })();
    let ok = true, reason = "";
    const useCli = CLICK_TOOL === "cliclick" || (CLICK_TOOL === "auto" && haveCliclick());
    const clickResult = core.clickAt(resolveHelper(), useCli, gx, gy, !!double);
    if (!clickResult.ok) { ok = false; reason = clickResult.reason || "click failed"; }
    spawnSync("sleep", ["0.7"]);
    const after = screencaptureWindow("click");
    const afterHash = after.ok ? fileHash(path.join(RUNDIR, after.screenshot)) : "";
    const changed = before !== "" && afterHash !== "" ? before !== afterHash : true;
    const s = logAction("click", { x, y, gx: Math.round(gx), gy: Math.round(gy), ok, changed, dead: ok && !changed, reason });
    return textResult({
      ok, seq: s, screen: SCREEN_LABEL, screen_changed: changed, screenshot: after.screenshot,
      reason: ok ? (changed ? "" : "WARNING: click landed but the view did not change — possible dead control; consider report_bug.") : reason,
    });
  }
);

server.registerTool(
  "type",
  {
    description:
      "Type text via synthetic keystrokes into whatever field the player window currently has focused. " +
      "Set submit=true to press Enter afterward (e.g. to send a chat/command line).",
    inputSchema: {
      text: z.string().describe("What to type, in plain English."),
      submit: z.boolean().optional().describe("Press Enter after typing (default false)."),
    },
  },
  async ({ text, submit }) => {
    let ok = true, reason = "";
    const r = runHelper(["type", String(text)]);
    if (!r || r.ok !== true) { ok = false; reason = (r && r._error) || "type helper failed"; }
    if (ok && submit) { runHelper(["key", "return"]); spawnSync("sleep", ["0.9"]); }
    const after = screencaptureWindow("type");
    const s = logAction("type", { text: String(text).slice(0, 200), submit: !!submit, ok, reason });
    return textResult({ ok, seq: s, screen: SCREEN_LABEL, screenshot: after.screenshot, submitted: !!submit, reason });
  }
);

server.registerTool(
  "key",
  {
    description:
      'Press a single key on the player window (e.g. "Enter", "Escape", "Tab", "ArrowDown"). Use Enter ' +
      "to confirm, Escape to close a popup.",
    inputSchema: { name: z.string().describe("Key name, e.g. Enter / Escape / Tab / ArrowDown.") },
  },
  async ({ name }) => {
    let ok = true, reason = "";
    const r = runHelper(["key", String(name)]);
    if (!r || r.ok !== true) { ok = false; reason = (r && r._error) || "key helper failed"; }
    spawnSync("sleep", ["0.4"]);
    const after = screencaptureWindow("key");
    const s = logAction("key", { name, ok, reason });
    return textResult({ ok, seq: s, screen: SCREEN_LABEL, screenshot: after.screenshot, reason });
  }
);

server.registerTool(
  "wait",
  {
    description:
      "Wait for the game to settle. Pass a number of milliseconds (capped at " + MAX_WAIT_MS + "ms). " +
      "(A selector wait has no meaning on a pixels-only native window; it is treated as a short wait.)",
    inputSchema: {
      ms: z.number().optional().describe("Milliseconds to wait (capped)."),
      selector: z.string().optional().describe("Ignored on native — no DOM. Present for contract parity."),
    },
  },
  async ({ ms, selector }) => {
    const dur = Math.max(0, Math.min(Number(ms || 1000), MAX_WAIT_MS));
    spawnSync("sleep", [String(dur / 1000)]);
    const s = logAction("wait", { ms: ms || null, selector: selector || null, ok: true });
    return textResult({ ok: true, seq: s, screen: SCREEN_LABEL, note: selector ? "selector ignored (native window has no DOM)" : "" });
  }
);

server.registerTool(
  "report_bug",
  {
    description:
      "Record ONE bug or UX problem you just hit. This is the POINT of the test — be specific. " +
      "Give the severity (critical = blocks all play / major = blocks this task / minor = annoying / " +
      "trivial = cosmetic), what you EXPECTED, and what ACTUALLY happened.",
    inputSchema: {
      severity: z.enum(["critical", "major", "minor", "trivial"]).describe("How bad is it."),
      screen: z.string().optional().describe("Which part of the game you are on."),
      expected: z.string().describe("What you expected to happen."),
      actual: z.string().describe("What actually happened."),
      title: z.string().optional().describe("One-line summary."),
      category: z.enum(["ux", "bug", "content", "accessibility", "performance"]).optional(),
      blocks_progress: z.boolean().optional().describe("Did this stop you from continuing?"),
      tried_alternatives: z.array(z.string()).optional().describe("Other things you tried first."),
    },
  },
  async (rec) => {
    const shot = screencaptureWindow("bug");
    const out = writeBug({
      category: rec.category || "ux", severity: rec.severity, screen: rec.screen || SCREEN_LABEL,
      title: rec.title || rec.expected.slice(0, 80), expected: rec.expected, actual: rec.actual,
      screenshot: shot.screenshot, tried_alternatives: rec.tried_alternatives || [],
      blocks_progress: rec.blocks_progress === true, source: "player",
    });
    logAction("report_bug", { severity: out.severity, title: out.title });
    return textResult({ ok: true, recorded: out.title, severity: out.severity, screenshot: shot.screenshot });
  }
);

server.registerTool(
  "give_up",
  {
    description:
      "End the playtest because you are stuck and cannot find a way to continue. Explain WHY in plain " +
      "words. Only use this when you have genuinely exhausted the obvious options.",
    inputSchema: { reason: z.string().describe("Why you are giving up.") },
  },
  async ({ reason }) => {
    const shot = screencaptureWindow("giveup");
    logAction("give_up", { reason });
    writeBug({
      category: "ux", severity: "major", screen: SCREEN_LABEL,
      title: "Player gave up: " + String(reason).slice(0, 80),
      expected: "A player can complete the quest loop in the rendered surface without getting stuck.",
      actual: String(reason).slice(0, 500), screenshot: shot.screenshot,
      blocks_progress: true, source: "give_up",
    });
    try { fs.writeFileSync(STATUS, JSON.stringify({ ended: true, reason: "give_up", detail: String(reason).slice(0, 500), at: nowIso() })); } catch (_e) {}
    return textResult({ ok: true, ended: true, note: "Run ended. Thanks for playing." });
  }
);

server.registerTool(
  "finish",
  {
    description:
      "End the playtest because you have PLAYED ENOUGH to fairly judge the experience and you are NOT " +
      "blocked (use give_up ONLY when genuinely stuck). Give your honest overall satisfaction (1-10) " +
      "and a 1-2 sentence closing verdict. This is the normal, satisfied way to end.",
    inputSchema: {
      satisfaction: z.number().int().min(1).max(10).describe("Your honest overall satisfaction, 1-10."),
      verdict: z.string().describe("A 1-2 sentence closing verdict."),
    },
  },
  async ({ satisfaction, verdict }) => {
    const shot = screencaptureWindow("finish");
    logAction("finish", { satisfaction, verdict: String(verdict).slice(0, 200), screenshot: shot.screenshot });
    try { fs.writeFileSync(STATUS, JSON.stringify({ ended: true, reason: "finish", satisfaction, detail: String(verdict).slice(0, 500), at: nowIso() })); } catch (_e) {}
    return textResult({ ok: true, ended: true, note: "Run ended — thanks for playing." });
  }
);

const TOOL_NAMES = ["screenshot", "a11y_tree", "click", "type", "key", "wait", "report_bug", "give_up", "finish"];

// ---- selfcheck (no GUI, no permission gate): assert the contract + helper ----
// Used by qa/test_native_palette.py to prove the server boots and registers the 9-tool contract
// without a live player window or a TCC grant.
if (SELFCHECK) {
  const registered = Object.keys((server && server._registeredTools) || {});
  const have = TOOL_NAMES.every((t) => registered.includes(t));
  const helperOk = fs.existsSync(SWIFT_SRC);
  const result = {
    ok: have && helperOk,
    tools: registered.sort(),
    expected: TOOL_NAMES.slice().sort(),
    tool_contract_match: have,
    helper_present: helperOk,
    helper_src: SWIFT_SRC,
  };
  process.stdout.write(JSON.stringify(result) + "\n");
  process.exit(result.ok ? 0 : 1);
}

// ---- live boot --------------------------------------------------------------
assertPermissions();
(async () => {
  const transport = new StdioServerTransport();
  await server.connect(transport);
})().catch((e) => {
  process.stderr.write("native_palette fatal: " + (e && e.stack ? e.stack : e) + "\n");
  process.exit(1);
});
