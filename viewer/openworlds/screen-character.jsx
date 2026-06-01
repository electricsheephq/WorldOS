/* Screen: Character Sheet — dense, codex/sourcebook style.
   Wired to the live /character-surface read model (full party sheets projected from the
   engine snapshot: classes, skills, spells, class_resources, conditions, AC, death saves).
   Polls every 5s while visible; renders an empty state until the first live fetch (never the demo party).
   Layout/design unchanged from the prototype. */

/* Build the /image scope for a hero portrait. Ingested canon art is keyed by a NAME-slug
   ("portrait_dal-lightspark") which the server normalises from "portrait-<name-slug>"; a met
   roster NPC already carries a slug id ("npc-minsc") that also normalises to the same key.
   A loaded PC/companion, however, carries a random instance id ("char_40c15af4c9fc") that
   matches no art — so we derive the scope from slug(name), which resolves real faces for
   canon heroes and degrades to the silhouette (via Img's onError) for portrait-less ones. */
function characterPortraitScope(p) {
  const s = (p && p.name && window.slug) ? window.slug(p.name) : "";
  if (s) return "portrait-" + s;
  return (p && p.id) ? "portrait-" + p.id : "";
}

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
  // Portrait scope: ingested canon art is keyed by a NAME-slug ("portrait_dal-lightspark"),
  // which the server normalises from "portrait-<name-slug>". A loaded party member's id is a
  // random instance hash ("char_40c15af4c9fc") that never matches, so deriving the scope from
  // the name slug is what makes a canon hero's real face render (a custom/portrait-less hero
  // still falls back to the silhouette via Img's onError). See heroPortraitScope below.

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
            <Img scope={characterPortraitScope(p)} label={p.name} w={36} h={44} framed />
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
            <Img scope={characterPortraitScope(hero)} label={`${hero.name} · portrait`} w={140} h={170} framed />
            <div>
              <div className="eyebrow" style={{ color: "var(--crimson)" }}>{hero.alignment}</div>
              <h1 className="h1" style={{ marginTop: 2 }}>{hero.name}</h1>
              <div className="hand" style={{ fontSize: 17, color: "var(--ink-700)", marginTop: 2 }}>
                {[hero.race, hero.class, hero.archetype].map((s) => (s || "").trim()).filter(Boolean).join(" · ")}
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
        <div className="stack-on-narrow" style={{ display: "grid", gridTemplateColumns: "1.05fr 1.7fr 1fr", gap: 14, minHeight: 0 }}>

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
            <HeroEquipDoll hero={hero} onNavigate={onNavigate} />
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
            <LineagePanel hero={hero} />

            <Divider />

            <SectionTitle>Traits</SectionTitle>
            {(Array.isArray(hero.traits) && hero.traits.length > 0) ? (
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
                    {t.detail && <div className="body-sm muted" style={{ marginTop: 2 }}>{t.detail}</div>}
                  </div>
                ))}
              </div>
            ) : (
              <p className="body-sm muted" style={{ margin: 0 }}>No distinguishing traits recorded.</p>
            )}

            {/* Resistances / Immunities — 5e carries no flat "damage reduction"; the read-model
                emits dr.value / dr.energy as named resistances. Hide the whole section when the
                hero has none ("None"/blank) rather than show a confusing "None / None". */}
            {(() => {
              const dr = hero.dr || {};
              const has = (v) => { const s = String(v ?? "").trim().toLowerCase(); return s && s !== "none" && s !== "0"; };
              if (!has(dr.value) && !has(dr.energy)) return null;
              return (
                <>
                  <Divider />
                  <SectionTitle>Resistances</SectionTitle>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                    {has(dr.value) && <StatLine k="Physical" v={dr.value} />}
                    {has(dr.energy) && <StatLine k="Elemental" v={dr.energy} />}
                  </div>
                </>
              );
            })()}
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
    }} onClick={onClose} role="dialog" aria-modal="true" aria-label="Rest and prepare" data-worldos-testid="rest-prepare-modal">
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
                <BrassButton tone="ghost" onClick={onClose} testId="modal-close" ariaLabel="Close rest and prepare modal">Not yet</BrassButton>
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
                              <div className="hand muted" style={{ fontSize: 11 }}>{spellMeta(sp)}</div>
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
                  <BrassButton tone="ghost" onClick={onClose} testId="modal-close" ariaLabel="Close rest and prepare modal">Close book</BrassButton>
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

// A short stat line for an equipped item's tooltip / slot caption from the read-model's
// REAL catalog stats — "1d8 slashing" for a weapon, "AC 18" for armor — or "" when the
// item is a catalog-miss (then we show just its name; never a fabricated stat).
function equippedStat(it) {
  if (!it) return "";
  if (it.damage) return [it.damage, it.damageType].filter(Boolean).join(" ");
  if (typeof it.ac === "number") return `AC ${it.ac}`;
  return "";
}

function HeroEquipDoll({ hero, onNavigate }) {
  // Slotted paper-doll for the combat column (#271 brought to the hero sheet). Reuses the
  // SAME canonical slot set + name→slot assignment that the inventory screen's PaperDoll uses
  // (window.EQUIP_SLOTS / window.assignEquipSlots) so a hero's gear lands in Head/Body/Main-Hand/
  // etc. instead of a flat "Worn" list. Each filled cell shows the item's real catalog stat
  // (damage dice / AC) from the read-model; empty cells render an honest labeled slot.
  const equipped = Array.isArray(hero.equipped) ? hero.equipped : [];
  const SLOTS = Array.isArray(window.EQUIP_SLOTS) ? window.EQUIP_SLOTS : [];
  const assign = typeof window.assignEquipSlots === "function" ? window.assignEquipSlots : null;
  const slug = window.slug || ((n) => (n || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""));

  if (equipped.length === 0) {
    return (
      <div style={{
        marginTop: 8, padding: "12px 14px", textAlign: "center",
        background: "rgba(176,141,87,0.06)",
        boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
      }}>
        <div className="hand muted" style={{ fontSize: 12 }}>No gear equipped.</div>
        {onNavigate && (
          <BrassButton tone="ghost" size="sm" style={{ marginTop: 8 }} onClick={() => onNavigate("inventory")}>
            Open the stash
          </BrassButton>
        )}
      </div>
    );
  }

  // Build the {slotId: item} map (preferring the shared inventory helper). If that helper
  // isn't loaded, fall back to listing each equipped item under its recorded slot — no crash.
  const assigned = assign ? assign(equipped) : null;
  const cells = (assigned && SLOTS.length)
    ? SLOTS.map((s) => ({ slot: s, item: assigned[s.id] })).filter((c) => c.item)
    : equipped.map((it, i) => ({ slot: { id: `e${i}`, label: it.slot || "Worn" }, item: it }));

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 8 }}>
      {cells.map(({ slot, item }) => {
        const stat = equippedStat(item);
        return (
          <window.Tooltip
            key={slot.id}
            content={<window.InfoTooltip kind={slot.label} title={item.name} body={[stat, item.rarity && item.rarity !== "common" ? item.rarity : "", item.attunement ? "Requires attunement" : ""].filter(Boolean).join(" · ") || `Worn in the ${slot.label} slot.`} />}
            side="top"
          >
            <div style={{
              display: "flex", gap: 8, alignItems: "center",
              padding: 8,
              background: "rgba(176,141,87,0.08)",
              boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
              cursor: "help",
            }}>
              {/* Equipped item art — mirror screen-inventory's `item-<slug(name)>` scope (the
                  engine keys ingested item icons by a name-slug; the surface emits it.name on
                  each equipped entry). <Img> falls back to a Placeholder on a 404. */}
              <Img scope={item.name ? "item-" + slug(item.name) : ""} label={item.name || item.glyph} w={32} h={32} fit="contain" framed />
              <div style={{ minWidth: 0 }}>
                <div className="eyebrow" style={{ fontSize: 9 }}>{slot.label}</div>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 11, color: "var(--ink-900)", letterSpacing: "0.05em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {item.name}
                </div>
                {/* Real stat (damage dice / AC) when the catalog resolved it — never faked. */}
                {stat && <div className="hand muted" style={{ fontSize: 10, lineHeight: 1.1 }}>{stat}</div>}
              </div>
            </div>
          </window.Tooltip>
        );
      })}
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

// Title-case a class token ("wizard" -> "Wizard") for the honest class-context line.
function titleCaseWord(s) {
  return String(s || "").replace(/\b\w/g, (m) => m.toUpperCase());
}

function AbilitiesTab({ hero }) {
  // `hero.abilities` is reserved for richly-modeled active-ability CARDS (name + detail);
  // the engine does not populate those today, so it is empty. But the engine DOES carry the
  // character's class/subclass features (as NAMES) in `hero.classFeatures` — those were only
  // ever shown on the Feats tab, so the Abilities tab read "No active abilities recorded" for
  // a real caster. Surface the class features here too (NAMES only — the engine does not model
  // feature descriptions, so we never invent body text), with honest class/level context.
  const abilities = Array.isArray(hero.abilities) ? hero.abilities : [];
  const feats = Array.isArray(hero.feats) ? hero.feats : [];
  const classFeatures = Array.isArray(hero.classFeatures) ? hero.classFeatures : [];
  const classLine = [
    hero.level != null ? `Level ${hero.level}` : null,
    hero.class ? titleCaseWord(hero.class) : null,
    hero.archetype || null,
  ].filter(Boolean).join(" · ");
  const nothing = abilities.length === 0 && classFeatures.length === 0;
  return (
    <div>
      <SectionTitle ordinal="·">Special Abilities</SectionTitle>
      {abilities.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
          {abilities.map((a) => (
            <AbilityCard key={a.name} a={a} />
          ))}
        </div>
      )}

      {/* Class & subclass features the engine granted at this level (Arcane Recovery, a
          School-of-Magic feature, etc.). Names come straight from the engine's `features`
          list; detail is shown only when the data carries it (the engine models names, not
          descriptions today — so most show name-only, never fabricated text). */}
      {classFeatures.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {classLine && <div className="eyebrow" style={{ marginBottom: 2 }}>{classLine}</div>}
          {classFeatures.map((c) => (
            <div key={c.name} style={{ padding: 10, background: "rgba(176,141,87,0.06)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)" }}>
              <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.12em", color: "var(--ink-900)" }}>{c.name}</div>
              {c.detail && <div className="body-sm muted" style={{ marginTop: 2 }}>{c.detail}</div>}
            </div>
          ))}
        </div>
      )}

      {nothing && (
        <p className="body-sm muted" style={{ margin: 0 }}>
          {classLine
            ? `No class, subclass, or racial features are recorded for this ${classLine} yet.`
            : "No active abilities recorded — this hero's edge is in their feats and class features."}
        </p>
      )}

      <Divider />

      <SectionTitle>Feats</SectionTitle>
      {feats.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {feats.map((f) => (
            <FeatRow key={f.name} f={f} />
          ))}
        </div>
      ) : (
        <p className="body-sm muted" style={{ margin: 0 }}>No feats taken yet.</p>
      )}
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
          {f.detail && <span className="body-sm muted" style={{ marginLeft: 8 }}>— {f.detail}</span>}
        </div>
      </div>
    </window.Tooltip>
  );
}

function ProficiencyDot({ proficient, expertise }) {
  const filled = {
    width: 7, height: 7, borderRadius: "50%",
    background: "radial-gradient(circle at 30% 30%, var(--gold-glow, #f4d27b), var(--b-500))",
    boxShadow: "inset 0 0 0 1px var(--b-600)",
  };
  const hollow = { width: 7, height: 7, borderRadius: "50%", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.5)" };
  const title = expertise ? "Expertise (double proficiency)" : proficient ? "Proficient" : "Not proficient";
  return (
    <span title={title} aria-label={title} style={{ display: "inline-flex", gap: 2, alignSelf: "center", flexShrink: 0 }}>
      <span style={proficient || expertise ? filled : hollow} />
      {expertise && <span style={filled} />}
    </span>
  );
}

// A small text badge naming a skill's training, paired with the proficiency dots so the
// marker is unmissable (the bare 7px dot read as decoration to power players). "Expertise"
// = double proficiency, "Prof" = proficient, nothing for untrained. Values come straight
// from the read-model's per-skill proficient/expertise flags — never inferred from the mod.
function ProficiencyBadge({ proficient, expertise }) {
  if (!proficient && !expertise) return null;
  const label = expertise ? "Expertise" : "Prof";
  return (
    <span className="eyebrow" style={{
      fontSize: 8, letterSpacing: "0.12em", padding: "1px 5px", flexShrink: 0,
      color: expertise ? "var(--w-300)" : "var(--ink-800)",
      background: expertise
        ? "linear-gradient(180deg, var(--b-300), var(--b-500))"
        : "rgba(176,141,87,0.22)",
      boxShadow: expertise
        ? "inset 0 0 0 1px var(--b-600), 0 0 8px -3px var(--gold-glow)"
        : "inset 0 0 0 1px rgba(140,100,60,0.45)",
    }}>{label}</span>
  );
}

function SkillsTab({ hero }) {
  const skills = Array.isArray(hero.skills) ? hero.skills : [];
  const proficientCount = skills.filter((s) => s.proficient || s.expertise).length;
  const expertiseCount = skills.filter((s) => s.expertise).length;
  return (
    <div>
      <SectionTitle ordinal="·" right={
        <span className="muted body-sm">
          {proficientCount} proficient{expertiseCount ? ` · ${expertiseCount} expertise` : ""}
        </span>
      }>Skills</SectionTitle>
      {/* Legend so the marker convention is self-explanatory at a glance. */}
      <div style={{ display: "flex", gap: 14, alignItems: "center", marginBottom: 8, flexWrap: "wrap" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          <ProficiencyDot proficient expertise={false} />
          <span className="muted" style={{ fontSize: 10 }}>Proficient</span>
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          <ProficiencyDot proficient expertise />
          <span className="muted" style={{ fontSize: 10 }}>Expertise (×2 prof)</span>
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
          <ProficiencyDot proficient={false} expertise={false} />
          <span className="muted" style={{ fontSize: 10 }}>Untrained</span>
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4 }}>
        {skills.map((s) => (
          <div key={s.name} title={s.expertise ? "Expertise — double proficiency bonus" : s.proficient ? "Proficient" : "Not proficient"} style={{
            display: "flex", justifyContent: "space-between", alignItems: "center",
            padding: "6px 10px 6px 8px",
            background: (s.proficient || s.expertise) ? "rgba(176,141,87,0.12)" : "transparent",
            // A gold left-accent bar on trained skills — the second, unmissable cue.
            borderLeft: s.expertise ? "3px solid var(--b-400)" : s.proficient ? "3px solid rgba(176,141,87,0.7)" : "3px solid transparent",
            boxShadow: "inset 0 -1px 0 rgba(140,100,60,0.15)",
          }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7, minWidth: 0 }}>
              {/* Proficiency marker (DNDBeyond/BG3 convention): filled gold dot = proficient,
                  two dots = expertise (double proficiency), hollow = untrained — paired with a
                  text badge so power players don't miss it. */}
              <ProficiencyDot proficient={s.proficient} expertise={s.expertise} />
              <span className="body-sm" style={{ color: (s.proficient || s.expertise) ? "var(--ink-900)" : "var(--ink-700)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{s.name}</span>
              <ProficiencyBadge proficient={s.proficient} expertise={s.expertise} />
            </span>
            <span style={{ fontFamily: "var(--f-display)", fontSize: 14, color: s.mod >= 0 ? "var(--ink-900)" : "var(--crimson)", flexShrink: 0 }}>
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

// Compose a spell's compact subline. Prefer the REAL SRD level/school the read-model now
// projects ("Cantrip · Evocation" / "Level 3 · Evocation"); fall back to the prepared/known
// grouping only when the SRD lookup missed. Drops any em-dash / blank placeholder so we never
// render the bare "— · prepared".
function spellMeta(sp) {
  const clean = (v) => { const s = String(v ?? "").trim(); return (s && s !== "—" && s !== "-") ? s : ""; };
  const lvl = clean(sp.levelLabel);
  const school = clean(sp.school);
  if (lvl || school) return [lvl, school].filter(Boolean).join(" · ");
  return [clean(sp.school), clean(sp.time)].filter(Boolean).join(" · ");
}

// True when the read-model resolved this spell's SRD rules block (so we have something richer
// than the bare name to show). A catalog-miss spell carries only name + grouping.
function hasSpellRules(sp) {
  return Boolean(sp && (sp.range || sp.duration || sp.castingTime || sp.damage || sp.save || sp.desc));
}

// One labeled rules chip ("Range · 150 feet"). Hidden when the value is blank so a spell that
// carries only some fields (e.g. a self-buff with no save) never shows empty rows.
function SpellRuleChip({ label, value }) {
  const v = String(value ?? "").trim();
  if (!v || v === "—") return null;
  return (
    <div style={{
      padding: "4px 8px",
      background: "rgba(176,141,87,0.06)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.2)",
    }}>
      <span className="eyebrow" style={{ fontSize: 8 }}>{label}</span>
      <div style={{ fontFamily: "var(--f-display)", fontSize: 12, color: "var(--ink-900)", marginTop: 1 }}>{v}</div>
    </div>
  );
}

// The per-spell rules block: range / casting time / duration / save (with the caster's DC) /
// damage / components, plus Concentration / Ritual badges and the upcast + description prose.
// EVERY field is sourced from the engine's real srd524 record via the read-model — a spell the
// SRD doesn't carry shows nothing here (just its name above). `compact` trims to the headline
// stat chips for the in-tab card; the full block (with desc + upcast) shows in the browser.
function SpellRules({ sp, compact }) {
  if (!hasSpellRules(sp)) return null;
  const dc = sp.save && (sp.saveDc !== null && sp.saveDc !== undefined)
    ? `DC ${sp.saveDc} ${String(sp.save).slice(0, 3).toUpperCase()}`
    : (sp.save ? `${String(sp.save).slice(0, 3).toUpperCase()} save` : "");
  const dmg = sp.damage ? [sp.damage, sp.damageType].filter(Boolean).join(" ") + (sp.attack ? " (spell attack)" : "") : "";
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: dc || dmg ? 6 : 0 }}>
        {sp.concentration && <Pill tone="royal" dot>Concentration</Pill>}
        {sp.ritual && <Pill tone="emerald">Ritual</Pill>}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: compact ? "1fr 1fr" : "1fr 1fr 1fr", gap: 4 }}>
        <SpellRuleChip label="Range" value={sp.range} />
        <SpellRuleChip label="Cast" value={sp.castingTime} />
        <SpellRuleChip label="Duration" value={sp.duration} />
        {dc && <SpellRuleChip label="Save" value={dc} />}
        {dmg && <SpellRuleChip label="Effect" value={dmg} />}
        {!compact && <SpellRuleChip label="Components" value={sp.components} />}
      </div>
      {!compact && sp.material && (
        <div className="hand muted" style={{ fontSize: 11, marginTop: 4 }}>Material: {sp.material}</div>
      )}
      {!compact && sp.desc && (
        <p className="body-sm" style={{ marginTop: 8, marginBottom: 0, color: "var(--ink-700)", lineHeight: 1.45 }}>{sp.desc}</p>
      )}
      {!compact && sp.higherLevel && (
        <p className="body-sm muted" style={{ marginTop: 6, marginBottom: 0, lineHeight: 1.4 }}>
          <span className="eyebrow" style={{ fontSize: 8 }}>At higher levels</span><br />
          {sp.higherLevel}
        </p>
      )}
    </div>
  );
}

function LineagePanel({ hero }) {
  // A dead hero's sheet drops the lineage panel entirely (#308) — the living-world surface
  // stops projecting lineage flavor for the fallen, so render nothing rather than a stale block.
  if (hero.dead) return null;
  // Surface the engine's authoritative lineage: race (+ subrace) as the heading, the
  // racial traits the snapshot carries, and any backstory/personality flavor note. Honest
  // empty-state only when the snapshot truly records no race and no flavor — never invent.
  const race = (hero.race || "").trim();
  const subrace = (hero.subrace || "").trim();
  const traits = Array.isArray(hero.raceTraits) ? hero.raceTraits.filter(Boolean) : [];
  const note = (hero.lineageNote || "").trim();

  if (!race && !note) {
    return <p className="body muted" style={{ marginTop: 0, fontSize: 14 }}>No lineage recorded for this hero.</p>;
  }

  return (
    <div>
      {race && (
        <div style={{ marginBottom: note ? 10 : 0 }}>
          <div style={{ fontFamily: "var(--f-display)", fontSize: 18, letterSpacing: "0.04em", color: "var(--ink-900)" }}>
            {race}
          </div>
          {subrace && <div className="muted body-sm" style={{ marginTop: 2 }}>{subrace}</div>}
        </div>
      )}
      {traits.length > 0 && (
        <ul className="body-sm" style={{ margin: "6px 0 0", paddingLeft: 18, color: "var(--ink-700)" }}>
          {traits.map((t) => (<li key={t}>{t}</li>))}
        </ul>
      )}
      {note && (
        <p className="body dropcap" style={{ marginTop: race ? 10 : 0, marginBottom: 0, fontSize: 15 }}>
          {note}
        </p>
      )}
    </div>
  );
}

function SpellSlotTrack({ slots }) {
  // Render the engine's per-level spell-slot pools as pip tracks (filled = available,
  // hollow = spent). Pure read-model projection (hero.spellSlots); no mutation.
  if (!Array.isArray(slots) || !slots.length) return null;
  return (
    <div style={{ marginBottom: 18 }}>
      <SectionTitle>Spell Slots</SectionTitle>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
        {slots.map((s) => (
          <div key={s.level} style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--ink-800)", minWidth: 64 }}>
              Level {s.level}
            </span>
            <div style={{ display: "flex", gap: 4 }}>
              {Array.from({ length: s.max }).map((_, i) => (
                <span key={i} title={i < s.remaining ? "available" : "spent"} style={{
                  width: 12, height: 12, borderRadius: "50%",
                  background: i < s.remaining
                    ? "radial-gradient(circle at 30% 30%, var(--gold-glow, #f4d27b), var(--b-500))"
                    : "transparent",
                  boxShadow: "inset 0 0 0 1px var(--b-500)",
                }} />
              ))}
            </div>
            <span className="muted body-sm">{s.remaining} / {s.max}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SpellsTab({ hero }) {
  // Data-driven from the /character-surface read-model. The surface projects spell-slot
  // pools (hero.spellSlots) and the spell NAMES the engine carries (hero.spells, grouped
  // Prepared/Known). We render the slot track always (when a caster has slots) and the
  // spell list when present — falling back to an honest empty state for the names, since
  // the snapshot stores no spell blocks to fabricate from.
  const groups = (Array.isArray(hero.spells) ? hero.spells : []).filter((g) => g && Array.isArray(g.list) && g.list.length);
  const slots = Array.isArray(hero.spellSlots) ? hero.spellSlots : [];
  const isCaster = slots.length > 0 || groups.length > 0;
  // #268: every caster gets a working "Browse spellbook" path — a read-only inspector over
  // the hero's known + prepared spells (the surface's hero.spells groups). The empty state
  // INVITES the browse instead of dead-ending; preparation stays in the Rest & Prepare modal.
  const [browsing, setBrowsing] = React.useState(false);
  const spellCount = groups.reduce((n, g) => n + g.list.length, 0);

  const browseCta = isCaster ? (
    <BrassButton
      tone="dark"
      size="sm"
      onClick={() => setBrowsing(true)}
      title="Inspect this hero's known & prepared spells (read-only)"
    >
      Browse spellbook{spellCount ? ` (${spellCount})` : ""}
    </BrassButton>
  ) : null;

  return (
    <div>
      <SectionTitle ordinal="·" right={browseCta}>Spellbook</SectionTitle>
      <SpellSlotTrack slots={slots} />
      {groups.length ? (
        groups.map((group) => (
          <div key={group.level} style={{ marginTop: 16 }}>
            <SectionTitle>{spellGroupLabel(group.level)}</SectionTitle>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {group.list.map((sp) => (
                <div key={sp.name} style={{
                  padding: 10,
                  background: "rgba(176,141,87,0.08)",
                  boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
                }}>
                  <div style={{ display: "flex", gap: 10, minWidth: 0 }}>
                    <Placeholder label={sp.glyph} w={36} h={36} framed />
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
                        {sp.name}
                      </div>
                      <div className="body-sm muted">{spellMeta(sp)}</div>
                    </div>
                  </div>
                  {/* Real SRD rules (range / duration / save DC / damage) from the read-model.
                      Renders nothing for a spell the SRD doesn't carry — never fabricated. */}
                  <SpellRules sp={sp} compact />
                </div>
              ))}
            </div>
          </div>
        ))
      ) : isCaster ? (
        // Caster with open slots but no spell NAMES bound yet — invite the browse path
        // (and the Rest & Prepare flow) instead of dead-ending on a flat message (#268).
        <div style={{
          marginTop: slots.length ? 16 : 8,
          padding: "16px 18px", textAlign: "center",
          background: "rgba(176,141,87,0.06)",
          boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
        }}>
          <div className="hand muted" style={{ fontSize: 13 }}>
            This hero holds open spell slots but has bound no spells to the page.
          </div>
          <div style={{ marginTop: 10 }}>
            <BrassButton
              tone="dark"
              size="sm"
              onClick={() => setBrowsing(true)}
              title="Open this hero's spellbook (read-only)"
            >
              Browse spellbook
            </BrassButton>
          </div>
        </div>
      ) : (
        <div className="muted body-sm" style={{ marginTop: slots.length ? 16 : 8 }}>
          This hero prepares no spells.
        </div>
      )}

      {browsing && <SpellbookBrowser hero={hero} groups={groups} onClose={() => setBrowsing(false)} />}
    </div>
  );
}

function SpellbookBrowser({ hero, groups, onClose }) {
  // Read-only spellbook inspector (#268). Surfaces the hero's known + prepared spells from
  // the /character-surface read-model — no preparation here (the Rest & Prepare modal owns
  // that write-flow). When the engine carries no spell NAMES, we say so honestly rather than
  // fabricate an SRD list, and point the player at Rest & Prepare.
  const list = (Array.isArray(groups) ? groups : []).filter((g) => g && Array.isArray(g.list) && g.list.length);
  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 200,
      background: "rgba(15, 8, 2, 0.7)",
      display: "grid", placeItems: "center",
      backdropFilter: "blur(2px)",
    }} onClick={onClose} role="dialog" aria-modal="true" aria-label="Spellbook" data-worldos-testid="spellbook-modal">
      <div onClick={(e) => e.stopPropagation()} style={{ width: 640, maxWidth: "92vw", maxHeight: "88vh", overflow: "auto" }}>
        <Panel framed>
          <div className="eyebrow" style={{ color: "var(--crimson)" }}>The Spellbook</div>
          <h2 className="h1" style={{ fontSize: 24 }}>{hero.name}'s Spells</h2>
          <p className="hand muted" style={{ fontSize: 13, marginTop: 2 }}>
            Read-only. Prepare or change today's spells from <em>Rest &amp; Prepare</em>.
          </p>
          <Divider />

          {list.length ? (
            list.map((group) => (
              <div key={group.level} style={{ marginTop: 16 }}>
                <SectionTitle right={<span className="muted body-sm">{group.list.length}</span>}>
                  {spellGroupLabel(group.level)}
                </SectionTitle>
                <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 8 }}>
                  {group.list.map((sp) => (
                    <div key={sp.name} style={{
                      padding: 12,
                      background: "rgba(176,141,87,0.08)",
                      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
                    }}>
                      <div style={{ display: "flex", gap: 10, alignItems: "center", minWidth: 0 }}>
                        <Placeholder label={sp.glyph} w={36} h={36} framed />
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
                            {sp.name}
                          </div>
                          <div className="body-sm muted">{spellMeta(sp)}</div>
                        </div>
                      </div>
                      {/* Full SRD rules block (range / save DC / damage / components / description /
                          upcast) — all from the engine's real srd524 record via the read-model. */}
                      <SpellRules sp={sp} />
                    </div>
                  ))}
                </div>
              </div>
            ))
          ) : (
            <p className="body muted" style={{ marginTop: 0 }}>
              No spells are inscribed in this hero's book yet. Open <em>Rest &amp; Prepare</em>
              {" "}to bind spells to the day.
            </p>
          )}

          <Divider />
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <BrassButton tone="ghost" onClick={onClose} testId="modal-close" ariaLabel="Close spellbook modal">Close book</BrassButton>
          </div>
        </Panel>
      </div>
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
            {c.detail && <div className="body-sm muted" style={{ marginTop: 2 }}>{c.detail}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, { ScreenCharacter, AbilityScore, StatLine, ResourcesStatus, HeroEquipDoll, equippedStat, AbilitiesTab, SkillsTab, SpellsTab, SpellbookBrowser, SpellSlotTrack, SpellRules, SpellRuleChip, hasSpellRules, LineagePanel, FeatsTab, AbilityCard, FeatRow, RestPrepareModal, RestCard, ProficiencyDot, ProficiencyBadge, characterPortraitScope, spellMeta });
