/* Screen: Roster — the canon-NPC PICKER ("reverse character creator").
 *
 * The owner's start flow: with no bound hero the player does NOT invent a portrait-less PC and
 * is NEVER handed one of the 7 BG3 origins. Instead they FILTER the ~2,000 ingested canon roster
 * by race / class / level and pick a pre-made canon figure (real backstory + ingested portrait)
 * to PLAY AS. Custom creation is a separate, deferred flow.
 *
 * Data: GET /roster-surface?campaign=&race=&class=&level= — the engine's playable_only canon
 * projection (origins/legends excluded by the record `playable` flag) + distinct race/class/level
 * facets. Read-only; the bind ("Play as <name>") seats the figure as kind="player" and starts a
 * live session via the native startProviderSession bridge (hero spec {canon:true, name}), which
 * scripts/play.sh pre-seeds with load_canon_character before the DM's first turn. Outside the
 * native app there is no DM to attach, so the picker explains that and leaves the choice in place.
 */

// How many chips to show per facet row before the "More" toggle (races run to ~120 distinct
// values; the surface orders them most-common-first so the lead chips are the useful ones).
const ROSTER_CHIP_CAP = 12;

function FilterChip({ label, active, onClick, count }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="pill"
      aria-pressed={active}
      style={{
        cursor: "pointer",
        border: 0,
        background: active ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(176,141,87,0.08)",
        color: active ? "var(--w-300)" : "var(--ink-700)",
        boxShadow: active
          ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)"
          : "inset 0 0 0 1px rgba(140,100,60,0.3)",
        letterSpacing: "0.04em",
        transition: "all 120ms",
      }}
    >
      {label}{typeof count === "number" ? <span style={{ opacity: 0.6, marginLeft: 6, fontSize: 10 }}>{count}</span> : null}
    </button>
  );
}

function FilterRow({ title, options, value, onChange }) {
  const [expanded, setExpanded] = React.useState(false);
  const opts = Array.isArray(options) ? options : [];
  const overflow = opts.length > ROSTER_CHIP_CAP;
  // Keep the active value visible even when it lives past the cap (so a chosen "Drow" doesn't
  // vanish when the row is collapsed).
  const visible = (() => {
    if (expanded || !overflow) return opts;
    const head = opts.slice(0, ROSTER_CHIP_CAP);
    if (value && !head.includes(value)) head.push(value);
    return head;
  })();
  return (
    <div style={{ marginBottom: 12 }}>
      <div className="eyebrow" style={{ marginBottom: 6 }}>{title}</div>
      <div className="tag-row">
        <FilterChip label="All" active={!value} onClick={() => onChange("")} />
        {visible.map((o) => (
          <FilterChip key={o} label={o} active={value === o} onClick={() => onChange(value === o ? "" : o)} />
        ))}
        {overflow && (
          <button
            type="button"
            onClick={() => setExpanded((x) => !x)}
            className="pill"
            style={{
              cursor: "pointer", border: 0, background: "transparent",
              color: "var(--crimson)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
              letterSpacing: "0.08em",
            }}
          >
            {expanded ? "Fewer" : `More (${opts.length - ROSTER_CHIP_CAP})`}
          </button>
        )}
      </div>
    </div>
  );
}

function RosterCard({ npc, onPlay, busy }) {
  const ident = [npc.race, npc.class].filter(Boolean).join(" · ");
  return (
    <div
      className="panel framed"
      style={{ display: "flex", flexDirection: "column", padding: 0, overflow: "hidden", minHeight: 0 }}
    >
      <div style={{ position: "relative" }}>
        <Img
          scope={npc.portrait_scope || (npc.id ? "portrait-" + npc.id : "")}
          label={npc.name}
          w="100%"
          h={168}
          fit="cover"
          style={{ boxShadow: "none" }}
        />
        {npc.level ? (
          <div style={{
            position: "absolute", top: 8, right: 8,
            padding: "3px 9px",
            background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
            boxShadow: "inset 0 0 0 1px var(--b-500), 0 2px 6px rgba(0,0,0,0.4)",
          }}>
            <span className="eyebrow" style={{ fontSize: 9 }}>Lv</span>
            <span style={{ fontFamily: "var(--f-display)", fontSize: 14, color: "var(--ink-900)", marginLeft: 4 }}>{npc.level}</span>
          </div>
        ) : null}
      </div>
      <div style={{ padding: "12px 14px 14px", display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 17, letterSpacing: "0.04em", color: "var(--ink-900)", lineHeight: 1.15 }}>
          {npc.name}
        </div>
        <div className="eyebrow" style={{ color: "var(--crimson)", marginTop: 3, fontSize: 10 }}>
          {ident || "of the Sword Coast"}
        </div>
        {npc.backstory ? (
          <p className="hand" style={{ fontSize: 13, color: "var(--ink-700)", margin: "8px 0 0", lineHeight: 1.4 }}>
            {npc.backstory}
          </p>
        ) : (
          <p className="hand muted" style={{ fontSize: 13, margin: "8px 0 0" }}>
            A figure of the Sword Coast, awaiting their chronicle.
          </p>
        )}
        <div style={{ flex: 1 }} />
        <div style={{ marginTop: 12 }}>
          <BrassButton size="sm" onClick={() => onPlay(npc)} disabled={busy} style={{ width: "100%" }}>
            {busy ? "Summoning…" : `Play as ${npc.name}`}
          </BrassButton>
        </div>
      </div>
    </div>
  );
}

function ScreenRoster({ onNavigate, state, setState }) {
  const campaigns = Array.isArray(state?.campaigns) ? state.campaigns : [];
  const campaignId = campaigns.some((c) => c.id === state?.activeCampaign)
    ? state.activeCampaign
    : (campaigns[0]?.id || "");

  const [race, setRace] = React.useState("");
  const [klass, setKlass] = React.useState("");
  const [level, setLevel] = React.useState("");
  const [surface, setSurface] = React.useState(null);  // null until first fetch
  const [error, setError] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [summoningName, setSummoningName] = React.useState("");
  const [bindNote, setBindNote] = React.useState("");
  const toast = window.useToast ? window.useToast() : (() => {});

  // Facets come from the FIRST (unfiltered) load so the chips offer every option regardless of
  // the current narrowing; held separately so a narrowing fetch (which returns facets for the
  // filtered slice) doesn't shrink the chip set out from under the player.
  const [facets, setFacets] = React.useState({ races: [], classes: [], levels: [] });
  const facetsSeeded = React.useRef(false);

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (campaignId) params.set("campaign", campaignId);
      if (race) params.set("race", race);
      if (klass) params.set("class", klass);
      if (level) params.set("level", level);
      const qs = params.toString();
      const response = await fetch("/roster-surface" + (qs ? "?" + qs : ""), { cache: "no-store" });
      if (!response.ok) throw new Error(`roster surface ${response.status}`);
      const payload = await response.json();
      if (isCancelled()) return;
      setSurface(payload);
      setError(payload?.error ? String(payload.error) : "");
      const f = payload?.facets;
      if (f && (!facetsSeeded.current || (!race && !klass && !level))) {
        // Seed (and refresh on a fully-cleared filter) the chip universe from the broad result.
        setFacets({
          races: Array.isArray(f.races) ? f.races : [],
          classes: Array.isArray(f.classes) ? f.classes : [],
          levels: Array.isArray(f.levels) ? f.levels : [],
        });
        facetsSeeded.current = true;
      }
    } catch (err) {
      if (isCancelled()) return;
      setError(err?.message || String(err));
    } finally {
      if (!isCancelled()) setLoading(false);
    }
  }, [campaignId, race, klass, level]);

  React.useEffect(() => {
    let cancelled = false;
    loadSurface(() => cancelled);
    return () => { cancelled = true; };
  }, [loadSurface]);

  // The BIND. "Play as <NPC>" seats the chosen canon figure as kind="player" and starts a live
  // session. Inside the native app we hand startProviderSession a hero spec {canon:true, name};
  // scripts/play.sh pre-seeds that exact canon PC (load_canon_character) before the DM's first
  // turn, then the app repoints its WebView at the live viewer and app.jsx auto-lands the table.
  // Outside the app there is no DM to attach — explain that rather than silently doing nothing.
  const playAs = async (npc) => {
    if (summoningName) return;
    setBindNote("");
    if (!window.OpenWorldsNative?.hasBridge?.()) {
      // FOLLOW-UP (flagged): a browser-only preview has no supervisor to mint the session. The
      // native path is the supported bind; here we surface the chosen hero so the flow is honest.
      setBindNote(
        `Selected ${npc.name} as your hero. Live play starts from the ClawDnD app — ` +
        `open this world there to begin the chronicle as ${npc.name}.`
      );
      toast({ kind: "info", title: `Chosen: ${npc.name}`, body: "Start live play from the ClawDnD app to embody this hero." });
      return;
    }
    setSummoningName(npc.name);
    const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "");
    const world = (campaigns.find((c) => c.id === campaignId)?.world) || surface?.world_id || "baldurs-gate";
    try {
      const reply = await window.OpenWorldsNative.request("startProviderSession", {
        provider: "claude",
        world,
        runId: `play-${stamp}`,
        companions: "",
        hero: JSON.stringify({ canon: true, name: npc.name }),
      });
      const liveUrl = reply && (reply.url || reply.viewer?.openWorldsURL);
      if (liveUrl) {
        window.location.assign(liveUrl);
        return;
      }
      setSummoningName("");
      setBindNote("The session started, but its live viewer address was missing.");
    } catch (err) {
      setSummoningName("");
      setBindNote(err?.message || String(err));
      toast({ kind: "danger", title: "Could not summon the Dungeon Master", body: err?.message || String(err) });
    }
  };

  const characters = Array.isArray(surface?.characters) ? surface.characters : [];
  const total = typeof surface?.total === "number" ? surface.total : characters.length;
  const shown = typeof surface?.returned === "number" ? surface.returned : characters.length;
  const capped = total > shown;  // more heroes match than the grid is painting — narrow to see them
  const anyFilter = Boolean(race || klass || level);

  return (
    <div className="screen" style={{ height: "100%", display: "flex", flexDirection: "column", gap: 10, padding: 14 }}>

      {/* Header plate */}
      <Panel framed style={{ padding: "18px 24px" }}>
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <div>
            <div className="eyebrow" style={{ color: "var(--crimson)" }}>Choose your hero</div>
            <h1 className="h1" style={{ fontSize: 26, marginTop: 2 }}>Take up a life already lived</h1>
            <p className="hand" style={{ fontSize: 14, color: "var(--ink-700)", margin: "6px 0 0", maxWidth: 620 }}>
              Filter the chronicle's living roster and step into a real figure of the Sword Coast —
              their face, their past, their place in the world. You will play <em>as</em> them.
            </p>
          </div>
          <BrassButton tone="ghost" size="sm" onClick={() => onNavigate("launcher")}>Back to Chronicles</BrassButton>
        </div>

        <Divider />

        <FilterRow title="Race" options={facets.races} value={race} onChange={setRace} />
        <FilterRow title="Class" options={facets.classes} value={klass} onChange={setKlass} />
        <FilterRow title="Level" options={facets.levels} value={level} onChange={setLevel} />

        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 4 }}>
          <div className="body-sm muted">
            {loading
              ? "Reading the roster…"
              : capped
                ? `Showing ${shown} of ${total} heroes — narrow by race, class, or level to see the rest`
                : `${total} ${total === 1 ? "hero" : "heroes"} available${anyFilter ? " · filtered" : ""}`}
          </div>
          {anyFilter && (
            <button
              type="button"
              onClick={() => { setRace(""); setKlass(""); setLevel(""); }}
              style={{ background: "transparent", border: 0, color: "var(--crimson)", cursor: "pointer", fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.14em", textTransform: "uppercase" }}
            >
              Clear filters
            </button>
          )}
        </div>
        {bindNote && (
          <div className="hand" style={{ color: "var(--ink-800)", fontSize: 13, marginTop: 8, background: "rgba(176,141,87,0.1)", padding: "8px 12px", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)" }}>
            {bindNote}
          </div>
        )}
        {error && (
          <div className="hand" style={{ color: "var(--crimson)", fontSize: 13, marginTop: 8 }}>
            The roster could not be read: {error}
          </div>
        )}
      </Panel>

      {/* Card grid */}
      <Panel framed style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 16 }}>
        {characters.length === 0 ? (
          <div style={{ display: "grid", placeItems: "center", height: "100%", minHeight: 220 }}>
            <div style={{ textAlign: "center", maxWidth: 420 }}>
              <div style={{ fontSize: 30, color: "var(--crimson)", lineHeight: 1 }}>✦</div>
              <h2 className="h1" style={{ fontSize: 20, marginTop: 8 }}>
                {loading ? "Summoning the roster…" : "No heroes match those bounds"}
              </h2>
              {!loading && (
                <p className="body" style={{ marginTop: 8 }}>
                  Loosen a filter — clear the race, class, or level to widen the field.
                </p>
              )}
            </div>
          </div>
        ) : (
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
            gap: 16,
            alignItems: "stretch",
          }}>
            {characters.map((npc) => (
              <RosterCard
                key={npc.id || npc.name}
                npc={npc}
                onPlay={playAs}
                busy={summoningName === npc.name}
              />
            ))}
          </div>
        )}
      </Panel>
    </div>
  );
}

Object.assign(window, { ScreenRoster, RosterCard, FilterRow, FilterChip });
