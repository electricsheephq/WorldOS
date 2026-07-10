#!/usr/bin/env python3
"""author_room_geometry.py — author a room_geometry.json for the TRUE-GREYBOX lane (epic #1508).

The two existing slice rooms (crypt on qa/seed_gfx_combat.py, camp on qa/seed_gfx_camp.py) have NO
committed room_geometry.json — the export_scene_grid.py-shaped input that greybox_render_headless.py +
greybox_sidecars_headless.py + tools/derive_room_manifest.py all consume. Their manifests are the
`measured` reconstructions qa/build_room_manifest.py made from calibrations. This tool emits the missing
geometry JSON directly from each seed's OWN prop constants, re-authored at CORRECT WORLD SCALE, so the
greybox — and every plate registered to it, and the manifest DERIVED from it — is correct-scale BY
CONSTRUCTION: each prop is a kind's proxy volume (height from greybox_render_headless._KIND_SPECS)
extruded on cells sized to the true 5-ft-grid object, not the drifted painted footprint.

WHY re-author scale (owner TRUE-GREYBOX diagnosis): the seeds' collision footprints were RE-MEASURED to
match the DRIFTED painted plates — the crypt sarcophagus was widened to a 12-cell blob (cols3-7 x rows6-8)
to sit under a tomb painted ~2x too large; the camp fire pit is a 2x2 (10ftx10ft) blob. Rendering the
greybox from those cells reproduces the drift. A 5-ft-grid-true stone coffin is ~10ftx10ft incl. base =
a 2x2 box ~1 cell tall; a crypt column is ~10ft = a 2-cell base; a campfire + stone ring is ~2x1 cells.
Authored here at those sizes, CENTERED on each seed prop's centroid so placement is preserved. The
regenerated plate registers to THIS greybox -> the paint is drawn at the true scale by construction (the
epic's premise: no cell retrofit can fix drifted paint).

Sizing is CALIBRATED to the coherence gate's localisation floor (qa/check_grid_paint_coherence.py):
sub-cell props (a 1-cell low fire, a razor-thin 2x1 low tomb) drift >0.5c even in the registered flux
base, so "correct scale" here means the smallest gate-localisable true size (2-cell pillars, a 2x2 tomb,
a 2x1 fire) — still ~3x smaller than the legacy drift.

Geometry schema mirrors qa/export_scene_grid.py / the forest_road geometry:
  {cols, rows, material, cell_default_walkable, walls, props:[{id, kind, cells}], impassable,
   door_cells, protected_lane_cells}
where `walls` is the export_scene_grid convention of EVERY non-walkable cell (true perimeter walls +
every prop footprint cell, conflated — greybox_render_headless dedupes prop cells out of the wall boxes).

  python3 tools/author_room_geometry.py crypt -o <out.json>
  python3 tools/author_room_geometry.py camp  -o <out.json>

Deterministic, offline, read-only w.r.t. engine state (imports only the seeds' module-level constants —
the seeds import scene_grid lazily inside functions, so importing the constants spins up no engine).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Optional

_TOOLS_DIR = Path(__file__).resolve().parent
_QA_DIR = _TOOLS_DIR.parent / "qa"


def _load_seed(module_name: str, filename: str):
    """Import a qa/seed_*.py module for its module-level constants only (no main())."""
    path = _QA_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _centered_box(cells: list, w: int, h: int) -> list:
    """A w-by-h (cols x rows) box centered on a footprint's centroid — the correct-scale proxy for a
    prop whose seed footprint drifted to the WRONG size (the crypt sarcophagus painted ~2x too large got
    a 12-cell footprint; its true 10ftx10ft coffin+base is a 2x2 box)."""
    cx = round(sum(c for (c, _) in cells) / len(cells) - (w - 1) / 2.0)
    cy = round(sum(r for (_, r) in cells) / len(cells) - (h - 1) / 2.0)
    return [[int(cx) + dc, int(cy) + dr] for dr in range(h) for dc in range(w)]


def _perimeter_walls(cols: int, rows: int) -> list:
    """The solid enclosing perimeter (crypt): row 0 / row rows-1 full, plus the side columns between."""
    cells = []
    for c in range(cols):
        cells.append((c, 0))
        cells.append((c, rows - 1))
    for r in range(1, rows - 1):
        cells.append((0, r))
        cells.append((cols - 1, r))
    return cells


def _geometry(cols: int, rows: int, material: str, props: list, *,
              perimeter: bool, door_cells: Optional[list] = None) -> dict:
    """Assemble the geometry dict. `props` is [(id, kind, [[c,r],...]), ...]. `walls` follows the
    export_scene_grid convention: perimeter wall cells (when enclosed) UNION every prop footprint cell."""
    prop_entries = [{"id": pid, "kind": kind, "cells": [[int(c), int(r)] for (c, r) in cells]}
                    for (pid, kind, cells) in props]
    wall_set: set = set()
    if perimeter:
        wall_set |= {(int(c), int(r)) for (c, r) in _perimeter_walls(cols, rows)}
    for p in prop_entries:
        wall_set |= {(c, r) for (c, r) in map(tuple, p["cells"])}
    walls = sorted((list(cr) for cr in wall_set), key=lambda cr: (cr[0], cr[1]))
    return {
        "location": None,  # set by caller
        "cols": cols, "rows": rows, "material": material,
        "cell_default_walkable": True,
        "walls": walls,
        "props": prop_entries,
        "impassable": walls,  # every non-walkable cell (same set) — matches forest_road geometry
        "door_cells": list(door_cells or []),
        "protected_lane_cells": [],
    }


def author_crypt() -> dict:
    """14x11 enclosed stone crypt: solid perimeter, two full-height stone pillars, one waist-high
    sarcophagus — re-authored at correct world scale (see module docstring)."""
    sc = _load_seed("_seed_crypt", "seed_gfx_combat.py")
    props = [
        # pillars: seed 2-cell bases kept. A ~10ft-wide crypt column is plausible masonry (NOT a scale
        # error like the tomb) AND a 2-cell hard silhouette localises reliably in the coherence gate.
        ("pillar_l", "stone_pillar", sc.PILLAR_L_CELLS),
        ("pillar_r", "stone_pillar", sc.PILLAR_R_CELLS),
        # sarcophagus: correct-scale 2x2 coffin+base (~1 cell tall) centered on the seed's 12-cell drift
        # centroid — ~3x smaller than the legacy footprint, and a big-enough hard silhouette for the gate.
        ("sarcophagus", "sarcophagus", _centered_box(sc.SARCOPHAGUS_CELLS, 2, 2)),
    ]
    geo = _geometry(sc.GRID_W, sc.GRID_H, "ancient stone", props, perimeter=True)
    geo["location"] = "Ancient Stone Crypt (firelit)"
    return geo


def author_camp() -> dict:
    """16x12 open-air night campfire clearing: NO perimeter (outdoor). The camp's error is the opposite
    of the crypt's — the SCENE is painted ~25% too small while the seed prop footprints are already
    plausible real-world sizes, so footprints are kept VERBATIM and the correct-scale greybox + registered
    regeneration fixes the paint fill. ONE exception: the fire pit's 2x2 (10ftx10ft) seed footprint is
    oversized; a campfire + stone ring is ~2x1 cells."""
    cp = _load_seed("_seed_camp", "seed_gfx_camp.py")
    props = [
        ("campfire", "campfire_pit", _centered_box(cp.CAMPFIRE_CELLS, 2, 1)),
        ("firewood", "fallen_log", cp.FIREWOOD_CELLS),
        ("crate_l", "supply_crates", cp.CRATE_L_CELLS),
        ("crate_c", "supply_crates", cp.CRATE_C_CELLS),
        ("crate_wall", "supply_crates", cp.CRATE_WALL_CELLS),
        ("crate_r", "supply_crates", cp.CRATE_R_CELLS),
        ("wall_bl", "stone_wall", cp.WALL_BL_CELLS),
        ("wall_br", "stone_wall", cp.WALL_BR_CELLS),
        ("post_l", "stone_pillar", cp.POST_CELLS),
        ("shelter", "timber_frame", cp.SHELTER_CELLS),
        ("bedroll_l", "bedroll", cp.BEDROLL_L_CELLS),
        ("bedroll_r", "bedroll_2", cp.BEDROLL_R_CELLS),
    ]
    geo = _geometry(cp.GRID_W, cp.GRID_H, "trampled dirt", props, perimeter=False)
    geo["location"] = "Forest Campfire Clearing (night)"
    return geo


_ROOMS = {"crypt": author_crypt, "camp": author_camp}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("room", choices=sorted(_ROOMS))
    ap.add_argument("-o", "--out", default=None, help="output geometry JSON (default: stdout)")
    args = ap.parse_args(argv)
    geo = _ROOMS[args.room]()
    text = json.dumps(geo) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"[author_room_geometry] {args.room}: {geo['cols']}x{geo['rows']}, "
              f"{len(geo['props'])} props, {len(geo['walls'])} non-walkable cells -> {args.out}",
              file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
