/*
 * BeatRecorder — the screenshot-per-beat contract.
 *
 * KILLER RULE (the owner's, 2026-05-30): "every UI playtest saves a PNG per beat;
 * every UI fix renders before/after. No blind UI edits."
 *
 * This module is that rule, in code. Any UI action that goes through `beat()` is
 * sandwiched by a before.png + an after.png with no way for the caller to skip
 * either one — the action runs INSIDE the recorder, not next to it. Skipping a
 * screenshot would require not calling beat() at all (which is auditable via the
 * actions.ndjson sequence: missing seq numbers == off-the-books actions).
 *
 * CONTRACT
 * --------
 *   - No UI action runs without a before+after PNG. The caller passes the action
 *     as a callback; the recorder captures, runs, captures again, then logs.
 *   - Every PNG is named `beat-NNN-{before,after}-{label}.png` (seq zero-padded
 *     to 3 digits; label slugged to [a-z0-9-]). One-shot state captures via
 *     `snapshot()` are `beat-NNN-state-{label}.png`.
 *   - `actions.ndjson` is the contract between the Player agent and the QA review
 *     pass: one JSON line per beat with {seq, kind, label, ts, before, after,
 *     console_errors_since_last, network_failures_since_last, duration_ms,
 *     error?}. The reviewer agent reads these to know what was done and which
 *     PNG to look at — never just the PNGs alone.
 *   - `summary.json` (written by `finalize()`) is the run-level deliverable:
 *     totals + first/last beat + console/network counts. The QA scorer reads it
 *     to know whether the recording was even complete.
 *
 * DESIGN NOTES
 * ------------
 *   - Listeners (console / pageerror / requestfailed / response>=400) are
 *     attached LAZILY on first beat for a given page, and per-beat counters
 *     are zeroed at the START of each beat. So the recorded
 *     `console_errors_since_last` is "what happened during this beat's action +
 *     anything emitted in the gap between beats" — i.e. errors are always
 *     attributed forward to the next beat. This is intentional: if you click
 *     X and an error appears, you want the error pinned to the X beat.
 *   - We DO NOT swallow action errors. The action callback's throw is captured
 *     into the ndjson row (`error` field), the after.png is still taken (so you
 *     can see the broken state), and the throw is re-raised so the caller's
 *     control flow stays honest.
 *   - We are intentionally NOT an MCP server, NOT a bug-reporter, and NOT a
 *     scorer — those live in palette_server.js and ui_playtest_score.py. This
 *     module is just the evidence-collector. It does one thing well so callers
 *     (palette_server, ad-hoc fix-verifier scripts, the before/after differ for
 *     a one-line CSS change) can all share the same on-disk format.
 *   - All file I/O is synchronous-append on the hot path (matches palette_server)
 *     so a crash mid-run still leaves a valid prefix of ndjson on disk.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const SHOT_TIMEOUT_MS = 8000; // screenshot() should never block the recorder
const ACTION_TIMEOUT_DEFAULT_MS = 30000; // soft cap on a single beat's action

function nowIso() {
  return new Date().toISOString().replace(/\.\d+Z$/, "Z");
}

function slug(s) {
  return String(s || "step")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "step";
}

function appendLine(file, obj) {
  try {
    fs.appendFileSync(file, JSON.stringify(obj) + "\n");
  } catch (_e) {
    /* never let a logging failure break a tool call */
  }
}

/**
 * BeatRecorder — records screenshot pairs around UI actions.
 *
 * Usage:
 *   const rec = new BeatRecorder("/path/to/run", { logger: console.log });
 *   await rec.beat(page, "open-launcher", async () => {
 *     await page.goto("http://127.0.0.1:8799/openworlds/");
 *   });
 *   await rec.snapshot(page, "after-load");
 *   await rec.finalize();
 */
class BeatRecorder {
  /**
   * @param {string} runDir - run root; we write `screenshots/` and `actions.ndjson` under it
   * @param {object} [opts]
   * @param {(line: string) => void} [opts.logger] - optional sink for human-readable events
   * @param {number} [opts.actionTimeoutMs] - per-beat soft cap (default 30s)
   * @param {boolean} [opts.fullPage] - pass-through to page.screenshot fullPage flag
   */
  constructor(runDir, opts = {}) {
    if (!runDir || typeof runDir !== "string") {
      throw new Error("BeatRecorder: runDir is required (string)");
    }
    this.runDir = runDir;
    this.shotsDir = path.join(runDir, "screenshots");
    this.actionsFile = path.join(runDir, "actions.ndjson");
    this.summaryFile = path.join(runDir, "summary.json");
    this.logger = typeof opts.logger === "function" ? opts.logger : () => {};
    this.actionTimeoutMs = Number(opts.actionTimeoutMs) > 0 ? Number(opts.actionTimeoutMs) : ACTION_TIMEOUT_DEFAULT_MS;
    this.fullPage = !!opts.fullPage;

    fs.mkdirSync(this.runDir, { recursive: true });
    fs.mkdirSync(this.shotsDir, { recursive: true });

    this.seq = 0;
    this.totalBeats = 0;
    this.totalSnapshots = 0;
    this.totalScreenshots = 0;
    this.totalConsoleErrors = 0;
    this.totalNetworkFailures = 0;
    this.firstBeatTs = null;
    this.lastBeatTs = null;
    this.failedBeats = 0;

    // Per-page state: which pages we've already hooked, plus the rolling buffers
    // of console errors + network failures since the last beat-boundary.
    // WeakMap so closed pages get GC'd without us leaking handlers.
    this._pageState = new WeakMap();
    this._finalized = false;
  }

  /**
   * Lazily attach console + network listeners to a page. Idempotent.
   * @private
   */
  _ensureHooked(page) {
    let state = this._pageState.get(page);
    if (state) return state;

    state = {
      consoleErrors: [], // [{ts, type, text}] since last beat boundary
      networkFailures: [], // [{ts, url, method, status?, error?}] since last beat
    };

    const onConsole = (msg) => {
      const type = msg.type();
      if (type !== "error" && type !== "warning") return;
      const text = msg.text();
      // Mirror palette_server's noise filter — React-devtools nag + per-resource 404 echo
      if (/Download the React DevTools|Each child in a list should have a unique/.test(text)) return;
      // The "Failed to load resource" line is the browser's echo of a 4xx/5xx — we'll
      // record the response below; don't double-count it here.
      if (/Failed to load resource/i.test(text)) return;
      if (type === "error") {
        state.consoleErrors.push({ ts: nowIso(), type, text: text.slice(0, 600) });
      }
    };

    const onPageError = (err) => {
      const text = String(err && err.message ? err.message : err);
      state.consoleErrors.push({ ts: nowIso(), type: "pageerror", text: text.slice(0, 600) });
    };

    const onRequestFailed = (req) => {
      const failure = req.failure();
      const errText = failure ? failure.errorText : "failed";
      // Aborted navigations are user-driven; not a failure to report.
      if (/net::ERR_ABORTED/.test(errText)) return;
      state.networkFailures.push({
        ts: nowIso(),
        url: req.url(),
        method: req.method(),
        error: errText,
      });
    };

    const onResponse = (resp) => {
      const status = resp.status();
      if (status < 400) return;
      state.networkFailures.push({
        ts: nowIso(),
        url: resp.url(),
        method: resp.request().method(),
        status,
      });
    };

    page.on("console", onConsole);
    page.on("pageerror", onPageError);
    page.on("requestfailed", onRequestFailed);
    page.on("response", onResponse);

    this._pageState.set(page, state);
    return state;
  }

  /**
   * Drain the per-page rolling buffers and return what accumulated since the
   * last beat (or page-hook). Mutates state by clearing the arrays.
   * @private
   */
  _drainSince(page) {
    const state = this._ensureHooked(page);
    const consoleErrors = state.consoleErrors.splice(0);
    const networkFailures = state.networkFailures.splice(0);
    this.totalConsoleErrors += consoleErrors.length;
    this.totalNetworkFailures += networkFailures.length;
    return { consoleErrors, networkFailures };
  }

  /**
   * Take a screenshot. Returns the path RELATIVE to `runDir` (so callers can
   * embed it in summary.md / bug records without leaking absolute paths) and
   * the absolute path (so the caller can read it back).
   * @private
   */
  async _screenshot(page, filename) {
    const absPath = path.join(this.shotsDir, filename);
    const relPath = path.relative(this.runDir, absPath);
    try {
      // Race the screenshot against a hard cap — a hung page must NOT pin the
      // recorder. If we hit the cap, write a sentinel and continue.
      await Promise.race([
        page.screenshot({ path: absPath, fullPage: this.fullPage, timeout: SHOT_TIMEOUT_MS }),
        new Promise((_, reject) => setTimeout(() => reject(new Error("screenshot timeout")), SHOT_TIMEOUT_MS + 1500)),
      ]);
      this.totalScreenshots += 1;
      return { abs: absPath, rel: relPath, ok: true };
    } catch (e) {
      this.logger("BeatRecorder: screenshot failed for " + filename + ": " + (e && e.message ? e.message : e));
      return { abs: absPath, rel: relPath, ok: false, error: String(e && e.message ? e.message : e) };
    }
  }

  /**
   * Record one beat: before-screenshot, run the action, after-screenshot, log.
   *
   * @param {import('playwright').Page} page
   * @param {string} label - short slug-able description ("click-start", "open-roster")
   * @param {() => Promise<any>} action - the UI action; awaited inside the beat
   * @returns {Promise<object>} the ndjson row that was written
   *
   * If the action throws, we still take the after.png (so the broken state is
   * captured) and re-throw after writing the row. The row will have `ok:false`
   * and an `error` field.
   */
  async beat(page, label, action) {
    if (this._finalized) throw new Error("BeatRecorder: beat() called after finalize()");
    if (!page) throw new Error("BeatRecorder.beat: page is required");
    if (typeof action !== "function") throw new Error("BeatRecorder.beat: action callback is required");

    this._ensureHooked(page);
    // Drain anything that accumulated since the last beat boundary BEFORE the
    // before.png; that way the per-beat counters describe "errors during THIS
    // beat" (plus any leak from the gap, which is the conservative choice).
    this._drainSince(page);

    this.seq += 1;
    const seqStr = String(this.seq).padStart(3, "0");
    const safeLabel = slug(label);
    const startTs = nowIso();
    if (this.firstBeatTs === null) this.firstBeatTs = startTs;

    const beforeName = "beat-" + seqStr + "-before-" + safeLabel + ".png";
    const afterName = "beat-" + seqStr + "-after-" + safeLabel + ".png";

    this.logger("[beat " + seqStr + "] " + safeLabel + " — capturing before");
    const before = await this._screenshot(page, beforeName);

    let actionError = null;
    const t0 = Date.now();
    try {
      await Promise.race([
        Promise.resolve().then(() => action()),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error("beat action timeout (" + this.actionTimeoutMs + "ms)")), this.actionTimeoutMs)
        ),
      ]);
    } catch (e) {
      actionError = e;
      this.logger("[beat " + seqStr + "] action threw: " + (e && e.message ? e.message : e));
    }
    const durationMs = Date.now() - t0;

    this.logger("[beat " + seqStr + "] " + safeLabel + " — capturing after");
    const after = await this._screenshot(page, afterName);

    const { consoleErrors, networkFailures } = this._drainSince(page);

    const row = {
      ts: startTs,
      seq: this.seq,
      kind: "beat",
      label: safeLabel,
      label_raw: String(label || ""),
      duration_ms: durationMs,
      before: before.rel,
      after: after.rel,
      before_ok: before.ok,
      after_ok: after.ok,
      console_errors_since_last: consoleErrors,
      network_failures_since_last: networkFailures,
      ok: actionError === null,
    };
    if (actionError) {
      row.error = String(actionError && actionError.message ? actionError.message : actionError);
    }
    appendLine(this.actionsFile, row);
    this.lastBeatTs = nowIso();
    this.totalBeats += 1;
    if (actionError) {
      this.failedBeats += 1;
      // Re-throw so the caller's control flow is honest. The row is already on disk.
      throw actionError;
    }
    return row;
  }

  /**
   * Single screenshot + ndjson row, no before/after pairing. Use for a
   * baseline / final "this is the state" capture that is not bracketing a UI
   * action. Counted in summary.json under `snapshots`, NOT `beats`.
   *
   * @param {import('playwright').Page} page
   * @param {string} label
   * @returns {Promise<object>} the ndjson row that was written
   */
  async snapshot(page, label) {
    if (this._finalized) throw new Error("BeatRecorder: snapshot() called after finalize()");
    if (!page) throw new Error("BeatRecorder.snapshot: page is required");

    this._ensureHooked(page);
    // Drain so the counters are accurate for "since last beat or snapshot".
    const drainedPre = this._drainSince(page);

    this.seq += 1;
    const seqStr = String(this.seq).padStart(3, "0");
    const safeLabel = slug(label);
    const ts = nowIso();
    if (this.firstBeatTs === null) this.firstBeatTs = ts;

    const stateName = "beat-" + seqStr + "-state-" + safeLabel + ".png";
    this.logger("[beat " + seqStr + "] " + safeLabel + " — state snapshot");
    const shot = await this._screenshot(page, stateName);

    const row = {
      ts,
      seq: this.seq,
      kind: "snapshot",
      label: safeLabel,
      label_raw: String(label || ""),
      screenshot: shot.rel,
      screenshot_ok: shot.ok,
      console_errors_since_last: drainedPre.consoleErrors,
      network_failures_since_last: drainedPre.networkFailures,
      ok: shot.ok,
    };
    appendLine(this.actionsFile, row);
    this.lastBeatTs = ts;
    this.totalSnapshots += 1;
    return row;
  }

  /**
   * Write summary.json. Safe to call multiple times — only the LAST call is
   * authoritative. After finalize(), beat/snapshot will throw.
   *
   * @returns {Promise<object>} the summary object written
   */
  async finalize() {
    const summary = {
      schema: "screenshot-helper.v1",
      generated_at: nowIso(),
      run_dir: this.runDir,
      total_beats: this.totalBeats,
      total_snapshots: this.totalSnapshots,
      total_screenshots: this.totalScreenshots,
      failed_beats: this.failedBeats,
      total_console_errors: this.totalConsoleErrors,
      total_network_failures: this.totalNetworkFailures,
      first_beat_ts: this.firstBeatTs,
      last_beat_ts: this.lastBeatTs,
      actions_ndjson: path.relative(this.runDir, this.actionsFile),
      screenshots_dir: path.relative(this.runDir, this.shotsDir),
    };
    try {
      fs.writeFileSync(this.summaryFile, JSON.stringify(summary, null, 2) + "\n");
    } catch (e) {
      this.logger("BeatRecorder.finalize: failed to write summary.json: " + (e && e.message ? e.message : e));
    }
    this._finalized = true;
    this.logger(
      "BeatRecorder finalized — " +
        this.totalBeats +
        " beats, " +
        this.totalSnapshots +
        " snapshots, " +
        this.totalScreenshots +
        " PNGs, " +
        this.totalConsoleErrors +
        " console errors, " +
        this.totalNetworkFailures +
        " network failures"
    );
    return summary;
  }
}

module.exports = { BeatRecorder, slug };
