/* Screen: Forge — item & spell crafting */

/* W2d: item-icon scope helper — mirrors screen-inventory's slug()/itemScope(). Recipes craft
   a real item (its `name`) and components are named reagents; build "item-<slug(name)>" so
   wiki icons resolve, with graceful 404 → <Placeholder> fallback inside <Img>. Obscure
   crafting reagents with no wiki page simply fall back to the placeholder glyph. */
function fItemScope(name) {
  const s = (window.slug ? window.slug(name) : "");
  return s ? "item-" + s : "";
}

/* Crafter portrait scope — mirrors screen-character's portraitScope(): a party member's
   instance id ("char_…") matches no ingested art, so derive the scope from slug(name)
   ("portrait-dal-lightspark") which resolves a canon hero's real face; falls back to the
   instance id, then to <Img>'s neutral PortraitSilhouette for a portrait-less crafter. */
function fPortraitScope(p) {
  const s = (p && p.name && window.slug) ? window.slug(p.name) : "";
  if (s) return "portrait-" + s;
  return (p && p.id) ? "portrait-" + p.id : "";
}

function ScreenForge({ onNavigate, state, setState }) {
  // Crafting-roll prototype: the recipe mechanics + roll simulation are display-only (not
  // persisted to the engine). The crafters at the bench, however, are bound to the LIVE
  // party from /character-surface — never a hardcoded demo roster. When the live party is
  // empty, the bench shows an honest empty-state below.
  const surfaceQuery = window.combatSurfaceFromCampaign
    ? window.combatSurfaceFromCampaign(
        (Array.isArray(state?.campaigns) ? state.campaigns : []).find((c) => c.id === state?.activeCampaign) ||
          (Array.isArray(state?.campaigns) ? state.campaigns : [])[0] || {},
        state,
      )
    : "";
  const [surface, setSurface] = React.useState(null);
  // LIVE party from the /character-surface read-model only — never a demo roster.
  const party = (Array.isArray(surface?.party) && surface.party.length) ? surface.party : [];
  const [category, setCategory] = React.useState("smith");
  const [selected, setSelected] = React.useState(RECIPES_LIST[0]);
  const [crafter, setCrafter] = React.useState("");
  // Workshop ledger starts empty — entries are prepended by an actual craft attempt
  // (relayed move when live, local-roll sim in read-only preview). No seeded history;
  // a fresh party's bench has nothing on the ledger until the first craft.
  const [log, setLog] = React.useState([]);
  const toast = window.useToast ? window.useToast() : (() => {});

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    try {
      const response = await fetch("/character-surface" + surfaceQuery, { cache: "no-store" });
      if (!response.ok) throw new Error(`character surface ${response.status}`);
      const payload = await response.json();
      if (!isCancelled()) setSurface(payload);
    } catch (error) { /* keep last good; empty-state shows until the first success */ }
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
    if (party.length > 0 && !party.find((p) => p.id === crafter)) {
      setCrafter(party[0].id);
    }
  }, [party, crafter]);

  const recipes = RECIPES_LIST.filter((r) => r.category === category);
  const hero = party.find((p) => p.id === crafter) || party[0];
  // Derive the crafting bonus from the live hero's projected skills (the /character-surface
  // `skills`/`toolProficiencies` array of { name, mod }). The recipe's `skill` may be a tool
  // proficiency (Smith's Tools / Alchemist's Supplies) the surface does not carry — in that
  // case the bonus is UNKNOWN (null), and we say so rather than fabricating a +4. Never a
  // hardcoded per-crafter table.
  const skillBonus = (() => {
    if (!selected || !hero) return null;
    const pools = [hero.skills, hero.toolProficiencies, hero.tools].filter(Array.isArray);
    const want = String(selected.skill || "").toLowerCase();
    for (const pool of pools) {
      const match = pool.find((s) => String(s?.name || "").toLowerCase() === want);
      if (match && typeof match.mod === "number") return match.mod;
    }
    return null;
  })();
  const hasForecast = typeof skillBonus === "number";
  const successChance = (selected && hasForecast) ? Math.max(5, Math.min(95, (skillBonus - selected.dc + 20) * 5)) : 0;

  // Phase-4 action lane: when live (DM attached), a Forge "Craft" relays a
  // structured `check` move (skill + DC + the recipe name) so the engine rolls
  // and the DM narrates the outcome via the real engine, not a local-only
  // simulation. Read-only preview keeps the existing local-roll behavior so
  // the screen still demonstrates the mechanic.
  const canAct = Boolean(surface?.can_act);
  const campaignId = surface?.campaign_id || "";

  const craft = () => {
    if (!hero || !selected) return;
    if (canAct) {
      const move = {
        kind: "check",
        name: `craft ${selected.name}`,
        skill: selected.skill,
        dc: selected.dc,
        text: `${hero.name.split(" ")[0]} attempts to craft ${selected.name} (DC ${selected.dc}, ${selected.skill})`,
        campaign: campaignId,
      };
      fetch("/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(move),
      }).then(() => {
        setLog((l) => [{ when: "just now", who: hero.name.split(" ")[0], item: selected.name, success: true, roll: `relayed (DC ${selected.dc})` }, ...l].slice(0, 8));
        toast({ kind: "item", eyebrow: "Forge", title: `${selected.name} — at the bench`, body: `Move relayed to the DM. The engine rolls the ${selected.skill} check (DC ${selected.dc}); the DM narrates the result.` });
      }).catch((e) => toast({ kind: "danger", title: "Move not sent", body: e?.message || "viewer unreachable" }));
      return;
    }
    // !canAct (read-only preview): local-roll simulation. When the crafting bonus is unknown
    // (the surface carries no matching proficiency), roll the raw d20 vs DC rather than
    // inventing a modifier — the roll line shows just the d20 so nothing is fabricated.
    const roll = 1 + Math.floor(Math.random() * 20);
    const total = roll + (hasForecast ? skillBonus : 0);
    const success = total >= selected.dc;
    const rollText = hasForecast ? (total + " vs DC " + selected.dc) : ("d20 " + roll + " vs DC " + selected.dc);
    setLog((l) => [{ when: "just now", who: hero.name.split(" ")[0], item: selected.name, success, roll: rollText }, ...l].slice(0, 8));
    toast({
      kind: success ? "item" : "danger",
      eyebrow: success ? "Forge" : "Failed",
      title: success ? selected.name + " is made" : "The forge will not have it today",
      body: success ? hero.name + " stows it in their pack." : "Roll " + rollText + ". The materials are not lost — try again at next rest.",
    });
  };

  return (
    <div className="screen" style={{ height: "100%", display: "flex", flexDirection: "column", gap: 8, padding: 14 }}>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "260px 1fr 300px", gap: 14, minHeight: 0 }}>

      {/* LEFT: Recipe categories + list */}
      <Panel framed style={{ padding: 18, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>What may be made</div>
        <h2 className="h1" style={{ fontSize: 20 }}>Recipes</h2>
        <Divider />

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, marginBottom: 12 }}>
          {[
            { id: "smith", label: "Smithing" },
            { id: "alchemy", label: "Alchemy" },
            { id: "scribe", label: "Scribing" },
            { id: "enchant", label: "Enchanting" },
          ].map((c) => (
            <button key={c.id} onClick={() => { setCategory(c.id); setSelected(RECIPES_LIST.find((r) => r.category === c.id)); }} className="pill" style={{
              cursor: "pointer", textAlign: "center",
              padding: "8px 4px",
              background: category === c.id ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(176,141,87,0.08)",
              color: category === c.id ? "var(--w-300)" : "var(--ink-700)",
              boxShadow: category === c.id ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)" : "inset 0 0 0 1px rgba(140,100,60,0.3)",
            }}>{c.label}</button>
          ))}
        </div>

        <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
          {recipes.map((r) => (
            <button key={r.id} onClick={() => setSelected(r)} style={{
              display: "grid", gridTemplateColumns: "36px 1fr auto", gap: 8, alignItems: "center",
              padding: "8px 10px",
              background: selected?.id === r.id ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
              boxShadow: selected?.id === r.id
                ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
                : "inset 0 -1px 0 rgba(140,100,60,0.15)",
              cursor: "pointer", textAlign: "left",
              opacity: r.locked ? 0.5 : 1,
            }}>
              {r.locked
                ? <Placeholder label={r.glyph} w={36} h={36} framed />
                : <Img scope={fItemScope(r.name)} label={r.glyph || r.name} w={36} h={36} fit="contain" framed />}
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.05em", color: r.locked ? "var(--ink-600)" : "var(--ink-900)" }}>
                  {r.locked ? "?????" : r.name}
                </div>
                <div className="hand muted" style={{ fontSize: 10 }}>
                  {r.locked ? "blueprint unknown" : "DC " + r.dc + " · " + r.time}
                </div>
              </div>
              <Pill>{r.tier}</Pill>
            </button>
          ))}
        </div>

        <div className="muted body-sm" style={{ marginTop: 8, textAlign: "center" }}>
          {recipes.filter((r) => !r.locked).length} known · {recipes.filter((r) => r.locked).length} rumoured
        </div>
      </Panel>

      {/* CENTER: Selected blueprint */}
      <Panel framed style={{ padding: 28, overflow: "auto" }}>
        {selected && !selected.locked ? (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 22, alignItems: "start" }}>
              <Img scope={fItemScope(selected.name)} label={selected.glyph + " · plate"} h={180} fit="contain" framed />
              <div>
                <div className="eyebrow" style={{ color: "var(--crimson)" }}>{CATEGORY_LABEL[selected.category]} · {selected.tier}</div>
                <h1 className="h1" style={{ marginTop: 2, fontSize: 24 }}>{selected.name}</h1>
                <p className="body" style={{ marginTop: 6, fontSize: 14 }}>{selected.desc}</p>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginTop: 14 }}>
                  <StatLine k="Skill" v={selected.skill} />
                  <StatLine k="Crafting DC" v={selected.dc} />
                  <StatLine k="Time" v={selected.time} />
                </div>
              </div>
            </div>

            <Divider />

            <SectionTitle ordinal="·">Components</SectionTitle>
            {/* auto-fit so a 3-reagent recipe fills the row instead of leaving a decorative
                empty 4th slot; minmax keeps cards a readable width and caps at ~4 across. */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8 }}>
              {selected.components.map((c, i) => (
                <ComponentSlot key={i} component={c} have={c.have} />
              ))}
            </div>

            {selected.note && (
              <>
                <Divider />

                <SectionTitle>Notes from the chronicle</SectionTitle>
                <div className="hand" style={{ fontSize: 14, color: "var(--ink-700)" }}>
                  "{selected.note}"
                  <div className="muted" style={{ fontFamily: "var(--f-body)", fontStyle: "normal", fontSize: 12, marginTop: 4 }}>
                    — {selected.noteBy || "the chronicle"}
                  </div>
                </div>
              </>
            )}
          </>
        ) : (
          <div style={{ display: "grid", placeItems: "center", height: "100%", textAlign: "center" }}>
            <div>
              <div style={{ fontSize: 48, color: "var(--crimson)", fontFamily: "var(--f-display)" }}>?</div>
              <h2 className="h1" style={{ fontSize: 20 }}>Blueprint unknown</h2>
              <p className="hand muted" style={{ marginTop: 6 }}>
                Read a tome. Apprentice a master. Find a scroll.<br/>
                The chronicle will record the moment.
              </p>
            </div>
          </div>
        )}
      </Panel>

      {/* RIGHT: Crafter + Forge button + history */}
      <Panel framed style={{ padding: 22, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <SectionTitle>Hands at the bench</SectionTitle>
        {party.length > 0 ? (
          <div style={{ display: "flex", gap: 6 }}>
            {party.map((p) => (
              <button key={p.id} onClick={() => setCrafter(p.id)} style={{
                flex: 1,
                padding: 4,
                background: crafter === p.id ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
                boxShadow: crafter === p.id ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)" : "inset 0 0 0 1px rgba(140,100,60,0.25)",
                cursor: "pointer",
              }}>
                <Img scope={fPortraitScope(p)} label={p.name || p.short || "portrait"} w="100%" h={56} fit="cover" framed />
                <div className="hand" style={{ fontSize: 11, marginTop: 4, color: "var(--ink-700)" }}>{p.name.split(" ")[0]}</div>
              </button>
            ))}
          </div>
        ) : (
          <div className="muted body-sm" style={{ marginTop: 4 }}>
            Your party's crafters appear here once you have a hero.
          </div>
        )}

        {selected && !selected.locked && hero && (
          <>
            <Divider />

            <SectionTitle>Forecast</SectionTitle>
            {hasForecast ? (
              <>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                  <StatLine k={hero.name.split(" ")[0]} v={(skillBonus >= 0 ? "+" : "") + skillBonus} />
                  <StatLine k="Target" v={"DC " + selected.dc} />
                </div>

                <div style={{ marginTop: 10 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span className="eyebrow">Likely success</span>
                    <span style={{ fontFamily: "var(--f-display)", fontSize: 16, color: successChance > 65 ? "var(--emerald)" : successChance > 35 ? "var(--ink-900)" : "var(--crimson)" }}>
                      {successChance}%
                    </span>
                  </div>
                  <div style={{ height: 8, background: "rgba(0,0,0,0.15)", position: "relative", boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.4)" }}>
                    <div style={{
                      position: "absolute", inset: 0, right: `${100 - successChance}%`,
                      background: successChance > 65
                        ? "linear-gradient(180deg, #5a8a3a, #3a6020)"
                        : successChance > 35
                        ? "linear-gradient(180deg, var(--b-200), var(--b-500))"
                        : "linear-gradient(180deg, var(--crimson), #4a1010)",
                    }} />
                  </div>
                </div>
              </>
            ) : (
              // Honest: the surface carries no proficiency matching this recipe's tool, so we
              // do NOT fabricate a +N forecast. The roll is still possible (raw d20 vs DC).
              <div style={{ marginTop: 4 }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                  <StatLine k={hero.name.split(" ")[0]} v="—" />
                  <StatLine k="Target" v={"DC " + selected.dc} />
                </div>
                <div className="hand muted" style={{ fontSize: 11, marginTop: 8 }}>
                  No {selected.skill} proficiency recorded for {hero.name.split(" ")[0]} — the forecast is unknown until the engine resolves the roll.
                </div>
              </div>
            )}

            <BrassButton tone="crimson" onClick={craft} style={{ width: "100%", marginTop: 14 }} title={canAct ? "Relays a skill check to the DM via /move — the engine rolls and resolves" : "No live session attached — the roll is simulated locally and not saved"}>
              ⚒ To the forge
            </BrassButton>
            <div className="hand muted" style={{ fontSize: 11, marginTop: 4, textAlign: "center" }}>
              {canAct ? "Relays a skill check to the DM — the engine rolls; the DM narrates." : "Crafting happens at next rest."}
            </div>
          </>
        )}

        <Divider />

        <SectionTitle>Workshop ledger</SectionTitle>
        <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
          {log.length === 0 && (
            <div className="body-sm muted" style={{ marginTop: 4 }}>
              No entries yet. The first craft will appear here.
            </div>
          )}
          {log.map((l, i) => (
            <div key={i} style={{
              padding: "6px 10px",
              background: l.success ? "rgba(95, 130, 70, 0.08)" : "rgba(110, 30, 30, 0.08)",
              boxShadow: "inset 0 0 0 1px " + (l.success ? "rgba(95,130,70,0.25)" : "rgba(110,30,30,0.25)"),
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.06em", color: "var(--ink-900)" }}>
                  {l.item}
                </span>
                <span style={{ fontFamily: "var(--f-mono)", fontSize: 9, color: "var(--ink-600)" }}>{l.when}</span>
              </div>
              <div className="hand muted" style={{ fontSize: 11 }}>
                {l.who} · {l.success ? "made" : "failed"} {l.roll && "· " + l.roll}
                {l.note && <span style={{ color: "var(--crimson)" }}>  {l.note}</span>}
              </div>
            </div>
          ))}
        </div>
      </Panel>
      </div>
    </div>
  );
}

function ComponentSlot({ component, have }) {
  const ok = have >= component.qty;
  return (
    <div style={{
      padding: 8,
      background: ok ? "rgba(95, 130, 70, 0.08)" : "rgba(176,141,87,0.06)",
      boxShadow: "inset 0 0 0 1px " + (ok ? "rgba(95,130,70,0.4)" : "rgba(140,100,60,0.3)"),
    }}>
      <Img scope={fItemScope(component.name)} label={component.glyph || component.name} w="100%" h={48} fit="contain" framed />
      <div style={{
        fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.04em",
        color: "var(--ink-900)", marginTop: 6,
        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
      }}>{component.name}</div>
      <div style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: ok ? "var(--emerald)" : "var(--crimson)", marginTop: 2 }}>
        {have ?? 0}/{component.qty}
      </div>
    </div>
  );
}

const CATEGORY_LABEL = {
  smith: "Smithing",
  alchemy: "Alchemy",
  scribe: "Scribing",
  enchant: "Enchanting",
};

const RECIPES_LIST = [
  // Smithing
  {
    id: "s1", category: "smith", tier: "I", name: "Sharpened greataxe edge",
    glyph: "axe edge", desc: "Restore an edge dulled by a season's road. The blade will sing again for a stretch.",
    skill: "Smith's Tools", dc: 12, time: "1 rest",
    components: [
      { name: "Whetstone", glyph: "whetstone", qty: 1, have: 1 },
      { name: "Oil", glyph: "oil flask", qty: 1, have: 3 },
      { name: "Iron filings", glyph: "filings", qty: 1, have: 2 },
    ],
    note: "Done every fourth rest whether the edge needs it or not — it steadies the hands.",
  },
  {
    id: "s2", category: "smith", tier: "II", name: "Iron-shod boots (repair)",
    glyph: "boots", desc: "Re-nail the heel-plates and tighten the buckle of a worn pair of iron-shods.",
    skill: "Smith's Tools", dc: 14, time: "1 rest",
    components: [
      { name: "Iron nails", glyph: "nails", qty: 4, have: 12 },
      { name: "Leather strap", glyph: "strap", qty: 1, have: 2 },
      { name: "Hammer", glyph: "smith's hammer", qty: 1, have: 1 },
    ],
    note: "If you do not own the boots, do not repair them. The chronicle has had to write that sentence twice.",
  },
  {
    id: "s3", category: "smith", tier: "III", name: "Fine silvered dagger",
    glyph: "dagger", desc: "A small blade sheathed in silver. Bites cleanly into creatures that shrug off ordinary steel.",
    skill: "Smith's Tools", dc: 18, time: "2 rests",
    components: [
      { name: "Silver ingot", glyph: "ingot", qty: 1, have: 1 },
      { name: "River pearl", glyph: "pearl", qty: 1, have: 2 },
      { name: "Leather strap", glyph: "strap", qty: 1, have: 2 },
      { name: "Hammer", glyph: "hammer", qty: 1, have: 1 },
    ],
    note: "Forged at the coldest hour of camp. The fire is for warmth, not for the work.",
  },
  { id: "s4", category: "smith", tier: "IV", locked: true, name: "?????", glyph: "?" },

  // Alchemy
  {
    id: "a1", category: "alchemy", tier: "I", name: "Potion of Healing",
    glyph: "red potion", desc: "Restores 2d4+2 hp when consumed. Tastes of iron and elderberry. Will keep a season.",
    skill: "Alchemist's Supplies", dc: 13, time: "1 rest",
    components: [
      { name: "Elderberry", glyph: "berries", qty: 2, have: 5 },
      { name: "Glass vial", glyph: "vial", qty: 1, have: 4 },
      { name: "Spring water", glyph: "flask", qty: 1, have: 6 },
    ],
    note: "Easy to spoil, easy to retry — the materials are forgiving. Try again at next rest.",
  },
  {
    id: "a2", category: "alchemy", tier: "II", name: "Antitoxin",
    glyph: "green vial", desc: "Advantage on saving throws against poison for one hour. Does no good once you are already poisoned.",
    skill: "Alchemist's Supplies", dc: 15, time: "1 rest",
    components: [
      { name: "Charcoal", glyph: "charcoal", qty: 1, have: 2 },
      { name: "Distilled wine", glyph: "wine flask", qty: 1, have: 1 },
      { name: "Glass vial", glyph: "vial", qty: 1, have: 4 },
    ],
    note: "Useful against fen-snakes. Useful against politicians.",
  },
  {
    id: "a3", category: "alchemy", tier: "III", name: "Alchemist's Fire",
    glyph: "orange flask", desc: "Thrown flask. 1d4 fire on a hit, then 1d4 each turn until someone douses it. Range 20. Do not store near rations.",
    skill: "Alchemist's Supplies", dc: 17, time: "1 rest",
    components: [
      { name: "Naphtha", glyph: "naphtha", qty: 1, have: 0 },
      { name: "Sulfur", glyph: "sulfur", qty: 1, have: 1 },
      { name: "Glass vial", glyph: "vial", qty: 1, have: 4 },
    ],
    note: "Naphtha is bought from the quartermaster; not always in stock.",
  },

  // Scribing
  {
    id: "sc1", category: "scribe", tier: "I", name: "Scroll of Light",
    glyph: "scroll", desc: "Single-use scroll of the cantrip Light. Useful for one who cannot cast it but might one day need to.",
    skill: "Arcana", dc: 11, time: "1 rest",
    components: [
      { name: "Vellum", glyph: "vellum", qty: 1, have: 4 },
      { name: "Brass ink", glyph: "inkpot", qty: 1, have: 2 },
      { name: "Quill", glyph: "quill", qty: 1, have: 1 },
    ],
    note: "A good first scroll to keep your hand in. Read it aloud once before the wax is set.",
  },
  {
    id: "sc2", category: "scribe", tier: "II", name: "Scroll of Cure Wounds",
    glyph: "scroll", desc: "Single-use scroll of the 1st-level spell Cure Wounds (1d8 + spellcasting modifier HP).",
    skill: "Arcana", dc: 14, time: "1 rest",
    components: [
      { name: "Vellum", glyph: "vellum", qty: 1, have: 4 },
      { name: "Brass ink", glyph: "inkpot", qty: 1, have: 2 },
      { name: "Holy ash", glyph: "ash", qty: 1, have: 1 },
    ],
    note: "For when no cleric is at hand. Improvised work — succeeds about two tries in three.",
  },
  {
    id: "sc3", category: "scribe", tier: "III", name: "Scroll of Mage Armor",
    glyph: "scroll, blue seal", desc: "Single-use scroll of Mage Armor (AC 13 + Dex for 8 hours). Useful when you have not slept and your wizard has not prepared it.",
    skill: "Arcana", dc: 16, time: "1 rest",
    components: [
      { name: "Vellum", glyph: "vellum", qty: 2, have: 4 },
      { name: "Blue wax", glyph: "wax", qty: 1, have: 1 },
      { name: "Spell focus shard", glyph: "shard", qty: 1, have: 1 },
    ],
    note: "Best written by hand, slowly. The patient scribes swear they cast better that way.",
  },

  // Enchanting
  {
    id: "e1", category: "enchant", tier: "II", name: "Ward chalk",
    glyph: "white chalk", desc: "Sketch a one-room ward on a stone floor. Detects undead, fey, and a few unhappier categories.",
    skill: "Arcana", dc: 15, time: "1 rest",
    components: [
      { name: "Chalk", glyph: "chalk", qty: 2, have: 1 },
      { name: "Spring water", glyph: "flask", qty: 1, have: 6 },
      { name: "Salt", glyph: "salt", qty: 1, have: 4 },
    ],
    note: "Useful in a haunted hall. Wholly useless against an ordinary crow, in case you were wondering.",
  },
  {
    id: "e2", category: "enchant", tier: "III", name: "Ring of Warding",
    glyph: "ring", desc: "+1 to saving throws against fey. Slim chance to attract them, depending on what you have been doing.",
    skill: "Arcana", dc: 18, time: "2 rests",
    components: [
      { name: "Silver ingot", glyph: "ingot", qty: 1, have: 1 },
      { name: "River pearl", glyph: "pearl", qty: 2, have: 2 },
      { name: "Hammer", glyph: "hammer", qty: 1, have: 1 },
    ],
    note: "One smith forges, another binds. They argue for an hour about whose name goes on it. Neither does.",
  },
  { id: "e3", category: "enchant", tier: "IV", locked: true, name: "?????", glyph: "?" },
];

Object.assign(window, { ScreenForge, ComponentSlot, RECIPES_LIST, CATEGORY_LABEL, fItemScope, fPortraitScope });
