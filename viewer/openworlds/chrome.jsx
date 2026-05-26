/* Shared chrome: window frame, nav rail, title bar */

const NAV_GROUPS = [
  {
    id: "g_table", label: "Table", glyph: "dice",
    tabs: [
      { id: "table", label: "Session" },
      { id: "combat", label: "Battle" },
      { id: "dialogue", label: "Parley" },
    ],
  },
  {
    id: "g_map", label: "Map", glyph: "map",
    tabs: [{ id: "map", label: "Atlas" }],
  },
  {
    id: "g_party", label: "Party", glyph: "shield",
    tabs: [
      { id: "character", label: "Heroes" },
      { id: "inventory", label: "Stash" },
      { id: "forge", label: "Forge" },
      { id: "relations", label: "Relations" },
    ],
  },
  {
    id: "g_journal", label: "Journal", glyph: "book",
    tabs: [
      { id: "journal", label: "Quests" },
      { id: "bestiary", label: "Codex" },
      { id: "acts", label: "Acts" },
    ],
  },
  {
    id: "g_market", label: "Market", glyph: "coins",
    tabs: [{ id: "merchant", label: "The Market" }],
  },
];

const NAV_BOTTOM = {
  id: "g_worlds", label: "Worlds", glyph: "compass",
  tabs: [
    { id: "launcher", label: "Chronicles" },
    { id: "create", label: "Creation Plane" },
    { id: "seed", label: "World Seed" },
    { id: "settings", label: "Settings" },
  ],
};

// Build flat lookup of screenId → group
const ALL_NAV = [...NAV_GROUPS, NAV_BOTTOM];
function getGroupForScreen(screenId) {
  return ALL_NAV.find((g) => g.tabs.some((t) => t.id === screenId));
}
function getDefaultScreen(groupId) {
  const g = ALL_NAV.find((x) => x.id === groupId);
  return g ? g.tabs[0].id : "launcher";
}

function Glyph({ kind, size = 22 }) {
  const stroke = { stroke: "currentColor", strokeWidth: 1.4, fill: "none", strokeLinecap: "round", strokeLinejoin: "round" };
  switch (kind) {
    case "compass":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="9" {...stroke} />
          <path d="M12 3v3M12 18v3M3 12h3M18 12h3" {...stroke} />
          <path d="M12 7l2.5 4.5L12 17l-2.5-5.5z" {...stroke} fill="currentColor" fillOpacity="0.25" />
        </svg>
      );
    case "dice":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <path d="M12 3l8 4.5v9L12 21 4 16.5v-9z" {...stroke} />
          <path d="M12 3v9M4 7.5l8 4.5M20 7.5l-8 4.5" {...stroke} opacity="0.6" />
          <circle cx="8.5" cy="13.5" r="0.8" fill="currentColor" />
          <circle cx="15.5" cy="13.5" r="0.8" fill="currentColor" />
          <circle cx="12" cy="17" r="0.8" fill="currentColor" />
        </svg>
      );
    case "shield":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <path d="M12 3l8 2v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V5l8-2z" {...stroke} />
          <path d="M12 8v8M8 12h8" {...stroke} opacity="0.7" />
        </svg>
      );
    case "pouch":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <path d="M6 9c0-2 2.5-3 6-3s6 1 6 3l1.5 9.5c.2 1.3-.8 2.5-2 2.5H6.5c-1.2 0-2.2-1.2-2-2.5L6 9z" {...stroke} />
          <path d="M8 6c0-1.5 1.8-2.5 4-2.5S16 4.5 16 6" {...stroke} />
          <circle cx="12" cy="14" r="2" {...stroke} />
        </svg>
      );
    case "map":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z" {...stroke} />
          <path d="M9 4v16M15 6v16" {...stroke} opacity="0.6" />
        </svg>
      );
    case "book":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <path d="M4 5c0-.6.4-1 1-1h6v15H5c-.6 0-1-.4-1-1V5zM20 5c0-.6-.4-1-1-1h-6v15h6c.6 0 1-.4 1-1V5z" {...stroke} />
          <path d="M12 4v15" {...stroke} />
        </svg>
      );
    case "scroll":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <path d="M5 5h11a3 3 0 010 6H8M8 11v6a3 3 0 01-3 3h11a3 3 0 003-3V11" {...stroke} />
          <path d="M5 5a2 2 0 100 4M19 17a2 2 0 100 4" {...stroke} />
        </svg>
      );
    case "quill":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <path d="M3 21l4-4M20 4c-3 1-8 3-10 7-1 2-1 4-3 6l2 2c2-2 4-2 6-3 4-2 6-7 7-10l-2-2z" {...stroke} />
        </svg>
      );
    case "sword":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <path d="M14 4l6 0 0 6-9 9-3-3 6-6z" {...stroke} />
          <path d="M3 21l5-5M9 18l-3 3M6 15l3 3" {...stroke} />
          <circle cx="17" cy="7" r="1" fill="currentColor" />
        </svg>
      );
    case "anvil":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <path d="M3 9h12c2 0 4 1 4 3v1H10v-2H5l-2-2z" {...stroke} />
          <path d="M8 13v3M14 13v3M5 16h12" {...stroke} />
          <path d="M5 16l-1 4h14l-1-4" {...stroke} />
        </svg>
      );
    case "feather":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <path d="M4 20l4-4M20 4c-2 0-6 1-9 4s-4 7-4 9c2 0 6-1 9-4s4-7 4-9z" {...stroke} />
          <path d="M14 6l-7 7M16 8l-5 5M18 10l-3 3" {...stroke} opacity="0.6" />
        </svg>
      );
    case "coins":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <ellipse cx="9" cy="9" rx="5" ry="3" {...stroke} />
          <path d="M4 9v3c0 1.7 2.2 3 5 3s5-1.3 5-3V9" {...stroke} />
          <ellipse cx="15" cy="15" rx="5" ry="3" {...stroke} />
          <path d="M10 15v3c0 1.7 2.2 3 5 3s5-1.3 5-3v-3" {...stroke} />
        </svg>
      );
    case "settings":
      return (
        <svg width={size} height={size} viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="3" {...stroke} />
          <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2" {...stroke} />
        </svg>
      );
    default:
      return null;
  }
}

function CornerOrnament({ corner }) {
  return (
    <svg className={`corner ${corner}`} viewBox="0 0 36 36" aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round">
        <path d="M2 12 C 2 6, 6 2, 12 2" />
        <path d="M2 18 C 2 9, 9 2, 18 2" opacity="0.6" />
        <path d="M8 8 C 12 8, 14 6, 14 4" opacity="0.7" />
        <circle cx="8" cy="8" r="1.4" fill="currentColor" />
        <path d="M14 4 C 16 4, 18 6, 18 8" opacity="0.7" />
        <path d="M4 14 C 4 10, 6 8, 10 8" opacity="0.7" />
      </g>
    </svg>
  );
}

function Divider({ children }) {
  return (
    <div className="divider" aria-hidden="true">
      <div className="diamond"></div>
      {children}
    </div>
  );
}

function SectionTitle({ ordinal, children, right }) {
  return (
    <div className="section-title">
      {ordinal && <span className="ord">{ordinal}</span>}
      <span>{children}</span>
      {right && <span style={{ marginLeft: "auto" }}>{right}</span>}
    </div>
  );
}

function Pill({ children, tone, dot }) {
  return <span className={`pill ${tone || ""} ${dot ? "dot" : ""}`}>{children}</span>;
}

function Placeholder({ label, w, h, framed, style, children, className }) {
  return (
    <div
      className={`placeholder ${framed ? "framed" : ""} ${className || ""}`}
      style={{ width: w, height: h, ...(style || {}) }}
    >
      {children || <span className="ph-label">{label}</span>}
    </div>
  );
}

function IconPlate({ size = 56, label, framed = true, glyph, tone, children, onClick, active, style }) {
  return (
    <button
      type="button"
      className={`icon-plate ${framed ? "framed" : ""} ${active ? "active" : ""}`}
      onClick={onClick}
      style={{
        width: size,
        height: size,
        ...(active ? { boxShadow: `inset 0 0 0 1px var(--b-500), inset 0 0 0 3px var(--p-100), inset 0 0 0 4px var(--b-400), 0 0 20px -2px var(--gold-glow)` } : {}),
        ...(style || {}),
      }}
      title={label}
    >
      {children || (
        <span className="ph-label" style={{ fontSize: 9 }}>{label}</span>
      )}
    </button>
  );
}

function BrassButton({ children, onClick, tone, size, disabled, style, type = "button" }) {
  const cls = ["btn", tone, size, disabled ? "disabled" : ""].filter(Boolean).join(" ");
  return (
    <button className={cls} onClick={onClick} disabled={disabled} type={type} style={style}>
      {children}
    </button>
  );
}

function Panel({ children, framed, dark, className, style, ornaments = true }) {
  const cls = ["panel", framed ? "framed" : "", dark ? "dark" : "", className || ""].filter(Boolean).join(" ");
  return (
    <div className={cls} style={style}>
      {ornaments && framed && (
        <React.Fragment>
          <CornerOrnament corner="tl" />
          <CornerOrnament corner="tr" />
          <CornerOrnament corner="bl" />
          <CornerOrnament corner="br" />
        </React.Fragment>
      )}
      {children}
    </div>
  );
}

function NavRail({ current, onNavigate }) {
  const currentGroup = getGroupForScreen(current);
  return (
    <nav className="nav-rail" aria-label="Codex">
      {NAV_GROUPS.map((g) => (
        <button
          type="button"
          key={g.id}
          className={`nav-item ${currentGroup?.id === g.id ? "active" : ""}`}
          onClick={() => onNavigate(getDefaultScreen(g.id))}
          aria-current={currentGroup?.id === g.id ? "page" : undefined}
        >
          <span className="glyph"><Glyph kind={g.glyph} /></span>
          <span className="tip">{g.label}</span>
        </button>
      ))}
      <div className="nav-spacer"></div>
      <div className="nav-divider"></div>
      <button
        type="button"
        className={`nav-item ${currentGroup?.id === NAV_BOTTOM.id ? "active" : ""}`}
        onClick={() => onNavigate(getDefaultScreen(NAV_BOTTOM.id))}
        aria-current={currentGroup?.id === NAV_BOTTOM.id ? "page" : undefined}
      >
        <span className="glyph"><Glyph kind={NAV_BOTTOM.glyph} size={20} /></span>
        <span className="tip">{NAV_BOTTOM.label}</span>
      </button>
    </nav>
  );
}

function TabBar({ current, onNavigate }) {
  const group = getGroupForScreen(current);
  if (!group || group.tabs.length < 2) return null;
  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 0,
      padding: "0 14px",
      marginBottom: -1,
      position: "relative",
      zIndex: 3,
    }}>
      {/* Group label as quill */}
      <div style={{
        padding: "4px 14px 4px 0",
        fontFamily: "var(--f-display)",
        fontSize: 10,
        letterSpacing: "0.28em",
        textTransform: "uppercase",
        color: "var(--b-300)",
      }}>
        <span style={{ color: "var(--b-200)" }}>{group.label}</span>
      </div>
      <span style={{
        width: 1, height: 18,
        background: "linear-gradient(180deg, transparent, var(--b-500), transparent)",
        marginRight: 12,
      }} />
      {group.tabs.map((tab) => (
        <button
          type="button"
          key={tab.id}
          onClick={() => onNavigate(tab.id)}
          style={{
            position: "relative",
            padding: "8px 18px",
            marginRight: 2,
            fontFamily: "var(--f-display)",
            fontSize: 11,
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            background: current === tab.id
              ? "linear-gradient(180deg, var(--p-100), var(--p-200))"
              : "transparent",
            color: current === tab.id ? "var(--ink-900)" : "var(--b-300)",
            boxShadow: current === tab.id
              ? "inset 0 0 0 1px var(--b-500), inset 0 2px 0 var(--p-100), inset 0 -1px 0 var(--p-200)"
              : "inset 0 0 0 1px transparent",
            cursor: "pointer",
            transition: "all 140ms",
            top: current === tab.id ? 1 : 0,
          }}
          onMouseEnter={(e) => { if (current !== tab.id) e.currentTarget.style.color = "var(--b-100)"; }}
          onMouseLeave={(e) => { if (current !== tab.id) e.currentTarget.style.color = "var(--b-300)"; }}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

function CapabilityBadge({ capability, nativeStatus }) {
  if (!capability) return null;
  const tone = capability.tone || "brass";
  const bridge = nativeStatus?.bridge ? "native" : "browser";
  return (
    <span className={`capability-badge ${tone}`} title={capability.detail || ""}>
      <span>{capability.label}</span>
      <span className="capability-source">{bridge}</span>
    </span>
  );
}

function TitleBar({ campaign, location, day, capability, nativeStatus }) {
  const bridgeReady = Boolean(nativeStatus?.bridge && window.OpenWorldsNative?.hasBridge?.());
  const commandWindow = (command) => {
    if (!bridgeReady) return;
    window.OpenWorldsNative.request("windowCommand", { command }).catch((error) => {
      window.dispatchEvent(new CustomEvent("openworlds:toast", {
        detail: {
          kind: "danger",
          title: "Window command failed",
          body: error?.message || String(error),
        },
      }));
    });
  };
  const controlTitle = bridgeReady
    ? "Native window control"
    : "Unavailable outside the ClawDnD macOS app";

  return (
    <div className="title-bar">
      <div className="traffic-lights" aria-label="Window controls">
        <button
          type="button"
          className="traffic-light close"
          onClick={() => commandWindow("close")}
          disabled={!bridgeReady}
          title={`${controlTitle}: close`}
          aria-label="Close window"
        />
        <button
          type="button"
          className="traffic-light min"
          onClick={() => commandWindow("minimize")}
          disabled={!bridgeReady}
          title={`${controlTitle}: minimize`}
          aria-label="Minimize window"
        />
        <button
          type="button"
          className="traffic-light zoom"
          onClick={() => commandWindow("zoom")}
          disabled={!bridgeReady}
          title={`${controlTitle}: zoom`}
          aria-label="Zoom window"
        />
      </div>
      <div className="title-text">
        <span>Open Worlds</span><em>·</em><span>{campaign || "The Long Road to Odrun"}</span>
        {location && (<><em>·</em><span>{location}</span></>)}
      </div>
      <div className="title-end">
        <CapabilityBadge capability={capability} nativeStatus={nativeStatus} />
        {day && <span>{day}</span>}
      </div>
    </div>
  );
}

Object.assign(window, {
  NAV_GROUPS, NAV_BOTTOM, ALL_NAV, getGroupForScreen, getDefaultScreen,
  Glyph, CornerOrnament, Divider, SectionTitle, Pill,
  Placeholder, IconPlate, BrassButton, Panel, NavRail, TabBar, CapabilityBadge, TitleBar,
});
