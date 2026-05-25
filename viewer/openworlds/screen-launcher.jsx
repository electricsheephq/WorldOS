/* Screen: Launcher / Worlds — campaign selection + new campaign */

function ScreenLauncher({ onNavigate, state, setState }) {
  const [selected, setSelected] = React.useState(state.activeCampaign || "odrun");
  const [showNew, setShowNew] = React.useState(false);

  const campaigns = state.campaigns;

  const onResume = () => {
    setState((s) => ({ ...s, activeCampaign: selected }));
    onNavigate("table");
  };

  return (
    <div className="screen" style={{ padding: "32px 40px 48px", minHeight: "100%" }}>
      <div style={{ display: "grid", gridTemplateColumns: "1.15fr 1fr", gap: 36, alignItems: "start" }}>

        {/* LEFT: Hero with title plate */}
        <div>
          <div style={{ position: "relative" }}>
            <Placeholder
              label="cover illustration · 16:9 · painted hero scene"
              h={360}
              framed
              style={{ width: "100%" }}
            />
            <div
              style={{
                position: "absolute",
                left: 24, bottom: -28,
                background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
                padding: "14px 28px",
                boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 0 0 4px var(--p-100), inset 0 0 0 5px var(--b-400), 0 8px 18px rgba(0,0,0,0.4)",
              }}
            >
              <div className="eyebrow" style={{ color: "var(--crimson)" }}>Open Worlds</div>
              <div style={{ fontFamily: "var(--f-display)", fontSize: 28, letterSpacing: "0.06em", color: "var(--ink-900)" }}>
                A Tabletop, Reawakened
              </div>
            </div>
          </div>

          <div style={{ marginTop: 56 }}>
            <SectionTitle ordinal="I.">Chronicles</SectionTitle>
            <div style={{ display: "grid", gap: 12 }}>
              {campaigns.map((c) => (
                <CampaignRow key={c.id} c={c} selected={selected === c.id} onSelect={() => setSelected(c.id)} />
              ))}
              <button
                onClick={() => onNavigate("create")}
                style={{
                  display: "flex", alignItems: "center", gap: 14,
                  padding: "16px 22px",
                  background: "transparent",
                  border: "1px dashed var(--b-500)",
                  color: "var(--ink-700)",
                  fontFamily: "var(--f-display)",
                  letterSpacing: "0.22em",
                  textTransform: "uppercase",
                  fontSize: 12,
                  cursor: "pointer",
                  transition: "all 140ms",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(176,141,87,0.1)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
              >
                <span style={{ fontSize: 22, color: "var(--crimson)", lineHeight: 1 }}>♕</span>
                <span>Forge a new hero</span>
              </button>
              <button
                onClick={() => setShowNew(true)}
                style={{
                  display: "flex", alignItems: "center", gap: 14,
                  padding: "18px 22px",
                  background: "transparent",
                  border: "1px dashed var(--b-500)",
                  color: "var(--ink-700)",
                  fontFamily: "var(--f-display)",
                  letterSpacing: "0.22em",
                  textTransform: "uppercase",
                  fontSize: 12,
                  cursor: "pointer",
                  transition: "all 140ms",
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(176,141,87,0.1)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
              >
                <span style={{ fontSize: 22, color: "var(--crimson)", lineHeight: 1 }}>✦</span>
                <span>Begin a new chronicle</span>
              </button>
            </div>
          </div>
        </div>

        {/* RIGHT: Selected detail */}
        <Panel framed style={{ padding: 0, overflow: "hidden" }}>
          {(() => {
            const c = campaigns.find((x) => x.id === selected) || campaigns[0];
            return (
              <div>
                {/* Top vignette with overlaid label */}
                <div style={{ position: "relative" }}>
                  <Placeholder label={`vignette · ${c.region.toLowerCase()}`} h={140} style={{ width: "100%", boxShadow: "none" }} />
                  <div style={{
                    position: "absolute", inset: 0,
                    background: "linear-gradient(180deg, transparent 40%, rgba(40, 25, 10, 0.85) 100%)",
                  }} />
                  <div style={{
                    position: "absolute", bottom: 14, left: 22, right: 22,
                    display: "flex", justifyContent: "space-between", alignItems: "flex-end",
                  }}>
                    <div>
                      <div className="eyebrow" style={{ color: "var(--gold-glow)", textShadow: "0 1px 2px rgba(0,0,0,0.6)" }}>
                        {c.system} · Chapter {c.chapter}
                      </div>
                      <div style={{ fontFamily: "var(--f-display)", fontSize: 22, color: "var(--p-100)", letterSpacing: "0.04em", marginTop: 2, textShadow: "0 1px 2px rgba(0,0,0,0.7)" }}>
                        {c.title}
                      </div>
                    </div>
                    <Pill tone="royal">{c.day.split(" · ")[0] || c.day}</Pill>
                  </div>
                </div>

                <div style={{ padding: "20px 28px 28px" }}>
                  <div className="hand" style={{ fontSize: 15, color: "var(--ink-700)" }}>{c.subtitle}</div>

                  {/* Stat strip with vertical brass dividers */}
                  <div style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr 1fr 1fr",
                    gap: 0,
                    marginTop: 18,
                    background: "rgba(176,141,87,0.06)",
                    boxShadow: "inset 0 0 0 1px var(--b-500)",
                  }}>
                    {[
                      { label: "Last sat", value: c.lastPlayed },
                      { label: "Sessions", value: c.sessions },
                      { label: "Heroes", value: c.party.length },
                      { label: "Region", value: c.region },
                    ].map((s, i) => (
                      <div key={s.label} style={{
                        padding: "10px 12px",
                        textAlign: "center",
                        boxShadow: i < 3 ? "inset -1px 0 0 rgba(140,100,60,0.3)" : "none",
                      }}>
                        <div className="eyebrow" style={{ fontSize: 9 }}>{s.label}</div>
                        <div style={{
                          fontFamily: "var(--f-display)", fontSize: 14, color: "var(--ink-900)", marginTop: 2,
                          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                        }}>{s.value}</div>
                      </div>
                    ))}
                  </div>

                  {/* Party row */}
                  <SectionTitle>The Party</SectionTitle>
                  <div style={{ display: "grid", gridTemplateColumns: `repeat(${c.party.length}, 1fr)`, gap: 8 }}>
                    {c.party.map((p, i) => (
                      <div key={i} style={{ textAlign: "center" }}>
                        <Placeholder label={p.short || "portrait"} w="100%" h={70} framed />
                        <div className="hand" style={{ fontSize: 12, marginTop: 4, color: "var(--ink-700)" }}>{p.name}</div>
                      </div>
                    ))}
                  </div>

                  {/* Recap with side sketch */}
                  <SectionTitle ordinal="·">Where last we stood</SectionTitle>
                  <div style={{ display: "grid", gridTemplateColumns: "100px 1fr", gap: 14, alignItems: "start" }}>
                    <div style={{ transform: "rotate(-2deg)" }}>
                      <Placeholder label="sketch · last scene" w={100} h={120} framed />
                    </div>
                    <p className="body dropcap" style={{ marginTop: 0, fontSize: 15 }}>
                      {c.recap}
                    </p>
                  </div>

                  {/* CTA bar */}
                  <div style={{
                    marginTop: 24, paddingTop: 16,
                    borderTop: "1px solid rgba(140,100,60,0.3)",
                    display: "flex", gap: 8,
                  }}>
                    <BrassButton onClick={onResume} size="lg" style={{ flex: 1 }}>
                      Resume Chronicle
                    </BrassButton>
                    <BrassButton tone="ghost" size="sm" onClick={() => onNavigate("character")}>Roster</BrassButton>
                    <BrassButton tone="ghost" size="sm" onClick={() => onNavigate("journal")}>Journal</BrassButton>
                  </div>
                </div>
              </div>
            );
          })()}
        </Panel>
      </div>

      {showNew && <NewCampaignModal onClose={() => setShowNew(false)} onCreate={(c) => {
        setState((s) => ({ ...s, campaigns: [c, ...s.campaigns], activeCampaign: c.id }));
        setShowNew(false);
        onNavigate("table");
      }} />}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div style={{
      padding: "10px 14px",
      background: "rgba(176,141,87,0.08)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
    }}>
      <div className="eyebrow">{label}</div>
      <div style={{ fontFamily: "var(--f-display)", fontSize: 18, color: "var(--ink-900)", marginTop: 2 }}>{value}</div>
    </div>
  );
}

function CampaignRow({ c, selected, onSelect }) {
  return (
    <button
      onClick={onSelect}
      style={{
        textAlign: "left",
        display: "grid",
        gridTemplateColumns: "72px 1fr auto",
        gap: 16,
        padding: "14px 18px",
        background: selected
          ? "linear-gradient(180deg, var(--p-100), var(--p-200))"
          : "rgba(176,141,87,0.06)",
        boxShadow: selected
          ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 4px var(--p-100), inset 0 0 0 5px var(--b-400), 0 4px 10px rgba(0,0,0,0.18)"
          : "inset 0 0 0 1px rgba(140,100,60,0.35)",
        cursor: "pointer",
        transition: "all 140ms",
        alignItems: "center",
      }}
    >
      <Placeholder label="seal" w={56} h={56} framed />
      <div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 17, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
          {c.title}
        </div>
        <div className="hand" style={{ fontSize: 14, color: "var(--ink-600)" }}>{c.subtitle}</div>
      </div>
      <div style={{ textAlign: "right" }}>
        <div className="eyebrow">{c.lastPlayed}</div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-700)", marginTop: 4 }}>
          Ch. {c.chapter}
        </div>
      </div>
    </button>
  );
}

function PartyPortrait({ portrait, small }) {
  const size = small ? 64 : 96;
  return (
    <div style={{ textAlign: "center" }}>
      <Placeholder label={portrait.short} w={size} h={size * 1.2} framed />
      <div className="hand" style={{ fontSize: 13, marginTop: 4, color: "var(--ink-700)" }}>{portrait.name}</div>
    </div>
  );
}

function NewCampaignModal({ onClose, onCreate }) {
  const [name, setName] = React.useState("");
  const [system, setSystem] = React.useState("Pathfinder 1e");
  const [tone, setTone] = React.useState("Heroic");

  const create = () => {
    onCreate({
      id: "new-" + Date.now(),
      title: name || "Untitled Chronicle",
      subtitle: "A new road begins.",
      system, chapter: "I",
      lastPlayed: "today",
      sessions: 0,
      region: "Unwritten",
      day: "Day 1",
      party: [{ name: "Cassian", short: "C·portrait" }],
      recap: "The first page is bare. What will you write upon it?",
    });
  };

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 100,
      background: "rgba(15, 8, 2, 0.6)",
      display: "grid", placeItems: "center",
      backdropFilter: "blur(2px)",
    }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 640, maxWidth: "90vw" }}>
        <Panel framed>
          <div className="eyebrow" style={{ color: "var(--crimson)" }}>The First Page</div>
          <h2 className="h1" style={{ fontSize: 26 }}>Begin a new chronicle</h2>
          <Divider />

          <FormField label="Chronicle title">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. The Bone Kings of Aelven"
              style={inkInput}
              autoFocus
            />
          </FormField>

          <FormField label="System">
            <SegRadio value={system} onChange={setSystem} options={["Pathfinder 1e", "D&D 5e", "Free Form"]} />
          </FormField>

          <FormField label="Narrative tone">
            <SegRadio value={tone} onChange={setTone} options={["Heroic", "Grim", "Picaresque", "Mythic"]} />
          </FormField>

          <FormField label="AI Game Master">
            <SegRadio value="Standard" onChange={() => {}} options={["Permissive", "Standard", "Strict"]} />
          </FormField>

          <div style={{ display: "flex", gap: 10, marginTop: 24, justifyContent: "flex-end" }}>
            <BrassButton tone="ghost" onClick={onClose}>Cancel</BrassButton>
            <BrassButton onClick={create}>Light the lantern</BrassButton>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function FormField({ label, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div className="eyebrow" style={{ marginBottom: 6 }}>{label}</div>
      {children}
    </div>
  );
}

const inkInput = {
  width: "100%",
  padding: "10px 14px",
  background: "rgba(255,250,230,0.5)",
  border: 0,
  boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 2px 4px rgba(80,50,20,0.15)",
  fontFamily: "var(--f-body)",
  fontSize: 17,
  color: "var(--ink-800)",
  borderRadius: 0,
  outline: 0,
};

function SegRadio({ value, onChange, options }) {
  return (
    <div style={{ display: "flex", boxShadow: "inset 0 0 0 1px var(--b-500)" }}>
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          style={{
            flex: 1,
            padding: "10px 12px",
            background: value === o ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "transparent",
            color: value === o ? "var(--w-300)" : "var(--ink-700)",
            fontFamily: "var(--f-display)",
            fontSize: 11,
            letterSpacing: "0.2em",
            textTransform: "uppercase",
            boxShadow: value === o ? "inset 0 1px 0 rgba(255,250,220,0.6), inset 0 0 0 1px var(--b-600)" : "none",
            cursor: "pointer",
            transition: "all 140ms",
          }}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

Object.assign(window, { ScreenLauncher, Stat, CampaignRow, PartyPortrait, NewCampaignModal, FormField, SegRadio, inkInput });
