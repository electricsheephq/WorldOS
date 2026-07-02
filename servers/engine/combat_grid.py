"""#461 grid (PR-1): pure, I/O-free coordinate-authority helpers.

Mirrors combat.py's pure-helper style — no Campaign, no lock, no save: just math
over (x, y) cells. The MCP tools in server.py wrap these with the lock +
persistence and surface their results. ADDITIVE: nothing here runs unless a fight
flips Combat.grid_enabled (set_grid); zone/theater combat never touches this module.

PR-1 SCOPE is the movement spine only: Chebyshev distance, a speed→cells budget, an
open-floor reachability flood, and a reach-leave opportunity-attack predicate. AoE
(#1257), cover / line-of-sight (#1261), difficult terrain + creature size/reach (#1253)
land in later PRs; ranged-range gating is still DEFERRED. The PR-1 helpers stay valid
for the default case — a single 1-cell, 5ft-reach token on open floor.
"""

from __future__ import annotations

Cell = tuple[int, int]

# ── #1253 / PR-5: creature SIZE → grid footprint ─────────────────────────────────
# SRD 5.2 space: Tiny/Small/Medium occupy a 5ft square (1 cell); Large a 10ft square
# (2×2 cells), Huge a 15ft square (3×3), Gargantuan a 20ft+ square (4×4). The engine
# models a token by its side length in CELLS (`footprint_cells`). The stored (x, y) is
# the token's anchor = its MIN-corner cell; the footprint spans [x, x+n) × [y, y+n).
# ADDITIVE: an unknown / absent / Medium size => 1 cell => PR-1 behaviour byte-for-byte.
_SIZE_CELLS = {
    "tiny": 1, "small": 1, "medium": 1,
    "large": 2, "huge": 3, "gargantuan": 4,
}


def footprint_cells(size: str) -> int:
    """The side length in CELLS of a creature of the given SRD size category (default 1
    for Medium/Small/Tiny, 2 for Large, 3 for Huge, 4 for Gargantuan). Case-insensitive;
    an unknown or empty size falls back to 1 cell (Medium) — the PR-1 default."""
    return _SIZE_CELLS.get((size or "").strip().lower(), 1)


def footprint(anchor: Cell, size: str) -> set[Cell]:
    """The set of cells a token of the given `size` occupies, anchored at `anchor` (its
    MIN-corner cell). Medium (1 cell) => just {anchor}; Large => a 2×2 block, etc. Pure;
    not clipped to the grid (the caller flags out-of-bounds like a 1-cell placement)."""
    n = footprint_cells(size)
    ax, ay = anchor
    return {(ax + i, ay + j) for i in range(n) for j in range(n)}


def footprints_overlap(a_anchor: Cell, a_size: str, b_anchor: Cell, b_size: str) -> bool:
    """True if two tokens' footprints share any cell (a placement/movement collision).
    For two Medium tokens this reduces to `a_anchor == b_anchor` (PR-1 occupancy)."""
    return bool(footprint(a_anchor, a_size) & footprint(b_anchor, b_size))


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
    cell or any of the 8 neighbours). This is the 1-cell (Medium) reach test; for larger
    tokens use `footprint_distance_cells` / `in_melee_reach_sized` which measure from the
    FOOTPRINT EDGE."""
    return chebyshev_cells(a, b) <= 1


def footprint_distance_cells(
    a_anchor: Cell, a_size: str, b_anchor: Cell, b_size: str
) -> int:
    """Chebyshev distance in CELLS between the two tokens' FOOTPRINT EDGES — the minimum
    Chebyshev distance over every pair of (a-cell, b-cell) in the two footprints. For two
    Medium (1-cell) tokens this is exactly `chebyshev_cells(a_anchor, b_anchor)` (PR-1),
    so it's additive. Overlapping footprints => 0. This is how a Large+ creature reaches
    an adjacent target from its NEAREST occupied cell, not its anchor."""
    an, bn = footprint_cells(a_size), footprint_cells(b_size)
    if an == 1 and bn == 1:
        return chebyshev_cells(a_anchor, b_anchor)
    (ax, ay), (bx, by) = a_anchor, b_anchor
    # Per-axis edge gap between the two [anchor, anchor+n-1] cell intervals; a negative
    # gap (overlap) clamps to 0. Chebyshev distance is the larger of the two axis gaps.
    dx = max(bx - (ax + an - 1), ax - (bx + bn - 1), 0)
    dy = max(by - (ay + an - 1), ay - (by + bn - 1), 0)
    return max(dx, dy)


def in_melee_reach_sized(
    a_anchor: Cell, a_size: str, b_anchor: Cell, b_size: str, reach_cells: int = 1
) -> bool:
    """True if a token at `a_anchor` (size `a_size`) can melee-reach a token at `b_anchor`
    (size `b_size`) — footprint-edge Chebyshev distance <= `reach_cells` (default 1 = 5ft).
    Additive: two Medium tokens with the default reach reduce to `in_melee_reach`."""
    return footprint_distance_cells(a_anchor, a_size, b_anchor, b_size) <= reach_cells


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
    difficult: set[Cell] = frozenset(),
) -> set[Cell]:
    """Reachability: every in-bounds cell reachable from `start` within `budget_cells`
    of movement over the 8-neighbourhood. Each step costs 1 cell, DOUBLED (2) when the
    step ENTERS a `difficult` cell (SRD 5.2 difficult terrain). A move may NOT END on
    an occupied cell, so `occupied` cells are dropped from the result (and the start
    itself is excluded — you stay put for free, it's not a "move to").

    `occupied` should NOT include `start` (the mover doesn't block itself). `difficult`
    empty (no terrain) => flat cost 1 (PR-1 behaviour, byte-for-byte). Returns a set of
    (x, y) cells. Pure Dijkstra over per-step cost; deterministic.
    """
    if budget_cells <= 0:
        return set()
    import heapq

    # Dijkstra: costs are 1 or 2, so a priority queue gives the minimal-cost distance to
    # each cell. Occupied/impassable cells can't be entered (a creature/wall blocks). The
    # cost to ENTER a difficult cell is 2; open cells cost 1.
    dist: dict[Cell, int] = {start: 0}
    pq: list[tuple[int, Cell]] = [(0, start)]
    while pq:
        d, (cx, cy) = heapq.heappop(pq)
        if d > dist.get((cx, cy), d) or d >= budget_cells:
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
                    continue  # terrain/wall/prop — can't enter or pass through
                step = 2 if cell in difficult else 1
                nd = d + step
                if nd > budget_cells:
                    continue
                if nd < dist.get(cell, nd + 1):
                    dist[cell] = nd
                    heapq.heappush(pq, (nd, cell))
    # Exclude the start cell (it's where you already are, not a destination).
    return {c for c in dist if c != start}


def shortest_path(
    start: Cell,
    goal: Cell,
    occupied: set[Cell],
    width: int,
    height: int,
    impassable: set[Cell] = frozenset(),
    difficult: set[Cell] = frozenset(),
) -> list[Cell] | None:
    """Cheapest route from `start` to `goal` over the 8-neighbourhood, routing AROUND
    cells that cannot be entered (`occupied` tokens + `impassable` terrain/walls/props)
    and preferring cheaper ground: each step costs 1, DOUBLED (2) to ENTER a `difficult`
    cell. Returns the list of step cells EXCLUDING `start` (so `path_cost_cells` re-derives
    the routed cost), `[]` if already at the goal, or None if the goal is itself blocked /
    out of bounds / unreachable without crossing a blocked cell. Deterministic (a fixed
    tie-break on equal-cost cells via the neighbour order). On OPEN floor with no difficult
    terrain this returns a straight diagonal whose cost equals the straight-line Chebyshev —
    behaviour is unchanged when there are no obstacles/terrain (additive)."""
    if start == goal:
        return []
    if not (0 <= goal[0] < width and 0 <= goal[1] < height):
        return None
    if goal in occupied or goal in impassable:
        return None
    import heapq

    # Dijkstra over per-step cost (1, or 2 into difficult terrain). `seq` is a monotonic
    # counter making the heap ordering total + deterministic when two cells tie on cost.
    prev: dict[Cell, Cell] = {start: start}
    dist: dict[Cell, int] = {start: 0}
    seq = 0
    pq: list[tuple[int, int, Cell]] = [(0, 0, start)]
    while pq:
        d, _, cur = heapq.heappop(pq)
        if cur == goal:
            break
        if d > dist.get(cur, d):
            continue
        cx, cy = cur
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < width and 0 <= ny < height):
                    continue
                cell = (nx, ny)
                if cell in occupied or cell in impassable:
                    continue
                nd = d + (2 if cell in difficult else 1)
                if nd < dist.get(cell, nd + 1):
                    dist[cell] = nd
                    prev[cell] = cur
                    seq += 1
                    heapq.heappush(pq, (nd, seq, cell))
    if goal not in prev:
        return None
    route: list[Cell] = []
    cur = goal
    while cur != start:
        route.append(cur)
        cur = prev[cur]
    route.reverse()
    return route


def path_cost_cells(
    from_cell: Cell,
    to_cell: Cell,
    path: list[Cell] | None = None,
    difficult: set[Cell] = frozenset(),
) -> int:
    """Measured movement cost in CELLS from `from_cell` to `to_cell`.

    If an explicit `path` (a list of waypoint cells the mover steps through, EXCLUDING
    the start) is given, the cost is the sum of the Chebyshev step between consecutive
    cells, with each STEP DOUBLED when it enters a `difficult` cell (SRD 5.2). With no
    path, open floor is assumed and the straight-line Chebyshev is charged — then, if any
    intervening cell of that straight diagonal is difficult, those entries are surcharged
    too (so a straight walk across difficult terrain still costs double per difficult cell).
    `difficult` empty => the plain Chebyshev cost (PR-1 behaviour, byte-for-byte). Pure; no
    occupancy check (the caller flags illegal ends)."""
    if not path:
        base = chebyshev_cells(from_cell, to_cell)
        if not difficult:
            return base
        # Surcharge each difficult cell the straight diagonal ENTERS (endpoints handled:
        # the start is never "entered"; the destination is if it's difficult).
        entered = _supercover_cells(from_cell, to_cell)[1:]  # drop the start
        return base + sum(1 for cell in entered if cell in difficult)
    total = 0
    prev = from_cell
    for step in path:
        hop = chebyshev_cells(prev, step)
        total += hop
        if difficult and step in difficult:
            total += 1  # entering a difficult cell costs double (one extra per entry)
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


# ── #1252 / PR-3: line-of-sight ray traversal + SRD cover ────────────────────────
#
# Pure geometry, no Campaign/lock/save — math over (x, y) cells + a set of BLOCKING
# cells (the fight's grid_impassable: walls/props/obstacles). Two capabilities:
#
#   * `line_blockers(a, b, blocking)` — the supercover ray from cell `a` to cell `b`:
#     every cell the straight segment between the two cell CENTRES passes through,
#     EXCLUDING the two endpoints, intersected with `blocking`. This is the raw
#     "what's between them" set that both LoS and cover read.
#   * `has_line_of_effect(a, b, blocking)` — True iff that ray crosses NO blocking cell
#     (an unobstructed straight shot). Used to cull AoE cells with no line of effect
#     from the burst origin (#1257's deferred TODO) and to detect TOTAL cover.
#   * `cover_between(a, b, blocking)` — the SRD 5.2 cover TIER a target at `b` has from
#     an attacker at `a`, derived from the count of intervening blockers (below).
#
# RAY MODEL — supercover, permissive corner-graze tie-break:
#   We march the exact segment between the two cell centres. When the segment passes
#   through a cell FACE we enter that cell (it counts). When it passes exactly through a
#   grid VERTEX (a "corner graze" — the ideal diagonal line of two aligned cells, or any
#   line hitting a lattice point), the segment touches the four cells meeting at that
#   corner only at a single point. SRD/tabletop LoS traces to a corner and treats a shot
#   that merely grazes a blocker's corner as UNOBSTRUCTED. So the tie-break is:
#
#     A corner graze severs the ray ONLY IF BOTH cells on the ray's leading diagonal at
#     that vertex are blockers (you cannot thread a line between two blockers that meet at
#     a point). A single diagonal blocker (or a blocker touched only at its corner) does
#     NOT block — the ray slips past its corner.
#
#   This is the standard permissive supercover rule and it makes LoS symmetric
#   (`a`->`b` blocked iff `b`->`a` blocked) and additive (no blockers => never severed).


def _supercover_cells(a: Cell, b: Cell) -> list[Cell]:
    """The ordered list of cells the straight segment between the CENTRES of `a` and `b`
    passes through, from `a` to `b` inclusive (a supercover line). A pure-diagonal or
    axis-aligned segment yields the minimal staircase; an oblique segment yields every
    cell whose interior the segment crosses. Deterministic; endpoints included (callers
    strip them). This is the geometry both LoS and cover read."""
    (x0, y0), (x1, y1) = a, b
    if a == b:
        return [a]
    dx = x1 - x0
    dy = y1 - y0
    nx, ny = abs(dx), abs(dy)
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    cells: list[Cell] = [(x0, y0)]
    x, y = x0, y0
    # March by comparing the accumulated cross-products (ix/nx vs iy/ny as ix*ny vs iy*nx),
    # stepping x, y, or BOTH (a diagonal step through a vertex). A simultaneous step is the
    # corner-graze case — it visits the vertex without adding either shoulder cell.
    ix = iy = 0
    while ix < nx or iy < ny:
        decision = (1 + 2 * ix) * ny - (1 + 2 * iy) * nx
        if decision == 0:
            # Exact corner graze: the segment passes through the lattice vertex. Step both
            # axes diagonally — do NOT add the two shoulder cells (they are only touched at
            # the corner point). The tie-break for blocking is applied by line_blockers.
            x += sx
            y += sy
            ix += 1
            iy += 1
        elif decision < 0:
            x += sx
            ix += 1
        else:
            y += sy
            iy += 1
        cells.append((x, y))
    return cells


def _diagonal_corner_pairs(a: Cell, b: Cell) -> list[tuple[Cell, Cell]]:
    """For each pure-diagonal step the supercover ray from `a` to `b` takes through a grid
    VERTEX, the pair of SHOULDER cells that meet at that corner (the two cells the ray did
    NOT enter, flanking the diagonal step). The corner-graze tie-break blocks the ray at a
    step only when BOTH shoulders in a pair are blockers. Empty for a ray with no diagonal
    vertex steps (axis-aligned or oblique face-crossing rays)."""
    (x0, y0), (x1, y1) = a, b
    if a == b:
        return []
    dx, dy = x1 - x0, y1 - y0
    nx, ny = abs(dx), abs(dy)
    sx = 1 if dx > 0 else -1
    sy = 1 if dy > 0 else -1
    pairs: list[tuple[Cell, Cell]] = []
    x, y = x0, y0
    ix = iy = 0
    while ix < nx or iy < ny:
        decision = (1 + 2 * ix) * ny - (1 + 2 * iy) * nx
        if decision == 0:
            # Diagonal step from (x,y) to (x+sx, y+sy): the two shoulders are the cells
            # reached by stepping ONE axis only — (x+sx, y) and (x, y+sy).
            pairs.append(((x + sx, y), (x, y + sy)))
            x += sx
            y += sy
            ix += 1
            iy += 1
        elif decision < 0:
            x += sx
            ix += 1
        else:
            y += sy
            iy += 1
    return pairs


def line_blockers(a: Cell, b: Cell, blocking: set[Cell]) -> set[Cell]:
    """The BLOCKING cells strictly BETWEEN `a` and `b` on the supercover ray (endpoints
    excluded). A cell the ray's interior crosses that is in `blocking` counts. A pure
    corner graze contributes a blocker ONLY when both shoulder cells of that diagonal step
    are blockers (the permissive tie-break above) — a lone diagonal blocker is slipped
    past and is NOT included. Empty `blocking` (open floor) => always empty (additive)."""
    if not blocking or a == b:
        return set()
    hit: set[Cell] = set()
    interior = _supercover_cells(a, b)[1:-1]  # strip both endpoints
    for cell in interior:
        if cell in blocking:
            hit.add(cell)
    # Corner-graze severing: a diagonal vertex step is blocked only if BOTH shoulders are
    # blockers. When it is, credit BOTH shoulder blockers (they jointly seal the corner);
    # a half-open corner (one shoulder blocking) is NOT credited (the ray slips past).
    for s1, s2 in _diagonal_corner_pairs(a, b):
        if s1 in blocking and s2 in blocking:
            if s1 != a and s1 != b:
                hit.add(s1)
            if s2 != a and s2 != b:
                hit.add(s2)
    return hit


def has_line_of_effect(a: Cell, b: Cell, blocking: set[Cell]) -> bool:
    """True iff the supercover ray from `a` to `b` crosses NO blocking cell — an
    unobstructed straight shot (LoS / line-of-effect). Endpoints are never their own
    blockers (a target standing in a doorway is still targetable). Open floor => always
    True (additive: with no blockers nothing is ever culled)."""
    return not line_blockers(a, b, blocking)


# SRD 5.2 cover TIERS. The derivation is deliberately simple and documented: count the
# DISTINCT blocking cells the attacker->target ray crosses (line_blockers), then:
#   0 blockers  -> "none"            (clear shot; no AC/DEX bonus)
#   1 blocker   -> "half"            (+2 AC / +2 DEX saves)
#   >=2 blockers -> "three_quarters" (+5 AC / +5 DEX saves)
#   ray fully severed (no line of effect at all, i.e. >=1 blocker) with the target
#     UNREACHABLE by any traced corner -> "total" (can't be targeted directly).
# A single ray can't distinguish "one thick wall" from "total" on its own, so total cover
# is derived separately: a target has TOTAL cover when it has NO line of effect from ANY
# of the traced corner rays (see cover_between). This mirrors the SRD "trace to a corner"
# rule — half/three-quarters come from the best (least-obstructed) corner; total means no
# corner has a clear line.
_COVER_AC = {"none": 0, "half": 2, "three_quarters": 5, "total": 0}


def cover_between(a: Cell, b: Cell, blocking: set[Cell]) -> str:
    """The SRD 5.2 cover tier a target at cell `b` has from an attacker/origin at `a`,
    given the fight's `blocking` cells. Derivation (documented, single-ray for PR-3's
    1-cell tokens):

      * count the distinct blockers on the centre-to-centre supercover ray (line_blockers);
      * 0 -> "none", exactly 1 -> "half", 2+ -> "three_quarters";
      * BUT if the ray has no line of effect AND no shoulder path threads past (total
        occlusion — the blockers fully seal the corner between them), the tier is "total".

    We approximate "total" as: the ray is severed by a corner-graze pair (both shoulders
    blocking) AND every interior cell on the ray is a blocker with no open shoulder — i.e.
    there is genuinely no gap. In practice, one blocker => half, two => three-quarters, and
    a fully-walled ray (a solid line of blockers with no threadable corner) => total. Open
    floor (`blocking` empty) or `a == b` => "none" (additive: no cover off the grid).

    SIZE NOTE (#1253/#1255): this is a SINGLE anchor-to-anchor ray. SRD "trace to a corner"
    for a Large+ token would trace from each of the attacker's / target's footprint corners
    and take the BEST (least-obstructed) line; the design doc does not require corner-tracing
    for big tokens, so PR-5 keeps the single-ray approximation. A large token whose anchor
    ray is walled may report more cover than a corner trace would — DEFERRED to #1255 (AI/
    tactics), which can consume the footprint corners if it needs the finer tier."""
    if not blocking or a == b:
        return "none"
    blockers = line_blockers(a, b, blocking)
    n = len(blockers)
    if n == 0:
        return "none"
    # TOTAL cover: no line of effect AND no threadable gap. A ray is TOTALLY severed when
    # every step is sealed — the interior is a contiguous blocker wall AND any diagonal
    # graze is a both-shoulders-blocked pair (no corner to slip through). For PR-3's
    # single-cell tokens the practical rule: if the interior contains a blocker on EVERY
    # cell the ray must cross (no open cell between attacker and target), the target is
    # behind solid cover and can't be targeted directly => total.
    interior = _supercover_cells(a, b)[1:-1]
    if interior and all(cell in blocking for cell in interior):
        return "total"
    if n == 1:
        return "half"
    return "three_quarters"


def cover_ac_bonus(tier: str) -> int:
    """The AC / DEX-save bonus a cover `tier` grants (SRD 5.2): half=+2, three-quarters=+5,
    none/total=0 (total cover isn't an AC bump — the target simply can't be targeted).
    Unknown tier => 0 (defensive; additive)."""
    return _COVER_AC.get(tier, 0)
