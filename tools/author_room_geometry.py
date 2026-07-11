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


def _perimeter_wall_run_props(cols: int, rows: int, door_cells: Optional[list] = None) -> list:
    """The EXTENT-CONTRACT perimeter wall band (#1543): the enclosing perimeter authored as CONTINUOUS
    wall RUNS — one prop per contiguous run of a wall edge, split wherever a door cell interrupts it —
    NOT one cell per box (the #1539 crenellation rule: per-cell boxes give the depth map a toothed
    wall-top the depth-CN paints as a tiled/gamey motif). Each run is emitted with kind ``wall_run`` so
    greybox_render_headless draws it as a single thin box spanning the run at wall_height; because the
    run cells are also folded into ``walls``/``impassable`` by _geometry, the painted wall sits ON
    impassable cells by construction, and the DOOR gap stays walkable (no wall box over it — the old
    _perimeter_walls walled the door cell shut). Returns [(id, "wall_run", [(c,r),...]), ...]."""
    doors = {(int(c), int(r)) for (c, r) in (door_cells or [])}
    edges = [
        ("wall_n", [(c, 0) for c in range(cols)]),                 # back wall (row 0)
        ("wall_s", [(c, rows - 1) for c in range(cols)]),          # near/front wall (row rows-1)
        ("wall_w", [(0, r) for r in range(1, rows - 1)]),          # left wall (corners owned by n/s)
        ("wall_e", [(cols - 1, r) for r in range(1, rows - 1)]),   # right wall
    ]
    props = []
    for edge_id, cells in edges:
        run: list = []
        seg = 0
        for cell in cells:
            if cell in doors:
                if run:
                    props.append((f"{edge_id}_{seg}", "wall_run", run))
                    seg += 1
                    run = []
                continue
            run.append(cell)
        if run:
            props.append((f"{edge_id}_{seg}", "wall_run", run))
    return props


def _geometry(cols: int, rows: int, material: str, props: list, *,
              perimeter: bool, door_cells: Optional[list] = None,
              camera_fit: bool = False) -> dict:
    """Assemble the geometry dict. `props` is [(id, kind, [[c,r],...]), ...]. `walls` follows the
    export_scene_grid convention: perimeter wall cells (when enclosed) UNION every prop footprint cell.
    `camera_fit` stamps the opt-in extent-contract flag greybox_render_headless reads (#1543)."""
    prop_entries = [{"id": pid, "kind": kind, "cells": [[int(c), int(r)] for (c, r) in cells]}
                    for (pid, kind, cells) in props]
    wall_set: set = set()
    if perimeter:
        wall_set |= {(int(c), int(r)) for (c, r) in _perimeter_walls(cols, rows)}
    for p in prop_entries:
        wall_set |= {(c, r) for (c, r) in map(tuple, p["cells"])}
    walls = sorted((list(cr) for cr in wall_set), key=lambda cr: (cr[0], cr[1]))
    geo = {
        "location": None,  # set by caller
        "cols": cols, "rows": rows, "material": material,
        "cell_default_walkable": True,
        "walls": walls,
        "props": prop_entries,
        "impassable": walls,  # every non-walkable cell (same set) — matches forest_road geometry
        "door_cells": list(door_cells or []),
        "protected_lane_cells": [],
    }
    if camera_fit:
        geo["camera_fit"] = True
    return geo


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


def author_crypt_rich() -> dict:
    """14x11 enclosed stone crypt — the RICHNESS-PRINCIPLE denser sibling of author_crypt (CRYPT-RICH,
    epic #1508 / PR #1528 lesson). The sparse 3-prop true-greybox capped an honest style-pass at ~7.1 vs
    the 8.0 incumbent (`crypt_armb_iter3`) because "paint richness follows GEOMETRY richness": the honest
    composition-faithful plate had only two plain pillar stubs + one coffin to carve, while the incumbent's
    8.0 buys its edge from ORNAMENTATION the greybox lacked (carved full-height knotwork columns, a reclining
    effigy tomb, wall reliefs/niches, a skull-and-bone pile, lit wall torch brackets, a spilled funerary urn,
    rubble/broken slabs). This room AUTHORS those ornament classes as real prop VOLUMES so the depth-ControlNet
    base and the Gemini style pass have surfaces to carve — more volumes = richer paint AND richer collision,
    both derived from the same geometry.

    Canonical layout contract KEPT EXACTLY (drop-in for the deployed incumbent's engine seed
    `qa/seed_gfx_combat.py`): pillars PILLAR_L_CELLS (3,3)/(3,4) + PILLAR_R_CELLS (8,9)/(9,9), the full
    12-cell coffin SARCOPHAGUS_CELLS (cols3-7 x rows6-8), solid perimeter, and door zones (6,0) (top) +
    (13,4) (the tavern-seam door, PR #1535) marked + KEPT CLEAR of props. Every ADDED prop hugs a wall band
    or corner (never the central circulation floor or a door approach), so the walkable topology — the ring
    around the tomb, and reachability of both doors — is preserved (flood-fill-verified in the CRYPT-RICH
    evidence). Runs are kept SHORT (1-2 cells) so each prop's derived occlusion hull stays tight
    (derive_room_manifest.py, CAMP-TUNE defect #5).

    Camera placement (30/45 dimetric; near walls = col0 + max-row, far walls = max-col + row0, per
    author_tavern): TALL ornaments (braziers 2.2, altars 2.0, the engaged column 7.5) sit on the BACK/FAR
    bands (row1, col12) where they won't occlude the interior; LOW clutter (rubble/skull/slabs/urn ~1.4)
    tucks into the near/left corners. Prop KINDS pick the right proxy silhouette+height from
    greybox_render_headless._KIND_SPECS; the ornament IDENTITY (knotwork/effigy/skulls/torch-flame/cobwebs)
    is carried by the style-pass prompt, not the greybox."""
    sc = _load_seed("_seed_crypt", "seed_gfx_combat.py")
    props = [
        # --- canonical layout, kept EXACTLY (matches the deployed incumbent seed) ---
        ("pillar_l", "stone_pillar", sc.PILLAR_L_CELLS),          # -> carved knotwork full-height column
        ("pillar_r", "stone_pillar", sc.PILLAR_R_CELLS),          # -> carved knotwork full-height column
        ("sarcophagus", "sarcophagus", sc.SARCOPHAGUS_CELLS),     # -> raised tomb w/ reclining effigy lid
        # --- ADDED ornamentation volumes (richness principle). TALL, on the BACK/FAR wall bands ---
        ("relief_back_l", "altar", [[2, 1]]),                     # carved wall relief panel, back-left
        ("torch_door_l", "brazier", [[5, 1]]),                    # lit torch bracket, left of the top door
        ("torch_door_r", "brazier", [[7, 1]]),                    # lit torch bracket, right of the top door
        ("niche_back_r", "altar", [[10, 1], [11, 1]]),            # recessed wall niche / 2nd tomb slab
        ("pilaster_arch", "stone_pillar", [[12, 3]]),             # engaged full-height column beside the door
        ("torch_l", "brazier", [[1, 4]]),                         # lit torch bracket, left (near) wall
        ("torch_r", "brazier", [[12, 6]]),                        # lit torch bracket, right (far) wall
        # --- LOW floor clutter, tucked into near/left corners (won't occlude, won't block circulation) ---
        ("rubble_bl", "rubble", [[1, 1], [1, 2]]),                # rubble pile, back-left corner
        ("broken_slabs", "rubble", [[1, 6], [1, 7]]),             # heaved/broken floor slabs, left wall
        ("skull_pile", "rubble", [[2, 9], [3, 9]]),               # skull-and-bone cluster, front-left
        ("urn_spill", "barrel", [[11, 9]]),                       # tipped funerary urn spilling coins
    ]
    geo = _geometry(sc.GRID_W, sc.GRID_H, "ancient stone", props, perimeter=True,
                    door_cells=[[6, 0], [13, 4]])
    geo["location"] = "Ancient Stone Crypt (firelit)"
    return geo


def author_camp() -> dict:
    """16x12 open-air night campfire clearing: NO perimeter (outdoor). The camp's error is the opposite
    of the crypt's — the SCENE is painted ~25% too small while the seed prop footprints are already
    plausible real-world sizes, so footprints are kept VERBATIM and the correct-scale greybox + registered
    regeneration fixes the paint fill. ONE exception: the fire pit's 2x2 (10ftx10ft) seed footprint is
    oversized; a campfire + stone ring is ~2x1 cells. Owner playtest #7 CAMP-TUNE (2026-07-11) further
    re-measured several footprints directly against the ADOPTED true-greybox plate (woodpile, crate
    cluster, shelter posts/back-wall, the ruin's tower/link walls) — see seed_gfx_camp.py's per-constant
    comments for the per-defect rationale."""
    cp = _load_seed("_seed_camp", "seed_gfx_camp.py")
    props = [
        ("campfire", "campfire_pit", _centered_box(cp.CAMPFIRE_CELLS, 2, 1)),
        ("firewood", "fallen_log", cp.FIREWOOD_CELLS),
        ("crate_l", "supply_crates", cp.CRATE_L_CELLS),
        ("crate_c", "supply_crates", cp.CRATE_C_CELLS),
        ("crate_wall", "supply_crates", cp.CRATE_WALL_CELLS),
        ("crate_r", "supply_crates", cp.CRATE_R_CELLS),
        ("wall_bl", "stone_wall", cp.WALL_BL_CELLS),
        # wall_br SPLIT into 3 short runs + the ruin's own tower1/tower2/link (owner playtest #7
        # CAMP-TUNE): a long multi-cell footprint gives derive_room_manifest.py's per-prop
        # bounding-box occlusion hull a huge span; several short props keep each hull tight.
        ("wall_br", "stone_wall", cp.WALL_BR_CELLS),
        ("wall_br2", "stone_wall", cp.WALL_BR2_CELLS),
        ("wall_br3", "stone_wall", cp.WALL_BR3_CELLS),
        ("ruin_tower1", "stone_wall", cp.RUIN_TOWER1_CELLS),
        ("ruin_tower2", "stone_wall", cp.RUIN_TOWER2_CELLS),
        ("ruin_link", "stone_wall", cp.RUIN_LINK_CELLS),
        ("shelter", "timber_frame", cp.SHELTER_CELLS),
        ("bedroll_l", "bedroll", cp.BEDROLL_L_CELLS),
        ("bedroll_r", "bedroll_2", cp.BEDROLL_R_CELLS),
    ]
    geo = _geometry(cp.GRID_W, cp.GRID_H, "trampled dirt", props, perimeter=False)
    geo["location"] = "Forest Campfire Clearing (night)"
    return geo


def author_tavern() -> dict:
    """12x10 enclosed firelit tavern interior — the FIRST room authored from NOTHING (no prior seed, no
    prior plate): a brand-new room proving the true-greybox method generalises beyond regenerations
    (NEW-ROOM-TAVERN, epic #1508). Cells are authored DIRECTLY at world-true 5-ft-grid scale (1 cell =
    5 ft ~ 1 human), not re-measured from a drifted plate — so the greybox, the plate registered to it,
    and the derived manifest are correct-scale by construction.

    Layout (indoor firelit class — the strongest): solid perimeter (enclosed hall); a stone HEARTH mass
    against the back wall (2x1, the future fire anchor — a tall chimney-breast); a waist-high BAR COUNTER
    on the far-right side (4x1); three round communal TABLES on the central floor (2x2 each — the
    coherence gate's localisation floor, since a 2x1 low prop drifts >0.5c even on the registered base,
    epic #1508 finding; stools are sub-cell and live in the style prompt's clutter vocab, NOT as separate
    footprints); an ALE-BARREL cluster (2x2). A back-wall DOOR cell (8,0) — the seam the walkslice wires
    to the crypt for the three-room world. Generous walkable floor across the near half.

    Placement respects the contract camera (30/45 dimetric from the -x,-z near corner = grid col0 /
    max-row): LOW props (tables/barrels) stay in the far/interior zone (cols 2-9, rows 1-7) OFF the two
    NEAR walls (col0 and the front wall) which would occlude/mislocalise them; only the TALL hearth and
    the far bar counter sit near back/far walls. Rendered as a CUTAWAY greybox (wall_height 5) so the
    tall near walls do not poison the interior NCC correlation — coherence-green 6/6 vs its own derived
    manifest.

    Prop kinds map onto greybox_render_headless._KIND_SPECS: 'bar'/'table' -> 1-cell-tall proxy volumes,
    'barrel' -> ~0.7-cell, 'hearth' -> the tall default stone mass (a chimney breast)."""
    props = [
        ("hearth", "hearth", [[5, 1], [6, 1]]),
        ("bar_counter", "bar", [[9, 2], [9, 3], [9, 4], [9, 5]]),
        ("table_nw", "table", [[3, 3], [4, 3], [3, 4], [4, 4]]),
        ("table_ne", "table", [[6, 3], [7, 3], [6, 4], [7, 4]]),
        ("table_s", "table", [[5, 6], [6, 6], [5, 7], [6, 7]]),
        ("barrels", "barrel", [[2, 6], [3, 6], [2, 7], [3, 7]]),
    ]
    geo = _geometry(12, 10, "worn wooden planks", props, perimeter=True, door_cells=[[8, 0]])
    geo["location"] = "Firelit Tavern Hall"
    return geo


def author_tavern_fit() -> dict:
    """The tavern re-authored under the EXTENT CONTRACT (#1543 / M-ALIGN) — the reference room proving
    paint == playable grid. Two additions over author_tavern(), both OPT-IN so nothing about the
    deployed tavern (its plate, manifest, and the engine-grid tests that pin author_tavern's props)
    changes:

    1. ``camera_fit: true`` — greybox_render_headless fits the ortho scale so the 12x10 grid diamond +
       wall band fills the 1344x768 frame edge-to-edge, instead of the fixed ortho=13 that left canvas
       margins the style pass out-painted into a room LARGER than the grid (owner playtest #8: unreachable
       painted floor, invisible walls at the grid edges).
    2. an explicit PERIMETER WALL BAND authored as continuous wall RUNS (``_perimeter_wall_run_props``,
       kind ``wall_run``) — one box per edge run (the #1539 no-crenellation rule), the door gap at (8,0)
       left OPEN (the old per-cell perimeter walled the door cell shut), so painted walls sit ON
       impassable cells by construction.

    The interior prop layout is IDENTICAL to author_tavern() (kept in sync deliberately — same 6 props,
    same cells); only the wall representation and the camera flag differ. Kept as a SEPARATE room key so
    ``tavern`` stays byte-identical for every existing consumer."""
    props = [
        # --- interior props: identical layout to author_tavern() ---
        ("hearth", "hearth", [[5, 1], [6, 1]]),
        ("bar_counter", "bar", [[9, 2], [9, 3], [9, 4], [9, 5]]),
        ("table_nw", "table", [[3, 3], [4, 3], [3, 4], [4, 4]]),
        ("table_ne", "table", [[6, 3], [7, 3], [6, 4], [7, 4]]),
        ("table_s", "table", [[5, 6], [6, 6], [5, 7], [6, 7]]),
        ("barrels", "barrel", [[2, 6], [3, 6], [2, 7], [3, 7]]),
    ]
    # --- explicit continuous perimeter wall band, door (8,0) left open ---
    props += _perimeter_wall_run_props(12, 10, door_cells=[[8, 0]])
    # perimeter=False: the wall band is now carried by the wall_run props (which _geometry folds into
    # walls/impassable), so we must NOT also add the per-cell _perimeter_walls (which would wall the door).
    geo = _geometry(12, 10, "worn wooden planks", props, perimeter=False,
                    door_cells=[[8, 0]], camera_fit=True)
    geo["location"] = "Firelit Tavern Hall"
    return geo


def author_tavern_fit2() -> dict:
    """The tavern re-authored under the EXTENT CONTRACT **plus the DENSITY LAW** (TAVERN_FIT-V2 —
    M-ALIGN wave-2 capstone retry). The v1 ``tavern_fit`` regen (PR #1557) was coherence-PERFECT
    (recall 0.9554, 0 invented furniture) but the blind panel scored it 6.0 vs the 7.0 incumbent:
    a truth-rooted 6-prop greybox is inherently SPARSER than the incumbent, whose score is bought
    from *invented walk-through furniture* (painted stools/benches/shelf-clutter with NO collision
    cells) — the exact paint≠world defect M-ALIGN exists to eliminate. The #1557 architect ruling:
    per the RICHNESS PRINCIPLE (runbook / PR #1528), close the density gap by AUTHORING the furniture
    the panel LIKED as REAL props — more prop VOLUMES give the depth-CN base and the Gemini style pass
    real surfaces to carve, and every solid object maps to an authored (impassable) cell.

    This is ``tavern_fit`` (same camera-fit flag + continuous ``wall_run`` perimeter band, door open
    at (8,0)) with the 6-prop interior grown to **14 interior props**: benches flanking each of the
    three tables, a stool row at the bar, a shelf + cask cluster behind the bar, a hearth-side
    woodpile, and a barrel pair in the SE corner. All additions are LOW/MID kinds (``fallen_log`` 0.8
    for benches/stools/woodpile — the low seat/log silhouette; ``supply_crates`` 1.5 for bar shelving;
    ``barrel`` 1.4 for casks) so they read as furniture, not architecture; the identity (bench vs stool
    vs cask) is carried by the style-pass prompt, not the greybox. Each added prop is a SHORT 2-cell run
    (runbook step 2 / CAMP-TUNE defect #5) so its derived occlusion hull stays tight.

    Walkability is preserved BY CONSTRUCTION (validate_scene_grid / an 8-connectivity flood-fill both
    pass): the door zone (8,0)+(8,1) is clear, the front lane (row 8) is fully open, and every added
    prop hugs a table, a wall band, or a corner — never a circulation lane or a door approach. LOW props
    stay in cols 2-9 / rows 1-7 off the two NEAR walls (col0, the front wall) that would occlude them
    under the 30/45 dimetric camera; only the tall hearth + bar sit on the back/far bands.

    Kept as a SEPARATE room key ``tavern_fit2`` so ``tavern`` and ``tavern_fit`` both stay byte-identical
    for every existing consumer (the deployed plate + the engine-grid tests that pin their props)."""
    props = [
        # --- canonical interior (kept from author_tavern/author_tavern_fit) ---
        ("hearth", "hearth", [[5, 1], [6, 1]]),
        ("bar_counter", "bar", [[9, 2], [9, 3], [9, 4], [9, 5]]),
        ("table_nw", "table", [[3, 3], [4, 3], [3, 4], [4, 4]]),
        ("table_ne", "table", [[6, 3], [7, 3], [6, 4], [7, 4]]),
        ("table_s", "table", [[5, 6], [6, 6], [5, 7], [6, 7]]),
        ("barrels", "barrel", [[2, 6], [3, 6], [2, 7], [3, 7]]),
        # --- DENSITY-LAW additions (8 props → 14 interior). LOW band: benches/stools/woodpile ---
        ("woodpile", "fallen_log", [[3, 1], [4, 1]]),        # stacked logs beside the hearth (back wall)
        ("bench_nw", "fallen_log", [[3, 5], [4, 5]]),        # bench flanking table_nw (south long side)
        ("bench_ne", "fallen_log", [[6, 5], [7, 5]]),        # bench flanking table_ne (south long side)
        ("bench_s", "fallen_log", [[7, 6], [7, 7]]),         # bench flanking table_s (east long side)
        ("stools_bar", "fallen_log", [[8, 3], [8, 4]]),      # patron stools at the bar (patron side)
        # --- MID band: bar shelving + casks (behind the bar), corner barrels ---
        ("shelf_bar", "supply_crates", [[10, 2], [10, 3]]),  # back-bar shelf, behind the counter (far wall)
        ("casks_bar", "barrel", [[10, 4], [10, 5]]),         # ale casks stacked behind the bar
        ("barrels_corner", "barrel", [[10, 7], [10, 8]]),    # a barrel pair in the SE corner
    ]
    # --- explicit continuous perimeter wall band, door (8,0) left open (same as tavern_fit) ---
    props += _perimeter_wall_run_props(12, 10, door_cells=[[8, 0]])
    geo = _geometry(12, 10, "worn wooden planks", props, perimeter=False,
                    door_cells=[[8, 0]], camera_fit=True)
    geo["location"] = "Firelit Tavern Hall"
    return geo


_ROOMS = {"crypt": author_crypt, "crypt_rich": author_crypt_rich,
          "camp": author_camp, "tavern": author_tavern, "tavern_fit": author_tavern_fit,
          "tavern_fit2": author_tavern_fit2}


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
