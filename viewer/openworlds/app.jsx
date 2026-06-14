/* App router + tweaks */

// #410: strip the leading write-lane routing tag ("[do] ", "[say] ", "[check] ", …) from a line
// before it is SHOWN in the chronicle. The tag is internal plumbing the engine uses to classify a
// player move; the player must see their own words, never "[do] opens the door". The write lane
// KEEPS the tag (engine routing) — this is display-only. Shared by every player-line render path:
// the optimistic echo (postMove) AND the /chat replay of the player's logged line. Defined as a
// window-guarded global (like neutralizeMarkup) so screen-table + the pytest suite can reach it.
window.stripRoutingTag = window.stripRoutingTag || function stripRoutingTag(text) {
  return String(text == null ? "" : text)
    .replace(/^\s*\[(say|do|check|save|continue|attack|cast|use_item|clarify)\]\s*/i, "");
};

window.playerReplayBeat = window.playerReplayBeat || function playerReplayBeat(text) {
  const raw = String(text == null ? "" : text);
  const match = raw.match(/^\s*\[(say|do|check|save|continue|attack|cast|use_item|clarify)\]\s*/i);
  const route = (match?.[1] || "").toLowerCase();
  const displayText = window.stripRoutingTag(raw).replace(/\s+/g, " ").trim();
  if (!displayText) return null;
  const quickLabels = {
    continue: "Continue",
    "look around": "Look",
  };
  if (route === "say" || !route) return { kind: "dialog", who: "You", text: displayText, route: route || "" };
  return { kind: "action", who: "You", text: quickLabels[displayText.toLowerCase()] || displayText, route };
};

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "palette": "warm",
  "ornaments": true,
  "candle": true
}/*EDITMODE-END*/;

/* Accessibility wiring — applied to <html> the same way data-palette is (below), but kept in
   one place so both App (on mount / reload) and the Settings screen drive the SAME document
   state. Reduced-motion and high-contrast set data-attrs (styled in styles.css); UI scale sets
   a --ui-scale custom property consumed by a `zoom` rule on .window (the layout is all px, so
   font-size scaling would do nothing — zoom actually reflows/resizes it). Persisted to
   localStorage so the choice survives a reload even when the Settings screen isn't mounted. */
const A11Y_KEY = "openworlds.accessibility";
const A11Y_DEFAULTS = { reducedMotion: false, highContrast: false, uiScale: 100 };

window.OpenWorldsA11y = window.OpenWorldsA11y || {
  read() {
    try {
      const raw = window.localStorage.getItem(A11Y_KEY);
      if (!raw) return { ...A11Y_DEFAULTS };
      const parsed = JSON.parse(raw);
      return {
        reducedMotion: Boolean(parsed.reducedMotion),
        highContrast: Boolean(parsed.highContrast),
        uiScale: Number.isFinite(parsed.uiScale) ? parsed.uiScale : A11Y_DEFAULTS.uiScale,
      };
    } catch (_e) {
      return { ...A11Y_DEFAULTS };
    }
  },
  apply(settings) {
    const s = { ...A11Y_DEFAULTS, ...(settings || {}) };
    const root = document.documentElement;
    root.setAttribute("data-reduced-motion", s.reducedMotion ? "on" : "off");
    root.setAttribute("data-contrast", s.highContrast ? "high" : "normal");
    const scale = Math.max(75, Math.min(150, Number(s.uiScale) || 100));
    root.style.setProperty("--ui-scale", String(scale / 100));
    try { window.localStorage.setItem(A11Y_KEY, JSON.stringify(s)); } catch (_e) {}
    return s;
  },
};

window.openWorldsRequestedCampaignFromLocation = window.openWorldsRequestedCampaignFromLocation || function openWorldsRequestedCampaignFromLocation() {
  try {
    const params = new URLSearchParams(window.location.search || "");
    return params.get("campaign") || "";
  } catch (_e) {
    return "";
  }
};

function openWorldsPlayerChronicle(c) {
  return Boolean(c?.canResume || c?.current);
}

function openWorldsCampaignMatches(c, campaignRef) {
  if (!campaignRef) return false;
  return c?.id === campaignRef || c?.campaign_id === campaignRef;
}

const OPENWORLDS_VALID_SCREENS = new Set([
  "launcher", "roster", "table", "combat", "dialogue", "map", "character", "inventory",
  "forge", "relations", "journal", "bestiary", "acts", "merchant", "create",
  "seed", "settings",
]);
const OPENWORLDS_HASH_ALIASES = {
  battle: "combat",
  parley: "dialogue",
  party: "character",
  heroes: "character",
  chronicles: "launcher",
  worlds: "launcher",
  market: "merchant",
  stash: "inventory",
  pick: "roster",
  picker: "roster",
  camp: "map",
  rest: "map",
};
const OPENWORLDS_SCREEN_HASHES = {
  launcher: "worlds",
  roster: "roster",
  table: "table",
  combat: "battle",
  dialogue: "parley",
  map: "map",
  character: "party",
  inventory: "stash",
  forge: "forge",
  relations: "relations",
  journal: "journal",
  bestiary: "bestiary",
  acts: "acts",
  merchant: "market",
  create: "create",
  seed: "seed",
  settings: "settings",
};
function openWorldsRouteFromHash() {
  const raw = (window.location.hash || "").replace(/^#\/?/, "").trim().toLowerCase();
  if (!raw) return null;
  const id = OPENWORLDS_VALID_SCREENS.has(raw) ? raw : (OPENWORLDS_HASH_ALIASES[raw] || null);
  if (!id) return null;
  return { id, campMode: raw === "camp" || raw === "rest" ? true : false };
}
function openWorldsHashForScreen(id, opts) {
  if (id === "map" && opts?.openCamp) return "camp";
  return OPENWORLDS_SCREEN_HASHES[id] || id;
}
function openWorldsSyncHashForScreen(id, opts) {
  const hash = openWorldsHashForScreen(id, opts);
  if (!hash) return;
  const nextHash = `#${hash}`;
  if (window.location.hash === nextHash) return;
  if (opts?.replaceHash && window.history?.replaceState) {
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${nextHash}`);
  } else {
    window.location.hash = nextHash;
  }
}

// #342 + #731: neutralize markup in player free-text BEFORE it is sent to the engine or echoed into
// the chronicle. The #324 v2 adversarial run found that submitting "<script>…</script>", "{{ }}", or
// "<b>…</b>" sent the raw markup straight to the DM (it stalled 35s+) and rode along in the local
// echo. React escapes on *display*, but #342's tag-only strip still leaked the script BODY as text:
// "<script>alert(1)</script>" → "alert(1)" rendered as the player's action — an injection/spoofing
// surface (#731, v1.0.4-rc1 RRI). The fix below excises the BODIES of script-class / embedded-content
// tags (their content is never in-world prose), then strips remaining angle-bracket tags and defangs
// template-style "{{ … }}" / "}}" runs to plain text, collapses whitespace, and caps absurd length —
// keeping ordinary apostrophes, quotes, punctuation, emoji, and benign emphasis prose intact so a
// normal in-character line is untouched. Viewer-side only; the engine stays sole writer.
window.neutralizeMarkup = window.neutralizeMarkup || function neutralizeMarkup(raw) {
  if (typeof raw !== "string") return "";
  let t = raw;
  // #731 (v1.0.4-rc1 adversarial RRI): excise the *bodies* of script-class / embedded-content tags
  // FIRST — before the generic tag strip below. Previously only the tags were removed, so
  // "<script>alert(1)</script>" left "alert(1)" as text, which rode into the chronicle as a player
  // action (an injection/spoofing surface). For these elements the CONTENT is never in-world prose,
  // so drop the whole "<tag …>…</tag>" span — open tag, body, and close tag — leaving nothing.
  // Also catch a self-closed/orphaned open tag of the same class. Case-insensitive; the `[\s\S]`
  // body match spans newlines. Ordinary emphasis tags (<b>/<i>/…) are NOT in this list — their text
  // is legitimate prose and is preserved by the generic tag strip that follows.
  t = t.replace(
    /<(script|style|iframe|object|embed|svg|math|template|noscript|xml|applet|frame|frameset)\b[\s\S]*?<\/\1\s*>/gi,
    " ",
  );
  t = t.replace(
    /<\/?(script|style|iframe|object|embed|svg|math|template|noscript|xml|applet|frame|frameset)\b[^>]*>/gi,
    " ",
  );
  // Drop anything that looks like an HTML/XML tag (incl. <b>…</b> bodies are kept as text once their
  // tags are removed — that is legitimate emphasis prose). Do it twice so "<<b>>" style nesting can't
  // leave a stray bracket.
  t = t.replace(/<\/?[a-zA-Z][^>]*>/g, " ").replace(/<\/?[a-zA-Z][^>]*>/g, " ");
  // Defang stray angle brackets that weren't part of a full tag.
  t = t.replace(/[<>]/g, " ");
  // Defang template/handlebars-style delimiters so they can't be interpreted downstream.
  t = t.replace(/\{\{+/g, "(").replace(/\}\}+/g, ")");
  // Collapse whitespace runs (a pasted wall of newlines/tabs shouldn't reach the DM as-is).
  t = t.replace(/\s+/g, " ").trim();
  // Hard cap — a 1000+ char dump is an attack-class input, not a turn.
  if (t.length > 2000) t = t.slice(0, 2000).trim();
  return t;
};

// #340 + #342: the live-session "in-flight turn" state — the /chat tail, its cursor, the
// accumulated DM/player beats, the local player echo, AND the "DM is narrating…" pending
// indicator — lifted from ScreenTable to the App so it SURVIVES screen navigation. Previously
// all of this was local to ScreenTable, so navigating away (Table→Map→Party) unmounted it: the
// in-flight DM beat that landed while away was never ingested (a silent "story hole", #340) and
// the pending indicator reset to null on return (the bar re-opened as if the turn had finished).
// Owning it at the app level means the /chat poll keeps running regardless of which screen is
// mounted, the beat always lands in the log, and the narrating state clears correctly on the
// turn that actually resolved it — no matter where the player wandered.
//
// The poll is best-effort and a no-op unless a LIVE campaign is bound (mirrors the server's
// /chat gating: empty items when no chat is configured / the view isn't the live run).
// #348: the recovery 'stuck' timeout is ADAPTIVE by turn position, because some DM paths can still
// land the main beat all-at-once (the /chat tail carries no streaming/partial signal; it appends one
// {"role":"dm",...} line when the turn's `result` is in). #393 added a live /events tail, and the
// Codex DM wrapper now writes an immediate wrapper-authored engine progress row, then asks the
// provider to write one short engine-owned progress narration through log_event early in the turn.
// The timer remains the backstop for providers or turns that do not produce mid-turn events. So a
// wall-clock from submit is still the operative fallback, and at 90s it PRE-EMPTED both the legit
// Act-opening (#348) AND a content-rich beat 2–4 that legitimately ran 90–120s (#399 — the
// playtester's give-up).
//   • FIRST beat of a session (the cold-open / Act-opening) gets a generous window — the engine
//     is building the world + setting the scene; a blind newbie run saw this take 5–8 min.
//   • LATER beats: #399 raises the window 90s → 180s to cover the worst-case ~120s turn with
//     margin while still recovering a genuine mid-session stall within ~3 min.
// The 12-min hard backstop is UNCHANGED — a turn that blows even the first-beat window still
// gets force-cleared. "first beat?" = no DM narration has arrived this session yet (the hook's
// dmBeatCountRef, reset to 0 on every run change).
// #399: later-beat stall window raised 90s → 180s. Some provider paths still batch the full turn at
// turn-end, and even live-progress providers can hit a turn where no early narration is available.
// With no mid-turn reset to lean on, the 90s window (tuned for the ~35–60s norm) pre-empted a
// content-rich beat 2–4 that legitimately ran 90–120s → a false 'stuck' on a working turn (the
// give-up the playtester filed). 180s covers the worst-case ~120s turn with margin while still
// recovering a GENUINE mid-session stall within ~3 min. The adaptive reset below is still useful:
// when /events progress arrives, it keeps a healthy turn alive without re-enabling actions early.
const PENDING_RECOVERY_MS = 180 * 1000;           // #399: later-beat stall window (worst-case DM turns run ~90–120s; was 90s/#342).
const PENDING_RECOVERY_FIRST_MS = 4 * 60 * 1000;  // #348: first-beat (Act-opening) window — fits the multi-minute cold open.
const PENDING_BACKSTOP_MS = 12 * 60 * 1000;       // …with the original hard backstop as a final net.
// #745 (the newbie mid-stream-stall give-up): a HARD stuck ceiling from submit that flags `stuck` (the
// recoverable "Try again" affordance) and — unlike the per-progress recovery timer — is NOT reset by
// streamed progress. Root cause it fixes: notePendingProgress re-arms the FULL recovery window (180s/240s)
// AND clears `stuck` on EVERY streamed /events paragraph. So a beat that streams several partial paragraphs
// ("You give the sergeant your own name… The charcoal touches the paper… That's the arithmetic o—") and
// then FREEZES mid-generation keeps pushing the stuck deadline forward with each partial; with partials
// <window apart, `stuck` never fires and recovery is deferred to the 12-min PENDING_BACKSTOP_MS — which
// CLEARS pending to null (a plain re-enabled bar, NO "Try again", the partial narration stranded). That is
// the ~12–15-min lockout the newbie gave up on. This ceiling bounds TOTAL stall from submit regardless of
// how many partials trickle in (progress does not reset it), and resolves to the SAME recoverable `stuck`
// state (the bar re-opens as "Try again"). It must be generous enough to clear a worst-case HEALTHY beat
// (which RESOLVES on /chat → clearPending cancels every timer long before this fires), so it never
// false-positives on a slow-but-alive turn; it only ever fires on a genuine freeze. Strictly between the
// position windows and the 12-min null-backstop, so the ordering is: position recovery (resettable) <
// stuck-backstop (hard, recoverable) < null-backstop (hard, last-resort clear).
// #746: the ceiling is BUDGET-AWARE by turn position, because the original flat 5-min value sat BELOW the
// system's own healthy turn budgets and false-fired `stuck` on healthy slow turns: the cold open measures
// ~300s with a 400–500s deadline (qa/lib_beat_driver.sh clawdnd_dm_timeout — 500s for Opus), and a healthy
// CONTINUING beat can legitimately run ~400s (scripts/play.sh CLAWDND_BEAT_TIMEOUT=200s + ONE retry). When
// the flat ceiling fired mid-flight on a working turn, pendingActive flipped false (the action bar
// re-opened, screen-table.jsx), the "DM seems stuck" toast fired, and retryStuck re-POSTed the move — so
// the SAME intent resolved TWICE once the in-flight beat landed. The fix: firstBeat (the cold open) ⇒
// 9 min (≥ the 500s cold-open budget); later beats ⇒ 7 min (≥ the ~400s timeout+retry budget). Both stay
// strictly under the 12-min null-backstop, preserving the #745 ordering above — a genuine
// trickle-then-freeze still surfaces the recoverable `stuck` affordance well before the silent null clear.
const PENDING_STUCK_BACKSTOP_MS = 7 * 60 * 1000;        // #745/#746: later-beat hard stuck ceiling from submit (progress does NOT reset it).
const PENDING_STUCK_BACKSTOP_FIRST_MS = 9 * 60 * 1000;  // #746: first-beat (cold-open) hard stuck ceiling — clears the 400–500s cold-open budget.
// #648: a JUST-armed narrating turn is protected from a SPURIOUS same-tick clear (the immediate
// post-armPending surface poll, a /chat cursor-reset re-reading the prior resolved turn's line as a
// fresh resolution, or a transient campaignId flip tripping the per-run reset) for this long — so the
// spinner can't be wiped milliseconds after submit, stranding the player with an enabled bar + no DM
// feedback for the whole ~150s beat (the #648 report). Far below a real DM beat (~100–150s) → a
// genuine resolution is never swallowed; above the 4s poll cycle → the one-shot post-arm clear can't
// beat it. Once /events prose streams (`streaming`) the guard lifts, and the 12-min backstop bypasses
// it entirely (it calls setPendingState(null) directly).
const PENDING_ARM_GRACE_MS = 10 * 1000;
// #348: the single source of truth for the recovery window, by turn position. Pure + exported
// (window.__PENDING_TIMING__ below) so the timing contract is unit-testable without reaching into
// the hook's internal beat counter. firstBeat ⇒ the longer cold-open window; else the snappy one.
function recoveryWindowMs(firstBeat) {
  return firstBeat ? PENDING_RECOVERY_FIRST_MS : PENDING_RECOVERY_MS;
}
// #746: the hard stuck-backstop ceiling by turn position, mirroring recoveryWindowMs — the single
// source of truth armPending arms. Pure + exported (window.stuckBackstopMs below) so the
// budget-aware ceiling contract is unit-testable without reaching into the hook's internals.
function stuckBackstopMs(firstBeat) {
  return firstBeat ? PENDING_STUCK_BACKSTOP_FIRST_MS : PENDING_STUCK_BACKSTOP_MS;
}

// #402: BOUND the live tail. `chatBeats` (every streamed/turn-end DM narration + dialogue beat) and
// `log` (every optimistic player echo) accumulated for the WHOLE session with no cap — so a long
// playtest grew them without limit. The chronicle rendered ALL of it (screen-table visibleLog.map),
// so the DOM + the accessibility tree grew unbounded: after a few beats an a11y reader (and a real
// screen reader) truncated BEFORE reaching the newest narration, and the latest beat + action box
// were buried under an ever-taller scroll region — the player couldn't see the DM's reply and the
// run stalled. We keep only the most-recent MAX_LIVE_* entries in each array; older prose still
// lives in the server's recentEvents history band (screen-table's leading window), so nothing is
// truly lost — the live tail just stops growing. The caps are generous so a single multi-paragraph
// DM turn (several /events beats in one turn) is NEVER clipped mid-beat. Pure + exported for tests.
const MAX_LIVE_BEATS = 60;   // DM narration/dialogue beats kept in the live tail (≫ one turn's paragraphs).
const MAX_LIVE_ECHOES = 40;  // optimistic player-action echoes kept in the live tail.
// Trim an append-only array to its last `max` entries WITHOUT copying when already within bound
// (so a steady-state turn doesn't reallocate the array every poll). Returns the same ref when no
// trim is needed — React's setState bails out on an identical ref, avoiding a needless re-render.
function boundTail(arr, max) {
  return (Array.isArray(arr) && arr.length > max) ? arr.slice(arr.length - max) : arr;
}

// #274: a monotonic, client-side sequence stamped on every chronicle entry created here (player
// echoes + each ingested chat beat) as `.at`. The session log in screen-table.jsx concatenates
// three sources (recentEvents → chatBeats → log); because the player's optimistic echo (`log`) and
// the DM's narration (`chatBeats`) live in SEPARATE arrays, a plain concat put a just-typed action
// ABOVE the older DM prose it was responding to. A shared ever-increasing counter records true
// creation order across BOTH arrays, so a stable sort by `.at` interleaves them correctly. It is a
// counter, not a wall-clock — two entries can never tie, and it survives across runs harmlessly
// (it only needs to be monotonic, not absolute). recentEvents (server history, no `.at`) stay the
// oldest band; that ordering is handled where visibleLog is assembled.
let __logSeq = 0;
function nextLogSeq() { __logSeq += 1; return __logSeq; }

function useLiveSession(state) {
  const campaigns = Array.isArray(state?.campaigns) ? state.campaigns : [];
  const activeCampaign =
    campaigns.find((c) => openWorldsCampaignMatches(c, state?.activeCampaign)) ||
    campaigns[0] ||
    {};
  const campaignId = activeCampaign.campaign_id || state?.activeCampaign || activeCampaign.id || "";
  const source = activeCampaign.source || "";
  const runId = activeCampaign.runId || "";

  const [chatBeats, setChatBeats] = React.useState([]);
  const [log, setLog] = React.useState([]);           // local optimistic player echoes
  const [pending, setPending] = React.useState(null);  // { text, since, stuck?, firstBeat?, streaming? } | null
  // `streaming` (set in notePendingProgress) means live /events prose has begun arriving for THIS
  // in-flight turn — the narrating affordance uses it to confirm the scene is being written above,
  // instead of showing the generic "weaving the next beat" wait. armPending starts a fresh pending
  // object without it, so each new turn re-derives `streaming` from its own /events arrivals.
  const chatCursor = React.useRef(0);
  const eventsCursor = React.useRef(0);                // #393: per-file cursor for the live /events tail
  const dmBeatCountRef = React.useRef(0);
  // #406: the count of turns that have RESOLVED on /chat (the turn-END signal), bumped ONLY in the
  // /chat poll — NOT by streamed /events paragraphs. `firstBeat` (the generous cold-open recovery
  // window) keys off THIS, not dmBeatCountRef: the cold-open is still "the first beat" until its
  // turn actually resolves, so a player who hits "Try again" after one paragraph streamed keeps the
  // 4-min window instead of being dropped to the 180s later-beat window (the #348 false-stuck trap
  // re-introduced for the still-opening cold open). dmBeatCountRef stays the per-paragraph counter
  // (it gates streaming dedup/order), but it no longer decides the recovery window.
  const resolvedTurnsRef = React.useRef(0);
  // #393/#405: dedup key sets across BOTH narration sources. There are TWO key spaces and the
  // distinction is the whole fix:
  //   • `seenSeq` — the STABLE session-log line index (`seq`) the server now stamps on every /events
  //     entry (and on the recentEvents history band). This is the engine's sole-writer per-beat
  //     identity; it does NOT depend on the prose, so a re-ingest (windowing / a session-rotation
  //     cursor rewind) of the same line collapses to one row, and it is the chronological sort key.
  //   • `seenText` — a normalized-TEXT fallback, used ONLY for narration that has NO seq (a /chat-only
  //     beat: a terse turn that streamed nothing via /events, or the human/native path where /chat is
  //     the sole source). Text keys are fragile (a reworded reply, or a whole-turn /chat blob vs the
  //     per-paragraph /events rows, hash differently) — which is exactly why /chat narration is now a
  //     FALLBACK only and the canonical live source is the seq-keyed /events stream.
  // #405 root-cause: previously a single text-keyed set reconciled both sources, so the DM rewording
  // its turn-END reply — or /chat carrying the whole beat as one blob while /events carried N
  // paragraphs — defeated the dedup and the chronicle showed each beat 3-4× and out of order.
  const seenSeq = React.useRef(new Set());
  // #406: seenText is RESET per turn (when a /chat DM line resolves a turn, below) — it only needs to
  // span one turn's /events→/chat gap, so a run-long text set is wrong: it would permanently suppress
  // a legitimately-repeated short line (a catchphrase, a repeated "Yes.") on a /chat-only path. seenSeq
  // stays run-long (stable ids never collide and must absorb re-ingests).
  const seenText = React.useRef(new Set());
  // #405: did the CURRENT in-flight turn stream any narration via the canonical /events source? When
  // true, the turn-END /chat DM line is a pure turn-RESOLUTION signal (it clears the pending
  // indicator) and adds NO narration row — the session log already carried that beat's canonical,
  // seq-keyed, per-paragraph prose. When the turn streamed NOTHING via /events (a terse turn that
  // logged no narration, or the human/native path where /chat is the sole source) the /chat copy is
  // the only source and IS rendered (text-keyed). This is reset each time a /chat DM line resolves a
  // turn, so the decision is per-TURN, not per-run: a streamed turn 1 followed by a terse turn 2 still
  // shows turn 2's /chat-only prose. The /events poll always lands a turn's paragraphs before its
  // turn-END /chat blob (prose is logged DURING the turn; /chat is written only at turn-end, and
  // /events polls faster), so this flag is reliably set by the time the resolving /chat line arrives.
  const eventsStreamedThisTurnRef = React.useRef(false);
  const recoveryTimer = React.useRef(null);
  const backstopTimer = React.useRef(null);
  const stuckBackstopTimer = React.useRef(null);  // #745: hard stuck ceiling from submit (not reset by progress)

  // sanitizeNarration lives in screen-table.jsx (loaded first); fall back to identity if absent.
  const sanitize = (txt) => (typeof window.sanitizeNarration === "function" ? window.sanitizeNarration(txt) : (txt || ""));
  // #405/BUG2: claim a narration beat by its STABLE, SESSION-SCOPED key — the composite
  // `${sid}:${seq}` (the server-stamped session id + the absolute session-log line index). First-seen
  // returns true (show it + record the id); any later arrival of the same line — a windowing re-mount,
  // a session-rotation cursor rewind, or the recentEvents history band overlapping the live tail —
  // returns false and is suppressed. Keyed by id, so it is immune to the DM rewording the prose
  // between its streamed copy and its reply.
  // BUG2 root cause: the bare `seq` is only a PER-SESSION-LOG line index — it carries NO session
  // scope. When the engine ROTATES the session log (cold-open start_session + the DM-turn-retry
  // re-mint, 5e71f77) the new session's narration restarts at seq 0,1,2 — the SAME values the
  // cold-open already claimed — so the post-move reply was wrongly suppressed here (and dropped by
  // buildChronicleLog's matching seq set). Composing the session id makes the key globally unique
  // across rotations, while preserving within-session monotonicity for the order tiebreak.
  const claimNarrationSeq = React.useCallback((key) => {
    if (typeof key !== "string" || !key) return false;
    if (seenSeq.current.has(key)) return false;
    seenSeq.current.add(key);
    return true;
  }, []);
  // #393/#405: the TEXT-key fallback for narration with no seq (a /chat-only beat). Whitespace-
  // collapsed + lowercased. First-seen returns true; a repeat returns false. Used only when no seq is
  // available — seq-keyed beats never consult this set, so a reworded /chat copy can't double a beat
  // that already streamed (that path is gated by eventsStreamedThisTurnRef, below).
  const claimNarration = React.useCallback((txt) => {
    const key = String(txt || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!key) return false;
    if (seenText.current.has(key)) return false;
    seenText.current.add(key);
    return true;
  }, []);

  // Clear ONLY the adaptive 'stuck' recovery timer (the one notePendingProgress re-arms each
  // streamed beat). Kept separate from the absolute backstop so a streaming turn can reset 'stuck'
  // WITHOUT pushing the hard 12-min cap forward (see notePendingProgress / #406).
  const clearRecoveryTimer = React.useCallback(() => {
    if (recoveryTimer.current) { window.clearTimeout(recoveryTimer.current); recoveryTimer.current = null; }
  }, []);
  const clearTimers = React.useCallback(() => {
    clearRecoveryTimer();
    if (backstopTimer.current) { window.clearTimeout(backstopTimer.current); backstopTimer.current = null; }
    // #745: the hard stuck-backstop is disarmed alongside the others — a real resolution (clearPending →
    // clearTimers) or a retry re-arm (armPending → clearTimers) must cancel it so it can't fire on a turn
    // that already resolved/re-armed.
    if (stuckBackstopTimer.current) { window.clearTimeout(stuckBackstopTimer.current); stuckBackstopTimer.current = null; }
  }, [clearRecoveryTimer]);

  // #393: a ref mirror of `pending` so a poll callback (whose effect deps deliberately EXCLUDE
  // `pending`, to avoid re-subscribing the 3s interval every turn) can read the CURRENT turn state
  // without a stale closure. `setPendingState` is the single writer that keeps the ref in lockstep
  // with state — every pending change (arm / clear / stuck-flag / progress) goes through it.
  const pendingRef = React.useRef(null);
  const setPendingState = React.useCallback((next) => {
    setPending((p) => {
      const v = (typeof next === "function") ? next(p) : next;
      pendingRef.current = v;
      return v;
    });
  }, []);

  const clearPending = React.useCallback(() => {
    // #648: keep a FRESHLY-armed, not-yet-streaming turn alive through a spurious same-tick clear
    // (see PENDING_ARM_GRACE_MS). The real "Try again" retry path re-arms via armPending (which calls
    // clearTimers itself), NOT clearPending, so this never blocks a legitimate re-arm; it only stops a
    // poll/reset/flip from wiping the narrating spinner before the DM's first line could land. A real
    // resolution arrives long past the grace (or after /events streamed), so it still clears normally.
    const p = pendingRef.current;
    if (p && !p.streaming && typeof p.since === "number"
        && (Date.now() - p.since) < PENDING_ARM_GRACE_MS) {
      return;
    }
    clearTimers();
    setPendingState(null);
  }, [clearTimers, setPendingState]);

  // #826: authoritatively ROLL BACK the optimistic in-flight arm when the /move POST itself is
  // REJECTED by the server (the move never started, so there is no DM turn to wait for). postMove now
  // arms the narrating gate the INSTANT the player commits — BEFORE the network round-trip — so the
  // one-move-at-a-time gate SURVIVES a navigation during the in-flight window (the App-level pending
  // state outlives ScreenTable's unmount, where a re-mounted submittingRef would otherwise re-open
  // the bar and let a second move double-fire the lane = the #826 state corruption). When that POST
  // comes back an ERROR, the gate must clear NOW — but a plain clearPending would be SWALLOWED by the
  // #648 arm-grace (it deliberately ignores a spurious clear inside the first PENDING_ARM_GRACE_MS).
  // This is NOT spurious: it is the server's authoritative rejection, so it bypasses the grace. It is
  // surgical — it only clears the move WE optimistically armed (text match against the still-pending,
  // not-yet-streaming turn) so it can never clobber a newer live turn (e.g. a fast retry).
  const abandonPending = React.useCallback((text) => {
    const p = pendingRef.current;
    if (!p || p.streaming) return;                          // a streaming turn is real — never abandon it
    const want = String(text == null ? "" : text);
    if (want && String(p.text == null ? "" : p.text) !== want) return;  // not the move we armed — no-op
    clearTimers();
    setPendingState(null);
  }, [clearTimers, setPendingState]);

  // #342 + #348: arm the narrating indicator + a recovery timeout. If a DM beat doesn't arrive within
  // the recovery window the turn is flagged `stuck` (the bar re-enables with a "try again" hint)
  // instead of staying frozen until the 12-minute backstop. A real beat (below) clears it outright.
  // #348: the window is turn-position-aware — the FIRST beat of a session (the cold-open, which the
  // engine spends minutes building) gets PENDING_RECOVERY_FIRST_MS so a slow-but-valid opening is no
  // longer falsely declared stuck; later beats keep the snappy PENDING_RECOVERY_MS. `firstBeat` is
  // recorded on the pending object so the narrating affordance can set an honest expectation, and so
  // a beat that lands AFTER 'stuck' still renders (the #340 path is unchanged — clearPending on any
  // real narration beat regardless of this flag).
  const armPending = React.useCallback((text) => {
    clearTimers();
    // #406: "first beat?" = no turn has RESOLVED on /chat yet (resolvedTurnsRef), NOT "no paragraph
    // has streamed" (dmBeatCountRef). So a retried cold-open — one paragraph streamed, then "Try
    // again" before the turn resolved — still gets the generous PENDING_RECOVERY_FIRST_MS window.
    const firstBeat = resolvedTurnsRef.current === 0;
    const recoveryMs = recoveryWindowMs(firstBeat);
    setPendingState({ text, since: Date.now(), stuck: false, firstBeat });
    recoveryTimer.current = window.setTimeout(() => {
      setPendingState((p) => (p ? { ...p, stuck: true } : p));
    }, recoveryMs);
    // #745: a HARD stuck ceiling from submit. Unlike recoveryTimer (re-armed by every streamed paragraph
    // in notePendingProgress), this is armed ONCE here and progress does NOT reset it — so a beat that
    // streams a partial trickle and then FREEZES mid-stream still surfaces the recoverable `stuck` "Try
    // again" affordance within a bounded time, instead of the trickle deferring recovery to the 12-min
    // null-backstop (which strands the partial narration behind a plain re-enabled bar). It is generous
    // enough that a healthy turn always RESOLVES (clearPending → clearTimers) first, so it never trips a
    // slow-but-alive beat. Fires only when nothing has resolved by its deadline.
    // #746: the ceiling is budget-aware by turn position (stuckBackstopMs): the cold open gets 9 min
    // (its healthy budget is 400–500s), later beats 7 min (a healthy timeout+retry beat runs ~400s) —
    // a flat 5 min sat INSIDE those budgets and false-fired on healthy slow turns (bar re-opened +
    // "DM seems stuck" toast + retryStuck double-resolution while the beat was still in flight).
    stuckBackstopTimer.current = window.setTimeout(() => {
      setPendingState((p) => (p ? { ...p, stuck: true } : p));
    }, stuckBackstopMs(firstBeat));
    backstopTimer.current = window.setTimeout(() => setPendingState(null), PENDING_BACKSTOP_MS);
  }, [clearTimers, setPendingState]);

  // #393: a turn that is STREAMING prose mid-flight (via the /events live tail) is demonstrably
  // alive — so reset the stall/backstop clocks on each streamed beat instead of letting a long-but-
  // healthy turn drift toward a false 'stuck'. This deliberately does NOT clear pending: the action
  // bar stays gated (one move at a time) and the honest "the DM is narrating" indicator stays up
  // WHILE the scene visibly builds above it — the turn only resolves (clearPending) when its final
  // text lands on /chat. No-op when no turn is pending (a streamed beat with the bar already idle).
  const notePendingProgress = React.useCallback(() => {
    // Read the live turn via the ref (the poll's closure can't see the latest `pending`). Side-
    // effects (timer re-arm) live OUTSIDE any state updater so they don't double-fire under React
    // StrictMode's double-invoked updaters.
    const p = pendingRef.current;
    if (!p) return;
    // #406: re-arm ONLY the adaptive 'stuck' recovery timer — NOT the absolute backstop. The
    // backstop is a hard wall-clock cap from submit (armed once in armPending); re-arming it on
    // every streamed beat let a turn that streams a paragraph every few seconds but never resolves
    // on /chat defer the 12-min cap FOREVER (so neither 'stuck' nor the backstop ever fired). Now a
    // long-but-healthy streaming turn keeps resetting 'stuck' (it's plainly alive) while the
    // absolute cap still fires at its original deadline.
    clearRecoveryTimer();
    // The per-progress recovery timer re-arms to the FULL position window on each streamed paragraph —
    // a long-but-healthy streaming turn (prose landing every few seconds) is plainly alive, so it must
    // NOT be falsely flagged stuck. The mid-stream-FREEZE case (a trickle that pushes this window forward
    // forever) is caught instead by the #745 stuck-backstop armed ONCE in armPending — a hard ceiling
    // from submit that progress does NOT reset (so it can't be deferred by a trickle). See armPending.
    const recoveryMs = recoveryWindowMs(Boolean(p.firstBeat));
    recoveryTimer.current = window.setTimeout(() => {
      setPendingState((q) => (q ? { ...q, stuck: true } : q));
    }, recoveryMs);
    // #G3-UX: fresh prose just streamed via /events for THIS in-flight turn → mark the pending turn
    // as `streaming`. The narrating affordance reads this to flip its copy from the generic "weaving
    // the next beat" wait to "the scene is arriving above" — so the spinner is no longer disconnected
    // from the live narration tail filling in right above it (the player WATCHES the beat being
    // written instead of staring at a static spinner). Clear any prior 'stuck' flag in the same
    // update — fresh prose just arrived, so the turn is plainly not stuck. Folding both into one
    // updater keeps `streaming`/`stuck` mutually consistent and avoids a second state churn.
    setPendingState((q) => (q ? (q.streaming && !q.stuck ? q : { ...q, streaming: true, stuck: false }) : q));
  }, [clearRecoveryTimer, setPendingState]);

  // #399: idempotent player echo for STUCK retries only. The #344 'Try again' recovery re-POSTs
  // the EXACT stalled move (postMove → recordPlayerEcho again), which used to append a SECOND
  // identical action row. Suppress that duplicate only while the pending turn is explicitly stuck:
  // a player may intentionally repeat the same words/action on a later normal turn, and the
  // Chronicle must show that second choice.
  const recordPlayerEcho = React.useCallback((who, text, move) => {
    setLog((l) => {
      const route = String(move?.kind || "").trim().toLowerCase();
      const echoText = route === "say"
        ? window.stripRoutingTag(move?.text || text).replace(/^\s*say\s*:\s*/i, "").replace(/\s+/g, " ").trim()
        : text;
      const entryKind = route === "say" ? "dialog" : "action";
      const last = l[l.length - 1];
      const retryingStuckTurn = Boolean(pendingRef.current && pendingRef.current.stuck);
      if (retryingStuckTurn && last && last.kind === entryKind && last.who === who
          && String(last.text || "").trim() === String(echoText || "").trim()
          && String(last.route || "") === route) {
        return l;  // identical to the row already showing (a Try-again re-POST) — no duplicate.
      }
      // #402: bound the echo tail so a long session doesn't grow `log` (and the rendered DOM /
      // a11y tree) without limit. Keep the most-recent MAX_LIVE_ECHOES.
      return boundTail([...l, { kind: entryKind, who, text: echoText, route, at: nextLogSeq(), eventAt: Date.now() / 1000 }], MAX_LIVE_ECHOES);  // #274: creation-order stamp
    });
  }, []);

  React.useEffect(() => clearTimers, [clearTimers]);

  // When the bound live campaign changes, reset the tail so we don't bleed one run's beats into
  // another (the cursor is per-file; a new run starts at 0).
  React.useEffect(() => {
    chatCursor.current = 0;
    eventsCursor.current = 0;          // #393: reset the live /events tail per run
    dmBeatCountRef.current = 0;
    resolvedTurnsRef.current = 0;       // #406: a fresh run has resolved no turns yet (cold-open window)
    seenSeq.current = new Set();        // #405: a fresh run shares no seq dedup keys with the last
    seenText.current = new Set();       // #405: …nor any text-key fallback keys
    eventsStreamedThisTurnRef.current = false;  // #405: no /events narration streamed for any turn yet
    setChatBeats([]);
    setLog([]);
    clearPending();
  }, [campaignId, source, runId, clearPending]);

  // The app-level /chat poll. Visibility-aware (pauses when the tab is hidden) and best-effort.
  React.useEffect(() => {
    if (!campaignId) return undefined;
    let cancelled = false;
    let timer = null;
    const pollOnce = async () => {
      if (cancelled) return;
      try {
        const params = new URLSearchParams();
        params.set("campaign", campaignId);
        if (source) params.set("source", source);
        if (runId) params.set("run", runId);
        params.set("since", String(chatCursor.current));
        const resp = await fetch(`/chat?${params.toString()}`, { cache: "no-store" });
        if (!resp.ok) return;
        const payload = await resp.json();
        const items = Array.isArray(payload.items) ? payload.items : [];
        if (!cancelled && items.length) {
          // #393: a DM line on /chat is the turn-RESOLUTION signal regardless of whether its prose
          // is novel — when the whole beat already streamed live via /events, claimNarration dedups
          // EVERY paragraph (beats below is empty), but the turn has still ended and the indicator
          // must clear. So track "a dm-role item arrived" separately from "novel beats to render".
          let dmLineArrived = false;
          const beats = items
            .map((it) => {
              // #274: stamp each beat with the shared monotonic counter at ingest time so it
              // time-merges correctly against local player echoes (which share the same counter).
              // #410/#548: the engine logs the player's line WITH its routing tag ("[do] …") for
              // move classification, and /chat replays it verbatim. Parse the tag for DISPLAY so
              // resumed/reloaded sessions keep actions as action rows while speech stays dialogue.
              if (it.role === "player") {
                const beat = window.playerReplayBeat(it.text);
                return beat ? { ...beat, at: nextLogSeq(), eventAt: it.at } : null;
              }
              dmLineArrived = true;
              // #405: a /chat DM line is the turn-RESOLUTION signal (it clears the pending indicator
              // below). It is NOT a second narration row when this run is streaming its prose via the
              // canonical seq-keyed /events source: the /chat reply is the SAME beat, but as the whole
              // turn's reply text — often a single blob, and sometimes REWORDED — so rendering it would
              // duplicate (and mis-order) what already streamed per-paragraph. The session log is the
              // canonical chronicle; the /chat copy is suppressed here. We ONLY render a /chat DM line
              // as narration when nothing streamed via /events for this run (a terse turn that logged
              // no narration, or the human/native path where /chat is the sole source) — text-keyed,
              // since a chat-only beat has no session-log seq, and there is no /events stream to
              // collide with in that case.
              if (it.engine_logged === true) return null;
              if (eventsStreamedThisTurnRef.current) return null;
              const clean = sanitize(it.text);
              return clean && claimNarration(clean) ? { kind: "narration", text: clean, at: nextLogSeq(), eventAt: it.at } : null;
            })
            .filter(Boolean);
          if (beats.length) setChatBeats((prev) => boundTail([...prev, ...beats], MAX_LIVE_BEATS));  // #402: cap the live tail
          // The arrival of the DM's turn-END line means the turn RESOLVED → clear the narrating
          // indicator + its timers. This fires even when the prose was wholly deduped (a turn whose
          // entire beat streamed live via /events), so a fully-streamed turn still re-opens the bar.
          // Player echoes alone never resolve a turn.
          if (dmLineArrived) {
            dmBeatCountRef.current += beats.filter((b) => b.kind === "narration").length;
            // #406: a /chat DM line is the turn-RESOLUTION signal — count it so the NEXT turn is no
            // longer treated as the cold-open 'firstBeat'. This is the ONLY place the count bumps
            // (the /events stream bumps dmBeatCountRef, not this), so a retried cold-open whose first
            // turn never resolved keeps its generous recovery window.
            resolvedTurnsRef.current += 1;
            clearPending();
            // #405: the turn is over → reset the per-turn "/events streamed" flag so the NEXT turn is
            // judged on ITS OWN streaming. Without this, a streamed turn would wrongly suppress a
            // later TERSE turn's /chat-only prose (the flag would stay stuck true for the whole run).
            eventsStreamedThisTurnRef.current = false;
            // #406: scope the TEXT-key dedup to the turn (the seq-keyed /events path is the canonical,
            // run-long dedup; #407). seenText only needs to span ONE turn's /events→/chat gap, so a
            // run-long text set would PERMANENTLY suppress a legitimately-repeated short line on a
            // /chat-only path (an NPC catchphrase, a repeated "Yes." / "The door is locked." on a
            // later turn) — the turn resolves but shows no new prose ("the DM said nothing"). Reset
            // it once the turn resolves so the next turn's identical line renders. (seenSeq is NOT
            // reset — stable ids never collide, and it must stay run-long to absorb re-ingests.)
            seenText.current = new Set();
          }
        }
        if (!cancelled && typeof payload.next === "number") chatCursor.current = payload.next;
      } catch (_e) { /* chat tail is non-critical; keep last good */ }
    };
    const stop = () => { if (timer !== null) { window.clearInterval(timer); timer = null; } };
    const start = () => { if (timer === null) timer = window.setInterval(pollOnce, 4000); };
    const onVisibility = () => {
      if (document.visibilityState === "visible") { pollOnce(); start(); } else { stop(); }
    };
    document.addEventListener("visibilitychange", onVisibility);
    onVisibility();
    return () => { cancelled = true; stop(); document.removeEventListener("visibilitychange", onVisibility); };
  }, [campaignId, source, runId, clearPending, claimNarration]);

  // #393: the LIVE narration stream — the fix for the "blank 90s wait" give-up.
  // The DM logs each narration/dialogue beat via the engine's log_event DURING its turn, and the
  // engine appends it to campaigns/<id>/sessions/<sid>.jsonl IMMEDIATELY (store.append_log). The
  // viewer's /events endpoint tails exactly that log with a line cursor. So polling /events here
  // surfaces the scene as it is being WRITTEN — a 60-90s blank wait becomes 60-90s of prose
  // appearing — WITHOUT changing the resolver's blocking turn or the engine's sole-writer semantics
  // (this is a pure read of state the engine already wrote). The turn-END /chat line carries the
  // same prose; claimNarration dedups it so each paragraph shows exactly once, from whichever source
  // reached the player first (live, in practice). Visibility-aware + best-effort, mirroring /chat.
  React.useEffect(() => {
    if (!campaignId) return undefined;
    let cancelled = false;
    let timer = null;
    const pollOnce = async () => {
      if (cancelled) return;
      try {
        const params = new URLSearchParams();
        params.set("campaign", campaignId);
        if (source) params.set("source", source);
        if (runId) params.set("run", runId);
        params.set("since", String(eventsCursor.current));
        const resp = await fetch(`/events?${params.toString()}`, { cache: "no-store" });
        if (!resp.ok) return;
        const payload = await resp.json();
        const entries = Array.isArray(payload.entries) ? payload.entries : [];
        // BUG2: the server now stamps the resolved session id on the /events response so the client
        // can SESSION-SCOPE each `seq`. The composite `${sid}:${seq}` is globally unique across a
        // session rotation (where the bare line index restarts at 0,1,2 and collided with the prior
        // session's cold-open). Empty sid (legacy server) degrades to ":${seq}", still unique within
        // the single session it serves.
        const sid = (payload && typeof payload.sid === "string") ? payload.sid : "";
        if (!cancelled && entries.length) {
          // Only player-facing prose streams live: narration + dialogue. Roll/system/combat rows are
          // mechanics the chronicle surfaces elsewhere — folding them in here would read as noise
          // mid-scene. Each new (un-seen) paragraph becomes a live, time-stamped chronicle beat.
          const beats = entries
            .map((e) => {
              const kind = (e && (e.kind || e.type)) || "narration";
              if (kind !== "narration" && kind !== "dialogue") return null;
              const raw = e && (e.text || e.detail);
              // #749: the wrapper progress heartbeat (#743) — a canned "the scene is arriving"
              // row the play/QA wrappers log BEFORE the DM model starts — is a LIVENESS signal,
              // not prose. It must flip the pending turn's streaming/progress state and NEVER
              // render. This check runs BEFORE the sanitize-drop below (sanitize excises the
              // exact wrapper lines, so the old order silently swallowed the row and the
              // heartbeat was a no-op for the player). Deliberately NOT setting
              // eventsStreamedThisTurnRef / dmBeatCountRef here: no prose streamed, so a
              // dead-DM beat's recovered /chat text must still render (it would otherwise be
              // suppressed to zero rows). Shared predicate from screen-table.jsx (loaded
              // first); if absent we fall through to sanitize — today's drop, no regression.
              if (typeof window.isWrapperProgressLine === "function" && window.isWrapperProgressLine(raw)) {
                notePendingProgress();
                return null;
              }
              const clean = sanitize(raw);
              if (!clean) return null;
              // #405/BUG2: dedup by the STABLE, SESSION-SCOPED composite key `${sid}:${seq}` — NOT by
              // prose. So a paragraph re-ingested by a windowing re-mount or a session-rotation cursor
              // rewind collapses to one row, the dedup can't be defeated by a reworded copy, AND a
              // post-rotation beat (a fresh session's seq 0,1,2) is no longer suppressed by collision
              // with a prior session's seq 0,1,2 (BUG2). A legacy entry with no seq (older server)
              // falls back to the text key. `orderSeq` carries the SAME composite as the chronological
              // order key; compareChronicle parses its numeric tail for the within-session tiebreak.
              const seq = (e && typeof e.seq === "number") ? e.seq : null;
              const seqKey = (seq !== null) ? `${sid}:${seq}` : null;
              const fresh = (seqKey !== null) ? claimNarrationSeq(seqKey) : claimNarration(clean);
              if (!fresh) return null;
              eventsStreamedThisTurnRef.current = true;  // the current turn HAS streamed live narration
              return { kind: "narration", text: clean, at: nextLogSeq(), orderSeq: seqKey, eventAt: e && e.t };
            })
            .filter(Boolean);
          if (beats.length) {
            setChatBeats((prev) => boundTail([...prev, ...beats], MAX_LIVE_BEATS));  // #402: cap the live tail
            // The scene is visibly building → the turn is plainly alive. Count the streamed prose as
            // real DM beats (so the NEXT turn isn't mis-treated as a cold-open 'firstBeat') and reset
            // the stall clock so a long-but-healthy streaming turn is never falsely declared 'stuck'.
            // We deliberately KEEP pending: the action bar stays gated (one move at a time) and the
            // honest "narrating" indicator stays up WHILE the scene fills in above it — the turn only
            // RESOLVES when its final text lands on /chat. This is the give-up fix: a blank wait
            // becomes "I can watch my story arriving," without relaxing the turn-gating semantics.
            dmBeatCountRef.current += beats.length;
            notePendingProgress();
          }
        }
        if (!cancelled && typeof payload.next === "number") eventsCursor.current = payload.next;
      } catch (_e) { /* the live event tail is non-critical; the /chat tail is the backstop */ }
    };
    const stop = () => { if (timer !== null) { window.clearInterval(timer); timer = null; } };
    // #393: poll a touch faster than /chat (4s) so streamed prose feels responsive without hammering
    // the stdlib server — 3s is well within the session log's mid-turn write cadence.
    const start = () => { if (timer === null) timer = window.setInterval(pollOnce, 3000); };
    const onVisibility = () => {
      if (document.visibilityState === "visible") { pollOnce(); start(); } else { stop(); }
    };
    document.addEventListener("visibilitychange", onVisibility);
    onVisibility();
    return () => { cancelled = true; stop(); document.removeEventListener("visibilitychange", onVisibility); };
  }, [campaignId, source, runId, notePendingProgress, claimNarration]);

  // #745: expose notePendingProgress so the live-progress signal is part of the hook's public surface
  // (consistent with armPending/clearPending; the /events poll calls the same ref). Purely additive —
  // existing consumers destructure named fields, so nothing breaks; it also makes the mid-stream stall
  // ceiling unit-testable without reaching into the hook's internals.
  return { chatBeats, log, pending, armPending, clearPending, abandonPending, recordPlayerEcho, notePendingProgress };
}
window.useLiveSession = useLiveSession;
// #348: expose the recovery-timing contract for tests (and devtools introspection). Purely
// additive — nothing in the running app reads these off window; the hook uses the locals above.
window.recoveryWindowMs = recoveryWindowMs;
window.stuckBackstopMs = stuckBackstopMs;  // #746: the budget-aware hard-ceiling selector (pure)
window.__PENDING_TIMING__ = {
  recoveryMs: PENDING_RECOVERY_MS,
  recoveryFirstMs: PENDING_RECOVERY_FIRST_MS,
  backstopMs: PENDING_BACKSTOP_MS,
  armGraceMs: PENDING_ARM_GRACE_MS,            // #648: the just-armed-turn protection window
  stuckBackstopMs: PENDING_STUCK_BACKSTOP_MS,  // #745/#746: later-beat hard stuck ceiling from submit (progress does NOT reset it)
  stuckBackstopFirstMs: PENDING_STUCK_BACKSTOP_FIRST_MS,  // #746: first-beat (cold-open) hard stuck ceiling
};
// #402: expose the live-tail bound for tests/devtools introspection (purely additive — the hook
// closes over the consts directly; nothing in the running app reads these off window).
window.boundTail = boundTail;
window.__LIVE_TAIL_CAPS__ = { maxBeats: MAX_LIVE_BEATS, maxEchoes: MAX_LIVE_ECHOES };

function App() {
  const [state, setState] = React.useState(window.INITIAL_STATE || {});
  const [screen, setScreen] = React.useState("launcher");
  const [campMode, setCampMode] = React.useState(false);
  const requestedCampaignRef = React.useRef(window.openWorldsRequestedCampaignFromLocation());
  const [nativeState, setNativeState] = React.useState(() => ({
    bridge: Boolean(window.OpenWorldsNative?.hasBridge?.()),
    appStatus: null,
    dependencies: [],
    providers: [],
    error: "",
  }));
  const [t, setTweak] = (window.useTweaks
    ? window.useTweaks(TWEAK_DEFAULTS)
    : [TWEAK_DEFAULTS, () => {}]);

  // #340: the in-flight-turn / live-narration state lives HERE (above the screen router) so it
  // survives navigation — the DM beat lands and the narrating indicator clears no matter which
  // screen is mounted when the turn resolves. ScreenTable reads/writes it via the `liveSession`
  // prop; the nav rail and every other screen are intentionally untouched by it.
  const liveSession = useLiveSession(state);

  // "Building your universe" loading experience (building-universe.jsx). The launcher's
  // startPlay / the Forge's bindHero stamp a sessionStorage "building" flag at the click; this
  // hook reads it on mount so the full-screen loading overlay covers BOTH waits — the
  // startProviderSession mint + the location.assign reload (the flag survives the reload) AND the
  // cold-open. It hands off (clears) when the first DM narration beat lands in liveSession.chatBeats
  // (the same real milestone the in-table cold-open clears on). Falls back gracefully if the
  // bundle/hook is absent.
  // #405: a cold-open / session error reported on the native bridge (appStatus.lastError, mirrored
  // into nativeState.error) means no narration will ever arrive — feed it to the hook so the overlay
  // DISMISSES immediately (yielding to the table, which surfaces the error + a retry) rather than
  // wedging a full-screen cover over the recovery. Pre-RELOAD mint failures are already torn down by
  // screen-launcher / screen-create (they call OpenWorldsBuilding.clear()); this catches the
  // POST-reload cold-open error, where the overlay is up and only the bridge status can flag it.
  const coldOpenError = (nativeState && (nativeState.appStatus?.lastError || nativeState.error)) || "";
  const building = (typeof window.useBuildingUniverse === "function")
    ? window.useBuildingUniverse(liveSession, coldOpenError)
    : { active: false, record: null, handoff: false, escapable: false, dismiss: () => {} };

  React.useEffect(() => {
    document.documentElement.setAttribute("data-palette", t.palette || "warm");
  }, [t.palette]);

  // Apply persisted accessibility choices (reduced motion / high contrast / UI scale) to <html>
  // on mount so they take effect app-wide, even before the Settings screen is opened.
  React.useEffect(() => {
    window.OpenWorldsA11y?.apply(window.OpenWorldsA11y.read());
  }, []);

  React.useEffect(() => {
    let cancelled = false;

    async function loadCampaignCatalog() {
      try {
        const response = await fetch("/openworlds/campaigns.json", { cache: "no-store" });
        if (!response.ok) throw new Error(`campaign catalog ${response.status}`);
        const payload = await response.json();
        if (cancelled) return;

        const nextCampaigns = Array.isArray(payload?.campaigns) ? payload.campaigns : [];
        setState((s) => {
          const requestedCampaign = requestedCampaignRef.current;
          const requestedEntry = requestedCampaign
            ? nextCampaigns.find((c) => openWorldsCampaignMatches(c, requestedCampaign))
            : null;
          const requestedStillExists = Boolean(requestedEntry);
          const requestedActiveId = requestedEntry?.id || "";
          const playerCampaigns = nextCampaigns.filter(openWorldsPlayerChronicle);
          const activeStillExists = playerCampaigns.some((c) => openWorldsCampaignMatches(c, s?.activeCampaign));
          const preferred =
            requestedActiveId ||
            playerCampaigns.find((c) => c.current)?.id ||
            playerCampaigns.find((c) => c.live && c.canResume)?.id ||
            playerCampaigns.find((c) => c.canResume)?.id ||
            "";
          if (requestedStillExists) requestedCampaignRef.current = "";
          return {
            ...s,
            campaigns: nextCampaigns,
            activeCampaign: requestedActiveId || (activeStillExists ? s.activeCampaign : preferred),
            campaignCatalog: {
              loaded: true,
              total: payload?.total ?? nextCampaigns.length,
              source: "viewer",
            },
          };
        });
      } catch (error) {
        if (cancelled) return;
        setState((s) => ({
          ...s,
          campaignCatalog: {
            loaded: false,
            source: "demo-fallback",
            error: error?.message || "campaign catalog unavailable",
          },
        }));
      }
    }

    loadCampaignCatalog();
    // Re-poll: a live play session mints its campaign a few seconds after the page loads (the
    // DM's first turn), so a one-shot load would miss it and the launcher/table would stick to
    // whatever stale save existed at boot. Polling keeps `current` fresh so the auto-follow
    // effect below can bind the table to the live campaign the moment it appears.
    const timer = window.setInterval(loadCampaignCatalog, 4000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  const refreshNative = React.useCallback(async () => {
    const bridge = Boolean(window.OpenWorldsNative?.hasBridge?.());
    if (!bridge) {
      setNativeState((s) => ({ ...s, bridge: false, error: "Native bridge unavailable" }));
      return;
    }
    try {
      const appStatus = await window.OpenWorldsNative.request("appStatus", {});
      setNativeState({
        bridge: true,
        appStatus,
        dependencies: Array.isArray(appStatus?.dependencies) ? appStatus.dependencies : [],
        providers: Array.isArray(appStatus?.providers) ? appStatus.providers : [],
        error: appStatus?.lastError || "",
      });
    } catch (error) {
      setNativeState((s) => ({
        ...s,
        bridge: false,
        error: error?.message || "Native bridge unavailable",
      }));
    }
  }, []);

  React.useEffect(() => {
    refreshNative();
    const onReady = () => refreshNative();
    window.addEventListener("clawdnd:native-ready", onReady);
    const timer = window.setInterval(refreshNative, 5000);
    return () => {
      window.removeEventListener("clawdnd:native-ready", onReady);
      window.clearInterval(timer);
    };
  }, [refreshNative]);

  const navigate = React.useCallback((id, opts = {}) => {
    if (opts?.openCamp) setCampMode(true);
    else if (id !== "map") setCampMode(false);
    setScreen(id);
    openWorldsSyncHashForScreen(id, opts);
  }, []);

  // Auto-land in the session when a live DM (provider) is attached. The launcher's "Resume /
  // Begin" calls the native startProviderSession bridge, which repoints the WebView at the
  // live, move-sink-wired viewer on a fresh port — the page reloads here at the launcher, and
  // this carries the player straight into the play surface. Fires once per load (a ref guard),
  // so manual navigation back to the launcher mid-session is respected.
  const didAutoRoute = React.useRef(false);
  React.useEffect(() => {
    if (didAutoRoute.current) return;
    if (nativeState?.appStatus?.runningProvider && screen === "launcher") {
      didAutoRoute.current = true;
      navigate("table", { replaceHash: true });
    }
  }, [nativeState, screen, navigate]);

  // Building→table handoff. The "building your universe" overlay clears (active → inactive) the
  // moment the first DM narration beat lands — that beat is already in the chronicle, so land the
  // player on the table to read it. This is belt-and-suspenders with didAutoRoute above (which
  // covers the native runningProvider signal); it also handles the in-browser already-live case
  // where the overlay was shown but no native provider status flips. Only redirects FROM the
  // launcher, so a player who navigated mid-build is respected.
  const wasBuilding = React.useRef(false);
  React.useEffect(() => {
    if (building.active) { wasBuilding.current = true; return; }
    if (wasBuilding.current && screen === "launcher") {
      navigate("table", { replaceHash: true });
    }
    wasBuilding.current = false;
  }, [building.active, screen, navigate]);

  // During a live play session (a DM provider is attached), keep the active campaign bound to
  // the viewer's CURRENT (live) campaign. The DM mints this run's campaign a few seconds after
  // the page loads, so the initial catalog pick can be a stale save; once the re-poll surfaces
  // the live campaign as `current`, follow it so the party/surface and can_act track the real
  // session (the chronicle already follows the live run via /chat). Outside a play session the
  // launcher's manual selection is left untouched.
  //
  // LOCKOUT P0 (detach-locks-the-action-bar): the OLD gate `!nativeState?.appStatus?.runningProvider`
  // early-returned in a PLAIN BROWSER (scripts/play.sh — the exact env the sweep personas ran), so
  // the client NEVER re-followed the live run when the catalog re-poll surfaced it, and kept posting
  // a STALE ?campaign after navigating away and back. The catalog already carries the move-sink truth
  // (`current` is the attached run, `live` folds in `move_sink_live`), so follow the live run whenever
  // it is present — native runningProvider OR an in-browser current+live catalog row — and let the
  // server-side heal (server.py `_live_play_view_campaign`) be the backstop if this ever lags.
  React.useEffect(() => {
    const list = Array.isArray(state?.campaigns) ? state.campaigns : [];
    const nativeLive = Boolean(nativeState?.appStatus?.runningProvider);
    // The in-browser play signal: a campaign the move sink is feeding (attached `current` AND `live`).
    const browserLive = list.some((c) => c && c.current && c.live);
    if (!nativeLive && !browserLive) return;
    const liveCampaign = list.find((c) => c.current && c.live)
      || list.find((c) => c.current)
      || list.find((c) => c.live);
    if (liveCampaign && liveCampaign.id !== state?.activeCampaign) {
      setState((s) => ({ ...s, activeCampaign: liveCampaign.id }));
    }
  }, [state?.campaigns, state?.activeCampaign, nativeState]);

  // Keyboard shortcuts
  React.useEffect(() => {
    const onKey = (e) => {
      const target = e.target;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (
        target instanceof Element &&
        (target.closest("input, textarea, select, [contenteditable='true']") ||
          target.isContentEditable)
      ) {
        return;
      }
      const map = {
        "t": "table",
        "x": "combat",
        "p": "dialogue",
        "m": "map",
        "c": "character",
        "i": "inventory",
        "f": "forge",
        "r": "relations",
        "j": "journal",
        "b": "bestiary",
        "a": "acts",
        "$": "merchant",
        "w": "launcher",
        "n": "create",
        "s": "seed",
        ",": "settings",
        "?": "settings",
      };
      const id = map[e.key.toLowerCase()];
      if (id) {
        e.preventDefault();
        navigate(id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  // Deep-link the active screen via the URL hash (e.g. #character, #battle→combat).
  // Lets a screen be linked/bookmarked directly and makes headless QA captures of a
  // specific screen possible. On mount we honor the hash; hashchange re-routes live.
  React.useEffect(() => {
    const initial = openWorldsRouteFromHash();
    if (initial) {
      setCampMode(initial.campMode);
      setScreen(initial.id);
    }
    const onHash = () => {
      const route = openWorldsRouteFromHash();
      if (!route) return;
      setCampMode(route.campMode);
      setScreen(route.id);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const campaigns = Array.isArray(state?.campaigns) ? state.campaigns : [];
  const playerChronicles = campaigns.filter(openWorldsPlayerChronicle);
  const current =
    playerChronicles.find((c) => openWorldsCampaignMatches(c, state?.activeCampaign)) ||
    playerChronicles[0] ||
    { title: "Open Worlds", day: "" };

  return (
    <React.Fragment>
    <div className="window">
      <TitleBar
        campaign={current.title}
        location={screen === "map" && campMode ? "Camp" : SCREEN_TITLES[screen]}
        day={current.day}
        capability={capabilityForScreen(screen, nativeState)}
        nativeStatus={nativeState}
      />
      <div className="app">
        <NavRail current={screen} onNavigate={navigate} />
        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <TabBar current={screen} onNavigate={navigate} />
          <div className="stage" style={{ flex: 1, minHeight: 0 }}>
            <div className="parchment">
              <div className="stage-inner">
                <ScreenRouter
                  screen={screen}
                  state={state}
                  setState={setState}
                  onNavigate={navigate}
                  campMode={campMode}
                  setCampMode={setCampMode}
                  nativeState={nativeState}
                  refreshNative={refreshNative}
                  liveSession={liveSession}
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      {window.TweaksPanel && (
        <window.TweaksPanel title="Codex Tweaks">
          <window.TweakSection label="Palette" />
          <window.TweakRadio
            label="Tone"
            value={t.palette}
            onChange={(v) => setTweak("palette", v)}
            options={[
              { value: "warm", label: "Warm" },
              { value: "cool", label: "Aged" },
              { value: "dark", label: "Walnut" },
            ]}
          />

          <window.TweakSection label="Atmosphere" />
          <window.TweakToggle
            label="Candle glow"
            value={t.candle}
            onChange={(v) => setTweak("candle", v)}
          />
          <window.TweakToggle
            label="Corner filigree"
            value={t.ornaments}
            onChange={(v) => setTweak("ornaments", v)}
          />

          <window.TweakSection label="Jump to a screen" />
          {[...window.NAV_GROUPS, window.NAV_BOTTOM].flatMap((g) => g.tabs).map((t) => (
            <window.TweakButton
              key={t.id}
              label={t.label}
              onClick={() => navigate(t.id)}
            />
          ))}
        </window.TweaksPanel>
      )}
    </div>

      {/* The full-screen "building your universe" loading overlay. position:fixed (styles.css), so
          it covers the whole app — title bar, rail, stage — while the table boots underneath and
          the app-level /chat poll keeps running. Clears itself when the first DM narration lands —
          or, #405, on a cold-open error, a ~120s stall ceiling, or the manual "Enter anyway →"
          (onEnterAnyway → dismiss), so it can never wedge over the table's own recovery. */}
      {building.active && window.BuildingUniverse && (
        <window.BuildingUniverse
          record={building.record}
          handoff={building.handoff}
          escapable={building.escapable}
          onEnterAnyway={building.dismiss}
        />
      )}
    </React.Fragment>
  );
}

const SCREEN_TITLES = {
  launcher: "Chronicles",
  roster: "Choose Your Hero",
  table: "The Session",
  combat: "Battle",
  character: "Heroes",
  create: "Creation Plane",
  forge: "Item Forge",
  relations: "Relations",
  acts: "Acts",
  seed: "World Seed",
  inventory: "Stash",
  map: "World Atlas",
  journal: "Quest Journal",
  bestiary: "Codex",
  merchant: "The Market",
  dialogue: "Parley",
  settings: "Setting",
};

function capabilityForScreen(screen, nativeState) {
  // v1.0.2: removed the per-screen Wired / Display-only / Unavailable TitleBar
  // badges — every screen is now backed by live read-models + the native bridge,
  // so the honesty distinction is no longer load-bearing (the surfaces that
  // still have preview-only buttons gate them locally on can_act, not via a
  // global label). Returning null suppresses the badge entirely; TitleBar
  // handles the falsy case.
  return null;
}

function nativePreferredProvider(nativeState) {
  const app = nativeState?.appStatus || {};
  return app?.preferences?.selectedProvider || app?.selectedProvider || "";
}

function ScreenRouter({ screen, state, setState, onNavigate, campMode, setCampMode, nativeState, refreshNative, liveSession }) {
  const preferredProvider = nativePreferredProvider(nativeState);
  switch (screen) {
    case "launcher":  return <ScreenLauncher state={state} setState={setState} onNavigate={onNavigate} preferredProvider={preferredProvider} />;
    case "roster":    return <ScreenRoster state={state} setState={setState} onNavigate={onNavigate} preferredProvider={preferredProvider} />;
    case "table":     return <ScreenTable state={state} setState={setState} onNavigate={onNavigate} liveSession={liveSession} />;
    case "combat":    return <ScreenCombat state={state} setState={setState} onNavigate={onNavigate} />;
    case "character": return <ScreenCharacter state={state} setState={setState} onNavigate={onNavigate} liveSession={liveSession} />;
    case "create":    return <ScreenCreate state={state} setState={setState} onNavigate={onNavigate} preferredProvider={preferredProvider} />;
    case "forge":     return <ScreenForge state={state} setState={setState} onNavigate={onNavigate} />;
    case "relations": return <ScreenRelations state={state} setState={setState} onNavigate={onNavigate} />;
    case "acts":      return <ScreenActs state={state} setState={setState} onNavigate={onNavigate} />;
    case "seed":      return <ScreenSeed state={state} setState={setState} onNavigate={onNavigate} />;
    case "inventory": return <ScreenInventory state={state} setState={setState} onNavigate={onNavigate} />;
    case "map":       return <ScreenMap state={state} setState={setState} onNavigate={onNavigate} campMode={campMode} setCampMode={setCampMode} liveSession={liveSession} />;
    case "journal":   return <ScreenJournal state={state} setState={setState} onNavigate={onNavigate} />;
    case "bestiary":  return <ScreenBestiary state={state} setState={setState} onNavigate={onNavigate} />;
    case "merchant":  return <ScreenMerchant state={state} setState={setState} onNavigate={onNavigate} />;
    case "dialogue":  return <ScreenDialogue state={state} setState={setState} onNavigate={onNavigate} />;
    case "settings":  return <ScreenSettings state={state} setState={setState} onNavigate={onNavigate} nativeState={nativeState} refreshNative={refreshNative} />;
    default:          return <ScreenLauncher state={state} setState={setState} onNavigate={onNavigate} preferredProvider={preferredProvider} />;
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <ToastProvider>
    <App />
  </ToastProvider>
);
