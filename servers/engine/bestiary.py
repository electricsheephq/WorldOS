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

_DIR = Path(__file__).resolve().parents[2] / "data" / "srd" / "srd524"

_ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")


@functools.lru_cache(maxsize=None)
def _raw_creatures() -> list[dict]:
    return json.loads((_DIR / "Creature.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=None)
def _actions_by_parent() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in json.loads((_DIR / "CreatureAction.json").read_text(encoding="utf-8")):
        f = row.get("fields", {})
        parent = f.get("parent")
        if parent:
            out.setdefault(parent, []).append(
                {"name": f.get("name", ""), "desc": f.get("desc", ""),
                 "action_type": f.get("action_type", "ACTION")}
            )
    return out


@functools.lru_cache(maxsize=None)
def _index() -> dict[str, dict]:
    """name (lowercased) -> the raw creature row (fields + pk)."""
    return {c["fields"]["name"].lower(): c for c in _raw_creatures() if c.get("fields", {}).get("name")}


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
    row = _index().get(name.strip().lower())
    if row is None:
        return None
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
        "actions": _actions_by_parent().get(row.get("pk"), []),
    }


def find(query: str, limit: int = 10) -> list[str]:
    """Creature names matching `query` (substring, case-insensitive), sorted."""
    q = query.strip().lower()
    names = sorted(c["fields"]["name"] for c in _raw_creatures() if c.get("fields", {}).get("name"))
    if not q:
        return names[:limit]
    return [n for n in names if q in n.lower()][:limit]


def count() -> int:
    return len(_index())
