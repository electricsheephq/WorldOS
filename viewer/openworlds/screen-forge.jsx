/* Screen: Forge — item & spell crafting */

function ScreenForge({ onNavigate, state, setState }) {
  const [category, setCategory] = React.useState("smith");
  const [selected, setSelected] = React.useState(RECIPES_LIST[0]);
  const [crafter, setCrafter] = React.useState("vell");
  const [log, setLog] = React.useState([
    { when: "yesterday", who: "Cassian", item: "Scroll of Light", success: true },
    { when: "2 days past", who: "Vell", item: "Iron-shod boots (repair)", success: true },
    { when: "5 days past", who: "Mira", item: "Healer's draught", success: false, note: "Cooking DC missed by 3" },
  ]);
  const toast = window.useToast ? window.useToast() : (() => {});

  const recipes = RECIPES_LIST.filter((r) => r.category === category);
  const hero = state.party.find((p) => p.id === crafter);
  const skillBonus = selected ? (CRAFTER_SKILL[hero.id]?.[selected.skill] ?? 4) : 0;
  const successChance = selected ? Math.max(5, Math.min(95, (skillBonus - selected.dc + 20) * 5)) : 0;

  const craft = () => {
    const roll = 1 + Math.floor(Math.random() * 20);
    const total = roll + skillBonus;
    const success = total >= selected.dc;
    setLog((l) => [{ when: "just now", who: hero.name.split(" ")[0], item: selected.name, success, roll: total + " vs DC " + selected.dc }, ...l].slice(0, 8));
    toast({
      kind: success ? "item" : "danger",
      eyebrow: success ? "Forge" : "Failed",
      title: success ? selected.name + " is made" : "The forge will not have it today",
      body: success ? hero.name + " stows it in their pack." : "Roll " + total + " vs DC " + selected.dc + ". The materials are not lost — try again at next rest.",
    });
  };

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "260px 1fr 300px", gap: 14, padding: 14 }}>

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
              <Placeholder label={r.glyph} w={36} h={36} framed />
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
              <Placeholder label={selected.glyph + " · plate"} h={180} framed />
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
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
              {selected.components.map((c, i) => (
                <ComponentSlot key={i} component={c} have={c.have} />
              ))}
            </div>

            <Divider />

            <SectionTitle>Notes from the chronicle</SectionTitle>
            <div className="hand" style={{ fontSize: 14, color: "var(--ink-700)" }}>
              "{selected.note}"
              <div className="muted" style={{ fontFamily: "var(--f-body)", fontStyle: "normal", fontSize: 12, marginTop: 4 }}>
                — {selected.noteBy || "Linzi, scribe"}
              </div>
            </div>
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
        <div style={{ display: "flex", gap: 6 }}>
          {state.party.map((p) => (
            <button key={p.id} onClick={() => setCrafter(p.id)} style={{
              flex: 1,
              padding: 4,
              background: crafter === p.id ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
              boxShadow: crafter === p.id ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)" : "inset 0 0 0 1px rgba(140,100,60,0.25)",
              cursor: "pointer",
            }}>
              <Placeholder label={p.short || "portrait"} w="100%" h={56} framed />
              <div className="hand" style={{ fontSize: 11, marginTop: 4, color: "var(--ink-700)" }}>{p.name.split(" ")[0]}</div>
            </button>
          ))}
        </div>

        {selected && !selected.locked && (
          <>
            <Divider />

            <SectionTitle>Forecast</SectionTitle>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
              <StatLine k={hero.name.split(" ")[0]} v={"+" + skillBonus} />
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

            <BrassButton tone="crimson" onClick={craft} style={{ width: "100%", marginTop: 14 }}>
              ⚒ To the forge
            </BrassButton>
            <div className="hand muted" style={{ fontSize: 11, marginTop: 4, textAlign: "center" }}>
              Crafting happens at next rest.
            </div>
          </>
        )}

        <Divider />

        <SectionTitle>Workshop ledger</SectionTitle>
        <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
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
      <Placeholder label={component.glyph} w="100%" h={48} framed />
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

const CRAFTER_SKILL = {
  cassian: { Craft: 5, Alchemy: 2, Spellcraft: 8, Linguistics: 2 },
  mira: { Craft: 3, Alchemy: 4, Spellcraft: 6, Linguistics: 8 },
  vell: { Craft: 9, Alchemy: 1, Spellcraft: 0, Linguistics: 1 },
};

const RECIPES_LIST = [
  // Smithing
  {
    id: "s1", category: "smith", tier: "I", name: "Sharpened greataxe edge",
    glyph: "axe edge", desc: "Restore an edge dulled by a season's road. The blade will sing again for a stretch.",
    skill: "Craft", dc: 12, time: "1 rest",
    components: [
      { name: "Whetstone", glyph: "whetstone", qty: 1, have: 1 },
      { name: "Oil", glyph: "oil flask", qty: 1, have: 3 },
      { name: "Iron filings", glyph: "filings", qty: 1, have: 2 },
    ],
    note: "Vell does this every fourth rest whether he needs to or not. Says it calms him. I am not arguing.",
  },
  {
    id: "s2", category: "smith", tier: "II", name: "Iron-shod boots (repair)",
    glyph: "boots", desc: "Re-nail the heel-plates and tighten the buckle of a worn pair of iron-shods.",
    skill: "Craft", dc: 14, time: "1 rest",
    components: [
      { name: "Iron nails", glyph: "nails", qty: 4, have: 12 },
      { name: "Leather strap", glyph: "strap", qty: 1, have: 2 },
      { name: "Hammer", glyph: "smith's hammer", qty: 1, have: 1 },
    ],
    note: "If you do not own the boots, do not repair them. The chronicle has had to write that sentence twice.",
  },
  {
    id: "s3", category: "smith", tier: "III", name: "Cold-forged dagger",
    glyph: "dagger", desc: "A small blade with a long memory of cold. +1 damage to creatures that fear winter; that is most of them.",
    skill: "Craft", dc: 18, time: "2 rests",
    components: [
      { name: "Cold iron ingot", glyph: "ingot", qty: 1, have: 1 },
      { name: "Bog-pearl", glyph: "pearl", qty: 1, have: 2 },
      { name: "Leather strap", glyph: "strap", qty: 1, have: 2 },
      { name: "Hammer", glyph: "hammer", qty: 1, have: 1 },
    ],
    note: "Forged at the coldest hour of camp. The fire is for warmth, not for the work.",
  },
  { id: "s4", category: "smith", tier: "IV", locked: true, name: "?????", glyph: "?" },

  // Alchemy
  {
    id: "a1", category: "alchemy", tier: "I", name: "Healer's draught",
    glyph: "red potion", desc: "Restores 2d4+2 hp when consumed. Tastes of iron and elderberry. Will keep a season.",
    skill: "Alchemy", dc: 13, time: "1 rest",
    components: [
      { name: "Elderberry", glyph: "berries", qty: 2, have: 5 },
      { name: "Glass vial", glyph: "vial", qty: 1, have: 4 },
      { name: "Spring water", glyph: "flask", qty: 1, have: 6 },
    ],
    note: "Mira tried twice. Mira is in the ledger. Mira will try again.",
  },
  {
    id: "a2", category: "alchemy", tier: "II", name: "Antitoxin",
    glyph: "green vial", desc: "+5 alchemical bonus to next save vs. poison. Lasts one hour.",
    skill: "Alchemy", dc: 15, time: "1 rest",
    components: [
      { name: "Charcoal", glyph: "charcoal", qty: 1, have: 2 },
      { name: "Distilled wine", glyph: "wine flask", qty: 1, have: 1 },
      { name: "Glass vial", glyph: "vial", qty: 1, have: 4 },
    ],
    note: "Useful against fen-snakes. Useful against politicians.",
  },
  {
    id: "a3", category: "alchemy", tier: "III", name: "Alchemist's fire",
    glyph: "orange flask", desc: "Splash flask. 1d6 fire, continues 1 round. Throw at range 10. Do not store near rations.",
    skill: "Alchemy", dc: 17, time: "1 rest",
    components: [
      { name: "Naphtha", glyph: "naphtha", qty: 1, have: 0 },
      { name: "Sulfur", glyph: "sulfur", qty: 1, have: 1 },
      { name: "Glass vial", glyph: "vial", qty: 1, have: 4 },
    ],
    note: "Naphtha is bought from Oleg. Oleg does not always sell it.",
  },

  // Scribing
  {
    id: "sc1", category: "scribe", tier: "I", name: "Scroll of Light",
    glyph: "scroll", desc: "Single-use scroll of the cantrip Light. Useful for one who cannot cast it but might one day need to.",
    skill: "Spellcraft", dc: 11, time: "1 rest",
    components: [
      { name: "Vellum", glyph: "vellum", qty: 1, have: 4 },
      { name: "Brass ink", glyph: "inkpot", qty: 1, have: 2 },
      { name: "Quill", glyph: "quill", qty: 1, have: 1 },
    ],
    note: "Cassian writes these to keep his hand in. Linzi reads them aloud before they are sealed.",
  },
  {
    id: "sc2", category: "scribe", tier: "II", name: "Scroll of Cure Light Wounds",
    glyph: "scroll", desc: "Single-use scroll of the 1st-level divine spell Cure Light Wounds (1d8+1 HP).",
    skill: "Spellcraft", dc: 14, time: "1 rest",
    components: [
      { name: "Vellum", glyph: "vellum", qty: 1, have: 4 },
      { name: "Brass ink", glyph: "inkpot", qty: 1, have: 2 },
      { name: "Holy ash", glyph: "ash", qty: 1, have: 1 },
    ],
    note: "Cleric absent. Improvised by Cassian. Worked twice in three tries.",
  },
  {
    id: "sc3", category: "scribe", tier: "III", name: "Scroll of Mage Armor",
    glyph: "scroll, blue seal", desc: "Single-use scroll of Mage Armor. Useful when you have not slept and your magus has not prayed.",
    skill: "Spellcraft", dc: 16, time: "1 rest",
    components: [
      { name: "Vellum", glyph: "vellum", qty: 2, have: 4 },
      { name: "Blue wax", glyph: "wax", qty: 1, have: 1 },
      { name: "Spell focus shard", glyph: "shard", qty: 1, have: 1 },
    ],
    note: "Linzi writes these in her own hand. Says they cast better that way.",
  },

  // Enchanting
  {
    id: "e1", category: "enchant", tier: "II", name: "Ward chalk",
    glyph: "white chalk", desc: "Sketch a one-room ward on a stone floor. Detects undead, fey, and a few unhappier categories.",
    skill: "Spellcraft", dc: 15, time: "1 rest",
    components: [
      { name: "Chalk", glyph: "chalk", qty: 2, have: 1 },
      { name: "Spring water", glyph: "flask", qty: 1, have: 6 },
      { name: "Salt", glyph: "salt", qty: 1, have: 4 },
    ],
    note: "Useful at the Lanternrest. Wholly useless against the crow on the gable, in case you were wondering.",
  },
  {
    id: "e2", category: "enchant", tier: "III", name: "Cold-iron ring",
    glyph: "ring", desc: "+1 saves vs. fey. Slim chance to attract them, depending on what you have been doing.",
    skill: "Spellcraft", dc: 18, time: "2 rests",
    components: [
      { name: "Cold iron ingot", glyph: "ingot", qty: 1, have: 1 },
      { name: "Bog-pearl", glyph: "pearl", qty: 2, have: 2 },
      { name: "Hammer", glyph: "hammer", qty: 1, have: 1 },
    ],
    note: "Vell forges, Cassian binds. They argue for an hour about whose name goes on it. Neither does.",
  },
  { id: "e3", category: "enchant", tier: "IV", locked: true, name: "?????", glyph: "?" },
];

Object.assign(window, { ScreenForge, ComponentSlot, RECIPES_LIST, CRAFTER_SKILL, CATEGORY_LABEL });
