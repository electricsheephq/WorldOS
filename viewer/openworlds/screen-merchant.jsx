/* Screen: Merchant / Market — buying, selling, haggling */

function ScreenMerchant({ onNavigate, state, setState }) {
  const [tab, setTab] = React.useState("buy");
  const [merchantId, setMerchantId] = React.useState("oleg");
  const [hoverItem, setHoverItem] = React.useState(null);
  const [coins, setCoins] = React.useState({ gp: 232, sp: 68, cp: 14 });
  const [cart, setCart] = React.useState([]);
  const [haggle, setHaggle] = React.useState(0);

  const stash = Array.isArray(state?.stash) ? state.stash : [];
  const merchant = MERCHANTS.find((m) => m.id === merchantId) || MERCHANTS[0];
  const buyTotal = cart.reduce((s, i) => s + (i.mode === "buy" ? i.price : 0), 0);
  const sellTotal = cart.reduce((s, i) => s + (i.mode === "sell" ? i.price : 0), 0);
  const adjustedBuyTotal = Math.round(buyTotal * (1 - haggle / 100));
  const balanceDelta = sellTotal - adjustedBuyTotal;
  const displayedTotal = Math.abs(balanceDelta);

  const inv = tab === "buy" ? merchant.stock : stash.filter((i) => i.type !== "quest");

  const _badge = { label: "Preview", tone: "muted", detail: "The Market is display-only — stock is demo data and transactions are not persisted to the engine." };

  return (
    <div className="screen" style={{ height: "100%", display: "flex", flexDirection: "column", gap: 8, padding: 14 }}>

      {/* Prototype banner */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 14px", background: "rgba(80,50,20,0.18)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.45)", borderRadius: 2 }}>
        <CapabilityBadge capability={_badge} nativeStatus={null} />
        <span className="hand muted" style={{ fontSize: 12 }}>Display-only — merchant stock is demo data; purchases are not wired to the engine inventory.</span>
      </div>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "260px 1fr 280px", gap: 14, minHeight: 0 }}>

      {/* LEFT — Merchant info + haggle */}
      <Panel framed style={{ padding: 22, overflow: "auto" }}>
        <div className="eyebrow" style={{ color: "var(--crimson)" }}>Of {merchant.location}</div>
        <h2 className="h1" style={{ fontSize: 22 }}>{merchant.name}</h2>
        <div className="hand" style={{ fontSize: 14, marginTop: 2 }}>{merchant.subtitle}</div>

        <Placeholder label={`${merchant.short} · portrait`} h={180} framed style={{ width: "100%", marginTop: 14 }} />

        <Divider />

        <p className="body dropcap" style={{ marginTop: 0, fontSize: 14 }}>
          {merchant.greeting}
        </p>

        <Divider />

        <SectionTitle>Reputation</SectionTitle>
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between" }}>
            <span className="hand" style={{ fontSize: 13 }}>{merchant.repLabel || "Cautious"}</span>
            <span className="muted body-sm">{merchant.rep || 32}/100</span>
          </div>
          <div style={{ height: 8, marginTop: 6, background: "rgba(0,0,0,0.15)", boxShadow: "inset 0 0 0 1px rgba(80,50,20,0.4)", position: "relative" }}>
            <div style={{ position: "absolute", inset: 0, right: `${100 - (merchant.rep || 32)}%`, background: "linear-gradient(180deg, var(--b-200), var(--b-500))" }} />
          </div>
        </div>

        <SectionTitle>Haggle</SectionTitle>
        <div className="muted body-sm" style={{ marginBottom: 6 }}>
          Mira insists. Cassian disapproves. The price moves either way.
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
                      <Placeholder label={it.glyph} w={36} h={36} framed />
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
                      <span style={{ fontFamily: "var(--f-display)", fontSize: 14, color: "var(--ink-900)" }}>
                        {shownPrice}
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
            </tbody>
          </table>
        </div>

        <div style={{ marginTop: 10, padding: "10px 14px", background: "rgba(176,141,87,0.1)", boxShadow: "inset 0 0 0 1px rgba(140,100,60,0.3)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span className="muted body-sm">{inv.length} on the table · {merchant.disposition || "open until dusk"}</span>
          <BrassButton size="sm" tone="ghost">Filter…</BrassButton>
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
                <Placeholder label={it.glyph} w={32} h={32} framed />
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
            setCoins((prev) => ({ ...prev, gp: prev.gp + balanceDelta }));
            setCart([]);
          }} style={{ width: "100%" }} disabled={cart.length === 0 || coins.gp + balanceDelta < 0} title="Display-only — transaction is not saved to the engine">
            {balanceDelta > 0 ? "Accept silver" : "Strike the bargain"} <span style={{ fontSize: 9, opacity: 0.7 }}>(preview)</span>
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
    id: "oleg",
    name: "Oleg Leveton",
    short: "O·portrait",
    subtitle: "Master of his post, by his post.",
    location: "Oleg's Trading Post",
    greeting: "If you mean to look, look. I will not be pressed for prices on what I'm not selling at the back of the bargain. The crossbow bolts are by the door. The salt is not for sale today.",
    repLabel: "Cautiously fond",
    rep: 42,
    disposition: "open until dusk · closes early on Sundays",
    stock: [
      { id: "m1", name: "Crossbow bolts", type: "weapon", glyph: "bolts", qty: 30, weight: "3 lb", price: 6, desc: "Standard. Iron-tipped. The fletching is reused." },
      { id: "m2", name: "Travel rations", type: "common", glyph: "rations", qty: 12, weight: "12 lb", price: 24, desc: "Hardtack, salted pork, hard cheese, dried apple." },
      { id: "m3", name: "Iron lantern", type: "common", glyph: "lantern", qty: 1, weight: "2 lb", price: 7, desc: "Wick included. Oil sold separately, by the merchant's wife." },
      { id: "m4", name: "Lantern oil", type: "common", glyph: "oil flask", qty: 4, weight: "1 lb", price: 1, desc: "One pint. Burns six hours, four in wind." },
      { id: "m5", name: "Studded leather", type: "armor", glyph: "leather armor", qty: 1, weight: "20 lb", price: 25, desc: "Sized for a medium frame. Belt may need a hole punched." },
      { id: "m6", name: "Handaxes", type: "weapon", glyph: "axe pair", qty: 6, weight: "4 lb", price: 8, desc: "A set of three, light and balanced for throwing. Forged in the capital, edged here." },
      { id: "m7", name: "Bandage roll", type: "common", glyph: "bandage", qty: 8, weight: "0.5 lb", price: 1, desc: "Linen. Clean. Mostly clean." },
      { id: "m8", name: "Potion of Healing", type: "spell", glyph: "red potion", qty: 3, weight: "0.5 lb", price: 50, desc: "Restores 2d4+2 HP. Tastes of iron and elderberry." },
      { id: "m9", name: "Antitoxin", type: "spell", glyph: "green vial", qty: 2, weight: "0.5 lb", price: 50, desc: "Advantage on saving throws against poison for 1 hour." },
      { id: "m10", name: "Climbing kit", type: "common", glyph: "rope & pitons", qty: 2, weight: "10 lb", price: 80, desc: "Rope, pitons, hammer. Used. The hammer is new." },
      { id: "m11", name: "Compass", type: "common", glyph: "brass compass", qty: 1, weight: "0.5 lb", price: 25, desc: "Brass. The needle drifts twelve degrees east of true. Oleg knows this and has not said so." },
      { id: "m12", name: "Heavy crossbow", type: "weapon", glyph: "heavy crossbow", qty: 1, weight: "8 lb", price: 50, desc: "Reliable. Slow. The kind of weapon you have time to be sorry about firing." },
      { id: "m13", name: "Iron chain (10ft)", type: "common", glyph: "iron chain", qty: 3, weight: "10 lb", price: 30, desc: "Forged in the capital. Tested at Tines, by Oleg's brother, who is no longer with us." },
      { id: "m14", name: "Spellbook (blank)", type: "spell", glyph: "blank book", qty: 1, weight: "3 lb", price: 15, desc: "Quality paper, oxblood binding. Oleg does not stock these often; he stocks them for Cassian." },
      { id: "m15", name: "Salt", type: "rare", glyph: "salt pouch", qty: 4, weight: "1 lb", price: 12, desc: "Coarse. Sourced from the Old Hills. Useful against more things than you think." },
      { id: "m16", name: "Wax candle (×6)", type: "common", glyph: "candles", qty: 4, weight: "1 lb", price: 4, desc: "Beeswax. Burns long. Useful for vigils and for less wholesome purposes." },
    ],
  },
];

Object.assign(window, { ScreenMerchant, MERCHANTS });
