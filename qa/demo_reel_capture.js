/*
 * demo_reel_capture.js — the BROWSER half of qa/demo_reel.py (#1346 R2).
 *
 * Drives the REAL /openworlds/ UI (system Chrome via Playwright's `channel:"chrome"`,
 * reusing the qa/playwright install — NO new deps, NO bundled-chromium download) around
 * the full W2/W3 walkable loop against a viewer that qa/demo_reel.py has already booted on
 * a seeded 2-room rest campaign, and saves one PNG per beat. Every walk / approach / door
 * step is a REAL DOM click on the rest board (the same onClick → POST /move the player
 * fires); the engine (in-process in the viewer) is the sole writer + router.
 *
 * Frames captured (into --out):
 *   01-rest-scene            the painterly rest board: party + a present NPC on the plate
 *   02-walk-arrived          click-to-walk — the selected token glided to the clicked cell
 *   03-approach-walk         a walk toward the NPC (the approach step)
 *   04-parley-open           clicking the NPC opens the parley AT the actor (Dialogue screen)
 *   05-door-cell             back on the board, the door cell selected for a cross
 *   06-arrived-linked-room   after the door click: arrived in the LINKED room
 *
 * Args (all required, positional):
 *   base heroName npcName walkX walkY approachX approachY outDir
 *
 * Exits 0 on a full clean loop; non-zero with a readable error if any step fails
 * (a selector never appears, a click never lands) — this is a CI-adjacent artifact.
 */
"use strict";

const path = require("path");
// Reuse the established qa/playwright install (system Chrome via channel) — never npm-install here.
// WORLDOS_PW_MODULE lets demo_reel.py point at a canonical checkout's install when this file runs
// from a git worktree (which git does not populate with the untracked node_modules).
const PW_MODULE = process.env.WORLDOS_PW_MODULE || path.join(__dirname, "playwright", "node_modules", "playwright");
const { chromium } = require(PW_MODULE);

const [, , BASE, HERO_NAME, NPC_NAME, WALK_X, WALK_Y, APP_X, APP_Y, OUT_DIR] = process.argv;
if (!BASE || !OUT_DIR) {
  console.error("usage: node demo_reel_capture.js <base> <heroName> <npcName> <walkX> <walkY> <approachX> <approachY> <outDir>");
  process.exit(2);
}
const CID_URL = `${BASE}/openworlds/#/combat`; // campaign is pinned by the booted viewer; #/combat lands on the scene screen
const SETTLE_MS = 900; // let the glide CSS-transition + surface reload land before a capture
const frames = [];

function fail(msg) {
  console.error(`DEMO-REEL-CAPTURE FAIL: ${msg}`);
  process.exit(1);
}

async function main() {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const pageErrors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 });
    page.on("pageerror", (e) => pageErrors.push(String((e && e.message) || e)));

    const shot = async (name) => {
      const p = path.join(OUT_DIR, `${name}.png`);
      await page.screenshot({ path: p });
      frames.push(name);
    };
    const board = '[data-worldos-testid="rest-board"]';
    // Click a rest-board cell by its exact aria-label (the real player click at the cell's screen box).
    const clickLabel = async (label, what) => {
      const sel = `${board} [aria-label="${label}"]`;
      const el = await page.waitForSelector(sel, { timeout: 12000 }).catch(() => null);
      if (!el) fail(`could not find board cell «${label}» (${what})`);
      await el.click();
    };
    const clickPrefix = async (prefix, what) => {
      const sel = `${board} [aria-label^="${prefix}"]`;
      const el = await page.waitForSelector(sel, { timeout: 12000 }).catch(() => null);
      if (!el) fail(`could not find board cell starting «${prefix}» (${what})`);
      await el.click();
    };
    const selectHero = () => clickPrefix(HERO_NAME, "select the hero token");

    const openBoard = async () => {
      await page.goto(CID_URL, { waitUntil: "networkidle", timeout: 30000 });
      await page.waitForSelector(board, { timeout: 15000 }).catch(() => fail("rest board never rendered"));
      await page.waitForTimeout(SETTLE_MS);
    };

    // ── 01 the rest scene: party + NPC on the plate ──────────────────────────────────────────
    await openBoard();
    await shot("01-rest-scene");

    // ── 02 click-to-walk: select the hero, click a walkable cell, glide to it ─────────────────
    await selectHero();
    const walkLabel = `walk → (${WALK_X}, ${WALK_Y})`;
    await clickLabel(walkLabel, "click-to-walk target");
    // deterministic arrival: the walkable-cell label is replaced by the token once it lands there.
    await page.waitForSelector(`${board} [aria-label="${walkLabel}"]`, { state: "detached", timeout: 12000 })
      .catch(() => fail("the click-to-walk token never arrived at the clicked cell"));
    await page.waitForTimeout(SETTLE_MS);
    await shot("02-walk-arrived");

    // ── 03 approach walk: walk the hero toward the NPC (the step before the parley) ───────────
    await selectHero();
    const appLabel = `walk → (${APP_X}, ${APP_Y})`;
    await clickLabel(appLabel, "approach-walk target (adjacent to the NPC)");
    await page.waitForSelector(`${board} [aria-label="${appLabel}"]`, { state: "detached", timeout: 12000 })
      .catch(() => fail("the approach-walk token never arrived"));
    await page.waitForTimeout(SETTLE_MS);
    await shot("03-approach-walk");

    // ── 04 click the NPC → approach + parley opens AT the actor (Dialogue screen) ─────────────
    await clickPrefix(`Talk to ${NPC_NAME}`, "click the NPC to talk");
    // the approach navigates to the Dialogue screen (onNavigate('dialogue')) and fetches /parley-surface.
    await page.waitForFunction(() => /parley|dialogue/.test((window.location.hash || "").toLowerCase()), { timeout: 12000 })
      .catch(() => fail("clicking the NPC never opened the Dialogue screen"));
    await page.waitForFunction(() => /Speaking with/i.test(document.body.innerText || ""), { timeout: 12000 })
      .catch(() => fail("the parley header (Speaking with …) never rendered"));
    await page.waitForTimeout(SETTLE_MS);
    await shot("04-parley-open");

    // ── 05 back to the board: select the hero, capture the door cell about to be crossed ──────
    // The door steps are OPTIONAL: a location with no linked room has no door cell on its board
    // (an art-backed single-room location is the common case). Capture the walk + approach-talk
    // loop only and finish clean — a shorter reel, not a failure. Skip 05/06 when no door exists.
    await openBoard();
    await selectHero();
    const doorCell = await page.$(`${board} [data-worldos-door="1"]`);
    if (!doorCell) {
      console.log(JSON.stringify({ ok: true, frames, doorSteps: false, pageErrors: pageErrors.slice(0, 8) }));
      await browser.close();
      return;
    }
    await shot("05-door-cell");

    // ── 06 door click → walk-then-cross → arrived in the LINKED room ──────────────────────────
    // onRestDoorWalk walks the selected hero to the doorway, then crosses into the linked room.
    // The linked room (Inner Hall) has an authored grid but no re-staged party token yet, so it
    // renders the door-bar empty state (not a rest board) — its "Cross to Antechamber →" return
    // door (present ONLY in the linked room, never on room A's board) is the honest arrival signal.
    const doorEl = await page.$(`${board} [data-worldos-door="1"]`);
    if (!doorEl) fail("door cell disappeared before the click");
    await doorEl.click();
    await page.waitForFunction(() => /Cross to Antechamber/i.test(document.body.innerText || ""), { timeout: 15000 })
      .catch(() => fail("never arrived in the linked room after the door click"));
    await page.waitForTimeout(SETTLE_MS);
    await shot("06-arrived-linked-room");

    console.log(JSON.stringify({ ok: true, frames, doorSteps: true, pageErrors: pageErrors.slice(0, 8) }));
  } finally {
    await browser.close();
  }
}

main().catch((e) => fail((e && e.message) || String(e)));
