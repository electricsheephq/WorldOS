/* Camp Sidebar — D&D 5e party role assignment during a long rest */

function CampSidebar({ state, onExit, onBeginRest, onTalk, talkPartner }) {
  // LIVE party for the active campaign. The camp sidebar has no dedicated surface route, so it
  // reuses the same /character-surface read-model screen-character.jsx polls (it carries `.party`).
  // We never fall back to `state.party` (the non-canonical demo party).
  const surfaceQuery = window.combatSurfaceFromCampaign
    ? window.combatSurfaceFromCampaign(
        (Array.isArray(state?.campaigns) ? state.campaigns : []).find((c) => c.id === state?.activeCampaign) ||
          (Array.isArray(state?.campaigns) ? state.campaigns : [])[0] || {},
        state,
      )
    : "";
  const [surface, setSurface] = React.useState(null);
  const party = Array.isArray(surface?.party) ? surface.party : [];

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    try {
      const response = await fetch("/character-surface" + surfaceQuery, { cache: "no-store" });
      if (!response.ok) throw new Error(`character surface ${response.status}`);
      const payload = await response.json();
      if (!isCancelled()) setSurface(payload);
    } catch (error) { /* keep last good — no demo fallback */ }
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

  // Role assignments (each portrait slot maps to a hero id, or null). Start unassigned —
  // the live party drives this; the old demo party's ids are never present.
  const [roles, setRoles] = React.useState({
    hunting: null,
    camouflage: null,
    cooking: null,
    watch1: null,
    watch2: null,
  });
  const [recipe, setRecipe] = React.useState("hearty");
  const [healing, setHealing] = React.useState("spells");
  const [draggingHero, setDraggingHero] = React.useState(null);
  const talkHero = talkPartner ? party.find((p) => p.id === talkPartner) : null;

  // Get the special-roles cards for any companion not assigned to a primary role
  const assignedIds = new Set(Object.values(roles).filter(Boolean));
  const specialRoles = party
    .filter((p) => !assignedIds.has(p.id))
    .map((p) => ({ hero: p, role: SPECIAL_ROLES[p.id] || { name: "Stand watch", detail: "Quiet hours." } }));

  // Time calculation — hunting=2h, cooking=1h, baseline rest=8h
  const huntingHours = roles.hunting ? 2 : 0;
  const totalHours = 8 + huntingHours;
  const ration = (roles.hunting ? 0 : 5); // hunting → 0 needed; otherwise 5
  const inPack = state?.rations ?? 6;

  const onDrop = (slot) => {
    if (!draggingHero) return;
    setRoles((r) => {
      const newRoles = { ...r };
      // Remove hero from any existing slot
      Object.keys(newRoles).forEach((k) => { if (newRoles[k] === draggingHero) newRoles[k] = null; });
      newRoles[slot] = draggingHero;
      return newRoles;
    });
    setDraggingHero(null);
  };

  const clearSlot = (slot) => setRoles((r) => ({ ...r, [slot]: null }));

  const _badge = { label: "Preview", tone: "muted", detail: "Camp is display-only — role assignments, recipes, and resting are not persisted to the engine." };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0, overflow: "auto" }}>

      {/* Prototype banner */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 14px", background: "rgba(80,50,20,0.18)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.45)", borderRadius: 2 }}>
        <CapabilityBadge capability={_badge} nativeStatus={null} />
        <span className="hand muted" style={{ fontSize: 12 }}>Preview — camp actions are not saved to the engine.</span>
      </div>

      {/* Time progression bar */}
      <Panel framed style={{ padding: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
          <div className="eyebrow">23rd, dusk</div>
          <div className="eyebrow" style={{ color: "var(--crimson)" }}>Camp</div>
          <div className="eyebrow">24th, dawn</div>
        </div>
        <TimelineBar startHour={17} hours={totalHours} />
        <div style={{ marginTop: 6, textAlign: "center" }}>
          <span className="hand" style={{ fontSize: 12, color: "var(--ink-700)" }}>
            Resting will take <strong style={{ color: "var(--crimson)" }}>{totalHours} hours</strong>.
            Wake at <span style={{ color: "var(--ink-900)" }}>{((17 + totalHours) % 24).toString().padStart(2, "0")}:00</span>.
          </span>
        </div>
      </Panel>

      {/* Manage / Rations */}
      <Panel framed style={{ padding: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <SectionTitle>Rations</SectionTitle>
          <BrassButton tone="ghost" size="sm" disabled title="Display-only — ration changes are not saved to the engine">Manage (preview)</BrassButton>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
          <RationStat label="Needed" value={ration} />
          <RationStat label="In pack" value={inPack} />
        </div>
        {ration > inPack && (
          <div className="hand" style={{ fontSize: 12, color: "var(--crimson)", marginTop: 6 }}>
            Hunting is required for a rest.
          </div>
        )}
        <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8, cursor: "pointer" }}>
          <input type="checkbox" defaultChecked style={{ accentColor: "var(--b-400)" }} />
          <span className="body-sm">Use rations</span>
        </label>
      </Panel>

      {/* Healing */}
      <Panel framed style={{ padding: 12 }}>
        <SectionTitle>Healing</SectionTitle>
        <CampRadio value={healing} onChange={setHealing} options={[
          { value: "spells", label: "Use healing spells & abilities before resting", detail: "Spend prepared healing before camp." },
          { value: "natural", label: "Natural healing only", detail: "Slower, no spell cost." },
        ]} />
      </Panel>

      {/* Role slots */}
      <Panel framed style={{ padding: 12 }}>
        <RoleSlot
          slot="hunting"
          icon="H"
          label="Hunting"
          summary="Survival"
          summaryValue="+6"
          detail={roles.hunting ? "Hunting will take 0–2 hours. You will recover 5 rations." : "No hunter. Rations from pack."}
          hero={party.find((p) => p.id === roles.hunting)}
          onDrop={() => onDrop("hunting")}
          onClear={() => clearSlot("hunting")}
          onDragOver={(e) => e.preventDefault()}
        />
        <RoleSlot
          slot="camouflage"
          icon="C"
          label="Camp Camouflage"
          summary="Stealth"
          summaryValue="+0"
          detail="Successful camouflage will reduce the probability of attack."
          hero={party.find((p) => p.id === roles.camouflage)}
          onDrop={() => onDrop("camouflage")}
          onClear={() => clearSlot("camouflage")}
          onDragOver={(e) => e.preventDefault()}
        />
        <RoleSlot
          slot="cooking"
          icon="C"
          label="Cooking"
          summary="Nature"
          summaryValue="+5"
          detail={
            <span>
              {recipe ? RECIPES[recipe].name : "No meal"} <span className="muted">· Cooking DC: 20</span>
              <div className="hand muted" style={{ fontSize: 11, marginTop: 2 }}>{recipe ? RECIPES[recipe].bonus : ""}</div>
            </span>
          }
          hero={party.find((p) => p.id === roles.cooking)}
          onDrop={() => onDrop("cooking")}
          onClear={() => clearSlot("cooking")}
          onDragOver={(e) => e.preventDefault()}
        />
        {roles.cooking && (
          <div style={{ marginTop: 6, padding: "8px 10px", background: "rgba(176,141,87,0.06)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)" }}>
            <div className="eyebrow" style={{ fontSize: 9, marginBottom: 6 }}>Recipes</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {Object.entries(RECIPES).map(([id, r]) => (
                <label key={id} style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12 }}>
                  <input type="radio" name="recipe" checked={recipe === id} onChange={() => setRecipe(id)} style={{ accentColor: "var(--b-400)" }} />
                  <span style={{ color: "var(--ink-800)" }}>{r.name}</span>
                </label>
              ))}
            </div>
          </div>
        )}
      </Panel>

      {/* Special Roles (auto-assigned to idle) */}
      {specialRoles.length > 0 && (
        <Panel framed style={{ padding: 12 }}>
          <SectionTitle>Special Roles</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {specialRoles.map(({ hero, role }) => (
              <SpecialRoleRow key={hero.id} hero={hero} role={role} />
            ))}
          </div>
        </Panel>
      )}

      {/* Watch */}
      <Panel framed style={{ padding: 12 }}>
        <SectionTitle>Watch Order</SectionTitle>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <WatchSlot
            num="I"
            hero={party.find((p) => p.id === roles.watch1)}
            value="+8"
            onDrop={() => onDrop("watch1")}
            onClear={() => clearSlot("watch1")}
            onDragOver={(e) => e.preventDefault()}
          />
          <WatchSlot
            num="II"
            hero={party.find((p) => p.id === roles.watch2)}
            value="+8"
            onDrop={() => onDrop("watch2")}
            onClear={() => clearSlot("watch2")}
            onDragOver={(e) => e.preventDefault()}
          />
        </div>
        <div className="hand muted" style={{ fontSize: 11, marginTop: 6 }}>
          Summary · Perception. The watch will guard during the whole rest.
        </div>
      </Panel>

      {/* Companions — drag source + talk affordance */}
      <Panel framed style={{ padding: 12 }}>
        <SectionTitle right={
          party.length > 0
            ? <span className="muted body-sm" style={{ fontSize: 10 }}>drag to assign</span>
            : null
        }>The Party</SectionTitle>
        {party.length === 0 && (
          <div className="muted body-sm">No party in camp. Camp is empty.</div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
          {party.map((p) => {
            const wantsToTalk = TALK_PROMPTS[p.id]?.openingPrompt;
            const isDragging = draggingHero === p.id;
            const isAssigned = assignedIds.has(p.id);
            return (
              <div key={p.id} style={{ position: "relative", textAlign: "center" }}>
                <button
                  draggable
                  onDragStart={() => setDraggingHero(p.id)}
                  onDragEnd={() => setDraggingHero(null)}
                  onClick={() => wantsToTalk && onTalk(p.id)}
                  style={{
                    width: "100%",
                    padding: 2,
                    background: isAssigned ? "rgba(176,141,87,0.18)" : isDragging ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
                    boxShadow: isAssigned ? "inset 0 0 0 1px var(--b-500)" : "none",
                    cursor: wantsToTalk ? "pointer" : "grab",
                    opacity: isDragging ? 0.4 : 1,
                  }}
                >
                  <Img scope={p.id ? "portrait-" + p.id : ""} label={p.short || "portrait"} w="100%" h={56} framed />
                </button>
                <div className="hand" style={{ fontSize: 10, marginTop: 2, color: "var(--ink-700)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {p.name.split(" ")[0]}
                </div>
                {/* Talk-to-companion quill */}
                {wantsToTalk && (
                  <button
                    onClick={() => onTalk(p.id)}
                    title="Has something to say"
                    style={{
                      position: "absolute", top: -6, right: -6,
                      width: 22, height: 22,
                      borderRadius: "50%",
                      background: "radial-gradient(circle at 30% 30%, var(--gold-glow), var(--b-400))",
                      boxShadow: "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.7), 0 0 12px var(--gold-glow)",
                      color: "var(--w-300)",
                      fontSize: 11,
                      fontFamily: "var(--f-display)",
                      cursor: "pointer",
                      animation: "flicker 2s ease-in-out infinite",
                      zIndex: 5,
                    }}
                  >✦</button>
                )}
              </div>
            );
          })}
        </div>
      </Panel>

      {/* Inline conversation panel */}
      {talkHero && <TalkPanel hero={talkHero} onClose={() => onTalk(null)} />}

      {/* Begin resting */}
      <div style={{ display: "flex", gap: 6, flex: "0 0 auto" }}>
        <BrassButton tone="ghost" size="sm" onClick={onExit}>Leave camp</BrassButton>
        <BrassButton tone="dark" disabled style={{ flex: 1 }} title="Display-only — resting is not yet wired to the engine; nothing is saved">
          ✺ Begin Resting <span style={{ fontSize: 9, opacity: 0.7 }}>(preview)</span>
        </BrassButton>
      </div>
    </div>
  );
}

function TimelineBar({ startHour, hours }) {
  // Render a 24-hour ribbon with day/night gradient and a wax-seal cursor
  const totalSpan = 24;
  const segments = Array.from({ length: totalSpan }).map((_, i) => (startHour + i) % 24);
  return (
    <div style={{ position: "relative", height: 24 }}>
      <div style={{
        position: "absolute", inset: 0,
        background: "linear-gradient(90deg, #8aa8c4 0%, #d4b97a 20%, #f4d27b 35%, #c97a44 55%, #4a2a4a 80%, #1a1838 100%)",
        boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 1px 0 rgba(255,250,220,0.4)",
      }} />
      <div style={{
        position: "absolute", inset: 0,
        display: "grid",
        gridTemplateColumns: `repeat(${totalSpan}, 1fr)`,
      }}>
        {segments.map((h, i) => (
          <div key={i} style={{
            borderRight: i < totalSpan - 1 ? "1px solid rgba(80,50,20,0.3)" : "none",
            fontSize: 7,
            fontFamily: "var(--f-mono)",
            color: "rgba(80,50,20,0.7)",
            display: "flex", alignItems: "flex-end", justifyContent: "center",
            paddingBottom: 2,
          }}>{h}</div>
        ))}
      </div>
      {/* Wax-seal cursor */}
      <div style={{
        position: "absolute",
        left: `${((hours) / totalSpan) * 100}%`,
        top: -8, bottom: -8,
        width: 18,
        transform: "translateX(-50%)",
      }}>
        <div style={{
          width: 18, height: 36, margin: "0 auto",
          background: "linear-gradient(180deg, var(--gold-glow), var(--b-400))",
          boxShadow: "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,255,235,0.8), 0 0 10px var(--gold-glow)",
          display: "grid", placeItems: "center",
          fontFamily: "var(--f-display)",
          fontSize: 10,
          color: "var(--w-300)",
        }}>{hours}</div>
      </div>
    </div>
  );
}

function RationStat({ label, value }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "6px 10px",
      background: "rgba(176,141,87,0.08)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
    }}>
      <div style={{
        width: 24, height: 18,
        background: "radial-gradient(ellipse at 30% 30%, var(--b-200), var(--b-500))",
        boxShadow: "inset 0 0 0 1px var(--b-600)",
        borderRadius: 2,
      }} />
      <div>
        <div className="eyebrow" style={{ fontSize: 8 }}>{label}</div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 16, color: "var(--ink-900)", lineHeight: 1 }}>{value}</div>
      </div>
    </div>
  );
}

function RoleSlot({ icon, label, summary, summaryValue, detail, hero, onDrop, onClear, onDragOver }) {
  return (
    <div
      onDrop={onDrop}
      onDragOver={onDragOver}
      style={{
        display: "grid",
        gridTemplateColumns: "56px 1fr",
        gap: 10,
        padding: 8,
        marginBottom: 6,
        background: hero ? "rgba(176,141,87,0.08)" : "transparent",
        boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
      }}
    >
      {/* Portrait slot */}
      <div style={{ position: "relative" }}>
        {hero ? (
          <>
            <Img scope={hero.id ? "portrait-" + hero.id : ""} label={hero.short || "portrait"} w={56} h={68} framed />
            <button onClick={onClear} style={{
              position: "absolute", top: -6, right: -6,
              width: 18, height: 18, borderRadius: "50%",
              background: "var(--p-100)",
              boxShadow: "inset 0 0 0 1px var(--b-500), 0 1px 2px rgba(0,0,0,0.3)",
              fontSize: 10, color: "var(--ink-700)",
              cursor: "pointer",
            }}>×</button>
          </>
        ) : (
          <div style={{
            width: 56, height: 68,
            background: "rgba(0,0,0,0.05)",
            boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)",
            display: "grid", placeItems: "center",
            color: "var(--b-500)",
            fontFamily: "var(--f-mono)",
            fontSize: 9,
            textAlign: "center",
            padding: 4,
          }}>drag<br/>here</div>
        )}
      </div>

      <div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.06em", color: "var(--ink-900)" }}>
          <span style={{ color: "var(--crimson)", fontFamily: "var(--f-hand)", fontStyle: "italic", fontSize: 20, marginRight: 2 }}>{icon}</span>
          {label}
        </div>
        <div className="hand muted" style={{ fontSize: 11 }}>{summary} <strong style={{ color: hero ? "var(--emerald)" : "var(--ink-500)" }}>{summaryValue}</strong></div>
        <div className="body-sm" style={{ marginTop: 4, color: "var(--ink-700)", lineHeight: 1.35, fontSize: 12 }}>
          {detail}
        </div>
      </div>
    </div>
  );
}

function WatchSlot({ num, hero, value, onDrop, onClear, onDragOver }) {
  return (
    <div
      onDrop={onDrop}
      onDragOver={onDragOver}
      style={{
        position: "relative",
        padding: 6,
        background: hero ? "rgba(176,141,87,0.08)" : "transparent",
        boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
        textAlign: "center",
      }}
    >
      <span style={{
        position: "absolute", top: -8, left: 4,
        background: "linear-gradient(180deg, var(--crimson), #5a1414)",
        color: "var(--p-100)",
        fontFamily: "var(--f-display)",
        fontSize: 9, letterSpacing: "0.16em",
        padding: "2px 6px",
        boxShadow: "inset 0 0 0 1px #2a0606",
      }}>{num}</span>
      {hero ? (
        <>
          <Img scope={hero.id ? "portrait-" + hero.id : ""} label={hero.short || "portrait"} w="100%" h={60} framed />
          <div className="hand" style={{ fontSize: 11, marginTop: 4, color: "var(--ink-700)" }}>
            {hero.name.split(" ")[0]} <span style={{ color: "var(--emerald)" }}>{value}</span>
          </div>
          <button onClick={onClear} style={{
            position: "absolute", top: -6, right: -6,
            width: 18, height: 18, borderRadius: "50%",
            background: "var(--p-100)",
            boxShadow: "inset 0 0 0 1px var(--b-500), 0 1px 2px rgba(0,0,0,0.3)",
            fontSize: 10, color: "var(--ink-700)",
            cursor: "pointer",
          }}>×</button>
        </>
      ) : (
        <div style={{
          height: 60,
          boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)",
          display: "grid", placeItems: "center",
          color: "var(--b-500)",
          fontFamily: "var(--f-mono)", fontSize: 9,
        }}>drag</div>
      )}
    </div>
  );
}

function SpecialRoleRow({ hero, role }) {
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "44px 1fr", gap: 10,
      padding: 8,
      background: "rgba(176,141,87,0.06)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
    }}>
      <Img scope={hero.id ? "portrait-" + hero.id : ""} label={hero.short || "portrait"} w={44} h={54} framed />
      <div>
        <div className="eyebrow" style={{ color: "var(--ink-700)", fontSize: 9 }}>{hero.name.split(" ")[0]}</div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.06em", color: "var(--ink-900)", marginTop: 2 }}>
          {role.name}
        </div>
        <div className="hand muted" style={{ fontSize: 11, marginTop: 2 }}>{role.detail}</div>
      </div>
    </div>
  );
}

function CampRadio({ value, onChange, options }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {options.map((o) => (
        <label key={o.value} style={{
          display: "flex", gap: 8, alignItems: "flex-start",
          padding: "6px 8px",
          background: value === o.value ? "rgba(176,141,87,0.18)" : "transparent",
          boxShadow: value === o.value ? "inset 0 0 0 1px var(--b-500)" : "inset 0 0 0 1px transparent",
          cursor: "pointer",
        }}>
          <input type="radio" checked={value === o.value} onChange={() => onChange(o.value)} style={{ accentColor: "var(--b-400)", marginTop: 4 }} />
          <div>
            <div style={{ fontSize: 12, color: "var(--ink-800)" }}>{o.label}</div>
            {o.detail && <div className="hand muted" style={{ fontSize: 11 }}>{o.detail}</div>}
          </div>
        </label>
      ))}
    </div>
  );
}

function TalkPanel({ hero, onClose }) {
  if (!hero) return null;
  const conv = TALK_PROMPTS[hero.id] || TALK_PROMPTS._default;
  const [reply, setReply] = React.useState(null);

  return (
    <Panel framed style={{ padding: 14, background: "linear-gradient(180deg, var(--w-100), var(--w-300))", color: "var(--p-200)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
        <div>
          <div className="eyebrow" style={{ color: "var(--gold-glow)" }}>At the fire</div>
          <div style={{ fontFamily: "var(--f-display)", fontSize: 15, letterSpacing: "0.06em", color: "var(--p-100)" }}>
            With {hero.name.split(" ")[0]}
          </div>
        </div>
        <button onClick={onClose} className="icon-btn" style={{ width: 22, height: 22, color: "var(--b-200)" }}>×</button>
      </div>

      <div style={{
        display: "grid", gridTemplateColumns: "60px 1fr", gap: 10, marginBottom: 10,
      }}>
        <Img scope={hero.id ? "portrait-" + hero.id : ""} label={hero.short || "portrait"} w={60} h={72} framed />
        <div className="body" style={{ color: "var(--p-200)", fontSize: 14, fontStyle: "italic", lineHeight: 1.4 }}>
          <span style={{ color: "var(--crimson-bright)", fontSize: 22, fontFamily: "var(--f-display)" }}>"</span>
          {reply ? reply.heroReply : conv.openingPrompt}
        </div>
      </div>

      {!reply && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {conv.responses.map((r, i) => (
            <button key={i} onClick={() => setReply(r)} style={{
              padding: "8px 10px",
              textAlign: "left",
              background: "rgba(176,141,87,0.12)",
              boxShadow: "inset 0 0 0 1px var(--b-500)",
              color: "var(--p-100)",
              cursor: "pointer",
              fontSize: 13,
              transition: "all 140ms",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(244,210,123,0.2)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "rgba(176,141,87,0.12)"; }}
            >
              <span style={{ color: "var(--crimson-bright)", fontFamily: "var(--f-display)", marginRight: 6, fontSize: 12 }}>{i + 1}.</span>
              {r.tag && <span className="pill" style={{ marginRight: 6, background: r.tag === "Lawful Good" ? "var(--royal)" : "var(--crimson)", color: "var(--p-100)", boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.4)" }}>{r.tag}</span>}
              {r.text}
            </button>
          ))}
        </div>
      )}

      {reply && (
        <div style={{ marginTop: 8, display: "flex", justifyContent: "flex-end" }}>
          <BrassButton size="sm" tone="ghost" onClick={onClose}>Bank the fire</BrassButton>
        </div>
      )}
    </Panel>
  );
}

// Camp-duty flavor keyed by hero id. The live party supplies its own ids; without a per-hero
// entry every idle companion falls back to the generic "Stand watch" card above — no demo names.
const SPECIAL_ROLES = {};

const RECIPES = {
  hearty: { name: "Hearty Meal", bonus: "+2 Constitution saving throws until next rest." },
  pheasant: { name: "Roast Pheasant", bonus: "Advantage on your first attack roll until next rest." },
  stew: { name: "Trail Stew", bonus: "Regain 1 extra Hit Die on your next long rest." },
};

// Fireside conversations keyed by hero id. The live party supplies its own ids, so without a
// per-hero entry the talk affordance stays hidden and TalkPanel uses the generic prompt below —
// no demo companions are invented here.
const TALK_PROMPTS = {
  _default: {
    openingPrompt: "Sit. The fire is low. There is room.",
    responses: [
      { text: "I'll sit a while.", heroReply: "Good. We do not have to fill it." },
      { text: "Walk with me first.", heroReply: "Then walk." },
    ],
  },
};

Object.assign(window, { CampSidebar, RoleSlot, WatchSlot, RationStat, TimelineBar, SpecialRoleRow, CampRadio, TalkPanel, SPECIAL_ROLES, RECIPES, TALK_PROMPTS });
