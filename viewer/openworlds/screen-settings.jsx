/* Screen: Settings — audio, video, controls, save slots, accessibility */

function ScreenSettings({ onNavigate, state, setState }) {
  const [section, setSection] = React.useState("audio");
  const [audio, setAudio] = React.useState({ master: 72, music: 60, sfx: 80, ambience: 50, voice: 70, duckMusic: true, crossfade: true });
  const [display, setDisplay] = React.useState({ scale: 100, contrast: 50, vignette: true, paperGrain: true, candleGlow: true });
  const [gameplay, setGameplay] = React.useState({ auto: 15, narration: "balanced", dice: "visible", dangerHints: true, confirmDestructive: true, aiPartyRolls: false });
  const [controls, setControls] = React.useState({ twoFingerScroll: true, pinchZoom: true, forceTouchInspect: false });
  const [accessibility, setAccessibility] = React.useState({ dyslexic: false, reducedMotion: false, captions: true, contrast: false, underlineChoices: false });

  const SECTIONS = [
    { id: "audio", label: "Sound" },
    { id: "display", label: "Display" },
    { id: "gameplay", label: "Gameplay" },
    { id: "controls", label: "Controls" },
    { id: "accessibility", label: "Accessibility" },
    { id: "saves", label: "Saves" },
    { id: "about", label: "About" },
  ];

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "220px 1fr", gap: 14, padding: 14 }}>

      {/* LEFT — section list */}
      <Panel framed style={{ padding: 22, overflow: "auto" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>Codex of</div>
        <h2 className="h1" style={{ fontSize: 22 }}>Setting</h2>
        <Divider />
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {SECTIONS.map((s) => (
            <button key={s.id} onClick={() => setSection(s.id)} style={{
              textAlign: "left",
              padding: "10px 12px",
              fontFamily: "var(--f-display)",
              fontSize: 11,
              letterSpacing: "0.22em",
              textTransform: "uppercase",
              color: section === s.id ? "var(--w-300)" : "var(--ink-700)",
              background: section === s.id ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "transparent",
              boxShadow: section === s.id
                ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)"
                : "inset 0 0 0 1px rgba(140,100,60,0.25)",
              cursor: "pointer",
            }}>{s.label}</button>
          ))}
        </div>

        <Divider />

        <div className="hand muted" style={{ fontSize: 13 }}>
          Press <span style={{ fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--ink-700)" }}>⌘ ,</span> at any time to open this codex.
        </div>
      </Panel>

      {/* RIGHT — section content */}
      <Panel framed style={{ padding: 28, overflow: "auto" }}>
        {section === "audio" && (
          <SettingsSection title="The Sound of the Chronicle" eyebrow="Mixing board" ordinal="I.">
            <Slider label="Master" value={audio.master} onChange={(v) => setAudio({ ...audio, master: v })} />
            <Slider label="Music" value={audio.music} onChange={(v) => setAudio({ ...audio, music: v })} />
            <Slider label="Sound effects" value={audio.sfx} onChange={(v) => setAudio({ ...audio, sfx: v })} />
            <Slider label="Ambience" value={audio.ambience} onChange={(v) => setAudio({ ...audio, ambience: v })} />
            <Slider label="Voice & narration" value={audio.voice} onChange={(v) => setAudio({ ...audio, voice: v })} />

            <Divider />
            <SectionTitle>Output</SectionTitle>
            <SelectRow label="Device" value="System default — MacBook Pro Speakers" options={["System default — MacBook Pro Speakers", "AirPods Pro", "Studio Monitor"]} />
            <SelectRow label="Surround mix" value="Stereo" options={["Stereo", "Spatial Audio", "Headphones (HRTF)"]} />
            <Toggle label="Duck music during GM narration" value={audio.duckMusic} onChange={(v) => setAudio({ ...audio, duckMusic: v })} />
            <Toggle label="Crossfade between scenes" value={audio.crossfade} onChange={(v) => setAudio({ ...audio, crossfade: v })} />
          </SettingsSection>
        )}

        {section === "display" && (
          <SettingsSection title="What the Eye Sees" eyebrow="Lantern & ink" ordinal="II.">
            <Slider label="UI scale" value={display.scale} onChange={(v) => setDisplay({ ...display, scale: v })} min={75} max={150} unit="%" />
            <Slider label="Contrast" value={display.contrast} onChange={(v) => setDisplay({ ...display, contrast: v })} />

            <Divider />
            <SectionTitle>Atmosphere</SectionTitle>
            <Toggle label="Candle glow on panels" value={display.candleGlow} onChange={(v) => setDisplay({ ...display, candleGlow: v })} />
            <Toggle label="Paper grain texture" value={display.paperGrain} onChange={(v) => setDisplay({ ...display, paperGrain: v })} />
            <Toggle label="Edge vignette" value={display.vignette} onChange={(v) => setDisplay({ ...display, vignette: v })} />

            <Divider />
            <SectionTitle>Window</SectionTitle>
            <SelectRow label="Mode" value="Windowed" options={["Windowed", "Fullscreen", "Borderless"]} />
            <SelectRow label="Frame rate" value="ProMotion — 120 Hz" options={["30 Hz", "60 Hz", "ProMotion — 120 Hz"]} />
            <SelectRow label="HDR" value="Off" options={["Off", "Standard", "Aggressive"]} />
          </SettingsSection>
        )}

        {section === "gameplay" && (
          <SettingsSection title="The Manner of Play" eyebrow="Pace & disclosure" ordinal="III.">
            <SectionTitle>Auto-save</SectionTitle>
            <Slider label="Cadence" value={gameplay.auto} onChange={(v) => setGameplay({ ...gameplay, auto: v })} min={5} max={60} unit=" min" />

            <Divider />
            <SectionTitle>Narration</SectionTitle>
            <Radio
              value={gameplay.narration}
              onChange={(v) => setGameplay({ ...gameplay, narration: v })}
              options={[
                { value: "terse", label: "Terse", note: "Short and lean. Mostly dice." },
                { value: "balanced", label: "Balanced", note: "Some prose, some mechanics." },
                { value: "florid", label: "Florid", note: "Read it like a novel." },
              ]}
            />

            <Divider />
            <SectionTitle>Dice</SectionTitle>
            <Radio
              value={gameplay.dice}
              onChange={(v) => setGameplay({ ...gameplay, dice: v })}
              options={[
                { value: "visible", label: "Show every roll", note: "All rolls appear in the chronicle log." },
                { value: "narrative", label: "Hide failures", note: "Only successes and dramatic moments." },
                { value: "blind", label: "GM keeps the dice", note: "Outcomes only. The chronicle decides." },
              ]}
            />

            <Divider />
            <Toggle label="Show danger hints in the world" value={gameplay.dangerHints} onChange={(v) => setGameplay({ ...gameplay, dangerHints: v })} />
            <Toggle label="Confirm before destructive actions" value={gameplay.confirmDestructive} onChange={(v) => setGameplay({ ...gameplay, confirmDestructive: v })} />
            <Toggle label="Permit AI GM to roll for the party" value={gameplay.aiPartyRolls} onChange={(v) => setGameplay({ ...gameplay, aiPartyRolls: v })} />
          </SettingsSection>
        )}

        {section === "controls" && (
          <SettingsSection title="The Player's Hand" eyebrow="Keys & gestures" ordinal="IV.">
            <SectionTitle>Bindings</SectionTitle>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {KEYBINDS.map((kb) => (
                <KeybindRow key={kb.label} kb={kb} />
              ))}
            </div>

            <Divider />
            <SectionTitle>Trackpad</SectionTitle>
            <Toggle label="Two-finger scroll the chronicle" value={controls.twoFingerScroll} onChange={(v) => setControls({ ...controls, twoFingerScroll: v })} />
            <Toggle label="Pinch to zoom the world map" value={controls.pinchZoom} onChange={(v) => setControls({ ...controls, pinchZoom: v })} />
            <Toggle label="Force-touch to inspect items" value={controls.forceTouchInspect} onChange={(v) => setControls({ ...controls, forceTouchInspect: v })} />
          </SettingsSection>
        )}

        {section === "accessibility" && (
          <SettingsSection title="So All May Sit at the Table" eyebrow="Open the door" ordinal="V.">
            <Toggle label="Dyslexic-friendly font for body text" value={accessibility.dyslexic} onChange={(v) => setAccessibility({ ...accessibility, dyslexic: v })} />
            <Toggle label="Reduce motion (no candle flicker, no fades)" value={accessibility.reducedMotion} onChange={(v) => setAccessibility({ ...accessibility, reducedMotion: v })} />
            <Toggle label="Always show captions for narration" value={accessibility.captions} onChange={(v) => setAccessibility({ ...accessibility, captions: v })} />
            <Toggle label="High-contrast UI" value={accessibility.contrast} onChange={(v) => setAccessibility({ ...accessibility, contrast: v })} />

            <Divider />
            <SectionTitle>Reading</SectionTitle>
            <SelectRow label="Body font" value="Cormorant Garamond" options={["Cormorant Garamond", "Atkinson Hyperlegible", "OpenDyslexic", "System default"]} />
            <Slider label="Line spacing" value={50} min={0} max={100} />
            <Toggle label="Underline interactive choices" value={accessibility.underlineChoices} onChange={(v) => setAccessibility({ ...accessibility, underlineChoices: v })} />

            <Divider />
            <SectionTitle>Colour</SectionTitle>
            <SelectRow label="Colour-blind mode" value="None" options={["None", "Deuteranopia", "Protanopia", "Tritanopia"]} />
          </SettingsSection>
        )}

        {section === "saves" && (
          <SettingsSection title="Anchors in Time" eyebrow="Save & restore" ordinal="VI.">
            <div style={{ display: "flex", gap: 8, marginBottom: 18 }}>
              <BrassButton size="sm">Quicksave</BrassButton>
              <BrassButton size="sm" tone="ghost">Quickload</BrassButton>
              <BrassButton size="sm" tone="ghost">Export chronicle…</BrassButton>
              <div style={{ flex: 1 }} />
              <BrassButton size="sm" tone="crimson">Erase all</BrassButton>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {SAVE_SLOTS.map((s, i) => <SaveSlot key={i} s={s} active={i === 0} />)}
            </div>
          </SettingsSection>
        )}

        {section === "about" && (
          <SettingsSection title="Of This Chronicle Engine" eyebrow="Marginalia" ordinal="VII.">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
              <div>
                <p className="body dropcap">
                  Open Worlds is a chronicle engine for tabletop games — a parchment laid across a Mac window, kept by an attentive but unintrusive scribe. Built by candlelight, intended for long roads and patient evenings.
                </p>
                <Divider />
                <StatLine k="Version" v="0.7.2 — Lanternrest build" />
                <StatLine k="Engine" v="Chronicle II / Scribe-of-roads" />
                <StatLine k="System" v="Pathfinder 1e · D&D 5e · Free Form" />
                <StatLine k="Built" v="29 Gozran, 4717" />
              </div>
              <div>
                <SectionTitle>Acknowledgements</SectionTitle>
                <ul className="body" style={{ paddingLeft: 16, margin: 0 }}>
                  <li>To every Game Master who ever lit a candle and a cigarette at the same table.</li>
                  <li>To Linzi, scribe of the Stolen Marches, who insists she is not a character.</li>
                  <li>To the long road between Restov and Odrun, where this engine first occurred to us.</li>
                </ul>
                <Divider />
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <BrassButton size="sm" tone="ghost">Patch notes</BrassButton>
                  <BrassButton size="sm" tone="ghost">Licenses</BrassButton>
                  <BrassButton size="sm" tone="ghost">Report a bug</BrassButton>
                </div>
              </div>
            </div>
          </SettingsSection>
        )}
      </Panel>
    </div>
  );
}

function SettingsSection({ title, eyebrow, ordinal, children }) {
  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--crimson)" }}>{eyebrow}</div>
      <h1 className="h1" style={{ marginTop: 4 }}>
        {ordinal && <span style={{ fontFamily: "var(--f-hand)", fontStyle: "italic", color: "var(--crimson)", fontSize: 26, marginRight: 12 }}>{ordinal}</span>}
        {title}
      </h1>
      <Divider />
      <div>{children}</div>
    </div>
  );
}

function Slider({ label, value, onChange, min = 0, max = 100, unit = "" }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-700)" }}>
          {label}
        </span>
        <span style={{ fontFamily: "var(--f-mono)", fontSize: 12, color: "var(--ink-700)" }}>
          {value}{unit}
        </span>
      </div>
      <div style={{
        position: "relative", marginTop: 6, height: 22,
        display: "flex", alignItems: "center",
      }}>
        <div style={{
          position: "absolute", left: 0, right: 0, height: 6,
          background: "rgba(0,0,0,0.18)",
          boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.45), inset 0 1px 1px rgba(0,0,0,0.2)",
        }} />
        <div style={{
          position: "absolute", left: 0, height: 6,
          width: `${((value - min) / (max - min)) * 100}%`,
          background: "linear-gradient(180deg, var(--b-200), var(--b-500))",
          boxShadow: "inset 0 1px 0 rgba(255,250,220,0.6)",
        }} />
        <input
          type="range" min={min} max={max} value={value}
          onChange={(e) => onChange && onChange(Number(e.target.value))}
          style={{
            position: "absolute", inset: 0, width: "100%", height: "100%",
            opacity: 0, cursor: "pointer",
          }}
        />
        <div style={{
          position: "absolute",
          left: `calc(${((value - min) / (max - min)) * 100}% - 8px)`,
          width: 16, height: 16,
          borderRadius: "50%",
          background: "radial-gradient(circle at 30% 30%, var(--b-100), var(--b-400) 60%, var(--b-600))",
          boxShadow: "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.8), 0 2px 3px rgba(0,0,0,0.3)",
          pointerEvents: "none",
        }} />
      </div>
    </div>
  );
}

function Toggle({ label, value, onChange }) {
  const checked = Boolean(value);
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange && onChange(!checked)} style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      width: "100%",
      padding: "10px 0",
      background: "transparent",
      borderBottom: "1px solid rgba(140,100,60,0.2)",
      cursor: "pointer",
      textAlign: "left",
    }}>
      <span className="body" style={{ color: "var(--ink-800)" }}>{label}</span>
      <span style={{
        width: 44, height: 22,
        background: checked ? "linear-gradient(180deg, var(--b-200), var(--b-500))" : "rgba(0,0,0,0.18)",
        boxShadow: checked
          ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.5)"
          : "inset 0 0 0 1px rgba(80,50,20,0.45)",
        position: "relative",
        borderRadius: 12,
        transition: "all 180ms",
      }}>
        <span style={{
          position: "absolute", top: 2, left: checked ? 24 : 2,
          width: 18, height: 18, borderRadius: "50%",
          background: checked
            ? "radial-gradient(circle at 30% 30%, var(--p-100), var(--p-400))"
            : "radial-gradient(circle at 30% 30%, var(--p-200), var(--ink-600))",
          boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.6), 0 1px 2px rgba(0,0,0,0.3)",
          transition: "all 180ms",
        }} />
      </span>
    </button>
  );
}

function SelectRow({ label, value, options }) {
  const [open, setOpen] = React.useState(false);
  const [current, setCurrent] = React.useState(value);
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "180px 1fr", gap: 16, alignItems: "center",
      padding: "8px 0",
      borderBottom: "1px solid rgba(140,100,60,0.2)",
    }}>
      <span style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-700)" }}>
        {label}
      </span>
      <div style={{ position: "relative" }}>
        <button onClick={() => setOpen(!open)} style={{
          width: "100%",
          padding: "8px 12px",
          background: "rgba(255,250,230,0.5)",
          boxShadow: "inset 0 0 0 1px var(--b-500)",
          fontFamily: "var(--f-body)",
          fontSize: 15,
          color: "var(--ink-800)",
          textAlign: "left",
          display: "flex", justifyContent: "space-between", alignItems: "center",
          cursor: "pointer",
        }}>
          <span>{current}</span>
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
              <button key={o} onClick={() => { setCurrent(o); setOpen(false); }} style={{
                width: "100%",
                padding: "8px 12px",
                background: o === current ? "rgba(176,141,87,0.18)" : "transparent",
                fontFamily: "var(--f-body)",
                fontSize: 15,
                color: "var(--ink-800)",
                textAlign: "left",
                cursor: "pointer",
                borderBottom: "1px solid rgba(140,100,60,0.18)",
              }}>{o}</button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Radio({ value, onChange, options }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 }}>
      {options.map((o) => (
        <button key={o.value} onClick={() => onChange && onChange(o.value)} style={{
          padding: "12px 14px",
          textAlign: "left",
          background: value === o.value
            ? "linear-gradient(180deg, var(--p-100), var(--p-200))"
            : "rgba(176,141,87,0.06)",
          boxShadow: value === o.value
            ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
            : "inset 0 0 0 1px rgba(140,100,60,0.3)",
          cursor: "pointer",
          transition: "all 140ms",
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

const KEYBINDS = [
  { label: "Open Stash", key: "I" },
  { label: "Open Journal", key: "J" },
  { label: "Open Map", key: "M" },
  { label: "Open Heroes", key: "C" },
  { label: "Quicksave", key: "⌘ S" },
  { label: "Quickload", key: "⌘ L" },
  { label: "Pause / unpause", key: "Space" },
  { label: "Toggle Tweaks", key: "⌘ ;" },
  { label: "Roll d20", key: "R" },
  { label: "Camp", key: "K" },
  { label: "Cycle hero", key: "Tab" },
  { label: "Centre on party", key: "Home" },
];

function KeybindRow({ kb }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "center",
      padding: "8px 12px",
      background: "rgba(176,141,87,0.06)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
    }}>
      <span className="body" style={{ color: "var(--ink-800)" }}>{kb.label}</span>
      <span style={{
        fontFamily: "var(--f-mono)",
        fontSize: 11,
        padding: "3px 10px",
        background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
        boxShadow: "inset 0 0 0 1px var(--b-500), 0 1px 0 var(--p-100), 0 2px 2px rgba(0,0,0,0.2)",
        color: "var(--ink-800)",
        minWidth: 30,
        textAlign: "center",
      }}>{kb.key}</span>
    </div>
  );
}

const SAVE_SLOTS = [
  { name: "The Lanternrest, before dusk", chronicle: "Long Road to Odrun · Ch II", time: "now", auto: false, party: 4, dayLabel: "Day 12 · 29 Gozran" },
  { name: "Auto-save", chronicle: "Long Road to Odrun · Ch II", time: "12 min ago", auto: true, party: 4, dayLabel: "Day 12 · 29 Gozran" },
  { name: "Thorn Ford crossed", chronicle: "Long Road to Odrun · Ch II", time: "yesterday", auto: false, party: 4, dayLabel: "Day 11" },
  { name: "Beneath the Drowned Cathedral", chronicle: "Bone Kings · Ch VI", time: "three weeks", auto: false, party: 3, dayLabel: "Day 41 · winter" },
  { name: "After the gate-keeper", chronicle: "Long Road to Odrun · Ch I", time: "last month", auto: false, party: 3, dayLabel: "Day 4" },
  { name: "Quicksave", chronicle: "Long Road to Odrun · Ch II", time: "3 hr ago", auto: true, party: 4, dayLabel: "Day 12" },
];

function SaveSlot({ s, active }) {
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "100px 1fr", gap: 14,
      padding: 12,
      background: active ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "rgba(176,141,87,0.06)",
      boxShadow: active
        ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
        : "inset 0 0 0 1px rgba(140,100,60,0.3)",
    }}>
      <Placeholder label="scene · save thumbnail" w={100} h={70} framed />
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
          <span style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.06em", color: "var(--ink-900)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {s.name}
          </span>
          {s.auto && <Pill>Auto</Pill>}
        </div>
        <div className="hand" style={{ fontSize: 12, color: "var(--ink-600)" }}>{s.chronicle}</div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 6 }}>
          <span className="body-sm muted">{s.dayLabel} · {s.party} heroes</span>
          <span style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-600)" }}>{s.time}</span>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { ScreenSettings, SettingsSection, Slider, Toggle, SelectRow, Radio, KEYBINDS, KeybindRow, SAVE_SLOTS, SaveSlot });
