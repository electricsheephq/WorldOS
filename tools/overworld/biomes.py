"""Biome classification from a documented elevation + slope + water-distance table.

The classifier is deliberately a small, explicit, DOCUMENTED constant table (not
a black-box ML/noise blend) so a downstream area generator and a human designer
can both read exactly why a cell is `swamp` vs `plain`. Order matters: rules are
applied as a priority cascade (ocean first, mountain last) — the FIRST matching
band wins.

All thresholds are on NORMALISED fields: elevation 0..1 (0 = deepest ocean,
1 = highest peak), slope 0..1 (normalised gradient magnitude), water_dist in
CELLS to the nearest fresh water (river) capped at a max radius.

Design attribution: elevation+slope+water-distance habitability banding is a
design reference from OpenMMO's doc/TERRAIN_GENERATION.md (ideas only).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Stable integer codes -> name + RGB (used by the schematic PNG and downstream).
BIOME_NAMES = ["ocean", "coast", "swamp", "plain", "forest", "hills", "mountain"]
BIOME_CODE = {name: i for i, name in enumerate(BIOME_NAMES)}
BIOME_COLOR = {
    "ocean": (38, 78, 122),
    "coast": (198, 178, 120),
    "swamp": (74, 92, 68),
    "plain": (150, 176, 104),
    "forest": (58, 110, 66),
    "hills": (132, 130, 92),
    "mountain": (128, 122, 118),
}


@dataclass(frozen=True)
class BiomeTable:
    """Threshold constants for the cascade. Frozen so a run cannot mutate it."""

    sea_level: float = 0.35          # elevation below this is ocean
    coast_band: float = 0.06         # elevation within this ABOVE sea_level -> coast
    swamp_max_elev: float = 0.46     # swamp only in low ground...
    swamp_max_slope: float = 0.05    # ...that is very flat...
    swamp_water_dist: float = 3.0    # ...and close to fresh water (cells)
    plain_max_elev: float = 0.52
    plain_max_slope: float = 0.12
    forest_max_elev: float = 0.66
    forest_max_slope: float = 0.28
    hills_max_elev: float = 0.80     # above forest, below bare rock
    mountain_min_slope: float = 0.30 # steep anywhere reads as mountain


def classify(
    elev: np.ndarray,
    slope: np.ndarray,
    water_dist: np.ndarray,
    table: BiomeTable = BiomeTable(),
) -> np.ndarray:
    """Vectorised priority cascade -> int biome-code grid (see BIOME_NAMES)."""
    h, w = elev.shape
    code = np.full((h, w), BIOME_CODE["plain"], dtype=np.int16)

    land = elev >= table.sea_level

    # Cascade from most-specific to catch-all; later assignments overwrite only
    # where their mask is true, and we guard each with `land` + prior exclusivity.
    is_coast = land & (elev < table.sea_level + table.coast_band)
    is_swamp = (
        land
        & (elev < table.swamp_max_elev)
        & (slope < table.swamp_max_slope)
        & (water_dist <= table.swamp_water_dist)
    )
    is_mountain = land & (slope >= table.mountain_min_slope)
    is_hills = land & (elev >= table.forest_max_elev) & (elev < table.hills_max_elev)
    is_bare_peak = land & (elev >= table.hills_max_elev)
    is_forest = (
        land
        & (elev < table.forest_max_elev)
        & (slope < table.forest_max_slope)
        & (elev >= table.plain_max_elev)
    )
    is_plain = land & (elev < table.plain_max_elev)

    # Apply in ascending priority (last write wins for overlaps we intend).
    code[is_plain] = BIOME_CODE["plain"]
    code[is_forest] = BIOME_CODE["forest"]
    code[is_hills] = BIOME_CODE["hills"]
    code[is_bare_peak] = BIOME_CODE["mountain"]
    code[is_mountain] = BIOME_CODE["mountain"]
    code[is_swamp] = BIOME_CODE["swamp"]
    code[is_coast] = BIOME_CODE["coast"]
    code[~land] = BIOME_CODE["ocean"]
    return code


def colorize(code: np.ndarray) -> np.ndarray:
    """int biome-code grid -> HxWx3 uint8 RGB for the schematic PNG."""
    h, w = code.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    for name, i in BIOME_CODE.items():
        rgb[code == i] = BIOME_COLOR[name]
    return rgb


def approx_water_distance(mask: np.ndarray, max_r: int = 24) -> np.ndarray:
    """Cell distance to the nearest True in `mask`, capped at `max_r`.

    A capped multi-source BFS done as `max_r` vectorised binary dilations (numpy
    shifts) — no scipy. Each ring that newly reaches a cell stamps its radius. We
    only need small distances (coast/swamp bands) so the cap keeps it O(max_r * n),
    far cheaper than an exact transform and dependency-free.
    """
    h, w = mask.shape
    dist = np.full((h, w), float(max_r), dtype=np.float64)
    dist[mask] = 0.0
    frontier = mask.copy()
    for d in range(1, max_r + 1):
        grown = frontier.copy()
        grown[1:, :] |= frontier[:-1, :]
        grown[:-1, :] |= frontier[1:, :]
        grown[:, 1:] |= frontier[:, :-1]
        grown[:, :-1] |= frontier[:, 1:]
        newly = grown & (dist > d - 0.5) & ~frontier
        dist[newly] = d
        frontier = grown
        if frontier.all():
            break
    return dist
