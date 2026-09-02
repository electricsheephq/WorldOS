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
from seed_gfx_town import choose_spawns, load_cell_verdicts  # noqa: E402

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


# ── coherence-aware spawn placement (#1647): a cell the player SEES as under painted furniture is
# grid-open yet must never host a spawn. choose_spawns consults a paint_coherence report's per-cell
# verdicts when one is supplied; absent verdicts, behaviour is byte-identical to the geometry path. ──
def _all_verdicts(cols, rows, default="covered", **overrides):
    """A dense verdict map for every interior cell (default `covered`) with per-cell `overrides` — e.g.
    _all_verdicts(9, 9, open_={(2, 2)}) marks (2,2) open and everything else covered."""
    v = {(c, r): default for r in range(1, rows - 1) for c in range(1, cols - 1)}
    for verdict, cells in overrides.items():
        for cell in cells:
            v[cell] = verdict.rstrip("_")   # open_ -> open, ambiguous_ -> ambiguous
    return v


def test_covered_centroid_moves_spawn_to_open_cell():
    """The red-first case: a report marking the geometry-centroid cells COVERED must relocate every
    spawn onto cells the report classifies OPEN (never covered)."""
    cols, rows = 16, 12
    geom = choose_spawns(cols, rows, blocked=set(), door_cells=[])   # geometry-only placement
    covered = {tuple(c) for c in geom["party"] + geom["npcs"]}       # exactly what sits on the centroid
    open_region = {(2, 2), (2, 3), (3, 2), (3, 3), (4, 2)}           # the only OPEN floor in this plate
    verdicts = _all_verdicts(cols, rows, open_=open_region)
    for cell in covered:                                            # ensure the old anchor reads covered
        verdicts[cell] = "covered"
    sp = choose_spawns(cols, rows, blocked=set(), door_cells=[], cell_verdicts=verdicts)
    placed = [tuple(c) for c in sp["party"] + sp["npcs"]]
    assert placed, "coherence-aware placement produced no spawn"
    for cell in placed:
        assert cell not in covered, f"spawn {cell} stayed on a covered centroid cell"
        assert verdicts.get(cell) == "open", f"spawn {cell} is not on an OPEN cell ({verdicts.get(cell)})"


def test_absent_report_is_byte_identical():
    """No report present ⇒ additive no-op: cell_verdicts=None reproduces the geometry-only spawn exactly."""
    for room, geofile, doors in ROOMS:
        geo = json.loads((GEO / geofile).read_text())
        blocked = _blocked(geo, doors)
        base = choose_spawns(geo["cols"], geo["rows"], blocked, doors)
        same = choose_spawns(geo["cols"], geo["rows"], blocked, doors, cell_verdicts=None)
        assert base == same, f"{room}: cell_verdicts=None changed the spawn"


def test_no_open_cells_warns_and_falls_back(capsys):
    """Every candidate covered (a fully mis-locked plate) ⇒ a LOUD warning + a geometry fallback, never
    a crash or an empty spawn."""
    cols, rows = 9, 9
    verdicts = _all_verdicts(cols, rows, default="covered")          # nothing open, nothing ambiguous
    sp = choose_spawns(cols, rows, blocked=set(), door_cells=[(0, 4)], cell_verdicts=verdicts)
    assert sp["party"], "fallback must still place a party rather than crash/return empty"
    assert sp == choose_spawns(cols, rows, blocked=set(), door_cells=[(0, 4)]), \
        "the all-covered fallback must equal the geometry-only placement"
    assert "WARNING" in capsys.readouterr().err.upper(), "an all-covered fallback must warn loudly"


def test_ambiguous_only_fallback_prefers_ambiguous_over_covered(capsys):
    """No OPEN cell but some ambiguous ⇒ warn and place on ambiguous cells, NEVER on covered ones."""
    cols, rows = 9, 9
    ambiguous = {(3, 3), (4, 4), (5, 5)}
    verdicts = _all_verdicts(cols, rows, default="covered", ambiguous_=ambiguous)
    sp = choose_spawns(cols, rows, blocked=set(), door_cells=[(0, 4)], cell_verdicts=verdicts)
    placed = [tuple(c) for c in sp["party"] + sp["npcs"]]
    assert placed and all(c in ambiguous for c in placed), f"spawn not confined to ambiguous cells: {placed}"
    assert "WARNING" in capsys.readouterr().err.upper()


def test_insufficient_open_cells_fill_from_ambiguous(capsys):
    """#1648 review: with fewer OPEN cells than spawn slots, open cells come FIRST but the remaining slots
    are filled from ambiguous/unclassified (never covered) — a mis-locked room must not silently drop a
    party anchor. n_party=2 + n_npc=1 needs 3 cells; only 1 is open here."""
    cols, rows = 9, 9
    open_one = {(4, 4)}
    ambiguous = {(3, 3), (5, 5), (6, 6)}
    verdicts = _all_verdicts(cols, rows, default="covered", open_=open_one, ambiguous_=ambiguous)
    sp = choose_spawns(cols, rows, blocked=set(), door_cells=[(0, 4)], cell_verdicts=verdicts)
    placed = [tuple(c) for c in sp["party"] + sp["npcs"]]
    assert len(placed) == 3, f"all three spawn slots must be seated, got {placed}"
    assert (4, 4) in placed, "the single OPEN cell must be used"
    assert all(verdicts.get(c) in ("open", "ambiguous") for c in placed), \
        f"no spawn may land on a covered cell: {[(c, verdicts.get(c)) for c in placed]}"
    assert "WARNING" in capsys.readouterr().err.upper()


def test_load_cell_verdicts_reads_a_real_report():
    """load_cell_verdicts parses a shipped coherence report into tuple-keyed verdicts; a missing report
    (or None dir) returns None so the seed path stays geometry-only."""
    reports = REPO / "qa" / "evidence" / "paint-coherence"
    crypt = load_cell_verdicts(reports, "crypt")
    assert crypt is not None and crypt.get((7, 7)) == "covered", "crypt (7,7) should read as covered"
    assert load_cell_verdicts(reports, "no_such_room") is None, "missing report ⇒ None"
    assert load_cell_verdicts(None, "crypt") is None, "no dir ⇒ None"
