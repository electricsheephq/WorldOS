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
    """A castable damaging-cantrip or save-or-suck spell the actor knows with an available
    slot. `save_ability` set => a save spell scored by P(target fails) * value; else an
    attack/auto cantrip scored like a weapon. EV uses `value` (avg damage, or a control weight)."""
    name: str
    value: float                 # EV magnitude: avg damage, or a control-weight for a save-or-suck
    range_ft: int = 60
    save_ability: str = ""       # "" => attack/auto cantrip; else the save (e.g. "wisdom")
    requires_slot: bool = True   # cantrips set False


@dataclass(frozen=True)
class CombatantView:
    """A living combatant the AI reasons about: position + the numbers needed for EV."""
    id: str
    name: str
    side: str                    # "party" (player/companion) or "enemy" (monster/npc)
    current_hp: int
    max_hp: int
    armor_class: int
    cell: Optional[tuple[int, int]] = None   # grid position, or None (zone/theater)
    zone: str = ""
    save_bonuses: dict = field(default_factory=dict)  # ability -> save bonus, for save-spell EV


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
    allies: tuple[CombatantView, ...] = ()  # living same-side combatants (for occupancy)
    attacks: tuple[AttackOption, ...] = ()  # the actor's authoritative attack lines
    spells: tuple[SpellOption, ...] = ()    # the actor's castable damaging/control spells


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


# ── The greedy-v1 policy ─────────────────────────────────────────────────────────────

def pick_action(
    actor: "Character",
    combat_state: CombatView,
    policy: str = "greedy-v1",
) -> Intent:
    """Choose the highest-expected-value action for a non-PC combatant's turn.

    Greedy-v1 (ADR §2), in priority order:
      1. retreat-if-low (disengage+move from the nearest threat) — only when RETREAT_FRACTION>0
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
