"""Reciprocal-door arrival contract (#1541, M-ALIGN): crossing a doorway from room A to room B must
land the party at B's door that maps BACK to A — the door you'd walk through to return — not at B's
lowest-sorted door. The defect: ``_seed_stage_cells_on_arrival`` always anchored the party at
``door_cells[0]``, so leaving the tavern (whose crypt-facing door is the crypt's SECOND door) dropped
the hero beside the crypt's CAMP door instead. Owner playtest #8: exiting the tavern placed him at
random in the crypt.

Exercises the real walkslice three-room seed (crypt hub <-> camp + tavern) end-to-end.
"""
from __future__ import annotations

import os
import sys

import pytest

# the gfx seeds live in <repo>/qa (pythonpath is servers/engine only).
_QA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "qa"))
if _QA not in sys.path:
    sys.path.insert(0, _QA)

import combat_grid  # noqa: E402
import server  # noqa: E402  (conftest puts servers/engine on the path)
import seed_gfx_walkslice as ws  # noqa: E402
from scene_grid import SceneGrid, SceneGridSpec, SceneCellDefault  # noqa: E402

CRYPT = "crypt"
CAMP = "camp_clearing_night"
TAVERN = "tavern"
DOOR = tuple(ws.DOOR)                      # crypt's camp-facing door (6,0) — door_cells[0]
TAVERN_DOOR = tuple(ws.TAVERN_DOOR)        # crypt's tavern-facing door (13,4) — door_cells[1]
CAMP_DOOR = tuple(ws.CAMP_DOOR)            # camp's single return door (5,0)
TAVERN_BACK_DOOR = tuple(ws.TAVERN_BACK_DOOR)  # tavern's single return door


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["seed_gfx_walkslice.py", str(tmp_path)])
    ws.main()  # in-process seed of the three-room world into tmp_path
    return ws.CID


def _hero_id(cid: str) -> str:
    c = server._require(cid)
    return next(hid for hid, ch in c.characters.items() if ch.kind == "player")


def test_tavern_round_trip_lands_at_the_reciprocal_door(seeded):
    """crypt -> tavern -> crypt: returning FROM the tavern must land the hero beside the crypt's
    TAVERN door (13,4) — the door back to the tavern — NOT the crypt's camp door (6,0), which is
    door_cells[0] and is where the pre-fix ``door_cells[0]`` anchor wrongly placed him."""
    cid = seeded
    hero = _hero_id(cid)

    # A(crypt) -> B(tavern) via the crypt's tavern door.
    server.cross_door(cid, TAVERN_DOOR[0], TAVERN_DOOR[1])
    c = server._require(cid)
    assert c.current_location_id == TAVERN
    cell = c.characters[hero].stage_cell
    assert cell is not None
    assert combat_grid.chebyshev_cells(tuple(cell), TAVERN_BACK_DOOR) <= 1  # at the tavern's return door

    # B(tavern) -> A(crypt) via the tavern's return door: land at the crypt's TAVERN-facing door.
    server.cross_door(cid, TAVERN_BACK_DOOR[0], TAVERN_BACK_DOOR[1])
    c = server._require(cid)
    assert c.current_location_id == CRYPT
    cell = c.characters[hero].stage_cell
    assert cell is not None
    assert combat_grid.chebyshev_cells(tuple(cell), TAVERN_DOOR) <= 1, (
        f"hero landed at {tuple(cell)} — should be beside the crypt's tavern door {TAVERN_DOOR}, "
        f"not the camp door {DOOR}"
    )


def test_camp_round_trip_lands_at_the_reciprocal_door(seeded):
    """crypt <-> camp coherence: the camp leg (whose reciprocal door IS door_cells[0]) still lands
    the hero beside the camp's return door and back at the crypt's camp door."""
    cid = seeded
    hero = _hero_id(cid)

    server.cross_door(cid, DOOR[0], DOOR[1])  # crypt -> camp
    c = server._require(cid)
    assert c.current_location_id == CAMP
    cell = c.characters[hero].stage_cell
    assert cell is not None
    assert combat_grid.chebyshev_cells(tuple(cell), CAMP_DOOR) <= 1

    server.cross_door(cid, CAMP_DOOR[0], CAMP_DOOR[1])  # camp -> crypt
    c = server._require(cid)
    assert c.current_location_id == CRYPT
    cell = c.characters[hero].stage_cell
    assert cell is not None
    assert combat_grid.chebyshev_cells(tuple(cell), DOOR) <= 1


# ── byte-identical fallback: no door back to the source -> today's door_cells[0] anchor ──────────
def _author_grid(cid, loc_id, door_cells):
    c = server._require(cid)
    c.locations[loc_id].scene_grid = SceneGrid(
        scene_id=f"{cid}:{loc_id}", location_id=loc_id, kind="dungeon", biome="test",
        grid=SceneGridSpec(cols=14, rows=11, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=[], props=[], door_cells=[tuple(d) for d in door_cells],
    )
    server.save_campaign(c)


def test_fallback_to_first_door_is_byte_identical_when_no_reciprocal(tmp_path, monkeypatch):
    """When there is no door that maps back to the source, the arrival anchor falls back to the
    lowest-sorted door — byte-identical to the pre-#1541 behavior (which had no source concept and
    always used ``door_cells[0]``). Proven by seeding the SAME destination with (a) no source and
    (b) a source that has no reciprocal door, and asserting an identical placement."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("fallback")["id"]
    a = server.add_location(campaign_id=cid, name="Room A", make_current=True)["id"]
    dest = server.add_location(campaign_id=cid, name="Dest", connections=[a])["id"]
    _author_grid(cid, dest, [[3, 0], [10, 8]])  # two doors; dest.connections == [a]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30)["id"]
    c = server._require(cid)
    c.characters[hero].location_id = dest  # traveling member already in the destination
    server.save_campaign(c)

    def _seed_with(source_id):
        cc = server._require(cid)
        cc.characters[hero].stage_cell = None  # reset before each seed
        placed = server._seed_stage_cells_on_arrival(cc, cc.locations[dest], source_id=source_id)
        return placed, cc.characters[hero].stage_cell

    baseline = _seed_with(None)                    # pre-fix contract (no source)
    no_reciprocal = _seed_with("not-a-connection")  # a source that maps to no door here
    assert baseline == no_reciprocal               # byte-identical fallback to door_cells[0]
    # And the fallback anchor is the lowest-sorted door (3,0), not (10,8).
    assert combat_grid.chebyshev_cells(tuple(baseline[1]), (3, 0)) <= 1
