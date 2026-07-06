"""cross_door — the M-E room-transition gameplay primitive.

server.cross_door(cid, x, y) crosses an authored doorway (a scene_grid.door_cell) to the linked
room-unit (Location.connections), delegating the party move to travel_to. Additive INTERNAL verb.
"""
import pytest

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
