"""Engine-run combat: the auto-sequencing loop (Track 2c/2e).

See docs/roadmap/engine-combat-loop-design.md §3. This is the orchestrator that drives a
fight by repeatedly asking the pure monster-AI (combat_ai.pick_action) what an actor wants,
then applying that Intent through the EXISTING engine write verbs (server.attack /
cast_spell / move_to_* / use_action / next_turn). It is NOT exposed as an MCP tool (to stay
inside the 120 KB tool-schema budget — see qa/.../test_tool_schema_budget.py); it is a
Python entry-point the engine-only combat smoke and tests call directly.

SOLE-WRITER INVARIANT (load-bearing): this module introduces NO new write path. Every state
change goes through one of server.py's existing verbs, each of which takes its own
campaign_lock + save_campaign. The loop holds no lock and never mutates the Campaign — it
only READS a snapshot to build the CombatView, and the verbs are the single mutating path.

TWO MODES (owner-decided):
  - mode="live": auto-run ONLY hostile (opposite-team) monster/NPC turns; STOP at the first
    PC/companion turn and hand a per-round digest back to the DM. Companions + PCs stay
    DM/agent-driven live.
  - mode="test": run EVERYONE (random PCs + monsters) with no LLM, to a terminal state — the
    trustworthy mechanical signal independent of the LLM scorer.
"""

from __future__ import annotations

from typing import Optional
from dataclasses import replace

import combat_ai
import combat_grid
import dice as dice_mod
import spells as spells_mod
from combat_ai import (
    AbilityOption,
    AoeSpellOption,
    AttackOption,
    CombatantView,
    CombatView,
    Intent,
    SneakAttackOption,
    SpellOption,
)

# The monster-AI policy the LIVE/TEST loop drives non-PC turns with. "tactical-v2" (#1255 /
# grid-461 PR-D) engages the grid positioning pass (AoE / cover / flanking / terrain routing)
# when the fight is ON the grid, and is byte-identical to greedy-v1 off the grid. Set to
# "greedy-v1" to pin the simpler policy. A single home so the smoke + tests read the same value.
_MONSTER_POLICY = "tactical-v2"

# The two "sides". Party = the player-aligned team (PCs + companions); Enemy = monsters/NPCs.
_PARTY_KINDS = ("player", "companion")
_ENEMY_KINDS = ("monster", "npc")
# In LIVE mode the loop auto-runs ONLY these kinds and STOPS at any other turn.
_LIVE_AUTORUN_KINDS = _ENEMY_KINDS


def _side_of(kind: str) -> str:
    return "party" if kind in _PARTY_KINDS else "enemy"


def _alive(ch) -> bool:
    """A combatant still IN the fight: not dead and above 0 HP. A downed-but-not-dead PC at 0
    HP is not 'alive' for victory detection (it can't act), matching the ADR's 'no living
    combatants' terminal condition."""
    return (not getattr(ch, "dead", False)) and int(getattr(ch, "current_hp", 0)) > 0


def _living_sides(c) -> set:
    """The set of sides ({'party','enemy'}) that still have a living combatant in the order."""
    sides = set()
    for cb in c.combat.order:
        ch = c.characters.get(cb.character_id)
        if ch is not None and _alive(ch):
            sides.add(_side_of(ch.kind))
    return sides


# ── Build the read-only CombatView the AI decides over ───────────────────────────────

def _pc_attack_options(server, ch) -> tuple[AttackOption, ...]:
    """Synthesize a generic weapon strike for a PC/companion (TEST mode runs everyone). PCs
    have no stat-block attack line, so we use the sheet-derived melee numbers (proficiency +
    STR/finesse) and a longsword-equivalent 1d8 — enough to drive a TEST fight to a terminal
    state and exercise the real attack() resolution path. (LIVE mode never auto-runs PCs.)"""
    nums = server._combat_numbers(ch)
    atk_bonus = int(nums.get("melee_attack_bonus", 0))
    dmg_mod = int(nums.get("melee_damage_mod", 0))
    expr = f"1d8{'+' + str(dmg_mod) if dmg_mod >= 0 else str(dmg_mod)}" if dmg_mod else "1d8"
    return (AttackOption(name="Weapon", to_hit=atk_bonus, damage_expr=expr,
                         damage_type="slashing", reach_ft=5),)


def _monster_attack_options(server, ch, c) -> tuple[AttackOption, ...]:
    """The actor's AUTHORITATIVE attack lines from its bestiary stat block (the same data the
    DM sees), via server._monster_combat_entry. Falls back to the PC-style synthesis if no
    stat block resolves, so a custom monster still acts."""
    entry = server._monster_combat_entry(ch, c)
    if entry is None or not entry.get("attacks"):
        return _pc_attack_options(server, ch)
    opts: list[AttackOption] = []
    for a in entry["attacks"]:
        rolls = tuple(a.get("damage_rolls") or ())
        opts.append(AttackOption(
            name=str(a.get("name", "Attack")),
            to_hit=int(a.get("to_hit", 0)),
            damage_expr=str(a.get("damage", "") or ""),
            damage_type=str(a.get("damage_type", "") or ""),
            damage_rolls=rolls,
            reach_ft=5,  # PR-1 reach model: 5ft tokens (ranged gating is a later PR)
        ))
    return tuple(opts) if opts else _pc_attack_options(server, ch)


# ── Caster numbers + castable-spell discovery (v2.0a) ────────────────────────────────

def _caster_numbers(server, ch) -> tuple[int, int, int]:
    """The actor's (spell_attack_bonus, spell_save_dc, caster_level) — computed via the SAME
    primitives server.spell_save_dc uses (8 + prof + casting-mod / prof + casting-mod), so the
    AI's numbers match what the DM would see. A non-caster (no casting ability) returns (0,0,0)
    so the view stays byte-identical to today. v2.0a uses caster_level for heal/cantrip scaling;
    the DC + attack bonus land for v2.0b offensive scoring. Pure read — never mutates."""
    try:
        mod = server._casting_mod(ch)
    except Exception:
        return 0, 0, 0
    prof = int(getattr(ch, "proficiency_bonus", 0))
    caster_level = int(getattr(ch, "total_level", 0)) or 1
    return prof + mod, 8 + prof + mod, caster_level


def _spell_options(server, ch, caster_level: int, casting_mod: int) -> tuple[SpellOption, ...]:
    """Discover the actor's castable spells as SpellOptions (v2.0a). For each known/prepared
    spell that resolves to a curated record WITH an available slot (or a cantrip), build an option:
    HEALS get is_heal + the expected heal amount at the lowest available slot (avg dice + casting
    mod) + the casting-time / range; offensive damage/save/cantrip spells are carried best-effort
    (pick_action ignores them in v2.0a — additive). A non-caster / no-known-spells actor returns ()
    so the view is byte-identical to today. PURE — reads the sheet + the bundled spell registry,
    never mutates state, never casts."""
    names = list(getattr(ch, "spells_prepared", None) or getattr(ch, "spells_known", None) or ())
    if not names:
        return ()
    slots = getattr(ch, "spell_slots", {}) or {}
    # The available slot levels (a slot with maximum > used), low -> high.
    avail = sorted(lvl for lvl, s in slots.items() if int(s.maximum) - int(s.used) > 0)

    opts: list[SpellOption] = []
    seen: set[str] = set()
    for name in names:
        key = str(name).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            rec = spells_mod.spell_data(name)  # curated full-automation record (heals live here)
        except ValueError:
            continue  # srd524-only spell: no curated mechanics to score in v2.0a (offence = v2.0b)
        mech = rec.get("mechanics", {}) or {}
        kind = mech.get("kind", "utility")
        level = int(rec.get("level", 0) or 0)
        is_cantrip = level == 0
        # A leveled spell needs an available slot at >= its level; a cantrip needs none.
        slot_level = 0 if is_cantrip else next((lvl for lvl in avail if lvl >= level), None)
        if not is_cantrip and slot_level is None:
            continue  # no slot can pay for this leveled spell -> not castable this turn
        rng = _spell_range_ft(rec)
        is_bonus = "bonus action" in str(rec.get("casting_time", "")).lower()
        if kind == "heal":
            eff = spells_mod.resolve_effect(rec, int(slot_level or level), caster_level, casting_mod)
            heal_expr = eff.get("heal", "")
            heal_amt = float(dice_mod.average_total(heal_expr)) if heal_expr else 0.0
            opts.append(SpellOption(
                name=str(rec.get("name", name)), range_ft=rng, requires_slot=not is_cantrip,
                kind="heal", is_heal=True, heal_amount=heal_amt, slot_level=int(slot_level or 0),
                is_bonus_action=is_bonus,
            ))
        else:
            # Offensive / control / utility (v2.0b: SCORED by pick_action). resolve_effect already
            # applies caster-level cantrip scaling (Fire Bolt 1d10 -> 4d10) + slot upcast, so `value`
            # is the avg damage at THIS cast. We carry the effect `kind` (attack / auto / save) +
            # `on_save` so the AI scores attack-roll vs auto-hit vs save-for-half correctly, and the
            # spell's top-level `concentration` so the AI won't break a better active concentration.
            eff = spells_mod.resolve_effect(rec, int(slot_level or level), caster_level, casting_mod)
            eff_kind = str(eff.get("kind", "") or "")
            dmg = eff.get("damage", "")
            value = float(dice_mod.average_total(dmg)) if dmg else 0.0
            opts.append(SpellOption(
                name=str(rec.get("name", name)), value=value, range_ft=rng,
                save_ability=str(eff.get("save_ability", "") or ""),
                kind=eff_kind, on_save=str(eff.get("on_save", "") or ""),
                damage_type=str(eff.get("damage_type", "") or ""),
                concentration=bool(rec.get("concentration", False)),
                requires_slot=not is_cantrip, slot_level=int(slot_level or 0),
                is_bonus_action=is_bonus,
            ))
    return tuple(opts)


def _aoe_spell_options(server, ch, caster_level: int, casting_mod: int) -> tuple[AoeSpellOption, ...]:
    """Discover the actor's castable SPHERE AoE spells as AoeSpellOptions (#1255 / PR-D), for the
    tactical-v2 policy. For each known/prepared spell with an available slot whose SRD record is a
    SPHERE shape and that deals damage, surface its radius + avg damage + save fields. Cones/lines are
    DEFERRED (their origin-anchored facing needs a different search than the radial burst PR-D does).
    Empty for a non-caster / no-AoE actor == today (greedy-v1 never sees these). PURE read — the sheet
    + the SRD shape registry; never casts. Mirrors _spell_options' slot-availability discipline."""
    names = list(getattr(ch, "spells_prepared", None) or getattr(ch, "spells_known", None) or ())
    if not names:
        return ()
    slots = getattr(ch, "spell_slots", {}) or {}
    avail = sorted(lvl for lvl, s in slots.items() if int(s.maximum) - int(s.used) > 0)
    out: list[AoeSpellOption] = []
    seen: set[str] = set()
    for name in names:
        key = str(name).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        srd = spells_mod.srd_spell(name)
        if srd is None or str(srd.get("shape_type") or "").lower() != "sphere":
            continue  # PR-D reasons about spheres only
        try:
            rec = spells_mod.spell_data(name)  # curated record: damage automation for the EV
        except ValueError:
            continue  # no curated mechanics to score the EV
        level = int(rec.get("level", 0) or 0)
        is_cantrip = level == 0
        slot_level = 0 if is_cantrip else next((lvl for lvl in avail if lvl >= level), None)
        if not is_cantrip and slot_level is None:
            continue  # no slot can pay for it this turn
        eff = spells_mod.resolve_effect(rec, int(slot_level or level), caster_level, casting_mod)
        if str(eff.get("kind", "") or "") not in ("save", "auto", "attack"):
            continue  # non-damage sphere (utility) — not an offensive AoE
        dmg = eff.get("damage", "")
        value = float(dice_mod.average_total(dmg)) if dmg else 0.0
        if value <= 0:
            continue
        radius = int(srd.get("shape_size") or 20)
        out.append(AoeSpellOption(
            name=str(rec.get("name", name)), radius_ft=radius, value=value,
            range_ft=_spell_range_ft(rec), save_ability=str(eff.get("save_ability", "") or ""),
            on_save=str(eff.get("on_save", "") or "half"), slot_level=int(slot_level or 0),
            concentration=bool(rec.get("concentration", False)),
        ))
    return tuple(out)


def _spell_range_ft(rec: dict) -> int:
    """Parse a curated spell's free-text `range` ("60 feet", "Touch", "Self") into feet. Touch/
    Self -> 5 (adjacent); a bare number -> that many feet; anything unparseable -> 60 (a safe
    default). Pure string parse — the engine/DM still gates true range at cast time."""
    raw = str(rec.get("range", "") or "").strip().lower()
    if not raw or raw in ("self",):
        return 5
    if "touch" in raw:
        return 5
    import re
    m = re.search(r"(\d+)", raw)
    return int(m.group(1)) if m else 60


def _save_bonuses(ch) -> dict:
    """The combatant's six saving-throw bonuses keyed by the SHORT ability name ("dex", "wis", …)
    so they match a curated spell's `save_ability` (resolve_effect emits the short form). Used by
    the AI's save-spell EV. Best-effort + pure: a sheet that can't compute a save yields no key for
    it (the EV falls back to a 0 bonus). Never mutates."""
    from models import Ability  # local import keeps this module's load-time import set unchanged
    out: dict = {}
    for ab in Ability:
        try:
            out[ab.value] = int(ch.saving_throw_bonus(ab))
        except Exception:
            continue
    return out


# ── Martial class abilities + Sneak Attack discovery (v2.0c) ──────────────────────────

# The barbarians who have ENTERED rage this fight (keyed by character id). The engine has no
# active-rage state (rage is a pool decrement only), so the loop tracks rage-entry here to stop
# the AI re-spending Rage every turn. Cleared whenever a fresh combat starts (no rage spent yet).
# This is loop-local bookkeeping, NOT campaign state — it never touches the snapshot (additive).
_raged_this_fight: set = set()


def _fighter_level(actor) -> int:
    """The actor's FIGHTER class level (for the Second Wind heal EV = 1d10 + fighter level). Falls
    back to total level for a single-class sheet / a stub. Pure read."""
    for cl in getattr(actor, "classes", ()) or ():
        if str(getattr(cl, "name", "")).strip().lower() == "fighter":
            return int(getattr(cl, "level", 0)) or 1
    return int(getattr(actor, "total_level", 1)) or 1


def _ability_options(server, actor) -> tuple[AbilityOption, ...]:
    """Surface the actor's spendable MARTIAL class abilities (v2.0c) from its authoritative
    `class_resources` pools (the SAME pools `use_resource` spends). Empty == a non-martial actor or
    a fully-spent pool, so the view is byte-identical to today. Maps a known resource id -> the AI's
    AbilityOption `kind`; Second Wind carries its heal EV (1d10 + fighter level). PURE — reads the
    sheet only, never mutates. The AI decides WHEN to spend; the loop applies via the locked verbs."""
    out: list[AbilityOption] = []
    res = getattr(actor, "class_resources", {}) or {}
    for rid, pool in res.items():
        remaining = int(getattr(pool, "max", 0)) - int(getattr(pool, "used", 0))
        if remaining <= 0:
            continue
        rid_l = str(rid).lower()
        if rid_l == "second_wind":
            lvl = _fighter_level(actor)
            heal_amt = float(dice_mod.average_total(f"1d10+{lvl}"))
            out.append(AbilityOption(kind="second_wind", resource=rid, remaining=remaining,
                                     is_bonus_action=True, heal_amount=heal_amt, name="Second Wind"))
        elif rid_l == "action_surge":
            out.append(AbilityOption(kind="action_surge", resource=rid, remaining=remaining,
                                     name="Action Surge"))
        elif rid_l == "channel_divinity":
            # War Domain Guided Strike (+10 to one attack roll) rides channel_divinity. Surface it as
            # a guided_strike option only for a War-Domain cleric (the SRD subclass that grants it);
            # other channel uses aren't a flat to-hit option the engine models, so we don't claim them.
            if _has_war_domain(actor):
                out.append(AbilityOption(kind="guided_strike", resource=rid, remaining=remaining,
                                         name="Guided Strike"))
        elif rid_l == "superiority_dice":
            out.append(AbilityOption(kind="maneuver", resource=rid, remaining=remaining,
                                     size=str(getattr(pool, "size", "") or "d8"), name="Trip Attack"))
        elif rid_l == "rage":
            out.append(AbilityOption(kind="rage", resource=rid, remaining=remaining,
                                     is_bonus_action=True, name="Rage"))
    return tuple(out)


def _has_war_domain(actor) -> bool:
    """Is `actor` a War-Domain cleric (the SRD subclass whose Channel Divinity is Guided Strike)?
    Checks the subclass tag on the sheet / its cleric ClassLevel. Conservative: only a clear War
    Domain match returns True, so a non-War cleric's channel_divinity is NOT claimed as Guided Strike."""
    sub = str(getattr(actor, "subclass", "") or "").lower()
    if "war" in sub:
        return True
    for cl in getattr(actor, "classes", ()) or ():
        if str(getattr(cl, "name", "")).strip().lower() == "cleric":
            if "war" in str(getattr(cl, "subclass", "") or "").lower():
                return True
    return False


def _sneak_attack_option(actor) -> "SneakAttackOption | None":
    """The actor's Sneak Attack rider (rogue) — the sheet's `sneak_attack_dice` + its avg value, or
    None for a non-rogue / no sneak dice (byte-identical). PURE read."""
    dice = str(getattr(actor, "sneak_attack_dice", "") or "").strip()
    if not dice:
        return None
    try:
        value = float(dice_mod.average_total(dice))
    except (ValueError, TypeError):
        value = 0.0
    return SneakAttackOption(dice=dice, value=value)


def _build_view(server, c, actor) -> CombatView:
    """Assemble the read-only CombatView for `actor` from the live Combat. READ-ONLY — touches
    no state; the loop never mutates here."""
    actor_side = _side_of(actor.kind)
    actor_cb = next((cb for cb in c.combat.order if cb.character_id == actor.id), None)
    actor_cell = (actor_cb.x, actor_cb.y) if (
        actor_cb is not None and actor_cb.x is not None and actor_cb.y is not None
    ) else None
    actor_zone = actor_cb.zone if actor_cb is not None else ""

    foes: list[CombatantView] = []
    allies: list[CombatantView] = []
    for cb in c.combat.order:
        ch = c.characters.get(cb.character_id)
        if ch is None or ch.id == actor.id:
            continue
        is_ally = _side_of(ch.kind) == actor_side
        # A FOE that's down (0 HP / dead) is no longer a target — skip it (today's behavior).
        # An ALLY at 0 HP but not dead is DOWNED/DYING and is the whole point of v2.0a: include it
        # (so the healer can see it) even though _alive() is False. A dead ally is never healable.
        downed = (not getattr(ch, "dead", False)) and int(getattr(ch, "current_hp", 0)) <= 0
        if is_ally:
            if not _alive(ch) and not downed:
                continue  # dead ally — nothing to heal
        else:
            if not _alive(ch):
                continue  # down/dead foe — not a target
        cell = (cb.x, cb.y) if (cb.x is not None and cb.y is not None) else None
        ac, _ = server._effective_armor_class(ch)
        cv = CombatantView(
            id=ch.id, name=ch.name, side=_side_of(ch.kind),
            current_hp=int(ch.current_hp), max_hp=int(ch.max_hp),
            armor_class=int(ac), cell=cell, zone=cb.zone,
            save_bonuses=_save_bonuses(ch),  # v2.0b: real save bonuses for save-spell EV
            conditions=tuple(str(getattr(cn, "value", cn)) for cn in getattr(ch, "conditions", ())),
            downed=bool(downed),
        )
        (allies if is_ally else foes).append(cv)

    if actor.kind in _ENEMY_KINDS:
        attacks = _monster_attack_options(server, actor, c)
    else:
        attacks = _pc_attack_options(server, actor)

    # Caster numbers + castable spells (v2.0a). A non-caster yields (0,0,0) + () so the view is
    # byte-identical to today (a party with no healer produces the same fight as pre-PR).
    atk_bonus, save_dc, caster_level = _caster_numbers(server, actor)
    try:
        casting_mod = server._casting_mod(actor)
    except Exception:
        casting_mod = 0
    spells = _spell_options(server, actor, caster_level, casting_mod)

    # Martial abilities + Sneak Attack (v2.0c). Empty/None for a non-martial actor == today.
    abilities = _ability_options(server, actor)
    sneak = _sneak_attack_option(actor)

    # tactical-v2 positioning inputs (#1255 / PR-D). The fight's impassable + difficult cells (for
    # cover / LoS / terrain routing) as tuples of int pairs, and the actor's castable sphere AoEs.
    # All are ADDITIVE: an open-floor fight has empty blocking/difficult, and a non-caster has no
    # AoEs — so the tactical layer degrades to the greedy behavior and the view is byte-compatible.
    blocking = tuple(sorted((int(bx), int(by)) for bx, by in (c.combat.grid_impassable or [])))
    difficult = tuple(sorted((int(dx), int(dy)) for dx, dy in (c.combat.grid_difficult or [])))
    aoe_spells = _aoe_spell_options(server, actor, caster_level, casting_mod)
    # The action economy this turn — meaningful only when `actor` is the CURRENT combatant (the
    # engine tracks action_used / bonus_action_used on c.combat for the current turn). When the
    # actor isn't current, default both to available (the AI's bonus channel only fires on the
    # actor's own turn anyway, and an out-of-combat view stays as today).
    is_current = bool(c.combat.active and c.combat.current_combatant_id == actor.id)
    action_available = (not c.combat.action_used) if is_current else True
    bonus_action_available = (not c.combat.bonus_action_used) if is_current else True

    return CombatView(
        actor_id=actor.id,
        actor_cell=actor_cell,
        actor_zone=actor_zone,
        actor_side=actor_side,
        speed=int(getattr(actor, "speed", 30)),
        dashed=bool(actor_cb.dashed) if actor_cb is not None else False,
        grid_enabled=bool(c.combat.grid_enabled),
        grid_width=int(c.combat.grid_width),
        grid_height=int(c.combat.grid_height),
        cell_size=int(c.combat.grid_cell_size),
        foes=tuple(foes),
        allies=tuple(allies),
        attacks=tuple(attacks),
        spells=spells,
        spell_attack_bonus=atk_bonus,
        spell_save_dc=save_dc,
        caster_level=caster_level,
        # v2.0b: the spell the actor is ALREADY concentrating on (or "" — today's default). Lets the
        # AI avoid breaking a higher-value active concentration with a new concentration spell.
        active_concentration=str(getattr(actor, "concentration", "") or ""),
        # v2.0c: martial abilities + Sneak Attack + the actor's HP + this turn's action economy +
        # whether the barbarian is already raging this fight. All default to today's behavior so a
        # non-martial actor's view is byte-identical (empty abilities, None sneak, fresh economy).
        actor_current_hp=int(getattr(actor, "current_hp", 0)),
        actor_max_hp=int(getattr(actor, "max_hp", 0)),
        abilities=abilities,
        sneak_attack=sneak,
        action_available=action_available,
        bonus_action_available=bonus_action_available,
        is_raging=actor.id in _raged_this_fight,
        # tactical-v2 positioning inputs (#1255 / PR-D). Empty/() off the grid or for a non-caster
        # == greedy-v1 behavior (the tactics pass is a no-op).
        blocking=blocking,
        difficult=difficult,
        aoe_spells=aoe_spells,
    )


# ── Apply an Intent via the EXISTING write verbs (the only mutating path) ────────────

def _apply_intent(server, campaign_id: str, actor_id: str, intent: Intent) -> dict:
    """Translate one Intent into existing-verb calls (sole writer). Degrades to a skip on any
    engine refusal (out of reach / no slot / illegal) — the advisory-not-block posture the rest
    of combat uses, so a bad Intent never crashes the loop. Returns a compact digest entry."""
    entry: dict = {"actor_id": actor_id, "kind": intent.kind, "note": intent.note}
    try:
        if intent.kind == "attack":
            opt = next(
                (o for o in _view_cache.get(actor_id, ()) if o.name == intent.attack_name), None
            )
            kwargs = dict(
                campaign_id=campaign_id, attacker_id=actor_id, target_id=intent.target_id,
                attack_name=intent.attack_name,
            )
            # SNEAK ATTACK (v2.0c): a tagged rider is a damage_rolls component. Fold it in WITH the
            # weapon's components so the engine rolls + crit-doubles both via the multi-component
            # path. When there's no sneak rider the path is byte-identical to v2.0b.
            sneak_components = [dict(r) for r in (intent.sneak_attack or ())]
            if opt is not None and (opt.damage_rolls or sneak_components):
                weapon_rolls = ([dict(r) for r in opt.damage_rolls] if opt.damage_rolls
                                else [{"dice": opt.damage_expr or "1d4", "type": opt.damage_type}])
                kwargs["damage_rolls"] = weapon_rolls + sneak_components
                kwargs["attack_bonus"] = opt.to_hit
            elif opt is not None:
                kwargs["attack_bonus"] = opt.to_hit
                kwargs["damage_dice"] = opt.damage_expr or "1d4"
                kwargs["damage_type"] = opt.damage_type
            else:
                # No cached option (defensive) — a minimal legal strike so the turn resolves.
                kwargs["attack_bonus"] = 0
                kwargs["damage_dice"] = "1d4"
                if sneak_components:
                    kwargs.pop("damage_dice", None)
                    kwargs["damage_rolls"] = [{"dice": "1d4", "type": "piercing"}] + sneak_components
            # BATTLE MASTER MANEUVER (v2.0c): declared ON the attack — the engine spends the die only
            # on a hit and folds it into the damage (the attack() maneuver rider). Empty == today.
            if intent.maneuver:
                kwargs["maneuver"] = intent.maneuver
                kwargs["maneuver_resource"] = intent.maneuver_resource or "superiority_dice"
            # GUIDED STRIKE (v2.0c): a flat +10-to-hit option is a SEPARATE use_resource declared
            # BEFORE the attack (it stashes a pending_attack_bonus the next attack folds in). Spend
            # it via the locked verb; if the pool refuses we just lose the bonus (the strike lands).
            if intent.channel:
                ch_res = intent.channel_resource or "channel_divinity"
                gs = server.use_resource(
                    campaign_id, actor_id, resource=ch_res, maneuver=intent.channel,
                )
                entry["channel"] = {"option": intent.channel, "ok": bool(gs.get("ok"))}
            res = server.attack(**kwargs)
            dmg = res.get("damage")
            dmg_applied = None
            if isinstance(dmg, dict):
                # multi-component surfaces applied_total; single-type surfaces total.
                dmg_applied = dmg.get("applied_total", dmg.get("total"))
            entry["result"] = {
                "target": res.get("target"), "hit": res.get("hit"), "crit": res.get("crit"),
                "damage": dmg_applied,
                "target_state": res.get("target_state"),
            }
            if intent.sneak_attack:
                entry["result"]["sneak_attack"] = intent.sneak_attack[0].get("dice")
            if intent.maneuver:
                entry["result"]["maneuver"] = res.get("maneuver_damage") or intent.maneuver
        elif intent.kind == "cast":
            # AREA cast (tactical-v2 / #1255): an AoE Intent carries a burst ORIGIN in `to_cell`
            # (no single target_id). Route it through cast_spell(origin=[x,y]) — the SAME PR-2 path
            # that projects the SRD template onto the occupants and resolves save-for-half over them
            # (sole writer preserved: cast_spell, no new path). A single-target cast (target_id set,
            # to_cell None) is byte-identical to today. When BOTH are absent it's a self/utility cast.
            is_aoe = (intent.to_cell is not None and not intent.target_id)
            if is_aoe:
                res = server.cast_spell(
                    campaign_id=campaign_id, character_id=actor_id,
                    spell_name=intent.spell_name,
                    origin=[int(intent.to_cell[0]), int(intent.to_cell[1])],
                )
            else:
                res = server.cast_spell(
                    campaign_id=campaign_id, character_id=actor_id,
                    spell_name=intent.spell_name, target_id=intent.target_id,
                )
            entry["result"] = {"spell": intent.spell_name, "target_id": intent.target_id}
            if is_aoe:
                # The PR-2 origin path auto-resolves the AoE occupants + saves inside cast_spell, so
                # there's no single-target damage to apply below. Surface the template for the digest.
                entry["result"]["origin"] = list(intent.to_cell)
                entry["result"]["affected_tiles"] = res.get("affected_tile_coords") if isinstance(res, dict) else None
                return entry
            # HEAL APPLY (v2.0a): cast_spell spends the slot + resolves the heal EXPRESSION but does
            # NOT auto-bump HP (in real play the DM applies it via apply_healing — see the cast_spell
            # note). To make the engine-run loop's heal actually raise the ally's HP, roll the
            # resolved heal expr and call apply_healing — the SAME locked verb the DM uses (sole
            # writer preserved: cast_spell + apply_healing, no new write path). Inert for any
            # non-heal cast (effect.kind != "heal") so offensive casts are byte-identical to today.
            effect = res.get("effect") if isinstance(res, dict) else None
            if isinstance(effect, dict) and effect.get("kind") == "heal" and intent.target_id:
                heal_expr = str(effect.get("heal", "") or "")
                if heal_expr:
                    rolled = dice_mod.roll(heal_expr)
                    healed = server.apply_healing(
                        campaign_id=campaign_id, target_id=intent.target_id,
                        amount=int(rolled.total),
                    )
                    entry["result"]["heal"] = {
                        "expr": heal_expr, "amount": int(rolled.total),
                        "healed": healed.get("healed"), "revived": healed.get("revived"),
                        "hp": healed.get("hp"),
                    }
            # OFFENSIVE APPLY (v2.0b): cast_spell spends the slot + resolves the damage EXPRESSION but
            # — like a heal — does NOT auto-bump a single-target's HP (in real play the DM applies it
            # via attack()/apply_damage; only the AoE target_ids path auto-resolves). To make the
            # engine-run loop's offensive cast actually REMOVE HP, roll the resolved damage and call
            # apply_damage — the SAME locked verb the DM uses (sole writer preserved: cast_spell +
            # apply_damage, NO new write path). For a SAVE spell, roll the target's save vs the real
            # DC and halve on a success ("half") / negate ("none"). Inert for heal/buff/utility
            # (effect.kind not in the offensive set) so non-damage casts are byte-identical to today.
            elif isinstance(effect, dict) and effect.get("kind") in ("attack", "auto", "save") \
                    and intent.target_id:
                dmg_expr = str(effect.get("damage", "") or "")
                if dmg_expr:
                    rolled = dice_mod.roll(dmg_expr)
                    amount = int(rolled.total)
                    half = False
                    save_made = None
                    if effect.get("kind") == "save":
                        # Resolve the target's save vs the real DC the engine just computed.
                        dc = int(res.get("spell_save_dc", 0) or 0)
                        ability = str(effect.get("save_ability", "") or "")
                        sv = server.saving_throw(
                            campaign_id=campaign_id, character_id=intent.target_id,
                            ability=ability, dc=dc,
                        ) if (dc and ability) else None
                        save_made = bool(sv.get("success")) if isinstance(sv, dict) else None
                        on_save = str(effect.get("on_save", "") or "")
                        if save_made and on_save == "half":
                            half = True
                        elif save_made:
                            amount = 0  # save-or-nothing: a successful save negates the damage
                    if amount > 0:
                        hit = server.apply_damage(
                            campaign_id=campaign_id, target_id=intent.target_id,
                            amount=amount, damage_type=str(effect.get("damage_type", "") or ""),
                            half=half,
                        )
                        entry["result"]["damage"] = {
                            "expr": dmg_expr, "rolled": int(rolled.total),
                            "applied": int(hit.get("damage_to_hp", 0)),
                            "target_hp": hit.get("hp"), "target_dead": hit.get("dead"),
                            "save_made": save_made,
                        }
                    else:
                        entry["result"]["damage"] = {
                            "expr": dmg_expr, "rolled": int(rolled.total), "applied": 0,
                            "save_made": save_made,
                        }
        elif intent.kind == "move":
            move_view: dict | None = None
            if intent.to_cell is not None:
                move_view = server.move_to_coords(campaign_id, actor_id, intent.to_cell[0], intent.to_cell[1])
            elif intent.to_zone:
                move_view = server.move_to_zone(campaign_id, combatant_id=actor_id, zone=intent.to_zone)
            entry["result"] = {"to_cell": intent.to_cell, "to_zone": intent.to_zone}
            # #1447 gap: move_to_coords/move_to_zone's advisory notes (movement_illegal /
            # move_blocked) were rolled but never surfaced here, so the player's advisory UI
            # went dormant. ADDITIVE: only set when the verb actually returned one — a legal
            # in-budget move's result dict stays byte-identical to today. Advisory text only;
            # never gates (the verbs themselves never block on this).
            if isinstance(move_view, dict):
                if move_view.get("movement_illegal") is not None:
                    entry["result"]["movement_illegal"] = move_view["movement_illegal"]
                if move_view.get("move_blocked") is not None:
                    entry["result"]["move_blocked"] = move_view["move_blocked"]
        elif intent.kind == "disengage":
            server.use_action(campaign_id, actor_id, kind="disengage")
            if intent.to_cell is not None:
                server.move_to_coords(campaign_id, actor_id, intent.to_cell[0], intent.to_cell[1])
            elif intent.to_zone:
                server.move_to_zone(campaign_id, combatant_id=actor_id, zone=intent.to_zone)
        elif intent.kind == "use_resource":
            # A class-resource spend (v2.0c): Second Wind / Rage (bonus action) or Action Surge (a
            # fresh Action grantor). The locked use_resource verb deducts the pool; Action Surge bumps
            # c.combat.surge_actions so the loop's re-ask gets an extra Attack action. For a BONUS-
            # action ability (Second Wind / Rage), also mark the bonus action spent + apply Second
            # Wind's heal via the locked apply_healing (the pool spend alone doesn't raise HP). Rage
            # entry is tracked per-fight so the AI won't re-spend it (see _raged_this_fight).
            rr = server.use_resource(campaign_id, actor_id, resource=intent.resource,
                                     amount=max(1, int(intent.amount)))
            entry["result"] = {"resource": intent.resource, "ok": bool(rr.get("ok")),
                               "remaining": rr.get("remaining")}
            rid_l = str(intent.resource).lower()
            if rr.get("ok") and rid_l == "second_wind":
                # Second Wind heals 1d10 + fighter level — apply via the SAME locked verb the DM uses.
                actor = server._require(campaign_id).characters.get(actor_id)
                lvl = _fighter_level(actor) if actor is not None else 1
                rolled = dice_mod.roll(f"1d10+{lvl}")
                healed = server.apply_healing(campaign_id=campaign_id, target_id=actor_id,
                                              amount=int(rolled.total))
                entry["result"]["heal"] = {"amount": int(rolled.total), "hp": healed.get("hp")}
            if rr.get("ok") and rid_l == "rage":
                _raged_this_fight.add(actor_id)
            # Mark the BONUS action consumed for a bonus-action ability so the loop doesn't re-issue it.
            if rr.get("ok") and rid_l in ("second_wind", "rage"):
                try:
                    server.use_action(campaign_id, actor_id, kind="bonus")
                except Exception:
                    pass
        elif intent.kind == "dash":
            server.use_action(campaign_id, actor_id, kind="dash")
        elif intent.kind == "dodge":
            # No dedicated Dodge verb; spend the action as a 'skip' so the turn is legally used
            # (the defensive benefit is narrative in v1). Keeps next_turn's PC-skip guard happy.
            server.use_action(campaign_id, actor_id, kind="skip")
        else:  # "skip"
            server.use_action(campaign_id, actor_id, kind="skip")
    except Exception as exc:  # advisory-not-block: degrade to a skip, record why
        entry["error"] = str(exc)
        try:
            server.use_action(campaign_id, actor_id, kind="skip")
        except Exception:
            pass
    return entry


# Per-turn cache of the actor's attack options so _apply_intent can recover the damage spec
# for the chosen attack_name without re-deriving. Set by run_combat_round before applying.
_view_cache: dict = {}


def _bonus_was_leveled_spell(bonus_intent: Optional[Intent], view: CombatView) -> bool:
    """Did this turn's BONUS action cast a LEVELED spell (a slot-spender — Healing Word, Spiritual
    Weapon, …)? 5e RAW (#1106): casting a leveled bonus-action spell forbids a SECOND leveled spell as
    the same turn's ACTION (only a cantrip may follow). This is the per-turn detector run_combat_round
    uses to set `view.bonus_spell_used` on the MAIN-action view.

    True ONLY for a `cast` Intent whose spell resolves (by name, in the view the bonus was decided over)
    to a LEVELED slot (slot_level > 0). A cantrip cast (slot_level 0), a Second Wind / Rage `use_resource`
    spend, a None bonus (non-caster), or an unrecognized spell -> False (no constraint; today's behavior).
    Pure: reads only the Intent + the view's already-discovered SpellOptions; no state, no I/O."""
    if bonus_intent is None or bonus_intent.kind != "cast":
        return False
    name = str(bonus_intent.spell_name or "").strip().lower()
    if not name:
        return False
    for sp in view.spells:
        if str(sp.name or "").strip().lower() == name:
            return int(sp.slot_level) > 0  # a leveled bonus spell triggers the rule; a cantrip does not
    return False


# ── The rounds ───────────────────────────────────────────────────────────────────────

def run_combat_round(campaign_id: str, mode: str = "live", max_turns: int = 60) -> dict:
    """Sequence combatants from the current turn to the end of the round.

    mode="test": run EVERYONE via pick_action + the existing verbs.
    mode="live": run ONLY hostile (monster/npc) turns; STOP at the first PC/companion turn and
                 return {round_digest, awaiting_pc: <id>} so the DM resolves it.

    Sole writer: every mutation goes through server.attack/cast_spell/move_to_*/use_action/
    next_turn. The loop holds no lock. max_turns is a safety rail against a non-advancing order.
    """
    import server  # lazy: avoid a server->combat_loop import cycle at module load

    if mode not in ("live", "test"):
        raise ValueError("mode must be 'live' or 'test'")

    digest: list[dict] = []
    awaiting_pc: Optional[str] = None
    start_round = None

    for _ in range(max_turns):
        c = server._require(campaign_id)
        if not c.combat.active or not c.combat.order:
            break
        if start_round is None:
            start_round = c.combat.round
        # Stop once the round has advanced past the one we started (one round per call).
        if c.combat.round > start_round and start_round is not None:
            break

        cur_id = c.combat.current_combatant_id
        if cur_id is None:
            break
        actor = c.characters.get(cur_id)
        if actor is None:
            server.next_turn(campaign_id)
            continue

        # LIVE: stop at the first PC/companion turn — never auto-play them.
        if mode == "live" and actor.kind not in _LIVE_AUTORUN_KINDS:
            awaiting_pc = actor.id
            break

        # Dead/downed current combatant -> just advance.
        if not _alive(actor):
            server.next_turn(campaign_id)
            continue

        # One full turn for `actor`: ask the AI, apply, repeat for a Multiattack budget.
        view = _build_view(server, c, actor)
        _view_cache[actor.id] = view.attacks
        acted = False

        # BONUS ACTION (v2.0c): fire a worthwhile bonus action ALONGSIDE the main action (Second
        # Wind self-heal / Rage entry / bonus-action Healing Word). Resolved FIRST so Second Wind's
        # HP lands before the swings. Returns None for a non-martial actor -> byte-identical (no
        # bonus call). The _apply_intent marks the bonus economy spent so it fires at most once.
        bonus_intent = combat_ai.pick_bonus_action(actor, view)
        # #1106: a LEVELED bonus-action spell (Healing Word, …) forbids a SECOND leveled spell as this
        # turn's ACTION — only a cantrip may follow. Detect it here (over the view the bonus was decided
        # on) and thread it onto every MAIN-action view this turn so pick_action refuses a leveled cast.
        # A cantrip / Second Wind / Rage bonus leaves this False == today (no constraint).
        bonus_spell_used = _bonus_was_leveled_spell(bonus_intent, view)
        if bonus_intent is not None:
            digest.append(_apply_intent(server, campaign_id, actor.id, bonus_intent))
            acted = True

        # Multiattack budget: how many attack() strikes this actor's Attack action grants.
        c = server._require(campaign_id)
        actor = c.characters.get(cur_id)
        ma = max(1, server._attacker_multiattack_count(actor, c)) if actor is not None else 1
        strikes_left = ma
        surged = False  # Action Surge spent this turn? (one extra Attack action, v2.0c)
        sneak_used = False  # Sneak Attack is once-per-turn (5e RAW): suppress after the first LANDS
        # Re-ask pick_action per granted strike (move-then-attack, or several strikes).
        for _strike in range(max(1, ma) + 4):  # +4 headroom: a move, the strikes, an Action Surge
            c = server._require(campaign_id)
            actor = c.characters.get(cur_id)
            if actor is None or not _alive(actor) or not c.combat.active:
                break
            if not _living_sides(c) or len(_living_sides(c)) < 2:
                break  # fight is decided; stop issuing this actor's strikes
            view = _build_view(server, c, actor)
            if sneak_used and view.sneak_attack is not None:
                # 5e RAW: Sneak Attack once per turn — already dealt this turn, never re-tag a later strike.
                view = replace(view, sneak_attack=None)
            if bonus_spell_used and not view.bonus_spell_used:
                # #1106: re-thread the leveled-bonus-spell rule onto every freshly-built main-action
                # view this turn (_build_view doesn't know the loop already cast a leveled bonus spell),
                # so pick_action refuses a SECOND leveled cast (no double Healing Word / leveled+leveled).
                view = replace(view, bonus_spell_used=True)
            _view_cache[actor.id] = view.attacks
            # ACTION SURGE (v2.0c): when the normal strikes are spent but the fight is still hot,
            # the fighter can spend Action Surge for a FRESH Attack action. Spend it ONCE, then keep
            # swinging (the engine's surge_actions grants the extra strikes). Only a fighter with the
            # surge ability + a hot moment surges (should_action_surge gates it); else None == today.
            if strikes_left <= 0 and not surged:
                surge_intent = combat_ai.should_action_surge(view)
                if surge_intent is not None:
                    digest.append(_apply_intent(server, campaign_id, actor.id, surge_intent))
                    surged = True
                    strikes_left = ma  # the surged action grants another Attack action's strikes
                    acted = True
                    continue
                break  # no surge — the actor's attacks are done
            intent = combat_ai.pick_action(actor, view, policy=_MONSTER_POLICY)
            entry = _apply_intent(server, campaign_id, actor.id, intent)
            digest.append(entry)
            acted = True
            if intent.kind == "attack":
                if intent.sneak_attack and (entry.get("result") or {}).get("hit"):
                    sneak_used = True  # Sneak Attack DEALT — a miss does NOT consume the once-per-turn
                strikes_left -= 1
                if strikes_left <= 0 and surged:
                    break  # already surged once; don't loop forever
            elif intent.kind in ("skip", "dodge", "disengage"):
                break
            elif intent.kind == "move":
                continue  # let the next iteration try to attack from the new cell
            else:  # cast / dash / use_resource
                break

        # Advance the turn (auto-resolves end-of-turn repeat saves). If the actor never acted,
        # mark a skip first so next_turn's PC-skip guard (companions count) is satisfied.
        c = server._require(campaign_id)
        if c.combat.active and c.combat.current_combatant_id == cur_id:
            if not acted:
                try:
                    server.use_action(campaign_id, cur_id, kind="skip")
                except Exception:
                    pass
            try:
                server.next_turn(campaign_id)
            except Exception:
                break
        _view_cache.pop(cur_id, None)

        # Stop the round if the fight is decided.
        c = server._require(campaign_id)
        if not c.combat.active or len(_living_sides(c)) < 2:
            break

    c = server._require(campaign_id)
    living = _living_sides(c)
    return {
        "mode": mode,
        "round": c.combat.round,
        "round_digest": digest,
        "awaiting_pc": awaiting_pc,
        "combat_active": c.combat.active,
        "living_sides": sorted(living),
    }


def run_combat_autonomous(campaign_id: str, mode: str = "test", max_rounds: int = 20) -> dict:
    """Drive run_combat_round repeatedly to a terminal state (victory / defeat / round-cap).

    mode="live": advance the fight up to the NEXT PC decision, then hand control back to the DM
                 (returns the digest + awaiting_pc). Never auto-plays a PC.
    mode="test": run the whole fight to a terminal state with no LLM (reproducible under
                 WORLDOS_COMBAT_SEED).

    Returns a rollup: {rounds, turns, victor, per-round digests, every combatant that acted,
    round_cap_hit}. The round-cap is a safety rail against a non-terminating fight; it ends the
    fight as a draw and flags round_cap_hit. Sole writer (only the round's verbs mutate)."""
    import server  # lazy

    if mode not in ("live", "test"):
        raise ValueError("mode must be 'live' or 'test'")

    # v2.0c: this is a FRESH fight from the AI's perspective — clear the per-fight rage tracker so a
    # barbarian can enter rage again (a new fight, rested or not, is a new rage decision). Loop-local
    # bookkeeping only; never touches the snapshot.
    _raged_this_fight.clear()

    rounds: list[dict] = []
    actors_acted: set = set()
    victor: Optional[str] = None
    round_cap_hit = False
    awaiting_pc: Optional[str] = None
    turns = 0

    for _ in range(max_rounds):
        c = server._require(campaign_id)
        if not c.combat.active:
            break
        living = _living_sides(c)
        if len(living) < 2:
            victor = next(iter(living)) if living else "draw"
            break

        rr = run_combat_round(campaign_id, mode)
        rounds.append(rr)
        for e in rr["round_digest"]:
            actors_acted.add(e["actor_id"])
            turns += 1

        if mode == "live" and rr.get("awaiting_pc"):
            awaiting_pc = rr["awaiting_pc"]
            break

        c = server._require(campaign_id)
        living = _living_sides(c)
        if not c.combat.active or len(living) < 2:
            victor = next(iter(living)) if living else "draw"
            break
    else:
        round_cap_hit = True

    # Resolve a final victor read if we exited via a terminal side condition.
    c = server._require(campaign_id)
    living = _living_sides(c)
    if victor is None and not awaiting_pc:
        victor = next(iter(living)) if len(living) == 1 else ("draw" if round_cap_hit else None)

    # TEST mode runs the fight to a terminal state -> close out combat so XP/cleanup fire.
    ended = None
    if mode == "test" and victor is not None and victor != "draw":
        try:
            ended = server.end_combat(campaign_id, resolution=f"{victor} victorious (engine-run test)")
        except Exception as exc:
            ended = {"error": str(exc)}

    return {
        "mode": mode,
        "rounds": len(rounds),
        "turns": turns,
        "victor": victor,
        "awaiting_pc": awaiting_pc,
        "round_cap_hit": round_cap_hit,
        "actors_acted": sorted(actors_acted),
        "round_digests": rounds,
        "end_combat": ended,
    }


# ── The PLAYER-TURN ARBITER (S1 keystone) ────────────────────────────────────────────
# The one path by which a HUMAN/UI-authored Intent is resolved on the player's combat turn.
# It is the mirror of the AI loop's `awaiting_pc` branch: where `run_combat_round(mode="live")`
# STOPS at a PC turn and hands a digest to the DM, THIS entry-point accepts the PC's own
# Intent (the cell to move to / the target to strike), resolves it through the SAME
# `_apply_intent` mapping the AI uses, advances the turn, then auto-runs the following enemy
# turns up to the next PC decision.
#
# SOLE-WRITER INVARIANT (unchanged): like the rest of this module, the arbiter holds NO lock
# and introduces NO new write path — every mutation goes through an existing server.py verb
# (move_to_coords / attack / next_turn), each of which takes its own campaign_lock +
# save_campaign. The arbiter only READS a snapshot to validate turn-ownership + prime the
# attack-option cache, then delegates to the locked verbs.
#
# TURN-OWNERSHIP is load-bearing: move_to_coords() does NOT gate on whose turn it is (it
# charges the named mover's own budget regardless), so a wrong-turn move would otherwise
# silently mutate the board. The arbiter REJECTS (mutating nothing) unless the actor is the
# current combatant AND a PC/companion. (attack() additionally self-guards turn-ownership,
# but we gate up front so BOTH kinds reject identically and nothing is half-applied.)

# A PC/companion player Intent the arbiter accepts. Mirrors _LIVE_AUTORUN_KINDS' complement:
# only a party actor (player/companion) is ever "awaiting" in a live fight.
_PLAYER_TURN_KINDS = _PARTY_KINDS


# Intent kinds that CONSUME the turn's action economy (so next_turn's PC-skip guard passes and
# the turn legally ends). A bare `move` consumes NO action (5e: you may move AND act), so it
# leaves the turn OPEN for a following action — the arbiter does NOT auto-advance on a bare move.
_ACTION_CONSUMING_KINDS = frozenset({"attack", "cast", "dash", "disengage", "dodge", "skip", "use_resource"})

# The intent kinds the PLAYER lane will resolve through _apply_intent: the action-consuming kinds
# plus a bare `move` (which keeps the turn open). Any other kind is rejected by the arbiter up
# front (defense-in-depth) so a malformed/unknown intent never reaches _apply_intent's skip
# fallback and burns the PC's turn.
_PLAYER_INTENT_KINDS = _ACTION_CONSUMING_KINDS | {"move"}


def turn_token(c) -> str:
    """A stable identity for the CURRENT turn slot: round + turn_index + current combatant.

    The UI reads this off build_combat_surface and echoes it on its next player-turn POST; the
    arbiter rejects a POST whose token != the live slot (idempotency). A double-click reuses the
    SAME token (the client read the surface once), so the second, now-stale POST rejects instead
    of burning a second PC turn — while a genuine NEXT turn carries a FRESH token (the client
    re-read the surface) and is accepted. Empty string when there's no active combat."""
    if not c.combat.active or not c.combat.order:
        return ""
    return f"{c.combat.round}:{c.combat.turn_index}:{c.combat.current_combatant_id or ''}"


def resolve_player_turn(
    campaign_id: str,
    actor_id: str,
    intent: Intent,
    *,
    advance: bool = True,
    end_turn: bool = False,
    expected_turn_token: str = "",
) -> dict:
    """Resolve ONE player/companion combat action from an injected Intent (S1 keystone).

    This is the player-control mirror of run_combat_round(mode="live")'s `awaiting_pc` stop:
    the loop hands a PC turn back to the human/UI; the UI POSTs the PC's chosen Intent (move
    to a cell, or attack a target); THIS function validates it is that actor's turn, resolves
    it through the EXISTING `_apply_intent` (the same Intent->locked-verb mapping the AI loop
    uses), and — when the turn is actually OVER — advances initiative + auto-runs the following
    enemy turns to the next PC decision.

    TURN MODEL (5e-correct): a bare `move` consumes NO action — the player may move AND then act
    — so a move-to-cell DOES NOT end the turn; the turn stays OPEN (awaiting_pc == actor) for a
    following action. An action-consuming intent (attack / cast / dash / disengage / dodge /
    skip) ends the turn → the arbiter advances. A move-then-end-without-acting is requested via
    `end_turn=True` (the UI's explicit "End Turn"): the arbiter declares a skip and advances.

    Args:
      campaign_id: the live campaign.
      actor_id:    the combatant declaring the action — MUST be the current combatant AND a
                   player/companion, or the call is rejected with nothing mutated.
      intent:      a `move` (to_cell set) or `attack` (target_id set) Intent. Other kinds are
                   resolvable too (cast/skip/dodge/disengage) via the shared mapping.
      advance:     master switch (default True). When False, resolve the intent only and never
                   touch initiative (test-introspection); the turn stays open.
      end_turn:    when True, end the turn after resolving even if the intent did not consume an
                   action (move-then-End-Turn) — the arbiter declares a skip so next_turn's
                   PC-skip guard passes, then advances.
      expected_turn_token: OPTIONAL idempotency token (see turn_token()). When non-empty, the
                   call is rejected unless it matches the LIVE turn slot — so a double-click /
                   stale retry (which echoes the token the client read once) rejects instead of
                   burning a second PC turn. Empty (default) == today's behavior (no token check).

    Returns (NEVER raises for a normal rejection):
      {ok: False, reason}                              — rejected; NOTHING mutated.
      {ok: True, resolved, advanced, turn_open,        — resolved. `turn_open` True == the PC may
       awaiting_pc, combat_active, round}                still act this turn (a bare move); when
                                                         the turn ended, `awaiting_pc` is whose
                                                         turn it is next (None if the fight ended).

    Sole writer: only the verbs `_apply_intent`/next_turn/run_combat_round invoke mutate; the
    arbiter holds no lock. Additive: a campaign that never calls this is byte-identical to today.
    """
    import server  # lazy: avoid a server->combat_loop import cycle at module load

    if intent is None or not isinstance(intent, Intent):
        return {"ok": False, "reason": "resolve_player_turn needs an Intent"}

    c = server._require(campaign_id)
    if not c.combat.active or not c.combat.order:
        return {"ok": False, "reason": "no active combat"}

    cur_id = c.combat.current_combatant_id
    if cur_id is None:
        return {"ok": False, "reason": "no current combatant"}

    # IDEMPOTENCY / TURN-SLOT GUARD (dedup): when the caller supplies a token, it must match the
    # LIVE turn slot. A double-click / stale retry echoes the token the client read ONCE off the
    # surface; after the first POST resolves + the order advances, the slot token changes, so the
    # second (stale-token) POST rejects here — NOTHING mutated — instead of being accepted as a
    # second legitimate PC turn. A genuine next turn carries a FRESH token (the client re-read the
    # surface) and passes. Empty token (default) == no check (today's behavior / tests).
    if expected_turn_token:
        live_token = turn_token(c)
        if expected_turn_token != live_token:
            return {
                "ok": False,
                "reason": "stale turn — the board advanced since this action was chosen (re-read and retry)",
                "expected_turn_token": expected_turn_token,
                "live_turn_token": live_token,
            }

    # TURN-OWNERSHIP GATE (load-bearing — move_to_coords does not self-gate). Reject a move by
    # anyone other than the current combatant, BEFORE any verb runs, so nothing is mutated.
    if actor_id != cur_id:
        cur = c.characters.get(cur_id)
        cur_name = cur.name if cur is not None else cur_id
        return {"ok": False, "reason": f"not {actor_id}'s turn — it is {cur_name}'s ({cur_id}) turn"}

    actor = c.characters.get(actor_id)
    if actor is None:
        return {"ok": False, "reason": f"unknown combatant {actor_id!r}"}
    # Only a PC/companion is ever the "awaiting" actor; refuse to drive a monster/NPC turn
    # through the player lane (those are the AI loop's / the DM's to run).
    if actor.kind not in _PLAYER_TURN_KINDS:
        return {"ok": False, "reason": f"{actor.name} is a {actor.kind}, not a player-controlled combatant"}
    if not _alive(actor):
        return {"ok": False, "reason": f"{actor.name} cannot act (downed/dead)"}

    # KIND ALLOWLIST (defense-in-depth): only resolve the intent kinds the player lane supports.
    # An unsupported/unknown kind REJECTS cleanly (nothing mutated) rather than slipping into
    # _apply_intent, whose `else` branch silently degrades to a skip (which would burn the PC's
    # turn on a malformed request). Mirrors the engine's "reject the illegal action outright"
    # posture. The set is the union of the action-consuming kinds + the bare `move`.
    if intent.kind not in _PLAYER_INTENT_KINDS:
        return {"ok": False, "reason": f"unsupported player intent kind {intent.kind!r}"}

    # Per-kind required-field validation, so an ill-formed Intent REJECTS cleanly (with nothing
    # mutated) instead of silently degrading to a skip inside _apply_intent.
    if intent.kind == "attack" and not intent.target_id:
        return {"ok": False, "reason": "an attack needs a target_id"}
    # ANY intent carrying a to_cell must carry a well-formed one — an (x,y) int pair — not just
    # `move`. The public arbiter admits every action-consuming kind (the bridge only ever sends
    # move/attack/skip, but harden the contract): a malformed cell on e.g. a disengage would
    # otherwise raise deep inside a verb AFTER the turn-ownership gate. Reject here so nothing is
    # mutated and the UI gets a clear why.
    tc = intent.to_cell
    if tc is not None and not (
        isinstance(tc, (tuple, list))
        and len(tc) == 2
        and all(isinstance(coord, int) and not isinstance(coord, bool) for coord in tc)
    ):
        return {"ok": False, "reason": f"to_cell must be an (x, y) pair of ints, got {tc!r}"}
    if intent.kind == "move" and intent.to_cell is None and not intent.to_zone:
        return {"ok": False, "reason": "a move needs a to_cell (x,y) or a to_zone"}

    # Prime the per-turn attack-option cache the way run_combat_round does, so an `attack`
    # Intent's damage spec resolves from the actor's authoritative attack lines (a PC gets the
    # synthesized weapon line). A pure move ignores the cache; priming is harmless either way.
    view = _build_view(server, c, actor)
    # A player /move attack carries only a target_id (no attack_name), so default it to the PC's
    # authoritative first attack option — otherwise _apply_intent fails the name match and falls
    # back to the defensive +0/1d4 strike instead of the real weapon/to-hit profile.
    if intent.kind == "attack" and not intent.attack_name:
        if not view.attacks:
            return {"ok": False, "reason": f"{actor.name} has no available attack option"}
        intent = replace(intent, attack_name=view.attacks[0].name)
    _view_cache[actor.id] = view.attacks
    try:
        resolved = _apply_intent(server, campaign_id, actor.id, intent)
    finally:
        _view_cache.pop(actor.id, None)

    # Decide whether the TURN is over. A bare `move` consumes no action -> the turn stays OPEN
    # (the PC may still act). An action-consuming intent ends it. `end_turn=True` ends a
    # move-only turn on explicit request (the arbiter declares the skip so the guard passes).
    # If _apply_intent itself degraded an action-consuming intent to a skip on a refusal, it
    # already called use_action(skip) -> the guard is satisfied and the turn legally ends.
    consumed_action = intent.kind in _ACTION_CONSUMING_KINDS
    turn_should_end = advance and (consumed_action or end_turn)

    # If the player is ENDING the turn but resolved only a move (no action consumed), declare an
    # explicit skip so next_turn's PC-skip guard passes (a do-nothing-action turn is legal).
    # FAIL CLOSED: use_action(kind="skip") reports a REJECTED pass via ok=False (it does NOT
    # raise) — e.g. the actor is no longer current, or the action was already spent (state drift /
    # a racing call). If the skip did not succeed we must NOT advance into next_turn (whose PC-skip
    # guard would then raise, or which would advance an out-of-sync turn): return a clean
    # not-advanced result with the reason and keep the turn OPEN. A genuine raise is handled the
    # same way. (When the action WAS already consumed, the skip's "already used" rejection is
    # benign — the guard is already satisfied — so we only bail when the turn is still ACTABLE.)
    if turn_should_end and not consumed_action:
        skip_failed_reason: Optional[str] = None
        try:
            skip_res = server.use_action(campaign_id, actor.id, kind="skip")
            if isinstance(skip_res, dict) and not skip_res.get("ok"):
                skip_failed_reason = str(skip_res.get("reason") or "skip rejected")
        except Exception as exc:
            skip_failed_reason = str(exc)
        if skip_failed_reason is not None:
            # Re-read: only bail if the turn is genuinely NOT yet endable (the actor is still
            # current AND hasn't acted). If the skip was rejected merely because the action was
            # already spent, the PC-skip guard is already satisfied and we proceed to advance.
            c = server._require(campaign_id)
            cur = c.combat.current_combatant_id if c.combat.active else None
            already_acted = (
                c.combat.action_used
                or c.combat.action_attacks_made > 0
                or c.combat.bonus_action_used
            ) if c.combat.active else False
            if cur == actor.id and not already_acted:
                return {
                    "ok": True,
                    "resolved": resolved,
                    "advanced": False,
                    "turn_open": True,
                    "skip_declare_error": skip_failed_reason,
                    "awaiting_pc": actor.id,
                    "combat_active": c.combat.active,
                    "round": c.combat.round,
                }
            # Otherwise the guard is satisfiable (already acted, or the actor is no longer
            # current) — record the benign rejection and proceed to advance.
            resolved.setdefault("skip_declare_error", skip_failed_reason)

    if not turn_should_end:
        # The turn is still the PC's (a bare move, or advance=False introspection). Do NOT touch
        # initiative — return turn_open so the UI keeps the action bar live for this actor.
        c = server._require(campaign_id)
        return {
            "ok": True,
            "resolved": resolved,
            "advanced": False,
            "turn_open": bool(c.combat.active and c.combat.current_combatant_id == actor.id),
            "awaiting_pc": cur_id,
            "combat_active": c.combat.active,
            "round": c.combat.round,
        }

    # Advance the turn (the locked next_turn enforces the PC-skip guard + end-of-turn saves).
    # If next_turn RAISES (e.g. the PC-skip guard refuses because the action didn't register),
    # the turn did NOT advance — the actor is STILL current. Returning advanced=True here would
    # be a lie the UI acts on (it would stop showing this PC's action bar though the engine still
    # awaits its turn). So on a raise we return a clean NOT-advanced result with the reason; the
    # action the player took (the resolved move/attack) still landed via the locked verb.
    c = server._require(campaign_id)
    if c.combat.active and c.combat.current_combatant_id == actor.id:
        try:
            server.next_turn(campaign_id)
        except Exception as exc:
            c = server._require(campaign_id)
            return {
                "ok": True,
                "resolved": resolved,
                "advanced": False,
                "turn_open": bool(c.combat.active and c.combat.current_combatant_id == actor.id),
                "advance_error": str(exc),
                "awaiting_pc": actor.id if c.combat.active else None,
                "combat_active": c.combat.active,
                "round": c.combat.round,
            }

    # Auto-run the following ENEMY turns to the next PC decision — the existing live loop.
    # run_combat_round is ONE round per call and breaks at a round boundary WITHOUT reporting
    # awaiting_pc (it can wrap to a PC at the top of the next round). So loop until the current
    # combatant is a PC/companion (the next decision) or the fight ends — bounded by a turn cap.
    enemy_digest: list[dict] = []
    for _ in range(64):  # safety rail against a non-advancing order
        c = server._require(campaign_id)
        if not c.combat.active or len(_living_sides(c)) < 2:
            break
        cur = c.characters.get(c.combat.current_combatant_id)
        if cur is not None and cur.kind in _PLAYER_TURN_KINDS:
            if _alive(cur):
                break  # reached the next PC decision — stop auto-running
            # A DOWNED PC/companion: run_combat_round(mode="live") would STOP here without acting,
            # so this loop would spin to its 64-turn cap and return no actionable awaiting_pc.
            # Advance past it ourselves toward the next decision (or terminal state).
            try:
                server.next_turn(campaign_id)
            except Exception:
                break
            continue
        rr = run_combat_round(campaign_id, mode="live")
        enemy_digest.extend(rr.get("round_digest") or [])
        # If the round produced no progress (no digest AND the current combatant is unchanged),
        # bail to avoid spinning (defensive — run_combat_round always advances or stops).
        if not rr.get("round_digest") and rr.get("awaiting_pc") is None:
            # nudge past a stuck non-PC current via next_turn, else break
            c2 = server._require(campaign_id)
            if c2.combat.active and c2.combat.current_combatant_id == (cur.id if cur else None):
                try:
                    server.next_turn(campaign_id)
                except Exception:
                    break
    if enemy_digest:
        resolved.setdefault("enemy_digest", enemy_digest)

    # The next PC decision == the current combatant IF it is now a living PC/companion.
    awaiting_pc: Optional[str] = None
    c = server._require(campaign_id)
    if c.combat.active:
        cur = c.characters.get(c.combat.current_combatant_id)
        if cur is not None and cur.kind in _PLAYER_TURN_KINDS and _alive(cur):
            awaiting_pc = cur.id

    return {
        "ok": True,
        "resolved": resolved,
        "advanced": True,
        "turn_open": False,
        "awaiting_pc": awaiting_pc,
        "combat_active": c.combat.active,
        "round": c.combat.round,
        "living_sides": sorted(_living_sides(c)),
    }
