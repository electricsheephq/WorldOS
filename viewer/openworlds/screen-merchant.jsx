/* Screen: Merchant / Market — buying, selling, haggling */

/* W2d: item-icon scope helper — mirrors screen-inventory's slug()/itemScope(). Ingested
   item art is keyed by a name-slug ("item:<slug>"); the server normalises "item-<slug>" to
   the same key. Build the scope from a slug of the item NAME so wiki icons resolve, with a
   graceful 404 → <Placeholder> fallback inside <Img>. */
function mItemScope(item) {
  if (window.itemArtScope) return window.itemArtScope(item);
  const s = (window.slug ? window.slug(item && item.name) : "");
  return s ? "item-" + s : "";
}

// #756: merge a ware's hardcoded display fields with the read-only /item-catalog stat
// block (AC / damage / versatile two-handed die / properties / weight / value) so the
// Market inspector can show what an item IS — the optimizer's CRITICAL "Studded Leather
// shows no AC value — impossible to evaluate the upgrade". `catalog` is the {name: rec}
// map fetched from /item-catalog; an unresolved name (rec absent or resolved:false) leaves
// the ware exactly as today (weight/price only — never a fabricated stat). Pure + additive.
function enrichWare(item, catalog) {
  if (!item) return item;
  const rec = catalog && catalog[item.name];
  if (!rec || rec.resolved === false) return item;
  const merged = { ...item };
  // The ware's OWN explicit fields win; the catalog only fills gaps + supplies the combat
  // stats the hardcoded stock never carried.
  if (typeof merged.ac !== "number" && typeof rec.ac === "number") merged.ac = rec.ac;
  // F09-6 / #874: carry the catalog's composed armor dex-rule line so the Market inspector
  // reads "AC 14 + DEX (max +2)" for medium armor and a shield's "+2" bonus (not the
  // misleading flat "AC 14"/"AC 2"), consistently with the Stash inspector — itemStatRows()
  // prefers acDisplay over the bare AC. The hardcoded stock never carries these, so fill them.
  if (!merged.acDisplay && rec.acDisplay) merged.acDisplay = rec.acDisplay;
  if (!merged.armorCategory && rec.armorCategory) merged.armorCategory = rec.armorCategory;
  if (!merged.acDexMod && rec.acDexMod) merged.acDexMod = rec.acDexMod;
  if (typeof merged.acDexCap !== "number" && typeof rec.acDexCap === "number") merged.acDexCap = rec.acDexCap;
  if (!merged.damage && rec.damage) { merged.damage = rec.damage; merged.damageType = rec.damageType; }
  if (!merged.versatile && rec.versatile) merged.versatile = rec.versatile;
  // #888: carry the catalog's weapon CATEGORY (Simple/Martial) + 2024 MASTERY property so the
  // Market inspector reads "Martial Weapon · Mastery: Sap" — Stash/Market parity. The hardcoded
  // stock never carries these, so fill them (the ware's own value, if any, still wins).
  if (!merged.weaponCategory && rec.weaponCategory) merged.weaponCategory = rec.weaponCategory;
  if (!merged.mastery && rec.mastery) merged.mastery = rec.mastery;
  // 3582dc2 optimizer (MAJOR "Weapon Mastery 'Sap' unexplained"): carry the catalog's canonical
  // SRD mastery EFFECT text so the Market inspector explains the property too (Stash/Market parity).
  if (!merged.masteryEffect && rec.masteryEffect) merged.masteryEffect = rec.masteryEffect;
  // RRI-25e55fa optimizer #3: carry the catalog's composed weapon range bracket so the Market
  // inspector reads "100/400 ft" for a Heavy Crossbow (the hardcoded stock never carries it).
  if (!merged.rangeDisplay && rec.rangeDisplay) merged.rangeDisplay = rec.rangeDisplay;
  if (typeof merged.range !== "number" && typeof rec.range === "number") merged.range = rec.range;
  if (typeof merged.rangeLong !== "number" && typeof rec.rangeLong === "number") merged.rangeLong = rec.rangeLong;
  // RRI-25e55fa optimizer #3 ("Value — blank while Price populated"): fold the catalog's gp
  // value string so the inspector's Value row matches the populated Price. The ware's own
  // price/value (its explicit fields) still win — this only fills a gap.
  if (!merged.value && rec.value && rec.value !== "—") merged.value = rec.value;
  if (!merged.weight || merged.weight === "—") { if (rec.weight && rec.weight !== "—") merged.weight = rec.weight; }
  if (!merged.kind && rec.kind) merged.kind = rec.kind;
  if (!merged.rarity && rec.rarity) merged.rarity = rec.rarity;
  if ((!Array.isArray(merged.properties) || !merged.properties.length) && Array.isArray(rec.properties) && rec.properties.length) {
    merged.properties = rec.properties;
  }
  if (rec.attunement) merged.attunement = true;
  return merged;
}

function ScreenMerchant({ onNavigate, state, setState }) {
  const [tab, setTab] = React.useState("buy");
  // MK-02/#548: the initial id MUST match a MERCHANTS entry and the first playable BG session
  // should not open on an Act Two Last Light Inn merchant while the party is in the Lower City.
  const [merchantId, setMerchantId] = React.useState("old-troutman");
  const [hoverItem, setHoverItem] = React.useState(null);
  // MK-14 (optimizer #2): the item-detail pane shows the LAST-hovered ware's properties and
  // persists after mouse-leave (hoverItem only drives the row tint, which clears on leave).
  const [detailItem, setDetailItem] = React.useState(null);
  // MK-13: local demo purse — used ONLY in read-only preview (no live surface). When a
  // live /character-surface is present, the displayed `coins` below derives from its live
  // currency so the Market matches the Stash exactly (no hardcoded-232-vs-live contradiction).
  const [localCoins, setLocalCoins] = React.useState({ gp: 232, sp: 68, cp: 14 });
  const [cart, setCart] = React.useState([]);
  const [haggle, setHaggle] = React.useState(0);
  // MK-11: guard the Confirm button against double-submit. The live path fires /move
  // fire-and-forget and only clears the cart in the async .then, so a fast second click
  // would relay a SECOND purchase before the first resolves. Synchronous ref lock.
  const submittingRef = React.useRef(false);
  // MK-06: real type filter for the wares table — no dead "Filter…" button.
  const [typeFilter, setTypeFilter] = React.useState("all");

  // Phase-4 wiring: read can_act + campaign_id from /character-surface so a BUY actually
  // lands as a structured `do` move on the live engine via POST /move (the constrained
  // palette; the DM resolves the purchase via `buy_item`). When can_act is false, the
  // transaction stays local + the button shows "(preview)" — same honest fallback as
  // the rest of the prototype. The hardcoded MERCHANTS stock is still demo data (a real
  // per-location shopkeeper read-model is a separate, bigger change); the ACTION LANE
  // is what this commit wires, so a live session can actually buy.
  const surfaceQuery = window.combatSurfaceFromCampaign
    ? window.combatSurfaceFromCampaign(
        (Array.isArray(state?.campaigns) ? state.campaigns : []).find((c) => c.id === state?.activeCampaign) ||
          (Array.isArray(state?.campaigns) ? state.campaigns : [])[0] || {},
        state,
      )
    : "";
  const [surface, setSurface] = React.useState(null);
  const [surfaceStatus, setSurfaceStatus] = React.useState("loading");
  React.useEffect(() => {
    let cancelled = false;
    setSurfaceStatus("loading");
    fetch("/character-surface" + surfaceQuery, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled) return;
        setSurface(d);
        setSurfaceStatus(d ? "ready" : "preview");
      })
      .catch(() => {
        if (cancelled) return;
        setSurface(null);
        setSurfaceStatus("preview");
      });
    return () => { cancelled = true; };
  }, [surfaceQuery]);
  const canAct = Boolean(surface?.can_act);
  const campaignId = surface?.campaign_id || "";
  const surfaceLoading = surfaceStatus === "loading";
  // MK-13 (RRI-5e98e6f optimizer "Stash 35 GP, Market 232 GP"): the purse shown + spent-against
  // is the LIVE currency when a session is attached (engine = sole writer; matches the Stash). The
  // coin purse rides PER party member of /character-surface (`party[i].currency`, the SAME engine
  // field the inventory shows) — NOT a top-level `surface.currency`, which never existed, so the
  // Market silently fell through to the hardcoded demo {gp:232} while the Stash showed live coin.
  // The Market has no hero switcher, so window.partyPurse lands on party[0] — exactly the Stash's
  // default active hero — via the ONE shared currency helper. Falls back to the local demo purse
  // only in read-only preview (no live party) where the local-spend simulation still applies.
  const liveParty = Array.isArray(surface?.party) ? surface.party : [];
  const coins = liveParty.length
    ? window.partyPurse(liveParty, "")
    : localCoins;
  // The unified gp-equivalent total — the same converter the Stash uses, so a "total" never
  // diverges between the two screens.
  const purseTotalGp = window.currencyTotalGp(coins);
  const toast = window.useToast ? window.useToast() : (() => {});

  // Sell-tab inventory. The Market is a display-only prototype and has NO live shop/stash
  // read-model, so this stays empty — we never fall back to the bundled demo stash (PF1e
  // leak). If a live merchant surface is ever wired, prefer it here; until then [].
  const stash = Array.isArray(state?.merchantStash) ? state.merchantStash : [];
  const merchant = MERCHANTS.find((m) => m.id === merchantId) || MERCHANTS[0];
  const buyTotal = cart.reduce((s, i) => s + (i.mode === "buy" ? i.price : 0), 0);
  const sellTotal = cart.reduce((s, i) => s + (i.mode === "sell" ? i.price : 0), 0);
  const adjustedBuyTotal = Math.round(buyTotal * (1 - haggle / 100));
  const balanceDelta = sellTotal - adjustedBuyTotal;
  const displayedTotal = Math.abs(balanceDelta);
  const merchantWaresName = merchant.waresName || merchant.name;

  const baseInv = tab === "buy" ? merchant.stock : stash.filter((i) => i.type !== "quest");
  // MK-06: the kinds actually present on the table, so the filter only offers real options.
  const presentTypes = Array.from(new Set((baseInv || []).map((i) => i.type).filter(Boolean)));
  const inv = typeFilter === "all" ? baseInv : baseInv.filter((i) => i.type === typeFilter);

  // #756: enrich the wares with their real SRD stats from the read-only /item-catalog
  // endpoint (AC / damage / Versatile two-handed / properties), so the Market inspector can
  // show what an item IS and the player can evaluate an upgrade. Fetched once per merchant
  // stock change; an unresolved name leaves the ware untouched (weight/price only).
  const [catalog, setCatalog] = React.useState({});
  React.useEffect(() => {
    const names = Array.from(new Set((baseInv || []).map((i) => i && i.name).filter(Boolean)));
    if (!names.length) { setCatalog({}); return; }
    let cancelled = false;
    const q = names.map((n) => "name=" + encodeURIComponent(n)).join("&");
    fetch("/item-catalog?" + q, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!cancelled && d && d.items) setCatalog(d.items); })
      .catch(() => { /* keep last good; the ware still shows weight/price */ });
    return () => { cancelled = true; };
  }, [merchantId, tab]);

  // #756: the party's equipped gear (live inventory surface) so the Market inspector can
  // compare a ware to what the player already wears ("is this an upgrade?"). Read-only.
  const [equipped, setEquipped] = React.useState([]);
  React.useEffect(() => {
    let cancelled = false;
    fetch("/inventory-surface" + surfaceQuery, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d || !Array.isArray(d.party)) return;
        const all = [];
        for (const m of d.party) for (const it of (m.equipped || [])) if (it && it.name) all.push(it);
        setEquipped(all);
      })
      .catch(() => { /* no compare peers — the inspector just omits the Versus block */ });
    return () => { cancelled = true; };
  }, [surfaceQuery]);

  const enrichedDetail = enrichWare(detailItem, catalog);
  const detailCompare = window.itemCompareRows ? window.itemCompareRows(enrichedDetail, equipped) : null;

  return (
    <div className="screen" style={{ height: "100%", display: "flex", flexDirection: "column", gap: 8, padding: 14 }}>
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "260px 1fr 280px", gap: 14, minHeight: 0 }}>

      {/* LEFT — Merchant info + haggle */}
      <Panel framed style={{ padding: 22, overflow: "auto" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>Of {merchant.location}</div>
        <h2 className="h1" style={{ fontSize: 22 }}>{merchant.name}</h2>
        <div className="hand" style={{ fontSize: 14, marginTop: 2 }}>{merchant.subtitle}</div>

        <Img scope={merchant.id ? "portrait-" + (merchant.id || merchant.slug || "") : ""} label={`${merchant.short} · portrait`} h={180} framed fit="cover" style={{ width: "100%", marginTop: 14 }} />

        <Divider />

        <p className="body dropcap" style={{ marginTop: 0, fontSize: 14 }}>
          {merchant.greeting}
        </p>

        <Divider />

        {/* MK-07: only show the reputation gauge when the merchant actually carries a rep value.
            The old `merchant.rep || 32` fabricated a "32/100" standing for any merchant without
            one — narrative misdirection. Hide the whole row instead of inventing a number. */}
        {typeof merchant.rep === "number" && (
          <>
            <SectionTitle>Reputation</SectionTitle>
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span className="hand" style={{ fontSize: 13 }}>{merchant.repLabel || "Cautious"}</span>
                <span className="muted body-sm">{merchant.rep}/100</span>
              </div>
              <div style={{ height: 8, marginTop: 6, background: "rgba(0,0,0,0.15)", boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.4)", position: "relative" }}>
                <div style={{ position: "absolute", inset: 0, right: `${100 - merchant.rep}%`, background: "linear-gradient(180deg, var(--b-200), var(--b-500))" }} />
              </div>
            </div>
          </>
        )}

        <SectionTitle>Haggle</SectionTitle>
        <div className="muted body-sm" style={{ marginBottom: 6 }}>
          Coin talks in the Lower City. Lean on the price and watch it move.
        </div>
        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <BrassButton size="sm" tone="ghost" onClick={() => setHaggle(Math.max(0, haggle - 5))}>−5%</BrassButton>
          <div style={{
            flex: 1, textAlign: "center",
            background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
            boxShadow: "inset 0 0 0 1px var(--b-500)",
            padding: "6px 0",
            fontFamily: "var(--f-display)",
            fontSize: 14,
            color: haggle > 0 ? "var(--emerald)" : "var(--ink-900)",
          }}>−{haggle}%</div>
          <BrassButton size="sm" tone="ghost" onClick={() => setHaggle(Math.min(25, haggle + 5))}>+5%</BrassButton>
        </div>
        <div className="hand muted" style={{ fontSize: 12 }}>
          {haggle === 0 ? "Pay the asking price." :
           haggle < 10 ? "A reasonable concession." :
           haggle < 20 ? "He is grumbling. Press anyway." :
           "He is about to refuse you entirely."}
        </div>

        <Divider />

        {/* MK-14 (optimizer #2): item-detail/inspect pane — Market rows had no properties view
            ("Market items have no properties or compare pane"). Shows the last-hovered ware's
            facts; honest empty-state until a row is hovered. */}
        <SectionTitle>Item Detail</SectionTitle>
        {enrichedDetail ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 4 }}>
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <Img scope={mItemScope(enrichedDetail)} label={enrichedDetail.glyph || enrichedDetail.name} w={44} h={44} fit="contain" framed />
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: "var(--f-display)", fontSize: 14, color: enrichedDetail.type === "rare" ? "var(--royal)" : "var(--ink-900)" }}>{enrichedDetail.name}</div>
                <Pill>{(window.ITEM_TYPES && window.ITEM_TYPES[enrichedDetail.type]) || enrichedDetail.type || "—"}</Pill>
              </div>
            </div>
            {/* #756: the REAL stat block — AC for armor, damage (+ Versatile two-handed die) for
                weapons — built from the same pure itemStatRows() the inventory inspector uses, so
                the Market can finally answer "what is this?". A field the catalog can't resolve is
                simply omitted; weight/price always show. */}
            {(window.itemStatRows ? window.itemStatRows(enrichedDetail) : []).map((r) => (
              <div key={r.k} style={{ display: "flex", justifyContent: "space-between" }}>
                <span className="muted body-sm">{r.k}</span>
                <span style={{ fontFamily: "var(--f-mono)", fontSize: 12 }}>{r.v}</span>
              </div>
            ))}
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span className="muted body-sm">Price</span>
              <span style={{ fontFamily: "var(--f-mono)", fontSize: 12 }}>
                {enrichedDetail.price || (typeof enrichedDetail.value === "string" && enrichedDetail.value.match(/(\d+) gp/) ? parseInt(enrichedDetail.value.match(/(\d+) gp/)[1]) : "—")}
                <span className="muted" style={{ fontSize: 9, marginLeft: 2 }}>gp</span>
              </span>
            </div>
            {Array.isArray(enrichedDetail.properties) && enrichedDetail.properties.length > 0 && (
              <div className="tag-row" style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 2 }}>
                {enrichedDetail.properties.map((p) => <Pill key={p}>{p}</Pill>)}
              </div>
            )}
            {detailCompare && (
              <div style={{ marginTop: 4, paddingTop: 6, borderTop: "1px solid rgba(140,100,60,0.25)" }}>
                <div className="eyebrow" style={{ fontSize: 9 }}>Versus {detailCompare.peer}</div>
                {detailCompare.rows.map((r) => (
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
            )}
            {enrichedDetail.desc && <p className="body-sm" style={{ marginTop: 2, color: "var(--ink-700)" }}>{enrichedDetail.desc}</p>}
          </div>
        ) : (
          <div className="hand muted" style={{ fontSize: 12, marginBottom: 4 }}>Hover a ware to inspect its properties.</div>
        )}

        <Divider />

        <BrassButton tone="dark" onClick={() => onNavigate("table")} style={{ width: "100%" }}>Leave Market</BrassButton>
      </Panel>

      {/* CENTER — split inventory */}
      <Panel framed style={{ padding: 22, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <SectionTitle>{tab === "buy" ? "Wares of " + merchantWaresName : "Your Stash"}</SectionTitle>
          <div style={{ display: "flex", gap: 4 }}>
            <button onClick={() => setTab("buy")} className="pill" style={{
              cursor: "pointer",
              background: tab === "buy" ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(176,141,87,0.08)",
              color: tab === "buy" ? "var(--w-300)" : "var(--ink-700)",
              boxShadow: tab === "buy" ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)" : "inset 0 0 0 1px rgba(140,100,60,0.3)",
            }}>Buy</button>
            <button onClick={() => setTab("sell")} className="pill" style={{
              cursor: "pointer",
              background: tab === "sell" ? "linear-gradient(180deg, var(--b-200), var(--b-400))" : "rgba(176,141,87,0.08)",
              color: tab === "sell" ? "var(--w-300)" : "var(--ink-700)",
              boxShadow: tab === "sell" ? "inset 0 0 0 1px var(--b-600), inset 0 1px 0 rgba(255,250,220,0.6)" : "inset 0 0 0 1px rgba(140,100,60,0.3)",
            }}>Sell</button>
          </div>
        </div>

        <div style={{
          flex: 1, overflow: "auto",
          padding: 12,
          background: "rgba(80,50,20,0.06)",
          boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)",
        }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={thStyle}></th>
                <th style={thStyle}>Item</th>
                <th style={thStyle}>Kind</th>
                <th style={{ ...thStyle, textAlign: "right" }}>Weight</th>
                <th style={{ ...thStyle, textAlign: "right" }}>Price</th>
                <th style={thStyle}></th>
              </tr>
            </thead>
            <tbody>
              {inv.map((it, i) => {
                const price = it.price || (typeof it.value === "string" && it.value.match(/(\d+) gp/) ? parseInt(it.value.match(/(\d+) gp/)[1]) : 12);
                const sellPrice = Math.round(price * 0.4);
                const shownPrice = tab === "buy" ? price : sellPrice;
                // MK-09: when haggling on the buy tab, show the discounted price per row with the
                // list price struck through — the player sees the deal in-line, not only on the
                // total. (Display only; the cart still stores the list price and the running total
                // applies the haggle once, so there's no double-discount.)
                const haggledPrice = tab === "buy" && haggle > 0 ? Math.round(shownPrice * (1 - haggle / 100)) : null;
                return (
                  <tr key={it.id || i}
                    onMouseEnter={() => { setHoverItem(it); setDetailItem(it); }}
                    onMouseLeave={() => setHoverItem(null)}
                    style={{
                      cursor: "pointer",
                      background: hoverItem?.id === it.id ? "rgba(176,141,87,0.15)" : "transparent",
                      borderBottom: "1px solid rgba(140,100,60,0.2)",
                      transition: "all 100ms",
                    }}
                  >
                    <td style={{ ...tdStyle, width: 50 }}>
                      <Img scope={mItemScope(it)} label={it.glyph || it.name} w={36} h={36} fit="contain" framed />
                    </td>
                    <td style={tdStyle}>
                      <div style={{
                        fontFamily: "var(--f-display)",
                        fontSize: 13,
                        letterSpacing: "0.04em",
                        color: it.type === "rare" ? "var(--royal)" : "var(--ink-900)",
                      }}>{it.name}</div>
                      {it.qty > 1 && <span className="muted body-sm">×{it.qty}</span>}
                    </td>
                    <td style={tdStyle}>
                      <Pill>{(window.ITEM_TYPES && window.ITEM_TYPES[it.type]) || it.type || "—"}</Pill>
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right", fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--ink-600)" }}>
                      {it.weight || "—"}
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right", whiteSpace: "nowrap" }}>
                      {/* MK-12: the struck list price + discounted price MUST be visually
                          separated (an explicit arrow) — rendered adjacent they read as one
                          run-together number, e.g. "24" + "23" → "2423" (adversarial bug). */}
                      {haggledPrice !== null && haggledPrice !== shownPrice && (
                        <span style={{ fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--ink-600)", textDecoration: "line-through", marginRight: 4 }}>
                          {shownPrice}
                        </span>
                      )}
                      {haggledPrice !== null && haggledPrice !== shownPrice && (
                        <span aria-hidden="true" style={{ fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--emerald)", marginRight: 4 }}>→</span>
                      )}
                      <span style={{ fontFamily: "var(--f-display)", fontSize: 14, color: haggledPrice !== null ? "var(--emerald)" : "var(--ink-900)" }}>
                        {haggledPrice !== null ? haggledPrice : shownPrice}
                      </span>
                      <span style={{ fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-600)", marginLeft: 4 }}>gp</span>
                    </td>
                    <td style={{ ...tdStyle, width: 60 }}>
                      <button onClick={() => setCart([...cart, { ...it, price: shownPrice, mode: tab }])} className="btn ghost sm" style={{ padding: "4px 10px", fontSize: 9 }}>
                        {tab === "buy" ? "Take" : "Sell"}
                      </button>
                    </td>
                  </tr>
                );
              })}
              {inv.length === 0 && (
                <tr>
                  <td colSpan={6} style={{ ...tdStyle, padding: "40px 16px", textAlign: "center" }}>
                    <div className="eyebrow" style={{ color: "var(--ink-600)" }}>
                      {tab === "buy" ? "No wares on offer" : "Nothing to sell"}
                    </div>
                    <div className="hand muted" style={{ fontSize: 13, marginTop: 6 }}>
                      Merchant — prototype. Not yet wired to a live shop read-model.
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div style={{ marginTop: 10, padding: "10px 14px", background: "rgba(176,141,87,0.1)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span className="muted body-sm">{inv.length} on the table · {merchant.disposition || "open until dusk"}</span>
          {/* MK-06: a working kind-filter replaces the old dead "Filter…" button. */}
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            aria-label="Filter wares by kind"
            style={{ ...window.inkInput, fontSize: 11, padding: "4px 8px", width: "auto", cursor: "pointer" }}
          >
            <option value="all">All kinds</option>
            {presentTypes.map((t) => (
              <option key={t} value={t}>{(window.ITEM_TYPES && window.ITEM_TYPES[t]) || t}</option>
            ))}
          </select>
        </div>
      </Panel>

      {/* RIGHT — Cart + balance */}
      <Panel framed style={{ padding: 22, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <SectionTitle>Your Purse</SectionTitle>
        {/* RRI-5e98e6f: render the SAME coin breakdown as the Stash (PP/GP/SP always; EP/CP only
            when held) from the same shared purse, so the two screens never disagree on the coins. */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6 }}>
          <CoinSlot tone="#e5e4e2" label="PP" val={coins.pp} />
          <CoinSlot tone="#d4b97a" label="GP" val={coins.gp} />
          <CoinSlot tone="#c0c0c0" label="SP" val={coins.sp} />
        </div>
        {(coins.ep > 0 || coins.cp > 0) && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 6 }}>
            {coins.ep > 0 && <CoinSlot tone="#b08860" label="EP" val={coins.ep} />}
            {coins.cp > 0 && <CoinSlot tone="#8a6a45" label="CP" val={coins.cp} />}
          </div>
        )}
        {/* Unified gp-equivalent total (the one shared converter) — only meaningful when there is
            mixed coin to roll up; a plain-gold purse already reads its total off the GP slot. */}
        {(coins.pp > 0 || coins.ep > 0 || coins.sp > 0 || coins.cp > 0) && (
          <div className="hand muted" style={{ fontSize: 12, marginTop: 6, textAlign: "right" }}>
            ≈ {Number.isInteger(purseTotalGp) ? purseTotalGp : purseTotalGp.toFixed(2)} gp total
          </div>
        )}

        <Divider />

        <SectionTitle>The Counter</SectionTitle>
        <div style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
          {cart.length === 0 ? (
            <div className="hand muted" style={{ textAlign: "center", padding: "24px 0" }}>
              The counter is empty.<br/>Take something off the shelf.
            </div>
          ) : (
            cart.map((it, i) => (
              <div key={i} style={{
                display: "grid", gridTemplateColumns: "32px 1fr auto auto", gap: 8, alignItems: "center",
                padding: "6px 10px",
                background: "rgba(176,141,87,0.08)",
                boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.25)",
              }}>
                <Img scope={mItemScope(it)} label={it.glyph || it.name} w={32} h={32} fit="contain" framed />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontFamily: "var(--f-display)", fontSize: 11, letterSpacing: "0.06em", color: "var(--ink-900)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {it.name}
                  </div>
                  <div className="muted body-sm" style={{ fontSize: 11 }}>{it.mode === "buy" ? "to buy" : "to sell"}</div>
                </div>
                <span style={{ fontFamily: "var(--f-display)", fontSize: 13, color: "var(--ink-900)" }}>{it.price}<span className="muted" style={{ fontSize: 9 }}>gp</span></span>
                <button onClick={() => setCart(cart.filter((_, j) => j !== i))} className="icon-btn" style={{ width: 22, height: 22 }}>×</button>
              </div>
            ))
          )}
        </div>

        <Divider />

        <div style={{ marginTop: "auto" }}>
          {haggle > 0 && cart.length > 0 && (
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
              <span className="muted body-sm">Listed price</span>
              <span style={{ fontFamily: "var(--f-mono)", fontSize: 12, textDecoration: "line-through", color: "var(--ink-600)" }}>{buyTotal} gp</span>
            </div>
          )}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
            <span className="eyebrow">{balanceDelta > 0 ? "You receive" : "Total"}</span>
            <span style={{ fontFamily: "var(--f-display)", fontSize: 24, color: "var(--ink-900)" }}>
              {displayedTotal} <span className="muted" style={{ fontSize: 12 }}>gp</span>
            </span>
          </div>
          <BrassButton onClick={() => {
            // canAct (live play): relay the transaction as a structured `do` move so the
            // DM resolves the purchase via the engine's buy_item tool (engine = sole
            // writer). While the live read-model is loading, keep the button disabled so
            // a fast click cannot silently mutate only local coins. Otherwise (read-only
            // preview), keep the local-only behavior + honest preview tooltip.
            if (surfaceLoading) return;
            // MK-11: double-submit guard — lock synchronously before the async /move so a
            // fast second click can't relay a second purchase; release in finally / after local apply.
            if (submittingRef.current) return;
            submittingRef.current = true;
            if (canAct) {
              const buyItems = cart.filter((i) => i.mode === "buy").map((i) => i.name);
              const sellItems = cart.filter((i) => i.mode === "sell").map((i) => i.name);
              const phrases = [];
              if (buyItems.length) {
                const list = buyItems.length === 1 ? buyItems[0] : buyItems.slice(0, -1).join(", ") + " and " + buyItems[buyItems.length - 1];
                phrases.push(`buy ${list} from ${merchant.name} for ${adjustedBuyTotal} gp`);
              }
              if (sellItems.length) {
                const list = sellItems.length === 1 ? sellItems[0] : sellItems.slice(0, -1).join(", ") + " and " + sellItems[sellItems.length - 1];
                phrases.push(`sell ${list} to ${merchant.name} for ${sellTotal} gp`);
              }
              if (haggle > 0) phrases.push(`(haggled ${haggle}% off)`);
              const text = "I " + phrases.join(", and ");
              fetch("/move", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ kind: "do", text, campaign: campaignId }),
              }).then((response) => {
                if (!response.ok) throw new Error(`move ${response.status}`);
                toast({ kind: "item", eyebrow: "Market", title: balanceDelta > 0 ? "Sold" : "Bought", body: `Move relayed to the DM — the engine resolves the purchase.` });
                setCart([]);
              }).catch((e) => toast({ kind: "danger", title: "Move not sent", body: e?.message || "viewer unreachable" }))
                .finally(() => { submittingRef.current = false; });
            } else {
              // Read-only preview: the local-spend simulation only applies when the displayed
              // purse IS the local demo purse (no live party). When a live party purse is shown
              // (read-only view of a live session, can_act=false), do NOT fake a coin change —
              // the engine is the sole writer; just clear the counter (honest no-write).
              if (!liveParty.length) {
                setLocalCoins((prev) => ({ ...prev, gp: prev.gp + balanceDelta }));
              }
              setCart([]);
              submittingRef.current = false;
            }
          }} style={{ width: "100%" }} disabled={cart.length === 0 || surfaceLoading || (!canAct && coins.gp + balanceDelta < 0)} title={surfaceLoading ? "Checking the live market action lane…" : (canAct ? "Relays the transaction to the DM via /move — the engine resolves the purchase" : "Display-only — transaction is not saved to the engine")}>
            {surfaceLoading && cart.length > 0 ? "Checking the counter…" : (balanceDelta > 0 ? "Accept silver" : "Strike the bargain")}
          </BrassButton>
        </div>
      </Panel>
      </div>
    </div>
  );
}

const thStyle = {
  textAlign: "left",
  padding: "8px 10px",
  fontFamily: "var(--f-display)",
  fontSize: 9,
  letterSpacing: "0.18em",
  textTransform: "uppercase",
  color: "var(--ink-600)",
  borderBottom: "1px solid rgba(140,100,60,0.35)",
};

const tdStyle = {
  padding: "8px 10px",
  verticalAlign: "middle",
};

const GATE_MARKET_STOCK = [
  { id: "m1", name: "Crossbow bolts", type: "weapon", glyph: "bolts", qty: 30, weight: "3 lb", price: 6, desc: "Standard. Iron-tipped. The fletching is reused." },
  { id: "m2", name: "Travel rations", type: "common", glyph: "rations", qty: 12, weight: "12 lb", price: 24, desc: "Hardtack, salted pork, hard cheese, dried apple." },
  { id: "m3", name: "Iron lantern", type: "common", glyph: "lantern", qty: 1, weight: "2 lb", price: 7, desc: "Wick included. Oil sold separately, by the stall two rows over." },
  { id: "m4", name: "Lantern oil", type: "common", glyph: "oil flask", qty: 4, weight: "1 lb", price: 1, desc: "One pint. Burns six hours, four in the river wind off the Chionthar." },
  { id: "m5", name: "Studded leather", type: "armor", glyph: "leather armor", qty: 1, weight: "20 lb", price: 25, desc: "Sized for a medium frame. Belt may need a hole punched." },
  { id: "m6", name: "Handaxes", type: "weapon", glyph: "axe pair", qty: 6, weight: "4 lb", price: 8, desc: "A set of three, light and balanced for throwing. Forged upriver, edged here at the Gate." },
  { id: "m7", name: "Bandage roll", type: "common", glyph: "bandage", qty: 8, weight: "0.5 lb", price: 1, desc: "Linen. Clean. Mostly clean." },
  { id: "m8", name: "Potion of Healing", type: "spell", glyph: "red potion", qty: 3, weight: "0.5 lb", price: 50, desc: "Restores 2d4+2 HP. Tastes of iron and elderberry." },
  { id: "m9", name: "Antitoxin", type: "spell", glyph: "green vial", qty: 2, weight: "0.5 lb", price: 50, desc: "Advantage on saving throws against poison for 1 hour." },
  { id: "m10", name: "Climbing kit", type: "common", glyph: "rope & pitons", qty: 2, weight: "10 lb", price: 80, desc: "Rope, pitons, hammer. Used. The hammer is new." },
  { id: "m11", name: "Compass", type: "common", glyph: "brass compass", qty: 1, weight: "0.5 lb", price: 25, desc: "Brass. The needle drifts twelve degrees east of true. Dell knows this and has not said so." },
  { id: "m12", name: "Heavy crossbow", type: "weapon", glyph: "heavy crossbow", qty: 1, weight: "8 lb", price: 50, desc: "Reliable. Slow. The kind of weapon you have time to be sorry about firing." },
  { id: "m13", name: "Iron chain (10ft)", type: "common", glyph: "iron chain", qty: 3, weight: "10 lb", price: 30, desc: "Forged upriver. Tested at Wyrm's Crossing, by a man no longer with us." },
  { id: "m14", name: "Spellbook (blank)", type: "spell", glyph: "blank book", qty: 1, weight: "3 lb", price: 15, desc: "Quality paper, oxblood binding. She rarely stocks them — Sorcerous Sundries keeps the good paper." },
  { id: "m15", name: "Salt", type: "rare", glyph: "salt pouch", qty: 4, weight: "1 lb", price: 12, desc: "Coarse. Hauled up the salt-roads south. Useful against more things than you think." },
  { id: "m16", name: "Wax candle (×6)", type: "common", glyph: "candles", qty: 4, weight: "1 lb", price: 4, desc: "Beeswax. Burns long. Useful for vigils and for less wholesome purposes." },
];

const MERCHANTS = [
  {
    id: "old-troutman",
    name: "Old Troutman",
    short: "Old Troutman",
    subtitle: "A shield dwarven trader working the docks east of Philgrave's Mansion.",
    location: "Baldur's Gate — Lower City",
    waresName: "Old Troutman",
    greeting: "Aye, you found the right crate. Bolts, rations, rope, oil, and a few things the Watch forgot to inventory. Keep your purse where I can see it and your questions shorter than the tide.",
    repLabel: "Wary but open",
    rep: 28,
    disposition: "dockside trade · open while the tide holds",
    stock: GATE_MARKET_STOCK,
  },
  {
    id: "talli",
    name: "Quartermaster Talli",
    short: "Q·portrait",
    subtitle: "The Harpers' quartermaster, and the woman the road found.",
    location: "the Last Light Inn",
    waresName: "Talli",
    greeting: "Come in, then. Mind the curse outside — the lantern's covenant ends a step past the threshold. The bolts are sharp, the rations dry, the draughts honest. Coin first, then the catalogue. Harpers don't quibble, but we don't subsidize the careless either.",
    repLabel: "Cautiously fond",
    rep: 42,
    disposition: "open until dusk · shuttered when the Watch patrols",
    stock: GATE_MARKET_STOCK,
  },
];

Object.assign(window, { ScreenMerchant, MERCHANTS, mItemScope, enrichWare });
