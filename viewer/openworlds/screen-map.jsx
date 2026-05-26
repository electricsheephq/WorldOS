/* Screen: World Map - engine-owned atlas and strategic read model */

function atlasSurfaceFromCampaign(activeCampaign, state) {
  const campaignId = activeCampaign?.campaign_id || state?.activeCampaign || activeCampaign?.id || "";
  const params = new URLSearchParams();
  if (campaignId) params.set("campaign", campaignId);
  if (activeCampaign?.source) params.set("source", activeCampaign.source);
  if (activeCampaign?.runId) params.set("run", activeCampaign.runId);
  const query = params.toString();
  return query ? `?${query}` : "";
}

function ScreenMap({ onNavigate, state, campMode, setCampMode }) {
  const campaigns = Array.isArray(state?.campaigns) ? state.campaigns : [];
  const activeCampaign =
    campaigns.find((c) => c.id === state?.activeCampaign) ||
    campaigns[0] ||
    {};
  const surfaceQuery = window.atlasSurfaceFromCampaign(activeCampaign, state);
  const campaignId = activeCampaign?.campaign_id || state?.activeCampaign || activeCampaign?.id || "";
  const [surface, setSurface] = React.useState(null);
  const [surfaceStatus, setSurfaceStatus] = React.useState("loading");
  const [selectedId, setSelectedId] = React.useState("");
  const [time, setTime] = React.useState("dusk");
  const [busyTravel, setBusyTravel] = React.useState("");
  const [talkPartner, setTalkPartner] = React.useState(null);
  const toast = window.useToast ? window.useToast() : (() => {});

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    try {
      const response = await fetch("/atlas-surface" + surfaceQuery, { cache: "no-store" });
      if (!response.ok) throw new Error(`atlas surface ${response.status}`);
      const payload = await response.json();
      if (isCancelled()) return;
      setSurface(payload);
      setSurfaceStatus("ready");
      const label = (payload.dayLabel || "").toLowerCase();
      if (label.includes("night")) setTime("night");
      else if (label.includes("dawn") || label.includes("morning")) setTime("dawn");
      else if (label.includes("dusk") || label.includes("evening")) setTime("dusk");
      else if (label.includes("day") || label.includes("noon")) setTime("day");
    } catch (error) {
      if (isCancelled()) return;
      setSurfaceStatus(error?.message || "unavailable");
    }
  }, [surfaceQuery]);

  React.useEffect(() => {
    let cancelled = false;
    let timer = null;
    const guardedLoad = async () => {
      if (cancelled) return;
      await loadSurface(() => cancelled);
    };
    const stopPolling = () => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    const startPolling = () => {
      if (timer === null) timer = window.setInterval(guardedLoad, 7000);
    };
    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        guardedLoad();
        startPolling();
      } else {
        stopPolling();
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    handleVisibility();
    return () => {
      cancelled = true;
      stopPolling();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [loadSurface]);

  const locations = Array.isArray(surface?.known_locations) ? surface.known_locations : [];
  const edges = Array.isArray(surface?.edges) ? surface.edges : [];
  const travelOptions = Array.isArray(surface?.travel_options) ? surface.travel_options : [];
  const quests = Array.isArray(surface?.quest_markers) ? surface.quest_markers : [];
  const clocks = Array.isArray(surface?.strategic_clocks) ? surface.strategic_clocks : [];
  const projects = Array.isArray(surface?.downtime_projects) ? surface.downtime_projects : [];
  const controls = Array.isArray(surface?.region_control) ? surface.region_control : [];
  const currentLocation = surface?.current_location || {};
  const selected =
    locations.find((l) => l.id === selectedId) ||
    locations.find((l) => l.id === currentLocation.id) ||
    locations[0] ||
    null;
  const selectedTravel = selected ? travelOptions.find((t) => t.to === selected.id) : null;
  const canCamp = Boolean(surface?.camp_available);
  const canAct = Boolean(surface?.can_act);

  React.useEffect(() => {
    if (!selectedId || !locations.some((l) => l.id === selectedId)) {
      setSelectedId(currentLocation.id || locations[0]?.id || "");
    }
  }, [currentLocation.id, locations, selectedId]);

  const postTravel = async (option) => {
    if (!option?.available || !option?.move || !canAct) {
      toast({
        kind: "danger",
        eyebrow: "Travel",
        title: option?.name ? `Cannot travel to ${option.name}` : "Travel unavailable",
        body: option?.disabled_reason || "This atlas is read-only.",
      });
      return;
    }
    setBusyTravel(option.to);
    try {
      const response = await fetch("/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...option.move, campaign: surface?.campaign_id || campaignId }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.reason || `move ${response.status}`);
      }
      toast({ kind: "quest", title: "Travel intent sent", body: option.name || option.to });
      onNavigate("table");
    } catch (error) {
      toast({ kind: "danger", title: "Move not sent", body: error?.message || "The viewer could not reach /move." });
    } finally {
      setBusyTravel("");
    }
  };

  const beginRest = () => {
    toast({
      kind: "rest",
      eyebrow: "Camp",
      title: canCamp ? "Camp is visible" : "Camp unavailable",
      body: canCamp
        ? "The atlas can show camp mode, but rest resolution remains engine-owned."
        : "The current location is not marked as a camp or rest point.",
    });
  };

  const locationQuests = selected ? quests.filter((q) => !q.location_id || q.location_id === selected.id) : quests;
  const locationClocks = selected ? clocks.filter((c) => !c.location_id || c.location_id === selected.id) : clocks;
  const locationProjects = selected ? projects.filter((p) => !p.location_id || p.location_id === selected.id) : projects;
  const locationControl = selected ? controls.find((c) => c.location_id === selected.id) : null;
  const calendar = surface?.calendar?.available ? surface.calendar : null;
  const calendarMoon = Array.isArray(calendar?.moons) ? calendar.moons[0] : null;
  const calendarDetail = calendar ? [calendar.season, calendarMoon ? `${calendarMoon.name}: ${calendarMoon.phase}` : ""].filter(Boolean).join(" · ") : "";

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "minmax(0, 1fr) 340px", gap: 14, padding: 14 }}>
      <Panel framed style={{ padding: 18, position: "relative", display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10, position: "relative", zIndex: 3, flex: "0 0 auto", gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <div className="eyebrow" style={{ color: "var(--crimson)" }}>{surface?.world || "Open Worlds"}</div>
            <h1 className="h1" style={{ fontSize: 24, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {campMode ? "Camp" : "World Atlas"}
            </h1>
            <div className="body-sm" style={{ color: "var(--ink-700)", marginTop: 3 }}>
              {surface?.dayLabel || surfaceStatus}
            </div>
            {calendarDetail && <div className="body-xs" style={{ color: "var(--b-700)", marginTop: 3 }}>{calendarDetail}</div>}
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
            {!campMode && ["dawn", "day", "dusk", "night"].map((t) => (
              <button key={t} onClick={() => setTime(t)} className="pill" style={{
                cursor: "pointer",
                background: time === t ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(176,141,87,0.1)",
                color: time === t ? "var(--w-300)" : "var(--ink-700)",
                boxShadow: time === t ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)" : "inset 0 0 0 1px rgba(140,100,60,0.3)",
              }}>{t}</button>
            ))}
            <BrassButton size="sm" tone={campMode ? "" : "ghost"} onClick={() => setCampMode && setCampMode(!campMode)} disabled={!canCamp}>
              {campMode ? "Camped" : "Make Camp"}
            </BrassButton>
            <BrassButton size="sm" tone="ghost" onClick={() => loadSurface()}>Refresh</BrassButton>
          </div>
        </div>

        {!campMode && selected && (
          <Img
            scope={selected.id}
            label={selected.name}
            w="100%"
            h={90}
            framed
            fit="cover"
            style={{ marginBottom: 8, flex: "0 0 auto" }}
          />
        )}

        <div style={{ position: "relative", flex: "1 1 auto", minHeight: 0 }}>
          {campMode && window.CampSidebar ? (
            <div style={{ position: "absolute", inset: 0, overflow: "auto" }}>
              <window.CampSidebar
                state={state}
                onExit={() => { setCampMode && setCampMode(false); setTalkPartner(null); }}
                onBeginRest={beginRest}
                onTalk={setTalkPartner}
                talkPartner={talkPartner}
              />
            </div>
          ) : (
            <AtlasMap locations={locations} edges={edges} selected={selected} time={time} onSelect={setSelectedId} />
          )}
        </div>

        <div style={{
          marginTop: 10,
          display: "flex",
          gap: 6,
          padding: 8,
          background: "linear-gradient(180deg, var(--w-100), var(--w-300))",
          boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 0 2px var(--b-500), inset 0 0 0 3px var(--w-200)",
          zIndex: 3,
          flex: "0 0 auto",
          alignItems: "center",
        }}>
          <Pill tone={surface?.is_live_view ? "emerald" : "crimson"} dot>{surface?.is_live_view ? "Live" : "Read-only"}</Pill>
          <Pill dot>{locations.length} known</Pill>
          <Pill dot>{clocks.filter((c) => c.urgent).length + projects.filter((p) => p.urgent).length} urgent</Pill>
          <div style={{ flex: 1 }} />
          <div style={{ color: "var(--b-200)", fontFamily: "var(--f-display)", fontSize: 10, letterSpacing: "0.18em", textTransform: "uppercase", padding: "0 8px" }}>
            Last strategic tick: {surface?.last_world_tick || "none"}
          </div>
        </div>
      </Panel>

      <AtlasSidebar
        selected={selected}
        travel={selectedTravel}
        currentId={currentLocation.id}
        busyTravel={busyTravel}
        canAct={canAct}
        quests={locationQuests}
        clocks={locationClocks}
        projects={locationProjects}
        control={locationControl}
        allLocations={locations}
        onSelect={setSelectedId}
        onTravel={postTravel}
        onMark={(loc) => toast({ title: "Local mark only", body: loc?.name ? `${loc.name} marked in this view.` : "Pick a location first." })}
      />
    </div>
  );
}

function AtlasMap({ locations, edges, selected, time, onSelect }) {
  return (
    <div style={{ position: "relative", height: "100%" }}>
      <div style={{
        position: "absolute", inset: 0,
        background:
          `radial-gradient(ellipse at 30% 25%, rgba(120, 80, 30, 0.25), transparent 50%),
           radial-gradient(ellipse at 75% 70%, rgba(100, 60, 20, 0.3), transparent 55%),
           radial-gradient(ellipse at 15% 80%, rgba(160, 110, 60, 0.18), transparent 40%),
           linear-gradient(135deg, #c8a878 0%, #b89868 40%, #a08055 100%)`,
        boxShadow: "inset 0 0 80px rgba(60, 30, 10, 0.6)",
        overflow: "hidden",
      }}>
        <svg width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, opacity: 0.72 }}>
          <defs>
            <pattern id="atlasForest" x="0" y="0" width="4" height="4" patternUnits="userSpaceOnUse">
              <path d="M2 .7 L2.8 2.8 L1.2 2.8 Z" fill="rgba(40,60,30,0.48)" />
            </pattern>
            <pattern id="atlasHills" x="0" y="0" width="8" height="4" patternUnits="userSpaceOnUse">
              <path d="M0 3.5 Q 2 1.5 4 3.5 T 8 3.5" fill="none" stroke="rgba(90,50,20,0.35)" strokeWidth=".25" />
            </pattern>
          </defs>
          <path d="M8 15 Q15 12 22 16 T38 18 Q42 32 36 40 T20 45 Q10 42 8 30 Z" fill="url(#atlasForest)" />
          <path d="M58 12 Q68 9 76 16 T94 22 Q95 37 83 44 T64 43 Q55 37 58 22 Z" fill="url(#atlasForest)" opacity="0.78" />
          <rect x="62" y="62" width="32" height="14" fill="url(#atlasHills)" opacity="0.72" />
          <path d="M-2 38 Q20 40 36 46 T70 54 Q85 60 102 64" stroke="rgba(60,100,130,0.55)" strokeWidth="1.5" fill="none" />
          {edges.map((edge, i) => {
            const from = locations.find((l) => l.id === edge.from);
            const to = locations.find((l) => l.id === edge.to);
            if (!from || !to) return null;
            return (
              <line key={`${edge.from}-${edge.to}-${i}`}
                x1={from.x} y1={from.y}
                x2={to.x} y2={to.y}
                stroke="rgba(60,30,10,0.48)" strokeWidth="0.45" strokeDasharray="1 1" />
            );
          })}
          <g transform="translate(89, 86)" opacity="0.85">
            <circle r="6" fill="none" stroke="rgba(60,30,10,0.56)" strokeWidth=".35" />
            <path d="M0 -5.5 L.7 0 L0 5.5 L-.7 0 Z" fill="rgba(120,30,30,0.7)" />
            <path d="M-5.5 0 L0 -.7 L5.5 0 L0 .7 Z" fill="rgba(60,30,10,0.7)" />
            <text y="-7" textAnchor="middle" fontFamily="Cinzel" fontSize="2" fill="rgba(60,30,10,0.9)">N</text>
          </g>
          <text x="50" y="12" textAnchor="middle" fontFamily="Cinzel" fontSize="5" fill="rgba(60,30,10,0.24)" letterSpacing="1.5">OPEN WORLDS ATLAS</text>
        </svg>

        <div style={{
          position: "absolute", inset: 0,
          background: time === "night"
            ? "linear-gradient(180deg, rgba(20, 30, 60, 0.55), rgba(20, 20, 40, 0.65))"
            : time === "dusk"
            ? "linear-gradient(180deg, rgba(80, 30, 20, 0.18), rgba(40, 20, 40, 0.25))"
            : time === "dawn"
            ? "linear-gradient(180deg, rgba(255, 200, 120, 0.18), rgba(255, 150, 80, 0.12))"
            : "transparent",
          pointerEvents: "none",
          transition: "background 400ms",
        }} />

        {locations.map((loc) => (
          <button
            key={loc.id}
            onClick={() => onSelect(loc.id)}
            style={{
              position: "absolute",
              left: `${loc.x}%`,
              top: `${loc.y}%`,
              transform: "translate(-50%, -100%)",
              background: "none",
              cursor: "pointer",
              padding: 0,
            }}
          >
            <LocationPin loc={loc} selected={selected?.id === loc.id} />
          </button>
        ))}

        {!locations.length && (
          <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>
            <div style={{ textAlign: "center", maxWidth: 320 }}>
              <div className="eyebrow" style={{ color: "var(--crimson)" }}>No atlas data</div>
              <h2 className="h1" style={{ fontSize: 22, marginTop: 6 }}>The map has not been discovered</h2>
              <p className="body-sm" style={{ color: "var(--ink-700)", marginTop: 8 }}>Known locations will appear here once the engine snapshot includes them.</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function AtlasSidebar({ selected, travel, currentId, busyTravel, canAct, quests, clocks, projects, control, allLocations, onSelect, onTravel, onMark }) {
  const travelDisabled = !travel?.available || !canAct || Boolean(busyTravel) || selected?.id === currentId;
  const travelReason = selected?.id === currentId ? "current location" : (travel?.disabled_reason || "no engine-backed route");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
      <Panel framed style={{ padding: 22 }}>
        {selected ? (
          <>
            <div className="eyebrow" style={{ color: "var(--crimson)" }}>{selected.region || "Unknown reach"}</div>
            <h2 className="h1" style={{ fontSize: 22 }}>{selected.name}</h2>
            <div className="hand" style={{ fontSize: 14, marginTop: 2 }}>
              {selected.current ? "Current location" : (travel?.minutes ? `${travel.minutes} minutes away` : "Known location")}
            </div>

            <Divider />
            <Img scope={selected.id} label={selected.name} w="100%" h={118} framed />
            <p className="body dropcap" style={{ marginTop: 12, fontSize: 15 }}>
              {selected.description || "No public description has been recorded for this place yet."}
            </p>

            <Divider />
            <div className="tag-row">
              {(selected.tags || []).length
                ? selected.tags.map((t) => <Pill key={t} tone={t === "danger" ? "crimson" : t === "rest" ? "emerald" : ""}>{t}</Pill>)
                : <Pill>known</Pill>}
            </div>

            {control && (
              <div style={{ marginTop: 12, display: "flex", gap: 6, flexWrap: "wrap" }}>
                <Pill tone="emerald">Control: {control.controller || "unclaimed"}</Pill>
                <Pill>Stability {control.stability}</Pill>
                <Pill tone={control.unrest > 50 ? "crimson" : ""}>Unrest {control.unrest}</Pill>
              </div>
            )}

            <div style={{ display: "flex", gap: 6, marginTop: 18 }}>
            <BrassButton disabled={travelDisabled} onClick={() => onTravel(travel)}>
                <window.OpenWorldsIcon id="atlas.travel" size={14} /> {busyTravel === selected.id ? "Sending..." : "Travel here"}
              </BrassButton>
              <BrassButton tone="ghost" size="sm" onClick={() => onMark(selected)}><window.OpenWorldsIcon id="quest.scroll" size={13} /> Mark</BrassButton>
            </div>
            {travelDisabled && (
              <div className="body-sm" style={{ color: "var(--ink-600)", marginTop: 8 }}>{travelReason}</div>
            )}
          </>
        ) : <div className="muted">Pick a place upon the map.</div>}
      </Panel>

      <Panel framed style={{ padding: 18, flex: 1, overflow: "auto" }}>
        <SectionTitle>Strategic Context</SectionTitle>
        <StrategicList label="Quests" items={quests} empty="No active quest markers here." render={(q) => (
          <ContextRow key={q.id} title={q.title} meta={q.objective || q.status || "active"} />
        )} />
        <StrategicList label="Clocks" items={clocks} empty="No strategic clocks here." render={(c) => (
          <ContextRow key={c.id} title={c.title} meta={`${c.kind} - ${c.progress}/${c.target}`} urgent={c.urgent} />
        )} />
        <StrategicList label="Projects" items={projects} empty="No downtime projects here." render={(p) => (
          <ContextRow key={p.id} title={p.title} meta={`${p.status} - ${p.progress_days}/${p.duration_days} days`} urgent={p.urgent} />
        )} />

        <Divider />
        <SectionTitle>Discovered</SectionTitle>
        <div className="body-sm" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {allLocations.map((loc) => (
            <button key={loc.id} onClick={() => onSelect(loc.id)} style={{
              display: "flex", justifyContent: "space-between", textAlign: "left",
              padding: "8px 12px", cursor: "pointer",
              background: selected?.id === loc.id ? "rgba(176,141,87,0.18)" : "transparent",
              boxShadow: "inset 0 -1px 0 rgba(140,100,60,0.2)",
            }}>
              <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.08em", color: "var(--ink-900)" }}>{loc.name}</span>
              <span className="muted" style={{ fontFamily: "var(--f-mono)", fontSize: 10 }}>{loc.current ? "here" : loc.region}</span>
            </button>
          ))}
        </div>
      </Panel>
    </div>
  );
}

function StrategicList({ label, items, empty, render }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div className="eyebrow" style={{ color: "var(--ink-600)", marginBottom: 6 }}>{label}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        {items.length ? items.map(render) : <div className="body-sm" style={{ color: "var(--ink-600)" }}>{empty}</div>}
      </div>
    </div>
  );
}

function ContextRow({ title, meta, urgent }) {
  return (
    <div style={{
      padding: "8px 10px",
      background: urgent ? "rgba(120,32,32,0.12)" : "rgba(176,141,87,0.08)",
      boxShadow: urgent ? "inset 0 0 0 1px rgba(120,32,32,0.22)" : "inset 0 0 0 1px rgba(140,100,60,0.18)",
    }}>
      <div style={{ fontFamily: "var(--f-display)", fontSize: 11, color: urgent ? "var(--crimson)" : "var(--ink-900)", letterSpacing: "0.08em" }}>{title}</div>
      {meta && <div className="body-sm" style={{ color: "var(--ink-600)", marginTop: 2 }}>{meta}</div>}
    </div>
  );
}

function LocationPin({ loc, selected }) {
  const isCurrent = loc.current;
  const isVisited = loc.visited;
  const [hover, setHover] = React.useState(false);

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4, position: "relative" }}
    >
      <div style={{
        position: "relative",
        padding: "4px 14px 4px",
        background: selected
          ? "linear-gradient(180deg, var(--crimson) 0%, #5a1414 100%)"
          : isCurrent
          ? "linear-gradient(180deg, var(--royal), var(--royal-deep))"
          : "linear-gradient(180deg, var(--p-100), var(--p-300))",
        color: selected || isCurrent ? "var(--p-100)" : "var(--ink-800)",
        fontFamily: "var(--f-display)",
        fontSize: 10,
        letterSpacing: "0.12em",
        textTransform: "uppercase",
        boxShadow: selected
          ? "inset 0 0 0 1px #3a0e0e, 0 0 16px -2px rgba(244, 100, 100, 0.6)"
          : "inset 0 0 0 1px var(--b-500), 0 2px 4px rgba(0,0,0,0.4)",
        whiteSpace: "nowrap",
      }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          <window.OpenWorldsIcon id={loc.current ? "atlas.travel" : (loc.tags || []).includes("rest") ? "camp.rest" : "settlement.tavern"} size={11} />
          {loc.name}
        </span>
      </div>
      <div style={{
        width: 12, height: 12, borderRadius: "50%",
        background: isCurrent
          ? "radial-gradient(circle at 30% 30%, var(--gold-glow), var(--b-500))"
          : isVisited
          ? "radial-gradient(circle at 30% 30%, var(--b-200), var(--b-500))"
          : "radial-gradient(circle at 30% 30%, var(--p-300), var(--ink-700))",
        boxShadow: isCurrent
          ? "0 0 0 1px var(--w-500), 0 0 20px rgba(244, 210, 123, 0.8)"
          : "0 0 0 1px var(--w-500)",
        animation: isCurrent ? "flicker 2.4s ease-in-out infinite" : "none",
      }} />

      {hover && !selected && (
        <div style={{
          position: "absolute",
          left: "50%", top: "calc(100% + 14px)",
          transform: "translateX(-50%)",
          width: 220,
          background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
          boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400), 0 8px 20px rgba(0,0,0,0.5)",
          padding: 12,
          zIndex: 50,
          pointerEvents: "none",
          animation: "tooltip-in 140ms ease both",
        }}>
          <Img scope={loc.id} label={loc.name} w="100%" h={70} framed fit="cover" />
          <div className="eyebrow" style={{ color: "var(--crimson)", marginTop: 8, fontSize: 9 }}>
            {loc.region || "Unknown reach"}
          </div>
          <div style={{ fontFamily: "var(--f-display)", fontSize: 12, color: "var(--ink-900)", letterSpacing: "0.06em", marginTop: 2 }}>
            {loc.name}
          </div>
          <div className="hand" style={{ fontSize: 12, color: "var(--ink-600)", marginTop: 2 }}>
            {loc.current ? "Current location" : "Known route"}
          </div>
          {loc.tags && loc.tags.length > 0 && (
            <div style={{ display: "flex", gap: 4, marginTop: 6, flexWrap: "wrap" }}>
              {loc.tags.slice(0, 3).map((t) => <Pill key={t} tone={t === "danger" ? "crimson" : t === "rest" ? "emerald" : ""}>{t}</Pill>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { ScreenMap, LocationPin, atlasSurfaceFromCampaign });
