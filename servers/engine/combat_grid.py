"""#461 grid (PR-1): pure, I/O-free coordinate-authority helpers.

Mirrors combat.py's pure-helper style — no Campaign, no lock, no save: just math
over (x, y) cells. The MCP tools in server.py wrap these with the lock +
persistence and surface their results. ADDITIVE: nothing here runs unless a fight
flips Combat.grid_enabled (set_grid); zone/theater combat never touches this module.

PR-1 SCOPE is the movement spine only: Chebyshev distance, a speed→cells budget, an
open-floor reachability flood, and a reach-leave opportunity-attack predicate. AoE,
cover, line-of-sight, terrain, creature size/reach, and ranged-range gating are all
DEFERRED to later PRs — every combatant here is a single 1-cell, 5ft-reach token.
"""

from __future__ import annotations

Cell = tuple[int, int]


def chebyshev_cells(a: Cell, b: Cell) -> int:
    """Chebyshev (king-move) distance in CELLS between two (x, y) cells: the larger
    of the column and row deltas. This is 5e's default diagonal rule (a diagonal step
    costs the same as an orthogonal one), so the count from a cell to any of its 8
    neighbours is 1."""
    (ax, ay), (bx, by) = a, b
    return max(abs(ax - bx), abs(ay - by))


def range_ft(cells: int, cell_size: int = 5) -> int:
    """Convert a CELL distance to feet. cells is the count-from-adjacent distance —
    the origin cell is NOT counted — so two cells one step apart are `cell_size` feet,
    matching the SRD ("a creature's space ... count[ing] from the edge")."""
    return cells * cell_size


def distance_ft(a: Cell, b: Cell, cell_size: int = 5) -> int:
    """Chebyshev distance between two cells, in feet (origin cell not counted)."""
    return range_ft(chebyshev_cells(a, b), cell_size)


def in_melee_reach(a: Cell, b: Cell) -> bool:
    """True if two cells are within 5ft melee reach: Chebyshev distance <= 1 (the same
    cell or any of the 8 neighbours). PR-1 has no creature-size/reach model — every
    token is 1 cell with 5ft reach."""
    return chebyshev_cells(a, b) <= 1


def movement_budget_cells(speed: int, cell_size: int = 5, dashed: bool = False) -> int:
    """Movement budget in CELLS for a turn: floor(speed / cell_size), doubled if the
    creature Dashed. Speed 30 / 5ft cells -> 6 cells (12 with Dash)."""
    if cell_size <= 0:
        return 0
    base = speed // cell_size
    return base * 2 if dashed else base


def reachable(
    start: Cell,
    budget_cells: int,
    occupied: set[Cell],
    width: int,
    height: int,
) -> set[Cell]:
    """Open-floor reachability: every in-bounds cell reachable from `start` within
    `budget_cells` of movement, flooding over the 8-neighbourhood at a flat cost of 1
    cell per step (Chebyshev movement). A move may PASS THROUGH nothing-blocks (PR-1
    has no terrain) but may NOT END on an occupied cell, so `occupied` cells are
    dropped from the result (and the start itself is excluded — you stay put for free,
    it's not a "move to"). PR-1: flat cost, no terrain, no size.

    `occupied` should NOT include `start` (the mover doesn't block itself). Returns a
    set of (x, y) cells. Pure Dijkstra/BFS over uniform cost; deterministic.
    """
    if budget_cells <= 0:
        return set()
    # BFS by ring: uniform cost 1 means a plain breadth-first expansion gives the
    # minimal step count to each cell. Occupied cells can't be entered at all (PR-1
    # treats a creature's cell as impassable — no moving through allies in this PR).
    dist: dict[Cell, int] = {start: 0}
    frontier = [start]
    while frontier:
        nxt: list[Cell] = []
        for (cx, cy) in frontier:
            d = dist[(cx, cy)]
            if d >= budget_cells:
                continue
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = cx + dx, cy + dy
                    if not (0 <= nx < width and 0 <= ny < height):
                        continue
                    cell = (nx, ny)
                    if cell in occupied:
                        continue  # can't enter (and so can't pass through) a token
                    if cell in dist:
                        continue
                    dist[cell] = d + 1
                    nxt.append(cell)
        frontier = nxt
    # Exclude the start cell (it's where you already are, not a destination).
    return {c for c in dist if c != start}


def path_cost_cells(from_cell: Cell, to_cell: Cell, path: list[Cell] | None = None) -> int:
    """Measured movement cost in CELLS from `from_cell` to `to_cell`.

    If an explicit `path` (a list of waypoint cells the mover steps through, EXCLUDING
    the start) is given, the cost is the sum of the Chebyshev step between consecutive
    cells (so a hand-routed path around an obstacle costs what it walked). With no path,
    PR-1 assumes open floor and charges the straight-line Chebyshev distance (the
    minimal open-floor cost). Pure; no occupancy check (the caller flags illegal ends).
    """
    if not path:
        return chebyshev_cells(from_cell, to_cell)
    total = 0
    prev = from_cell
    for step in path:
        total += chebyshev_cells(prev, step)
        prev = step
    return total


def provokes_on_leave(prev_cell: Cell, new_cell: Cell, threat_cell: Cell) -> bool:
    """Reach-leave opportunity-attack predicate: a mover provokes an OA from a threat
    if it WAS within the threat's 5ft melee reach at `prev_cell` AND is OUT of that
    reach at `new_cell` (it left the threatened area). Staying within reach (a 5ft
    side-step) does NOT provoke; never being in reach does not provoke. PR-1 reach is a
    flat 5ft (Chebyshev <= 1) for every threatener."""
    return in_melee_reach(prev_cell, threat_cell) and not in_melee_reach(new_cell, threat_cell)
