"""A1 — the SceneGrid emitter (engine sole-writer).

Pins the load-bearing contract for the engine-authored per-location spatial layout:
  * ADDITIVE — an old snapshot WITHOUT scene_grid loads -> scene_grid is None, byte-identical.
  * ROUND-TRIP — a Location carrying a scene_grid serializes -> deserializes byte-identical.
  * DETERMINISTIC — same (world_id, location_id) -> identical SceneGrid (reproducible).
  * GENERATOR VALIDITY — the tavern generator emits a valid SceneGrid (perimeter walls,
    walkable interior, the required props, party + foe spawns).
  * EMIT HOOKS — seed_world / travel_to / add_location attach a grid (guarded re-entry).
  * ISOLATION — the emitter never touches the global combat dice stream (invariant #4).

Engine-only; additive. Does NOT touch the #461 combat grid (combat_grid.py).
"""

from __future__ import annotations

import content
import server
from models import Location, SceneGrid
from scene_grid import derive_seed, emit_scene_grid, ensure_scene_grid


# ── ADDITIVITY: an old snapshot (no scene_grid) loads to None, unchanged ──────────────


def test_old_location_without_scene_grid_loads_to_none():
    """A Location JSON lacking the scene_grid key round-trips to scene_grid=None — proving
    the field is additive (empty == today; old snapshots behave exactly as before)."""
    legacy = '{"id":"loc_legacy","name":"Old Room","description":"a room"}'
    loc = Location.model_validate_json(legacy)
    assert loc.scene_grid is None
    # ...and re-serializing leaves the place otherwise intact.
    again = Location.model_validate_json(loc.model_dump_json())
    assert again.scene_grid is None
    assert again.name == "Old Room"


def test_location_default_scene_grid_is_none():
    """A freshly constructed Location defaults scene_grid to None (no auto-emit at the
    model layer — only the explicit engine hooks emit)."""
    assert Location(name="Bare").scene_grid is None


# ── ROUND-TRIP: a Location WITH a scene_grid serializes byte-identical ────────────────


def test_location_with_scene_grid_round_trips_byte_identical():
    loc = Location(name="The Yawning Portal", notes="tavern")
    loc.scene_grid = emit_scene_grid("baldurs-gate", "tavern_lower_city",
                                     name="The Yawning Portal Tavern")
    dumped = loc.model_dump_json()
    reloaded = Location.model_validate_json(dumped)
    assert reloaded.scene_grid is not None
    assert isinstance(reloaded.scene_grid, SceneGrid)
    # Byte-identical serialization (save -> load -> save).
    assert reloaded.model_dump_json() == dumped


def test_scene_grid_is_strict_model():
    """SceneGrid (and its sub-models) inherit extra='forbid' — a typo'd field raises."""
    import pytest
    from pydantic import ValidationError

    g = emit_scene_grid("w", "loc1").model_dump()
    g["bogus_field"] = 1
    with pytest.raises(ValidationError):
        SceneGrid.model_validate(g)


# ── DETERMINISM: same inputs -> identical grid ────────────────────────────────────────


def test_emit_is_deterministic_same_inputs():
    a = emit_scene_grid("baldurs-gate", "tavern_lower_city", name="A Tavern")
    b = emit_scene_grid("baldurs-gate", "tavern_lower_city", name="A Tavern")
    assert a.model_dump_json() == b.model_dump_json()
    # The derived seed is stable + non-negative + bounded.
    s = derive_seed("baldurs-gate", "tavern_lower_city")
    assert s == a.seed
    assert 0 <= s < 2**31


def test_different_locations_get_different_layouts():
    """A different location id yields a different deterministic layout (the seed varies)."""
    a = emit_scene_grid("baldurs-gate", "tavern_one", name="Tavern One")
    b = emit_scene_grid("baldurs-gate", "tavern_two", name="Tavern Two")
    assert a.seed != b.seed
    # scene_id encodes the deterministic key.
    assert a.scene_id == "baldurs-gate:tavern_one"
    assert b.scene_id == "baldurs-gate:tavern_two"


# ── GENERATOR VALIDITY: the tavern matches the fixture's SHAPE ────────────────────────


def _cells_by_coord(grid: SceneGrid) -> dict[tuple[int, int], object]:
    return {(c.c, c.r): c for c in grid.cells}


def test_tavern_generator_emits_valid_scenegrid():
    g = emit_scene_grid("baldurs-gate", "tav", name="The Elfsong Tavern")
    assert g.kind == "tavern"
    assert g.grid.cols >= 14 and g.grid.rows >= 10
    assert g.grid.projection == "dimetric-2to1"
    assert g.art.status == "tier1_blockout"
    assert g.art.layout_hash  # a non-empty cache key

    cells = _cells_by_coord(g)

    # Perimeter: the full back wall (row 0) + the left/right interior columns are walls.
    for c in range(g.grid.cols):
        cell = cells.get((c, 0))
        assert cell is not None and cell.type == "wall" and cell.walkable is False, \
            f"back-wall cell ({c},0) must be a non-walkable wall"
    for r in range(1, g.grid.rows - 1):
        for c in (0, g.grid.cols - 1):
            cell = cells.get((c, r))
            assert cell is not None and cell.type == "wall" and cell.walkable is False, \
                f"side-wall cell ({c},{r}) must be a non-walkable wall"

    # Walkable interior: the center floor is walkable (unlisted cells default to floor).
    mid = (g.grid.cols // 2, g.grid.rows // 2)
    assert mid not in cells or cells[mid].walkable  # default floor is walkable
    assert g.cell_default.type == "floor" and g.cell_default.walkable is True

    # Required props present (bar + hearth + two tables + barrels), each an occluder.
    prop_ids = {p.id for p in g.props}
    assert {"bar", "hearth", "table1", "table2", "barrels"}.issubset(prop_ids)
    hearth = next(p for p in g.props if p.id == "hearth")
    assert hearth.occluder and hearth.height_band == "tall"

    # Every prop's footprint cells are non-walkable prop cells tagged with its id.
    for p in g.props:
        for (c, r) in p.cells:
            cell = cells.get((c, r))
            assert cell is not None and cell.type == "prop" and not cell.walkable
            assert cell.prop_ref == p.id

    # Spawns: party (≥1) near the entrance + foes inside.
    assert g.spawns.get("party") and len(g.spawns["party"]) >= 1
    assert g.spawns.get("foes") and len(g.spawns["foes"]) >= 1

    # Warm lighting (the hearth key).
    assert g.lighting.mood and g.lighting.key_color == "#ff9a45"


def test_non_tavern_falls_back_to_valid_default_interior():
    g = emit_scene_grid("baldurs-gate", "crypt1", name="The Crypt", notes="dungeon")
    assert g.kind == "interior"  # no bespoke dungeon generator yet -> generic interior
    assert g.grid.cols > 0 and g.grid.rows > 0
    cells = _cells_by_coord(g)
    # Perimeter walls + a walkable center, even with no props.
    assert cells[(0, 0)].type == "wall"
    assert g.cell_default.walkable is True
    assert g.art.status == "tier1_blockout"


# ── GUARD: ensure_scene_grid is a no-op when one already exists ───────────────────────


def test_ensure_scene_grid_guards_re_entry():
    loc = Location(name="Tavern", notes="tavern")
    assert ensure_scene_grid("w", loc) is True          # first emit attaches
    first = loc.scene_grid
    assert first is not None
    assert ensure_scene_grid("w", loc) is False         # second is a no-op
    assert loc.scene_grid is first                       # untouched (preserves async art)


# ── ISOLATION: the emitter never perturbs the global combat dice stream ───────────────


def test_emitter_does_not_touch_global_dice_stream():
    """Invariant #4 — the engine rolls combat. Emitting a SceneGrid must NOT activate or
    advance the global process RNG (it uses a LOCAL random.Random), so a later un-seeded
    combat roll stays on its pre-existing mechanism."""
    import dice

    before_active = dice._SEED_ACTIVE
    emit_scene_grid("baldurs-gate", "tav", name="A Tavern")
    assert dice._SEED_ACTIVE == before_active  # the emitter never flipped the seed gate


# ── EMIT HOOKS (integration through the real server/content/travel paths) ─────────────


def test_seed_world_emits_scene_grids_for_every_location():
    world = {
        "id": "test-scenegrid-world",
        "name": "SceneGrid Test World",
        "premise": "a world for testing the emitter",
        "regions": [
            {"id": "tavern_loc", "name": "The Roadside Tavern", "tags": ["tavern"]},
            {"id": "field_loc", "name": "An Open Field", "connections": ["tavern_loc"]},
        ],
    }
    c = content.seed_world(world)
    for loc in c.locations.values():
        assert loc.scene_grid is not None, f"{loc.id} should have a scene_grid"
        assert loc.scene_grid.scene_id == f"test-scenegrid-world:{loc.id}"
    # The tavern region was inferred as a tavern kind; the field is the generic interior.
    assert c.locations["tavern_loc"].scene_grid.kind == "tavern"
    assert c.locations["field_loc"].scene_grid.kind == "interior"


def test_add_location_emits_scene_grid():
    cid = server.create_campaign("scenegrid add_location")["id"]
    res = server.add_location(cid, "The Singing Kettle Tavern")
    loc = server._require(cid).locations[res["id"]]
    assert loc.scene_grid is not None
    assert loc.scene_grid.kind == "tavern"
    assert loc.scene_grid.location_id == loc.id


def test_travel_to_emits_scene_grid_on_arrival():
    cid = server.create_campaign("scenegrid travel")["id"]
    # First place becomes current (and gets a grid via add_location's own hook).
    start = server.add_location(cid, "Harbor Start")["id"]
    dest = server.add_location(cid, "The Anchor Tavern", connections=[start])["id"]
    # Clear the destination's grid to prove travel_to's hook (re-)emits on arrival.
    c = server._require(cid)
    c.locations[dest].scene_grid = None
    server.save_campaign(c)
    server.travel_to(cid, dest)
    arrived = server._require(cid).locations[dest]
    assert arrived.scene_grid is not None
    assert arrived.scene_grid.location_id == dest


def test_emit_hooks_do_not_clobber_existing_grid():
    """A re-visit / re-add must NOT re-roll a location that already has a grid (guard)."""
    cid = server.create_campaign("scenegrid guard")["id"]
    start = server.add_location(cid, "Harbor Start")["id"]
    dest = server.add_location(cid, "The Anchor Tavern", connections=[start])["id"]
    c = server._require(cid)
    grid_before = c.locations[dest].scene_grid.model_dump_json()
    # Travel away and back; the existing grid must be preserved verbatim.
    server.travel_to(cid, dest)
    server.travel_to(cid, start)
    server.travel_to(cid, dest)
    grid_after = server._require(cid).locations[dest].scene_grid.model_dump_json()
    assert grid_after == grid_before


# ── SERIALIZATION (omit-when-None) — the store dirty-skip / byte-identity contract ────
# scene_grid defaults to None; an unconditional Pydantic dump would emit "scene_grid":null
# for EVERY grid-less location — a key a pre-A1 on-disk snapshot never carried. That breaks
# byte-identity with old snapshots AND defeats the store's F08-2 dirty-skip (store.py:135-148),
# so a pure cross-campaign inspect (check_*/world_tick/scene_context) would silently rewrite +
# re-stamp the file and could flip the #640 "most-recently-updated == live" pointer. The
# Location wrap serializer OMITS scene_grid when it is None (only that one key).


def test_grid_less_location_omits_scene_grid_key():
    """(a) A grid-less Location dump contains NO scene_grid key (json + dict), while OTHER
    Optional=None fields (e.g. hex) are STILL emitted — i.e. we omit ONLY scene_grid, not
    every None field (no blanket exclude_none)."""
    loc = Location(id="loc_a", name="Bare Room")
    dj = loc.model_dump_json()
    dd = loc.model_dump()
    assert "scene_grid" not in dj
    assert "scene_grid" not in dd
    # The other additive Optional[...]=None field is unaffected (still serialized as null).
    assert '"hex":null' in dj
    assert "hex" in dd and dd["hex"] is None
    # And it still round-trips back to a valid grid-less Location.
    again = Location.model_validate_json(dj)
    assert again.scene_grid is None and again.name == "Bare Room"


def test_grid_ful_location_serializes_scene_grid_and_round_trips():
    """(b) A Location WITH an emitted grid DOES serialize scene_grid (not null, not omitted)
    and is byte-stable across load -> dump."""
    loc = Location(id="loc_b", name="The Yawning Portal", notes="tavern")
    loc.scene_grid = emit_scene_grid("baldurs-gate", "tavern_lower_city",
                                     name="The Yawning Portal Tavern")
    dumped = loc.model_dump_json()
    assert '"scene_grid":' in dumped and '"scene_grid":null' not in dumped
    assert "scene_grid" in loc.model_dump()
    reloaded = Location.model_validate_json(dumped)
    assert isinstance(reloaded.scene_grid, SceneGrid)
    assert reloaded.model_dump_json() == dumped  # byte-identical (save -> load -> save)


def test_pure_inspect_of_pre_a1_snapshot_is_a_no_op(tmp_path, monkeypatch):
    """(c) THE DIRTY-SKIP REGRESSION (the real hazard): a real Campaign with a grid-less
    Location, persisted then STRIPPED of any scene_grid keys on disk to mimic a pre-A1
    snapshot, must survive a pure load -> save (NO mutation) BYTE-IDENTICALLY with NO
    updated_at bump — otherwise an un-migrated campaign gets silently rewritten + can steal
    the #640 live pointer. This is the exact reviewer reproduction; it must now PASS."""
    import json
    import re

    import store

    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))

    # A real Campaign with a grid-less Location, written through the store's save path.
    cid = server.create_campaign("dirty-skip pre-A1")["id"]
    c = server._require(cid)
    c.locations["loc_pre"] = Location(id="loc_pre", name="A Pre-A1 Room")
    assert c.locations["loc_pre"].scene_grid is None
    path = store.save_campaign(c)

    # Strip ANY residual "scene_grid":... fragments from the on-disk JSON to mimic a snapshot
    # written before A1 ever existed (belt-and-suspenders: the omit-serializer already drops
    # grid-less ones, so this only removes a grid-ful key if some location carried one).
    on_disk = path.read_text(encoding="utf-8")
    stripped = re.sub(r',?\s*"scene_grid":\s*(?:null|\{.*?\})', "", on_disk, flags=re.DOTALL)
    # Sanity: the stripped bytes carry NO scene_grid key and still parse as a Campaign snapshot.
    assert "scene_grid" not in stripped
    json.loads(stripped)  # still valid JSON
    path.write_text(stripped, encoding="utf-8")

    before_bytes = path.read_text(encoding="utf-8")
    before_mtime = path.stat().st_mtime_ns
    before_updated_at = json.loads(before_bytes)["updated_at"]

    # The hazard path: a pure load -> (no mutation) -> save. Must be a NO-OP.
    loaded = store.load_campaign(cid)
    assert loaded is not None
    assert loaded.locations["loc_pre"].scene_grid is None
    store.save_campaign(loaded)

    after_bytes = path.read_text(encoding="utf-8")
    after_updated_at = json.loads(after_bytes)["updated_at"]

    assert after_bytes == before_bytes, "pure inspect of a pre-A1 snapshot REWROTE the file"
    assert after_updated_at == before_updated_at, "pure inspect bumped updated_at (#640 risk)"
    assert path.stat().st_mtime_ns == before_mtime, "the snapshot file was rewritten on disk"
