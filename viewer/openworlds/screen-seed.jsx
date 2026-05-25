/* Screen: World Seed — campaign foundational parameters */

function ScreenSeed({ onNavigate, state, setState }) {
  const [seed, setSeed] = React.useState({
    system: "Pathfinder 1e",
    tone: "Heroic",
    difficulty: "Standard",
    gmStrictness: "Standard",
    permadeath: false,
    metaCurrency: true,
    chronicleVoice: "First-person plural",
    aiNarration: "Florid",
  });
  const toast = window.useToast ? window.useToast() : (() => {});

  const update = (k, v) => setSeed({ ...seed, [k]: v });

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "1fr 1.1fr", gap: 14, padding: 14, minHeight: 0 }}>

      {/* LEFT — seed card */}
      <Panel framed style={{ padding: 28, overflow: "auto" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>Of this chronicle's</div>
        <h1 className="h1" style={{ fontSize: 28 }}>World Seed</h1>
        <div className="hand" style={{ fontSize: 16, color: "var(--ink-700)", marginTop: 4 }}>
          The seed is what the chronicle was sown with. Change it lightly; it remembers.
        </div>

        <Divider />

        {/* Quote / seed identity */}
        <div style={{
          padding: 20,
          background: "linear-gradient(180deg, var(--w-100), var(--w-300))",
          color: "var(--p-200)",
          boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 0 3px var(--w-200), inset 0 0 0 4px var(--b-500)",
          position: "relative",
        }}>
          <div style={{ position: "absolute", top: 6, left: 8, fontSize: 28, color: "var(--crimson-bright)", fontFamily: "var(--f-display)", lineHeight: 1 }}>"</div>
          <div className="body" style={{ fontSize: 17, fontStyle: "italic", lineHeight: 1.55, paddingLeft: 22, color: "var(--p-100)" }}>
            The marches do not respect a single law. Travel by post-road if you must travel by anything.
          </div>
          <div className="hand" style={{ marginTop: 8, paddingLeft: 22, fontSize: 13, color: "var(--gold-glow)" }}>
            — found in a Restov coachman's pocket, undated
          </div>
        </div>

        <Divider />

        <SectionTitle ordinal="·">The Quickening</SectionTitle>
        <div className="body" style={{ fontSize: 15 }}>
          <p>
            This chronicle is sown for a heroic register in the post-tabletop tradition of Brevoy and the Stolen Marches. The reading voice is communal — we, when we walked; we, when we found. Decisions are remembered. Failure is rarely permanent but always written down. Salt and silence have meanings the rules will not state.
          </p>
        </div>

        <Divider />

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <StatLine k="Seeded" v="27 Pharast, 4717" />
          <StatLine k="By" v="Linzi (chronicler)" />
          <StatLine k="Pattern" v="9b3d-2f1e-77ac" />
          <StatLine k="Engine" v="Chronicle II" />
        </div>

        <Divider />

        <SectionTitle>Re-seed</SectionTitle>
        <div className="hand muted" style={{ fontSize: 13 }}>
          A new seed begins a new chronicle. The party's standing is reset; their names are not.
        </div>
        <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
          <BrassButton tone="ghost" size="sm">Copy pattern</BrassButton>
          <BrassButton tone="crimson" size="sm" onClick={() => toast({ kind: "danger", title: "Reseed locked", body: "The chronicle protects itself from accidental wipes. Hold ⌘+Shift on this button to confirm." })}>Reseed</BrassButton>
        </div>
      </Panel>

      {/* RIGHT — tunables */}
      <Panel framed style={{ padding: 28, overflow: "auto" }}>
        <SectionTitle ordinal="I.">System</SectionTitle>
        <SeedSelect
          value={seed.system}
          options={["Pathfinder 1e", "Pathfinder 2e", "D&D 5e", "Free Form (No System)"]}
          onChange={(v) => update("system", v)}
        />

        <Divider />

        <SectionTitle ordinal="II.">Tone</SectionTitle>
        <SeedRadio
          value={seed.tone}
          onChange={(v) => update("tone", v)}
          options={[
            { value: "Heroic", label: "Heroic", note: "Gold, royal blue, candlelight. Players are who they say they are." },
            { value: "Grim", label: "Grim", note: "Crimson and walnut. Successes are uncomfortable. Most are." },
            { value: "Picaresque", label: "Picaresque", note: "The party will lie. The chronicle will pretend not to notice." },
            { value: "Mythic", label: "Mythic", note: "Brass and oxblood. The land is older than the law and is winning." },
          ]}
        />

        <Divider />

        <SectionTitle ordinal="III.">Difficulty</SectionTitle>
        <SeedRadio
          value={seed.difficulty}
          onChange={(v) => update("difficulty", v)}
          options={[
            { value: "Story", label: "Story", note: "Combat is brief. The chronicle is the point." },
            { value: "Standard", label: "Standard", note: "The rules as written. The road as expected." },
            { value: "Hard", label: "Hard", note: "The enemies have read the rules and are using them." },
            { value: "Unfair", label: "Unfair", note: "The chronicle hopes you are taking notes." },
          ]}
        />

        <Divider />

        <SectionTitle ordinal="IV.">AI Game Master</SectionTitle>
        <SeedRow label="GM strictness" value={seed.gmStrictness}>
          <SeedSelect
            value={seed.gmStrictness}
            options={["Permissive", "Standard", "Strict", "Pedantic"]}
            onChange={(v) => update("gmStrictness", v)}
            inline
          />
        </SeedRow>
        <SeedRow label="Narration register" value={seed.aiNarration}>
          <SeedSelect
            value={seed.aiNarration}
            options={["Terse", "Balanced", "Florid", "Almost-poetic"]}
            onChange={(v) => update("aiNarration", v)}
            inline
          />
        </SeedRow>
        <SeedRow label="Chronicle voice" value={seed.chronicleVoice}>
          <SeedSelect
            value={seed.chronicleVoice}
            options={["First-person singular", "First-person plural", "Second person", "Third-person omniscient", "Third-person close"]}
            onChange={(v) => update("chronicleVoice", v)}
            inline
          />
        </SeedRow>

        <Divider />

        <SectionTitle ordinal="V.">World Rules</SectionTitle>
        <SeedToggle
          label="Permadeath"
          detail="When a hero dies, they stay dead. The chronicle continues without them."
          value={seed.permadeath}
          onChange={(v) => update("permadeath", v)}
        />
        <SeedToggle
          label="Fate dice"
          detail="Each hero starts each act with a fate die. Spend to ask the world for a complication, not a setback."
          value={seed.metaCurrency}
          onChange={(v) => update("metaCurrency", v)}
        />
        <SeedToggle
          label="Item destruction"
          detail="Weapons and armour wear with use. Forge it again or find it again."
          value={false}
        />
        <SeedToggle
          label="Anachronism"
          detail="The chronicle permits a small number of out-of-period words for the sake of clarity."
          value={true}
        />

        <Divider />

        <SectionTitle ordinal="VI.">Chronicler's notes</SectionTitle>
        <textarea
          defaultValue="Linzi keeps the book. Do not edit her entries even when they are wrong. Especially when they are wrong."
          style={{
            width: "100%", minHeight: 90, padding: 12,
            background: "rgba(255,250,230,0.5)",
            border: 0,
            boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 2px 4px rgba(80,50,20,0.15)",
            fontFamily: "var(--f-hand)",
            fontSize: 15,
            fontStyle: "italic",
            color: "var(--ink-700)",
            resize: "vertical",
          }}
        />

        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 14 }}>
          <BrassButton onClick={() => toast({ kind: "rest", title: "Seed updated", body: "The chronicle accepts the change." })}>Sow the change</BrassButton>
        </div>
      </Panel>
    </div>
  );
}

function SeedSelect({ value, options, onChange, inline }) {
  const [open, setOpen] = React.useState(false);
  return (
    <div style={{ position: "relative" }}>
      <button onClick={() => setOpen(!open)} style={{
        width: "100%",
        padding: inline ? "6px 12px" : "10px 14px",
        background: "rgba(255,250,230,0.5)",
        boxShadow: "inset 0 0 0 1px var(--b-500)",
        fontFamily: "var(--f-body)",
        fontSize: inline ? 14 : 16,
        color: "var(--ink-800)",
        textAlign: "left",
        display: "flex", justifyContent: "space-between", alignItems: "center",
        cursor: "pointer",
      }}>
        <span>{value}</span>
        <span style={{ color: "var(--b-500)", fontSize: 10 }}>▾</span>
      </button>
      {open && (
        <div style={{
          position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0,
          background: "linear-gradient(180deg, var(--p-100), var(--p-200))",
          boxShadow: "inset 0 0 0 1px var(--b-500), 0 8px 20px rgba(0,0,0,0.3)",
          zIndex: 10,
        }}>
          {options.map((o) => (
            <button key={o} onClick={() => { onChange(o); setOpen(false); }} style={{
              width: "100%",
              padding: "8px 14px",
              background: o === value ? "rgba(176,141,87,0.18)" : "transparent",
              fontFamily: "var(--f-body)",
              fontSize: inline ? 14 : 16,
              color: "var(--ink-800)",
              textAlign: "left",
              cursor: "pointer",
              borderBottom: "1px solid rgba(140,100,60,0.18)",
            }}>{o}</button>
          ))}
        </div>
      )}
    </div>
  );
}

function SeedRadio({ value, onChange, options }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
      {options.map((o) => (
        <button key={o.value} onClick={() => onChange(o.value)} style={{
          padding: 12,
          textAlign: "left",
          background: value === o.value
            ? "linear-gradient(180deg, var(--p-100), var(--p-200))"
            : "rgba(176,141,87,0.06)",
          boxShadow: value === o.value
            ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
            : "inset 0 0 0 1px rgba(140,100,60,0.3)",
          cursor: "pointer",
        }}>
          <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--ink-900)" }}>
            {o.label}
          </div>
          {o.note && <div className="hand muted" style={{ fontSize: 12, marginTop: 4 }}>{o.note}</div>}
        </button>
      ))}
    </div>
  );
}

function SeedRow({ label, children }) {
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "180px 1fr", gap: 14, alignItems: "center",
      padding: "8px 0",
      borderBottom: "1px solid rgba(140,100,60,0.2)",
    }}>
      <span className="eyebrow" style={{ fontSize: 10 }}>{label}</span>
      <div>{children}</div>
    </div>
  );
}

function SeedToggle({ label, detail, value, onChange }) {
  return (
    <button onClick={() => onChange && onChange(!value)} style={{
      display: "grid", gridTemplateColumns: "1fr 44px", gap: 14, alignItems: "center",
      width: "100%",
      padding: "10px 0",
      background: "transparent",
      borderBottom: "1px solid rgba(140,100,60,0.2)",
      cursor: "pointer",
      textAlign: "left",
    }}>
      <div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--ink-900)" }}>
          {label}
        </div>
        {detail && <div className="hand muted" style={{ fontSize: 13, marginTop: 2 }}>{detail}</div>}
      </div>
      <span style={{
        width: 44, height: 22,
        background: value ? "linear-gradient(180deg, var(--b-200), var(--b-500))" : "rgba(0,0,0,0.18)",
        boxShadow: value
          ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.5)"
          : "inset 0 0 0 1px rgba(80,50,20,0.45)",
        position: "relative",
        borderRadius: 12,
        transition: "all 180ms",
      }}>
        <span style={{
          position: "absolute", top: 2, left: value ? 24 : 2,
          width: 18, height: 18, borderRadius: "50%",
          background: value
            ? "radial-gradient(circle at 30% 30%, var(--p-100), var(--p-400))"
            : "radial-gradient(circle at 30% 30%, var(--p-200), var(--ink-600))",
          boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.6), 0 1px 2px rgba(0,0,0,0.3)",
          transition: "all 180ms",
        }} />
      </span>
    </button>
  );
}

Object.assign(window, { ScreenSeed, SeedSelect, SeedRadio, SeedRow, SeedToggle });
