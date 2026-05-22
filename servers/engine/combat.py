"""Combat logic for ClawDnD — pure, testable helpers operating on Character.

The MCP tools in server.py wrap these with the campaign lock + persistence. All
SRD 5.2 rules (damage order, death saves, concentration, condition hooks) live
here so they can be unit-tested without MCP plumbing. Dice come from dice.py;
crit damage is produced by doubling the dice count in the damage expression
(double_dice) since dice.roll has no crit mode.
"""

from __future__ import annotations

import re

from models import Character, Condition, DeathSaves

_DICE = re.compile(r"(\d*)d(\d+)", re.IGNORECASE)

# Conditions that break concentration / prevent actions.
INCAPACITATING = {
    Condition.INCAPACITATED,
    Condition.STUNNED,
    Condition.PARALYZED,
    Condition.PETRIFIED,
    Condition.UNCONSCIOUS,
}
_ATTACKER_DISADV = {
    Condition.BLINDED,
    Condition.FRIGHTENED,
    Condition.POISONED,
    Condition.PRONE,
    Condition.RESTRAINED,
}
_TARGET_GIVES_ADV = {
    Condition.BLINDED,
    Condition.PARALYZED,
    Condition.RESTRAINED,
    Condition.STUNNED,
    Condition.UNCONSCIOUS,
}


def double_dice(expr: str) -> str:
    """Double the DICE count of a damage expression for a critical hit, leaving
    flat modifiers unchanged (SRD: double the dice, not the ability modifier).
    '1d8+3' -> '2d8+3'; '2d6' -> '4d6'; '1d10+1d6+4' -> '2d10+2d6+4'.
    """
    return _DICE.sub(lambda m: f"{(int(m.group(1) or 1)) * 2}d{m.group(2)}", expr)


def attack_modifiers(attacker: Character, target: Character, is_ranged: bool = False) -> tuple[bool, bool]:
    """(advantage, disadvantage) implied by the combatants' conditions. The caller
    combines these with any explicit flags; dice.roll cancels adv+disadv. A prone
    target grants advantage to melee attackers and disadvantage to ranged ones."""
    adv = disadv = False
    ac = set(attacker.conditions)
    tc = set(target.conditions)
    if ac & _ATTACKER_DISADV:
        disadv = True
    if Condition.INVISIBLE in ac:
        adv = True
    if tc & _TARGET_GIVES_ADV:
        adv = True
    if Condition.INVISIBLE in tc:
        disadv = True
    if Condition.PRONE in tc:
        adv = adv or (not is_ranged)
        disadv = disadv or is_ranged
    return adv, disadv


def clears_concentration(conditions) -> bool:
    return bool(INCAPACITATING & set(conditions))


# A melee hit within 5 ft of an Unconscious or Paralyzed creature is automatically
# a Critical Hit (SRD). We have no distance model, so a non-ranged attack is
# treated as being within 5 ft.
_AUTO_CRIT_TARGET = {Condition.UNCONSCIOUS, Condition.PARALYZED}


def melee_auto_crit(target: Character, is_ranged: bool = False) -> bool:
    """True if a landing melee attack against ``target`` is automatically a crit."""
    return (not is_ranged) and bool(_AUTO_CRIT_TARGET & set(target.conditions))


def _ensure_unconscious(ch: Character) -> None:
    if Condition.UNCONSCIOUS not in ch.conditions:
        ch.conditions.append(Condition.UNCONSCIOUS)


def status(ch: Character) -> dict:
    dying = ch.current_hp == 0 and not ch.dead and not ch.stable
    return {
        "hp": f"{ch.current_hp}/{ch.max_hp}",
        "current_hp": ch.current_hp,
        "temp_hp": ch.temp_hp,
        "conditions": [c.value for c in ch.conditions],
        "dying": dying,
        "stable": ch.stable,
        "dead": ch.dead,
    }


def apply_damage(ch: Character, amount: int, crit: bool = False, half: bool = False) -> dict:
    """Apply damage with full SRD order: temp HP absorb -> floor at 0 -> massive-
    damage instant death -> dying transition -> death-save failure if hit while
    already down -> concentration-check DC. If half=True (a successful save vs a
    'half on save' spell), the incoming amount is halved (rounded down) first.
    Mutates ch."""
    amount = max(0, amount)
    if half:
        amount //= 2
    if ch.dead:
        return {"absorbed": 0, "damage_to_hp": 0, "concentration_dc": None, **status(ch)}

    absorbed = min(ch.temp_hp, amount)
    ch.temp_hp -= absorbed
    to_hp = amount - absorbed
    hp_before = ch.current_hp
    ch.current_hp = max(0, hp_before - to_hp)

    if ch.current_hp == 0:
        if hp_before > 0:
            overkill = to_hp - hp_before  # damage remaining after reaching 0
            if overkill >= ch.max_hp:  # massive damage -> instant death
                ch.dead = True
                ch.stable = False
                _ensure_unconscious(ch)
            else:  # newly dying
                ch.death_saves = DeathSaves()
                ch.stable = False
                _ensure_unconscious(ch)
        else:  # already at 0 (dying or stable) and took a hit
            ch.stable = False
            _ensure_unconscious(ch)
            if to_hp >= ch.max_hp:  # SRD: damage >= HP max while at 0 -> instant death
                ch.dead = True
            else:
                ch.death_saves.failures += 2 if crit else 1
                if ch.death_saves.failures >= 3:
                    ch.dead = True

    conc_dc = None
    if ch.current_hp == 0:
        ch.concentration = None  # unconsciousness or death ends concentration (no save)
    elif to_hp > 0 and ch.concentration:
        conc_dc = max(10, to_hp // 2)
    return {"absorbed": absorbed, "damage_to_hp": to_hp, "concentration_dc": conc_dc, **status(ch)}


def apply_healing(ch: Character, amount: int) -> dict:
    amount = max(0, amount)
    if ch.dead:
        return {"healed": 0, "revived": False, "note": "cannot heal a dead creature", **status(ch)}
    was_down = ch.current_hp == 0
    ch.current_hp = min(ch.max_hp, ch.current_hp + amount)
    revived = False
    if ch.current_hp > 0 and was_down:
        ch.death_saves = DeathSaves()
        ch.stable = False
        ch.conditions = [c for c in ch.conditions if c != Condition.UNCONSCIOUS]
        revived = True
    return {"healed": amount, "revived": revived, **status(ch)}


def resolve_death_save(ch: Character, roll) -> dict:
    """Apply a death-saving-throw roll (a plain 1d20 DiceRoll). nat20 -> 1 HP;
    nat1 -> two failures; >=10 success; <10 failure; 3 successes stabilize;
    3 failures die. Mutates ch."""
    if ch.dead:
        return {"result": "dead", **status(ch)}
    if ch.stable or ch.current_hp != 0:
        return {"result": "not_dying", **status(ch)}  # death saves only while at 0 and unstable
    if roll.crit:  # natural 20
        ch.current_hp = 1
        ch.death_saves = DeathSaves()
        ch.stable = False
        ch.conditions = [c for c in ch.conditions if c != Condition.UNCONSCIOUS]
        return {"result": "regain_1hp", "roll": roll.natural, **status(ch)}
    if roll.fumble:  # natural 1
        ch.death_saves.failures += 2
    elif roll.total >= 10:
        ch.death_saves.successes += 1
    else:
        ch.death_saves.failures += 1

    result = "pending"
    if ch.death_saves.failures >= 3:
        ch.dead = True
        result = "dead"
    elif ch.death_saves.successes >= 3:
        ch.stable = True
        ch.death_saves = DeathSaves()
        result = "stabilized"
    return {
        "result": result,
        "roll": roll.natural,
        "successes": ch.death_saves.successes,
        "failures": ch.death_saves.failures,
        **status(ch),
    }
