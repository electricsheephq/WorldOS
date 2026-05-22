"""Spellcasting helpers — load bundled SRD spell mechanics and resolve a cast's
effect (damage / heal expression) given the slot level used, the caster's level
(for cantrip scaling), and the casting modifier (for heals). Pure + testable;
the engine's cast_spell tool wraps these with slot/concentration state.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

_SPELLS_PATH = Path(__file__).resolve().parents[2] / "data" / "srd" / "spells.json"


@functools.lru_cache(maxsize=None)
def _all() -> dict:
    rows = json.loads(_SPELLS_PATH.read_text(encoding="utf-8"))
    return {r["name"].lower(): r for r in rows}


def spell_data(name: str) -> dict:
    s = _all().get(name.strip().lower())
    if s is None:
        raise ValueError(f"unknown spell {name!r}")
    return s


_SRD524_SPELLS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "srd" / "srd524" / "Spell.json"
)


@functools.lru_cache(maxsize=None)
def _srd524() -> dict:
    rows = json.loads(_SRD524_SPELLS_PATH.read_text(encoding="utf-8"))
    return {
        r["fields"]["name"].lower(): r["fields"]
        for r in rows
        if r.get("fields", {}).get("name")
    }


def srd_spell(name: str):
    """The structured SRD record for any spell (the full ~339-spell srd524 dump),
    or None. Unlike spell_data (the hand-authored spells with full damage/heal
    automation), this returns level / concentration / save ability / damage_roll /
    attack_roll / upcast text for ALL SRD spells — enough for cast_spell to spend
    the right slot, set concentration, and hand the DM the values to resolve with."""
    return _srd524().get(name.strip().lower())


def _parse_dice(expr: str) -> tuple[int, int]:
    """Parse the dice part of 'NdM(+K)' -> (N, M), ignoring any flat modifier."""
    body = expr.lower().split("+")[0].split("-")[0]
    n, _, sides = body.partition("d")
    return int(n or 1), int(sides)


def _scale_per_dart(per: str, darts: int) -> str:
    """'1d4+1' x darts -> '{darts}d4+{darts}'."""
    count, sides = _parse_dice(per)
    plus = int(per.split("+")[1]) if "+" in per else 0
    expr = f"{count * darts}d{sides}"
    total_plus = plus * darts
    if total_plus:
        expr += f"+{total_plus}"
    return expr


def resolve_effect(spell: dict, slot_level: int, caster_level: int, casting_mod: int) -> dict:
    """Compute a cast's mechanical effect. slot_level is the slot used (== spell
    level for cantrips); caster_level scales cantrips; casting_mod is added to
    heals. Returns a dict the DM applies via attack / apply_damage / apply_healing.
    """
    m = spell.get("mechanics", {})
    kind = m.get("kind", "utility")
    spell_level = spell.get("level", 0)
    extra = max(0, slot_level - spell_level)  # upcast steps above the spell's base level

    if kind == "attack":
        dmg = m.get("damage", "1d6")
        scaling = m.get("cantrip_scaling", {})
        for threshold in sorted((int(k) for k in scaling), reverse=True):
            if caster_level >= threshold:
                dmg = scaling[str(threshold)]
                break
        return {"kind": "attack", "damage": dmg, "damage_type": m.get("damage_type", "")}

    if kind == "auto":  # e.g. Magic Missile — auto-hit darts
        darts = m.get("darts", 1) + extra * m.get("upcast_darts_per_level", 0)
        per = m.get("per_dart", "1d4+1")
        return {
            "kind": "auto",
            "darts": darts,
            "per_dart": per,
            "damage": _scale_per_dart(per, darts),
            "damage_type": m.get("damage_type", ""),
        }

    if kind == "heal":
        count, sides = _parse_dice(m.get("heal", "1d8"))
        up = m.get("upcast_dice")
        if up:
            uc, _ = _parse_dice(up)
            count += extra * uc
        expr = f"{count}d{sides}"
        if m.get("add_casting_mod") and casting_mod:
            expr += f"+{casting_mod}" if casting_mod > 0 else str(casting_mod)
        return {"kind": "heal", "heal": expr}

    if kind == "save":
        count, sides = _parse_dice(m.get("damage", "1d6"))
        up = m.get("upcast_dice")
        if up:
            uc, _ = _parse_dice(up)
            count += extra * uc
        return {
            "kind": "save",
            "save_ability": m.get("save_ability", "dex"),
            "damage": f"{count}d{sides}",
            "on_save": m.get("on_save", "half"),
            "damage_type": m.get("damage_type", ""),
        }

    return {"kind": kind, "effect": m.get("effect", "")}  # buff / utility
