"""CRYPT-ALIGN-V2 arrival-seeding contract (M-ALIGN, 2026-07-15): realigning the crypt sarcophagus to the
painted BACK band (cols 7-11 x rows 3-4) narrows the tavern door (13,4)'s Chebyshev-1 walkable ring to
just (12,4)/(12,5) — the tomb and the pilaster wall take the rest. This pins that a FULL 4-seat party
arriving FROM the tavern still seeds cleanly: ``_seed_stage_cells_on_arrival``'s BFS floods outward from
the reciprocal door and every member lands on a DISTINCT walkable cell that is NOT a prop footprint (never
inside the tomb / pillar / an ornament), so the door landing being tight can never strand or stack the
party. Guards the packet's ⚠ TAVERN-DOOR LANDING check.

Exercises the REAL v2 walkslice crypt grid (``seed_gfx_walkslice.build_crypt_grid`` -> the canonical
``seed_gfx_combat`` grid + the two doors) directly against the engine seeder.
"""
from __future__ import annotations

import os
import sys

import pytest

_QA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "qa"))
if _QA not in sys.path:
    sys.path.insert(0, _QA)

import combat_grid  # noqa: E402
import server  # noqa: E402  (conftest puts servers/engine on the path)
import seed_gfx_combat as combat  # noqa: E402
import seed_gfx_walkslice as ws  # noqa: E402

TAVERN = "tavern"
TAVERN_DOOR = tuple(ws.TAVERN_DOOR)  # the crypt's tavern-facing door (13,4)


@pytest.fixture
def crypt_world(tmp_path, monkeypatch):
    """A campaign whose CURRENT location is the v2 walkslice crypt, connected to the tavern via the
    tavern door (door_cells[1] -> connections[1]), with a 4-member party already co-located here (as
    ``_move_party_to`` leaves them on a ``cross_door`` arrival)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("crypt_arrival")["id"]
    camp_loc = server.add_location(campaign_id=cid, name="Camp")["id"]
    tavern_loc = server.add_location(campaign_id=cid, name="Tavern")["id"]
    # crypt is current; its connections are [camp, tavern] positionally so door_cells[1] (13,4, the tavern
    # door) maps back to the tavern — the reciprocal the arrival seeder anchors on (both real locations, so
    # neither connection is dropped as unresolvable).
    crypt_loc = server.add_location(campaign_id=cid, name="Crypt", make_current=True,
                                    connections=[camp_loc, tavern_loc])["id"]
    party_ids = []
    for i in range(4):
        hid = server.create_character(cid, f"PC{i}", kind="player", max_hp=30,
                                      add_to_party=True, location_id=crypt_loc)["id"]
        party_ids.append(hid)
    # attach the v2 grid + co-locate the party AFTER character creation, then persist ONCE (create_character
    # re-saves the campaign, so an earlier scene_grid write would be clobbered).
    c = server._require(cid)
    c.locations[crypt_loc].scene_grid = ws.build_crypt_grid(crypt_loc)
    for hid in party_ids:
        c.characters[hid].location_id = crypt_loc  # the members _move_party_to co-located here
        c.characters[hid].stage_cell = None
    server.save_campaign(c)
    return cid, crypt_loc, tavern_loc, party_ids


def test_four_seat_party_arriving_from_tavern_seeds_on_clean_walkable_cells(crypt_world):
    cid, crypt_loc, tavern_loc, party_ids = crypt_world
    c = server._require(cid)
    seeded = server._seed_stage_cells_on_arrival(c, c.locations[crypt_loc], source_id=tavern_loc)

    assert len(seeded) == 4, f"all 4 party members must seed; got {len(seeded)}"
    cells = [tuple(c.characters[hid].stage_cell) for hid in party_ids]
    assert all(cell is not None for cell in cells)
    assert len(set(cells)) == 4, f"members must land on DISTINCT cells (no stacking): {cells}"

    # None of them may land on a prop footprint (tomb / pillar / ornament) or a wall.
    prop_cells = {(cc, rr) for p in c.locations[crypt_loc].scene_grid.props for (cc, rr) in p.cells}
    blocked = {(x, y) for (x, y) in (tuple(p) for p in
               __import__("scene_grid").impassable_cells(
                   c.locations[crypt_loc].scene_grid, combat.GRID_W, combat.GRID_H))}
    for cell in cells:
        assert cell not in prop_cells, f"party member seeded ON a prop {cell} (tomb/pillar/ornament)"
        assert cell not in blocked, f"party member seeded on a BLOCKED cell {cell} (wall/prop)"

    # The tomb east end was trimmed to col 11 for exactly this door landing: at least one member must land
    # in the door's Chebyshev-1 walkable ring (the tight landing is usable, not a dead door).
    assert any(combat_grid.chebyshev_cells(cell, TAVERN_DOOR) <= 1 for cell in cells), (
        f"no member landed beside the tavern door {TAVERN_DOOR}; landings={cells}")
