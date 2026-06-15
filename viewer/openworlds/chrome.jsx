/* Shared chrome: window frame, nav rail, title bar */

// Shared name→slug for /image scope keys (item-<slug>, etc.). screen-character / forge /
// merchant build their item-art scopes via `window.slug` but it was NEVER defined — so the
// scope collapsed to "item-" and every item silently 404'd to a Placeholder despite the art
// existing (item-chain-mail etc. resolve). Define it once here (chrome.jsx loads before the
// screens). Same logic as screen-inventory's local slug().
window.slug = function slug(name) {
  return (name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
};

// Shared item art aliases. Player-facing item names often carry table qualifiers
// ("Travel rations", "Iron lantern", "Wax candle (x6)") while the ingested art cache
// stores the reusable base prop ("item-rations", "item-lantern", "item-candle").
// Keep this in the viewer read layer so engine item identity stays unchanged.
const ITEM_ART_ALIASES = {
  "travel-ration": "rations",
  "travel-rations": "rations",
  "iron-lantern": "lantern",
  "climbing-kit": "rope",
  "wax-candle-6": "candle",
  "wax-candles-6": "candle",
  "candles": "candle",
  "sharpened-greataxe-edge": "greataxe",
  "bandage-roll": "",
  "compass": "",
  "iron-chain-10ft": "",
  "spellbook-blank": "",
};

window.itemArtScope = function itemArtScope(itemOrName) {
  const name = typeof itemOrName === "string" ? itemOrName : itemOrName?.name;
  const s = window.slug(name);
  const aliased = Object.prototype.hasOwnProperty.call(ITEM_ART_ALIASES, s) ? ITEM_ART_ALIASES[s] : s;
  return aliased ? "item-" + aliased : "";
};

// ── Shared currency layer (RRI-5e98e6f optimizer finding: "Stash 35 GP, Market 232 GP") ──
// The coin purse is engine-owned (server `_currency_for` → {cp,sp,ep,gp,pp} ints) and rides on
// EACH party member of BOTH the /character-surface and /inventory-surface read models
// (`party[i].currency`). The Market used to read a non-existent top-level `surface.currency` and
// fell through to a hardcoded demo purse ({gp:232}), while the Stash showed the live per-hero
// purse (e.g. 35gp) — the 35-vs-232 contradiction. Both screens now derive their displayed coins
// from these ONE shared helpers so the same character renders the same purse + total everywhere.
// Read-only: these only project engine-owned numbers; they never write campaign state.
const _COIN_KEYS = ["pp", "gp", "sp", "ep", "cp"];

// Coerce any currency-ish object to the canonical {pp,gp,sp,ep,cp} int shape (mirrors the engine
// `_currency_for`). Tolerates undefined/null/garbage by zeroing — never throws, never invents.
window.normalizeCurrency = function normalizeCurrency(cur) {
  const src = (cur && typeof cur === "object") ? cur : {};
  const out = {};
  for (const k of _COIN_KEYS) {
    const n = Number(src[k]);
    out[k] = Number.isFinite(n) ? Math.trunc(n) : 0;
  }
  return out;
};

// The ONE 5e gp-equivalent conversion (1pp=10gp, 1ep=0.5gp, 1sp=0.1gp, 1cp=0.01gp). Using this
// single converter on both screens is what keeps a "total" from diverging between them.
window.currencyTotalGp = function currencyTotalGp(cur) {
  const c = window.normalizeCurrency(cur);
  return c.pp * 10 + c.gp + c.ep * 0.5 + c.sp * 0.1 + c.cp * 0.01;
};

// Select the SAME source-of-truth hero's purse both screens render: the active hero by id, else
// the first party member. Both surfaces are projected from the same snapshot in the same `party`
// order, so an absent/blank active id (the Market has no hero switcher) lands on party[0] — which
// is exactly the Stash's default active hero. Always returns the normalized coin shape.
window.partyPurse = function partyPurse(party, activeId) {
  const list = Array.isArray(party) ? party : [];
  const hero = list.find((p) => p && p.id === activeId) || list[0] || null;
  return window.normalizeCurrency(hero && hero.currency);
};

const NAV_GROUPS = [
  {
    id: "g_table", label: "Table", glyph: "dice",
    tabs: [
      { id: "table", label: "Session" },
      // `hint` is the newcomer disambiguator (dogfood: two newbies found "Battle"/"Parley"
      // opaque D&D jargon — "unclear what they do vs just typing in the box"). TabBar renders it
      // as a small plain-language sub-label + folds it into the tab's title/aria-label, so a
      // no-prior-knowledge player (and a screen reader) reads what the tab does — WITHOUT renaming
      // away the thematic flavor. Only the jargon tabs carry one; plain-English tabs (Session) don't.
      { id: "combat", label: "Battle", hint: "combat" },
      { id: "dialogue", label: "Parley", hint: "talk" },
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
      { id: "bestiary", label: "Codex", hint: "lore & journal" },
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

const CHROME_BUILTIN_GLYPHS = new Set(["map", "compass", "dice", "shield", "book", "coins", "settings"]);

function Glyph({ kind, size = 22 }) {
  if (!CHROME_BUILTIN_GLYPHS.has(kind) && window.OpenWorldsIcon?.has?.(kind)) {
    return <window.OpenWorldsIcon id={kind} size={size} />;
  }
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

// The render bridge (#W2a): show generated/ingested art from the viewer's `/image?scope=…`
// endpoint, falling back to the styled <Placeholder> when no art is cached (404) — so the UI
// is beautiful when art exists and graceful when it doesn't (the default null-image path).
// `scope` follows the engine convention: a location_id for a scene, `portrait-<character_id>`
// for an NPC/PC, `item-<item_id>` for an item icon. Empty scope → placeholder (no fetch).
function PortraitSilhouette() {
  // A neutral head-and-shoulders silhouette for a character with no ingested face.
  // Deliberately NOT a class/race heraldic crest — a coat of arms is not a person.
  return (
    <svg viewBox="0 0 64 64" width="56%" height="56%" aria-hidden="true"
         style={{ opacity: 0.5, color: "var(--b-500, #8c7a52)" }}>
      <circle cx="32" cy="23" r="13" fill="currentColor" />
      <path d="M9 60c0-13 10-21 23-21s23 8 23 21z" fill="currentColor" />
    </svg>
  );
}

// #826: how many times Img RETRIES a scope whose /image 404s, and the backoff between tries. The
// scene image is fire-and-forget (#399): the engine returns a "pending" descriptor immediately and a
// daemon worker writes the real art OFF the turn path, so /image legitimately 404s for a window after
// a new scope appears. The OLD Img latched `failed` on the first onError and only ever cleared it on
// a SCOPE CHANGE — so a same-scope image that became servable LATER stayed frozen on the placeholder
// forever (a dead handle). That froze the scene art when a player navigated away mid-narration and
// back (the surface re-projects the same scope, but the latched component never re-attempts). These
// retries let the component recover when the pending art lands, while a bounded budget + backoff
// keeps a genuinely-missing image from hammering the endpoint.
const _IMG_MAX_RETRIES = 8;
const _IMG_RETRY_MS = 4000;

function Img({ scope, label, w, h, framed, style, className, fit = "cover" }) {
  // `attempt` doubles as the cache-buster + the retry counter; `failed` is the per-attempt error
  // latch (placeholder while we wait), NOT a permanent freeze.
  const [attempt, setAttempt] = React.useState(0);
  const [failed, setFailed] = React.useState(false);
  const retryRef = React.useRef(null);
  const clearRetry = () => {
    if (retryRef.current != null) { window.clearTimeout(retryRef.current); retryRef.current = null; }
  };
  // A new scope is a fresh subject — reset the retry budget + error latch and cancel any pending retry.
  React.useEffect(() => {
    setFailed(false);
    setAttempt(0);
    return clearRetry;
  }, [scope]);
  const isPortrait = /(^|[-:/])(portrait|pc|npc|char)/i.test(scope || "");
  const onError = () => {
    // #826: do NOT permanently latch. Show the placeholder for THIS attempt, then — if we still have
    // retry budget for this scope (the #399 pending-art window) — schedule another try so a scope
    // whose image becomes servable later RECOVERS instead of freezing on a dead handle. Past the
    // budget we stop (a genuinely-missing image), still on the graceful placeholder.
    setFailed(true);
    if (attempt >= _IMG_MAX_RETRIES) return;
    clearRetry();
    retryRef.current = window.setTimeout(() => {
      retryRef.current = null;
      setFailed(false);          // clear the per-attempt latch …
      setAttempt((a) => a + 1);  // … and re-mount the <img> (cache-busted) to re-probe /image.
    }, _IMG_RETRY_MS);
  };
  if (!scope || failed) {
    return (
      <Placeholder label={isPortrait ? "" : label} w={w} h={h} framed={framed} style={style} className={className}>
        {isPortrait ? <PortraitSilhouette /> : undefined}
      </Placeholder>
    );
  }
  // The cache-buster (`v=attempt`) forces the browser to actually re-request the scope on a retry
  // rather than re-serve the cached 404; attempt 0 keeps the original URL shape (no change for the
  // happy path / existing tests that assert the `/image?scope=` prefix).
  const src = attempt > 0
    ? `/image?scope=${encodeURIComponent(scope)}&v=${attempt}`
    : `/image?scope=${encodeURIComponent(scope)}`;
  return (
    <img
      src={src}
      alt={label || ""}
      loading="lazy"
      onError={onError}
      className={`ow-img ${framed ? "framed" : ""} ${className || ""}`}
      style={{ width: w, height: h, objectFit: fit, display: "block", ...(style || {}) }}
    />
  );
}

function IconPlate({ size = 56, label, framed = true, glyph, tone, children, onClick, active, style, testId, ...buttonProps }) {
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
      aria-label={buttonProps["aria-label"] || label || undefined}
      data-worldos-testid={testId || undefined}
      {...buttonProps}
    >
      {children || (glyph && window.OpenWorldsIcon?.has?.(glyph) ? (
        <window.OpenWorldsIcon id={glyph} size={Math.max(18, size * 0.46)} label={label} />
      ) : (
        <span className="ph-label" style={{ fontSize: 9 }}>{label}</span>
      ))}
    </button>
  );
}

function BrassButton({ children, onClick, tone, size, disabled, style, type = "button", title, ariaLabel, testId, ...buttonProps }) {
  const cls = ["btn", tone, size, disabled ? "disabled" : ""].filter(Boolean).join(" ");
  // `title` is optional — forwarded so callers can attach a hover/affordance tooltip
  // (e.g. the #337 action-bar hints) without giving every BrassButton one.
  return (
    <button
      className={cls}
      onClick={onClick}
      disabled={disabled}
      type={type}
      style={style}
      title={title || undefined}
      aria-label={ariaLabel || undefined}
      data-worldos-testid={testId || undefined}
      {...buttonProps}
    >
      {children}
    </button>
  );
}

function Panel({ children, framed, dark, className, style, ornaments = true }) {
  const cls = ["panel", framed ? "framed" : "", dark ? "dark" : "", className || ""].filter(Boolean).join(" ");
  // a11y (#291): framed panels are the scrollable content containers. axe
  // `scrollable-region-focusable` requires a scrollable region be keyboard-reachable —
  // tabIndex={0} makes it focusable so keyboard users can scroll it. (No role=region without
  // an accessible name, which would trade one violation for another.)
  return (
    <div className={cls} style={style} tabIndex={framed ? 0 : undefined}>
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
    <nav className="nav-rail" aria-label="Codex" data-worldos-testid="primary-navigation">
      {NAV_GROUPS.map((g) => (
        <button
          type="button"
          key={g.id}
          className={`nav-item ${currentGroup?.id === g.id ? "active" : ""}`}
          onClick={() => onNavigate(getDefaultScreen(g.id))}
          aria-current={currentGroup?.id === g.id ? "page" : undefined}
          aria-label={g.label}
          data-worldos-testid="primary-nav-item"
          data-worldos-nav-id={g.id}
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
        aria-label={NAV_BOTTOM.label}
        data-worldos-testid="primary-nav-item"
        data-worldos-nav-id={NAV_BOTTOM.id}
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
    <div role="tablist" aria-label={`${group.label} screens`} data-worldos-testid="screen-tabs" style={{
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
      {group.tabs.map((tab) => {
        // Newcomer disambiguator (dogfood): the jargon tabs (Battle/Parley/Codex) carry a `hint`.
        // We surface it BOTH ways — a small visible plain-language sub-label under the thematic
        // label (sighted first-timer) AND folded into the button's title + aria-label (hover
        // tooltip + screen reader), e.g. "Parley · talk". The flavor label is never replaced.
        const tip = tab.hint ? `${tab.label} · ${tab.hint}` : tab.label;
        return (
        <button
          type="button"
          key={tab.id}
          role="tab"
          aria-selected={current === tab.id}
          data-worldos-testid="screen-tab"
          data-worldos-tab-id={tab.id}
          className={`tab-button ${current === tab.id ? "active" : ""}`}
          onClick={() => onNavigate(tab.id)}
          title={tip}
          aria-label={tip}
          style={{
            position: "relative",
            // a hint adds a sub-label line, so stack the two lines instead of centering one.
            display: "inline-flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: tab.hint ? 1 : 0,
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
          <span>{tab.label}</span>
          {tab.hint && (
            <span
              className="tab-sublabel"
              data-worldos-testid="screen-tab-hint"
              style={{
                fontSize: 8,
                letterSpacing: "0.12em",
                fontWeight: 400,
                opacity: current === tab.id ? 0.85 : 0.7,
                // keep the plain-language hint readable — no uppercase mangling of "lore & journal".
                textTransform: "none",
                lineHeight: 1,
                marginTop: 1,
              }}
            >
              {tab.hint}
            </span>
          )}
        </button>
        );
      })}
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
  return (
    <div className="title-bar">
      {/* Platform-aware (#260, finished in #306): the macOS native window floats the REAL traffic
          lights (RootView.swift) at top-left over this transparent bar, so reserve 76px to clear
          them in the native app (nativeStatus.bridge true). #306: the browser branch was 0, which
          let a long campaign title's left edge run UNDER the 78px nav-rail below it (the rail starts
          at x=0 in the column beneath this bar) — so reserve ~78px there too. paddingLeft stays
          inline because it's the only nativeStatus-conditional bit; the nowrap + ellipsis + maxWidth
          clamp that keeps a long title on ONE line (clear of the day/capability pills) lives in
          styles.css (.title-text). */}
      <div className="title-text" style={{ paddingLeft: nativeStatus?.bridge ? 76 : 78 }}>
        <span>Open Worlds</span><em>·</em><span>{campaign || "Open Worlds"}</span>
        {location && (<><em>·</em><span>{location}</span></>)}
      </div>
      {/* #306: the right band (minWidth + larger font) now lives in styles.css (.title-end); the day
          pill keeps a slightly larger size so "DAY 1 · MORNING" reads against the campaign title. */}
      <div className="title-end">
        <CapabilityBadge capability={capability} nativeStatus={nativeStatus} />
        {day && <span style={{ fontSize: 13, letterSpacing: "0.06em" }}>{day}</span>}
      </div>
    </div>
  );
}

Object.assign(window, {
  NAV_GROUPS, NAV_BOTTOM, ALL_NAV, getGroupForScreen, getDefaultScreen,
  Glyph, CornerOrnament, Divider, SectionTitle, Pill,
  Placeholder, Img, IconPlate, BrassButton, Panel, NavRail, TabBar, CapabilityBadge, TitleBar,
});
