/* Screen: Character Creation — wizard flow */

function ScreenCreate({ onNavigate, state, setState }) {
  const [step, setStep] = React.useState(0);
  const [hero, setHero] = React.useState({
    name: "",
    race: "human",
    class: "magus",
    background: "wanderer",
    portrait: 0,
    alignment: "neutral-good",
    abilities: { str: 10, dex: 10, con: 10, int: 10, wis: 10, cha: 10 },
    points: 20,
  });

  const steps = [
    { id: "race", label: "Lineage" },
    { id: "class", label: "Calling" },
    { id: "background", label: "Past" },
    { id: "abilities", label: "Aptitudes" },
    { id: "portrait", label: "Face" },
    { id: "name", label: "Name" },
    { id: "review", label: "Bind" },
  ];

  const next = () => setStep(Math.min(steps.length - 1, step + 1));
  const prev = () => setStep(Math.max(0, step - 1));

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "240px 1fr 280px", gap: 14, padding: 14 }}>

      {/* LEFT — wizard steps */}
      <Panel framed style={{ padding: 22, display: "flex", flexDirection: "column" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>Binding of a</div>
        <h2 className="h1" style={{ fontSize: 22 }}>New Hero</h2>
        <Divider />
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {steps.map((s, i) => (
            <button key={s.id} onClick={() => setStep(i)} style={{
              display: "grid", gridTemplateColumns: "28px 1fr auto", gap: 8, alignItems: "center",
              padding: "10px 12px",
              textAlign: "left",
              background: i === step ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
              boxShadow: i === step
                ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
                : "inset 0 -1px 0 rgba(140,100,60,0.2)",
              cursor: "pointer",
            }}>
              <span style={{
                fontFamily: "var(--f-hand)", fontStyle: "italic", color: "var(--crimson)",
                fontSize: 20, textAlign: "center",
              }}>{toRomanCC(i + 1)}</span>
              <span style={{
                fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.22em", textTransform: "uppercase",
                color: i === step ? "var(--ink-900)" : "var(--ink-700)",
              }}>{s.label}</span>
              {i < step && <span style={{ color: "var(--emerald)", fontSize: 12 }}>✓</span>}
            </button>
          ))}
        </div>

        <div style={{ flex: 1 }} />

        <Divider />
        <div className="hand" style={{ fontSize: 13, color: "var(--ink-700)" }}>
          Every step changes the next. The chronicle remembers them all.
        </div>
      </Panel>

      {/* CENTER — step content */}
      <Panel framed style={{ padding: 28, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ flex: 1, overflow: "auto" }}>
          {step === 0 && <StepRace hero={hero} setHero={setHero} />}
          {step === 1 && <StepClass hero={hero} setHero={setHero} />}
          {step === 2 && <StepBackground hero={hero} setHero={setHero} />}
          {step === 3 && <StepAbilities hero={hero} setHero={setHero} />}
          {step === 4 && <StepPortrait hero={hero} setHero={setHero} />}
          {step === 5 && <StepName hero={hero} setHero={setHero} />}
          {step === 6 && <StepReview hero={hero} setHero={setHero} />}
        </div>

        {/* Footer nav */}
        <div style={{
          marginTop: 18, paddingTop: 18,
          borderTop: "1px solid rgba(140,100,60,0.3)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <BrassButton tone="ghost" onClick={prev} disabled={step === 0}>← Back</BrassButton>
          <span className="muted body-sm">Step {step + 1} of {steps.length}</span>
          {step < steps.length - 1 ? (
            <BrassButton onClick={next}>Continue →</BrassButton>
          ) : (
            <BrassButton onClick={() => onNavigate("table")} tone="crimson">Bind the hero</BrassButton>
          )}
        </div>
      </Panel>

      {/* RIGHT — live hero summary */}
      <Panel framed style={{ padding: 22, overflow: "auto" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>What you have made</div>
        <h2 className="h1" style={{ fontSize: 18, marginTop: 2 }}>
          {hero.name || <span className="hand" style={{ fontStyle: "italic", color: "var(--ink-600)", fontSize: 16 }}>Unnamed</span>}
        </h2>
        <div className="hand" style={{ fontSize: 13, color: "var(--ink-700)" }}>
          {RACES[hero.race]?.name} · {CLASSES[hero.class]?.name}
        </div>

        <Placeholder label={`portrait · ${hero.portrait}`} h={160} framed style={{ width: "100%", marginTop: 12 }} />

        <Divider />

        <div className="eyebrow">Aptitudes</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6, marginTop: 8 }}>
          {["str","dex","con","int","wis","cha"].map((a) => {
            const total = hero.abilities[a] + (RACES[hero.race]?.bonus?.[a] || 0);
            return (
              <div key={a} style={{
                padding: "6px 0", textAlign: "center",
                background: "linear-gradient(180deg, var(--p-100), var(--p-200))",
                boxShadow: "inset 0 0 0 1px var(--b-500)",
              }}>
                <div className="eyebrow" style={{ fontSize: 9 }}>{a.toUpperCase()}</div>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 16, color: "var(--ink-900)" }}>{total}</div>
              </div>
            );
          })}
        </div>

        <Divider />

        <div className="eyebrow">Background</div>
        <div className="body-sm" style={{ marginTop: 4, color: "var(--ink-800)" }}>{BACKGROUNDS[hero.background]?.name}</div>
        <div className="hand muted" style={{ fontSize: 12, marginTop: 2 }}>{BACKGROUNDS[hero.background]?.brief}</div>

        <Divider />

        <div className="eyebrow">Alignment</div>
        <div className="body-sm" style={{ marginTop: 4, color: "var(--ink-800)", textTransform: "capitalize" }}>{hero.alignment.replace("-", " ")}</div>

        <Divider />

        <div className="eyebrow">Points remaining</div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 22, color: hero.points === 0 ? "var(--emerald)" : "var(--crimson)" }}>
          {hero.points}
        </div>
      </Panel>
    </div>
  );
}

/* ===== STEPS ===== */

function StepRace({ hero, setHero }) {
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--crimson)" }}>I. Of Lineage</div>
      <h1 className="h1">What blood do you carry?</h1>
      <p className="body muted" style={{ marginTop: 4 }}>
        Lineage decides not your worth but your starting reach. Most heroes find themselves regardless.
      </p>
      <Divider />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        {Object.entries(RACES).map(([id, r]) => (
          <SelectCard
            key={id}
            selected={hero.race === id}
            onClick={() => setHero({ ...hero, race: id })}
            label={r.name}
            sublabel={r.size + " · " + r.life}
            portrait={r.glyph}
            body={r.body}
            tags={Object.entries(r.bonus || {}).map(([k, v]) => `${v > 0 ? "+" : ""}${v} ${k.toUpperCase()}`)}
          />
        ))}
      </div>
    </div>
  );
}

function StepClass({ hero, setHero }) {
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--crimson)" }}>II. Of Calling</div>
      <h1 className="h1">What discipline keeps you?</h1>
      <p className="body muted" style={{ marginTop: 4 }}>
        Your calling chooses what you wake up doing every morning. It does not choose what wakes you.
      </p>
      <Divider />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        {Object.entries(CLASSES).map(([id, c]) => (
          <SelectCard
            key={id}
            selected={hero.class === id}
            onClick={() => setHero({ ...hero, class: id })}
            label={c.name}
            sublabel={c.role}
            portrait={c.glyph}
            body={c.body}
            tags={c.tags}
          />
        ))}
      </div>
    </div>
  );
}

function StepBackground({ hero, setHero }) {
  const ALIGNMENTS = [
    "lawful-good", "neutral-good", "chaotic-good",
    "lawful-neutral", "true-neutral", "chaotic-neutral",
    "lawful-evil", "neutral-evil", "chaotic-evil",
  ];
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--crimson)" }}>III. Of the Past</div>
      <h1 className="h1">What did you do before?</h1>
      <Divider />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
        {Object.entries(BACKGROUNDS).map(([id, b]) => (
          <button key={id} onClick={() => setHero({ ...hero, background: id })} style={{
            textAlign: "left",
            padding: 14,
            background: hero.background === id ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "rgba(176,141,87,0.06)",
            boxShadow: hero.background === id
              ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
              : "inset 0 0 0 1px rgba(140,100,60,0.3)",
            cursor: "pointer",
          }}>
            <div style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
              {b.name}
            </div>
            <div className="body-sm muted" style={{ marginTop: 4 }}>{b.brief}</div>
            <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
              {b.skills.map((s) => <Pill key={s}>{s}</Pill>)}
            </div>
          </button>
        ))}
      </div>

      <Divider />

      <SectionTitle>Alignment</SectionTitle>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 4 }}>
        {ALIGNMENTS.map((a) => (
          <button key={a} onClick={() => setHero({ ...hero, alignment: a })} style={{
            padding: "10px 8px",
            background: hero.alignment === a ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "transparent",
            color: hero.alignment === a ? "var(--w-300)" : "var(--ink-700)",
            boxShadow: hero.alignment === a
              ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)"
              : "inset 0 0 0 1px rgba(140,100,60,0.3)",
            cursor: "pointer",
            fontFamily: "var(--f-display)",
            fontSize: 10,
            letterSpacing: "0.16em",
            textTransform: "uppercase",
            textAlign: "center",
          }}>{a.replace("-", " ")}</button>
        ))}
      </div>
    </div>
  );
}

function StepAbilities({ hero, setHero }) {
  const adjust = (key, delta) => {
    const cur = hero.abilities[key];
    const target = cur + delta;
    if (target < 8 || target > 18) return;
    const cost = abilityCost(target) - abilityCost(cur);
    if (hero.points - cost < 0) return;
    setHero({
      ...hero,
      abilities: { ...hero.abilities, [key]: target },
      points: hero.points - cost,
    });
  };
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--crimson)" }}>IV. Of Aptitudes</div>
      <h1 className="h1">Distribute your gifts.</h1>
      <p className="body muted" style={{ marginTop: 4 }}>
        Twenty points, six gifts. Higher scores cost more. Pathfinder rules.
      </p>
      <Divider />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        {["str", "dex", "con", "int", "wis", "cha"].map((k) => {
          const racial = RACES[hero.race]?.bonus?.[k] || 0;
          const total = hero.abilities[k] + racial;
          const mod = Math.floor((total - 10) / 2);
          return (
            <div key={k} style={{
              display: "grid", gridTemplateColumns: "60px 1fr auto auto auto", gap: 10, alignItems: "center",
              padding: "10px 14px",
              background: "rgba(176,141,87,0.08)",
              boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
            }}>
              <div>
                <div className="eyebrow" style={{ fontSize: 10 }}>{k.toUpperCase()}</div>
                <div className="hand muted" style={{ fontSize: 11 }}>{ABILITY_LABEL[k]}</div>
              </div>
              <div className="muted body-sm">
                {hero.abilities[k]} {racial !== 0 && (<span style={{ color: racial > 0 ? "var(--emerald)" : "var(--crimson)" }}>{racial > 0 ? "+" : ""}{racial}</span>)}
              </div>
              <button onClick={() => adjust(k, -1)} className="icon-btn" style={{ width: 28, height: 28 }}>−</button>
              <div style={{ width: 50, textAlign: "center" }}>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 22, color: "var(--ink-900)" }}>{total}</div>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 10, color: mod >= 0 ? "var(--emerald)" : "var(--crimson)" }}>
                  {mod >= 0 ? "+" : ""}{mod}
                </div>
              </div>
              <button onClick={() => adjust(k, +1)} className="icon-btn" style={{ width: 28, height: 28 }}>+</button>
            </div>
          );
        })}
      </div>

      <Divider />

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div>
          <div className="eyebrow">Points remaining</div>
          <div style={{ fontFamily: "var(--f-display)", fontSize: 28, color: hero.points === 0 ? "var(--emerald)" : "var(--ink-900)" }}>
            {hero.points}<span className="muted" style={{ fontSize: 14 }}>/20</span>
          </div>
        </div>
        <BrassButton tone="ghost" size="sm" onClick={() => setHero({
          ...hero,
          abilities: { str: 10, dex: 10, con: 10, int: 10, wis: 10, cha: 10 },
          points: 20,
        })}>Reset</BrassButton>
      </div>
    </div>
  );
}

function StepPortrait({ hero, setHero }) {
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--crimson)" }}>V. Of Face</div>
      <h1 className="h1">What will the chronicle remember of you?</h1>
      <Divider />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 10 }}>
        {Array.from({ length: 12 }).map((_, i) => (
          <button key={i} onClick={() => setHero({ ...hero, portrait: i })} style={{
            padding: 4,
            background: hero.portrait === i ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
            boxShadow: hero.portrait === i
              ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400), 0 0 16px -2px var(--gold-glow)"
              : "inset 0 0 0 1px rgba(140,100,60,0.3)",
            cursor: "pointer",
          }}>
            <Placeholder label={`portrait ${i + 1}`} w="100%" h={140} framed />
          </button>
        ))}
      </div>

      <Divider />
      <div className="hand muted">
        Bring your own — drop a PNG onto any frame to replace it.
      </div>
    </div>
  );
}

function StepName({ hero, setHero }) {
  const [biography, setBiography] = React.useState("");
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--crimson)" }}>VI. Of Name</div>
      <h1 className="h1">What shall the chronicle call you?</h1>
      <Divider />

      <div className="eyebrow" style={{ marginBottom: 6 }}>Name</div>
      <input
        value={hero.name}
        onChange={(e) => setHero({ ...hero, name: e.target.value })}
        placeholder="e.g. Eira of the Hollow Reach"
        style={{ ...window.inkInput, fontSize: 22, fontFamily: "var(--f-display)", letterSpacing: "0.06em" }}
        autoFocus
      />

      <div className="eyebrow" style={{ marginTop: 18, marginBottom: 6 }}>Family / House (optional)</div>
      <input
        placeholder="e.g. House of the Three Bells"
        style={{ ...window.inkInput, fontSize: 16 }}
      />

      <div className="eyebrow" style={{ marginTop: 18, marginBottom: 6 }}>Biography</div>
      <textarea
        value={biography}
        onChange={(e) => setBiography(e.target.value)}
        placeholder="A few lines for the chronicle. What you have done. What you are still doing. What you intend never to do."
        style={{ ...window.inkInput, fontSize: 15, fontFamily: "var(--f-body)", height: 140, resize: "vertical", lineHeight: 1.5 }}
      />

      <div className="hand muted" style={{ marginTop: 8, fontSize: 13 }}>
        The chronicle will read what you write. It will also remember what you didn't.
      </div>
    </div>
  );
}

function StepReview({ hero }) {
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--crimson)" }}>VII. The Binding</div>
      <h1 className="h1">{hero.name || "Unnamed"}</h1>
      <div className="hand" style={{ fontSize: 16, color: "var(--ink-700)", marginTop: 2 }}>
        {RACES[hero.race]?.name} · {CLASSES[hero.class]?.name} · {BACKGROUNDS[hero.background]?.name}
      </div>
      <Divider />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
        <div>
          <Placeholder label={`portrait · ${hero.portrait}`} h={240} framed style={{ width: "100%" }} />
          <div style={{ marginTop: 12, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
            <StatLine k="Alignment" v={hero.alignment.replace("-", " ")} />
            <StatLine k="Level" v="1" />
            <StatLine k="HP" v={CLASSES[hero.class]?.hp || 8} />
            <StatLine k="AC" v="10 + DEX" />
          </div>
        </div>
        <div>
          <SectionTitle>Aptitudes</SectionTitle>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6 }}>
            {["str","dex","con","int","wis","cha"].map((a) => {
              const total = hero.abilities[a] + (RACES[hero.race]?.bonus?.[a] || 0);
              const mod = Math.floor((total - 10) / 2);
              return (
                <div key={a} style={{
                  padding: "8px 0", textAlign: "center",
                  background: "linear-gradient(180deg, var(--p-100), var(--p-200))",
                  boxShadow: "inset 0 0 0 1px var(--b-500)",
                }}>
                  <div className="eyebrow" style={{ fontSize: 9 }}>{a.toUpperCase()}</div>
                  <div style={{ fontFamily: "var(--f-display)", fontSize: 18, color: "var(--ink-900)" }}>{total}</div>
                  <div style={{ fontFamily: "var(--f-display)", fontSize: 11, color: mod >= 0 ? "var(--emerald)" : "var(--crimson)" }}>
                    {mod >= 0 ? "+" : ""}{mod}
                  </div>
                </div>
              );
            })}
          </div>

          <Divider />
          <SectionTitle>What you start with</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {(CLASSES[hero.class]?.kit || []).map((it, i) => (
              <div key={i} className="body-sm" style={{ display: "flex", justifyContent: "space-between", padding: "4px 8px", boxShadow: "inset 0 -1px 0 rgba(140,100,60,0.2)" }}>
                <span style={{ color: "var(--ink-800)" }}>{it.name}</span>
                <span className="muted" style={{ fontFamily: "var(--f-mono)", fontSize: 11 }}>{it.qty || "—"}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <Divider />
      <p className="body dropcap">
        When you press Bind, the chronicle records this hero. The choices below this line will be remembered by every road taken hereafter. There is no penalty for hesitation; the parchment is patient.
      </p>
    </div>
  );
}

function SelectCard({ selected, onClick, label, sublabel, portrait, body, tags }) {
  return (
    <button onClick={onClick} style={{
      display: "grid", gridTemplateColumns: "80px 1fr", gap: 12,
      padding: 14,
      textAlign: "left",
      background: selected
        ? "linear-gradient(180deg, var(--p-100), var(--p-200))"
        : "rgba(176,141,87,0.06)",
      boxShadow: selected
        ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400), 0 0 20px -4px var(--gold-glow)"
        : "inset 0 0 0 1px rgba(140,100,60,0.3)",
      cursor: "pointer",
      transition: "all 140ms",
    }}>
      <Placeholder label={portrait} w={80} h={96} framed />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 14, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
          {label}
        </div>
        <div className="hand muted" style={{ fontSize: 12 }}>{sublabel}</div>
        <div className="body-sm" style={{ color: "var(--ink-700)", marginTop: 6, lineHeight: 1.4 }}>{body}</div>
        {tags && tags.length > 0 && (
          <div style={{ marginTop: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
            {tags.map((t) => <Pill key={t}>{t}</Pill>)}
          </div>
        )}
      </div>
    </button>
  );
}

function abilityCost(score) {
  const cost = { 8: -2, 9: -1, 10: 0, 11: 1, 12: 2, 13: 3, 14: 5, 15: 7, 16: 10, 17: 13, 18: 17 };
  return cost[score] ?? 0;
}

function toRomanCC(n) {
  return ["I","II","III","IV","V","VI","VII","VIII"][n - 1] || "" + n;
}

const ABILITY_LABEL = {
  str: "Strength of arm",
  dex: "Of hand and foot",
  con: "Endurance",
  int: "Reasoning",
  wis: "Perceptions",
  cha: "Of word and bearing",
};

const RACES = {
  human: {
    name: "Human",
    size: "Medium",
    life: "70 years",
    glyph: "human · sketch",
    body: "Adaptive, ambitious, and overrepresented in chronicles. Heroes from the south road to the Old Hills count themselves human by habit.",
    bonus: {},
  },
  halfling: {
    name: "Halfling",
    size: "Small",
    life: "100 years",
    glyph: "halfling · sketch",
    body: "Footloose, footsure, and oddly hard to startle. Scribes, scouts, and second daughters of failed dynasties.",
    bonus: { dex: 2, str: -2, cha: 2 },
  },
  dwarf: {
    name: "Dwarf",
    size: "Medium",
    life: "350 years",
    glyph: "dwarf · sketch",
    body: "Slow to leave, slower to anger, slowest to forget. Stonecunning, ironwise, and oddly reliable.",
    bonus: { con: 2, wis: 2, cha: -2 },
  },
  elf: {
    name: "Elf",
    size: "Medium",
    life: "750 years",
    glyph: "elf · sketch",
    body: "Older than several wars they were not in. Sharp eyes, sharp words, sharper at the wrong times.",
    bonus: { dex: 2, int: 2, con: -2 },
  },
  half: {
    name: "Half-Elf",
    size: "Medium",
    life: "180 years",
    glyph: "half-elf",
    body: "Sufficient to neither lineage to be considered theirs by either. Many things, often very well.",
    bonus: { cha: 2 },
  },
  tiefling: {
    name: "Tiefling",
    size: "Medium",
    life: "120 years",
    glyph: "tiefling",
    body: "Touched by something hot once. Counts the chambers of the soul on three hands.",
    bonus: { dex: 2, int: 2, cha: -2 },
  },
};

const CLASSES = {
  magus: {
    name: "Magus",
    role: "Sword and spell",
    glyph: "magus · sigil",
    body: "Weaves a single touched spell through a held blade. Best when nobody knows whether the cut or the cantrip arrives first.",
    tags: ["d10 HP", "spell combat", "longsword"],
    hp: 10,
    kit: [
      { name: "Longsword", qty: 1 },
      { name: "Studded leather", qty: 1 },
      { name: "Spellbook (5 spells)", qty: 1 },
      { name: "Travel rations", qty: 4 },
    ],
  },
  bard: {
    name: "Bard",
    role: "Sword, song, and tongue",
    glyph: "bard · sigil",
    body: "Carries a chronicle and a rapier; uses each more often than the other. The party will follow you because you are loudest.",
    tags: ["d8 HP", "performance", "rapier"],
    hp: 8,
    kit: [
      { name: "Rapier", qty: 1 },
      { name: "Lute", qty: 1 },
      { name: "Knowledge of three songs", qty: 3 },
      { name: "Bandages", qty: 2 },
    ],
  },
  fighter: {
    name: "Fighter",
    role: "Steel",
    glyph: "fighter · sigil",
    body: "Trained until the question of whether to fight is shorter than the answer of how. The doors of inns do not survive you.",
    tags: ["d10 HP", "all martial", "any armour"],
    hp: 10,
    kit: [
      { name: "Greataxe", qty: 1 },
      { name: "Chainmail", qty: 1 },
      { name: "Iron rations", qty: 6 },
    ],
  },
  cleric: {
    name: "Cleric",
    role: "Sworn and channeling",
    glyph: "cleric · sigil",
    body: "Tied to a god by oath, debt, or unconcluded argument. Keeps the company alive by negotiating with the dying on a god's behalf.",
    tags: ["d8 HP", "channels", "heavy armour"],
    hp: 8,
    kit: [
      { name: "Warhammer", qty: 1 },
      { name: "Shield, holy", qty: 1 },
      { name: "Holy symbol", qty: 1 },
      { name: "Cure light wounds", qty: 3 },
    ],
  },
  rogue: {
    name: "Rogue",
    role: "First in, first out",
    glyph: "rogue · sigil",
    body: "Knows what the door is for, has a different way through it. Useful in the dark; useful in the meeting; useful in the kitchens.",
    tags: ["d8 HP", "sneak", "tools"],
    hp: 8,
    kit: [
      { name: "Shortsword + dagger", qty: 2 },
      { name: "Leather armour", qty: 1 },
      { name: "Thieves' tools", qty: 1 },
      { name: "Caltrops", qty: 1 },
    ],
  },
  ranger: {
    name: "Ranger",
    role: "Of the second-shallowest water",
    glyph: "ranger · sigil",
    body: "Has slept outside more nights than indoors. The road obeys you because you obey it first.",
    tags: ["d10 HP", "two-weapon", "tracker"],
    hp: 10,
    kit: [
      { name: "Longbow", qty: 1 },
      { name: "Twin shortswords", qty: 2 },
      { name: "Studded leather", qty: 1 },
      { name: "Snares", qty: 3 },
    ],
  },
};

const BACKGROUNDS = {
  wanderer: { name: "Wanderer", brief: "No address. Many addresses.", skills: ["Survival", "Knowledge (World)"] },
  scholar: { name: "Scholar", brief: "Of an institution, real or alleged.", skills: ["Knowledge (Arcana)", "Linguistics"] },
  noble: { name: "Disinherited Noble", brief: "Was someone. Is no longer.", skills: ["Persuasion", "Knowledge (Nobility)"] },
  soldier: { name: "Soldier", brief: "Served, returned, signed nothing.", skills: ["Athletics", "Intimidate"] },
  outlaw: { name: "Outlaw", brief: "Wanted in three districts; welcome in two.", skills: ["Stealth", "Trickery"] },
  pilgrim: { name: "Pilgrim", brief: "Going somewhere. Still going.", skills: ["Knowledge (Religion)", "Survival"] },
  artisan: { name: "Artisan", brief: "Made something good. Will make another.", skills: ["Craft", "Appraise"] },
  hedge: { name: "Hedge-witch", brief: "Taught by an older woman, since gone.", skills: ["Knowledge (Nature)", "Heal"] },
  spy: { name: "Spy", brief: "Was paid for nine years to be elsewhere.", skills: ["Stealth", "Persuasion"] },
};

Object.assign(window, { ScreenCreate, RACES, CLASSES, BACKGROUNDS, SelectCard, abilityCost });
