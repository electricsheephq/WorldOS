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
 * A 12-min hard backstop (matching the cold-open's PENDING_BACKSTOP_MS) guarantees the overlay can
 * never wedge forever even if no beat ever comes.
 *
 * The bar of honesty (per the owner): animated "composing your opening…" + rotating lore flavor +
 * a live elapsed readout. No fake progress bar we can't back.
 */

// ---- persistence facade (survives location.assign) ------------------------------------------
// One sessionStorage record describes the in-flight "build". begin() is called SYNCHRONOUSLY at
// the click — before startProviderSession — by both the launcher (startPlay) and the Forge
// (bindHero), so the overlay is up instantly, before the async bridge hop and the reload.
const OW_BUILDING_KEY = "openworlds.building";
// A floor on how long the overlay lingers once "begun", so a fast-failing bridge call (no reload,
// instant reject) still shows the intent for a readable moment rather than a flash — but mostly
// this exists so begin()→immediate-error in a browser preview doesn't blink. The real lifetime is
// governed by the first-narration handoff and the hard backstop below.
const OW_BUILDING_BACKSTOP_MS = 12 * 60 * 1000; // mirrors app.jsx PENDING_BACKSTOP_MS (the cold-open ceiling)

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
    try { window.dispatchEvent(new CustomEvent("clawdnd:building-begin", { detail: record })); } catch (_e) {}
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
// in-table cold-open clears on. Returns { active, record, dismiss }.
function useBuildingUniverse(liveSession) {
  const [record, setRecord] = React.useState(() => window.OpenWorldsBuilding.read());
  // "handoff" is the brief flourish phase after the first beat lands but before we unmount — so the
  // table doesn't pop in with a jarring cut; the player reads "Your story begins…" for a beat.
  const [handoff, setHandoff] = React.useState(false);
  const handoffTimer = React.useRef(null);
  const backstopTimer = React.useRef(null);

  // begin() fired on THIS page (no reload yet — the launcher/forge click) → show immediately.
  React.useEffect(() => {
    const onBegin = (e) => {
      setHandoff(false);
      setRecord((e && e.detail) || window.OpenWorldsBuilding.read());
    };
    window.addEventListener("clawdnd:building-begin", onBegin);
    return () => window.removeEventListener("clawdnd:building-begin", onBegin);
  }, []);

  // Hard backstop: never let the overlay wedge forever. If no first beat arrives within the
  // ceiling, clear the flag and dismiss (the table's own cold-open/stuck handling takes over).
  React.useEffect(() => {
    if (!record || handoff) return undefined;
    const elapsed = Date.now() - (record.startedAt || Date.now());
    const remaining = Math.max(0, OW_BUILDING_BACKSTOP_MS - elapsed);
    backstopTimer.current = window.setTimeout(() => {
      window.OpenWorldsBuilding.clear();
      setRecord(null);
    }, remaining);
    return () => {
      if (backstopTimer.current) { window.clearTimeout(backstopTimer.current); backstopTimer.current = null; }
    };
  }, [record, handoff]);

  // THE HANDOFF. The first DM narration beat = the universe is built. Detect it off the live
  // chat tail (the exact signal the cold-open pending clears on). Run the short flourish, then
  // clear the flag + unmount so App routes to the table (where this beat is already in the log).
  const hasFirstNarration =
    Array.isArray(liveSession && liveSession.chatBeats) &&
    liveSession.chatBeats.some((b) => b && b.kind === "narration");

  React.useEffect(() => {
    if (!record || handoff) return undefined;
    if (!hasFirstNarration) return undefined;
    setHandoff(true);
    handoffTimer.current = window.setTimeout(() => {
      window.OpenWorldsBuilding.clear();
      setRecord(null);
      setHandoff(false);
    }, 1400);
    return () => {
      if (handoffTimer.current) { window.clearTimeout(handoffTimer.current); handoffTimer.current = null; }
    };
  }, [record, handoff, hasFirstNarration]);

  React.useEffect(() => () => {
    if (handoffTimer.current) window.clearTimeout(handoffTimer.current);
    if (backstopTimer.current) window.clearTimeout(backstopTimer.current);
  }, []);

  const dismiss = React.useCallback(() => {
    window.OpenWorldsBuilding.clear();
    setRecord(null);
    setHandoff(false);
  }, []);

  return { active: Boolean(record), record, handoff, dismiss };
}
window.useBuildingUniverse = useBuildingUniverse;

// ---- the full-screen overlay ----------------------------------------------------------------
// On-brand (parchment + brass + candleglow), animated (a rotating brass seal + an etched
// "assembling" progress sweep that is HONEST — it loops, it does not claim a percentage), a live
// elapsed readout, and rotating lore flavor. a11y: a single stable role="status" announces the
// wait ONCE (it never re-fires per tick); the ticking elapsed + rotating headline are visible and
// in the a11y tree (so a screenshot AND the accessibility snapshot both see motion — the #385
// frozen-app lesson) but live OUTSIDE the announced region so a screen reader isn't spammed.
function BuildingUniverse({ record, handoff }) {
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

  // Rotate the headline every ~3.5s and the subline every ~9s so both visibly change over the wait.
  const headline = handoff
    ? "Your story begins…"
    : BUILDING_FLAVOR[Math.floor(secs / 3.5) % BUILDING_FLAVOR.length];
  const subline = handoff
    ? "Stepping into the scene the Dungeon Master has set for you."
    : BUILDING_SUBLINE[Math.floor(secs / 9) % BUILDING_SUBLINE.length];

  const eyebrow = handoff
    ? "The world awakens"
    : (record && record.kind === "forge" ? "Binding your hero" : "Building your universe");

  return (
    <div className={`building-universe ${handoff ? "is-handoff" : ""}`} role="dialog" aria-modal="true" aria-label="Building your universe">
      {/* Announced ONCE — stable text, so the polite region does not re-fire every second. */}
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
      </div>
    </div>
  );
}
window.BuildingUniverse = BuildingUniverse;

// Expose the flavor pools for tests / devtools introspection (purely additive — the component
// closes over the consts directly; nothing in the running app reads these off window).
window.BUILDING_FLAVOR = BUILDING_FLAVOR;
window.BUILDING_SUBLINE = BUILDING_SUBLINE;
