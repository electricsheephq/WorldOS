"""Combat logic for ClawDnD — pure, testable helpers operating on Character.

The MCP tools in server.py wrap these with the campaign lock + persistence. All
SRD 5.2 rules (damage order, death saves, concentration, condition hooks) live
here so they can be unit-tested without MCP plumbing. Dice come from dice.py;
crit damage is produced by doubling the dice count in the damage expression
(double_dice) since dice.roll has no crit mode.
"""

from __future__ import annotations

import re

from models import Character, Condition, DeathSaves, Zone

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


def is_incapacitated(ch: Character) -> bool:
    """True if the creature can take no actions, bonus actions, or reactions — the SRD
    Incapacitated condition and the conditions that include it (Stunned, Paralyzed,
    Petrified, Unconscious)."""
    return bool(INCAPACITATING & set(ch.conditions))


# Saving throws a condition forces: paralyzed/petrified/stunned/unconscious AUTO-FAIL STR & DEX
# saves; restrained gives DISADVANTAGE on DEX saves (SRD condition rules).
SAVE_AUTOFAIL = {Condition.PARALYZED, Condition.PETRIFIED, Condition.STUNNED, Condition.UNCONSCIOUS}


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


def _die(ch: Character) -> None:
    """Mark a creature dead — and DEATH SUPERSEDES ALL CONDITIONS. Clears conditions (incl. the
    'unconscious' applied while dying), concentration, and stable. A dead record left carrying a
    stale 'unconscious' is an inconsistent state downstream reads trip on (QA finding)."""
    ch.dead = True
    ch.stable = False
    ch.concentration = None
    ch.conditions = []


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


def _damage_type_matches(dt: str, entries: list[str]) -> bool:
    """A damage type matches a creature's resistance/immunity entry if it appears
    in the entry text (entries may be phrases like 'piercing from nonmagical attacks')."""
    return bool(dt) and any(dt in e.lower() for e in entries)


def apply_damage(
    ch: Character, amount: int, crit: bool = False, half: bool = False, damage_type: str = ""
) -> dict:
    """Apply damage with full SRD order: halve-on-save -> apply the target's
    resistance/immunity/vulnerability for `damage_type` -> temp HP absorb -> floor
    at 0 -> massive-damage instant death -> dying transition -> death-save failure
    if hit while already down -> concentration-check DC. If half=True (a successful
    save vs a 'half on save' spell), the incoming amount is halved (rounded down)
    first; per SRD, resistance/vulnerability apply after other modifiers. Mutates ch."""
    amount = max(0, amount)
    if half:
        amount //= 2
    dt = damage_type.strip().lower()
    if amount > 0 and dt:
        if _damage_type_matches(dt, ch.damage_immunities):
            amount = 0
        elif _damage_type_matches(dt, ch.damage_vulnerabilities):
            amount *= 2
        elif _damage_type_matches(dt, ch.damage_resistances):
            amount //= 2
    if ch.dead:
        return {"absorbed": 0, "damage_to_hp": 0, "concentration_dc": None, **status(ch)}

    # Damage that survived resistance/immunity is what counts for a concentration check —
    # even when temp HP absorbs all of it (you still TOOK the damage; SRD/Sage Advice).
    damage_taken = amount
    absorbed = min(ch.temp_hp, amount)
    ch.temp_hp -= absorbed
    to_hp = amount - absorbed
    hp_before = ch.current_hp
    ch.current_hp = max(0, hp_before - to_hp)

    if ch.current_hp == 0:
        if ch.kind in ("monster", "npc"):
            # Monsters and NPCs die outright at 0 HP — death saves are a
            # player-character (and companion) mechanic in the SRD.
            _die(ch)
        elif hp_before > 0:
            overkill = to_hp - hp_before  # damage remaining after reaching 0
            if overkill >= ch.max_hp:  # massive damage -> instant death
                _die(ch)
            else:  # newly dying
                ch.death_saves = DeathSaves()
                ch.stable = False
                _ensure_unconscious(ch)
        else:  # already at 0 (dying or stable) and took a hit
            ch.stable = False
            _ensure_unconscious(ch)
            if to_hp >= ch.max_hp:  # SRD: damage >= HP max while at 0 -> instant death
                _die(ch)
            else:
                ch.death_saves.failures += 2 if crit else 1
                if ch.death_saves.failures >= 3:
                    _die(ch)

    conc_dc = None
    if ch.current_hp == 0:
        ch.concentration = None  # unconsciousness or death ends concentration (no save)
    elif damage_taken > 0 and ch.concentration:
        # DC is half the damage TAKEN (min 10) — temp HP absorbing it doesn't dodge the check.
        conc_dc = max(10, damage_taken // 2)
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
        _die(ch)
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


# --- Tactical zones (S2.7) -------------------------------------------------
# A light positional model: combat scenes can declare named regions with
# adjacency. The engine uses zones for melee range and movement; everything is
# inert when no zones are declared (theater-of-the-mind default).


def adjacent_zones(zones: list[Zone], name: str) -> set[str]:
    """Names directly reachable from zone ``name`` (the zone's own ``adjacent``
    list, plus any zone that lists ``name`` — adjacency is treated as symmetric so
    the DM only has to wire each edge once). Empty if the zone isn't declared."""
    out: set[str] = set()
    for z in zones:
        if z.name == name:
            out.update(z.adjacent)
        elif name in z.adjacent:
            out.add(z.name)
    return out


def zones_in_melee(zones: list[Zone], a: str, b: str) -> bool:
    """True if two zone names are in melee reach of each other: the SAME zone, or
    directly ADJACENT on the zone graph. (Empty names — unplaced combatants — are
    never in reach, so the caller can warn rather than silently allow.)"""
    if not a or not b:
        return False
    if a == b:
        return True
    return b in adjacent_zones(zones, a)


def melee_range_warning(
    zones: list[Zone],
    attacker: Character,
    target: Character,
    attacker_zone: str,
    target_zone: str,
) -> str:
    """A human-readable out-of-range note for a MELEE attack, or "" if the attack
    is in reach (or no zones are declared, i.e. theater-of-the-mind — never gate).

    The positional state lives on the COMBATANT records, so the caller passes the
    attacker's and target's current zone names (and the Characters only for naming
    the message). ADDITIVE / non-blocking: the engine surfaces this for the DM to
    adjudicate; it never hard-blocks an attack. Ranged attacks reach any zone and
    are not checked by callers."""
    if not zones:
        return ""  # no positional model in play — nothing to gate
    if not attacker_zone or not target_zone:
        return ""  # an un-placed combatant: don't invent a constraint
    if zones_in_melee(zones, attacker_zone, target_zone):
        return ""
    return (
        f"{attacker.name} (in {attacker_zone!r}) is not in melee reach of "
        f"{target.name} (in {target_zone!r}) — those zones are neither the same "
        f"nor adjacent. A melee attack normally requires closing the distance "
        f"(move_to_zone) first; this is advisory, the attack was still resolved."
    )
