#!/usr/bin/env python3
"""W1 — DRY-RUN demo: overworld schematic -> the engine's location/travel SHAPE.

This proves the schematic->engine SEAM without touching engine state. It reads an
overworld.json and prints (to stdout) a JSON document shaped like the engine's own
models — Location (id / name / region / connections / travel_times) and
WorldGraphNode (location_id / x / y / biome / atlas_layer) — so a human (or a
later wiring pass) can see exactly how settlements and crossings would become
locations, travel edges and atlas rows.

It is DELIBERATELY inert: it imports NOTHING from the engine, calls NO tools,
mutates NO campaign. It only demonstrates the mapping. Wiring it into seed_world /
add_location is a separate, gated step (W3), out of scope for the W1 spike.

Run: python seed_overworld_locations.py --in <dir>   # prints JSON to stdout
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# Wilderness travel pace. D&D "normal" overland ~24 mi/day; on foot a schematic
# metre-per-minute pace of ~50 m/min keeps travel_times in a sane minute range.
WALK_M_PER_MIN = 50.0


def _region_of(cell, region_grid):
    """Which coarse region cell a (row, col) falls in -> a region SLOT string."""
    step = region_grid["cell_cells"]
    gy, gx = cell[0] // step, cell[1] // step
    gy = min(region_grid["n_div"] - 1, gy)
    gx = min(region_grid["n_div"] - 1, gx)
    for reg in region_grid["regions"]:
        if reg["grid"] == [gy, gx]:
            return reg["id"], reg["dominant_biome"]
    return f"region_{gy}_{gx}", "unknown"


def to_engine_shape(ow: dict) -> dict:
    """Map a schematic dict to engine-shaped locations + world-graph rows."""
    region_grid = ow["region_grid"]
    # road length (m) -> walk minutes, keyed by the unordered settlement pair.
    edge_minutes: dict[tuple[str, str], int] = {}
    for rd in ow["roads"]:
        minutes = max(1, round(rd["length_m"] / WALK_M_PER_MIN))
        edge_minutes[tuple(sorted((rd["from"], rd["to"])))] = minutes

    locations = []
    nodes = []
    for s in ow["settlements"]:
        reg_id, reg_biome = _region_of(s["cell"], region_grid)
        travel_times = {}
        connections = []
        for other in ow["settlements"]:
            if other["id"] == s["id"]:
                continue
            key = tuple(sorted((s["id"], other["id"])))
            if key in edge_minutes:
                connections.append(other["id"])
                travel_times[other["id"]] = edge_minutes[key]
        locations.append(
            {
                "id": s["id"],
                # Name is still a SLOT (schematic invents no prose): the engine /
                # a later naming pass fills it; we surface the hint verbatim.
                "name": f"<{s['name_hint']}>",
                "description": "",
                "region": f"{reg_biome} {reg_id}",
                "connections": sorted(connections),
                "travel_times": dict(sorted(travel_times.items())),
            }
        )
        nodes.append(
            {
                "location_id": s["id"],
                "x": s["x"],
                "y": s["y"],
                "biome": s["biome"],
                "atlas_layer": "settlement",
                "danger": 0,
                "tags": [s["feature"]],
            }
        )

    # Bridge crossings become atlas "route" waypoints (sites on the travel layer),
    # not first-class Locations — they enrich the map without authorizing movement.
    for b in ow["bridges"]:
        nodes.append(
            {
                "location_id": b["id"],
                "x": b["at"][0],
                "y": b["at"][1],
                "biome": "crossing",
                "atlas_layer": "route",
                "danger": 1,
                "tags": ["bridge", b["river_id"], b["road_id"]],
            }
        )

    return {
        "_note": "DRY RUN — engine-shaped preview only; nothing was written to any campaign.",
        "seed": ow["seed"],
        "locations": locations,
        "world_graph": {"nodes": nodes, "provenance": "overworld-schematic-w1"},
    }


def _main(argv=None):
    ap = argparse.ArgumentParser(description="DRY-RUN schematic->engine shape preview")
    ap.add_argument("--in", dest="in_dir", type=str, required=True)
    args = ap.parse_args(argv)
    ow = json.loads((Path(args.in_dir) / "overworld.json").read_text())
    print(json.dumps(to_engine_shape(ow), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
