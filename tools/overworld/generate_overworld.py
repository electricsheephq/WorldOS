#!/usr/bin/env python3
"""W1 — deterministic overworld SCHEMATIC generator (epic #1640, The World Layer).

Pure CPU, zero LLM/CU spend, stdlib + numpy + PIL only. One seed in, a complete
region schematic out:

    overworld.json        seed, params, settlements[], rivers[] (vector polylines
                          + widths), roads[] (polylines), bridges[], region grid
    biome_map.png         coloured biome schematic (rivers rasterised in)
    height_map.png        grayscale heightfield
    area_constraints.json THE downstream contract: one proposed AREA per settlement
                          and per river/road crossing, each with edge_constraints
                          telling a future outdoor-area generator exactly where a
                          river/road enters each edge — so discrete painted areas
                          stay globally coherent.

Run: python generate_overworld.py --seed 42 --res 1024 --out <dir>

Design references (ideas only, licence blocks their code): OpenMMO
doc/TERRAIN_GENERATION.md — two-resolution terrain, vector rivers/roads, D8-flow
hydrology, Poisson settlements + habitability, A* roads + auto bridges.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling-module imports

import biomes
import hydrology
import noise
import roads as roads_mod
import settlements as stmt_mod


@dataclass
class OverworldParams:
    """All knobs of a run. Serialised into overworld.json for reproducibility."""

    res: int = 1024
    cell_size_m: float = 32.0          # ~32 m/cell => 1024 cells ~ 32 km world
    sea_level: float = 0.35
    island_radius: float = 1.15        # continental falloff radius (in half-diagonals)
    octaves: int = 6
    base_freq: float = 3.0
    river_flow_frac: float = 0.0016    # river threshold as a fraction of cell count
    n_settlements: int = 8
    settlement_sep_cells: float = 60.0
    road_res_factor: int = 4           # A* runs on res/factor grid
    settlement_footprint: int = 48     # area box side (cells) around a settlement
    crossing_footprint: int = 32       # area box side (cells) around a bridge


def _shape_heightfield(res: int, seed: int, p: OverworldParams) -> np.ndarray:
    """fBm * a radial continental falloff -> an island with natural coastlines.

    The falloff pulls the map edges underwater so the world is a bounded landmass
    (an ocean border), while the fBm keeps the interior coastline irregular rather
    than a circle. Renormalised to 0..1 so `sea_level` is a stable cut."""
    h = noise.fbm(res, res, seed, octaves=p.octaves, base_freq=p.base_freq)
    yy, xx = np.mgrid[0:res, 0:res].astype(np.float64)
    ny = yy / (res - 1) * 2.0 - 1.0
    nx = xx / (res - 1) * 2.0 - 1.0
    d = np.sqrt(nx * nx + ny * ny) / p.island_radius
    falloff = np.clip(1.0 - d * d, 0.0, 1.0)          # smooth radial mask
    elev = h * (0.35 + 0.65 * falloff)                 # keep detail, sink the edges
    lo, hi = float(elev.min()), float(elev.max())
    return (elev - lo) / (hi - lo + 1e-9)


def _slope_field(elev: np.ndarray) -> np.ndarray:
    """Normalised gradient magnitude (0..1). Normalised by its own 99th percentile
    so the biome thresholds are meaningful and roughly resolution-independent."""
    gy, gx = np.gradient(elev)
    raw = np.hypot(gx, gy)
    scale = float(np.percentile(raw, 99.0)) + 1e-9
    return np.clip(raw / scale, 0.0, 1.0)


def _seg_intersect(p1, p2, p3, p4):
    """Intersection point of segment p1p2 with p3p4, or None. Standard param solve."""
    x1, y1 = p1
    x2, y2 = p2
    x3, y3 = p3
    x4, y4 = p4
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
    u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / den
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    return None


def _edge_constraints(center, side_cells, cell_size_m, features):
    """Where each feature polyline enters the area box, as {edge, feature, ...}.

    `features` = list of (feature_kind, ref_id, polyline, width_cells). For each we
    walk its segments against the four box edges; the first crossing per edge is
    recorded with the fraction along that edge (0..1) so a downstream generator can
    reproduce the exact entry point on a freshly painted tile."""
    cx, cy = center
    half = side_cells * cell_size_m / 2.0
    left, right = cx - half, cx + half
    top, bottom = cy - half, cy + half
    edges = {
        "N": ((left, top), (right, top)),
        "S": ((left, bottom), (right, bottom)),
        "W": ((left, top), (left, bottom)),
        "E": ((right, top), (right, bottom)),
    }
    out = []
    seen = set()
    for kind, ref_id, poly, width_cells in features:
        for a, b in zip(poly[:-1], poly[1:]):
            for ename, (e1, e2) in edges.items():
                key = (ref_id, ename)
                if key in seen:
                    continue
                hit = _seg_intersect(a, b, e1, e2)
                if hit is None:
                    continue
                if ename in ("N", "S"):
                    frac = (hit[0] - left) / (2 * half)
                else:
                    frac = (hit[1] - top) / (2 * half)
                out.append(
                    {
                        "edge": ename,
                        "feature": kind,
                        "ref_id": ref_id,
                        "at_fraction": round(float(np.clip(frac, 0.0, 1.0)), 3),
                        "width_cells": round(float(width_cells), 2),
                    }
                )
                seen.add(key)
    return out


def _build_area_constraints(settlements, rivers, roads, bridges, p: OverworldParams):
    """One AREA per settlement + one per bridge crossing, each with edge constraints
    referencing the real river/road features that cross its footprint."""
    river_feats = [("river", rv["id"], rv["points"], rv["width_cells"]) for rv in rivers]
    road_feats = [("road", rd["id"], rd["points"], 2.0) for rd in roads]
    all_feats = river_feats + road_feats
    areas = []

    for s in settlements:
        ec = _edge_constraints((s["x"], s["y"]), p.settlement_footprint, p.cell_size_m, all_feats)
        areas.append(
            {
                "id": f"area_{s['id']}",
                "type": "settlement",
                "source_id": s["id"],
                "biome": s["biome"],
                "center": [s["x"], s["y"]],
                "footprint_cells": [p.settlement_footprint, p.settlement_footprint],
                "edge_constraints": ec,
            }
        )

    for b in bridges:
        # A crossing area centres on the bridge; both its river and its road must
        # enter, so it is the canonical "river crossing" downstream area.
        feats = [f for f in all_feats if f[1] in (b["river_id"], b["road_id"])]
        ec = _edge_constraints(tuple(b["at"]), p.crossing_footprint, p.cell_size_m, feats)
        areas.append(
            {
                "id": f"area_{b['id']}",
                "type": "crossing",
                "source_id": b["id"],
                "biome": "crossing",
                "center": b["at"],
                "footprint_cells": [p.crossing_footprint, p.crossing_footprint],
                "edge_constraints": ec,
            }
        )
    return {"areas": areas}


def _region_grid(code, p: OverworldParams, n_div: int = 8):
    """Coarse region metadata: split the map into n_div x n_div cells and label each
    by its dominant land biome. This is the region-graph scaffold the engine's
    Location.region strings map onto."""
    res = p.res
    step = res // n_div
    regions = []
    for gy in range(n_div):
        for gx in range(n_div):
            block = code[gy * step:(gy + 1) * step, gx * step:(gx + 1) * step]
            vals, counts = np.unique(block, return_counts=True)
            dom = int(vals[int(np.argmax(counts))])
            regions.append(
                {
                    "id": f"region_{gy}_{gx}",
                    "grid": [gy, gx],
                    "center": [round((gx + 0.5) * step * p.cell_size_m, 1),
                               round((gy + 0.5) * step * p.cell_size_m, 1)],
                    "dominant_biome": biomes.BIOME_NAMES[dom],
                }
            )
    return {"n_div": n_div, "cell_cells": step, "regions": regions}


def _rasterize_rivers(rgb, rivers, cell_size_m):
    """Burn river vectors into the biome RGB as a distinct water stroke so the
    schematic PNG shows the SAME rivers the vectors describe (raster ∥ vector)."""
    from PIL import ImageDraw

    img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(img)
    for rv in rivers:
        px = [(x / cell_size_m, y / cell_size_m) for x, y in rv["points"]]
        wid = max(1, int(round(rv["width_cells"])))
        draw.line(px, fill=(48, 96, 150), width=wid, joint="curve")
    return np.asarray(img)


def generate(seed: int, out_dir: str | Path, params: OverworldParams | None = None) -> dict:
    """Full pipeline. Returns the overworld dict AND writes all four artifacts."""
    p = params or OverworldParams()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    elev = _shape_heightfield(p.res, seed, p)
    land = elev >= p.sea_level
    slope = _slope_field(elev)

    # Hydrology on the (filled) heightfield.
    filled = hydrology.priority_flood(elev, land)
    recv = hydrology.d8_receivers(filled, land)
    acc = hydrology.flow_accum(recv, filled)
    threshold = p.river_flow_frac * (p.res * p.res)
    rng_r = np.random.default_rng(seed + 101)
    rivers, river_mask = hydrology.extract_rivers(
        recv, acc, land, p.cell_size_m, threshold, rng_r
    )

    # Biomes need distance to fresh water (rivers) and to the ocean for the coast.
    water_dist = biomes.approx_water_distance(river_mask | ~land)
    code = biomes.classify(elev, slope, water_dist, biomes.BiomeTable(sea_level=p.sea_level))

    # Settlements + roads.
    rng_s = np.random.default_rng(seed + 202)
    settlements = stmt_mod.place_settlements(
        elev, slope, water_dist, code, river_mask, p.cell_size_m, rng_s,
        n_settlements=p.n_settlements, min_sep_cells=p.settlement_sep_cells,
    )
    cost = roads_mod.build_cost_field(elev, slope, code, river_mask, p.sea_level)
    road_list, bridges = roads_mod.build_roads(
        settlements, cost, p.cell_size_m, p.sea_level, river_mask, rivers,
        road_res_factor=p.road_res_factor,
    )

    # Assemble contracts.
    for s in settlements:
        s["connections"] = sorted(
            {r["to"] if r["from"] == s["id"] else r["from"]
             for r in road_list if s["id"] in (r["from"], r["to"])}
        )
    region_grid = _region_grid(code, p)
    overworld = {
        "seed": seed,
        "params": asdict(p),
        "grid": {
            "res": p.res,
            "cell_size_m": p.cell_size_m,
            "world_size_m": round(p.res * p.cell_size_m, 1),
            "sea_level": p.sea_level,
        },
        "settlements": settlements,
        "rivers": rivers,
        "roads": road_list,
        "bridges": bridges,
        "region_grid": region_grid,
    }
    area_constraints = _build_area_constraints(settlements, rivers, road_list, bridges, p)

    # Write artifacts. JSON is sorted+rounded upstream so same seed => same bytes.
    (out / "overworld.json").write_text(json.dumps(overworld, indent=2, sort_keys=True))
    (out / "area_constraints.json").write_text(json.dumps(area_constraints, indent=2, sort_keys=True))

    height_u8 = (np.clip(elev, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(height_u8, mode="L").save(out / "height_map.png")
    rgb = biomes.colorize(code)
    rgb = _rasterize_rivers(rgb, rivers, p.cell_size_m)
    Image.fromarray(rgb, mode="RGB").save(out / "biome_map.png")

    return overworld


def _main(argv=None):
    ap = argparse.ArgumentParser(description="W1 overworld schematic generator")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--res", type=int, default=1024)
    ap.add_argument("--out", type=str, required=True)
    ap.add_argument("--settlements", type=int, default=8)
    args = ap.parse_args(argv)

    p = OverworldParams(res=args.res, n_settlements=args.settlements)
    # Scale settlement separation with resolution so density is res-independent.
    p.settlement_sep_cells = max(20.0, p.res * 0.06)
    t0 = time.time()
    ow = generate(args.seed, args.out, p)
    dt = time.time() - t0
    print(
        f"seed={args.seed} res={args.res} -> {len(ow['settlements'])} settlements, "
        f"{len(ow['rivers'])} rivers, {len(ow['roads'])} roads, "
        f"{len(ow['bridges'])} bridges in {dt:.1f}s -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
