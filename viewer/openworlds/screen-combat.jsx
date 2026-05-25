/* Screen: Combat Encounter — tactical grid, initiative, action bar */

function ScreenCombat({ onNavigate, state, setState }) {
  const [selectedToken, setSelectedToken] = React.useState("cassian");
  const [activeAction, setActiveAction] = React.useState(null);
  const [round, setRound] = React.useState(2);
  const [ap, setAp] = React.useState({ standardUsed: false, moveUsed: false, swiftUsed: false });
  const toast = window.useToast ? window.useToast() : (() => {});
  const [log, setLog] = React.useState([
    { t: "round", text: "Round 1 — initiative." },
    { t: "act", who: "Mira", text: "moves to G4 and looses a crossbow bolt at the south bandit." },
    { t: "roll", text: "Attack roll: d20+5 = 19. Hit." },
    { t: "dmg", text: "1d8+2 piercing = 6. Bandit south staggered." },
    { t: "act", who: "Cassian", text: "casts shocking grasp, charges the courtyard." },
    { t: "roll", text: "Concentration check: 18. Spell held." },
    { t: "round", text: "Round 2 — initiative continues." },
    { t: "act", who: "Bandit North", text: "advances. Shortbow at Vell. Misses (13 vs AC 18)." },
  ]);

  const tokens = TOKENS;
  const selected = tokens.find((t) => t.id === selectedToken);

  const onMove = (gx, gy) => {
    if (!activeAction || activeAction !== "move") return;
    selected.x = gx;
    selected.y = gy;
    setLog((l) => [...l, { t: "act", who: selected.name, text: `moves to ${String.fromCharCode(64 + gx)}${gy}.` }]);
    setAp({ ...ap, moveUsed: true });
    setActiveAction(null);
    toast({ kind: "rest", eyebrow: "Action", title: selected.name + " moves", body: `to ${String.fromCharCode(64 + gx)}${gy}` });
  };

  const endTurn = () => {
    setRound(round + 1);
    setAp({ standardUsed: false, moveUsed: false, swiftUsed: false });
    toast({ eyebrow: "Round", title: "Round " + (round + 1) + " begins" });
  };

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "1fr 280px", gap: 14, padding: 14 }}>

      <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
        {/* Battle map */}
        <Panel framed style={{ padding: 18, position: "relative", flex: "1 1 auto", minHeight: 0, overflow: "hidden" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
            <div>
              <div className="eyebrow" style={{ color: "var(--crimson)" }}>Encounter · Round {round}</div>
              <h2 className="h1" style={{ fontSize: 22 }}>Lanternrest Courtyard</h2>
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <Pill tone="emerald" dot>Surprise broken</Pill>
              <Pill dot>Cover: light</Pill>
              <BrassButton size="sm" tone="ghost">↩ Undo</BrassButton>
              <BrassButton size="sm" onClick={() => onNavigate("table")}>End encounter</BrassButton>
            </div>
          </div>

          <CombatMap tokens={tokens} selected={selectedToken} onSelect={setSelectedToken} activeAction={activeAction} onMove={onMove} />
        </Panel>

        {/* Action bar */}
        <Panel framed style={{ padding: 14, flex: "0 0 auto" }}>
          <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 16, alignItems: "center" }}>
            <div style={{
              display: "flex", gap: 10, alignItems: "center",
              padding: "8px 12px",
              background: "linear-gradient(180deg, var(--p-100), var(--p-200))",
              boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)",
            }}>
              <Placeholder label={selected.short || "token"} w={40} h={48} framed />
              <div>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
                  {selected.name}
                </div>
                <div style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-700)", marginTop: 2 }}>
                  HP {selected.hp}/{selected.hpMax} · AC {selected.ac}
                </div>
                <div style={{ marginTop: 4, display: "flex", gap: 6 }}>
                  <ApBadge used={ap.standardUsed} label="Std" />
                  <ApBadge used={ap.moveUsed} label="Move" />
                  <ApBadge used={ap.swiftUsed} label="Sw" />
                </div>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 6 }}>
              <ActionTile icon="↗" label="Move" hint={ap.moveUsed ? "spent" : "6 squares"} onClick={() => !ap.moveUsed && setActiveAction("move")} active={activeAction === "move"} spent={ap.moveUsed} />
              <ActionTile icon="⚔" label="Attack" hint={ap.standardUsed ? "spent" : "d20+5 · 1d8+2"} onClick={() => !ap.standardUsed && (setActiveAction("attack"), setAp({ ...ap, standardUsed: true }), toast({ eyebrow: "Attack", title: selected.name + " attacks", body: "d20+5 vs AC 15: 18 — hit. 7 piercing." }))} active={activeAction === "attack"} spent={ap.standardUsed} />
              <ActionTile icon="✦" label="Cast" hint={ap.standardUsed ? "spent" : "Shocking Grasp"} onClick={() => !ap.standardUsed && (setActiveAction("cast"), setAp({ ...ap, standardUsed: true }), toast({ kind: "item", eyebrow: "Spell", title: selected.name + " casts Shocking Grasp", body: "Held in the off hand. Touch attack next." }))} active={activeAction === "cast"} spent={ap.standardUsed} />
              <ActionTile icon="◈" label="Defend" hint="+4 AC, end turn" onClick={() => { toast({ eyebrow: "Stance", title: selected.name + " defends", body: "+4 AC until next turn." }); endTurn(); }} />
              <ActionTile icon="◊" label="Item" hint="3 in belt" />
              <ActionTile icon="✺" label="Dodge" hint={ap.swiftUsed ? "spent" : "opt-out reactions"} onClick={() => !ap.swiftUsed && setAp({ ...ap, swiftUsed: true })} spent={ap.swiftUsed} />
              <ActionTile icon="⊘" label="End turn" hint="advance order" onClick={endTurn} />
            </div>
          </div>

          {activeAction === "move" && (
            <div className="hand" style={{ marginTop: 10, fontSize: 14, color: "var(--crimson)" }}>
              Pick a tile within 6 squares. Yellow = within reach. Esc to cancel.
            </div>
          )}
        </Panel>
      </div>

      {/* RIGHT — initiative + log */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
        <Panel framed style={{ padding: 18 }}>
          <SectionTitle>Initiative</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {INITIATIVE.map((t) => {
              const tok = tokens.find((x) => x.id === t.id) || t;
              const isFoe = tok.team === "foe";
              const isActive = t.active;
              const hpRatio = tok.hp ? tok.hp / tok.hpMax : 1;
              return (
                <button key={t.id} onClick={() => setSelectedToken(t.id)} style={{
                  display: "grid", gridTemplateColumns: "28px 36px 1fr auto", gap: 8, alignItems: "center",
                  padding: "6px 10px",
                  background: isActive
                    ? "linear-gradient(180deg, var(--p-100), var(--p-200))"
                    : selectedToken === t.id ? "rgba(176,141,87,0.18)" : "transparent",
                  boxShadow: isActive
                    ? "inset 0 0 0 1px var(--b-500), 0 0 16px -6px var(--gold-glow)"
                    : "inset 0 -1px 0 rgba(140,100,60,0.2)",
                  cursor: "pointer",
                  textAlign: "left",
                }}>
                  <span style={{
                    fontFamily: "var(--f-display)",
                    fontSize: 14,
                    color: isFoe ? "var(--crimson)" : "var(--ink-900)",
                    fontWeight: 600,
                  }}>{t.init}</span>
                  <Placeholder label={tok.short || "?"} w={36} h={36} framed />
                  <div style={{ minWidth: 0 }}>
                    <div style={{
                      fontFamily: "var(--f-display)",
                      fontSize: 11,
                      letterSpacing: "0.06em",
                      color: isFoe ? "var(--crimson)" : "var(--ink-900)",
                      fontStyle: isFoe ? "italic" : "normal",
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}>{tok.name}</div>
                    <div style={{ display: "flex", gap: 4, alignItems: "center", marginTop: 3 }}>
                      <div style={{ flex: 1, height: 4, background: "rgba(0,0,0,0.15)", position: "relative", boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.3)" }}>
                        <div style={{
                          position: "absolute", inset: 0, right: `${(1 - hpRatio) * 100}%`,
                          background: isFoe ? "linear-gradient(180deg, var(--crimson), #4a1010)" :
                            (hpRatio > 0.5 ? "linear-gradient(180deg, #5a8a3a, #3a6020)" : "linear-gradient(180deg, var(--crimson), #4a1010)"),
                        }} />
                      </div>
                    </div>
                  </div>
                  {isActive && <span style={{ color: "var(--crimson)", fontFamily: "var(--f-display)", fontSize: 14 }}>▶</span>}
                </button>
              );
            })}
          </div>
        </Panel>

        <Panel framed style={{ padding: 18, flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
          <SectionTitle>Battle Log</SectionTitle>
          <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
            {log.map((l, i) => <BattleLogLine key={i} l={l} />)}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function CombatMap({ tokens, selected, onSelect, activeAction, onMove }) {
  const COLS = 16, ROWS = 10;
  const selectedToken = tokens.find((t) => t.id === selected);

  // Determine which tiles are within reach (movement = 6 squares)
  const inRange = (gx, gy) => {
    if (!selectedToken || activeAction !== "move") return false;
    const dx = Math.abs(gx - selectedToken.x);
    const dy = Math.abs(gy - selectedToken.y);
    return Math.max(dx, dy) <= 6 && Math.max(dx, dy) > 0;
  };

  return (
    <div style={{
      position: "relative",
      width: "100%", height: "calc(100% - 50px)",
      background:
        `radial-gradient(ellipse at 50% 50%, rgba(60,30,10,0.2), transparent 70%),
         linear-gradient(135deg, #3a2418 0%, #25160e 100%)`,
      boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 60px rgba(0,0,0,0.6)",
      overflow: "hidden",
    }}>
      {/* Painted scene backdrop */}
      <div style={{ position: "absolute", inset: 12 }}>
        <Placeholder label="terrain · courtyard · packed earth · stable wall north · gate south" h="100%" style={{ width: "100%", height: "100%", opacity: 0.5 }} />
      </div>

      {/* Grid overlay */}
      <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}>
        <defs>
          <pattern id="gridPattern" x="0" y="0" width={`${100 / COLS}%`} height={`${100 / ROWS}%`} patternUnits="userSpaceOnUse">
            <rect width="100%" height="100%" fill="none" stroke="rgba(176, 141, 87, 0.18)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#gridPattern)" />

        {/* Column labels */}
        {Array.from({ length: COLS }).map((_, i) => (
          <text key={`c${i}`} x={`${(i + 0.5) * (100 / COLS)}%`} y="12" textAnchor="middle"
            fontFamily="Cinzel" fontSize="9" fill="rgba(212, 185, 122, 0.4)" letterSpacing="1">
            {String.fromCharCode(65 + i)}
          </text>
        ))}
        {/* Row labels */}
        {Array.from({ length: ROWS }).map((_, i) => (
          <text key={`r${i}`} x="6" y={`${(i + 0.5) * (100 / ROWS) + 3}%`}
            fontFamily="Cinzel" fontSize="9" fill="rgba(212, 185, 122, 0.4)">{i + 1}</text>
        ))}
      </svg>

      {/* Tile click overlay */}
      <div style={{
        position: "absolute", inset: 0,
        display: "grid",
        gridTemplateColumns: `repeat(${COLS}, 1fr)`,
        gridTemplateRows: `repeat(${ROWS}, 1fr)`,
      }}>
        {Array.from({ length: COLS * ROWS }).map((_, idx) => {
          const gx = (idx % COLS) + 1;
          const gy = Math.floor(idx / COLS) + 1;
          const reach = inRange(gx, gy);
          return (
            <div
              key={idx}
              onClick={() => reach && onMove(gx, gy)}
              style={{
                background: reach ? "rgba(244, 210, 123, 0.16)" : "transparent",
                boxShadow: reach ? "inset 0 0 0 1px rgba(244, 210, 123, 0.4)" : "none",
                cursor: reach ? "pointer" : "default",
                transition: "background 100ms",
              }}
            />
          );
        })}
      </div>

      {/* Tokens */}
      {tokens.map((t) => (
        <CombatToken
          key={t.id}
          t={t}
          cols={COLS} rows={ROWS}
          selected={selected === t.id}
          onClick={() => onSelect(t.id)}
        />
      ))}
    </div>
  );
}

function CombatToken({ t, cols, rows, selected, onClick }) {
  const xPct = ((t.x - 0.5) / cols) * 100;
  const yPct = ((t.y - 0.5) / rows) * 100;
  const isFoe = t.team === "foe";
  return (
    <button onClick={onClick} style={{
      position: "absolute",
      left: `${xPct}%`, top: `${yPct}%`,
      width: 48, height: 48,
      transform: "translate(-50%, -50%)",
      background: "none",
      cursor: "pointer",
      padding: 0,
      zIndex: selected ? 10 : 5,
    }}>
      <div style={{
        width: "100%", height: "100%",
        borderRadius: "50%",
        background: isFoe
          ? "radial-gradient(circle at 30% 30%, #c54040, var(--crimson) 60%, #3a0a0a)"
          : "radial-gradient(circle at 30% 30%, var(--p-100), var(--p-300) 60%, var(--b-500))",
        boxShadow: selected
          ? `inset 0 0 0 2px var(--gold-glow), 0 0 0 3px ${isFoe ? "var(--crimson-bright)" : "var(--gold-glow)"}, 0 0 24px rgba(244, 210, 123, 0.7), 0 4px 8px rgba(0,0,0,0.5)`
          : `inset 0 0 0 2px ${isFoe ? "#5a1414" : "var(--b-500)"}, 0 0 0 2px ${isFoe ? "var(--crimson)" : "var(--b-300)"}, 0 4px 8px rgba(0,0,0,0.6)`,
        display: "grid", placeItems: "center",
        fontFamily: "var(--f-display)",
        fontSize: 13,
        letterSpacing: "0.04em",
        color: isFoe ? "var(--p-100)" : "var(--ink-900)",
      }}>
        {t.initial}
      </div>
      {/* Floating HP bar */}
      <div style={{
        position: "absolute", left: "50%", bottom: -10, transform: "translateX(-50%)",
        width: 44, height: 4,
        background: "rgba(0,0,0,0.5)",
        boxShadow: "0 0 0 1px rgba(0,0,0,0.8)",
      }}>
        <div style={{
          position: "absolute", left: 0, top: 0, bottom: 0,
          width: `${(t.hp / t.hpMax) * 100}%`,
          background: isFoe ? "linear-gradient(180deg, #d63a3a, #8a1a1a)" : "linear-gradient(180deg, #5cd56a, #2a8c39)",
        }} />
      </div>
      {/* Name label */}
      <div style={{
        position: "absolute", left: "50%", top: -16, transform: "translateX(-50%)",
        fontFamily: "var(--f-display)", fontSize: 8, letterSpacing: "0.15em",
        textTransform: "uppercase", whiteSpace: "nowrap",
        color: isFoe ? "var(--crimson-bright)" : "var(--gold-glow)",
        textShadow: "0 1px 2px rgba(0,0,0,0.9)",
      }}>{t.name}</div>
    </button>
  );
}

function ActionTile({ icon, label, hint, onClick, active, spent }) {
  return (
    <button onClick={onClick} disabled={spent} style={{
      padding: "10px 8px",
      textAlign: "center",
      background: active
        ? "linear-gradient(180deg, var(--b-200), var(--b-400))"
        : spent ? "rgba(0,0,0,0.18)" : "rgba(176,141,87,0.08)",
      color: active ? "var(--w-300)" : spent ? "var(--ink-500)" : "var(--ink-800)",
      boxShadow: active
        ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6), 0 0 16px -4px var(--gold-glow)"
        : "inset 0 0 0 1px rgba(140,100,60,0.3)",
      cursor: spent ? "not-allowed" : "pointer",
      transition: "all 140ms",
      opacity: spent ? 0.5 : 1,
    }}
    onMouseEnter={(e) => { if (!active && !spent) e.currentTarget.style.background = "rgba(176,141,87,0.18)"; }}
    onMouseLeave={(e) => { if (!active && !spent) e.currentTarget.style.background = "rgba(176,141,87,0.08)"; }}>
      <div style={{ fontSize: 18, lineHeight: 1, marginBottom: 4 }}>{icon}</div>
      <div style={{ fontFamily: "var(--f-display)", fontSize: 9, letterSpacing: "0.12em", textTransform: "uppercase" }}>{label}</div>
      {hint && <div style={{ fontFamily: "var(--f-mono)", fontSize: 8, color: active ? "var(--w-300)" : "var(--ink-600)", marginTop: 2 }}>{hint}</div>}
    </button>
  );
}

function ApBadge({ used, label }) {
  return (
    <span style={{
      fontFamily: "var(--f-display)", fontSize: 8, letterSpacing: "0.1em", textTransform: "uppercase",
      padding: "2px 5px",
      background: used ? "rgba(0,0,0,0.2)" : "rgba(95, 75, 45, 0.4)",
      color: used ? "var(--ink-600)" : "var(--ink-800)",
      boxShadow: used ? "inset 0 0 0 1px rgba(80,50,20,0.4)" : "inset 0 0 0 1px var(--b-500)",
      textDecoration: used ? "line-through" : "none",
    }}>{label}</span>
  );
}

function BattleLogLine({ l }) {
  if (l.t === "round") return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 0", margin: "6px 0", borderTop: "1px solid rgba(140,100,60,0.3)", borderBottom: "1px solid rgba(140,100,60,0.3)" }}>
      <span style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.2em", textTransform: "uppercase", color: "var(--crimson)" }}>{l.text}</span>
    </div>
  );
  if (l.t === "act") return (
    <div style={{ padding: "4px 0", fontSize: 13 }}>
      <span style={{ fontFamily: "var(--f-display)", fontSize: 10, letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--ink-900)", marginRight: 6 }}>
        {l.who}
      </span>
      <span className="body-sm" style={{ color: "var(--ink-700)" }}>{l.text}</span>
    </div>
  );
  if (l.t === "roll") return (
    <div style={{ fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--ink-600)", padding: "2px 12px" }}>
      ▷ {l.text}
    </div>
  );
  if (l.t === "dmg") return (
    <div style={{ fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--crimson)", padding: "2px 12px" }}>
      ▷ {l.text}
    </div>
  );
  return null;
}

const TOKENS = [
  { id: "cassian", name: "Cassian", initial: "C", short: "C·portrait", team: "ally", x: 5, y: 7, hp: 22, hpMax: 24, ac: 17, standardUsed: false, moveUsed: false, swiftUsed: false },
  { id: "mira", name: "Mira", initial: "M", short: "M·portrait", team: "ally", x: 7, y: 8, hp: 18, hpMax: 22, ac: 15 },
  { id: "vell", name: "Vell", initial: "V", short: "V·portrait", team: "ally", x: 4, y: 8, hp: 28, hpMax: 30, ac: 18 },
  { id: "bandit-n", name: "Bandit N", initial: "♠", team: "foe", x: 11, y: 3, hp: 14, hpMax: 16, ac: 15 },
  { id: "bandit-s", name: "Bandit S", initial: "♠", team: "foe", x: 13, y: 6, hp: 8, hpMax: 16, ac: 15 },
  { id: "bandit-e", name: "Sgt.", initial: "♣", team: "foe", x: 13, y: 4, hp: 22, hpMax: 22, ac: 16 },
];

const INITIATIVE = [
  { id: "mira", init: 19 },
  { id: "cassian", init: 14, active: true },
  { id: "bandit-e", init: 12 },
  { id: "bandit-n", init: 11 },
  { id: "vell", init: 9 },
  { id: "bandit-s", init: 4 },
];

Object.assign(window, { ScreenCombat, CombatMap, CombatToken, ActionTile, ApBadge, BattleLogLine, TOKENS, INITIATIVE });
