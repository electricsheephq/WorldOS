/* App router + tweaks */

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "palette": "warm",
  "ornaments": true,
  "candle": true
}/*EDITMODE-END*/;

function App() {
  const [state, setState] = React.useState(window.INITIAL_STATE || {});
  const [screen, setScreen] = React.useState("launcher");
  const [campMode, setCampMode] = React.useState(false);
  const [t, setTweak] = (window.useTweaks
    ? window.useTweaks(TWEAK_DEFAULTS)
    : [TWEAK_DEFAULTS, () => {}]);

  React.useEffect(() => {
    document.documentElement.setAttribute("data-palette", t.palette || "warm");
  }, [t.palette]);

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
    return () => { cancelled = true; };
  }, []);

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
      />
      <div className="app">
        <NavRail current={screen} onNavigate={navigate} />
        <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
          <TabBar current={screen} onNavigate={navigate} />
          <div className="stage" style={{ flex: 1, minHeight: 0 }}>
            <div className="parchment">
              <div className="stage-inner">
                <ScreenRouter screen={screen} state={state} setState={setState} onNavigate={navigate} campMode={campMode} setCampMode={setCampMode} />
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

function ScreenRouter({ screen, state, setState, onNavigate, campMode, setCampMode }) {
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
    case "settings":  return <ScreenSettings state={state} setState={setState} onNavigate={onNavigate} />;
    default:          return <ScreenLauncher state={state} setState={setState} onNavigate={onNavigate} />;
  }
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <ToastProvider>
    <App />
  </ToastProvider>
);
