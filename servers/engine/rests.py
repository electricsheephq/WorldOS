"""Short/long rest mechanics (SRD 5.2). Pure helpers operating on a Character;
the engine's rest tools wrap them with the campaign lock + persistence.
"""

from __future__ import annotations

import re

import srd_tables
from models import Ability, Character, Condition, DeathSaves

_HIT_DIE = re.compile(r"\d*d(\d+)")


def _hit_die_size(ch: Character) -> int:
    m = _HIT_DIE.search(ch.hit_dice or "")
    return int(m.group(1)) if m else 8  # sensible default if unset


def _is_single_class_warlock(ch: Character) -> bool:
    return len(ch.classes) == 1 and ch.classes[0].name.lower() == "warlock"


def _restore_class_resources(ch: Character, recharges: tuple[str, ...]) -> list[str]:
    """Refill (set used=0) every class-resource pool whose recharge is in
    `recharges`. Returns the resource ids restored. "none"-recharge pools are never
    touched here (the DM restores them manually). Mutates ch."""
    restored: list[str] = []
    for res_id, res in ch.class_resources.items():
        if res.recharge in recharges and res.used:
            res.used = 0
            restored.append(res_id)
    return restored


def short_rest(ch: Character, dice_to_spend: int, roll_fn) -> dict:
    """Spend up to dice_to_spend Hit Dice to heal (each: 1d{hit die} + CON mod,
    floored at 0 per die). A single-class Warlock recovers its (pact) spell slots
    (multiclass Warlock pact recovery is not modeled). Requires >=1 HP (SRD: a
    creature at 0 HP can't benefit from a rest). Multiclass mixed hit dice collapse
    to the single stored die size (model limitation). Mutates ch."""
    if ch.current_hp < 1:
        raise ValueError("must have at least 1 HP to benefit from a rest")
    die = _hit_die_size(ch)
    con = ch.ability_modifier(Ability.CON)
    spend = max(0, min(dice_to_spend, ch.hit_dice_remaining))
    rolls: list[int] = []
    healed = 0
    for _ in range(spend):
        r = roll_fn(f"1d{die}")
        rolls.append(r.total)
        healed += max(0, r.total + con)
    ch.hit_dice_remaining -= spend
    before = ch.current_hp
    ch.current_hp = min(ch.max_hp, ch.current_hp + healed)

    pact_recovered = False
    if _is_single_class_warlock(ch):
        pact = srd_tables.warlock_pact_slots(ch.classes[0].level)
        if pact and pact["level"] in ch.spell_slots:
            ch.spell_slots[pact["level"]].used = 0  # restore only the pact slot
            pact_recovered = True

    # Short-rest class-resource pools (Ki, Channel Divinity for clerics, Second
    # Wind, Action Surge, Wild Shape, post-L5 Bardic Inspiration) refresh now.
    resources_restored = _restore_class_resources(ch, ("short",))

    return {
        "dice_spent": spend,
        "rolls": rolls,
        "hp_restored": ch.current_hp - before,
        "hp": f"{ch.current_hp}/{ch.max_hp}",
        "hit_dice_remaining": ch.hit_dice_remaining,
        "pact_slots_recovered": pact_recovered,
        "resources_restored": resources_restored,
    }


def long_rest(ch: Character) -> dict:
    """A full long rest: HP to max, recover half total Hit Dice (min 1; RAW is
    floor(total/2) — the min-1 is a deliberate house rule), reset all spell slots,
    reduce exhaustion by 1, and end the dying state. Requires >=1 HP and not dead
    (SRD: a creature at 0 HP can't benefit from a rest). Mutates ch."""
    if ch.dead:
        raise ValueError("cannot take a long rest while dead")
    if ch.current_hp < 1:
        raise ValueError("must have at least 1 HP to benefit from a long rest")
    total_hd = ch.total_level
    recovered = max(1, total_hd // 2)
    ch.hit_dice_remaining = min(total_hd, ch.hit_dice_remaining + recovered)
    ch.current_hp = ch.max_hp
    ch.exhaustion = max(0, ch.exhaustion - 1)
    for slot in ch.spell_slots.values():
        slot.used = 0
    ch.conditions = [c for c in ch.conditions if c != Condition.UNCONSCIOUS]
    ch.death_saves = DeathSaves()
    ch.stable = False
    # A long rest refreshes BOTH short- and long-recharge pools (Rage, Lay on
    # Hands, Sorcery Points, Channel Divinity, Ki, …); "none" pools are untouched.
    resources_restored = _restore_class_resources(ch, ("short", "long"))
    return {
        "hp": f"{ch.current_hp}/{ch.max_hp}",
        "hit_dice_recovered": recovered,
        "hit_dice_remaining": ch.hit_dice_remaining,
        "exhaustion": ch.exhaustion,
        "spell_slots_restored": True,
        "resources_restored": resources_restored,
    }
