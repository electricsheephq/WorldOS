// qa/ui_gate_probe.js
// Headless Playwright probe behind `qa/ui_audit_health.sh --ui-gate`.
//
// Drives one or more OpenWorlds screens at two viewport widths (1366 + 1512 —
// the "narrow desktop" + "MBP 13-inch" pair the Loop-9 audit measured against)
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
//         "launcherCta": { "text": "Resume → play", "disabled": false }
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

const VIEWPORTS = [1366, 1512];

// Canonical RACE ids — source of truth is viewer/openworlds/screen-create.jsx
// `RACES` (lines 777-866). This list exists here so `--ui-gate` can verify the
// PORTRAIT_GALLERY filter yields >= 1 entry per race (see #382 / regression
// #379 — PR #369 added 5 races without adding portraits, leaving the gallery
// empty for dwarf/halfling/gnome/dragonborn/half-orc). When RACES changes,
// update this list — the next CI run of the create-screen probe will FAIL
// loudly until both are in sync.
const CREATE_RACE_IDS = [
  'human', 'halfling', 'dwarf', 'elf', 'half', 'tiefling',
  'dragonborn', 'drow', 'githyanki', 'gnome', 'half-orc',
];

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
      const n = document.querySelector('.nav-rail');
      const rect = (el) => (el ? el.getBoundingClientRect() : null);
      const tr = rect(t);
      const nr = rect(n);
      const overlap = tr && nr
        ? (tr.left < nr.right && tr.right > nr.left)
          && (tr.top < nr.bottom && tr.bottom > nr.top)
        : false;
      const placeholders = document.querySelectorAll('.placeholder').length;
      const imgEls = Array.from(document.querySelectorAll('img'));
      const imgsBroken = imgEls.filter((i) => i.complete && i.naturalWidth === 0).length;
      // Launcher CTA — the primary Continue / Resume → play button. Other
      // screens leave this null. We can't check `el.onclick` because React
      // uses synthetic event delegation (handlers live on a root listener,
      // not on the DOM node), so "non-empty onClick" is really "the button
      // is not disabled and not aria-disabled" — which is what binds it.
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
        titleNavOverlap: overlap,
        titleRect: tr ? { left: tr.left, right: tr.right, top: tr.top, bottom: tr.bottom } : null,
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
      consoleErrors: errs.length,
      consoleErrorSamples: errs.slice(0, 3),
    };

    // 13e. gallery_per_race — create-screen only. Pure-data scrape of the
    // PORTRAIT_GALLERY source on disk (served by the viewer); no wizard
    // navigation required. This deliberately tests the SOURCE that drives the
    // UI, not the rendered DOM after a chain of clicks, because (a) the
    // filter predicate at screen-create.jsx:548 is a one-liner so it's not
    // where the bug lives, and (b) the actual regression (#379) was in the
    // PORTRAIT_GALLERY data missing entries for 5 of 11 races. We catch the
    // bug at its root cause, not its rendered symptom.
    if (screen === 'create' && !measured?.unreachable) {
      try {
        out.viewports[String(width)].galleryPerRace = await page.evaluate(
          async (raceIds) => {
            try {
              const resp = await fetch('/openworlds/screen-create.jsx');
              if (!resp.ok) return { error: `screen-create.jsx fetch ${resp.status}` };
              const src = await resp.text();
              const start = src.indexOf('const PORTRAIT_GALLERY');
              if (start < 0) return { error: 'PORTRAIT_GALLERY declaration not found' };
              const end = src.indexOf('];', start);
              if (end < 0) return { error: 'PORTRAIT_GALLERY closing not found' };
              const block = src.slice(start, end + 2);
              const counts = {};
              for (const id of raceIds) counts[id] = 0;
              // Entry-by-entry scan. Each PORTRAIT_GALLERY entry is well-formed
              // `{ slug: "...", name: "...", race: "...", alive: <bool> }` on
              // its own line; a non-greedy match across {...} captures each.
              const entryRe = /\{[^{}]*\}/g;
              let m;
              const unknownRaces = new Set();
              while ((m = entryRe.exec(block)) !== null) {
                const entry = m[0];
                const raceMatch = entry.match(/race:\s*["']([^"']+)["']/);
                if (!raceMatch) continue;
                const race = raceMatch[1];
                if (!(race in counts)) {
                  // Race tag in PORTRAIT_GALLERY that isn't a known RACE id —
                  // e.g. the #375 Dame Aylin / "aasimar" bug. Surface this so
                  // CI flags drift between PORTRAIT_GALLERY and RACES.
                  unknownRaces.add(race);
                  continue;
                }
                const aliveMatch = entry.match(/alive:\s*(true|false)/);
                const alive = !aliveMatch || aliveMatch[1] === 'true';
                if (alive) counts[race]++;
              }
              return { counts, unknownRaces: Array.from(unknownRaces) };
            } catch (e) {
              return { error: String(e).slice(0, 240) };
            }
          },
          CREATE_RACE_IDS,
        );
      } catch (e) {
        out.viewports[String(width)].galleryPerRace = { error: String(e).slice(0, 240) };
      }
    }

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
