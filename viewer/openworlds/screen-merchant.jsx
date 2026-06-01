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

function ScreenMerchant({ onNavigate, state, setState }) {
  const [tab, setTab] = React.useState("buy");
  // MK-02: the initial id MUST match a MERCHANTS entry. It previously read "gate-sundries"
  // while the only merchant is id:"talli", so the find() silently fell back to MERCHANTS[0] —
  // masking the mismatch and breaking any id-keyed lookup (e.g. the portrait scope).
  const [merchantId, setMerchantId] = React.useState("talli");
  const [hoverItem, setHoverItem] = React.useState(null);
  const [coins, setCoins] = React.useState({ gp: 232, sp: 68, cp: 14 });
  const [cart, setCart] = React.useState([]);
  const [haggle, setHaggle] = React.useState(0);
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
  React.useEffect(() => {
    let cancelled = false;
    fetch("/character-surface" + surfaceQuery, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!cancelled) setSurface(d); })
      .catch(() => { if (!cancelled) setSurface(null); });
    return () => { cancelled = true; };
  }, [surfaceQuery]);
  const canAct = Boolean(surface?.can_act);
  const campaignId = surface?.campaign_id || "";
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

  const baseInv = tab === "buy" ? merchant.stock : stash.filter((i) => i.type !== "quest");
  // MK-06: the kinds actually present on the table, so the filter only offers real options.
  const presentTypes = Array.from(new Set((baseInv || []).map((i) => i.type).filter(Boolean)));
  const inv = typeFilter === "all" ? baseInv : baseInv.filter((i) => i.type === typeFilter);

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

        <BrassButton tone="dark" onClick={() => onNavigate("table")} style={{ width: "100%" }}>Leave Market</BrassButton>
      </Panel>

      {/* CENTER — split inventory */}
      <Panel framed style={{ padding: 22, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <SectionTitle>{tab === "buy" ? "Wares of " + merchant.name.split(" ")[0] : "Your Stash"}</SectionTitle>
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
                    onMouseEnter={() => setHoverItem(it)}
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
                    <td style={{ ...tdStyle, textAlign: "right" }}>
                      {haggledPrice !== null && haggledPrice !== shownPrice && (
                        <span style={{ fontFamily: "var(--f-mono)", fontSize: 11, color: "var(--ink-600)", textDecoration: "line-through", marginRight: 5 }}>
                          {shownPrice}
                        </span>
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
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6 }}>
          <CoinSlot tone="#d4b97a" label="GP" val={coins.gp} />
          <CoinSlot tone="#c0c0c0" label="SP" val={coins.sp} />
          <CoinSlot tone="#b08860" label="CP" val={coins.cp} />
        </div>

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
            // writer). Otherwise (read-only preview), keep the local-only behavior +
            // honest "(preview)" label.
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
              }).then(() => {
                toast({ kind: "item", eyebrow: "Market", title: balanceDelta > 0 ? "Sold" : "Bought", body: `Move relayed to the DM — the engine resolves the purchase.` });
                setCart([]);
              }).catch((e) => toast({ kind: "danger", title: "Move not sent", body: e?.message || "viewer unreachable" }));
            } else {
              setCoins((prev) => ({ ...prev, gp: prev.gp + balanceDelta }));
              setCart([]);
            }
          }} style={{ width: "100%" }} disabled={cart.length === 0 || (!canAct && coins.gp + balanceDelta < 0)} title={canAct ? "Relays the transaction to the DM via /move — the engine resolves the purchase" : "Display-only — transaction is not saved to the engine"}>
            {balanceDelta > 0 ? "Accept silver" : "Strike the bargain"}
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

const MERCHANTS = [
  {
    id: "talli",
    name: "Quartermaster Talli",
    short: "Q·portrait",
    subtitle: "The Harpers' quartermaster, and the woman the road found.",
    location: "the Last Light Inn",
    greeting: "Come in, then. Mind the curse outside — the lantern's covenant ends a step past the threshold. The bolts are sharp, the rations dry, the draughts honest. Coin first, then the catalogue. Harpers don't quibble, but we don't subsidize the careless either.",
    repLabel: "Cautiously fond",
    rep: 42,
    disposition: "open until dusk · shuttered when the Watch patrols",
    stock: [
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
    ],
  },
];

Object.assign(window, { ScreenMerchant, MERCHANTS, mItemScope });
