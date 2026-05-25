/* Screen: Quest Journal — handwritten chronicle / two-page spread */

function ScreenJournal({ onNavigate, state, setState }) {
  const [activeQuest, setActiveQuest] = React.useState(state.quests[0].id);
  const [tab, setTab] = React.useState("active");
  const quest = state.quests.find((q) => q.id === activeQuest);

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
          {state.quests.filter((q) => {
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
        </div>
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
