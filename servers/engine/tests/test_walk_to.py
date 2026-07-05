"""W2 (#1319, charter #1337 lane 2): the additive `walk_to` verb + rest-mode pathing +
Character.stage_cell — the ENGINE half of the walkable world.

walk_to is the OUT-OF-COMBAT twin of move_to_coords: it paths a party member across the
current location's scene grid (rest_blocked_cells: walls + prop footprints + where people
stand) via the SHARED combat_grid.shortest_path, writes the mover's Character.stage_cell, and
emits a `rest_walk` Action-Replay beat (verb "walk", the engine path cells) through the session
log for #1303's Animator.

INVARIANTS pinned here:
  * combat gate UNTOUCHED — move_to_coords still hard-raises "no active combat" outside a fight,
    and walk_to refuses while combat IS active (the two lanes never overlap).
  * additive / default-off — no stage_cell == today; old snapshots round-trip; a character with
    no stage_cell serializes BYTE-IDENTICALLY (no `stage_cell` key), and the byte-identity test
    below FAILS if the omit-none wrap serializer is removed (catches the naive Optional=None
    null-emission regression).
  * engine sole-writer — walk_to is the only writer of stage_cell; the viewer only PROJECTS.

Reuses the M-B scene-obstacle fixture idiom (test_combat_scene_obstacles.py).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import server  # imported FIRST: resolves the models<->scene_grid import cycle in the right order
import store
from models import Character
from scene_grid import (
    SceneGrid,
    SceneGridSpec,
    SceneCell,
    SceneCellDefault,
    SceneProp,
)


# ── fixtures ──────────────────────────────────────────────────────────────────────────


def _wall_column_room() -> SceneGrid:
    """A 5x3 rest scene with a solid wall down the middle column x=2 (rows 0..2) EXCEPT a gap
    at (2,0) so the left (x<2) and right (x>2) halves connect only across the top. Cols->x,
    rows->y. Lets us test route-around-wall + reject-into-wall + reachable-via-gap."""
    cells = [SceneCell(c=2, r=r, type="wall", walkable=False) for r in (1, 2)]  # gap at (2,0)
    props = [SceneProp(id="crate", kind="crates", cells=[(4, 2)], anchor_cell=(4, 2))]
    cells.append(SceneCell(c=4, r=2, type="prop", walkable=False, prop_ref="crate"))
    return SceneGrid(
        scene_id="w:loc", location_id="loc", kind="dungeon",
        grid=SceneGridSpec(cols=5, rows=3, cell_size_ft=5),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props,
    )


@pytest.fixture
def room(tmp_path, monkeypatch):
    """A campaign whose CURRENT location carries the deterministic wall-column rest scene, with a
    Hero placed at (0,0) and an Ally placed at (1,0). No combat is started (this is rest mode)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("W2 rest room")["id"]
    loc_id = server.add_location(cid, "The Rest Chamber")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30)["id"]
    ally = server.create_character(cid, "Ally", kind="companion", max_hp=20)["id"]
    # OVERWRITE the procedural scene_grid add_location emitted with the deterministic wall-column
    # room, and place both standers — all in one save so nothing reloads over it.
    c = server._require(cid)
    c.locations[loc_id].scene_grid = _wall_column_room()
    c.characters[hero].stage_cell = (0, 0)
    c.characters[ally].stage_cell = (1, 0)
    server.save_campaign(c)
    return cid, hero, ally, loc_id


# ── rest_blocked_cells: the ONE shared geometry+occupancy builder ─────────────────────


def test_rest_blocked_cells_geometry_from_scene_grid(room):
    cid, hero, _ally, loc_id = room
    c = server._require(cid)
    loc = c.locations[loc_id]
    w, h, blocked = server.rest_blocked_cells(c, loc)
    assert (w, h) == (5, 3)  # geometry from scene_grid.grid.cols/rows (not combat extents)
    # walls (2,1)/(2,2) + prop (4,2) are impassable; both standers block their cells.
    assert (2, 1) in blocked and (2, 2) in blocked and (4, 2) in blocked
    assert (0, 0) in blocked and (1, 0) in blocked  # hero + ally stage cells
    # the gap (2,0) is NOT a wall (walkable)
    assert (2, 0) not in blocked


def test_rest_blocked_cells_excludes_mover(room):
    cid, hero, _ally, loc_id = room
    c = server._require(cid)
    loc = c.locations[loc_id]
    _w, _h, blocked = server.rest_blocked_cells(c, loc, exclude_id=hero)
    assert (0, 0) not in blocked  # the mover never blocks itself
    assert (1, 0) in blocked      # the ally still blocks its cell


def test_rest_blocked_cells_reads_npc_spawns(room):
    cid, _hero, _ally, loc_id = room
    c = server._require(cid)
    loc = c.locations[loc_id]
    # W1 #1318 forward-compat: an npc:<id> spawn anchor is folded into rest occupancy even before
    # that NPC has been walked. Positional party/foes/npcs lists are NOT per-id and are ignored.
    loc.scene_grid.spawns = {"npc:barkeep": [(3, 1)], "npcs": [(1, 1)]}
    server.save_campaign(c)
    _w, _h, blocked = server.rest_blocked_cells(c, loc)
    assert (3, 1) in blocked      # npc:<id> anchor blocks
    assert (1, 1) not in blocked  # a positional "npcs" list is NOT treated as per-id occupancy


def test_rest_blocked_cells_no_scene_grid_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("no grid")["id"]
    server.add_location(cid, "Bare Room")
    c = server._require(cid)
    loc = c.locations[c.current_location_id]
    loc.scene_grid = None  # no painted room → walk unavailable (additive: today's behavior)
    server.save_campaign(c)
    assert server.rest_blocked_cells(c, loc) == (0, 0, set())


# ── walk_to: path legality via the shared shortest_path ───────────────────────────────


def test_walk_to_open_cell_writes_stage_cell(room):
    cid, hero, _ally, _loc = room
    out = server.walk_to(cid, hero, 0, 2)  # straight down the open left column
    assert out["walked"] is True
    assert out["to"] == [0, 2]
    c = server._require(cid)
    assert tuple(c.characters[hero].stage_cell) == (0, 2)
    # the envelope path begins at the start cell (for the Animator glide) and ends at the goal
    assert out["path"][0] == [0, 0]
    assert out["path"][-1] == [0, 2]


def test_walk_to_routes_around_wall_via_gap(room):
    cid, hero, ally, _loc = room
    # Hero at (0,0) -> (4,0) on the right half. The wall column (2,1)/(2,2) blocks the direct
    # low route; the only crossing is the gap at (2,0), so the path must go across the top.
    out = server.walk_to(cid, hero, 4, 0)
    assert out["walked"] is True
    path = [tuple(p) for p in out["path"]]
    assert (2, 1) not in path and (2, 2) not in path  # never steps onto a wall
    assert (2, 0) in path                             # crossed through the gap
    assert path[-1] == (4, 0)


def test_walk_to_rejects_onto_wall(room):
    cid, hero, _ally, _loc = room
    out = server.walk_to(cid, hero, 2, 1)  # a wall cell
    assert out["walked"] is False
    assert "move_blocked" in out
    c = server._require(cid)
    assert tuple(c.characters[hero].stage_cell) == (0, 0)  # stayed put — no write


def test_walk_to_rejects_onto_occupied(room):
    cid, hero, ally, _loc = room
    out = server.walk_to(cid, hero, 1, 0)  # where the ally stands
    assert out["walked"] is False
    assert "move_blocked" in out


def test_walk_to_rejects_out_of_bounds(room):
    cid, hero, _ally, _loc = room
    out = server.walk_to(cid, hero, 9, 9)  # off the 5x3 grid
    assert out["walked"] is False
    assert "move_blocked" in out


def test_walk_to_rejects_unreachable(room):
    cid, hero, ally, loc_id = room
    # Seal the gap at (2,0) with an NPC so the right half is unreachable from the left.
    c = server._require(cid)
    c.locations[loc_id].scene_grid.spawns = {"npc:sentry": [(2, 0)]}
    server.save_campaign(c)
    out = server.walk_to(cid, hero, 4, 0)
    assert out["walked"] is False
    assert "unreachable" in out["move_blocked"]["reason"]


# ── combat-gate PIN: the two lanes never overlap, and move_to_coords is untouched ─────


def test_move_to_coords_still_raises_outside_combat(room):
    """PIN the invariant walk_to must NOT weaken: move_to_coords hard-raises 'no active combat'
    outside a fight. If this ever passes silently, walk_to has un-gated the combat verb."""
    cid, hero, _ally, _loc = room
    with pytest.raises(ValueError, match="no active combat"):
        server.move_to_coords(cid, hero, 0, 2)


def test_walk_to_refuses_during_combat(room):
    cid, hero, ally, _loc = room
    server.start_combat(cid, [hero, ally])
    with pytest.raises(ValueError, match="combat is active"):
        server.walk_to(cid, hero, 0, 2)


# ── rest_walk beat + path through the /events envelope projection (viewer, unit-level) ─


def _viewer_module():
    """Import viewer/server.py as a distinct module (it shares the basename `server` with the
    engine, already imported) so we assert against the REAL envelope projection, not a mirror."""
    if "viewer_server" in sys.modules:
        return sys.modules["viewer_server"]
    viewer_path = Path(__file__).resolve().parents[3] / "viewer" / "server.py"
    spec = importlib.util.spec_from_file_location("viewer_server", viewer_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["viewer_server"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_walk_to_emits_rest_walk_beat_with_path(room):
    cid, hero, _ally, loc_id = room
    server.walk_to(cid, hero, 0, 2)
    c = server._require(cid)
    entries = store.read_log(cid, c.active_session_id)
    beat = next(e for e in entries if e.payload and e.payload.get("event") == "rest_walk")
    assert beat.payload["schema"] == "worldos.combat_event.v1"
    assert beat.payload["actor"] == {"id": hero, "name": "Hero"}
    assert beat.payload["to"] == [0, 2]
    assert beat.payload["location_id"] == loc_id
    path = beat.payload["path"]
    assert path[0] == [0, 0] and path[-1] == [0, 2]

    # The viewer's Action-Replay envelope projects a `walk` verb + a glide hint + the engine path.
    viewer = _viewer_module()
    enriched = viewer._enrich_events_envelope([beat.model_dump()])
    env = enriched[0]
    assert env["verb"] == "walk"
    assert env["anim_hint"] == "glide"
    assert env["result"]["path"] == path  # engine-confirmed cells carried verbatim (no prediction)


# ── byte-identity / additive round-trip (must CATCH a naive Optional=None regression) ──


def test_character_without_stage_cell_omits_the_key():
    """A character who has never walked serializes BYTE-IDENTICALLY to a pre-W2 snapshot: the
    `stage_cell` key is ABSENT (not `null`). This FAILS if the omit-none wrap serializer is
    removed — a bare `Optional[...] = None` would emit `"stage_cell": null`, breaking the store's
    dirty-skip byte-compare exactly as the Location.scene_grid case documents."""
    ch = Character(name="Nobody")
    assert ch.stage_cell is None
    dumped = ch.model_dump()
    assert "stage_cell" not in dumped, "un-walked character must OMIT stage_cell (not emit null)"
    assert "stage_cell" not in json.loads(ch.model_dump_json())


def test_character_with_stage_cell_round_trips():
    ch = Character(name="Walker", stage_cell=(3, 4))
    reloaded = Character.model_validate_json(ch.model_dump_json())
    assert tuple(reloaded.stage_cell) == (3, 4)
    assert "stage_cell" in ch.model_dump()  # a walked character DOES serialize the key


def test_old_snapshot_without_stage_cell_deserializes(room):
    """An on-disk snapshot predating W2 (no stage_cell key on any character) round-trips to
    None — additive, today's behavior exactly."""
    cid, hero, _ally, _loc = room
    c = server._require(cid)
    # Simulate a pre-W2 snapshot: strip stage_cell everywhere, then re-validate.
    raw = json.loads(c.model_dump_json())
    for ch in raw["characters"].values():
        ch.pop("stage_cell", None)
    from models import Campaign
    restored = Campaign.model_validate(raw)
    assert all(ch.stage_cell is None for ch in restored.characters.values())
