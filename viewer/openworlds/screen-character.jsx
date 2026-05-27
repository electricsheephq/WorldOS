/* Screen: Character Sheet — dense, codex/sourcebook style.
   Wired to the live /character-surface read model (full party sheets projected from the
   engine snapshot: classes, skills, spells, class_resources, conditions, AC, death saves).
   Polls every 5s while visible; renders an empty state until the first live fetch (never the demo party).
   Layout/design unchanged from the prototype. */

function ScreenCharacter({ onNavigate, state, setState }) {
  const surfaceQuery = window.combatSurfaceFromCampaign
    ? window.combatSurfaceFromCampaign(
        (Array.isArray(state?.campaigns) ? state.campaigns : []).find((c) => c.id === state?.activeCampaign) ||
          (Array.isArray(state?.campaigns) ? state.campaigns : [])[0] || {},
        state,
      )
    : "";
  const [surface, setSurface] = React.useState(null);
  // LIVE party from the /character-surface read-model only — never the Pathfinder demo party.
  const party = (Array.isArray(surface?.party) && surface.party.length)
    ? surface.party
    : [];
  const [active, setActive] = React.useState("");
  const [tab, setTab] = React.useState("abilities");
  const [restOpen, setRestOpen] = React.useState(false);
  const toast = window.useToast ? window.useToast() : (() => {});

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    try {
      const response = await fetch("/character-surface" + surfaceQuery, { cache: "no-store" });
      if (!response.ok) throw new Error(`character surface ${response.status}`);
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
            <Img scope={p.id ? "portrait-" + p.id : ""} label={p.short} w={36} h={44} framed />
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
            <Img scope={hero.id ? "portrait-" + hero.id : ""} label={`${hero.short} · portrait`} w={140} h={170} framed />
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

              {/* Resources & Status — combat/status state projected by the read-model
                  (classResources / tempHp / deathSaves / concentration / exhaustion /
                  conditions). Each row is hidden when absent or zero; never faked. */}
              <ResourcesStatus hero={hero} />
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
              <StatLine k="Proficiency" v={`+${hero.stats.proficiency_bonus}`} />
              <StatLine k="Initiative" v={`${hero.stats.initiative >= 0 ? "+" : ""}${hero.stats.initiative}`} />
              <StatLine k="Melee" v={`${hero.stats.melee >= 0 ? "+" : ""}${hero.stats.melee}`} />
              <StatLine k="Ranged" v={`${hero.stats.ranged >= 0 ? "+" : ""}${hero.stats.ranged}`} />
            </div>

            <Divider />

            <div className="eyebrow">Defense</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 4, marginTop: 8 }}>
              <StatLine k="AC" v={hero.stats.ac} />
              <StatLine k="Speed" v={`${hero.stats.speed} ft`} />
            </div>

            <Divider />

            {/* 5e saving throws: one per ability (mod + proficiency where proficient),
                computed by the engine read-model (server _character_sheet). */}
            <div className="eyebrow">Saving Throws</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 4, marginTop: 8 }}>
              {["STR","DEX","CON","INT","WIS","CHA"].map((s) => {
                const v = (hero.stats.saves || {})[s.toLowerCase()] ?? 0;
                return <StatLine key={s} k={s} v={`${v >= 0 ? "+" : ""}${v}`} />;
              })}
            </div>

            <Divider />

            <div className="eyebrow">Equipped</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 8 }}>
              {hero.equipped.map((it, i) => (
                <div key={`${it.slot}-${it.name || i}`} style={{
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

      {restOpen && <RestPrepareModal hero={hero} party={party} onClose={() => setRestOpen(false)} toast={toast} setState={setState} />}
    </div>
  );
}

function RestPrepareModal({ hero, party, onClose, toast, setState }) {
  const [step, setStep] = React.useState("rest");
  const [restType, setRestType] = React.useState("long");
  const [prepared, setPrepared] = React.useState({});
  // Watch order is drawn from the LIVE party (first names), never the hardcoded demo trio.
  const watchOrder = (Array.isArray(party) ? party : []).map((p) => (p.name || "").split(" ")[0]).filter(Boolean);

  const availableSpells = hero.spells || [];
  // Data-driven from the live hero. The /character-surface read-model does not project
  // spell-slot counts (it carries spell names only), so this stays empty rather than
  // inventing the old 4/3/1 — every slot row is gated on max > 0, so empty = no fabricated pips.
  const slots = (hero.spellSlots && typeof hero.spellSlots === "object") ? hero.spellSlots : {};

  const toggleSpell = (lv, name) => {
    const cur = prepared[lv] || [];
    const max = slots[lv] || 0;
    if (cur.includes(name)) {
      setPrepared({ ...prepared, [lv]: cur.filter((n) => n !== name) });
    } else if (cur.length < max) {
      setPrepared({ ...prepared, [lv]: [...cur, name] });
    }
  };

  // Display-only: this modal has no write route to the engine. The toasts below are
  // neutral previews — nothing here mutates HP, spell slots, or prepared spells.
  const completeRest = () => {
    toast({
      kind: "rest",
      eyebrow: (restType === "long" ? "Long rest" : "Short rest") + " (preview)",
      title: "Rest preview",
      body: "Display-only — rest is not saved to the engine.",
    });
    setStep("prep");
  };

  const completePrep = () => {
    const count = Object.values(prepared).reduce((s, l) => s + l.length, 0);
    toast({
      kind: "item",
      eyebrow: "Spellbook (preview)",
      title: count + " spell" + (count !== 1 ? "s" : "") + " selected",
      body: "Display-only — spell preparation is not saved to the engine.",
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
              {watchOrder.length > 0 ? (
                <div style={{ display: "flex", gap: 6 }}>
                  {watchOrder.map((p, i) => (
                    <div key={`${p}-${i}`} style={{
                      flex: 1, padding: "8px 10px", textAlign: "center",
                      background: "rgba(176,141,87,0.08)",
                      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
                    }}>
                      <div className="eyebrow" style={{ fontSize: 9 }}>Watch {i + 1}</div>
                      <div style={{ fontFamily: "var(--f-display)", fontSize: 13, color: "var(--ink-900)", marginTop: 2 }}>{p}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="muted body-sm">No party to set a watch.</div>
              )}

              <div style={{ display: "flex", gap: 10, marginTop: 24, justifyContent: "flex-end" }}>
                <BrassButton tone="ghost" onClick={onClose}>Not yet</BrassButton>
                <BrassButton onClick={completeRest} disabled title="Display-only — rest is not saved to the engine">
                  Make camp <span style={{ fontSize: 9, opacity: 0.7 }}>(preview)</span>
                </BrassButton>
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
                  <BrassButton onClick={completePrep} disabled title="Display-only — spell preparation is not saved to the engine">
                    Seal the choices <span style={{ fontSize: 9, opacity: 0.7 }}>(preview)</span>
                  </BrassButton>
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

function ResourcesStatus({ hero }) {
  // Pull only what the surface actually carries; every value is gated so an absent
  // or zero field renders nothing (no hardcoded fakes).
  const resources = (Array.isArray(hero.classResources) ? hero.classResources : [])
    .filter((r) => r && (r.max > 0 || (r.remaining !== null && r.remaining !== undefined)));
  const conditions = Array.isArray(hero.conditions) ? hero.conditions : [];
  const tempHp = Number(hero.tempHp) || 0;
  const exhaustion = Number(hero.exhaustion) || 0;
  const concentration = typeof hero.concentration === "string" ? hero.concentration.trim() : "";
  const death = hero.deathSaves || {};
  const successes = Number(death.successes) || 0;
  const failures = Number(death.failures) || 0;
  // "Dying" per 5e: downed and not yet stable/dead but accruing death saves.
  const dying = !hero.dead && !hero.stable && (successes > 0 || failures > 0);

  const hasStatusPills = tempHp > 0 || exhaustion > 0 || concentration || dying || conditions.length > 0;
  if (!resources.length && !hasStatusPills) return null;

  return (
    <div style={{ marginTop: 14 }}>
      <div className="eyebrow" style={{ marginBottom: 6 }}>Resources &amp; Status</div>

      {hasStatusPills && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {tempHp > 0 && <Pill tone="emerald" dot>Temp HP {tempHp}</Pill>}
          {dying && <Pill tone="crimson" dot>Death Saves {successes}✓ / {failures}✗</Pill>}
          {concentration && <Pill tone="royal" dot>Concentrating · {concentration}</Pill>}
          {exhaustion > 0 && <Pill tone="crimson">Exhaustion {exhaustion}</Pill>}
          {conditions.map((c) => (<Pill key={c}>{c}</Pill>))}
        </div>
      )}

      {resources.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, marginTop: hasStatusPills ? 8 : 0 }}>
          {resources.filter((r) => Number(r.max) > 0).map((r) => {
            const remaining = (r.remaining !== null && r.remaining !== undefined) ? r.remaining : Math.max(0, (r.max || 0) - (r.used || 0));
            return <StatLine key={r.id || r.name} k={r.name} v={`${remaining} / ${r.max}`} />;
          })}
        </div>
      )}
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

// Render a spell group's heading from the surface's real `level` field. The engine
// groups everything under the string "Known" (it stores spell names, not slot blocks),
// so prefix numeric levels with "Level" but show string labels (e.g. "Known") verbatim —
// never the nonsense "Level Known", and never invented slot math.
function spellGroupLabel(level) {
  return (typeof level === "number" || /^\d+$/.test(String(level))) ? `Level ${level}` : String(level);
}

function SpellsTab({ hero }) {
  // Data-driven from the /character-surface read-model. The surface does not project
  // spell-slot counts, so we render only what it provides (known/prepared spells) and
  // fall back to an honest empty state — no hardcoded class label, no fabricated slots.
  const groups = (Array.isArray(hero.spells) ? hero.spells : []).filter((g) => g && Array.isArray(g.list) && g.list.length);

  if (!groups.length) {
    return (
      <div>
        <SectionTitle ordinal="·">Spellbook</SectionTitle>
        <div className="muted body-sm" style={{ marginTop: 8 }}>No spells prepared.</div>
      </div>
    );
  }

  return (
    <div>
      <SectionTitle ordinal="·">Spellbook</SectionTitle>
      {groups.map((group) => (
        <div key={group.level} style={{ marginTop: 16 }}>
          <SectionTitle>{spellGroupLabel(group.level)}</SectionTitle>
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

Object.assign(window, { ScreenCharacter, AbilityScore, StatLine, ResourcesStatus, AbilitiesTab, SkillsTab, SpellsTab, FeatsTab, AbilityCard, FeatRow, RestPrepareModal, RestCard });
