"""Roads: A* over a cost field on an MST+k-nearest topology, with auto bridges.

Pipeline:
  1. build a per-cell COST field: base travel cost + slope penalty + swamp
     penalty + a river-crossing penalty (crossings are expensive but ALLOWED — a
     road may ford/bridge a river, it just prefers not to).
  2. choose which settlement PAIRS get a road: a Minimum Spanning Tree (every
     settlement reachable, no redundancy) PLUS the k nearest-neighbour extra
     edges (gives the loops/shortcuts a real road network has).
  3. A* each chosen pair over the cost field.
  4. BRIDGES: wherever a finished road path crosses a river cell, record an
     explicit bridge node at the crossing. These are the seam the downstream
     area generator keys on (a "river crossing" area).

Performance: A* runs on a DOWN-SAMPLED cost grid (`road_res`, default 256) not
the full heightfield. Roads are a coarse-scale feature; pathing at 1024^2 would
explode the search with no schematic benefit. Paths are mapped back to world
coordinates so the vectors still live in the same metric space as rivers.

Design attribution: A* roads + auto bridges + a slope/river cost field are
design references from OpenMMO's doc/TERRAIN_GENERATION.md (ideas only).
"""

from __future__ import annotations

import heapq
import numpy as np

from biomes import BIOME_CODE

_NB8 = [(-1, -1, np.sqrt(2)), (-1, 0, 1.0), (-1, 1, np.sqrt(2)), (0, -1, 1.0),
        (0, 1, 1.0), (1, -1, np.sqrt(2)), (1, 0, 1.0), (1, 1, np.sqrt(2))]


def build_cost_field(elev, slope, code, river_mask, sea_level):
    """Per-cell traversal cost. Ocean is impassable (inf); everything else is a
    weighted sum so A* naturally hugs flat, dry, non-swamp ground and crosses
    rivers only where it must."""
    cost = 1.0 + 6.0 * slope                     # slope is the dominant penalty
    cost = cost + np.where(code == BIOME_CODE["swamp"], 4.0, 0.0)
    cost = cost + np.where(river_mask, 8.0, 0.0)  # crossing penalty (allowed)
    cost = np.where(elev < sea_level, np.inf, cost)  # no roads on open water
    return cost


def _downsample_max(a: np.ndarray, factor: int) -> np.ndarray:
    """Block-reduce by MAX (conservative: a block is as costly as its worst cell,
    so coarse roads never tunnel through a peak/river that the fine grid blocks)."""
    h, w = a.shape
    nh, nw = h // factor, w // factor
    a = a[: nh * factor, : nw * factor].reshape(nh, factor, nw, factor)
    return a.max(axis=(1, 3))


def _astar(cost: np.ndarray, start, goal):
    """8-connected A* with an octile-distance heuristic. Returns (path_cells, total)
    or (None, inf) if unreachable. Cost of a step is the mean of the two endpoint
    cell costs times the step length (trapezoidal, so entering an expensive cell
    is penalised proportionally)."""
    h, w = cost.shape
    if not np.isfinite(cost[start]) or not np.isfinite(cost[goal]):
        return None, float("inf")

    def _hx(r, c):
        dr, dc = abs(r - goal[0]), abs(c - goal[1])
        return (dr + dc) + (np.sqrt(2) - 2) * min(dr, dc)  # octile

    g = np.full((h, w), np.inf)
    g[start] = 0.0
    came: dict[int, int] = {}
    openh = [(_hx(*start), start[0] * w + start[1])]
    while openh:
        _, flat = heapq.heappop(openh)
        r, c = divmod(flat, w)
        if (r, c) == goal:
            break
        base = g[r, c]
        for dr, dc, step in _NB8:
            nr, nc = r + dr, c + dc
            if 0 <= nr < h and 0 <= nc < w and np.isfinite(cost[nr, nc]):
                ng = base + step * 0.5 * (cost[r, c] + cost[nr, nc])
                if ng < g[nr, nc]:
                    g[nr, nc] = ng
                    came[nr * w + nc] = flat
                    heapq.heappush(openh, (ng + _hx(nr, nc), nr * w + nc))
    if not np.isfinite(g[goal]):
        return None, float("inf")

    path = []
    cur = goal[0] * w + goal[1]
    start_flat = start[0] * w + start[1]
    while cur != start_flat:
        path.append(divmod(cur, w))
        cur = came[cur]
    path.append(start)
    path.reverse()
    return path, float(g[goal])


def _mst_plus_knn(points, k: int = 2):
    """Edge set = MST (Prim, euclidean) UNION each node's k nearest neighbours.
    Returns a sorted list of undirected (i, j) index pairs."""
    n = len(points)
    if n < 2:
        return []
    pts = np.asarray(points, dtype=np.float64)
    d2 = ((pts[:, None, :] - pts[None, :, :]) ** 2).sum(-1)

    # Prim's MST.
    in_tree = [False] * n
    in_tree[0] = True
    edges = set()
    best = d2[0].copy()
    best_src = [0] * n
    for _ in range(n - 1):
        best[[i for i in range(n) if in_tree[i]]] = np.inf
        j = int(np.argmin(best))
        edges.add((min(j, best_src[j]), max(j, best_src[j])))
        in_tree[j] = True
        upd = d2[j] < best
        best = np.where(upd, d2[j], best)
        for t in range(n):
            if upd[t]:
                best_src[t] = j

    # k nearest neighbours per node (adds loops/shortcuts).
    for i in range(n):
        order = np.argsort(d2[i])
        added = 0
        for j in order:
            j = int(j)
            if j == i:
                continue
            edges.add((min(i, j), max(i, j)))
            added += 1
            if added >= k:
                break
    return sorted(edges)


def build_roads(settlements, cost_full, cell_size_m, sea_level, river_mask,
                rivers, road_res_factor: int = 4):
    """Route roads between MST+kNN settlement pairs; emit road vectors + bridges.

    Returns (roads, bridges). Roads are world-space polylines with a total cost
    and length; bridges are the river crossings recorded as explicit nodes that
    reference BOTH the road and the river they join.
    """
    h, w = cost_full.shape
    f = road_res_factor
    cost = _downsample_max(cost_full, f)
    rmask_ds = _downsample_max(river_mask.astype(np.float64), f) > 0
    ch, cw = cost.shape

    def _to_coarse(cell):
        return (min(ch - 1, cell[0] // f), min(cw - 1, cell[1] // f))

    coarse_pts = [_to_coarse(s["cell"]) for s in settlements]
    pairs = _mst_plus_knn([s["cell"] for s in settlements], k=2)

    roads = []
    bridges = []
    for (i, j) in pairs:
        path, total = _astar(cost, coarse_pts[i], coarse_pts[j])
        if path is None:
            continue
        # Map coarse path cells to world coords (coarse-cell centre in fine cells).
        pts_world = [
            [round((c * f + f / 2) * cell_size_m, 2), round((r * f + f / 2) * cell_size_m, 2)]
            for r, c in path
        ]
        length_m = 0.0
        for a, b in zip(pts_world[:-1], pts_world[1:]):
            length_m += float(np.hypot(a[0] - b[0], a[1] - b[1]))
        rid = f"road_{len(roads):02d}"
        roads.append(
            {
                "id": rid,
                "from": settlements[i]["id"],
                "to": settlements[j]["id"],
                "points": pts_world,
                "cost": round(total, 2),
                "length_m": round(length_m, 1),
            }
        )
        # Bridge detection: contiguous runs of river cells along the coarse path.
        in_run = False
        for k_idx, (r, c) in enumerate(path):
            on_river = rmask_ds[r, c]
            if on_river and not in_run:
                in_run = True
                bx, by = pts_world[k_idx]
                river_id = _nearest_river(bx, by, rivers)
                bridges.append(
                    {
                        "id": f"bridge_{len(bridges):02d}",
                        "at": [bx, by],
                        "road_id": rid,
                        "river_id": river_id,
                    }
                )
            elif not on_river:
                in_run = False
    return roads, bridges


def _nearest_river(x, y, rivers):
    """Return the id of the river vector whose polyline passes closest to (x, y).
    Used to bind a bridge to the specific river it spans."""
    best_id, best_d = None, float("inf")
    for rv in rivers:
        for px, py in rv["points"]:
            d = (px - x) ** 2 + (py - y) ** 2
            if d < best_d:
                best_d = d
                best_id = rv["id"]
    return best_id
