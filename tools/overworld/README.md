# Overworld schematic generator (W1 — epic #1640, The World Layer)

Deterministic, seeded, **CPU-only, zero LLM/CU spend** generator for a whole-region
overworld *schematic*: a heightfield, D8 hydrology, biomes, settlements, roads and
bridges — emitted as a region-graph JSON, two schematic PNGs, and (the load-bearing
piece) per-area **edge constraints** a future outdoor-area generator consumes to keep
discrete painted areas globally coherent.

Pure Python: **stdlib + numpy + PIL only** (already in the engine venv). No new deps.

## Modules

| File | Role |
|------|------|
| `noise.py` | Hand-rolled value-noise fBm (no Perlin/simplex dep). |
| `hydrology.py` | Priority-flood pit fill → D8 flow accumulation → **vector** river polylines (meander + Chaikin, log-flow width). |
| `biomes.py` | Documented elevation+slope+water-distance threshold table → biome codes. |
| `settlements.py` | Bridson Poisson-disk candidates scored by habitability → top-N with name-hint slots. |
| `roads.py` | Cost field (slope/swamp/river penalties) → A* on an MST+kNN topology → auto **bridges**. |
| `generate_overworld.py` | Orchestrator + output contract writer. |
| `render_worldmap.py` | Schematic → stylized hillshaded world map PNG (pure PIL). |
| `seed_overworld_locations.py` | **DRY-RUN** preview: schematic → engine `Location`/`WorldGraphNode` shape (prints JSON, touches no engine state). |
| `test_overworld.py` | Offline pytest: determinism, downhill rivers, road/bridge/constraint validity. |

## Run

```bash
# generate (default 1024², ~6s)
python generate_overworld.py --seed 42 --res 1024 --out /path/out
python render_worldmap.py --in /path/out                 # -> worldmap.png
python seed_overworld_locations.py --in /path/out         # DRY-RUN engine-shape preview

# test
uv run --directory <repo>/servers/engine --group dev \
  python -m pytest test_overworld.py -q -p no:xdist
```

## Output contract

`overworld.json` — `seed`, `params`, `grid`, `settlements[]`, `rivers[]` (vector
polylines + widths), `roads[]` (polylines), `bridges[]`, `region_grid`.

`area_constraints.json` — **the downstream contract.** One proposed AREA per
settlement and per river/road crossing:

```json
{
  "id": "area_stmt_00", "type": "settlement", "biome": "plain",
  "center": [x, y], "footprint_cells": [48, 48],
  "edge_constraints": [
    {"edge": "N", "feature": "river", "ref_id": "river_03",
     "at_fraction": 0.62, "width_cells": 3.1}
  ]
}
```

An outdoor-area generator paints each AREA independently, but honours its
`edge_constraints` so a river/road **enters neighbouring tiles at the same place** —
the mechanism that keeps a discrete-area world globally coherent.

## Design references

The two-resolution terrain split, vector rivers/roads, D8-flow hydrology, Poisson
settlements + habitability, and A* roads + auto bridges are **design references**
(ideas only, no code) from OpenMMO's `doc/TERRAIN_GENERATION.md` — their licence is
noncommercial and blocks code reuse, not concepts.
