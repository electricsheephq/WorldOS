/* Building-Your-Universe — the full-screen "the world is being made" loading experience.
 *
 * THE WAIT IT COVERS (the owner's ask). Pressing Start / Resume → Play (launcher) or Bind
 * (the Forge/Creation wizard) mints a DM provider session and then generates the cold-open —
 * two long waits with a full page reload wedged BETWEEN them:
 *   (a) startProviderSession mints the run + the bridge returns a live viewer URL, then
 *       window.location.assign() RELOADS the page onto that fresh viewer; and
 *   (b) the reloaded live viewer boots and the DM composes the first beat (~30–90s, sometimes
 *       minutes — the engine is building the world + setting the scene; the /chat tail carries
 *       no streaming, so the first narration lands all-at-once).
 * Before this, the player saw "nothing happens" then an abrupt read-only flash. This replaces
 * BOTH waits with one intentional, on-brand (parchment/brass) loading state that PERSISTS across
 * the mint, the reload, AND the cold-open, then hands off to the live table the instant the first
 * DM narration arrives.
 *
 * HOW IT SURVIVES THE RELOAD. The "we are building a universe" intent is stamped into
 * sessionStorage (NOT React state — React state dies with the page on location.assign).
 * sessionStorage survives a same-tab navigation and is auto-dropped when the tab closes, so a
 * stale flag can't leak into an unrelated future session. window.OpenWorldsBuilding is the tiny
 * persistence facade; useBuildingUniverse (consumed by App) reads it on mount so the overlay is
 * already up the moment the reloaded page paints — no blank gap.
 *
 * HOW IT HANDS OFF (honest, not faked). It does NOT guess a percentage. It detects the REAL
 * milestone — the first DM narration beat — off the SAME signal the in-table cold-open uses:
 * liveSession.chatBeats gaining a { kind: "narration" } entry (app.jsx's /chat poll). When that
 * lands, the universe is built: we show a one-beat "Your story begins…" flourish, then clear the
 * flag and let App route to the table where that very first beat is already in the chronicle.
 *
 * HOW IT CAN NEVER WEDGE (#405). The overlay is only a COVER for the slow cold-open — it must YIELD
 * to the table (which has the real recovery: live streaming narration, a 180s "DM is narrating…"
 * timeout, and a "Try again" affordance). So dismiss() — wired in App — fires on ANY of three exits,
 * not just the handoff: (1) a HARD ERROR (a cold-open / session error reported on the native bridge)
 * dismisses immediately; (2) a ~120s STALL CEILING (a FIXED deadline from the build's start, NOT a
 * 12-min one and NOT re-armed per beat) dismisses so the table's own handling takes over; and (3) a
 * manual "Enter anyway →" affordance (after ~15s) lets an impatient player skip to the table anytime.
 * After any dismiss the table is fully reachable (the z-9000 cover is unmounted) and usable — on a
 * cold-open the action bar is enabled, so the player can act and the table's own narrating/stuck-
 * recovery engages. The 12-min sessionStorage self-heal in read() is a SEPARATE net for the reload
 * path only (a stale flag can't re-enter the overlay), not the live overlay's ceiling.
 *
 * The bar of honesty (per the owner): animated "composing your opening…" + rotating lore flavor +
 * a live elapsed readout. No fake progress bar we can't back.
 */

// ---- persistence facade (survives location.assign) ------------------------------------------
// One sessionStorage record describes the in-flight "build". begin() is called SYNCHRONOUSLY at
// the click — before startProviderSession — by both the launcher (startPlay) and the Forge
// (bindHero), so the overlay is up instantly, before the async bridge hop and the reload.
const OW_BUILDING_KEY = "openworlds.building";
// #405: the LIVE dismiss ceiling. The overlay is only a COVER for the legitimately-slow cold-open
// (the engine spends ~55s building the world + composing the opening). It is NOT the recovery
// surface — the table is (live streaming narration, a 180s "DM is narrating…" timeout, and a
// "Try again" recovery). So if no first-narration handoff arrives within this ceiling we DISMISS
// the overlay and let the table's own handling take over, rather than wedging the player on a
// full-screen cover. ~120s comfortably covers the real cold-open + margin without the old
// 12-minute dead-end. This is a FIXED deadline measured from the build's startedAt — it does NOT
// re-arm on streamed beats (the overlay's handoff is the first-narration milestone, not "any beat").
const OW_BUILDING_CEILING_MS = 120 * 1000;
// #405: a manual escape. After this long with no handoff, surface an "Enter anyway →" affordance so
// an impatient player can skip straight to the table at any time (the table is fully usable — its
// own narrating/stuck-recovery takes over). Well below the ceiling so the player is never trapped.
const OW_BUILDING_ESCAPE_MS = 15 * 1000;
// #405: a short grace before an ERROR signal is honored as a dismiss. The error comes from the
// native bridge's lastError, which can carry a STALE error from a PRIOR failed attempt at the
// instant a fresh build begins (the same-page launcher window, before the reload re-inits native
// state). Ignoring the error for the first few seconds of a build prevents a just-clicked Play from
// being nuked by a leftover error; a genuine cold-open failure is observed on a later bridge poll
// (well past this grace), so real errors still dismiss promptly. Far below the manual-escape time,
// so the player is never stuck waiting on it.
const OW_BUILDING_ERROR_GRACE_MS = 3 * 1000;
// The cross-RELOAD stale-flag net (NOT a min-display floor — no such floor exists; on a fast bridge
// failure both screen-launcher and screen-create call clear() immediately, so the overlay flashes
// away). A persisted record older than this is stale — e.g. a tab left on the overlay, then a fresh
// load — so read() drops it rather than re-entering the overlay. Kept generous (12 min) because it
// only guards the reload path; the LIVE overlay's lifetime is governed by the handoff + the ~120s
// ceiling above + the error/manual dismiss wired in App, none of which depend on this.
const OW_BUILDING_BACKSTOP_MS = 12 * 60 * 1000;

window.OpenWorldsBuilding = window.OpenWorldsBuilding || {
  // Stamp the intent + announce it so a still-mounted App shows the overlay this tick (pre-reload).
  begin(meta) {
    const record = {
      startedAt: Date.now(),
      world: (meta && meta.world) || "",
      title: (meta && meta.title) || "",
      // "forge" | "play" — purely cosmetic (the eyebrow copy), never load-bearing.
      kind: (meta && meta.kind) || "play",
    };
    try { window.sessionStorage.setItem(OW_BUILDING_KEY, JSON.stringify(record)); } catch (_e) {}
    try { window.dispatchEvent(new CustomEvent("worldos:building-begin", { detail: record })); } catch (_e) {}
    return record;
  },
  read() {
    try {
      const raw = window.sessionStorage.getItem(OW_BUILDING_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed.startedAt !== "number") return null;
      // Self-heal: a record older than the hard backstop is stale (e.g. the tab was left on the
      // overlay for 12+ min with no beat) — drop it so a fresh load doesn't re-enter the overlay.
      if (Date.now() - parsed.startedAt > OW_BUILDING_BACKSTOP_MS) {
        this.clear();
        return null;
      }
      return parsed;
    } catch (_e) {
      return null;
    }
  },
  clear() {
    try { window.sessionStorage.removeItem(OW_BUILDING_KEY); } catch (_e) {}
  },
  backstopMs: OW_BUILDING_BACKSTOP_MS,
  ceilingMs: OW_BUILDING_CEILING_MS,   // #405: the live dismiss ceiling (yield to the table)
  escapeMs: OW_BUILDING_ESCAPE_MS,     // #405: when the "Enter anyway" affordance appears
  errorGraceMs: OW_BUILDING_ERROR_GRACE_MS,  // #405: grace before a (possibly stale) error dismisses
};

// ---- rotating lore flavor (honest "the world is being assembled" cues) -----------------------
// Three phases so the copy tracks the real arc of the wait (the world → the factions → your
// hero → it's almost ready), and so consecutive renders/snapshots DIFFER (the same proof-of-life
// lesson as #385: a single unchanging line reads as a frozen app to a screenshot AND the a11y
// tree). These are flavor, not status — they don't claim a step is "done", only that the world is
// coming together. The headline rotates every ~3.5s.
const BUILDING_FLAVOR = [
  "Assembling the Sword Coast…",
  "Unrolling the map of Faerûn…",
  "Lighting the lamps along the cobbled streets…",
  "The Flaming Fist musters at the city gates…",
  "Harpers trade whispers in shadowed taverns…",
  "Thieves of the Guild count coin in the undercellars…",
  "Your hero draws breath at the edge of the tale…",
  "Fate shuffles the deck of your first encounter…",
  "Gathering the threads of your story…",
  "The Dungeon Master composes your opening scene…",
  "The ink is still drying on your first page…",
];

// #406 (5): the LATE-phase pool. The early pool plays through in ~40s, but a real cold-open can run
// well past that (a blind newbie run saw minutes). Rather than recycle the bright early lines —
// which reads as "stuck on a loop" — shift past ~42s to a calmer, patient register that owns the
// length of the wait honestly ("a rich opening is worth it"). Still rotates so renders/snapshots
// differ (the #385 proof-of-life lesson), and is intentionally a different length from the early
// pool so the two phases don't lock-step.
const BUILDING_FLAVOR_LATE = [
  "Still composing — a rich opening is worth the wait…",
  "The Dungeon Master is weaving the finer details…",
  "Setting the final pieces of your opening scene…",
  "A great tale takes a moment longer to begin…",
  "Almost there — the first page is nearly written…",
  "Holding the curtain a beat longer, for a worthy entrance…",
  "The world is taking shape around your hero…",
];

// A short, calm sub-line that rotates more slowly — sets the expectation honestly.
const BUILDING_SUBLINE = [
  "Your world is being built. This first moment can take up to a minute.",
  "The Dungeon Master is setting the stage — hang tight, your story is on its way.",
  "Worlds are not made in an instant. The first scene is worth the wait.",
];

// ---- the App-level hook --------------------------------------------------------------------
// Owns the overlay's lifecycle. Reads the persisted intent on mount (so a reloaded page shows the
// overlay immediately), listens for begin() (the pre-reload, same-page case), and HANDS OFF when
// the first DM narration beat lands in liveSession.chatBeats — the same real milestone the
// in-table cold-open clears on. #405: it ALSO dismisses (yields to the table) on a hard cold-open
// error, on a ~120s stall ceiling, and via a manual "Enter anyway →" affordance — so the cover can
// never wedge full-screen over the table's own recovery.
//   `sessionError` (optional): a truthy cold-open/session error reported on the native bridge. When
//   set, the overlay dismisses immediately so the player isn't stranded on a full-screen cover while
//   the table (which surfaces the error + a retry) sits unreachable beneath it.
// Returns { active, record, handoff, escapable, dismiss }.
function useBuildingUniverse(liveSession, sessionError) {
  const [record, setRecord] = React.useState(() => window.OpenWorldsBuilding.read());
  // "handoff" is the brief flourish phase after the first beat lands but before we unmount — so the
  // table doesn't pop in with a jarring cut; the player reads "Your story begins…" for a beat.
  const [handoff, setHandoff] = React.useState(false);
  // #405: once the build has been up past OW_BUILDING_ESCAPE_MS with no handoff, expose the manual
  // "Enter anyway →" affordance. Drives only the affordance's visibility; dismiss() does the work.
  const [escapable, setEscapable] = React.useState(false);
  const handoffTimer = React.useRef(null);
  const ceilingTimer = React.useRef(null);
  const escapeTimer = React.useRef(null);
  const errorTimer = React.useRef(null);

  // The one true exit. Clears the persisted flag + unmounts the overlay (App then routes to the
  // table). Used by the handoff flourish, the error dismiss, the stall ceiling, AND the manual
  // "Enter anyway" button — every path that reveals the table funnels through here.
  const dismiss = React.useCallback(() => {
    window.OpenWorldsBuilding.clear();
    setRecord(null);
    setHandoff(false);
    setEscapable(false);
  }, []);

  // begin() fired on THIS page (no reload yet — the launcher/forge click) → show immediately.
  React.useEffect(() => {
    const onBegin = (e) => {
      setHandoff(false);
      setEscapable(false);
      setRecord((e && e.detail) || window.OpenWorldsBuilding.read());
    };
    window.addEventListener("worldos:building-begin", onBegin);
    return () => window.removeEventListener("worldos:building-begin", onBegin);
  }, []);

  // #405 (1) HARD ERROR → dismiss. A cold-open / session error means no narration will ever arrive;
  // the table surfaces the error + a retry, so get off the cover rather than making the player wait
  // out a ceiling on a doomed build. Honored only after a short grace from the build's start, so a
  // STALE bridge error at the click instant can't nuke a just-begun build (see the grace const);
  // a genuine cold-open failure is seen on a later poll, well past the grace, so it still dismisses.
  React.useEffect(() => {
    if (!record || handoff) return undefined;
    if (!sessionError) return undefined;
    const sinceStart = Date.now() - (record.startedAt || Date.now());
    if (sinceStart >= OW_BUILDING_ERROR_GRACE_MS) { dismiss(); return undefined; }
    // Within the grace window — re-check once it elapses (the error may be a stale leftover that a
    // fresh build will clear; if it's still set past the grace, dismiss then).
    errorTimer.current = window.setTimeout(dismiss, OW_BUILDING_ERROR_GRACE_MS - sinceStart);
    return () => {
      if (errorTimer.current) { window.clearTimeout(errorTimer.current); errorTimer.current = null; }
    };
  }, [record, handoff, sessionError, dismiss]);

  // #405 (2) STALL CEILING — a FIXED ~120s deadline from the build's start (NOT re-armed per beat,
  // NOT the old 12-min wall). If no first-narration handoff lands within it, dismiss so the table's
  // own cold-open/stuck recovery (its 180s "DM is narrating…" timeout + "Try again") takes over.
  // The cover exists only to mask the slow cold-open; once that's plausibly overrun, the table is
  // the better surface. Also arms the ~15s manual-escape affordance off the SAME fixed clock.
  React.useEffect(() => {
    if (!record || handoff) return undefined;
    const start = record.startedAt || Date.now();
    const ceilingRemaining = Math.max(0, OW_BUILDING_CEILING_MS - (Date.now() - start));
    const escapeRemaining = Math.max(0, OW_BUILDING_ESCAPE_MS - (Date.now() - start));
    ceilingTimer.current = window.setTimeout(dismiss, ceilingRemaining);
    // Surface the manual escape once past the threshold (immediately if a reload already overran it).
    if (escapeRemaining <= 0) setEscapable(true);
    else escapeTimer.current = window.setTimeout(() => setEscapable(true), escapeRemaining);
    return () => {
      if (ceilingTimer.current) { window.clearTimeout(ceilingTimer.current); ceilingTimer.current = null; }
      if (escapeTimer.current) { window.clearTimeout(escapeTimer.current); escapeTimer.current = null; }
    };
  }, [record, handoff, dismiss]);

  // THE HANDOFF. The first DM narration beat = the universe is built. Detect it off the live
  // chat tail (the exact signal the cold-open pending clears on). FLIP into the flourish phase —
  // but do NOT arm the dismiss timer here: this effect's deps include `handoff`, so flipping it
  // re-runs the effect and its cleanup, which would CANCEL a timer armed in the same run before it
  // could fire (the latent bug the un-effect'd unit tests could never catch — #406). The dismiss
  // timer is armed in a SEPARATE effect keyed on `handoff`, below, whose cleanup only fires on
  // unmount/record-change — so the flourish reliably ends in a dismiss.
  const hasFirstNarration =
    Array.isArray(liveSession && liveSession.chatBeats) &&
    liveSession.chatBeats.some((b) => b && b.kind === "narration");

  React.useEffect(() => {
    if (!record || handoff) return;
    if (!hasFirstNarration) return;
    setHandoff(true);
    setEscapable(false);
  }, [record, handoff, hasFirstNarration]);

  // Arm the 1400ms flourish→dismiss timer ONCE we're in the handoff phase. Separate effect so the
  // timer outlives the render that set `handoff` (see above). Cleanup only on unmount/record swap.
  React.useEffect(() => {
    if (!handoff) return undefined;
    handoffTimer.current = window.setTimeout(() => {
      dismiss();
    }, 1400);
    return () => {
      if (handoffTimer.current) { window.clearTimeout(handoffTimer.current); handoffTimer.current = null; }
    };
  }, [handoff, dismiss]);

  React.useEffect(() => () => {
    if (handoffTimer.current) window.clearTimeout(handoffTimer.current);
    if (ceilingTimer.current) window.clearTimeout(ceilingTimer.current);
    if (escapeTimer.current) window.clearTimeout(escapeTimer.current);
    if (errorTimer.current) window.clearTimeout(errorTimer.current);
  }, []);

  return { active: Boolean(record), record, handoff, escapable, dismiss };
}
window.useBuildingUniverse = useBuildingUniverse;

// ---- the full-screen overlay ----------------------------------------------------------------
// On-brand (parchment + brass + candleglow), animated (a rotating brass seal + an etched
// "assembling" progress sweep that is HONEST — it loops, it does not claim a percentage), a live
// elapsed readout, and rotating lore flavor. a11y (#406): this is a LOADING STATE, not a dialog —
// it does NOT trap focus, inert the app, or handle Escape, so it must NOT claim role="dialog"
// aria-modal (a false-modal that leaves the covered app in the tab order). We use role="status" +
// aria-busy on the root and a single stable polite announcement; the ticking elapsed + rotating
// headline are visible and in the a11y tree (so a screenshot AND the accessibility snapshot both
// see motion — the #385 frozen-app lesson) but live OUTSIDE the announced region so a screen reader
// isn't spammed per tick. #405: when `escapable`, a real focusable "Enter anyway →" button lets a
// keyboard/screen-reader user leave the cover for the table at any time.
function BuildingUniverse({ record, handoff, escapable, onEnterAnyway }) {
  const start = (record && typeof record.startedAt === "number") ? record.startedAt : Date.now();
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const secs = Math.max(0, Math.floor((now - start) / 1000));
  const mm = Math.floor(secs / 60);
  const ss = String(secs % 60).padStart(2, "0");
  const elapsedLabel = `${mm}:${ss}`;

  // Rotate the headline + subline for the FULL overlay lifetime (#406 item 5). The early pool plays
  // through ~40s; past that, switch to a calmer "still composing — a rich opening is worth the wait"
  // register (BUILDING_FLAVOR_LATE) rather than recycling the bright early lines — so a long
  // cold-open (a blind newbie run saw minutes) keeps copy that tracks the real arc of the wait
  // instead of looping. Both phases still ROTATE so consecutive renders/snapshots differ.
  const lateHeadlines = secs >= 42 ? BUILDING_FLAVOR_LATE : BUILDING_FLAVOR;
  const headline = handoff
    ? "Your story begins…"
    : lateHeadlines[Math.floor(secs / 3.5) % lateHeadlines.length];
  const subline = handoff
    ? "Stepping into the scene the Dungeon Master has set for you."
    : BUILDING_SUBLINE[Math.floor(secs / 9) % BUILDING_SUBLINE.length];

  const eyebrow = handoff
    ? "The world awakens"
    : (record && record.kind === "forge" ? "Binding your hero" : "Building your universe");

  return (
    <div className={`building-universe ${handoff ? "is-handoff" : ""}`} aria-busy={!handoff} aria-label="Building your universe">
      {/* Announced ONCE via a dedicated stable role="status" region — stable text, so the polite
          region does not re-fire every second. The ROOT is NOT role="status" (it wraps the ticking
          elapsed/headline, which would spam the announcement); it is a plain labeled, aria-busy
          container — NOT a role="dialog" aria-modal (#406: no focus trap exists, so claiming modal
          would be a false attribute that leaves the covered app in the tab order). */}
      <span className="visually-hidden" role="status" aria-live="polite">
        {handoff
          ? "Your story is ready. Entering the table."
          : "Building your universe. The Dungeon Master is composing your opening scene; this first moment can take up to a minute."}
      </span>

      <div className="bu-stage">
        {/* The animated brass seal — the centerpiece "the world is being forged" motion. Two
            counter-rotating rings + a breathing core glow. Decorative (aria-hidden); stilled under
            reduced-motion via CSS. */}
        <div className="bu-seal" aria-hidden="true">
          <span className="bu-candleglow" />
          <svg viewBox="0 0 120 120" width="124" height="124">
            <defs>
              <radialGradient id="buCore" cx="50%" cy="50%" r="50%">
                <stop offset="0%" stopColor="var(--gold-glow)" stopOpacity="0.9" />
                <stop offset="60%" stopColor="var(--b-300)" stopOpacity="0.5" />
                <stop offset="100%" stopColor="var(--b-500)" stopOpacity="0" />
              </radialGradient>
            </defs>
            <circle className="bu-ring bu-ring-outer" cx="60" cy="60" r="52"
              fill="none" stroke="var(--b-400)" strokeWidth="1.4"
              strokeDasharray="6 10" opacity="0.85" />
            <circle className="bu-ring bu-ring-inner" cx="60" cy="60" r="40"
              fill="none" stroke="var(--b-300)" strokeWidth="1.1"
              strokeDasharray="2 12" opacity="0.7" />
            <circle cx="60" cy="60" r="30" fill="url(#buCore)" className="bu-core" />
            {/* compass star — the "your tale begins here" mark */}
            <path d="M60 34 L66 60 L60 86 L54 60 Z" fill="var(--crimson)" opacity="0.85" />
            <path d="M34 60 L60 54 L86 60 L60 66 Z" fill="var(--b-500)" opacity="0.75" />
            <circle cx="60" cy="60" r="3.4" fill="var(--gold-glow)" />
          </svg>
        </div>

        <div className="bu-eyebrow">{eyebrow}</div>
        {/* Visible + in the a11y tree (NOT inside the announced region) so the rotating headline +
            elapsed prove life on a screenshot AND in an aria snapshot, without per-tick spam. */}
        <h1 className="bu-headline">{headline}</h1>
        <p className="bu-subline">{subline}</p>

        {/* The honest "sweep" — an indeterminate, looping etched bar. It is explicitly NOT a
            percentage; it conveys "work is ongoing", paired with the real elapsed clock beside it. */}
        <div className="bu-sweep" aria-hidden="true">
          <span className="bu-sweep-fill" />
        </div>
        <div className="bu-meta" aria-hidden="true">
          <span className="bu-dots">
            <span /><span /><span />
          </span>
          <span className="bu-elapsed">{handoff ? "ready" : `composing · ${elapsedLabel}`}</span>
        </div>

        {/* #405: the manual escape. Appears after ~15s (escapable) so an impatient player — or a
            keyboard/screen-reader user — can skip straight to the table at any time. The table is
            fully usable on a cold-open (its action bar is enabled, and its own narrating/stuck
            recovery engages on the first move), so this never strands the player. Hidden during the
            handoff flourish (we're already entering the table). */}
        {escapable && !handoff && (
          <button type="button" className="bu-escape" onClick={onEnterAnyway}>
            Enter anyway →
          </button>
        )}
      </div>
    </div>
  );
}
window.BuildingUniverse = BuildingUniverse;

// Expose the flavor pools for tests / devtools introspection (purely additive — the component
// closes over the consts directly; nothing in the running app reads these off window).
window.BUILDING_FLAVOR = BUILDING_FLAVOR;
window.BUILDING_FLAVOR_LATE = BUILDING_FLAVOR_LATE;
window.BUILDING_SUBLINE = BUILDING_SUBLINE;
