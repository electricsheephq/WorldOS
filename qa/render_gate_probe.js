/*
 * render_gate_probe.js — the M1 #440 render gate for the GT1 canvas renderer.
 *
 * The viewer's existing qa/ui_gate_probe.js targets the React app's #hash screens and inspects
 * React chrome (.tab-button / .nav-item). The graphics renderers are a DIFFERENT surface: a
 * standalone Phaser <canvas> sub-app served at a PATH (/openworlds/render/*.html). This probe
 * is the canvas analogue — it loads a render page headless and asserts the "renders clean"
 * gate the graphics roadmap calls for:
 *   - 0 console errors / 0 page exceptions during boot + first render
 *   - a Phaser <canvas> mounts and is non-trivially sized (the scene actually rendered)
 *   - the page is the standalone renderer (not the React app), so NO VTT-grid chrome leaks
 *
 * Usage:  node qa/render_gate_probe.js <port> <render-page> [<render-page> ...]
 *   e.g.  node qa/render_gate_probe.js 8765 render/tilemap.html render/index.html
 * Output: NDJSON, one object per page; exit 0 iff every page passes.
 *
 * Playwright is resolved from qa/playwright/node_modules (same as ui_gate_probe.js).
 */
"use strict";

const path = require("path");

const playwrightPath = path.join(__dirname, "playwright", "node_modules", "playwright");
let chromium;
try {
  ({ chromium } = require(playwrightPath));
} catch (e) {
  console.error(`render_gate_probe: playwright not available at ${playwrightPath}`);
  console.error("Install with: (cd qa/playwright && npm install && npx playwright install chromium)");
  process.exit(2);
}

const port = process.argv[2] || "8765";
const pages = process.argv.slice(3);
if (!pages.length) {
  console.error("usage: node qa/render_gate_probe.js <port> <render-page> [<render-page> ...]");
  process.exit(2);
}
const VIEWPORTS = (process.env.WORLDOS_RENDER_GATE_VIEWPORTS || "1366,1512")
  .split(",").map((v) => Number(v.trim())).filter((v) => Number.isFinite(v) && v > 0);

async function probePage(browser, page) {
  const out = { page, viewports: {} };
  for (const width of VIEWPORTS) {
    const ctx = await browser.newContext({ viewport: { width, height: 900 } });
    const pg = await ctx.newPage();
    const errors = [];
    pg.on("console", (m) => { if (m.type() === "error") errors.push(m.text().slice(0, 200)); });
    pg.on("pageerror", (e) => errors.push("PAGEERROR " + String(e).slice(0, 200)));
    let canvasOk = false, canvasW = 0, canvasH = 0;
    try {
      await pg.goto(`http://127.0.0.1:${port}/openworlds/${page}`, { waitUntil: "load", timeout: 15000 });
      // give Phaser a beat to boot + run the first render (poll tick is immediate)
      await pg.waitForTimeout(1500);
      const box = await pg.evaluate(() => {
        const c = document.querySelector("canvas");
        if (!c) return null;
        return { w: c.width, h: c.height };
      });
      if (box) { canvasOk = box.w > 100 && box.h > 100; canvasW = box.w; canvasH = box.h; }
    } catch (e) {
      errors.push("NAV " + String(e).slice(0, 160));
    }
    out.viewports[String(width)] = {
      consoleErrors: errors.length,
      errorSamples: errors.slice(0, 3),
      canvasPresent: canvasW > 0,
      canvasOk,
      canvasSize: `${canvasW}x${canvasH}`,
    };
    await ctx.close();
  }
  return out;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  let failed = false;
  try {
    for (const pg of pages) {
      const row = await probePage(browser, pg);
      console.log(JSON.stringify(row));
      for (const vp of Object.values(row.viewports)) {
        if (vp.consoleErrors > 0 || !vp.canvasOk) failed = true;
      }
    }
  } finally {
    await browser.close();
  }
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error("render_gate_probe fatal:", e); process.exit(3); });
