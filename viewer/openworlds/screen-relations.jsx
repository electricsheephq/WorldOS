/* Screen: Relations — factions (left) + NPCs (right).
   Wired to the live /relations-surface read model (factions with reputation/tags, met
   NPCs + companions with attitude + dossier banter/relationships, companion arcs). Polls
   every 5s while visible. Before the first fetch (or if it fails) the screen shows honest
   empty-states — it never falls back to demo data.
   Layout/design unchanged from the prototype. */

/* Build the /image scope for an NPC/companion portrait. Ingested canon art is keyed by a
   NAME-slug ("portrait_jaheira"); a met roster NPC already carries a slug id ("npc-jaheira")
   that normalises to the same key, but a RECRUITED companion carries a random instance id
   ("char_…") that matches no art. Deriving the scope from slug(name) resolves real faces for
   both, and degrades to the silhouette (via Img's onError) when no art exists. */
function npcPortraitScope(n) {
  const s = (n && n.name && window.slug) ? window.slug(n.name) : "";
  if (s) return "portrait-" + s;
  return (n && n.id) ? "portrait-" + n.id : "";
}

function ScreenRelations({ onNavigate, state, setState }) {
  const surfaceQuery = window.combatSurfaceFromCampaign
    ? window.combatSurfaceFromCampaign(
        (Array.isArray(state?.campaigns) ? state.campaigns : []).find((c) => c.id === state?.activeCampaign) ||
          (Array.isArray(state?.campaigns) ? state.campaigns : [])[0] || {},
        state,
      )
    : "";
  const [surface, setSurface] = React.useState(null);
  // Live surface only — never fall back to bundled demo data. Until the first fetch (or
  // if it fails) both lists are empty and the panels show honest empty-states.
  const factions = Array.isArray(surface?.factions) ? surface.factions : [];
  const npcs = Array.isArray(surface?.npcs) ? surface.npcs : [];
  const campBeats = surface?.campBeats || null;
  // Live action lane (mirrors merchant/map): /relations-surface exposes can_act + campaign_id,
  // so "Send word" can relay a structured `do` move to the DM when a live session is attached.
  const canAct = Boolean(surface?.can_act);
  const campaignId = surface?.campaign_id || "";
  const toast = window.useToast ? window.useToast() : (() => {});
  // Companion personal-quest arcs (from /relations-surface `companionArcs`): each is
  // { id, companion_id, companion, title, status, note, stages:[{title,status,note}] }.
  const companionArcs = Array.isArray(surface?.companionArcs) ? surface.companionArcs : [];
  const [selectedFactionId, setSelectedFactionId] = React.useState("");
  const [selectedNPCId, setSelectedNPCId] = React.useState("");
  // Resolve to the live selection or first live faction; when the live list is empty the
  // selection stays null and the render shows an honest empty-state.
  const selectedFaction = factions.find((f) => f.id === selectedFactionId) || factions[0] || null;
  const selectedNPC = npcs.find((n) => n.id === selectedNPCId) || npcs[0] || null;
  const setSelectedFaction = (f) => setSelectedFactionId(f.id);
  const setSelectedNPC = (n) => setSelectedNPCId(n.id);

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    try {
      const response = await fetch("/relations-surface" + surfaceQuery, { cache: "no-store" });
      if (!response.ok) throw new Error(`relations surface ${response.status}`);
      const payload = await response.json();
      if (!isCancelled()) setSurface(payload);
    } catch (error) { /* keep the last good surface; empty-states show until the first success */ }
  }, [surfaceQuery]);

  React.useEffect(() => {
    let cancelled = false;
    let timer = null;
    const guardedLoad = async () => { if (!cancelled) await loadSurface(() => cancelled); };
    const stopPolling = () => { if (timer !== null) { window.clearInterval(timer); timer = null; } };
    const startPolling = () => { if (timer === null) timer = window.setInterval(guardedLoad, 5000); };
    const handleVisibility = () => {
      if (document.visibilityState === "visible") { guardedLoad(); startPolling(); } else { stopPolling(); }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    handleVisibility();
    return () => { cancelled = true; stopPolling(); document.removeEventListener("visibilitychange", handleVisibility); };
  }, [loadSurface]);

  return (
    <div className="screen" style={{ height: "100%", display: "flex", flexDirection: "column", gap: 14, padding: 14, minHeight: 0 }}>
     <div style={{ flex: 1, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, minHeight: 0 }}>

      {/* LEFT — Factions */}
      <Panel framed style={{ padding: 22, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 8 }}>
          <div>
            <div className="eyebrow" style={{ color: "var(--crimson)" }}>The Powers That</div>
            <h1 className="h1" style={{ fontSize: 22 }}>Factions</h1>
          </div>
          <span className="muted body-sm">{factions.length} known</span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 14, flex: 1, minHeight: 0 }}>
          {/* Faction list with banner colors */}
          <div style={{ overflow: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
            {factions.map((f) => (
              <button key={f.id} onClick={() => setSelectedFaction(f)} style={{
                position: "relative",
                padding: "10px 12px",
                textAlign: "left",
                background: selectedFaction?.id === f.id ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "rgba(176,141,87,0.06)",
                boxShadow: selectedFaction?.id === f.id
                  ? "inset 0 0 0 1px var(--b-500), inset 4px 0 0 " + f.color + ", inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
                  : "inset 0 0 0 1px rgba(140,100,60,0.25), inset 4px 0 0 " + f.color,
                cursor: "pointer",
              }}>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.06em", color: "var(--ink-900)", paddingLeft: 8 }}>
                  {f.name}
                </div>
                <div className="hand muted" style={{ fontSize: 11, paddingLeft: 8 }}>{f.short}</div>
                <RepBar value={f.rep} max={100} threshold={f.threshold} />
              </button>
            ))}
            {!factions.length && <div className="body-sm muted">No factions recorded yet.</div>}
          </div>

          {/* Faction detail */}
          <div tabIndex={0} style={{ overflow: "auto", display: "flex", flexDirection: "column" }}>
            {selectedFaction ? <FactionDetail f={selectedFaction} /> : <div className="body-sm muted">No faction selected.</div>}
          </div>
        </div>
      </Panel>

      {/* RIGHT — NPCs */}
      <Panel framed style={{ padding: 22, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 8 }}>
          <div>
            <div className="eyebrow" style={{ color: "var(--crimson)" }}>The Persons We</div>
            <h1 className="h1" style={{ fontSize: 22 }}>Know</h1>
          </div>
          <span className="muted body-sm">{npcs.length} acquainted</span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: 14, flex: 1, minHeight: 0 }}>
          <div style={{ overflow: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
            {npcs.map((n) => (
              <button key={n.id} onClick={() => setSelectedNPC(n)} style={{
                display: "grid", gridTemplateColumns: "44px 1fr", gap: 8, alignItems: "center",
                padding: "6px 10px",
                textAlign: "left",
                background: selectedNPC?.id === n.id ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "rgba(176,141,87,0.06)",
                boxShadow: selectedNPC?.id === n.id
                  ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
                  : "inset 0 0 0 1px rgba(140,100,60,0.25)",
                cursor: "pointer",
              }}>
                <Img scope={npcPortraitScope(n)} label={n.name} w={44} h={54} framed />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.04em", color: "var(--ink-900)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {n.name}
                  </div>
                  <div className="hand muted" style={{ fontSize: 10 }}>{n.role}</div>
                  <DispositionDot d={n.disposition} />
                </div>
              </button>
            ))}
            {!npcs.length && <div className="body-sm muted">No one met yet. NPCs appear here once the party speaks with them.</div>}
          </div>

          <div tabIndex={0} style={{ overflow: "auto" }}>
            {selectedNPC ? <NPCDetail n={selectedNPC} onNavigate={onNavigate} campBeats={campBeats} canAct={canAct} campaignId={campaignId} toast={toast} /> : <div className="body-sm muted">No acquaintance selected.</div>}
          </div>
        </div>
      </Panel>
     </div>

      {/* Companion Arcs — the character-owned personal-quest lifecycle from
          /relations-surface `companionArcs`. Hidden entirely when the surface provides none. */}
      {companionArcs.length > 0 && (
        <Panel framed style={{ padding: 22, flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
            <div>
              <div className="eyebrow" style={{ color: "var(--crimson)" }}>The Roads They Walk</div>
              <h2 className="h1" style={{ fontSize: 18 }}>Companion Arcs</h2>
            </div>
            <span className="muted body-sm">{companionArcs.length} in motion</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
            {companionArcs.map((arc) => <CompanionArcCard key={arc.id} arc={arc} />)}
          </div>
        </Panel>
      )}
    </div>
  );
}

function CompanionArcCard({ arc }) {
  const statusTone = (s) => {
    const v = String(s || "").toLowerCase();
    if (v === "complete" || v === "completed" || v === "resolved" || v === "done") return "var(--emerald)";
    if (v === "active" || v === "in_progress" || v === "unlocked" || v === "open") return "var(--royal)";
    if (v === "failed") return "var(--crimson)";
    return "var(--b-400)"; // locked / unknown
  };
  const stages = Array.isArray(arc.stages) ? arc.stages : [];
  return (
    <div style={{
      padding: "12px 14px",
      background: "rgba(176,141,87,0.06)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25), inset 3px 0 0 " + statusTone(arc.status),
    }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
        <span style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.06em", color: "var(--ink-900)" }}>
          {arc.title}
        </span>
        {arc.status && <Pill>{arc.status}</Pill>}
      </div>
      {arc.companion && <div className="hand muted" style={{ fontSize: 11, marginTop: 2 }}>{arc.companion}</div>}
      {arc.note && <div className="body-sm" style={{ color: "var(--ink-700)", marginTop: 6 }}>{arc.note}</div>}
      {stages.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 8 }}>
          {stages.map((s, i) => (
            <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
              <span style={{ width: 7, height: 7, borderRadius: "50%", marginTop: 5, background: statusTone(s.status), boxShadow: "0 0 0 1px rgba(0,0,0,0.3)", flexShrink: 0 }} />
              <div style={{ minWidth: 0 }}>
                <div className="body-sm" style={{ color: "var(--ink-800)" }}>{s.title}</div>
                {s.note && <div className="hand muted" style={{ fontSize: 11 }}>{s.note}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RepBar({ value, max, threshold }) {
  const ratio = value / max;
  const tone = value < (threshold?.hostile || 25) ? "var(--crimson)" :
               value < (threshold?.neutral || 50) ? "var(--b-400)" :
               value < (threshold?.friendly || 80) ? "var(--emerald)" :
               "var(--gold-glow)";
  return (
    <div style={{ marginTop: 8, paddingLeft: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span className="hand muted" style={{ fontSize: 10 }}>
          {value < 20 ? "Hostile" : value < 40 ? "Cool" : value < 60 ? "Civil" : value < 80 ? "Cordial" : "Welcome"}
        </span>
        <span style={{ fontFamily: "var(--f-mono)", fontSize: 9, color: "var(--ink-600)" }}>{value}/{max}</span>
      </div>
      <div style={{ height: 5, background: "rgba(0,0,0,0.15)", position: "relative", boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.4)", marginTop: 3 }}>
        <div style={{ position: "absolute", inset: 0, right: `${(1 - ratio) * 100}%`, background: `linear-gradient(180deg, ${tone}, color-mix(in oklab, ${tone}, black 25%))` }} />
      </div>
    </div>
  );
}

function FactionDetail({ f }) {
  return (
    <div>
      <div style={{
        position: "relative",
        height: 90,
        background: `linear-gradient(135deg, ${f.color} 0%, color-mix(in oklab, ${f.color}, black 40%) 100%)`,
        boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 1px 0 rgba(255,255,255,0.15), 0 4px 10px rgba(0,0,0,0.25)",
        display: "flex", alignItems: "center", justifyContent: "center",
        marginBottom: 14,
      }}>
        {/* The banner shows the faction's motto; when the seed carries no motto, fall back to
            the faction name so the banner is never an empty colored band. Right-padded to 44px
            so long mottos clear the 36px sigil medallion at top-right (R-04). */}
        <div style={{
          fontFamily: "var(--f-display)",
          fontSize: 14, letterSpacing: "0.22em", textTransform: "uppercase",
          color: "rgba(255,250,220,0.92)",
          textShadow: "0 1px 2px rgba(0,0,0,0.6)",
          textAlign: "center", padding: "0 44px",
        }}>{(f.motto && f.motto.trim()) ? f.motto : f.name}</div>
        {/* Sigil */}
        <div style={{
          position: "absolute", top: 8, right: 10,
          width: 36, height: 36, borderRadius: "50%",
          background: "radial-gradient(circle at 30% 30%, var(--b-200), var(--b-500))",
          boxShadow: "inset 0 0 0 1px var(--b-600), 0 2px 4px rgba(0,0,0,0.4)",
          display: "grid", placeItems: "center",
          color: f.color,
          fontFamily: "var(--f-display)",
          fontSize: 18,
        }}>{f.sigil}</div>
      </div>

      <div className="eyebrow" style={{ color: "var(--crimson)" }}>{f.kind}</div>
      <h2 className="h1" style={{ fontSize: 20, marginTop: 2 }}>{f.name}</h2>
      {/* Seat — hide when the surface leaves it blank (a live faction has no seat field). */}
      {f.seat && <div className="hand" style={{ fontSize: 14, color: "var(--ink-700)" }}>{f.seat}</div>}

      {f.seat && <Divider />}

      <p className="body dropcap" style={{ marginTop: 0, fontSize: 14 }}>{f.body}</p>

      {(f.standing || f.lastContact || (f.events && f.events.length) || (f.offers && f.offers.length)) && <Divider />}

      {/* Standing/last-contact grid — only render a StatLine whose value is present; the live
          surface emits standing but leaves lastContact blank. */}
      {(f.standing || f.lastContact) && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
            {f.standing && <StatLine k="Standing" v={f.standing} />}
            {f.lastContact && <StatLine k="Last contact" v={f.lastContact} />}
          </div>

          <Divider />
        </>
      )}

      {/* "Of late" — recent faction events. The live surface emits an empty events list, so
          hide the whole section (heading + rows) rather than show an empty "Of late" label. */}
      {Array.isArray(f.events) && f.events.length > 0 && (
        <>
          <SectionTitle>Of late</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {f.events.map((e, i) => (
              <div key={i} style={{
                display: "grid", gridTemplateColumns: "80px 1fr",
                gap: 10,
                padding: "8px 10px",
                background: "rgba(176,141,87,0.06)",
                boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
              }}>
                <span className="eyebrow" style={{ fontSize: 9 }}>{e.when}</span>
                <span className="body-sm" style={{ color: "var(--ink-800)" }}>{e.text}</span>
              </div>
            ))}
          </div>

          <Divider />
        </>
      )}

      {/* "They offer" — the live surface fills this with the faction's raw reputation tags;
          hide the section entirely when there are none rather than show an empty tag row. */}
      {Array.isArray(f.offers) && f.offers.length > 0 && (
        <>
          <SectionTitle>They offer</SectionTitle>
          <div className="tag-row">
            {f.offers.map((o) => <Pill key={o}>{o}</Pill>)}
          </div>
        </>
      )}
    </div>
  );
}

function DispositionDot({ d }) {
  const tone = d === "friend" ? "var(--emerald)" :
               d === "ally" ? "var(--royal)" :
               d === "neutral" ? "var(--b-400)" :
               d === "cool" ? "var(--ink-500)" :
               "var(--crimson)";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, marginTop: 2 }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: tone, boxShadow: "0 0 0 1px rgba(0,0,0,0.3)" }} />
      <span className="hand muted" style={{ fontSize: 10, textTransform: "capitalize" }}>{d}</span>
    </span>
  );
}

function BetrayalWarning({ w }) {
  // Advisory "approaching a breaking point" telegraph (#118) — display-only, icon-free.
  // Crimson left-rule mirrors the journal's high-severity GM advisory styling.
  const band = Array.isArray(w?.band) && w.band.length === 2 ? `${w.band[0]}..${w.band[1]}` : "danger band";
  return (
    <div style={{
      marginBottom: 10,
      padding: "8px 10px",
      background: "rgba(110, 29, 29, 0.10)",
      boxShadow: "inset 0 0 0 1px rgba(110,29,29,0.35), inset 3px 0 0 var(--crimson)",
    }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <span className="eyebrow" style={{ color: "var(--crimson)" }}>{w?.label || "Bond fracturing"}</span>
        <span style={{ fontFamily: "var(--f-mono)", fontSize: 9, color: "var(--ink-600)" }}>
          {typeof w?.attitude_value === "number" ? `${w.attitude_value} / band ${band}` : band}
        </span>
      </div>
      <div className="hand" style={{ fontSize: 12, color: "var(--ink-700)", marginTop: 3 }}>
        {w?.note || "This companion is approaching a breaking point."}
      </div>
      {w?.decision_active && (
        <div className="hand muted" style={{ fontSize: 11, marginTop: 3, color: "var(--crimson)" }}>
          A recorded choice has deepened the rift.
        </div>
      )}
    </div>
  );
}

function NPCDetail({ n, onNavigate, campBeats, canAct, campaignId, toast }) {
  // "Send word" — relay a structured `do` move to the DM (the engine resolves it, e.g. a
  // courier / sending). Mirrors the merchant/inventory live-action pattern: only acts when a
  // live session is attached (can_act); otherwise the button is disabled with an honest reason.
  const sendWord = () => {
    if (!canAct || !n?.name) return;
    fetch("/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "do", text: `I send word to ${n.name}`, campaign: campaignId }),
    }).then(() => {
      (toast || (() => {}))({ kind: "info", eyebrow: "Relations", title: "Word sent", body: `Move relayed to the DM — the engine resolves reaching ${n.name}.` });
    }).catch((e) => (toast || (() => {}))({ kind: "danger", title: "Move not sent", body: e?.message || "viewer unreachable" }));
  };
  return (
    <div>
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <Img scope={npcPortraitScope(n)} label={n.name} w={120} h={148} framed />
        <div style={{ flex: 1 }}>
          <div className="eyebrow" style={{ color: "var(--crimson)" }}>{n.role}</div>
          <h2 className="h1" style={{ fontSize: 20, marginTop: 2 }}>{n.name}</h2>
          <div className="hand" style={{ fontSize: 13, color: "var(--ink-700)" }}>{n.location}</div>

          <div style={{ marginTop: 10 }}>
            <DispositionDot d={n.disposition} />
          </div>

          {n.faction && (
            <div style={{ marginTop: 8 }}>
              <Pill tone="royal">of {n.faction}</Pill>
            </div>
          )}
        </div>
      </div>

      <Divider />

      <p className="body dropcap" style={{ marginTop: 0, fontSize: 14 }}>{n.body}</p>

      {/* Companion dossier (#68): approval gauge + banter themes + standing ties. */}
      {n.companion && (
        <>
          <Divider />
          {typeof n.approval === "number" && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span className="eyebrow">Approval</span>
                <span style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-600)" }}>{n.approval > 0 ? "+" : ""}{n.approval}</span>
              </div>
              <div style={{ height: 5, marginTop: 3, background: "rgba(0,0,0,0.15)", position: "relative", boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.4)" }}>
                <div style={{ position: "absolute", inset: 0, right: `${(1 - Math.max(0, Math.min(100, (n.approval + 100) / 2)) / 100) * 100}%`, background: "linear-gradient(180deg, var(--emerald), #2a6a30)" }} />
              </div>
            </div>
          )}
          {/* Betrayal-warning band (#118): advisory telegraph when a companion's bond has
              soured into the engine's danger band. Read-only — surfaced from the engine's
              own `betrayal_warning`; never an action. */}
          {n.betrayalWarning && <BetrayalWarning w={n.betrayalWarning} />}
          {Array.isArray(n.banter_tags) && n.banter_tags.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div className="eyebrow" style={{ marginBottom: 4 }}>Banter</div>
              <div className="tag-row">{n.banter_tags.map((t) => <Pill key={t}>{t}</Pill>)}</div>
            </div>
          )}
          {n.relationships && Object.keys(n.relationships).length > 0 && (
            <div>
              <div className="eyebrow" style={{ marginBottom: 4 }}>Ties</div>
              {Object.entries(n.relationships).map(([who, tie]) => (
                <div key={who} className="hand muted" style={{ fontSize: 12 }}>{who}: {tie}</div>
              ))}
            </div>
          )}
          <CampBeatLedger npcId={n.id} campBeats={campBeats} />
        </>
      )}

      <Divider />

      <SectionTitle>{n.companion ? "What they remember" : "What stands between you"}</SectionTitle>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {(n.dues || []).map((d, i) => (
          <div key={i} style={{
            padding: "8px 10px",
            background: d.fulfilled ? "rgba(95, 130, 70, 0.08)" : "rgba(176,141,87,0.06)",
            boxShadow: "inset 0 0 0 1px " + (d.fulfilled ? "rgba(95,130,70,0.3)" : "rgba(140,100,60,0.25)"),
            display: "flex", alignItems: "flex-start", gap: 10,
          }}>
            <span style={{ color: d.fulfilled ? "var(--emerald)" : "var(--b-500)", fontSize: 14, lineHeight: 1.2 }}>
              {d.fulfilled ? "✓" : "·"}
            </span>
            <div>
              <div className="body-sm" style={{ color: "var(--ink-800)" }}>{d.text}</div>
              {d.note && <div className="hand muted" style={{ fontSize: 11, marginTop: 2 }}>{d.note}</div>}
            </div>
          </div>
        ))}
        {!(n.dues || []).length && <div className="body-sm muted">Nothing recorded between you yet.</div>}
      </div>

      {n.lastSpoken && (
        <>
          <Divider />
          <div className="eyebrow">Last spoken</div>
          <div className="hand" style={{ fontSize: 13, color: "var(--ink-700)", marginTop: 4 }}>
            "{n.lastSpoken}"
            <div className="muted" style={{ fontFamily: "var(--f-body)", fontStyle: "normal", fontSize: 12, marginTop: 4 }}>
              — at {n.lastSpokenAt}
            </div>
          </div>
        </>
      )}

      <div style={{ display: "flex", gap: 6, marginTop: 18 }}>
        <BrassButton size="sm" onClick={() => onNavigate("dialogue")}>Find them</BrassButton>
        <BrassButton size="sm" tone="ghost" onClick={sendWord} disabled={!canAct}
          title={canAct ? `Relays "send word to ${n.name}" to the DM via /move` : "No live session attached — start a session to send word"}>
          Send word
        </BrassButton>
      </div>
    </div>
  );
}

function CampBeatLedger({ npcId, campBeats }) {
  const recent = Array.isArray(campBeats?.recent)
    ? campBeats.recent.filter((beat) => (beat.participants || []).some((p) => p.id === npcId)).slice(0, 3)
    : [];
  if (!campBeats) return null;
  return (
    <>
      <Divider />
      <div className="eyebrow" style={{ marginBottom: 4 }}>Camp</div>
      <div className="body-sm muted" style={{ marginBottom: 6 }}>
        {campBeats.summary?.records || 0} recorded · solo {campBeats.summary?.solo_cooldown_days || 0}d · pair {campBeats.summary?.pair_cooldown_days || 0}d
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {recent.map((beat) => (
          <div key={beat.id} style={{
            padding: "8px 10px",
            background: "rgba(176,141,87,0.06)",
            boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
              <span className="body-sm" style={{ color: "var(--ink-800)" }}>{beat.note || beat.kind}</span>
              <span style={{ fontFamily: "var(--f-mono)", fontSize: 9, color: "var(--ink-600)", whiteSpace: "nowrap" }}>
                day {beat.day}
              </span>
            </div>
            <div className="hand muted" style={{ fontSize: 11, marginTop: 3 }}>
              {beat.cooldown?.remaining_days > 0 ? `ready day ${beat.cooldown.ready_day}` : "ready now"}
            </div>
          </div>
        ))}
        {!recent.length && <div className="body-sm muted">No camp beats recorded for them yet.</div>}
      </div>
    </>
  );
}

Object.assign(window, { ScreenRelations, FactionDetail, NPCDetail, BetrayalWarning, CompanionArcCard, RepBar, DispositionDot, npcPortraitScope });
