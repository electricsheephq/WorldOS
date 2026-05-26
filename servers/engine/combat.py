"""Combat logic for ClawDnD — pure, testable helpers operating on Character.

The MCP tools in server.py wrap these with the campaign lock + persistence. All
SRD 5.2 rules (damage order, death saves, concentration, condition hooks) live
here so they can be unit-tested without MCP plumbing. Dice come from dice.py;
crit damage is produced by doubling the dice count in the damage expression
(double_dice) since dice.roll has no crit mode.
"""

from __future__ import annotations

import re

from models import Ability, ActiveEffect, Character, Condition, DeathSaves, Zone

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


# --- Attack-action economy (turn ownership + attacks-per-action) -----------
# Pure helpers the attack() tool wraps. They answer: given whose turn it is and
# how many attacks have already resolved this turn, is THIS attack legal, and if
# so does it consume a fresh Attack action? Reactions (opportunity attacks) are
# NOT routed through here — they act off-turn and are gated by reaction_used.


def attacks_allowed(extra_attacks: int, surge_actions: int, multiattack: int = 0) -> int:
    """How many ATTACK ROLLS the current combatant may make this turn under the
    Attack action(s) available. One Attack action grants ``extra_attacks + 1``
    attacks (a level-1 fighter -> 1, an Extra-Attack fighter -> 2); each Action
    Surge spent this turn (``surge_actions``) grants another whole Attack action,
    i.e. another ``extra_attacks + 1`` attacks. Clamps negatives to 0.

    ``multiattack`` (default 0): the number of attacks granted by a monster's
    Multiattack stat-block entry. When >0 it raises the per-action ceiling to
    max(extra_attacks+1, multiattack) so a Bandit Captain (multiattack=2,
    extra_attacks=0) is allowed 2 attacks, not 1. PCs leave multiattack=0 so
    their behaviour is byte-identical to before."""
    per_action = max(max(0, int(extra_attacks)) + 1, max(0, int(multiattack)))
    return per_action * (1 + max(0, int(surge_actions)))


def check_action_attack(
    *,
    is_current: bool,
    attacks_made: int,
    extra_attacks: int,
    surge_actions: int,
    multiattack: int = 0,
) -> tuple[bool, str]:
    """Decide whether a NON-reaction (action) attack by ``is_current`` combatant is
    legal given how many attacks already resolved this turn. Returns
    ``(ok, reason)``; reason is "" when ok. The caller (attack()) only invokes this
    for an on/off-turn action attack — reactions bypass it entirely (gated by
    reaction_used so opportunity attacks legitimately happen off-turn).

    Rules enforced:
      * an action attack must be made by the CURRENT combatant (turn ownership) —
        otherwise rejected (the QA defect: Kield attacked on Renn's turn);
      * the total attacks this turn may not exceed ``attacks_allowed`` — a 2nd
        attack with no Extra Attack and no Action Surge is rejected (the QA defect:
        two full attacks in one round); a fighter with extra_attacks makes its
        allowed multiple attacks under the one action; a spent Action Surge
        (surge_actions>0) grants the extra attacks for a 2nd action.

    ``multiattack`` (default 0): pass the monster's Multiattack count to raise the
    per-action ceiling for stat-block Multiattack creatures (see attacks_allowed).
    Zero leaves PC Extra-Attack / Action-Surge behaviour byte-identical to before."""
    if not is_current:
        return False, (
            "it is not this creature's turn — an attack as your action is only legal "
            "on your own turn (an off-turn melee strike is a reaction/opportunity "
            "attack; track it with use_action(kind='reaction'))"
        )
    allowed = attacks_allowed(extra_attacks, surge_actions, multiattack)
    if attacks_made >= allowed:
        ma = max(0, int(multiattack))
        if ma > 0:
            return False, (
                f"this creature's Multiattack grants {ma} attack(s) per turn; "
                f"{attacks_made} already made this turn."
            )
        if surge_actions <= 0 and int(extra_attacks) <= 0:
            return False, (
                "already attacked this turn — one Attack action grants a single "
                "attack without the Extra Attack feature. Make another action "
                "available first (Action Surge via use_resource(resource="
                "'action_surge')) to attack again."
            )
        return False, (
            f"no attacks left this turn — {allowed} attack(s) allowed "
            f"(extra_attacks={int(extra_attacks)}, action surges={int(surge_actions)}), "
            f"{attacks_made} already made. Spend an Action Surge "
            f"(use_resource(resource='action_surge')) for another Attack action."
        )
    return True, ""


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
    ch.active_effects = [eff for eff in ch.active_effects if not eff.concentration]
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
        # ...and its engine-tracked effect (kept consistent: one source of truth).
        ch.active_effects = [eff for eff in ch.active_effects if not eff.concentration]
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


def apply_hp_set_transition(ch: Character, was_down: bool) -> dict:
    """Apply the SAME downed/wake/concentration transition the combat path uses, for a manual
    HP set (set_hp) — `ch.current_hp` has ALREADY been set + clamped to 0..max_hp; `was_down`
    is whether it was at 0 BEFORE the set. One source of truth with apply_damage/apply_healing:
      * dropped TO 0 (was up): clear concentration + its twin effect; monsters/NPCs die outright,
        PCs/companions go unconscious + dying (fresh death saves, not stable);
      * raised FROM 0 (was down, now >0): wake — fresh death saves, not stable, drop unconscious
        (mirrors apply_healing's un-down; does NOT touch `dead`, like apply_healing).
    Mutates ch; returns the resulting status() (with a `revived` flag on a wake)."""
    if ch.current_hp == 0 and not was_down and not ch.dead:
        if ch.kind in ("monster", "npc"):
            _die(ch)  # monsters/NPCs die outright at 0 HP (no death saves) — same as apply_damage
        else:
            ch.concentration = None  # 0 HP ends concentration (no save)...
            expire_concentration_effects(ch)  # ...and drops its engine-tracked twin effect
            ch.death_saves = DeathSaves()
            ch.stable = False
            _ensure_unconscious(ch)
    revived = False
    if ch.current_hp > 0 and was_down and not ch.dead:
        ch.death_saves = DeathSaves()
        ch.stable = False
        ch.conditions = [c for c in ch.conditions if c != Condition.UNCONSCIOUS]
        revived = True
    return {"revived": revived, **status(ch)}


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


# --- Timed spell effects (auto-expiry) -------------------------------------
# The engine tracks Character.active_effects so timed spells (Bless 10 rounds,
# Hex 1 hour, Mage Armor 8h) auto-expire instead of relying on the DM. Pure
# helpers operating on a Character; the MCP tools (next_turn, advance_time,
# long/short_rest, travel_to) wrap them with the lock + persistence and surface
# the returned names as `expired_effects`. See models.ActiveEffect for the shape
# and the unit mapping (1 round = 6s; 1 minute = 10 rounds; hour/day = clock).

# in-world phases per day — mirrors travel.PHASES; combat.py stays I/O-free, so we
# take the *index* of the current phase from the caller rather than import travel.
PHASES_PER_DAY = 4


def _commit_expiry(ch: Character, surviving: list[ActiveEffect], expired: list[ActiveEffect]) -> list[str]:
    """Apply an expiry result: keep `surviving`, and if any `expired` effect was the
    concentration twin, clear `ch.concentration` too — when a concentration spell's
    DURATION runs out the spell is over, so the field and the effect stay one source of
    truth (the inverse of expire_concentration_effects). Returns the expired names."""
    ch.active_effects = surviving
    if any(eff.concentration for eff in expired):
        ch.concentration = None
    return [eff.name for eff in expired]


def tick_round_effects(ch: Character) -> list[str]:
    """Decrement every round/minute-scale effect by ONE combat round and drop those
    that hit 0. Hour/day-scale effects (clock-based) are untouched here. Returns the
    names of effects that just expired (for the DM to narrate). Mutates ch — and clears
    concentration if the expiring effect was a concentration spell."""
    expired: list[ActiveEffect] = []
    surviving: list[ActiveEffect] = []
    for eff in ch.active_effects:
        if eff.scale in ("rounds", "minutes"):
            eff.rounds_remaining -= 1
            if eff.rounds_remaining <= 0:
                expired.append(eff)
                continue
        surviving.append(eff)
    return _commit_expiry(ch, surviving, expired)


def expire_clock_effects(
    ch: Character, day: int, phase_index: int, *, long_rest: bool = False
) -> list[str]:
    """Expire effects whose in-world time has elapsed, given the NEW clock
    (`day`, `phase_index` into the 4-phase day). Mutates ch; returns expired names.

    Rules (a time-of-day phase ≫ minutes, so anything finer than an hour ends the
    moment the clock moves at all):
      * round/minute-scale  -> expire on ANY phase advance out of combat;
      * hour/day-scale      -> expire when (day, phase_index) reaches/passes the
                               effect's stored (expires_day, expires_phase_index);
      * `long_rest=True` (an overnight ~8h) additionally expires every effect flagged
        `until_long_rest` (the hour-scale buffs — Mage Armor, Aid, Longstrider)."""
    expired: list[ActiveEffect] = []
    surviving: list[ActiveEffect] = []
    for eff in ch.active_effects:
        gone = False
        if eff.scale in ("rounds", "minutes"):
            gone = True  # a phase passed; minute/round effects don't survive it
        elif long_rest and eff.until_long_rest:
            gone = True  # the overnight ends hour-scale buffs regardless of phase math
        elif (day, phase_index) >= (eff.expires_day, eff.expires_phase_index):
            gone = True  # the clock has reached/passed the effect's deadline
        if gone:
            expired.append(eff)
        else:
            surviving.append(eff)
    return _commit_expiry(ch, surviving, expired)


def expire_short_rest_effects(ch: Character, day: int, phase_index: int) -> list[str]:
    """Expire effects that a SHORT REST (~1 in-world hour) ends: every minute/round-scale
    effect (sub-hour — it can't survive an hour of rest) plus any hour/day-scale effect
    whose absolute clock deadline has ALREADY passed. Hour-scale buffs not yet at their
    deadline (e.g. an 8h Mage Armor) SURVIVE a short rest. Mutates ch; returns expired
    names. (A short rest doesn't move the campaign clock, so it can't cross a phase
    boundary on its own — hence the explicit sub-hour rule.)"""
    expired: list[ActiveEffect] = []
    surviving: list[ActiveEffect] = []
    for eff in ch.active_effects:
        if eff.scale in ("rounds", "minutes"):
            expired.append(eff)
        elif (day, phase_index) >= (eff.expires_day, eff.expires_phase_index):
            expired.append(eff)
        else:
            surviving.append(eff)
    return _commit_expiry(ch, surviving, expired)


def expire_concentration_effects(ch: Character) -> list[str]:
    """Drop every concentration-flagged ActiveEffect — call this the instant
    `ch.concentration` is cleared (failed save / incapacitation / 0 HP / death) so the
    engine-tracked effect stays a faithful twin of the concentration field (one source
    of truth). Mutates ch; returns the names removed. A no-op when there are none."""
    expired = [eff.name for eff in ch.active_effects if eff.concentration]
    if expired:
        ch.active_effects = [eff for eff in ch.active_effects if not eff.concentration]
    return expired


# --- Grapple / Shove (SRD 5.2 / 2024 Unarmed Strike options) ---------------
# In 2024 D&D, Grapple and Shove are options of the Unarmed Strike attack:
# the target makes a STR or DEX saving throw (their choice / attacker's choice for
# engine default) against DC = 8 + attacker's Strength modifier + proficiency bonus.
# Source: ClassFeature srd-2024_monk_martial-arts ("the Grapple or Shove option of
# your Unarmed Strike … save DC"); ConditionDescription srd-2024_grappled;
# CreatureAction escape DC pattern ("escape DC = <same formula>").


def grapple_save_dc(attacker: Character) -> int:
    """SRD 2024 Grapple/Shove save DC: 8 + attacker's STR modifier + proficiency bonus.
    Pure; does not mutate anything."""
    return 8 + attacker.ability_modifier(Ability.STR) + attacker.proficiency_bonus


def roll_grapple_save(target: Character, save_ability: Ability) -> tuple[int, int, int]:
    """Return (bonus, natural, total) for the target's STR or DEX saving throw —
    caller passes the die roll object and resolves hit/miss. Pure helper: no state mutation.
    This is factored out so tests can inspect the bonus without needing real dice.

    Enforces SRD auto-fail: a paralyzed/petrified/stunned/unconscious target
    automatically fails a STR or DEX save (returned as (bonus, -1, -999) signal —
    the server layer reads total < dc). Returns the bonus only; the actual roll is the
    server's responsibility (sole-writer)."""
    bonus = target.saving_throw_bonus(save_ability)
    return bonus


def best_save_ability(target: Character) -> Ability:
    """Return whichever of STR/DEX gives the target the higher saving throw bonus.
    Ties go to STR (a deliberate default; callers may override with save_ability)."""
    str_bonus = target.saving_throw_bonus(Ability.STR)
    dex_bonus = target.saving_throw_bonus(Ability.DEX)
    return Ability.DEX if dex_bonus > str_bonus else Ability.STR
