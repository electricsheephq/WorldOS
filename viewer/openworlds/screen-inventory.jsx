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
  if (window.itemArtScope) return window.itemArtScope(item);
  const s = slug(item && item.name);
  return s ? "item-" + s : "";
}

/* Hero portrait scope — like itemScope, derive from the NAME slug. Ingested canon art is
   keyed "portrait_<name-slug>"; a loaded party member's id is a random instance hash
   ("char_…") that matches no art, so building the scope from slug(name) is what makes a
   canon hero's real face render (portrait-less heroes still fall back to the silhouette). */
function heroPortraitScope(hero) {
  const s = slug(hero && hero.name);
  if (s) return "portrait-" + s;
  return (hero && hero.id) ? "portrait-" + hero.id : "";
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
  // I-06: client-side sort order for the stash grid (display-only reordering — never an engine
  // write). "found" keeps the surface's own order (as carried). Cycles found → name → type.
  const [sortKey, setSortKey] = React.useState("found");
  const [activeHero, setActiveHero] = React.useState("");
  const [selectedItem, setSelectedItem] = React.useState(null);
  const [ctxMenu, setCtxMenu] = React.useState(null);
  // #756: a monotonically-bumped nonce the right-click "Examine" raises so the detail
  // pane opens its read-only Examine PANEL (not a toast). ItemDetail watches it.
  const [examineNonce, setExamineNonce] = React.useState(0);
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

  // RRI-25e55fa optimizer #5 (Stash/Market examine PARITY): the read-only /item-catalog map the
  // Stash inspector backfills from. Declared here so the fetch effect (after the stash list is
  // derived below) can populate it; merged through the SAME window.enrichWare the Market uses.
  const [catalog, setCatalog] = React.useState({});

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
  // RRI-5e98e6f: derive the displayed coin purse from the ONE shared selector + normalizer the
  // Market also uses, so the same character's coins read identically on both screens (no
  // 35-vs-232 divergence). partyPurse resolves the active hero (else party[0]) and zeroes/ints it.
  const purse = window.partyPurse
    ? window.partyPurse(party, hero?.id || activeHero)
    : { pp: 0, gp: Number(hero?.currency?.gp) || 0, sp: 0, ep: 0, cp: 0 };
  const purseTotalGp = window.currencyTotalGp ? window.currencyTotalGp(purse) : purse.gp;
  // The stash is the active hero's own pack (live surface); never the demo stash.
  const stash = (hero && Array.isArray(hero.items)) ? hero.items
    : (Array.isArray(surface?.stash) ? surface.stash : []);

  // Fetch the SRD catalog for the current stash's item names (read-only), so the Stash
  // inspector can backfill a stat the granted item didn't persist (range / value / properties)
  // through the SAME window.enrichWare helper the Market uses. Refetched only when the set of
  // names changes; an unreachable endpoint degrades to the item's own persisted fields.
  const stashNamesKey = React.useMemo(
    () => Array.from(new Set((stash || []).map((i) => i && i.name).filter(Boolean))).sort().join("\u0000"),
    [stash],
  );
  React.useEffect(() => {
    const names = stashNamesKey ? stashNamesKey.split("\u0000") : [];
    if (!names.length) { setCatalog({}); return; }
    let cancelled = false;
    const q = names.map((n) => "name=" + encodeURIComponent(n)).join("&");
    fetch("/item-catalog?" + q, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!cancelled && d && d.items) setCatalog(d.items); })
      .catch(() => { /* keep last good; the inspector still shows the item's persisted fields */ });
    return () => { cancelled = true; };
  }, [stashNamesKey]);

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

  const _typeFiltered = filter === "all"
    ? stash
    : stash.filter((i) => i.type === filter);
  // I-06: apply the client-side sort as a render-only reordering. "found" preserves the
  // surface's carried order; "name"/"type" sort a shallow copy so the source list is untouched.
  const filtered = sortKey === "found"
    ? _typeFiltered
    : [..._typeFiltered].sort((a, b) =>
        sortKey === "name"
          ? (a.name || "").localeCompare(b.name || "")
          : (a.type || "").localeCompare(b.type || "") || (a.name || "").localeCompare(b.name || ""));

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
    <div className="screen stack-on-narrow" style={{ height: "100%", display: "grid", gridTemplateColumns: "320px 1fr 320px", gap: 14, padding: 14 }}>

      {/* LEFT — Hero & equipped */}
      <Panel framed style={{ padding: 22, overflow: "auto" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>{hero.alignment}</div>
        <h2 className="h1" style={{ fontSize: 22 }}>{hero.name}</h2>
        <div className="hand" style={{ fontSize: 14, color: "var(--ink-700)" }}>Lv {hero.level} {hero.class}</div>

        {/* Hero switcher — pick whose pack to view (live surface gives each their own). */}
        {party.length > 1 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 10 }}>
            {party.map((p) => (
              <button key={p.id} onClick={() => setActiveHero(p.id)} aria-pressed={activeHero === p.id ? "true" : "false"} className="pill" style={{
                cursor: "pointer",
                background: activeHero === p.id ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(176,141,87,0.08)",
                color: activeHero === p.id ? "var(--w-300)" : "var(--ink-700)",
                boxShadow: activeHero === p.id ? "inset 0 0 0 1px var(--b-600)" : "inset 0 0 0 1px rgba(140,100,60,0.3)",
              }}>{(p.name || "").split(" ")[0]}</button>
            ))}
          </div>
        )}

        {/* Hero paper-doll: portrait flanked by the canonical equipment slots (#271). */}
        <PaperDoll hero={hero} />

        <Divider />

        {/* Weapon Set + Encumbrance were removed: the live /inventory-surface exposes no
            loadout/weapon-set concept and no carry-capacity or STR, and per-item weight is a
            display string ("3 lb"/"—") that isn't reliably summable — any bar here would be a
            fabricated number. The equipment slots above already show worn gear from
            hero.equipped (live). Coin Purse below is the hero's live currency. */}

        <div className="eyebrow">Coin Purse</div>
        {/* I-09: PP/GP/SP are the everyday 5e coinage — always shown. Electrum (EP) and Copper
            (CP) are minted only rarely; the surface emits them as 0 by default, so an empty
            EP/CP slot is dead chrome. Render those two only when the hero actually holds them
            (data-driven, hide-when-absent) — never fabricate a metal the engine isn't tracking.
            RRI-5e98e6f: the displayed purse comes from window.partyPurse(party, activeHero) — the
            ONE shared selector the Market also uses — so the Stash and the Market never disagree
            on the same character's coins (the 35-vs-232 contradiction). */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginTop: 6 }}>
          <CoinSlot tone="#e5e4e2" label="PP" val={String(purse.pp)} />
          <CoinSlot tone="#d4b97a" label="GP" val={String(purse.gp)} />
          <CoinSlot tone="#c0c0c0" label="SP" val={String(purse.sp)} />
        </div>
        {(purse.ep > 0 || purse.cp > 0) && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 6 }}>
            {purse.ep > 0 && <CoinSlot tone="#b08860" label="EP" val={String(purse.ep)} />}
            {purse.cp > 0 && <CoinSlot tone="#8a6a45" label="CP" val={String(purse.cp)} />}
          </div>
        )}
        {/* Unified gp-equivalent total (the one shared converter the Market uses) — only when
            there is mixed coin to roll up; a plain-gold purse reads its total off the GP slot. */}
        {(purse.pp > 0 || purse.ep > 0 || purse.sp > 0 || purse.cp > 0) && (
          <div className="hand muted" style={{ fontSize: 12, marginTop: 6, textAlign: "right" }}>
            ≈ {Number.isInteger(purseTotalGp) ? purseTotalGp : purseTotalGp.toFixed(2)} gp total
          </div>
        )}
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
            <button key={f.id} onClick={() => setFilter(f.id)} aria-pressed={filter === f.id ? "true" : "false"} className={`pill ${filter === f.id ? "" : "muted"}`} style={{
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
                onContextKey={(e) => {
                  // I-05(b) / C6: keyboard equivalent of right-click — Enter or the context-menu
                  // key on a focused item opens the same actions menu, anchored to the tile.
                  if (e.key !== "Enter" && e.key !== "ContextMenu") return;
                  e.preventDefault();
                  const r = e.currentTarget.getBoundingClientRect();
                  setSelectedItem(it);
                  setCtxMenu({ x: Math.round(r.right - 8), y: Math.round(r.bottom - 8), item: it });
                }}
              />
            ))}
            {/* I-07: no fixed 60-slot pack — the grid grows to actual content (OpenWorlds is
                organic, with no max pack size), so empty placeholder slots are not padded in. */}
          </div>
        )}

        <div style={{ marginTop: 12, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span className="muted body-sm">{filtered.length} items · {stash.length} total</span>
          <div style={{ display: "flex", gap: 6 }}>
            {/* I-06: Sort is a real client-side reordering (no engine write needed), so it is
                wired live rather than shipped as a disabled "(preview)" button. The two other
                bottom buttons were removed: per the audit (I-06 / I-11) they had no engine route
                and no defined behavior — dead UI we do not ship. */}
            <BrassButton
              tone="ghost"
              size="sm"
              onClick={() => setSortKey((k) => k === "found" ? "name" : k === "name" ? "type" : "found")}
              title="Reorder the stash on screen (display-only — not saved to the engine)"
            >
              Sort: {sortKey === "found" ? "As Carried" : sortKey === "name" ? "Name" : "Type"}
            </BrassButton>
          </div>
        </div>
      </Panel>

      {/* RIGHT — Item detail. RRI-25e55fa optimizer #5: enrich the selected item from the SAME
          /item-catalog via the SAME window.enrichWare the Market uses (Stash/Market PARITY), so a
          stash item missing a stat the granted item didn't persist backfills to the same depth.
          The item's OWN persisted fields win; an unresolved name returns the item untouched. */}
      <Panel framed style={{ padding: 22, overflow: "auto" }}>
        {selectedItem ? <ItemDetail item={window.enrichWare ? window.enrichWare(selectedItem, catalog) : selectedItem} hero={hero} toast={toast} canAct={canAct} postInvMove={postInvMove} examineSignal={examineNonce} /> : <div className="muted">Select an item.</div>}
      </Panel>

      {ctxMenu && (
        <window.ContextMenu
          x={ctxMenu.x} y={ctxMenu.y}
          onClose={() => setCtxMenu(null)}
          items={[
            { label: "Examine", icon: "◈", hint: "E", title: "Open the read-only Examine panel", onClick: () => { setSelectedItem(ctxMenu.item); setExamineNonce((n) => n + 1); } },
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

// #271: the canonical D&D/BG3 equipment slot set, laid out as a paper-doll (two columns
// flanking the portrait + a weapons row beneath). Each slot has a stable `id` (used for
// item→slot assignment), a short `label`, and a `col` (left/right/arms) placing it in the
// doll. Ring 1 / Ring 2 and Main / Off-Hand are distinct so a hero can wear two of each.
const EQUIP_SLOTS = [
  { id: "head",    label: "Head",    col: "left"  },
  { id: "cloak",   label: "Cloak",   col: "left"  },
  { id: "body",    label: "Body",    col: "left"  },
  { id: "hands",   label: "Hands",   col: "left"  },
  { id: "belt",    label: "Belt",    col: "left"  },
  { id: "amulet",  label: "Amulet",  col: "right" },
  { id: "ring1",   label: "Ring I",  col: "right" },
  { id: "ring2",   label: "Ring II", col: "right" },
  { id: "boots",   label: "Boots",   col: "right" },
  { id: "mainhand", label: "Main Hand", col: "arms" },
  { id: "offhand",  label: "Off-Hand",  col: "arms" },
  { id: "ranged",   label: "Ranged",    col: "arms" },
];

// Name→slot inference. The engine carries no per-item slot (the Item model has no `slot`
// field, so the surface emits slot:"Worn" for everything), so we infer the doll cell from
// the item NAME — exactly as the surface infers item TYPE from the name. Forward-compatible:
// if the surface ever emits a real canonical slot id, assignEquipSlots prefers it.
const _SLOT_NAME_HINTS = [
  ["head",   ["helm", "helmet", "circlet", "crown", "hat", "hood", "cap", "coif", "mask", "diadem"]],
  ["cloak",  ["cloak", "cape", "mantle", "shawl"]],
  ["amulet", ["amulet", "necklace", "pendant", "talisman", "torc", "locket", "periapt", "medallion", "brooch"]],
  ["body",   ["robe", "armor", "armour", "mail", "plate", "cuirass", "breastplate", "leather", "hide", "vest", "tunic", "garb", "raiment", "padded", "scale", "brigandine", "chain shirt", "half plate"]],
  ["hands",  ["gauntlet", "glove", "bracer", "vambrace", "handwrap", "gloves"]],
  ["belt",   ["belt", "girdle", "sash", "cord"]],
  ["boots",  ["boot", "boots", "greave", "sabaton", "shoe", "sandal", "footwrap", "slipper"]],
  ["ring",   ["ring", "band", "signet"]],
  // Off-hand / ranged are detected before generic main-hand weapons.
  ["offhand", ["shield", "buckler", "off-hand", "offhand"]],
  ["ranged",  ["bow", "crossbow", "sling", "dart", "javelin", "arrows", "bolts", "ammunition", "quiver"]],
  ["mainhand", ["sword", "axe", "dagger", "mace", "spear", "rapier", "blade", "hammer", "staff", "club", "flail", "scimitar", "glaive", "halberd", "warhammer", "maul", "trident", "whip", "wand", "scepter", "morningstar", "pike", "lance", "greatsword", "longsword", "shortsword"]],
];

function inferEquipSlotId(name) {
  const low = (name || "").toLowerCase();
  for (const [slot, needles] of _SLOT_NAME_HINTS) {
    if (needles.some((n) => low.includes(n))) return slot;
  }
  return ""; // unrecognized — placed into the first free generic cell by assignEquipSlots
}

// Build a { slotId: equippedItem } map from hero.equipped. Rings and weapons spill from
// their primary cell to the secondary (ring1→ring2, mainhand→offhand) so a hero wearing two
// rings or dual-wielding shows both. Anything unrecognized lands in the first open cell so
// no equipped item is ever silently dropped from the doll.
function assignEquipSlots(equipped) {
  const byId = {};
  const order = EQUIP_SLOTS.map((s) => s.id);
  const place = (item, preferred) => {
    // honor a real canonical slot id from the surface if present
    const fromSurface = order.includes(item.slot) ? item.slot : "";
    const chain = fromSurface
      ? [fromSurface]
      : preferred === "ring" ? ["ring1", "ring2"]
      : preferred === "mainhand" ? ["mainhand", "offhand"]
      : preferred ? [preferred]
      : [];
    for (const id of chain) {
      if (!byId[id]) { byId[id] = item; return true; }
    }
    return false;
  };
  const leftovers = [];
  for (const it of (Array.isArray(equipped) ? equipped : [])) {
    if (!it || !it.name) continue;
    const placed = place(it, inferEquipSlotId(it.name));
    if (!placed) leftovers.push(it);
  }
  // Drop any still-unplaced equipped items into the first open cell (never lose worn gear).
  for (const it of leftovers) {
    const open = order.find((id) => !byId[id]);
    if (open) byId[open] = it;
  }
  return byId;
}

function EquipSlotCell({ slot, item }) {
  return (
    <div style={{ textAlign: "center" }}>
      {item
        ? (
          <window.Tooltip content={<window.InfoTooltip kind="Equipped" title={item.name} body={`Worn in the ${slot.label} slot.`} />} side="top">
            <div>
              <Img scope={"item-" + slug(item.name)} label={`${item.name} · ${slot.label}`} w="100%" h={44} framed />
            </div>
          </window.Tooltip>
        )
        : <Placeholder label={slot.label} w="100%" h={44} framed />}
      <div style={{ fontFamily: "var(--f-mono)", fontSize: 8, marginTop: 2, color: item ? "var(--ink-700)" : "var(--ink-600)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        {item ? item.name : slot.label}
      </div>
    </div>
  );
}

function PaperDoll({ hero }) {
  // Paper-doll equip layout (#271): portrait centered, equipment slots flanking it in two
  // columns (left + right), with the weapons row (Main / Off-Hand / Ranged) beneath. Each
  // cell shows the equipped item's name-slug icon, or an honest empty slot with its label.
  const assigned = assignEquipSlots(hero.equipped);
  const col = (name) => EQUIP_SLOTS.filter((s) => s.col === name);
  return (
    <div style={{ marginTop: 16 }}>
      <div className="eyebrow" style={{ marginBottom: 8 }}>Equipped</div>
      {/* doll: left slots | portrait | right slots */}
      <div style={{ display: "grid", gridTemplateColumns: "52px 280px 52px", gap: 8, alignItems: "start" }}>
        <div style={{ display: "grid", gap: 6 }}>
          {col("left").map((s) => <EquipSlotCell key={s.id} slot={s} item={assigned[s.id]} />)}
        </div>
        <Img scope={heroPortraitScope(hero)} label={`${hero.name} · full art`} h={258} framed style={{ width: "100%" }} />
        <div style={{ display: "grid", gap: 6 }}>
          {col("right").map((s) => <EquipSlotCell key={s.id} slot={s} item={assigned[s.id]} />)}
        </div>
      </div>
      {/* weapons row under the doll */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6, marginTop: 8 }}>
        {col("arms").map((s) => <EquipSlotCell key={s.id} slot={s} item={assigned[s.id]} />)}
      </div>
    </div>
  );
}

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

function ItemSlot({ item, selected, onClick, onContextMenu, onContextKey }) {
  const tone = item.type === "rare" ? "var(--royal)" :
               item.type === "quest" ? "var(--crimson)" :
               item.type === "spell" ? "var(--royal-bright)" :
               item.type === "weapon" ? "var(--ink-700)" :
               "var(--b-500)";
  return (
    <window.Tooltip content={<window.ItemTooltip item={item} />} side="top">
      <button
        onClick={onClick}
        onContextMenu={onContextMenu}
        onKeyDown={onContextKey}
        aria-haspopup="menu"
        aria-label={item.name + (item.qty > 1 ? " ×" + item.qty : "")}
        style={{
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

// #756: pure stat-row builder for the item inspector — the SAME logic the harness
// drives so "an armor inspector shows its AC" / "a versatile weapon shows 1d8 two-handed"
// is proven against the real code, not a string grep. Each row is {k, v}; a field the
// catalog didn't resolve is simply omitted (never a fabricated number). Damage folds the
// Versatile two-handed die inline ("1d6 bludgeoning (1d8 two-handed)") — the optimizer's
// "Examine is missing the Versatile property and the 1d8 two-handed damage" finding.
function itemStatRows(item) {
  if (!item) return [];
  const rows = [];
  rows.push({ k: "Weight", v: item.weight || "—" });
  // RRI-25e55fa optimizer #3 ("Value — blank while Price populated"): prefer the item's OWN
  // value, then fall back to the catalog cost string (costValue) so a priced item never reads a
  // bare "—" beside a populated Price. Only "—" when neither is known (honest, never fabricated).
  rows.push({ k: "Value", v: item.value || item.costValue || "—" });
  if (item.damage) {
    const dmg = [item.damage, item.damageType].filter(Boolean).join(" ");
    const v = item.versatile ? `${dmg} (${item.versatile} two-handed)` : dmg;
    rows.push({ k: "Damage", v });
  }
  // RRI-25e55fa optimizer #3: a ranged/thrown weapon shows its real range bracket
  // ("100/400 ft"); the server composes rangeDisplay from the SRD weapon range, blank for a
  // pure melee weapon — so the row is rendered ONLY when there is a real bracket (never "0/0 ft").
  if (item.rangeDisplay) {
    rows.push({ k: "Range", v: item.rangeDisplay });
  }
  // F09-6 armor dex rule: the server composes acDisplay from REAL fields — medium reads
  // "AC 14 + DEX (max +2)", a shield reads its bonus "+2" (not the misleading flat "AC 2").
  // Fall back to the bare AC only for an older surface that predates acDisplay.
  if (item.acDisplay) {
    rows.push({ k: item.armorCategory === "shield" ? "Shield" : "Armor", v: item.acDisplay });
  } else if (typeof item.ac === "number") {
    rows.push({ k: "Armor Class", v: String(item.ac) });
  }
  if (item.attunement) rows.push({ k: "Attunement", v: "Required" });
  return rows;
}

// #756: compare a candidate item against what the hero currently has equipped in the
// same conceptual slot, so the inspector can answer "is this an upgrade?" — the
// optimizer's "no compare-on-hover anywhere in inventory or market". Returns null when
// there is nothing comparable (no equipped peer, or neither carries a comparable stat).
// Pure + display-only: reads hero.equipped (live read-model), writes nothing.
function itemCompareRows(item, equipped) {
  if (!item || !Array.isArray(equipped) || !equipped.length) return null;
  // The peer is the equipped item inferred into the SAME doll slot as this candidate.
  const slotOf = window.inferEquipSlotId ? window.inferEquipSlotId : () => "";
  const mySlot = slotOf(item.name);
  if (!mySlot) return null;
  const peer = equipped.find((e) => e && e.name && slotOf(e.name) === mySlot && e.name !== item.name);
  if (!peer) return null;
  const rows = [];
  // AC delta (armor): both numeric.
  if (typeof item.ac === "number" && typeof peer.ac === "number") {
    rows.push({ k: "Armor Class", mine: item.ac, theirs: peer.ac, delta: item.ac - peer.ac });
  }
  // Damage (weapons): compared as strings (dice exprs aren't linearly orderable), so just
  // surface both sides — the player reads the upgrade. delta is null (no numeric ordering).
  if (item.damage && peer.damage && item.damage !== peer.damage) {
    rows.push({ k: "Damage", mine: item.damage, theirs: peer.damage, delta: null });
  }
  if (!rows.length) return null;
  return { peer: peer.name, rows };
}

/* RRI-5e98e6f (optimizer minor): itemize a PACK/KIT's contents when the engine already carries
   them in the item's description. The engine grants e.g. an "Explorer's Pack" whose description is
   a manifest ("Bedroll, rations, rope, torches, and the like."). This is a READ-ONLY reformatting
   of the engine-provided desc into a bulleted contents list — it parses ONLY the desc string the
   surface already returns and never fabricates an item the engine isn't tracking. Returns [] for a
   non-pack item or a description that isn't a contents manifest, so the list is shown only when
   there is real content to itemize. */
function packContents(item) {
  if (!item) return [];
  const name = String(item.name || "").toLowerCase();
  if (!/\b(pack|kit|pouch|set|tools?|supplies)\b/.test(name)) return [];
  let desc = String(item.desc || "").trim();
  if (!desc) return [];
  // Drop a trailing catch-all clause ("…, and the like.", "…, etc.") — it is flavor, not an item.
  desc = desc.replace(/[.;]\s*$/, "").replace(/\s*,?\s*(and\s+)?(the like|so on|etc\.?|more)\s*$/i, "");
  // Split on commas and a trailing "and"/"&" conjunction; trim, drop empties + a leading article.
  const parts = desc
    .split(/\s*,\s*|\s+and\s+|\s*&\s*/i)
    .map((s) => s.trim().replace(/^(a|an|the)\s+/i, ""))
    .filter(Boolean);
  // Only itemize when the desc actually reads like a list (≥2 entries) — a one-line prose
  // description is left to the paragraph above, untouched.
  if (parts.length < 2) return [];
  // De-dupe case-insensitively, cap to a sane number, and Title-case the first letter for display.
  const seen = new Set();
  const out = [];
  for (const p of parts) {
    const key = p.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(p.charAt(0).toUpperCase() + p.slice(1));
    if (out.length >= 12) break;
  }
  return out;
}

function ItemDetail({ item, hero, toast, canAct, postInvMove, examineSignal }) {
  // #756: Examine opens a real read-only PANEL (the full description + every resolved
  // stat), not a fleeting toast — the optimizer's "Examine fires a toast ONLY". Local
  // display state; closing returns to the standard inspector. Reset when the item changes.
  const [examineOpen, setExamineOpen] = React.useState(false);
  React.useEffect(() => { setExamineOpen(false); }, [item && item.id]);
  // The right-click "Examine" raises examineSignal (a nonce). Open the panel when it
  // changes to a truthy value (the first bump after a fresh selection).
  React.useEffect(() => { if (examineSignal) setExamineOpen(true); }, [examineSignal]);
  const statRows = itemStatRows(item);
  const compare = itemCompareRows(item, hero && hero.equipped);
  // RRI-5e98e6f: a pack/kit's contents, itemized from the engine desc (read-only).
  const contents = packContents(item);

  if (examineOpen) {
    return (
      <div role="dialog" aria-label={"Examine " + (item.name || "item")}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div className="eyebrow" style={{ color: "var(--royal)" }}>Examining</div>
          <BrassButton tone="ghost" size="sm" onClick={() => setExamineOpen(false)} aria-label="Close examine panel">Close</BrassButton>
        </div>
        <div style={{ display: "flex", gap: 14, alignItems: "flex-start", marginTop: 6 }}>
          <Img scope={itemScope(item)} label={item.name} w={88} h={88} framed />
          <div style={{ minWidth: 0 }}>
            <h2 className="h1" style={{ fontSize: 20, lineHeight: 1.1 }}>{item.name}</h2>
            <div className="hand" style={{ fontSize: 13, color: "var(--ink-700)" }}>{itemCategory(item)}</div>
          </div>
        </div>
        <Divider />
        <p className="body" style={{ marginTop: 0, fontSize: 15 }}>{item.desc}</p>
        {statRows.length > 0 && (
          <>
            <div className="eyebrow">Particulars</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 6 }}>
              {statRows.map((r) => <StatLine key={r.k} k={r.k} v={r.v} />)}
            </div>
          </>
        )}
        {Array.isArray(item.properties) && item.properties.length > 0 && (
          <>
            <div className="eyebrow" style={{ marginTop: 12 }}>Properties</div>
            <div className="tag-row" style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
              {item.properties.map((p) => <Pill key={p}>{p}</Pill>)}
            </div>
          </>
        )}
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <Img scope={itemScope(item)} label={item.name} w={72} h={72} framed />
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow" style={{ color:
            item.type === "rare" ? "var(--royal)" :
            item.type === "quest" ? "var(--crimson)" :
            "var(--ink-600)" }}>
            {/* Prefer the SRD catalog's own classification ("Martial Weapon", "Wondrous") when
                the read-model resolved it; fall back to the coarse grid type. */}
            {itemCategory(item)}
          </div>
          <h2 className="h1" style={{ fontSize: 20, lineHeight: 1.1, marginTop: 2 }}>{item.name}</h2>
          {/* Rarity sits under the name when it is anything beyond the default "common". */}
          {item.rarity && item.rarity.toLowerCase() !== "common" && (
            <div className="hand" style={{ fontSize: 13, color: "var(--royal)", marginTop: 2, textTransform: "capitalize" }}>{item.rarity}</div>
          )}
        </div>
      </div>

      <Divider />

      <p className="body dropcap" style={{ marginTop: 0, fontSize: 15 }}>
        {item.desc}
      </p>

      {/* RRI-5e98e6f: itemize a pack/kit's contents (e.g. Explorer's Pack) when the engine
          description carries them — a read-only reformatting of the desc the surface already
          returns. Hidden for non-packs / prose descriptions (packContents returns []). */}
      {contents.length > 0 && (
        <>
          <Divider />
          <div className="eyebrow">Contents</div>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18, color: "var(--ink-700)" }}>
            {contents.map((c) => (
              <li key={c} className="body-sm" style={{ marginBottom: 2 }}>{c}</li>
            ))}
          </ul>
        </>
      )}

      {/* Stat block — Weight/Value always, plus the REAL combat stats the read-model surfaces:
          damage dice + type for weapons (Versatile two-handed die folded in, #756), and the
          F09-6 armor line for armor/shields (medium "AC 14 + DEX (max +2)", a shield's bonus
          "+2"). The stats come from the item's OWN persisted fields first (F09-7 / #756),
          falling back to the SRD catalog only for a datum it lacks — so a renamed/enchanted
          "Longsword +1" the catalog can't resolve still shows its real numbers, while an item
          with no stat renders no row (never a fabricated number). Built from the pure
          itemStatRows() so the harness drives the exact shipped rows. */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 14 }}>
        {statRows.map((r) => <StatLine key={r.k} k={r.k} v={r.v} />)}
      </div>

      {/* #756: compare-to-equipped — "is this an upgrade?" The optimizer flagged the absence of any
          compare affordance. Shown only when a comparable peer is equipped in the same slot. */}
      {compare && (
        <>
          <Divider />
          <div className="eyebrow">Versus Equipped</div>
          <div className="muted body-sm" style={{ marginTop: 2 }}>Compared to {compare.peer}</div>
          <div style={{ display: "grid", gap: 6, marginTop: 6 }}>
            {compare.rows.map((r) => (
              <div key={r.k} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <span className="muted body-sm">{r.k}</span>
                <span style={{ fontFamily: "var(--f-mono)", fontSize: 12 }}>
                  {String(r.mine)} <span className="muted">vs {String(r.theirs)}</span>
                  {typeof r.delta === "number" && r.delta !== 0 && (
                    <span style={{ marginLeft: 6, color: r.delta > 0 ? "var(--emerald)" : "var(--crimson)", fontFamily: "var(--f-display)" }}>
                      {r.delta > 0 ? "+" + r.delta : String(r.delta)}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </>
      )}

      {Array.isArray(item.properties) && item.properties.length > 0 && (
        <>
          <Divider />
          <div className="eyebrow">Properties</div>
          <div className="tag-row" style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
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
        <BrassButton tone="ghost" size="sm" aria-haspopup="dialog" onClick={() => setExamineOpen(true)}>Examine</BrassButton>
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

// The SRD catalog `kind` slug -> a readable category label for the item header. When the
// read-model resolved a catalog record we prefer its real classification (so a relic reads
// "Wondrous Item", a blade "Weapon"); otherwise we fall back to the coarse grid type label.
const ITEM_KINDS = {
  weapon: "Weapon", armor: "Armor", shield: "Shield", wondrous: "Wondrous Item",
  ring: "Ring", rod: "Rod", staff: "Staff", wand: "Wand", scroll: "Scroll",
  potion: "Potion", gear: "Adventuring Gear", ammunition: "Ammunition",
};

function itemCategory(item) {
  const kind = (item && item.kind ? String(item.kind) : "").toLowerCase();
  if (kind) return ITEM_KINDS[kind] || (kind.charAt(0).toUpperCase() + kind.slice(1));
  return ITEM_TYPES[item && item.type] || (item && item.type) || "Item";
}

function toRoman(n) { return ["", "I", "II", "III", "IV", "V"][n] || n; }

Object.assign(window, { ScreenInventory, CoinSlot, ItemSlot, ItemDetail, packContents, EQUIP_SLOTS, PaperDoll, EquipSlotCell, inferEquipSlotId, assignEquipSlots, ITEM_TYPES, ITEM_KINDS, itemCategory, toRoman, slug, itemScope, itemStatRows, itemCompareRows });
