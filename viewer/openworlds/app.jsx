/* App router + tweaks */

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

// #342: neutralize markup in player free-text BEFORE it is sent to the engine or echoed into the
// chronicle. The adversarial run (#324 v2) found that submitting "<script>…</script>", "{{ }}", or
// "<b>…</b>" sent the raw markup straight to the DM (it stalled 35s+) and rode along in the local
// echo. React already escapes on *display* (it never renders raw HTML), so this is NOT an XSS fix —
// it is a robustness fix: a hostile/odd free-text turn must not be able to wedge the DM or the loop.
// We strip angle-bracket tags and defang template-style "{{ … }}" / "}}" runs to plain text, collapse
// whitespace, and cap absurd length — keeping ordinary apostrophes, quotes, punctuation, and emoji
// intact so a normal in-character line is untouched. Viewer-side only; the engine stays sole writer.
window.neutralizeMarkup = window.neutralizeMarkup || function neutralizeMarkup(raw) {
  if (typeof raw !== "string") return "";
  let t = raw;
  // Drop anything that looks like an HTML/XML tag (incl. <script>…</script> bodies are kept as text
  // once their tags are removed). Do it twice so "<<b>>" style nesting can't leave a stray bracket.
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
// #348: the recovery 'stuck' timeout is ADAPTIVE by turn position, because the DM beat lands
// all-at-once (the /chat tail carries NO streaming/partial/heartbeat signal — the duo+human
// runners append ONE {"role":"dm",...} line only after the whole turn's `result` is in, so the
// poll sees zero new items for the entire turn then the complete beat). With no in-flight
// progress to reset on, a fixed wall-clock from submit was the only lever — and at 90s it
// PRE-EMPTED the legit Act-opening (the #324 narrative persona saw the cold-open take several
// minutes and still succeed → false 'stuck', narration lost at a cliffhanger, #348).
//   • FIRST beat of a session (the cold-open / Act-opening) gets a generous window — the engine
//     is building the world + setting the scene; a blind newbie run saw this take 5–8 min.
//   • LATER beats are quick (the old 90s was tuned for these); keep them snappy so a genuine
//     mid-session stall still recovers fast.
// The 12-min hard backstop is UNCHANGED — a turn that blows even the first-beat window still
// gets force-cleared. "first beat?" = no DM narration has arrived this session yet (the hook's
// dmBeatCountRef, reset to 0 on every run change).
const PENDING_RECOVERY_MS = 90 * 1000;            // #342: later-beat stall window (DM turns are ~35–60s).
const PENDING_RECOVERY_FIRST_MS = 4 * 60 * 1000;  // #348: first-beat (Act-opening) window — fits the multi-minute cold open.
const PENDING_BACKSTOP_MS = 12 * 60 * 1000;       // …with the original hard backstop as a final net.
// #348: the single source of truth for the recovery window, by turn position. Pure + exported
// (window.__PENDING_TIMING__ below) so the timing contract is unit-testable without reaching into
// the hook's internal beat counter. firstBeat ⇒ the longer cold-open window; else the snappy one.
function recoveryWindowMs(firstBeat) {
  return firstBeat ? PENDING_RECOVERY_FIRST_MS : PENDING_RECOVERY_MS;
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
    campaigns.find((c) => c.id === state?.activeCampaign) ||
    campaigns[0] ||
    {};
  const campaignId = activeCampaign.campaign_id || state?.activeCampaign || activeCampaign.id || "";
  const source = activeCampaign.source || "";
  const runId = activeCampaign.runId || "";

  const [chatBeats, setChatBeats] = React.useState([]);
  const [log, setLog] = React.useState([]);           // local optimistic player echoes
  const [pending, setPending] = React.useState(null);  // { text, since, stuck? } | null
  const chatCursor = React.useRef(0);
  const eventsCursor = React.useRef(0);                // #393: per-file cursor for the live /events tail
  const dmBeatCountRef = React.useRef(0);
  // #393: dedup key set shared across BOTH narration sources. The session log streams a turn's
  // narration mid-flight via /events; the duo/human runner ALSO appends the SAME prose to /chat at
  // turn-END. Without a shared seen-set the player would see each streamed paragraph twice (once live,
  // once when the chat line lands). Keyed by normalized narration text so whichever source surfaces a
  // given paragraph FIRST wins and the later duplicate is dropped. Player echoes are never deduped.
  const seenNarration = React.useRef(new Set());
  const recoveryTimer = React.useRef(null);
  const backstopTimer = React.useRef(null);

  // sanitizeNarration lives in screen-table.jsx (loaded first); fall back to identity if absent.
  const sanitize = (txt) => (typeof window.sanitizeNarration === "function" ? window.sanitizeNarration(txt) : (txt || ""));
  // #393: a stable dedup key for a narration paragraph — whitespace-collapsed + lowercased so the
  // /events copy and the /chat copy of the same prose hash identically. First-seen returns true (show
  // it + record the key); a repeat returns false (suppress). Empty/blank text is never recorded.
  const claimNarration = React.useCallback((txt) => {
    const key = String(txt || "").replace(/\s+/g, " ").trim().toLowerCase();
    if (!key) return false;
    if (seenNarration.current.has(key)) return false;
    seenNarration.current.add(key);
    return true;
  }, []);

  const clearTimers = React.useCallback(() => {
    if (recoveryTimer.current) { window.clearTimeout(recoveryTimer.current); recoveryTimer.current = null; }
    if (backstopTimer.current) { window.clearTimeout(backstopTimer.current); backstopTimer.current = null; }
  }, []);

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

  const clearPending = React.useCallback(() => { clearTimers(); setPendingState(null); }, [clearTimers, setPendingState]);

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
    const firstBeat = dmBeatCountRef.current === 0;
    const recoveryMs = recoveryWindowMs(firstBeat);
    setPendingState({ text, since: Date.now(), stuck: false, firstBeat });
    recoveryTimer.current = window.setTimeout(() => {
      setPendingState((p) => (p ? { ...p, stuck: true } : p));
    }, recoveryMs);
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
    clearTimers();
    const recoveryMs = recoveryWindowMs(Boolean(p.firstBeat));
    recoveryTimer.current = window.setTimeout(() => {
      setPendingState((q) => (q ? { ...q, stuck: true } : q));
    }, recoveryMs);
    backstopTimer.current = window.setTimeout(() => setPendingState(null), PENDING_BACKSTOP_MS);
    // Clear any prior 'stuck' flag — fresh prose just arrived, so the turn is plainly not stuck.
    if (p.stuck) setPendingState((q) => (q ? { ...q, stuck: false } : q));
  }, [clearTimers, setPendingState]);

  const recordPlayerEcho = React.useCallback((who, text) => {
    setLog((l) => [...l, { kind: "action", who, text, at: nextLogSeq() }]);  // #274: creation-order stamp
  }, []);

  React.useEffect(() => clearTimers, [clearTimers]);

  // When the bound live campaign changes, reset the tail so we don't bleed one run's beats into
  // another (the cursor is per-file; a new run starts at 0).
  React.useEffect(() => {
    chatCursor.current = 0;
    eventsCursor.current = 0;          // #393: reset the live /events tail per run
    dmBeatCountRef.current = 0;
    seenNarration.current = new Set();  // #393: a fresh run shares no dedup keys with the last
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
              if (it.role === "player") return { kind: "dialog", who: "You", text: it.text, at: nextLogSeq() };
              dmLineArrived = true;
              const clean = sanitize(it.text);
              // #393: drop a turn-END chat beat whose prose already streamed live via /events this
              // turn (claimNarration is false on a repeat) so the same paragraph isn't shown twice.
              return clean && claimNarration(clean) ? { kind: "narration", text: clean, at: nextLogSeq() } : null;
            })
            .filter(Boolean);
          if (beats.length) setChatBeats((prev) => [...prev, ...beats]);
          // The arrival of the DM's turn-END line means the turn RESOLVED → clear the narrating
          // indicator + its timers. This fires even when the prose was wholly deduped (a turn whose
          // entire beat streamed live via /events), so a fully-streamed turn still re-opens the bar.
          // Player echoes alone never resolve a turn.
          if (dmLineArrived) {
            dmBeatCountRef.current += beats.filter((b) => b.kind === "narration").length;
            clearPending();
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
        if (!cancelled && entries.length) {
          // Only player-facing prose streams live: narration + dialogue. Roll/system/combat rows are
          // mechanics the chronicle surfaces elsewhere — folding them in here would read as noise
          // mid-scene. Each new (un-seen) paragraph becomes a live, time-stamped chronicle beat.
          const beats = entries
            .map((e) => {
              const kind = (e && (e.kind || e.type)) || "narration";
              if (kind !== "narration" && kind !== "dialogue") return null;
              const clean = sanitize(e && (e.text || e.detail));
              return clean && claimNarration(clean) ? { kind: "narration", text: clean, at: nextLogSeq() } : null;
            })
            .filter(Boolean);
          if (beats.length) {
            setChatBeats((prev) => [...prev, ...beats]);
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

  return { chatBeats, log, pending, armPending, clearPending, recordPlayerEcho };
}
window.useLiveSession = useLiveSession;
// #348: expose the recovery-timing contract for tests (and devtools introspection). Purely
// additive — nothing in the running app reads these off window; the hook uses the locals above.
window.recoveryWindowMs = recoveryWindowMs;
window.__PENDING_TIMING__ = {
  recoveryMs: PENDING_RECOVERY_MS,
  recoveryFirstMs: PENDING_RECOVERY_FIRST_MS,
  backstopMs: PENDING_BACKSTOP_MS,
};

function App() {
  const [state, setState] = React.useState(window.INITIAL_STATE || {});
  const [screen, setScreen] = React.useState("launcher");
  const [campMode, setCampMode] = React.useState(false);
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
  const building = (typeof window.useBuildingUniverse === "function")
    ? window.useBuildingUniverse(liveSession)
    : { active: false, record: null, handoff: false, dismiss: () => {} };

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
          const activeStillExists = nextCampaigns.some((c) => c.id === s?.activeCampaign);
          const preferred =
            nextCampaigns.find((c) => c.current)?.id ||
            nextCampaigns.find((c) => c.live)?.id ||
            nextCampaigns[0]?.id ||
            "";
          return {
            ...s,
            campaigns: nextCampaigns,
            activeCampaign: activeStillExists ? s.activeCampaign : preferred,
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
      setScreen("table");
    }
  }, [nativeState, screen]);

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
      setScreen("table");
    }
    wasBuilding.current = false;
  }, [building.active, screen]);

  // During a live play session (a DM provider is attached), keep the active campaign bound to
  // the viewer's CURRENT (live) campaign. The DM mints this run's campaign a few seconds after
  // the page loads, so the initial catalog pick can be a stale save; once the re-poll surfaces
  // the live campaign as `current`, follow it so the party/surface and can_act track the real
  // session (the chronicle already follows the live run via /chat). Outside a play session the
  // launcher's manual selection is left untouched.
  React.useEffect(() => {
    if (!nativeState?.appStatus?.runningProvider) return;
    const list = Array.isArray(state?.campaigns) ? state.campaigns : [];
    const liveCampaign = list.find((c) => c.current) || list.find((c) => c.live);
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
        setScreen(id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Deep-link the active screen via the URL hash (e.g. #character, #battle→combat).
  // Lets a screen be linked/bookmarked directly and makes headless QA captures of a
  // specific screen possible. On mount we honor the hash; hashchange re-routes live.
  React.useEffect(() => {
    const VALID = new Set([
      "launcher", "roster", "table", "combat", "dialogue", "map", "character", "inventory",
      "forge", "relations", "journal", "bestiary", "acts", "merchant", "create",
      "seed", "settings",
    ]);
    const ALIAS = { battle: "combat", parley: "dialogue", chronicles: "launcher", market: "merchant", stash: "inventory", heroes: "character", pick: "roster", picker: "roster" };
    const fromHash = () => {
      const raw = (window.location.hash || "").replace(/^#\/?/, "").trim().toLowerCase();
      if (!raw) return null;
      return VALID.has(raw) ? raw : (ALIAS[raw] || null);
    };
    const initial = fromHash();
    if (initial) setScreen(initial);
    const onHash = () => { const id = fromHash(); if (id) setScreen(id); };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const navigate = (id, opts) => {
    if (opts?.openCamp) setCampMode(true);
    setScreen(id);
  };

  const campaigns = Array.isArray(state?.campaigns) ? state.campaigns : [];
  const current =
    campaigns.find((c) => c.id === state?.activeCampaign) ||
    campaigns[0] ||
    { title: "Open Worlds", day: "" };

  return (
    <React.Fragment>
    <div className="window">
      <TitleBar
        campaign={current.title}
        location={SCREEN_TITLES[screen]}
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
          the app-level /chat poll keeps running. Clears itself when the first DM narration lands. */}
      {building.active && window.BuildingUniverse && (
        <window.BuildingUniverse record={building.record} handoff={building.handoff} />
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

function ScreenRouter({ screen, state, setState, onNavigate, campMode, setCampMode, nativeState, refreshNative, liveSession }) {
  switch (screen) {
    case "launcher":  return <ScreenLauncher state={state} setState={setState} onNavigate={onNavigate} />;
    case "roster":    return <ScreenRoster state={state} setState={setState} onNavigate={onNavigate} />;
    case "table":     return <ScreenTable state={state} setState={setState} onNavigate={onNavigate} liveSession={liveSession} />;
    case "combat":    return <ScreenCombat state={state} setState={setState} onNavigate={onNavigate} />;
    case "character": return <ScreenCharacter state={state} setState={setState} onNavigate={onNavigate} />;
    case "create":    return <ScreenCreate state={state} setState={setState} onNavigate={onNavigate} />;
    case "forge":     return <ScreenForge state={state} setState={setState} onNavigate={onNavigate} />;
    case "relations": return <ScreenRelations state={state} setState={setState} onNavigate={onNavigate} />;
    case "acts":      return <ScreenActs state={state} setState={setState} onNavigate={onNavigate} />;
    case "seed":      return <ScreenSeed state={state} setState={setState} onNavigate={onNavigate} />;
    case "inventory": return <ScreenInventory state={state} setState={setState} onNavigate={onNavigate} />;
    case "map":       return <ScreenMap state={state} setState={setState} onNavigate={onNavigate} campMode={campMode} setCampMode={setCampMode} />;
    case "journal":   return <ScreenJournal state={state} setState={setState} onNavigate={onNavigate} />;
    case "bestiary":  return <ScreenBestiary state={state} setState={setState} onNavigate={onNavigate} />;
    case "merchant":  return <ScreenMerchant state={state} setState={setState} onNavigate={onNavigate} />;
    case "dialogue":  return <ScreenDialogue state={state} setState={setState} onNavigate={onNavigate} />;
    case "settings":  return <ScreenSettings state={state} setState={setState} onNavigate={onNavigate} nativeState={nativeState} refreshNative={refreshNative} />;
    default:          return <ScreenLauncher state={state} setState={setState} onNavigate={onNavigate} />;
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <ToastProvider>
    <App />
  </ToastProvider>
);
