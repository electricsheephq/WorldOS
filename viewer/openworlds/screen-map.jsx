/* Screen: World Map — hand-drawn cartography with location nodes */

function ScreenMap({ onNavigate, state, setState, campMode, setCampMode }) {
  const locations = Array.isArray(state?.locations) ? state.locations : [];
  const party = Array.isArray(state?.party) ? state.party : [];
  const [selected, setSelected] = React.useState(() => locations.find((l) => l.current) || locations[0] || null);
  const [time, setTime] = React.useState("dusk");
  const [talkPartner, setTalkPartner] = React.useState(null);
  const toast = window.useToast ? window.useToast() : (() => {});

  React.useEffect(() => {
    if (locations.length === 0) {
      if (selected) setSelected(null);
      return;
    }
    if (!selected || !locations.find((l) => l.id === selected.id)) {
      setSelected(locations.find((l) => l.current) || locations[0] || null);
    }
  }, [locations, selected?.id]);

  const beginRest = () => {
    toast({ kind: "rest", eyebrow: "Long rest", title: "The camp settles", body: "10 hours pass. Wounds knit. Spells return. The road is patient." });
    setCampMode && setCampMode(false);
    setTalkPartner(null);
    setTimeout(() => onNavigate("character"), 600);
  };

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "1fr 340px", gap: 14, padding: 14 }}>

      {/* MAP CANVAS */}
      <Panel framed style={{ padding: 18, position: "relative", display: "flex", flexDirection: "column", minHeight: 0, overflow: "hidden" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10, position: "relative", zIndex: 3, flex: "0 0 auto" }}>
          <div>
            <div className="eyebrow" style={{ color: "var(--crimson)" }}>The Stolen Marches</div>
            <h1 className="h1" style={{ fontSize: 24 }}>{campMode ? "Camp" : "World Atlas"}</h1>
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            {!campMode && ["dawn", "day", "dusk", "night"].map((t) => (
              <button key={t} onClick={() => setTime(t)} className="pill" style={{
                cursor: "pointer",
                background: time === t ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(176,141,87,0.1)",
                color: time === t ? "var(--w-300)" : "var(--ink-700)",
                boxShadow: time === t ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)" : "inset 0 0 0 1px rgba(140,100,60,0.3)",
              }}>{t}</button>
            ))}
            <BrassButton size="sm" tone={campMode ? "" : "ghost"} onClick={() => setCampMode && setCampMode(!campMode)}>
              {campMode ? "✺ Camped" : "Make Camp"}
            </BrassButton>
          </div>
        </div>

        {/* Map surface */}
        <div style={{ position: "relative", flex: "1 1 auto", minHeight: 0 }}>
          {/* Aged map background */}
          <div style={{
            position: "absolute", inset: 0,
            background:
              `radial-gradient(ellipse at 30% 25%, rgba(120, 80, 30, 0.25), transparent 50%),
               radial-gradient(ellipse at 75% 70%, rgba(100, 60, 20, 0.3), transparent 55%),
               radial-gradient(ellipse at 15% 80%, rgba(160, 110, 60, 0.18), transparent 40%),
               linear-gradient(135deg, #c8a878 0%, #b89868 40%, #a08055 100%)`,
            boxShadow: "inset 0 0 80px rgba(60, 30, 10, 0.6)",
          }}>
            {/* Terrain pattern overlay using SVG */}
            <svg width="100%" height="100%" viewBox="0 0 1000 600" preserveAspectRatio="xMidYMid slice" style={{ position: "absolute", inset: 0, opacity: 0.6 }}>
              <defs>
                <pattern id="forest" x="0" y="0" width="20" height="20" patternUnits="userSpaceOnUse">
                  <path d="M10 4 L13 14 L7 14 Z" fill="rgba(40,60,30,0.6)" />
                </pattern>
                <pattern id="hills" x="0" y="0" width="40" height="20" patternUnits="userSpaceOnUse">
                  <path d="M0 18 Q 10 10 20 18 T 40 18" fill="none" stroke="rgba(90, 50, 20, 0.4)" strokeWidth="1" />
                </pattern>
                <pattern id="water" x="0" y="0" width="20" height="14" patternUnits="userSpaceOnUse">
                  <path d="M0 7 Q 5 4 10 7 T 20 7" fill="none" stroke="rgba(30, 60, 90, 0.5)" strokeWidth="1" />
                </pattern>
                <filter id="paper">
                  <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" />
                  <feColorMatrix values="0 0 0 0 0.4  0 0 0 0 0.25  0 0 0 0 0.1  0 0 0 0.15 0" />
                  <feComposite in2="SourceGraphic" operator="in" />
                </filter>
              </defs>

              {/* Forest blobs */}
              <path d="M80 100 Q 150 80 220 110 T 380 120 Q 420 200 360 240 T 200 280 Q 100 260 80 180 Z" fill="url(#forest)" opacity="0.7" />
              <path d="M520 80 Q 620 60 700 100 T 880 130 Q 900 220 820 260 T 620 280 Q 540 240 520 160 Z" fill="url(#forest)" opacity="0.55" />
              <path d="M200 380 Q 300 360 380 400 T 540 440 Q 500 500 380 510 T 200 480 Q 160 440 200 380 Z" fill="url(#forest)" opacity="0.6" />

              {/* Hills */}
              <rect x="600" y="350" width="350" height="80" fill="url(#hills)" opacity="0.5" />
              <rect x="50" y="450" width="200" height="60" fill="url(#hills)" opacity="0.4" />

              {/* River */}
              <path d="M -20 200 Q 200 220 360 260 T 700 320 Q 850 360 1020 380" stroke="rgba(60, 100, 130, 0.7)" strokeWidth="6" fill="none" />
              <path d="M -20 200 Q 200 220 360 260 T 700 320 Q 850 360 1020 380" fill="url(#water)" stroke="none" />

              {/* Roads */}
              <path d="M 100 540 Q 220 480 320 460 T 540 380 Q 680 320 820 280" stroke="rgba(80, 40, 10, 0.7)" strokeWidth="2" strokeDasharray="4 4" fill="none" />
              <path d="M 540 380 Q 600 300 660 200 T 720 60" stroke="rgba(80, 40, 10, 0.7)" strokeWidth="2" strokeDasharray="4 4" fill="none" />

              {/* Compass rose */}
              <g transform="translate(900, 510)" opacity="0.85">
                <circle r="40" fill="none" stroke="rgba(60,30,10,0.6)" strokeWidth="1" />
                <circle r="32" fill="none" stroke="rgba(60,30,10,0.4)" strokeWidth="0.5" />
                <path d="M 0 -38 L 4 0 L 0 38 L -4 0 Z" fill="rgba(120, 30, 30, 0.7)" />
                <path d="M -38 0 L 0 -4 L 38 0 L 0 4 Z" fill="rgba(60,30,10,0.7)" />
                <text y="-44" textAnchor="middle" fontFamily="Cinzel" fontSize="9" fill="rgba(60,30,10,0.9)">N</text>
                <text y="52" textAnchor="middle" fontFamily="Cinzel" fontSize="9" fill="rgba(60,30,10,0.9)">S</text>
                <text x="-48" y="3" textAnchor="middle" fontFamily="Cinzel" fontSize="9" fill="rgba(60,30,10,0.9)">W</text>
                <text x="48" y="3" textAnchor="middle" fontFamily="Cinzel" fontSize="9" fill="rgba(60,30,10,0.9)">E</text>
              </g>

              {/* Region label */}
              <text x="500" y="80" textAnchor="middle" fontFamily="Cinzel" fontSize="36" fill="rgba(60, 30, 10, 0.25)" letterSpacing="8" fontWeight="600">THE STOLEN MARCHES</text>
              <text x="180" y="500" textAnchor="middle" fontFamily="Cinzel" fontSize="20" fill="rgba(60, 30, 10, 0.3)" letterSpacing="4">Thorn River</text>
              <text x="800" y="450" textAnchor="middle" fontFamily="Cinzel" fontSize="18" fill="rgba(60, 30, 10, 0.3)" letterSpacing="3">Old Hills</text>

              {/* Roads/connections between locations */}
              {locations.map((loc) =>
                loc.connections?.map((cId) => {
                  const target = locations.find((l) => l.id === cId);
                  if (!target) return null;
                  return (
                    <line key={`${loc.id}-${cId}`}
                      x1={loc.x * 10} y1={loc.y * 6}
                      x2={target.x * 10} y2={target.y * 6}
                      stroke="rgba(60, 30, 10, 0.4)" strokeWidth="1.5" strokeDasharray="3 4" />
                  );
                })
              )}
            </svg>

            {/* Time-of-day overlay */}
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

            {/* Location nodes */}
            {locations.map((loc) => (
              <button
                key={loc.id}
                onClick={() => setSelected(loc)}
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
                <LocationPin loc={loc} selected={selected?.id === loc.id} time={time} />
              </button>
            ))}
          </div>
        </div>

        {/* Bottom party portraits strip — now a separate row in the flex column */}
        <div style={{
          marginTop: 10,
          display: "flex", gap: 6,
          padding: 8,
          background: "linear-gradient(180deg, var(--w-100), var(--w-300))",
          boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 0 2px var(--b-500), inset 0 0 0 3px var(--w-200)",
          zIndex: 3,
          flex: "0 0 auto",
        }}>
          {party.map((p) => (
            <div key={p.id} style={{ width: 48, height: 48, position: "relative" }}>
              <Placeholder label={p.short} w="100%" h="100%" framed />
              <div style={{
                position: "absolute", bottom: 0, left: 0, right: 0,
                height: 3,
                background: "linear-gradient(90deg, #2a8c39, #5cd56a)",
                width: `${(p.hp / p.hpMax) * 100}%`,
              }} />
            </div>
          ))}
          <div style={{ flex: 1 }} />
          <div style={{ color: "var(--b-200)", fontFamily: "var(--f-display)", fontSize: 10, letterSpacing: "0.18em", textTransform: "uppercase", alignSelf: "center", padding: "0 8px" }}>
            Day 12 · {time} · 29 Gozran
          </div>
        </div>
      </Panel>

      {/* SIDE — Location detail OR Camp panel */}
      {campMode ? (
        <window.CampSidebar
          state={state}
          onExit={() => { setCampMode && setCampMode(false); setTalkPartner(null); }}
          onBeginRest={beginRest}
          onTalk={setTalkPartner}
          talkPartner={talkPartner}
        />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
          <Panel framed style={{ padding: 22 }}>
            {selected ? (
              <>
                <div className="eyebrow" style={{ color: "var(--crimson)" }}>{selected.region || "The Stolen Marches"}</div>
                <h2 className="h1" style={{ fontSize: 22 }}>{selected.name}</h2>
                <div className="hand" style={{ fontSize: 14, marginTop: 2 }}>{selected.distance} · {selected.travel}</div>

                <Divider />

                <Placeholder label={`${selected.short || "location vignette"} · painted`} h={120} framed />

                <p className="body dropcap" style={{ marginTop: 12, fontSize: 15 }}>
                  {selected.description}
                </p>

                <Divider />

                <div className="tag-row">
                  {selected.tags.map((t) => <Pill key={t} tone={t === "danger" ? "crimson" : t === "rest" ? "emerald" : ""}>{t}</Pill>)}
                </div>

                <div style={{ display: "flex", gap: 6, marginTop: 18 }}>
                  <BrassButton onClick={() => {
                    toast({ kind: "quest", title: "The party travels to " + selected.name, body: selected.travel + " · " + selected.distance });
                    onNavigate("table");
                  }}>Travel here</BrassButton>
                  <BrassButton tone="ghost" size="sm" onClick={() => toast({ title: "Marked: " + selected.name, body: "Linzi makes a note." })}>Mark</BrassButton>
                </div>
              </>
            ) : <div className="muted">Pick a place upon the map.</div>}
          </Panel>

          <Panel framed style={{ padding: 22, flex: 1, overflow: "auto" }}>
            <SectionTitle>Discovered</SectionTitle>
            <div className="body-sm" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {locations.filter((l) => l.discovered).map((l) => (
                <button key={l.id} onClick={() => setSelected(l)} style={{
                  display: "flex", justifyContent: "space-between", textAlign: "left",
                  padding: "8px 12px", cursor: "pointer",
                  background: selected?.id === l.id ? "rgba(176,141,87,0.18)" : "transparent",
                  boxShadow: "inset 0 -1px 0 rgba(140,100,60,0.2)",
                }}>
                  <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.08em", color: "var(--ink-900)" }}>{l.name}</span>
                  <span className="muted" style={{ fontFamily: "var(--f-mono)", fontSize: 10 }}>{l.distance}</span>
                </button>
              ))}
            </div>
          </Panel>
        </div>
      )}
    </div>
  );
}

function LocationPin({ loc, selected, time }) {
  const isCurrent = loc.current;
  const isVisited = loc.visited;
  const [hover, setHover] = React.useState(false);

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4, position: "relative" }}
    >
      {/* Banner label */}
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
        {loc.name}
      </div>
      {/* Pin */}
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

      {/* Hover preview card */}
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
          <Placeholder label={loc.short || "vignette"} h={70} framed style={{ width: "100%" }} />
          <div className="eyebrow" style={{ color: "var(--crimson)", marginTop: 8, fontSize: 9 }}>
            {loc.region || "Unknown reach"}
          </div>
          <div style={{ fontFamily: "var(--f-display)", fontSize: 12, color: "var(--ink-900)", letterSpacing: "0.06em", marginTop: 2 }}>
            {loc.name}
          </div>
          <div className="hand" style={{ fontSize: 12, color: "var(--ink-600)", marginTop: 2 }}>
            {loc.distance} · {loc.travel}
          </div>
          {loc.tags && (
            <div style={{ display: "flex", gap: 4, marginTop: 6, flexWrap: "wrap" }}>
              {loc.tags.slice(0, 3).map((t) => <Pill key={t} tone={t === "danger" ? "crimson" : t === "rest" ? "emerald" : ""}>{t}</Pill>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { ScreenMap, LocationPin });
