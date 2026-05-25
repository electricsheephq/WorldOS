/* Screen: Bestiary / Codex — encounters, lore, NPCs */

function ScreenBestiary({ onNavigate, state, setState }) {
  const [tab, setTab] = React.useState("creatures");
  const [selected, setSelected] = React.useState(BESTIARY[0]);
  const [filter, setFilter] = React.useState("");

  const entries = tab === "creatures" ? BESTIARY : tab === "people" ? PEOPLE : LORE;
  const filtered = entries.filter((e) => !filter || e.name.toLowerCase().includes(filter.toLowerCase()));

  React.useEffect(() => {
    if (filtered.length > 0 && !filtered.find((e) => e.id === selected?.id)) {
      setSelected(filtered[0]);
    }
  }, [tab, filter]);

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "280px 1fr", gap: 14, padding: 14 }}>

      {/* LEFT — index */}
      <Panel framed style={{ padding: 22, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>Encyclopaedia of</div>
        <h2 className="h1" style={{ fontSize: 22 }}>The Marches</h2>
        <Divider />

        <div style={{ display: "flex", gap: 4, marginBottom: 12 }}>
          {[
            { id: "creatures", label: "Creatures" },
            { id: "people", label: "Persons" },
            { id: "lore", label: "Lore" },
          ].map((t) => (
            <button key={t.id} onClick={() => setTab(t.id)} className="pill" style={{
              cursor: "pointer", flex: 1, textAlign: "center",
              background: tab === t.id ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(176,141,87,0.08)",
              color: tab === t.id ? "var(--w-300)" : "var(--ink-700)",
              boxShadow: tab === t.id ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)" : "inset 0 0 0 1px rgba(140,100,60,0.3)",
            }}>{t.label}</button>
          ))}
        </div>

        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search the index…"
          style={{ ...window.inkInput, fontSize: 14, padding: "8px 12px" }}
        />

        <div style={{ flex: 1, overflow: "auto", marginTop: 12, display: "flex", flexDirection: "column", gap: 4 }}>
          {filtered.map((e) => (
            <button key={e.id} onClick={() => setSelected(e)} style={{
              display: "grid", gridTemplateColumns: "36px 1fr auto", gap: 8, alignItems: "center",
              padding: "6px 10px",
              background: selected?.id === e.id ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
              boxShadow: selected?.id === e.id
                ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
                : "inset 0 -1px 0 rgba(140,100,60,0.15)",
              cursor: "pointer",
              textAlign: "left",
            }}>
              <Placeholder label={e.short} w={36} h={44} framed />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.06em", color: e.unknown ? "var(--ink-600)" : "var(--ink-900)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontStyle: e.unknown ? "italic" : "normal" }}>
                  {e.unknown ? "?????" : e.name}
                </div>
                <div className="hand muted" style={{ fontSize: 11 }}>{e.short_descriptor}</div>
              </div>
              {e.cr && <span className="pill" style={{ background: "rgba(176,141,87,0.18)", boxShadow: "inset 0 0 0 1px var(--b-500)" }}>CR {e.cr}</span>}
            </button>
          ))}
        </div>

        <div className="muted body-sm" style={{ marginTop: 8, textAlign: "center" }}>
          {filtered.length} known · {BESTIARY.filter((e) => e.unknown).length + PEOPLE.filter((e) => e.unknown).length} rumoured
        </div>
      </Panel>

      {/* RIGHT — entry */}
      {selected ? <BestiaryEntry entry={selected} tab={tab} /> : <Panel framed><div className="muted">Nothing selected.</div></Panel>}
    </div>
  );
}

function BestiaryEntry({ entry, tab }) {
  if (entry.unknown) {
    return (
      <Panel framed style={{ padding: 40, display: "grid", placeItems: "center" }}>
        <div style={{ textAlign: "center", maxWidth: 400 }}>
          <div style={{ fontSize: 48, color: "var(--crimson)", fontFamily: "var(--f-display)" }}>?</div>
          <h2 className="h1" style={{ fontSize: 22 }}>Not yet known</h2>
          <p className="body dropcap" style={{ marginTop: 12, textAlign: "left" }}>
            The chronicle has heard rumour of this, but has not yet seen it with its own eyes. Investigate, encounter, or be told to fill this page.
          </p>
        </div>
      </Panel>
    );
  }
  return (
    <Panel framed style={{ padding: 28, overflow: "auto" }}>
      <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 22, alignItems: "start" }}>
        <div>
          <Placeholder label={`${entry.short} · plate`} h={240} framed />
          {entry.cr && (
            <div style={{ marginTop: 8, padding: 8, background: "rgba(176,141,87,0.1)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)" }}>
              <div className="eyebrow text-center" style={{ textAlign: "center" }}>Challenge</div>
              <div style={{ fontFamily: "var(--f-display)", fontSize: 28, textAlign: "center", color: "var(--crimson)", letterSpacing: "0.06em" }}>{entry.cr}</div>
            </div>
          )}
        </div>

        <div>
          <div className="eyebrow" style={{ color: "var(--crimson)" }}>
            {tab === "creatures" ? entry.alignment + " · " + entry.size + " " + entry.kind :
             tab === "people" ? entry.role :
             "Lore entry"}
          </div>
          <h1 className="h1" style={{ marginTop: 2 }}>{entry.name}</h1>
          <div className="hand" style={{ fontSize: 15, color: "var(--ink-700)" }}>{entry.subtitle}</div>

          <Divider />

          {tab === "creatures" && entry.stats && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 6, marginBottom: 16 }}>
              {Object.entries(entry.stats).map(([k, v]) => (
                <div key={k} style={{
                  padding: "8px 0", textAlign: "center",
                  background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
                  boxShadow: "inset 0 0 0 1px var(--b-500)",
                }}>
                  <div className="eyebrow" style={{ fontSize: 9 }}>{k.toUpperCase()}</div>
                  <div style={{ fontFamily: "var(--f-display)", fontSize: 18, color: "var(--ink-900)", marginTop: 2 }}>{v}</div>
                </div>
              ))}
            </div>
          )}

          {tab === "creatures" && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginBottom: 16 }}>
              <StatLine k="HD" v={entry.hd} />
              <StatLine k="AC" v={entry.ac} />
              <StatLine k="Speed" v={entry.speed} />
              <StatLine k="Senses" v={entry.senses} />
              <StatLine k="Save" v={entry.save} />
              <StatLine k="Encountered" v={entry.encounteredAt} />
            </div>
          )}

          <p className="body dropcap" style={{ marginTop: 12 }}>
            {entry.body}
          </p>

          {entry.tactics && (
            <>
              <Divider />
              <SectionTitle>{tab === "creatures" ? "Tactics" : "What is known"}</SectionTitle>
              <p className="body">{entry.tactics}</p>
            </>
          )}

          {entry.loot && (
            <>
              <Divider />
              <SectionTitle>Spoils</SectionTitle>
              <div className="tag-row" style={{ marginTop: 6 }}>
                {entry.loot.map((l) => <Pill key={l}>{l}</Pill>)}
              </div>
            </>
          )}

          {entry.marginalia && (
            <>
              <Divider />
              <div className="eyebrow">Marginalia</div>
              <div className="hand" style={{ fontSize: 14, marginTop: 6, color: "var(--ink-700)" }}>
                "{entry.marginalia}"
                <div className="muted" style={{ fontFamily: "var(--f-body)", fontStyle: "normal", fontSize: 12, marginTop: 4 }}>
                  — {entry.marginaliaBy || "Linzi, scribe"}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </Panel>
  );
}

const BESTIARY = [
  {
    id: "kobold-skirmisher",
    name: "Kobold Skirmisher",
    short: "kobold",
    short_descriptor: "humanoid · scout",
    subtitle: "Trouble of the small and patient kind.",
    alignment: "Lawful Evil",
    size: "Small",
    kind: "humanoid (reptilian)",
    cr: "1/2",
    hd: "1d10+1",
    ac: "16",
    speed: "30 ft",
    senses: "Darkvision 60",
    save: "Fort +1 · Ref +3 · Will -1",
    stats: { str: 9, dex: 13, con: 10, int: 10, wis: 9, cha: 8 },
    encounteredAt: "Thorn Ford",
    body: "Kobolds of the southern fens are smaller than their cousins to the east but twice as patient. They will not engage a target they have not measured against three contingencies and a snare. Cassian remarked, after their first ambush, that they were not so much fighting as auditing.",
    tactics: "Open with shortspear and crossbow from elevation. If the player engages a flanker, the principals retreat to the second prepared snare, never the first. Will not pursue past the treeline. Will absolutely use the river.",
    loot: ["shortspear", "crossbow", "rags", "1d6 sp", "trap diagram"],
    marginalia: "I do not think they hate us. I think they are simply doing their jobs."
  },
  {
    id: "bog-strider",
    name: "Bog Strider",
    short: "strider",
    short_descriptor: "fey · ambush",
    subtitle: "Long-legged thing of the second-shallowest water.",
    alignment: "Neutral",
    size: "Large",
    kind: "fey (aquatic)",
    cr: "4",
    hd: "5d8+15",
    ac: "18",
    speed: "20 ft, swim 40 ft",
    senses: "Low-light, tremorsense 60",
    save: "Fort +5 · Ref +4 · Will +6",
    stats: { str: 16, dex: 13, con: 16, int: 8, wis: 14, cha: 6 },
    encounteredAt: "Thorn Ford",
    body: "A creature of stilt-legs and patience, the bog strider hunts where the water is exactly knee-deep. You will not see it until it is at hand, and you will not hear it because it is always already there.",
    tactics: "Initiates with grapple, drags to mid-river, releases the lighter party member at the deepest stride. Will not attack on dry land. Disengages if any party member casts light on its head.",
    loot: ["bog-pearls (3d4)", "fey ichor"],
    marginalia: "It blinked at me. Once. I do not know what was meant by it."
  },
  {
    id: "stag-lord-bandit",
    name: "Stag Lord Bandit",
    short: "bandit",
    short_descriptor: "humanoid · brigand",
    subtitle: "Tax-collector by his own definition.",
    alignment: "Chaotic Evil",
    size: "Medium",
    kind: "human",
    cr: "1",
    hd: "2d10+4",
    ac: "15",
    speed: "30 ft",
    senses: "—",
    save: "Fort +4 · Ref +3 · Will +0",
    stats: { str: 14, dex: 12, con: 14, int: 8, wis: 10, cha: 11 },
    encounteredAt: "Lanternrest courtyard",
    body: "The Stag Lord's bandits wear what they can steal and call it livery. They count themselves an army and a kingdom; the woods of the Stolen Marches indulge them. The leadership is, by Svetlana's account, considerably worse.",
    tactics: "Bandits open with crossbow from across the courtyard, advance to longsword if the player closes. They will not break and run on their own; the sergeant breaks first and the company follows.",
    loot: ["longsword", "leather", "shortbow", "3d6 sp", "stamped writ (forged)"],
    marginalia: "The Stag Lord pays them in salt. Not gold. Salt. Mira says this matters."
  },
  {
    id: "owlbear",
    name: "Owlbear",
    short: "owlbear",
    short_descriptor: "magical beast",
    subtitle: "The most-feared mistake in the Marches.",
    alignment: "Neutral",
    size: "Large",
    kind: "magical beast",
    cr: "4",
    hd: "5d10+20",
    ac: "15",
    speed: "30 ft",
    senses: "Low-light, scent",
    save: "Fort +8 · Ref +4 · Will +2",
    stats: { str: 21, dex: 12, con: 19, int: 2, wis: 12, cha: 10 },
    encounteredAt: "Rumoured · Old Hills",
    body: "An owlbear does not stop. There is no documented case of an owlbear breaking from a fight to do anything other than wedge itself between something and its eggs. It will not be reasoned with. It cannot be reasoned with. The only good owlbear is a den entrance that has not seen footprints in a week.",
    tactics: "Closes. Grapples. Does not let go. Pursue is the only verb it knows.",
    loot: ["owlbear pelt", "owlbear talons", "trauma"],
    marginalia: "Sketch from a distance. Always from a distance."
  },
  {
    id: "spectral-watcher",
    name: "Spectral Watcher",
    short: "spectre",
    short_descriptor: "undead · incorporeal",
    subtitle: "What waits in the Lanternrest, possibly.",
    alignment: "?",
    size: "Medium",
    kind: "undead (incorporeal)",
    cr: "?",
    hd: "?",
    ac: "?",
    speed: "fly",
    senses: "?",
    save: "?",
    stats: { str: "—", dex: "?", con: "—", int: "?", wis: "?", cha: "?" },
    encounteredAt: "Unconfirmed",
    body: "Reported by Mira, half-confirmed by the temperature of the hallway. The Lanternrest stands too long for a building maintained by no one. Whatever maintains it does so on terms that exclude lit lanterns and unbroken sleep.",
    tactics: "Unknown. Speculation: drains warmth before drains anything more vital. Does not appear to enter the courtyard.",
    marginalia: "I will not call it a ghost until it gives me reason. I will not call it nothing, either."
  },
  { id: "unk1", name: "??????", short: "?", short_descriptor: "rumoured · old hills", unknown: true },
  { id: "unk2", name: "??????", short: "?", short_descriptor: "rumoured · saltwell", unknown: true },
];

const PEOPLE = [
  {
    id: "svetlana",
    name: "Svetlana Leveton",
    short: "S·portrait",
    short_descriptor: "trader's wife · friend",
    subtitle: "Of Oleg's Trading Post.",
    role: "Ally · Quest-giver",
    encounteredAt: "Oleg's Trading Post",
    body: "Married to Oleg, who would not say his own name kindly. She runs the trading post and the silence of the trading post in equal measure. She wanted a partner against the bandits and met you instead. She has chosen, for the moment, to consider this an improvement.",
    tactics: "Trusts no one quickly. Trusts permanently once she has. Pays in stew and beds, never in coin.",
    loot: ["intelligence on bandit raids", "free first watch"],
    marginalia: "Her hands do not stop moving. I have not yet seen them rest."
  },
  {
    id: "linzi",
    name: "Linzi",
    short: "L·portrait",
    short_descriptor: "halfling · scribe · party",
    subtitle: "Chronicler of the Long Road.",
    role: "Party · NPC",
    encounteredAt: "Restov, prologue",
    body: "Halfling, chronicler, refuses the word 'bard.' Writes the chronicle by candle when there is candle and by memory when there is not. Is in some real sense the reason this engine works.",
    tactics: "Will not be left out of dialogue. Will write down your line whether you said it well or not.",
    marginalia: "I am not a character. I am a chronicler. — Linzi"
  },
  {
    id: "stag-lord",
    name: "The Stag Lord",
    short: "lord·portrait",
    short_descriptor: "antagonist · uncrowned",
    subtitle: "King of nothing, by his own coronation.",
    role: "Antagonist",
    encounteredAt: "Not yet",
    body: "Nobody who has come back has said. The bandits speak his name like a prayer they don't believe in. The toll-keepers of Odrun pretend the name does not affect them.",
    tactics: "Unknown. Pays in salt. Does not pay in promises. Does not lose deserters.",
    marginalia: "I do not think this is a man. I think it is a man with company."
  },
  {
    id: "oleg",
    name: "Oleg Leveton",
    short: "O·portrait",
    short_descriptor: "trader · uneasy",
    subtitle: "Of his post, by his post.",
    role: "Ally",
    encounteredAt: "Oleg's Trading Post",
    body: "Trader by trade and by temperament. Would have been a miller if the river had favoured him. The eastern wall has more spear-marks than the others because Oleg stands at the eastern wall.",
    tactics: "Will accept your help. Will not thank you for it. Will, much later, pretend it was his idea.",
    marginalia: "Oleg pretends the hating is what saved him. — Svetlana"
  },
  { id: "unk-p1", name: "??????", short: "?", short_descriptor: "the toll-keeper of Tines", unknown: true },
  { id: "unk-p2", name: "??????", short: "?", short_descriptor: "the singer at Saltwell", unknown: true },
];

const LORE = [
  {
    id: "stolen-marches",
    name: "The Stolen Marches",
    short: "borderland",
    short_descriptor: "borderland · contested",
    subtitle: "Where the maps and the road disagree.",
    body: "A borderland between Brevoy and the older claims of the south. So named because every kingdom that has held it has lost it, and because every map drawn of it has been drawn by someone who has been lied to.",
    tactics: "The marches do not respect a single law. Travel by post-road if you must travel by anything. Do not trust a shrine that has not been re-built in the last twenty years. Trust no inn that has not been lit in seven.",
    marginalia: "The marches are not stolen from anyone. The marches steal."
  },
  {
    id: "fate-die",
    name: "The Fate Die",
    short: "die",
    short_descriptor: "system · mechanic",
    subtitle: "What the chronicle hands you when you have already failed.",
    body: "A bone die, kept by the chronicle and given to each hero at the start of every chapter. Spend a fate die to ask the world for a complication, not a setback. The complication is binding. The setback would have been worse.",
    tactics: "Spend early. Spend on the road, not in the den. Fate dice do not refresh between chapters. Linzi keeps the count.",
    marginalia: "I have spent three. I had five. The other two were Mira's. — Cassian"
  },
  {
    id: "road-wardens",
    name: "Road Wardens of Restov",
    short: "order",
    short_descriptor: "knightly order",
    subtitle: "Sword-companies of the southern post-roads.",
    body: "The Wardens keep the post-roads between Restov and Tines passable, by patrol, by stone, and by occasional necessary brutality. They are sworn not to draw a blade in a Warden hall. Cassian is sworn. Cassian has not drawn a blade in a Warden hall.",
    tactics: "Will hire competent strangers if their own roster is short. Pay is fair. Loyalty is not requested. Honour is — and is verified.",
    marginalia: "I have been a Warden for seven years. I have served the order for one of them. — Cassian"
  },
  {
    id: "lanternrest",
    name: "The Lanternrest, history of",
    short: "inn",
    short_descriptor: "place · history",
    subtitle: "Why the lantern is not lit.",
    body: "The Lanternrest was, before the war of the Stolen Marches, an inn for couriers and clerics travelling the south road to Odrun. After the war the inn was kept by a man named Hessan, who is not known to have died. The lantern over the door went unlit on the seventh evening of the third Gozran following, and has not been lit since.",
    tactics: "Approach in the afternoon. Make camp in the courtyard. Do not enter the eastern hallway. Do not light the lantern.",
    marginalia: "Hessan is the part to be afraid of."
  },
];

Object.assign(window, { ScreenBestiary, BestiaryEntry, BESTIARY, PEOPLE, LORE });
