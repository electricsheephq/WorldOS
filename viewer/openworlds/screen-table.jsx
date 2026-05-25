/* Screen: Campaign Table — live session: scene art + party + GM narration + actions */

function ScreenTable({ onNavigate, state, setState }) {
  const campaigns = Array.isArray(state?.campaigns) ? state.campaigns : [];
  const activeCampaign =
    campaigns.find((c) => c.id === state?.activeCampaign) ||
    campaigns[0] ||
    {};
  const campaignId = activeCampaign.campaign_id || state?.activeCampaign || activeCampaign.id || "";
  const [surface, setSurface] = React.useState(null);
  const [surfaceStatus, setSurfaceStatus] = React.useState("loading");
  const demoLog = Array.isArray(state?.tableLog) ? state.tableLog : [];
  const [log, setLog] = React.useState([]);
  const [input, setInput] = React.useState("");
  const logRef = React.useRef(null);
  const inputRef = React.useRef(null);
  const toast = window.useToast ? window.useToast() : (() => {});
  const fallbackParty = Array.isArray(state?.party) ? state.party : [];
  const party = Array.isArray(surface?.party) && surface.party.length ? surface.party : fallbackParty;
  const quests = Array.isArray(surface?.activeQuests) ? surface.activeQuests : (Array.isArray(state?.quests) ? state.quests : []);
  const stash = Array.isArray(surface?.quickInventory) ? surface.quickInventory : (Array.isArray(state?.stash) ? state.stash : []);
  const conditions = Array.isArray(surface?.conditions) ? surface.conditions : [];
  const recentEvents = Array.isArray(surface?.recentEvents) ? surface.recentEvents : [];
  const actions = Array.isArray(surface?.availableActions) ? surface.availableActions : [];
  const roundOrder = Array.isArray(surface?.roundOrder) ? surface.roundOrder : [];
  const scene = surface?.scene || {};
  const encounter = surface?.encounter || {};
  const [activeHero, setActiveHero] = React.useState(() => party[0]?.id || "");
  const hero = party.find((p) => p.id === activeHero) || party[0] || { id: "", name: "Hero", short: "Hero", level: 1, class: "Adventurer", hp: 1, hpMax: 1 };
  const visibleQuests = quests.filter((q) => !q.status || q.status === "active" || q.status === "open");
  const canAct = Boolean(surface?.can_act);
  const readOnlyReason = actions.find((a) => a.disabled_reason)?.disabled_reason || "read-only surface";
  const visibleLog = surface ? [...recentEvents, ...log] : [...demoLog, ...log];
  const actionById = (id) => actions.find((a) => a.id === id);

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    const params = new URLSearchParams();
    if (campaignId) params.set("campaign", campaignId);
    if (activeCampaign.source) params.set("source", activeCampaign.source);
    if (activeCampaign.runId) params.set("run", activeCampaign.runId);
    const query = params.toString() ? `?${params.toString()}` : "";
    try {
      const response = await fetch(`/session-surface${query}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`session surface ${response.status}`);
      const payload = await response.json();
      if (isCancelled()) return;
      setSurface(payload);
      setSurfaceStatus("ready");
    } catch (error) {
      if (isCancelled()) return;
      setSurfaceStatus(error?.message || "unavailable");
    }
  }, [campaignId, activeCampaign.source, activeCampaign.runId]);

  React.useEffect(() => {
    let cancelled = false;
    let timer = null;
    const guardedLoad = async () => {
      if (cancelled) return;
      await loadSurface(() => cancelled);
    };
    const stopPolling = () => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };
    const startPolling = () => {
      if (timer === null) {
        timer = window.setInterval(guardedLoad, 5000);
      }
    };
    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        guardedLoad();
        startPolling();
      } else {
        stopPolling();
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    handleVisibility();
    return () => {
      cancelled = true;
      stopPolling();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [loadSurface]);

  React.useEffect(() => {
    if (!party.some((p) => p.id === activeHero)) {
      setActiveHero(party[0]?.id || "");
    }
  }, [party, activeHero]);

  React.useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [visibleLog]);

  const postMove = async (move, label) => {
    if (!move || !canAct) {
      toast({ kind: "danger", title: "Action unavailable", body: readOnlyReason });
      return;
    }
    const text = label || move.text || move.name || "declares an action";
    try {
      const response = await fetch("/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...move, campaign: surface?.campaign_id || campaignId }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.reason || `move ${response.status}`);
      }
      setLog((l) => [...l, { kind: "action", who: hero.name, text }]);
      loadSurface();
    } catch (error) {
      toast({ kind: "danger", title: "Move not sent", body: error?.message || "The viewer could not reach /move." });
    }
  };

  const sendAction = async () => {
    const text = input.trim();
    if (!text) return;
    const action = actionById("do");
    if (!action?.available) {
      toast({ kind: "danger", title: "Declare is unavailable", body: action?.disabled_reason || readOnlyReason });
      return;
    }
    await postMove({ kind: "do", text }, text);
    setInput("");
  };

  const requestRoll = (sides = 20) => {
    const action = actionById("check");
    if (!action?.available) {
      toast({ kind: "danger", title: `d${sides} unavailable`, body: action?.disabled_reason || readOnlyReason });
      return;
    }
    postMove({ kind: "check", name: `d${sides}`, text: `roll d${sides}` }, `requests a d${sides} roll`);
  };

  const invokeAction = (action) => {
    if (!action?.available) {
      toast({ kind: "danger", title: action?.label || "Action unavailable", body: action?.disabled_reason || readOnlyReason });
      return;
    }
    if (action.ui) {
      inputRef.current?.focus();
      return;
    }
    if (action.move) {
      postMove(action.move, action.label);
    }
  };

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "260px 1fr 280px", gap: 14, padding: 14 }}>

      {/* LEFT — Party roster */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
        <Panel framed style={{ padding: 18, flex: "0 0 auto" }}>
          <div className="eyebrow" style={{ color: "var(--crimson)" }}>{encounter.active ? encounter.summary : "Session"}</div>
          <SectionTitle>The Party</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {party.length ? party.map((p) => (
              <PartyRow
                key={p.id}
                p={p}
                active={activeHero === p.id}
                onClick={() => setActiveHero(p.id)}
              />
            )) : <div className="body-sm muted">No party members in the current read model.</div>}
          </div>
        </Panel>

        <Panel framed style={{ padding: 18, flex: "1 1 auto", minHeight: 0, overflow: "auto" }}>
          <SectionTitle>Conditions</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {conditions.length ? conditions.map((c) => (
              <ConditionRow key={c.id || `${c.name}:${c.who}`} icon={c.icon || "◆"} name={c.name} who={c.who} detail={c.detail} tone={c.tone} />
            )) : <div className="body-sm muted">No active party conditions.</div>}
          </div>
        </Panel>
      </div>

      {/* CENTER — Scene + log */}
      <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
        {/* Scene plate */}
        <div style={{ position: "relative", flex: "0 0 auto" }}>
          <Placeholder
            label={`scene · ${scene.caption || surface?.location?.name || activeCampaign.title || "Open Worlds"}`}
            h={260}
            framed
            style={{ width: "100%" }}
          />
          {/* Glow + caption */}
          <div className="candleglow" style={{ width: 200, height: 200, left: "30%", top: "30%" }} />
          <div style={{
            position: "absolute", bottom: 14, left: 14, right: 14,
            display: "flex", justifyContent: "space-between", alignItems: "flex-end",
            pointerEvents: "none",
          }}>
            <div>
              <Pill tone="royal" dot>{surface?.dayLabel || activeCampaign.day || "Unknown time"}</Pill>
              <div className="hand" style={{ marginTop: 6, color: "var(--p-100)", fontSize: 16, textShadow: "0 1px 2px rgba(0,0,0,0.8)" }}>
                {scene.summary || activeCampaign.recap || "The table is waiting for a campaign snapshot."}
              </div>
            </div>
            <div style={{ display: "flex", gap: 6, pointerEvents: "auto" }}>
              <BrassButton tone="dark" size="sm" onClick={() => onNavigate("map")}>Travel</BrassButton>
              <BrassButton tone="dark" size="sm" onClick={() => onNavigate("dialogue")}>Parley</BrassButton>
              <BrassButton tone="dark" size="sm" onClick={() => onNavigate("map", { openCamp: true })}>Camp</BrassButton>
            </div>
          </div>
        </div>

        {/* Log */}
        <Panel framed style={{ flex: "1 1 auto", display: "flex", flexDirection: "column", minHeight: 0, padding: 22 }}>
          <SectionTitle ordinal="·" right={<Pill>{canAct ? "AI GM · Listening" : surfaceStatus === "ready" ? "Read Only" : "Loading"}</Pill>}>The Tabletop Chronicle</SectionTitle>
          <div ref={logRef} style={{ flex: "1 1 auto", overflow: "auto", paddingRight: 12 }}>
            {visibleLog.length ? visibleLog.map((entry, i) => (
              <LogEntry key={i} entry={entry} />
            )) : <div className="body-sm muted">No recent table events have been written yet.</div>}
          </div>

          {/* Action bar */}
          <div style={{ marginTop: 14, padding: 12, background: "rgba(80,50,20,0.06)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.35)" }}>
            <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 8 }}>
              <span className="eyebrow">Active</span>
              <strong style={{ fontFamily: "var(--f-display)", color: "var(--ink-900)", letterSpacing: "0.1em" }}>
                {hero.name}
              </strong>
              <div style={{ flex: 1 }} />
              <button onClick={() => requestRoll(20)} className="btn ghost sm" disabled={!actionById("check")?.available}>d20</button>
              <button onClick={() => requestRoll(12)} className="btn ghost sm" disabled={!actionById("check")?.available}>d12</button>
              <button onClick={() => requestRoll(8)} className="btn ghost sm" disabled={!actionById("check")?.available}>d8</button>
              <button onClick={() => requestRoll(6)} className="btn ghost sm" disabled={!actionById("check")?.available}>d6</button>
            </div>
            <div style={{ display: "flex", gap: 10 }}>
              <input
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && sendAction()}
                placeholder={canAct ? "Describe what your hero does..." : `Read-only: ${readOnlyReason}`}
                style={{ ...inkInput, fontFamily: "var(--f-body)", fontSize: 16 }}
              />
              <BrassButton onClick={sendAction} disabled={!actionById("do")?.available}>Declare</BrassButton>
            </div>
          </div>
        </Panel>
      </div>

      {/* RIGHT — Quests + Quick stash + GM tools */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10, minHeight: 0 }}>
        <Panel framed style={{ padding: 18 }}>
          <SectionTitle>Active Quests</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {visibleQuests.map((q) => (
              <button key={q.id} onClick={() => onNavigate("journal")} style={{
                textAlign: "left",
                padding: "10px 12px",
                background: "rgba(176,141,87,0.08)",
                boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
                cursor: "pointer",
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.1em", color: "var(--ink-900)" }}>
                    {q.title}
                  </span>
                  <Pill tone={q.tone}>{q.label}</Pill>
                </div>
                <div className="hand" style={{ fontSize: 13, color: "var(--ink-600)", marginTop: 2 }}>{q.objective}</div>
              </button>
            ))}
            {!visibleQuests.length && <div className="body-sm muted">No active quests in the current read model.</div>}
          </div>
        </Panel>

        <Panel framed style={{ padding: 18 }}>
          <SectionTitle right={<button className="btn ghost sm" onClick={() => onNavigate("inventory")}>Open</button>}>Quick Stash</SectionTitle>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 6 }}>
            {stash.slice(0, 8).map((it) => (
              <IconPlate key={it.id} size={48} label={it.glyph} framed />
            ))}
            {!stash.length && <div className="body-sm muted" style={{ gridColumn: "1 / -1" }}>No quick inventory items.</div>}
          </div>
          <div style={{ marginTop: 12, display: "flex", justifyContent: "space-between" }}>
            <Stat label="Items" value={stash.length} />
            <Stat label="Live" value={canAct ? "yes" : "no"} />
          </div>
        </Panel>

        <Panel framed style={{ padding: 18, flex: 1, minHeight: 0, overflow: "auto" }}>
          <SectionTitle>Encounter</SectionTitle>
          <div className="body-sm muted" style={{ marginBottom: 10 }}>
            {encounter.summary || scene.summary || "Choose what to risk."}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {actions.slice(0, 6).map((a) => (
              <EncounterButton
                key={`${a.group}:${a.id}`}
                icon={a.available ? "◈" : "◆"}
                label={a.label}
                detail={a.available ? a.groupLabel : a.disabled_reason}
                tone={a.available ? (a.group === "combat" ? "royal" : "") : "crimson"}
                disabled={!a.available}
                onClick={() => invokeAction(a)}
              />
            ))}
            {!actions.length && <div className="body-sm muted">No actions are available until a campaign snapshot loads.</div>}
          </div>

          <div className="divider" style={{ margin: "14px 0" }}>
            <div className="diamond"></div>
          </div>

          <SectionTitle>Round Order</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {roundOrder.length ? roundOrder.map((t, i) => (
              <div key={t.id || t.name || `round-${i}`} style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "6px 10px",
                background: t.active ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
                boxShadow: t.active ? "inset 0 0 0 1px var(--b-500)" : "inset 0 -1px 0 rgba(140,100,60,0.2)",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  {t.active && <span style={{ color: "var(--crimson)", fontFamily: "var(--f-display)", fontSize: 12 }}>▶</span>}
                  <span className="body-sm" style={{ color: t.foe ? "var(--crimson)" : "var(--ink-800)", fontStyle: t.foe ? "italic" : "normal" }}>
                    {t.name}
                  </span>
                </div>
                <span style={{ fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--ink-600)" }}>{t.init}</span>
              </div>
            )) : <div className="body-sm muted">No active combat round.</div>}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function PartyRow({ p, active, onClick }) {
  const hp = Number.isFinite(Number(p.hp)) ? Number(p.hp) : 1;
  const hpMax = Number.isFinite(Number(p.hpMax)) && Number(p.hpMax) > 0 ? Number(p.hpMax) : 1;
  const hpRatio = Math.max(0, Math.min(1, hp / hpMax));
  return (
    <button onClick={onClick} style={{
      display: "grid", gridTemplateColumns: "44px 1fr", gap: 10, alignItems: "center",
      padding: "8px",
      background: active ? "linear-gradient(180deg, var(--p-100), var(--p-200))" : "transparent",
      boxShadow: active
        ? "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)"
        : "inset 0 0 0 1px rgba(140,100,60,0.25)",
      textAlign: "left",
      cursor: "pointer",
      transition: "all 140ms",
    }}>
      <Placeholder label={p.short} w={44} h={56} framed />
      <div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.08em", color: "var(--ink-900)" }}>
          {p.name}
        </div>
        <div className="hand" style={{ fontSize: 12, color: "var(--ink-600)" }}>
          Lvl {p.level} {p.class}
        </div>
        <div style={{ display: "flex", gap: 4, marginTop: 4, alignItems: "center" }}>
          <div style={{ flex: 1, height: 6, background: "rgba(0,0,0,0.15)", borderRadius: 1, position: "relative", boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.4)" }}>
            <div style={{
              position: "absolute", inset: 0, right: `${(1 - hpRatio) * 100}%`,
              background: hpRatio > 0.5 ? "linear-gradient(180deg, #5a8a3a, #3a6020)" : "linear-gradient(180deg, var(--crimson), #4a1010)",
            }} />
          </div>
          <span style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-700)" }}>{hp}/{hpMax}</span>
        </div>
      </div>
    </button>
  );
}

function ConditionRow({ icon, name, who, detail, tone }) {
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 8, alignItems: "center",
      padding: "6px 10px",
      background: "rgba(176,141,87,0.06)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.2)",
    }}>
      <span style={{ color: `var(--${tone === "crimson" ? "crimson" : tone === "royal" ? "royal" : "b-500"})`, fontSize: 16 }}>{icon}</span>
      <div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-900)" }}>
          {name} <span className="muted" style={{ textTransform: "none", letterSpacing: 0 }}>· {who}</span>
        </div>
        <div className="hand" style={{ fontSize: 12, color: "var(--ink-600)" }}>{detail}</div>
      </div>
    </div>
  );
}

function LogEntry({ entry }) {
  const kind = entry.kind || "narration";
  if (entry.kind === "narration") {
    return (
      <div style={{ margin: "14px 0", display: "flex", gap: 12 }}>
        <div style={{
          width: 4, alignSelf: "stretch",
          background: "linear-gradient(180deg, var(--b-400), transparent)",
        }} />
        <div className="body" style={{ flex: 1 }}>
          <span className="eyebrow" style={{ color: "var(--crimson)", marginRight: 8 }}>Chronicle</span>
          {entry.text}
        </div>
      </div>
    );
  }
  if (entry.kind === "action") {
    return (
      <div style={{ margin: "10px 0" }}>
        <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-900)" }}>
          {entry.who}
        </span>
        <span className="hand" style={{ marginLeft: 8, color: "var(--ink-700)" }}>—</span>
        <span className="body" style={{ marginLeft: 8, color: "var(--ink-800)" }}>{entry.text}</span>
      </div>
    );
  }
  if (entry.kind === "roll") {
    return (
      <div style={{ margin: "8px 0", display: "flex", gap: 10, alignItems: "center" }}>
        <Pill tone="emerald">d{entry.sides ?? 20}</Pill>
        <span style={{ fontFamily: "var(--f-mono)", fontSize: 13, color: "var(--ink-700)" }}>
          {entry.who} {entry.text}
        </span>
      </div>
    );
  }
  if (entry.kind === "dialog") {
    return (
      <div style={{ margin: "10px 0" }}>
        <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--royal)" }}>
          {entry.who}
        </span>
        <span className="body" style={{ marginLeft: 8, fontStyle: "italic" }}>"{entry.text}"</span>
      </div>
    );
  }
  return (
    <div style={{ margin: "8px 0" }}>
      <span style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.18em", textTransform: "uppercase", color: "var(--ink-700)" }}>
        {entry.label || kind}
      </span>
      <span className="body" style={{ marginLeft: 8, color: "var(--ink-800)" }}>{entry.text || entry.detail}</span>
    </div>
  );
}

Object.assign(window, { ScreenTable, PartyRow, ConditionRow, LogEntry });

function EncounterButton({ icon, label, detail, tone, onClick, disabled }) {
  return (
    <button onClick={onClick} style={{
      display: "grid", gridTemplateColumns: "24px 1fr", gap: 8, alignItems: "center",
      textAlign: "left",
      padding: "8px 10px",
      background: "rgba(176,141,87,0.08)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
      cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.62 : 1,
      transition: "all 140ms",
    }}
    disabled={disabled}
    onMouseEnter={(e) => {
      if (disabled) return;
      e.currentTarget.style.background = "linear-gradient(180deg, var(--p-100), var(--p-200))";
      e.currentTarget.style.boxShadow = "inset 0 0 0 1px var(--b-500), 0 0 16px -6px var(--gold-glow)";
    }}
    onMouseLeave={(e) => {
      if (disabled) return;
      e.currentTarget.style.background = "rgba(176,141,87,0.08)";
      e.currentTarget.style.boxShadow = "inset 0 0 0 1px rgba(140,100,60,0.3)";
    }}>
      <span style={{ color: tone === "crimson" ? "var(--crimson)" : tone === "royal" ? "var(--royal)" : "var(--b-500)", fontSize: 16 }}>{icon}</span>
      <div>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--ink-900)" }}>
          {label}
        </div>
        <div className="hand muted" style={{ fontSize: 11 }}>{detail}</div>
      </div>
    </button>
  );
}

window.EncounterButton = EncounterButton;
