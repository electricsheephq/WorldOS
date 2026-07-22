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


def author_crypt_fresh() -> dict:
    """14x11 enclosed stone crypt — a BRAND-NEW crypt authored with EVERY M-ALIGN learning applied
    (FRESH-CRYPT lane).

    CRYPT-ALIGN-V2 (M-ALIGN, 2026-07-15): REALIGNED to the PAINTED crypt_fresh_v1 plate. Fit-camera
    overlay forensics (ortho=10.5224 via qa/greybox_render_headless._fit_ortho_size(14,11), CENTER
    convention) proved flux depth-CN RELOCATED the interior furniture during the style pass: the hero
    sarcophagus is painted as a 6x2 monumental tomb across cols 7-12 x rows 3-4 (base; the lid/effigy
    silhouette rises up-screen over rows 2-3), NOT the authored 2x2 coffin at cols4-5 x rows7-8 — which
    the plate paints as OPEN FLOOR. The left pillar plinth is painted at (4,2)/(4,3) (authored (3,3)/(3,4)
    is painted floor). And pillar_r (8,9)/(9,9), skull_pile (2,9)/(3,9), urn_spill (11,9) are INVISIBLE:
    they sit behind the wall_height=5 cutaway's near south/east wall band, and flux painted the
    skulls/urn OUTSIDE the walls on the non-playable exterior apron — their authored cells are painted
    clear floor. Edge-recall (0.96/0.975) passed anyway because that metric is dominated by walls/extent
    and is structurally insensitive to small/low props (#1491); only check_grid_paint_coherence + the
    visual sweep measured the prop-level drift. So the geometry is realigned to the PAINT: sarcophagus ->
    cols 7-12 rows 3-4, pillar_l -> (4,2)/(4,3), pillar_r/skull_pile/urn_spill DELETED. The seed collision
    (qa/seed_gfx_combat.py) is realigned in lock-step so pathing agrees with paint.

    ONE forced deviation from the pure paint truth (door-zone gate): with the tomb foot at (12,3)/(12,4)
    and the tavern doorway at (13,4), a sarcophagus PROP on (12,3)/(12,4) trips validate_scene_grid's
    door-zone rule (props must keep a doorway's Chebyshev-1 landing clear). So BOTH the geometry coffin
    AND the seed coffin are trimmed one cell at the east end to cols 7-11 (a 5x2 tomb). The plate's
    1-cell paint overhang at col 12 is left as an ACCEPTED, DOCUMENTED residual (it flags in the visual
    sweep at (12,4); it is NOT silently exempted). See qa/evidence/crypt-fresh/WALKSLICE-RECONCILIATION.md
    (v2 addendum). This is NOT a regen of the deployed incumbent (``crypt_armb_iter3``, panel 8.0)
    nor of ``crypt_rich``: it is a fresh authoring that combines the RICHNESS PRINCIPLE (PR #1528 — dense
    ornament VOLUMES so the depth-CN base + Gemini style pass have surfaces to carve) with the EXTENT
    CONTRACT (#1543 — ``camera_fit`` + a CONTINUOUS ``wall_run`` perimeter band split at the doors, the
    #1539 no-crenellation rule) that ``crypt_rich`` predated and therefore lacked (it used ``perimeter=True``
    per-cell walls, which give the depth map the toothed/castellated wall-top penalized as a "tiled/gamey"
    motif). Three learnings not in ``crypt_rich``:

    1. **CAMERA-FIT + continuous wall band** (extent contract) — no canvas margin to out-paint, painted
       walls sit ON impassable cells by construction, both door gaps stay walkable (``wall_run`` split at
       (6,0) + (13,4)).
    2. **TRUE 2x2-proportioned coffin** — the sarcophagus is authored as the correct-scale 2x2 stone
       coffin (centered in the canonical cols2-7 x rows7-9 tomb region, #1505), NOT the 12-cell drift
       blob ``crypt_rich`` inherited from the seed. Cleaner silhouette, tighter coherence localisation.
    3. **DOOR RINGS kept clear** — both door landings ((6,1) for the camp seam, (12,4) for the tavern
       seam) are prop-free; torches FLANK the top door (5,1)/(7,1) and a pilaster sits BESIDE the tavern
       door (12,3), never on the landing.

    Canonical layout contract kept: pillars PILLAR_L_CELLS (3,3)/(3,4) + PILLAR_R_CELLS (8,9)/(9,9)
    (imported from the engine seed so they track the combat grid), doors (6,0) (camp) + (13,4) (tavern).
    The fresh geometry's coffin footprint DIFFERS from the canonical combat grid's sarcophagus footprint
    (the engine seed is NOT edited in this lane — walkslice reconciliation is REPORTED, per #1559).

    Ornament classes (effigy niches, skull/bone piles, rubble, torch brackets, spilled urn) are authored
    as real prop VOLUMES in SHORT 1-2 cell runs (runbook step 2 / CAMP-TUNE defect #5 — tight occlusion
    hulls). TALL ornaments (braziers 2.2, altars 2.0, engaged column 7.5) sit on the BACK band (row 1) or
    FAR wall (col 12) where they won't occlude the interior under the 30/45 dimetric camera; LOW clutter
    (rubble/skull/urn ~1.4) tucks into the near/left corners. Prop identity (knotwork/effigy/skulls/
    torch-flame/cobwebs) is carried by the style-pass prompt, not the greybox. Walkable topology
    (the ring around the tomb + reachability of both doors) is preserved by construction."""
    sc = _load_seed("_seed_crypt", "seed_gfx_combat.py")
    props = [
        # --- CRYPT-ALIGN-V2: realigned to the painted plate (pillar_l tracks the engine combat grid) ---
        ("pillar_l", "stone_pillar", sc.PILLAR_L_CELLS),          # (4,2)/(4,3) plinth -> carved knotwork column
        # PAINT-TRUE monumental tomb: painted base cols 7-12 x rows 3-4, trimmed to cols 7-11 for the
        # tavern-door zone (see docstring) -> raised tomb, reclining effigy lid rising up-screen.
        ("sarcophagus", "sarcophagus", sc.SARCOPHAGUS_CELLS),
        # --- TALL ornament volumes on the BACK band (row 1) / FAR wall (col 12) — won't occlude interior ---
        ("effigy_niche_l", "altar", [[2, 1], [3, 1]]),           # carved wall effigy niche, back-left (2-cell)
        ("torch_door_l", "brazier", [[5, 1]]),                   # lit torch bracket, left of the camp door
        ("torch_door_r", "brazier", [[7, 1]]),                   # lit torch bracket, right of the camp door
        ("niche_back_r", "altar", [[10, 1], [11, 1]]),           # recessed wall niche / 2nd tomb slab (2-cell)
        ("pilaster_arch", "stone_pillar", [[12, 3]]),            # engaged column BESIDE the tavern door (12,4 clear)
        ("torch_near_l", "brazier", [[1, 4]]),                   # lit torch bracket, left (near) wall
        ("torch_far_r", "brazier", [[12, 6]]),                   # lit torch bracket, far (right) wall
        # --- LOW floor clutter, near/left corners (won't occlude, won't block circulation or doors) ---
        ("rubble_bl", "rubble", [[1, 1], [1, 2]]),               # rubble pile, back-left corner (2-cell)
        ("broken_slabs", "rubble", [[1, 6], [1, 7]]),            # heaved/broken floor slabs, left wall (2-cell)
        # CRYPT-ALIGN-V2: pillar_r, skull_pile, urn_spill DELETED — painted behind the cutaway / outside
        # the playable walls (their authored cells are painted clear floor; the flux relocation forensics).
    ]
    # continuous perimeter wall band as wall_run props, split at both doors (no crenellation, doors open)
    props += _perimeter_wall_run_props(sc.GRID_W, sc.GRID_H, door_cells=[[6, 0], [13, 4]])
    geo = _geometry(sc.GRID_W, sc.GRID_H, "ancient stone", props, perimeter=False,
                    door_cells=[[6, 0], [13, 4]], camera_fit=True)
    geo["location"] = "Ancient Stone Crypt (firelit)"
    return geo


def author_camp_v2() -> dict:
    """CAMP REGEN (#1644, owner-directed retirement of the paint-first camp plates): the modern
    geometry-FIRST camp. author_camp()'s owner-playtest-tuned footprints stay VERBATIM as ground
    truth, plus:
    - DOORS at the adventure-fixture wire contract: (8,0) north -> tavern_snug, (0,6) west -> crypt
      (seed_adventure_demo ROOMS adjacency — the fixture discovers cells live, but the authored
      geometry must carry them for the greybox door cues + landing checks).
    - 3 NON-COLLINEAR FIRE BEACONS (the #1621 registration bar): the campfire (4,8)-(5,8) plus two
      1-cell standing torches — torch_shelter (12,1) against the shelter frame, torch_ruin (14,9)
      in the ruin courtyard. Triangle area 37 cell^2 (bar: >=2.0). Beacon geometry IS room design:
      these give the blob solver both axes on an outdoor plate with no wall lattice.
    The paint-first camp_clearing/_night plates are RETIRED — never patch geometry under them."""
    g = author_camp()
    g["door_cells"] = [[8, 0], [0, 6]]
    g.pop("doors", None)
    g["props"].append({"id": "torch_shelter", "kind": "brazier", "cells": [[12, 1]]})
    g["props"].append({"id": "torch_ruin", "kind": "brazier", "cells": [[14, 9]]})
    # ORPHAN-POCKET design pass (the invisible-wall class the owner flagged on the OLD paint —
    # geometry-first lets us design it out instead of rubble-filling after the fact):
    # 1. SOUTH FIELD: shorten the log pile by one cell (8,9) — reconnects the whole 6-cell south
    #    pocket through (9,9) as real play space.
    #    Also (3,9): the treeline perimeter closes the old open map edge, so the SW field needs a
    #    walk-through gap in the bedroll line — campsites have gaps between bedrolls.
    for prop in g["props"]:
        prop["cells"] = [c for c in prop["cells"] if tuple(c) not in {(8, 9), (3, 9)}]
    g["props"] = [prop for prop in g["props"] if prop["cells"]]
    # 2. SHELTER INTERIOR: enclosed by the frame — honest bedding, never walkable-looking dead cells.
    g["props"].append({"id": "shelter_bedding", "kind": "bedroll",
                       "cells": [[11, 4], [11, 5], [12, 4], [12, 5]]})
    # 3. RUIN COURTYARD: enclosed by walls/towers — debris-choked interior (the torch stands amid it;
    #    beacons need VISIBILITY, not reachability). 2-cell crate/ruin slot likewise.
    g["props"].append({"id": "ruin_debris", "kind": "rubble",
                       "cells": [[12, 7], [13, 5], [13, 6], [13, 7], [13, 8], [13, 9],
                                 [14, 6], [14, 7], [14, 8]]})
    g["props"].append({"id": "ruin_slot_debris", "kind": "rubble", "cells": [[11, 10], [11, 11]]})
    # EXTENT CONTRACT (#1543): the unified builder requires wall_run props. An outdoor clearing's
    # perimeter IS the forest treeline (the old painting shows exactly this) — honest impassable
    # boundary, minus the two door paths. Cells already held by props (ruin towers etc.) stay theirs.
    taken = {tuple(c) for prop in g["props"] for c in prop["cells"]}
    doors_set = {tuple(d) for d in g["door_cells"]}
    cols, rows = g["cols"], g["rows"]
    perim = ([(c, 0) for c in range(cols)] + [(c, rows - 1) for c in range(cols)]
             + [(0, r) for r in range(rows)] + [(cols - 1, r) for r in range(rows)])
    tree_cells = [list(c) for c in dict.fromkeys(perim) if c not in taken and c not in doors_set]
    # The pocket BEHIND the lean-to (sealed by shelter + torch + perimeter) is forest undergrowth
    # creeping into the clearing's NE corner — honest treeline, not walkable-looking dead space.
    tree_cells += [[13, 1], [13, 2], [14, 1], [14, 2], [14, 3]]
    g["props"].append({"id": "treeline", "kind": "wall_run", "cells": tree_cells})
    # Render-contract schema (mirror qa/room_geometries/*_geometry.json consumers).
    prop_cells = sorted({tuple(c) for prop in g["props"] for c in prop["cells"]})
    doors = {tuple(d) for d in g["door_cells"]}
    g["impassable"] = [list(c) for c in prop_cells if c not in doors]
    g["walls"] = [c for c in tree_cells]
    g["cell_default_walkable"] = True
    g["outdoor"] = True
    g["location"] = "camp_clearing"
    g["material"] = "forest_floor"
    landings = sorted({(dc + oc, dr + orr) for (dc, dr) in doors
                       for (oc, orr) in ((0, 1), (0, -1), (1, 0), (-1, 0))
                       if 0 <= dc + oc < g["cols"] and 0 <= dr + orr < g["rows"]
                       and (dc + oc, dr + orr) not in {tuple(x) for x in prop_cells}})
    g["protected_lane_cells"] = [list(c) for c in landings]
    return g


def author_camp() -> dict:
    """16x12 open-air night campfire clearing: NO perimeter (outdoor). The camp's error is the opposite
    of the crypt's — the SCENE is painted ~25% too small while the seed prop footprints are already
    plausible real-world sizes, so footprints are kept VERBATIM and the correct-scale greybox + registered
    regeneration fixes the paint fill. ONE exception: the fire pit's 2x2 (10ftx10ft) seed footprint is
    oversized; a campfire + stone ring is ~2x1 cells. Owner playtest #7 CAMP-TUNE (2026-07-11) further
    re-measured several footprints directly against the ADOPTED true-greybox plate (woodpile, crate
    cluster, shelter posts/back-wall, the ruin's tower/link walls) — see seed_gfx_camp.py's per-constant
    comments for the per-defect rationale. CAMP-CELLS wave-2 (#1540/#1552, 2026-07-15) added 7 more
    short props (firewood_tail, gear_stones, camp_sack, shelter_post_r, ruin_rubble1/2) for painted
    solids the inverse-coherence visual sweep flagged with no footprint — seed_gfx_camp.py stays the
    ONE source; this function just mirrors its OBSTACLES list into named geometry props. FOLLOW-UP:
    the derived true-greybox manifest (qa/room_manifests/camp_truegrey.cells.json, whose per-prop
    fingerprints feed check_grid_paint_coherence.py) was NOT regenerated by this wave — it still
    reflects the pre-wave-2 prop set; re-running the true-greybox derivation pipeline to add the 7
    new props' fingerprints is separate follow-up work, not required for the walkability fix itself."""
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
        # CAMP-CELLS wave-2 (#1540/#1552, 2026-07-15): 7 painted solids the inverse-coherence sweep
        # flagged with no footprint yet — see seed_gfx_camp.py's per-constant comments.
        ("firewood_tail", "fallen_log", cp.FIREWOOD_TAIL_CELLS),
        ("gear_stones", "rubble", cp.GEAR_STONES_CELLS),
        ("camp_sack", "rubble", cp.CAMP_SACK_CELLS),
        ("shelter_post_r", "timber_frame", cp.SHELTER_POST_R_CELLS),
        ("ruin_rubble1", "stone_wall", cp.RUIN_RUBBLE1_CELLS),
        ("ruin_rubble2", "stone_wall", cp.RUIN_RUBBLE2_CELLS),
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


def author_shop() -> dict:
    """13x10 firelit GENERAL-GOODS SHOP — the Room Readiness Pipeline's scale-proof class (epic #1581,
    issue #1588): a brand-new interior class authored to run the WHOLE chain (geometry → unified render
    → pinned paint → beauty gate → WALK gate) hands-off, proving the pipeline generalises past the
    shipped crypt/tavern/throne trio.

    Authored under the EXTENT CONTRACT + the DENSITY LAW (tavern_fit2 pattern): explicit continuous
    ``wall_run`` perimeter band with the two doors left open; a rich prop set so the depth-CN base has
    real surfaces to carve (RICHNESS PRINCIPLE) and every solid object maps to an authored impassable
    cell (paint == world). Focal point: the 4-cell COUNTER mid-room with open floor on every side; a
    tall SHELVING run against the back wall (unknown kind ⇒ the 2.6 default mass — shelving IS boxy);
    barrels/crates/sacks as short 2-cell mid/low runs; a customer bench (low log silhouette); one
    brazier hugging the counter end for the warm key light. Identity (shelves vs crates vs sacks) is
    carried by the style-pass prompt, not the greybox.

    Walkability by construction: back door (6,0) landing (6,1) clear; east door (12,5) landing (11,5)
    clear; row 8 = the fully open front lane; rows 2 and 4 = open aisles around the counter. LOW props
    stay in cols 2-11 / rows 1-7, off the two NEAR walls (col0, the front wall) per the 30/45 dimetric
    placement law; only the tall shelving sits on the back band. Doors: (6,0) = the world seam
    (crypt/tavern side), (12,5) = a second seam for town wiring."""
    props = [
        ("shelves_back", "shelf", [[2, 1], [3, 1], [4, 1], [5, 1]]),   # tall back-wall shelving run
        ("till_table", "table", [[10, 1], [11, 1]]),                   # till/wrapping table, right band
        ("counter", "bar", [[5, 3], [6, 3], [7, 3], [8, 3]]),          # THE focal: centred shop counter
        ("brazier_counter", "brazier", [[9, 3]]),                      # warm key light at the counter end
        ("barrels_e", "barrel", [[10, 4], [10, 5]]),                   # goods barrels, east aisle
        ("bench_front", "fallen_log", [[5, 5], [6, 5]]),               # low customer bench, counter front
        ("display_table", "table", [[7, 6], [8, 6], [7, 7], [8, 7]]),  # 2x2 wares display, SE-centre
        ("crates_sw", "supply_crates", [[2, 6], [3, 6], [2, 7], [3, 7]]),  # crate stack, SW interior
        ("sacks_se", "crate", [[10, 7], [11, 7]]),                     # grain sacks/crates, SE corner
    ]
    props += _perimeter_wall_run_props(13, 10, door_cells=[[6, 0], [12, 5]])
    geo = _geometry(13, 10, "worn wooden planks", props, perimeter=False,
                    door_cells=[[6, 0], [12, 5]], camera_fit=True)
    geo["location"] = "General Goods Shop"
    return geo


def author_tavern_snug() -> dict:
    """12x10 firelit SNUG tavern — the Room Readiness Pipeline's VARIANT proof (epic #1581, #1588):
    the SAME room class (tavern vocabulary: bar/hearth/tables/kegs) in a genuinely DIFFERENT layout,
    proving the pipeline mints class variants without new molded kinds or recipe machinery. Where
    tavern_v2 runs its bar along the back-left with the hearth back-right, the snug turns the plan
    90°: the BAR spans the back wall centre, the HEARTH sits in the EAST wall, seating clusters in
    the south-west quadrant, kegs rack behind the bar's west end.

    Density-law prop set; walkability by construction (door (5,0) landing clear, front lane row 8
    open, aisles rows 2 and 4-5 open); LOW props off the two NEAR walls per the 30/45 placement law;
    door (11,4) = an east seam left for town wiring (declare in the seed allowlist)."""
    props = [
        ("kegs_back", "barrel", [[1, 1], [1, 2]]),                     # keg rack, WEST wall band
        # (moved from (2,1)+(3,1): with post_w added, the back-west kegs orphaned the whole
        # west strip — 14 unreachable cells, caught by walk_static pre-CU)
        ("bar_snug", "bar", [[4, 2], [5, 2], [6, 2], [7, 2]]),         # THE focal: back-centre bar
        ("hearth_east", "hearth", [[10, 2], [10, 3]]),                 # chimney breast, EAST wall
        # (10,4) stays clear — it is the east door (11,4)'s landing; the static gate caught the
        # original (10,3)+(10,4) placement blocking it (walk_static check_geometry, pre-CU)
        ("candle_bar", "brazier", [[8, 2]]),                           # light at the bar's east end
        # CYCLE-3 root lever (flat-interior class, panels c1+c2 both 6-vs-9): the paint INVENTED a
        # free-standing post in both cycles because nothing tall anchors the interior depth. Author
        # the posts it keeps asking for — two structural TIMBER SUPPORTS (pillar kind = tall molded
        # shaft) framing the mid-room, per the RICHNESS PRINCIPLE (paint richness follows geometry).
        ("post_w", "pillar", [[3, 3], [3, 4]]),                        # west roof post, stone base
        ("post_e", "pillar", [[8, 4], [8, 5]]),                        # east roof post, stone base
        ("table_sw", "table", [[3, 5], [4, 5], [3, 6], [4, 6]]),       # 2x2 communal table, SW
        ("bench_sw", "fallen_log", [[5, 5], [5, 6]]),                  # bench on the table's east side
        ("table_s", "table", [[7, 6], [8, 6], [7, 7], [8, 7]]),        # 2x2 table, south-centre
        ("woodpile_e", "fallen_log", [[10, 6], [10, 7]]),              # hearth-side woodpile
        ("barrels_sw", "barrel", [[2, 7], [2, 8]]),                    # corner casks
    ]
    props += _perimeter_wall_run_props(12, 10, door_cells=[[5, 0], [11, 4]])
    geo = _geometry(12, 10, "worn wooden planks", props, perimeter=False,
                    door_cells=[[5, 0], [11, 4]], camera_fit=True)
    geo["location"] = "The Snug (tavern variant)"
    return geo


_ROOMS = {"crypt": author_crypt, "crypt_rich": author_crypt_rich,
          "crypt_fresh": author_crypt_fresh,
          "camp": author_camp, "tavern": author_tavern, "tavern_fit": author_tavern_fit,
          "tavern_fit2": author_tavern_fit2, "shop": author_shop,
          "tavern_snug": author_tavern_snug}


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
