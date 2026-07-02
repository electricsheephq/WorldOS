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
    impassable: set[Cell] = frozenset(),
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
                    if cell in impassable:
                        continue  # terrain/wall/prop — can't enter or pass through (P1)
                    if cell in dist:
                        continue
                    dist[cell] = d + 1
                    nxt.append(cell)
        frontier = nxt
    # Exclude the start cell (it's where you already are, not a destination).
    return {c for c in dist if c != start}


def shortest_path(
    start: Cell,
    goal: Cell,
    occupied: set[Cell],
    width: int,
    height: int,
    impassable: set[Cell] = frozenset(),
) -> list[Cell] | None:
    """P1: BFS shortest route from `start` to `goal` over the 8-neighbourhood, routing
    AROUND cells that cannot be entered (`occupied` tokens + `impassable` terrain/walls/
    props). Returns the list of step cells EXCLUDING `start` (so `path_cost_cells` sums its
    Chebyshev hops = the routed distance), `[]` if already at the goal, or None if the goal
    is itself blocked / out of bounds / unreachable without crossing a blocked cell. Uniform
    cost 1/step; deterministic (neighbour order fixed). On OPEN floor this returns a straight
    diagonal whose cost equals the old straight-line Chebyshev — so behaviour is unchanged
    when there are no obstacles (additive)."""
    if start == goal:
        return []
    if not (0 <= goal[0] < width and 0 <= goal[1] < height):
        return None
    if goal in occupied or goal in impassable:
        return None
    from collections import deque

    prev: dict[Cell, Cell] = {start: start}
    q: deque[Cell] = deque([start])
    while q:
        cur = q.popleft()
        if cur == goal:
            break
        cx, cy = cur
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < width and 0 <= ny < height):
                    continue
                cell = (nx, ny)
                if cell in prev or cell in occupied or cell in impassable:
                    continue
                prev[cell] = cur
                q.append(cell)
    if goal not in prev:
        return None
    route: list[Cell] = []
    cur = goal
    while cur != start:
        route.append(cur)
        cur = prev[cur]
    route.reverse()
    return route


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


# ── #1251 / PR-2: area-of-effect TEMPLATES (sphere / cone / line) ────────────────
#
# Pure geometry: map an SRD area shape (a sphere radius, a cone length, a line length/
# width — all in FEET) onto the set of grid cells it covers. Mirrors the movement-spine
# helpers above: no Campaign, no lock, no save — just math over (x, y) cells, clipped to
# the grid extents. server.py's cast_spell wraps these to compute `affected_tile_coords`
# and the occupants caught, then reuses the existing multi-target save-for-half loop.
#
# SRD 5.2 grid adaptation (the "Areas of Effect" template rules): a cell is IN an area if
# the point at the CENTER of that cell lies within the shape. Distance uses the grid's
# Chebyshev metric (the module's one distance model — a diagonal step is one cell), so a
# `radius`-ft sphere reaches `radius // cell_size` cells out in king-moves. Line-of-effect
# (whether a wall between origin and a cell blocks the area) is DEFERRED to #1252 — PR-2
# templates are permissive (every in-shape cell is affected). TODO(#1252): LoE cull.


def _clip(cells: set[Cell], width: int, height: int) -> set[Cell]:
    """Drop cells outside the [0,width) x [0,height) grid (edge clipping at bounds)."""
    return {(x, y) for (x, y) in cells if 0 <= x < width and 0 <= y < height}


def _facing(origin: Cell, toward: Cell) -> Cell:
    """The unit step (dx, dy) from `origin` toward `toward`, each component in {-1,0,1}
    (one of the 8 grid directions, or (0,0) if the same cell). Cones and lines are cast
    along one of these 8 facings — the grid adaptation snaps an aim point to a facing."""
    ox, oy = origin
    tx, ty = toward
    sx = (tx > ox) - (tx < ox)
    sy = (ty > oy) - (ty < oy)
    return (sx, sy)


def sphere_cells(
    center: Cell, radius_ft: int, width: int, height: int, cell_size: int = 5
) -> set[Cell]:
    """Cells within a `radius_ft` sphere/circle centred on `center` (the burst point of a
    Fireball et al.). A cell is in the area when its Chebyshev distance from `center` is
    <= radius_ft/cell_size cells — i.e. the burst reaches `radius_ft` feet out in every
    direction. Includes `center`. Clipped to the grid (a burst at the edge is truncated).
    radius_ft <= 0 yields just the centre cell (if in bounds)."""
    reach = max(0, radius_ft // cell_size) if cell_size > 0 else 0
    cx, cy = center
    cells = {
        (cx + dx, cy + dy)
        for dx in range(-reach, reach + 1)
        for dy in range(-reach, reach + 1)
    }
    return _clip(cells, width, height)


def cone_cells(
    origin: Cell, toward: Cell, length_ft: int, width: int, height: int, cell_size: int = 5
) -> set[Cell]:
    """Cells in a `length_ft` cone whose point is at `origin`, cast toward `toward`. Grid
    adaptation of the SRD cone (its width at any point equals its distance from the point):
    a cell at forward-depth `d` cells (1..length) is in the cone if its lateral offset from
    the centre line is <= `d` — the 1:1 widening quadrant fan. The cone is snapped to one of
    the 8 grid facings (from origin toward `toward`); a diagonal facing fans along both
    diagonals. `origin` itself is NOT included (the caster's cell — the point is the emitter,
    not a target). length_ft <= 0 or a zero facing yields the empty set. Clipped to bounds."""
    depth = max(0, length_ft // cell_size) if cell_size > 0 else 0
    fx, fy = _facing(origin, toward)
    if depth <= 0 or (fx == 0 and fy == 0):
        return set()
    ox, oy = origin
    cells: set[Cell] = set()
    if fx != 0 and fy != 0:
        # Diagonal facing: the cone occupies the quadrant between the two axes. At depth d
        # (measured in king-moves = max(|dx|,|dy|)) a cell is in-cone if BOTH axis offsets
        # advance with the facing and neither exceeds d (a square quadrant fan).
        for i in range(1, depth + 1):
            for j in range(0, depth + 1):
                cells.add((ox + fx * i, oy + fy * j))
                cells.add((ox + fx * j, oy + fy * i))
    else:
        # Orthogonal facing: primary axis is the facing; lateral offset <= depth-along-axis.
        for d in range(1, depth + 1):
            for off in range(-d, d + 1):
                if fx != 0:  # cast along x -> lateral is y
                    cells.add((ox + fx * d, oy + off))
                else:  # cast along y -> lateral is x
                    cells.add((ox + off, oy + fy * d))
    return _clip(cells, width, height)


def line_cells(
    origin: Cell, toward: Cell, length_ft: int, width_ft: int,
    grid_w: int, grid_h: int, cell_size: int = 5,
) -> set[Cell]:
    """Cells in a `length_ft` line `width_ft` wide, drawn from `origin` toward `toward`
    (a Lightning Bolt). The line runs `length_ft/cell_size` cells along the facing from
    `origin` toward `toward` (snapped to one of the 8 grid directions); `width_ft/cell_size`
    (>=1) sets how many cells wide, thickened symmetrically about the centre line. `origin`
    itself is NOT included (the emitter cell). length_ft <= 0 or a zero facing yields empty.
    Clipped to the grid."""
    length = max(0, length_ft // cell_size) if cell_size > 0 else 0
    half = max(0, (max(cell_size, width_ft) // cell_size - 1) // 2) if cell_size > 0 else 0
    fx, fy = _facing(origin, toward)
    if length <= 0 or (fx == 0 and fy == 0):
        return set()
    ox, oy = origin
    cells: set[Cell] = set()
    # Perpendicular unit (for thickness): rotate the facing 90 degrees. For a diagonal
    # facing this thickens along the opposite diagonal; for an orthogonal facing, along the
    # cross axis. half=0 (5ft wide) is a single-cell-wide line — the common case.
    px, py = -fy, fx
    for step in range(1, length + 1):
        bx, by = ox + fx * step, oy + fy * step
        for w in range(-half, half + 1):
            cells.add((bx + px * w, by + py * w))
    return _clip(cells, grid_w, grid_h)
