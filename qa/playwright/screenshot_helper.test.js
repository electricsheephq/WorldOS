/*
 * Smoke test for BeatRecorder.
 *
 * Launches a headless Chromium, navigates to https://example.com, runs two
 * beats + one snapshot through the recorder, finalizes, and asserts that:
 *   1. The before/after PNGs exist for each beat (and are non-empty).
 *   2. The state PNG exists for the snapshot.
 *   3. actions.ndjson has exactly the rows we expect, in order, with the
 *      right shape (seq, label, kind, before/after fields).
 *   4. summary.json reports the right totals.
 *   5. Calling beat() after finalize() throws.
 *
 * Run it directly:
 *   cd qa/playwright && node screenshot_helper.test.js
 *
 * Exit code is 0 on success, non-zero on the first failed assertion. Output
 * is plain text so it's tail-friendly under `gh run watch` and the playtest
 * harness's run log.
 *
 * NOTE: this test makes one real network request to https://example.com — it
 * is deliberately the same target the W3C / IETF / Playwright docs use as a
 * stable smoke endpoint. If that's offline, the test will fail the navigation
 * (which is itself a fine signal that the recorder still wrote a row with
 * `ok:false` and the after.png for the broken state).
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const assert = require("assert");

const { BeatRecorder, slug } = require("./screenshot_helper.js");

const TEST_URL = process.env.SCREENSHOT_HELPER_TEST_URL || "https://example.com";

function readNdjson(file) {
  const raw = fs.readFileSync(file, "utf8");
  return raw
    .split("\n")
    .filter((l) => l.trim().length > 0)
    .map((l) => JSON.parse(l));
}

async function main() {
  // 1. Slug helper sanity (no Playwright needed)
  assert.strictEqual(slug("Open Launcher!"), "open-launcher", "slug should lowercase + dash");
  assert.strictEqual(slug(""), "step", "slug empty -> fallback");
  assert.strictEqual(slug("a/b/c"), "a-b-c", "slug should replace slashes");
  console.log("[ok] slug helper");

  // 2. Set up a run dir under the OS tempdir so the test is self-cleaning-ish
  const runDir = fs.mkdtempSync(path.join(os.tmpdir(), "beatrec-smoke-"));
  console.log("[info] runDir = " + runDir);

  // 3. Capture logger output so we can assert it isn't silent
  const loggedLines = [];
  const rec = new BeatRecorder(runDir, { logger: (s) => loggedLines.push(s) });

  // 4. Launch Playwright. Resolve the local install path (sub-agent 1 installs
  // playwright at qa/playwright/node_modules) so this test works both from the
  // qa/playwright dir and elsewhere.
  let chromium;
  try {
    ({ chromium } = require("playwright"));
  } catch (e) {
    console.error("[fail] playwright not installed; install it first via `npm i` in qa/playwright/");
    console.error("       (" + (e && e.message ? e.message : e) + ")");
    process.exit(2);
  }

  const browser = await chromium.launch({ headless: true });
  let exitCode = 0;
  try {
    const ctx = await browser.newContext({ viewport: { width: 1024, height: 768 } });
    const page = await ctx.newPage();

    // Beat 1 — navigate. This is the canonical first beat in any UI playtest.
    const row1 = await rec.beat(page, "Goto example", async () => {
      await page.goto(TEST_URL, { waitUntil: "domcontentloaded", timeout: 20000 });
    });
    assert.strictEqual(row1.seq, 1, "first beat seq is 1");
    assert.strictEqual(row1.kind, "beat", "kind is beat");
    assert.strictEqual(row1.label, "goto-example", "label slugged");
    assert.strictEqual(row1.ok, true, "first beat ok");
    assert.ok(row1.before && row1.after, "before+after recorded");
    assert.ok(row1.before.startsWith("screenshots/"), "before path is run-relative");
    assert.ok(Array.isArray(row1.console_errors_since_last), "console-errors array present");
    console.log("[ok] beat 1 — goto");

    // Beat 2 — read the page title (a no-op for the DOM, but exercises the
    // before/after contract on a second call so we can assert seq advances).
    const row2 = await rec.beat(page, "Read title", async () => {
      const t = await page.title();
      assert.ok(t.length > 0, "page has a title");
    });
    assert.strictEqual(row2.seq, 2, "second beat seq is 2");
    console.log("[ok] beat 2 — read title");

    // Snapshot — single screenshot, no action.
    const row3 = await rec.snapshot(page, "final state");
    assert.strictEqual(row3.seq, 3, "snapshot uses next seq");
    assert.strictEqual(row3.kind, "snapshot", "kind is snapshot");
    assert.ok(row3.screenshot.startsWith("screenshots/"), "snapshot screenshot path");
    console.log("[ok] snapshot");

    // Assert files on disk
    const shotsDir = path.join(runDir, "screenshots");
    const expectedFiles = [
      "beat-001-before-goto-example.png",
      "beat-001-after-goto-example.png",
      "beat-002-before-read-title.png",
      "beat-002-after-read-title.png",
      "beat-003-state-final-state.png",
    ];
    for (const f of expectedFiles) {
      const p = path.join(shotsDir, f);
      assert.ok(fs.existsSync(p), "expected screenshot exists: " + f);
      const stat = fs.statSync(p);
      assert.ok(stat.size > 200, "screenshot non-trivial size (>200b): " + f + " (got " + stat.size + ")");
    }
    console.log("[ok] all 5 expected PNGs exist + non-trivial");

    // Assert ndjson contents
    const actionsFile = path.join(runDir, "actions.ndjson");
    assert.ok(fs.existsSync(actionsFile), "actions.ndjson exists");
    const rows = readNdjson(actionsFile);
    assert.strictEqual(rows.length, 3, "ndjson has 3 rows");
    assert.deepStrictEqual(
      rows.map((r) => r.seq),
      [1, 2, 3],
      "ndjson seq is 1,2,3 in order"
    );
    assert.deepStrictEqual(
      rows.map((r) => r.kind),
      ["beat", "beat", "snapshot"],
      "ndjson kinds beat/beat/snapshot"
    );
    console.log("[ok] actions.ndjson well-formed");

    // Finalize + summary.json
    const summary = await rec.finalize();
    assert.strictEqual(summary.total_beats, 2, "summary total_beats");
    assert.strictEqual(summary.total_snapshots, 1, "summary total_snapshots");
    assert.strictEqual(summary.total_screenshots, 5, "summary total_screenshots");
    assert.strictEqual(summary.failed_beats, 0, "summary failed_beats");
    assert.ok(summary.first_beat_ts && summary.last_beat_ts, "summary has timestamps");

    const summaryFile = path.join(runDir, "summary.json");
    assert.ok(fs.existsSync(summaryFile), "summary.json on disk");
    const summaryOnDisk = JSON.parse(fs.readFileSync(summaryFile, "utf8"));
    assert.strictEqual(summaryOnDisk.schema, "screenshot-helper.v1", "schema label");
    console.log("[ok] summary.json");

    // Calling beat() after finalize must throw
    let threw = false;
    try {
      await rec.beat(page, "post-final", async () => {});
    } catch (_e) {
      threw = true;
    }
    assert.ok(threw, "beat() after finalize() throws");
    console.log("[ok] post-finalize beat() throws");

    // Logger received output
    assert.ok(loggedLines.length > 0, "logger received lines");
    console.log("[ok] logger received " + loggedLines.length + " lines");

    console.log("\n[PASS] screenshot_helper smoke OK — runDir kept for inspection: " + runDir);
  } catch (e) {
    exitCode = 1;
    console.error("[FAIL] " + (e && e.stack ? e.stack : e));
  } finally {
    await browser.close().catch(() => {});
  }
  process.exit(exitCode);
}

main().catch((e) => {
  console.error("[FAIL] unhandled: " + (e && e.stack ? e.stack : e));
  process.exit(1);
});
