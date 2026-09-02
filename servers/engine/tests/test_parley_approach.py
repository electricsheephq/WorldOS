"""W3 (#1320, Act II roadmap §4b/W3): approach-to-talk + parley stage metadata — the ENGINE
half of "Talk". `generate_parley_options` gains an additive ``approach=True`` param that first
WALKS the lead PC to a cell ADJACENT to the target NPC's rest-mode stage cell (reusing the W2
``walk_to`` machinery — the sole writer of ``stage_cell``), then opens the parley; and the npc
block gains an additive ``stage_cell`` echo so the DM/renderer can open the dialogue AT the actor.

INVARIANTS pinned here:
  * additive / default-off — ``approach`` defaults False; without it the parley payload is
    BYTE-IDENTICAL to today (no ``approach`` key, no ``stage_cell`` echo when the NPC is unstaged).
  * engine sole-writer — approach WRITES stage_cell only via ``walk_to``; the parley projection is
    otherwise a pure read.
  * combat gate untouched — approach in active combat degrades to a freeform parley (walk_to owns
    the refusal); the options still open.
  * MED-addendum fallback — no stage cell / no scene grid / no reachable adjacent cell -> freeform
    parley (no ``approach`` key), never a raise or a blocked dialogue open.
  * adjacency = Chebyshev <=1 (combat_grid.in_melee_reach) — the party stands BESIDE the NPC,
    never onto it; pathing reuses combat_grid.shortest_path (never forked).

Reuses the wall-column rest-scene idiom from test_walk_to.py.
"""

from __future__ import annotations

import pytest

import server  # imported FIRST: resolves the models<->scene_grid import cycle in the right order
import combat_grid  # noqa: F401  (referenced by name in assertions/comments)
from scene_grid import (
    SceneGrid,
    SceneGridSpec,
    SceneCell,
    SceneCellDefault,
    SceneProp,
)


def _wall_column_room() -> SceneGrid:
    """A 5x3 rest scene with a solid wall down column x=2 (rows 1..2) EXCEPT a gap at (2,0), and
    a crate prop at (4,2). Cols->x, rows->y. Same fixture idiom as test_walk_to.py."""
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
def talk_room(tmp_path, monkeypatch):
    """A campaign whose CURRENT location carries the wall-column rest scene: a Hero (lead PC) at
    (0,0) and an Innkeeper NPC staged at (4,0). No combat (rest mode)."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("W3 talk room")["id"]
    loc_id = server.add_location(cid, "The Tavern")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30)["id"]
    npc = server.create_character(cid, "Innkeeper", kind="npc", max_hp=12)["id"]
    server.set_attitude(cid, npc, attitude="indifferent", value=0)
    c = server._require(cid)
    c.locations[loc_id].scene_grid = _wall_column_room()
    c.characters[hero].stage_cell = (0, 0)
    c.characters[npc].stage_cell = (4, 0)
    c.characters[npc].met = True
    server.save_campaign(c)
    return cid, hero, npc, loc_id


# ── _nearest_walkable_adjacent: the Chebyshev<=1 approach-cell picker ─────────────────


def test_nearest_adjacent_prefers_reachable_neighbour_closest_to_start():
    # NPC at (4,0) on the 5x3 grid; neighbours are (3,0),(3,1),(4,1). From (0,0) the closest
    # reachable is (3,0). Adjacency is Chebyshev<=1 by construction.
    dest = server._nearest_walkable_adjacent((4, 0), (0, 0), blocked=set(), width=5, height=3)
    assert dest is not None
    assert combat_grid.in_melee_reach(dest, (4, 0))
    assert dest == (3, 0)


def test_nearest_adjacent_already_adjacent_returns_start():
    # Standing at (3,0), already beside the NPC at (4,0) -> no walk needed.
    dest = server._nearest_walkable_adjacent((4, 0), (3, 0), blocked=set(), width=5, height=3)
    assert dest == (3, 0)


def test_nearest_adjacent_none_when_all_neighbours_blocked():
    # Fence every neighbour of (4,0) -> no legal adjacent cell (fallback trigger).
    blocked = {(3, 0), (3, 1), (4, 1)}
    assert server._nearest_walkable_adjacent((4, 0), (0, 0), blocked, 5, 3) is None


# ── approach-to-talk via generate_parley_options ──────────────────────────────────────


def test_approach_walks_lead_pc_adjacent_and_attaches_block(talk_room):
    cid, hero, npc, _loc = talk_room
    out = server.generate_parley_options(cid, npc_id=npc, approach=True)
    assert out["approach"]["walked"] is True
    dest = tuple(out["approach"]["to"])
    assert combat_grid.in_melee_reach(dest, (4, 0))  # ended up adjacent to the NPC
    assert out["approach"]["npc_cell"] == [4, 0]
    assert out["approach"]["path"][0] == [0, 0]        # glide starts where the hero stood
    assert out["approach"]["path"][-1] == list(dest)
    # engine sole-writer: walk_to actually moved the hero's stage cell.
    c = server._require(cid)
    assert tuple(c.characters[hero].stage_cell) == dest


def test_approach_echoes_npc_stage_cell(talk_room):
    cid, _hero, npc, _loc = talk_room
    out = server.generate_parley_options(cid, npc_id=npc, approach=True)
    assert out["npc"]["stage_cell"] == [4, 0]  # renderer stages the speaker at its cell


def test_approach_already_adjacent_is_a_noop_walk(talk_room):
    cid, hero, npc, _loc = talk_room
    c = server._require(cid)
    c.characters[hero].stage_cell = (3, 0)  # already beside the NPC at (4,0)
    server.save_campaign(c)
    out = server.generate_parley_options(cid, npc_id=npc, approach=True)
    assert out["approach"]["walked"] is False
    assert out["approach"]["to"] == [3, 0]
    assert tuple(server._require(cid).characters[hero].stage_cell) == (3, 0)


def test_approach_unreachable_npc_degrades_to_freeform(talk_room):
    cid, hero, npc, loc_id = talk_room
    # Wall off every neighbour of the NPC by staging blockers there so no adjacent cell is free.
    b1 = server.create_character(cid, "Guard A", kind="npc")["id"]
    b2 = server.create_character(cid, "Guard B", kind="npc")["id"]
    b3 = server.create_character(cid, "Guard C", kind="npc")["id"]
    c = server._require(cid)
    c.characters[b1].stage_cell = (3, 0)
    c.characters[b2].stage_cell = (3, 1)
    c.characters[b3].stage_cell = (4, 1)
    server.save_campaign(c)
    out = server.generate_parley_options(cid, npc_id=npc, approach=True)
    assert "approach" not in out           # degraded to freeform parley
    assert out["npc"]["id"] == npc         # the parley STILL opens (never blocked)
    # the hero never moved (no legal approach was taken)
    assert tuple(server._require(cid).characters[hero].stage_cell) == (0, 0)


def test_approach_unstaged_npc_degrades_to_freeform(talk_room):
    cid, hero, npc, _loc = talk_room
    c = server._require(cid)
    c.characters[npc].stage_cell = None  # NPC not staged anywhere
    server.save_campaign(c)
    out = server.generate_parley_options(cid, npc_id=npc, approach=True)
    assert "approach" not in out
    assert "stage_cell" not in out["npc"]  # no cell to echo
    assert tuple(server._require(cid).characters[hero].stage_cell) == (0, 0)


def test_approach_during_combat_degrades_to_freeform(talk_room):
    cid, hero, npc, _loc = talk_room
    server.start_combat(cid, [hero, npc])  # combat active -> walk_to would refuse
    out = server.generate_parley_options(cid, npc_id=npc, approach=True)
    assert "approach" not in out
    assert out["npc"]["id"] == npc  # options still open


def test_approach_location_mismatch_degrades_to_freeform_never_raises(talk_room):
    # Regression for the "Never raises" contract: walk_to (W2 review fix) rejects a mover
    # anchored at a location_id different from the current one. _approach_to_talk must degrade
    # to freeform BEFORE ever reaching that raise, not propagate a ValueError mid-parley.
    cid, hero, npc, loc_id = talk_room
    c = server._require(cid)
    c.characters[hero].location_id = "some-other-location"  # anchored elsewhere, not loc_id
    server.save_campaign(c)
    out = server.generate_parley_options(cid, npc_id=npc, approach=True)  # must not raise
    assert "approach" not in out           # degraded to freeform parley
    assert out["npc"]["id"] == npc         # the parley STILL opens (never blocked)
    # the hero never moved (walk_to was never reached) and location_id is untouched
    assert tuple(server._require(cid).characters[hero].stage_cell) == (0, 0)
    assert server._require(cid).characters[hero].location_id == "some-other-location"


# ── byte-identity: the default (no-approach) payload is unchanged ─────────────────────


def test_no_approach_payload_is_byte_identical(talk_room):
    # The whole W3 engine change is additive: without approach=True (and no situation), the
    # payload carries NO approach key. With an unstaged NPC, no stage_cell echo either — so the
    # npc block is byte-identical to F10-2/SYN-07's shape. (Guards the round-trip invariant.)
    cid, _hero, npc, _loc = talk_room
    c = server._require(cid)
    c.characters[npc].stage_cell = None
    server.save_campaign(c)
    out = server.generate_parley_options(cid, npc_id=npc, difficulty="medium")
    assert "approach" not in out
    assert set(out["npc"].keys()) == {"id", "name", "attitude", "attitude_value", "met", "difficulty"}


def test_absent_npc_id_still_byte_identical_with_approach_true(talk_room):
    # approach=True with NO npc bound is a no-op: nothing to approach, no npc/approach keys.
    cid, _hero, _npc, _loc = talk_room
    out = server.generate_parley_options(cid, difficulty="medium", approach=True)
    assert "approach" not in out
    assert "npc" not in out
    assert set(out.keys()) == {"actor", "skills", "free_form", "guidance", "alignment"}
