/* Screen: Quest Journal — handwritten chronicle / two-page spread.
   Wired to the live /journal-surface read model (tracked quests + unresolved hooks as
   rumors + the Campaign Director advisory). Polls every 5s while visible; degrades to a
   graceful empty when there is no snapshot. Design unchanged from the prototype. */

function ScreenJournal({ onNavigate, state, setState }) {
  const surfaceQuery = window.combatSurfaceFromCampaign
    ? window.combatSurfaceFromCampaign(
        (Array.isArray(state?.campaigns) ? state.campaigns : []).find((c) => c.id === state?.activeCampaign) ||
          (Array.isArray(state?.campaigns) ? state.campaigns : [])[0] || {},
        state,
      )
    : "";
  const [surface, setSurface] = React.useState(null);
  const surfaceQuests = Array.isArray(surface?.quests) ? surface.quests : null;
  const quests = surfaceQuests || (Array.isArray(state?.quests) ? state.quests : []);
  const advisory = surface?.directorAdvisory || { debts: [], total_debts: 0 };
  // Scheduled quest-evolution callbacks (#120) — the "this thread will return" threads.
  const threads = Array.isArray(surface?.threads) ? surface.threads : [];
  const [activeQuest, setActiveQuest] = React.useState("");
  const [tab, setTab] = React.useState("active");

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    try {
      const response = await fetch("/journal-surface" + surfaceQuery, { cache: "no-store" });
      if (!response.ok) throw new Error(`journal surface ${response.status}`);
      const payload = await response.json();
      if (isCancelled()) return;
      setSurface(payload);
    } catch (error) {
      if (isCancelled()) return;
      /* keep last good surface; the demo fallback shows until first success */
    }
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

  React.useEffect(() => {
    if (quests.length && !quests.some((q) => q.id === activeQuest)) {
      setActiveQuest(quests[0]?.id || "");
    }
  }, [quests, activeQuest]);

  const quest = quests.find((q) => q.id === activeQuest) || quests[0] || {
    label: "Empty",
    title: "No quest selected",
    entry: "No quests have been recorded yet.",
    objectives: [],
  };

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "300px 1fr", gap: 14, padding: 14 }}>

      {/* LEFT — Quest list */}
      <Panel framed style={{ padding: 22, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <SectionTitle ordinal="·">Chronicle</SectionTitle>

        {/* Tabs */}
        <div style={{ display: "flex", gap: 4, marginBottom: 12 }}>
          {[
            { id: "active", label: "Active" },
            { id: "complete", label: "Past" },
            { id: "rumor", label: "Rumors" },
          ].map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)} className="pill" style={{
              cursor: "pointer", flex: 1, textAlign: "center",
              background: tab === t.id ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(176,141,87,0.08)",
              color: tab === t.id ? "var(--w-300)" : "var(--ink-700)",
              boxShadow: tab === t.id ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)" : "inset 0 0 0 1px rgba(140,100,60,0.3)",
            }}>{t.label}</button>
          ))}
        </div>

        <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
          {quests.filter((q) => {
            if (tab === "active") return q.status === "active";
            if (tab === "complete") return q.status === "complete";
            return q.status === "rumor";
          }).map((q) => (
            <button key={q.id} onClick={() => setActiveQuest(q.id)} style={{
              textAlign: "left",
              padding: "10px 12px",
              background: activeQuest === q.id ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
              boxShadow: activeQuest === q.id
                ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
                : "inset 0 0 0 1px rgba(140,100,60,0.2)",
              cursor: "pointer",
            }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <span style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
                  {q.title}
                </span>
                <Pill tone={q.tone}>{q.label}</Pill>
              </div>
              <div className="hand" style={{ fontSize: 13, color: "var(--ink-600)", marginTop: 4 }}>{q.objective}</div>
            </button>
          ))}
          {!quests.filter((q) => (tab === "active" ? q.status === "active" : tab === "complete" ? q.status === "complete" : q.status === "rumor")).length && (
            <div className="body-sm muted" style={{ padding: "8px 4px" }}>
              {tab === "active" ? "No active quests in the chronicle yet." : tab === "complete" ? "Nothing has been resolved or failed yet." : "No rumors or untracked hooks."}
            </div>
          )}
        </div>

        {/* GM Advisory — Campaign Director (#72): structural debts the campaign owes. */}
        {Array.isArray(advisory.debts) && advisory.debts.length > 0 && (
          <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px dashed rgba(80,50,20,0.3)" }}>
            <div className="eyebrow" style={{ color: "var(--crimson)", marginBottom: 6 }}>
              GM Advisory{advisory.total_debts ? ` · ${advisory.total_debts}` : ""}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {advisory.debts.map((d) => (
                <div key={d.id} style={{
                  padding: "7px 9px",
                  background: "rgba(176,141,87,0.06)",
                  boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25), inset 3px 0 0 " +
                    (d.severity === "high" ? "var(--crimson)" : d.severity === "low" ? "var(--b-400)" : "var(--royal)"),
                }}>
                  <div className="eyebrow" style={{ fontSize: 8, color: "var(--ink-600)" }}>
                    {(d.kind || "debt").replace(/_/g, " ")}
                  </div>
                  <div className="hand" style={{ fontSize: 12, color: "var(--ink-700)", marginTop: 2 }}>{d.nudge}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </Panel>

      {/* RIGHT — Two-page spread */}
      <div style={{
        position: "relative",
        background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
        boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 0 0 5px var(--p-200), inset 0 0 0 6px var(--b-400), 0 6px 14px rgba(60, 40, 20, 0.25)",
        padding: 36,
        overflow: "auto",
      }}>
        <CornerOrnament corner="tl" />
        <CornerOrnament corner="tr" />
        <CornerOrnament corner="bl" />
        <CornerOrnament corner="br" />

        {/* Spine */}
        <div style={{
          position: "absolute", top: 16, bottom: 16, left: "50%",
          width: 28, transform: "translateX(-50%)",
          background:
            `linear-gradient(90deg,
              transparent 0%,
              rgba(80, 50, 20, 0.15) 30%,
              rgba(80, 50, 20, 0.35) 50%,
              rgba(80, 50, 20, 0.15) 70%,
              transparent 100%)`,
          pointerEvents: "none",
        }} />

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 56, position: "relative" }}>
          {/* LEFT PAGE — quest narrative */}
          <div>
            <div className="eyebrow" style={{ color: "var(--crimson)" }}>{quest.label} · {quest.region || "The Stolen Marches"}</div>
            <h1 className="h1" style={{ fontSize: 28, marginTop: 4 }}>{quest.title}</h1>
            <div className="hand" style={{ fontSize: 15, color: "var(--ink-600)", marginTop: 2 }}>
              Inscribed {quest.dateOpened || "Day 9 of Gozran"}
            </div>

            {/* Rule-of-three evolution badge (#120): a quest carrying an evolves_to hook
                will echo back. Display-only telegraph; icon-free. */}
            {quest.evolvesTo && (
              <div style={{ marginTop: 8 }}>
                <Pill tone="royal">
                  {quest.callbackInDays > 0
                    ? `This thread will return · echoes in ${quest.callbackInDays} day${quest.callbackInDays === 1 ? "" : "s"}`
                    : "This thread will return"}
                </Pill>
              </div>
            )}

            <Divider />

            <p className="body dropcap" style={{ marginTop: 0 }}>
              {quest.entry}
            </p>

            {quest.entries?.map((e, i) => (
              <div key={i} style={{ marginTop: 18 }}>
                <div className="eyebrow" style={{ color: "var(--crimson)" }}>{e.date}</div>
                <p className="body" style={{ marginTop: 4 }}>{e.text}</p>
              </div>
            ))}

            {/* Sketch */}
            {quest.sketch && (
              <div style={{ marginTop: 20, padding: 6, transform: "rotate(-1deg)" }}>
                <Placeholder label={`sketch · ${quest.sketch}`} h={140} framed />
                <div className="hand" style={{ textAlign: "center", fontSize: 12, marginTop: 6, color: "var(--ink-700)" }}>
                  fig. {Math.floor(Math.random() * 9) + 1} — {quest.sketch}
                </div>
              </div>
            )}

            <div style={{ marginTop: 20, fontFamily: "var(--f-hand)", fontSize: 13, color: "var(--ink-600)", borderTop: "1px dashed rgba(80,50,20,0.3)", paddingTop: 8 }}>
              — Linzi, scribe and reluctant cartographer
            </div>
          </div>

          {/* RIGHT PAGE — objectives, NPCs, related */}
          <div>
            <div className="eyebrow" style={{ color: "var(--crimson)" }}>Objectives</div>
            <h2 className="h2" style={{ marginTop: 4, fontSize: 16 }}>What must be done</h2>

            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
              {quest.objectives.map((o, i) => (
                <div key={i} style={{
                  display: "flex", gap: 10, alignItems: "flex-start",
                  padding: "8px 12px",
                  background: o.done ? "rgba(120, 100, 60, 0.1)" : "rgba(176,141,87,0.06)",
                  boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
                }}>
                  <span style={{
                    width: 18, height: 18, marginTop: 2,
                    background: o.done ? "var(--emerald)" : "transparent",
                    boxShadow: "inset 0 0 0 1px var(--b-500)",
                    color: "var(--p-100)",
                    display: "grid", placeItems: "center",
                    fontSize: 11,
                  }}>{o.done ? "✓" : ""}</span>
                  <div style={{ flex: 1 }}>
                    <div style={{ textDecoration: o.done ? "line-through" : "none", color: o.done ? "var(--ink-600)" : "var(--ink-800)" }}>
                      {o.text}
                    </div>
                    {o.note && <div className="hand muted" style={{ fontSize: 12, marginTop: 2 }}>{o.note}</div>}
                  </div>
                </div>
              ))}
            </div>

            <Divider />

            <div className="eyebrow" style={{ color: "var(--crimson)" }}>Of note</div>
            <h2 className="h2" style={{ marginTop: 4, fontSize: 16 }}>Names mentioned</h2>
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
              {quest.npcs?.map((n) => (
                <div key={n.name} style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <Placeholder label={n.short} w={36} h={44} framed />
                  <div>
                    <div style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.08em", color: "var(--ink-900)" }}>{n.name}</div>
                    <div className="hand muted" style={{ fontSize: 12 }}>{n.role}</div>
                  </div>
                </div>
              ))}
            </div>

            {/* Threads & Callbacks (#120): scheduled quest-evolution echoes — a resolved
                quest's pending "this thread will return" callback. Display-only. */}
            {threads.length > 0 && (
              <>
                <Divider />
                <div className="eyebrow" style={{ color: "var(--crimson)" }}>Threads & Callbacks</div>
                <h2 className="h2" style={{ marginTop: 4, fontSize: 16 }}>What will return</h2>
                <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
                  {threads.map((t) => {
                    const tone = t.status === "due" ? "var(--crimson)" : t.status === "fired" ? "var(--emerald)" : "var(--royal)";
                    return (
                      <div key={t.id} style={{
                        padding: "8px 10px",
                        background: "rgba(176,141,87,0.06)",
                        boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25), inset 3px 0 0 " + tone,
                      }}>
                        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
                          <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.06em", color: "var(--ink-900)" }}>
                            {t.questTitle}
                          </span>
                          <span className="eyebrow" style={{ fontSize: 8, color: "var(--ink-600)" }}>{t.label}</span>
                        </div>
                        <div className="hand" style={{ fontSize: 12, color: "var(--ink-700)", marginTop: 2 }}>{t.note}</div>
                      </div>
                    );
                  })}
                </div>
              </>
            )}

            {/* Wax seal */}
            <div style={{ marginTop: 24, display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{
                width: 60, height: 60, borderRadius: "50%",
                background: `radial-gradient(circle at 30% 30%, #c54040, var(--crimson) 50%, #3a0a0a)`,
                boxShadow: "inset 0 0 0 1px #2a0606, 0 2px 6px rgba(0,0,0,0.4), inset 0 2px 6px rgba(255,200,200,0.2)",
                color: "rgba(255, 200, 200, 0.85)",
                fontFamily: "var(--f-display)",
                fontSize: 9,
                letterSpacing: "0.2em",
                display: "grid", placeItems: "center",
                textAlign: "center",
                lineHeight: 1,
                transform: "rotate(-12deg)",
              }}>
                OPEN<br/>WORLDS
              </div>
              <div>
                <div className="eyebrow" style={{ color: "var(--crimson)" }}>Reward</div>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 14, color: "var(--ink-900)" }}>
                  {quest.reward || "750 XP · 120 gp"}
                </div>
                <div className="hand muted" style={{ fontSize: 12, marginTop: 2 }}>and one quiet road, hopefully</div>
              </div>
            </div>

            <div style={{ display: "flex", gap: 6, marginTop: 18, flexWrap: "wrap" }}>
              <BrassButton onClick={() => onNavigate("map")} size="sm">Show on map</BrassButton>
              <BrassButton tone="ghost" size="sm">Bookmark</BrassButton>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ScreenJournal });
