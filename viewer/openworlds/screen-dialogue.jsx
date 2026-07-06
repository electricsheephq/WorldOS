/* Screen: Parley — sheet-correct social options over scene art.
   Wired to the live /parley-surface read model (the UI side of #141): the lead PC's
   alignment + per-skill {skill, modifier, suggested_dc} + a free-form path. Polls every
   5s while visible. These are SLOTS, not authored lines — the player picks an approach
   and the DM (via the engine) voices + adjudicates it. When no live parley resolves the
   screen shows an honest empty-state — it never falls back to authored demo dialogue.
   Scene chrome / dialogue-panel design unchanged. */

const DIFFICULTY_OPTIONS = ["easy", "medium", "hard"];

/* Build the /image scope for the parley actor's portrait — derive from the NAME slug, NOT the
   instance id, matching the character/combat/inventory/relations portraitScope fix on main.
   `surface.actor_id` is an opaque engine hash ("char_7d98224877ec") that only resolves via the
   server's _portrait_by_name bridge; slug(name) is the stable key the ingested canon art is
   actually filed under ("portrait-dal-lightspark"). Falls back to the actor_id scope (still
   bridged), then the scene's anchor NPC, then a graceful silhouette via <Img>'s onError. */
function parleyPortraitScope(surface, actorName) {
  const s = (actorName && window.slug) ? window.slug(actorName) : "";
  if (s) return "portrait-" + s;
  if (surface && surface.actor_id) return "portrait-" + surface.actor_id;
  if (surface && surface.event && surface.event.anchor_npc_id) return "portrait-" + surface.event.anchor_npc_id;
  return "";
}

function ScreenDialogue({ onNavigate, state, setState }) {
  const surfaceQuery = window.combatSurfaceFromCampaign
    ? window.combatSurfaceFromCampaign(
        (Array.isArray(state?.campaigns) ? state.campaigns : []).find((c) => c.id === state?.activeCampaign) ||
          (Array.isArray(state?.campaigns) ? state.campaigns : [])[0] || {},
        state,
      )
    : "";
  // W3 (#1320): the interlocutor the player clicked-to-talk on the rest board (stashed by
  // ScreenCombat's approach). Binds /parley-surface to THAT NPC so the header names it and the
  // speaker is staged at its cell (npc.stage_cell/facing). Absent -> today's freeform/anchor parley.
  const parleyNpc = state?.activeParleyNpc || "";
  const [surface, setSurface] = React.useState(null);
  const [difficulty, setDifficulty] = React.useState("medium");
  const [history, setHistory] = React.useState([]);
  const [status, setStatus] = React.useState("loading");
  const toast = window.useToast ? window.useToast() : (() => {});

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    const sep = surfaceQuery ? "&" : "?";
    const npcParam = parleyNpc ? `&npc=${encodeURIComponent(parleyNpc)}` : "";
    const url = `/parley-surface${surfaceQuery}${sep}difficulty=${encodeURIComponent(difficulty)}${npcParam}`;
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
  }, [surfaceQuery, difficulty, parleyNpc]);

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

  // No live parley (no snapshot / no actor): show an honest BG-neutral empty-state rather
  // than authored demo dialogue.
  return <ParleyEmpty status={status} />;
}

function ParleyEmpty({ status }) {
  return (
    <div className="screen" style={{ height: "100%", display: "grid", placeItems: "center", padding: 14 }}>
      <Panel framed style={{ padding: 40, textAlign: "center", maxWidth: 460 }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>Parley</div>
        <h2 className="h1" style={{ fontSize: 22, marginTop: 6 }}>No one to parley with</h2>
        <p className="hand muted" style={{ fontSize: 14, marginTop: 10 }}>
          When the party sits down to talk, this is where the lead speaker's approaches
          appear — each a sheet-correct skill check the DM voices and adjudicates.
          {status && status !== "ready" && status !== "loading"
            ? " The engine's parley read model is not reachable yet."
            : ""}
        </p>
      </Panel>
    </div>
  );
}

function ParleyMenu({ surface, slots, difficulty, setDifficulty, history, setHistory, onNavigate, toast }) {
  const actorName = surface.actor || "Hero";
  // #751: the conversation TARGET. The header used to name `surface.actor` (the lead PC) — a
  // parley that named the wrong speaker. When the surface binds an NPC, name THAT NPC and pin to
  // it for the interaction; the actor's sheet still drives the skill slots below. No npc -> the
  // header keeps today's behavior (the lead speaker), so the no-target freeform parley is unchanged.
  const npc = surface.npc && typeof surface.npc === "object" ? surface.npc : null;
  const npcName = npc && npc.name ? npc.name : "";
  const canAct = Boolean(surface.can_act);
  // #615: reuse the Relations screen's DispositionDot meter (exported on window) for a live
  // attitude read while talking — reads the existing disposition band (fine at 0 -> "neutral").
  const DispositionDot = window.DispositionDot;
  const sceneScope = surface.imageScope ||
    (surface.location_id ? `location:${surface.location_id}` : "");
  // Free-form path: reveal a textarea so the player types their OWN line, then POST that
  // actual text (not a hardcoded placeholder). Send is disabled until something is typed.
  const [freeFormMode, setFreeFormMode] = React.useState(false);
  const [userText, setUserText] = React.useState("");
  // #robustness: synchronous in-flight lock — pick()/sendFreeForm() fire /move fire-and-forget with
  // no guard, so a rapid double-click queues duplicate social-check/say intents. Cleared in .finally.
  const submittingRef = React.useRef(false);

  const pick = (slot) => {
    const move = { kind: "check", name: `${slot.label} (DC ${slot.suggested_dc})`, skill: slot.skill, dc: slot.suggested_dc, text: `attempts ${slot.label} (DC ${slot.suggested_dc})` };
    setHistory((h) => [...h, { skill: slot.label, dc: slot.suggested_dc, mod: slot.modifier }]);
    if (!canAct) {
      toast({ kind: "danger", eyebrow: "Parley", title: "Preview — no live DM", body: "Open a chronicle from Chronicles to converse; then the DM voices and adjudicates the approach you pick." });
      return;
    }
    if (submittingRef.current) return; // already submitting a check — drop the rapid double-click
    submittingRef.current = true;
    fetch("/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...move, campaign: surface.campaign_id || "" }),
    }).then((r) => r.json().catch(() => ({}))).then((payload) => {
      if (payload && payload.ok === false) throw new Error(payload.reason || "move rejected");
      toast({ kind: "item", eyebrow: "Parley", title: `${actorName} — ${slot.label}`, body: `Requested a ${slot.label} check at DC ${slot.suggested_dc}.` });
    }).catch((e) => toast({ kind: "danger", title: "Move not sent", body: e?.message || "The viewer could not reach /move." })).finally(() => { submittingRef.current = false; });
  };

  const openFreeForm = () => {
    if (!canAct) {
      toast({ kind: "danger", eyebrow: "Parley", title: "Preview — no live DM", body: "Open a chronicle from Chronicles to converse; the DM voices the free-form path at the table." });
      return;
    }
    setFreeFormMode(true);
  };

  const sendFreeForm = () => {
    const text = userText.trim();
    if (!text) return;
    if (submittingRef.current) return; // already sending — drop the rapid double-click / double-Enter
    submittingRef.current = true;
    fetch("/move", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "say", text, campaign: surface.campaign_id || "" }),
    }).then((r) => r.json().catch(() => ({}))).then((payload) => {
      if (payload && payload.ok === false) throw new Error(payload.reason || "move rejected");
      setHistory((h) => [...h, { skill: "Free-form", dc: null, mod: null, text }]);
      setUserText("");
      setFreeFormMode(false);
      toast({ kind: "item", eyebrow: "Parley", title: `${actorName} — Free-form`, body: "Spoke their own words — the DM adjudicates." });
    }).catch((e) => toast({ kind: "danger", title: "Move not sent", body: e?.message || "The viewer could not reach /move." })).finally(() => { submittingRef.current = false; });
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
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 6 }}>
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
              Parley · Speaking with {npcName || actorName} · {surface.dayLabel || "the table"}
            </div>
            {/* #615: live disposition meter for the bound NPC — the same DispositionDot the
                Relations screen uses, so the player sees where they stand WHILE talking. Reads
                the existing disposition band (present even at attitude_value 0 -> "neutral"). */}
            {npc && npc.disposition && DispositionDot && (
              <div style={{
                display: "inline-flex", alignItems: "center", gap: 8,
                padding: "4px 12px",
                background: "linear-gradient(180deg, var(--w-100), var(--w-300))",
                boxShadow: "inset 0 0 0 1px var(--w-500)",
              }}>
                <span className="eyebrow" style={{ color: "var(--b-300)", letterSpacing: "0.16em", fontSize: 9 }}>
                  Disposition
                </span>
                <DispositionDot d={npc.disposition} />
                {typeof npc.attitude_value === "number" && (
                  <span style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-600)" }}>
                    {npc.attitude_value > 0 ? "+" : ""}{npc.attitude_value}
                  </span>
                )}
              </div>
            )}
            {/* W3 (#1320): dialogue-AT-the-actor readout. When the parley bound an on-stage NPC
                (approach-to-talk staged it at its rest cell), echo the engine's stage_cell (+ the
                party-relative facing) so the conversation reads as spatial — the speaker stands
                WHERE the player walked up to, not a disembodied portrait. Present only when the
                engine attached stage metadata (a click-to-talk approach); absent for a freeform
                parley, so the header is byte-identical to today otherwise. Pure projection. */}
            {Array.isArray(npc?.stage_cell) && npc.stage_cell.length === 2 && (
              <div style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                padding: "4px 12px",
                background: "linear-gradient(180deg, var(--w-100), var(--w-300))",
                boxShadow: "inset 0 0 0 1px var(--w-500)",
              }}>
                <span className="eyebrow" style={{ color: "var(--b-300)", letterSpacing: "0.16em", fontSize: 9 }}>
                  On stage
                </span>
                <span style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-600)" }}>
                  ({npc.stage_cell[0]}, {npc.stage_cell[1]}){npc.facing ? ` · ${npc.facing}` : ""}
                </span>
              </div>
            )}
          </div>
          {/* Approach difficulty for THIS attempt — sets the suggested DC of the check
              the engine projects (easy 10 / medium 14 / hard 18, before any house shift). */}
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
            {/* Visible inline caption above the buttons (not just the hover tooltip). */}
            <div className="eyebrow" style={{ color: "var(--p-100)", letterSpacing: "0.16em", fontSize: 9, textAlign: "right" }}>
              Approach difficulty
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <window.Tooltip side="bottom" content="Approach difficulty for this attempt — sets the suggested DC of the check below (easy ≈ 10 · medium ≈ 14 · hard ≈ 18).">
                <span className="eyebrow" style={{ color: "var(--p-100)", letterSpacing: "0.16em", fontSize: 9, cursor: "help" }}>DC</span>
              </window.Tooltip>
              {DIFFICULTY_OPTIONS.map((d) => (
                <button key={d} onClick={() => setDifficulty(d)} className="btn sm" title={`Set this attempt's approach difficulty to ${d}`} style={{
                  textTransform: "capitalize",
                  background: difficulty === d ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(20,10,4,0.5)",
                  color: difficulty === d ? "var(--w-300)" : "var(--p-100)",
                  boxShadow: difficulty === d ? "inset 0 0 0 1px var(--b-600)" : "inset 0 0 0 1px rgba(176,141,87,0.4)",
                }}>{d}</button>
              ))}
            </div>
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
              <Img scope={parleyPortraitScope(surface, actorName)} label={actorName} w={120} h={150} framed />
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
                {slots.map((slot, i) => {
                  // Dialogue-depth (BG3 "how hard is this roll"): a per-slot success-odds chip.
                  // p = passing d20 faces / 20, clamped to [5%,95%] (nat-1 always fails, nat-20
                  // always succeeds). Pure client-derived from the modifier + suggested_dc the
                  // surface already carries — no engine/read-model change.
                  const oddsFaces = Math.max(1, Math.min(19, 21 - (slot.suggested_dc - slot.modifier)));
                  const oddsPct = Math.round(oddsFaces / 20 * 100);
                  const oddsTone = oddsPct >= 65 ? "var(--emerald)" : oddsPct >= 35 ? "#c9a227" : "var(--crimson)";
                  return (
                  <button key={slot.skill} onClick={() => pick(slot)} style={{
                    display: "grid",
                    gridTemplateColumns: "24px 1fr auto auto auto",
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
                    <span title={`Roll d20 ${slot.modifier >= 0 ? "+" : ""}${slot.modifier} vs DC ${slot.suggested_dc} → about ${oddsPct}% to succeed`}
                      style={{ fontFamily: "var(--f-mono)", fontSize: 11, fontWeight: 700, color: oddsTone, minWidth: 36, textAlign: "right" }}>
                      {oddsPct}%
                    </span>
                  </button>
                  );
                })}

                {/* Free-form path — always present (free_form is always true). When active,
                    reveal a textarea so the player types their OWN line and POSTs that text. */}
                {!freeFormMode ? (
                  <button onClick={openFreeForm} style={{
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
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: "8px 4px" }}>
                    <div className="eyebrow" style={{ color: "var(--crimson)", letterSpacing: "0.16em", fontSize: 9 }}>
                      ✦ Speak freely — {actorName}'s own words
                    </div>
                    <textarea
                      aria-label="Speak freely"
                      autoFocus
                      value={userText}
                      onChange={(e) => setUserText(e.target.value)}
                      placeholder={`What does ${actorName.split(" ")[0]} say? The DM voices and adjudicates your own words.`}
                      style={{
                        width: "100%", minHeight: 72, padding: 12,
                        background: "rgba(255,250,230,0.5)",
                        border: 0,
                        boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 2px 4px rgba(80,50,20,0.15)",
                        fontFamily: "var(--f-hand)", fontSize: 15, fontStyle: "italic",
                        color: "var(--ink-700)", resize: "vertical",
                      }}
                    />
                    <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                      <button onClick={() => { setFreeFormMode(false); setUserText(""); }} className="btn sm" style={{
                        background: "rgba(20,10,4,0.5)", color: "var(--p-100)",
                        boxShadow: "inset 0 0 0 1px rgba(176,141,87,0.4)",
                      }}>Cancel</button>
                      <button onClick={sendFreeForm} disabled={!userText.trim()} className="btn sm" title={userText.trim() ? "Send your words to the DM" : "Type something first"} style={{
                        background: userText.trim() ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(20,10,4,0.4)",
                        color: userText.trim() ? "var(--w-300)" : "var(--p-200)",
                        boxShadow: "inset 0 0 0 1px var(--b-600)",
                        cursor: userText.trim() ? "pointer" : "not-allowed",
                        opacity: userText.trim() ? 1 : 0.6,
                      }}>Send</button>
                    </div>
                  </div>
                )}
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
                  {(typeof h.mod === "number" && typeof h.dc === "number")
                    ? `${h.mod >= 0 ? "+" : ""}${h.mod} vs DC ${h.dc}`
                    : (h.text ? `“${h.text}”` : "own words")}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { ScreenDialogue, ParleyEmpty, ParleyMenu });
