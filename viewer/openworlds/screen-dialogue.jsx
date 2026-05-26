/* Screen: Parley — sheet-correct social options over scene art.
   Wired to the live /parley-surface read model (the UI side of #141): the lead PC's
   alignment + per-skill {skill, modifier, suggested_dc} + a free-form path. Polls every
   5s while visible. These are SLOTS, not authored lines — the player picks an approach
   and the DM (via the engine) voices + adjudicates it. Falls back to the demo scene tree
   only if no snapshot resolves. Scene chrome / dialogue-panel design unchanged. */

const DIFFICULTY_OPTIONS = ["easy", "medium", "hard"];

function ScreenDialogue({ onNavigate, state, setState }) {
  const surfaceQuery = window.combatSurfaceFromCampaign
    ? window.combatSurfaceFromCampaign(
        (Array.isArray(state?.campaigns) ? state.campaigns : []).find((c) => c.id === state?.activeCampaign) ||
          (Array.isArray(state?.campaigns) ? state.campaigns : [])[0] || {},
        state,
      )
    : "";
  const [surface, setSurface] = React.useState(null);
  const [difficulty, setDifficulty] = React.useState("medium");
  const [history, setHistory] = React.useState([]);
  const [status, setStatus] = React.useState("loading");
  const toast = window.useToast ? window.useToast() : (() => {});

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    const sep = surfaceQuery ? "&" : "?";
    const url = `/parley-surface${surfaceQuery}${sep}difficulty=${encodeURIComponent(difficulty)}`;
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`parley surface ${response.status}`);
      const payload = await response.json();
      if (isCancelled()) return;
      setSurface(payload);
      setStatus("ready");
    } catch (error) {
      if (isCancelled()) return;
      setStatus(error?.message || "unavailable");
    }
  }, [surfaceQuery, difficulty]);

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

  const slots = Array.isArray(surface?.skills) ? surface.skills : [];
  const hasParley = Boolean(surface && surface.actor && slots.length);
  if (hasParley) {
    return <ParleyMenu
      surface={surface}
      slots={slots}
      difficulty={difficulty}
      setDifficulty={setDifficulty}
      history={history}
      setHistory={setHistory}
      onNavigate={onNavigate}
      toast={toast}
    />;
  }

  // No live parley (no snapshot / no actor): keep the prototype choice tree as a graceful
  // demo fallback so the screen still reads instead of going blank.
  return <ScreenDialogueDemo onNavigate={onNavigate} state={state} status={status} />;
}

function ParleyMenu({ surface, slots, difficulty, setDifficulty, history, setHistory, onNavigate, toast }) {
  const actorName = surface.actor || "Hero";
  const canAct = Boolean(surface.can_act);
  const sceneScope = surface.location_id ||
    (surface.event?.anchor_npc_id ? `portrait-${surface.event.anchor_npc_id}` : null);

  const pick = (slot) => {
    const move = { kind: "check", name: `${slot.label} (DC ${slot.suggested_dc})`, skill: slot.skill, dc: slot.suggested_dc, text: `attempts ${slot.label} (DC ${slot.suggested_dc})` };
    setHistory((h) => [...h, { skill: slot.label, dc: slot.suggested_dc, mod: slot.modifier }]);
    if (!canAct) {
      toast({ kind: "danger", eyebrow: "Parley", title: "Read-only", body: "This view can't land moves. The DM voices the chosen approach." });
      return;
    }
    fetch("/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...move, campaign: surface.campaign_id || "" }),
    }).then((r) => r.json().catch(() => ({}))).then((payload) => {
      if (payload && payload.ok === false) throw new Error(payload.reason || "move rejected");
      toast({ kind: "item", eyebrow: "Parley", title: `${actorName} — ${slot.label}`, body: `Requested a ${slot.label} check at DC ${slot.suggested_dc}.` });
    }).catch((e) => toast({ kind: "danger", title: "Move not sent", body: e?.message || "The viewer could not reach /move." }));
  };

  const freeForm = () => {
    if (!canAct) {
      toast({ kind: "danger", eyebrow: "Parley", title: "Read-only", body: "The DM voices the free-form path." });
      return;
    }
    fetch("/move", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "say", text: `${actorName} speaks their own way`, campaign: surface.campaign_id || "" }),
    }).then(() => toast({ kind: "item", eyebrow: "Parley", title: "Free-form", body: "Speak it at the table — the DM adjudicates." }))
      .catch((e) => toast({ kind: "danger", title: "Move not sent", body: e?.message || "unreachable" }));
  };

  return (
    <div className="screen" style={{ height: "100%", position: "relative", padding: 14 }}>
      {/* Scene backdrop */}
      <div style={{ position: "relative", height: "100%", overflow: "hidden", borderRadius: 4, boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 0 4px var(--b-500), inset 0 0 0 5px var(--b-300), inset 0 0 0 6px var(--b-500)" }}>
        <Img scope={sceneScope} label={`scene · parley · ${surface.title || "the table"}`} w="100%" h="100%" fit="cover" style={{ position: "absolute", inset: 0 }} />

        {/* Vignette */}
        <div style={{
          position: "absolute", inset: 0,
          background: "radial-gradient(ellipse at 50% 40%, transparent 30%, rgba(20, 10, 4, 0.7) 100%)",
          pointerEvents: "none",
        }} />

        {/* Candle glows */}
        <div className="candleglow" style={{ width: 280, height: 280, left: "10%", top: "20%" }} />
        <div className="candleglow" style={{ width: 200, height: 200, right: "15%", top: "40%", animationDelay: "1s" }} />

        {/* Top breadcrumb */}
        <div style={{
          position: "absolute", top: 18, left: 18, right: 18,
          display: "flex", justifyContent: "space-between", alignItems: "flex-start",
          zIndex: 3,
        }}>
          <div style={{
            padding: "8px 16px",
            background: "linear-gradient(180deg, var(--w-100), var(--w-300))",
            color: "var(--b-200)",
            boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 1px 0 rgba(255,220,170,0.2)",
            fontFamily: "var(--f-display)",
            fontSize: 11,
            letterSpacing: "0.22em",
            textTransform: "uppercase",
          }}>
            Parley · {surface.dayLabel || "the table"}
          </div>
          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span className="pill" style={{ background: "rgba(20,10,4,0.55)", color: "var(--p-100)", boxShadow: "inset 0 0 0 1px var(--b-500)" }}>
              {canAct ? "Live" : "Read-only"}
            </span>
            {DIFFICULTY_OPTIONS.map((d) => (
              <button key={d} onClick={() => setDifficulty(d)} className="btn sm" style={{
                textTransform: "capitalize",
                background: difficulty === d ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(20,10,4,0.5)",
                color: difficulty === d ? "var(--w-300)" : "var(--p-100)",
                boxShadow: difficulty === d ? "inset 0 0 0 1px var(--b-600)" : "inset 0 0 0 1px rgba(176,141,87,0.4)",
              }}>{d}</button>
            ))}
          </div>
        </div>

        {/* Parley panel — bottom */}
        <div style={{ position: "absolute", bottom: 18, left: 18, right: 18, zIndex: 3 }}>
          <div style={{
            display: "grid",
            gridTemplateColumns: "160px 1fr",
            gap: 0,
            background: "linear-gradient(180deg, var(--p-100), var(--p-200))",
            boxShadow:
              "inset 0 0 0 1px var(--b-600), inset 0 0 0 4px var(--p-100), inset 0 0 0 5px var(--b-400), 0 12px 30px rgba(0,0,0,0.5)",
          }}>

            {/* Actor portrait (left) */}
            <div style={{ padding: 14, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", borderRight: "1px solid rgba(140,100,60,0.35)" }}>
              <Img scope={surface.event?.anchor_npc_id ? "portrait-" + surface.event.anchor_npc_id : ""} label={actorName} w={120} h={150} framed />
              <div style={{ marginTop: 8, fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-900)", textAlign: "center" }}>
                {actorName}
              </div>
              {surface.alignment && <div className="hand muted" style={{ fontSize: 12, textAlign: "center" }}>{surface.alignment}</div>}
            </div>

            {/* Body — skill slots */}
            <div style={{ padding: "18px 22px", minHeight: 240, display: "flex", flexDirection: "column" }}>
              <div className="hand" style={{ fontSize: 14, color: "var(--ink-600)", marginBottom: 6 }}>
                How does {actorName.split(" ")[0]} approach this? These are SLOTS, sheet-correct — the DM voices the line and adjudicates the roll.
              </div>

              <div style={{ flex: 1 }} />

              {/* Skill slot choices */}
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {slots.map((slot, i) => (
                  <button key={slot.skill} onClick={() => pick(slot)} style={{
                    display: "grid",
                    gridTemplateColumns: "24px 1fr auto auto",
                    gap: 10, alignItems: "center",
                    padding: "8px 12px",
                    textAlign: "left",
                    background: "transparent",
                    boxShadow: "inset 0 -1px 0 rgba(140,100,60,0.25)",
                    cursor: "pointer",
                    transition: "all 140ms",
                    fontSize: 15,
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "rgba(176,141,87,0.12)";
                    e.currentTarget.style.boxShadow = "inset 0 0 0 1px var(--b-500), 0 0 16px -6px var(--gold-glow)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "transparent";
                    e.currentTarget.style.boxShadow = "inset 0 -1px 0 rgba(140,100,60,0.25)";
                  }}>
                    <span style={{ fontFamily: "var(--f-display)", color: "var(--crimson)", fontSize: 14 }}>{i + 1}.</span>
                    <span className="body" style={{ color: "var(--ink-800)" }}>
                      {slot.label}
                      {slot.expertise ? <span className="hand muted" style={{ fontSize: 11, marginLeft: 6 }}>expertise</span>
                        : slot.proficient ? <span className="hand muted" style={{ fontSize: 11, marginLeft: 6 }}>proficient</span> : null}
                    </span>
                    <span className="pill" style={{
                      background: slot.core ? "var(--royal)" : "var(--emerald)",
                      color: "var(--p-100)",
                      boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.15)",
                    }}>{slot.modifier >= 0 ? "+" : ""}{slot.modifier}</span>
                    <span style={{ fontFamily: "var(--f-display)", color: "var(--b-500)", fontSize: 12, letterSpacing: "0.1em" }}>
                      DC {slot.suggested_dc}
                    </span>
                  </button>
                ))}

                {/* Free-form path — always present (free_form is always true). */}
                <button onClick={freeForm} style={{
                  display: "grid", gridTemplateColumns: "24px 1fr auto", gap: 10, alignItems: "center",
                  padding: "8px 12px", textAlign: "left", background: "transparent",
                  boxShadow: "inset 0 -1px 0 rgba(140,100,60,0.25)", cursor: "pointer", fontSize: 15,
                }}
                onMouseEnter={(e) => { e.currentTarget.style.background = "rgba(176,141,87,0.12)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>
                  <span style={{ fontFamily: "var(--f-display)", color: "var(--crimson)", fontSize: 14 }}>✦</span>
                  <span className="body" style={{ color: "var(--ink-800)", fontStyle: "italic" }}>Speak freely — your own words</span>
                  <span style={{ color: "var(--b-500)", fontFamily: "var(--f-display)", fontSize: 12 }}>free-form</span>
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Right side rail — chosen approaches this parley */}
        {history.length > 0 && (
          <div style={{
            position: "absolute", top: 70, right: 18, width: 240,
            maxHeight: "55%", overflow: "auto",
            background: "linear-gradient(180deg, var(--w-100), var(--w-300))",
            color: "var(--p-200)",
            padding: 14,
            boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 0 3px var(--w-200), inset 0 0 0 4px var(--b-500)",
            zIndex: 3,
          }}>
            <div className="eyebrow" style={{ color: "var(--b-200)", marginBottom: 8 }}>Approaches taken</div>
            {history.slice(-8).map((h, i) => (
              <div key={i} style={{ marginBottom: 8, paddingBottom: 8, borderBottom: "1px dashed rgba(176,141,87,0.25)" }}>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 10, letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--b-200)" }}>
                  {h.skill}
                </div>
                <div className="hand" style={{ fontSize: 11, color: "var(--b-300)", marginTop: 2 }}>
                  {h.mod >= 0 ? "+" : ""}{h.mod} vs DC {h.dc}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ScreenDialogueDemo({ onNavigate, state, status }) {
  const [nodeId, setNodeId] = React.useState("start");
  const [history, setHistory] = React.useState([]);
  const node = DIALOGUE[nodeId];
  const listener = (Array.isArray(state?.party) ? state.party : [])[0] || { short: "Hero", name: "Hero" };

  const choose = (choice) => {
    setHistory((h) => [...h, { node, choice }]);
    if (choice.goto && DIALOGUE[choice.goto]) {
      setNodeId(choice.goto);
    } else if (choice.action === "leave") {
      onNavigate("table");
    }
  };

  const reset = () => { setHistory([]); setNodeId("start"); };

  return (
    <div className="screen" style={{ height: "100%", position: "relative", padding: 14 }}>
      {/* Scene backdrop */}
      <div style={{ position: "relative", height: "100%", overflow: "hidden", borderRadius: 4, boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 0 4px var(--b-500), inset 0 0 0 5px var(--b-300), inset 0 0 0 6px var(--b-500)" }}>
        <Img scope={node.locationScope || null} label="scene · oleg's trading post · interior · candlelit · 3 figures at table" w="100%" h="100%" fit="cover" style={{ position: "absolute", inset: 0 }} />

        {/* Vignette */}
        <div style={{
          position: "absolute", inset: 0,
          background: "radial-gradient(ellipse at 50% 40%, transparent 30%, rgba(20, 10, 4, 0.7) 100%)",
          pointerEvents: "none",
        }} />

        {/* Candle glows */}
        <div className="candleglow" style={{ width: 280, height: 280, left: "10%", top: "20%" }} />
        <div className="candleglow" style={{ width: 200, height: 200, right: "15%", top: "40%", animationDelay: "1s" }} />

        {/* Top breadcrumb */}
        <div style={{
          position: "absolute", top: 18, left: 18, right: 18,
          display: "flex", justifyContent: "space-between", alignItems: "flex-start",
          zIndex: 3,
        }}>
          <div style={{
            padding: "8px 16px",
            background: "linear-gradient(180deg, var(--w-100), var(--w-300))",
            color: "var(--b-200)",
            boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 1px 0 rgba(255,220,170,0.2)",
            fontFamily: "var(--f-display)",
            fontSize: 11,
            letterSpacing: "0.22em",
            textTransform: "uppercase",
          }}>
            {status === "ready" ? "No live parley · demo" : "Oleg's Trading Post · evening"}
          </div>
          <button onClick={reset} className="btn dark sm">↻ Restart</button>
        </div>

        {/* Dialogue panel — bottom */}
        <div style={{
          position: "absolute", bottom: 18, left: 18, right: 18,
          zIndex: 3,
        }}>
          <div style={{
            display: "grid",
            gridTemplateColumns: "160px 1fr 160px",
            gap: 0,
            background: "linear-gradient(180deg, var(--p-100), var(--p-200))",
            boxShadow:
              "inset 0 0 0 1px var(--b-600), inset 0 0 0 4px var(--p-100), inset 0 0 0 5px var(--b-400), 0 12px 30px rgba(0,0,0,0.5)",
          }}>

            {/* Speaker portrait (left) */}
            <div style={{ padding: 14, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", borderRight: "1px solid rgba(140,100,60,0.35)" }}>
              <Placeholder label={node.speakerShort} w={120} h={150} framed />
              <div style={{ marginTop: 8, fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-900)", textAlign: "center" }}>
                {node.speaker}
              </div>
              <div className="hand muted" style={{ fontSize: 12, textAlign: "center" }}>{node.role}</div>
            </div>

            {/* Body */}
            <div style={{ padding: "18px 22px", minHeight: 240, display: "flex", flexDirection: "column" }}>
              {node.narration && (
                <div className="hand" style={{ fontSize: 14, color: "var(--ink-600)", marginBottom: 10 }}>
                  {node.narration}
                </div>
              )}

              <div className="body" style={{ fontSize: 17, fontStyle: "italic", color: "var(--ink-800)", marginBottom: 14, position: "relative", paddingLeft: 16 }}>
                <span style={{ position: "absolute", left: 0, top: -4, fontSize: 32, color: "var(--crimson)", fontFamily: "var(--f-display)", lineHeight: 1 }}>"</span>
                {node.text}
              </div>

              <div style={{ flex: 1 }} />

              {/* Choices */}
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {node.choices.map((c, i) => (
                  <button key={i} onClick={() => choose(c)} style={{
                    display: "grid",
                    gridTemplateColumns: "24px auto 1fr auto",
                    gap: 10, alignItems: "center",
                    padding: "8px 12px",
                    textAlign: "left",
                    background: "transparent",
                    boxShadow: "inset 0 -1px 0 rgba(140,100,60,0.25)",
                    cursor: "pointer",
                    transition: "all 140ms",
                    fontSize: 15,
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "rgba(176,141,87,0.12)";
                    e.currentTarget.style.boxShadow = "inset 0 0 0 1px var(--b-500), 0 0 16px -6px var(--gold-glow)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "transparent";
                    e.currentTarget.style.boxShadow = "inset 0 -1px 0 rgba(140,100,60,0.25)";
                  }}>
                    <span style={{ fontFamily: "var(--f-display)", color: "var(--crimson)", fontSize: 14 }}>
                      {i + 1}.
                    </span>
                    {c.tag && (
                      <span className="pill" style={{
                        background: c.tag === "Lawful Good" ? "var(--royal)" :
                                    c.tag === "Chaotic" ? "var(--crimson)" :
                                    c.tag === "Skill" ? "var(--emerald)" :
                                    "transparent",
                        color: c.tag ? "var(--p-100)" : "var(--ink-700)",
                        boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.15)",
                      }}>{c.tag}</span>
                    )}
                    <span className="body" style={{ color: "var(--ink-800)" }}>{c.text}</span>
                    <span style={{ color: "var(--b-500)", fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.1em" }}>
                      {c.hint || "→"}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* Listener (right) */}
            <div style={{ padding: 14, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", borderLeft: "1px solid rgba(140,100,60,0.35)" }}>
              <Img scope={listener.id ? "portrait-" + listener.id : ""} label={listener.short} w={120} h={150} framed />
              <div style={{ marginTop: 8, fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-900)", textAlign: "center" }}>
                {listener.name}
              </div>
              <div className="hand muted" style={{ fontSize: 12, textAlign: "center" }}>Player Hero</div>
            </div>
          </div>
        </div>

        {/* Right side rail — history */}
        {history.length > 0 && (
          <div style={{
            position: "absolute", top: 70, right: 18, width: 260,
            maxHeight: "55%", overflow: "auto",
            background: "linear-gradient(180deg, var(--w-100), var(--w-300))",
            color: "var(--p-200)",
            padding: 14,
            boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 0 3px var(--w-200), inset 0 0 0 4px var(--b-500)",
            zIndex: 3,
          }}>
            <div className="eyebrow" style={{ color: "var(--b-200)", marginBottom: 8 }}>What was said</div>
            {history.slice(-6).map((h, i) => (
              <div key={i} style={{ marginBottom: 8, paddingBottom: 8, borderBottom: "1px dashed rgba(176,141,87,0.25)" }}>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 9, letterSpacing: "0.2em", textTransform: "uppercase", color: "var(--b-200)" }}>
                  {h.node.speaker}
                </div>
                <div style={{ fontFamily: "var(--f-body)", fontSize: 12, color: "var(--p-200)", marginTop: 2, fontStyle: "italic", opacity: 0.85 }}>
                  "{h.node.text.slice(0, 90)}{h.node.text.length > 90 ? "…" : ""}"
                </div>
                <div className="hand" style={{ fontSize: 11, color: "var(--b-300)", marginTop: 4 }}>
                  → {h.choice.text.slice(0, 80)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const DIALOGUE = {
  start: {
    speaker: "Svetlana Leveton",
    role: "Trader's Wife",
    speakerShort: "S·portrait",
    narration: "Svetlana folds a stained cloth across her lap, then places it carefully aside. Her hands have not stopped moving since you sat down.",
    text: "There are a few monsters among the leadership, especially those close to the Stag Lord. Auchs and Dovan from Nisroch come to mind. They like to make a show of their tortures. My husband and I... we saw the bodies.",
    choices: [
      { tag: "Lawful Good", text: "You have nothing to fear. I'll help you deal with the attack.", hint: "+rep", goto: "agree" },
      { tag: "Chaotic", text: "I'll help. I'm not really interested in your post, but I never back down from a fight.", goto: "agree_chaotic" },
      { tag: "Skill", text: "[Persuasion 14] Tell me everything you remember. How many were there?", hint: "DC 14", goto: "persuade" },
      { text: "They claimed they were collecting taxes. Why?", goto: "taxes" },
      { text: "Enough about the bandits. Tell me about the road south.", goto: "road" },
      { text: "I've heard enough. We'll talk again later.", hint: "exit", action: "leave" },
    ],
  },
  agree: {
    speaker: "Svetlana Leveton",
    role: "Trader's Wife",
    speakerShort: "S·portrait",
    narration: "Her shoulders drop a finger's width. It is the closest thing to gratitude she will let herself show today.",
    text: "May the Inheritor walk with you. Oleg will pretend he is not relieved — that is his way. Sleep in the back room tonight. We have stew, and the watch is mine.",
    choices: [
      { text: "We'll camp on the roof. Better sightlines.", goto: "tactics" },
      { text: "Tell me what you know of the bandits' patterns.", goto: "persuade" },
      { text: "We rest. We're done talking.", hint: "exit", action: "leave" },
    ],
  },
  agree_chaotic: {
    speaker: "Svetlana Leveton",
    role: "Trader's Wife",
    speakerShort: "S·portrait",
    narration: "She studies you the way a merchant studies a coin offered by a stranger.",
    text: "A fight then. That is honest, at least. I have known worse partners.",
    choices: [
      { text: "What do they take, when they come?", goto: "taxes" },
      { text: "We sleep here. Wake us when you hear hooves.", hint: "exit", action: "leave" },
    ],
  },
  persuade: {
    speaker: "Svetlana Leveton",
    role: "Trader's Wife",
    speakerShort: "S·portrait",
    narration: "She closes her eyes a moment, counting. The number she opens them with is not the first one she landed on.",
    text: "Six the last time. Eight the time before. They come on the third night of the new moon — that is in two nights, by my reckoning. The big one calls himself Falgrim. He rides a piebald gelding that limps on the off-fore.",
    choices: [
      { text: "We'll be ready. Set the lanterns where I tell you.", goto: "tactics" },
      { text: "Two nights is enough.", hint: "exit", action: "leave" },
    ],
  },
  taxes: {
    speaker: "Svetlana Leveton",
    role: "Trader's Wife",
    speakerShort: "S·portrait",
    text: "Taxes were the word, but it was furs they wanted, then iron, then anything that shines. The Stag Lord makes a kingdom out of other people's pockets.",
    choices: [
      { text: "Where does it all go?", goto: "stag_lord" },
      { tag: "Lawful Good", text: "Not anymore.", goto: "agree" },
      { text: "I've heard enough.", hint: "exit", action: "leave" },
    ],
  },
  road: {
    speaker: "Svetlana Leveton",
    role: "Trader's Wife",
    speakerShort: "S·portrait",
    text: "The south road goes to Tatzlford if you can find it. Mostly people don't. The fork at the broken alder is the right one — the left one ends at a cairn that wasn't there last spring.",
    choices: [
      { text: "Whose cairn?", goto: "stag_lord" },
      { text: "We'll watch for the alder. Thank you.", hint: "exit", action: "leave" },
    ],
  },
  stag_lord: {
    speaker: "Svetlana Leveton",
    role: "Trader's Wife",
    speakerShort: "S·portrait",
    narration: "Her voice drops. The candle on the table chooses now to lean.",
    text: "Nobody knows. Or nobody who has come back has said. The bandits speak his name like a prayer they don't believe in. That is the part that frightens me most.",
    choices: [
      { tag: "Lawful Good", text: "We will end him.", goto: "agree" },
      { text: "Then we will see what comes back from his hall.", hint: "exit", action: "leave" },
    ],
  },
  tactics: {
    speaker: "Svetlana Leveton",
    role: "Trader's Wife",
    speakerShort: "S·portrait",
    narration: "She nods once. Decisively.",
    text: "Then it is settled. Oleg will hate it, and he will pretend the hating is what saved us. Sleep when you can. The crows will tell us when they come.",
    choices: [
      { text: "We rest. Wake us at the third bell.", hint: "exit", action: "leave" },
    ],
  },
};

Object.assign(window, { ScreenDialogue, ScreenDialogueDemo, ParleyMenu, DIALOGUE });
