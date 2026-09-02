#!/usr/bin/env node
/*
 * journey_capture.js — drive the SCRIPTED journey (qa/journey_eval.py build-script) through the box
 * player and capture a frame per step (+ BOTH sides of every transition), for the factual-VQA pass.
 *
 * Reuses the EXACT #1466 primitives qa/player_smoke.sh / player_smoke_driver.js use
 * (native_palette_core.js: captureWindow via SCK, qaClickCell via the player's in-process localhost
 * click listener) so a journey drives the same no-activation input path a human player click takes.
 * CELL clicks (no pixel/aspect calibration): the script already carries grid cells.
 *
 * Boot (player + WORLDOS_QA_INPUT=1 + WORLDOS_QA_INPUT_PORT) is the CALLER's job (qa/journey_eval.py
 * capture -> lib_native_player_boot.sh, same as player_smoke.sh); this driver only clicks + captures.
 *
 * Usage:
 *   WORLDOS_QA_INPUT=1 WORLDOS_QA_INPUT_PORT=8971 \
 *     node journey_capture.js --script <script.json> --rundir <dir> --owner WorldOSPlayer [--helper <bin>]
 *
 * Writes:  <rundir>/frames/<NN>_<step>_<side>.png  and  <rundir>/frames_manifest.json
 *   { "frames": [ {path, step, kind, side, transition, capture_ok, hash}, ... ] }
 * Prints ONE JSON summary line to stdout.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const core = require("./native_palette/native_palette_core.js");

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    out[a.slice(2)] = argv[++i];
  }
  return out;
}
function sleep(sec) { spawnSync("sleep", [String(sec)]); }

const args = parseArgs(process.argv.slice(2));
const SCRIPT = args.script;
const RUNDIR = args.rundir || path.join(require("os").tmpdir(), "worldos-journey");
const OWNER = args.owner || "WorldOSPlayer";
const HELPER = args.helper || "";
const FULLSCREEN_FALLBACK = process.env.WORLDOS_NPT_FULLSCREEN_FALLBACK === "1";
const QA_PORT = process.env.WORLDOS_QA_INPUT === "1" ? String(process.env.WORLDOS_QA_INPUT_PORT || "8971") : "";
const SETTLE_MS = Number(process.env.WORLDOS_JOURNEY_SETTLE_MS || 900);

if (!SCRIPT || !fs.existsSync(SCRIPT)) {
  console.error("[journey_capture] --script <script.json> is required and must exist");
  process.exit(2);
}
const script = JSON.parse(fs.readFileSync(SCRIPT, "utf8"));
const steps = script.steps || [];

const FRAMES = path.join(RUNDIR, "frames");
fs.mkdirSync(FRAMES, { recursive: true });
const helperCmd = core.resolveHelper(path.join(RUNDIR, "player"), HELPER);
const captureState = {};
const frames = [];
let idx = 0;

function shot(step, kind, side, transition) {
  const name = String(idx).padStart(2, "0") + "_" + step + "_" + side + ".png";
  idx += 1;
  const outFile = path.join(FRAMES, name);
  const cap = core.captureWindow({ helperCmd, owner: OWNER, outFile, fullscreenFallback: FULLSCREEN_FALLBACK, state: captureState });
  const rec = {
    path: outFile, step, kind, side, transition: !!transition,
    capture_ok: !!(cap && cap.ok), mode: cap ? cap.mode : "none",
    hash: cap && cap.ok && fs.existsSync(outFile) ? core.fileHash(outFile) : null,
  };
  frames.push(rec);
  return rec;
}

function click(cell) {
  if (!QA_PORT) return { ok: false, reason: "no WORLDOS_QA_INPUT channel (capture-only)" };
  const [c, r] = cell;
  return core.qaClickCell(QA_PORT, c, r);
}

// How many steps REQUIRE a click to land (everything but the establishing 'start' frame). A journey
// that never lands a click would VQA a stack of stale/identical frames and falsely "pass" — so the
// capture fails loud rather than publish a manifest for cells it never actually visited.
const clickSteps = steps.filter((s) => s.kind !== "start").length;

// health preflight — if clicks are needed, the #1466 QA channel MUST be up (the player booted with
// WORLDOS_QA_INPUT=1, exactly as qa/player_smoke.sh). Fail loud before driving, never VQA stale frames.
if (clickSteps > 0) {
  if (!QA_PORT) {
    console.error("[journey_capture] FATAL: " + clickSteps + " click steps but WORLDOS_QA_INPUT is not set — " +
      "boot the player with WORLDOS_QA_INPUT=1 + WORLDOS_QA_INPUT_PORT first (the player_smoke.sh pattern).");
    process.exit(3);
  }
  const h = core.qaHealth(QA_PORT);
  if (!h || h.ok === false) {
    console.error("[journey_capture] FATAL: QA input channel " + QA_PORT + " unhealthy — the player is not booted " +
      "with the #1466 listener; refusing to capture stale frames.");
    process.exit(3);
  }
}

let clicks_ok = 0;
for (const s of steps) {
  const cell = s.cell || [0, 0];
  if (s.transition) {
    // BOTH sides of the transition: capture the room BEFORE crossing, then click + settle + capture AFTER.
    shot(s.id, s.kind, "pre", true);
    const r = click(cell);
    if (r && r.ok) clicks_ok += 1;
    sleep(SETTLE_MS / 1000);
    shot(s.id, s.kind, "post", true);
  } else if (s.kind === "start") {
    shot(s.id, s.kind, "step", false); // establishing frame, no click
  } else {
    const r = click(cell);
    if (r && r.ok) clicks_ok += 1;
    sleep(SETTLE_MS / 1000);
    shot(s.id, s.kind, "step", false);
  }
}

const manifest = { campaign: script.campaign || null, owner: OWNER, qa_port: QA_PORT || null,
                   steps: steps.length, clicks_ok, frames };
fs.writeFileSync(path.join(RUNDIR, "frames_manifest.json"), JSON.stringify(manifest, null, 2));

const captured_ok = frames.filter((f) => f.capture_ok).length;
// Accept only if screenshots were captured AND (no clicks were needed OR at least one click landed).
// clicks needed but zero landed => every frame is the same pre-move view => not evidence of a journey.
const ok = captured_ok > 0 && (clickSteps === 0 || clicks_ok > 0);
if (clickSteps > 0 && clicks_ok === 0) {
  console.error("[journey_capture] FATAL: " + clickSteps + " click steps but 0 clicks landed — frames are " +
    "the pre-move view, not the visited cells. Failing loud (check the QA channel / player focus).");
}
console.log(JSON.stringify({ ok, frames: frames.length, captured_ok, clickSteps, clicks_ok,
                             manifest: path.join(RUNDIR, "frames_manifest.json") }));
process.exit(ok ? 0 : 1);
