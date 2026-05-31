/* Toast notifications + right-click context menu */

const ToastCtx = React.createContext(null);

function ToastProvider({ children }) {
  const [toasts, setToasts] = React.useState([]);

  const push = React.useCallback((toast) => {
    const id = Math.random().toString(36).slice(2);
    setToasts((ts) => [...ts, { ...toast, id }]);
    setTimeout(() => {
      setToasts((ts) => ts.filter((t) => t.id !== id));
    }, toast.duration || 4200);
  }, []);

  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div style={{
        position: "fixed", top: 56, right: 24, zIndex: 500,
        display: "flex", flexDirection: "column", gap: 10,
        pointerEvents: "none",
        maxWidth: 360,
      }} aria-live="polite" aria-label="Notifications" data-worldos-testid="toast-region">
        {toasts.map((t) => <Toast key={t.id} toast={t} />)}
      </div>
    </ToastCtx.Provider>
  );
}

function Toast({ toast }) {
  const tone =
    toast.kind === "quest" ? { ribbon: "var(--crimson)", icon: "✦" } :
    toast.kind === "item" ? { ribbon: "var(--royal)", icon: "◈" } :
    toast.kind === "level" ? { ribbon: "var(--gold-glow)", icon: "♕" } :
    toast.kind === "danger" ? { ribbon: "var(--crimson)", icon: "▲" } :
    toast.kind === "rest" ? { ribbon: "var(--emerald)", icon: "✺" } :
    { ribbon: "var(--b-400)", icon: "·" };

  return (
    <div
      role={toast.kind === "danger" ? "alert" : "status"}
      data-worldos-testid={toast.kind === "danger" ? "error-banner" : "toast"}
      style={{
      display: "grid",
      gridTemplateColumns: "auto 1fr",
      gap: 0,
      pointerEvents: "auto",
      background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
      boxShadow: "inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400), 0 12px 24px rgba(0,0,0,0.45)",
      animation: "toast-in 280ms ease both",
    }}>
      <div style={{
        width: 8,
        background: `linear-gradient(180deg, ${tone.ribbon}, color-mix(in oklab, ${tone.ribbon}, black 30%))`,
        boxShadow: `inset 1px 0 0 rgba(255,255,255,0.2), inset -1px 0 0 rgba(0,0,0,0.3)`,
      }} />
      <div style={{ padding: "12px 18px 12px 14px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ color: tone.ribbon, fontSize: 14, lineHeight: 1 }}>{tone.icon}</span>
          <span style={{ fontFamily: "var(--f-display)", fontSize: 10, letterSpacing: "0.24em", textTransform: "uppercase", color: "var(--ink-700)" }}>
            {toast.eyebrow || (toast.kind === "quest" ? "Chronicle" : toast.kind === "item" ? "Acquired" : toast.kind === "level" ? "Level" : "Note")}
          </span>
        </div>
        <div style={{ marginTop: 4, fontFamily: "var(--f-display)", fontSize: 13, letterSpacing: "0.06em", color: "var(--ink-900)", lineHeight: 1.25 }}>
          {toast.title}
        </div>
        {toast.body && (
          <div className="hand" style={{ fontSize: 13, color: "var(--ink-700)", marginTop: 4 }}>{toast.body}</div>
        )}
      </div>
    </div>
  );
}

function useToast() {
  return React.useContext(ToastCtx);
}

/* ===== Context menu ===== */

function ContextMenu({ x, y, items, onClose }) {
  React.useEffect(() => {
    const close = () => onClose();
    const esc = (e) => e.key === "Escape" && onClose();
    const timer = window.setTimeout(() => {
      window.addEventListener("click", close);
      window.addEventListener("contextmenu", close);
      window.addEventListener("keydown", esc);
    }, 0);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("click", close);
      window.removeEventListener("contextmenu", close);
      window.removeEventListener("keydown", esc);
    };
  }, [onClose]);

  return ReactDOM.createPortal(
    <div
      onClick={(e) => e.stopPropagation()}
      style={{
        position: "fixed",
        left: x, top: y,
        zIndex: 600,
        minWidth: 200,
        background: "linear-gradient(180deg, var(--p-100), var(--p-300))",
        boxShadow: "inset 0 0 0 1px var(--b-600), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400), 0 12px 28px rgba(0,0,0,0.5)",
        padding: 4,
        animation: "tooltip-in 120ms ease both",
      }}
    >
      {items.map((it, i) => {
        if (it.divider) return (
          <div key={i} style={{
            height: 1,
            background: "linear-gradient(90deg, transparent, var(--b-500) 50%, transparent)",
            margin: "4px 8px",
          }} />
        );
        return (
          <button
            key={i}
            onClick={() => { it.onClick && it.onClick(); onClose(); }}
            disabled={it.disabled}
            style={{
              display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16,
              width: "100%", textAlign: "left",
              padding: "8px 14px",
              fontFamily: "var(--f-display)",
              fontSize: 11,
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              color: it.tone === "crimson" ? "var(--crimson)" : it.disabled ? "var(--ink-500)" : "var(--ink-800)",
              cursor: it.disabled ? "not-allowed" : "pointer",
              background: "transparent",
              opacity: it.disabled ? 0.5 : 1,
              transition: "all 100ms",
            }}
            onMouseEnter={(e) => { if (!it.disabled) e.currentTarget.style.background = "rgba(176,141,87,0.18)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
          >
            <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {it.icon && <span style={{ color: it.tone === "crimson" ? "var(--crimson)" : "var(--b-500)", fontSize: 14, width: 14, textAlign: "center" }}>{it.icon}</span>}
              {it.label}
            </span>
            {it.hint && <span style={{ fontFamily: "var(--f-mono)", fontSize: 9, color: "var(--ink-600)", letterSpacing: "0.04em", textTransform: "none" }}>{it.hint}</span>}
          </button>
        );
      })}
    </div>,
    document.body
  );
}

Object.assign(window, { ToastCtx, ToastProvider, useToast, ContextMenu });
