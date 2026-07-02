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
#
# v2.0c martial riders (ALL additive — empty/None == today's Intent byte-for-byte):
#   * `use_resource` + `resource` — a class-resource spend (Action Surge / Rage) the loop
#     applies via the locked server.use_resource verb. `kind="use_resource"` is the new
#     dedicated intent for a bare resource spend (Rage entry, Action Surge);
#   * on an `attack` Intent, `maneuver` / `maneuver_resource` declare a Battle Master damage
#     maneuver, `channel` declares a flat to-hit option (Guided Strike), and `sneak_attack`
#     (a damage_rolls component dict) tags a Sneak Attack — all consumed by the existing
#     attack() riders. A plain attack leaves every one empty == today.
@dataclass(frozen=True)
class Intent:
    kind: Literal[
        "attack", "cast", "move", "dash", "disengage", "dodge", "skip", "use_resource"
    ]
    target_id: str = ""           # attack / single-target cast
    attack_name: str = ""         # scopes a Multiattack budget (server._attacker_multiattack_count)
    spell_name: str = ""          # cast
    to_cell: Optional[tuple[int, int]] = None  # move (grid / #461)
    to_zone: str = ""             # move (zone / S2.7)
    note: str = ""                # human-readable rationale for the digest / debugging
    # v2.0c riders (additive; default empty == today). The loop folds these into the
    # SAME locked verbs (use_resource / attack riders) — no new write path.
    resource: str = ""            # use_resource: the pool to spend (e.g. "action_surge", "rage")
    amount: int = 1               # use_resource: how much to spend (default 1)
    maneuver: str = ""            # attack: a Battle Master damage maneuver name (Trip Attack, …)
    maneuver_resource: str = "superiority_dice"  # attack: the die pool the maneuver spends
    channel: str = ""             # attack: a flat to-hit option (Guided Strike) declared via use_resource
    channel_resource: str = ""    # attack: the pool the channel option spends (channel_divinity)
    sneak_attack: tuple = ()      # attack: a damage_rolls component dict for Sneak Attack ({} == none)


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
class AbilityOption:
    """A MARTIAL class ability the actor can spend a class-resource on this turn (v2.0c). The view
    surfaces it from the actor's `class_resources` (the loop reads the authoritative pool); the AI
    decides WHEN to spend and the loop applies it via the locked `use_resource` / attack-rider verbs.

    `kind` selects the decision branch + how the loop applies it:
      * "second_wind"  — fighter BONUS-action self-heal (1d10+level); `heal_amount` is the EV. The
        bonus-action channel fires it when the fighter is hurt.
      * "action_surge" — fighter: a fresh Attack action this turn. Spent via use_resource; the loop's
        re-ask grants the extra strike. Worth it when the fight is hot / it converts to a likely kill.
      * "maneuver"     — Battle Master: a superiority die added to an attack's damage (Trip/Menacing).
        Declared ON a worthy attack via the attack() maneuver rider. `size` is the die (e.g. "d8").
      * "guided_strike" — War-cleric Channel Divinity: +10 to one attack roll. Declared on a key
        likely-to-miss attack via use_resource(channel_divinity, maneuver='Guided Strike').
      * "rage"         — barbarian: enter rage (the obvious on when meleeing). A bare use_resource spend.
    `resource` is the pool id `use_resource` spends; `remaining` is what's left (>0 to be usable)."""
    kind: str                     # "second_wind" | "action_surge" | "maneuver" | "guided_strike" | "rage"
    resource: str                 # the class_resources pool id (e.g. "second_wind", "action_surge")
    remaining: int = 0            # uses left in the pool (>0 == usable)
    is_bonus_action: bool = False  # consumes the BONUS action (Second Wind) vs the action / a rider
    heal_amount: float = 0.0      # second_wind: expected HP restored (1d10 + fighter level)
    size: str = ""                # maneuver: the die the pool rolls (e.g. "d8"), for the EV note
    name: str = ""                # the human ability name (Trip Attack / Guided Strike / Rage / …)


@dataclass(frozen=True)
class AoeSpellOption:
    """A castable AREA spell the actor can aim at a burst ORIGIN (tactical-v2, #1255). The loop
    surfaces it ONLY for the tactics policy (empty on the view == today's greedy-v1, byte-identical).
    PR-D only reasons about the SPHERE family (Fireball et al.) — the bounded origin-search + the
    catch-≥2-foes-zero-allies rule are cleanest for a radial burst; cones/lines (origin-anchored,
    facing-dependent) are DEFERRED. `radius_ft` sizes the sphere; `value` is avg damage at this cast
    (save-for-half weighted like a single-target save spell); `save_ability`/`on_save` drive the
    per-target EV exactly as SpellOption. Pure data — the AI evaluates candidate origins, the loop's
    cast_spell(origin=...) resolves the real occupants + saves (sole writer, no new path)."""
    name: str
    radius_ft: int = 20
    value: float = 0.0            # avg damage per caught target at this slot (full, pre-save)
    range_ft: int = 150          # how far the burst origin can be placed from the caster
    save_ability: str = ""       # the save the area forces (e.g. "dexterity"); "" => no save (auto)
    on_save: str = "half"        # "half" (save-for-half) else save-or-nothing
    slot_level: int = 0          # the slot this cast spends (0 == a cantrip AoE, rare)
    concentration: bool = False  # don't start one that breaks a >= -value active concentration


@dataclass(frozen=True)
class SneakAttackOption:
    """The actor's Sneak Attack rider (rogue, v2.0c). `dice` is the sheet's Sneak-Attack dice (e.g.
    "3d6"); the AI TAGS an eligible attack with it as a damage_rolls component, and the existing
    attack() multi-component path rolls + crit-doubles it. `value` is the EV (avg of the dice) so the
    AI can note the magnitude. None on the view == not a rogue / no sneak dice (byte-identical)."""
    dice: str                     # e.g. "3d6" (the rogue's Sneak Attack dice from the sheet)
    value: float = 0.0            # avg damage of `dice`, for the rationale note


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
    # ── Martial class abilities + bonus-action economy (v2.0c) ───────────────────────────
    # The actor's own HP, surfaced so the Second-Wind / Action-Surge thresholds read from the
    # view (not getattr(actor)) — a non-martial actor never looks at these (byte-identical).
    actor_current_hp: int = 0
    actor_max_hp: int = 0
    # The actor's spendable MARTIAL abilities this turn (Second Wind / Action Surge / Battle
    # Master maneuver / Guided Strike / Rage). Empty == a non-martial actor (today's behavior):
    # pick_action / pick_bonus_action skip every ability branch and the fight is unchanged.
    abilities: tuple[AbilityOption, ...] = ()
    # Whether the barbarian is ALREADY raging this fight (the loop tracks rage-entry per fight, since
    # the engine has no active-rage state — see the v2.0d flag in combat_loop). True suppresses a
    # second Rage spend (don't drain the pool). False/default == not yet raging.
    is_raging: bool = False
    # The rogue's Sneak Attack rider (or None == not a rogue / no sneak dice). When present the AI
    # TAGS an eligible attack (advantage OR an ally within 5 ft of the target, no disadvantage).
    sneak_attack: Optional[SneakAttackOption] = None
    # Whether the actor still has its ACTION / BONUS action this turn. The bonus-action channel
    # (pick_bonus_action) only fires a bonus ability when bonus_action_available; both default True
    # so a fresh turn behaves as today. Action Surge needs the action; Second Wind the bonus.
    action_available: bool = True
    bonus_action_available: bool = True
    # 5e RAW bonus-action-spell rule (#1106): if a LEVELED spell was already cast as this turn's BONUS
    # action (Healing Word / Spiritual Weapon / …), the ACTION this turn may cast ONLY a CANTRIP — never
    # a second leveled spell. The loop sets this True (replace(view, bonus_spell_used=True)) once its
    # per-turn bonus action was a slot-spending cast; pick_action then refuses a leveled main-action cast
    # (heal-triage OR offence). False/default == no leveled bonus spell this turn (today's behavior): the
    # main action is unconstrained. A cantrip / Second Wind / Rage bonus does NOT set this (no slot spent).
    bonus_spell_used: bool = False
    # ── Positioning depth for the tactical-v2 policy (#1255 / grid-461 PR-D) ──────────────
    # The fight's grid_impassable (walls/props) and grid_difficult (terrain) cells, surfaced so the
    # tactics layer can read cover (line_blockers/cover_between) + route around difficult terrain via
    # the SAME combat_grid helpers the engine uses. Empty == open floor (greedy-v1 behavior unchanged).
    blocking: tuple[tuple[int, int], ...] = ()   # grid_impassable — walls/props for cover + LoS + routing
    difficult: tuple[tuple[int, int], ...] = ()  # grid_difficult — double-cost cells the router avoids
    # The actor's castable AREA spells (spheres), surfaced ONLY for policy="tactical-v2". Empty for a
    # non-caster / non-tactics turn == today (greedy-v1 never sees these and cannot cast an AoE).
    aoe_spells: tuple[AoeSpellOption, ...] = ()


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
    / off-grid). Pure: combat_grid.reachable + Chebyshev distance, no state.

    Terrain-aware (#1269): the fight's `blocking` (walls/props) and `difficult` cells are threaded
    into the SAME terrain-aware Dijkstra the engine uses, so a greedy-v1 monster ROUTES AROUND walls
    (impassable cells are never reachable) and, among equally-close cells, prefers a CHEAP clear route
    over one through difficult ground (the routed-cost tie-break). On OPEN FLOOR (no walls / no
    difficult — both sets empty) `reachable` degenerates to the flat-cost Dijkstra and the tie-break
    stays `(distance, cell)` exactly as before — byte-identical to the pre-#1269 behaviour."""
    if not view.grid_enabled or view.actor_cell is None or target.cell is None:
        return None
    budget = combat_grid.movement_budget_cells(view.speed, view.cell_size, view.dashed)
    if budget <= 0:
        return None
    occupied = _occupied_cells(view) - {view.actor_cell}
    blocking = _blocking_set(view)
    difficult = _difficult_set(view)
    reach = combat_grid.reachable(
        view.actor_cell, budget, occupied, view.grid_width, view.grid_height,
        impassable=blocking, difficult=difficult,
    )
    if not reach:
        return None
    if not blocking and not difficult:
        # Open floor: the flat-cost path — key stays (distance, cell), byte-identical to pre-#1269.
        best = min(
            reach,
            key=lambda cell: (combat_grid.distance_ft(cell, target.cell, view.cell_size), cell),
        )
    else:
        # Walls/difficult terrain present: break distance ties by the terrain-aware ROUTED move cost
        # (a same-distance CLEAR route beats one through difficult ground) — the same tie-break the
        # tactical-v2 _terrain_step_toward uses, so v1-fallback routing matches the engine's picture.
        def _routed_cost(cell: tuple[int, int]) -> int:
            route = combat_grid.shortest_path(
                view.actor_cell, cell, occupied, view.grid_width, view.grid_height,
                impassable=blocking, difficult=difficult,
            )
            return combat_grid.path_cost_cells(view.actor_cell, cell, route, difficult=difficult)

        best = min(
            reach,
            key=lambda cell: (
                combat_grid.distance_ft(cell, target.cell, view.cell_size),
                _routed_cost(cell),
                cell,
            ),
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


def _pick_heal(view: CombatView, *, action_only: bool = False) -> Optional[tuple[CombatantView, SpellOption]]:
    """The (ally, heal-spell) the actor should cast THIS turn, or None when no heal is warranted
    or possible. Only fires when the actor HAS a heal option AND an ally needs one. Picks the most
    urgent ally (downed > critical > wounded; ties -> most missing HP, then stable id), then prefers
    the BONUS-ACTION ranged heal (Healing Word — the classic save) over a touch heal (Cure Wounds)
    that needs an adjacent target, and a lower-slot heal over a higher one (cheap first). Pure.

    `action_only` (#1106): when True, a BONUS-action-only heal (Healing Word, casting time "1 bonus
    action") is NOT a candidate — that spell is the bonus channel's job (pick_bonus_action), and the
    MAIN-action heal-triage must use only an ACTION-castable heal (Cure Wounds). Default False keeps
    the bonus channel's behavior (it filters to bonus heals itself after this call) byte-identical."""
    heals = [sp for sp in view.spells if sp.is_heal and sp.heal_amount > 0]
    if action_only:
        # The MAIN action can't cast a bonus-action-only spell (Healing Word) — drop those candidates
        # so the action-heal can only ever be an action-castable heal (Cure Wounds).
        heals = [sp for sp in heals if not sp.is_bonus_action]
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


# ── Martial class abilities (v2.0c) — pure detectors + decision helpers ──────────────
#
# Each helper reads ONLY the view (the loop already surfaced the authoritative pool + numbers)
# and returns whether/which ability to spend. A view with no `abilities` short-circuits every
# one of these to None/False, so a non-martial actor is byte-identical to today.

# Second Wind fires when the fighter is meaningfully hurt — at/below this fraction of max HP. 1/2
# matches the brief ("< ~1/2 HP"); pure constant (a future tier could lower it for a cautious AI).
_SECOND_WIND_FRACTION = 0.5
# Action Surge is a NOVA button: don't waste it round 1 vs trivial foes. Spend it when the fight is
# HOT — the actor is hurt, OR the action's extra attack is likely to CONVERT a worthy foe to a kill.
# A foe whose current HP is within this multiple of one attack's EV is "finishable" by the surge.
_SURGE_FINISH_MULT = 2.0
# A Battle Master spends a die on an attack worth boosting — a foe that's a real threat (not a sliver
# a plain swing already kills). Spend when the foe's HP is above the plain-attack EV (the +die matters)
# AND the strike has a reasonable chance to land (the die only adds damage on a hit).
_MANEUVER_MIN_PHIT = 0.4
# Guided Strike (+10 to hit) is best spent on a KEY attack that would OTHERWISE likely MISS — its
# whole value is turning a miss into a hit. Spend when the best attack's P(hit) is at/below this.
_GUIDED_STRIKE_MAX_PHIT = 0.6


def _ability(view: CombatView, kind: str) -> Optional[AbilityOption]:
    """The first usable (remaining > 0) ability of `kind` on the view, or None. Pure lookup."""
    for ab in view.abilities:
        if ab.kind == kind and ab.remaining > 0:
            return ab
    return None


def _sneak_attack_eligible(view: CombatView, target: CombatantView,
                           attack_has_advantage: bool) -> bool:
    """Is the rogue's Sneak Attack rider TRIGGERED on `target` this strike (5e RAW, no disadvantage
    branch — the engine's loop doesn't pass disadvantage here)? Either the attack has ADVANTAGE, OR
    an ALLY of the rogue is within 5 ft of the target (a flanking-style trigger). Pure geometry +
    the advantage flag. The once-per-turn cap (5e RAW) is enforced by run_combat_round, which nulls
    view.sneak_attack for the rest of the actor's turn once a Sneak Attack LANDS (a miss doesn't)."""
    if view.sneak_attack is None:
        return False
    if attack_has_advantage:
        return True
    # An ally within 5 ft of the target (the "another enemy of the target is within 5 ft" trigger,
    # here read as one of the rogue's own-side allies adjacent to the foe). On the grid, Chebyshev
    # distance; off-grid we can't prove adjacency, so require the explicit advantage path only.
    if not view.grid_enabled or target.cell is None:
        return False
    for ally in view.allies:
        if ally.cell is None or not _alive_for_flank(ally):
            continue
        if combat_grid.distance_ft(ally.cell, target.cell, view.cell_size) <= 5:
            return True
    return False


def _alive_for_flank(ally: CombatantView) -> bool:
    """An ally that can actually threaten a foe for the Sneak-Attack adjacency trigger: not downed
    and above 0 HP. (A downed ally on the floor doesn't menace the target.)"""
    return (not ally.downed) and int(ally.current_hp) > 0


def _target_advantage(view: CombatView, target: CombatantView) -> bool:
    """Does the actor have ADVANTAGE attacking `target` from condition? A foe that's prone /
    restrained / stunned / paralyzed / unconscious grants advantage to melee attackers. Pure read
    of the target's surfaced conditions (the engine's attack() recomputes the real advantage; this
    is the AI's pre-check for the Sneak-Attack trigger so the rider is only TAGGED when warranted)."""
    adv_conditions = {"prone", "restrained", "stunned", "paralyzed", "unconscious", "incapacitated"}
    return bool(adv_conditions & {str(cn).lower() for cn in target.conditions})


def _enrich_attack_intent(view: CombatView, opt: AttackOption, foe: CombatantView,
                          base_ev: float) -> Intent:
    """Build the `attack` Intent for `opt` on `foe`, folding in the v2.0c martial ON-ATTACK riders
    when warranted (Sneak Attack / Guided Strike / Battle Master maneuver). A non-martial actor (no
    sneak_attack, no abilities) gets a PLAIN attack Intent byte-identical to v2.0b — every rider
    branch is gated on a surfaced ability/dice, so empty == today. The riders are mutually compatible
    where 5e allows (a maneuver + a sneak attack can ride the same strike); Guided Strike is reserved
    for a likely-MISS attack (its value is the to-hit, not extra damage). All consumed by the
    EXISTING attack() riders — the loop adds no new write path. Pure: reads only the view + the foe."""
    phit = p_hit(opt.to_hit, foe.armor_class)
    note_bits = [
        f"best in-reach attack {opt.name} on {foe.name} (EV {base_ev:.1f}, P(hit) {phit:.0%})"
    ]
    # SNEAK ATTACK (rogue): tag the strike when the trigger is met (advantage OR an ally within 5 ft
    # of the target). The engine rolls + crit-doubles the extra dice via the multi-component path.
    sneak_rider: tuple = ()
    has_adv = _target_advantage(view, foe)
    if view.sneak_attack is not None and _sneak_attack_eligible(view, foe, has_adv):
        sneak_rider = ({"dice": view.sneak_attack.dice,
                        "type": opt.damage_type or "piercing"},)
        note_bits.append(
            f"+ Sneak Attack {view.sneak_attack.dice} (~{view.sneak_attack.value:.0f}, "
            f"{'advantage' if has_adv else 'ally adjacent'})"
        )
    # GUIDED STRIKE (War cleric Channel Divinity, +10 to hit): reserve it for a KEY attack that
    # would OTHERWISE likely MISS — its whole value is turning a miss into a hit. Spend only when the
    # plain P(hit) is at/below the bar AND the foe is a real (non-trivial) threat worth the channel.
    channel = ""
    channel_resource = ""
    gs = _ability(view, "guided_strike")
    if (gs is not None and phit <= _GUIDED_STRIKE_MAX_PHIT
            and int(foe.current_hp) > math.ceil(base_ev)):
        channel = gs.name or "Guided Strike"
        channel_resource = gs.resource or "channel_divinity"
        note_bits.append(f"+ {channel} (+10 to hit; P(hit) was {phit:.0%})")
    # BATTLE MASTER MANEUVER (superiority die -> +damage on a hit): spend on a worthy strike — a foe
    # that's a real threat (HP above what a plain swing removes) and a strike likely to LAND (the die
    # only adds damage on a hit). Skip Guided-Strike turns (don't double-spend two resources on one
    # marginal swing) and trivial foes (a plain swing already finishes them).
    maneuver = ""
    maneuver_resource = "superiority_dice"
    man = _ability(view, "maneuver")
    if (man is not None and not channel and phit >= _MANEUVER_MIN_PHIT
            and int(foe.current_hp) > math.ceil(base_ev)):
        maneuver = man.name or "Trip Attack"
        maneuver_resource = man.resource or "superiority_dice"
        note_bits.append(f"+ maneuver {maneuver} ({man.size or 'die'} on hit)")
    return Intent(
        kind="attack",
        target_id=foe.id,
        attack_name=opt.name,
        sneak_attack=sneak_rider,
        channel=channel,
        channel_resource=channel_resource,
        maneuver=maneuver,
        maneuver_resource=maneuver_resource,
        note="; ".join(note_bits),
    )


def should_action_surge(combat_state: CombatView) -> Optional[Intent]:
    """Decide whether the fighter should spend Action Surge for an EXTRA Attack action this turn
    (v2.0c), or None. Called by the loop AFTER the actor's normal strikes resolve — Action Surge is a
    NOVA button, so it's gated to a HOT moment so it isn't wasted round 1 on a trivial foe:
      * the actor is HURT (<= 1/2 max HP — surging to end the fight faster is worth it), OR
      * a worthy foe is FINISHABLE — the best in-reach attack's EV is within `_SURGE_FINISH_MULT` of
        a living foe's current HP, so the extra action is likely to convert to a KILL.
    Returns a `use_resource` Intent for action_surge (the loop applies it via the locked verb, then
    re-runs the strike budget which now sees the granted surge action). A non-fighter / no-surge /
    no-foes view returns None == today (the loop never surges). Pure: reads only the view.

    NOTE: this does NOT gate on `action_available` — Action Surge's whole purpose is to grant a FRESH
    action AFTER the turn's normal Attack action is already spent (attack() sets action_used), so the
    loop calls this once the normal strikes are exhausted. The loop's own `surged` guard prevents a
    second surge in the same turn (don't drain the pool)."""
    view = combat_state
    surge = _ability(view, "action_surge")
    if surge is None or not view.foes:
        return None
    # Best in-reach single-attack EV (the per-strike value a surged action would add again).
    best_ev = 0.0
    for opt in view.attacks:
        for foe in view.foes:
            if _in_reach(view, foe, opt.reach_ft):
                best_ev = max(best_ev, _attack_ev(opt, foe))
    if best_ev <= 0:
        return None  # nothing to swing at from here — a surged action would do nothing
    hurt = view.actor_max_hp > 0 and (view.actor_current_hp / max(1, view.actor_max_hp)) <= 0.5
    finishable = any(
        int(foe.current_hp) <= math.ceil(best_ev * _SURGE_FINISH_MULT) for foe in view.foes
    )
    if not (hurt or finishable):
        return None
    why = "hurt — surge to end it" if hurt else "a foe is finishable with an extra action"
    return Intent(
        kind="use_resource",
        resource=surge.resource,
        amount=1,
        note=f"Action Surge: extra Attack action ({why})",
    )


def pick_bonus_action(actor: "Character", combat_state: CombatView) -> Optional[Intent]:
    """Choose a worthwhile BONUS action for the actor THIS turn, or None (v2.0c). Separate from
    pick_action so the bonus-action ECONOMY is a clean, removable channel: the loop calls this ONCE
    per turn (alongside the main action), and a non-martial / no-bonus actor returns None so the turn
    is byte-identical to today. Priority:
      1. Second Wind (fighter) — self-heal when hurt (< ~1/2 HP) and a use remains.
      2. a BONUS-ACTION heal (Healing Word) on a downed/critical ally — the classic save, when the
         actor has a bonus-action heal spell + a needy ally (reuses the v2.0a heal triage).
    Returns a `use_resource` Intent (Second Wind) or a bonus `cast` Intent (Healing Word). PURE."""
    view = combat_state
    if not view.bonus_action_available:
        return None
    if not view.foes:  # the fight is over — no bonus action worth spending
        return None
    # 1. Second Wind — a fighter's bonus-action self-heal. Fire when meaningfully hurt.
    sw = _ability(view, "second_wind")
    if sw is not None and view.actor_max_hp > 0:
        frac = view.actor_current_hp / max(1, view.actor_max_hp)
        if frac <= _SECOND_WIND_FRACTION:
            return Intent(
                kind="use_resource",
                resource=sw.resource,
                amount=1,
                note=(
                    f"Second Wind (bonus action): self-heal ~{sw.heal_amount:.0f} HP at "
                    f"{view.actor_current_hp}/{view.actor_max_hp}"
                ),
            )
    # 2. Rage (barbarian) — the obvious bonus-action "on" when meleeing. Enter ONCE per fight (the
    #    is_raging flag, which the loop tracks since the engine has no active-rage state). Only when a
    #    foe is within MELEE reach (rage benefits melee), and a use remains. FLAG (v2.0d): the engine
    #    models the rage POOL but not its +damage / resistance — entering rage drains a use and is
    #    narrated; the mechanical bonus is deferred. Gated so it can't burn the pool turn after turn.
    rage = _ability(view, "rage")
    if rage is not None and not view.is_raging:
        in_melee = any(
            _in_reach(view, foe, opt.reach_ft)
            for opt in view.attacks if not opt.is_ranged
            for foe in view.foes
        )
        if in_melee:
            return Intent(
                kind="use_resource",
                resource=rage.resource,
                amount=1,
                note="Rage (bonus action): enter rage — a foe is in melee reach",
            )
    # 3. A bonus-action heal (Healing Word) on a downed/critical ally — reuse the v2.0a triage but
    #    restricted to BONUS-action heals so it rides the bonus channel alongside the main action.
    heal = _pick_heal(view)
    if heal is not None:
        ally, sp = heal
        if sp.is_bonus_action:
            return Intent(
                kind="cast",
                target_id=ally.id,
                spell_name=sp.name,
                note=(
                    f"bonus-action heal {sp.name} on {ally.name} "
                    f"({ally.current_hp}/{ally.max_hp} HP{', DOWNED' if ally.downed else ''})"
                ),
            )
    return None


# ── Tactical-v2 positioning layer (#1255 / grid-461 PR-D) ────────────────────────────
#
# A pluggable, GRID-ONLY tactics pass that upgrades the greedy pick where #461 positioning
# depth pays off — AoE placement, cover-aware target choice, flank-seeking movement, and
# terrain-aware routing. It reads the SAME combat_grid primitives the engine's own attack()/
# cast_spell() use (cover_between / flanking / reachable / shortest_path), so the AI's picture
# matches what the verbs will resolve. It NEVER short-circuits the fallback: anything it can't
# improve (no grid, off-grid actor, no clumped foes, no flank) returns None and pick_action
# runs the unchanged greedy-v1+v2.0 path. Pure + deterministic — every tie-break is a stable
# key; no unseeded randomness. DOCUMENTED TIE-BREAK ORDER is stated at each decision below.

def _blocking_set(view: CombatView) -> set:
    """The fight's impassable (wall/prop) cells as a set, for cover + LoS. Empty == open floor."""
    return {(int(x), int(y)) for x, y in view.blocking}


def _difficult_set(view: CombatView) -> set:
    """The fight's difficult-terrain cells as a set, for terrain-aware routing. Empty == flat."""
    return {(int(x), int(y)) for x, y in view.difficult}


def _cover_of(view: CombatView, target: CombatantView, blocking: set) -> str:
    """The SRD cover tier the actor's attack ray grants `target` (none/half/three_quarters/total),
    from the actor's cell. 'none' when off-grid or no walls (byte-identical open-floor behavior)."""
    if view.actor_cell is None or target.cell is None or not blocking:
        return "none"
    return combat_grid.cover_between(view.actor_cell, target.cell, blocking)


def _attack_ev_with_cover(opt: AttackOption, target: CombatantView, cover: str) -> float:
    """EV of striking `target` with `opt`, DISCOUNTED by the target's cover: cover raises the
    effective AC (+2 half / +5 three-quarters), lowering P(hit). 'total' cover => the target can't
    be targeted directly, EV 0 (the tactics layer skips it). Pure — reuses cover_ac_bonus."""
    if cover == "total":
        return 0.0
    eff_ac = int(target.armor_class) + combat_grid.cover_ac_bonus(cover)
    return p_hit(opt.to_hit, eff_ac) * _expected_damage(opt)


def _aoe_origin_candidates(view: CombatView) -> list[tuple[int, int]]:
    """The BOUNDED set of candidate burst origins the AoE search evaluates: every FOE cell plus its
    8 neighbours, deduped + clipped to the grid. This is O(enemies × 9) origins — NEVER O(grid²):
    a good Fireball centre is on or one cell off a foe (to catch a cluster), so we never scan the
    whole board. Deterministic (sorted). Empty off-grid / no foes."""
    if not view.grid_enabled:
        return []
    w, h = view.grid_width, view.grid_height
    origins: set[tuple[int, int]] = set()
    for foe in view.foes:
        if foe.cell is None:
            continue
        fx, fy = foe.cell
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                ox, oy = fx + dx, fy + dy
                if 0 <= ox < w and 0 <= oy < h:
                    origins.add((ox, oy))
    return sorted(origins)


def _aoe_ev_at(view: CombatView, sp: AoeSpellOption, origin: tuple[int, int],
               blocking: set) -> tuple[int, int, float]:
    """Evaluate an AoE `sp` burst at `origin`: (#foes caught, #allies caught, total EV). A cell is
    caught only if it has LINE OF EFFECT from the burst origin (a wall shields it — SRD 5.2, the same
    cull cast_spell(origin=...) applies), so cover is respected. EV sums each caught FOE's expected
    HP removed (save-for-half weighted via the real save DC vs that foe's save bonus). The ACTOR
    itself is never friendly-fire counted here (it's not in foes/allies); an ally in the burst is
    counted so the caller can REJECT any origin that catches ≥1 ally. Pure geometry + EV."""
    reach = max(0, sp.radius_ft // view.cell_size) if view.cell_size > 0 else 0
    ox, oy = origin
    foes_caught = 0
    allies_caught = 0
    total_ev = 0.0
    full = float(sp.value)
    for foe in view.foes:
        if foe.cell is None:
            continue
        if combat_grid.chebyshev_cells(origin, foe.cell) > reach:
            continue
        if blocking and foe.cell != origin and not combat_grid.has_line_of_effect(
            origin, foe.cell, blocking
        ):
            continue  # a wall between the burst point and the foe shields it
        foes_caught += 1
        if full > 0:
            if sp.save_ability:
                sb = int(foe.save_bonuses.get(sp.save_ability, 0))
                p_fail = _p_save_fail(view.spell_save_dc, sb)
                total_ev += p_fail * full + (1.0 - p_fail) * (
                    full / 2.0 if sp.on_save == "half" else 0.0
                )
            else:
                total_ev += full  # auto-hit area (rare)
    for ally in view.allies:
        if ally.cell is None:
            continue
        if combat_grid.chebyshev_cells(origin, ally.cell) > reach:
            continue
        if blocking and ally.cell != origin and not combat_grid.has_line_of_effect(
            origin, ally.cell, blocking
        ):
            continue
        allies_caught += 1
    return foes_caught, allies_caught, total_ev


def _best_aoe(view: CombatView, blocking: set) -> Optional[tuple[AoeSpellOption, tuple[int, int], float]]:
    """The best (AoE spell, origin, EV) the actor should cast this turn, or None. RULE (the brief):
    an AoE is only chosen when it catches ≥2 FOES and ZERO allies (never friendly-fire). Among
    those, TIE-BREAK ORDER: (1) most foes caught, (2) highest total EV, (3) lower slot level (cheaper),
    (4) stable spell name, (5) stable origin. The origin must be within the spell's range of the actor
    (the burst has to be placeable). A #1106 leveled-bonus-spell turn forbids a leveled AoE; a
    concentration AoE never breaks a >= active one. Bounded: O(foes × neighbours × combatants)."""
    if not view.aoe_spells or not view.grid_enabled or view.actor_cell is None:
        return None
    origins = _aoe_origin_candidates(view)
    if not origins:
        return None
    best: Optional[tuple] = None  # (foes, ev, -slot, name, origin, sp)
    for sp in view.aoe_spells:
        if view.bonus_spell_used and sp.slot_level > 0:
            continue  # #1106: only a cantrip may follow a leveled bonus-action spell
        if sp.concentration and view.active_concentration and sp.name != view.active_concentration:
            continue  # don't break a better active concentration
        range_cells = max(0, sp.range_ft // view.cell_size) if view.cell_size > 0 else 0
        for origin in origins:
            if combat_grid.chebyshev_cells(view.actor_cell, origin) > range_cells:
                continue  # burst can't be placed that far
            foes_caught, allies_caught, ev = _aoe_ev_at(view, sp, origin, blocking)
            if foes_caught < 2 or allies_caught > 0:
                continue  # the rule: catch ≥2 foes AND zero allies
            key = (foes_caught, ev, -sp.slot_level, sp.name, origin)
            if best is None or key > best[:5]:
                best = (*key, sp)
    if best is None:
        return None
    return best[5], best[4], best[1]


def _flank_move_cell(view: CombatView, opt: AttackOption, target: CombatantView,
                     occupied: set, blocking: set, difficult: set) -> Optional[tuple[int, int]]:
    """A reachable cell that completes a FLANK on `target` with a living ally already in melee reach
    of it — or None. When two threateners are on opposite sides of the target, a melee attacker gains
    advantage (the #1254 rule the engine auto-applies), so moving INTO a flank is worth more than a
    plain adjacent step. TIE-BREAK ORDER among flanking cells: (1) lowest ROUTED move cost (terrain-
    aware, via shortest_path — a same-cost clear route beats a difficult one), (2) stable cell. Only
    Medium (1-cell) geometry (PR-D); reach is Chebyshev vs the target. Pure + deterministic."""
    if not view.grid_enabled or view.actor_cell is None or target.cell is None:
        return None
    budget = combat_grid.movement_budget_cells(view.speed, view.cell_size, view.dashed)
    if budget <= 0:
        return None
    reach = combat_grid.reachable(
        view.actor_cell, budget, occupied, view.grid_width, view.grid_height,
        impassable=blocking, difficult=difficult,
    )
    if not reach:
        return None
    reach_cells = max(1, opt.reach_ft // view.cell_size) if view.cell_size > 0 else 1
    # Allies who ALREADY threaten the target in melee — a flank needs a second threatener across.
    threatening_allies = [
        a for a in view.allies
        if a.cell is not None and _alive_for_flank(a)
        and combat_grid.chebyshev_cells(a.cell, target.cell) <= reach_cells
    ]
    if not threatening_allies:
        return None
    candidates: list[tuple[int, tuple[int, int]]] = []
    for cell in reach:
        if combat_grid.chebyshev_cells(cell, target.cell) > reach_cells:
            continue  # must be in melee reach of the target from the new cell
        if any(
            combat_grid.flanking(cell, "medium", a.cell, "medium", target.cell, "medium")
            for a in threatening_allies
        ):
            route = combat_grid.shortest_path(
                view.actor_cell, cell, occupied, view.grid_width, view.grid_height,
                impassable=blocking, difficult=difficult,
            )
            cost = combat_grid.path_cost_cells(view.actor_cell, cell, route, difficult=difficult)
            candidates.append((cost, cell))
    if not candidates:
        return None
    candidates.sort()  # (routed cost, cell) — cheapest terrain-aware route, then stable cell
    return candidates[0][1]


def _pick_action_tactical(actor: "Character", view: CombatView) -> Optional[Intent]:
    """The tactical-v2 positioning pass (grid-only). Returns an Intent when tactics IMPROVE on the
    greedy pick, else None (pick_action then runs the unchanged greedy path). Decision-priority order:

      T1. AoE cast — the best sphere catching ≥2 foes and ZERO allies (bounded origin search), when
          its EV beats the best single-target attack. (Retreat-if-low + heal-triage run BEFORE this in
          pick_action, so a hurt healer still saves an ally first — tactics only upgrades OFFENCE.)
      T2. Cover-aware melee/ranged: among IN-REACH foes, pick the best COVER-DISCOUNTED attack EV,
          SKIPPING any total-cover foe; if that changes which foe/attack greedy would pick, act on it.
      T3. Flank-seek move: when NO attack lands from here, prefer a reachable cell that COMPLETES a
          flank with an ally (advantage) over a plain step — terrain-aware routed cost as the tie-break.

    Runs only when grid_enabled; off-grid returns None (greedy handles zone/theater). Pure."""
    if not view.grid_enabled or not view.foes:
        return None
    blocking = _blocking_set(view)
    difficult = _difficult_set(view)

    # T1 — AoE placement. Compare the best legal AoE (≥2 foes, 0 allies) to the best single-target
    # attack EV; cast the area only when it's strictly better (a tie prefers the free swing / cheaper
    # slot). This is an ACTION, so it doesn't fire when a leveled bonus spell already used the action's
    # leveled budget (#1106) — enforced inside _best_aoe.
    best_single_ev = 0.0
    for opt in view.attacks:
        for foe in view.foes:
            if not _in_reach(view, foe, opt.reach_ft):
                continue
            cover = _cover_of(view, foe, blocking)
            best_single_ev = max(best_single_ev, _attack_ev_with_cover(opt, foe, cover))
    aoe = _best_aoe(view, blocking)
    if aoe is not None:
        sp, origin, aoe_ev = aoe
        if aoe_ev > best_single_ev:
            return Intent(
                kind="cast",
                spell_name=sp.name,
                to_cell=origin,
                note=(
                    f"tactical-v2 AoE {sp.name} at {origin} (EV {aoe_ev:.1f} > best attack "
                    f"{best_single_ev:.1f}; catches ≥2 foes, no allies)"
                ),
            )

    # T2 — Cover-aware attack. Score every in-reach (attack, foe) by cover-DISCOUNTED EV, skipping a
    # total-cover foe (can't be targeted). TIE-BREAK ORDER: (1) higher discounted EV, (2) lower foe HP
    # (focus-fire), (3) stable foe id, (4) stable attack name. Fall through to greedy when nothing is
    # in reach (greedy will move / dodge). The enriched martial riders still apply via the shared path.
    best: Optional[tuple] = None  # (ev, -hp, foe_id, atk_name, opt, foe)
    for opt in view.attacks:
        for foe in view.foes:
            if not _in_reach(view, foe, opt.reach_ft):
                continue
            cover = _cover_of(view, foe, blocking)
            if cover == "total":
                continue  # can't target a totally-covered foe — prefer a reachable alternative
            ev = _attack_ev_with_cover(opt, foe, cover)
            if ev <= 0:
                continue
            key = (ev, -foe.current_hp, foe.id, opt.name)
            if best is None or key > best[:4]:
                best = (*key, opt, foe)
    if best is not None:
        # Only OVERRIDE greedy when cover actually changed the pick — otherwise let greedy (which also
        # runs the offensive-spell comparison) own the choice. We detect a change by re-scoring greedy's
        # cover-BLIND best and comparing the chosen foe/attack; a mismatch means a total/discounted-cover
        # foe was avoided, so the tactics pick is the correct one to act on.
        opt, foe = best[4], best[5]
        blind_best: Optional[tuple] = None
        for o in view.attacks:
            for f in view.foes:
                if not _in_reach(view, f, o.reach_ft):
                    continue
                ev = _attack_ev(o, f)
                if ev <= 0:
                    continue
                k = (ev, -f.current_hp, f.id, o.name)
                if blind_best is None or k > blind_best[:4]:
                    blind_best = (*k, o, f)
        if blind_best is not None and (blind_best[5].id != foe.id or blind_best[4].name != opt.name):
            return _enrich_attack_intent(view, opt, foe, best[0])
        # cover didn't change the pick — defer to greedy (which also weighs offensive spells).
        return None

    # T3 — Flank-seek move. No attack lands from here: try to move into a cell that completes a flank
    # with an ally (advantage next turn) rather than a plain close-the-distance step.
    occupied = _occupied_cells(view) - {view.actor_cell}
    target = _nearest_foe(view)
    if target is not None:
        for opt in view.attacks:
            if opt.is_ranged:
                continue  # flanking is a melee advantage rule
            cell = _flank_move_cell(view, opt, target, occupied, blocking, difficult)
            if cell is not None:
                return Intent(
                    kind="move",
                    target_id=target.id,
                    to_cell=cell,
                    note=f"tactical-v2 flank move to {cell} on {target.name} (completes a flank)",
                )

    # T4 — Terrain-aware close-the-distance. When walls/difficult terrain are present, greedy's
    # _step_toward (which routes on OPEN floor only) can pick a cell it must cross a wall/difficult
    # ground to reach. Here we route with the SAME impassable/difficult Dijkstra the engine uses:
    # among reachable cells, minimize (distance-to-target, ROUTED move cost, cell) — so a same-distance
    # CLEAR route beats one through difficult terrain, and no wall is crossed. We only OVERRIDE greedy
    # when terrain actually exists (blocking or difficult non-empty); on open floor we return None and
    # greedy's identical open-floor step-toward runs (byte-identical). Bounded: O(reachable cells).
    if (blocking or difficult) and target is not None and (view.attacks or view.spells):
        best_reach_ft = max(
            [a.reach_ft for a in view.attacks] + [s.range_ft for s in view.spells] + [5]
        )
        cell = _terrain_step_toward(view, target, best_reach_ft, occupied, blocking, difficult)
        if cell is not None:
            return Intent(
                kind="move",
                target_id=target.id,
                to_cell=cell,
                note=f"tactical-v2 terrain-routed move to {cell} toward {target.name}",
            )
    return None


def _terrain_step_toward(view: CombatView, target: CombatantView, reach_ft: int,
                         occupied: set, blocking: set, difficult: set) -> Optional[tuple[int, int]]:
    """The reachable cell that best closes on `target` while ROUTING around walls + difficult terrain
    (tactical-v2). Reachability is the terrain-aware Dijkstra (impassable walls can't be entered;
    difficult cells cost double). TIE-BREAK ORDER: (1) closest to the target (Chebyshev), (2) lowest
    ROUTED move cost (a same-distance CLEAR route beats one through difficult ground), (3) stable cell.
    Returns None when nothing gets strictly closer than staying put (matches _step_toward's contract)."""
    if not view.grid_enabled or view.actor_cell is None or target.cell is None:
        return None
    budget = combat_grid.movement_budget_cells(view.speed, view.cell_size, view.dashed)
    if budget <= 0:
        return None
    reach = combat_grid.reachable(
        view.actor_cell, budget, occupied, view.grid_width, view.grid_height,
        impassable=blocking, difficult=difficult,
    )
    if not reach:
        return None

    def _routed_cost(cell: tuple[int, int]) -> int:
        route = combat_grid.shortest_path(
            view.actor_cell, cell, occupied, view.grid_width, view.grid_height,
            impassable=blocking, difficult=difficult,
        )
        return combat_grid.path_cost_cells(view.actor_cell, cell, route, difficult=difficult)

    best = min(
        reach,
        key=lambda cell: (
            combat_grid.distance_ft(cell, target.cell, view.cell_size),
            _routed_cost(cell),
            cell,
        ),
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

    MARTIAL on-attack riders (v2.0c): the chosen weapon attack is enriched (`_enrich_attack_intent`)
    with Sneak Attack (rogue, when the trigger is met), Guided Strike (War cleric, on a likely-MISS
    key attack), and a Battle Master maneuver (on a worthy hit) — all via the EXISTING attack()
    riders, gated on a surfaced ability/dice so a non-martial actor's attack is byte-identical to
    v2.0b. The BONUS action (Second Wind / bonus heal / Rage) and Action Surge are decided by the
    sibling `pick_bonus_action` / `should_action_surge`, which the loop calls alongside this.

    PURE + deterministic: reads only `actor` (read-only Character) and `combat_state` (a
    read-only CombatView). Returns an Intent; the loop is the sole writer that applies it.

    `policy` selects the decision layer (additive; an unknown policy falls back to greedy-v1):
      * "greedy-v1" (default) — the EV policy above, byte-identical to today.
      * "tactical-v2" (#1255 / grid-461 PR-D) — when the fight is ON THE GRID, a positioning pass
        (_pick_action_tactical) runs FIRST for OFFENCE only: prefer an AoE catching ≥2 foes and no
        allies; respect cover (skip total-cover, discount partial); seek a flank when moving to melee;
        route around difficult terrain. It returns None (and this falls through to the identical greedy
        path) whenever tactics can't improve the pick — and OFF the grid it is a no-op. The retreat +
        heal-triage steps below still run first, so a hurt monster/healer behaves as in v1; tactics
        only upgrades how it ATTACKS/MOVES on a battlefield.
    """
    view = combat_state

    # No foes left -> nothing to do (the loop ends the fight; the AI just skips).
    if not view.foes:
        return Intent(kind="skip", note="no living foes")

    # tactical-v2 (#1255): the grid positioning pass runs AFTER retreat/heal (below share the same
    # priority as v1) but BEFORE the greedy attack/spell/move steps. It is consulted only for
    # OFFENCE + movement, so we first run the retreat + heal gates (identical to greedy-v1), then
    # let tactics improve the attack; if tactics returns None we fall through to the greedy pick.
    # Off-grid or an unknown policy => `_tactical` is None throughout == today (byte-identical).
    use_tactical = policy == "tactical-v2" and view.grid_enabled

    # 1. Retreat-if-low (morale hook; OFF by default — RETREAT_FRACTION 0.0 == fight to the death).
    if RETREAT_FRACTION > 0.0:
        max_hp = max(1, int(getattr(actor, "max_hp", 1)))
        cur_hp = int(getattr(actor, "current_hp", max_hp))
        if cur_hp <= math.floor(max_hp * RETREAT_FRACTION):
            threat = _nearest_foe(view)
            if threat is not None and view.grid_enabled and view.actor_cell is not None:
                budget = combat_grid.movement_budget_cells(view.speed, view.cell_size, view.dashed)
                occupied = _occupied_cells(view) - {view.actor_cell}
                # Terrain-aware retreat (#1269): route the flee AROUND walls + difficult terrain via the
                # SAME Dijkstra — a fleeing monster can't disengage THROUGH a wall. Empty sets (open floor)
                # degenerate to the flat-cost reachable + the (distance, cell) flee key == byte-identical.
                reach = combat_grid.reachable(
                    view.actor_cell, budget, occupied, view.grid_width, view.grid_height,
                    impassable=_blocking_set(view), difficult=_difficult_set(view),
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
    #
    #     #1106 RAW gates: (a) `action_only=True` — the MAIN-action heal can NEVER be a bonus-action-
    #     only spell (Healing Word); that's the bonus channel's job, so a main-action heal uses only an
    #     ACTION-castable heal (Cure Wounds). (b) When a LEVELED spell was already cast as this turn's
    #     bonus action (`bonus_spell_used`), the action may cast ONLY a cantrip — so a LEVELED main-
    #     action heal (Cure Wounds is a leveled spell) is forbidden; skip the heal-triage entirely and
    #     fall through to a cantrip/weapon. (Heal cantrips don't exist in SRD, so this skips heals.)
    if not view.bonus_spell_used:
        heal = _pick_heal(view, action_only=True)
    else:
        heal = None
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

    # 1.75 tactical-v2 OFFENCE/MOVE (#1255): after retreat + heal (which match greedy-v1), let the
    #     grid positioning pass improve the ATTACK/MOVE — AoE placement, cover-aware targeting, and
    #     flank-seeking. It returns None whenever it can't beat the greedy pick, so we fall straight
    #     through to steps 2-5 unchanged. Off-grid / greedy-v1 policy: use_tactical is False == today.
    if use_tactical:
        tactical = _pick_action_tactical(actor, view)
        if tactical is not None:
            return tactical

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
        # #1106 RAW: after a LEVELED bonus-action spell this turn, the ACTION may cast ONLY a cantrip —
        # a leveled offensive cast is forbidden. A cantrip (slot_level 0) is still allowed, so the
        # caster can follow Healing Word with Sacred Flame / Fire Bolt. (Empty view.bonus_spell_used
        # == today: no constraint, every leveled offensive cast is still a candidate.)
        if view.bonus_spell_used and sp.slot_level > 0:
            continue
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
        return _enrich_attack_intent(view, opt, foe, best_attack[0])

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
