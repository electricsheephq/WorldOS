/* OpenWorlds semantic icon registry.

   Imported Game Icons are kept local, attributed, and referenced through stable
   semantic ids so gameplay screens do not scatter third-party asset paths. */

const OPENWORLDS_ICON_MANIFEST = Object.freeze({
  "atlas.travel": {
    src: "assets/icons/game-icons/lorc/compass.svg",
    label: "Travel",
    author: "Lorc",
    license: "CC-BY-3.0",
  },
  "camp.rest": {
    src: "assets/icons/game-icons/lorc/campfire.svg",
    label: "Camp",
    author: "Lorc",
    license: "CC-BY-3.0",
  },
  "codex.book": {
    src: "assets/icons/game-icons/lorc/open-book.svg",
    label: "Codex",
    author: "Lorc",
    license: "CC-BY-3.0",
  },
  "combat.attack": {
    src: "assets/icons/game-icons/lorc/sword-clash.svg",
    label: "Attack",
    author: "Lorc",
    license: "CC-BY-3.0",
  },
  "dice.d20": {
    src: "assets/icons/game-icons/delapouite/dice-twenty-faces-twenty.svg",
    label: "d20",
    author: "Delapouite",
    license: "CC-BY-3.0",
  },
  "dice.roll": {
    src: "assets/icons/game-icons/delapouite/rolling-dices.svg",
    label: "Roll",
    author: "Delapouite",
    license: "CC-BY-3.0",
  },
  "economy.coins": {
    src: "assets/icons/game-icons/delapouite/coins.svg",
    label: "Coins",
    author: "Delapouite",
    license: "CC-BY-3.0",
  },
  "inventory.locked": {
    src: "assets/icons/game-icons/lorc/locked-chest.svg",
    label: "Locked",
    author: "Lorc",
    license: "CC-BY-3.0",
  },
  "inventory.potion": {
    src: "assets/icons/game-icons/delapouite/health-potion.svg",
    label: "Potion",
    author: "Delapouite",
    license: "CC-BY-3.0",
  },
  "party.shield": {
    src: "assets/icons/game-icons/willdabeast/round-shield.svg",
    label: "Shield",
    author: "Willdabeast",
    license: "CC-BY-3.0",
  },
  "quest.scroll": {
    src: "assets/icons/game-icons/lorc/tied-scroll.svg",
    label: "Quest",
    author: "Lorc",
    license: "CC-BY-3.0",
  },
  "settlement.tavern": {
    src: "assets/icons/game-icons/delapouite/tavern-sign.svg",
    label: "Settlement",
    author: "Delapouite",
    license: "CC-BY-3.0",
  },
});

const OPENWORLDS_ICON_ALIASES = Object.freeze({
  attack: "combat.attack",
  book: "codex.book",
  camp: "camp.rest",
  check: "dice.d20",
  coins: "economy.coins",
  compass: "atlas.travel",
  dice: "dice.d20",
  do: "quest.scroll",
  economy: "economy.coins",
  item: "inventory.potion",
  locked: "inventory.locked",
  map: "atlas.travel",
  potion: "inventory.potion",
  quest: "quest.scroll",
  rest: "camp.rest",
  roll: "dice.roll",
  say: "quest.scroll",
  scroll: "quest.scroll",
  shield: "party.shield",
  sword: "combat.attack",
  tavern: "settlement.tavern",
  travel: "atlas.travel",
});

function resolveOpenWorldsIconId(id) {
  if (!id) return "";
  const raw = String(id).trim();
  if (OPENWORLDS_ICON_MANIFEST[raw]) return raw;
  const normalized = raw.toLowerCase().replace(/_/g, "-");
  return OPENWORLDS_ICON_ALIASES[normalized] || "";
}

function OpenWorldsIcon({ id, label, size = 18, className = "", style, fallback }) {
  const iconId = resolveOpenWorldsIconId(id);
  const icon = iconId ? OPENWORLDS_ICON_MANIFEST[iconId] : null;
  if (!icon) {
    return (
      <span
        className={`ow-icon-fallback ${className || ""}`}
        style={{ width: size, height: size, fontSize: Math.max(9, size * 0.58), ...(style || {}) }}
        aria-label={label || fallback || "Icon"}
        title={label || fallback || ""}
      >
        {fallback || "◆"}
      </span>
    );
  }
  return (
    <span
      className={`ow-icon ${className || ""}`}
      style={{
        width: size,
        height: size,
        WebkitMaskImage: `url(${icon.src})`,
        maskImage: `url(${icon.src})`,
        ...(style || {}),
      }}
      role="img"
      aria-label={label || icon.label}
      title={label || icon.label}
    />
  );
}

OpenWorldsIcon.manifest = OPENWORLDS_ICON_MANIFEST;
OpenWorldsIcon.aliases = OPENWORLDS_ICON_ALIASES;
OpenWorldsIcon.resolve = resolveOpenWorldsIconId;
OpenWorldsIcon.has = (id) => Boolean(resolveOpenWorldsIconId(id));
OpenWorldsIcon.src = (id) => {
  const iconId = resolveOpenWorldsIconId(id);
  return iconId ? OPENWORLDS_ICON_MANIFEST[iconId]?.src || "" : "";
};

Object.assign(window, {
  OpenWorldsIcon,
  OPENWORLDS_ICON_MANIFEST,
  OPENWORLDS_ICON_ALIASES,
  resolveOpenWorldsIconId,
});
