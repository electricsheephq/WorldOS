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

/* Token art scope (audit B-02) — derive from the NAME slug, NOT the instance id, mirroring the
   character/inventory portraitScope fix already on main. A combatant's `id` is an opaque engine
   hash ("char_badefdd1fb16") that only resolves via the server's _portrait_by_name bridge; the
   name slug is the stable key the ingested art is actually filed under. FOES draw on the
   `creature-<slug>` plate (e.g. "Gnoll Warrior 1" → creature-gnoll-warrior-1, which the server's
   fuzzy _scope_key folds onto creature_gnoll-warrior) so they show a real beast portrait instead
   of a bare red blob; ALLIES draw on `portrait-<slug>` for their canon face. Empty name → fall
   back to the id scope (still bridged) → graceful silhouette/placeholder via <Img>. */
function tokenScope(t) {
  if (!t) return "";
  const name = t.name || "";
  const s = (name && window.slug) ? window.slug(name) : "";
  const prefix = t.team === "foe" ? "creature-" : "portrait-";
  if (s) return prefix + s;
  return t.id ? "portrait-" + t.id : "";
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
  // #robustness: synchronous in-flight lock. setBusyAction is async (state read at the disabled
  // check is stale), so two rapid clicks before re-render both pass — the adversarial's "Attack
  // dies on double-click" / double-submit vector. busyRef gates synchronously.
  const busyRef = React.useRef(false);
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
  // FIX A (combat scene backdrop): the servable scene scope the server projects on
  // the location block (`location:<id>`). Mirror the dialogue screen's sceneScope
  // fallback so an older surface without imageScope still resolves via location.id;
  // empty string -> CombatMap renders no backdrop (graceful, transparent).
  // M-D: prefer the box-rendered PoE2 3D-on-2D combat FRAME (combatFrameScope, turn-suffixed for cache-bust)
  // as the backdrop when present; fall back to the static location plate (imageScope) so a fight with no box
  // frame yet still shows a backdrop (the <Img> 404→placeholder covers the pending-frame window). The grid/
  // zone input overlay (zIndex 1) is untouched — only the backdrop image swaps.
  const sceneScope = surface?.combatFrameScope ||
    surface?.location?.imageScope ||
    (surface?.location?.id ? `location:${surface.location.id}` : "");
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
    if (busyRef.current) return; // already submitting — drop the rapid double-click / double-Enter
    busyRef.current = true;
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
      busyRef.current = false;
      setBusyAction("");
    }
  };

  // Grid-combat player-turn intents (move_to_cell / on-turn attack). POST /move with the turnToken
  // echoed for idempotency; the grid lane returns the REFRESHED surface in {ok,arbiter,combat}, so we
  // apply it directly (no extra GET). The engine stays the sole writer — this only posts an intent.
  const postCombatIntent = async (move, label) => {
    if (!canAct) {
      toast({ kind: "danger", eyebrow: "Combat", title: `${label} unavailable`, body: "Not your turn, or read-only surface." });
      return;
    }
    if (busyRef.current) return; // synchronous double-click guard
    busyRef.current = true;
    setBusyAction(label);
    try {
      const body = { ...move, turn_token: surface?.turnToken || "", campaign: surface?.campaign_id || campaignId };
      const response = await fetch("/move", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.reason || `move ${response.status}`);
      }
      setLocalLog((rows) => [
        ...rows,
        {
          event: "player-intent",
          title: `Player ${label}`,
          text: move.kind === "move_to_cell" ? `→ cell (${move.x}, ${move.y})` : "attack",
          meta: [{ label: "lane", value: "/move" }],
        },
      ]);
      if (payload.combat) { setSurface(payload.combat); setSurfaceStatus("ready"); }
      else await loadSurface();
    } catch (error) {
      toast({ kind: "danger", title: `${label} not sent`, body: error?.message || "The viewer could not reach /move." });
    } finally {
      busyRef.current = false;
      setBusyAction("");
    }
  };
  const onCellMove = (x, y) => postCombatIntent({ kind: "move_to_cell", x, y }, "move");
  const onAttackToken = (targetId) => postCombatIntent({ kind: "attack", target_id: targetId }, "attack");

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
              {/* Clamp the scene blurb to two lines — the engine often hands back the whole
                  world-premise paragraph here, which otherwise crushes the tactical board. Full
                  text stays available on hover. */}
              <div className="body-sm" title={encounter.summary || ""} style={{
                color: "var(--ink-700)", marginTop: 4,
                display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                overflow: "hidden", textOverflow: "ellipsis",
              }}>
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
            <CombatMap tokens={tokens} zones={zones} grid={surface?.grid} selected={selected?.id} onSelect={setSelectedToken} onCellMove={onCellMove} onAttack={onAttackToken} canAct={canAct} sceneScope={sceneScope} />
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
      <Img scope={tokenScope(token)} label={token.short || token.initial || "token"} w={40} h={48} framed />
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

/* Grid combat board (#10/#461): when the engine sends authoritative cell coordinates
   (grid.mode==="grid", tokens positionAuthority:"engine"), render a real cols×rows tactical board.
   Tokens sit at their (x,y) cell; an EMPTY-cell click posts move_to_cell, a FOE click posts attack,
   an ALLY click selects. The engine validates reach/budget/OA and re-emits the surface (it is the
   sole writer) — this board only POSTS intents and renders what comes back. */
function CombatGridBoard({ tokens, grid, selected, onSelect, onCellMove, onAttack, canAct, sceneScope }) {
  const cols = Math.max(1, Number(grid && grid.cols) || 16);
  const rows = Math.max(1, Number(grid && grid.rows) || 10);
  const at = {};
  (tokens || []).forEach((t) => {
    const x = Number(t.x), y = Number(t.y);
    if (Number.isInteger(x) && Number.isInteger(y)) at[`${x},${y}`] = t;
  });
  // #10 terrain: honor the engine's per-cell walkability override (`{c,r,walkable:bool}`) — record BOTH
  // true and false, so a cellDefault.walkable=false scene with explicit walkable floors works too. Any
  // in-bounds cell NOT listed in grid.cells falls back to cellDefault (walkable unless it says otherwise).
  const walkability = {};
  ((grid && Array.isArray(grid.cells)) ? grid.cells : []).forEach((c) => {
    if (c && Number.isInteger(c.c) && Number.isInteger(c.r) && typeof c.walkable === "boolean") {
      walkability[`${c.c},${c.r}`] = c.walkable;
    }
  });
  const defaultWalkable = !(grid && grid.cellDefault && grid.cellDefault.walkable === false);
  const isWalkable = (x, y) => {
    const key = `${x},${y}`;
    return Object.prototype.hasOwnProperty.call(walkability, key) ? walkability[key] : defaultWalkable;
  };
  const curId = ((tokens || []).find((t) => t.isCurrent) || {}).id || "";
  const cells = [];
  for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) cells.push({ x, y, t: at[`${x},${y}`] });
  return (
    <div style={{
      position: "relative", width: "100%", height: "calc(100% - 50px)", overflow: "hidden",
      background:
        `radial-gradient(ellipse at 50% 40%, rgba(60,30,10,0.2), transparent 70%),
         linear-gradient(135deg, #3a2418 0%, #25160e 100%)`,
      boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 60px rgba(0,0,0,0.6)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 12,
    }}>
      {sceneScope ? (
        <Img scope={sceneScope} label="scene · battlefield" w="100%" h="100%" fit="cover"
          style={{ position: "absolute", inset: 0, zIndex: 0, opacity: 0.9 }} />
      ) : null}
      <div style={{
        position: "relative", zIndex: 1,
        display: "grid",
        gridTemplateColumns: `repeat(${cols}, 1fr)`, gridTemplateRows: `repeat(${rows}, 1fr)`,
        gap: 1, aspectRatio: `${cols} / ${rows}`, height: "100%", maxWidth: "100%", width: "auto",
        boxShadow: "inset 0 0 0 1px rgba(176,141,87,0.25)",
      }}>
        {cells.map(({ x, y, t }) => {
          const isFoe = t && t.team === "foe";
          const walkable = isWalkable(x, y);
          // Selecting a token is ALWAYS allowed (inspect on any turn); move/attack gate on can_act.
          const onActivate = t
            ? () => { if (isFoe && canAct) onAttack(t.id); else onSelect(t.id); }
            : () => { if (canAct && walkable) onCellMove(x, y); };
          const actionable = t ? true : (canAct && walkable);   // gets button semantics + keyboard
          const hoverBg = t ? (isFoe && canAct ? "rgba(197,64,64,0.22)" : "rgba(244,210,123,0.10)") : "rgba(244,210,123,0.12)";
          const title = t ? `${t.name}${isFoe && canAct ? " — attack" : ""}` : (walkable ? `move → (${x}, ${y})` : "blocked");
          const restBox = walkable ? "inset 0 0 0 0.5px rgba(176,141,87,0.10)" : "inset 0 0 0 0.5px rgba(80,40,30,0.5)";
          return (
            <div key={`${x},${y}`}
              title={title} aria-label={title}
              role={actionable ? "button" : undefined}
              tabIndex={actionable ? 0 : -1}
              onClick={onActivate}
              onKeyDown={(e) => { if (actionable && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onActivate(); } }}
              onMouseEnter={(e) => { if (actionable) e.currentTarget.style.background = hoverBg; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
              onFocus={(e) => { if (actionable) e.currentTarget.style.boxShadow = "inset 0 0 0 1.5px var(--gold-glow)"; }}
              onBlur={(e) => { e.currentTarget.style.boxShadow = restBox; }}
              style={{
                position: "relative", boxShadow: restBox,
                background: walkable ? "transparent" : "rgba(20,10,6,0.45)",
                cursor: actionable ? "pointer" : "default", outline: "none",
                transition: "background 0.08s, box-shadow 0.08s",
                display: "flex", alignItems: "center", justifyContent: "center",
              }}>
              {t ? <GridCellToken t={t} selected={selected === t.id} isCurrent={t.id === curId} /> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* A token sized to its grid cell: circle + team tint + isCurrent ring + tiny HP pip. */
function GridCellToken({ t, selected, isCurrent }) {
  const isFoe = t.team === "foe";
  const ratio = healthRatio(t);
  const ring = isCurrent
    ? "0 0 0 2px var(--gold-glow), 0 0 16px rgba(244,210,123,0.7)"
    : (selected ? "0 0 0 2px rgba(244,210,123,0.6)" : "none");
  return (
    <div style={{ width: "84%", height: "84%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 2 }}>
      <div style={{
        width: "72%", aspectRatio: "1", borderRadius: "50%",
        background: isFoe
          ? "radial-gradient(circle at 35% 30%, #d35a5a, #7a1414 70%, #3a0a0a)"
          : "radial-gradient(circle at 35% 30%, var(--p-100, #e8d6a8), var(--p-300, #b08d57) 70%, #4a3618)",
        boxShadow: `${ring}, inset 0 -2px 4px rgba(0,0,0,0.4)`,
        display: "flex", alignItems: "center", justifyContent: "center",
        color: isFoe ? "#fff" : "#2a1c08", fontFamily: "var(--f-display)", fontWeight: 700, fontSize: "min(2.4vmin, 17px)",
      }}>{t.initial || (t.name || "?").slice(0, 1)}</div>
      <div style={{ width: "72%", height: 3, background: "rgba(0,0,0,0.5)", boxShadow: "inset 0 0 0 0.5px rgba(0,0,0,0.6)" }}>
        <div style={{ width: `${Math.round(ratio * 100)}%`, height: "100%", background: hpBarFill(t, isFoe) }} />
      </div>
    </div>
  );
}

/* HONEST RENDERING (graphics #432 / #438): the engine's combat is GRIDLESS / named-zone. A
   token's x/y are DERIVED render-hints (positionAuthority:"derived"), never authoritative — so
   we must NOT draw a measured VTT grid and place tokens by x/y. The previous board did exactly
   that: a fixed 16×10 grid with tokens at (t.x, t.y), which fell back to `Number(t.x) || 1` and
   piled EVERY token onto cell (1,1) whenever the engine sent no coordinates (the normal case),
   while zones floated as disconnected labels. Instead we render ZONE BANDS and group each
   combatant under its named zone — the same honest model the M1/M2 graphical renderers use.

   A real measured-tile grid (range/LoS/flanking) is the evidence-gated #461 future: it requires
   the engine to gain authoritative coordinates (positionAuthority:"engine" + grid.mode==="grid").
   When that lands, a grid board plugs in here behind that flag; until then, zones are the truth. */
function CombatMap({ tokens, zones, grid, selected, onSelect, onCellMove, onAttack, canAct, sceneScope }) {
  // #10/#461: when the engine sends authoritative cell coords (grid.mode==="grid"), render a real
  // tactical board (the slot this comment-block reserved). Otherwise fall back to the zone bands.
  if (grid && grid.mode === "grid" && Number(grid.cols) > 0 && Number(grid.rows) > 0) {
    return (
      <CombatGridBoard
        tokens={tokens} grid={grid} selected={selected} onSelect={onSelect}
        onCellMove={onCellMove} onAttack={onAttack} canAct={canAct} sceneScope={sceneScope}
      />
    );
  }
  const named = (zones || []).map((z) => (typeof z === "string" ? z : z && z.name)).filter(Boolean);
  const FIELD = "The Field";
  let zoneNames = named.slice();
  if (!zoneNames.length) zoneNames = [...new Set(tokens.map((t) => t.zone).filter(Boolean))];
  const inList = (t) => Boolean(t.zone) && zoneNames.includes(t.zone);
  if (!zoneNames.length) zoneNames = [FIELD];
  else if (tokens.some((t) => !inList(t))) zoneNames = [...zoneNames, FIELD];
  const byZone = {};
  zoneNames.forEach((z) => (byZone[z] = []));
  tokens.forEach((t) => byZone[inList(t) ? t.zone : FIELD].push(t));

  return (
    <div style={{
      position: "relative",
      width: "100%", height: "calc(100% - 50px)",
      background:
        `radial-gradient(ellipse at 50% 50%, rgba(60,30,10,0.2), transparent 70%),
         linear-gradient(135deg, #3a2418 0%, #25160e 100%)`,
      boxShadow: "inset 0 0 0 1px var(--w-500), inset 0 0 60px rgba(0,0,0,0.6)",
      overflow: "hidden",
      display: "flex", gap: 10, padding: 12,
    }}>
      {/* FIX A (combat scene backdrop): render the location/scene art as an
          absolute-inset cover image BEHIND the zone bands (zIndex 0), mirroring the
          dialogue screen's backdrop (screen-dialogue.jsx:177). The zone bands keep
          their semi-transparent fills so the place reads through. <Img> degrades to a
          silhouette/placeholder when the scope is empty/unservable, so an empty
          sceneScope is a graceful no-op (the gradient base layer stays visible). */}
      {sceneScope ? (
        <Img
          scope={sceneScope}
          label="scene · battlefield"
          w="100%"
          h="100%"
          fit="cover"
          style={{ position: "absolute", inset: 0, zIndex: 0 }}
        />
      ) : null}
      {/* Darkening scrim so token art / HP bars / zone labels keep contrast over a
          bright backdrop (matches the dialogue vignette intent). Behind the bands. */}
      {sceneScope ? (
        <div style={{
          position: "absolute", inset: 0, zIndex: 0,
          background: "radial-gradient(ellipse at 50% 50%, rgba(20,10,4,0.35), rgba(20,10,4,0.72) 100%)",
          pointerEvents: "none",
        }} />
      ) : null}
      {zoneNames.map((zn) => (
        <div key={zn} style={{
          flex: 1, minWidth: 0,
          // FIX A: sit the zone bands above the absolute backdrop + scrim (zIndex 0).
          position: "relative", zIndex: 1,
          display: "flex", flexDirection: "column",
          background: "rgba(30,18,10,0.30)",
          boxShadow: "inset 0 0 0 1px rgba(176,141,87,0.18)",
        }}>
          <div title={zn} style={{
            padding: "5px 8px",
            fontFamily: "var(--f-display)", fontSize: 9, letterSpacing: "0.12em",
            textTransform: "uppercase", color: "rgba(244, 210, 123, 0.75)",
            borderBottom: "1px solid rgba(176,141,87,0.18)",
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>{zn}</div>
          <div style={{
            flex: 1, display: "flex", flexWrap: "wrap", gap: 12,
            alignContent: "flex-start", justifyContent: "center",
            padding: "16px 8px", overflow: "auto",
          }}>
            {byZone[zn].length
              ? byZone[zn].map((t) => (
                  <CombatToken key={t.id} t={t} selected={selected === t.id} onClick={() => onSelect(t.id)} />
                ))
              : <div className="body-sm" style={{ color: "rgba(212,185,122,0.35)", alignSelf: "center" }}>empty</div>}
          </div>
        </div>
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

/* B-07 — never paint a precise-looking HP fraction the engine doesn't actually know. When
   `hpKnown` is false (foes, until the party learns their HP) the bar still shows the qualitative
   band the player CAN observe (steady / wounded / bloodied / down), but overlaid with a diagonal
   hatch so it reads as an *estimate*, not an exact value. A known bar stays a clean solid fill. */
function hpBarFill(t, isFoe) {
  const solid = isFoe
    ? "linear-gradient(180deg, #d63a3a, #8a1a1a)"
    : "linear-gradient(180deg, #5cd56a, #2a8c39)";
  if (t.hpKnown) return solid;
  const hatch = "repeating-linear-gradient(45deg, rgba(255,255,255,0.28) 0 2px, transparent 2px 5px)";
  return `${hatch}, ${solid}`;
}

/* Initiative-row variant: the ally bar greens-then-reds on the 50% threshold; reuse the same
   honest-hatch treatment for unknown HP. */
function hpBarFillInit(t, isFoe, ratio) {
  const solid = isFoe
    ? "linear-gradient(180deg, var(--crimson), #4a1010)"
    : (ratio > 0.5 ? "linear-gradient(180deg, #5a8a3a, #3a6020)" : "linear-gradient(180deg, var(--crimson), #4a1010)");
  if (t.hpKnown) return solid;
  const hatch = "repeating-linear-gradient(45deg, rgba(255,255,255,0.3) 0 2px, transparent 2px 5px)";
  return `${hatch}, ${solid}`;
}

/* A single combatant token — now a FLOW element laid out inside its zone band (no absolute
   x/y on a fake grid). Visual language (circle, foe/ally tint, selection glow, honest HP bar,
   name) is preserved; only positioning changed from x/y-on-grid to zone-grouped flow. */
/* FIX B (condition chips): a single compact status badge below a token, styled after
   the existing CueChip (~line 397). Kept tiny so a row of 2-3 never overflows the
   48px token column. Harmless slug-casing of the engine's condition string. */
function ConditionChip({ condition }) {
  const text = String(condition || "");
  if (!text) return null;
  return (
    <span title={text} style={{
      fontFamily: "var(--f-mono)",
      fontSize: 7,
      lineHeight: 1.2,
      textTransform: "uppercase",
      letterSpacing: "0.04em",
      color: "var(--ink-700)",
      padding: "1px 3px",
      background: "rgba(176,141,87,0.16)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.30)",
      maxWidth: 44,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    }}>{text}</span>
  );
}

function CombatToken({ t, selected, onClick }) {
  const isFoe = t.team === "foe";
  const hpRatio = healthRatio(t);
  // FIX B: render the first ~3 engine-supplied conditions as chips. No-op ([]/missing
  // -> nothing renders) so a clean token is byte-for-byte unchanged. The engine is the
  // sole writer; the viewer only reflects the conditions list already on the token.
  const conditions = Array.isArray(t.conditions) ? t.conditions.filter(Boolean) : [];
  const shownConditions = conditions.slice(0, 3);
  return (
    <button onClick={onClick} title={t.name} style={{
      position: "relative",
      width: 48,
      background: "none",
      cursor: "pointer",
      padding: 0,
      display: "flex", flexDirection: "column", alignItems: "center", gap: 3,
      zIndex: selected ? 10 : 5,
    }}>
      <div style={{
        width: 48, height: 48,
        borderRadius: "50%",
        background: isFoe
          ? "radial-gradient(circle at 30% 30%, #c54040, var(--crimson) 60%, #3a0a0a)"
          : "radial-gradient(circle at 30% 30%, var(--p-100), var(--p-300) 60%, var(--b-500))",
        boxShadow: selected
          ? `inset 0 0 0 2px var(--gold-glow), 0 0 0 3px ${isFoe ? "var(--crimson-bright)" : "var(--gold-glow)"}, 0 0 24px rgba(244, 210, 123, 0.7), 0 4px 8px rgba(0,0,0,0.5)`
          : `inset 0 0 0 2px ${isFoe ? "#5a1414" : "var(--b-500)"}, 0 0 0 2px ${isFoe ? "var(--crimson)" : "var(--b-300)"}, 0 4px 8px rgba(0,0,0,0.6)`,
        display: "grid", placeItems: "center",
        overflow: "hidden",
      }}>
        <Img
          scope={tokenScope(t)}
          label={t.initial}
          w="100%"
          h="100%"
          fit="cover"
          style={{ width: "100%", height: "100%", borderRadius: "50%" }}
        />
      </div>
      <div style={{
        width: 44, height: 4,
        background: "rgba(0,0,0,0.5)",
        boxShadow: "0 0 0 1px rgba(0,0,0,0.8)",
        position: "relative",
      }}>
        <div style={{
          position: "absolute", left: 0, top: 0, bottom: 0,
          width: `${hpRatio * 100}%`,
          background: hpBarFill(t, isFoe),
        }} />
      </div>
      {/* FIX B: condition chips below the HP bar. No-op when there are none. */}
      {shownConditions.length ? (
        <div style={{
          display: "flex", flexWrap: "wrap", gap: 2,
          justifyContent: "center", maxWidth: 64,
        }}>
          {shownConditions.map((c, i) => (
            <ConditionChip key={`${c}-${i}`} condition={c} />
          ))}
        </div>
      ) : null}
      <div style={{
        fontFamily: "var(--f-display)", fontSize: 8, letterSpacing: "0.1em",
        textTransform: "uppercase", whiteSpace: "nowrap", maxWidth: 64,
        overflow: "hidden", textOverflow: "ellipsis",
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
      <Img scope={tokenScope({ id: token.id || row.id, name: token.name || row.name, team: row.team })} label={token.short || "?"} w={36} h={36} framed />
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
              background: hpBarFillInit(token, isFoe, hpRatio),
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
  tokenScope,
  hpBarFill,
  hpBarFillInit,
});
