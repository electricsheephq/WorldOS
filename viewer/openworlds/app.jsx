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
      "launcher", "table", "combat", "dialogue", "map", "character", "inventory",
      "forge", "relations", "journal", "bestiary", "acts", "merchant", "create",
      "seed", "settings",
    ]);
    const ALIAS = { battle: "combat", parley: "dialogue", chronicles: "launcher", market: "merchant", stash: "inventory" };
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

function ScreenRouter({ screen, state, setState, onNavigate, campMode, setCampMode, nativeState, refreshNative }) {
  switch (screen) {
    case "launcher":  return <ScreenLauncher state={state} setState={setState} onNavigate={onNavigate} />;
    case "table":     return <ScreenTable state={state} setState={setState} onNavigate={onNavigate} />;
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
