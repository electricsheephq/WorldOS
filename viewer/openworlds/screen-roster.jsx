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
        {/* #dogfood onboarding (option b): the obvious safe FIRST pick. The engine flags a small
            easy-starter subset of the recommended set (simple classes, low-ish level); the card
            wears a "Great for your first session" ribbon so a newcomer has a clear safe choice. */}
        {npc.easy_starter ? (
          <div style={{
            position: "absolute", top: 8, left: 8,
            padding: "3px 9px",
            background: "linear-gradient(180deg, var(--b-200), var(--b-400))",
            boxShadow: "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)",
          }}>
            <span className="eyebrow" style={{ fontSize: 9, color: "var(--w-300)", letterSpacing: "0.08em" }}>
              Great for your first session
            </span>
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
        {/* #dogfood onboarding (option a): a plain-language playstyle hint so a no-prior-knowledge
            player has a BASIS TO CHOOSE — each card teaches itself how the figure PLAYS. Derived
            engine-side from the class (pure class→phrase, no fabricated lore); absent-safe. */}
        {npc.playstyle ? (
          <div className="body-sm" style={{ color: "var(--ink-600)", marginTop: 4, fontSize: 11, fontStyle: "italic" }}>
            {npc.playstyle}
          </div>
        ) : null}
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

function ScreenRoster({ onNavigate, state, setState, preferredProvider = "" }) {
  const campaigns = Array.isArray(state?.campaigns) ? state.campaigns : [];
  const campaignId = campaigns.some((c) => c.id === state?.activeCampaign)
    ? state.activeCampaign
    : (campaigns[0]?.id || "");
  const activeCampaign = campaigns.find((c) => c.id === campaignId) || campaigns[0] || {};
  const rosterCampaignId = activeCampaign.campaign_id || campaignId;
  const hasBridge = Boolean(window.OpenWorldsNative?.hasBridge?.());
  // #326: an already-playable session (live + resumable) the browser player can enter directly,
  // mirroring the launcher. Without the desktop bridge a NEW hero bind can't mint a DM session,
  // so if such a session exists we redirect the player to CONTINUE it rather than dead-ending.
  const playableCampaign =
    campaigns.find((c) => c.live && c.canResume) ||
    campaigns.find((c) => c.canResume) ||
    null;

  const [race, setRace] = React.useState("");
  const [klass, setKlass] = React.useState("");
  const [level, setLevel] = React.useState("");
  // BEGINNER ENTRY: the picker OPENS on a small curated "recommended for beginners" set rather than
  // dropping a newcomer into ~2,000 alphabetical names. Applying any filter, or clicking "Browse the
  // full roster", flips to the full playable+alive roster (illegible no-class/no-level records still
  // dropped via ?require_stats). The full list is always one click away — the curation is a default,
  // never a wall.
  const [browseAll, setBrowseAll] = React.useState(false);
  const [surface, setSurface] = React.useState(null);  // null until first fetch
  const [error, setError] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [summoningName, setSummoningName] = React.useState("");
  const [bindNote, setBindNote] = React.useState("");
  const toast = window.useToast ? window.useToast() : (() => {});

  const anyFilter = Boolean(race || klass || level);
  // The recommended surface is the default ONLY while unfiltered and the player hasn't asked to see
  // everything — a filter is an explicit "search the whole roster" intent.
  const recommended = !browseAll && !anyFilter;

  // Facets come from the FIRST (unfiltered) load so the chips offer every option regardless of
  // the current narrowing; held separately so a narrowing fetch (which returns facets for the
  // filtered slice) doesn't shrink the chip set out from under the player.
  const [facets, setFacets] = React.useState({ races: [], classes: [], levels: [] });
  const facetsSeeded = React.useRef(false);

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (rosterCampaignId) params.set("campaign", rosterCampaignId);
      if (activeCampaign.source) params.set("source", activeCampaign.source);
      if (activeCampaign.runId) params.set("run", activeCampaign.runId);
      if (race) params.set("race", race);
      if (klass) params.set("class", klass);
      if (level) params.set("level", level);
      if (recommended) {
        // The curated beginner subset (each card has a class + a mid-tier level + a backstory).
        params.set("recommended", "1");
      } else {
        // The full roster, minus the illegible no-class/no-level records (#dogfood).
        params.set("require_stats", "1");
      }
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
  }, [rosterCampaignId, activeCampaign.source, activeCampaign.runId, race, klass, level, recommended]);

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
    if (!hasBridge) {
      // #326: a browser-only preview has no supervisor to mint a NEW session for a freshly-picked
      // hero. Don't dead-end. If a live/resumable chronicle already exists (the #324 harness case),
      // send the player there to actually PLAY; otherwise be honest that a new chronicle needs the
      // desktop app — never a silent nothing.
      if (playableCampaign) {
        setState((s) => ({ ...s, activeCampaign: playableCampaign.id }));
        toast({ kind: "info", title: "Continuing your live chronicle", body: "A session is already in progress — dropping you into the table." });
        onNavigate("table");
        return;
      }
      setBindNote(
        `Picking ${npc.name} as a brand-new hero starts a fresh chronicle, which needs the ` +
        `WorldOS desktop app (it spins up the Dungeon Master). In this browser preview you can ` +
        `browse the roster, but you can't begin a new chronicle here.`
      );
      toast({ kind: "info", title: `Chosen: ${npc.name}`, body: "Starting a new chronicle needs the WorldOS desktop app." });
      return;
    }
    setSummoningName(npc.name);
    const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "");
    const world = (campaigns.find((c) => c.id === campaignId)?.world) || surface?.world_id || "baldurs-gate";
    try {
      const payload = {
        world,
        runId: `play-${stamp}`,
        companions: "",
        hero: JSON.stringify({ canon: true, name: npc.name }),
      };
      if (preferredProvider) payload.provider = preferredProvider;
      const reply = await window.OpenWorldsNative.request("startProviderSession", payload);
      const liveUrl = reply && (reply.url || reply.viewer?.openWorldsURL);
      if (liveUrl) {
        window.location.replace(liveUrl);
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
  // The payload itself reports whether it's the curated subset (the engine sets recommended:true).
  const isRecommended = Boolean(surface?.recommended);

  return (
    <div className="screen" style={{ height: "100%", display: "flex", flexDirection: "column", gap: 10, padding: 14 }}>

      {/* Header plate */}
      <Panel framed style={{ padding: "18px 24px" }}>
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 16, flexWrap: "wrap" }}>
          <div>
            <div className="eyebrow" style={{ color: "var(--crimson)" }}>Choose your hero</div>
            <h1 className="h1" style={{ fontSize: 26, marginTop: 2 }}>Take up a life already lived</h1>
            <p className="hand" style={{ fontSize: 14, color: "var(--ink-700)", margin: "6px 0 0", maxWidth: 620 }}>
              {isRecommended
                ? <>New to the Sword Coast? Start with a handful of <em>recommended</em> heroes — each a real figure with a class, a level, and a story. Filter or browse the full roster whenever you like; you will play <em>as</em> them.</>
                : <>Filter the chronicle's living roster and step into a real figure of the Sword Coast — their face, their past, their place in the world. You will play <em>as</em> them.</>}
            </p>
          </div>
          <BrassButton tone="ghost" size="sm" onClick={() => onNavigate("launcher")}>Back to Chronicles</BrassButton>
        </div>

        <Divider />

        <FilterRow title="Race" options={facets.races} value={race} onChange={setRace} />
        <FilterRow title="Class" options={facets.classes} value={klass} onChange={setKlass} />
        <FilterRow title="Level" options={facets.levels} value={level} onChange={setLevel} />

        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 4, flexWrap: "wrap" }}>
          <div className="body-sm muted">
            {loading
              ? "Reading the roster…"
              : isRecommended
                ? `${shown} ${shown === 1 ? "hero" : "heroes"} recommended for newcomers — or browse the full roster`
                : capped
                  ? `Showing ${shown} of ${total} heroes — narrow by race, class, or level to see the rest`
                  : `${total} ${total === 1 ? "hero" : "heroes"} available${anyFilter ? " · filtered" : ""}`}
          </div>
          {/* Beginner ⇄ full-roster toggle. A filter implies "search everything", so it lives
              alongside Clear filters; with no filter it flips the curated default on and off. */}
          {!anyFilter && (
            <button
              type="button"
              onClick={() => setBrowseAll((x) => !x)}
              style={{ background: "transparent", border: 0, color: "var(--crimson)", cursor: "pointer", fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.14em", textTransform: "uppercase" }}
            >
              {isRecommended ? "Browse the full roster" : "Show recommended"}
            </button>
          )}
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
