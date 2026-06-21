"""Engine-run combat: the monster-AI contract + greedy-v1 policy (Track 2b).

See docs/roadmap/engine-combat-loop-design.md for the full ADR. This module is the
monster-AI for the auto-sequencing combat loop. PR-A implements the greedy-v1
expected-value policy.

Posture (mirrors combat_grid.py): PURE — no Campaign mutation, no lock, no save, no
LLM, no I/O. `pick_action` is a deterministic decision over a read-only snapshot, so
the same state + same dice-seed always yields the same Intent. That purity is what
makes the engine-only combat smoke reproducible and lets the AI be unit-tested in
isolation — feed a CombatView, assert the Intent — without standing up a campaign.
The MCP-facing loop in server.py is the SOLE WRITER: it translates an Intent into the
existing write verbs (attack / cast_spell / move_to_* / use_action / next_turn), never
a parallel resolution path.

The only imports are pure-math helpers (dice.average_total, combat_grid distance/
reachability). No engine state, no server, no filesystem — keeping this module I/O-free
is what guarantees determinism and isolated testability.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Optional

import combat_grid
import dice as dice_mod

if TYPE_CHECKING:  # avoid any runtime import coupling — this module stays I/O-free
    from models import Character


# ── Policy constants (one readable home; the greedy-v1 policy is one function) ────────
# Owner-decided (ADR open-Q 5): v1 monsters FIGHT TO THE DEATH by default (RETREAT_FRACTION
# 0.0 == no morale), with the retreat hook present for a future morale policy. Set >0 (e.g.
# 0.25) to make a monster disengage+flee when below that fraction of max HP. Owner-decided
# (the brief): "Monster v1 = greedy retreat-if-low (no full morale)" — the hook is wired and
# tested; the default value keeps today's fight-to-the-death behaviour until a fight opts in.
RETREAT_FRACTION = 0.0


# A monster/NPC's declared intent for its turn. The loop maps `kind` to one or more
# existing write verbs (a Multiattack re-asks pick_action per granted strike). DECLARATIVE
# only — it names WHAT the actor wants; it never mutates state. See ADR §2.
@dataclass(frozen=True)
class Intent:
    kind: Literal["attack", "cast", "move", "dash", "disengage", "dodge", "skip"]
    target_id: str = ""           # attack / single-target cast
    attack_name: str = ""         # scopes a Multiattack budget (server._attacker_multiattack_count)
    spell_name: str = ""          # cast
    to_cell: Optional[tuple[int, int]] = None  # move (grid / #461)
    to_zone: str = ""             # move (zone / S2.7)
    note: str = ""                # human-readable rationale for the digest / debugging


# ── The read-only CombatView the loop assembles per turn (no new persistent model) ───
# Plain frozen dataclasses the loop builds from the live Combat + the actor's authoritative
# attack lines (server._monster_combat_entry). pick_action reads ONLY these — never the live
# Campaign — so it cannot mutate state and is trivially unit-testable.

@dataclass(frozen=True)
class AttackOption:
    """One authoritative weapon/natural attack from the actor's stat block (or PC sheet).
    Numbers come straight from the engine — the AI never invents a bonus. `damage_expr` is the
    single-component dice for EV; `damage_rolls` (if multi-type) is carried verbatim so the
    loop passes it to attack(damage_rolls=...)."""
    name: str
    to_hit: int
    damage_expr: str = ""
    damage_type: str = ""
    damage_rolls: tuple = ()  # tuple of {"dice","type"} dicts (frozen-safe); () == single-type
    reach_ft: int = 5         # melee 5ft default; ranged attacks set a longer reach
    is_ranged: bool = False


@dataclass(frozen=True)
class SpellOption:
    """A castable spell the actor knows/prepared with an available slot that can reach a target.
    `kind` selects the family + the EV scoring branch (v2.0b):
      * "heal" (`is_heal=True`) — `heal_amount` is the expected HP restored at `slot_level`
        (the healer triage targets a hurt/downed ALLY, not a foe);
      * "attack" — an attack-roll spell (Fire Bolt, Scorching Ray): scored P(hit | spell_attack_bonus,
        target AC) * `value` (avg damage), exactly like a weapon attack;
      * "auto"   — an auto-hit spell (Magic Missile): scored as `value` (avg total damage), no P(hit);
      * "save"   — a damage-on-a-save spell (Burning Hands): scored from the REAL spell_save_dc vs the
        target's save bonus → P(fail); `on_save` ("half" / else) weights save-for-half vs save-or-suck;
      * "" / "utility" / "buff" — not scored as offence (carried but ignored by the offence policy).
    EV uses `value` (avg damage / control weight) for offence; `heal_amount` for a heal. v2.0b scores
    all offensive families on the SAME EV scale as weapon attacks (v2.0a scored only heals)."""
    name: str
    value: float = 0.0           # EV magnitude: avg damage, or a control-weight for a save-or-suck
    range_ft: int = 60
    save_ability: str = ""       # "" => attack/auto spell; else the save ability (e.g. "dexterity")
    requires_slot: bool = True   # cantrips set False
    # Offensive-family fields (v2.0b). kind selects the scoring branch; on_save weights save spells.
    kind: str = ""               # "attack" | "auto" | "save" | "heal" | "" (utility/buff — not offence)
    on_save: str = ""            # save spells: "half" (save-for-half) else save-or-nothing (full|0)
    damage_type: str = ""        # for the apply path (resistance/vulnerability); advisory for EV
    concentration: bool = False  # this spell requires concentration (v2.0b: don't break a better one)
    # Healing-spell fields (v2.0a). is_heal => this option restores HP to a chosen TARGET (an ally).
    is_heal: bool = False
    heal_amount: float = 0.0     # expected HP restored when cast at slot_level (avg dice + casting mod)
    slot_level: int = 0          # the slot level this option spends (0 == a cantrip / no slot)
    is_bonus_action: bool = False  # bonus-action casting time (Healing Word) — preferred for the save


@dataclass(frozen=True)
class CombatantView:
    """A combatant the AI reasons about: position + the numbers needed for EV. For an ALLY the
    healer triage also reads `current_hp`/`max_hp` + `downed` (0 HP / dying) to decide who to heal."""
    id: str
    name: str
    side: str                    # "party" (player/companion) or "enemy" (monster/npc)
    current_hp: int
    max_hp: int
    armor_class: int
    cell: Optional[tuple[int, int]] = None   # grid position, or None (zone/theater)
    zone: str = ""
    save_bonuses: dict = field(default_factory=dict)  # ability -> save bonus, for save-spell EV
    conditions: tuple[str, ...] = ()  # 5e condition names (e.g. "unconscious"), for triage/EV
    downed: bool = False         # at 0 HP / dying (a downed ally is the top heal-triage priority)


@dataclass(frozen=True)
class CombatView:
    """The read-only snapshot pick_action decides over. Built by the loop each turn."""
    actor_id: str
    actor_cell: Optional[tuple[int, int]]
    actor_zone: str
    actor_side: str
    speed: int
    dashed: bool
    grid_enabled: bool
    grid_width: int = 0
    grid_height: int = 0
    cell_size: int = 5
    foes: tuple[CombatantView, ...] = ()    # living opposite-side combatants
    allies: tuple[CombatantView, ...] = ()  # same-side combatants (occupancy + heal triage; incl. downed)
    attacks: tuple[AttackOption, ...] = ()  # the actor's authoritative attack lines
    spells: tuple[SpellOption, ...] = ()    # the actor's castable heal/damage/control spells
    # Caster numbers (v2.0a). caster_level drives heal/cantrip scaling + heal amounts; the
    # save DC / attack bonus drive v2.0b offensive scoring (0 == a non-caster, today's behavior).
    spell_attack_bonus: int = 0
    spell_save_dc: int = 0
    caster_level: int = 0
    # Concentration awareness (v2.0b): the name of the spell the actor is ALREADY concentrating on
    # (or "" if none). pick_action will not start a NEW concentration spell that breaks a >= -value
    # active one — "" == today's behavior (no active concentration to protect).
    active_concentration: str = ""


# ── Pure EV helpers ──────────────────────────────────────────────────────────────────

def p_hit(to_hit: int, target_ac: int) -> float:
    """P(a d20+to_hit lands vs target_ac), on the standard single-d20 curve. A natural 1
    always misses and a natural 20 always hits, so the chance is clamped to [0.05, 0.95]
    (1/20 each end). needed = AC - to_hit is the lowest face that hits; faces needed..20 hit."""
    needed = target_ac - to_hit
    faces_that_hit = 21 - needed  # needed<=1 -> 20 faces; needed>=20 -> 1 face
    # nat-1 ALWAYS misses (so at most 19/20 hit) and nat-20 ALWAYS hits (so at least 1/20).
    faces_that_hit = max(1, min(19, faces_that_hit))
    return faces_that_hit / 20.0


def _expected_damage(opt: AttackOption) -> float:
    """E[damage] for an attack option: sum of every component's average. Pure math via
    dice.average_total so the term grammar stays single-sourced. A malformed expr scores 0
    (the AI degrades rather than raising — combat is advisory-not-block)."""
    total = 0.0
    specs = opt.damage_rolls or (
        ({"dice": opt.damage_expr, "type": opt.damage_type},) if opt.damage_expr else ()
    )
    for spec in specs:
        expr = str(spec.get("dice", "") or "")
        if not expr:
            continue
        try:
            total += float(dice_mod.average_total(expr))
        except (ValueError, TypeError):
            continue
    return total


def _attack_ev(opt: AttackOption, target: CombatantView) -> float:
    """EV of striking `target` with `opt`: P(hit) * E[damage]."""
    return p_hit(opt.to_hit, target.armor_class) * _expected_damage(opt)


def _p_save_fail(save_dc: int, save_bonus: int) -> float:
    """P(the target FAILS a DC `save_dc` save with `save_bonus`), on the single-d20 curve.
    needed = save_dc - save_bonus is the lowest face that SUCCEEDS, so faces 1..(needed-1) fail.
    A nat-20 always succeeds and a nat-1 always fails, so P(fail) is clamped to [0.05, 0.95].
    When the DC is unknown (0 — a non-curated/srd524 spell), fall back to a neutral 0.5."""
    if save_dc <= 0:
        return 0.5
    needed = save_dc - save_bonus          # the lowest face that SAVES
    faces_that_fail = needed - 1           # 1..needed-1 fail
    faces_that_fail = max(1, min(19, faces_that_fail))  # nat-1 always fails, nat-20 always saves
    return faces_that_fail / 20.0


def _spell_ev(sp: SpellOption, target: CombatantView, view: CombatView) -> float:
    """EV of casting offensive spell `sp` at `target`, on the SAME scale as a weapon's _attack_ev
    (expected HP removed this turn) so pick_action can compare weapon-vs-spell directly:

      * "attack" (Fire Bolt / Scorching Ray): P(hit | spell_attack_bonus, AC) * E[damage], exactly
        like a weapon — the attack-roll spell competes one-for-one with a swing.
      * "auto"   (Magic Missile): E[damage], no P(hit) term — the darts always land.
      * "save"   (Burning Hands / Sacred Flame): from the REAL spell_save_dc vs the target's save
        bonus, P(fail). For a save-FOR-HALF spell (`on_save=="half"`) the EV is the proper
        expectation P(fail)*full + P(save)*half; for save-or-nothing it is P(fail)*full.

    A non-offensive option (heal / buff / utility) scores 0 here — it is handled elsewhere (heal
    triage) or not cast offensively. Pure: reads only the option + the target + the view's numbers."""
    if sp.is_heal or sp.kind not in ("attack", "auto", "save"):
        return 0.0
    full = float(sp.value)
    if full <= 0:
        return 0.0
    if sp.kind == "auto":
        return full
    if sp.kind == "attack":
        return p_hit(int(view.spell_attack_bonus), target.armor_class) * full
    # "save": P(fail) from the real DC vs the target's save bonus for this ability.
    save_bonus = int(target.save_bonuses.get(sp.save_ability, 0)) if sp.save_ability else 0
    p_fail = _p_save_fail(int(view.spell_save_dc), save_bonus)
    if sp.on_save == "half":
        return p_fail * full + (1.0 - p_fail) * (full / 2.0)
    return p_fail * full  # save-or-nothing (the failed-save damage only)


# ── Slot economy (v2.0b): don't blow a leveled slot on a trivial target ───────────────

# A leveled spell slot is a scarce resource. The greedy-v1+ rule: a leveled-slot offensive spell is
# only worth its slot when the target is NOT trivially finishable by a FREE action (a cantrip or a
# weapon) AND the spell's EV clears a slot-scaled bar. A target whose current HP is at/below what a
# free option's EV already removes is "trivial" — finishing it with a leveled slot wastes the slot.
# Cantrips (slot_level 0) are always free to choose. Pure thresholds; no personality/tier yet.
_SLOT_VALUE_FLOOR_PER_LEVEL = 6.0  # min EV a leveled slot should buy, scaled by slot level


def _is_trivial_target(target: CombatantView, best_free_ev: float) -> bool:
    """A foe is TRIVIAL when a FREE action (best cantrip/weapon EV) is already expected to drop it:
    its current HP <= the EV a free option removes (with a small cushion). Finishing a trivial foe
    with a leveled slot is the 'don't Fireball one 8-HP goblin' waste the brief calls out."""
    if best_free_ev <= 0:
        return False
    return int(target.current_hp) <= math.ceil(best_free_ev)


def _slot_is_worth_it(sp: SpellOption, spell_ev: float, target: CombatantView,
                      best_free_ev: float) -> bool:
    """Is spending `sp`'s leveled slot on `target` justified? A cantrip (slot_level 0) is always
    worth it (free). A leveled slot is worth it only when the target is NOT trivially finishable by
    a free option AND the spell's EFFECTIVE value clears a slot-scaled floor (a L1 slot needs ~6,
    a L3 slot ~18). The floor is checked against the EV CAPPED at the target's current HP — damage
    beyond what the target can absorb is OVERKILL that does not earn a bigger slot. That cap is what
    keeps a L3 Fireball (23 EV) OFF a lone 8-HP goblin (only 8 effective, < the 18 L3 floor) while
    still letting it through vs a worthy target — the 'don't blow a slot on one goblin' rule."""
    if sp.slot_level <= 0:
        return True  # cantrip — no slot to conserve
    if _is_trivial_target(target, best_free_ev):
        return False  # a free swing/cantrip already finishes it — don't burn a slot
    effective_ev = min(spell_ev, float(target.current_hp))  # overkill past the target's HP is wasted
    return effective_ev >= _SLOT_VALUE_FLOOR_PER_LEVEL * sp.slot_level


# ── Reach / range geometry (grid-aware, zone/theater fallback) ───────────────────────

def _in_reach(view: CombatView, target: CombatantView, reach_ft: int) -> bool:
    """Is `target` within `reach_ft` of the actor? On the grid, measured Chebyshev distance;
    off-grid (zone/theater) every foe is treated as reachable (the engine/DM gates range),
    which keeps v1 from refusing to act in a theater fight that has no coordinates."""
    if not view.grid_enabled or view.actor_cell is None or target.cell is None:
        return True
    return combat_grid.distance_ft(view.actor_cell, target.cell, view.cell_size) <= reach_ft


def _occupied_cells(view: CombatView) -> set:
    cells = set()
    for c in list(view.foes) + list(view.allies):
        if c.cell is not None:
            cells.add(c.cell)
    return cells


def _nearest_foe(view: CombatView) -> Optional[CombatantView]:
    if not view.foes:
        return None
    if not view.grid_enabled or view.actor_cell is None:
        # No geometry — "nearest" is the lowest-HP foe (the focus-fire target), id-tiebroken.
        return min(view.foes, key=lambda f: (f.current_hp, f.id))
    return min(
        view.foes,
        key=lambda f: (
            combat_grid.distance_ft(view.actor_cell, f.cell, view.cell_size)
            if f.cell is not None else 10**6,
            f.current_hp,
            f.id,
        ),
    )


def _step_toward(view: CombatView, target: CombatantView, reach_ft: int) -> Optional[tuple[int, int]]:
    """Pick the reachable cell (within this turn's movement budget) that gets the actor as
    close as possible to `target` — ideally into reach. Returns a cell or None (can't improve
    / off-grid). Pure: combat_grid.reachable + Chebyshev distance, no state."""
    if not view.grid_enabled or view.actor_cell is None or target.cell is None:
        return None
    budget = combat_grid.movement_budget_cells(view.speed, view.cell_size, view.dashed)
    if budget <= 0:
        return None
    occupied = _occupied_cells(view) - {view.actor_cell}
    reach = combat_grid.reachable(view.actor_cell, budget, occupied, view.grid_width, view.grid_height)
    if not reach:
        return None
    best = min(
        reach,
        key=lambda cell: (combat_grid.distance_ft(cell, target.cell, view.cell_size), cell),
    )
    cur = combat_grid.distance_ft(view.actor_cell, target.cell, view.cell_size)
    new = combat_grid.distance_ft(best, target.cell, view.cell_size)
    return best if new < cur else None


# ── Heal-the-dying-ally triage (v2.0a) ───────────────────────────────────────────────

# An ally below this fraction of max HP is "critical" and worth a heal (after a downed ally,
# who is always top priority). 1/3 is the v2.0a threshold; the < 1/2 tier is an optional
# extra rung below it. Pure constants — no morale/personality yet (a v2.0b/tier lever).
_HEAL_CRITICAL_FRACTION = 1.0 / 3.0
_HEAL_WOUNDED_FRACTION = 1.0 / 2.0


def _heal_priority(ally: CombatantView) -> Optional[int]:
    """Triage rank for healing `ally`: 0 == downed (0 HP / dying), 1 == critical (< 1/3 max),
    2 == wounded (< 1/2 max), or None == healthy enough to skip. Lower rank = more urgent.
    Pure read of the ally's HP/downed flag — no state, no roll."""
    if ally.downed or ally.current_hp <= 0:
        return 0
    max_hp = max(1, int(ally.max_hp))
    frac = ally.current_hp / max_hp
    if frac < _HEAL_CRITICAL_FRACTION:
        return 1
    if frac < _HEAL_WOUNDED_FRACTION:
        return 2
    return None


def _pick_heal(view: CombatView) -> Optional[tuple[CombatantView, SpellOption]]:
    """The (ally, heal-spell) the actor should cast THIS turn, or None when no heal is warranted
    or possible. Only fires when the actor HAS a heal option AND an ally needs one. Picks the most
    urgent ally (downed > critical > wounded; ties -> most missing HP, then stable id), then prefers
    the BONUS-ACTION ranged heal (Healing Word — the classic save) over a touch heal (Cure Wounds)
    that needs an adjacent target, and a lower-slot heal over a higher one (cheap first). Pure."""
    heals = [sp for sp in view.spells if sp.is_heal and sp.heal_amount > 0]
    if not heals:
        return None
    # The neediest healable ally: lowest triage rank, then most missing HP, then stable id.
    best_ally: Optional[tuple] = None  # (rank, -missing, id, CombatantView)
    for ally in view.allies:
        rank = _heal_priority(ally)
        if rank is None:
            continue
        missing = max(0, int(ally.max_hp) - int(ally.current_hp))
        key = (rank, -missing, ally.id)
        if best_ally is None or key < best_ally[:3]:
            best_ally = (*key, ally)
    if best_ally is None:
        return None
    target = best_ally[3]
    # Among heal spells that can REACH this ally, prefer a bonus-action ranged heal, then the
    # cheapest slot, then the larger expected heal, then stable name (full determinism).
    reachable = [sp for sp in heals if _in_reach(view, target, sp.range_ft)]
    if not reachable:
        return None
    chosen = min(
        reachable,
        key=lambda sp: (not sp.is_bonus_action, sp.slot_level, -sp.heal_amount, sp.name),
    )
    return target, chosen


# ── The greedy-v1 policy ─────────────────────────────────────────────────────────────

def pick_action(
    actor: "Character",
    combat_state: CombatView,
    policy: str = "greedy-v1",
) -> Intent:
    """Choose the highest-expected-value action for a non-PC combatant's turn.

    Greedy-v1 (ADR §2), in priority order:
      1. retreat-if-low (disengage+move from the nearest threat) — only when RETREAT_FRACTION>0
      1.5 heal-the-dying-ally (v2.0a) — ONLY when the actor has a heal spell + a slot AND an
         ally needs it: a downed/dying ally first, then a critical (< 1/3 max HP) ally, then a
         wounded (< 1/2) one. Prefers bonus-action ranged Healing Word over touch Cure Wounds.
         No healable target / no heal spell -> falls straight through to the attack logic (today's behavior).
      2. score the best in-reach WEAPON attack (P(hit)*E[damage], focus-fire ties by lowest HP)
      3. score the best in-reach OFFENSIVE spell on the SAME EV scale (v2.0b): attack-roll spells via
         P(hit | spell_attack_bonus), auto-hit via E[damage], save spells via the REAL spell_save_dc
         → P(fail) with save-for-half weighting; a leveled-slot spell must clear the slot-economy gate
         (don't burn a slot on a trivial target). Pick the GLOBAL best of weapon-vs-spell (ties ->
         the free weapon). A new concentration spell never breaks a >= -value active one.
      4. move-to-reach toward the best target (the loop re-asks pick_action so move-then-attack
         resolves both halves)
      5. dodge / skip fallback when nothing productive is reachable

    PURE + deterministic: reads only `actor` (read-only Character) and `combat_state` (a
    read-only CombatView). Returns an Intent; the loop is the sole writer that applies it.
    `policy` is a seam for a future BG3-tactical-v2 (additive; unknown policy falls back to v1).
    """
    view = combat_state

    # No foes left -> nothing to do (the loop ends the fight; the AI just skips).
    if not view.foes:
        return Intent(kind="skip", note="no living foes")

    # 1. Retreat-if-low (morale hook; OFF by default — RETREAT_FRACTION 0.0 == fight to the death).
    if RETREAT_FRACTION > 0.0:
        max_hp = max(1, int(getattr(actor, "max_hp", 1)))
        cur_hp = int(getattr(actor, "current_hp", max_hp))
        if cur_hp <= math.floor(max_hp * RETREAT_FRACTION):
            threat = _nearest_foe(view)
            if threat is not None and view.grid_enabled and view.actor_cell is not None:
                budget = combat_grid.movement_budget_cells(view.speed, view.cell_size, view.dashed)
                occupied = _occupied_cells(view) - {view.actor_cell}
                reach = combat_grid.reachable(
                    view.actor_cell, budget, occupied, view.grid_width, view.grid_height
                )
                if reach and threat.cell is not None:
                    flee_cell = max(
                        reach,
                        key=lambda cell: (
                            combat_grid.distance_ft(cell, threat.cell, view.cell_size), cell
                        ),
                    )
                    return Intent(
                        kind="disengage",
                        to_cell=flee_cell,
                        note=f"retreat-if-low: {cur_hp}/{max_hp} HP, disengage from {threat.name}",
                    )

    # 1.5 Heal-the-dying-ally (v2.0a). HIGH priority — a healer with a slot saves a downed/critical
    #     ally BEFORE it swings a weapon. Gated inside _pick_heal so it fires ONLY when the actor
    #     HAS a heal spell + slot AND an ally needs one; otherwise it returns None and we fall
    #     through to today's attack logic UNCHANGED (additive: a non-caster / no-hurt-ally party is
    #     byte-identical). Returns a `cast` Intent targeting the ally (the loop's cast_spell ->
    #     apply_healing sole-writer path applies it).
    heal = _pick_heal(view)
    if heal is not None:
        ally, sp = heal
        return Intent(
            kind="cast",
            target_id=ally.id,
            spell_name=sp.name,
            note=(
                f"heal {ally.name} ({ally.current_hp}/{ally.max_hp} HP"
                f"{', DOWNED' if ally.downed else ''}) with {sp.name} "
                f"(~{sp.heal_amount:.0f} HP, L{sp.slot_level}"
                f"{', bonus action' if sp.is_bonus_action else ''})"
            ),
        )

    # 2. Best in-reach WEAPON attack. EV = P(hit)*E[damage]; ties -> lowest-HP target (focus-fire),
    #    then stable target id, then stable attack name (full determinism). This is a FREE action
    #    (no slot) and the baseline the offensive-spell scoring (step 3) competes against.
    best_attack: Optional[tuple] = None  # (ev, -hp, foe_id, atk_name, AttackOption, CombatantView)
    for opt in view.attacks:
        for foe in view.foes:
            if not _in_reach(view, foe, opt.reach_ft):
                continue
            ev = _attack_ev(opt, foe)
            if ev <= 0:
                continue
            key = (ev, -foe.current_hp, foe.id, opt.name)  # higher EV, then lower HP, then ids
            if best_attack is None or key > best_attack[:4]:
                best_attack = (*key, opt, foe)
    best_weapon_ev = best_attack[0] if best_attack is not None else 0.0

    # 3. Best castable OFFENSIVE spell that can reach, scored on the SAME EV scale (expected HP
    #    removed) as the weapon — so the AI picks the GLOBAL best of weapon-vs-spell (v2.0b). A
    #    free cantrip that beats the swing is preferred; a leveled-slot spell must clear the slot-
    #    economy gate (`_slot_is_worth_it`) so we don't blow a slot finishing a trivial target. A
    #    cantrip/weapon is the "free" baseline used to judge whether a leveled target is trivial.
    best_free_ev = max(best_weapon_ev, 0.0)
    for sp in view.spells:
        if sp.slot_level <= 0:  # fold cantrip EV into the free baseline (a cantrip costs no slot)
            for foe in view.foes:
                if _in_reach(view, foe, sp.range_ft):
                    best_free_ev = max(best_free_ev, _spell_ev(sp, foe, view))
    # best_spell tuple: (kills, kill_slot_rank, ev, -slot_level, -hp, foe_id, name, SpellOption,
    #                    CombatantView, ev) — ev at index 2 (and the tail) for the weapon comparison.
    best_spell: Optional[tuple] = None
    for sp in view.spells:
        if sp.is_heal or sp.kind not in ("attack", "auto", "save"):
            continue  # heals are step 1.5; buff/utility aren't an offensive option
        # Concentration guard (v2.0b): don't START a new concentration spell that would break an
        # active one of >= EV. (Damage spells here rarely concentrate, but Scorching-Ray-class data
        # could; the guard keeps a high-value lockdown from being clobbered by a marginal swap.)
        if (sp.concentration and view.active_concentration
                and sp.name != view.active_concentration):
            continue
        for foe in view.foes:
            if not _in_reach(view, foe, sp.range_ft):
                continue
            ev = _spell_ev(sp, foe, view)
            if ev <= 0:
                continue
            if not _slot_is_worth_it(sp, ev, foe, best_free_ev):
                continue  # leveled slot not worth it on this (trivial / low-value) target
            # Selection key (v2.0b — "prefer a cantrip when it suffices"): rank by, in order,
            #   1. KILLS this foe? (effective EV, capped at the foe's HP, >= its HP) — a CHEAPER
            #      option that already kills is as good as a pricier one that also kills, so among
            #      KILLERS we then prefer the lower slot (a cantrip kill beats a leveled-slot kill);
            #   2. among NON-killers, the higher RAW EV wins (honest greedy — a much-worse cantrip
            #      does NOT beat a much-better leveled spell just for being free);
            #   3. then the LOWER slot (break an exact-EV tie toward the cantrip), lower foe HP
            #      (focus-fire), and stable ids for full determinism.
            # `slot_rank` folds 1+3: when the option kills, a lower slot ranks HIGHER (-slot_level);
            # when it doesn't, slot is only the final tie-break (ev dominates), so it sits after ev.
            eff = min(ev, float(foe.current_hp))
            kills = eff >= float(foe.current_hp) - 1e-9
            kill_slot_rank = -sp.slot_level if kills else 0  # prefer a cheaper KILL
            key = (kills, kill_slot_rank, ev, -sp.slot_level, -foe.current_hp, foe.id, sp.name)
            if best_spell is None or key > best_spell[:7]:
                best_spell = (*key, sp, foe, ev)

    # Pick the GLOBAL best action. The best offensive spell beats the weapon only when its EV is
    # strictly higher (ties -> the FREE weapon, conserving the spell/slot — a tie is no reason to
    # spend magic). best_spell carries its raw EV at index 2 (and the tail).
    best_spell_ev = best_spell[2] if best_spell is not None else 0.0
    if best_spell is not None and (best_attack is None or best_spell_ev > best_weapon_ev):
        sp = best_spell[7]
        foe = best_spell[8]
        return Intent(
            kind="cast",
            target_id=foe.id,
            spell_name=sp.name,
            note=(
                f"best offensive spell {sp.name} on {foe.name} (EV {best_spell_ev:.1f} > "
                f"weapon EV {best_weapon_ev:.1f}, slot L{sp.slot_level})"
            ),
        )
    if best_attack is not None:
        opt = best_attack[4]
        foe = best_attack[5]
        return Intent(
            kind="attack",
            target_id=foe.id,
            attack_name=opt.name,
            note=(
                f"best in-reach attack {opt.name} on {foe.name} "
                f"(EV {best_attack[0]:.1f}, P(hit) {p_hit(opt.to_hit, foe.armor_class):.0%})"
            ),
        )

    # 4. Move-to-reach: no attack/spell lands from here -> close on the best (lowest-HP) target
    #    so next turn (the loop re-asks pick_action) the strike resolves. Needs the grid.
    if view.attacks or view.spells:
        best_reach = max(
            [a.reach_ft for a in view.attacks] + [s.range_ft for s in view.spells] + [5]
        )
        target = _nearest_foe(view)
        if target is not None:
            cell = _step_toward(view, target, best_reach)
            if cell is not None:
                return Intent(
                    kind="move",
                    target_id=target.id,
                    to_cell=cell,
                    note=f"move toward {target.name} to get in reach",
                )

    # 5. Fallback: nothing productive reachable -> Dodge (defensive) rather than waste the turn.
    return Intent(kind="dodge", note="no productive action reachable; Dodge")
