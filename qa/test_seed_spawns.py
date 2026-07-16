#!/usr/bin/env python3
"""Unit proof for choose_spawns — the party spawns on open floor, never in a barrel/corner/door.

Epic #1581, issue #1584. The 2026-07-15 bug: build_grid_from_geometry placed the party at the first
free interior cells in row-major order → a back-wall corner or right next to a barrel ("spawn in a
barrel"). choose_spawns places a compact cluster on the open-floor centroid, clear of prop footprints
and door landing rings. Pure + deterministic, so it is unit-testable here (no engine).

Run: python3 -m pytest qa/test_seed_spawns.py -q -p no:xdist
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_gfx_town import choose_spawns  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
GEO = REPO / "qa" / "room_geometries"
ROOMS = [("crypt", "crypt_v36_geometry.json", [(7, 0), (15, 5)]),
         ("tavern", "tavern_v2_geometry.json", [(7, 0)]),
         ("throne", "throne_hall_geometry.json", [(8, 11)])]


def _blocked(geo, door_cells):
    walls = {tuple(c) for c in geo.get("walls", [])} - set(door_cells)
    props = {tuple(c) for p in geo.get("props", []) if p.get("kind") != "wall_run" for c in p["cells"]}
    return walls | props


def test_synthetic_spawn_avoids_a_barrel_cluster():
    """A prop (barrel) sitting on the geometric centre must NOT get a spawn on it."""
    cols, rows = 9, 9
    barrel = {(4, 4), (4, 5)}  # a prop at dead centre
    sp = choose_spawns(cols, rows, blocked=set(barrel), door_cells=[(0, 4)])
    for cell in sp["party"] + sp["npcs"]:
        assert tuple(cell) not in barrel, f"spawned in the barrel: {cell}"
        assert 0 < cell[0] < cols - 1 and 0 < cell[1] < rows - 1  # interior


def test_spawn_not_in_a_corner():
    """The centroid cluster must be central, never jammed at (1,1) like the old first-free rule."""
    sp = choose_spawns(16, 12, blocked=set(), door_cells=[(7, 0)])
    ax, ay = sp["party"][0]
    assert 4 <= ax <= 11 and 3 <= ay <= 8, f"party anchor {sp['party'][0]} is not central"


def test_real_rooms_spawn_on_open_floor():
    for room, geofile, doors in ROOMS:
        geo = json.loads((GEO / geofile).read_text())
        blocked = _blocked(geo, doors)
        sp = choose_spawns(geo["cols"], geo["rows"], blocked, doors)
        assert sp["party"], f"{room}: no party spawn"
        for cell in sp["party"] + sp["npcs"]:
            assert tuple(cell) not in blocked, f"{room}: spawn {cell} on a wall/prop"
            assert tuple(cell) not in set(doors), f"{room}: spawn {cell} on a door cell"


def test_party_members_cluster_together():
    """Party members stand together — the 2 party cells are within 2 Chebyshev cells of each other."""
    sp = choose_spawns(16, 12, blocked=set(), door_cells=[])
    (ax, ay), (bx, by) = sp["party"][0], sp["party"][1]
    assert max(abs(ax - bx), abs(ay - by)) <= 2


def test_spawns_are_distinct():
    sp = choose_spawns(16, 12, blocked=set(), door_cells=[(7, 0)])
    cells = [tuple(c) for c in sp["party"] + sp["npcs"]]
    assert len(cells) == len(set(cells)), "duplicate spawn cells"


def test_avoids_door_landing_ring():
    """No spawn on a cell adjacent to a door (so the party never blocks/overlaps a doorway)."""
    doors = [(7, 0)]
    ring = {(7 + dx, 0 + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    sp = choose_spawns(16, 12, blocked=set(), door_cells=doors)
    for cell in sp["party"] + sp["npcs"]:
        assert tuple(cell) not in ring, f"spawn {cell} in the door landing ring"
