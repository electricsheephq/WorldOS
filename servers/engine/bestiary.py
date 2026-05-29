"""Bestiary: instantiate combat-ready monsters from the bundled SRD creature data.

Pure module (no MCP, no campaign I/O). It reads the vendored SRD 5.2.1 creature
dump (``data/srd/srd524/Creature.json`` + ``CreatureAction.json``, the Open5e
srd-2024 fixtures, CC-BY-4.0) and flattens each creature into the engine's stat
block shape — so the play loop can spawn a goblin or an aboleth from data
instead of the DM hand-transcribing HP/AC every fight (the single biggest
consistency gap the audit found). The SRD JSON is a Django fixture
(``{model, pk, fields}``); actions live in a separate file and FK-join to the
creature ``pk`` via ``fields.parent``.

Attack to-hit/damage stays as descriptive text (the engine never parsed it — the
DM reads the action and supplies attack_bonus/damage_dice to ``attack``); what
the engine *uses* mechanically is hp / ac / abilities / resistances / immunities.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Optional

import encounter

_ROOT = Path(__file__).resolve().parents[2] / "data" / "srd"
_PRIMARY = _ROOT / "srd524"  # canonical SRD 5.2 — always wins a name collision
_AUTHORED_ROOT = Path(__file__).resolve().parents[2] / "data" / "bestiary" / "authored"

_ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
_ABILITY_SHORTS = ("str", "dex", "con", "int", "wis", "cha")
_CONTENT_METADATA = ("license", "source", "provenance")

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def creature_slug(name: str) -> str:
    """Stable join key for a creature TYPE — IDENTICAL to the viewer's ``creatureSlug``
    (screen-bestiary.jsx): lowercase, runs of ``[^a-z0-9]+`` collapsed to ``-``, then any
    leading/trailing ``-`` trimmed. So the engine's intel key and the UI's art-scope key
    never diverge ("Goblin Warrior" -> "goblin-warrior"). Pure; ``""`` for an empty name."""
    return _SLUG_NON_ALNUM.sub("-", str(name or "").lower()).strip("-")


def _dirs() -> list:
    """Creature-data dirs in PRECEDENCE order: srd524 first (canonical), then any
    additional packs under data/srd/ (e.g. an ingested ``bfrpg/``). Each later pack
    only fills gaps — it never overrides an SRD creature of the same name (first-wins).
    Only dirs that actually carry a ``Creature.json`` are included."""
    dirs = [_PRIMARY]
    if _ROOT.is_dir():
        for sub in sorted(_ROOT.iterdir()):
            if sub.is_dir() and sub != _PRIMARY and (sub / "Creature.json").exists():
                dirs.append(sub)
    return [d for d in dirs if (d / "Creature.json").exists()]


@functools.lru_cache(maxsize=None)
def _actions_by_source_parent() -> dict:
    """(source_dir_name, parent_pk) -> [actions]. Keyed by SOURCE as well as pk so two
    packs that happen to reuse the same fixture pk never cross-attribute their actions."""
    out: dict = {}
    for d in _dirs():
        caf = d / "CreatureAction.json"
        if not caf.exists():
            continue
        for row in json.loads(caf.read_text(encoding="utf-8")):
            f = row.get("fields", {})
            parent = f.get("parent")
            if parent:
                out.setdefault((d.name, parent), []).append(
                    {"name": f.get("name", ""), "desc": f.get("desc", ""),
                     "action_type": f.get("action_type", "ACTION")}
                )
    return out


@functools.lru_cache(maxsize=None)
def _index() -> dict[str, dict]:
    """name (lowercased) -> ``{"src": dir_name, "row": creature row}``. FIRST-WINS
    across dirs in precedence order (srd524 first): a later pack whose creature name is
    already present is skipped, so SRD creatures are never silently overwritten."""
    out: dict[str, dict] = {}
    for d in _dirs():
        for c in json.loads((d / "Creature.json").read_text(encoding="utf-8")):
            name = c.get("fields", {}).get("name")
            if name:
                key = name.lower()
                if key not in out:  # FIRST-WINS — earlier dir (srd524) takes precedence
                    out[key] = {"src": d.name, "row": c, "content_origin": "srd"}
    for key, entry in _authored_entries()[0].items():
        if key not in out:  # authored monsters fill gaps, never shadow SRD names
            out[key] = entry
    return out


def _authored_pack_dirs() -> list[Path]:
    """Native authored monster pack dirs.

    These are intentionally separate from ``data/srd`` fixture packs: authored records
    need explicit license/source/provenance metadata and never participate in SRD
    fixture precedence. A pack is a directory with one ``pack.json`` manifest.
    """
    if not _AUTHORED_ROOT.is_dir():
        return []
    return sorted(p for p in _AUTHORED_ROOT.iterdir() if p.is_dir() and (p / "pack.json").exists())


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _metadata_errors(obj: dict, label: str) -> list[str]:
    errors: list[str] = []
    for field in _CONTENT_METADATA:
        value = obj.get(field)
        if not isinstance(value, dict) or not any(str(v).strip() for v in value.values()):
            errors.append(f"{label} missing explicit {field} metadata")
    return errors


def _authored_abilities(raw: object) -> dict[str, int]:
    data = raw if isinstance(raw, dict) else {}
    out: dict[str, int] = {}
    for short, full in zip(_ABILITY_SHORTS, _ABILITIES):
        out[short] = int(data.get(short, data.get(full, 10)) or 10)
    return out


def _authored_actions(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        desc = str(row.get("desc", row.get("description", ""))).strip()
        if name:
            out.append({"name": name, "desc": desc, "action_type": str(row.get("action_type", "ACTION"))})
    return out


@functools.lru_cache(maxsize=None)
def _authored_entries() -> tuple[dict[str, dict], tuple[str, ...]]:
    """Load valid native authored monsters and collect validation errors.

    The loader is intentionally strict about metadata and conservative about name
    collisions. Invalid records are excluded from the runtime index; collisions against
    SRD names are reported and skipped so authored packs cannot silently replace a
    canonical SRD creature.
    """
    entries: dict[str, dict] = {}
    errors: list[str] = []
    srd_names = {
        c.get("fields", {}).get("name", "").strip().lower()
        for d in _dirs()
        for c in _read_json(d / "Creature.json")
        if c.get("fields", {}).get("name")
    }
    for pack_dir in _authored_pack_dirs():
        pack_path = pack_dir / "pack.json"
        try:
            pack = _read_json(pack_path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{pack_path}: unreadable authored monster pack: {exc}")
            continue
        if not isinstance(pack, dict):
            errors.append(f"{pack_path}: authored monster pack must be a JSON object")
            continue
        pack_label = f"pack {pack.get('id') or pack_dir.name}"
        errors.extend(_metadata_errors(pack, pack_label))
        monsters = pack.get("monsters")
        if not isinstance(monsters, list):
            errors.append(f"{pack_label} missing monsters list")
            continue
        for row in monsters:
            if not isinstance(row, dict):
                errors.append(f"{pack_label} has non-object monster record")
                continue
            name = str(row.get("name", "")).strip()
            record_label = f"{pack_label} monster {name or '<unnamed>'}"
            record_errors = _metadata_errors(row, record_label)
            if not name:
                record_errors.append(f"{record_label} missing name")
            key = name.lower()
            if key in srd_names:
                record_errors.append(f"{record_label} overrides SRD creature {name!r}; authored packs may only add net-new names")
            if key in entries:
                record_errors.append(f"{record_label} duplicates authored creature {name!r}")
            if record_errors:
                errors.extend(record_errors)
                continue
            fields = {
                "name": name,
                "size": str(row.get("size", "")),
                "type": str(row.get("type", "")),
                "armor_class": int(row.get("armor_class", row.get("ac", 10)) or 10),
                "hit_points": int(row.get("hit_points", row.get("hp", 1)) or 1),
                "hit_dice": str(row.get("hit_dice", "")),
                "abilities": _authored_abilities(row.get("abilities")),
                "challenge_rating": _norm_cr(row.get("challenge_rating", row.get("cr", "0"))),
                "experience_points": int(row.get("experience_points", row.get("xp", 0)) or 0),
                "proficiency_bonus": int(row.get("proficiency_bonus", 2) or 2),
                "initiative_bonus": int(row.get("initiative_bonus", 0) or 0),
                "damage_resistances": _as_list(row.get("damage_resistances")),
                "damage_immunities": _as_list(row.get("damage_immunities")),
                "damage_vulnerabilities": _as_list(row.get("damage_vulnerabilities")),
                "condition_immunities": _as_list(row.get("condition_immunities")),
                "actions": _authored_actions(row.get("actions")),
            }
            repo_root = Path(__file__).resolve().parents[2]
            try:
                src = str(pack_path.relative_to(repo_root))
            except ValueError:
                src = str(pack_path)
            entries[key] = {
                "src": src,
                "content_origin": "authored",
                "fields": fields,
                "license": row["license"],
                "source": row["source"],
                "provenance": row["provenance"],
            }
    return entries, tuple(errors)


def _norm_cr(cr) -> str:
    """srd524 stores CR as a decimal string ('10.000', '0.250'). Canonicalize to
    the engine's '0'/'1/8'/'1/4'/'1/2'/'1'..'30' keys."""
    if cr in (None, ""):
        return "0"
    try:
        val = float(cr)
    except (TypeError, ValueError):
        return str(cr).strip()
    fractions = {0.125: "1/8", 0.25: "1/4", 0.5: "1/2"}
    if val in fractions:
        return fractions[val]
    return str(int(val))


def _as_list(value) -> list[str]:
    """resistance/immunity fields may be a list, a comma/semicolon string, or empty."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).replace(";", ",").split(",") if p.strip()]


# Movement / sense modes carried by the srd524 Creature fixture (feet, or None when absent).
# Order is the conventional stat-block order so a composed display string reads naturally.
_SPEED_MODES = ("walk", "fly", "swim", "climb", "burrow")
_SENSE_MODES = (
    ("darkvision", "darkvision_range"),
    ("blindsight", "blindsight_range"),
    ("tremorsense", "tremorsense_range"),
    ("truesight", "truesight_range"),
)


def _speed_from_srd(f: dict) -> dict[str, int]:
    """The creature's movement modes from the raw srd524 fields, as ``{mode: feet}`` for
    every mode actually present (walk/fly/swim/climb/burrow). Empty when none are set —
    so the UI hides the row rather than print a fake '0 ft'. Pure."""
    out: dict[str, int] = {}
    for mode in _SPEED_MODES:
        val = f.get(mode)
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
            out[mode] = int(val)
    return out


def _senses_from_srd(f: dict) -> dict[str, int]:
    """The creature's special senses + passive Perception from the raw srd524 fields, as
    ``{sense: range_or_value}`` for every sense present (darkvision/blindsight/tremorsense/
    truesight ranges in feet + ``passive_perception``). Empty when none are set. Pure."""
    out: dict[str, int] = {}
    for label, key in _SENSE_MODES:
        val = f.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
            out[label] = int(val)
    pp = f.get("passive_perception")
    if isinstance(pp, (int, float)) and not isinstance(pp, bool):
        out["passive_perception"] = int(pp)
    return out


def _saves_from_srd(f: dict, abilities: dict[str, int]) -> dict[str, int]:
    """The creature's PROFICIENT saving throws from the raw srd524 fields, as
    ``{ability_short: total_bonus}``. srd524 stores all six ``saving_throw_*`` as the
    creature's TOTAL save bonus; a save is proficient (and so worth listing, matching a
    printed stat block) only when that total EXCEEDS the bare ability modifier. Returns
    only the proficient ones; empty when the creature has no save proficiencies. Pure."""
    out: dict[str, int] = {}
    for short, full in zip(_ABILITY_SHORTS, _ABILITIES):
        save = f.get(f"saving_throw_{full}")
        if not isinstance(save, (int, float)) or isinstance(save, bool):
            continue
        score = abilities.get(short, 10)
        ability_mod = (int(score) - 10) // 2
        if int(save) > ability_mod:
            out[short] = int(save)
    return out


def authored_validation_errors() -> list[str]:
    """Validation errors for committed/native authored monster packs.

    Read-only helper for CI, PR review, and future authoring UI work. Invalid authored
    records are excluded from the bestiary index, so this can be surfaced without
    mutating combat state or allowing unsafe content through.
    """
    return list(_authored_entries()[1])


def _stat_block_from_authored(entry: dict, fallback_name: str) -> dict:
    f = entry["fields"]
    cr = _norm_cr(f.get("challenge_rating"))
    xp = int(f.get("experience_points") or 0)
    if xp == 0:
        try:
            xp = encounter.xp_for_cr(cr)
        except ValueError:
            xp = 0
    return {
        "name": f.get("name", fallback_name),
        "size": f.get("size", ""),
        "type": f.get("type", ""),
        "ac": int(f.get("armor_class") or 10),
        "hp": int(f.get("hit_points") or 1),
        "hit_dice": f.get("hit_dice", ""),
        "abilities": dict(f.get("abilities") or {}),
        "cr": cr,
        "xp": xp,
        "proficiency_bonus": int(f.get("proficiency_bonus") or 2),
        "initiative_bonus": int(f.get("initiative_bonus") or 0),
        "damage_resistances": _as_list(f.get("damage_resistances")),
        "damage_immunities": _as_list(f.get("damage_immunities")),
        "damage_vulnerabilities": _as_list(f.get("damage_vulnerabilities")),
        "condition_immunities": _as_list(f.get("condition_immunities")),
        # The authored pack schema does not carry speed/senses/saves yet (see
        # MONSTER_AUTHORING.md). Default empty so the intel-tier reveal (#263) degrades
        # gracefully — the UI simply hides the blank rows; no fake "0 ft" / empty save.
        "speed": {},
        "senses": {},
        "saves": {},
        "actions": list(f.get("actions") or []),
        "content_origin": "authored",
        "license": entry["license"],
        "source": entry["source"],
        "provenance": entry["provenance"],
    }


def stat_block(name: str) -> Optional[dict]:
    """A flat, engine-shaped stat block for a creature by (case-insensitive) name,
    or None if unknown. Includes abilities, AC, HP, CR/XP, the damage
    resistance/immunity/vulnerability + condition-immunity lists, and the creature's
    actions/traits as text."""
    entry = _index().get(name.strip().lower())
    if entry is None:
        return None
    if entry.get("content_origin") == "authored":
        return _stat_block_from_authored(entry, name)
    row = entry["row"]
    f = row["fields"]
    abilities = {
        short: int(f.get(f"ability_score_{full}") or 10)
        for short, full in zip(("str", "dex", "con", "int", "wis", "cha"), _ABILITIES)
    }
    cr = _norm_cr(f.get("challenge_rating"))
    # The 2024 SRD dump omits XP; derive it from CR via the engine's table.
    xp = int(f.get("experience_points_integer") or 0)
    if xp == 0:
        try:
            xp = encounter.xp_for_cr(cr)
        except ValueError:
            xp = 0
    return {
        "name": f.get("name", name),
        "size": f.get("size", ""),
        "type": f.get("type", ""),
        "ac": int(f.get("armor_class") or 10),
        "hp": int(f.get("hit_points") or 1),
        "hit_dice": f.get("hit_dice", ""),
        "abilities": abilities,
        "cr": cr,
        "xp": xp,
        "proficiency_bonus": int(f.get("proficiency_bonus") or 2),
        "initiative_bonus": int(f.get("initiative_bonus") or 0),
        "damage_resistances": _as_list(f.get("damage_resistances")),
        "damage_immunities": _as_list(f.get("damage_immunities")),
        "damage_vulnerabilities": _as_list(f.get("damage_vulnerabilities")),
        "condition_immunities": _as_list(f.get("condition_immunities")),
        # speed/senses/saves power the intel-tier reveal (#263). Additive keys; existing
        # callers ignore them. Empty dict when a mode/sense/proficiency is absent.
        "speed": _speed_from_srd(f),
        "senses": _senses_from_srd(f),
        "saves": _saves_from_srd(f, abilities),
        "actions": _actions_by_source_parent().get((entry["src"], row.get("pk")), []),
        "content_origin": "srd",
    }


_PARRY_AC_RE = re.compile(r"adds\s+(\d+)\s+to\s+its\s+ac", re.IGNORECASE)


def parry_bonus(sb: Optional[dict]) -> int:
    """The AC bonus a creature can add via a defensive REACTION against a melee hit it can
    see — the Parry reaction (Bandit Captain +2, fallen consular +4). Scans the stat block's
    REACTION-type actions for the 'adds N to its AC … melee' pattern and returns N; 0 if the
    creature has no such reaction. Pure; used at spawn to set Character.parry (#218)."""
    if not sb:
        return 0
    for a in sb.get("actions", []) or []:
        if str(a.get("action_type", "")).upper() != "REACTION":
            continue
        desc = str(a.get("desc", ""))
        m = _PARRY_AC_RE.search(desc)
        if m and "melee" in desc.lower():
            return int(m.group(1))
    return 0


def player_bestiary_preview(name: str) -> Optional[dict]:
    """Player-safe codex preview for a known creature.

    This is deliberately narrower than ``stat_block``: no HP, AC, ability scores,
    tactical notes, or action text. It is safe for viewer/browser projection and is
    read-only over the bestiary index.
    """
    sb = stat_block(name)
    if sb is None:
        return None
    preview = {
        "name": sb["name"],
        "size": sb.get("size", ""),
        "type": sb.get("type", ""),
        "cr": sb.get("cr", "0"),
        "content_origin": sb.get("content_origin", "srd"),
        "known_actions": [str(a.get("name", "")).strip() for a in sb.get("actions", []) if a.get("name")][:5],
    }
    if sb.get("content_origin") == "authored":
        preview["source"] = sb["source"]
        preview["license"] = sb["license"]
        preview["provenance"] = sb["provenance"]
    return preview


def _tactics_text(sb: dict) -> str:
    """A short tactics blurb composed from the creature's action TEXT (kill-tier only).
    Joins the first few actions' name + desc into a paragraph the codex 'Tactics' panel can
    render. Pure; "" when the creature has no action text."""
    lines: list[str] = []
    for a in sb.get("actions", []) or []:
        name = str(a.get("name", "")).strip()
        desc = str(a.get("desc", "")).strip()
        if name and desc:
            lines.append(f"{name}. {desc}")
        elif desc:
            lines.append(desc)
        if len(lines) >= 4:
            break
    return "\n\n".join(lines)


def intel_projection(name: str, tier: int) -> Optional[dict]:
    """Tier-gated player-facing stat reveal for a creature (intel-tier codex, #263).

    The party earns intel per creature TYPE: 1=sighted (CR + size + type), 2=engaged
    (+ AC + speed + senses), 3=slain (+ HP/HD + ability scores + saves + actions/tactics).
    Each higher tier strictly SUPERSETS the lower, so the reveal grows monotonically. Pure +
    read-only over the bestiary index; ``None`` for an unknown creature, ``None`` for tier<=0
    (an unencountered creature has no stat reveal — the caller renders an 'unknown' row).

    The returned dict always carries ``tier`` so the UI can label the page. speed/senses/saves
    are passed through as structured dicts (the viewer formats them for display); ac/hp/hit_dice
    are raw. authored creatures (no speed/senses/saves in their schema) simply omit empty slots,
    which the hide-when-blank UI drops — never a fake stat block.
    """
    t = int(tier)
    if t <= 0:
        return None
    sb = stat_block(name)
    if sb is None:
        return None
    # Tier 1 — sighted: identity + threat rating only (mirrors player_bestiary_preview's
    # safe surface, minus action names which are a kill-tier reveal here).
    out: dict = {
        "name": sb["name"],
        "size": sb.get("size", ""),
        "type": sb.get("type", ""),
        "cr": sb.get("cr", "0"),
        "content_origin": sb.get("content_origin", "srd"),
        "tier": t,
    }
    if sb.get("content_origin") == "authored":
        out["source"] = sb["source"]
        out["license"] = sb["license"]
        out["provenance"] = sb["provenance"]
    # Tier 2 — engaged: defenses you'd learn trading blows.
    if t >= 2:
        out["ac"] = sb.get("ac")
        if sb.get("speed"):
            out["speed"] = dict(sb["speed"])
        if sb.get("senses"):
            out["senses"] = dict(sb["senses"])
    # Tier 3 — slain: the full sheet — vitals, ability scores, proficient saves, actions.
    if t >= 3:
        out["hp"] = sb.get("hp")
        out["hit_dice"] = sb.get("hit_dice", "")
        if sb.get("abilities"):
            out["abilities"] = dict(sb["abilities"])
        if sb.get("saves"):
            out["saves"] = dict(sb["saves"])
        out["known_actions"] = [
            str(a.get("name", "")).strip() for a in sb.get("actions", []) if a.get("name")
        ][:8]
        tactics = _tactics_text(sb)
        if tactics:
            out["tactics"] = tactics
    return out


def player_bestiary(query: str = "", limit: int = 20, intel: Optional[dict] = None) -> dict:
    """Read-only player-facing bestiary/codex projection.

    Two modes, selected by ``intel`` (kept PURE — this module never opens a campaign file;
    the viewer loads the snapshot read-only and passes the dict in):

    * ``intel is None`` (today's call): the global SRD browse — ``player_bestiary_preview``
      for every match, no campaign scope, no leakage. Back-compat preserved byte-for-byte.
    * ``intel`` set (a ``{creature_slug: max_tier}`` dict, the campaign's earned intel): the
      intel-tier codex (#263). Each match's tier is looked up by ``creature_slug(name)``;
      creatures at tier >= 1 get ``intel_projection`` (progressively more stats per tier),
      and unencountered matches (tier 0) become a REDACTED ``{id_hint, tier:0, unknown:true}``
      rumour row — the real name is withheld from the wire (only an opaque render key is sent),
      so the index can show "N known · M rumoured" without leaking unencountered creature names.
    """
    n = max(1, min(int(limit), 50))
    names = find(query, n)
    if intel is None:
        return {
            "items": [p for name in names if (p := player_bestiary_preview(name)) is not None],
            "validation_errors": authored_validation_errors(),
        }
    items: list[dict] = []
    for idx, name in enumerate(names):
        tier = int(intel.get(creature_slug(name), 0) or 0)
        if tier >= 1:
            proj = intel_projection(name, tier)
            if proj is not None:
                items.append(proj)
        else:
            # Unencountered: a blurred rumour row. We deliberately do NOT put the real creature
            # name on the wire. The name is the very thing progressive reveal withholds, so
            # emitting it — even though the viewer only renders "?????" — would leak the names
            # of as-yet-unencountered creatures matching the query to anyone reading the network
            # response (#263 redaction hygiene). Instead we send a stable, opaque render key (the
            # match index) so the client can key the row in React without learning the name. The
            # index carries no creature identity and is stable for a given query (``find`` returns
            # a deterministic sorted order); it is intentionally NOT a hash of the name — a hash
            # over the small, public, query-narrowed SRD candidate set would be trivially
            # reversible, so an index is both simpler and more honestly opaque.
            items.append({"id_hint": idx, "tier": 0, "unknown": True})
    return {"items": items, "validation_errors": authored_validation_errors()}


def _entry_name(entry: dict) -> str:
    if entry.get("content_origin") == "authored":
        return entry["fields"]["name"]
    return entry["row"]["fields"]["name"]


def find(query: str, limit: int = 10) -> list[str]:
    """Creature names matching `query` (substring, case-insensitive), sorted. Deduped
    against the index (first-wins), so a pack's same-named creature never appears twice."""
    q = query.strip().lower()
    names = sorted(_entry_name(e) for e in _index().values())
    if not q:
        return names[:limit]
    return [n for n in names if q in n.lower()][:limit]


def _token_prefix_matches(name: str) -> list[str]:
    """Candidate names where EVERY whitespace token of `name` is a prefix of a DISTINCT token in
    the candidate — a forgiving near-miss fallback ('Cult Fanatic' -> 'Cultist Fanatic', 'Goblin
    Boss' -> 'Goblin Boss'). Conservative: requires all query tokens to land, so it only resolves
    a genuine near-miss, not an arbitrary partial."""
    q_tokens = [t for t in name.strip().lower().split() if t]
    if not q_tokens:
        return []
    out = set()
    for e in _index().values():
        cand = _entry_name(e)
        c_tokens = cand.lower().split()
        used = [False] * len(c_tokens)
        ok = True
        for qt in q_tokens:
            hit = next((i for i, ct in enumerate(c_tokens) if not used[i] and ct.startswith(qt)), None)
            if hit is None:
                ok = False
                break
            used[hit] = True
        if ok:
            out.add(cand)
    return sorted(out)


# 2014-SRD names a DM reaches for that the 2024 SRD (srd524) RENAMED — map them to the
# existing 2024 statblock so a natural choice resolves instead of dead-ending. These are
# pure name aliases to creatures already in the bestiary (no new content, no stat numbers).
_ALIASES = {
    "thug": "Tough",          # 2014 Thug -> 2024 Tough (CR 1/2 hired muscle)
    "veteran": "Warrior Veteran",
    "bandit captain": "Bandit Captain",
}


def resolve(name: str) -> Optional[str]:
    """Resolve a loose creature name to a canonical bestiary name, or None.

    Tries exact match, then a known 2014->2024 rename alias ('Thug' -> 'Tough'), then
    ``<name> Warrior`` (the 2024 SRD's baseline statblock for many humanoids — e.g.
    'Goblin' -> 'Goblin Warrior'), then a unique substring match, then a unique
    TOKEN-PREFIX match ('Cult Fanatic' -> 'Cultist Fanatic'; a QA finding where a
    near-miss name returned no match). Returns None when ambiguous or absent (the
    caller should then offer ``find()`` suggestions)."""
    key = name.strip().lower()
    idx = _index()
    if key in idx:
        return _entry_name(idx[key])
    alias = _ALIASES.get(key)
    if alias and alias.strip().lower() in idx:
        return _entry_name(idx[alias.strip().lower()])
    warrior = f"{key} warrior"
    if warrior in idx:
        return _entry_name(idx[warrior])
    matches = find(name)
    if len(matches) == 1:
        return matches[0]
    tok = _token_prefix_matches(name)
    return tok[0] if len(tok) == 1 else None


def count() -> int:
    return len(_index())
