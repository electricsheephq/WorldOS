"""#1752 / #1751 — the COMBAT-SURFACE SEAM: an active fight must be visible on the rendered stage,
and no combatant may be placed off the board.

This is a CONTRACT test across the engine→viewer seam, not a unit test of either side: it seeds a
real campaign on the REAL v2 walkslice crypt grid (16x12, the room both issues were filed against),
starts combat through the REAL ``server.start_combat``, dumps the engine-owned snapshot, and feeds
that snapshot to the REAL ``viewer/server.py::build_combat_surface``. No mock stands in for either
half — the two bugs both lived exactly in the gap between them:

  * #1752 — ``stage.tokens`` was hardcoded EMPTY in combat mode, so the Unity client (which renders
    the world FROM the stage) kept the last rest frame: 11+ identical frames while the engine ran a
    whole boss fight, no HP bars, no turn marker.
  * #1751 — ``start_combat`` flipped the fight onto the room's grid but placed NOBODY, so the
    surface synthesised a zone layout on a legacy 16x10 board and put an actor at column 16 of a
    16-column crypt — the hero rendered as a ghost inside the east wall.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_QA = str(_REPO / "qa")
if _QA not in sys.path:
    sys.path.insert(0, _QA)

import server  # noqa: E402  (conftest puts servers/engine on the path; import first — models
                # resolves its SceneGrid forward ref at the END of its own module body)
import scene_grid as sg  # noqa: E402  (the engine's own SceneGrid model)
import seed_gfx_walkslice as ws  # noqa: E402  (the canonical painted-room grids)
import store  # noqa: E402

# The room both issues were filed against: a 16-column, 12-row crypt whose LAST cell is (15, 11).
COLS, ROWS = 16, 12
STAGE_CELLS = [(6, 1), (1, 1), (4, 5), (3, 8)]  # PC + 3 goblins, as G4 found them at rest


def _room_16x12(loc_id: str) -> sg.SceneGrid:
    """A painted 16x12 room: solid border walls, an interior pillar, authored spawn buckets."""
    walls = [(c, r) for c in range(COLS) for r in range(ROWS)
             if c in (0, COLS - 1) or r in (0, ROWS - 1)]
    return sg.SceneGrid(
        scene_id=f":{loc_id}",
        location_id=loc_id,
        grid=sg.SceneGridSpec(cols=COLS, rows=ROWS),
        cells=[sg.SceneCell(c=c, r=r, type="wall", walkable=False) for c, r in walls],
        props=[sg.SceneProp(id="pillar", kind="pillar", cells=[(8, 4), (8, 5)],
                            anchor_cell=(8, 4), occluder=True)],
        spawns={"party": [(7, 9), (8, 9)], "foes": [(6, 2), (9, 3)], "npcs": [(2, 9)]},
    )


def _viewer():
    """The REAL viewer surface builder, loaded by path (viewer/server.py is stdlib-only at import)
    — the same module the HTTP handler calls, so this test cannot pass against a mock."""
    spec = importlib.util.spec_from_file_location(
        "viewer_server_stage_contract", _REPO / "viewer" / "server.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_fight(tmp_path, monkeypatch, *, grid_for, stage_cells):
    """A PC + three goblins in a painted room, combat STARTED through the engine — the exact shape
    agent G4 drove in the adventure_demo_v1 sandbox."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("combat-surface-stage")["id"]
    crypt = server.add_location(campaign_id=cid, name="Crypt", make_current=True)["id"]
    pc = server.create_character(cid, "Aidan", kind="player", max_hp=28,
                                 add_to_party=True, location_id=crypt)["id"]
    foes = [m["id"] for m in
            server.spawn_monster(campaign_id=cid, name="Goblin", count=3)["spawned"]]
    c = server._require(cid)
    c.locations[crypt].scene_grid = grid_for(crypt)
    if stage_cells:
        c.characters[pc].stage_cell = stage_cells[0]
    for fid, cell in zip(foes, (stage_cells or [None] * 4)[1:]):
        c.characters[fid].location_id = crypt
        c.characters[fid].stage_cell = cell
    server.save_campaign(c)
    server.start_combat(cid, [pc] + foes)
    return cid, crypt, pc, foes


@pytest.fixture
def crypt_fight(tmp_path, monkeypatch):
    return _seed_fight(tmp_path, monkeypatch, grid_for=_room_16x12, stage_cells=STAGE_CELLS)


@pytest.fixture
def painted_crypt_fight(tmp_path, monkeypatch):
    """The SAME fight on the real v2 walkslice crypt grid — proves the placement rule holds on an
    authored room (props, pilasters, a sarcophagus), not just the synthetic one."""
    return _seed_fight(tmp_path, monkeypatch, grid_for=ws.build_crypt_grid, stage_cells=None)


def _snapshot(cid: str) -> dict:
    return json.loads((store._campaign_dir(cid) / "snapshot.json").read_text())


def _surface(cid: str) -> dict:
    return _viewer().build_combat_surface(
        _snapshot(cid), campaign_id=cid, live=False, is_live_view=False, recent_events=[]
    )


def test_every_combatant_gets_a_distinct_in_bounds_walkable_cell(crypt_fight):
    """#1751 PLACEMENT, asserted where placement is actually DECIDED. ``start_combat`` leaves a
    combatant's tactical cell unset unless it is explicitly seeded, so the cell the player SEES is
    the one the surface derives — and that derivation is what put an actor at column 16 of a
    16-column board. A 16x12 room with 4 combatants: every surfaced cell is in-bounds, walkable,
    and unique."""
    cid, crypt, pc, foes = crypt_fight
    surface = _surface(cid)
    cols, rows = surface["grid"]["cols"], surface["grid"]["rows"]
    assert (cols, rows) == (COLS, ROWS), "the fixture is the 16x12 room both issues cite"
    blocked = {(int(x), int(y)) for x, y in surface["impassable"]}
    cells = [(t["x"], t["y"]) for t in surface["tokens"]]
    assert len(cells) == 4
    for tok in surface["tokens"]:
        cell = (tok["x"], tok["y"])
        assert 0 <= cell[0] < cols, f"{tok['name']} at column {cell[0]} of {cols}"
        assert 0 <= cell[1] < rows, f"{tok['name']} at row {cell[1]} of {rows}"
        assert cell not in blocked, f"{tok['name']} rendered inside a wall/prop at {cell}"
    assert len(set(cells)) == len(cells), f"two combatants share a cell: {cells}"


def test_the_fight_opens_where_everyone_stood(crypt_fight):
    """#1751 NO TELEPORT: with no engine-seeded tactical cells, the surface derives each token's
    cell from the engine's own rest position (``Character.stage_cell``) rather than a synthesized
    zone slot — so the rendered frame does not jump the instant initiative is rolled."""
    cid, crypt, pc, foes = crypt_fight
    surface = _surface(cid)
    board = {t["id"]: (t["x"], t["y"]) for t in surface["tokens"]}
    assert [board[c] for c in [pc] + foes] == STAGE_CELLS
    # ...and the stage the client renders agrees with the board, cell for cell.
    stage = {t["id"]: (t["x"], t["y"]) for t in surface["stage"]["tokens"]}
    assert [stage[c] for c in [pc] + foes] == STAGE_CELLS


def test_stage_lists_every_living_combatant_in_bounds(crypt_fight):
    """#1752 THE SEAM: the surface a live fight produces carries a stage token for EVERY living
    combatant, every one inside the board it declares. An empty stage here is the frozen frame."""
    cid, crypt, pc, foes = crypt_fight
    surface = _surface(cid)
    assert surface["encounter"]["active"] is True
    stage = surface["stage"]
    assert stage["mode"] == "combat"
    living = [cb.character_id for cb in server._require(cid).combat.order]
    assert len(stage["tokens"]) >= len(living), "the fight must not be invisible on the stage"
    by_id = {t["id"]: t for t in stage["tokens"]}
    for combatant in living:
        assert combatant in by_id, f"{combatant} is fighting but absent from the rendered stage"
    cols, rows = surface["grid"]["cols"], surface["grid"]["rows"]
    for tok in stage["tokens"]:
        assert 0 <= tok["x"] < cols, f"{tok['name']} at column {tok['x']} of {cols}"
        assert 0 <= tok["y"] < rows, f"{tok['name']} at row {tok['y']} of {rows}"


def test_no_surface_token_is_off_grid(crypt_fight):
    """#1751 at the surface: the tactical board's own tokens are in-bounds too (this is the exact
    assertion that fails on the reported build, where a goblin surfaced at x=16 of 16)."""
    surface = _surface(crypt_fight[0])
    cols, rows = surface["grid"]["cols"], surface["grid"]["rows"]
    for tok in surface["tokens"]:
        assert 0 <= tok["x"] < cols, f"{tok['name']} at column {tok['x']} of {cols}"
        assert 0 <= tok["y"] < rows, f"{tok['name']} at row {tok['y']} of {rows}"


def test_stage_tokens_carry_the_hud_fields(crypt_fight):
    """#1752 "nothing tells me I'm dying": every stage token carries team + turn + vitals, so an
    HP bar and a turn marker are drawable from the stage alone. Foe HP keeps the combat board's
    disclosure rule (a public health band, no raw numbers until the party earns them)."""
    cid, crypt, pc, foes = crypt_fight
    stage = _surface(cid)["stage"]
    by_id = {t["id"]: t for t in stage["tokens"]}
    hero = by_id[pc]
    assert hero["team"] == "ally"
    assert (hero["hp"], hero["max_hp"]) == (28, 28)
    assert hero["health"] == "steady"
    goblin = by_id[foes[0]]
    assert goblin["team"] == "foe"
    assert goblin["hp_known"] is False and goblin["hp"] is None
    assert goblin["health"] in {"steady", "wounded", "bloodied", "down"}
    turns = [t["id"] for t in stage["tokens"] if t.get("is_turn")]
    assert len(turns) == 1, f"exactly one token holds the turn marker, got {turns}"
    assert turns[0] == server._require(cid).combat.order[0].character_id


def test_stage_projection_is_deterministic_and_read_only(crypt_fight):
    """Re-emitting the same snapshot is byte-identical, and projecting never writes back — the
    engine stays the sole writer of every cell the stage renders."""
    cid = crypt_fight[0]
    snap = _snapshot(cid)
    before = json.dumps(snap, sort_keys=True)
    viewer = _viewer()
    a = viewer.build_combat_surface(snap, campaign_id=cid, live=False, is_live_view=False,
                                    recent_events=[])["stage"]
    b = viewer.build_combat_surface(snap, campaign_id=cid, live=False, is_live_view=False,
                                    recent_events=[])["stage"]
    assert a == b
    assert json.dumps(snap, sort_keys=True) == before


def test_in_bounds_holds_on_the_real_painted_crypt(painted_crypt_fight):
    """The authored v2 walkslice crypt (props, pilasters, a sarcophagus) with NO rest cells at
    all — the pure zone-derived path, the one that produced column 16. Every surfaced cell is
    still inside the board, and no two tokens stack."""
    cid, crypt, pc, foes = painted_crypt_fight
    surface = _surface(cid)
    cols, rows = surface["grid"]["cols"], surface["grid"]["rows"]
    cells = [(t["x"], t["y"]) for t in surface["tokens"]]
    assert all(0 <= x < cols and 0 <= y < rows for x, y in cells), (cells, cols, rows)
    assert len(set(cells)) == len(cells), cells
    assert {(t["x"], t["y"]) for t in surface["stage"]["tokens"]} >= set(cells)
