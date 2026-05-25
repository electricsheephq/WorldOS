/* Tooltip primitive — ink-on-parchment popover */

function Tooltip({ children, content, side = "right", maxWidth = 280 }) {
  const [open, setOpen] = React.useState(false);
  const [coords, setCoords] = React.useState({ x: 0, y: 0 });
  const ref = React.useRef(null);

  const show = () => {
    if (!ref.current) return;
    const r = ref.current.getBoundingClientRect();
    let x = 0, y = 0;
    if (side === "right") { x = r.right + 10; y = r.top + r.height / 2; }
    else if (side === "left") { x = r.left - 10; y = r.top + r.height / 2; }
    else if (side === "top") { x = r.left + r.width / 2; y = r.top - 10; }
    else { x = r.left + r.width / 2; y = r.bottom + 10; }
    setCoords({ x, y });
    setOpen(true);
  };
  const hide = () => setOpen(false);

  return (
    <React.Fragment>
      <span
        ref={ref}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        style={{ display: "contents" }}
      >
        {children}
      </span>
      {open && ReactDOM.createPortal(
        <div
          style={{
            position: "fixed",
            left: coords.x,
            top: coords.y,
            transform:
              side === "right" ? "translate(0, -50%)" :
              side === "left" ? "translate(-100%, -50%)" :
              side === "top" ? "translate(-50%, -100%)" : "translate(-50%, 0)",
            background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
            padding: "10px 14px",
            maxWidth,
            zIndex: 1000,
            pointerEvents: "none",
            boxShadow:
              "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400), 0 8px 22px rgba(0,0,0,0.4)",
            animation: "tooltip-in 140ms ease both",
          }}
        >
          {content}
        </div>,
        document.body
      )}
    </React.Fragment>
  );
}

/* Item tooltip body */
function ItemTooltip({ item }) {
  return (
    <div>
      <div className="eyebrow" style={{
        color: item.type === "rare" ? "var(--royal)" : item.type === "quest" ? "var(--crimson)" : "var(--ink-600)",
        marginBottom: 4,
      }}>
        {(window.ITEM_TYPES && window.ITEM_TYPES[item.type]) || item.type || "Item"}
      </div>
      <div style={{ fontFamily: "var(--f-display)", fontSize: 14, letterSpacing: "0.06em", color: "var(--ink-900)", lineHeight: 1.2 }}>
        {item.name}
      </div>
      <div style={{ fontFamily: "var(--f-body)", fontSize: 13, color: "var(--ink-700)", marginTop: 6, lineHeight: 1.4 }}>
        {item.desc}
      </div>
      {(item.weight || item.value) && (
        <div style={{ display: "flex", gap: 12, marginTop: 8, fontFamily: "var(--f-mono)", fontSize: 10, color: "var(--ink-600)" }}>
          {item.weight && <span>{item.weight}</span>}
          {item.value && <span>{item.value}</span>}
        </div>
      )}
    </div>
  );
}

/* Generic info tooltip body */
function InfoTooltip({ title, body, kind }) {
  return (
    <div>
      {kind && <div className="eyebrow" style={{ color: "var(--crimson)", marginBottom: 2 }}>{kind}</div>}
      {title && <div style={{ fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.08em", color: "var(--ink-900)" }}>{title}</div>}
      <div style={{ fontFamily: "var(--f-body)", fontSize: 13, color: "var(--ink-700)", marginTop: 6, lineHeight: 1.4 }}>
        {body}
      </div>
    </div>
  );
}

Object.assign(window, { Tooltip, ItemTooltip, InfoTooltip });
