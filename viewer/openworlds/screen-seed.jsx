/* Screen: World Seed — campaign foundational parameters.
   Wired to the live /seed-surface read model (#266): the seed IDENTITY (de-faked from real
   campaign fields — created_at / world_id / era / ruleset+engine_sha / stable id pattern),
   the live `params` each control binds to, the free/gated/locked `mutability` matrix, and
   `session_started`. Each control submits an INTENT through POST /seed-param (the engine is
   the SOLE writer — the viewer never writes snapshot state). Polls while visible; degrades to
   an honest empty-state when there is no chronicle. */

// Option tables: each control shows a human LABEL but submits the engine VALUE the
// /seed-surface params bind to (e.g. narration "florid", chronicle_voice "first_person_plural").
const SEED_OPTIONS = {
  tone: [
    { value: "Heroic", label: "Heroic", note: "Gold, royal blue, candlelight. Players are who they say they are." },
    { value: "Grim", label: "Grim", note: "Crimson and walnut. Successes are uncomfortable. Most are." },
    { value: "Picaresque", label: "Picaresque", note: "The party will lie. The chronicle will pretend not to notice." },
    { value: "Mythic", label: "Mythic", note: "Brass and oxblood. The land is older than the law and is winning." },
  ],
  difficulty: [
    { value: "easy", label: "Story", note: "Combat is forgiving. The chronicle is the point." },
    { value: "standard", label: "Standard", note: "The rules as written. The road as expected." },
    { value: "hard", label: "Hard", note: "The enemies have read the rules and are using them." },
  ],
  gm_strictness: [
    { value: "permissive", label: "Permissive" },
    { value: "standard", label: "Standard" },
    { value: "strict", label: "Strict" },
    { value: "pedantic", label: "Pedantic" },
  ],
  narration: [
    { value: "terse", label: "Terse" },
    { value: "balanced", label: "Balanced" },
    { value: "florid", label: "Florid" },
    { value: "almost_poetic", label: "Almost-poetic" },
  ],
  chronicle_voice: [
    { value: "first_person_singular", label: "First-person singular" },
    { value: "first_person_plural", label: "First-person plural" },
    { value: "second_person", label: "Second person" },
    { value: "third_person_omniscient", label: "Third-person omniscient" },
    { value: "third_person_close", label: "Third-person close" },
  ],
};

function seedLabel(key, value) {
  const opt = (SEED_OPTIONS[key] || []).find((o) => o.value === value);
  return opt ? opt.label : (value == null ? "" : String(value));
}

function ScreenSeed({ onNavigate, state, setState }) {
  const activeCampaign =
    (Array.isArray(state?.campaigns) ? state.campaigns : []).find((c) => c.id === state?.activeCampaign) ||
    (Array.isArray(state?.campaigns) ? state.campaigns : [])[0] || {};
  const surfaceQuery = window.combatSurfaceFromCampaign
    ? window.combatSurfaceFromCampaign(activeCampaign, state)
    : "";
  const campaignId = activeCampaign?.campaign_id || state?.activeCampaign || activeCampaign?.id || "";
  const toast = window.useToast ? window.useToast() : (() => {});

  const [surface, setSurface] = React.useState(null);
  const [busy, setBusy] = React.useState("");

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    try {
      const response = await fetch("/seed-surface" + surfaceQuery, { cache: "no-store" });
      if (!response.ok) throw new Error(`seed surface ${response.status}`);
      const payload = await response.json();
      if (isCancelled()) return;
      setSurface(payload);
    } catch (error) {
      /* keep the last good surface */
    }
  }, [surfaceQuery]);

  React.useEffect(() => {
    let cancelled = false;
    let timer = null;
    const guardedLoad = async () => { if (!cancelled) await loadSurface(() => cancelled); };
    const stop = () => { if (timer !== null) { window.clearInterval(timer); timer = null; } };
    const start = () => { if (timer === null) timer = window.setInterval(guardedLoad, 5000); };
    const onVis = () => {
      if (document.visibilityState === "visible") { guardedLoad(); start(); } else { stop(); }
    };
    document.addEventListener("visibilitychange", onVis);
    onVis();
    return () => { cancelled = true; stop(); document.removeEventListener("visibilitychange", onVis); };
  }, [loadSurface]);

  const present = !!surface?.present;
  const params = surface?.params || {};
  const mutability = surface?.mutability || {};
  const identity = surface?.identity || {};
  const canAct = !!surface?.can_act;
  const sessionStarted = !!surface?.session_started;

  // Submit ONE seed-param change through the engine's write lane. free params apply
  // immediately; a gated param on a chronicle that has already begun asks for confirmation
  // (it is retroactive) and re-submits with force; locked params are read-only.
  const writeParam = React.useCallback(async (key, value, opts = {}) => {
    const cls = mutability[key];
    if (cls === "locked") return;
    if (!canAct) {
      toast({ kind: "danger", title: "Seed is read-only", body: "Attach the live chronicle to change its seed." });
      return;
    }
    const force = !!opts.force;
    if (cls === "gated" && sessionStarted && !force) {
      const ok = window.confirm(
        "“" + seedLabel(key, value) + "” changes a rule of a chronicle already in progress — this is " +
        "retroactive and can shift the felt balance from here on. Apply it anyway?",
      );
      if (!ok) return;
      return writeParam(key, value, { force: true });
    }
    setBusy(key);
    try {
      const body = { param: key, value, campaign: campaignId };
      if (force) body.force = true;
      const response = await fetch("/seed-param", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) throw new Error(payload.reason || `seed-param ${response.status}`);
      // Optimistic: reflect the change locally, then re-fetch the authoritative surface.
      setSurface((s) => (s ? { ...s, params: { ...s.params, [key]: value } } : s));
      toast({ kind: "ok", title: "Relayed to the chronicle", body: seedLabel(key, value) + " — the engine will apply it." });
      await loadSurface();
    } catch (error) {
      toast({ kind: "danger", title: "Change not sent", body: error?.message || "The viewer could not reach /seed-param." });
    } finally {
      setBusy("");
    }
  }, [mutability, canAct, sessionStarted, campaignId, toast, loadSurface]);

  // ── empty-state (no chronicle sown) ────────────────────────────────────────
  if (surface && !present) {
    return (
      <div className="screen" style={{ height: "100%", display: "flex", flexDirection: "column", padding: 14, minHeight: 0 }}>
        <Panel framed style={{ padding: 28, flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", textAlign: "center" }}>
          <div className="eyebrow" style={{ color: "var(--crimson)" }}>World Seed</div>
          <h1 className="h1" style={{ fontSize: 28 }}>No chronicle sown yet</h1>
          <div className="hand" style={{ fontSize: 16, color: "var(--ink-700)", marginTop: 8, maxWidth: 440 }}>
            The seed is what a chronicle is sown with — its tone, its voice, its rules. Begin a chronicle and its seed will appear here, ready to tend.
          </div>
        </Panel>
      </div>
    );
  }

  return (
    <div className="screen" style={{ height: "100%", display: "flex", flexDirection: "column", gap: 8, padding: 14, minHeight: 0 }}>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "1fr 1.1fr", gap: 14, minHeight: 0, overflow: "auto" }}>

      {/* LEFT — seed card */}
      <Panel framed style={{ padding: 28, overflow: "auto" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>Of this chronicle's</div>
        <h1 className="h1" style={{ fontSize: 28 }}>{present ? (surface.title || "World Seed") : "World Seed"}</h1>
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
            — found in a border coachman's pocket, undated
          </div>
        </div>

        <Divider />

        <SectionTitle ordinal="·">The Quickening</SectionTitle>
        <div className="body" style={{ fontSize: 15 }}>
          <p>
            This chronicle is sown for a heroic register in the tradition of frontier baronies and the contested marches. The reading voice is communal — we, when we walked; we, when we found. Decisions are remembered. Failure is rarely permanent but always written down. Salt and silence have meanings the rules will not state.
          </p>
        </div>

        <Divider />

        {/* De-faked seed identity (S-03) — every value is a real campaign field. */}
        {present && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {identity.seeded ? <StatLine k="Seeded" v={identity.seeded} /> : null}
            <StatLine k="By" v={identity.by || "the chronicle"} />
            {identity.era ? <StatLine k="Chronology" v={identity.era} /> : null}
            {identity.pattern ? <StatLine k="Pattern" v={identity.pattern} /> : null}
            {identity.engine ? <StatLine k="Engine" v={identity.engine} /> : null}
            {identity.ending ? <StatLine k="Ending" v={identity.ending} /> : null}
          </div>
        )}

        <Divider />

        <SectionTitle>Re-seed</SectionTitle>
        <div className="hand muted" style={{ fontSize: 13 }}>
          A new seed begins a new chronicle. The party's standing is reset; their names are not.
        </div>
        <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
          <BrassButton tone="ghost" size="sm" onClick={() => {
            if (identity.pattern && navigator.clipboard) {
              navigator.clipboard.writeText(identity.pattern).then(
                () => toast({ kind: "ok", title: "Pattern copied", body: identity.pattern }),
                () => toast({ kind: "danger", title: "Copy failed", body: "Could not reach the clipboard." }),
              );
            }
          }}>Copy pattern</BrassButton>
          <BrassButton tone="crimson" size="sm" onClick={() => toast({ kind: "danger", title: "Reseed locked", body: "Re-seeding is a destructive, two-step flow not yet wired. The chronicle protects itself from accidental wipes." })}>Reseed</BrassButton>
        </div>
      </Panel>

      {/* RIGHT — tunables (each bound to /seed-param via the engine write lane) */}
      <Panel framed style={{ padding: 28, overflow: "auto", opacity: canAct ? 1 : 0.7 }}>
        {!canAct && present && (
          <div className="hand muted" style={{ fontSize: 13, marginBottom: 8, color: "var(--crimson)" }}>
            Director's view — attach the live chronicle to tend its seed.
          </div>
        )}

        <SectionTitle ordinal="I.">System</SectionTitle>
        {/* System (ruleset) is LOCKED post-seed — shown read-only. */}
        <div style={{
          padding: "10px 14px", background: "rgba(176,141,87,0.06)",
          boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
          fontFamily: "var(--f-body)", fontSize: 16, color: "var(--ink-700)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <span>{params.system || "SRD 5.2"}</span>
          <span className="hand muted" style={{ fontSize: 12 }}>locked at seeding</span>
        </div>

        <Divider />

        <SectionTitle ordinal="II.">Tone</SectionTitle>
        <SeedRadio
          value={params.tone}
          onChange={(v) => writeParam("tone", v)}
          options={SEED_OPTIONS.tone}
        />

        <Divider />

        <SectionTitle ordinal="III.">Difficulty</SectionTitle>
        {mutability.difficulty === "gated" && sessionStarted && (
          <div className="hand muted" style={{ fontSize: 12, marginBottom: 6 }}>Retroactive once a chronicle has begun — you'll be asked to confirm.</div>
        )}
        <SeedRadio
          value={params.difficulty}
          onChange={(v) => writeParam("difficulty", v)}
          options={SEED_OPTIONS.difficulty}
        />

        <Divider />

        <SectionTitle ordinal="IV.">AI Game Master</SectionTitle>
        <SeedRow label="GM strictness" value={seedLabel("gm_strictness", params.gm_strictness)}>
          <SeedSelect
            value={seedLabel("gm_strictness", params.gm_strictness)}
            options={SEED_OPTIONS.gm_strictness.map((o) => o.label)}
            onChange={(label) => writeParam("gm_strictness", (SEED_OPTIONS.gm_strictness.find((o) => o.label === label) || {}).value)}
            inline
          />
        </SeedRow>
        <SeedRow label="Narration register" value={seedLabel("narration", params.narration)}>
          <SeedSelect
            value={seedLabel("narration", params.narration)}
            options={SEED_OPTIONS.narration.map((o) => o.label)}
            onChange={(label) => writeParam("narration", (SEED_OPTIONS.narration.find((o) => o.label === label) || {}).value)}
            inline
          />
        </SeedRow>
        <SeedRow label="Chronicle voice" value={seedLabel("chronicle_voice", params.chronicle_voice)}>
          <SeedSelect
            value={seedLabel("chronicle_voice", params.chronicle_voice)}
            options={SEED_OPTIONS.chronicle_voice.map((o) => o.label)}
            onChange={(label) => writeParam("chronicle_voice", (SEED_OPTIONS.chronicle_voice.find((o) => o.label === label) || {}).value)}
            inline
          />
        </SeedRow>

        <Divider />

        <SectionTitle ordinal="V.">World Rules</SectionTitle>
        <SeedToggle
          label="Permadeath"
          detail="When a hero dies, they stay dead. The chronicle continues without them."
          gated={mutability.permadeath === "gated" && sessionStarted}
          value={!!params.permadeath}
          onChange={(v) => writeParam("permadeath", v)}
        />
        <SeedToggle
          label="Fate dice"
          detail="Each hero starts each act with a fate die. Spend to ask the world for a complication, not a setback."
          gated={mutability.fate_dice === "gated" && sessionStarted}
          value={!!params.fate_dice}
          onChange={(v) => writeParam("fate_dice", v)}
        />
        <SeedToggle
          label="Item destruction"
          detail="Weapons and armour wear with use. Forge it again or find it again."
          gated={mutability.item_destruction === "gated" && sessionStarted}
          value={!!params.item_destruction}
          onChange={(v) => writeParam("item_destruction", v)}
        />
        <SeedToggle
          label="Anachronism"
          detail="The chronicle permits a small number of out-of-period words for the sake of clarity."
          value={!!params.anachronism}
          onChange={(v) => writeParam("anachronism", v)}
        />

        <Divider />

        <SectionTitle ordinal="VI.">Chronicler's notes</SectionTitle>
        <SeedNotes
          value={params.chronicler_notes || ""}
          disabled={!canAct}
          busy={busy === "chronicler_notes"}
          onCommit={(text) => { if (text !== (params.chronicler_notes || "")) writeParam("chronicler_notes", text); }}
        />
      </Panel>
      </div>
    </div>
  );
}

// Free-text notes that COMMIT (relay to the engine) on blur, not on every keystroke, so a
// single edit is one intent. Mirrors the live param value when it changes underneath.
function SeedNotes({ value, disabled, busy, onCommit }) {
  const [draft, setDraft] = React.useState(value);
  React.useEffect(() => { setDraft(value); }, [value]);
  return (
    <div>
      <textarea
        aria-label="Chronicler's notes"
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => onCommit(draft)}
        placeholder="The chronicle keeps the book. Do not edit its entries even when they are wrong. Especially when they are wrong."
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
          opacity: disabled ? 0.6 : 1,
        }}
      />
      <div className="hand muted" style={{ fontSize: 12, marginTop: 4 }}>
        {busy ? "Relaying…" : "Saved to the chronicle when you click away."}
      </div>
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

function SeedToggle({ label, detail, value, onChange, gated }) {
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
          {label}{gated ? <span className="hand muted" style={{ textTransform: "none", letterSpacing: 0, marginLeft: 6, fontSize: 11, color: "var(--crimson)" }}>· retroactive</span> : null}
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

Object.assign(window, { ScreenSeed, SeedSelect, SeedRadio, SeedRow, SeedToggle, SeedNotes });
