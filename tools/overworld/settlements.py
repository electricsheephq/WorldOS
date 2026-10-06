"""Settlement placement: Poisson-disk candidates scored by habitability.

Two stages, kept separate on purpose:
  1. Bridson Poisson-disk sampling gives blue-noise candidate points with a
     guaranteed minimum spacing — settlements never clump, which is what makes a
     schematic read as a believable region rather than a random scatter.
  2. Each candidate is scored for HABITABILITY (flat ground + near fresh water +
     a fertile biome). The top-N by score become the world's settlements. Names
     are left as SLOTS (`name_hint = biome + feature`) for a later naming pass —
     W1 never invents prose (zero LLM spend).

Design attribution: Poisson settlements + a habitability score are design
references from OpenMMO's doc/TERRAIN_GENERATION.md (ideas only, no code).
"""

from __future__ import annotations

import numpy as np

from biomes import BIOME_CODE


def poisson_disk(h: int, w: int, radius: float, rng: np.random.Generator, k: int = 30):
    """Bridson (2007) blue-noise sampling on the grid. Returns list of (row, col).

    Background grid cells are radius/sqrt(2) so each holds <=1 sample; for a new
    candidate we only test the 5x5 neighbourhood of cells, giving O(n) sampling.
    """
    cell = radius / np.sqrt(2.0)
    gw = int(np.ceil(w / cell)) + 1
    gh = int(np.ceil(h / cell)) + 1
    grid = -np.ones((gh, gw), dtype=np.int64)
    samples: list[tuple[float, float]] = []
    active: list[int] = []

    def _grid_xy(p):
        return int(p[1] / cell), int(p[0] / cell)  # (gx, gy) from (row,col)

    first = (rng.random() * h, rng.random() * w)
    samples.append(first)
    gx, gy = _grid_xy(first)
    grid[gy, gx] = 0
    active.append(0)

    while active:
        ai = rng.integers(0, len(active))
        idx = active[ai]
        base = samples[idx]
        placed = False
        for _ in range(k):
            ang = rng.random() * 2.0 * np.pi
            rad = radius * (1.0 + rng.random())  # annulus [r, 2r)
            cand = (base[0] + rad * np.sin(ang), base[1] + rad * np.cos(ang))
            if not (0 <= cand[0] < h and 0 <= cand[1] < w):
                continue
            cgx, cgy = _grid_xy(cand)
            ok = True
            for yy in range(max(0, cgy - 2), min(gh, cgy + 3)):
                for xx in range(max(0, cgx - 2), min(gw, cgx + 3)):
                    j = grid[yy, xx]
                    if j >= 0:
                        o = samples[j]
                        if (o[0] - cand[0]) ** 2 + (o[1] - cand[1]) ** 2 < radius * radius:
                            ok = False
                            break
                if not ok:
                    break
            if ok:
                samples.append(cand)
                grid[cgy, cgx] = len(samples) - 1
                active.append(len(samples) - 1)
                placed = True
                break
        if not placed:
            active.pop(ai)
    return [(int(round(r)), int(round(c))) for r, c in samples]


def habitability(rc, elev, slope, water_dist, code) -> float:
    """Score 0..1: high = flat, near fresh water, in a fertile (plain/forest) biome.

    Weighted sum of three normalised terms. A settlement on a mountainside far
    from water scores ~0; a plain beside a river scores near 1. The weights favour
    water access (villages hug rivers) then flatness then biome fertility.
    """
    r, c = rc
    b = int(code[r, c])
    if b == BIOME_CODE["ocean"]:
        return -1.0
    flat = 1.0 - min(1.0, slope[r, c] / 0.25)          # 1 when flat
    wet = 1.0 - min(1.0, water_dist[r, c] / 18.0)        # 1 when on water
    fertile = 1.0 if b in (BIOME_CODE["plain"], BIOME_CODE["forest"]) else (
        0.5 if b in (BIOME_CODE["coast"], BIOME_CODE["hills"]) else 0.15
    )
    return round(0.34 * flat + 0.40 * wet + 0.26 * fertile, 4)


def place_settlements(
    elev, slope, water_dist, code, river_mask, cell_size_m, rng,
    n_settlements: int = 8, min_sep_cells: float = 60.0,
):
    """Poisson candidates -> habitability -> top-N settlements with name-hint slots.

    Returns a list of settlement dicts (world coords, cell, biome, score, feature,
    name_hint). `feature` records what the settlement sits by (river / coast /
    open) so the name slot and area constraints can reference it.
    """
    h, w = elev.shape
    from biomes import BIOME_NAMES

    cands = poisson_disk(h, w, min_sep_cells, rng)
    scored = []
    for rc in cands:
        r, c = rc
        if not (0 <= r < h and 0 <= c < w):
            continue
        s = habitability(rc, elev, slope, water_dist, code)
        if s <= 0:
            continue
        scored.append((s, rc))
    scored.sort(key=lambda t: -t[0])

    settlements = []
    for rank, (s, rc) in enumerate(scored[:n_settlements]):
        r, c = rc
        biome = BIOME_NAMES[int(code[r, c])]
        near_river = bool(water_dist[r, c] <= 4.0)
        feature = "river" if near_river else ("coast" if biome == "coast" else "open")
        settlements.append(
            {
                "id": f"stmt_{rank:02d}",
                "cell": [r, c],
                "x": round((c + 0.5) * cell_size_m, 2),
                "y": round((r + 0.5) * cell_size_m, 2),
                "biome": biome,
                "habitability": s,
                "feature": feature,
                # Name is a SLOT, not prose: biome + feature, e.g. "plain-river".
                "name_hint": f"{biome}-{feature}",
            }
        )
    return settlements
