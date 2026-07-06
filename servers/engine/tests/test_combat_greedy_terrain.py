"""Greedy-v1 terrain-aware movement tests (#1269).

Found during #1255/#1268: greedy-v1's movement helpers (`_step_toward` and the retreat-if-low
`reachable` call) did NOT thread the fight's impassable/difficult cells, so a v1-policy monster on
a WALLED grid could step toward/into a blocked cell (the engine's move_to_coords then refuses, or
the loop picks an illegal-looking step). #1269 threads the SAME grid_impassable / grid_difficult
sets the engine uses into those helpers, so a v1 monster ROUTES AROUND walls and prefers a cheap
clear route over difficult ground.

Two invariants, both asserted here:
  * FIX — a v1 monster routes around a wall (was: stepped toward/into it); the retreat path respects
    walls; difficult terrain is avoided when a same-cost clear path exists.
  * BYTE-IDENTICAL — an OPEN-FLOOR fight (no walls, no difficult) is unchanged: the SAME seed/state
    picks the SAME move cell as before the fix (the empty-set case degenerates to today).

Pure + LLM-free: build a CombatView under the DEFAULT greedy-v1 policy, assert the Intent. No
campaign, no lock, no I/O.
"""
from __future__ import annotations

import combat_ai
import combat_grid
from combat_ai import AttackOption, CombatantView, CombatView, Intent


# ── view builders (mirror test_combat_tactical) ──────────────────────────────────────

def _view(actor_attacks, foes, *, actor_cell, grid=True, **kw) -> CombatView:
    return CombatView(
        actor_id="A",
        actor_cell=actor_cell,
        actor_zone="",
        actor_side="enemy",
        speed=kw.get("speed", 30),
        dashed=False,
        grid_enabled=grid,
        grid_width=kw.get("w", 12),
        grid_height=kw.get("h", 12),
        cell_size=5,
        foes=tuple(foes),
        allies=tuple(kw.get("allies", ())),
        attacks=tuple(actor_attacks),
        spells=tuple(kw.get("spells", ())),
        blocking=tuple(kw.get("blocking", ())),
        difficult=tuple(kw.get("difficult", ())),
    )


def _foe(fid, hp=20, ac=12, cell=None, name=None):
    return CombatantView(id=fid, name=name or fid, side="party",
                         current_hp=hp, max_hp=hp, armor_class=ac, cell=cell)


def _greedy(view) -> Intent:
    return combat_ai.pick_action(actor=object(), combat_state=view, policy="greedy-v1")


# ── FIX: greedy-v1 routes around a wall to reach its target ──────────────────────────

def test_greedy_move_routes_around_wall():
    """A v1 melee monster too far to strike, with a nearly-solid WALL column between it and the foe
    (a gap only at the top): the move destination must be a cell the actor can LEGALLY reach by
    routing AROUND the wall — never a cell reachable only by walking THROUGH the wall. Before #1269,
    _step_toward's `reachable` ignored blocking, so it happily picked the straight-line cell just past
    the wall (which has NO legal route this turn) — the illegal step the issue names."""
    atks = [AttackOption(name="Claw", to_hit=5, damage_expr="1d6", reach_ft=5)]
    foe = _foe("g1", cell=(8, 5))
    # A vertical wall at column x=5 spanning rows 1..11 with a single gap at (5,0): the ONLY route to
    # the foe's side is a long detour up-and-over, far beyond a 6-cell budget. Straight-line cells just
    # past the wall (e.g. (6,5)) are UNREACHABLE this turn — old code picked one anyway.
    wall = [(5, y) for y in range(1, 12)]
    v = _view(atks, [foe], actor_cell=(2, 5), blocking=wall, speed=30)
    intent = _greedy(v)
    assert intent.kind == "move"
    assert intent.to_cell is not None
    assert intent.to_cell not in set(wall)
    # The destination MUST be a terrain-aware reachable cell (no wall crossed / passed through).
    budget = combat_grid.movement_budget_cells(v.speed, v.cell_size, v.dashed)
    reach = combat_grid.reachable(
        v.actor_cell, budget, set(), v.grid_width, v.grid_height,
        impassable=set(wall),
    )
    assert intent.to_cell in reach
    # And it may not be on the far (foe) side of the wall — there is no legal route there this turn.
    assert intent.to_cell[0] < 5


def test_greedy_move_never_targets_unreachable_cell_behind_wall():
    """The cell closest to the foe sits behind a solid wall with no route this turn; greedy must NOT
    pick it. Regression guard for the exact bug the issue names (moving toward a blocked cell)."""
    atks = [AttackOption(name="Claw", to_hit=5, damage_expr="1d6", reach_ft=5)]
    foe = _foe("g1", cell=(8, 5))
    # A full-height wall at x=5 — no gap at all: the foe's side is wholly unreachable this turn.
    wall = [(5, y) for y in range(0, 12)]
    v = _view(atks, [foe], actor_cell=(2, 5), blocking=wall, speed=30)
    intent = _greedy(v)
    # With no cell getting strictly closer via a legal route, greedy must fall back (Dodge), NOT
    # emit a move onto an unreachable far-side cell. Either way it must never target a wall / far side.
    if intent.kind == "move":
        assert intent.to_cell not in set(wall)
        assert intent.to_cell[0] < 5
    else:
        assert intent.kind == "dodge"


# ── FIX: retreat-if-low respects walls ───────────────────────────────────────────────

def test_greedy_retreat_respects_walls(monkeypatch):
    """With morale ON (RETREAT_FRACTION>0) and a low-HP actor, the disengage flee cell must be a
    terrain-aware reachable cell (never a wall). Before #1269 the retreat `reachable` ignored
    blocking and could flee THROUGH a wall."""
    monkeypatch.setattr(combat_ai, "RETREAT_FRACTION", 0.5)

    class _Actor:
        max_hp = 20
        current_hp = 4  # <= floor(20 * 0.5) => retreat triggers

    atks = [AttackOption(name="Claw", to_hit=5, damage_expr="1d6", reach_ft=5)]
    threat = _foe("g1", cell=(2, 5))
    wall = [(5, y) for y in range(0, 9)]  # a full wall column to the actor's right
    v = _view(atks, [threat], actor_cell=(4, 5), blocking=wall, speed=30)
    intent = combat_ai.pick_action(actor=_Actor(), combat_state=v, policy="greedy-v1")
    assert intent.kind == "disengage"
    assert intent.to_cell is not None
    assert intent.to_cell not in set(wall)
    budget = combat_grid.movement_budget_cells(v.speed, v.cell_size, v.dashed)
    reach = combat_grid.reachable(
        v.actor_cell, budget, set(), v.grid_width, v.grid_height,
        impassable=set(wall),
    )
    assert intent.to_cell in reach


# ── FIX: difficult terrain avoided when a same-cost clear path exists ─────────────────

def test_greedy_move_prefers_clear_ground_over_difficult():
    """Two equally-CLOSE cells to the foe are reachable; one is reached only by crossing difficult
    terrain, the other by clear ground. The terrain-aware routed-cost tie-break must pick the cheap
    clear route (this is the whole point of threading `difficult` into _step_toward)."""
    atks = [AttackOption(name="Claw", to_hit=5, damage_expr="1d6", reach_ft=5)]
    foe = _foe("g1", cell=(6, 3))
    # Actor at (2,3). Difficult terrain smeared across the straight-line row toward the foe, so
    # the equal-Chebyshev-distance cell reached via the clear row above/below should win on cost.
    difficult = [(3, 3), (4, 3), (3, 2), (4, 2)]
    v = _view(atks, [foe], actor_cell=(2, 3), difficult=difficult, speed=30)
    intent = _greedy(v)
    assert intent.kind == "move"
    assert intent.to_cell is not None
    # The chosen destination must have the minimal ROUTED cost among cells at its distance-to-foe.
    budget = combat_grid.movement_budget_cells(v.speed, v.cell_size, v.dashed)
    diff = set(difficult)
    reach = combat_grid.reachable(
        v.actor_cell, budget, set(), v.grid_width, v.grid_height, difficult=diff
    )

    def routed_cost(cell):
        route = combat_grid.shortest_path(
            v.actor_cell, cell, set(), v.grid_width, v.grid_height, difficult=diff
        )
        return combat_grid.path_cost_cells(v.actor_cell, cell, route, difficult=diff)

    chosen = intent.to_cell
    chosen_dist = combat_grid.distance_ft(chosen, foe.cell, v.cell_size)
    # No equally-close cell should have a strictly cheaper route than the one greedy chose.
    same_dist = [
        c for c in reach
        if combat_grid.distance_ft(c, foe.cell, v.cell_size) == chosen_dist
    ]
    assert routed_cost(chosen) == min(routed_cost(c) for c in same_dist)


# ── BYTE-IDENTICAL: open floor is unchanged (empty-set degenerates to today) ─────────

def test_greedy_open_floor_move_byte_identical():
    """No walls, no difficult terrain: greedy-v1's move pick must be EXACTLY what the pre-#1269
    flat-cost `_step_toward` produced — the cell closest to the foe by (distance, cell). This is the
    regression the issue mandates: the empty-set case is byte-identical to today."""
    atks = [AttackOption(name="Claw", to_hit=5, damage_expr="1d6", reach_ft=5)]
    foe = _foe("g1", cell=(9, 5))
    v = _view(atks, [foe], actor_cell=(2, 5), speed=30)
    intent = _greedy(v)
    assert intent.kind == "move"
    # Recompute the OLD flat-cost pick (no impassable/difficult, key=(distance, cell)) directly.
    budget = combat_grid.movement_budget_cells(v.speed, v.cell_size, v.dashed)
    reach = combat_grid.reachable(v.actor_cell, budget, set(), v.grid_width, v.grid_height)
    expected = min(
        reach,
        key=lambda cell: (combat_grid.distance_ft(cell, foe.cell, v.cell_size), cell),
    )
    assert intent.to_cell == expected


def test_greedy_open_floor_matches_flat_step_toward_helper():
    """Belt-and-suspenders: on open floor the helper `_step_toward` returns the SAME cell as the
    pre-#1269 flat-cost algorithm for a spread of actor positions (empty-set == today)."""
    atks = [AttackOption(name="Claw", to_hit=5, damage_expr="1d6", reach_ft=5)]
    foe = _foe("g1", cell=(9, 6))
    for actor_cell in [(1, 1), (2, 6), (3, 9), (0, 6), (5, 0)]:
        v = _view(atks, [foe], actor_cell=actor_cell, speed=30)
        got = combat_ai._step_toward(v, foe, reach_ft=5)
        budget = combat_grid.movement_budget_cells(v.speed, v.cell_size, v.dashed)
        occupied = combat_ai._occupied_cells(v) - {v.actor_cell}
        reach = combat_grid.reachable(
            v.actor_cell, budget, occupied, v.grid_width, v.grid_height
        )
        best = min(
            reach,
            key=lambda cell: (combat_grid.distance_ft(cell, foe.cell, v.cell_size), cell),
        )
        cur = combat_grid.distance_ft(v.actor_cell, foe.cell, v.cell_size)
        new = combat_grid.distance_ft(best, foe.cell, v.cell_size)
        expected = best if new < cur else None
        assert got == expected, f"open-floor _step_toward diverged at {actor_cell}"
