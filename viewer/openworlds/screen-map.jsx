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

function ScreenMap({ onNavigate, state, campMode, setCampMode, liveSession }) {
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
  // Urgent filter (M-02): the "N urgent" pill is a live control — toggling it focuses the
  // strategic sidebar on urgent clocks/projects and jumps the map selection to the first
  // urgent thread's location so the count is actionable, not just a read-only badge.
  const [urgentOnly, setUrgentOnly] = React.useState(false);
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
  // Tier rollup for the pin footer (issue #380): prefer the server-computed split, but
  // degrade gracefully for older surfaces that predate `tier_counts` by deriving it from
  // the per-location `rumoured` flag (absent on legacy rows ⇒ everything counts as known).
  const tierCounts = surface?.tier_counts && typeof surface.tier_counts === "object"
    ? { known: surface.tier_counts.known || 0, rumoured: surface.tier_counts.rumoured || 0 }
    : { known: locations.filter((l) => !l.rumoured).length, rumoured: locations.filter((l) => l.rumoured).length };
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
  // #402: is the DM mid-turn? The /move sink always accepts (it just appends an intent), and
  // `can_act` stays true while the DM narrates — so a camp "Begin Resting" click during a beat used
  // to POST a move that silently queued behind the in-flight turn (no advance, a misleading success
  // toast). Mirror ScreenTable's gate: `pending` (present + not flagged stuck) ⇒ the DM is narrating
  // and the player can't take a new action yet. Threaded down to CampSidebar so the rest CTA can
  // disable + explain instead of no-op'ing. (`pending` lives on the app-level liveSession hook.)
  const dmPending = liveSession?.pending || null;
  const dmBusy = Boolean(dmPending && !dmPending.stuck);
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
    // #913/#623 (map leg): map click-to-travel posts a move to /move that resolves a ~100s DM beat,
    // then navigates to the table — but it never armed the app-level narrating indicator, so the
    // table opened on a SPINNER-LESS dead wait (the frozen-feel #826/#648 killed for the table's own
    // postMove). Mirror ScreenTable.postMove EXACTLY: arm the pending gate + record the chronicle echo
    // OPTIMISTICALLY, the instant the player commits and BEFORE the /move round-trip resolves, so the
    // "Narrating…" affordance is already up when we navigate to the table. Both live on the app-level
    // liveSession hook, so the arm + echo SURVIVE this screen unmounting on onNavigate("table").
    // Pure viewer presentation: no engine write, no /move-contract change — reuses armPending/
    // abandonPending (never forks the pending state machine).
    const travelText = option.name || option.to;
    const armText = window.neutralizeMarkup ? window.neutralizeMarkup(String(travelText)) : String(travelText);
    // The chronicle echo's `who` is cosmetic here — the atlas surface (unlike the table's
    // /session-surface) carries no party roster, so a player-initiated travel is echoed as "You".
    if (typeof liveSession?.recordPlayerEcho === "function") {
      liveSession.recordPlayerEcho("You", armText, option.move);
    }
    if (typeof liveSession?.armPending === "function") liveSession.armPending(armText);
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
      toast({ kind: "quest", title: "Travel intent sent", body: travelText });
      onNavigate("table");
    } catch (error) {
      // The POST was rejected — the move never started, so authoritatively roll back the optimistic
      // arm (abandonPending bypasses the #648 arm-grace; it's surgical — clears ONLY the move we just
      // armed, never a newer live turn). Falls back to clearPending on an older bundle that predates
      // abandonPending. No phantom "Narrating…" stranded on the table after a dead /move sink.
      if (typeof liveSession?.abandonPending === "function") liveSession.abandonPending(armText);
      else if (typeof liveSession?.clearPending === "function") liveSession.clearPending();
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
  const baseClocks = selected ? clocks.filter((c) => !c.location_id || c.location_id === selected.id) : clocks;
  const baseProjects = selected ? projects.filter((p) => !p.location_id || p.location_id === selected.id) : projects;
  // When the urgent filter is on, the strategic sidebar narrows to urgent threads only.
  const locationClocks = urgentOnly ? baseClocks.filter((c) => c.urgent) : baseClocks;
  const locationProjects = urgentOnly ? baseProjects.filter((p) => p.urgent) : baseProjects;
  const locationControl = selected ? controls.find((c) => c.location_id === selected.id) : null;
  const calendar = surface?.calendar?.available ? surface.calendar : null;
  const calendarMoon = Array.isArray(calendar?.moons) ? calendar.moons[0] : null;
  const calendarDetail = calendar ? [calendar.season, calendarMoon ? `${calendarMoon.name}: ${calendarMoon.phase}` : ""].filter(Boolean).join(" · ") : "";

  // Urgent pill control: count across ALL clocks/projects, plus a toggle that jumps the
  // map selection to the first urgent thread that carries a location so the badge acts.
  const urgentCount = clocks.filter((c) => c.urgent).length + projects.filter((p) => p.urgent).length;
  const toggleUrgent = () => {
    const next = !urgentOnly;
    setUrgentOnly(next);
    if (next) {
      const firstUrgent = [...clocks, ...projects].find((it) => it.urgent && it.location_id && locations.some((l) => l.id === it.location_id));
      if (firstUrgent) setSelectedId(firstUrgent.location_id);
    }
  };

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "minmax(0, 1fr) 340px", gap: 14, padding: 14 }}>
      <Panel framed style={{ padding: 18, position: "relative", display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 10, position: "relative", zIndex: 3, flex: "0 0 auto", gap: 12 }}>
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
                dmBusy={dmBusy}
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
          <Pill dot>{tierCounts.known} known</Pill>
          {tierCounts.rumoured > 0 && (
            <Pill dot tone="" >
              <span style={{ fontStyle: "italic", opacity: 0.85 }}>{tierCounts.rumoured} rumoured</span>
            </Pill>
          )}
          <button
            type="button"
            onClick={toggleUrgent}
            disabled={urgentCount === 0}
            aria-pressed={urgentOnly}
            title={urgentCount === 0 ? "No urgent threads" : urgentOnly ? "Showing urgent threads — click to clear" : "Filter the strategic context to urgent threads"}
            style={{
              background: "none", border: "none", padding: 0, margin: 0,
              cursor: urgentCount === 0 ? "default" : "pointer",
              display: "inline-flex", alignItems: "center", borderRadius: 999,
              outline: urgentOnly ? "1px solid var(--crimson)" : "none",
              outlineOffset: 2,
              opacity: urgentCount === 0 ? 0.6 : 1,
            }}
          >
            <Pill dot tone={urgentOnly ? "crimson" : ""}>{urgentCount} urgent</Pill>
          </button>
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
        onNavigate={onNavigate}
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

  // Collision-resolution pass (M-04): the force sim can still leave two nodes nearly
  // co-located when the repulsion balances against several springs, so pins overlap and
  // a label hides another. Do a few short relaxation sweeps that push any pair closer
  // than MIN_SEP apart along their separation axis. A pinned node holds (it carries a
  // real engine hex); when both are free they share the nudge. Units are the pre-scale
  // layout space — MIN_SEP ≈ 8 here normalizes to a ~60px gap in the framed viewport.
  const MIN_SEP = 8, SEP_SWEEPS = 14;
  for (let sweep = 0; sweep < SEP_SWEEPS; sweep++) {
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = px[i] - px[j], dy = py[i] - py[j];
        let d = Math.sqrt(dx * dx + dy * dy);
        if (d >= MIN_SEP) continue;
        // Degenerate (exactly stacked) → pick a deterministic axis from the id hashes.
        if (d < 0.001) {
          const a = (hashSeed(locations[i].id) + hashSeed(locations[j].id)) * 6.283;
          dx = Math.cos(a); dy = Math.sin(a); d = 1;
        }
        const push = (MIN_SEP - d) / d;
        const ux = dx * push, uy = dy * push;
        if (pinned[i] && pinned[j]) continue; // two anchors — leave the engine's truth
        if (pinned[i]) { px[j] -= ux; py[j] -= uy; }
        else if (pinned[j]) { px[i] += ux; py[i] += uy; }
        else { px[i] += ux * 0.5; py[i] += uy * 0.5; px[j] -= ux * 0.5; py[j] -= uy * 0.5; }
      }
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
  // High-danger routes dominate the style: dark red dashed, regardless of kind.
  if (danger >= 6) return { stroke: "rgba(120,32,32,0.62)", width: 0.7, dash: "1.4 1" };
  // Urban + extramural surface routes — solid brown.
  if (kind === "street" || kind === "road") return { stroke: "rgba(60,30,10,0.6)", width: 0.85, dash: "" };
  // Water + crossing — solid blue. "ferry" is now a valid Literal member (#381).
  if (kind === "river" || kind === "ferry" || kind === "sea") return { stroke: "rgba(40,90,120,0.6)", width: 0.7, dash: "" };
  // Bridge — slightly thicker amber-brown to mark a load-bearing crossing
  // (Wyrm's Crossing over the Chionthar, etc.) as distinct from a regular road.
  // Added Loop-10 / #381.
  if (kind === "bridge") return { stroke: "rgba(110,75,30,0.72)", width: 1.05, dash: "" };
  // Underground — Underdark passages, Bhaal Temple stairs, sewers. Tight dotted
  // dash reads as "you go down, not across." Added Loop-10 / #381 ahead of the
  // #380 Underdark POIs.
  if (kind === "underground" || kind === "passage") return { stroke: "rgba(40,25,10,0.66)", width: 0.7, dash: "0.7 1.8" };
  // Portal — extraplanar / arcane (Avernus gate, Astral, etc.). A subtle violet
  // tint flags the route as "not walking distance" even when the connection is
  // visually short.
  if (kind === "portal") return { stroke: "rgba(95,55,135,0.62)", width: 0.7, dash: "1.2 1.2" };
  // Trail — wilds-grade path. Lighter and looser-dashed than the default to
  // read as "you'll get there, just not quickly."
  if (kind === "trail") return { stroke: "rgba(85,60,30,0.5)", width: 0.6, dash: "1.4 1.6" };
  return { stroke: "rgba(60,30,10,0.46)", width: 0.5, dash: "1.6 1.2" };
}

// Brass square for the atlas zoom controls (+ / − / FIT) — matches the parchment chrome.
const atlasZoomBtn = {
  width: 26, height: 26, display: "grid", placeItems: "center", cursor: "pointer",
  fontFamily: "var(--f-display)", fontSize: 16, lineHeight: 1, color: "var(--ink-900)",
  background: "linear-gradient(180deg, var(--w-100), var(--w-300))",
  boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 0 2px var(--b-500)",
};

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

  // ── Zoom / pan (atlas navigation) ─────────────────────────────────────────────
  // The backdrop image, the SVG (viewBox 0..100, preserveAspectRatio="none") and the
  // percent-positioned pins all live in ONE coordinate box, so transforming a single wrapper
  // by scale+translate moves them together and keeps pins/edges aligned. Wheel zooms toward
  // the cursor; pointer-drag pans; "Fit" resets. Clamped to [1, MAX] so you can't zoom out
  // past the framed map (no empty margins) or lose the nodes off-canvas.
  const ZOOM_MIN = 1, ZOOM_MAX = 4;
  const [view, setView] = React.useState({ scale: 1, tx: 0, ty: 0 });
  const frameRef = React.useRef(null);
  const drag = React.useRef(null);
  const clampPan = (tx, ty, scale) => {
    // At scale s the content overflows the frame by (s-1)/2 of its size on each side (origin
    // center); keep the translate within that so an edge can't pull past the frame border.
    const max = (rect) => ({ x: rect ? (rect.width * (scale - 1)) / 2 : 9999,
                             y: rect ? (rect.height * (scale - 1)) / 2 : 9999 });
    const m = max(frameRef.current && frameRef.current.getBoundingClientRect());
    return { tx: Math.max(-m.x, Math.min(m.x, tx)), ty: Math.max(-m.y, Math.min(m.y, ty)) };
  };
  const onWheel = (e) => {
    e.preventDefault();
    const rect = frameRef.current && frameRef.current.getBoundingClientRect();
    const next = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, view.scale * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
    setView((v) => {
      if (next === 1) return { scale: 1, tx: 0, ty: 0 };
      // Keep the point under the cursor stable: scale the existing translate about the cursor.
      const cx = rect ? e.clientX - rect.left - rect.width / 2 : 0;
      const cy = rect ? e.clientY - rect.top - rect.height / 2 : 0;
      const k = next / v.scale;
      const p = clampPan(cx + (v.tx - cx) * k, cy + (v.ty - cy) * k, next);
      return { scale: next, ...p };
    });
  };
  const onPointerDown = (e) => {
    if (view.scale <= 1) return; // nothing to pan when fit
    drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty };
    e.currentTarget.setPointerCapture && e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e) => {
    if (!drag.current) return;
    const p = clampPan(drag.current.tx + (e.clientX - drag.current.x),
                       drag.current.ty + (e.clientY - drag.current.y), view.scale);
    setView((v) => ({ ...v, ...p }));
  };
  const endDrag = () => { drag.current = null; };
  const zoomed = view.scale > 1;

  return (
    <div style={{ position: "relative", height: "100%" }}>
      <div
        ref={frameRef}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}
        style={{
        position: "absolute", inset: 0,
        background:
          `radial-gradient(ellipse at 30% 25%, rgba(120, 80, 30, 0.25), transparent 50%),
           radial-gradient(ellipse at 75% 70%, rgba(100, 60, 20, 0.3), transparent 55%),
           radial-gradient(ellipse at 15% 80%, rgba(160, 110, 60, 0.18), transparent 40%),
           linear-gradient(135deg, #c8a878 0%, #b89868 40%, #a08055 100%)`,
        boxShadow: "inset 0 0 80px rgba(60, 30, 10, 0.6)",
        overflow: "hidden",
        cursor: zoomed ? (drag.current ? "grabbing" : "grab") : "default",
        touchAction: "none",
      }}>
        {/* Zoom / pan transform layer — backdrop + SVG + pins move together so they stay
            aligned. Full-frame overlays (dusk wash, no-data state) live OUTSIDE this group. */}
        <div style={{
          position: "absolute", inset: 0,
          transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})`,
          transformOrigin: "center center",
          transition: drag.current ? "none" : "transform 160ms ease-out",
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
          {/* Atlas watermark — moved out of the top node band (it crowded the northern pins)
              into a dim bottom-left cartouche opposite the compass rose, smaller + fainter so
              it reads as map furniture, never competing with location labels (M-03). */}
          <text x="11" y="95" textAnchor="start" fontFamily="Cinzel" fontSize="3" fill="rgba(60,30,10,0.13)" letterSpacing="1.2">OPEN WORLDS ATLAS</text>
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
              title={loc.name}
              aria-label={loc.current ? `${loc.name} (current location)` : loc.name}
              aria-pressed={selected?.id === loc.id}
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
        </div>{/* end zoom/pan transform layer */}

        {/* Zoom controls — outside the transform so they stay pinned to the frame. Wheel to
            zoom, drag to pan; "Fit" resets. Hidden when there's no atlas to navigate. */}
        {locations.length > 0 && (
          <div style={{ position: "absolute", right: 8, top: 8, display: "flex", flexDirection: "column", gap: 4, zIndex: 4 }}>
            <button onClick={() => setView((v) => { const s = Math.min(ZOOM_MAX, v.scale * 1.3); return s === 1 ? { scale: 1, tx: 0, ty: 0 } : { ...v, scale: s }; })}
              title="Zoom in" aria-label="Zoom in" style={atlasZoomBtn}>+</button>
            <button onClick={() => setView((v) => { const s = Math.max(ZOOM_MIN, v.scale / 1.3); return s === 1 ? { scale: 1, tx: 0, ty: 0 } : { ...clampPan(v.tx, v.ty, s), scale: s }; })}
              title="Zoom out" aria-label="Zoom out" style={atlasZoomBtn}>−</button>
            {zoomed && (
              <button onClick={() => setView({ scale: 1, tx: 0, ty: 0 })} title="Fit the whole map" aria-label="Fit the whole map"
                style={{ ...atlasZoomBtn, fontSize: 8, letterSpacing: "0.08em" }}>FIT</button>
            )}
          </div>
        )}

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

function AtlasSidebar({ selected, travel, currentId, busyTravel, canAct, quests, clocks, projects, control, allLocations, onSelect, onTravel, onNavigate }) {
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
              {selected.current
                ? "Current location"
                : (travel?.minutes
                  ? `${travel.minutes} minutes away`
                  : (selected.rumoured ? "Rumoured — heard of, not yet found" : "Known location"))}
            </div>

            {/* Route readout (BG3/PFK "do I dare take this road"): the engine ships
                danger/difficulty/route_kind/tags per travel edge; the sidebar was rendering
                only minutes. Pure presentation of data already on the travel object. */}
            {!selected.current && travel && (typeof travel.danger === "number" || travel.route_kind || travel.difficulty) && (
              <div className="tag-row" style={{ marginTop: 8 }}>
                {travel.route_kind && <Pill>{String(travel.route_kind).replace(/_/g, " ")}</Pill>}
                {travel.difficulty && <Pill tone={/hard|treacher|deadly/i.test(String(travel.difficulty)) ? "crimson" : ""}>{travel.difficulty}</Pill>}
                {typeof travel.danger === "number" && (
                  <Pill tone={travel.danger >= 6 ? "crimson" : travel.danger >= 3 ? "" : "emerald"}>
                    {travel.danger >= 6 ? "⚠ " : ""}Danger {travel.danger}/10
                  </Pill>
                )}
                {(travel.tags || []).filter((t) => t !== "danger").slice(0, 3).map((t) => <Pill key={t}>{t}</Pill>)}
              </div>
            )}

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
        {quests.length === 0 && clocks.length === 0 && projects.length === 0 ? (
          <div style={{ marginTop: 8 }}>
            <div className="body-sm" style={{ color: "var(--ink-600)" }}>
              No active threads in this region yet — quests, clocks, and downtime projects appear here as they develop. Pick up a thread to set one in motion:
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
              {onNavigate && <BrassButton size="sm" onClick={() => onNavigate("dialogue")}>Talk to locals</BrassButton>}
              {onNavigate && <BrassButton size="sm" tone="ghost" onClick={() => onNavigate("journal")}>Open the chronicle</BrassButton>}
            </div>
            <div className="hand muted" style={{ fontSize: 11, marginTop: 8 }}>
              Or choose a place on the atlas to scout where to go next.
            </div>
          </div>
        ) : (
          <>
            <StrategicList label="Quests" items={quests} empty="No active quest markers here." render={(q) => (
              <ContextRow key={q.id} title={q.title} meta={q.objective || q.status || "active"} />
            )} />
            <StrategicList label="Clocks" items={clocks} empty="No strategic clocks here." render={(c) => (
              <ContextRow key={c.id} title={c.title} meta={`${c.kind} - ${c.progress}/${c.target}`} urgent={c.urgent} />
            )} />
            <StrategicList label="Projects" items={projects} empty="No downtime projects here." render={(p) => (
              <ContextRow key={p.id} title={p.title} meta={`${p.status} - ${p.progress_days}/${p.duration_days} days`} urgent={p.urgent} />
            )} />
          </>
        )}

        <Divider />
        <SectionTitle>Discovered</SectionTitle>
        <div className="body-sm" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {allLocations.map((loc) => {
            // Rumoured places (issue #380) read distinctly in the gazetteer too — italic
            // name + a "rumoured" meta in place of the region — so the fog-of-war tier is
            // legible in the list, not only on the pins.
            const isRumoured = Boolean(loc.rumoured) && !loc.current;
            return (
              <button key={loc.id} onClick={() => onSelect(loc.id)} style={{
                display: "flex", justifyContent: "space-between", textAlign: "left",
                padding: "8px 12px", cursor: "pointer",
                background: selected?.id === loc.id ? "rgba(176,141,87,0.18)" : "transparent",
                boxShadow: "inset 0 -1px 0 rgba(140,100,60,0.2)",
                opacity: isRumoured ? 0.82 : 1,
              }}>
                <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.08em", color: "var(--ink-900)", fontStyle: isRumoured ? "italic" : "normal" }}>{loc.name}</span>
                <span className="muted" style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: isRumoured ? "var(--b-700)" : undefined }}>{loc.current ? "here" : isRumoured ? "rumoured" : loc.region}</span>
              </button>
            );
          })}
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
      <div style={{ fontFamily: "var(--f-display)", fontSize: 13, lineHeight: 1.5, color: urgent ? "var(--crimson)" : "var(--ink-900)", letterSpacing: "0.08em" }}>{title}</div>
      {meta && <div className="body-sm" style={{ color: "var(--ink-600)", marginTop: 2 }}>{meta}</div>}
    </div>
  );
}

function LocationPin({ loc, selected }) {
  const isCurrent = loc.current;
  const isVisited = loc.visited;
  // Rumoured tier (issue #380): a place the party has HEARD of but not confirmed. The
  // engine surfaces it as visible-but-fogged; here it gets a distinct fog-of-war
  // affordance — a desaturated, dashed-outline label and a "?" badge on the dot — so it
  // reads as a HORIZON, never confused with a solid known/visited pin. A current or
  // selected pin always overrides the fog styling (you're looking right at it).
  const isRumoured = Boolean(loc.rumoured) && !isCurrent;
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
          : isRumoured
          ? "linear-gradient(180deg, rgba(214,200,176,0.62), rgba(176,158,128,0.62))"
          : "linear-gradient(180deg, var(--p-100), var(--p-300))",
        color: selected || isCurrent ? "var(--p-100)" : isRumoured ? "var(--ink-600)" : "var(--ink-800)",
        fontFamily: "var(--f-display)",
        fontSize: active ? 10 : 9,
        letterSpacing: active ? "0.12em" : "0.06em",
        textTransform: "uppercase",
        fontStyle: isRumoured && !selected ? "italic" : "normal",
        opacity: isRumoured && !active ? 0.72 : active ? 1 : 0.92,
        boxShadow: selected
          ? "inset 0 0 0 1px #3a0e0e, 0 0 16px -2px rgba(244, 100, 100, 0.6)"
          : isRumoured
          ? "inset 0 0 0 1px var(--b-400), 0 1px 3px rgba(0,0,0,0.25)"
          : "inset 0 0 0 1px var(--b-500), 0 2px 4px rgba(0,0,0,0.4)",
        border: isRumoured && !selected ? "1px dashed rgba(90,60,30,0.7)" : "none",
        whiteSpace: "nowrap",
        zIndex: active ? 6 : 1,
      }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          {(() => {
            const iconId = loc.current ? "atlas.travel" : (loc.tags || []).includes("rest") ? "camp.rest" : "settlement.tavern";
            return window.OpenWorldsIcon?.has?.(iconId) ? <window.OpenWorldsIcon id={iconId} size={11} /> : null;
          })()}
          {labelText}
          {isRumoured && <span title="Rumoured — heard of, not yet found" style={{ fontSize: 9, opacity: 0.8 }}>?</span>}
        </span>
      </div>
      <div style={{
        position: "relative",
        width: 12, height: 12, borderRadius: "50%",
        background: isCurrent
          ? "radial-gradient(circle at 30% 30%, var(--gold-glow), var(--b-500))"
          : isVisited
          ? "radial-gradient(circle at 30% 30%, var(--b-200), var(--b-500))"
          : isRumoured
          ? "radial-gradient(circle at 30% 30%, rgba(190,176,150,0.85), rgba(120,100,70,0.85))"
          : "radial-gradient(circle at 30% 30%, var(--p-300), var(--ink-700))",
        boxShadow: isCurrent
          ? "0 0 0 1px var(--w-500), 0 0 20px rgba(244, 210, 123, 0.8)"
          : isRumoured
          ? "0 0 0 1px rgba(120,100,70,0.7)"
          : "0 0 0 1px var(--w-500)",
        border: isRumoured ? "1px dashed rgba(90,60,30,0.8)" : "none",
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
          <div className="hand" style={{ fontSize: 12, color: isRumoured ? "var(--b-700)" : "var(--ink-600)", marginTop: 2 }}>
            {loc.current ? "Current location" : isRumoured ? "Rumoured — heard of, not yet found" : "Known route"}
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
        marginLeft: 4, fontFamily: "var(--f-display)", fontSize: 13, lineHeight: 1, letterSpacing: "0.14em",
        textTransform: "uppercase", color: "var(--ink-700)",
      }}>{active.title}</span>
    </div>
  );
}

Object.assign(window, { ScreenMap, LocationPin, atlasSurfaceFromCampaign, atlasShortLabel, atlasTimePhase, ClockDial });
