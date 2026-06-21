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
    Three families share this one option:
      * a HEAL (`is_heal=True`) — `heal_amount` is the expected HP restored at `slot_level`
        (v2.0a: the healer triage targets a hurt/downed ALLY, not a foe);
      * a save-or-suck (`save_ability` set) — scored by P(target fails) * `value`;
      * an attack/auto cantrip (`save_ability` == "") — scored like a weapon via `value`.
    EV uses `value` (avg damage / control weight) for offence; `heal_amount` for a heal. v2.0a
    populates heals correctly + best-effort offence; pick_action only SCORES heals (offence is the
    v2.0b increment — additive: the offensive options are carried but ignored by the policy today)."""
    name: str
    value: float = 0.0           # EV magnitude: avg damage, or a control-weight for a save-or-suck
    range_ft: int = 60
    save_ability: str = ""       # "" => attack/auto cantrip; else the save (e.g. "wisdom")
    requires_slot: bool = True   # cantrips set False
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
    # save DC / attack bonus land for v2.0b offensive scoring (0 == a non-caster, today's behavior).
    spell_attack_bonus: int = 0
    spell_save_dc: int = 0
    caster_level: int = 0


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
      2. best in-reach attack (P(hit)*E[damage], focus-fire ties by lowest target HP)
      3. best castable cantrip / save-or-suck spell that can reach
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

    # 2. Best in-reach attack. EV = P(hit)*E[damage]; ties -> lowest-HP target (focus-fire),
    #    then stable target id, then stable attack name (full determinism).
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

    # 3. Best castable cantrip / save-or-suck spell that can reach.
    best_spell: Optional[tuple] = None  # (ev, SpellOption, CombatantView)
    for sp in view.spells:
        for foe in view.foes:
            if not _in_reach(view, foe, sp.range_ft):
                continue
            if sp.save_ability:
                # Save-or-suck: P(target FAILS the save) * value. We approximate P(fail) from
                # the target's save bonus vs a ~DC-13 baseline (no DC threaded into v1's view).
                save_bonus = int(foe.save_bonuses.get(sp.save_ability, 0))
                p_fail = max(0.05, min(0.95, (13 - save_bonus) / 20.0))
                ev = p_fail * sp.value
            else:
                ev = sp.value  # damaging/auto cantrip: value is its avg damage
            if ev <= 0:
                continue
            if best_spell is None or (ev, -foe.current_hp, foe.id) > (
                best_spell[0], -best_spell[2].current_hp, best_spell[2].id
            ):
                best_spell = (ev, sp, foe)
    if best_spell is not None:
        sp = best_spell[1]
        foe = best_spell[2]
        return Intent(
            kind="cast",
            target_id=foe.id,
            spell_name=sp.name,
            note=f"best spell {sp.name} on {foe.name} (EV {best_spell[0]:.1f})",
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
