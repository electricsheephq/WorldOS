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

// Compact label for a map node so dense clusters stay legible. Drops a leading article,
// keeps the most distinctive words, and caps the length — the full name still shows for
// the selected / current / hovered node and in the hover card. Pure presentation; never
// changes the layout or the engine's `name`.
function atlasShortLabel(name) {
  const full = (name || "").trim();
  if (!full) return "";
  const dropArticle = (s) => s.replace(/^(?:the|a|an)\s+/i, "").trim();
  // District / qualified names ("Baldur's Gate — Lower Market", "Citadel: Vault") carry
  // the distinctive part AFTER the separator — keep that so the chip reads "Lower Market".
  let candidate = full;
  const sep = full.split(/\s*[—–\-:·|]\s+/);
  if (sep.length > 1) {
    const tail = sep[sep.length - 1].trim();
    if (tail) candidate = tail;
  }
  candidate = dropArticle(candidate);
  if (candidate.length <= 14) return candidate;
  // "X & the Y" / "X of Y" — keep the trailing noun, minus any article.
  const conj = dropArticle(candidate.split(/\s+(?:&|and|of)\s+/i).pop().trim());
  const base = conj.length <= 14 ? conj : conj.split(/\s+/)[0];
  return base.length <= 16 ? base : base.slice(0, 15) + "…";
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
  // Day/night is CLOCK-DRIVEN: read the engine's normalized phase off the surface (falling
  // back to a sniff of the legacy day label for older builds). There is no manual toggle —
  // the indicator always reflects the live campaign clock.
  const time = window.atlasTimePhase(surface);

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
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end", alignItems: "center" }}>
            {!campMode && <ClockDial phase={time} />}
            <BrassButton
              size={campMode ? "sm" : ""}
              tone={campMode ? "" : "crimson"}
              onClick={() => setCampMode && setCampMode(!campMode)}
              disabled={!campMode && !canCamp}
              style={!campMode ? { boxShadow: canCamp ? "0 0 18px -4px var(--gold-glow)" : undefined } : undefined}
            >
              {window.OpenWorldsIcon?.has?.("camp.rest") && <window.OpenWorldsIcon id="camp.rest" size={15} />}
              {campMode ? " Leave Camp" : " Make Camp"}
            </BrassButton>
            <BrassButton size="sm" tone="ghost" onClick={() => loadSurface()}>Refresh</BrassButton>
          </div>
        </div>

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
            <AtlasMap
              locations={locations}
              edges={edges}
              selected={selected}
              currentId={currentLocation.id}
              region={surface?.world}
              time={time}
              onSelect={setSelectedId}
            />
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
      />
    </div>
  );
}

// ── Deterministic spatial layout ──────────────────────────────────────────────
// The engine snapshot rarely carries real hex coordinates, so the atlas would
// otherwise collapse into a rigid 4-column grid (every node at x∈{24,42,60,78}).
// We instead lay the location GRAPH out spatially with a seeded force simulation:
// node-id hashes seed start positions (stable across the 7s poll), edges act as
// springs, and all nodes repel, so a connected web of 20+ sites spreads into a
// readable map. If a node DOES carry a distinct engine hex (loc.x/loc.y not on
// the fallback grid), we honor it as a pinned anchor.
function hashSeed(str) {
  let h = 2166136261;
  for (let i = 0; i < (str || "").length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967295; // 0..1
}

// The values _atlas_hex_position emits for its index fallback (no engine hex).
const ATLAS_FALLBACK_XS = new Set([24, 42, 60, 78]);
function nodeHasEngineHex(loc) {
  // A pinned node deviates from the fallback lattice (xs in {24,42,60,78}, ys a
  // multiple-of-18 ladder). Anything off that lattice came from a real hex.
  if (!ATLAS_FALLBACK_XS.has(loc.x)) return true;
  return (loc.y - 24) % 18 !== 0 && (loc.y - 24) % 14 !== 0;
}

function computeAtlasLayout(locations, edges) {
  const n = locations.length;
  if (!n) return {};
  const PAD = 12, SPAN = 100 - PAD * 2;
  const idx = new Map(locations.map((l, i) => [l.id, i]));
  const px = new Float64Array(n), py = new Float64Array(n), pinned = new Array(n).fill(false);

  locations.forEach((loc, i) => {
    if (nodeHasEngineHex(loc)) {
      px[i] = loc.x; py[i] = loc.y; pinned[i] = true;
    } else {
      // Seed on a golden-angle spiral jittered by the id hash → spread, deterministic.
      const a = i * 2.399963 + hashSeed(loc.id) * 6.283;
      const rad = 8 + Math.sqrt((i + 0.5) / n) * 36;
      px[i] = 50 + Math.cos(a) * rad;
      py[i] = 50 + Math.sin(a) * rad * 0.82;
    }
  });

  const adj = edges
    .map((e) => [idx.get(e.from), idx.get(e.to)])
    .filter(([a, b]) => a != null && b != null);

  const ITER = 320, REPULSE = 240, SPRING = 0.035, IDEAL = 26;
  for (let step = 0; step < ITER; step++) {
    const fx = new Float64Array(n), fy = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = px[i] - px[j], dy = py[i] - py[j];
        let d2 = dx * dx + dy * dy || 0.01;
        const f = REPULSE / d2;
        const d = Math.sqrt(d2);
        fx[i] += (dx / d) * f; fy[i] += (dy / d) * f;
        fx[j] -= (dx / d) * f; fy[j] -= (dy / d) * f;
      }
    }
    for (const [a, b] of adj) {
      let dx = px[b] - px[a], dy = py[b] - py[a];
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = (d - IDEAL) * SPRING;
      fx[a] += (dx / d) * f * d; fy[a] += (dy / d) * f * d;
      fx[b] -= (dx / d) * f * d; fy[b] -= (dy / d) * f * d;
    }
    const cool = 0.9 * (1 - step / ITER) + 0.04;
    for (let i = 0; i < n; i++) {
      if (pinned[i]) continue;
      px[i] += Math.max(-4, Math.min(4, fx[i] * cool));
      py[i] += Math.max(-4, Math.min(4, fy[i] * cool));
    }
  }

  // Normalize into the padded 0..100 viewport (preserve aspect of the spread).
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let i = 0; i < n; i++) {
    minX = Math.min(minX, px[i]); maxX = Math.max(maxX, px[i]);
    minY = Math.min(minY, py[i]); maxY = Math.max(maxY, py[i]);
  }
  const w = maxX - minX || 1, h = maxY - minY || 1, scale = SPAN / Math.max(w, h);
  const offX = PAD + (SPAN - w * scale) / 2, offY = PAD + (SPAN - h * scale) / 2;
  const out = {};
  locations.forEach((loc, i) => {
    out[loc.id] = {
      x: Math.round((offX + (px[i] - minX) * scale) * 10) / 10,
      y: Math.round((offY + (py[i] - minY) * scale) * 10) / 10,
    };
  });
  return out;
}

// Route styling by the edge's engine-declared kind / danger (display-only).
function edgeStyle(edge) {
  const kind = (edge.route_kind || "").toLowerCase();
  const danger = typeof edge.danger === "number" ? edge.danger : 0;
  if (danger >= 6) return { stroke: "rgba(120,32,32,0.62)", width: 0.7, dash: "1.4 1" };
  if (kind === "street" || kind === "road") return { stroke: "rgba(60,30,10,0.6)", width: 0.85, dash: "" };
  if (kind === "river" || kind === "ferry" || kind === "sea") return { stroke: "rgba(40,90,120,0.6)", width: 0.7, dash: "" };
  return { stroke: "rgba(60,30,10,0.46)", width: 0.5, dash: "1.6 1.2" };
}

function AtlasMap({ locations, edges, selected, currentId, region, time, onSelect }) {
  // Recompute only when the set of nodes/edges changes — NOT on every 7s poll tick,
  // so pins don't jiggle while the live clock and selection update around them.
  const layoutKey = React.useMemo(
    () => locations.map((l) => l.id).join("|") + "::" + edges.map((e) => `${e.from}>${e.to}`).join("|"),
    [locations, edges]
  );
  const layout = React.useMemo(
    () => computeAtlasLayout(locations, edges),
    [layoutKey] // eslint-disable-line react-hooks/exhaustive-deps
  );
  const at = (id) => layout[id] || { x: 50, y: 50 };
  // Region → backdrop map scope. The atlas spans Sword Coast geography (Baldur's Gate
  // districts AND Candlekeep / Elturel / Wyrm's Crossing), so the real Sword Coast
  // regional map is the right backdrop for the baldurs-gate world — and it's a static
  // image (the city page's own lead media is a webm an <img> can't render). Curated
  // per-region; falls back to map-<region> then the parchment styling for any world
  // without a dedicated backdrop. (#atlas-worldmap)
  const REGION_BACKDROP = { "baldurs-gate": "map-sword-coast" };
  const regionSlug = region ? String(region).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") : "";
  const regionScope = REGION_BACKDROP[regionSlug] || (regionSlug ? "map-" + regionSlug : "");

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
        {/* Optional region-map backdrop (scope `map-<region>`); 404 → the parchment
            styling below shows through (the Img placeholder is transparent here). */}
        {regionScope && (
          <div style={{ position: "absolute", inset: 0, opacity: 0.5, mixBlendMode: "multiply", pointerEvents: "none" }}>
            <Img scope={regionScope} label="" w="100%" h="100%" fit="cover" className="atlas-backdrop" />
          </div>
        )}

        <svg width="100%" height="100%" viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, opacity: 0.78 }}>
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
            const from = at(edge.from), to = at(edge.to);
            if (!layout[edge.from] || !layout[edge.to]) return null;
            const s = edgeStyle(edge);
            const touchesCurrent = edge.from === currentId || edge.to === currentId;
            const touchesSelected = selected && (edge.from === selected.id || edge.to === selected.id);
            return (
              <line key={`${edge.from}-${edge.to}-${i}`}
                x1={from.x} y1={from.y} x2={to.x} y2={to.y}
                stroke={touchesSelected ? "rgba(120,32,32,0.8)" : touchesCurrent ? "rgba(180,141,87,0.85)" : s.stroke}
                strokeWidth={touchesSelected || touchesCurrent ? s.width + 0.35 : s.width}
                strokeDasharray={s.dash}
                strokeLinecap="round" />
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

        {locations.map((loc) => {
          const p = at(loc.id);
          return (
            <button
              key={loc.id}
              onClick={() => onSelect(loc.id)}
              style={{
                position: "absolute",
                left: `${p.x}%`,
                top: `${p.y}%`,
                transform: "translate(-50%, -100%)",
                background: "none",
                cursor: "pointer",
                padding: 0,
                transition: "left 500ms, top 500ms",
              }}
            >
              <LocationPin loc={loc} selected={selected?.id === loc.id} />
            </button>
          );
        })}

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

function AtlasSidebar({ selected, travel, currentId, busyTravel, canAct, quests, clocks, projects, control, allLocations, onSelect, onTravel }) {
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
            <Img scope={selected.id ? "location:" + selected.id : ""} label={selected.name} w="100%" h={118} framed />
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
                {window.OpenWorldsIcon?.has?.("atlas.travel") && <window.OpenWorldsIcon id="atlas.travel" size={14} />} {busyTravel === selected.id ? "Sending..." : "Travel here"}
              </BrassButton>
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
  // The full label is reserved for the node in focus (selected / current / hovered);
  // every other node shows a compact label so the dense city centre stays legible. The
  // full name remains one hover away (and in the hover card below).
  const active = selected || isCurrent || hover;
  const labelText = active ? loc.name : window.atlasShortLabel(loc.name);

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4, position: "relative" }}
    >
      <div style={{
        position: "relative",
        padding: active ? "4px 14px 4px" : "3px 9px 3px",
        background: selected
          ? "linear-gradient(180deg, var(--crimson) 0%, #5a1414 100%)"
          : isCurrent
          ? "linear-gradient(180deg, var(--royal), var(--royal-deep))"
          : "linear-gradient(180deg, var(--p-100), var(--p-300))",
        color: selected || isCurrent ? "var(--p-100)" : "var(--ink-800)",
        fontFamily: "var(--f-display)",
        fontSize: active ? 10 : 9,
        letterSpacing: active ? "0.12em" : "0.06em",
        textTransform: "uppercase",
        opacity: active ? 1 : 0.92,
        boxShadow: selected
          ? "inset 0 0 0 1px #3a0e0e, 0 0 16px -2px rgba(244, 100, 100, 0.6)"
          : "inset 0 0 0 1px var(--b-500), 0 2px 4px rgba(0,0,0,0.4)",
        whiteSpace: "nowrap",
        zIndex: active ? 6 : 1,
      }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          {(() => {
            const iconId = loc.current ? "atlas.travel" : (loc.tags || []).includes("rest") ? "camp.rest" : "settlement.tavern";
            return window.OpenWorldsIcon?.has?.(iconId) ? <window.OpenWorldsIcon id={iconId} size={11} /> : null;
          })()}
          {labelText}
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
          <Img scope={loc.id ? "location:" + loc.id : ""} label={loc.name} w="100%" h={70} framed fit="cover" />
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

// Resolve the day/night phase from the LIVE campaign clock. Prefers the surface's
// engine-normalized `time_phase` (server `_openworlds_time_phase`); falls back to a
// sniff of `time_of_day`/`dayLabel` for older builds that predate the field. Never a
// user choice — the atlas shades whatever the engine clock says.
function atlasTimePhase(surface) {
  const phase = (surface?.time_phase || "").toLowerCase();
  if (["dawn", "day", "dusk", "night"].includes(phase)) return phase;
  const label = String(surface?.time_of_day || surface?.dayLabel || "").toLowerCase();
  if (label.includes("night") || label.includes("midnight")) return "night";
  if (label.includes("dawn") || label.includes("morning") || label.includes("sunrise")) return "dawn";
  if (label.includes("dusk") || label.includes("evening") || label.includes("sunset")) return "dusk";
  return "day";
}

// Display-only clock indicator. Lights the segment for the live phase; it is NOT
// clickable (the campaign clock owns the time of day, not the viewer).
function ClockDial({ phase }) {
  const PHASES = [
    { id: "dawn", glyph: "◓", title: "Dawn" },
    { id: "day", glyph: "☀", title: "Day" },
    { id: "dusk", glyph: "◒", title: "Dusk" },
    { id: "night", glyph: "☾", title: "Night" },
  ];
  const active = PHASES.find((p) => p.id === phase) || PHASES[1];
  return (
    <div
      title={`Campaign clock: ${active.title}`}
      aria-label={`Time of day: ${active.title} (set by the campaign clock)`}
      style={{
        display: "inline-flex", alignItems: "center", gap: 2, padding: "3px 6px",
        background: "rgba(176,141,87,0.1)",
        boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.35)",
        borderRadius: 2,
      }}
    >
      {PHASES.map((p) => {
        const on = p.id === phase;
        return (
          <span key={p.id} title={p.title} style={{
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            width: 20, height: 18, fontSize: 12, lineHeight: 1,
            color: on ? "var(--w-300)" : "var(--ink-600)",
            background: on ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "transparent",
            boxShadow: on ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.5)" : "none",
            opacity: on ? 1 : 0.55,
          }}>{p.glyph}</span>
        );
      })}
      <span style={{
        marginLeft: 4, fontFamily: "var(--f-display)", fontSize: 9, letterSpacing: "0.14em",
        textTransform: "uppercase", color: "var(--ink-700)",
      }}>{active.title}</span>
    </div>
  );
}

Object.assign(window, { ScreenMap, LocationPin, atlasSurfaceFromCampaign, atlasShortLabel, atlasTimePhase, ClockDial });
