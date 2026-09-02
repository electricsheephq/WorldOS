"""Coherence-aware ARRIVAL HINTS (#1647 wave-2): a cross_door arrival must PREFER a world-data-baked
hinted cell (a visually-OPEN cell the seed computed from the paint-coherence report) over the naive
nearest-free cell, so the party never lands on a grid-open cell the player SEES as under painted
furniture (owner's "Aldric standing ON the tavern bar" bug).

The engine stays PAINT-BLIND — ``_seed_stage_cells_on_arrival`` reads ``scene_grid.arrival_hints`` and
prefers the first hint that is reachable + free, then falls back to today's nearest-free logic. These
tests drive the seed function directly (as test_arrival_reciprocal_door.py does) and assert:
  * a hint is honored (member lands on the hinted cell, not the nearest-free cell);
  * an occupied/blocked hint SKIPS to the next hint;
  * ABSENT hints are byte-identical to today (placement AND serialization);
  * malformed hints are logged (loud) and ignored — never a crash.
"""
from __future__ import annotations

import json

import pytest

import combat_grid  # noqa: E402  (conftest puts servers/engine on the path)
import server  # noqa: E402
from scene_grid import SceneGrid, SceneGridSpec, SceneCellDefault  # noqa: E402

COLS, ROWS = 10, 10
DOOR = (5, 0)               # door_cells[0]; with source_id=None the arrival anchors here
NEAREST_FREE = (4, 0)       # sorted(reach, key=(chebyshev, r, c))[0] for an all-floor room


def _mk_campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("arrival-hints")["id"]
    dest = server.add_location(campaign_id=cid, name="Dest", make_current=True)["id"]
    return cid, dest


def _author_grid(cid, loc_id, *, arrival_hints=None):
    """An all-floor 10x10 room with a single door at (5,0). ``arrival_hints`` (if given) uses tuple
    cells — the same Cell convention door_cells/spawns use — so it round-trips through the store."""
    c = server._require(cid)
    grid = SceneGrid(
        scene_id=f"{cid}:{loc_id}", location_id=loc_id, kind="dungeon", biome="test",
        grid=SceneGridSpec(cols=COLS, rows=ROWS, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=[], props=[], door_cells=[DOOR],
    )
    if arrival_hints is not None:
        grid.arrival_hints = arrival_hints
    c.locations[loc_id].scene_grid = grid
    server.save_campaign(c)


def _seat(cid, dest, name="Hero", kind="player", cell=None):
    hid = server.create_character(cid, name, kind=kind, max_hp=20)["id"]
    c = server._require(cid)
    c.characters[hid].location_id = dest
    if cell is not None:
        c.characters[hid].stage_cell = cell
    server.save_campaign(c)
    return hid


def _run_seed(cid, dest, source_id=None):
    c = server._require(cid)
    placed = server._seed_stage_cells_on_arrival(c, c.locations[dest], source_id=source_id)
    return placed, c


def test_hint_is_honored_over_nearest_free(tmp_path, monkeypatch):
    """A single traveling member lands on the FIRST hinted cell (5,5) — far from the door — instead
    of the nearest-free cell (4,0) the pre-#1647 logic would pick."""
    cid, dest = _mk_campaign(tmp_path, monkeypatch)
    _author_grid(cid, dest, arrival_hints={f"{DOOR[0]},{DOOR[1]}": [(5, 5), (6, 6)]})
    hero = _seat(cid, dest)

    placed, c = _run_seed(cid, dest)

    assert placed == [hero]
    assert tuple(c.characters[hero].stage_cell) == (5, 5)
    assert tuple(c.characters[hero].stage_cell) != NEAREST_FREE


def test_occupied_hint_skips_to_next(tmp_path, monkeypatch):
    """When the first hint is occupied (a stationary NPC stands on it ⇒ it is `blocked` ⇒ not in
    `reach`), the arriving member skips to the NEXT hint (6,6), not back to the nearest-free cell."""
    cid, dest = _mk_campaign(tmp_path, monkeypatch)
    _author_grid(cid, dest, arrival_hints={f"{DOOR[0]},{DOOR[1]}": [(5, 5), (6, 6)]})
    # A stationary NPC (kind that does NOT travel) sits on the first hint -> occupied/blocked there.
    _seat(cid, dest, name="Barkeep", kind="npc", cell=(5, 5))
    hero = _seat(cid, dest)

    placed, c = _run_seed(cid, dest)

    assert placed == [hero]
    assert tuple(c.characters[hero].stage_cell) == (6, 6)


def test_two_members_consume_hints_in_order(tmp_path, monkeypatch):
    """Two traveling members claim distinct hinted cells in order (hint[0] then hint[1]) — a sequential
    consume, never a stack, mirroring the nearest-free contention discipline."""
    cid, dest = _mk_campaign(tmp_path, monkeypatch)
    _author_grid(cid, dest, arrival_hints={f"{DOOR[0]},{DOOR[1]}": [(5, 5), (6, 6)]})
    a = _seat(cid, dest, name="Aldric", kind="player")
    b = _seat(cid, dest, name="Shadowheart", kind="companion")

    placed, c = _run_seed(cid, dest)

    assert set(placed) == {a, b}
    cells = {tuple(c.characters[a].stage_cell), tuple(c.characters[b].stage_cell)}
    assert cells == {(5, 5), (6, 6)}


def test_absent_hints_is_byte_identical_placement(tmp_path, monkeypatch):
    """No arrival_hints ⇒ the arriving member lands on the nearest-free cell (4,0) — byte-identical to
    the pre-#1647 nearest-free logic. Proven against BOTH an empty-dict grid and a never-set grid."""
    cid, dest = _mk_campaign(tmp_path, monkeypatch)
    _author_grid(cid, dest, arrival_hints={})   # explicit empty
    hero = _seat(cid, dest)
    placed, c = _run_seed(cid, dest)
    assert placed == [hero]
    assert tuple(c.characters[hero].stage_cell) == NEAREST_FREE

    # A grid constructed WITHOUT ever setting arrival_hints places identically.
    cid2, dest2 = _mk_campaign(tmp_path, monkeypatch)
    _author_grid(cid2, dest2, arrival_hints=None)
    hero2 = _seat(cid2, dest2)
    _, c2 = _run_seed(cid2, dest2)
    assert tuple(c2.characters[hero2].stage_cell) == NEAREST_FREE


def test_empty_arrival_hints_omitted_from_dump_present_when_set():
    """Serializer byte-identity: an empty ``arrival_hints`` is OMITTED from the dump (so a hint-less
    grid serializes exactly as a pre-#1647-wave-2 snapshot and the store's no-op-save guard holds);
    a populated one serializes normally."""
    grid = SceneGrid(
        scene_id="c:loc", location_id="loc",
        grid=SceneGridSpec(cols=COLS, rows=ROWS), cell_default=SceneCellDefault(),
        door_cells=[DOOR],
    )
    dump_empty = grid.model_dump(mode="json")
    assert "arrival_hints" not in dump_empty
    assert "arrival_hints" not in json.dumps(dump_empty)

    grid.arrival_hints = {f"{DOOR[0]},{DOOR[1]}": [(5, 5)]}
    dump_set = grid.model_dump(mode="json")
    assert dump_set["arrival_hints"] == {"5,0": [[5, 5]]}


def test_malformed_hints_are_logged_and_ignored_not_crash(tmp_path, monkeypatch, capsys):
    """Malformed hint entries (wrong arity, non-int) are logged LOUDLY to stderr and skipped; the one
    well-formed hint (6,6) is still honored — never a crash."""
    cid, dest = _mk_campaign(tmp_path, monkeypatch)
    _author_grid(cid, dest)          # a valid, hint-less grid persists (round-trips cleanly)
    hero = _seat(cid, dest)
    # Inject malformed hints on the IN-MEMORY grid only (bypasses pydantic) to mimic a corrupted /
    # hand-built grid, then seed WITHOUT a save/reload — the store would reject the invalid shape.
    c = server._require(cid)
    c.locations[dest].scene_grid.arrival_hints = {f"{DOOR[0]},{DOOR[1]}": [[1], "bad", [5, 5, 5], [6, 6]]}
    placed = server._seed_stage_cells_on_arrival(c, c.locations[dest], source_id=None)

    assert placed == [hero]
    assert tuple(c.characters[hero].stage_cell) == (6, 6)  # only the well-formed hint survived
    err = capsys.readouterr().err
    assert "malformed hint" in err or "non-int hint" in err
