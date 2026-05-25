/* Camp Sidebar — Pathfinder Kingmaker-style party role assignment */

function CampSidebar({ state, onExit, onBeginRest, onTalk, talkPartner }) {
  // Role assignments (each portrait slot maps to a hero id, or null)
  const [roles, setRoles] = React.useState({
    hunting: "mira",
    camouflage: null,
    cooking: "linzi",
    watch1: "cassian",
    watch2: "vell",
  });
  const [recipe, setRecipe] = React.useState("hearty");
  const [healing, setHealing] = React.useState("spells");
  const [draggingHero, setDraggingHero] = React.useState(null);

  // Get the special-roles cards for any companion not assigned to a primary role
  const assignedIds = new Set(Object.values(roles).filter(Boolean));
  const specialRoles = state.party
    .filter((p) => !assignedIds.has(p.id))
    .map((p) => ({ hero: p, role: SPECIAL_ROLES[p.id] || { name: "Stand watch", detail: "Quiet hours." } }));

  // Time calculation — hunting=2h, cooking=1h, baseline rest=8h
  const huntingHours = roles.hunting ? 2 : 0;
  const totalHours = 8 + huntingHours;
  const ration = (roles.hunting ? 0 : 5); // hunting → 0 needed; otherwise 5

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

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0, overflow: "auto" }}>

      {/* Time progression bar */}
      <Panel framed style={{ padding: 12 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
          <div className="eyebrow">23 Gozran</div>
          <div className="eyebrow" style={{ color: "var(--crimson)" }}>Camp</div>
          <div className="eyebrow">24 Gozran</div>
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
          <BrassButton tone="ghost" size="sm">Manage</BrassButton>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
          <RationStat label="Needed" value={ration} />
          <RationStat label="In pack" value={6} />
        </div>
        {ration > 6 && (
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
          { value: "spells", label: "Use healing spells & abilities before resting", detail: "Cassian: 2 cure light." },
          { value: "natural", label: "Natural healing only", detail: "Slower, no spell cost." },
        ]} />
      </Panel>

      {/* Role slots */}
      <Panel framed style={{ padding: 12 }}>
        <RoleSlot
          slot="hunting"
          icon="H"
          label="Hunting"
          summary="Lore (Nature)"
          summaryValue="+6"
          detail={roles.hunting ? "Hunting will take 0–2 hours. You will recover 5 rations." : "No hunter. Rations from pack."}
          hero={state.party.find((p) => p.id === roles.hunting)}
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
          hero={state.party.find((p) => p.id === roles.camouflage)}
          onDrop={() => onDrop("camouflage")}
          onClear={() => clearSlot("camouflage")}
          onDragOver={(e) => e.preventDefault()}
        />
        <RoleSlot
          slot="cooking"
          icon="C"
          label="Cooking"
          summary="Knowledge (World)"
          summaryValue="+5"
          detail={
            <span>
              {recipe ? RECIPES[recipe].name : "No meal"} <span className="muted">· Cooking DC: 20</span>
              <div className="hand muted" style={{ fontSize: 11, marginTop: 2 }}>{recipe ? RECIPES[recipe].bonus : ""}</div>
            </span>
          }
          hero={state.party.find((p) => p.id === roles.cooking)}
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
            hero={state.party.find((p) => p.id === roles.watch1)}
            value="+8"
            onDrop={() => onDrop("watch1")}
            onClear={() => clearSlot("watch1")}
            onDragOver={(e) => e.preventDefault()}
          />
          <WatchSlot
            num="II"
            hero={state.party.find((p) => p.id === roles.watch2)}
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
          <span className="muted body-sm" style={{ fontSize: 10 }}>drag to assign</span>
        }>The Party</SectionTitle>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
          {state.party.map((p) => {
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
                  <Placeholder label={p.short || "portrait"} w="100%" h={56} framed />
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
      {talkPartner && <TalkPanel hero={state.party.find((p) => p.id === talkPartner)} onClose={() => onTalk(null)} />}

      {/* Begin resting */}
      <div style={{ display: "flex", gap: 6, flex: "0 0 auto" }}>
        <BrassButton tone="ghost" size="sm" onClick={onExit}>Leave camp</BrassButton>
        <BrassButton tone="dark" onClick={onBeginRest} style={{ flex: 1 }}>
          ✺ Begin Resting
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
            <Placeholder label={hero.short || "portrait"} w={56} h={68} framed />
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
          <Placeholder label={hero.short || "portrait"} w="100%" h={60} framed />
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
      <Placeholder label={hero.short || "portrait"} w={44} h={54} framed />
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
        <Placeholder label={hero.short || "portrait"} w={60} h={72} framed />
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

const SPECIAL_ROLES = {
  cassian: { name: "Maintain Armor", detail: "Sees to the buckles. +1 AC for tomorrow." },
  vell: { name: "Sharpen Weapons", detail: "Iron on whetstone. +1 damage for tomorrow." },
  mira: { name: "Patrol Perimeter", detail: "Quiet steps. -2 to ambush chance." },
  linzi: { name: "Inspire Competence", detail: "Linzi's enthusiasm gives camp-duty rolls a +2 competence bonus." },
};

const RECIPES = {
  hearty: { name: "Hearty Meal", bonus: "+2 Fortitude saves until next rest." },
  pheasant: { name: "Roast Pheasant", bonus: "+1 to all attack rolls until next rest." },
  stew: { name: "Trail Stew", bonus: "+1 hp/level on next long rest." },
};

const TALK_PROMPTS = {
  cassian: {
    openingPrompt: "I will tell you a thing I have not told the others — I have not drawn a blade in a Warden hall in seven years. I am not certain whether that is honour or its imitation.",
    responses: [
      { tag: "Lawful Good", text: "Honour. There is no version of that that is imitation.", heroReply: "You make me want to believe you. That is a useful thing in a captain." },
      { tag: "Chaotic", text: "It is whatever you need it to be tomorrow. Tonight it is rest.", heroReply: "You are a poor philosopher and a good companion. I will take both." },
      { text: "Why have you not drawn?", heroReply: "Because the one I would have drawn against was my teacher. He died first. I was not given the chance to learn whether I would have." },
    ],
  },
  mira: {
    openingPrompt: "I have written you down badly twice this week. The first time I called you brave. The second I left out an adjective entirely. I am embarrassed about both.",
    responses: [
      { text: "Brave is not wrong.", heroReply: "Brave is what people put in margins. I am trying for the body of the page." },
      { tag: "Chaotic", text: "Leave more adjectives out. I am tired of them.", heroReply: "That is the most useful thing you have said to me. I will start tonight." },
      { text: "Read me what you have so far.", heroReply: "Tomorrow. I want to fix one more thing before you hear it as written." },
    ],
  },
  vell: {
    openingPrompt: "The Iron-Shod taught me one prayer. I do not say it any more. I was wondering if you would mind me telling you why.",
    responses: [
      { text: "Tell me.", heroReply: "The prayer asks the stone to remember you. The stone does not need to be asked. That is the whole of it." },
      { tag: "Lawful Good", text: "If you do not wish to say it, you do not have to.", heroReply: "I thank you for that. I think I will say it tomorrow regardless. To the camp, before we leave it. So it has been said once." },
      { text: "Why did you leave them?", heroReply: "Because I asked a question. They thought it impolite. I thought it the only question. We were both correct." },
    ],
  },
  linzi: {
    openingPrompt: "This is the part of the night when I write down what we did today. I am asking your permission, formally, before I write down what you did today.",
    responses: [
      { text: "Write what you saw.", heroReply: "I always do. I will note that you said so." },
      { tag: "Chaotic", text: "Write down what I would have done if I were braver.", heroReply: "I will write down what you did. The chronicle is generous about counterfactuals when read aloud, not when read." },
      { text: "Don't write the part with the door.", heroReply: "I will write the door. I will not write what you said about it. That is a compromise." },
    ],
  },
  _default: {
    openingPrompt: "Sit. The fire is low. There is room.",
    responses: [
      { text: "I'll sit a while.", heroReply: "Good. We do not have to fill it." },
      { text: "Walk with me first.", heroReply: "Then walk." },
    ],
  },
};

Object.assign(window, { CampSidebar, RoleSlot, WatchSlot, RationStat, TimelineBar, SpecialRoleRow, CampRadio, TalkPanel, SPECIAL_ROLES, RECIPES, TALK_PROMPTS });
