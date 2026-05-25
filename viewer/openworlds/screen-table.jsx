/* Screen: Campaign Table — live session: scene art + party + GM narration + actions */

function ScreenTable({ onNavigate, state, setState }) {
  const party = Array.isArray(state?.party) ? state.party : [];
  const quests = Array.isArray(state?.quests) ? state.quests : [];
  const stash = Array.isArray(state?.stash) ? state.stash : [];
  const [log, setLog] = React.useState(Array.isArray(state?.tableLog) ? state.tableLog : []);
  const [input, setInput] = React.useState("");
  const [activeHero, setActiveHero] = React.useState(() => party[0]?.id || "");
  const logRef = React.useRef(null);
  const toast = window.useToast ? window.useToast() : (() => {});
  const hero = party.find((p) => p.id === activeHero) || party[0] || { id: "", name: "Hero", short: "Hero", level: 1, class: "Adventurer", hp: 1, hpMax: 1 };

  React.useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  const sendAction = () => {
    if (!input.trim()) return;
    setLog((l) => [
      ...l,
      { kind: "action", who: hero.name, text: input },
      { kind: "narration", text: synthNarration(input, hero) },
    ]);
    setInput("");
  };

  const roll = (sides = 20) => {
    const r = 1 + Math.floor(Math.random() * sides);
    setLog((l) => [
      ...l,
      { kind: "roll", who: hero.name, sides, text: `rolls d${sides}: ${r}${r === sides ? " — natural!" : ""}` },
    ]);
    toast({
      eyebrow: `d${sides}`,
      title: hero.name + " rolls " + r,
      body: r === sides ? "A natural — the chronicle leans forward." : r === 1 ? "A one. The chronicle leans the other way." : null,
      kind: r === sides ? "level" : r === 1 ? "danger" : undefined,
    });
  };

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "260px 1fr 280px", gap: 14, padding: 14 }}>

      {/* LEFT — Party roster */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
        <Panel framed style={{ padding: 18, flex: "0 0 auto" }}>
          <div className="eyebrow" style={{ color: "var(--crimson)" }}>Round IV · Initiative</div>
          <SectionTitle>The Party</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {party.map((p) => (
              <PartyRow
                key={p.id}
                p={p}
                active={activeHero === p.id}
                onClick={() => setActiveHero(p.id)}
              />
            ))}
          </div>
        </Panel>

        <Panel framed style={{ padding: 18, flex: "1 1 auto", minHeight: 0, overflow: "auto" }}>
          <SectionTitle>Conditions</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <ConditionRow icon="✦" name="Blessed" who="Cassian" detail="+1 attacks · 3 rounds" />
            <ConditionRow icon="◆" name="Shaken" who="Mira" detail="-2 saves · until camp" tone="crimson" />
            <ConditionRow icon="◈" name="Inspired" who="Vell" detail="re-roll one d20" tone="royal" />
          </div>
        </Panel>
      </div>

      {/* CENTER — Scene + log */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
        {/* Scene plate */}
        <div style={{ position: "relative", flex: "0 0 auto" }}>
          <Placeholder
            label="scene · isometric · Lanternrest courtyard · dusk · 7 figures"
            h={260}
            framed
            style={{ width: "100%" }}
          />
          {/* Glow + caption */}
          <div className="candleglow" style={{ width: 200, height: 200, left: "30%", top: "30%" }} />
          <div style={{
            position: "absolute", bottom: 14, left: 14, right: 14,
            display: "flex", justifyContent: "space-between", alignItems: "flex-end",
            pointerEvents: "none",
          }}>
            <div>
              <Pill tone="royal" dot>Day 12 · Dusk</Pill>
              <div className="hand" style={{ marginTop: 6, color: "var(--p-100)", fontSize: 16, textShadow: "0 1px 2px rgba(0,0,0,0.8)" }}>
                The Lanternrest stands silent. A crow sits the gable, watching.
              </div>
            </div>
            <div style={{ display: "flex", gap: 6, pointerEvents: "auto" }}>
              <BrassButton tone="dark" size="sm" onClick={() => onNavigate("map")}>Travel</BrassButton>
              <BrassButton tone="dark" size="sm" onClick={() => onNavigate("dialogue")}>Parley</BrassButton>
              <BrassButton tone="dark" size="sm" onClick={() => onNavigate("map", { openCamp: true })}>Camp</BrassButton>
            </div>
          </div>
        </div>

        {/* Log */}
        <Panel framed style={{ flex: "1 1 auto", display: "flex", flexDirection: "column", minHeight: 0, padding: 22 }}>
          <SectionTitle ordinal="·" right={<Pill>AI GM · Listening</Pill>}>The Tabletop Chronicle</SectionTitle>
          <div ref={logRef} style={{ flex: "1 1 auto", overflow: "auto", paddingRight: 12 }}>
            {log.map((entry, i) => (
              <LogEntry key={i} entry={entry} />
            ))}
          </div>

          {/* Action bar */}
          <div style={{ marginTop: 14, padding: 12, background: "rgba(80,50,20,0.06)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.35)" }}>
            <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 8 }}>
              <span className="eyebrow">Active</span>
              <strong style={{ fontFamily: "var(--f-display)", color: "var(--ink-900)", letterSpacing: "0.1em" }}>
                {hero.name}
              </strong>
              <div style={{ flex: 1 }} />
              <button onClick={() => roll(20)} className="btn ghost sm">d20</button>
              <button onClick={() => roll(12)} className="btn ghost sm">d12</button>
              <button onClick={() => roll(8)} className="btn ghost sm">d8</button>
              <button onClick={() => roll(6)} className="btn ghost sm">d6</button>
            </div>
            <div style={{ display: "flex", gap: 10 }}>
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && sendAction()}
                placeholder="Describe what your hero does…"
                style={{ ...inkInput, fontFamily: "var(--f-body)", fontSize: 16 }}
              />
              <BrassButton onClick={sendAction}>Declare</BrassButton>
            </div>
          </div>
        </Panel>
      </div>

      {/* RIGHT — Quests + Quick stash + GM tools */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
        <Panel framed style={{ padding: 18 }}>
          <SectionTitle>Active Quests</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {quests.filter((q) => q.status === "active").map((q) => (
              <button key={q.id} onClick={() => onNavigate("journal")} style={{
                textAlign: "left",
                padding: "10px 12px",
                background: "rgba(176,141,87,0.08)",
                boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
                cursor: "pointer",
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.1em", color: "var(--ink-900)" }}>
                    {q.title}
                  </span>
                  <Pill tone={q.tone}>{q.label}</Pill>
                </div>
                <div className="hand" style={{ fontSize: 13, color: "var(--ink-600)", marginTop: 2 }}>{q.objective}</div>
              </button>
            ))}
          </div>
        </Panel>

        <Panel framed style={{ padding: 18 }}>
          <SectionTitle right={<button className="btn ghost sm" onClick={() => onNavigate("inventory")}>Open</button>}>Quick Stash</SectionTitle>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
            {stash.slice(0, 8).map((it) => (
              <IconPlate key={it.id} size={48} label={it.glyph} framed />
            ))}
          </div>
          <div style={{ marginTop: 12, display: "flex", justifyContent: "space-between" }}>
            <Stat label="Coin" value="232 gp" />
            <Stat label="Fate" value="3" />
          </div>
        </Panel>

        <Panel framed style={{ padding: 18, flex: 1, minHeight: 0, overflow: "auto" }}>
          <SectionTitle>Encounter</SectionTitle>
          <div className="body-sm muted" style={{ marginBottom: 10 }}>
            The Lanternrest waits. Choose what to risk.
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <EncounterButton icon="◈" label="Approach the door" detail="Mira leads · Stealth DC 14" tone=""
              onClick={() => toast({ kind: "quest", title: "Mira approaches the door", body: "Stealth check: 18 — silent." })} />
            <EncounterButton icon="✦" label="Light the lantern" detail="Cassian, 1 ritual" tone="royal"
              onClick={() => toast({ kind: "item", title: "The lantern is lit", body: "For the first time in seven years. Something inside the inn registers it." })} />
            <EncounterButton icon="◆" label="Speak aloud" detail="Persuasion · invites response" tone=""
              onClick={() => toast({ title: "You call out", body: "Silence answers. Then a single floorboard." })} />
            <EncounterButton icon="▲" label="Force the door" detail="Vell · loud · -1 reputation" tone="crimson"
              onClick={() => toast({ kind: "danger", title: "Vell hits the door", body: "It opens. -1 with the Road Wardens of Restov." })} />
          </div>

          <div className="divider" style={{ margin: "14px 0" }}>
            <div className="diamond"></div>
          </div>

          <SectionTitle>Round Order</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {[
              { name: "Mira", init: 19, active: true },
              { name: "Cassian", init: 14 },
              { name: "The Crow", init: 11, foe: true },
              { name: "Vell", init: 9 },
              { name: "Linzi", init: 6 },
            ].map((t) => (
              <div key={t.name} style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "6px 10px",
                background: t.active ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
                boxShadow: t.active ? "inset 0 0 0 1px var(--b-500)" : "inset 0 -1px 0 rgba(140,100,60,0.2)",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  {t.active && <span style={{ color: "var(--crimson)", fontFamily: "var(--f-display)", fontSize: 12 }}>▶</span>}
                  <span className="body-sm" style={{ color: t.foe ? "var(--crimson)" : "var(--ink-800)", fontStyle: t.foe ? "italic" : "normal" }}>
                    {t.name}
                  </span>
                </div>
                <span style={{ fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--ink-600)" }}>{t.init}</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function PartyRow({ p, active, onClick }) {
  const hpRatio = p.hp / p.hpMax;
  return (
    <button onClick={onClick} style={{
      display: "grid", gridTemplateColumns: "44px 1fr", gap: 10, alignItems: "center",
      padding: "8px",
      background: active ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
      boxShadow: active
        ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
        : "inset 0 0 0 1px rgba(140,100,60,0.25)",
      textAlign: "left",
      cursor: "pointer",
      transition: "all 140ms",
    }}>
      <Placeholder label={p.short} w={44} h={56} framed />
      <div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
          {p.name}
        </div>
        <div className="hand" style={{ fontSize: 12, color: "var(--ink-600)" }}>
          Lvl {p.level} {p.class}
        </div>
        <div style={{ display: "flex", gap: 4, marginTop: 4, alignItems: "center" }}>
          <div style={{ flex: 1, height: 6, background: "rgba(0,0,0,0.15)", borderRadius: 1, position: "relative", boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.4)" }}>
            <div style={{
              position: "absolute", inset: 0, right: `${(1 - hpRatio) * 100}%`,
              background: hpRatio > 0.5 ? "linear-gradient(180deg, #5a8a3a, #3a6020)" : "linear-gradient(180deg, var(--crimson), #4a1010)",
            }} />
          </div>
          <span style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-700)" }}>{p.hp}/{p.hpMax}</span>
        </div>
      </div>
    </button>
  );
}

function ConditionRow({ icon, name, who, detail, tone }) {
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 8, alignItems: "center",
      padding: "6px 10px",
      background: "rgba(176,141,87,0.06)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.2)",
    }}>
      <span style={{ color: `var(--${tone === "crimson" ? "crimson" : tone === "royal" ? "royal" : "b-500"})`, fontSize: 16 }}>{icon}</span>
      <div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-900)" }}>
          {name} <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}>· {who}</span>
        </div>
        <div className="hand" style={{ fontSize: 12, color: "var(--ink-600)" }}>{detail}</div>
      </div>
    </div>
  );
}

function LogEntry({ entry }) {
  if (entry.kind === "narration") {
    return (
      <div style={{ margin: "14px 0", display: "flex", gap: 12 }}>
        <div style={{
          width: 4, alignSelf: "stretch",
          background: "linear-gradient(180deg, var(--b-400), transparent)",
        }} />
        <div className="body" style={{ flex: 1 }}>
          <span className="eyebrow" style={{ color: "var(--crimson)", marginRight: 8 }}>Chronicle</span>
          {entry.text}
        </div>
      </div>
    );
  }
  if (entry.kind === "action") {
    return (
      <div style={{ margin: "10px 0" }}>
        <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-900)" }}>
          {entry.who}
        </span>
        <span className="hand" style={{ marginLeft: 8, color: "var(--ink-700)" }}>—</span>
        <span className="body" style={{ marginLeft: 8, color: "var(--ink-800)" }}>{entry.text}</span>
      </div>
    );
  }
  if (entry.kind === "roll") {
    return (
      <div style={{ margin: "8px 0", display: "flex", gap: 10, alignItems: "center" }}>
        <Pill tone="emerald">d{entry.sides ?? 20}</Pill>
        <span style={{ fontFamily: "var(--f-mono)", fontSize: 13, color: "var(--ink-700)" }}>
          {entry.who} {entry.text}
        </span>
      </div>
    );
  }
  if (entry.kind === "dialog") {
    return (
      <div style={{ margin: "10px 0" }}>
        <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--royal)" }}>
          {entry.who}
        </span>
        <span className="body" style={{ marginLeft: 8, fontStyle: "italic" }}>"{entry.text}"</span>
      </div>
    );
  }
  return null;
}

function synthNarration(action, hero) {
  const lines = [
    `${hero.name} steps forward; the dust pauses where their boot lands. The world holds its breath for the length of a stanza.`,
    `A floorboard answers somewhere within the Lanternrest. The crow does not move. A faint smell of beeswax and old iron crosses the threshold.`,
    `The chronicle records: ${action.toLowerCase()}. A success — but the room is now aware of you.`,
    `Roll persuasion, or let silence keep its grip on the door a moment longer.`,
  ];
  return lines[Math.floor(Math.random() * lines.length)];
}

Object.assign(window, { ScreenTable, PartyRow, ConditionRow, LogEntry, synthNarration });

function EncounterButton({ icon, label, detail, tone, onClick }) {
  return (
    <button onClick={onClick} style={{
      display: "grid", gridTemplateColumns: "24px 1fr", gap: 8, alignItems: "center",
      textAlign: "left",
      padding: "8px 10px",
      background: "rgba(176,141,87,0.08)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
      cursor: "pointer",
      transition: "all 140ms",
    }}
    onMouseEnter={(e) => {
      e.currentTarget.style.background = "linear-gradient(180deg, var(--p-100), var(--p-200))";
      e.currentTarget.style.boxShadow = "inset 0 0 0 1px var(--b-500), 0 0 16px -6px var(--gold-glow)";
    }}
    onMouseLeave={(e) => {
      e.currentTarget.style.background = "rgba(176,141,87,0.08)";
      e.currentTarget.style.boxShadow = "inset 0 0 0 1px rgba(140,100,60,0.3)";
    }}>
      <span style={{ color: tone === "crimson" ? "var(--crimson)" : tone === "royal" ? "var(--royal)" : "var(--b-500)", fontSize: 16 }}>{icon}</span>
      <div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--ink-900)" }}>
          {label}
        </div>
        <div className="hand muted" style={{ fontSize: 11 }}>{detail}</div>
      </div>
    </button>
  );
}

window.EncounterButton = EncounterButton;
