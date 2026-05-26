/* Screen: Relations — factions (left) + NPCs (right).
   Wired to the live /relations-surface read model (factions with reputation/tags, met
   NPCs + companions with attitude + dossier banter/relationships, companion arcs). Polls
   every 5s while visible; falls back to the demo constants until the first fetch.
   Layout/design unchanged from the prototype. */

function ScreenRelations({ onNavigate, state, setState }) {
  const surfaceQuery = window.combatSurfaceFromCampaign
    ? window.combatSurfaceFromCampaign(
        (Array.isArray(state?.campaigns) ? state.campaigns : []).find((c) => c.id === state?.activeCampaign) ||
          (Array.isArray(state?.campaigns) ? state.campaigns : [])[0] || {},
        state,
      )
    : "";
  const [surface, setSurface] = React.useState(null);
  const factions = (Array.isArray(surface?.factions) && surface.factions.length) ? surface.factions : FACTIONS;
  const npcs = (Array.isArray(surface?.npcs) && surface.npcs.length) ? surface.npcs
    : (surface ? [] : NPCS);
  const campBeats = surface?.campBeats || null;
  // Companion personal-quest arcs (from /relations-surface `companionArcs`): each is
  // { id, companion_id, companion, title, status, note, stages:[{title,status,note}] }.
  const companionArcs = Array.isArray(surface?.companionArcs) ? surface.companionArcs : [];
  const [selectedFactionId, setSelectedFactionId] = React.useState("");
  const [selectedNPCId, setSelectedNPCId] = React.useState("");
  const selectedFaction = factions.find((f) => f.id === selectedFactionId) || factions[0] || FACTIONS[0];
  const selectedNPC = npcs.find((n) => n.id === selectedNPCId) || npcs[0] || null;
  const setSelectedFaction = (f) => setSelectedFactionId(f.id);
  const setSelectedNPC = (n) => setSelectedNPCId(n.id);

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    try {
      const response = await fetch("/relations-surface" + surfaceQuery, { cache: "no-store" });
      if (!response.ok) throw new Error(`relations surface ${response.status}`);
      const payload = await response.json();
      if (!isCancelled()) setSurface(payload);
    } catch (error) { /* keep last good / demo fallback */ }
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
          <div style={{ overflow: "auto", display: "flex", flexDirection: "column" }}>
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
                <Img scope={n.id ? "portrait-" + n.id : ""} label={n.short} w={44} h={54} framed />
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

          <div style={{ overflow: "auto" }}>
            {selectedNPC ? <NPCDetail n={selectedNPC} onNavigate={onNavigate} campBeats={campBeats} /> : <div className="body-sm muted">No acquaintance selected.</div>}
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
        <div style={{
          fontFamily: "var(--f-display)",
          fontSize: 14, letterSpacing: "0.22em", textTransform: "uppercase",
          color: "rgba(255,250,220,0.92)",
          textShadow: "0 1px 2px rgba(0,0,0,0.6)",
        }}>{f.motto}</div>
        {/* Sigil */}
        <div style={{
          position: "absolute", top: -10, right: 10,
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

      <Divider />

      <p className="body dropcap" style={{ marginTop: 0, fontSize: 14 }}>{f.body}</p>

      <Divider />

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

function NPCDetail({ n, onNavigate, campBeats }) {
  return (
    <div>
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <Img scope={n.id ? "portrait-" + n.id : ""} label={n.short} w={120} h={148} framed />
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
        <BrassButton size="sm" tone="ghost">Send word</BrassButton>
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

const FACTIONS = [
  {
    id: "wardens",
    name: "Road Wardens of Restov",
    short: "knightly order",
    kind: "Sword-company",
    color: "#22305E",
    sigil: "✚",
    motto: "By the road, by the stone",
    seat: "Restov · Brevoy",
    rep: 64, threshold: { hostile: 25, neutral: 50, friendly: 75 },
    standing: "Cordial",
    lastContact: "9 days past",
    body: "The Wardens keep the post-roads passable, by patrol, by stone, and by occasional necessary brutality. Cassian is sworn. The order remembers oaths longer than men remember to keep them.",
    events: [
      { when: "9 days past", text: "Cassian accepted Warden Olwen's writ. The party agreed to investigate the Lanternrest in lieu of payment." },
      { when: "21 days past", text: "Reported the deserter Falgrim seen riding south of Tines. Order added our names to its ledger." },
      { when: "spring", text: "Vell, drunk, brawled with a Warden corporal. Vell, sober, paid the fine. The fine was not in coin." },
    ],
    offers: ["safe-conduct writs", "stable beds in Warden halls", "first claim on bounties", "intelligence on bandit movements"],
  },
  {
    id: "stag",
    name: "The Stag Lord's Company",
    short: "bandit lord",
    kind: "Outlaw warband",
    color: "#6E1D1D",
    sigil: "♛",
    motto: "Salt and silence",
    seat: "Fort of bones · undisclosed",
    rep: 8, threshold: { hostile: 30, neutral: 60 },
    standing: "Hostile",
    lastContact: "12 days past",
    body: "Bandits who think themselves a kingdom. The Stag Lord pays in salt, not gold; this matters to those who count. They are not many but they are everywhere, and they are oddly disciplined for men who answer to a name no one has heard spoken aloud.",
    events: [
      { when: "12 days past", text: "Bandit Falgrim raided Oleg's east wall. Oleg lost two crates. Falgrim lost an ear." },
      { when: "29 days past", text: "Three of their company found in the Thorn Ford, bound and floating. Authorship unattributed." },
      { when: "midwinter", text: "First confirmed reference to the Stag Lord by name, by a deserter who did not last the week." },
    ],
    offers: ["nothing you want"],
  },
  {
    id: "olegs",
    name: "Oleg's Trading Post",
    short: "lone trade-house",
    kind: "Independent",
    color: "#7a6644",
    sigil: "❦",
    motto: "Open until dusk",
    seat: "Cliff-back · North Outskirts",
    rep: 78, threshold: { hostile: 20, neutral: 40, friendly: 70 },
    standing: "Welcome",
    lastContact: "today",
    body: "A trade-house under a cliff that does not pretend to be anything else. Oleg keeps the books and the silences; Svetlana keeps everything else. They are friends, and they will tell you so by feeding you and refusing your coin.",
    events: [
      { when: "today", text: "Sold us 6 rations and a brass compass at last week's price. Svetlana would not let us pay for the salt." },
      { when: "9 days past", text: "Asked us to deal with the Stag Lord's company. We agreed. Reputation +20." },
    ],
    offers: ["fair prices", "back-room bed", "first refusal on rare components", "Svetlana's stew, free of charge"],
  },
  {
    id: "elk",
    name: "Order of the Elk",
    short: "sylvan priesthood",
    kind: "Holy order",
    color: "#2f5a3a",
    sigil: "𓃥",
    motto: "What the antlers remember",
    seat: "Temple of the Elk · Stagwood",
    rep: 44, threshold: { hostile: 20, neutral: 40, friendly: 70 },
    standing: "Civil",
    lastContact: "26 days past",
    body: "An older religion than the maps allow for. The antlers on the temple roof are new wool, old bone. The priests speak softly because the wood listens. They will let you camp, but only on the western edge.",
    events: [
      { when: "26 days past", text: "Vell left two silver in the offering bowl. The priestess returned one." },
      { when: "last spring", text: "Mira wrote down a hymn she heard sung by a child. The priestess asked her to burn the parchment. Mira did." },
    ],
    offers: ["sanctuary by night", "healing for the desperate", "the Elk's regard"],
  },
  {
    id: "pitax",
    name: "Court of Pitax",
    short: "decadent court",
    kind: "City-state",
    color: "#7a3d6e",
    sigil: "♚",
    motto: "Beauty as decree",
    seat: "Pitax · 14 days east",
    rep: 35, threshold: { hostile: 25, neutral: 50, friendly: 75 },
    standing: "Cool",
    lastContact: "last summer",
    body: "The court of Pitax considers itself an aesthetic movement and the rest of the Marches a slow embarrassment. They keep poets on stipend and assassins on retainer. The two roles overlap.",
    events: [
      { when: "last summer", text: "Mira was offered a chronicler's post. She declined. The offer was repeated three times." },
    ],
    offers: ["letters of introduction", "patronage (with strings)", "trouble"],
  },
  {
    id: "league",
    name: "Technic League",
    short: "scholarly cult",
    kind: "Foreign order",
    color: "#3a4a5a",
    sigil: "⚙",
    motto: "All that is hidden",
    seat: "Reported only · Stagwood",
    rep: 22, threshold: { hostile: 30, neutral: 60 },
    standing: "Wary",
    lastContact: "rumour only",
    body: "From outside the Marches. They collect things. They are oddly polite about it.",
    events: [
      { when: "9 days past", text: "Smoke seen rising from the Stagwood, wrong colour. Linzi made a sketch. Linzi is afraid of the sketch." },
    ],
    offers: ["unknown"],
  },
];

const NPCS = [
  {
    id: "svetlana",
    name: "Svetlana Leveton",
    short: "S·portrait",
    role: "Trader's wife · ally",
    location: "Oleg's Trading Post",
    faction: "Oleg's Trading Post",
    disposition: "ally",
    body: "Married to Oleg. Runs the post and the silences of the post in equal measure. She has chosen to consider you, for the moment, an improvement on circumstance.",
    dues: [
      { text: "Investigate the Lanternrest before reaching Odrun.", fulfilled: false, note: "She has not asked again. She is waiting." },
      { text: "Deal with the Stag Lord's raiders.", fulfilled: false },
      { text: "Return the brass key, if not used.", fulfilled: false },
    ],
    lastSpoken: "May the Inheritor walk with you. Oleg will pretend he is not relieved — that is his way.",
    lastSpokenAt: "Oleg's, evening of the 12th",
  },
  {
    id: "oleg",
    name: "Oleg Leveton",
    short: "O·portrait",
    role: "Trader · uneasy ally",
    location: "Oleg's Trading Post",
    faction: "Oleg's Trading Post",
    disposition: "friend",
    body: "Trader by trade and by temperament. Would have been a miller if the river had favoured him. The eastern wall has more spear-marks than the others.",
    dues: [
      { text: "Pay him back for the brass compass.", fulfilled: false, note: "He has not asked. He will." },
      { text: "Buy something from him at full price.", fulfilled: true, note: "Cassian, for the spellbook." },
    ],
    lastSpoken: "If you mean to look, look. I will not be pressed for prices.",
    lastSpokenAt: "Oleg's, the same evening",
  },
  {
    id: "olwen",
    name: "Toll-keeper Olwen",
    short: "O·toll",
    role: "Warden of the south gate",
    location: "Gate of Tines",
    faction: "Road Wardens",
    disposition: "neutral",
    body: "Wears the Warden colours; will not say a word he has not weighed first. Stamped your writ once and did not stamp it the second time. Did not say why.",
    dues: [
      { text: "Ask him a second time, in better light.", fulfilled: false },
      { text: "Pay the gate-toll if it is asked.", fulfilled: false },
    ],
    lastSpoken: "The seal is correct. That is not the problem.",
    lastSpokenAt: "Gate of Tines, 5 Gozran",
  },
  {
    id: "linzi",
    name: "Linzi",
    short: "L·portrait",
    role: "Chronicler · party",
    location: "with the party, always",
    faction: null,
    disposition: "friend",
    body: "Halfling, chronicler, refuses the word 'bard.' Writes the chronicle by candle when there is candle and by memory when there is not. Is in some real sense the reason the engine works.",
    dues: [
      { text: "Read what she has written, when she offers.", fulfilled: false },
    ],
    lastSpoken: "I am not a character. I am a chronicler.",
    lastSpokenAt: "every camp",
  },
  {
    id: "crow",
    name: "The Crow",
    short: "crow",
    role: "Watcher · unmoving",
    location: "Lanternrest gable",
    faction: null,
    disposition: "cool",
    body: "Sits the gable of the Lanternrest. Has not moved in three days. The wind moves around it.",
    dues: [
      { text: "Find out whose crow.", fulfilled: false },
      { text: "Do not feed the crow.", fulfilled: true, note: "Mira tried. Mira regretted." },
    ],
    lastSpoken: null,
  },
  {
    id: "stag-lord",
    name: "The Stag Lord",
    short: "lord·portrait",
    role: "Antagonist · uncrowned",
    location: "Fort of bones (rumoured)",
    faction: "The Stag Lord's Company",
    disposition: "enemy",
    body: "Nobody who has come back has said. The bandits speak his name like a prayer they do not believe in.",
    dues: [
      { text: "Find the fort.", fulfilled: false },
      { text: "End him, or be ended.", fulfilled: false },
    ],
    lastSpoken: null,
  },
  {
    id: "priestess",
    name: "Priestess Eira",
    short: "E·priestess",
    role: "Of the Elk",
    location: "Temple of the Elk",
    faction: "Order of the Elk",
    disposition: "neutral",
    body: "Speaks soft. Listens harder than she speaks. Returned Vell's second silver. Did not return his first.",
    dues: [
      { text: "Visit the temple before the new moon.", fulfilled: false },
    ],
    lastSpoken: "What you owe, you owe. What you do not, you do not.",
    lastSpokenAt: "Temple of the Elk, last spring",
  },
];

Object.assign(window, { ScreenRelations, FactionDetail, NPCDetail, BetrayalWarning, CompanionArcCard, RepBar, DispositionDot, FACTIONS, NPCS });
