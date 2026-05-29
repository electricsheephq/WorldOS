/* Screen: Character Creation — wizard flow */

/* W2 art bridge for the Creation Plane (CR-02/03/04): the race + class pickers and the
   portrait gallery render real ingested art through the shared <Img scope=…> → /image
   render bridge, with a graceful Placeholder / silhouette fallback on a miss (never a
   broken image, never a heraldic crest where a face belongs).

   Scope keys follow the server's _scope_key normalization (viewer/server.py): the kind
   prefix (race/class/portrait) is stripped and separators are unified, so "race-human"
   resolves the ingested dir `_private/baldurs-gate/images/race_human/` and "class-fighter"
   resolves `class_fighter`. Slugify the id so multi-word ids stay one path segment. */
function ccSlug(s) {
  return String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

// Race ids in this screen are shorthand ("half" = Half-Elf); map them to the ingested
// race slug before building the scope so "half" reaches `race_half-elf`, not `race_half`.
const RACE_SLUG = { half: "half-elf" };
function raceScope(id) {
  const s = RACE_SLUG[id] || ccSlug(id);
  return s ? "race-" + s : "";
}
function classScope(id) {
  const s = ccSlug(id);
  return s ? "class-" + s : "";
}

// Curated default portrait gallery — recognizable canon BG faces that all have ingested
// art under `_private/baldurs-gate/images/portrait_<slug>/`. The gallery index stored on
// hero.portrait maps into this list; an out-of-range or future index falls back to a clean
// silhouette via <Img> (the scope matches /portrait/ so it shows a face silhouette, not a
// crest). "Bring your own — drop a PNG" remains a future affordance.
const PORTRAIT_GALLERY = [
  // A LIVING canon face leads the gallery (Aubree, a Flaming Fist ranger). Was Dal Lightspark,
  // but he is dead in canon — per #305's content-curation policy a dead figure is lore-only,
  // never offered as a player avatar.
  { slug: "aubree", name: "Aubree" },
  { slug: "shadowheart", name: "Shadowheart" },
  { slug: "astarion", name: "Astarion" },
  { slug: "gale", name: "Gale" },
  { slug: "lae-zel", name: "Lae'zel" },
  { slug: "wyll", name: "Wyll" },
  { slug: "karlach", name: "Karlach" },
  { slug: "jaheira", name: "Jaheira" },
  { slug: "minsc", name: "Minsc" },
  { slug: "halsin", name: "Halsin" },
  { slug: "minthara", name: "Minthara" },
  { slug: "dame-aylin", name: "Dame Aylin" },
];
function portraitScope(i) {
  const p = PORTRAIT_GALLERY[i];
  return p ? "portrait-" + p.slug : "";
}

// The scope the hero's CURRENT face resolves through (#265). When the player generated a
// unique face the preview/summary/review render that provisional scope (portrait-pc-<hash>,
// returned by POST /portrait-gen); otherwise it's the chosen gallery face. A generation that
// fell back to a placeholder leaves portraitMode "gallery", so the gallery face stays.
function heroPortraitScope(hero) {
  if (hero.portraitMode === "gen" && hero.portraitGenScope) return hero.portraitGenScope;
  return portraitScope(hero.portrait);
}

function ScreenCreate({ onNavigate, state, setState }) {
  const [step, setStep] = React.useState(0);
  const [hero, setHero] = React.useState({
    name: "",
    race: "human",
    class: "fighter",
    background: "wanderer",
    portrait: 0,
    // #265 portrait choice: "gallery" (default, the 12-face grid) or "gen" (a unique face
    // generated via the gateway, cached at portraitGenScope). appearance = optional cues.
    portraitMode: "gallery",
    portraitGenScope: "",
    appearance: "",
    alignment: "neutral-good",
    abilities: { str: 8, dex: 8, con: 8, int: 8, wis: 8, cha: 8 },
    points: 27,
  });
  const [summoning, setSummoning] = React.useState(false);
  const [summonError, setSummonError] = React.useState("");
  const toast = window.useToast ? window.useToast() : (() => {});

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

  // Bind the authored hero into a real, playable game. Mirrors screen-launcher's startPlay:
  // serialize the wizard's choices into a compact hero spec, hand it to the native supervisor
  // through the SAME startProviderSession bridge (one new optional `hero` field), and reload
  // onto the live, move-sink-wired viewer the bridge returns. play.sh pre-seeds this exact PC
  // via the engine (the sole writer) before the DM's first turn, so the hero the player
  // authored is the hero they play. Outside the native app (a plain browser preview) there is
  // no supervisor to summon — fall back to the read-only table so the surface stays reachable.
  const bindHero = async () => {
    if (summoning) return;
    if (!window.OpenWorldsNative?.hasBridge?.()) {
      onNavigate("table");
      return;
    }
    const spec = {
      name: (hero.name || "").trim() || "Unnamed Hero",
      race: hero.race,
      class: hero.class,
      level: 1,
      abilities: hero.abilities,
      background: hero.background,
      alignment: hero.alignment,
      skills: BACKGROUNDS[hero.background]?.skills || [],
      // #265: carry the portrait choice through the seam so the seeded PC gets the right face.
      // mode "gen" -> play.sh re-keys the generated portraitGenScope onto portrait-<char_id>;
      // mode "gallery" -> the canon slug resolves via the viewer's _portrait_by_name bridge.
      portrait: hero.portraitMode === "gen" && hero.portraitGenScope
        ? { mode: "gen", scope: hero.portraitGenScope }
        : { mode: "gallery", gallerySlug: PORTRAIT_GALLERY[hero.portrait]?.slug || "" },
    };
    setSummonError("");
    setSummoning(true);
    const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "");
    try {
      const reply = await window.OpenWorldsNative.request("startProviderSession", {
        provider: "claude",
        world: "baldurs-gate",
        runId: `play-${stamp}`,
        companions: "",
        hero: JSON.stringify(spec),
      });
      // Drive the reload to the live viewer from JS using the URL the bridge returns (same as
      // screen-launcher) — the live viewer boots fresh and app.jsx auto-routes into the table
      // once the provider is running.
      const liveUrl = reply && (reply.url || reply.viewer?.openWorldsURL);
      if (liveUrl) {
        window.location.assign(liveUrl);
        return;
      }
      setSummoning(false);
      setSummonError("The hero was bound, but the live viewer address was missing.");
    } catch (error) {
      setSummoning(false);
      setSummonError(error?.message || String(error));
      toast({
        kind: "danger",
        title: "Could not bind the hero",
        body: error?.message || String(error),
      });
    }
  };

  return (
    <div className="screen" style={{ height: "100%", display: "flex", flexDirection: "column", gap: 8, padding: 14 }}>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "240px 1fr 280px", gap: 14, minHeight: 0 }}>

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
          <BrassButton tone="ghost" onClick={prev} disabled={step === 0 || summoning}>← Back</BrassButton>
          <span className="muted body-sm">Step {step + 1} of {steps.length}</span>
          {step < steps.length - 1 ? (
            <BrassButton onClick={next}>Continue →</BrassButton>
          ) : (
            <BrassButton tone="crimson" onClick={bindHero} disabled={summoning}>
              {summoning ? "Binding the hero…" : "Bind the hero"}
            </BrassButton>
          )}
        </div>
        {summonError && (
          <div className="hand" style={{ color: "var(--crimson)", fontSize: 13, marginTop: 10, textAlign: "right" }}>
            {summonError}
          </div>
        )}
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

        <Img scope={heroPortraitScope(hero)} label={hero.portraitMode === "gen" ? "your unique face" : (PORTRAIT_GALLERY[hero.portrait]?.name || "portrait")} h={160} fit="cover" framed style={{ width: "100%", marginTop: 12 }} />

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
            imgScope={raceScope(id)}
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
            imgScope={classScope(id)}
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
    if (target < 8 || target > 15) return;
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
        Twenty-seven points, six gifts. Scores run 8 to 15; higher scores cost more. D&D 5e point buy.
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
            {hero.points}<span className="muted" style={{ fontSize: 14 }}>/27</span>
          </div>
        </div>
        <BrassButton tone="ghost" size="sm" onClick={() => setHero({
          ...hero,
          abilities: { str: 8, dex: 8, con: 8, int: 8, wis: 8, cha: 8 },
          points: 27,
        })}>Reset</BrassButton>
      </div>
    </div>
  );
}

function StepPortrait({ hero, setHero }) {
  // #265: BOTH paths. The 12-face gallery is the default; "Generate a unique face" is opt-in.
  // genState: "idle" | "generating" | "done" | "failed". The generated face is previewed from
  // its provisional scope (hero.portraitGenScope). On a box with no image provider, or any
  // failure/timeout, we DON'T switch away from the gallery — the player's selection stands.
  const [genState, setGenState] = React.useState(hero.portraitMode === "gen" ? "done" : "idle");
  const toast = window.useToast ? window.useToast() : (() => {});
  const genMode = hero.portraitMode === "gen" && !!hero.portraitGenScope;

  // Picking a gallery face always returns to gallery mode (so the gallery selection wins back).
  const pickGallery = (i) => setHero({ ...hero, portrait: i, portraitMode: "gallery" });

  const generate = async () => {
    if (genState === "generating") return;  // debounce: one in-flight gen
    setGenState("generating");
    try {
      const resp = await fetch("/portrait-gen", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          race: hero.race,
          class: hero.class,
          name: (hero.name || "").trim(),
          appearance: (hero.appearance || "").trim(),
          alignment: hero.alignment,
        }),
      });
      const res = await resp.json().catch(() => ({}));
      if (res && res.ok && res.generated && res.scope) {
        // A real, unique face was produced — switch to it (bust the <Img> cache with the scope).
        setHero({ ...hero, portraitMode: "gen", portraitGenScope: res.scope });
        setGenState("done");
        return;
      }
      // Null provider / placeholder / degraded: keep the gallery face, tell the player gently.
      setGenState("failed");
      toast({
        kind: "info",
        title: "Couldn't summon a unique face",
        body: "Using your selected portrait instead. Unique faces need the image gateway.",
      });
    } catch (err) {
      setGenState("failed");
      toast({
        kind: "info",
        title: "Couldn't summon a unique face",
        body: "Using your selected portrait instead.",
      });
    }
  };

  return (
    <div>
      <div className="eyebrow" style={{ color: "var(--crimson)" }}>V. Of Face</div>
      <h1 className="h1">What will the chronicle remember of you?</h1>
      <Divider />

      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 10 }}>
        {PORTRAIT_GALLERY.map((p, i) => (
          <button key={p.slug} onClick={() => pickGallery(i)} title={p.name} style={{
            padding: 4,
            background: (!genMode && hero.portrait === i) ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
            boxShadow: (!genMode && hero.portrait === i)
              ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400), 0 0 16px -2px var(--gold-glow)"
              : "inset 0 0 0 1px rgba(140,100,60,0.3)",
            cursor: "pointer",
          }}>
            {/* CR-04: real ingested portraits via the render bridge; a miss falls back to a
                neutral head-and-shoulders silhouette (the scope matches /portrait/), never a crest. */}
            <Img scope={portraitScope(i)} label={p.name} w="100%" h={140} fit="cover" framed />
          </button>
        ))}
      </div>

      <Divider />

      {/* #265: opt-in unique-face generation. The gallery above stays the default + fallback. */}
      <div style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 16, alignItems: "start" }}>
        <div>
          {/* Live preview of the generated face (or a silhouette until one exists). */}
          <Img
            scope={genMode ? hero.portraitGenScope : ""}
            label={genMode ? "your unique face" : "a face of your own"}
            w="100%" h={150} fit="cover" framed
            style={genMode ? { boxShadow: "inset 0 0 0 2px var(--b-500), 0 0 16px -2px var(--gold-glow)" } : undefined}
          />
        </div>
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>A face of your own</div>
          <p className="body-sm muted" style={{ marginTop: 0, lineHeight: 1.4 }}>
            Summon a unique portrait painted for this hero alone. Optional — your gallery
            choice is kept if none can be summoned.
          </p>
          <div className="eyebrow" style={{ marginTop: 12, marginBottom: 6 }}>Appearance (optional)</div>
          <input
            value={hero.appearance || ""}
            onChange={(e) => setHero({ ...hero, appearance: e.target.value })}
            placeholder="e.g. weathered scar, silver braid, amber eyes"
            maxLength={160}
            style={{ ...window.inkInput, fontSize: 14 }}
          />
          <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center" }}>
            <BrassButton onClick={generate} disabled={genState === "generating"}>
              {genState === "generating"
                ? "Summoning a face…"
                : (genMode ? "Summon another" : "Generate a unique face")}
            </BrassButton>
            {genMode && (
              <BrassButton tone="ghost" size="sm" onClick={() => { setHero({ ...hero, portraitMode: "gallery" }); setGenState("idle"); }}>
                Use a gallery face
              </BrassButton>
            )}
          </div>
        </div>
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
          <Img scope={heroPortraitScope(hero)} label={hero.portraitMode === "gen" ? "your unique face" : (PORTRAIT_GALLERY[hero.portrait]?.name || "portrait")} h={240} fit="cover" framed style={{ width: "100%" }} />
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

function SelectCard({ selected, onClick, label, sublabel, portrait, imgScope, body, tags }) {
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
      {/* CR-02/03: ingested race/class art via the render bridge; falls back to the styled
          Placeholder label (the prior behaviour) when the scope misses — never a broken image. */}
      {imgScope
        ? <Img scope={imgScope} label={portrait} w={80} h={96} fit="cover" framed />
        : <Placeholder label={portrait} w={80} h={96} framed />}
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
  // D&D 5e point buy: scores 8–15, total budget 27.
  const cost = { 8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9 };
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
    bonus: { str: 1, dex: 1, con: 1, int: 1, wis: 1, cha: 1 },
  },
  halfling: {
    name: "Halfling",
    size: "Small",
    life: "100 years",
    glyph: "halfling · sketch",
    body: "Footloose, footsure, and oddly hard to startle. Scribes, scouts, and second daughters of failed dynasties.",
    bonus: { dex: 2 },
  },
  dwarf: {
    name: "Dwarf",
    size: "Medium",
    life: "350 years",
    glyph: "dwarf · sketch",
    body: "Slow to leave, slower to anger, slowest to forget. Stonecunning, ironwise, and oddly reliable.",
    bonus: { con: 2 },
  },
  elf: {
    name: "Elf",
    size: "Medium",
    life: "750 years",
    glyph: "elf · sketch",
    body: "Older than several wars they were not in. Sharp eyes, sharp words, sharper at the wrong times.",
    bonus: { dex: 2 },
  },
  half: {
    name: "Half-Elf",
    size: "Medium",
    life: "180 years",
    glyph: "half-elf",
    body: "Sufficient to neither lineage to be considered theirs by either. Many things, often very well.",
    bonus: { cha: 2, dex: 1, int: 1 },
  },
  tiefling: {
    name: "Tiefling",
    size: "Medium",
    life: "120 years",
    glyph: "tiefling",
    body: "Touched by something hot once. Counts the chambers of the soul on three hands.",
    bonus: { int: 1, cha: 2 },
  },
};

const CLASSES = {
  wizard: {
    name: "Wizard",
    role: "Spell and study",
    glyph: "wizard · sigil",
    body: "Carries a book of borrowed lightning. Prepares the day's spells each dawn and spends them like a careful purse.",
    tags: ["d6 HP", "spellbook", "arcane"],
    hp: 6,
    kit: [
      { name: "Quarterstaff", qty: 1 },
      { name: "Spellbook (6 spells)", qty: 1 },
      { name: "Component pouch", qty: 1 },
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
      { name: "Chain mail", qty: 1 },
      { name: "Rations", qty: 6 },
    ],
  },
  cleric: {
    name: "Cleric",
    role: "Sworn and channeling",
    glyph: "cleric · sigil",
    body: "Tied to a god by oath, debt, or unconcluded argument. Keeps the company alive by negotiating with the dying on a god's behalf.",
    tags: ["d8 HP", "channel divinity", "heavy armour"],
    hp: 8,
    kit: [
      { name: "Warhammer", qty: 1 },
      { name: "Shield, holy", qty: 1 },
      { name: "Holy symbol", qty: 1 },
      { name: "Cure Wounds (prepared)", qty: 3 },
    ],
  },
  rogue: {
    name: "Rogue",
    role: "First in, first out",
    glyph: "rogue · sigil",
    body: "Knows what the door is for, has a different way through it. Useful in the dark; useful in the meeting; useful in the kitchens.",
    tags: ["d8 HP", "sneak attack", "tools"],
    hp: 8,
    kit: [
      { name: "Shortsword + dagger", qty: 2 },
      { name: "Leather armour", qty: 1 },
      { name: "Thieves' tools", qty: 1 },
      { name: "Caltrops", qty: 1 },
    ],
  },
  paladin: {
    name: "Paladin",
    role: "Oath and aegis",
    glyph: "paladin · sigil",
    body: "Bound to an oath that burns brighter than any blade. Lays on hands what the sword could not mend.",
    tags: ["d10 HP", "lay on hands", "heavy armour"],
    hp: 10,
    kit: [
      { name: "Longsword", qty: 1 },
      { name: "Chain mail", qty: 1 },
      { name: "Shield", qty: 1 },
      { name: "Holy symbol", qty: 1 },
    ],
  },
};

const BACKGROUNDS = {
  wanderer: { name: "Wanderer", brief: "No address. Many addresses.", skills: ["Survival", "Nature"] },
  scholar: { name: "Scholar", brief: "Of an institution, real or alleged.", skills: ["Arcana", "History"] },
  noble: { name: "Disinherited Noble", brief: "Was someone. Is no longer.", skills: ["Persuasion", "History"] },
  soldier: { name: "Soldier", brief: "Served, returned, signed nothing.", skills: ["Athletics", "Intimidation"] },
  outlaw: { name: "Outlaw", brief: "Wanted in three districts; welcome in two.", skills: ["Stealth", "Deception"] },
  pilgrim: { name: "Pilgrim", brief: "Going somewhere. Still going.", skills: ["Religion", "Survival"] },
  artisan: { name: "Artisan", brief: "Made something good. Will make another.", skills: ["Investigation", "Persuasion"] },
  hedge: { name: "Hedge-witch", brief: "Taught by an older woman, since gone.", skills: ["Nature", "Medicine"] },
  spy: { name: "Spy", brief: "Was paid for nine years to be elsewhere.", skills: ["Stealth", "Insight"] },
};

Object.assign(window, { ScreenCreate, RACES, CLASSES, BACKGROUNDS, SelectCard, abilityCost, raceScope, classScope, portraitScope, heroPortraitScope, PORTRAIT_GALLERY });
