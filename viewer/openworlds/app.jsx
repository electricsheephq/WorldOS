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
  const dmBeatCountRef = React.useRef(0);
  const recoveryTimer = React.useRef(null);
  const backstopTimer = React.useRef(null);

  // sanitizeNarration lives in screen-table.jsx (loaded first); fall back to identity if absent.
  const sanitize = (txt) => (typeof window.sanitizeNarration === "function" ? window.sanitizeNarration(txt) : (txt || ""));

  const clearTimers = React.useCallback(() => {
    if (recoveryTimer.current) { window.clearTimeout(recoveryTimer.current); recoveryTimer.current = null; }
    if (backstopTimer.current) { window.clearTimeout(backstopTimer.current); backstopTimer.current = null; }
  }, []);

  const clearPending = React.useCallback(() => { clearTimers(); setPending(null); }, [clearTimers]);

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
    setPending({ text, since: Date.now(), stuck: false, firstBeat });
    recoveryTimer.current = window.setTimeout(() => {
      setPending((p) => (p ? { ...p, stuck: true } : p));
    }, recoveryMs);
    backstopTimer.current = window.setTimeout(() => setPending(null), PENDING_BACKSTOP_MS);
  }, [clearTimers]);

  const recordPlayerEcho = React.useCallback((who, text) => {
    setLog((l) => [...l, { kind: "action", who, text }]);
  }, []);

  React.useEffect(() => clearTimers, [clearTimers]);

  // When the bound live campaign changes, reset the tail so we don't bleed one run's beats into
  // another (the cursor is per-file; a new run starts at 0).
  React.useEffect(() => {
    chatCursor.current = 0;
    dmBeatCountRef.current = 0;
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
          const beats = items
            .map((it) => {
              if (it.role === "player") return { kind: "dialog", who: "You", text: it.text };
              const clean = sanitize(it.text);
              return clean ? { kind: "narration", text: clean } : null;
            })
            .filter(Boolean);
          if (beats.length) setChatBeats((prev) => [...prev, ...beats]);
          // A fresh DM narration beat means the turn resolved → clear the narrating indicator
          // (and its timers). Player echoes / wholly-internal beats don't count.
          if (beats.some((b) => b.kind === "narration")) {
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
  }, [campaignId, source, runId, clearPending]);

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
