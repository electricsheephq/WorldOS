/* Screen: Character Sheet — dense, codex/sourcebook style */

function ScreenCharacter({ onNavigate, state, setState }) {
  const party = Array.isArray(state?.party) ? state.party : [];
  const [active, setActive] = React.useState(() => party[0]?.id || "");
  const [tab, setTab] = React.useState("abilities");
  const [restOpen, setRestOpen] = React.useState(false);
  const toast = window.useToast ? window.useToast() : (() => {});

  const hero = party.find((p) => p.id === active) || party[0];

  React.useEffect(() => {
    if (party.length > 0 && !party.find((p) => p.id === active)) {
      setActive(party[0].id);
    }
  }, [party, active]);

  if (!hero) {
    return (
      <div className="screen" style={{ height: "100%", padding: 14 }}>
        <Panel framed><div className="muted">No heroes available.</div></Panel>
      </div>
    );
  }

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "200px 1fr", gap: 14, padding: 14 }}>

      {/* LEFT: party rail */}
      <Panel framed style={{ padding: 18, display: "flex", flexDirection: "column", gap: 10 }}>
        <SectionTitle>Roster</SectionTitle>
        {party.map((p) => (
          <button key={p.id} onClick={() => setActive(p.id)} style={{
            display: "flex", gap: 8, alignItems: "center", padding: 6, cursor: "pointer",
            background: active === p.id ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
            boxShadow: active === p.id
              ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
              : "inset 0 0 0 1px rgba(140,100,60,0.2)",
            textAlign: "left",
          }}>
            <Placeholder label={p.short} w={36} h={44} framed />
            <div>
              <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
                {p.name}
              </div>
              <div className="hand" style={{ fontSize: 11, color: "var(--ink-600)" }}>Lv {p.level}</div>
            </div>
          </button>
        ))}

        <div className="divider" style={{ margin: "12px 0" }}><div className="diamond"></div></div>

        <div className="eyebrow" style={{ marginBottom: 6 }}>Tabs</div>
        {["abilities", "skills", "spells", "feats"].map((t) => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: "8px 10px", textAlign: "left",
            fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.2em", textTransform: "uppercase",
            color: tab === t ? "var(--w-300)" : "var(--ink-700)",
            background: tab === t ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "transparent",
            boxShadow: tab === t ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)" : "inset 0 0 0 1px rgba(140,100,60,0.25)",
            cursor: "pointer",
          }}>
            {t}
          </button>
        ))}

        <div className="divider" style={{ margin: "12px 0" }}><div className="diamond"></div></div>

        <BrassButton tone="dark" size="sm" onClick={() => setRestOpen(true)} style={{ width: "100%" }}>
          Rest & Prepare
        </BrassButton>
      </Panel>

      {/* RIGHT: sheet */}
      <div style={{ display: "grid", gridTemplateRows: "auto 1fr", gap: 14, minHeight: 0 }}>
        {/* Hero header card */}
        <Panel framed style={{ padding: 22 }}>
          <div style={{ display: "grid", gridTemplateColumns: "140px 1fr auto", gap: 22, alignItems: "start" }}>
            <Placeholder label={`${hero.short} · portrait`} w={140} h={170} framed />
            <div>
              <div className="eyebrow" style={{ color: "var(--crimson)" }}>{hero.alignment}</div>
              <h1 className="h1" style={{ marginTop: 2 }}>{hero.name}</h1>
              <div className="hand" style={{ fontSize: 17, color: "var(--ink-700)", marginTop: 2 }}>
                {hero.race} · {hero.class} · {hero.archetype}
              </div>

              <div style={{ display: "flex", gap: 10, marginTop: 14, flexWrap: "wrap" }}>
                <Pill tone="crimson" dot>HP {hero.hp}/{hero.hpMax}</Pill>
                <Pill tone="royal" dot>AC {hero.stats.ac}</Pill>
                <Pill tone="emerald" dot>Lv {hero.level}</Pill>
                <Pill>XP {hero.xp.toLocaleString()} / {hero.xpMax.toLocaleString()}</Pill>
                <Pill>Speed {hero.stats.speed} ft</Pill>
              </div>

              {/* XP bar */}
              <div style={{ marginTop: 12 }}>
                <div style={{ height: 8, background: "rgba(0,0,0,0.15)", boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.4)", position: "relative" }}>
                  <div style={{
                    position: "absolute", inset: 0, right: `${(1 - hero.xp / hero.xpMax) * 100}%`,
                    background: "linear-gradient(180deg, #d4b97a, #8a6d3f)",
                    boxShadow: "inset 0 1px 0 rgba(255,250,220,0.5)",
                  }} />
                </div>
              </div>
            </div>

            {/* Ability scores column */}
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, auto)",
              gap: 8,
              padding: 14,
              background: "rgba(176,141,87,0.08)",
              boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.35)",
            }}>
              {["STR","DEX","CON","INT","WIS","CHA"].map((s) => (
                <AbilityScore key={s} label={s} value={hero.stats[s.toLowerCase()]} mod={Math.floor((hero.stats[s.toLowerCase()] - 10) / 2)} />
              ))}
            </div>
          </div>
        </Panel>

        {/* Lower split: stats blocks + tab content */}
        <div style={{ display: "grid", gridTemplateColumns: "1.05fr 1.7fr 1fr", gap: 14, minHeight: 0 }}>

          {/* Combat block */}
          <Panel framed style={{ padding: 22, overflow: "auto" }}>
            <SectionTitle ordinal="·">Combat</SectionTitle>

            <div className="eyebrow" style={{ marginTop: 4 }}>Attack</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, marginTop: 8 }}>
              <StatLine k="Base Attack" v={`+${hero.stats.bab}`} />
              <StatLine k="Initiative" v={`+${hero.stats.initiative}`} />
              <StatLine k="Melee" v={`+${hero.stats.melee}`} />
              <StatLine k="Ranged" v={`+${hero.stats.ranged}`} />
              <StatLine k="CMB" v={`+${hero.stats.cmb}`} />
              <StatLine k="CMD" v={hero.stats.cmd} />
            </div>

            <Divider />

            <div className="eyebrow">Defense</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 4, marginTop: 8 }}>
              <StatLine k="AC" v={hero.stats.ac} />
              <StatLine k="Flat" v={hero.stats.flat} />
              <StatLine k="Touch" v={hero.stats.touch} />
              <StatLine k="Fort" v={`+${hero.stats.fort}`} />
              <StatLine k="Reflex" v={`+${hero.stats.reflex}`} />
              <StatLine k="Will" v={`+${hero.stats.will}`} />
            </div>

            <Divider />

            <div className="eyebrow">Equipped</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 8 }}>
              {hero.equipped.map((it) => (
                <div key={it.slot} style={{
                  display: "flex", gap: 8, alignItems: "center",
                  padding: 8,
                  background: "rgba(176,141,87,0.08)",
                  boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
                }}>
                  <Placeholder label={it.glyph} w={32} h={32} framed />
                  <div style={{ minWidth: 0 }}>
                    <div className="eyebrow" style={{ fontSize: 9 }}>{it.slot}</div>
                    <div style={{ fontFamily: "var(--f-display)", fontSize: 11, color: "var(--ink-900)", letterSpacing: "0.05em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                      {it.name}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Panel>

          {/* Center tab content */}
          <Panel framed style={{ padding: 22, overflow: "auto" }}>
            {tab === "abilities" && <AbilitiesTab hero={hero} />}
            {tab === "skills" && <SkillsTab hero={hero} />}
            {tab === "spells" && <SpellsTab hero={hero} />}
            {tab === "feats" && <FeatsTab hero={hero} />}
          </Panel>

          {/* Right column: lineage + traits */}
          <Panel framed style={{ padding: 22, overflow: "auto" }}>
            <SectionTitle ordinal="·">Lineage</SectionTitle>
            <p className="body dropcap" style={{ marginTop: 0, fontSize: 15 }}>
              {hero.lineage}
            </p>

            <Divider />

            <SectionTitle>Traits</SectionTitle>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {hero.traits.map((t) => (
                <div key={t.name} style={{
                  padding: 10,
                  background: "rgba(176,141,87,0.08)",
                  boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
                }}>
                  <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--ink-900)" }}>
                    {t.name}
                  </div>
                  <div className="body-sm muted" style={{ marginTop: 2 }}>{t.detail}</div>
                </div>
              ))}
            </div>

            <Divider />

            <SectionTitle>Damage Reduction</SectionTitle>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
              <StatLine k="Value" v={hero.dr.value} />
              <StatLine k="Energy" v={hero.dr.energy} />
            </div>
          </Panel>
        </div>
      </div>

      {restOpen && <RestPrepareModal hero={hero} onClose={() => setRestOpen(false)} toast={toast} setState={setState} />}
    </div>
  );
}

function RestPrepareModal({ hero, onClose, toast, setState }) {
  const [step, setStep] = React.useState("rest");
  const [restType, setRestType] = React.useState("long");
  const [prepared, setPrepared] = React.useState({});

  const availableSpells = hero.spells || [];
  const slots = { 0: 4, 1: 3, 2: 1 };

  const toggleSpell = (lv, name) => {
    const cur = prepared[lv] || [];
    const max = slots[lv] || 0;
    if (cur.includes(name)) {
      setPrepared({ ...prepared, [lv]: cur.filter((n) => n !== name) });
    } else if (cur.length < max) {
      setPrepared({ ...prepared, [lv]: [...cur, name] });
    }
  };

  const completeRest = () => {
    toast({
      kind: "rest",
      eyebrow: restType === "long" ? "Long rest" : "Short rest",
      title: hero.name + " is restored",
      body: restType === "long" ? "HP and spell slots refreshed. The watch goes to Mira." : "Some wounds knit. Spell slots untouched.",
    });
    setStep("prep");
  };

  const completePrep = () => {
    const count = Object.values(prepared).reduce((s, l) => s + l.length, 0);
    toast({
      kind: "item",
      eyebrow: "Spellbook",
      title: hero.name + " prepares " + count + " spell" + (count !== 1 ? "s" : ""),
      body: "The chronicle records the binding. They may be unbound at next rest.",
    });
    onClose();
  };

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 200,
      background: "rgba(15, 8, 2, 0.7)",
      display: "grid", placeItems: "center",
      backdropFilter: "blur(2px)",
    }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 720, maxWidth: "92vw", maxHeight: "88vh", overflow: "auto" }}>
        <Panel framed>
          {step === "rest" ? (
            <>
              <div className="eyebrow" style={{ color: "var(--crimson)" }}>The Camp</div>
              <h2 className="h1" style={{ fontSize: 26 }}>Rest & Recover</h2>
              <Divider />

              <p className="body dropcap">
                {hero.name} finds a quiet hour. The chronicle pauses with them. Choose what kind of rest to claim from the road.
              </p>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 18 }}>
                <RestCard
                  selected={restType === "short"}
                  onClick={() => setRestType("short")}
                  title="Short Rest"
                  hand="One hour. A second wind."
                  body="Restores hit points spent from class features. Spell slots and abilities remain spent. Watch is not required."
                  cost="1 hour · no rations"
                />
                <RestCard
                  selected={restType === "long"}
                  onClick={() => setRestType("long")}
                  title="Long Rest"
                  hand="Eight hours. The whole road forgiven."
                  body="Full HP. All spell slots restored. Abilities refresh. Watch order required; one in four sleeps light."
                  cost="8 hours · 1 ration each"
                />
              </div>

              <Divider />

              <SectionTitle>Watch order</SectionTitle>
              <div style={{ display: "flex", gap: 6 }}>
                {["Mira", "Cassian", "Vell"].map((p, i) => (
                  <div key={p} style={{
                    flex: 1, padding: "8px 10px", textAlign: "center",
                    background: "rgba(176,141,87,0.08)",
                    boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
                  }}>
                    <div className="eyebrow" style={{ fontSize: 9 }}>Watch {i + 1}</div>
                    <div style={{ fontFamily: "var(--f-display)", fontSize: 13, color: "var(--ink-900)", marginTop: 2 }}>{p}</div>
                  </div>
                ))}
              </div>

              <div style={{ display: "flex", gap: 10, marginTop: 24, justifyContent: "flex-end" }}>
                <BrassButton tone="ghost" onClick={onClose}>Not yet</BrassButton>
                <BrassButton onClick={completeRest}>Make camp</BrassButton>
              </div>
            </>
          ) : (
            <>
              <div className="eyebrow" style={{ color: "var(--crimson)" }}>The Spellbook</div>
              <h2 className="h1" style={{ fontSize: 26 }}>Prepare Today's Spells</h2>
              <Divider />

              <p className="body" style={{ marginTop: 0 }}>
                {hero.name} reads by the dying fire. Choose what will be at hand when the day breaks. Unchosen spells remain bound to the page.
              </p>

              {availableSpells.map((group) => {
                const cur = prepared[group.level] || [];
                const max = slots[group.level] || 0;
                if (max === 0) return null;
                return (
                  <div key={group.level} style={{ marginTop: 18 }}>
                    <SectionTitle right={
                      <span className="muted body-sm">{cur.length} / {max} prepared</span>
                    }>
                      Level {group.level}
                    </SectionTitle>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                      {group.list.map((sp) => {
                        const isPrepared = cur.includes(sp.name);
                        const canPrep = cur.length < max;
                        return (
                          <button
                            key={sp.name}
                            onClick={() => toggleSpell(group.level, sp.name)}
                            disabled={!isPrepared && !canPrep}
                            style={{
                              display: "grid", gridTemplateColumns: "40px 1fr auto", gap: 10, alignItems: "center",
                              padding: 10,
                              textAlign: "left",
                              background: isPrepared
                                ? "linear-gradient(180deg, var(--p-100), var(--p-200))"
                                : "rgba(176,141,87,0.06)",
                              boxShadow: isPrepared
                                ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400), 0 0 14px -4px var(--gold-glow)"
                                : "inset 0 0 0 1px rgba(140,100,60,0.25)",
                              cursor: (!isPrepared && !canPrep) ? "not-allowed" : "pointer",
                              opacity: (!isPrepared && !canPrep) ? 0.5 : 1,
                              transition: "all 140ms",
                            }}
                          >
                            <Placeholder label={sp.glyph} w={40} h={40} framed />
                            <div style={{ minWidth: 0 }}>
                              <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.06em", color: "var(--ink-900)" }}>
                                {sp.name}
                              </div>
                              <div className="hand muted" style={{ fontSize: 11 }}>{sp.school} · {sp.time}</div>
                            </div>
                            {isPrepared && <span style={{ color: "var(--emerald)", fontSize: 14 }}>✓</span>}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}

              <Divider />

              <div style={{ display: "flex", gap: 10, justifyContent: "space-between", alignItems: "center" }}>
                <span className="hand muted">
                  {Object.values(prepared).reduce((s, l) => s + l.length, 0)} bound to the day.
                </span>
                <div style={{ display: "flex", gap: 10 }}>
                  <BrassButton tone="ghost" onClick={onClose}>Close book</BrassButton>
                  <BrassButton onClick={completePrep}>Seal the choices</BrassButton>
                </div>
              </div>
            </>
          )}
        </Panel>
      </div>
    </div>
  );
}

function RestCard({ selected, onClick, title, hand, body, cost }) {
  return (
    <button onClick={onClick} style={{
      textAlign: "left",
      padding: 14,
      background: selected
        ? "linear-gradient(180deg, var(--p-100), var(--p-200))"
        : "rgba(176,141,87,0.06)",
      boxShadow: selected
        ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
        : "inset 0 0 0 1px rgba(140,100,60,0.3)",
      cursor: "pointer",
    }}>
      <div style={{ fontFamily: "var(--f-display)", fontSize: 14, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
        {title}
      </div>
      <div className="hand muted" style={{ fontSize: 13, marginTop: 2 }}>{hand}</div>
      <div className="body-sm" style={{ marginTop: 8, color: "var(--ink-700)", lineHeight: 1.4 }}>{body}</div>
      <div className="eyebrow" style={{ marginTop: 10, color: "var(--crimson)", fontSize: 9 }}>{cost}</div>
    </button>
  );
}

function AbilityScore({ label, value, mod }) {
  return (
    <div style={{
      width: 76, padding: "8px 0",
      textAlign: "center",
      background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
      boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 1px 0 rgba(255,250,220,0.6)",
    }}>
      <div className="eyebrow" style={{ fontSize: 9 }}>{label}</div>
      <div style={{ fontFamily: "var(--f-display)", fontSize: 28, color: "var(--ink-900)", lineHeight: 1, marginTop: 2 }}>
        {value}
      </div>
      <div style={{ fontFamily: "var(--f-display)", fontSize: 12, color: mod >= 0 ? "var(--emerald)" : "var(--crimson)", marginTop: 2 }}>
        {mod >= 0 ? "+" : ""}{mod}
      </div>
    </div>
  );
}

function StatLine({ k, v }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "baseline",
      padding: "6px 10px",
      background: "rgba(176,141,87,0.06)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.2)",
    }}>
      <span className="eyebrow" style={{ fontSize: 9 }}>{k}</span>
      <span style={{ fontFamily: "var(--f-display)", fontSize: 14, color: "var(--ink-900)" }}>{v}</span>
    </div>
  );
}

function AbilitiesTab({ hero }) {
  return (
    <div>
      <SectionTitle ordinal="·">Special Abilities</SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        {hero.abilities.map((a) => (
          <AbilityCard key={a.name} a={a} />
        ))}
      </div>

      <Divider />

      <SectionTitle>Feats</SectionTitle>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {hero.feats.map((f) => (
          <FeatRow key={f.name} f={f} />
        ))}
      </div>
    </div>
  );
}

function AbilityCard({ a }) {
  return (
    <window.Tooltip content={<window.InfoTooltip kind="Ability" title={a.name} body={a.detail} />} side="top">
      <div style={{
        display: "flex", gap: 10, padding: 12,
        background: "rgba(176,141,87,0.08)",
        boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
        cursor: "help",
      }}>
        <Placeholder label={a.glyph} w={44} h={44} framed />
        <div style={{ minWidth: 0 }}>
          <div style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
            {a.name}
          </div>
          <div className="body-sm muted" style={{ marginTop: 2 }}>{a.detail}</div>
        </div>
      </div>
    </window.Tooltip>
  );
}

function FeatRow({ f }) {
  return (
    <window.Tooltip content={<window.InfoTooltip kind="Feat" title={f.name} body={f.detail} />} side="top">
      <div style={{
        display: "flex", gap: 10, alignItems: "center", padding: "8px 10px",
        background: "rgba(176,141,87,0.06)",
        boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
        cursor: "help",
      }}>
        <Placeholder label={f.glyph || "feat"} w={28} h={28} framed />
        <div style={{ flex: 1 }}>
          <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.1em", color: "var(--ink-900)" }}>
            {f.name}
          </span>
          <span className="body-sm muted" style={{ marginLeft: 8 }}>— {f.detail}</span>
        </div>
      </div>
    </window.Tooltip>
  );
}

function SkillsTab({ hero }) {
  return (
    <div>
      <SectionTitle ordinal="·">Skills</SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4 }}>
        {hero.skills.map((s) => (
          <div key={s.name} style={{
            display: "flex", justifyContent: "space-between", alignItems: "baseline",
            padding: "6px 12px",
            background: s.mod > 0 ? "rgba(176,141,87,0.1)" : "transparent",
            boxShadow: "inset 0 -1px 0 rgba(140,100,60,0.15)",
          }}>
            <span className="body-sm" style={{ color: "var(--ink-800)" }}>{s.name}</span>
            <span style={{ fontFamily: "var(--f-display)", fontSize: 14, color: s.mod >= 0 ? "var(--ink-900)" : "var(--crimson)" }}>
              {s.mod >= 0 ? "+" : ""}{s.mod}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SpellsTab({ hero }) {
  return (
    <div>
      <SectionTitle ordinal="·">Spellbook</SectionTitle>
      <div className="eyebrow muted" style={{ marginBottom: 10 }}>Slots per level — Magus</div>
      <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
        {[0,1,2,3,4,5,6,7,8,9].map((lv) => (
          <div key={lv} style={{
            flex: 1, padding: "10px 0", textAlign: "center",
            background: "linear-gradient(180deg, var(--p-100), var(--p-200))",
            boxShadow: "inset 0 0 0 1px var(--b-500)",
          }}>
            <div className="eyebrow" style={{ fontSize: 9 }}>Lv {lv}</div>
            <div style={{ fontFamily: "var(--f-display)", fontSize: 16, color: "var(--ink-900)", marginTop: 2 }}>
              {lv === 0 ? "∞" : lv <= 2 ? Math.max(0, 4 - lv) : "—"}
            </div>
          </div>
        ))}
      </div>

      {hero.spells.map((group) => (
        <div key={group.level} style={{ marginTop: 16 }}>
          <SectionTitle>Level {group.level}</SectionTitle>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {group.list.map((sp) => (
              <div key={sp.name} style={{
                display: "flex", gap: 10, padding: 10,
                background: "rgba(176,141,87,0.08)",
                boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
              }}>
                <Placeholder label={sp.glyph} w={36} h={36} framed />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
                    {sp.name}
                  </div>
                  <div className="body-sm muted">{sp.school} · {sp.time}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function FeatsTab({ hero }) {
  return (
    <div>
      <SectionTitle ordinal="·">Weapon Proficiency</SectionTitle>
      <ul className="body" style={{ paddingLeft: 18, margin: 0 }}>
        {hero.proficiencies.map((p) => (<li key={p}>{p}</li>))}
      </ul>
      <Divider />
      <SectionTitle>Class Features</SectionTitle>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {hero.classFeatures.map((c) => (
          <div key={c.name} style={{ padding: 10, background: "rgba(176,141,87,0.06)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)" }}>
            <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.12em", color: "var(--ink-900)" }}>{c.name}</div>
            <div className="body-sm muted" style={{ marginTop: 2 }}>{c.detail}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { ScreenCharacter, AbilityScore, StatLine, AbilitiesTab, SkillsTab, SpellsTab, FeatsTab, AbilityCard, FeatRow, RestPrepareModal, RestCard });
