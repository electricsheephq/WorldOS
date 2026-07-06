"""Pre-greybox PATHING VALIDATOR (gfx occlusion/pathing Sprint 2).

Pins ``scene_grid.validate_scene_grid`` + ``door_zone_cells``: the authored-room gate that runs BEFORE any
art is generated (Diablo's topology-then-dressing), so a generated room can never place a prop in a door
zone / protected lane, wall off a pocket of floor, or be too crunched for actors. Engine-only; additive.

See docs/roadmap/ROOM-OCCLUSION-PATHING-SPRINTS.md.
"""

from __future__ import annotations

import server  # noqa: F401  (import first to resolve the models<->scene_grid cycle)
from models import SceneGrid
from scene_grid import (
    SceneCell,
    SceneGridSpec,
    SceneProp,
    door_zone_cells,
    emit_scene_grid,
    validate_scene_grid,
)


def _perimeter(cols: int, rows: int) -> list[SceneCell]:
    out: list[SceneCell] = []
    for c in range(cols):
        out.append(SceneCell(c=c, r=0, type="wall", walkable=False))
        out.append(SceneCell(c=c, r=rows - 1, type="wall", walkable=False))
    for r in range(1, rows - 1):
        out.append(SceneCell(c=0, r=r, type="wall", walkable=False))
        out.append(SceneCell(c=cols - 1, r=r, type="wall", walkable=False))
    return out


def _grid(cols, rows, cells, props=None, **kw) -> SceneGrid:
    return SceneGrid(scene_id="t", location_id="t",
                     grid=SceneGridSpec(cols=cols, rows=rows),
                     cells=cells, props=props or [], **kw)


def test_open_room_is_valid():
    g = _grid(14, 11, _perimeter(14, 11))
    assert validate_scene_grid(g, 14, 11) == []


def test_generated_rooms_pass():
    # every shipping generator must produce a room that passes the gate.
    for loc in ("tavern_lower_city", "crypt_one", "forest_glade", "town_square"):
        g = emit_scene_grid("baldurs-gate", loc, name=loc)
        assert validate_scene_grid(g, g.grid.cols, g.grid.rows) == [], f"{loc} failed: {validate_scene_grid(g, g.grid.cols, g.grid.rows)}"


def test_prop_walling_off_a_pocket_is_caught():
    # an 8x8 room (30 clear cells, so NOT crunched) where a full-height barrier at col 3 seals the
    # interior into two disconnected regions -> a connectivity violation (the prop walls off a pocket).
    bar = [(3, r) for r in range(1, 7)]
    cells = _perimeter(8, 8) + [SceneCell(c=c, r=r, type="prop", walkable=False, prop_ref="b") for (c, r) in bar]
    g = _grid(8, 8, cells, props=[SceneProp(id="b", kind="barrier", cells=bar)])
    issues = validate_scene_grid(g, 8, 8)
    assert any("DISCONNECTED" in v for v in issues), issues


def test_prop_in_door_zone_is_caught():
    cells = _perimeter(10, 9) + [SceneCell(c=4, r=1, type="prop", walkable=False, prop_ref="p")]
    g = _grid(10, 9, cells,
              props=[SceneProp(id="p", kind="table", cells=[(4, 1)])],
              door_cells=[(4, 0)])  # (4,0) door -> zone includes (4,1)
    issues = validate_scene_grid(g, 10, 9)
    assert any("DOOR ZONE" in v for v in issues), issues


def test_prop_in_protected_lane_is_caught():
    cells = _perimeter(10, 9) + [SceneCell(c=5, r=4, type="prop", walkable=False, prop_ref="p")]
    g = _grid(10, 9, cells,
              props=[SceneProp(id="p", kind="barrel", cells=[(5, 4)])],
              protected_lane_cells=[(5, 4), (5, 5)])
    issues = validate_scene_grid(g, 10, 9)
    assert any("PROTECTED LANE" in v for v in issues), issues


def test_door_zone_is_door_plus_chebyshev_ring():
    g = _grid(6, 6, _perimeter(6, 6), door_cells=[(3, 0)])
    zone = door_zone_cells(g, 6, 6)
    # (3,0) + its 8 neighbours clipped to bounds (the r=-1 row drops out).
    assert (3, 0) in zone and (2, 1) in zone and (4, 1) in zone and (3, 1) in zone
    assert (3, -1) not in zone  # clipped


def test_too_crunched_room_is_caught():
    # a tiny 4x4 has only 2x2=4 interior cells -> below the 12-cell clear floor minimum.
    g = _grid(4, 4, _perimeter(4, 4))
    issues = validate_scene_grid(g, 4, 4)
    assert any("crunched" in v for v in issues), issues


def test_npcs_spawn_bucket_on_a_blocked_cell_is_caught():
    # W1 (#1318): the new `npcs` at-rest spawn bucket must pass the SAME blocked-cell gate as
    # party/foe spawns — validate's generic spawns.values() loop covers it (pin it so a future
    # bucket that forks the loop can't slip an npc anchor onto a wall/prop).
    cells = _perimeter(14, 11) + [SceneCell(c=5, r=5, type="prop", walkable=False, prop_ref="p")]
    g = _grid(14, 11, cells,
              props=[SceneProp(id="p", kind="table", cells=[(5, 5)])],
              spawns={"npcs": [(5, 5)]})  # an npc anchor placed ON the prop
    issues = validate_scene_grid(g, 14, 11)
    assert any("spawn cell [5, 5] is BLOCKED" in v for v in issues), issues
