/* Screen: Combat Encounter - engine-owned tactical projection */

function combatSurfaceFromCampaign(activeCampaign, state) {
  const campaignId = activeCampaign?.campaign_id || state?.activeCampaign || activeCampaign?.id || "";
  const params = new URLSearchParams();
  if (campaignId) params.set("campaign", campaignId);
  if (activeCampaign?.source) params.set("source", activeCampaign.source);
  if (activeCampaign?.runId) params.set("run", activeCampaign.runId);
  const query = params.toString();
  return query ? `?${query}` : "";
}

function ScreenCombat({ onNavigate, state }) {
  const campaigns = Array.isArray(state?.campaigns) ? state.campaigns : [];
  const activeCampaign =
    campaigns.find((c) => c.id === state?.activeCampaign) ||
    campaigns[0] ||
    {};
  const surfaceQuery = window.combatSurfaceFromCampaign(activeCampaign, state);
  const campaignId = activeCampaign?.campaign_id || state?.activeCampaign || activeCampaign?.id || "";
  const [surface, setSurface] = React.useState(null);
  const [surfaceStatus, setSurfaceStatus] = React.useState("loading");
  const [selectedToken, setSelectedToken] = React.useState("");
  const [localLog, setLocalLog] = React.useState([]);
  const [busyAction, setBusyAction] = React.useState("");
  const toast = window.useToast ? window.useToast() : (() => {});

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    try {
      const response = await fetch("/combat-surface" + surfaceQuery, { cache: "no-store" });
      if (!response.ok) throw new Error(`combat surface ${response.status}`);
      const payload = await response.json();
      if (isCancelled()) return;
      setSurface(payload);
      setSurfaceStatus("ready");
    } catch (error) {
      if (isCancelled()) return;
      setSurfaceStatus(error?.message || "unavailable");
    }
  }, [surfaceQuery]);

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
      if (timer === null) timer = window.setInterval(guardedLoad, 5000);
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

  const tokens = Array.isArray(surface?.tokens) ? surface.tokens : [];
  const initiative = Array.isArray(surface?.initiative) ? surface.initiative : [];
  const actions = Array.isArray(surface?.actionBar) ? surface.actionBar : [];
  const zones = Array.isArray(surface?.zones) ? surface.zones : [];
  const battleLog = Array.isArray(surface?.battleLog) ? surface.battleLog : [];
  const encounter = surface?.encounter || { active: false, name: "No active encounter" };
  const commandCenter = surface?.commandCenter || {};
  const economy = surface?.actionEconomy || {};
  const canAct = Boolean(surface?.can_act);
  const selected =
    tokens.find((t) => t.id === selectedToken) ||
    tokens.find((t) => t.id === surface?.selectedTokenId) ||
    tokens[0] ||
    null;
  const visibleLog = [...battleLog, ...localLog];

  React.useEffect(() => {
    if (!selectedToken || !tokens.some((t) => t.id === selectedToken)) {
      setSelectedToken(surface?.selectedTokenId || tokens[0]?.id || "");
    }
  }, [surface?.selectedTokenId, selectedToken, tokens]);

  const actionById = (id) => actions.find((a) => a.id === id) || {};
  const actionHint = (action) => {
    if (!action) return "unavailable";
    if (action.disabled_reason) return action.disabled_reason;
    if (action.move?.name) return action.move.name;
    if (action.move?.kind) return action.move.kind;
    return action.available ? "ready" : "unavailable";
  };

  const postMove = async (action) => {
    if (!action?.available || !action?.move || !canAct) {
      toast({
        kind: "danger",
        eyebrow: "Combat",
        title: action?.label ? `${action.label} unavailable` : "Action unavailable",
        body: action?.disabled_reason || "This surface is read-only.",
      });
      return;
    }
    setBusyAction(action.id);
    try {
      const response = await fetch("/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...action.move, campaign: surface?.campaign_id || campaignId }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.reason || `move ${response.status}`);
      }
      setLocalLog((rows) => [
        ...rows,
        {
          event: "player-intent",
          title: "Player intent sent",
          text: action.label || action.move.name || action.move.kind,
          meta: [{ label: "lane", value: "/move" }],
        },
      ]);
      await loadSurface();
    } catch (error) {
      toast({ kind: "danger", title: "Move not sent", body: error?.message || "The viewer could not reach /move." });
    } finally {
      setBusyAction("");
    }
  };

  const actionTile = (id, fallbackIcon, fallbackLabel) => {
    const action = actionById(id);
    return (
      <ActionTile
        key={id}
        icon={action.icon || fallbackIcon}
        label={action.label || fallbackLabel}
        hint={actionHint(action)}
        active={busyAction === id}
        disabled={!action.available || !action.move || !canAct || Boolean(busyAction)}
        onClick={() => postMove(action)}
      />
    );
  };

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "minmax(0, 1fr) 280px", gap: 14, padding: 14 }}>

      <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
        <Panel framed style={{ padding: 18, position: "relative", flex: "1 1 auto", minHeight: 0, overflow: "hidden" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10, gap: 12 }}>
            <div style={{ minWidth: 0 }}>
              <div className="eyebrow" style={{ color: "var(--crimson)" }}>
                Encounter {encounter.round ? `- Round ${encounter.round}` : ""}
              </div>
              <h2 className="h1" style={{ fontSize: 22, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {encounter.name || "No active encounter"}
              </h2>
              <div className="body-sm" style={{ color: "var(--ink-700)", marginTop: 4 }}>
                {encounter.summary || surfaceStatus}
              </div>
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
              <Pill dot>{surface?.grid?.mode === "grid" ? "Grid" : "Zones"}</Pill>
              <BrassButton size="sm" tone="ghost" onClick={() => loadSurface()}>Refresh</BrassButton>
              <BrassButton size="sm" onClick={() => onNavigate("table")}>Back to table</BrassButton>
            </div>
          </div>

          {encounter.active ? (
            <CombatMap tokens={tokens} zones={zones} selected={selected?.id} onSelect={setSelectedToken} />
          ) : (
            <CombatEmptyState status={surfaceStatus} onNavigate={onNavigate} />
          )}
        </Panel>

        <Panel framed style={{ padding: 14, flex: "0 0 auto" }}>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(180px, 220px) 1fr", gap: 16, alignItems: "center" }}>
            <CombatantSummary token={selected} economy={economy} commandCenter={commandCenter} />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 6 }}>
              {actionTile("move", "atlas.travel", "Move")}
              {actionTile("attack", "combat.attack", "Attack")}
              {actionTile("cast", "dice.roll", "Cast")}
              {actionTile("bonus-action", "◈", "Bonus")}
              {actionTile("item", "inventory.potion", "Item")}
              {actionTile("reaction", "✺", "Reaction")}
              {actionTile("end-turn", "⊘", "End turn")}
            </div>
          </div>
        </Panel>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
        <Panel framed style={{ padding: 18 }}>
          <SectionTitle>Initiative</SectionTitle>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {initiative.length ? initiative.map((row) => {
              const tok = tokens.find((x) => x.id === row.id) || row;
              return (
                <InitiativeRow
                  key={row.id}
                  row={row}
                  token={tok}
                  selected={selected?.id === row.id}
                  onClick={() => setSelectedToken(row.id)}
                />
              );
            }) : <div className="body-sm" style={{ color: "var(--ink-600)" }}>No initiative order.</div>}
          </div>
        </Panel>

        <Panel framed style={{ padding: 18 }}>
          <SectionTitle>Command</SectionTitle>
          <CommandCenterPanel commandCenter={commandCenter} selectedId={selected?.id} onSelect={setSelectedToken} />
        </Panel>

        <Panel framed style={{ padding: 18, flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
          <SectionTitle>Battle Log</SectionTitle>
          <div tabIndex={0} style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 6 }}>
            {visibleLog.length
              ? visibleLog.map((row, i) => <BattleLogLine key={`${row.event || "log"}-${i}`} l={row} />)
              : <div className="body-sm" style={{ color: "var(--ink-600)" }}>Combat events will appear here.</div>}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function CombatantSummary({ token, economy, commandCenter }) {
  if (!token) {
    return (
      <div style={{ padding: "8px 12px", background: "rgba(176,141,87,0.08)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)" }}>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 12, color: "var(--ink-800)" }}>No token selected</div>
        <div style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-600)", marginTop: 2 }}>Open a live combat to act.</div>
      </div>
    );
  }
  const hpText = token.hpKnown ? `HP ${token.hp}/${token.hpMax}` : token.health || "unknown";
  const acText = token.ac ? ` · AC ${token.ac}` : "";
  const cues = Array.isArray(commandCenter?.cues)
    ? commandCenter.cues.filter((cue) => cue.character_id === token.id).slice(0, 2)
    : [];
  return (
    <div style={{
      display: "flex", gap: 10, alignItems: "center",
      padding: "8px 12px",
      background: "linear-gradient(180deg, var(--p-100), var(--p-200))",
      boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400)",
    }}>
      <Img scope={token.id ? "portrait-" + token.id : ""} label={token.short || token.initial || "token"} w={40} h={48} framed />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 12, letterSpacing: "0.08em", color: "var(--ink-900)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {token.name}
        </div>
        <div style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-700)", marginTop: 2 }}>
          {hpText}{acText}
        </div>
        <div style={{ marginTop: 4, display: "flex", gap: 6 }}>
          <ApBadge used={economy.action_available === false} label="Act" />
          <ApBadge used={economy.bonus_available === false} label="Bonus" />
          <ApBadge used={economy.reaction_available === false} label="React" />
        </div>
        {cues.length > 0 && (
          <div style={{ marginTop: 4, display: "flex", gap: 4, flexWrap: "wrap" }}>
            {cues.map((cue, i) => <CueChip key={`${cue.type}-${i}`} cue={cue} />)}
          </div>
        )}
      </div>
    </div>
  );
}

function CommandCenterPanel({ commandCenter, selectedId, onSelect }) {
  const actor = commandCenter?.activeActor || {};
  const slots = commandCenter?.slots || {};
  const budget = commandCenter?.attackBudget || {};
  const targetability = Array.isArray(commandCenter?.targetability) ? commandCenter.targetability : [];
  const targets = targetability.filter((row) => row.id !== actor.id);
  const cues = Array.isArray(commandCenter?.cues) ? commandCenter.cues.slice(0, 4) : [];
  const attackLine = Number.isFinite(Number(budget.allowed))
    ? `${budget.remaining ?? 0}/${budget.allowed} attacks left`
    : "attack budget unavailable";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 6 }}>
        <div style={{ fontFamily: "var(--f-display)", fontSize: 13, color: "var(--ink-900)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {actor.name || "No active actor"}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 4 }}>
          <SlotPip label="Act" slot={slots.action} />
          <SlotPip label="Bonus" slot={slots.bonusAction} />
          <SlotPip label="React" slot={slots.reaction} />
        </div>
        <div style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-700)" }}>{attackLine}</div>
      </div>

      {cues.length > 0 && (
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {cues.map((cue, i) => <CueChip key={`${cue.character_id}-${cue.type}-${i}`} cue={cue} />)}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {targets.length ? targets.map((row) => (
          <button key={row.id} onClick={() => onSelect(row.id)} title={row.reason || "targetable"} style={{
            display: "grid",
            gridTemplateColumns: "1fr auto",
            gap: 8,
            alignItems: "center",
            padding: "6px 8px",
            textAlign: "left",
            background: selectedId === row.id ? "rgba(176,141,87,0.18)" : "transparent",
            color: row.targetable ? "var(--ink-900)" : "var(--ink-600)",
            boxShadow: "inset 0 -1px 0 rgba(140,100,60,0.16)",
            cursor: "pointer",
          }}>
            <span style={{ minWidth: 0 }}>
              <span style={{ display: "block", fontFamily: "var(--f-display)", fontSize: 10, letterSpacing: "0.06em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.name}</span>
              <span style={{ display: "block", fontFamily: "var(--f-mono)", fontSize: 9, color: "var(--ink-600)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{row.zone || row.health || "field"}</span>
            </span>
            <span style={{
              fontFamily: "var(--f-mono)",
              fontSize: 9,
              color: row.targetable ? "var(--crimson)" : "var(--ink-600)",
              textTransform: "uppercase",
            }}>{row.targetable ? "target" : row.reason}</span>
          </button>
        )) : <div className="body-sm" style={{ color: "var(--ink-600)" }}>No combatants projected.</div>}
      </div>
    </div>
  );
}

function SlotPip({ label, slot }) {
  const available = Boolean(slot?.available);
  return (
    <span title={slot?.reason || "ready"} style={{
      fontFamily: "var(--f-display)",
      fontSize: 8,
      letterSpacing: "0.1em",
      textTransform: "uppercase",
      padding: "3px 5px",
      textAlign: "center",
      color: available ? "var(--ink-900)" : "var(--ink-600)",
      background: available ? "rgba(95,75,45,0.38)" : "rgba(0,0,0,0.18)",
      boxShadow: available ? "inset 0 0 0 1px var(--b-500)" : "inset 0 0 0 1px rgba(80,50,20,0.35)",
      textDecoration: available ? "none" : "line-through",
    }}>{label}</span>
  );
}

function CueChip({ cue }) {
  const danger = cue?.severity === "danger";
  return (
    <span title={cue?.text || cue?.label || ""} style={{
      fontFamily: "var(--f-mono)",
      fontSize: 9,
      color: danger ? "var(--crimson)" : "var(--ink-700)",
      padding: "2px 5px",
      background: danger ? "rgba(166,39,39,0.1)" : "rgba(176,141,87,0.12)",
      boxShadow: danger ? "inset 0 0 0 1px rgba(166,39,39,0.28)" : "inset 0 0 0 1px rgba(140,100,60,0.18)",
      maxWidth: 170,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    }}>{cue?.label || cue?.type}</span>
  );
}

function CombatEmptyState({ status, onNavigate }) {
  return (
    <div style={{
      height: "calc(100% - 50px)",
      display: "grid",
      placeItems: "center",
      background: "linear-gradient(135deg, rgba(58,36,24,0.55), rgba(37,22,14,0.65))",
      boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 60px rgba(0,0,0,0.45)",
    }}>
      <div style={{ textAlign: "center", maxWidth: 360 }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>No active initiative</div>
        <h3 className="h1" style={{ fontSize: 22, marginTop: 6 }}>Return to the table</h3>
        <p className="body-sm" style={{ color: "var(--ink-700)", marginTop: 8 }}>{status === "ready" ? "The engine has no active combat board for this campaign." : status}</p>
        <div style={{ marginTop: 14 }}>
          <BrassButton size="sm" onClick={() => onNavigate("table")}>Open table</BrassButton>
        </div>
      </div>
    </div>
  );
}

function CombatMap({ tokens, zones, selected, onSelect }) {
  const cols = 16, rows = 10;
  const terrain = zones.length
    ? zones.map((z) => z.name).filter(Boolean).join(" · ")
    : "tactical field";
  return (
    <div style={{
      position: "relative",
      width: "100%", height: "calc(100% - 50px)",
      background:
        `radial-gradient(ellipse at 50% 50%, rgba(60,30,10,0.2), transparent 70%),
         linear-gradient(135deg, #3a2418 0%, #25160e 100%)`,
      boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 60px rgba(0,0,0,0.6)",
      overflow: "hidden",
    }}>
      <div style={{ position: "absolute", inset: 12 }}>
        <Placeholder label={terrain} h="100%" style={{ width: "100%", height: "100%", opacity: 0.5 }} />
      </div>

      <svg style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}>
        <defs>
          <pattern id="openworldsCombatGrid" x="0" y="0" width={`${100 / cols}%`} height={`${100 / rows}%`} patternUnits="userSpaceOnUse">
            <rect width="100%" height="100%" fill="none" stroke="rgba(176, 141, 87, 0.18)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#openworldsCombatGrid)" />
        {Array.from({ length: cols }).map((_, i) => (
          <text key={`c${i}`} x={`${(i + 0.5) * (100 / cols)}%`} y="12" textAnchor="middle"
            fontFamily="Cinzel" fontSize="9" fill="rgba(212, 185, 122, 0.4)" letterSpacing="1">
            {String.fromCharCode(65 + i)}
          </text>
        ))}
        {Array.from({ length: rows }).map((_, i) => (
          <text key={`r${i}`} x="6" y={`${(i + 0.5) * (100 / rows) + 3}%`}
            fontFamily="Cinzel" fontSize="9" fill="rgba(212, 185, 122, 0.4)">{i + 1}</text>
        ))}
      </svg>

      {zones.map((z, i) => (
        <div key={z.name || i} style={{
          position: "absolute",
          left: `${4 + (i % 4) * 24}%`,
          top: `${8 + Math.floor(i / 4) * 28}%`,
          padding: "4px 7px",
          fontFamily: "var(--f-display)",
          fontSize: 9,
          letterSpacing: "0.12em",
          textTransform: "uppercase",
          color: "rgba(244, 210, 123, 0.68)",
          background: "rgba(30,18,10,0.32)",
          boxShadow: "inset 0 0 0 1px rgba(176,141,87,0.18)",
        }}>
          {z.name}
        </div>
      ))}

      {tokens.map((t) => (
        <CombatToken
          key={t.id}
          t={t}
          cols={cols}
          rows={rows}
          selected={selected === t.id}
          onClick={() => onSelect(t.id)}
        />
      ))}
    </div>
  );
}

function healthRatio(t) {
  if (t.hpKnown && Number.isFinite(Number(t.hp)) && Number.isFinite(Number(t.hpMax)) && Number(t.hpMax) > 0) {
    return Math.max(0, Math.min(1, Number(t.hp) / Number(t.hpMax)));
  }
  if (t.health === "down") return 0.08;
  if (t.health === "bloodied") return 0.38;
  if (t.health === "wounded") return 0.68;
  return 1;
}

function CombatToken({ t, cols, rows, selected, onClick }) {
  const xPct = (((Number(t.x) || 1) - 0.5) / cols) * 100;
  const yPct = (((Number(t.y) || 1) - 0.5) / rows) * 100;
  const isFoe = t.team === "foe";
  const hpRatio = healthRatio(t);
  return (
    <button onClick={onClick} style={{
      position: "absolute",
      left: `${xPct}%`, top: `${yPct}%`,
      width: 48, height: 48,
      transform: "translate(-50%, -50%)",
      background: "none",
      cursor: "pointer",
      padding: 0,
      zIndex: selected ? 10 : 5,
    }}>
      <div style={{
        width: "100%", height: "100%",
        borderRadius: "50%",
        background: isFoe
          ? "radial-gradient(circle at 30% 30%, #c54040, var(--crimson) 60%, #3a0a0a)"
          : "radial-gradient(circle at 30% 30%, var(--p-100), var(--p-300) 60%, var(--b-500))",
        boxShadow: selected
          ? `inset 0 0 0 2px var(--gold-glow), 0 0 0 3px ${isFoe ? "var(--crimson-bright)" : "var(--gold-glow)"}, 0 0 24px rgba(244, 210, 123, 0.7), 0 4px 8px rgba(0,0,0,0.5)`
          : `inset 0 0 0 2px ${isFoe ? "#5a1414" : "var(--b-500)"}, 0 0 0 2px ${isFoe ? "var(--crimson)" : "var(--b-300)"}, 0 4px 8px rgba(0,0,0,0.6)`,
        display: "grid", placeItems: "center",
        fontFamily: "var(--f-display)",
        fontSize: 13,
        letterSpacing: "0.04em",
        color: isFoe ? "var(--p-100)" : "var(--ink-900)",
        overflow: "hidden",
      }}>
        <Img
          scope={t.id ? "portrait-" + t.id : ""}
          label={t.initial}
          w="100%"
          h="100%"
          fit="cover"
          style={{ width: "100%", height: "100%", borderRadius: "50%" }}
        />
      </div>
      <div style={{
        position: "absolute", left: "50%", bottom: -10, transform: "translateX(-50%)",
        width: 44, height: 4,
        background: "rgba(0,0,0,0.5)",
        boxShadow: "0 0 0 1px rgba(0,0,0,0.8)",
      }}>
        <div style={{
          position: "absolute", left: 0, top: 0, bottom: 0,
          width: `${hpRatio * 100}%`,
          background: isFoe ? "linear-gradient(180deg, #d63a3a, #8a1a1a)" : "linear-gradient(180deg, #5cd56a, #2a8c39)",
        }} />
      </div>
      <div style={{
        position: "absolute", left: "50%", top: -16, transform: "translateX(-50%)",
        fontFamily: "var(--f-display)", fontSize: 8, letterSpacing: "0.15em",
        textTransform: "uppercase", whiteSpace: "nowrap",
        color: isFoe ? "var(--crimson-bright)" : "var(--gold-glow)",
        textShadow: "0 1px 2px rgba(0,0,0,0.9)",
      }}>{t.name}</div>
    </button>
  );
}

function InitiativeRow({ row, token, selected, onClick }) {
  const isFoe = row.team === "foe";
  const hpRatio = healthRatio(token);
  return (
    <button onClick={onClick} style={{
      display: "grid", gridTemplateColumns: "28px 36px 1fr auto", gap: 8, alignItems: "center",
      padding: "6px 10px",
      background: row.active
        ? "linear-gradient(180deg, var(--p-100), var(--p-200))"
        : selected ? "rgba(176,141,87,0.18)" : "transparent",
      boxShadow: row.active
        ? "inset 0 0 0 1px var(--b-500), 0 0 16px -6px var(--gold-glow)"
        : "inset 0 -1px 0 rgba(140,100,60,0.2)",
      cursor: "pointer",
      textAlign: "left",
    }}>
      <span style={{
        fontFamily: "var(--f-display)",
        fontSize: 14,
        color: isFoe ? "var(--crimson)" : "var(--ink-900)",
        fontWeight: 600,
      }}>{row.init ?? "-"}</span>
      <Img scope={(token.id || row.id) ? "portrait-" + (token.id || row.id) : ""} label={token.short || "?"} w={36} h={36} framed />
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontFamily: "var(--f-display)",
          fontSize: 11,
          letterSpacing: "0.06em",
          color: isFoe ? "var(--crimson)" : "var(--ink-900)",
          fontStyle: isFoe ? "italic" : "normal",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>{row.name}</div>
        <div style={{ display: "flex", gap: 4, alignItems: "center", marginTop: 3 }}>
          <div style={{ flex: 1, height: 4, background: "rgba(0,0,0,0.15)", position: "relative", boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.3)" }}>
            <div style={{
              position: "absolute", left: 0, top: 0, bottom: 0, width: `${hpRatio * 100}%`,
              background: isFoe ? "linear-gradient(180deg, var(--crimson), #4a1010)" :
                (hpRatio > 0.5 ? "linear-gradient(180deg, #5a8a3a, #3a6020)" : "linear-gradient(180deg, var(--crimson), #4a1010)"),
            }} />
          </div>
        </div>
      </div>
      {row.active && <span style={{ color: "var(--crimson)", fontFamily: "var(--f-display)", fontSize: 14 }}>▶</span>}
    </button>
  );
}

function ActionTile({ icon, label, hint, onClick, active, disabled }) {
  const iconNode = window.OpenWorldsIcon?.has?.(icon)
    ? <window.OpenWorldsIcon id={icon} size={19} label={label} />
    : icon;
  return (
    <button onClick={onClick} disabled={disabled} title={hint || label} style={{
      padding: "10px 8px",
      textAlign: "center",
      background: active
        ? "linear-gradient(180deg, var(--b-200), var(--b-400))"
        : disabled ? "rgba(0,0,0,0.18)" : "rgba(176,141,87,0.08)",
      color: active ? "var(--w-300)" : disabled ? "var(--ink-500)" : "var(--ink-800)",
      boxShadow: active
        ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6), 0 0 16px -4px var(--gold-glow)"
        : "inset 0 0 0 1px rgba(140,100,60,0.3)",
      cursor: disabled ? "not-allowed" : "pointer",
      transition: "all 140ms",
      opacity: disabled ? 0.55 : 1,
      minWidth: 0,
    }}>
      <div style={{ fontSize: 18, lineHeight: 1, marginBottom: 4, color: "inherit" }}>{iconNode}</div>
      <div style={{ fontFamily: "var(--f-display)", fontSize: 9, letterSpacing: "0.12em", textTransform: "uppercase", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</div>
      {hint && <div style={{ fontFamily: "var(--f-mono)", fontSize: 8, color: active ? "var(--w-300)" : "var(--ink-600)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{hint}</div>}
    </button>
  );
}

function ApBadge({ used, label }) {
  return (
    <span style={{
      fontFamily: "var(--f-display)", fontSize: 8, letterSpacing: "0.1em", textTransform: "uppercase",
      padding: "2px 5px",
      background: used ? "rgba(0,0,0,0.2)" : "rgba(95, 75, 45, 0.4)",
      color: used ? "var(--ink-600)" : "var(--ink-800)",
      boxShadow: used ? "inset 0 0 0 1px rgba(80,50,20,0.4)" : "inset 0 0 0 1px var(--b-500)",
      textDecoration: used ? "line-through" : "none",
    }}>{label}</span>
  );
}

function BattleLogLine({ l }) {
  const meta = Array.isArray(l.meta) ? l.meta : [];
  return (
    <div style={{ padding: "5px 0", borderBottom: "1px solid rgba(140,100,60,0.16)" }}>
      <div style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
        <span style={{ fontFamily: "var(--f-display)", fontSize: 10, letterSpacing: "0.16em", textTransform: "uppercase", color: "var(--ink-900)" }}>
          {l.title || l.event || "Combat"}
        </span>
        <span className="body-sm" style={{ color: "var(--ink-700)" }}>{l.text}</span>
      </div>
      {meta.length > 0 && (
        <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 4 }}>
          {meta.map((m, i) => (
            <span key={`${m.label}-${i}`} style={{
              fontFamily: "var(--f-mono)",
              fontSize: 10,
              color: "var(--ink-600)",
              padding: "1px 5px",
              background: "rgba(176,141,87,0.12)",
              boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.18)",
            }}>{m.label}: {m.value}</span>
          ))}
        </div>
      )}
    </div>
  );
}

Object.assign(window, {
  ScreenCombat,
  CombatMap,
  CombatToken,
  CommandCenterPanel,
  ActionTile,
  ApBadge,
  BattleLogLine,
  combatSurfaceFromCampaign,
});
