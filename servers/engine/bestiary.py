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
from pathlib import Path
from typing import Optional

import encounter

_ROOT = Path(__file__).resolve().parents[2] / "data" / "srd"
_PRIMARY = _ROOT / "srd524"  # canonical SRD 5.2 — always wins a name collision

_ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")


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
                    out[key] = {"src": d.name, "row": c}
    return out


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


def stat_block(name: str) -> Optional[dict]:
    """A flat, engine-shaped stat block for a creature by (case-insensitive) name,
    or None if unknown. Includes abilities, AC, HP, CR/XP, the damage
    resistance/immunity/vulnerability + condition-immunity lists, and the creature's
    actions/traits as text."""
    entry = _index().get(name.strip().lower())
    if entry is None:
        return None
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
        "actions": _actions_by_source_parent().get((entry["src"], row.get("pk")), []),
    }


def find(query: str, limit: int = 10) -> list[str]:
    """Creature names matching `query` (substring, case-insensitive), sorted. Deduped
    against the index (first-wins), so a pack's same-named creature never appears twice."""
    q = query.strip().lower()
    names = sorted(e["row"]["fields"]["name"] for e in _index().values())
    if not q:
        return names[:limit]
    return [n for n in names if q in n.lower()][:limit]


def resolve(name: str) -> Optional[str]:
    """Resolve a loose creature name to a canonical bestiary name, or None.

    Tries exact match, then ``<name> Warrior`` (the 2024 SRD's baseline statblock
    for many humanoids — e.g. 'Goblin' -> 'Goblin Warrior'), then a unique
    substring match. Returns None when ambiguous or absent (the caller should then
    offer ``find()`` suggestions)."""
    key = name.strip().lower()
    idx = _index()
    if key in idx:
        return idx[key]["row"]["fields"]["name"]
    warrior = f"{key} warrior"
    if warrior in idx:
        return idx[warrior]["row"]["fields"]["name"]
    matches = find(name)
    return matches[0] if len(matches) == 1 else None


def count() -> int:
    return len(_index())
