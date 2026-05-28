/* Screen: Inventory & Stash.
   Wired to the live /inventory-surface read model (each party member's pack + currency,
   plus a flat shared-stash view). Polls every 5s while visible. Before the first fetch
   (or if it fails) the screen shows a clean empty-state — it never falls back to demo
   data. The per-hero coin purse comes from the live currency.
   Layout/design unchanged from the prototype. */

/* W2c: item-icon scope helper — lowercase, non-alphanumeric → "-". Used as a fallback
   when item.id is absent (it is always present in the live surface, but kept for safety). */
function slug(name) {
  return (name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

/* Resolve the /image scope for an inventory item. Ingested item art is keyed by a name-slug
   ("item:<slug>"), and the server normalises "item-<slug>" to the same key — so we must build
   the scope from a slug of the item NAME, exactly as the equipped slots do ("item-"+slug(name)).
   The engine item.id is a composite "{character_id}:{idx}:{name}" which normalises to a unique
   per-instance key that never matches the shared art, so it must NOT be used for the scope. */
function itemScope(item) {
  const s = slug(item && item.name);
  return s ? "item-" + s : "";
}

function ScreenInventory({ onNavigate, state, setState }) {
  const surfaceQuery = window.combatSurfaceFromCampaign
    ? window.combatSurfaceFromCampaign(
        (Array.isArray(state?.campaigns) ? state.campaigns : []).find((c) => c.id === state?.activeCampaign) ||
          (Array.isArray(state?.campaigns) ? state.campaigns : [])[0] || {},
        state,
      )
    : "";
  const [surface, setSurface] = React.useState(null);
  // Live party only — never fall back to the bundled demo data (PF1e leak).
  const party = (Array.isArray(surface?.party) && surface.party.length)
    ? surface.party
    : [];
  const [filter, setFilter] = React.useState("all");
  const [activeHero, setActiveHero] = React.useState("");
  const [selectedItem, setSelectedItem] = React.useState(null);
  const [ctxMenu, setCtxMenu] = React.useState(null);
  const toast = window.useToast ? window.useToast() : (() => {});

  // Phase-4 wiring: when a live session is attached the surface reports can_act +
  // campaign_id, so equip/use/give/drop land as a constrained `do`/`use_item` move on
  // the engine via POST /move (the DM resolves it). When can_act is false the actions
  // stay honestly display-only — they never silently no-op a "saved" claim.
  const canAct = Boolean(surface?.can_act);
  const campaignId = surface?.campaign_id || "";
  const postInvMove = React.useCallback((kind, fields, okToast) => {
    fetch("/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind, campaign: campaignId, ...fields }),
    }).then((r) => {
      if (!r.ok) throw new Error("move " + r.status);
      toast(okToast);
    }).catch((e) => toast({ kind: "danger", title: "Move not sent", body: e?.message || "viewer unreachable" }));
  }, [campaignId, toast]);

  const loadSurface = React.useCallback(async (isCancelled = () => false) => {
    try {
      const response = await fetch("/inventory-surface" + surfaceQuery, { cache: "no-store" });
      if (!response.ok) throw new Error(`inventory surface ${response.status}`);
      const payload = await response.json();
      if (!isCancelled()) setSurface(payload);
    } catch (error) { /* keep last good / demo fallback */ }
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

  const hero = party.find((p) => p.id === activeHero) || party[0] || null;
  // The stash is the active hero's own pack (live surface); never the demo stash.
  const stash = (hero && Array.isArray(hero.items)) ? hero.items
    : (Array.isArray(surface?.stash) ? surface.stash : []);

  React.useEffect(() => {
    if (party.length && !party.some((p) => p.id === activeHero)) {
      setActiveHero(party[0]?.id || "");
    }
  }, [party, activeHero]);

  React.useEffect(() => {
    if (!selectedItem || !stash.some((i) => i.id === selectedItem.id)) {
      setSelectedItem(stash[0] || null);
    }
  }, [stash, selectedItem]);

  const filtered = filter === "all"
    ? stash
    : stash.filter((i) => i.type === filter);

  // No live hero yet (pre-fetch, fetch failed, or an empty party) — clean empty-state
  // instead of dereferencing a fabricated hero or leaking demo data.
  if (!hero) {
    return (
      <div className="screen" style={{ height: "100%", display: "grid", placeItems: "center", padding: 14 }}>
        <Panel framed style={{ padding: 40, textAlign: "center", maxWidth: 420 }}>
          <div className="eyebrow" style={{ color: "var(--ink-600)" }}>Inventory &amp; Stash</div>
          <h2 className="h1" style={{ fontSize: 20, marginTop: 6 }}>No party in this world</h2>
          <p className="hand muted" style={{ fontSize: 14, marginTop: 8 }}>
            Select a hero to view their pack and coin purse. Once a campaign is live,
            each member's inventory appears here.
          </p>
        </Panel>
      </div>
    );
  }

  return (
    <div className="screen" style={{ height: "100%", display: "grid", gridTemplateColumns: "320px 1fr 320px", gap: 14, padding: 14 }}>

      {/* LEFT — Hero & equipped */}
      <Panel framed style={{ padding: 22, overflow: "auto" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>{hero.alignment}</div>
        <h2 className="h1" style={{ fontSize: 22 }}>{hero.name}</h2>
        <div className="hand" style={{ fontSize: 14, color: "var(--ink-700)" }}>Lv {hero.level} {hero.class}</div>

        {/* Hero switcher — pick whose pack to view (live surface gives each their own). */}
        {party.length > 1 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 10 }}>
            {party.map((p) => (
              <button key={p.id} onClick={() => setActiveHero(p.id)} className="pill" style={{
                cursor: "pointer",
                background: activeHero === p.id ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(176,141,87,0.08)",
                color: activeHero === p.id ? "var(--w-300)" : "var(--ink-700)",
                boxShadow: activeHero === p.id ? "inset 0 0 0 1px var(--b-600)" : "inset 0 0 0 1px rgba(140,100,60,0.3)",
              }}>{(p.name || "").split(" ")[0]}</button>
            ))}
          </div>
        )}

        {/* Hero portrait + slots */}
        <div style={{ position: "relative", marginTop: 16, padding: "0 8px" }}>
          <Img scope={hero.id ? "portrait-" + hero.id : ""} label={`${hero.short} · full art`} h={220} framed style={{ width: "100%" }} />

          {/* Equipment slots ringing portrait */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 6, marginTop: 10 }}>
            {EQUIP_SLOTS.map((s) => {
              const equipped = hero.equipped.find((e) => e.slot === s.label);
              return (
                <div key={s.label} style={{ textAlign: "center" }}>
                  {equipped
                    ? <Img scope={"item-" + slug(equipped.name)} label={equipped.name} w="100%" h={44} framed />
                    : <Placeholder label={s.label} w="100%" h={44} framed />}
                  <div style={{ fontFamily: "var(--f-mono)", fontSize: 8, marginTop: 2, color: "var(--ink-600)" }}>
                    {s.label}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <Divider />

        {/* Weapon Set + Encumbrance were removed: the live /inventory-surface exposes no
            loadout/weapon-set concept and no carry-capacity or STR, and per-item weight is a
            display string ("3 lb"/"—") that isn't reliably summable — any bar here would be a
            fabricated number. The equipment slots above already show worn gear from
            hero.equipped (live). Coin Purse below is the hero's live currency. */}

        <div className="eyebrow">Coin Purse</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginTop: 6 }}>
          <CoinSlot tone="#e5e4e2" label="PP" val={String(hero.currency?.pp ?? 0)} />
          <CoinSlot tone="#d4b97a" label="GP" val={String(hero.currency?.gp ?? 0)} />
          <CoinSlot tone="#c0c0c0" label="SP" val={String(hero.currency?.sp ?? 0)} />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 6 }}>
          <CoinSlot tone="#b08860" label="EP" val={String(hero.currency?.ep ?? 0)} />
          <CoinSlot tone="#8a6a45" label="CP" val={String(hero.currency?.cp ?? 0)} />
        </div>
      </Panel>

      {/* CENTER — Shared Stash */}
      <Panel framed style={{ padding: 22, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <SectionTitle ordinal="II.">Shared Stash</SectionTitle>
        </div>

        {/* Filter chips */}
        <div style={{ display: "flex", gap: 6, marginBottom: 14, marginTop: -8 }}>
          {[
            { id: "all", label: "All" },
            { id: "weapon", label: "Arms" },
            { id: "armor", label: "Armor" },
            { id: "spell", label: "Reagents" },
            { id: "quest", label: "Quest" },
            { id: "rare", label: "Relics" },
            { id: "common", label: "Sundries" },
          ].map((f) => (
            <button key={f.id} onClick={() => setFilter(f.id)} className={`pill ${filter === f.id ? "" : "muted"}`} style={{
              cursor: "pointer",
              background: filter === f.id ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(176,141,87,0.1)",
              color: filter === f.id ? "var(--w-300)" : "var(--ink-700)",
              boxShadow: filter === f.id ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)" : "inset 0 0 0 1px rgba(140,100,60,0.3)",
            }}>{f.label}</button>
          ))}
        </div>

        {/* Stash grid */}
        {stash.length === 0 ? (
          <div style={{
            flex: 1, display: "grid", placeItems: "center", textAlign: "center",
            padding: 24,
            background: "rgba(80,50,20,0.06)",
            boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
          }}>
            <div>
              <div className="eyebrow" style={{ color: "var(--ink-600)" }}>Empty pack</div>
              <div className="hand muted" style={{ fontSize: 14, marginTop: 6 }}>
                {(hero.name || "").split(" ")[0] || "This hero"} is carrying nothing yet.
              </div>
            </div>
          </div>
        ) : (
          <div style={{
            flex: 1, overflow: "auto",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(72px, 1fr))",
            gap: 8,
            padding: 12,
            background: "rgba(80,50,20,0.06)",
            boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
            alignContent: "start",
          }}>
            {filtered.map((it) => (
              <ItemSlot
                key={it.id}
                item={it}
                selected={selectedItem?.id === it.id}
                onClick={() => setSelectedItem(it)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setSelectedItem(it);
                  setCtxMenu({ x: e.clientX, y: e.clientY, item: it });
                }}
              />
            ))}
            {Array.from({ length: Math.max(0, 60 - filtered.length) }).map((_, i) => (
              <Placeholder key={`e${i}`} w="100%" h={68} label="" />
            ))}
          </div>
        )}

        <div style={{ marginTop: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span className="muted body-sm">{filtered.length} items · {stash.length} total</span>
          <div style={{ display: "flex", gap: 6 }}>
            <BrassButton tone="ghost" size="sm" disabled title="Display-only — not saved to the engine">Sort (preview)</BrassButton>
            <BrassButton tone="ghost" size="sm" disabled title="Display-only — not saved to the engine">Mark Trash (preview)</BrassButton>
            <BrassButton size="sm" disabled title="Display-only — not saved to the engine">Loot Pile (preview)</BrassButton>
          </div>
        </div>
      </Panel>

      {/* RIGHT — Item detail */}
      <Panel framed style={{ padding: 22, overflow: "auto" }}>
        {selectedItem ? <ItemDetail item={selectedItem} hero={hero} toast={toast} canAct={canAct} postInvMove={postInvMove} /> : <div className="muted">Select an item.</div>}
      </Panel>

      {ctxMenu && (
        <window.ContextMenu
          x={ctxMenu.x} y={ctxMenu.y}
          onClose={() => setCtxMenu(null)}
          items={[
            { label: "Examine", icon: "◈", hint: "E", onClick: () => toast({ kind: "item", title: ctxMenu.item.name, body: ctxMenu.item.desc }) },
            canAct
              ? { label: "Equip", icon: "⚔", hint: "Q", title: "Relays to the DM via /move — the engine resolves it", onClick: () => postInvMove("do", { text: "I equip " + ctxMenu.item.name + "." }, { kind: "item", title: "Equipping " + ctxMenu.item.name, body: hero.name + " takes it up — relayed to the DM." }) }
              : { label: "Equip (preview)", icon: "⚔", hint: "Q", disabled: true, title: "Display-only — start a live session to act", onClick: () => toast({ kind: "item", title: "Equipped: " + ctxMenu.item.name, body: hero.name + " takes it up." }) },
            canAct
              ? { label: "Use", icon: "✦", title: "Relays to the DM via /move — the engine resolves it", onClick: () => postInvMove("use_item", { name: ctxMenu.item.name, text: "I use " + ctxMenu.item.name + "." }, { kind: "item", title: "Using " + ctxMenu.item.name, body: "Relayed to the DM." }) }
              : { label: "Use (preview)", icon: "✦", disabled: true, title: "Display-only — start a live session to act", onClick: () => toast({ kind: "item", title: "Used: " + ctxMenu.item.name }) },
            { divider: true },
            canAct
              ? { label: "Hand to a companion", icon: "→", title: "Relays to the DM via /move — the engine resolves it", onClick: () => postInvMove("do", { text: "I hand the " + ctxMenu.item.name + " to a companion." }, { kind: "item", title: "Handing over " + ctxMenu.item.name, body: "Relayed to the DM." }) }
              : { label: "Hand to a companion (preview)", icon: "→", disabled: true, title: "Display-only — start a live session to act", onClick: () => toast({ kind: "item", title: ctxMenu.item.name + " handed over" }) },
            { divider: true },
            canAct
              ? { label: "Drop", icon: "▾", tone: "crimson", title: "Relays to the DM via /move — the engine resolves it", onClick: () => postInvMove("do", { text: "I drop the " + ctxMenu.item.name + "." }, { kind: "danger", title: "Dropping " + ctxMenu.item.name, body: "Relayed to the DM — you will not get it back unless you fetch it yourself." }) }
              : { label: "Drop (preview)", icon: "▾", tone: "crimson", disabled: true, title: "Display-only — start a live session to act", onClick: () => toast({ kind: "danger", title: "Dropped: " + ctxMenu.item.name, body: "You will not get it back unless you fetch it yourself." }) },
          ]}
        />
      )}
    </div>
  );
}

const EQUIP_SLOTS = [
  { label: "Head" }, { label: "Neck" }, { label: "Body" },
  { label: "Hands" }, { label: "Ring" }, { label: "Boots" },
];

function CoinSlot({ tone, label, val }) {
  return (
    <div style={{
      padding: 8, textAlign: "center",
      background: "rgba(176,141,87,0.08)",
      boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
    }}>
      <div style={{
        width: 24, height: 24, borderRadius: "50%", margin: "0 auto",
        background: `radial-gradient(circle at 30% 30%, ${tone}, color-mix(in oklab, ${tone}, black 40%))`,
        boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.4)",
      }} />
      <div className="eyebrow" style={{ fontSize: 9, marginTop: 4 }}>{label}</div>
      <div style={{ fontFamily: "var(--f-display)", fontSize: 14, color: "var(--ink-900)" }}>{val}</div>
    </div>
  );
}

function ItemSlot({ item, selected, onClick, onContextMenu }) {
  const tone = item.type === "rare" ? "var(--royal)" :
               item.type === "quest" ? "var(--crimson)" :
               item.type === "spell" ? "var(--royal-bright)" :
               item.type === "weapon" ? "var(--ink-700)" :
               "var(--b-500)";
  return (
    <window.Tooltip content={<window.ItemTooltip item={item} />} side="top">
      <button onClick={onClick} onContextMenu={onContextMenu} style={{
      position: "relative",
      padding: 0,
      height: 68,
      cursor: "pointer",
      background:
        item.type === "rare" ? "linear-gradient(180deg, color-mix(in oklab, var(--royal) 18%, var(--p-100)), color-mix(in oklab, var(--royal) 30%, var(--p-200)))" :
        item.type === "quest" ? "linear-gradient(180deg, color-mix(in oklab, var(--crimson) 12%, var(--p-100)), color-mix(in oklab, var(--crimson) 22%, var(--p-200)))" :
        "linear-gradient(180deg, var(--p-100), var(--p-200))",
      boxShadow: selected
        ? `inset 0 0 0 1px var(--b-600), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-300), 0 0 16px -2px var(--gold-glow)`
        : `inset 0 0 0 1px ${tone}`,
      display: "flex", alignItems: "center", justifyContent: "center",
      transition: "all 120ms",
    }}>
      <Img
        scope={itemScope(item)}
        label={item.name}
        w={44}
        h={44}
        fit="contain"
        style={{ pointerEvents: "none" }}
      />
      {item.qty > 1 && (
        <span style={{
          position: "absolute", bottom: 2, right: 4,
          fontFamily: "var(--f-display)", fontSize: 11,
          color: "var(--ink-900)",
          textShadow: "0 1px 0 var(--p-100)",
        }}>{item.qty}</span>
      )}
      </button>
    </window.Tooltip>
  );
}

function ItemDetail({ item, hero, toast, canAct, postInvMove }) {
  return (
    <div>
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <Img scope={itemScope(item)} label={item.name} w={72} h={72} framed />
        <div>
          <div className="eyebrow" style={{ color:
            item.type === "rare" ? "var(--royal)" :
            item.type === "quest" ? "var(--crimson)" :
            "var(--ink-600)" }}>
            {ITEM_TYPES[item.type] || item.type}
          </div>
          <h2 className="h1" style={{ fontSize: 20, lineHeight: 1.1, marginTop: 2 }}>{item.name}</h2>
        </div>
      </div>

      <Divider />

      <p className="body dropcap" style={{ marginTop: 0, fontSize: 15 }}>
        {item.desc}
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 14 }}>
        <StatLine k="Weight" v={item.weight || "—"} />
        <StatLine k="Value" v={item.value || "—"} />
        <StatLine k="Slot" v={item.slot || "—"} />
        <StatLine k="Origin" v={item.origin || "Unknown"} />
      </div>

      {item.properties && (
        <>
          <Divider />
          <div className="eyebrow">Properties</div>
          <div className="tag-row" style={{ marginTop: 6 }}>
            {item.properties.map((p) => <Pill key={p}>{p}</Pill>)}
          </div>
        </>
      )}

      {item.lore && (
        <>
          <Divider />
          <div className="eyebrow">Marginalia</div>
          <div className="hand" style={{ fontSize: 14, marginTop: 6, color: "var(--ink-700)" }}>
            "{item.lore}"
            {item.loreBy && (
              <div className="muted" style={{ fontFamily: "var(--f-body)", fontStyle: "normal", fontSize: 12, marginTop: 4 }}>
                — {item.loreBy}
              </div>
            )}
          </div>
        </>
      )}

      <div style={{ display: "flex", gap: 6, marginTop: 18, flexWrap: "wrap" }}>
        {canAct ? (
          <BrassButton size="sm" title="Relays to the DM via /move — the engine resolves it" onClick={() => postInvMove("use_item", { name: item.name, text: "I use " + item.name + "." }, { kind: "item", title: "Using " + item.name, body: "Relayed to the DM." })}>
            Use
          </BrassButton>
        ) : (
          <BrassButton size="sm" disabled title="Display-only — start a live session to act" onClick={() => toast && toast({ kind: "item", title: "Used: " + item.name })}>
            Use (preview)
          </BrassButton>
        )}
        <BrassButton tone="ghost" size="sm" onClick={() => toast && toast({ kind: "item", title: item.name, body: item.desc })}>Examine</BrassButton>
        {canAct ? (
          <BrassButton tone="ghost" size="sm" title="Relays to the DM via /move — the engine resolves it" onClick={() => postInvMove("do", { text: "I drop the " + item.name + "." }, { kind: "danger", title: "Dropping " + item.name, body: "Relayed to the DM." })}>
            Drop
          </BrassButton>
        ) : (
          <BrassButton tone="ghost" size="sm" disabled title="Display-only — start a live session to act">Drop (preview)</BrassButton>
        )}
      </div>
    </div>
  );
}

const ITEM_TYPES = {
  weapon: "Arms",
  armor: "Armor",
  spell: "Reagent",
  quest: "Quest Item",
  rare: "Relic",
  common: "Sundry",
};

function toRoman(n) { return ["", "I", "II", "III", "IV", "V"][n] || n; }

Object.assign(window, { ScreenInventory, CoinSlot, ItemSlot, ItemDetail, EQUIP_SLOTS, ITEM_TYPES, toRoman, slug, itemScope });
