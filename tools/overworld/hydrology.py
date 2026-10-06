"""Hydrology: priority-flood pit fill -> D8 flow accumulation -> river vectors.

The pipeline (each step justified inline):
  1. priority_flood  — remove interior pits so every land cell has a downhill
     drainage path to the ocean. Without this, D8 accumulation dead-ends in local
     minima and rivers fragment.
  2. d8_receivers    — for each cell pick the single steepest-descent neighbour
     (D8 = 8-connected). After flooding this graph is a set of trees rooted at
     the ocean, so accumulation is exact and cycle-free.
  3. flow_accum      — how many upstream cells drain THROUGH each cell (unit
     rainfall). Cells above a flow threshold are river.
  4. extract_rivers  — trace the river network into VECTOR polylines (headwater
     -> confluence/ocean), apply meander jitter + Chaikin smoothing, and derive a
     log-compressed width from flow. Rivers-as-vectors is the load-bearing idea:
     a downstream area generator consumes the polyline crossing its tile, not a
     raster, so the river lands at a consistent world position across tiles.

Design attribution: vector rivers + D8-flow hydrology are design references from
OpenMMO's doc/TERRAIN_GENERATION.md (ideas only, licence blocks their code).

Pure stdlib (heapq) + numpy. Loops that must be per-cell operate on python lists
(.tolist()) so 1M iterations stay in the ~0.5s range rather than paying numpy
scalar-indexing overhead a million times.
"""

from __future__ import annotations

import heapq
import numpy as np

# 8-neighbour offsets (row, col). Diagonal distance sqrt(2) matters for D8 slope.
_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
_NB_DIST = [np.sqrt(2), 1.0, np.sqrt(2), 1.0, 1.0, np.sqrt(2), 1.0, np.sqrt(2)]


def priority_flood(elev: np.ndarray, land: np.ndarray, epsilon: float = 1e-5) -> np.ndarray:
    """Barnes (2014) priority-flood + epsilon: fill pits so drainage is monotone.

    Seed a min-heap with every ocean/border cell at its true elevation. Repeatedly
    pop the lowest cell and, for each unvisited neighbour, raise it to at least
    (current_filled + epsilon) so there is always a strictly downhill path out.
    The tiny epsilon carves a gradient across otherwise-flat filled basins so D8
    still has a defined receiver there.
    """
    h, w = elev.shape
    filled = elev.copy()
    visited = np.zeros((h, w), dtype=bool)

    # Boundary condition: ocean cells AND the grid border are drainage outlets.
    # Vectorise the seed-mask so we only ever touch seed cells in python, not the
    # whole grid (the border+ocean can still be a large minority of cells).
    seed_mask = ~land
    seed_mask[0, :] = seed_mask[-1, :] = True
    seed_mask[:, 0] = seed_mask[:, -1] = True
    visited[seed_mask] = True
    seed_rc = np.argwhere(seed_mask)
    heap: list[tuple[float, int, int]] = [
        (float(filled[r, c]), int(r), int(c)) for r, c in seed_rc
    ]
    heapq.heapify(heap)

    while heap:
        e, r, c = heapq.heappop(heap)
        for dr, dc in _NB:
            nr, nc = r + dr, c + dc
            if 0 <= nr < h and 0 <= nc < w and not visited[nr, nc]:
                visited[nr, nc] = True
                ne = filled[nr, nc]
                if ne <= e + epsilon:
                    ne = e + epsilon
                filled[nr, nc] = ne
                heapq.heappush(heap, (ne, nr, nc))
    return filled


def d8_receivers(filled: np.ndarray, land: np.ndarray) -> np.ndarray:
    """Flat receiver index (into the raveled grid) of each cell's steepest neighbour.

    A cell whose receiver == itself is a sink (ocean or the global outlet). Slope is
    drop / distance so diagonals are not unfairly preferred. Vectorised across the 8
    neighbour directions using shifted views; only the argmax reduction is per-cell.
    """
    h, w = filled.shape
    n = h * w
    idx = np.arange(n).reshape(h, w)
    best_slope = np.zeros((h, w), dtype=np.float64)
    recv = idx.copy()

    for (dr, dc), dist in zip(_NB, _NB_DIST):
        # Shift the elevation field by this neighbour offset; out-of-bounds -> +inf
        # so border cells never pick an off-grid receiver.
        shifted = np.full((h, w), np.inf)
        nidx = np.full((h, w), -1, dtype=np.int64)
        r0s, r1s = max(0, -dr), h - max(0, dr)
        c0s, c1s = max(0, -dc), w - max(0, dc)
        r0d, r1d = max(0, dr), h - max(0, -dr)
        c0d, c1d = max(0, dc), w - max(0, -dc)
        shifted[r0s:r1s, c0s:c1s] = filled[r0d:r1d, c0d:c1d]
        nidx[r0s:r1s, c0s:c1s] = idx[r0d:r1d, c0d:c1d]

        slope = (filled - shifted) / dist  # >0 means the neighbour is lower
        take = (slope > best_slope) & (nidx >= 0)
        best_slope = np.where(take, slope, best_slope)
        recv = np.where(take, nidx, recv)

    # Ocean cells are outlets: force self-receiver so accumulation stops there.
    recv = recv.ravel()
    recv[~land.ravel()] = np.arange(n)[~land.ravel()]
    return recv


def flow_accum(recv: np.ndarray, filled: np.ndarray) -> np.ndarray:
    """Unit-rainfall drainage area: cells upstream that drain through each cell.

    Because receivers form trees after flooding, processing cells in DESCENDING
    filled-elevation order guarantees a cell is handled before its (lower)
    receiver, so a single pass accumulates exactly. Done on python lists to keep a
    ~1M-iteration loop cheap.
    """
    n = recv.size
    order = np.argsort(filled.ravel())[::-1].tolist()  # highest first
    recv_l = recv.tolist()
    acc = [1.0] * n
    for i in order:
        r = recv_l[i]
        if r != i:
            acc[r] += acc[i]
    return np.asarray(acc, dtype=np.float64)


def _chaikin(points: list[tuple[float, float]], iterations: int = 2) -> list[tuple[float, float]]:
    """Chaikin corner-cutting: smooths a polyline toward a quadratic B-spline.

    Each pass replaces every segment with its 1/4 and 3/4 points, so hard D8
    staircase turns become gentle bends. Endpoints are preserved so a river still
    starts at its headwater and ends at the coast."""
    for _ in range(iterations):
        if len(points) < 3:
            break
        out = [points[0]]
        for a, b in zip(points[:-1], points[1:]):
            out.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            out.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        out.append(points[-1])
        points = out
    return points


def extract_rivers(
    recv: np.ndarray,
    acc: np.ndarray,
    land: np.ndarray,
    cell_size_m: float,
    threshold: float,
    rng: np.random.Generator,
    meander_cells: float = 0.6,
):
    """Trace the river network into smoothed world-space vector polylines.

    river cells = (acc >= threshold) & land. We build the river-subgraph in-degree
    to find headwaters (no river cell drains in) and confluences (>=2 drain in),
    then walk each headwater downstream until it hits the ocean or an
    already-claimed confluence cell. Each walk is one polyline. Width is a
    log-compressed function of peak flow (real rivers widen sublinearly with
    drainage area). Returns (rivers, river_mask).
    """
    h, w = land.shape
    n = h * w
    river = (acc >= threshold) & land.ravel()

    # In-degree within the river subgraph (how many river cells flow into c),
    # computed vectorised: for each river cell whose receiver is also a river cell
    # (and not itself), bump the receiver's count via bincount.
    river_idx = np.where(river)[0]
    r_of = recv[river_idx]
    edge = (r_of != river_idx) & river[r_of]
    indeg = np.bincount(r_of[edge], minlength=n).astype(np.int32)

    recv_l = recv.tolist()
    river_l = river.tolist()
    headwaters = [int(i) for i in river_idx if indeg[i] == 0]
    # Sort headwaters by flow so the largest rivers get the lowest ids (stable).
    headwaters.sort(key=lambda i: -acc[i])

    rivers = []
    river_mask = np.zeros((h, w), dtype=bool)
    claimed_confluence = set()

    def _log_width(flow: float) -> float:
        # width in cells: 1 at threshold, growing ~ln(flow/threshold).
        return 1.0 + 1.4 * np.log1p(max(0.0, flow - threshold) / threshold)

    for hw_idx in headwaters:
        cells: list[int] = []
        cur = hw_idx
        while True:
            cells.append(cur)
            r = recv_l[cur]
            if r == cur or not river_l[r]:
                break  # reached ocean/outlet
            # Stop at a confluence already used as another river's terminus so we
            # don't duplicate the shared downstream trunk.
            if indeg[r] >= 2:
                cells.append(r)
                if r in claimed_confluence:
                    break
                claimed_confluence.add(r)
                cur = r
                continue
            cur = r
        if len(cells) < 3:
            continue

        # Cell index -> world coords (cell centre). x = col, y = row in metres.
        pts: list[tuple[float, float]] = []
        widths: list[float] = []
        for c in cells:
            rr, cc = divmod(c, w)
            river_mask[rr, cc] = True
            # Perpendicular-ish meander jitter, seeded & bounded (small).
            jx = (rng.random() - 0.5) * 2.0 * meander_cells
            jy = (rng.random() - 0.5) * 2.0 * meander_cells
            pts.append(((cc + 0.5 + jx) * cell_size_m, (rr + 0.5 + jy) * cell_size_m))
            widths.append(_log_width(float(acc[c])))
        pts = _chaikin(pts, 2)

        peak_flow = float(acc[cells[-1]])
        width_cells = float(_log_width(peak_flow))
        rivers.append(
            {
                "id": f"river_{len(rivers):02d}",
                "points": [[round(x, 2), round(y, 2)] for x, y in pts],
                "widths": [round(x, 3) for x in widths],
                "width_cells": round(width_cells, 2),
                "peak_flow": round(peak_flow, 1),
            }
        )
    return rivers, river_mask
