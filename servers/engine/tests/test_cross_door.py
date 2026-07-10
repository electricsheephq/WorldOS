"""cross_door — the M-E room-transition gameplay primitive.

server.cross_door(cid, x, y) crosses an authored doorway (a scene_grid.door_cell) to the linked
room-unit (Location.connections), delegating the party move to travel_to. Additive INTERNAL verb.
"""
import pytest

import combat_grid
import server  # noqa: PLC0415  (conftest puts servers/engine on the path; import-first per the
from scene_grid import SceneGrid, SceneGridSpec, SceneCellDefault  # models<->scene_grid circular note)


def _author_grid(cid, loc_id, door_cells):
    c = server._require(cid)
    c.locations[loc_id].scene_grid = SceneGrid(
        scene_id=f"{cid}:{loc_id}", location_id=loc_id, kind="dungeon", biome="test crypt",
        grid=SceneGridSpec(cols=14, rows=11, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=[], props=[], door_cells=[tuple(d) for d in door_cells],
    )
    server.save_campaign(c)


@pytest.fixture
def two_room(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("cross-door")["id"]
    a = server.add_location(campaign_id=cid, name="Room A", make_current=True)["id"]
    b = server.add_location(campaign_id=cid, name="Room B", connections=[a])["id"]
    _author_grid(cid, a, [[6, 0]])
    return cid, a, b


def test_cross_door_travels_to_the_connected_room(two_room):
    cid, a, b = two_room
    assert server._require(cid).current_location_id == a
    res = server.cross_door(cid, 6, 0)
    assert server._require(cid).current_location_id == b  # crossed into the linked unit
    assert res["crossed_door"] == [6, 0]
    assert res["multi_connection"] is False


def test_cross_door_restages_party_near_the_destination_door(two_room):
    # #1378: when the linked room is ALSO painted, the party must arrive re-staged onto walkable
    # cells beside that room's door — not the empty door-bar board (their stage_cell is cleared on
    # every travel by _move_party_to). Give room B its own grid + entry door and cross into it.
    cid, a, b = two_room
    _author_grid(cid, b, [[3, 0]])
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30)["id"]
    ally = server.create_character(cid, "Ally", kind="companion", max_hp=20)["id"]
    c = server._require(cid)
    for pid, cell in ((hero, (5, 5)), (ally, (6, 5))):  # standing at rest in room A
        c.characters[pid].location_id = a
        c.characters[pid].stage_cell = cell
    server.save_campaign(c)

    server.cross_door(cid, 6, 0)  # walk through A's door → arrive in room B

    c = server._require(cid)
    assert c.current_location_id == b
    hero_cell = c.characters[hero].stage_cell
    ally_cell = c.characters[ally].stage_cell
    assert hero_cell is not None and ally_cell is not None  # re-staged (was None: the bug)
    assert hero_cell != ally_cell  # no stacking — deterministic spread
    door = (3, 0)
    for pid, cell in ((hero, hero_cell), (ally, ally_cell)):
        w, h, blocked = server.rest_blocked_cells(c, c.locations[b], exclude_id=pid)
        assert 0 <= cell[0] < w and 0 <= cell[1] < h  # on the destination grid
        assert cell not in blocked  # walkable, not a wall/prop and not on a party-mate
        assert combat_grid.chebyshev_cells(cell, door) <= 2  # beside the arrival door


def test_cross_door_into_ungridded_room_writes_no_stage_cells(two_room):
    # ADDITIVE guard: the destination (room B) has NO scene_grid → seeding is a no-op, exactly as
    # before #1378 (stage_cell stays None after the travel clear). Byte-identical legacy behavior.
    cid, a, b = two_room
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30)["id"]
    c = server._require(cid)
    c.characters[hero].location_id = a
    c.characters[hero].stage_cell = (5, 5)
    server.save_campaign(c)

    server.cross_door(cid, 6, 0)

    c = server._require(cid)
    assert c.current_location_id == b
    assert c.characters[hero].stage_cell is None


def test_cross_door_rejects_a_non_doorway_cell(two_room):
    cid, a, b = two_room
    with pytest.raises(ValueError):
        server.cross_door(cid, 3, 3)
    assert server._require(cid).current_location_id == a  # did not move


def test_cross_door_raises_when_no_connected_room(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("isolated")["id"]
    a = server.add_location(campaign_id=cid, name="Lonely", make_current=True)["id"]
    _author_grid(cid, a, [[6, 0]])  # a door, but no connection
    with pytest.raises(ValueError):
        server.cross_door(cid, 6, 0)


def test_cross_door_flags_multi_connection_and_takes_first(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("hub")["id"]
    a = server.add_location(campaign_id=cid, name="Hub", make_current=True)["id"]
    b = server.add_location(campaign_id=cid, name="North", connections=[a])["id"]
    server.add_location(campaign_id=cid, name="East", connections=[a])
    _author_grid(cid, a, [[6, 0]])
    res = server.cross_door(cid, 6, 0)
    assert res["multi_connection"] is True
    assert server._require(cid).current_location_id == b  # best-effort: the first connection


def test_cross_door_resolves_each_door_of_a_multi_door_hub_to_its_own_room(tmp_path, monkeypatch):
    """SHIP-MORNING regression (#1508/#1531): a hub with TWO authored doors (mirroring the walkslice
    crypt's camp-door + tavern-door) must send the party through the door-cell-SPECIFIC destination —
    door_cells[i] -> connections[i] — not always connections[0]. Verified live on the box before this
    fix: crossing the walkslice tavern's (0,5) door landed in camp_clearing_night, never tavern, because
    cross_door ignored WHICH door cell was crossed. Reproduces that shape with a minimal 2-door hub."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("multi-door-hub")["id"]
    hub = server.add_location(campaign_id=cid, name="Hub", make_current=True)["id"]
    north = server.add_location(campaign_id=cid, name="North", connections=[hub])["id"]
    east = server.add_location(campaign_id=cid, name="East", connections=[hub])["id"]
    # add_location bidirectionally wires connections, so hub.connections is now [north, east] in
    # creation order — author door_cells in the SAME order (door_cells[0]->north, door_cells[1]->east).
    _author_grid(cid, hub, [[6, 0], [0, 5]])

    res_north = server.cross_door(cid, 6, 0)
    assert res_north["multi_connection"] is True
    assert server._require(cid).current_location_id == north

    # travel back to the hub via the same primitive cross_door delegates to, then cross the OTHER door.
    server.travel_to(cid, hub)
    res_east = server.cross_door(cid, 0, 5)
    assert res_east["multi_connection"] is True
    assert server._require(cid).current_location_id == east  # NOT north — each door its own room
