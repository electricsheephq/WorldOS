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
    a perimeter-walled room with a door gap, no props, party spawns at the entrance. This
    keeps the emitter total (every location gets a valid, walkable Tier-1 block-out) while
    we add richer per-kind generators incrementally."""
    cols = 12 + rng.randint(0, 2)
    rows = 9 + rng.randint(0, 2)

    cells: list[SceneCell] = []
    for c in range(cols):
        cells.append(SceneCell(c=c, r=0, type="wall", walkable=False))
    for r in range(1, rows - 1):
        cells.append(SceneCell(c=0, r=r, type="wall", walkable=False))
        cells.append(SceneCell(c=cols - 1, r=r, type="wall", walkable=False))

    mid_c = cols // 2
    zone_anchors = {
        "the entrance": (mid_c, rows - 1),
        "center floor": (mid_c, rows // 2),
    }
    exits = [{"cell": [mid_c, rows - 1], "to_location_id": "", "label": "the way out"}]
    spawns = {"party": [(mid_c - 1, rows - 2), (mid_c, rows - 2), (mid_c + 1, rows - 2)]}

    grid = SceneGrid(
        scene_id=scene_id,
        location_id=location_id,
        kind=kind or "interior",
        biome="",
        seed=seed,
        grid=SceneGridSpec(cols=cols, rows=rows, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells,
        zone_anchors=zone_anchors,
        exits=exits,
        spawns=spawns,
    )
    grid.art.layout_hash = _layout_hash(grid)
    grid.art.status = "tier1_blockout"
    return grid


# Registry of per-kind generators. Add a bespoke generator here as each kind is authored;
# anything not listed falls back to the generic interior.
_GENERATORS = {
    "tavern": _gen_tavern,
}


def _infer_kind(name: str, notes: str) -> str:
    """Best-effort scene KIND from a Location's free text (name + notes/tags). The engine
    has no explicit per-location ``kind`` field, so we infer one for the generator pick.
    Conservative: only the kinds we have a bespoke generator for are matched; everything
    else falls through to the generic interior."""
    text = f"{name} {notes}".lower()
    if any(w in text for w in ("tavern", "inn", "alehouse", "pub", "taproom")):
        return "tavern"
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
    the global combat dice stream. ``kind`` is taken as given, else inferred from name/notes.
    Callers (content.seed_world / travel.travel_to / server.add_location) GUARD re-entry
    (skip if ``Location.scene_grid`` is already present) and persist via the existing save
    path; this function itself is pure (no I/O, no mutation of campaign state)."""
    seed = derive_seed(world_id, location_id)
    rng = random.Random(seed)
    scene_id = f"{world_id}:{location_id}"
    resolved_kind = kind.strip() or _infer_kind(name, notes)
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
    passed Location in memory. Pure-deterministic via ``emit_scene_grid``."""
    if getattr(location, "scene_grid", None) is not None:
        return False
    location.scene_grid = emit_scene_grid(
        world_id,
        location.id,
        name=getattr(location, "name", "") or "",
        notes=getattr(location, "notes", "") or "",
    )
    return True
