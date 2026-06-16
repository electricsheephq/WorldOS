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

function ScreenCharacter({ onNavigate, state, setState, liveSession }) {
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
  const [levelUpOpen, setLevelUpOpen] = React.useState(false);
  const toast = window.useToast ? window.useToast() : (() => {});

  // #610/#617 — the Rest & Prepare relay. The Make-camp / Seal-the-choices CTAs write THROUGH THE
  // ENGINE (sole writer) via /move, exactly as camp-sidebar.jsx and the level-up picker do — the
  // viewer never mutates HP / slots / prepared spells itself. They are live + functional when a
  // session is attached (`can_act`) AND the DM isn't mid-turn (#402), honestly disabled + explained
  // otherwise. `dmBusy` rides the app-level liveSession hook, mirroring ScreenMap → CampSidebar.
  const canAct = Boolean(surface?.can_act);
  const dmPending = liveSession?.pending || null;
  const dmBusy = Boolean(dmPending && !dmPending.stuck);
  const campaignId = surface?.campaign_id || state?.activeCampaign || "";

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
        {/* #397: the build-choice affordance. Shown ONLY when the engine read-model says a choice
            is actually due — a pending subclass (pendingSubclass, even from creation above the
            choose-level) or enough XP for the next level. Never a faked "always available" button. */}
        {(hero.pendingSubclass || (Number(hero.xp) >= Number(hero.xpMax) && Number(hero.xpMax) > 0)) && (
          <BrassButton size="sm" onClick={() => setLevelUpOpen(true)} style={{ width: "100%", marginTop: 8 }}
            testId="level-up-open" ariaLabel={hero.pendingSubclass ? "Choose your subclass" : "Level up"}>
            {hero.pendingSubclass ? "Choose Subclass" : "Level Up"}
          </BrassButton>
        )}
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
              {/* Loop-10 #383: player-authored house line under the name, italic and
                  muted so it reads as a subtitle, not a status. Renders only when
                  the wizard's Family/House input is set; honest empty otherwise. */}
              {hero.house && (
                <div className="hand" style={{ fontSize: 14, color: "var(--ink-600)", marginTop: 0, fontStyle: "italic" }}>
                  of House {hero.house}
                </div>
              )}
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
              {/* #depth: read-model now emits passivePerception + hitDice/hitDiceRemaining (server.py) */}
              {hero.stats.passivePerception != null ? (
                <StatLine k="Passive Perc" v={hero.stats.passivePerception} />
              ) : null}
              {hero.stats.hitDice ? (
                <StatLine k="Hit Dice" v={`${hero.stats.hitDiceRemaining}/${hero.stats.hitDice}`} />
              ) : null}
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

      {restOpen && (
        <RestPrepareModal
          hero={hero}
          party={party}
          campaignId={campaignId}
          canAct={canAct}
          dmBusy={dmBusy}
          onClose={() => setRestOpen(false)}
          onDone={() => { setRestOpen(false); loadSurface(); }}
          toast={toast}
          setState={setState}
        />
      )}
      {levelUpOpen && (
        <LevelUpModal
          hero={hero}
          campaignId={surface?.campaign_id || state?.activeCampaign || ""}
          onClose={() => setLevelUpOpen(false)}
          onDone={() => { setLevelUpOpen(false); loadSurface(); }}
          toast={toast}
        />
      )}
    </div>
  );
}

// #397 — the build-choice PICKER. Like RestPrepareModal's prepare step (which relays prepare_spells
// the same way), this writes for real: it reads the engine-owned legal level-up preview from
// /build-options (HP/features/slots — never faked), and on confirm relays a `do` move-intent to the
// DM, who resolves it through the engine level_up tool (sole writer) exactly as camp-sidebar.jsx
// relays "make camp" and RestPrepareModal relays the rest + prepare. The subclass is NOT
// a hardcoded dropdown — the engine does not enumerate world-canon subclasses (class_data has no
// subclass list); the player NAMES it and the DM, which knows the world's options, finalizes it.
function LevelUpModal({ hero, campaignId, onClose, onDone, toast }) {
  const [planner, setPlanner] = React.useState(null);
  const [error, setError] = React.useState("");
  const [loading, setLoading] = React.useState(true);
  const [chosenClass, setChosenClass] = React.useState((hero.class || "").toLowerCase());
  const [subclassName, setSubclassName] = React.useState("");
  // #607: the ASI/feat choice is STRUCTURED now (not a free-text note). `asiBumps` maps an ability
  // abbrev (str/dex/…) to its chosen increment; the engine's contract is +2 to one ability OR +1 to
  // two (each capped at 20 — server.py `_validated_asi_choice`). `takingFeat` flips to the feat path
  // (mutually exclusive: the engine rejects both). `featName` carries the chosen feat's name.
  const [asiBumps, setAsiBumps] = React.useState({});
  const [takingFeat, setTakingFeat] = React.useState(false);
  const [featName, setFeatName] = React.useState("");
  // #feat-browser: the browsable SRD feat list (from GET /feat-catalog), so the feat choice is a
  // real picker showing each feat's effect text + prerequisite — not a blind free-text box. Lazily
  // loaded the first time the feat pane opens (`takingFeat`), then filtered client-side by `featSearch`.
  const [feats, setFeats] = React.useState(null);   // null = not yet loaded; [] = loaded/empty
  const [featSearch, setFeatSearch] = React.useState("");
  // #882 roadmap: the "see your path to 20" planning view. Lazily fetched (GET /level-roadmap) the
  // first time the player expands it, so the common confirm-the-next-level flow pays no extra fetch.
  // This is a READ-ONLY projection of the SRD tables — it relays NOTHING (not an action, a plan).
  const [roadmapOpen, setRoadmapOpen] = React.useState(false);
  const [roadmap, setRoadmap] = React.useState(null);   // null = not yet loaded; [] = loaded/empty
  const [roadmapError, setRoadmapError] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);
  // #robustness: synchronous in-flight lock (mirrors screen-table.jsx). The `submitting` STATE
  // only updates on re-render, so a rapid double-click would otherwise relay TWO level-up intents
  // and double-level the character. The ref blocks the second call within the same tick.
  const submittingRef = React.useRef(false);
  const cap = (s) => (s || "").replace(/^./, (ch) => ch.toUpperCase());

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const url = "/build-options?campaign=" + encodeURIComponent(campaignId || "") +
                    "&character=" + encodeURIComponent(hero.id || "");
        const response = await fetch(url, { cache: "no-store" });
        const payload = await response.json();
        if (cancelled) return;
        if (payload && payload.ok && payload.planner) {
          setPlanner(payload.planner);
        } else {
          setError((payload && Array.isArray(payload.errors) && payload.errors[0]) || "The build planner is unavailable.");
        }
      } catch (e) {
        if (!cancelled) setError("Could not reach the build planner.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [campaignId, hero.id]);

  // #feat-browser: load the SRD feat catalog the first time the player opens the feat pane (so the
  // common ASI path pays no fetch). Read-only GET /feat-catalog — the chosen feat name still rides
  // the existing level_up relay. On any failure we leave `feats` as [] so the text input still works.
  React.useEffect(() => {
    if (!takingFeat || feats !== null) return;
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch("/feat-catalog", { cache: "no-store" });
        const payload = await response.json();
        if (cancelled) return;
        setFeats(Array.isArray(payload && payload.feats) ? payload.feats : []);
      } catch (e) {
        if (!cancelled) setFeats([]);  // honest empty -> the free-text input remains usable
      }
    })();
    return () => { cancelled = true; };
  }, [takingFeat, feats]);

  // #882 roadmap: load the multi-level projection the first time the player expands the "path to 20"
  // panel (so the common flow pays no fetch). Read-only GET /level-roadmap — a pure projection of the
  // SRD tables, relays nothing. On any failure we leave `roadmap` as [] (the panel shows a quiet note).
  React.useEffect(() => {
    if (!roadmapOpen || roadmap !== null) return;
    let cancelled = false;
    (async () => {
      try {
        const url = "/level-roadmap?campaign=" + encodeURIComponent(campaignId || "") +
                    "&character=" + encodeURIComponent(hero.id || "") + "&through=20";
        const response = await fetch(url, { cache: "no-store" });
        const payload = await response.json();
        if (cancelled) return;
        if (payload && payload.ok && payload.roadmap && Array.isArray(payload.roadmap.roadmap)) {
          setRoadmap(payload.roadmap.roadmap);
        } else {
          setRoadmap([]);
          setRoadmapError((payload && Array.isArray(payload.errors) && payload.errors[0]) || "");
        }
      } catch (e) {
        if (!cancelled) { setRoadmap([]); setRoadmapError("Could not reach the roadmap planner."); }
      }
    })();
    return () => { cancelled = true; };
  }, [roadmapOpen, roadmap, campaignId, hero.id]);

  // a11y (WCAG 2.1.2 — no keyboard trap): Escape dismisses the dialog, mirroring toast.jsx.
  React.useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const options = (planner && Array.isArray(planner.options)) ? planner.options : [];
  // The option for the chosen class (default: continue the current class), else the first legal path.
  const option = options.find((o) => (o.class_name || "").toLowerCase() === chosenClass) || options[0] || null;
  const featuresGained = (option && Array.isArray(option.features_gained)) ? option.features_gained : [];
  // #624: the engine now exposes the legal SRD subclass options (with a feature preview) for the
  // chosen class at its subclass level. Present them as a pickable list instead of a blind text box —
  // selecting one fills `subclassName`. The free-text input REMAINS for any world-canon tradition the
  // engine's SRD table doesn't enumerate (additive: the DM still finalizes a homebrew name).
  const subclassBlock = (option && option.subclass) || null;
  // Subclass is DUE if: the engine flags it OVERDUE/required on this build option
  // (subclass.required — RRI-25e55fa optimizer #1, the L11-no-archetype case where a Fighter past
  // the choice level with no archetype is leveling into a level that grants no fresh "subclass"
  // feature), OR the engine grants a "<X> Subclass" feature at this level, OR the read-model already
  // flagged one pending (created above the choose-level). Either way the player names it. Reading the
  // engine's required flag (not only the feature-name heuristic / pendingSubclass) is what makes the
  // overdue archetype enforced at the level it's due.
  const subclassDue = !!(subclassBlock && subclassBlock.required) ||
    !!hero.pendingSubclass ||
    featuresGained.some((f) => /subclass/i.test((f && f.name) || ""));
  const subclassOptions = (subclassBlock && Array.isArray(subclassBlock.options)) ? subclassBlock.options : [];
  const subclassGroupLabel = (subclassBlock && subclassBlock.group_label) || "subclass";
  const asiRequired = !!(option && option.choices && option.choices.asi_required);
  const featAllowed = !!(option && option.choices && option.choices.feat_allowed);
  const toLevel = (option && option.to && option.to.level) || (Number(hero.level) + 1);

  // #607: the structured ability-score stepper. ABILITIES is the canonical 6 in 5e order; each abbrev
  // maps to the engine's full ability name (the level_up `asi` contract keys on full names). The
  // current score is read from the engine read-model (hero.stats.<abbrev>) so the 20-cap is honest.
  const ABILITIES = ["str", "dex", "con", "int", "wis", "cha"];
  const ASI_FULL = {
    str: "strength", dex: "dexterity", con: "constitution",
    int: "intelligence", wis: "wisdom", cha: "charisma",
  };
  const heroStats = (hero && hero.stats) || {};
  const asiTotal = ABILITIES.reduce((n, ab) => n + (Number(asiBumps[ab]) || 0), 0);
  const asiAtCap = (ab) => (Number(heroStats[ab]) || 0) + (Number(asiBumps[ab]) || 0) >= 20;
  // A +1 is legal while: fewer than 2 points spent total; this ability isn't already at +2 (so a
  // single ability can take at most +2); no THIRD ability would be touched; and the post-bump score
  // stays ≤ 20 (the engine cap). Mirrors `_validated_asi_choice` so the viewer never composes an
  // intent the engine would reject.
  const canBumpAsi = (ab) => {
    if (asiTotal >= 2) return false;
    if ((Number(asiBumps[ab]) || 0) >= 2) return false;
    const distinct = ABILITIES.filter((a) => (Number(asiBumps[a]) || 0) > 0);
    if (distinct.length >= 2 && !(Number(asiBumps[ab]) || 0)) return false;
    return !asiAtCap(ab);
  };
  const bumpAsi = (ab, delta) => setAsiBumps((prev) => {
    const cur = Number(prev[ab]) || 0;
    const nextVal = Math.max(0, cur + delta);
    const next = Object.assign({}, prev);
    if (nextVal <= 0) delete next[ab]; else next[ab] = nextVal;
    return next;
  });
  // The structured choice is complete when: in feat mode, a feat is named; otherwise the full +2 ASI
  // is allocated. Drives both the disabled-Confirm reason and the composed move text.
  const featChosen = takingFeat && !!featName.trim();
  const asiComplete = asiTotal === 2;
  const asiChoiceReady = takingFeat ? featChosen : asiComplete;

  const confirm = async () => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    const cls = cap((option && option.class_name) || hero.class || "my class");
    const parts = ["I advance " + (hero.name || "my character") + " to level " + toLevel + " as a " + cls];
    if (subclassDue && subclassName.trim()) parts.push("choosing the " + subclassName.trim() + " subclass");
    if (asiRequired) {
      // #607: compose the STRUCTURED choice into an unambiguous intent the DM relays to level_up.
      // Feat path and ASI path are mutually exclusive (the engine rejects both).
      if (takingFeat && featName.trim()) {
        parts.push("and instead of an ability score improvement, I take the " + featName.trim() + " feat");
      } else {
        const picks = ABILITIES
          .filter((ab) => (Number(asiBumps[ab]) || 0) > 0)
          .map((ab) => "+" + asiBumps[ab] + " " + ab.toUpperCase());
        parts.push("and for my ability score improvement: " + (picks.join(" and ") || "ask me which to take"));
      }
    }
    const text = parts.join(", ") + ".";
    try {
      const response = await fetch("/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "do", text, campaign: campaignId }),
      });
      if (!response.ok) throw new Error("move " + response.status);
      toast({
        kind: "rest",
        eyebrow: "Advancement",
        title: "Level-up relayed to the DM",
        body: "Move relayed — the engine resolves the level-up and refreshes the sheet.",
      });
      if (onDone) onDone(); else onClose();
    } catch (e) {
      toast({
        kind: "danger",
        eyebrow: "Advancement",
        title: "Level-up not sent",
        body: (e && e.message) || "The viewer could not reach /move.",
      });
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  // #607: is there an XP-legal level to advance into? The planner only lists LEGAL options (it
  // pushes no-XP paths to blocked_options), so an EMPTY options list when the modal was opened on a
  // pending subclass means "no XP earned yet for the next level" — the affordance is open to NAME
  // the missed subclass, but a level-up confirm would be illegal. The free-standing subclass naming
  // still rides the next earned level-up; until then, block + explain.
  const noLegalLevel = !loading && !error && options.length === 0;
  const subclassNeedsName = subclassDue && !subclassName.trim();
  // #607: at an ASI level the structured choice must be COMPLETE before confirming — either a full
  // +2 ability allocation or a named feat. An incomplete choice blocks Confirm with a reason (never a
  // silently-dead button) so the viewer never composes a half-formed intent the engine would reject.
  const asiNeedsChoice = asiRequired && !asiChoiceReady;
  // Confirm is blocked while submitting, when a subclass is required but unnamed, when an ASI/feat
  // choice is incomplete, or when there is no XP-legal level to advance into — never PERMANENTLY
  // (that is the old display-only stub). The reason is surfaced as a hover tooltip + inline text so
  // the disabled state is never a mystery (the optimizer persona's complaint: a dead "Confirm
  // advancement" with no explanation of WHY).
  const confirmBlockReason = noLegalLevel
    ? "No XP earned yet — there's no level to advance into. Earn more XP first."
    : subclassNeedsName
      ? "Name (or pick) your " + subclassGroupLabel + " to confirm — the DM finalizes it"
      : asiNeedsChoice
        ? (takingFeat
            ? "Name the feat you're taking to confirm"
            : "Allocate your ability score improvement (+2 to one, or +1 to two) to confirm")
        : "";
  const confirmDisabled = submitting || !!confirmBlockReason;

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 200,
      background: "rgba(15, 8, 2, 0.7)",
      display: "grid", placeItems: "center",
      backdropFilter: "blur(2px)",
    }} onClick={onClose} role="dialog" aria-modal="true" aria-label="Level up" data-worldos-testid="level-up-modal">
      <div onClick={(e) => e.stopPropagation()} style={{ width: 640, maxWidth: "92vw", maxHeight: "88vh", overflow: "auto" }}>
        <Panel framed>
          <div className="eyebrow" style={{ color: "var(--crimson)" }}>Advancement</div>
          <h2 className="h1" style={{ fontSize: 26 }}>Level Up — {hero.name}</h2>
          <Divider />

          {loading ? (
            <p className="body muted">Consulting the build planner…</p>
          ) : error ? (
            <p className="body" style={{ color: "var(--crimson)" }} data-worldos-testid="levelup-error">{error}</p>
          ) : (
            <>
              <p className="body" style={{ marginTop: 0 }}>
                {hero.name} has earned the next rung. The engine planner shows what this level grants —
                confirm to relay the advancement to the Dungeon Master, who records it.
              </p>

              {options.length > 1 && (
                <div style={{ marginTop: 12 }}>
                  <SectionTitle>Advance as</SectionTitle>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {options.map((o) => {
                      const cn = (o.class_name || "").toLowerCase();
                      return (
                        <button key={cn} onClick={() => setChosenClass(cn)}
                          data-worldos-testid={"levelup-class-" + cn}
                          style={{
                            padding: "6px 12px", cursor: "pointer",
                            background: chosenClass === cn ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "transparent",
                            boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
                            color: chosenClass === cn ? "var(--w-300)" : "var(--ink-800)",
                            fontFamily: "var(--f-display)", fontSize: 12,
                          }}>
                          {cap(o.class_name)}{o.multiclass ? " · multiclass" : ""}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {option && (
                <div style={{ marginTop: 16 }}>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                    <Pill tone="emerald" dot>Level {(option.from && option.from.level) || hero.level} → {toLevel}</Pill>
                    {typeof option.hp_gain === "number" && <Pill tone="crimson" dot>+{option.hp_gain} HP</Pill>}
                    <Pill>{cap(option.class_name)}</Pill>
                  </div>
                  {featuresGained.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                      <SectionTitle>Features gained</SectionTitle>
                      <ul style={{ margin: 0, paddingLeft: 18 }}>
                        {featuresGained.map((f, i) => (
                          <li key={i} className="body-sm" style={{ color: "var(--ink-800)" }}>{(f && f.name) || String(f)}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              {subclassDue && (
                <div style={{ marginTop: 16 }} data-worldos-testid="levelup-subclass-section">
                  <SectionTitle>Choose your {subclassGroupLabel}</SectionTitle>
                  {subclassOptions.length > 0 ? (
                    <>
                      <p className="body-sm muted" style={{ marginTop: 0 }}>
                        This level grants a {subclassGroupLabel}. Pick one of the options below (each shows what it grants),
                        or name a different one your world offers — the DM finalizes it.
                      </p>
                      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 10 }}
                        data-worldos-testid="levelup-subclass-options">
                        {subclassOptions.map((opt) => {
                          const selected = subclassName.trim().toLowerCase() === (opt.name || "").toLowerCase();
                          // #607: each option lists the level-3 features it grants so the picker is a
                          // real BROWSABLE comparison ("what each tradition gives me"), not a name list.
                          const optFeatures = Array.isArray(opt.features) ? opt.features : [];
                          return (
                            <button key={opt.name} type="button"
                              aria-pressed={selected}
                              onClick={() => setSubclassName(opt.name)}
                              data-worldos-testid={"levelup-subclass-option-" + (opt.name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-")}
                              style={{
                                textAlign: "left", padding: "8px 12px", cursor: "pointer",
                                background: selected ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "transparent",
                                boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.35)",
                                color: selected ? "var(--w-300)" : "var(--ink-800)",
                                fontFamily: "var(--f-body)", fontSize: 13,
                              }}>
                              <div style={{ fontFamily: "var(--f-display)", fontSize: 13 }}>{opt.name}</div>
                              {opt.desc && <div className="body-sm" style={{ opacity: 0.85, marginTop: 2 }}>{opt.desc}</div>}
                              {optFeatures.length > 0 && (
                                <ul style={{ margin: "6px 0 0", paddingLeft: 16 }}
                                  data-worldos-testid={"levelup-subclass-features-" + (opt.name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-")}>
                                  {optFeatures.map((f, i) => (
                                    <li key={i} className="body-sm" style={{ opacity: 0.9, marginTop: 1 }}>
                                      <strong>{(f && f.name) || String(f)}</strong>
                                      {f && f.desc ? <span style={{ opacity: 0.8 }}> — {f.desc}</span> : null}
                                    </li>
                                  ))}
                                </ul>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    </>
                  ) : (
                    <p className="body-sm muted" style={{ marginTop: 0 }}>
                      This level grants a subclass. Name the one your character takes — the DM confirms it against the world's options.
                    </p>
                  )}
                  <input type="text" value={subclassName} onChange={(e) => setSubclassName(e.target.value)}
                    placeholder={subclassOptions.length > 0 ? "…or type another tradition your world offers" : "e.g. School of Evocation"}
                    data-worldos-testid="levelup-subclass-input"
                    style={{
                      width: "100%", padding: "8px 10px", boxSizing: "border-box",
                      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.4)",
                      background: "rgba(255,250,235,0.5)", fontFamily: "var(--f-body)", fontSize: 14,
                    }} />
                </div>
              )}

              {asiRequired && (
                <div style={{ marginTop: 16 }} data-worldos-testid="levelup-asi-section">
                  <SectionTitle>Ability Score Improvement{featAllowed ? " or feat" : ""}</SectionTitle>
                  {/* #607: a structured choice, not a free-text note. When the campaign allows feats,
                      a toggle swaps between the ASI stepper and a named-feat input (mutually exclusive —
                      the engine rejects taking both). */}
                  {featAllowed && (
                    <div style={{ display: "flex", gap: 8, marginBottom: 12 }} role="tablist" aria-label="Improvement type">
                      <button type="button" onClick={() => setTakingFeat(false)}
                        aria-pressed={!takingFeat}
                        data-worldos-testid="levelup-asi-toggle"
                        style={{
                          padding: "6px 12px", cursor: "pointer",
                          background: !takingFeat ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "transparent",
                          boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.35)",
                          color: !takingFeat ? "var(--w-300)" : "var(--ink-800)",
                          fontFamily: "var(--f-display)", fontSize: 12,
                        }}>
                        Raise abilities
                      </button>
                      <button type="button" onClick={() => setTakingFeat(true)}
                        aria-pressed={takingFeat}
                        data-worldos-testid="levelup-feat-toggle"
                        style={{
                          padding: "6px 12px", cursor: "pointer",
                          background: takingFeat ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "transparent",
                          boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.35)",
                          color: takingFeat ? "var(--w-300)" : "var(--ink-800)",
                          fontFamily: "var(--f-display)", fontSize: 12,
                        }}>
                        Take a feat instead
                      </button>
                    </div>
                  )}

                  {takingFeat ? (
                    <div data-worldos-testid="levelup-feat-pane">
                      <p className="body-sm muted" style={{ marginTop: 0 }}>
                        Pick a feat below — each shows its effect and any prerequisite — or type a different one
                        your world offers. The DM finalizes it.
                      </p>
                      {/* #feat-browser: the browsable SRD feat list (GET /feat-catalog). Selecting a
                          feat fills `featName` (which rides the existing level_up relay), mirroring the
                          subclass-options picker above. The free-text input REMAINS below for any
                          world-canon feat the SRD list doesn't enumerate (additive). */}
                      {Array.isArray(feats) && feats.length > 0 && (
                        <>
                          <input type="text" value={featSearch} onChange={(e) => setFeatSearch(e.target.value)}
                            placeholder="Filter feats…"
                            data-worldos-testid="levelup-feat-search"
                            style={{
                              width: "100%", padding: "6px 10px", boxSizing: "border-box", marginBottom: 8,
                              boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.4)",
                              background: "rgba(255,250,235,0.5)", fontFamily: "var(--f-body)", fontSize: 13,
                            }} />
                          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 10, maxHeight: 280, overflowY: "auto" }}
                            data-worldos-testid="levelup-feat-options">
                            {feats
                              .filter((f) => {
                                const q = featSearch.trim().toLowerCase();
                                if (!q) return true;
                                return ((f.name || "").toLowerCase().indexOf(q) !== -1)
                                  || ((f.prerequisite || "").toLowerCase().indexOf(q) !== -1)
                                  || ((f.desc || "").toLowerCase().indexOf(q) !== -1);
                              })
                              .map((f) => {
                                const selected = featName.trim().toLowerCase() === (f.name || "").toLowerCase();
                                return (
                                  <button key={f.name} type="button"
                                    aria-pressed={selected}
                                    onClick={() => setFeatName(f.name)}
                                    data-worldos-testid={"levelup-feat-option-" + (f.name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-")}
                                    style={{
                                      textAlign: "left", padding: "8px 12px", cursor: "pointer",
                                      background: selected ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "transparent",
                                      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.35)",
                                      color: selected ? "var(--w-300)" : "var(--ink-800)",
                                      fontFamily: "var(--f-body)", fontSize: 13,
                                    }}>
                                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                                      <span style={{ fontFamily: "var(--f-display)", fontSize: 13 }}>{f.name}</span>
                                      {f.prerequisite ? (
                                        <span className="body-sm" style={{ opacity: 0.8, whiteSpace: "nowrap" }}>
                                          Prereq: {f.prerequisite}
                                        </span>
                                      ) : null}
                                    </div>
                                    {f.desc && (
                                      <div className="body-sm" style={{ opacity: 0.85, marginTop: 3, whiteSpace: "pre-line" }}>{f.desc}</div>
                                    )}
                                  </button>
                                );
                              })}
                          </div>
                        </>
                      )}
                      <input type="text" value={featName} onChange={(e) => setFeatName(e.target.value)}
                        placeholder={Array.isArray(feats) && feats.length > 0 ? "…or type another feat your world offers" : "e.g. Great Weapon Master"}
                        data-worldos-testid="levelup-feat-input"
                        style={{
                          width: "100%", padding: "8px 10px", boxSizing: "border-box",
                          boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.4)",
                          background: "rgba(255,250,235,0.5)", fontFamily: "var(--f-body)", fontSize: 14,
                        }} />
                    </div>
                  ) : (
                    <div data-worldos-testid="levelup-asi-stepper">
                      <p className="body-sm muted" style={{ marginTop: 0 }}>
                        Spend +2: either +2 into one ability, or +1 into two. Scores cap at 20.
                      </p>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
                        {ABILITIES.map((ab) => {
                          const bump = Number(asiBumps[ab]) || 0;
                          const base = Number(heroStats[ab]) || 0;
                          const shown = base + bump;
                          return (
                            <div key={ab} style={{
                              display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6,
                              padding: "6px 8px",
                              boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
                              background: bump > 0 ? "rgba(176,141,87,0.14)" : "transparent",
                            }}>
                              <div>
                                <div className="eyebrow" style={{ fontSize: 10 }}>{ab.toUpperCase()}</div>
                                <div style={{ fontFamily: "var(--f-display)", fontSize: 15, color: "var(--ink-900)" }}>
                                  {shown}{bump > 0 ? <span style={{ color: "var(--emerald)", fontSize: 11 }}> (+{bump})</span> : null}
                                </div>
                              </div>
                              <div style={{ display: "flex", gap: 4 }}>
                                <button type="button" onClick={() => bumpAsi(ab, -1)} disabled={bump <= 0}
                                  aria-label={"Lower " + ASI_FULL[ab] + " improvement"}
                                  data-worldos-testid={"levelup-asi-dec-" + ab}
                                  style={{
                                    width: 24, height: 24, cursor: bump <= 0 ? "default" : "pointer",
                                    opacity: bump <= 0 ? 0.4 : 1,
                                    boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.4)",
                                    background: "transparent", fontFamily: "var(--f-display)",
                                  }}>−</button>
                                <button type="button" onClick={() => bumpAsi(ab, 1)} disabled={!canBumpAsi(ab)}
                                  aria-label={"Raise " + ASI_FULL[ab] + " improvement"}
                                  data-worldos-testid={"levelup-asi-inc-" + ab}
                                  title={asiAtCap(ab) ? "Already at the 20 cap" : (asiTotal >= 2 ? "Both points are already spent" : "")}
                                  style={{
                                    width: 24, height: 24, cursor: !canBumpAsi(ab) ? "default" : "pointer",
                                    opacity: !canBumpAsi(ab) ? 0.4 : 1,
                                    boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.4)",
                                    background: "transparent", fontFamily: "var(--f-display)",
                                  }}>+</button>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                      <div className="hand muted" style={{ fontSize: 11, marginTop: 8 }}
                        data-worldos-testid="levelup-asi-remaining">
                        {2 - asiTotal > 0 ? (2 - asiTotal) + " point" + (2 - asiTotal === 1 ? "" : "s") + " left to spend" : "All set — +2 allocated"}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* #882 roadmap: "See your path to 20" — a READ-ONLY projection of the upcoming levels
                  (features, ASI/feat markers, prof bonus, slot/resource notes) so a build-optimizing
                  player can theorycraft beyond the single next level. It relays NOTHING — purely a
                  planning view. Lazily fetched on expand; renders nothing when the roadmap is empty
                  (already at 20 / a non-class entity) beyond a quiet note. */}
              {Number(hero.level) < 20 && (
                <div style={{ marginTop: 20, paddingTop: 16, borderTop: "1px solid rgba(140,100,60,0.25)" }}
                  data-worldos-testid="levelup-roadmap-section">
                  <button type="button" onClick={() => setRoadmapOpen((v) => !v)}
                    aria-expanded={roadmapOpen}
                    data-worldos-testid="levelup-roadmap-toggle"
                    style={{
                      display: "flex", alignItems: "center", gap: 8, width: "100%",
                      padding: "8px 12px", cursor: "pointer", textAlign: "left",
                      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.35)",
                      background: roadmapOpen ? "rgba(176,141,87,0.12)" : "transparent",
                      color: "var(--ink-800)", fontFamily: "var(--f-display)", fontSize: 13,
                    }}>
                    <span style={{ transform: roadmapOpen ? "rotate(90deg)" : "none", transition: "transform 0.15s" }}>▸</span>
                    See your path to 20
                  </button>
                  {roadmapOpen && (
                    <div data-worldos-testid="levelup-roadmap-pane" style={{ marginTop: 10 }}>
                      {roadmap === null ? (
                        <p className="body-sm muted" style={{ margin: 0 }}>Charting your path…</p>
                      ) : roadmap.length === 0 ? (
                        <p className="body-sm muted" style={{ margin: 0 }}
                          data-worldos-testid="levelup-roadmap-empty">
                          {roadmapError || "No further progression to project from here."}
                        </p>
                      ) : (
                        <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 300, overflowY: "auto" }}
                          data-worldos-testid="levelup-roadmap-list">
                          {roadmap.map((row) => {
                            const feats = Array.isArray(row.features) ? row.features : [];
                            const subFeats = Array.isArray(row.subclass_features) ? row.subclass_features : [];
                            return (
                              <div key={row.level}
                                data-worldos-testid={"levelup-roadmap-level-" + row.level}
                                style={{
                                  padding: "8px 12px",
                                  boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
                                  background: "rgba(255,250,235,0.4)",
                                }}>
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                                  <span style={{ fontFamily: "var(--f-display)", fontSize: 13, color: "var(--ink-900)" }}>
                                    Level {row.level}
                                  </span>
                                  <span className="body-sm" style={{ opacity: 0.8, whiteSpace: "nowrap" }}>
                                    Prof +{row.prof_bonus}
                                    {row.is_asi_or_feat ? (
                                      <span style={{ color: "var(--emerald)", marginLeft: 6 }}
                                        data-worldos-testid={"levelup-roadmap-asi-" + row.level}>• ASI / feat</span>
                                    ) : null}
                                  </span>
                                </div>
                                {feats.length > 0 && (
                                  <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                                    {feats.map((f) => (
                                      <li key={f.name} className="body-sm" style={{ opacity: 0.9 }}>
                                        <span style={{ fontFamily: "var(--f-body)", color: "var(--ink-800)" }}>{f.name}</span>
                                      </li>
                                    ))}
                                  </ul>
                                )}
                                {subFeats.length > 0 && (
                                  <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                                    {subFeats.map((f) => (
                                      <li key={f.name} className="body-sm" style={{ opacity: 0.85, fontStyle: "italic" }}>
                                        {f.name}
                                      </li>
                                    ))}
                                  </ul>
                                )}
                                {(row.resources_note || row.spell_slots_note) && (
                                  <div className="body-sm muted" style={{ marginTop: 4 }}>
                                    {[row.spell_slots_note, row.resources_note].filter(Boolean).join(" · ")}
                                  </div>
                                )}
                                {feats.length === 0 && subFeats.length === 0 && !row.resources_note && !row.spell_slots_note && (
                                  <div className="body-sm muted" style={{ marginTop: 4, opacity: 0.7 }}>
                                    No new features — ability/proficiency growth only.
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              <div style={{ display: "flex", gap: 10, marginTop: 24, justifyContent: "flex-end" }}>
                <BrassButton tone="ghost" onClick={onClose} testId="modal-close" ariaLabel="Close level up modal">Not yet</BrassButton>
                <BrassButton onClick={confirm} disabled={confirmDisabled} testId="levelup-confirm"
                  ariaLabel="Confirm level up and relay to the Dungeon Master"
                  title={confirmBlockReason || "Relays the advancement to the DM via /move — the engine resolves the level-up"}>
                  {submitting ? "Relaying…" : "Confirm advancement"}
                </BrassButton>
              </div>
              {/* #607: when Confirm is disabled, say WHY inline (a hover tooltip alone is invisible
                  to touch + screen-reader users) — never a silently dead button. */}
              {confirmBlockReason && (
                <div className="hand muted" style={{ fontSize: 11, marginTop: 8, textAlign: "right" }}
                  data-worldos-testid="levelup-confirm-reason">
                  {confirmBlockReason}.
                </div>
              )}
            </>
          )}
        </Panel>
      </div>
    </div>
  );
}

function RestPrepareModal({ hero, party, campaignId, canAct, dmBusy, onClose, onDone, toast, setState, initialStep }) {
  // initialStep lets a caller open this modal straight at the "prep" step (the camp long-rest
  // affordance — the rest already happened, the player just wants to set their prepared spells).
  // Defaults to "rest" so every existing caller (the sheet's "Rest & Prepare" button) is unchanged.
  const [step, setStep] = React.useState(initialStep === "prep" ? "prep" : "rest");
  const [restType, setRestType] = React.useState("long");
  const [prepared, setPrepared] = React.useState({});
  const [submitting, setSubmitting] = React.useState(false);
  // RRI-25e55fa optimizer #4 — how many Hit Dice to SPEND on a short rest (for HP). The sheet
  // shows e.g. "11/11d10"; this is the control the optimizer found missing. The engine's
  // short_rest(hit_dice_to_spend=N) applies it — the viewer only composes the intent (move-sink).
  const hitDiceRemaining = Math.max(0, Number(hero?.stats?.hitDiceRemaining) || 0);
  const hitDicePool = String(hero?.stats?.hitDice || "");
  const [hitDiceToSpend, setHitDiceToSpend] = React.useState(0);
  // #robustness: synchronous in-flight lock (mirrors LevelUpModal / screen-table.jsx). The
  // `submitting` STATE only updates on re-render, so a rapid double-click would otherwise relay
  // TWO rest/prepare intents. The ref blocks the second call within the same tick.
  const submittingRef = React.useRef(false);
  // Watch order is drawn from the LIVE party (first names), never the hardcoded demo trio.
  const watchOrder = (Array.isArray(party) ? party : []).map((p) => (p.name || "").split(" ")[0]).filter(Boolean);
  // name→slug for stable testids (mirror HeroEquipDoll); window.slug is the shared chrome helper.
  const slug = window.slug || ((n) => (n || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""));

  // a11y (WCAG 2.1.2 — no keyboard trap): Escape dismisses the dialog, mirroring toast.jsx.
  React.useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  // #754 — the BROWSABLE preparable pool: the full class spell list this caster can choose
  // to prepare FROM (the read-model's hero.preparableSpells), grouped by spell level. This is
  // the optimizer's MAJOR complaint — the prep step used to iterate only hero.spells (the few
  // currently prepared/known), so a Paladin could never SELECT a new spell to prepare. We group
  // the full pool by level so the player picks from everything they can slot.
  const prepPool = (() => {
    const byLevel = {};
    for (const sp of (Array.isArray(hero.preparableSpells) ? hero.preparableSpells : [])) {
      const lv = Number(sp.level) || 0;
      (byLevel[lv] = byLevel[lv] || []).push(sp);
    }
    return Object.keys(byLevel)
      .map((k) => Number(k))
      .sort((a, b) => a - b)
      .map((lv) => ({ level: lv, list: byLevel[lv] }));
  })();

  // Pre-seed the picker with the caster's CURRENTLY prepared spells (from hero.spells'
  // "Prepared" group) so opening the modal shows their real preparation, and "Seal" edits it.
  React.useEffect(() => {
    const seed = {};
    for (const grp of (Array.isArray(hero.spells) ? hero.spells : [])) {
      if (String(grp.level).toLowerCase() !== "prepared") continue;
      for (const sp of (grp.list || [])) {
        // place each prepared spell into its level bucket using the pool's known level
        const inPool = (Array.isArray(hero.preparableSpells) ? hero.preparableSpells : [])
          .find((p) => p.name === sp.name);
        const lv = inPool ? (Number(inPool.level) || 0) : 0;
        (seed[lv] = seed[lv] || []).push(sp.name);
      }
    }
    if (Object.keys(seed).length) setPrepared(seed);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hero.id]);

  // The prepared CAP (engine-mirrored read-model: prepared-caster-level + casting-ability mod;
  // half for a Paladin/Ranger). A prepared caster may have only `cap` LEVELED spells prepared at
  // once — the optimizer's MAJOR "only 3 of 7 prepared" with no visible budget. null/absent for a
  // known-caster (Bard/Sorcerer/Warlock) or non-caster -> no cap is enforced or shown.
  const cap = (typeof hero.preparedCap === "number" && hero.preparedCap > 0) ? hero.preparedCap : null;
  // CANTRIPS (spell level 0) are ALWAYS-known and never count against the prepared cap — only
  // leveled spells (level >= 1) consume the budget. Count the currently-selected leveled spells.
  const leveledSelected = Object.keys(prepared)
    .filter((lv) => Number(lv) > 0)
    .reduce((sum, lv) => sum + ((prepared[lv] || []).length), 0);
  const atCap = cap !== null && leveledSelected >= cap;

  const toggleSpell = (lv, name) => {
    const cur = prepared[lv] || [];
    const selecting = !cur.includes(name);
    // Block SELECTING a new LEVELED spell once the cap is full (deselecting is always allowed; a
    // cantrip at level 0 is never blocked). The engine remains the sole writer — this is a UI
    // guard so the relayed preparation can never exceed the caster's legal prepared count.
    if (selecting && Number(lv) > 0 && atCap) return;
    if (cur.includes(name)) {
      setPrepared({ ...prepared, [lv]: cur.filter((n) => n !== name) });
    } else {
      setPrepared({ ...prepared, [lv]: [...cur, name] });
    }
  };

  // #610/#617 — relay a composed `do` move-intent to the DM (sole writer) and let the engine
  // resolve it through long_rest/short_rest + prepare_spells (refreshing HP + spell slots,
  // advancing the clock, recording prepared spells). The viewer NEVER writes snapshot — this is
  // the same write path camp-sidebar.jsx and LevelUpModal use. Synchronously locked + busy-gated.
  const relayMove = async (text, onSuccess, eyebrow, title) => {
    if (submittingRef.current) return;
    if (dmBusy) {
      toast({ kind: "danger", eyebrow: "Camp", title: "The Dungeon Master is still narrating", body: "Resolve the current beat first — then make camp and rest." });
      return;
    }
    if (!canAct) {
      toast({ kind: "danger", eyebrow: "Camp", title: "The chronicle is read-only", body: "Start a session to make camp, rest, and prepare spells." });
      return;
    }
    submittingRef.current = true;
    setSubmitting(true);
    try {
      const response = await fetch("/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "do", text, campaign: campaignId }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) throw new Error(payload.reason || "move " + response.status);
      toast({ kind: "rest", eyebrow, title, body: "Move relayed to the DM — the engine resolves it, refreshes the party, and advances the clock." });
      if (onSuccess) onSuccess();
    } catch (e) {
      toast({ kind: "danger", eyebrow, title: title + " not sent", body: (e && e.message) || "The viewer could not reach /move." });
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  // Make camp: relay the chosen rest, then advance to the spell-preparation step.
  const completeRest = () => {
    const watch = watchOrder.length > 0 ? ` ${watchOrder.join(", then ")} keep watch.` : "";
    // RRI-25e55fa optimizer #4: on a short rest, name the exact Hit-Dice count to spend so the
    // engine's short_rest(hit_dice_to_spend=N) applies precisely that — defaulting to "as needed"
    // when the player leaves the stepper at 0 (today's behavior, no forced spend).
    const hdClause = hitDiceToSpend > 0
      ? ` ${hero.name} spends ${hitDiceToSpend} Hit ${hitDiceToSpend === 1 ? "Die" : "Dice"} to recover HP.`
      : ` Spend Hit Dice as needed to recover HP.`;
    const text = restType === "long"
      ? `${hero.name} and the party make camp and take a long rest — restore HP, recover all spell slots, and refresh abilities, then advance the clock to morning.${watch}`
      : `${hero.name} and the party take a short rest to recover HP and refresh short-rest abilities.${hdClause}${watch}`;
    relayMove(text, () => { submittingRef.current = false; setSubmitting(false); setStep("prep"); },
      restType === "long" ? "Long rest" : "Short rest", "Rest");
  };

  const completePrep = () => {
    // Compose the prepared list (level-tagged) into the intent so the engine records exactly
    // which spells are prepared for the day; empty = "leave my preparation unchanged".
    const named = Object.keys(prepared)
      .sort((a, b) => Number(a) - Number(b))
      .flatMap((lv) => (prepared[lv] || []).map((n) => n))
      .filter(Boolean);
    // Name prepare_spells + "replace" explicitly so the DM resolves it with one engine call: the
    // chosen set IS the day's complete preparation (the engine = sole writer of spells_prepared).
    const text = named.length > 0
      ? `${hero.name} prepares today's spells — set their prepared spells (prepare_spells, replace) to exactly: ${named.join(", ")}.`
      : `${hero.name} keeps their currently prepared spells.`;
    relayMove(text, () => { if (onDone) onDone(); else onClose(); },
      "Spellbook", "Preparation");
  };

  // Why the CTAs are disabled, surfaced as a hover tooltip (and the inline note below) so a dead
  // button is never a mystery — mirrors camp-sidebar.jsx and the level-up picker.
  const blockReason = !canAct
    ? "The chronicle is read-only — start a session to make camp and rest"
    : dmBusy
      ? "The Dungeon Master is still narrating — resolve the current beat first"
      : "";
  const ctaDisabled = submitting || !!blockReason;

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
                  testId="rest-card-short"
                  title="Short Rest"
                  hand="One hour. A second wind."
                  body="Restores hit points spent from class features. Spell slots and abilities remain spent. Watch is not required."
                  cost="1 hour · no rations"
                />
                <RestCard
                  selected={restType === "long"}
                  onClick={() => setRestType("long")}
                  testId="rest-card-long"
                  title="Long Rest"
                  hand="Eight hours. The whole road forgiven."
                  body="Full HP. All spell slots restored. Abilities refresh. Watch order required; one in four sleeps light."
                  cost="8 hours · 1 ration each"
                />
              </div>

              {/* RRI-25e55fa optimizer #4: on a SHORT rest, a Hit-Dice-SPEND control — the sheet
                  shows e.g. "11/11d10" but had no way to spend them for HP. Shown only on the short
                  rest AND only when the hero actually has dice remaining (never a dead stepper). The
                  chosen count rides the relayed move; the engine's short_rest(hit_dice_to_spend=N)
                  applies it (the viewer stays a move-sink — it never edits HP itself). */}
              {restType === "short" && hitDiceRemaining > 0 && (
                <div data-worldos-testid="short-rest-hit-dice" style={{ marginTop: 18 }}>
                  <SectionTitle right={
                    <span className="muted body-sm">{hitDiceRemaining}/{hitDicePool || hitDiceRemaining} available</span>
                  }>Spend Hit Dice</SectionTitle>
                  <div className="muted body-sm" style={{ marginBottom: 8 }}>
                    {hero.name} has {hitDiceRemaining} of {hitDicePool || (hitDiceRemaining + " Hit Dice")} to spend. Each Hit Die rolls its die + your Constitution modifier back as hit points. Leave at 0 to let the DM spend them as needed.
                  </div>
                  <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                    <BrassButton size="sm" tone="ghost"
                      testId="short-rest-hd-dec"
                      ariaLabel="Spend one fewer Hit Die"
                      onClick={() => setHitDiceToSpend((n) => Math.max(0, n - 1))}>−</BrassButton>
                    <div style={{
                      minWidth: 56, textAlign: "center",
                      background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
                      boxShadow: "inset 0 0 0 1px var(--b-500)",
                      padding: "6px 0",
                      fontFamily: "var(--f-display)", fontSize: 18,
                      color: hitDiceToSpend > 0 ? "var(--emerald)" : "var(--ink-900)",
                    }} aria-live="polite">{hitDiceToSpend}</div>
                    <BrassButton size="sm" tone="ghost"
                      testId="short-rest-hd-inc"
                      ariaLabel="Spend one more Hit Die"
                      onClick={() => setHitDiceToSpend((n) => Math.min(hitDiceRemaining, n + 1))}>+</BrassButton>
                    <span className="hand muted" style={{ fontSize: 12, marginLeft: 4 }}>
                      {hitDiceToSpend === 0 ? "DM spends as needed" : `spend ${hitDiceToSpend} of ${hitDiceRemaining}`}
                    </span>
                  </div>
                </div>
              )}

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
                <BrassButton onClick={completeRest} disabled={ctaDisabled} testId="rest-make-camp"
                  ariaLabel="Make camp and relay the rest to the Dungeon Master"
                  title={blockReason || "Relays the rest to the DM via /move — the engine refreshes HP + spell slots and advances the clock"}>
                  {submitting ? "Relaying…" : "Make camp"}
                </BrassButton>
              </div>
              {blockReason && (
                <div className="hand muted" style={{ fontSize: 11, marginTop: 8, textAlign: "right" }}>
                  {blockReason}.
                </div>
              )}
            </>
          ) : (
            <>
              <div className="eyebrow" style={{ color: "var(--crimson)" }}>The Spellbook</div>
              <h2 className="h1" style={{ fontSize: 26 }}>Prepare Today's Spells</h2>
              <Divider />

              <p className="body" style={{ marginTop: 0 }}>
                {hero.name} reads by the dying fire. Choose what will be at hand when the day breaks — the whole class spell list is yours to browse. Unchosen spells remain bound to the page.
              </p>

              {/* The prepared CAP budget: "N / cap selected" of LEVELED spells (cantrips excluded).
                  The optimizer's MAJOR was "3 of 7 prepared" with no visible budget — this shows
                  exactly how many leveled spells fit. Shown only when the read-model gives a cap
                  (a prepared caster); a known-caster/non-caster sees no counter (never a fake cap). */}
              {cap !== null && prepPool.length > 0 && (
                <div data-worldos-testid="prep-cap-counter" aria-live="polite" style={{
                  marginTop: 14, padding: "8px 12px",
                  display: "flex", justifyContent: "space-between", alignItems: "baseline",
                  background: atCap ? "rgba(122,40,32,0.10)" : "rgba(176,141,87,0.08)",
                  boxShadow: atCap ? "inset 0 0 0 1px var(--crimson)" : "inset 0 0 0 1px rgba(140,100,60,0.3)",
                }}>
                  <span className="eyebrow" style={{ fontSize: 10, color: atCap ? "var(--crimson)" : "var(--ink-700)" }}>
                    Leveled spells prepared
                  </span>
                  <span style={{ fontFamily: "var(--f-display)", fontSize: 16, color: atCap ? "var(--crimson)" : "var(--emerald)" }}>
                    {`${leveledSelected} / ${cap} selected`}
                  </span>
                </div>
              )}

              {/* #754 — pick from the FULL browsable class list (hero.preparableSpells), grouped
                  by spell level and capped to the caster's highest slot level by the engine. The
                  optimizer could never SELECT a new spell before; now the entire preparable pool
                  is here. The chosen set rides the relayed move; the engine's prepare_spells records
                  it (the viewer stays a move-sink). Empty pool -> an honest invitation, not a stub. */}
              {prepPool.length === 0 ? (
                <div data-worldos-testid="prep-empty-pool" className="muted body-sm" style={{ marginTop: 16 }}>
                  This hero has no preparable spells (no spell slots, or not a prepared caster).
                </div>
              ) : prepPool.map((group) => {
                const cur = prepared[group.level] || [];
                return (
                  <div key={group.level} style={{ marginTop: 18 }} data-worldos-testid={`prep-level-${group.level}`}>
                    <SectionTitle right={
                      <span className="muted body-sm">{cur.length} prepared · {group.list.length} available</span>
                    }>
                      {group.level === 0 ? "Cantrips" : `Level ${group.level}`}
                    </SectionTitle>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                      {group.list.map((sp) => {
                        const isPrepared = cur.includes(sp.name);
                        // A LEVELED (level >= 1) spell that ISN'T already selected is disabled once
                        // the cap is full — you can't prepare more than the budget. Cantrips
                        // (group.level 0) and already-selected spells (to deselect) stay clickable.
                        const capBlocked = !isPrepared && group.level > 0 && atCap;
                        return (
                          <button
                            key={sp.name}
                            data-worldos-testid={`prep-spell-${slug(sp.name)}`}
                            aria-pressed={isPrepared ? "true" : "false"}
                            disabled={capBlocked}
                            title={capBlocked ? `Prepared cap reached (${cap}) — unprepare a spell to make room` : undefined}
                            onClick={() => toggleSpell(group.level, sp.name)}
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
                              cursor: capBlocked ? "not-allowed" : "pointer",
                              opacity: capBlocked ? 0.45 : 1,
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
                  <BrassButton onClick={completePrep} disabled={ctaDisabled} testId="rest-prepare-spells"
                    ariaLabel="Seal today's prepared spells and relay to the Dungeon Master"
                    title={blockReason || "Relays your prepared spells to the DM via /move — the engine records the day's preparation"}>
                    {submitting ? "Relaying…" : "Seal the choices"}
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

function RestCard({ selected, onClick, title, hand, body, cost, testId }) {
  return (
    <button onClick={onClick} data-worldos-testid={testId} aria-pressed={selected ? "true" : "false"} style={{
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
// REAL stats — "1d8 slashing" for a weapon, "AC 14 + DEX (max +2)" for armor, a shield's
// bonus "+2" — or "" when neither the item's persisted stats nor the catalog have one (then
// we show just its name; never a fabricated stat). acDisplay carries the F09-6 dex rule.
function equippedStat(it) {
  if (!it) return "";
  if (it.damage) return [it.damage, it.damageType].filter(Boolean).join(" ");
  if (it.acDisplay) return it.acDisplay;
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

// RRI-25e55fa optimizer #1 (the "#1 min-maxer pain point") — the CLASS-FEATURE INSPECTOR.
// Every class/subclass feature (Extra Attack, Action Surge, Indomitable, …) used to be static
// text with NO click-through to its full rules. The /character-surface read-model ALREADY carries
// the SRD rules text on each feature as `detail` (server _feature_desc + data/srd/class_features.json);
// this surfaces it on demand. Mirrors the #872 item-Examine read-only PANEL pattern: a feature with
// rules text becomes a clickable row that opens a read-only dialog with the full text; a feature the
// engine couldn't resolve (no detail) keeps today's static blurb (graceful degrade — additive).
function _featureSlug(name) {
  return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

// The read-only rules panel for one class/subclass feature. Pure reader — props in, no /move,
// no fetch, no engine write. Escape / Close dismisses (no keyboard trap, WCAG 2.1.2).
function FeatureInspector({ feature, contextLabel, onClose }) {
  React.useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  if (!feature) return null;
  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 200,
      background: "rgba(15, 8, 2, 0.7)",
      display: "grid", placeItems: "center",
      backdropFilter: "blur(2px)",
    }} onClick={onClose} role="dialog" aria-modal="true" aria-label={"Feature — " + (feature.name || "")}
      data-worldos-testid="feature-inspector">
      <div onClick={(e) => e.stopPropagation()} style={{ width: 560, maxWidth: "92vw", maxHeight: "88vh", overflow: "auto" }}>
        <Panel framed>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div className="eyebrow" style={{ color: "var(--royal)" }}>{contextLabel || "Class Feature"}</div>
            <BrassButton tone="ghost" size="sm" onClick={onClose} testId="feature-inspector-close" ariaLabel="Close feature panel">Close</BrassButton>
          </div>
          <h2 className="h1" style={{ fontSize: 22, marginTop: 4 }}>{feature.name}</h2>
          <Divider />
          {/* Full SRD rules text — a plain React text node (never dangerouslySetInnerHTML). */}
          <p className="body" style={{ marginTop: 0, fontSize: 15, lineHeight: 1.5 }}>{feature.detail}</p>
        </Panel>
      </div>
    </div>
  );
}

// A list of class/subclass features. A feature WITH rules text (detail) renders as a click-through
// row (testid class-feature-<slug>) opening the FeatureInspector; a feature WITHOUT detail keeps the
// static blurb (no dead click). `contextLabel` (e.g. "Level 9 · Fighter · Champion") rides the panel.
function ClassFeatureList({ features, contextLabel }) {
  const [open, setOpen] = React.useState(null);
  const list = Array.isArray(features) ? features : [];
  if (!list.length) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {list.map((c) => {
        const hasRules = !!(c && typeof c.detail === "string" && c.detail.trim());
        const card = (
          <div style={{ padding: 10, background: "rgba(176,141,87,0.06)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
              <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.12em", color: "var(--ink-900)" }}>{c.name}</span>
              {/* A subtle affordance so the reader knows the row opens the rules. Only on rows
                  that have rules text — a detail-less feature shows no misleading "read" cue. */}
              {hasRules && <span className="eyebrow" style={{ fontSize: 8, color: "var(--royal)" }}>Rules ▸</span>}
            </div>
            {c.detail && <div className="body-sm muted" style={{ marginTop: 2, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{c.detail}</div>}
          </div>
        );
        if (!hasRules) {
          // Graceful degrade: no rules text -> today's static blurb (not a dead click-through).
          return <div key={c.name}>{card}</div>;
        }
        return (
          <button
            key={c.name}
            type="button"
            onClick={() => setOpen(c)}
            aria-haspopup="dialog"
            aria-label={"Read the rules for " + c.name}
            data-worldos-testid={"class-feature-" + _featureSlug(c.name)}
            style={{ display: "block", width: "100%", textAlign: "left", padding: 0, background: "transparent", border: "none", cursor: "pointer" }}
          >
            {card}
          </button>
        );
      })}
      {open && <FeatureInspector feature={open} contextLabel={contextLabel} onClose={() => setOpen(null)} />}
    </div>
  );
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

      {/* Class & subclass features the engine granted at this level (Arcane Recovery, Extra
          Attack, Action Surge, …). RRI-25e55fa optimizer #1: each feature with SRD rules text
          (detail, from the read-model) is now CLICK-THROUGH to a read-only rules panel — the
          optimizer's "#1 min-maxer pain point". A feature with no detail keeps its static blurb
          (ClassFeatureList handles both, never fabricating rules text). */}
      {classFeatures.length > 0 && (
        <>
          {classLine && <div className="eyebrow" style={{ marginBottom: 2 }}>{classLine}</div>}
          <ClassFeatureList features={classFeatures} contextLabel={classLine} />
        </>
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
  // Loop-10 #383: the player-authored biography is its own narrative source. A
  // hero with no race/flavor but an authored biography must still render it —
  // include it in the honest-empty guard so a bio-only hero isn't dropped.
  const biography = (hero.biography || "").trim();

  if (!race && !note && !biography) {
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
      {/* Loop-10 #383: the wizard's "Biography" textarea, surfaced as its own
          paragraph with a small eyebrow so the player can see their authored
          prose preserved on the Character sheet. Distinct from lineageNote
          (which pulls backstory/personality from the engine's NPC-flavor
          fields) — biography is the PLAYER's longer narrative input. Render
          only when set; honest empty otherwise. */}
      {biography && (
        <div style={{ marginTop: (note || race) ? 12 : 0 }}>
          <div className="eyebrow" style={{ fontSize: 11, letterSpacing: "0.14em", color: "var(--ink-600)", marginBottom: 4 }}>Biography</div>
          <p className="body" style={{ margin: 0, fontSize: 14, color: "var(--ink-700)", whiteSpace: "pre-wrap" }}>
            {biography}
          </p>
        </div>
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
  // #754 — the browsable preparable pool (the full class spell list this caster can prepare FROM),
  // distinct from the few they have prepared/known. Surfaces in the Spellbook so a prepared caster
  // (Paladin/Cleric/…) can browse the WHOLE list, not just their current preparation.
  const preparable = (Array.isArray(hero.preparableSpells) ? hero.preparableSpells : []);
  const isCaster = slots.length > 0 || groups.length > 0 || preparable.length > 0;
  // #268: every caster gets a working "Browse spellbook" path — a read-only inspector over
  // the hero's prepared/known spells AND the full preparable class list (#754). The empty state
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
      {/* #depth: surface the caster header the read-model already computes (hero.spellcasting:
          {abilityShort, spellSaveDc, spellAttackBonus}, server.py _character_spellcasting). Omitted
          for non-casters (spellcasting null). The optimizer persona's #1-cited missing number. */}
      {hero.spellcasting ? (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "6px 0 14px" }}>
          {[
            `Spell Save DC ${hero.spellcasting.spellSaveDc}`,
            `Spell Attack ${hero.spellcasting.spellAttackBonus >= 0 ? "+" : ""}${hero.spellcasting.spellAttackBonus}`,
            `${String(hero.spellcasting.abilityShort || "").toUpperCase()} casting`,
          ].map((label) => (
            <span key={label} style={{
              padding: "4px 11px",
              background: "rgba(176,141,87,0.12)",
              boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
              fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.06em",
              color: "var(--ink-900)", whiteSpace: "nowrap",
            }}>{label}</span>
          ))}
        </div>
      ) : null}
      <SpellSlotTrack slots={slots} />
      {/* #half-caster: a muted line naming the next LOCKED slot level for a slower-than-full
          caster (server.py _caster_tier_and_note), so an SRD-correct half-caster slot count
          (e.g. an L10 Paladin topping out at 3rd-level slots) reads as intentional, not missing.
          Guarded: renders NOTHING when the read-model has no note (full casters, top-slot
          half-casters, non-casters). */}
      {hero.spellcasting && hero.spellcasting.slotProgressionNote ? (
        <div className="muted body-sm" style={{ marginTop: -10, marginBottom: 16 }}>
          {hero.spellcasting.slotProgressionNote}
        </div>
      ) : null}
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

      {browsing && <SpellbookBrowser hero={hero} groups={groups} preparable={preparable} onClose={() => setBrowsing(false)} />}
    </div>
  );
}

function SpellbookBrowser({ hero, groups, preparable, onClose }) {
  // Read-only spellbook inspector (#268 + #754). Surfaces TWO things from the
  // /character-surface read-model: (1) the hero's currently PREPARED/KNOWN spells, and
  // (2) the FULL browsable class spell list they can prepare FROM (hero.preparableSpells) —
  // the optimizer's MAJOR complaint that a Paladin could only see the few prepared, never the
  // whole list. Preparation still happens in Rest & Prepare (this stays read-only); here the
  // player BROWSES + plans. When the engine carries no spell data we say so honestly.
  const list = (Array.isArray(groups) ? groups : []).filter((g) => g && Array.isArray(g.list) && g.list.length);
  const pool = (Array.isArray(preparable) ? preparable : []);
  // name→slug for stable testids (mirror HeroEquipDoll); window.slug is the shared chrome helper.
  const slug = window.slug || ((n) => (n || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""));
  // Names the hero already has prepared — so the browsable list can MARK prepared vs available.
  const preparedNames = new Set();
  for (const g of list) {
    if (String(g.level).toLowerCase() === "prepared") {
      for (const sp of (g.list || [])) preparedNames.add(sp.name);
    }
  }
  // Group the full preparable pool by spell level for a scannable browse.
  const poolByLevel = (() => {
    const byLevel = {};
    for (const sp of pool) {
      const lv = Number(sp.level) || 0;
      (byLevel[lv] = byLevel[lv] || []).push(sp);
    }
    return Object.keys(byLevel).map((k) => Number(k)).sort((a, b) => a - b)
      .map((lv) => ({ level: lv, list: byLevel[lv] }));
  })();
  // a11y (WCAG 2.1.2 — no keyboard trap): Escape dismisses the dialog, mirroring toast.jsx.
  React.useEffect(() => {
    const esc = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
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
              <div key={group.level} style={{ marginTop: 16 }} data-worldos-testid={`spellbook-group-${slug(String(group.level))}`}>
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

          {/* #754 — the FULL browsable class spell list (what this caster CAN prepare). Clearly
              separated from "prepared/known" above, and each entry is tagged Prepared vs Available
              so a prepared caster (Paladin/Cleric/…) can plan from the whole list, not just the few. */}
          {poolByLevel.length > 0 && (
            <div data-worldos-testid="spellbook-available">
              <Divider />
              <SectionTitle right={<span className="muted body-sm">{pool.length}</span>}>
                Available to Prepare
              </SectionTitle>
              <p className="hand muted" style={{ fontSize: 12, marginTop: -2, marginBottom: 8 }}>
                The full {hero.class ? titleCaseWord(hero.class) + " " : ""}spell list you can choose from at <em>Rest &amp; Prepare</em>.
              </p>
              {poolByLevel.map((group) => (
                <div key={group.level} style={{ marginTop: 12 }} data-worldos-testid={`spellbook-available-level-${group.level}`}>
                  <SectionTitle right={<span className="muted body-sm">{group.list.length}</span>}>
                    {group.level === 0 ? "Cantrips" : `Level ${group.level}`}
                  </SectionTitle>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                    {group.list.map((sp) => {
                      const isPrepared = preparedNames.has(sp.name);
                      return (
                        <div key={sp.name} data-worldos-testid={`spellbook-available-spell-${slug(sp.name)}`} style={{
                          padding: 9,
                          background: isPrepared ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "rgba(176,141,87,0.05)",
                          boxShadow: isPrepared
                            ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
                            : "inset 0 0 0 1px rgba(140,100,60,0.2)",
                        }}>
                          <div style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "space-between", minWidth: 0 }}>
                            <div style={{ minWidth: 0 }}>
                              <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.05em", color: "var(--ink-900)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {sp.name}
                              </div>
                              <div className="hand muted" style={{ fontSize: 11 }}>{spellMeta(sp)}</div>
                            </div>
                            <span className="eyebrow" style={{ fontSize: 8, color: isPrepared ? "var(--emerald)" : "var(--ink-500)", whiteSpace: "nowrap" }}>
                              {isPrepared ? "Prepared" : "Available"}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
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

// Short SRD 5.2 effect blurbs for the Fighting Styles the engine defaults a canon martial to
// (plus the other common SRD styles, so a hand-authored/DM-set style still reads with its effect).
// The engine is the sole writer of hero.fightingStyle; this map is display-only flavor for the tab.
const FIGHTING_STYLE_EFFECTS = {
  "Defense": "+1 AC while you are wearing armor.",
  "Archery": "+2 bonus to attack rolls you make with ranged weapons.",
  "Dueling": "+2 damage when wielding a melee weapon in one hand and no other weapon.",
  "Great Weapon Fighting": "Reroll 1s and 2s on a damage die of a two-handed melee weapon.",
  "Two-Weapon Fighting": "Add your ability modifier to the damage of the off-hand attack.",
  "Protection": "Use your reaction and shield to impose disadvantage on an attack against a nearby ally.",
};

function FeatsTab({ hero }) {
  // The chosen Fighting Style of a canon martial (Fighter/Paladin/Ranger) — a NAMED style with its
  // short SRD effect, so the slot is no longer the blank stub the sweep flagged. Omitted (honest
  // empty-state) when the hero has none, matching the tab's other read-only sections.
  const fightingStyle = String(hero.fightingStyle || "").trim();
  return (
    <div>
      {fightingStyle && (
        <>
          <SectionTitle ordinal="·">Fighting Style</SectionTitle>
          <div style={{
            padding: 10,
            marginBottom: 8,
            background: "rgba(176,141,87,0.08)",
            boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
          }}>
            <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--ink-900)" }}>
              {fightingStyle}
            </div>
            {FIGHTING_STYLE_EFFECTS[fightingStyle] && (
              <div className="body-sm muted" style={{ marginTop: 2 }}>{FIGHTING_STYLE_EFFECTS[fightingStyle]}</div>
            )}
          </div>
          <Divider />
        </>
      )}
      <SectionTitle ordinal={fightingStyle ? undefined : "·"}>Weapon Proficiency</SectionTitle>
      <ul className="body" style={{ paddingLeft: 18, margin: 0 }}>
        {hero.proficiencies.map((p) => (<li key={p}>{p}</li>))}
      </ul>
      <Divider />
      <SectionTitle>Class Features</SectionTitle>
      {/* RRI-25e55fa optimizer #1: the Feats tab's Class Features are click-through to the SAME
          read-only rules panel the Abilities tab uses (the optimizer hit it on BOTH tabs). */}
      <ClassFeatureList
        features={hero.classFeatures}
        contextLabel={[hero.level != null ? `Level ${hero.level}` : null, hero.class ? titleCaseWord(hero.class) : null, hero.archetype || null].filter(Boolean).join(" · ")}
      />
    </div>
  );
}

Object.assign(window, { ScreenCharacter, AbilityScore, StatLine, ResourcesStatus, HeroEquipDoll, equippedStat, AbilitiesTab, SkillsTab, SpellsTab, SpellbookBrowser, SpellSlotTrack, SpellRules, SpellRuleChip, hasSpellRules, LineagePanel, FeatsTab, AbilityCard, FeatRow, ClassFeatureList, FeatureInspector, RestPrepareModal, RestCard, ProficiencyDot, ProficiencyBadge, characterPortraitScope, spellMeta });
