/* Screen: Acts — chronicle progression, current act, memorable moments */

/* Party portrait scope — mirrors screen-character/relations: derive from slug(name)
   ("portrait-jaheira") so a canon face resolves; fall back to the instance id, then to
   <Img>'s neutral PortraitSilhouette for a portrait-less name. */
function aPortraitScope(p) {
  const s = (p && p.name && window.slug) ? window.slug(p.name) : "";
  if (s) return "portrait-" + s;
  return (p && p.id) ? "portrait-" + p.id : "";
}

/* The shape every campaign takes — the DM runs a 3-act arc (skills/dungeon-master/AGENT.md).
   Shown ONLY as a faded preview while a save has no compiled acts yet, so a new player sees
   what the chronicle will hold. This is a TEMPLATE of the dramatic form, never fabricated
   progress: no dates, no "you are here", no choices — the engine fills the real acts in. */
const ARC_PREVIEW = [
  { numeral: "I", name: "The Inciting Incident", note: "A grounded hook that matters to a person — where the road begins." },
  { numeral: "II", name: "The Turn", note: "Rising trouble and a midpoint reversal that costs the hero something real." },
  { numeral: "III", name: "The Reckoning", note: "The threads converge — a climax, and the price paid finally matters." },
];

function ScreenActs({ onNavigate, state, setState }) {
  const campaigns = Array.isArray(state?.campaigns) ? state.campaigns : [];
  const activeCampaign =
    campaigns.find((c) => c.id === state?.activeCampaign) ||
    campaigns[0] ||
    {};
  const campaignId = activeCampaign.campaign_id || state?.activeCampaign || activeCampaign.id || "";
  const [surface, setSurface] = React.useState(null);
  const [surfaceStatus, setSurfaceStatus] = React.useState("loading");
  // Acts come from the live /acts-surface read model only. Until the first fetch returns we
  // show nothing (empty timeline + honest empty-state) — never a hardcoded demo chronicle.
  const acts = Array.isArray(surface?.acts) ? surface.acts : [];
  const currentAct = acts.find((a) => a.id === surface?.currentActId) || acts.find((a) => a.current) || acts[0] || null;
  const [selectedActId, setSelectedActId] = React.useState("");
  const selectedAct = acts.find((a) => a.id === selectedActId) || currentAct;

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    const query = window.combatSurfaceFromCampaign
      ? window.combatSurfaceFromCampaign(activeCampaign, state)
      : (campaignId ? `?campaign=${encodeURIComponent(campaignId)}` : "");
    try {
      const response = await fetch("/acts-surface" + query, { cache: "no-store" });
      if (!response.ok) throw new Error(`acts surface ${response.status}`);
      const payload = await response.json();
      if (isCancelled()) return;
      setSurface(payload);
      setSurfaceStatus("ready");
    } catch (error) {
      if (!isCancelled()) setSurfaceStatus(error?.message || "unavailable");
    }
  }, [activeCampaign, state, campaignId]);

  React.useEffect(() => {
    let cancelled = false;
    let timer = null;
    const guardedLoad = async () => { if (!cancelled) await loadSurface(() => cancelled); };
    const stopPolling = () => { if (timer !== null) { window.clearInterval(timer); timer = null; } };
    const startPolling = () => { if (timer === null) timer = window.setInterval(guardedLoad, 7000); };
    const handleVisibility = () => {
      if (document.visibilityState === "visible") { guardedLoad(); startPolling(); } else { stopPolling(); }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    handleVisibility();
    return () => { cancelled = true; stopPolling(); document.removeEventListener("visibilitychange", handleVisibility); };
  }, [loadSurface]);

  React.useEffect(() => {
    if (currentAct?.id && !acts.some((a) => a.id === selectedActId)) setSelectedActId(currentAct.id);
  }, [currentAct?.id, selectedActId, acts]);

  return (
    <div className="screen" style={{ height: "100%", display: "flex", flexDirection: "column", gap: 14, padding: 14 }}>

      {/* A-09: read-only banner sits ABOVE both panes, spanning the full width, rather than
          buried in the left spine column — more visible. Shown only while untracked. */}
      {surface && !surface.tracked && (
        <div style={{ padding: 12, background: "rgba(176,141,87,0.08)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.28)" }}>
          <div className="eyebrow">Read-only</div>
          <div className="body-sm muted" style={{ marginTop: 4 }}>{surface.emptyState?.body}</div>
        </div>
      )}

      <div style={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: 14 }}>

      {/* LEFT — Timeline of acts */}
      <Panel framed style={{ padding: 22, overflow: "auto" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>The Chronicle, in</div>
        <h1 className="h1" style={{ fontSize: 22 }}>Acts</h1>
        <div className="body-sm muted" style={{ marginTop: 4 }}>
          {surface ? (surface.tracked ? surface.dayLabel : surface.emptyState?.title) : surfaceStatus}
        </div>
        <Divider />

        <div style={{ position: "relative", paddingLeft: 24 }}>
          {/* Spine */}
          <div style={{
            position: "absolute",
            left: 8, top: 6, bottom: 6,
            width: 2,
            background: "linear-gradient(180deg, var(--b-500), var(--b-300), var(--b-500))",
            boxShadow: "0 0 0 1px var(--b-600)",
          }} />

          {acts.map((a, i) => (
            <ActSpineRow
              key={a.id}
              act={a}
              isLast={i === acts.length - 1}
              selected={selectedAct?.id === a.id}
              onSelect={() => setSelectedActId(a.id)}
            />
          ))}
          {/* No compiled acts yet → show the faded SHAPE the chronicle will take (the 3-act
              arc), so the spine reads as "waiting to be written" rather than broken. Purely
              illustrative: dimmed, no "you are here", no dates. */}
          {!acts.length && ARC_PREVIEW.map((a, i) => (
            <div key={a.numeral} style={{ position: "relative", paddingBottom: i === ARC_PREVIEW.length - 1 ? 0 : 24, opacity: 0.5 }}>
              <div aria-hidden="true" style={{
                position: "absolute", left: -22, top: 0,
                width: 24, height: 24, borderRadius: "50%",
                background: "radial-gradient(circle at 30% 30%, var(--b-400), color-mix(in oklab, var(--b-400), black 35%))",
                boxShadow: "inset 0 0 0 1px var(--w-500), 0 2px 4px rgba(0,0,0,0.25)",
                display: "grid", placeItems: "center",
                fontFamily: "var(--f-display)", fontSize: 10, color: "var(--w-300)",
                fontStyle: "italic",
              }}>{a.numeral}</div>
              <div style={{
                marginLeft: 12, padding: "8px 14px",
                boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
                borderLeft: "2px dashed rgba(140,100,60,0.4)",
              }}>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.08em", color: "var(--ink-600)", fontStyle: "italic" }}>
                  Act {a.numeral} · {a.name}
                </div>
                <div className="hand muted" style={{ fontSize: 12, marginTop: 2 }}>{a.note}</div>
              </div>
            </div>
          ))}
        </div>
      </Panel>

      {/* RIGHT — Act detail */}
      <Panel framed style={{ padding: 28, overflow: "auto" }}>
        <ActDetail act={selectedAct} surface={surface} />
      </Panel>
      </div>
    </div>
  );
}

function ActSpineRow({ act, isLast, selected, onSelect }) {
  const status = act.status || (act.current ? "current" : "");
  const tone = ["complete", "completed", "resolved"].includes(status) ? "var(--emerald)" :
               ["current", "active"].includes(status) ? "var(--gold-glow)" :
               ["future", "planned"].includes(status) ? "var(--ink-500)" :
               "var(--b-400)";
  return (
    <div style={{ position: "relative", paddingBottom: isLast ? 0 : 24 }}>
      {/* Wax-seal node */}
      <button onClick={onSelect} style={{
        position: "absolute",
        left: -22, top: 0,
        width: 24, height: 24, borderRadius: "50%",
        background: `radial-gradient(circle at 30% 30%, ${tone}, color-mix(in oklab, ${tone}, black 35%))`,
        boxShadow: selected
          ? `inset 0 0 0 2px var(--w-300), 0 0 0 2px ${tone}, 0 0 16px ${tone}, 0 2px 4px rgba(0,0,0,0.4)`
          : `inset 0 0 0 1px var(--w-500), 0 2px 4px rgba(0,0,0,0.3)`,
        cursor: "pointer",
        fontFamily: "var(--f-display)",
        fontSize: 10,
        color: "var(--w-300)",
        animation: act.status === "current" ? "flicker 3s ease-in-out infinite" : "none",
      }}>{act.numeral}</button>

      <button onClick={onSelect} style={{
        width: "100%",
        marginLeft: 12,
        padding: "8px 14px",
        textAlign: "left",
        background: selected ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
        boxShadow: selected
          ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
          : "inset 0 0 0 1px rgba(140,100,60,0.25)",
        cursor: "pointer",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 6 }}>
          <span style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.08em", color: act.status === "future" ? "var(--ink-600)" : "var(--ink-900)" }}>
            {status === "future" ? "?????" : (act.name || act.title)}
          </span>
          <span className="muted" style={{ fontFamily: "var(--f-mono)", fontSize: 10 }}>{act.duration || status}</span>
        </div>
        <div className="hand muted" style={{ fontSize: 12, marginTop: 2 }}>
          {status === "future" ? "the chronicle has not reached this yet" : (act.subtitle || act.summary || `${(act.beats || []).length} beats`)}
        </div>
        {(status === "current" || status === "active" || act.current) && (
          <div style={{ marginTop: 6 }}>
            <Pill tone="crimson" dot>You are here</Pill>
          </div>
        )}
        {["complete", "completed", "resolved"].includes(status) && (
          <div style={{ marginTop: 6, display: "flex", gap: 4 }}>
            <Pill tone="emerald">Resolved</Pill>
            {act.outcome && <span className="hand muted" style={{ fontSize: 11, alignSelf: "center" }}>· {act.outcome}</span>}
          </div>
        )}
      </button>
    </div>
  );
}

function ActDetail({ act, surface }) {
  if (!act) {
    // Honest + inviting empty-state: no acts are compiled yet, so instead of a barren pane
    // we explain WHY (the chronicle is written as you play) and show the SHAPE a campaign
    // takes — the 3-act arc — clearly as a preview, never as fabricated progress.
    return (
      <div>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>The Chronicle</div>
        <h1 className="h1" style={{ fontSize: 26 }}>{surface?.emptyState?.title || "Acts not tracked yet"}</h1>
        <p className="body" style={{ marginTop: 6 }}>
          {surface?.emptyState?.body || "The campaign director has not compiled act progress for this save yet."}
        </p>
        <p className="hand muted" style={{ marginTop: 8, maxWidth: 520 }}>
          Your chronicle is written as you play. Once the campaign director has compiled the
          first act — after a few beats on the road — the acts you've lived will appear here,
          with their key choices, the beats worth remembering, and who walked them with you.
        </p>

        <Divider />

        <SectionTitle ordinal="·">The shape a campaign takes</SectionTitle>
        <p className="body-sm muted" style={{ marginTop: 2, marginBottom: 12 }}>
          Every story here is told in three acts. This is the form your chronicle will fill —
          not what has happened yet.
        </p>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {ARC_PREVIEW.map((a) => (
            <div key={a.numeral} style={{
              display: "grid", gridTemplateColumns: "44px 1fr", gap: 12, alignItems: "center",
              padding: 12, opacity: 0.7,
              background: "rgba(176,141,87,0.06)",
              boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
              borderLeft: "2px dashed rgba(140,100,60,0.45)",
            }}>
              <div aria-hidden="true" style={{
                width: 40, height: 40, borderRadius: "50%", justifySelf: "center",
                background: "radial-gradient(circle at 30% 30%, var(--b-400), color-mix(in oklab, var(--b-400), black 35%))",
                boxShadow: "inset 0 0 0 1px var(--w-500), 0 2px 4px rgba(0,0,0,0.25)",
                display: "grid", placeItems: "center",
                fontFamily: "var(--f-display)", fontSize: 15, color: "var(--w-300)", fontStyle: "italic",
              }}>{a.numeral}</div>
              <div>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 14, letterSpacing: "0.06em", color: "var(--ink-800)" }}>
                  Act {a.numeral} · {a.name}
                </div>
                <div className="hand muted" style={{ fontSize: 13, marginTop: 2 }}>{a.note}</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }
  const status = act.status || (act.current ? "current" : "");
  if (status === "future") {
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100%", textAlign: "center" }}>
        <div>
          <div style={{ fontSize: 56, color: "var(--crimson)", fontFamily: "var(--f-display)" }}>?</div>
          <h2 className="h1" style={{ fontSize: 22 }}>Not yet written</h2>
          <p className="hand muted" style={{ marginTop: 6, maxWidth: 380 }}>
            The chronicle holds the page in reserve. What it will say depends on the road taken.
          </p>
        </div>
      </div>
    );
  }
  // Resolve the choice + beat lists once so A-07 can collapse them into a single empty-state
  // when BOTH are empty (same data sources the two sub-sections render from).
  const choices = act.choices || surface?.majorChoices || [];
  const beats = act.memories || act.beats || surface?.threads || [];
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--crimson)" }}>Act {act.numeral || act.id}{(status === "current" || status === "active") ? " · in progress" : ""}</div>
      <h1 className="h1" style={{ fontSize: 28 }}>{act.name || act.title}</h1>
      <div className="hand" style={{ fontSize: 16, color: "var(--ink-700)" }}>{act.subtitle || status}</div>

      <Divider />

      {act.illustration && (
        <Img scope={act.imageScope || ("act-" + act.id)} label={`illustration · ${act.illustration}`} h={140} fit="cover" framed style={{ width: "100%", marginBottom: 16 }} />
      )}

      <p className="body dropcap">{act.synopsis || act.summary || "This act has no player-facing synopsis yet."}</p>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginTop: 18 }}>
        <StatLine k="Began" v={act.beginDate} />
        <StatLine k="Through" v={act.endDate || "still"} />
        <StatLine k="Hero Lv" v={act.heroLevel} />
      </div>

      <Divider />

      {/* A-07: "Key choices" + "Beats and callbacks" are each empty-able. When BOTH are empty
          (the common case for a freshly-compiled act), collapse them into a single empty-state
          instead of stacking two near-identical "nothing yet" sub-sections. */}
      {choices.length + beats.length === 0 ? (
        <div className="hand muted">
          No turning points or memorable beats have been tracked for this act yet. The road still has shape to give.
        </div>
      ) : (
        <>
          <SectionTitle ordinal="·">Key choices made</SectionTitle>
          {!choices.length ? (
            <div className="hand muted">No turning points yet. The road still has shape to give.</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {choices.map((c, i) => (
                <div key={i} style={{
                  padding: 10,
                  background: "rgba(176,141,87,0.08)",
                  boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
                  borderLeft: `3px solid ${c.tone === "good" ? "var(--royal)" : c.tone === "ill" ? "var(--crimson)" : "var(--b-400)"}`,
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
                      {c.title}
                    </span>
                    <span className="muted" style={{ fontFamily: "var(--f-mono)", fontSize: 10 }}>{c.when}</span>
                  </div>
                  <div className="body-sm" style={{ color: "var(--ink-700)", marginTop: 4 }}>{c.body}</div>
                  {c.consequence && (
                    <div className="hand muted" style={{ fontSize: 12, marginTop: 4 }}>
                      → {c.consequence}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <Divider />

          <SectionTitle>Beats and callbacks</SectionTitle>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            {beats.map((m, i) => (
              <div key={i} style={{
                padding: 10,
                background: "rgba(95, 75, 45, 0.06)",
                boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
              }}>
                <Img scope={m.imageScope || ("beat-" + (m.id != null ? m.id : i))} label={`chronicle · ${m.sketch || m.status || m.label || "beat"}`} h={70} fit="cover" framed />
                <div className="hand" style={{ fontSize: 13, marginTop: 6, color: "var(--ink-700)", fontStyle: "italic" }}>
                  "{m.text || m.title || m.questTitle || m.note}"
                </div>
                <div className="muted" style={{ fontFamily: "var(--f-mono)", fontSize: 9, marginTop: 4 }}>
                  {m.when || (m.triggerDay ? `day ${m.triggerDay}` : m.status)}
                </div>
              </div>
            ))}
            {!beats.length && <div className="body-sm muted">No beats have been tracked for this act yet.</div>}
          </div>
        </>
      )}

      {act.partyAtStart && (
        <>
          <Divider />
          <SectionTitle>Who walked this act</SectionTitle>
          <div style={{ display: "flex", gap: 8 }}>
            {act.partyAtStart.map((p, i) => (
              <div key={i} style={{ textAlign: "center", flex: 1 }}>
                <Img scope={aPortraitScope(p)} label={p.name || p.short} w="100%" h={60} fit="cover" framed />
                <div className="hand" style={{ fontSize: 11, marginTop: 4, color: "var(--ink-700)" }}>{p.name}</div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

Object.assign(window, { ScreenActs, ActSpineRow, ActDetail, aPortraitScope, ARC_PREVIEW });
