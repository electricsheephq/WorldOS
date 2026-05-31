// qa/ui_gate_probe.js
// Headless Playwright probe behind `qa/ui_audit_health.sh --ui-gate`.
//
// Drives one or more OpenWorlds screens at viewport widths from
// WORLDOS_UI_GATE_VIEWPORTS (default: 1366 + 1512 — the "narrow desktop" +
// "MBP 13-inch" pair the Loop-9 audit measured against)
// and emits a structured JSON line per screen so the shell wrapper can pass/fail
// per-check.
//
// Usage:
//   node qa/ui_gate_probe.js <port> <screen-hash> [screen-hash ...]
//
// Output: NDJSON, one JSON object per screen, like:
//   {
//     "screen": "launcher",
//     "viewports": {
//       "1366": {
//         "consoleErrors": 0,
//         "placeholders": 6,
//         "imgsTotal": 0,
//         "imgsBroken": 0,
//         "titleNavOverlap": true,         // collision check (#260 / #306)
//         "titleEndOverlap": false,
//         "titleLineCount": 1,
//         "titleDayReadable": true,
//         "launcherCta": { "text": "Resume → play", "disabled": false },
//         "hitTargets": null               // populated for representative clickability screens
//       },
//       "1512": { ... }
//     }
//   }
//
// Why two widths: #260 (title-bar text overlaps nav-rail) reproduces below
// ~1380px. Measuring at both 1366 (under) and 1512 (over) catches regressions
// that introduce new clipping AND verifies the fix actually clears both bands.
//
// Console-error filter: we drop `Failed to load resource … 404` lines on
// purpose — those come from un-ingested art-scope tiles and are already tracked
// by the "art_present" check + the existing _private asset-catalog gate. We
// only surface JavaScript runtime errors (TypeError, ReferenceError, React
// "uncaught" boundaries, page-level exceptions) — the kind that would render
// a screen *broken*, not just art-poor.

const path = require('path');

// Resolve playwright via the local install — works whether the script is
// invoked from the repo root, /tmp, or CI's checkout dir.
const playwrightPath = path.join(__dirname, 'playwright', 'node_modules', 'playwright');
let chromium;
try {
  ({ chromium } = require(playwrightPath));
} catch (e) {
  console.error(`ui_gate_probe: playwright not available at ${playwrightPath}`);
  console.error('Install with: (cd qa/playwright && npm install)');
  process.exit(2);
}

const port = process.argv[2] || '8765';
const screens = process.argv.slice(3);
if (!screens.length) {
  console.error('usage: node qa/ui_gate_probe.js <port> <screen> [<screen> ...]');
  process.exit(2);
}

const VIEWPORTS = (process.env.WORLDOS_UI_GATE_VIEWPORTS || '1366,1512')
  .split(',')
  .map((v) => Number(v.trim()))
  .filter((v) => Number.isFinite(v) && v > 0);

async function clickButtonPadding(page, selector, label) {
  const locator = page.locator(selector).filter({ hasText: label }).first();
  try {
    await locator.waitFor({ state: 'visible', timeout: 5000 });
  } catch {
    return { ok: false, reason: `missing ${selector} ${label}` };
  }
  const box = await locator.boundingBox();
  if (!box) return { ok: false, reason: `no bounding box for ${selector} ${label}` };
  await page.mouse.click(
    box.x + Math.min(6, Math.max(2, box.width * 0.12)),
    box.y + box.height / 2,
  );
  return { ok: true };
}

async function focusButtonAndPress(page, selector, label, key = 'Enter') {
  const locator = page.locator(selector).filter({ hasText: label }).first();
  try {
    await locator.waitFor({ state: 'visible', timeout: 5000 });
  } catch {
    return { ok: false, reason: `missing ${selector} ${label}` };
  }
  await locator.focus();
  await page.keyboard.press(key);
  return { ok: true };
}

async function activeTabLabel(page) {
  return await page.evaluate(() => (document.querySelector('.tab-button.active')?.textContent || '').trim());
}

async function activeNavLabel(page) {
  return await page.evaluate(() => (document.querySelector('.nav-item.active .tip')?.textContent || '').trim());
}

async function runHitTargetChecks(page, screen) {
  if (screen !== 'character') return null;
  const checks = {};

  const tabClick = await clickButtonPadding(page, '.tab-button', 'Stash');
  await page.waitForTimeout(250);
  checks.tabPaddingClickFired = tabClick.ok && await activeTabLabel(page) === 'Stash';
  if (!tabClick.ok) checks.tabPaddingClickReason = tabClick.reason;

  const tabKeyboard = await focusButtonAndPress(page, '.tab-button', 'Forge');
  await page.waitForTimeout(250);
  checks.tabKeyboardFired = tabKeyboard.ok && await activeTabLabel(page) === 'Forge';
  if (!tabKeyboard.ok) checks.tabKeyboardReason = tabKeyboard.reason;

  const navClick = await clickButtonPadding(page, '.nav-item', 'Map');
  await page.waitForTimeout(250);
  checks.navPaddingClickFired = navClick.ok && await activeNavLabel(page) === 'Map';
  if (!navClick.ok) checks.navPaddingClickReason = navClick.reason;

  const navKeyboard = await focusButtonAndPress(page, '.nav-item', 'Journal');
  await page.waitForTimeout(250);
  checks.navKeyboardFired = navKeyboard.ok && await activeNavLabel(page) === 'Journal';
  if (!navKeyboard.ok) checks.navKeyboardReason = navKeyboard.reason;

  return checks;
}

async function probeScreen(browser, screen) {
  const out = { screen, viewports: {} };
  for (const width of VIEWPORTS) {
    const ctx = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await ctx.newPage();
    const errs = [];
    page.on('console', (m) => {
      if (m.type() !== 'error') return;
      const t = m.text();
      // Filter the noisy asset-404 stream — those are tracked by art_present, not renders_clean.
      if (/Failed to load resource.*(404|net::ERR_)/.test(t)) return;
      errs.push(t.slice(0, 240));
    });
    page.on('pageerror', (e) => errs.push('PAGEERROR ' + String(e).slice(0, 240)));
    try {
      await page.goto(`http://127.0.0.1:${port}/openworlds/#${screen}`, {
        waitUntil: 'domcontentloaded',
        timeout: 15000,
      });
      // Babel-standalone transpiles JSX on first paint; wait for the React tree to
      // mount and lazy effects (Img resolution, route-bound data) to settle. We
      // poll `document.querySelector('.title-bar')` rather than `networkidle`
      // because the dev server's hot-reload pings keep the network busy.
      await page.waitForFunction(() => !!document.querySelector('.title-bar'), { timeout: 10000 });
      await page.waitForTimeout(1200);
    } catch (e) {
      errs.push('NAV ' + String(e).slice(0, 220));
    }
    // Babel-standalone can fire a late re-eval that destroys the V8 context
     // while we're mid-evaluate. Retry once on context-destroyed.
    const evalOnce = async () => await page.evaluate(() => {
      // title-bar / nav-rail collision (#260 / #306). The actual bug is text
      // overflowing the title-text grid cell and being occluded by the nav-rail
      // at narrow widths. We check both horizontal AND vertical overlap of the
      // BOUNDING RECTS — when title text wraps, its bottom extends down past
      // the nav-rail's top, producing a true visual collision.
      const t = document.querySelector('.title-text');
      const e = document.querySelector('.title-end');
      const day = document.querySelector('.title-end > span:last-child');
      const n = document.querySelector('.nav-rail');
      const rect = (el) => (el ? el.getBoundingClientRect() : null);
      const tr = rect(t);
      const er = rect(e);
      const dr = rect(day);
      const nr = rect(n);
      const navOverlap = tr && nr
        ? (tr.left < nr.right && tr.right > nr.left)
          && (tr.top < nr.bottom && tr.bottom > nr.top)
        : false;
      const titleEndOverlap = tr && er
        ? (tr.left < er.right && tr.right > er.left)
          && (tr.top < er.bottom && tr.bottom > er.top)
        : false;
      let titleLineCount = null;
      if (t) {
        const range = document.createRange();
        range.selectNodeContents(t);
        const tops = Array.from(range.getClientRects())
          .filter((r) => r.width > 0 && r.height > 0)
          .map((r) => Math.round(r.top));
        titleLineCount = new Set(tops).size || 0;
        range.detach();
      }
      const placeholders = document.querySelectorAll('.placeholder').length;
      const imgEls = Array.from(document.querySelectorAll('img'));
      const imgsBroken = imgEls.filter((i) => i.complete && i.naturalWidth === 0).length;
      // Launcher CTA — the primary Continue / Resume → play button. Other
      // screens leave this null.
      let launcherCta = null;
      if ((location.hash === '#launcher' || location.hash === '' || location.hash === '#')) {
        const btns = Array.from(document.querySelectorAll('button'));
        const cta = btns.find((b) => /continue|resume/i.test((b.textContent || '')));
        launcherCta = cta
          ? {
              text: (cta.textContent || '').trim().slice(0, 80),
              disabled: cta.disabled || cta.getAttribute('aria-disabled') === 'true',
            }
          : { missing: true };
      }
      return {
        titleNavOverlap: navOverlap,
        titleEndOverlap,
        titleLineCount,
        titleDayReadable: dr ? dr.height >= 12 && dr.width >= 24 : null,
        titleRect: tr ? { left: tr.left, right: tr.right, top: tr.top, bottom: tr.bottom } : null,
        titleEndRect: er ? { left: er.left, right: er.right, top: er.top, bottom: er.bottom } : null,
        titleDayRect: dr ? { left: dr.left, right: dr.right, top: dr.top, bottom: dr.bottom, height: dr.height } : null,
        navRect: nr ? { left: nr.left, right: nr.right, top: nr.top, bottom: nr.bottom } : null,
        placeholders,
        imgsTotal: imgEls.length,
        imgsBroken,
        launcherCta,
      };
    });
    let measured;
    try {
      measured = await evalOnce();
    } catch (e) {
      if (/Execution context was destroyed/.test(String(e))) {
        await page.waitForTimeout(800);
        measured = await evalOnce();
      } else {
        throw e;
      }
    }
    out.viewports[String(width)] = {
      ...measured,
      hitTargets: await runHitTargetChecks(page, screen),
      consoleErrors: errs.length,
      consoleErrorSamples: errs.slice(0, 3),
    };
    await ctx.close();
  }
  return out;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const s of screens) {
      const row = await probeScreen(browser, s);
      // NDJSON so the shell can stream and parse incrementally.
      console.log(JSON.stringify(row));
    }
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error('ui_gate_probe fatal: ' + (e?.stack || e));
  process.exit(3);
});
