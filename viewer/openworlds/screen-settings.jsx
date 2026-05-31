/* Screen: Settings — audio, video, controls, save slots, accessibility */

function ScreenSettings({ onNavigate, state, setState, nativeState, refreshNative }) {
  const [section, setSection] = React.useState("native");
  const toast = window.useToast ? window.useToast() : (() => {});

  // Genuinely functional accessibility/display controls are driven through the shared
  // window.OpenWorldsA11y bridge (defined in app.jsx): it writes data-reduced-motion /
  // data-contrast / --ui-scale onto <html> (all backed by real CSS in styles.css) and
  // persists to localStorage. Seed local UI from the persisted values so the controls
  // reflect the document state the app applied on mount, then drive the bridge on change.
  const a11yBridge = window.OpenWorldsA11y;
  const [a11y, setA11y] = React.useState(() =>
    a11yBridge?.read ? a11yBridge.read() : { reducedMotion: false, highContrast: false, uiScale: 100 }
  );
  const applyA11y = React.useCallback((patch) => {
    setA11y((prev) => {
      const next = { ...prev, ...patch };
      a11yBridge?.apply?.(next); // sets <html> attrs/--ui-scale + persists; no-op if bridge absent
      return next;
    });
  }, [a11yBridge]);

  // Remaining sections are display-only prototypes: they have no backing mechanism in the
  // app, so their controls are labelled "(preview)" / disabled rather than silently lying.
  const [audio, setAudio] = React.useState({ master: 72, music: 60, sfx: 80, ambience: 50, voice: 70, duckMusic: true, crossfade: true });
  const [display, setDisplay] = React.useState({ contrast: 50, vignette: true, paperGrain: true, candleGlow: true });
  const [gameplay, setGameplay] = React.useState({ auto: 15, narration: "balanced", dice: "visible", dangerHints: true, confirmDestructive: true, aiPartyRolls: false });
  const [controls, setControls] = React.useState({ twoFingerScroll: true, pinchZoom: true, forceTouchInspect: false });
  const [accessibility, setAccessibility] = React.useState({ dyslexic: false, captions: true, underlineChoices: false });

  const campaigns = Array.isArray(state?.campaigns) ? state.campaigns : [];

  // The campaign the save/load/export actions target: the active one app.jsx tracks, else the
  // catalog's `current` (the attached live run), else the newest in the list. Empty when there
  // is no chronicle on disk yet (the actions then toast a graceful "no chronicle" notice). The
  // engine save/load tools still path-validate this id, so a stale value can't escape the store.
  const activeCampaignId =
    state?.activeCampaign ||
    (campaigns.find((c) => c.current) || campaigns[0] || {}).id ||
    "";
  const [savesBusy, setSavesBusy] = React.useState("");

  // Export (ST-03): pure read — GET /export streams the campaign's snapshot.json verbatim, which
  // we wrap in a Blob and hand to the browser as <campaign>-chronicle.json. Non-destructive.
  const exportChronicle = async () => {
    if (!activeCampaignId) {
      toast({ kind: "danger", eyebrow: "Saves", title: "No chronicle to export", body: "Begin a chronicle from the Worlds shelf first." });
      return;
    }
    setSavesBusy("export");
    try {
      const resp = await fetch(`/export?campaign=${encodeURIComponent(activeCampaignId)}`, { cache: "no-store" });
      if (!resp.ok) throw new Error(`export ${resp.status}`);
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${activeCampaignId}-chronicle.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast({ kind: "ok", eyebrow: "Saves", title: "Chronicle exported", body: `${activeCampaignId}-chronicle.json` });
    } catch (error) {
      toast({ kind: "danger", eyebrow: "Saves", title: "Export failed", body: error?.message || "The viewer could not reach /export." });
    } finally {
      setSavesBusy("");
    }
  };

  // Quicksave / Quickload (ST-02): the engine is the sole writer, so these POST a save/load INTENT
  // to /save-slot|/load-slot, which bridge in-process to the engine's save_slot/load_slot tools
  // (the engine performs the snapshot copy/restore under its own campaign_lock + save_campaign).
  const saveSlot = async () => {
    if (!activeCampaignId) {
      toast({ kind: "danger", eyebrow: "Saves", title: "No chronicle to save", body: "Begin a chronicle from the Worlds shelf first." });
      return;
    }
    setSavesBusy("save");
    try {
      const resp = await fetch("/save-slot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ campaign: activeCampaignId, slot: "quicksave" }),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok || payload.ok === false) throw new Error(payload.reason || `save ${resp.status}`);
      toast({ kind: "ok", eyebrow: "Saves", title: "Quicksave written", body: "Restore it any time with Quickload." });
    } catch (error) {
      toast({ kind: "danger", eyebrow: "Saves", title: "Quicksave failed", body: error?.message || "The viewer could not reach the engine save lane." });
    } finally {
      setSavesBusy("");
    }
  };

  const loadSlot = async () => {
    if (!activeCampaignId) {
      toast({ kind: "danger", eyebrow: "Saves", title: "No chronicle to restore", body: "Begin a chronicle from the Worlds shelf first." });
      return;
    }
    // Quickload OVERWRITES the live chronicle with the quicksave — gate behind an explicit
    // confirm so a fat-fingered click can't silently rewind an in-progress session.
    const ok = window.confirm(
      "Restore the quicksave?\n\nThis OVERWRITES the current live chronicle with the last quicksave. " +
      "Any progress since that quicksave will be lost. This cannot be undone."
    );
    if (!ok) return;
    setSavesBusy("load");
    try {
      const resp = await fetch("/load-slot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ campaign: activeCampaignId, slot: "quicksave" }),
      });
      const payload = await resp.json().catch(() => ({}));
      if (!resp.ok || payload.ok === false) throw new Error(payload.reason || `load ${resp.status}`);
      toast({ kind: "ok", eyebrow: "Saves", title: "Quicksave restored", body: "The chronicle has been rolled back to the quicksave." });
    } catch (error) {
      toast({ kind: "danger", eyebrow: "Saves", title: "Quickload failed", body: error?.message || "The viewer could not reach the engine load lane." });
    } finally {
      setSavesBusy("");
    }
  };

  const SECTIONS = [
    { id: "native", label: "WorldOS" },
    { id: "audio", label: "Sound" },
    { id: "display", label: "Display" },
    { id: "gameplay", label: "Gameplay" },
    { id: "controls", label: "Controls" },
    { id: "accessibility", label: "Accessibility" },
    { id: "saves", label: "Saves" },
    { id: "about", label: "About" },
  ];

  return (
    <div className="screen stack-on-narrow" id="worldos-screen-settings" data-worldos-testid="settings-root" style={{ height: "100%", display: "grid", gridTemplateColumns: "220px 1fr", gap: 14, padding: 14 }}>

      {/* LEFT — section list */}
      <Panel framed style={{ padding: 22, overflow: "auto" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>Codex of</div>
        <h2 className="h1" style={{ fontSize: 22 }}>Setting</h2>
        <Divider />
        <div role="tablist" aria-label="Settings sections" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {SECTIONS.map((s) => (
            <button key={s.id} type="button" onClick={() => setSection(s.id)} role="tab" aria-selected={section === s.id} data-worldos-testid="settings-tab" data-worldos-tab-id={s.id} style={{
              textAlign: "left",
              padding: "10px 12px",
              fontFamily: "var(--f-display)",
              fontSize: 11,
              letterSpacing: "0.22em",
              textTransform: "uppercase",
              color: section === s.id ? "var(--w-300)" : "var(--ink-700)",
              background: section === s.id ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "transparent",
              boxShadow: section === s.id
                ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)"
                : "inset 0 0 0 1px rgba(140,100,60,0.25)",
              cursor: "pointer",
            }}>{s.label}</button>
          ))}
        </div>

        <Divider />

        <div className="hand muted" style={{ fontSize: 13 }}>
          Press <span style={{ fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--ink-700)" }}>⌘ ,</span> at any time to open this codex.
        </div>
      </Panel>

      {/* RIGHT — section content */}
      <Panel framed style={{ padding: 28, overflow: "auto" }}>
        {section === "native" && (
          <NativeAppSection nativeState={nativeState} refreshNative={refreshNative} />
        )}

        {section === "audio" && (
          <SettingsSection title="The Sound of the Chronicle" eyebrow="Mixing board" ordinal="I.">
            <PreviewBanner>Display-only — there is no audio engine behind the mixing board yet. These controls move but change nothing.</PreviewBanner>
            <Slider preview label="Master" value={audio.master} onChange={(v) => setAudio({ ...audio, master: v })} />
            <Slider preview label="Music" value={audio.music} onChange={(v) => setAudio({ ...audio, music: v })} />
            <Slider preview label="Sound effects" value={audio.sfx} onChange={(v) => setAudio({ ...audio, sfx: v })} />
            <Slider preview label="Ambience" value={audio.ambience} onChange={(v) => setAudio({ ...audio, ambience: v })} />
            <Slider preview label="Voice & narration" value={audio.voice} onChange={(v) => setAudio({ ...audio, voice: v })} />

            <Divider />
            <SectionTitle>Output</SectionTitle>
            <SelectRow preview label="Device" value="System default — MacBook Pro Speakers" options={["System default — MacBook Pro Speakers", "AirPods Pro", "Studio Monitor"]} />
            <SelectRow preview label="Surround mix" value="Stereo" options={["Stereo", "Spatial Audio", "Headphones (HRTF)"]} />
            <Toggle preview label="Duck music during GM narration" value={audio.duckMusic} onChange={(v) => setAudio({ ...audio, duckMusic: v })} />
            <Toggle preview label="Crossfade between scenes" value={audio.crossfade} onChange={(v) => setAudio({ ...audio, crossfade: v })} />
          </SettingsSection>
        )}

        {section === "display" && (
          <SettingsSection title="What the Eye Sees" eyebrow="Lantern & ink" ordinal="II.">
            {/* GENUINELY FUNCTIONAL: drives --ui-scale on <html> (styles.css zooms .window). */}
            <Slider label="UI scale" value={a11y.uiScale} onChange={(v) => applyA11y({ uiScale: v })} min={75} max={150} unit="%" />

            <Divider />
            <SectionTitle>Not yet wired</SectionTitle>
            <PreviewBanner>Display-only — the controls below have no backing yet. UI scale above is live and persists across reloads.</PreviewBanner>
            <Slider preview label="Contrast" value={display.contrast} onChange={(v) => setDisplay({ ...display, contrast: v })} />

            <Divider />
            <SectionTitle>Atmosphere</SectionTitle>
            <Toggle preview label="Candle glow on panels" value={display.candleGlow} onChange={(v) => setDisplay({ ...display, candleGlow: v })} />
            <Toggle preview label="Paper grain texture" value={display.paperGrain} onChange={(v) => setDisplay({ ...display, paperGrain: v })} />
            <Toggle preview label="Edge vignette" value={display.vignette} onChange={(v) => setDisplay({ ...display, vignette: v })} />

            <Divider />
            <SectionTitle>Window</SectionTitle>
            <SelectRow preview label="Mode" value="Windowed" options={["Windowed", "Fullscreen", "Borderless"]} />
            <SelectRow preview label="Frame rate" value="ProMotion — 120 Hz" options={["30 Hz", "60 Hz", "ProMotion — 120 Hz"]} />
            <SelectRow preview label="HDR" value="Off" options={["Off", "Standard", "Aggressive"]} />
          </SettingsSection>
        )}

        {section === "gameplay" && (
          <SettingsSection title="The Manner of Play" eyebrow="Pace & disclosure" ordinal="III.">
            <PreviewBanner>Display-only — these preferences are not yet read by the engine. Pacing, narration and dice behaviour are unaffected.</PreviewBanner>
            <SectionTitle>Auto-save</SectionTitle>
            <Slider preview label="Cadence" value={gameplay.auto} onChange={(v) => setGameplay({ ...gameplay, auto: v })} min={5} max={60} unit=" min" />

            <Divider />
            <SectionTitle>Narration</SectionTitle>
            <Radio
              preview
              value={gameplay.narration}
              onChange={(v) => setGameplay({ ...gameplay, narration: v })}
              options={[
                { value: "terse", label: "Terse", note: "Short and lean. Mostly dice." },
                { value: "balanced", label: "Balanced", note: "Some prose, some mechanics." },
                { value: "florid", label: "Florid", note: "Read it like a novel." },
              ]}
            />

            <Divider />
            <SectionTitle>Dice</SectionTitle>
            <Radio
              preview
              value={gameplay.dice}
              onChange={(v) => setGameplay({ ...gameplay, dice: v })}
              options={[
                { value: "visible", label: "Show every roll", note: "All rolls appear in the chronicle log." },
                { value: "narrative", label: "Hide failures", note: "Only successes and dramatic moments." },
                { value: "blind", label: "GM keeps the dice", note: "Outcomes only. The chronicle decides." },
              ]}
            />

            <Divider />
            <Toggle preview label="Show danger hints in the world" value={gameplay.dangerHints} onChange={(v) => setGameplay({ ...gameplay, dangerHints: v })} />
            <Toggle preview label="Confirm before destructive actions" value={gameplay.confirmDestructive} onChange={(v) => setGameplay({ ...gameplay, confirmDestructive: v })} />
            <Toggle preview label="Permit AI GM to roll for the party" value={gameplay.aiPartyRolls} onChange={(v) => setGameplay({ ...gameplay, aiPartyRolls: v })} />
          </SettingsSection>
        )}

        {section === "controls" && (
          <SettingsSection title="The Player's Hand" eyebrow="Keys & gestures" ordinal="IV.">
            <PreviewBanner>Display-only — these are the fixed default shortcuts; rebinding and gesture options are not yet wired.</PreviewBanner>
            <SectionTitle>Bindings <span style={{ fontSize: 9, opacity: 0.7, letterSpacing: "0.18em" }}>(preview)</span></SectionTitle>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {KEYBINDS.map((kb) => (
                <KeybindRow key={kb.label} kb={kb} />
              ))}
            </div>

            <Divider />
            <SectionTitle>Trackpad</SectionTitle>
            <Toggle preview label="Two-finger scroll the chronicle" value={controls.twoFingerScroll} onChange={(v) => setControls({ ...controls, twoFingerScroll: v })} />
            <Toggle preview label="Pinch to zoom the world map" value={controls.pinchZoom} onChange={(v) => setControls({ ...controls, pinchZoom: v })} />
            <Toggle preview label="Force-touch to inspect items" value={controls.forceTouchInspect} onChange={(v) => setControls({ ...controls, forceTouchInspect: v })} />
          </SettingsSection>
        )}

        {section === "accessibility" && (
          <SettingsSection title="So All May Sit at the Table" eyebrow="Open the door" ordinal="V.">
            {/* GENUINELY FUNCTIONAL: Reduce motion + High-contrast drive data-reduced-motion /
                data-contrast on <html> (real CSS in styles.css) and persist via OpenWorldsA11y. */}
            <Toggle label="Reduce motion (no candle flicker, no fades)" value={a11y.reducedMotion} onChange={(v) => applyA11y({ reducedMotion: v })} />
            <Toggle label="High-contrast UI" value={a11y.highContrast} onChange={(v) => applyA11y({ highContrast: v })} />

            <Divider />
            <SectionTitle>Not yet wired</SectionTitle>
            <PreviewBanner>Display-only — the controls below have no backing yet. Reduce motion and high-contrast above are live and persist across reloads.</PreviewBanner>
            <Toggle preview label="Dyslexic-friendly font for body text" value={accessibility.dyslexic} onChange={(v) => setAccessibility({ ...accessibility, dyslexic: v })} />
            <Toggle preview label="Always show captions for narration" value={accessibility.captions} onChange={(v) => setAccessibility({ ...accessibility, captions: v })} />

            <Divider />
            <SectionTitle>Reading</SectionTitle>
            <SelectRow preview label="Body font" value="Cormorant Garamond" options={["Cormorant Garamond", "Atkinson Hyperlegible", "OpenDyslexic", "System default"]} />
            <Slider preview label="Line spacing" value={50} min={0} max={100} />
            <Toggle preview label="Underline interactive choices" value={accessibility.underlineChoices} onChange={(v) => setAccessibility({ ...accessibility, underlineChoices: v })} />

            <Divider />
            <SectionTitle>Colour</SectionTitle>
            <SelectRow preview label="Colour-blind mode" value="None" options={["None", "Deuteranopia", "Protanopia", "Tritanopia"]} />
          </SettingsSection>
        )}

        {section === "saves" && (
          <SettingsSection title="Anchors in Time" eyebrow="Save & restore" ordinal="VI.">
            {/* Read-only list bound to the real campaign catalog (state.campaigns, fetched from
                /openworlds/campaigns.json by app.jsx). Quicksave/Quickload are wired to the engine
                save lane (the engine is the sole writer); Export streams the snapshot as a download.
                Erase-all stays disabled — destructive deletion is intentionally out of scope. */}
            <div style={{ display: "flex", gap: 8, marginBottom: 18, alignItems: "center" }}>
              <BrassButton size="sm" disabled={!activeCampaignId || !!savesBusy} onClick={saveSlot}>{savesBusy === "save" ? "Saving…" : "Quicksave"}</BrassButton>
              <BrassButton size="sm" tone="ghost" disabled={!activeCampaignId || !!savesBusy} onClick={loadSlot}>{savesBusy === "load" ? "Restoring…" : "Quickload"}</BrassButton>
              <BrassButton size="sm" tone="ghost" disabled={!activeCampaignId || !!savesBusy} onClick={exportChronicle}>{savesBusy === "export" ? "Exporting…" : "Export chronicle…"}</BrassButton>
              <div style={{ flex: 1 }} />
              <BrassButton size="sm" tone="crimson" disabled title="Display-only — erase is not wired; nothing is deleted">Erase all <span style={{ fontSize: 9, opacity: 0.7 }}>(preview)</span></BrassButton>
            </div>
            {campaigns.length > 0 ? (
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                {campaigns.map((c) => (
                  <SaveSlot key={c.id} active={c.current || c.id === state?.activeCampaign} s={{
                    name: c.title,
                    chronicle: c.subtitle || c.region || c.world || "",
                    time: c.lastPlayed || "",
                    auto: false,
                    party: c.partyCount,
                    dayLabel: c.day || c.region || "",
                  }} />
                ))}
              </div>
            ) : (
              <div className="hand muted" style={{ padding: "24px 8px", textAlign: "center", fontSize: 15 }}>
                No saved chronicles yet. Begin a chronicle from the Worlds shelf and it will be anchored here.
              </div>
            )}
          </SettingsSection>
        )}

        {section === "about" && (
          <SettingsSection title="Of This Chronicle Engine" eyebrow="Marginalia" ordinal="VII.">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
              <div>
                <p className="body dropcap">
                  Open Worlds is a chronicle engine for tabletop games — a parchment laid across a Mac window, kept by an attentive but unintrusive scribe. Built by candlelight, intended for long roads and patient evenings.
                </p>
                <Divider />
                <StatLine k="Version" v="1.0.0" />
                <StatLine k="Engine" v="Chronicle II / Scribe-of-roads" />
                <StatLine k="System" v="D&D 5e · Free Form" />
              </div>
              <div>
                <SectionTitle>Acknowledgements</SectionTitle>
                <ul className="body" style={{ paddingLeft: 16, margin: 0 }}>
                  <li>To every Game Master who ever lit a candle and a cigarette at the same table.</li>
                  <li>To the scribes who insist they are not characters — and to the worlds that prove them wrong.</li>
                  <li>To the long roads where this engine first occurred to us, walked by patient evening.</li>
                </ul>
                <Divider />
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {/* The CHANGELOG.md / THIRD_PARTY_NOTICES.md docs live at the repo root, which the
                      viewer's HTTP server does NOT serve (only /openworlds/* assets are reachable,
                      and traversal outside the bundle is blocked — see server.py _openworlds_asset).
                      Pointing at /CHANGELOG.md would 404, so to stay honest we open the canonical
                      source on GitHub in a new tab rather than wiring a dead local link. */}
                  <BrassButton size="sm" tone="ghost" title="Open the changelog on GitHub" onClick={() => window.open("https://github.com/electricsheephq/WorldOS/blob/HEAD/CHANGELOG.md", "_blank", "noopener,noreferrer")}>Patch notes</BrassButton>
                  <BrassButton size="sm" tone="ghost" title="Open the third-party licenses on GitHub" onClick={() => window.open("https://github.com/electricsheephq/WorldOS/blob/HEAD/THIRD_PARTY_NOTICES.md", "_blank", "noopener,noreferrer")}>Licenses</BrassButton>
                  <BrassButton size="sm" tone="ghost" title="Report a bug on GitHub" onClick={() => window.open("https://github.com/electricsheephq/WorldOS/issues/new", "_blank", "noopener,noreferrer")}>Report a bug</BrassButton>
                </div>
              </div>
            </div>
          </SettingsSection>
        )}
      </Panel>
    </div>
  );
}

function NativeAppSection({ nativeState, refreshNative }) {
  const toast = window.useToast ? window.useToast() : (() => {});
  const app = nativeState?.appStatus || {};
  const viewer = app.viewer || {};
  const providers = Array.isArray(nativeState?.providers) ? nativeState.providers : [];
  const dependencies = Array.isArray(nativeState?.dependencies) ? nativeState.dependencies : [];
  const bridgeReady = Boolean(nativeState?.bridge);

  const nativeAction = async (type, payload = {}) => {
    if (!window.OpenWorldsNative?.hasBridge?.()) {
      toast({ kind: "danger", title: "Native bridge unavailable", body: "OpenWorlds is running outside the WorldOS macOS app." });
      return;
    }
    try {
      await window.OpenWorldsNative.request(type, payload);
      await refreshNative?.();
      toast({ kind: "ok", title: "Native action complete", body: type });
    } catch (error) {
      toast({ kind: "danger", title: "Native action failed", body: error?.message || String(error) });
    }
  };

  const startProvider = () => {
    const prefs = app.preferences || {};
    const now = new Date();
    const stamp = now.toISOString().slice(0, 19).replace(/[-:T]/g, "").replace(/^(\d{8})(\d{6})$/, "$1-$2");
    const payload = {
      world: prefs.defaultWorld || app.defaultWorld || "baldurs-gate",
      runId: `play-${stamp}`,
      companions: "",
    };
    const provider = prefs.selectedProvider || app.selectedProvider || "";
    if (provider) payload.provider = provider;
    nativeAction("startProviderSession", payload);
  };

  return (
    <SettingsSection title="WorldOS Native App" eyebrow="Supervisor bridge" ordinal="I.">
      <div role="status" aria-live="polite" data-worldos-testid="provider-status" style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
        <Pill tone={bridgeReady ? "emerald" : "crimson"}>{bridgeReady ? "Wired" : "Unavailable"}</Pill>
        <Pill tone={viewer.status === "running" ? "emerald" : "royal"}>Viewer {viewer.status || "stopped"}</Pill>
        {app.runningProvider && <Pill tone="royal">Provider {app.runningProvider}</Pill>}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <Panel framed style={{ padding: 18 }}>
          <SectionTitle>App Status</SectionTitle>
          <StatLine k="Repo" v={app.repoPath || "unknown"} />
          <StatLine k="State" v={app.stateDir || "default"} />
          <StatLine k="Port" v={viewer.port || app.preferredPort || "auto"} />
          <StatLine k="World" v={app.defaultWorld || "baldurs-gate"} />
          <StatLine k="Last error" v={app.lastError || nativeState?.error || "none"} />
        </Panel>

        <Panel framed style={{ padding: 18 }}>
          <SectionTitle>Native Actions</SectionTitle>
          <div data-worldos-testid="provider-controls" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            <BrassButton size="sm" title="Start the local viewer process that serves this UI" onClick={() => nativeAction("startViewer")} testId="native-start-viewer">Start Viewer</BrassButton>
            <BrassButton size="sm" tone="ghost" title="Stop the local viewer process serving this UI" onClick={() => nativeAction("stopViewer")} testId="native-stop-viewer">Stop Viewer</BrassButton>
            <BrassButton size="sm" title="Launch a provider session for the default world (e.g. Claude)" onClick={startProvider} testId="provider-start">Start Provider</BrassButton>
            <BrassButton size="sm" tone="ghost" title="Stop the running provider session" onClick={() => nativeAction("stopProvider")} testId="provider-stop">Stop Provider</BrassButton>
            <BrassButton size="sm" tone="ghost" title="Copy app status and recent diagnostics to the clipboard" onClick={() => nativeAction("copyDiagnostics")} testId="native-copy-diagnostics">Copy Diagnostics</BrassButton>
            <BrassButton size="sm" tone="ghost" title="Open the fallback debug dashboard in your browser" onClick={() => nativeAction("openFallbackDashboard")} testId="native-debug-dashboard">Debug Dashboard</BrassButton>
          </div>
          <p className="body-sm muted" style={{ marginTop: 12 }}>
            Native actions supervise local processes only. Game intent still travels through the existing engine/player move lane.
          </p>
        </Panel>
      </div>

      <Divider />
      <SectionTitle>Providers</SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10 }}>
        {providers.map((p) => (
          <Panel key={p.kind} framed style={{ padding: 16 }} className="provider-card">
            <div data-worldos-testid="provider-card" data-worldos-provider-id={p.kind || undefined}>
            <div className="eyebrow" style={{ color: "var(--crimson)" }}>{p.displayName || p.kind}</div>
            <h3 style={{ margin: "4px 0 8px", fontFamily: "var(--f-display)", letterSpacing: "0.08em" }}>{p.availability}</h3>
            <div className="body-sm muted">{p.detail}</div>
            </div>
          </Panel>
        ))}
        {!providers.length && (
          <div className="body-sm muted">Provider status is unavailable until the native bridge is connected.</div>
        )}
      </div>

      <Divider />
      <SectionTitle>Dependencies</SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8 }}>
        {dependencies.map((d) => (
          <div key={d.command} className={`capability-badge ${d.installed ? "emerald" : "crimson"}`} style={{ justifyContent: "space-between" }}>
            <span>{d.command}</span>
            <span className="capability-source">{d.installed ? "ready" : "missing"}</span>
          </div>
        ))}
      </div>
    </SettingsSection>
  );
}

/* Honest-UI marker, matching screen-merchant.jsx / screen-forge.jsx: a brass "Preview" badge
   plus a one-line explanation, shown above any section whose controls are decorative. */
function PreviewBanner({ children }) {
  const badge = { label: "Preview", tone: "muted", detail: typeof children === "string" ? children : "Display-only — not yet wired." };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 14px", marginBottom: 16, background: "rgba(80,50,20,0.18)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.45)", borderRadius: 2 }}>
      <CapabilityBadge capability={badge} nativeStatus={null} />
      <span className="hand muted" style={{ fontSize: 12 }}>{children}</span>
    </div>
  );
}

/* Small "(preview)" tag appended to a decorative control's label, matching the merchant/forge
   convention (`<span style={{ fontSize: 9, opacity: 0.7 }}>(preview)</span>`). */
const PREVIEW_TITLE = "Display-only — not yet saved";
function PreviewTag() {
  return <span style={{ fontSize: 9, opacity: 0.7, letterSpacing: "0.12em" }}> (preview)</span>;
}

function SettingsSection({ title, eyebrow, ordinal, children }) {
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--crimson)" }}>{eyebrow}</div>
      <h1 className="h1" style={{ marginTop: 4 }}>
        {ordinal && <span style={{ fontFamily: "var(--f-hand)", fontStyle: "italic", color: "var(--crimson)", fontSize: 26, marginRight: 12 }}>{ordinal}</span>}
        {title}
      </h1>
      <Divider />
      <div>{children}</div>
    </div>
  );
}

function Slider({ label, value, onChange, min = 0, max = 100, unit = "", preview = false }) {
  return (
    <div style={{ marginBottom: 12, opacity: preview ? 0.6 : 1 }} title={preview ? PREVIEW_TITLE : undefined}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-700)" }}>
          {label}{preview && <PreviewTag />}
        </span>
        <span style={{ fontFamily: "var(--f-mono)", fontSize: 12, color: "var(--ink-700)" }}>
          {value}{unit}
        </span>
      </div>
      <div style={{
        position: "relative", marginTop: 6, height: 22,
        display: "flex", alignItems: "center",
      }}>
        <div style={{
          position: "absolute", left: 0, right: 0, height: 6,
          background: "rgba(0,0,0,0.18)",
          boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.45), inset 0 1px 1px rgba(0,0,0,0.2)",
        }} />
        <div style={{
          position: "absolute", left: 0, height: 6,
          width: `${((value - min) / (max - min)) * 100}%`,
          background: "linear-gradient(180deg, var(--b-200), var(--b-500))",
          boxShadow: "inset 0 1px 0 rgba(255,250,220,0.6)",
        }} />
        <input
          type="range" min={min} max={max} value={value}
          disabled={preview}
          aria-label={label}
          onChange={(e) => onChange && onChange(Number(e.target.value))}
          style={{
            position: "absolute", inset: 0, width: "100%", height: "100%",
            opacity: 0, cursor: preview ? "not-allowed" : "pointer",
          }}
        />
        <div style={{
          position: "absolute",
          left: `calc(${((value - min) / (max - min)) * 100}% - 8px)`,
          width: 16, height: 16,
          borderRadius: "50%",
          background: "radial-gradient(circle at 30% 30%, var(--b-100), var(--b-400) 60%, var(--b-600))",
          boxShadow: "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.8), 0 2px 3px rgba(0,0,0,0.3)",
          pointerEvents: "none",
        }} />
      </div>
    </div>
  );
}

function Toggle({ label, value, onChange, preview = false }) {
  const checked = Boolean(value);
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-disabled={preview || undefined}
      disabled={preview}
      title={preview ? PREVIEW_TITLE : undefined}
      onClick={() => { if (!preview && onChange) onChange(!checked); }}
      style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        width: "100%",
        padding: "10px 0",
        background: "transparent",
        borderBottom: "1px solid rgba(140,100,60,0.2)",
        cursor: preview ? "not-allowed" : "pointer",
        opacity: preview ? 0.6 : 1,
        textAlign: "left",
      }}>
      <span className="body" style={{ color: "var(--ink-800)" }}>{label}{preview && <PreviewTag />}</span>
      <span style={{
        width: 44, height: 22,
        background: checked ? "linear-gradient(180deg, var(--b-200), var(--b-500))" : "rgba(0,0,0,0.18)",
        boxShadow: checked
          ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.5)"
          : "inset 0 0 0 1px rgba(80,50,20,0.45)",
        position: "relative",
        borderRadius: 12,
        transition: "all 180ms",
      }}>
        <span style={{
          position: "absolute", top: 2, left: checked ? 24 : 2,
          width: 18, height: 18, borderRadius: "50%",
          background: checked
            ? "radial-gradient(circle at 30% 30%, var(--p-100), var(--p-400))"
            : "radial-gradient(circle at 30% 30%, var(--p-200), var(--ink-600))",
          boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.6), 0 1px 2px rgba(0,0,0,0.3)",
          transition: "all 180ms",
        }} />
      </span>
    </button>
  );
}

function SelectRow({ label, value, options, preview = false }) {
  const [open, setOpen] = React.useState(false);
  const [current, setCurrent] = React.useState(value);
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "180px 1fr", gap: 16, alignItems: "center",
      padding: "8px 0",
      borderBottom: "1px solid rgba(140,100,60,0.2)",
      opacity: preview ? 0.6 : 1,
    }} title={preview ? PREVIEW_TITLE : undefined}>
      <span style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-700)" }}>
        {label}{preview && <PreviewTag />}
      </span>
      <div style={{ position: "relative" }}>
        <button disabled={preview} onClick={() => { if (!preview) setOpen(!open); }} style={{
          width: "100%",
          padding: "8px 12px",
          background: "rgba(255,250,230,0.5)",
          boxShadow: "inset 0 0 0 1px var(--b-500)",
          fontFamily: "var(--f-body)",
          fontSize: 15,
          color: "var(--ink-800)",
          textAlign: "left",
          display: "flex", justifyContent: "space-between", alignItems: "center",
          cursor: preview ? "not-allowed" : "pointer",
        }}>
          <span>{current}</span>
          <span style={{ color: "var(--b-500)", fontSize: 10 }}>▾</span>
        </button>
        {open && !preview && (
          <div style={{
            position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0,
            background: "linear-gradient(180deg, var(--p-100), var(--p-200))",
            boxShadow: "inset 0 0 0 1px var(--b-500), 0 8px 20px rgba(0,0,0,0.3)",
            zIndex: 10,
          }}>
            {options.map((o) => (
              <button key={o} onClick={() => { setCurrent(o); setOpen(false); }} style={{
                width: "100%",
                padding: "8px 12px",
                background: o === current ? "rgba(176,141,87,0.18)" : "transparent",
                fontFamily: "var(--f-body)",
                fontSize: 15,
                color: "var(--ink-800)",
                textAlign: "left",
                cursor: "pointer",
                borderBottom: "1px solid rgba(140,100,60,0.18)",
              }}>{o}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Radio({ value, onChange, options, preview = false }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, opacity: preview ? 0.6 : 1 }} title={preview ? PREVIEW_TITLE : undefined}>
      {options.map((o) => (
        <button key={o.value} disabled={preview} onClick={() => { if (!preview && onChange) onChange(o.value); }} style={{
          padding: "12px 14px",
          textAlign: "left",
          background: value === o.value
            ? "linear-gradient(180deg, var(--p-100), var(--p-200))"
            : "rgba(176,141,87,0.06)",
          boxShadow: value === o.value
            ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
            : "inset 0 0 0 1px rgba(140,100,60,0.3)",
          cursor: preview ? "not-allowed" : "pointer",
          transition: "all 140ms",
        }}>
          <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--ink-900)" }}>
            {o.label}
          </div>
          {o.note && <div className="hand muted" style={{ fontSize: 12, marginTop: 4 }}>{o.note}</div>}
        </button>
      ))}
    </div>
  );
}

const KEYBINDS = [
  { label: "Open Stash", key: "I" },
  { label: "Open Journal", key: "J" },
  { label: "Open Map", key: "M" },
  { label: "Open Heroes", key: "C" },
  { label: "Quicksave", key: "⌘ S" },
  { label: "Quickload", key: "⌘ L" },
  { label: "Pause / unpause", key: "Space" },
  { label: "Toggle Tweaks", key: "⌘ ;" },
  { label: "Roll d20", key: "R" },
  { label: "Camp", key: "K" },
  { label: "Cycle hero", key: "Tab" },
  { label: "Centre on party", key: "Home" },
];

function KeybindRow({ kb }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "8px 12px",
      background: "rgba(176,141,87,0.06)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
    }}>
      <span className="body" style={{ color: "var(--ink-800)" }}>{kb.label}</span>
      <span style={{
        fontFamily: "var(--f-mono)",
        fontSize: 11,
        padding: "3px 10px",
        background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
        boxShadow: "inset 0 0 0 1px var(--b-500), 0 1px 0 var(--p-100), 0 2px 2px rgba(0,0,0,0.2)",
        color: "var(--ink-800)",
        minWidth: 30,
        textAlign: "center",
      }}>{kb.key}</span>
    </div>
  );
}

function SaveSlot({ s, active }) {
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "100px 1fr", gap: 14,
      padding: 12,
      background: active ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "rgba(176,141,87,0.06)",
      boxShadow: active
        ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
        : "inset 0 0 0 1px rgba(140,100,60,0.3)",
    }}>
      <Placeholder label="scene · save thumbnail" w={100} h={70} framed />
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
          <span style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.06em", color: "var(--ink-900)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {s.name}
          </span>
          {s.auto && <Pill>Auto</Pill>}
        </div>
        <div className="hand" style={{ fontSize: 12, color: "var(--ink-600)" }}>{s.chronicle}</div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 6 }}>
          <span className="body-sm muted">{s.dayLabel} · {s.party} heroes</span>
          <span style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-600)" }}>{s.time}</span>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ScreenSettings, SettingsSection, Slider, Toggle, SelectRow, Radio, KEYBINDS, KeybindRow, SaveSlot });
