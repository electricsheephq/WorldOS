#!/usr/bin/env node
/*
 * WorldOS AI playtester — the PLAYER PALETTE MCP server (issue #324, v1).
 *
 * This is the Player agent's ENTIRE tool surface. It mirrors the engine's
 * constrained `player_server.py` facade (roles enforced in CODE, not prose): the
 * Player sees ONLY what is on screen and may act ONLY through these eight tools.
 * There is NO source-code access, NO engine introspection, NO filesystem here —
 * every tool is backed by a Playwright browser this process drives. The Player is
 * a real, blind user clicking the real `/openworlds/` UI.
 *
 *   screenshot()                 -> page.screenshot() (saved PNG + a11y text)
 *   a11y_tree()                  -> ariaSnapshot() (what a screen reader announces)
 *   click(target)                -> getByRole/getByText locator click (visible label/text)
 *   type(target, text[, submit]) -> fill a field by its label/placeholder, optional Enter
 *   key(name)                    -> keyboard.press (Enter / Tab / Escape / ArrowDown ...)
 *   wait(ms | selector)          -> waitForTimeout / waitForSelector (capped)
 *   report_bug(record)           -> append ONE JSON line to bugs.ndjson (the gold of the test)
 *   give_up(reason)              -> end the run (the player is stuck)
 *
 * NOTE on the a11y tree: the spec named `page.accessibility.snapshot()`. That API was
 * removed in Playwright >= 1.5x; its modern successor is `locator.ariaSnapshot()`, which
 * returns the SAME "what a screen reader sees" tree (roles, names, values, placeholders,
 * disabled state) as compact YAML. We use ariaSnapshot — same blind-user semantics.
 *
 * The harness (qa/ui_playtest.sh) launches the Player `claude -p` with --strict-mcp-config
 * pointed at a generated .mcp.json that runs THIS server with these env vars:
 *   CLAWDND_UIPT_URL     — the live viewer URL to open (e.g. http://127.0.0.1:8993/openworlds/)
 *   CLAWDND_UIPT_RUNDIR  — the run dir; we write screenshots/, a11y/, console.ndjson,
 *                          network.ndjson, actions.ndjson, bugs.ndjson under it
 *   CLAWDND_UIPT_CHANNEL — "" (bundled chromium) or "chrome" (reuse system Chrome)
 *   CLAWDND_UIPT_PERSONA — persona slug stamped on each bug
 *
 * Console errors and failed network requests are captured passively for EVERY page and
 * auto-emitted as bugs (category "console"/"network") in addition to whatever the Player
 * notices — so we catch breakage even when the persona misses it.
 *
 * Pure player-side surface: it NEVER imports the engine, NEVER reads campaign state,
 * NEVER writes anything but the run-dir artifacts. The engine stays the sole writer.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");
const { McpServer } = require("@modelcontextprotocol/sdk/server/mcp.js");
const { StdioServerTransport } = require("@modelcontextprotocol/sdk/server/stdio.js");
const { z } = require("zod");

const TARGET_URL = process.env.CLAWDND_UIPT_URL || "http://127.0.0.1:8799/openworlds/";
const RUNDIR = process.env.CLAWDND_UIPT_RUNDIR || path.join(process.cwd(), "uipt-run");
const CHANNEL = (process.env.CLAWDND_UIPT_CHANNEL || "").trim();
const PERSONA = (process.env.CLAWDND_UIPT_PERSONA || "newbie").trim();
const MAX_WAIT_MS = 8000; // hard cap on a single wait, so the player can't stall the run

const SHOTS = path.join(RUNDIR, "screenshots");
const A11Y = path.join(RUNDIR, "a11y");
for (const d of [RUNDIR, SHOTS, A11Y]) fs.mkdirSync(d, { recursive: true });

const BUGS = path.join(RUNDIR, "bugs.ndjson");
const ACTIONS = path.join(RUNDIR, "actions.ndjson");
const CONSOLE = path.join(RUNDIR, "console.ndjson");
const NETWORK = path.join(RUNDIR, "network.ndjson");
const STATUS = path.join(RUNDIR, "status.json"); // give_up / end signalling for the orchestrator

let seq = 0;
let lastScreen = "launcher"; // best-effort current-screen label, from the URL hash

function appendLine(file, obj) {
  try {
    fs.appendFileSync(file, JSON.stringify(obj) + "\n");
  } catch (_e) {
    /* never let a logging failure break a tool call */
  }
}

function nowIso() {
  return new Date().toISOString().replace(/\.\d+Z$/, "Z");
}

// Best-effort "which screen are we on" from the URL hash (#/table) or fallback.
function screenFromUrl(u) {
  try {
    const m = String(u || "").match(/#\/?([a-z-]+)/i);
    if (m) return m[1].toLowerCase();
  } catch (_e) {}
  return lastScreen;
}

function shortUrl(u) {
  try {
    const p = new globalThis.URL(u);
    return p.pathname + (p.search || "");
  } catch (_e) {
    return String(u).slice(0, 80);
  }
}

// ---- Playwright lifecycle ---------------------------------------------------
let browser = null;
let page = null;

async function ensurePage() {
  if (page && !page.isClosed()) return page;
  const launchOpts = { headless: true };
  if (CHANNEL) launchOpts.channel = CHANNEL; // e.g. "chrome" to reuse system Chrome
  browser = await chromium.launch(launchOpts);
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  page = await context.newPage();

  // Passive capture: console errors + failed/4xx/5xx network requests -> bugs.
  page.on("console", (msg) => {
    const type = msg.type();
    if (type !== "error" && type !== "warning") return;
    const text = msg.text();
    if (/Download the React DevTools|Each child in a list should have a unique/.test(text)) return;
    appendLine(CONSOLE, { ts: nowIso(), type, text: text.slice(0, 600) });
    if (type === "error") {
      autoBug({
        category: "console",
        severity: "major",
        screen: screenFromUrl(page.url()),
        title: "Console error: " + text.slice(0, 100),
        expected: "No JavaScript console errors during normal play.",
        actual: text.slice(0, 400),
        evidence: { console_error: text.slice(0, 400) },
        blocks_progress: false,
        source: "auto",
      });
    }
  });
  page.on("pageerror", (err) => {
    const text = String(err && err.message ? err.message : err);
    appendLine(CONSOLE, { ts: nowIso(), type: "pageerror", text: text.slice(0, 600) });
    autoBug({
      category: "console",
      severity: "critical",
      screen: screenFromUrl(page.url()),
      title: "Uncaught exception: " + text.slice(0, 100),
      expected: "No uncaught JavaScript exceptions.",
      actual: text.slice(0, 400),
      evidence: { pageerror: text.slice(0, 400) },
      blocks_progress: true,
      source: "auto",
    });
  });
  page.on("requestfailed", (req) => {
    const failure = req.failure();
    const rec = { ts: nowIso(), url: req.url(), method: req.method(), error: failure ? failure.errorText : "failed" };
    appendLine(NETWORK, rec);
    if (/net::ERR_ABORTED/.test(rec.error)) return; // aborted navigations are noise
    autoBug({
      category: "network",
      severity: "major",
      screen: screenFromUrl(page.url()),
      title: "Network request failed: " + req.method() + " " + shortUrl(req.url()),
      expected: "The page's network requests succeed.",
      actual: rec.error + " — " + req.url(),
      evidence: { network_failure: rec },
      blocks_progress: false,
      source: "auto",
    });
  });
  page.on("response", (resp) => {
    const status = resp.status();
    if (status < 400) return;
    const rec = { ts: nowIso(), url: resp.url(), status, method: resp.request().method() };
    appendLine(NETWORK, rec);
    autoBug({
      category: "network",
      severity: status >= 500 ? "major" : "minor",
      screen: screenFromUrl(page.url()),
      title: "HTTP " + status + " on " + resp.request().method() + " " + shortUrl(resp.url()),
      expected: "Requests the UI makes return 2xx/3xx.",
      actual: "HTTP " + status + " for " + resp.url(),
      evidence: { http_status: rec },
      blocks_progress: false,
      source: "auto",
    });
  });

  await page.goto(TARGET_URL, { waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => {});
  // The OpenWorlds SPA mounts React asynchronously; give it a beat to render.
  await page.waitForTimeout(1500);
  lastScreen = screenFromUrl(page.url());
  return page;
}

// ---- a11y snapshot ----------------------------------------------------------
// ariaSnapshot() returns the screen-reader view as compact YAML. Cap its size so a
// huge screen can't blow the Player's context.
async function ariaText(pg) {
  try {
    const snap = await pg.locator("body").ariaSnapshot({ timeout: 4000 });
    return String(snap).slice(0, 9000);
  } catch (_e) {
    return "(accessibility snapshot unavailable for this screen)";
  }
}

// ---- bug + action logging ---------------------------------------------------
// De-dup auto-bugs so one repeated console error doesn't flood bugs.ndjson.
const seenAuto = new Set();
function autoBug(rec) {
  const dedup = (rec.category || "") + "|" + (rec.title || "");
  if (seenAuto.has(dedup)) return;
  seenAuto.add(dedup);
  writeBug(rec);
}

function writeBug(rec) {
  const out = {
    ts: rec.ts || nowIso(),
    action_seq: seq,
    persona: PERSONA,
    screen: rec.screen || lastScreen,
    category: rec.category || "ux",
    severity: rec.severity || "minor",
    title: rec.title || "(untitled)",
    expected: rec.expected || "",
    actual: rec.actual || "",
    screenshot: rec.screenshot || "",
    evidence: rec.evidence || {},
    tried_alternatives: rec.tried_alternatives || [],
    blocks_progress: rec.blocks_progress === true,
    source: rec.source || "player",
  };
  appendLine(BUGS, out);
  return out;
}

function logAction(action, detail) {
  seq += 1;
  appendLine(ACTIONS, { ts: nowIso(), seq, action, ...detail, screen: lastScreen });
  return seq;
}

// Save a screenshot for the current step; returns the relative filename.
async function snap(pg, label) {
  const name = "step-" + String(seq).padStart(3, "0") + (label ? "-" + label : "") + ".png";
  const file = path.join(SHOTS, name);
  try {
    await pg.screenshot({ path: file, fullPage: false });
  } catch (_e) {
    return "";
  }
  return path.join("screenshots", name);
}

// ---- locator resolution -----------------------------------------------------
// A blind user clicks by the WORDS they see. Prefer accessible role+name, fall
// back to visible text, then a raw selector (only the orchestrator ever passes one).
async function resolveClickable(pg, target) {
  const t = String(target || "").trim();
  if (!t) return null;
  const tries = [
    () => pg.getByRole("button", { name: t, exact: false }).first(),
    () => pg.getByRole("tab", { name: t, exact: false }).first(),
    () => pg.getByRole("link", { name: t, exact: false }).first(),
    () => pg.getByRole("menuitem", { name: t, exact: false }).first(),
    () => pg.getByText(t, { exact: false }).first(),
  ];
  for (const mk of tries) {
    try {
      const loc = mk();
      if ((await loc.count()) > 0) return loc;
    } catch (_e) {}
  }
  try {
    const loc = pg.locator(t).first();
    if ((await loc.count()) > 0) return loc;
  } catch (_e) {}
  return null;
}

function escapeRe(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function resolveField(pg, target) {
  const t = String(target || "").trim();
  // Empty target = "the main action input" (the table's "Describe what your hero does...").
  if (!t) {
    const byPh = pg.getByPlaceholder(/describe what your hero does/i).first();
    if ((await byPh.count().catch(() => 0)) > 0) return byPh;
    const anyTextbox = pg.getByRole("textbox").first();
    if ((await anyTextbox.count().catch(() => 0)) > 0) return anyTextbox;
    return null;
  }
  const tries = [
    () => pg.getByPlaceholder(new RegExp(escapeRe(t), "i")).first(),
    () => pg.getByLabel(new RegExp(escapeRe(t), "i")).first(),
    () => pg.getByRole("textbox", { name: t, exact: false }).first(),
  ];
  for (const mk of tries) {
    try {
      const loc = mk();
      if ((await loc.count()) > 0) return loc;
    } catch (_e) {}
  }
  const tb = pg.getByRole("textbox").first();
  if ((await tb.count().catch(() => 0)) > 0) return tb;
  return null;
}

// ---- MCP server -------------------------------------------------------------
const server = new McpServer({ name: "clawdnd-uiplayer", version: "1.0.0" });

function textResult(obj) {
  return { content: [{ type: "text", text: typeof obj === "string" ? obj : JSON.stringify(obj) }] };
}

server.registerTool(
  "screenshot",
  {
    description:
      "Take a screenshot of what is on screen RIGHT NOW and read the page's accessible text. " +
      "Returns the saved PNG path plus what a screen reader sees (buttons, tabs, fields, headings, " +
      "and whether controls are disabled). Use this to SEE before you act.",
    inputSchema: {},
  },
  async () => {
    const pg = await ensurePage();
    lastScreen = screenFromUrl(pg.url());
    const sc = await snap(pg, "look");
    logAction("screenshot", { screenshot: sc });
    const aria = await ariaText(pg);
    appendLine(path.join(A11Y, "_index.ndjson"), { seq, screen: lastScreen });
    try {
      fs.writeFileSync(path.join(A11Y, "step-" + String(seq).padStart(3, "0") + ".txt"), aria);
    } catch (_e) {}
    return textResult({ screen: lastScreen, screenshot: sc, a11y: aria });
  }
);

server.registerTool(
  "a11y_tree",
  {
    description:
      "Read ONLY the accessibility tree of the current screen (what a screen reader announces): " +
      "every button, tab, link, heading, text field and its value, plus disabled state. Cheaper " +
      "than a screenshot when you just need the list of controls and text.",
    inputSchema: {},
  },
  async () => {
    const pg = await ensurePage();
    lastScreen = screenFromUrl(pg.url());
    logAction("a11y_tree", {});
    const aria = await ariaText(pg);
    try {
      fs.writeFileSync(path.join(A11Y, "step-" + String(seq).padStart(3, "0") + ".txt"), aria);
    } catch (_e) {}
    return textResult({ screen: lastScreen, a11y: aria });
  }
);

server.registerTool(
  "click",
  {
    description:
      "Click a button, tab, link, or menu item by the WORDS you see on it (e.g. \"Declare\", " +
      "\"Atlas\", \"New Chronicle\"). Prefer the exact visible label. Returns whether the click " +
      "landed and whether the screen changed (so you can tell if the button was dead).",
    inputSchema: { target: z.string().describe("The visible text/label on the control to click.") },
  },
  async ({ target }) => {
    const pg = await ensurePage();
    const before = await ariaText(pg);
    const loc = await resolveClickable(pg, target);
    if (!loc) {
      const s = logAction("click", { target, ok: false, reason: "no matching control" });
      const sc = await snap(pg, "click-miss");
      return textResult({
        ok: false,
        seq: s,
        screenshot: sc,
        reason: 'No control found matching "' + target + '". Take a screenshot or read the a11y_tree to see the exact labels.',
      });
    }
    let ok = true;
    let reason = "";
    try {
      await loc.click({ timeout: 5000 });
    } catch (e) {
      ok = false;
      reason = String(e && e.message ? e.message : e).slice(0, 200);
    }
    await pg.waitForTimeout(700);
    lastScreen = screenFromUrl(pg.url());
    const after = await ariaText(pg);
    const changed = before !== after;
    const s = logAction("click", { target, ok, changed, dead: ok && !changed, reason });
    const sc = await snap(pg, "click");
    return textResult({
      ok,
      seq: s,
      screen: lastScreen,
      screen_changed: changed,
      screenshot: sc,
      reason: ok
        ? changed
          ? ""
          : "WARNING: the click landed but NOTHING on screen changed. This looks like a dead button — consider report_bug."
        : reason,
    });
  }
);

server.registerTool(
  "type",
  {
    description:
      "Type text into a field. Leave target empty to type into the main action box (the " +
      '"Describe what your hero does..." field on the play screen). Set submit=true to press ' +
      "Enter after typing (that is how you take your turn in the story).",
    inputSchema: {
      text: z.string().describe("What to type, in plain English."),
      target: z.string().optional().describe("Field label/placeholder. Empty = the main action box."),
      submit: z.boolean().optional().describe("Press Enter after typing (default false)."),
    },
  },
  async ({ text, target, submit }) => {
    const pg = await ensurePage();
    const loc = await resolveField(pg, target || "");
    if (!loc) {
      const s = logAction("type", { target: target || "", ok: false, reason: "no field" });
      const sc = await snap(pg, "type-miss");
      return textResult({ ok: false, seq: s, screenshot: sc, reason: "No text field found. Take a screenshot to see if a field is visible." });
    }
    let ok = true;
    let reason = "";
    try {
      await loc.click({ timeout: 4000 }).catch(() => {});
      await loc.fill(String(text), { timeout: 5000 });
      if (submit) {
        await loc.press("Enter");
        await pg.waitForTimeout(900);
      }
    } catch (e) {
      ok = false;
      reason = String(e && e.message ? e.message : e).slice(0, 200);
    }
    lastScreen = screenFromUrl(pg.url());
    const s = logAction("type", {
      target: target || "(main action box)",
      text: String(text).slice(0, 200),
      submit: !!submit,
      ok,
      reason,
    });
    const sc = await snap(pg, "type");
    return textResult({ ok, seq: s, screen: lastScreen, screenshot: sc, submitted: !!submit, reason });
  }
);

server.registerTool(
  "key",
  {
    description:
      'Press a single keyboard key on the page (e.g. "Enter", "Escape", "Tab", "ArrowDown"). ' +
      "Use Enter to submit the action box, or Escape to close a popup.",
    inputSchema: { name: z.string().describe("Key name, e.g. Enter / Escape / Tab / ArrowDown.") },
  },
  async ({ name }) => {
    const pg = await ensurePage();
    let ok = true;
    let reason = "";
    try {
      await pg.keyboard.press(String(name));
      await pg.waitForTimeout(500);
    } catch (e) {
      ok = false;
      reason = String(e && e.message ? e.message : e).slice(0, 200);
    }
    lastScreen = screenFromUrl(pg.url());
    const s = logAction("key", { name, ok, reason });
    const sc = await snap(pg, "key");
    return textResult({ ok, seq: s, screen: lastScreen, screenshot: sc, reason });
  }
);

server.registerTool(
  "wait",
  {
    description:
      "Wait for the page to settle. Pass a number of milliseconds (capped at " + MAX_WAIT_MS +
      "ms) to wait that long, or a CSS selector to wait until that element appears. Use after an " +
      "action when the story or a screen might still be loading.",
    inputSchema: {
      ms: z.number().optional().describe("Milliseconds to wait (capped)."),
      selector: z.string().optional().describe("CSS selector to wait for instead of a fixed time."),
    },
  },
  async ({ ms, selector }) => {
    const pg = await ensurePage();
    let ok = true;
    let reason = "";
    if (selector) {
      try {
        await pg.waitForSelector(String(selector), { timeout: MAX_WAIT_MS });
      } catch (_e) {
        ok = false;
        reason = "selector did not appear within " + MAX_WAIT_MS + "ms";
      }
    } else {
      const dur = Math.max(0, Math.min(Number(ms || 1000), MAX_WAIT_MS));
      await pg.waitForTimeout(dur);
    }
    lastScreen = screenFromUrl(pg.url());
    const s = logAction("wait", { ms: ms || null, selector: selector || null, ok });
    return textResult({ ok, seq: s, screen: lastScreen, reason });
  }
);

server.registerTool(
  "report_bug",
  {
    description:
      "Record ONE bug or UX problem you just hit. This is the POINT of the test — be specific. " +
      "Give the severity (critical = blocks all play / major = blocks this task / minor = annoying / " +
      "trivial = cosmetic), which screen you are on, what you EXPECTED, and what ACTUALLY happened.",
    inputSchema: {
      severity: z.enum(["critical", "major", "minor", "trivial"]).describe("How bad is it."),
      screen: z.string().optional().describe("Which screen/area you are on."),
      expected: z.string().describe("What you expected to happen."),
      actual: z.string().describe("What actually happened."),
      title: z.string().optional().describe("One-line summary."),
      category: z.enum(["ux", "bug", "content", "accessibility", "performance"]).optional(),
      blocks_progress: z.boolean().optional().describe("Did this stop you from continuing?"),
      tried_alternatives: z.array(z.string()).optional().describe("Other things you tried first."),
    },
  },
  async (rec) => {
    const pg = await ensurePage().catch(() => null);
    const sc = pg ? await snap(pg, "bug") : "";
    const out = writeBug({
      category: rec.category || "ux",
      severity: rec.severity,
      screen: rec.screen || lastScreen,
      title: rec.title || rec.expected.slice(0, 80),
      expected: rec.expected,
      actual: rec.actual,
      screenshot: sc,
      tried_alternatives: rec.tried_alternatives || [],
      blocks_progress: rec.blocks_progress === true,
      source: "player",
    });
    logAction("report_bug", { severity: out.severity, title: out.title });
    return textResult({ ok: true, recorded: out.title, severity: out.severity, screenshot: sc });
  }
);

server.registerTool(
  "give_up",
  {
    description:
      "End the playtest because you are stuck and cannot find a way to continue. Explain WHY in " +
      "plain words (what you tried, what blocked you). Only use this when you have genuinely " +
      "exhausted the obvious options.",
    inputSchema: { reason: z.string().describe("Why you are giving up.") },
  },
  async ({ reason }) => {
    const pg = await ensurePage().catch(() => null);
    const sc = pg ? await snap(pg, "giveup") : "";
    logAction("give_up", { reason });
    writeBug({
      category: "ux",
      severity: "major",
      screen: lastScreen,
      title: "Player gave up: " + String(reason).slice(0, 80),
      expected: "A first-timer can keep playing without getting stuck.",
      actual: String(reason).slice(0, 500),
      screenshot: sc,
      blocks_progress: true,
      source: "give_up",
    });
    try {
      fs.writeFileSync(STATUS, JSON.stringify({ ended: true, reason: "give_up", detail: String(reason).slice(0, 500), at: nowIso() }));
    } catch (_e) {}
    return textResult({ ok: true, ended: true, note: "Run ended. Thanks for playing." });
  }
);

// Graceful browser teardown on exit.
async function shutdown() {
  try {
    if (browser) await browser.close();
  } catch (_e) {}
}
process.on("SIGTERM", async () => {
  await shutdown();
  process.exit(0);
});
process.on("SIGINT", async () => {
  await shutdown();
  process.exit(0);
});

(async () => {
  const transport = new StdioServerTransport();
  await server.connect(transport);
})().catch((e) => {
  process.stderr.write("palette_server fatal: " + (e && e.stack ? e.stack : e) + "\n");
  process.exit(1);
});
