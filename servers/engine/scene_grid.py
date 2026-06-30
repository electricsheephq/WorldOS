"""SceneGrid — the engine-authored, deterministic per-location spatial layout (A1).

The Python engine is the SOLE WRITER of a ``SceneGrid``: a procedural floor/wall/prop
layout per location, emitted at location-creation time (seed_world / travel_to /
add_location) and persisted on ``Location.scene_grid``. The Unity Tier-1 block-out
renderer draws straight from it; the Tier-2 painterly upgrade conditions a Scenario
ControlNet render on the same grid (see the contract at
worldos-session-notes/2026-06-22-unity-pivot/scene-grid-contract.md and the validated
fixture LEXAR/WorldOS-Unity-spike/fixtures/tavern.scenegrid.json).

LOAD-BEARING INVARIANTS (honored here):
  * **Additive-by-default.** ``Location.scene_grid`` defaults to ``None`` — an old
    snapshot round-trips to ``None`` and behaves exactly as today. Empty == today.
  * **_StrictModel (extra="forbid").** Every model below rejects unknown fields, so a
    typo'd field raises instead of silently vanishing.
  * **Deterministic.** The layout is generated from a LOCAL ``random.Random`` seeded off
    ``f"{world_id}:{location_id}"`` (the same string-seed pattern seed_world already uses
    for quest variants / questgen). It NEVER touches the global combat dice stream
    (``dice.reseed_process_rng``), so "the engine rolls combat" stays untouched.
  * **Disjoint from the #461 combat grid.** ``combat_grid.py`` (zone-based combat
    positioning, the authoritative x/y carve-out) is not imported or modified here; a
    SceneGrid is set-dressing/presentation DATA, not combat positioning.

This module is pure (no I/O, no MCP, no campaign lock). The emit hooks in content.py /
travel.py / server.py wrap ``emit_scene_grid`` and persist via the existing save path.
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Literal, Optional

from pydantic import Field

from models import _StrictModel

# Coordinate convention (matches the fixture): [col, row], col 0..cols-1 left->right,
# row 0..rows-1 back->front. Any in-bounds cell NOT listed in ``cells`` is ``cell_default``.
Cell = tuple[int, int]


class SceneGridSpec(_StrictModel):
    """Grid extents + projection. Mirrors the fixture's ``grid`` block."""

    cols: int = Field(0, ge=0)
    rows: int = Field(0, ge=0)
    cell_size_ft: int = Field(5, ge=1)
    projection: str = "dimetric-2to1"


class SceneCellDefault(_StrictModel):
    """The default fill for any in-bounds cell not explicitly listed in ``cells``."""

    type: str = "floor"
    walkable: bool = True
    cost: int = Field(1, ge=0)


class SceneCell(_StrictModel):
    """A single explicitly-typed cell. ``type`` is a free string (the renderer maps it):
    floor|wall|door|prop|water|void|low_wall|..."""

    c: int = Field(..., ge=0)
    r: int = Field(..., ge=0)
    type: str = "floor"
    walkable: bool = True
    cost: int = Field(1, ge=0)
    elevation: int = 0
    prop_ref: Optional[str] = None


class SceneProp(_StrictModel):
    """Set dressing + occluder. ``cells`` are the footprint; ``anchor_cell`` is where the
    renderer pins the sprite/proxy. ``occluder``/``height_band`` drive the Tier-2 depth
    occluder-proxies (so they stay load-bearing per the contract's ★ CORRECTION)."""

    id: str
    kind: str
    cells: list[Cell] = Field(default_factory=list)
    anchor_cell: Optional[Cell] = None
    occluder: bool = False
    height_band: Literal["low", "mid", "tall"] = "mid"
    silhouette: str = ""


class SceneLighting(_StrictModel):
    """Feeds BOTH the painterly-gen prompt AND the Unity actor-relight (so actors match
    the scene key light). ``key_dir_deg`` is the key-light azimuth in degrees."""

    key_dir_deg: int = 0
    key_color: str = "#ffffff"
    ambient_color: str = "#3a3f55"
    mood: str = ""


class SceneArt(_StrictModel):
    """Tier-2 cache slot, filled async by the painterly-upgrade workflow. The engine sets
    ``status="tier1_blockout"`` + ``layout_hash`` at emit time; the async pipeline fills
    the *_ref scope keys + ``critic_score`` and flips ``status`` to tier2_pending/ready."""

    layout_hash: str = ""
    backdrop_ref: Optional[str] = None
    depth_ref: Optional[str] = None
    walkmask_ref: Optional[str] = None
    status: Literal["tier1_blockout", "tier2_pending", "tier2_ready"] = "tier1_blockout"
    critic_score: Optional[float] = None


class SceneGrid(_StrictModel):
    """The engine-authored spatial layout for ONE location. Additive: present only when
    the engine has emitted one; absent (``Location.scene_grid is None``) == today."""

    scene_id: str
    location_id: str
    kind: str = "interior"
    biome: str = ""
    seed: int = 0
    grid: SceneGridSpec = Field(default_factory=SceneGridSpec)
    cell_default: SceneCellDefault = Field(default_factory=SceneCellDefault)
    cells: list[SceneCell] = Field(default_factory=list)
    props: list[SceneProp] = Field(default_factory=list)
    zone_anchors: dict[str, Cell] = Field(default_factory=dict)
    exits: list[dict] = Field(default_factory=list)
    spawns: dict[str, list[Cell]] = Field(default_factory=dict)
    # PROTECTED-PATHING discipline (additive; empty == today): door_cells are first-class doorway cells
    # (every exit cell is also a door cell, plus any authored interior archway); protected_lane_cells are a
    # connectivity-critical path the prop pass must keep CLEAR. A prop may NEVER occupy a door-zone cell
    # (door + Chebyshev-1) or a protected-lane cell (see validate_scene_grid). See
    # docs/roadmap/ROOM-OCCLUSION-PATHING-SPRINTS.md.
    door_cells: list[Cell] = Field(default_factory=list)
    protected_lane_cells: list[Cell] = Field(default_factory=list)
    lighting: SceneLighting = Field(default_factory=SceneLighting)
    art: SceneArt = Field(default_factory=SceneArt)


# ── Deterministic seed derivation ───────────────────────────────────────────────────


def derive_seed(world_id: str, location_id: str) -> int:
    """A stable non-negative 31-bit int derived from (world_id, location_id) via SHA-256.

    Used as the SceneGrid's ``seed`` field AND the seed of the LOCAL ``random.Random``
    that draws the layout. Deterministic + reproducible (same inputs -> same int -> same
    layout) and salt-free, so it survives process restarts and a re-emit on the same
    location is byte-identical. Does NOT touch the global dice stream."""
    h = hashlib.sha256(f"{world_id}:{location_id}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) % (2**31)


def _layout_hash(grid: SceneGrid) -> str:
    """A short content hash over the structural layout (grid + cells + props) — the Tier-2
    cache key + invalidation handle. Excludes the async ``art`` block (it would otherwise
    invalidate itself). Deterministic ordering: cells/props are emitted in a fixed order."""
    payload = grid.model_dump(
        mode="json",
        include={"grid", "cell_default", "cells", "props", "spawns", "exits", "zone_anchors", "lighting"},
    )
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


# ── Per-kind procedural generators ──────────────────────────────────────────────────


def _gen_tavern(scene_id: str, location_id: str, seed: int, rng: random.Random) -> SceneGrid:
    """A procedural tavern matching the fixture's SHAPE: perimeter walls, a bar along the
    back-left, a lit stone hearth (the warm key light) on the back-right, a couple of
    tables, a stack of barrels, party spawns by the entrance + a couple of foe spawns
    inside, warm candlelit lighting. Deterministic from ``rng`` (size + a little jitter)."""
    # Modest size jitter so taverns aren't all identical, but always large enough for the
    # fixture's furniture + walkable interior (>= the fixture's 14x10).
    cols = 14 + rng.randint(0, 2)   # 14..16
    rows = 10 + rng.randint(0, 2)   # 10..12

    cells: list[SceneCell] = []
    walls: set[Cell] = set()

    # Perimeter walls on the three back/side runs (the front row is the open entrance wall
    # with a door gap), matching the fixture: full back wall (row 0) + left/right columns.
    for c in range(cols):
        walls.add((c, 0))
    for r in range(1, rows - 1):
        walls.add((0, r))
        walls.add((cols - 1, r))
    for (c, r) in sorted(walls):
        cells.append(SceneCell(c=c, r=r, type="wall", walkable=False))

    props: list[SceneProp] = []

    def _place_prop(pid: str, kind: str, footprint: list[Cell], band: str,
                    silhouette: str, occluder: bool = True) -> None:
        anchor = footprint[0]
        props.append(SceneProp(
            id=pid, kind=kind, cells=footprint, anchor_cell=anchor,
            occluder=occluder, height_band=band, silhouette=silhouette,
        ))
        for (c, r) in footprint:
            cells.append(SceneCell(c=c, r=r, type="prop", walkable=False, prop_ref=pid))

    # The bar: a 4-cell counter along the back-left interior (row 1).
    bar_cells = [(c, 1) for c in range(1, 5)]
    _place_prop("bar", "bar_counter", bar_cells, "tall",
                "long waist-high wooden counter with bottles/shelf behind")

    # The hearth: 2 cells on the back-right interior (row 1) — the warm key light source.
    hearth_cells = [(cols - 3, 1), (cols - 2, 1)]
    _place_prop("hearth", "stone_hearth", hearth_cells, "tall",
                "large stone fireplace, fire lit (the warm key light source)")

    # Two tables in the mid-floor, jittered within safe interior bounds.
    t1_c = 3 + rng.randint(0, 1)
    t1_r = 4
    table1_cells = [(t1_c, t1_r), (t1_c + 1, t1_r)]
    _place_prop("table1", "round_table", table1_cells, "mid",
                "round wooden table with stools, mugs")

    t2_c = (cols // 2) + 1 + rng.randint(0, 1)
    t2_r = 5
    table2_cells = [(t2_c, t2_r), (t2_c + 1, t2_r)]
    _place_prop("table2", "long_table", table2_cells, "mid",
                "long trestle table with benches")

    # A barrel stack low against the left, a couple rows up from the entrance.
    barrel_cell = (2, rows - 3)
    _place_prop("barrels", "barrels", [barrel_cell], "low",
                "stacked ale barrels", occluder=True)

    # De-dup any accidental overlap (jitter could in theory collide a table with a wall):
    # keep the FIRST cell entry for any (c,r) and drop later duplicates so the cell list
    # stays a clean single-typed map (deterministic — input order is fixed above).
    seen: set[Cell] = set()
    deduped: list[SceneCell] = []
    for sc in cells:
        key = (sc.c, sc.r)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sc)
    cells = deduped

    # Zone anchors for engine zone positioning + DM narration hooks (mirrors the fixture).
    mid_c = cols // 2
    zone_anchors: dict[str, Cell] = {
        "the bar": (3, 2),
        "the hearth": (cols - 3, 2),
        "the entrance": (mid_c, rows - 1),
        "center floor": (mid_c, rows // 2),
    }

    # The door + party/foe spawns near the entrance (front), foes a couple cells inside.
    exits = [{"cell": [mid_c, rows - 1], "to_location_id": "", "label": "the door to the street"}]
    spawns = {
        "party": [(mid_c - 1, rows - 2), (mid_c, rows - 2), (mid_c + 1, rows - 2)],
        "foes": [(mid_c - 1, 2), (mid_c + 1, 3)],
    }

    lighting = SceneLighting(
        key_dir_deg=210,
        key_color="#ff9a45",
        ambient_color="#3a3f55",
        mood="warm candlelit, firelight from the right-side hearth, cool shadow fill",
    )

    grid = SceneGrid(
        scene_id=scene_id,
        location_id=location_id,
        kind="tavern",
        biome="warm candlelit stone-and-timber tavern interior",
        seed=seed,
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells,
        props=props,
        zone_anchors=zone_anchors,
        exits=exits,
        spawns=spawns,
        lighting=lighting,
    )
    grid.art.layout_hash = _layout_hash(grid)
    grid.art.status = "tier1_blockout"
    return grid


def _gen_default(scene_id: str, location_id: str, kind: str, seed: int,
                 rng: random.Random) -> SceneGrid:
    """A safe generic interior for any kind we don't yet have a bespoke generator for:
    a perimeter-walled room, a few ambient props (crates, barrels, a table), warm-neutral
    interior lighting (NOT white), and party spawns at the entrance. Keeps the emitter
    total (every location gets a valid, walkable Tier-1 block-out) while we add richer
    per-kind generators incrementally."""
    cols = 12 + rng.randint(0, 2)
    rows = 9 + rng.randint(0, 2)

    cells: list[SceneCell] = []
    for c in range(cols):
        cells.append(SceneCell(c=c, r=0, type="wall", walkable=False))
    for r in range(1, rows - 1):
        cells.append(SceneCell(c=0, r=r, type="wall", walkable=False))
        cells.append(SceneCell(c=cols - 1, r=r, type="wall", walkable=False))

    props: list[SceneProp] = []

    def _place_prop(pid: str, pkind: str, footprint: list[Cell], band: str,
                    silhouette: str, occluder: bool = True) -> None:
        anchor = footprint[0]
        props.append(SceneProp(
            id=pid, kind=pkind, cells=footprint, anchor_cell=anchor,
            occluder=occluder, height_band=band, silhouette=silhouette,
        ))
        for (c, r) in footprint:
            cells.append(SceneCell(c=c, r=r, type="prop", walkable=False, prop_ref=pid))

    # A crate stack against the back-left interior wall (row 1).
    _place_prop("crate_stack", "crates",
                [(2, 1)], "mid",
                "stacked wooden crates", occluder=True)

    # A barrel against the back-right interior (row 1).
    _place_prop("barrel", "barrels",
                [(cols - 3, 1)], "low",
                "an old wooden barrel", occluder=True)

    # A simple table in the mid-floor.
    mid_c = cols // 2
    _place_prop("table", "simple_table",
                [(mid_c, rows // 2)], "mid",
                "a plain wooden table", occluder=True)

    # De-dup (belt-and-suspenders: props are small here, but keep pattern consistent).
    seen: set[Cell] = set()
    deduped: list[SceneCell] = []
    for sc in cells:
        key = (sc.c, sc.r)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sc)
    cells = deduped

    zone_anchors = {
        "the entrance": (mid_c, rows - 1),
        "center floor": (mid_c, rows // 2),
    }
    exits = [{"cell": [mid_c, rows - 1], "to_location_id": "", "label": "the way out"}]
    spawns = {
        "party": [(mid_c - 1, rows - 2), (mid_c, rows - 2), (mid_c + 1, rows - 2)],
        "foes": [(mid_c - 1, 2), (mid_c + 1, 2)],
    }

    # Warm-neutral interior key — NOT #ffffff/0. A muted amber lantern-light from the left.
    lighting = SceneLighting(
        key_dir_deg=240,
        key_color="#d4a96a",
        ambient_color="#3a3f55",
        mood="dim interior, warm lantern light from the left, cool shadow fill",
    )

    grid = SceneGrid(
        scene_id=scene_id,
        location_id=location_id,
        kind=kind or "interior",
        biome="generic interior",
        seed=seed,
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells,
        props=props,
        zone_anchors=zone_anchors,
        exits=exits,
        spawns=spawns,
        lighting=lighting,
    )
    grid.art.layout_hash = _layout_hash(grid)
    grid.art.status = "tier1_blockout"
    return grid


def _gen_dungeon(scene_id: str, location_id: str, seed: int, rng: random.Random) -> SceneGrid:
    """A procedural dungeon chamber: perimeter walls, a walkable stone-floor interior,
    load-bearing props (pillars + rubble + a sarcophagus), brazier key light, cold-blue
    ambient. Deterministic from ``rng``."""
    cols = 14 + rng.randint(0, 3)   # 14..17
    rows = 11 + rng.randint(0, 3)   # 11..14

    cells: list[SceneCell] = []
    walls: set[Cell] = set()

    # Solid perimeter walls on all four sides.
    for c in range(cols):
        walls.add((c, 0))
        walls.add((c, rows - 1))
    for r in range(1, rows - 1):
        walls.add((0, r))
        walls.add((cols - 1, r))
    for (c, r) in sorted(walls):
        cells.append(SceneCell(c=c, r=r, type="wall", walkable=False))

    props: list[SceneProp] = []

    def _place_prop(pid: str, pkind: str, footprint: list[Cell], band: str,
                    silhouette: str, occluder: bool = True) -> None:
        anchor = footprint[0]
        props.append(SceneProp(
            id=pid, kind=pkind, cells=footprint, anchor_cell=anchor,
            occluder=occluder, height_band=band, silhouette=silhouette,
        ))
        for (c, r) in footprint:
            cells.append(SceneCell(c=c, r=r, type="prop", walkable=False, prop_ref=pid))

    # Two interior pillars, symmetric left/right at back-quarter row.
    pillar_r = 2 + rng.randint(0, 1)
    _place_prop("pillar_l", "stone_pillar",
                [(2, pillar_r)], "tall",
                "ancient stone pillar, cracked", occluder=True)
    _place_prop("pillar_r", "stone_pillar",
                [(cols - 3, pillar_r)], "tall",
                "ancient stone pillar, mossy", occluder=True)

    # Rubble pile low near the left side.
    rubble_r = rows // 2 + rng.randint(0, 1)
    _place_prop("rubble", "rubble_pile",
                [(2, rubble_r)], "low",
                "fallen masonry rubble", occluder=False)

    # A sarcophagus along the back wall center.
    mid_c = cols // 2
    _place_prop("sarcophagus", "sarcophagus",
                [(mid_c - 1, 1), (mid_c, 1)], "tall",
                "carved stone sarcophagus, lid ajar", occluder=True)

    # Two braziers — the warm key-light sources — flanking the sarcophagus.
    _place_prop("brazier_l", "brazier",
                [(mid_c - 3, 1)], "mid",
                "iron brazier, fire lit (warm key light)", occluder=False)
    _place_prop("brazier_r", "brazier_r",
                [(mid_c + 2, 1)], "mid",
                "iron brazier, fire lit (warm key light)", occluder=False)

    # De-dup.
    seen: set[Cell] = set()
    deduped: list[SceneCell] = []
    for sc in cells:
        key = (sc.c, sc.r)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sc)
    cells = deduped

    zone_anchors: dict[str, Cell] = {
        "the sarcophagus": (mid_c, 2),
        "the entrance": (mid_c, rows - 2),
        "center floor": (mid_c, rows // 2),
    }
    exits = [{"cell": [mid_c, rows - 1], "to_location_id": "", "label": "the passage back"}]
    spawns = {
        "party": [(mid_c - 1, rows - 3), (mid_c, rows - 3), (mid_c + 1, rows - 3)],
        "foes": [(mid_c - 1, 3), (mid_c + 1, 3)],
    }

    # Warm brazier key, cold-blue ambient — classic dungeon contrast.
    lighting = SceneLighting(
        key_dir_deg=200,
        key_color="#e8823a",
        ambient_color="#1a2040",
        mood="dim torchlit dungeon, warm brazier glow, cold blue shadow fill",
    )

    grid = SceneGrid(
        scene_id=scene_id,
        location_id=location_id,
        kind="dungeon",
        biome="ancient stone dungeon chamber, flickering brazier light",
        seed=seed,
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells,
        props=props,
        zone_anchors=zone_anchors,
        exits=exits,
        spawns=spawns,
        lighting=lighting,
    )
    grid.art.layout_hash = _layout_hash(grid)
    grid.art.status = "tier1_blockout"
    return grid


def _gen_forest(scene_id: str, location_id: str, seed: int, rng: random.Random) -> SceneGrid:
    """A procedural forest clearing: no hard perimeter walls (open sides), scattered tree
    and rock props as occluders, mostly walkable interior, daylight lighting (cool-neutral
    key, no torch). Deterministic from ``rng``."""
    cols = 16 + rng.randint(0, 3)   # 16..19
    rows = 12 + rng.randint(0, 3)   # 12..15

    cells: list[SceneCell] = []
    props: list[SceneProp] = []

    def _place_prop(pid: str, pkind: str, footprint: list[Cell], band: str,
                    silhouette: str, occluder: bool = True) -> None:
        anchor = footprint[0]
        props.append(SceneProp(
            id=pid, kind=pkind, cells=footprint, anchor_cell=anchor,
            occluder=occluder, height_band=band, silhouette=silhouette,
        ))
        for (c, r) in footprint:
            cells.append(SceneCell(c=c, r=r, type="prop", walkable=False, prop_ref=pid))

    # Tree line along the back (row 0..1) — four trees, evenly spaced.
    spacing = cols // 4
    for i, col_base in enumerate(range(1, cols - 1, spacing)):
        c = min(col_base + rng.randint(0, 1), cols - 2)
        _place_prop(f"tree_{i}", "large_tree",
                    [(c, 0), (c, 1)], "tall",
                    "gnarled forest tree, dense canopy", occluder=True)

    # Two rock clusters on the sides — visual anchors for the clearing.
    rock_r = rows // 2 + rng.randint(0, 1)
    _place_prop("rock_l", "boulder",
                [(1, rock_r)], "mid",
                "mossy boulder", occluder=True)
    _place_prop("rock_r", "boulder_r",
                [(cols - 2, rock_r)], "mid",
                "lichen-covered boulder", occluder=True)

    # A fallen log across the lower mid — low occluder, adds depth.
    log_c = cols // 2 - 1
    log_r = rows - 4
    _place_prop("log", "fallen_log",
                [(log_c, log_r), (log_c + 1, log_r)], "low",
                "fallen mossy log", occluder=True)

    # De-dup.
    seen: set[Cell] = set()
    deduped: list[SceneCell] = []
    for sc in cells:
        key = (sc.c, sc.r)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sc)
    cells = deduped

    mid_c = cols // 2
    zone_anchors: dict[str, Cell] = {
        "the clearing center": (mid_c, rows // 2),
        "the tree line": (mid_c, 2),
        "the trail": (mid_c, rows - 1),
    }
    exits = [{"cell": [mid_c, rows - 1], "to_location_id": "", "label": "the forest trail"}]
    spawns = {
        "party": [(mid_c - 1, rows - 3), (mid_c, rows - 3), (mid_c + 1, rows - 3)],
        "foes": [(mid_c - 1, 3), (mid_c + 1, 3)],
    }

    # Daylight — cool-neutral key from upper-left (sun), pale blue ambient (open sky).
    lighting = SceneLighting(
        key_dir_deg=145,
        key_color="#e8dcc8",
        ambient_color="#7090b0",
        mood="dappled forest daylight, cool-neutral sun from the upper-left, blue-sky ambient",
    )

    grid = SceneGrid(
        scene_id=scene_id,
        location_id=location_id,
        kind="forest",
        biome="open forest clearing, dappled daylight",
        seed=seed,
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells,
        props=props,
        zone_anchors=zone_anchors,
        exits=exits,
        spawns=spawns,
        lighting=lighting,
    )
    grid.art.layout_hash = _layout_hash(grid)
    grid.art.status = "tier1_blockout"
    return grid


def _gen_town(scene_id: str, location_id: str, seed: int, rng: random.Random) -> SceneGrid:
    """A procedural town square/plaza: building-edge walls along back + sides, a central
    open plaza with stalls, a well, and a cart as props, daylight lighting. Deterministic
    from ``rng``."""
    cols = 16 + rng.randint(0, 3)   # 16..19
    rows = 12 + rng.randint(0, 2)   # 12..14

    cells: list[SceneCell] = []
    walls: set[Cell] = set()

    # Building fronts: back wall (row 0) + partial left/right walls (just a few rows deep)
    # — simulates building facades, leaving the lower area open to the street.
    for c in range(cols):
        walls.add((c, 0))
    side_depth = 3 + rng.randint(0, 1)
    for r in range(1, side_depth):
        walls.add((0, r))
        walls.add((cols - 1, r))
    for (c, r) in sorted(walls):
        cells.append(SceneCell(c=c, r=r, type="wall", walkable=False))

    props: list[SceneProp] = []

    def _place_prop(pid: str, pkind: str, footprint: list[Cell], band: str,
                    silhouette: str, occluder: bool = True) -> None:
        anchor = footprint[0]
        props.append(SceneProp(
            id=pid, kind=pkind, cells=footprint, anchor_cell=anchor,
            occluder=occluder, height_band=band, silhouette=silhouette,
        ))
        for (c, r) in footprint:
            cells.append(SceneCell(c=c, r=r, type="prop", walkable=False, prop_ref=pid))

    mid_c = cols // 2

    # A market well at the plaza center — tall, two cells wide.
    well_r = rows // 2
    _place_prop("well", "stone_well",
                [(mid_c - 1, well_r), (mid_c, well_r)], "tall",
                "stone well with a rope and bucket, plaza centerpiece", occluder=True)

    # Two market stalls flanking the well on both sides.
    stall_r = well_r - 1
    _place_prop("stall_l", "market_stall",
                [(mid_c - 4, stall_r), (mid_c - 3, stall_r)], "mid",
                "canvas market stall, herbs and bread on display", occluder=True)
    _place_prop("stall_r", "market_stall_r",
                [(mid_c + 2, stall_r), (mid_c + 3, stall_r)], "mid",
                "canvas market stall, bolts of cloth for sale", occluder=True)

    # A cart near the entrance side — low, adds scale.
    cart_r = rows - 4
    _place_prop("cart", "merchants_cart",
                [(mid_c - 1, cart_r), (mid_c, cart_r)], "low",
                "wooden merchant's cart, parked", occluder=True)

    # De-dup.
    seen: set[Cell] = set()
    deduped: list[SceneCell] = []
    for sc in cells:
        key = (sc.c, sc.r)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sc)
    cells = deduped

    zone_anchors: dict[str, Cell] = {
        "the well": (mid_c, well_r),
        "the market stalls": (mid_c, stall_r),
        "the plaza entrance": (mid_c, rows - 1),
        "center plaza": (mid_c, rows // 2),
    }
    exits = [{"cell": [mid_c, rows - 1], "to_location_id": "", "label": "the main street"}]
    spawns = {
        "party": [(mid_c - 1, rows - 3), (mid_c, rows - 3), (mid_c + 1, rows - 3)],
        "foes": [(mid_c - 2, well_r - 2), (mid_c + 2, well_r - 2)],
    }

    # Daylight — bright overhead sun from upper-right, pale-blue sky ambient.
    lighting = SceneLighting(
        key_dir_deg=120,
        key_color="#f5e8c8",
        ambient_color="#98b8d0",
        mood="bright town square midday, warm overhead sun from the upper-right, clear-sky ambient",
    )

    grid = SceneGrid(
        scene_id=scene_id,
        location_id=location_id,
        kind="town",
        biome="outdoor town square, open plaza with market stalls and a stone well",
        seed=seed,
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells,
        props=props,
        zone_anchors=zone_anchors,
        exits=exits,
        spawns=spawns,
        lighting=lighting,
    )
    grid.art.layout_hash = _layout_hash(grid)
    grid.art.status = "tier1_blockout"
    return grid


# Registry of per-kind generators. Add a bespoke generator here as each kind is authored;
# anything not listed falls back to the generic interior.
_GENERATORS = {
    "tavern": _gen_tavern,
    "dungeon": _gen_dungeon,
    "forest": _gen_forest,
    "town": _gen_town,
}

# ── Kind-resolution keyword tables ──────────────────────────────────────────────────────

# Ordered list of (kind, keywords) tuples. Resolution walks this list and returns the FIRST
# match. Longer/more-specific lists go FIRST so "crypt" beats a hypothetical generic "cave".
_KIND_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("tavern",  ("tavern", "inn", "alehouse", "pub", "taproom")),
    ("dungeon", ("dungeon", "crypt", "cave", "catacomb", "tomb", "mine", "vault", "cellar")),
    ("forest",  ("forest", "wood", "woods", "glade", "grove", "clearing", "jungle", "thicket")),
    ("town",    ("town", "market", "square", "plaza", "district", "village", "city", "hamlet",
                 "settlement")),
]


def _infer_kind_from_text(text: str) -> str:
    """Resolve a scene kind from free text (name + notes). Returns the matching kind string
    or "" if nothing matches."""
    t = text.lower()
    for kind, keywords in _KIND_KEYWORDS:
        if any(w in t for w in keywords):
            return kind
    return ""


def _infer_kind_from_id(location_id: str) -> str:
    """Resolve a scene kind from the location_id itself (underscore/hyphen-segmented).
    Example: ``tavern_lower_city`` -> ``tavern``.  Returns the matching kind or ""."""
    t = location_id.lower().replace("-", " ").replace("_", " ")
    return _infer_kind_from_text(t)


def _infer_kind(name: str, notes: str, location_id: str = "") -> str:
    """Best-effort scene KIND resolution for generator selection.

    Resolution order (first non-empty match wins):
      (a) name + notes keywords (highest signal — the DM set the name deliberately);
      (b) location_id keywords (e.g. ``tavern_lower_city`` -> ``tavern``);
      (c) generic "interior" fallback.

    The caller (``emit_scene_grid``) applies the even-higher-priority explicit ``kind``
    argument BEFORE calling this function, so we never need to handle that here."""
    hit = _infer_kind_from_text(f"{name} {notes}")
    if hit:
        return hit
    hit = _infer_kind_from_id(location_id)
    if hit:
        return hit
    return "interior"


def emit_scene_grid(
    world_id: str,
    location_id: str,
    *,
    name: str = "",
    notes: str = "",
    kind: str = "",
) -> SceneGrid:
    """Emit a deterministic ``SceneGrid`` for one location (the engine's sole-writer hook).

    The layout is generated from a LOCAL ``random.Random`` seeded off
    ``derive_seed(world_id, location_id)`` — deterministic, reproducible, and isolated from
    the global combat dice stream.

    Kind resolution (first non-empty match wins):
      (a) explicit ``kind`` arg (caller override);
      (b) Location ``kind``/``type``/``biome`` field, if the Location model has one
          (``ensure_scene_grid`` passes these via the ``kind`` kwarg when present);
      (c) name + notes keywords;
      (d) location_id keywords (e.g. ``tavern_lower_city`` → ``tavern``);
      (e) generic "interior" fallback.

    Callers (content.seed_world / travel.travel_to / server.add_location) GUARD re-entry
    (skip if ``Location.scene_grid`` is already present) and persist via the existing save
    path; this function itself is pure (no I/O, no mutation of campaign state)."""
    seed = derive_seed(world_id, location_id)
    rng = random.Random(seed)
    scene_id = f"{world_id}:{location_id}"
    # (a) explicit kind wins
    resolved_kind = kind.strip()
    # (b/c/d) infer from name+notes, then from location_id
    if not resolved_kind:
        resolved_kind = _infer_kind(name, notes, location_id)
    generator = _GENERATORS.get(resolved_kind, None)
    if generator is not None:
        return generator(scene_id, location_id, seed, rng)
    return _gen_default(scene_id, location_id, resolved_kind, seed, rng)


def ensure_scene_grid(world_id: str, location) -> bool:
    """Emit + attach a ``SceneGrid`` onto ``location`` IFF it doesn't already have one.

    The shared GUARDED entry point for the three emit hooks (seed_world / travel_to /
    add_location). Re-entry guard: if ``location.scene_grid`` is already present it is a
    no-op (returns False) — so a re-visit / re-seed / re-emit never clobbers an existing
    grid (and so an async Tier-2 ``art`` fill is preserved). Returns True iff it attached
    a fresh grid. The caller persists via the existing save path; this only mutates the
    passed Location in memory. Pure-deterministic via ``emit_scene_grid``.

    Kind-resolution from the Location model: reads ``location.kind``, ``location.type``,
    and ``location.biome`` (in that order) if they exist, passing the first non-empty one
    as the explicit ``kind`` arg so the emitter's highest-priority resolution path fires.
    The Location model currently has no such fields (they're inferred from name/notes/id),
    but this is forward-compatible: if a future Location model gains a ``kind`` field the
    emitter will use it automatically."""
    if getattr(location, "scene_grid", None) is not None:
        return False
    # Probe the Location for an explicit kind/type/biome field (forward-compatible).
    explicit_kind = ""
    for field in ("kind", "type", "biome"):
        val = getattr(location, field, None)
        if val and isinstance(val, str):
            explicit_kind = val
            break
    location.scene_grid = emit_scene_grid(
        world_id,
        location.id,
        name=getattr(location, "name", "") or "",
        notes=getattr(location, "notes", "") or "",
        kind=explicit_kind,
    )
    return True


# ── SceneGrid → combat-grid obstacle derivation (gfx M-B) ─────────────────────────────


def impassable_cells(
    grid: "SceneGrid",
    width: int,
    height: int,
    *,
    occupied: set[Cell] = frozenset(),
) -> list[list[int]]:
    """Derive the IMPASSABLE combat-grid cells (walls + prop footprints) from a SceneGrid,
    so a fight bound to a painted room routes movement around its geometry.

    Returns a sorted list of ``[x, y]`` pairs in the exact shape ``Combat.grid_impassable``
    expects (the same shape ``set_grid(obstacles=...)`` produces), ready to assign directly.

    Coordinate mapping (the alignment care-point): a SceneGrid cell is ``(c, r)`` = (col, row),
    cols along +x and rows along +y (see the ``_gen_*`` generators), and the combat grid is
    ``(x, y)`` with ``grid_width == cols`` and ``grid_height == rows``. So the mapping is the
    identity ``c -> x``, ``r -> y`` — both are 0-indexed and ``cell_size_ft`` matches the combat
    ``grid_cell_size`` default of 5. We still CLIP every derived cell to ``0 <= x < width`` and
    ``0 <= y < height`` so a grid sized smaller than the scene (or vice-versa) never emits an
    out-of-bounds obstacle.

    A cell is impassable if EITHER:
      * an explicit ``SceneCell`` lists it with ``walkable=False`` (walls/props/water/void), OR
      * it falls inside any ``SceneProp.cells`` footprint (belt-and-suspenders: props already
        emit ``walkable=False`` cells, but a footprint cell missing from ``cells`` is still a
        solid object), OR
      * ``cell_default.walkable`` is False (a fully-solid default — every UNLISTED in-bounds
        cell is then a wall; rare, but honored).

    ``occupied`` cells (where a combatant already stands at fight-start) are EXCLUDED from the
    result so nobody is trapped on a cell the router would treat as a wall — a placement on a
    prop cell stays legal and movable. Pure: no I/O, no mutation, deterministic order.
    """
    if width <= 0 or height <= 0:
        return []

    blocked: set[Cell] = set()

    # A fully-solid default makes every in-bounds cell a wall unless an explicit cell overrides
    # it to walkable below. (Default is walkable=True for every authored generator, so this
    # branch is inert in practice — empty == today — but it keeps the derivation total.)
    default_walkable = True
    cd = getattr(grid, "cell_default", None)
    if cd is not None:
        default_walkable = bool(getattr(cd, "walkable", True))
    if not default_walkable:
        for x in range(width):
            for y in range(height):
                blocked.add((x, y))

    # Explicit cells: a walkable cell CLEARS the default-solid fill; a non-walkable cell BLOCKS.
    for sc in getattr(grid, "cells", None) or []:
        cell = (int(sc.c), int(sc.r))
        if not (0 <= cell[0] < width and 0 <= cell[1] < height):
            continue
        if getattr(sc, "walkable", True):
            blocked.discard(cell)
        else:
            blocked.add(cell)

    # Prop footprints: every cell a prop sits on is solid (occluder or not). Props already emit
    # walkable=False cells, but honor the footprint directly in case a cell was elided.
    for prop in getattr(grid, "props", None) or []:
        for (c, r) in getattr(prop, "cells", None) or []:
            cell = (int(c), int(r))
            if 0 <= cell[0] < width and 0 <= cell[1] < height:
                blocked.add(cell)

    # Never trap a combatant: a cell someone already stands on is walkable for this fight.
    blocked -= set(occupied)

    return [[x, y] for (x, y) in sorted(blocked)]


# ── Protected-pathing discipline: door zones + a pre-greybox validator (gfx occlusion/pathing Sprint 2) ──


def door_zone_cells(grid: "SceneGrid", width: int, height: int) -> set[Cell]:
    """The cells props must keep CLEAR around every doorway: each ``door_cell`` plus its Chebyshev-1 ring
    (clipped to bounds), so a doorway always has a free landing on both sides (the D&D 2-square-doorway /
    PoE2 'no furniture behind the door' convention). Pure, deterministic."""
    zone: set[Cell] = set()
    for (c, r) in getattr(grid, "door_cells", None) or []:
        for dc in (-1, 0, 1):
            for dr in (-1, 0, 1):
                x, y = int(c) + dc, int(r) + dr
                if 0 <= x < width and 0 <= y < height:
                    zone.add((x, y))
    return zone


def validate_scene_grid(grid: "SceneGrid", width: int, height: int) -> list[str]:
    """Pre-greybox GATE (run before any art is generated — Diablo's 'topology decided before dressing').
    Returns a list of human-readable violation strings (``[]`` == valid). Enforces the protected-pathing
    discipline so a generated room can never (a) place a prop in a door zone or a protected lane, (b) wall
    off a pocket of floor with a prop, or (c) be too crunched for actors to move. Pure: no I/O, no mutation,
    deterministic order. See docs/roadmap/ROOM-OCCLUSION-PATHING-SPRINTS.md."""
    issues: list[str] = []
    if width <= 0 or height <= 0:
        return ["grid has non-positive dimensions"]

    dz = door_zone_cells(grid, width, height)
    lanes: set[Cell] = {(int(c), int(r)) for (c, r) in (getattr(grid, "protected_lane_cells", None) or [])}

    # (1)+(2) prop placement: no prop footprint may sit in a door zone or a protected lane.
    for prop in getattr(grid, "props", None) or []:
        pid = getattr(prop, "id", "?")
        for (c, r) in getattr(prop, "cells", None) or []:
            cell = (int(c), int(r))
            if cell in dz:
                issues.append(f"prop '{pid}' at {list(cell)} blocks a DOOR ZONE (door + 1-cell landing)")
            if cell in lanes:
                issues.append(f"prop '{pid}' at {list(cell)} blocks a PROTECTED LANE")

    blocked: set[Cell] = {(x, y) for (x, y) in (tuple(p) for p in impassable_cells(grid, width, height))}
    walkable: list[Cell] = [(x, y) for x in range(width) for y in range(height) if (x, y) not in blocked]

    # (3) connectivity: every walkable cell must be in ONE region (a prop must never wall off a pocket —
    # this is exactly the 'sarcophagus jammed next to the staircase' failure the owner named).
    if walkable:
        from collections import deque  # noqa: PLC0415

        start = walkable[0]
        seen: set[Cell] = {start}
        q: deque[Cell] = deque([start])
        while q:
            cx, cy = q.popleft()
            for dc in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    if dc == 0 and dr == 0:
                        continue
                    nb = (cx + dc, cy + dr)
                    if nb not in blocked and nb not in seen and 0 <= nb[0] < width and 0 <= nb[1] < height:
                        seen.add(nb)
                        q.append(nb)
        unreached = [c for c in walkable if c not in seen]
        if unreached:
            issues.append(
                f"{len(unreached)} walkable cells are a DISCONNECTED pocket (a prop/wall walls them off), "
                f"e.g. {list(unreached[0])} — pathing would be broken"
            )

    # (4) every door cell + spawn cell must itself be walkable (you can't enter/spawn on a wall or prop).
    for (c, r) in getattr(grid, "door_cells", None) or []:
        if (int(c), int(r)) in blocked:
            issues.append(f"door cell {[int(c), int(r)]} is BLOCKED (wall/prop) — unusable doorway")
    for cells in (getattr(grid, "spawns", None) or {}).values():
        for (c, r) in cells:
            if (int(c), int(r)) in blocked:
                issues.append(f"spawn cell {[int(c), int(r)]} is BLOCKED (wall/prop)")

    # (5) enough clear combat floor (outside door zones/lanes) for actors to actually maneuver.
    clear = sum(1 for cell in walkable if cell not in dz and cell not in lanes)
    if clear < 12:
        issues.append(f"only {clear} clear combat-floor cells (< 12) — too crunched for actor movement")

    return issues
