"""RED-FIRST regression for W6.2 (#1461) — the owner's walking-over-logs bug.

qa/test_seed_gfx_camp.py pins the COMBAT camp fixture (camp_gfxdemo01): every painted prop cell must
be in the engine's `impassable_cells` set. This file is the REST-MODE sibling: it pins the canonical
rest-camp fixture (qa/seed_gfx_camp_clearing.py, campaign camp_gfxcampnight01) through the viewer's
`build_combat_surface`, asserting every painted-prop cell — the fallen-log seat especially — appears
in the REST-MODE surface `impassable` set.

Before W6.2 existed, `build_combat_surface`'s `impassable` field had no rest-mode branch: with no
active combat, `combat.grid_impassable` is empty, so the surface reported `impassable: []` and the
CombatSurfaceClient had no collision truth at all — actors walked straight over the logs. This file
would fail (`impassable` empty) against that pre-fix surface; it pins the rest-mode branch that
surfaces `rest_blocked_cells()` so the regression can't silently return.

Byte-identity guard: a COMBAT snapshot's `impassable` must stay EXACTLY `combat.grid_impassable`
(the pre-W6.2 expression) — the rest branch may never touch a combat surface.

Run with the engine venv (pydantic + pytest live there):
    uv run --directory servers/engine python -m pytest ../../qa/test_seed_gfx_camp_rest.py -q -p no:xdist
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "qa"))
sys.path.insert(0, str(_ROOT / "servers" / "engine"))

import server  # noqa: E402  imported FIRST: resolves the models<->scene_grid import cycle
import seed_gfx_camp_clearing as seed  # noqa: E402


# Load the stdlib viewer server under a distinct module name (its file is also `server.py`), the same
# way viewer/tests/test_scene_at_rest_stage.py does — so both the engine `server` (for seeding) and
# the viewer `build_combat_surface` (the thing under test) are importable in one process.
_VIEWER_PATH = _ROOT / "viewer" / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_camprest", _VIEWER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
viewer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(viewer)


def _surface(snapshot: dict) -> dict:
    return viewer.build_combat_surface(
        snapshot, campaign_id=seed.CID, live=False, is_live_view=False, recent_events=[]
    )


def _seed_camp(state_dir: Path):
    """Seed the canonical rest-camp fixture into a temp state dir and return (snapshot, grid).

    Reuses seed_gfx_camp_clearing's own authoring code (the CANONICAL fixture) so the test can never
    drift from what ships. Returns the campaign snapshot dict the viewer consumes + the authored
    SceneGrid (its props are the painted set we assert against)."""
    import os

    os.environ["WORLDOS_STATE_DIR"] = str(state_dir)
    server.save_campaign(
        server.Campaign(id=seed.CID, title="GFX Camp Clearing Demo (rest test)")
    )
    server.add_location(
        campaign_id=seed.CID, name="Campfire Clearing (at rest)", make_current=True,
        description="A quiet forest clearing at night: a low campfire, bedrolls, packs against a log.",
    )
    grid = seed._author_camp_grid(server, seed.CID)
    server.create_character(
        campaign_id=seed.CID, name="Aldric", kind="player", race="human",
        class_name="fighter", level=4, apply_srd_defaults=True, add_to_party=True,
    )
    campaign = server._require(seed.CID)
    return campaign.model_dump(mode="json"), grid


def _prop_cells(grid) -> set[tuple[int, int]]:
    cells: set[tuple[int, int]] = set()
    for prop in grid.props:
        for (c, r) in prop.cells:
            cells.add((int(c), int(r)))
    return cells


def test_rest_surface_exposes_a_non_empty_impassable_set(tmp_path):
    """RED-FIRST: with no active combat the rest camp surface must carry the geometry-derived blocked
    set — NOT the empty `[]` the pre-W6.2 surface reported (grid_impassable is empty at rest)."""
    snapshot, _grid = _seed_camp(tmp_path)
    surface = _surface(snapshot)
    assert surface["impassable"], (
        "rest-mode surface must expose a non-empty impassable set from rest_blocked_cells() "
        "(pre-W6.2 this was [] and actors walked over the logs)"
    )


def test_rest_surface_impassable_contains_every_painted_prop(tmp_path):
    """Every painted prop footprint on the camp_clearing_night plate — trees, boulders, campfire,
    bedrolls, the fallen-log seat, supply crates — must be a pathing obstacle on the REST surface."""
    snapshot, grid = _seed_camp(tmp_path)
    imp = {tuple(c) for c in surface_impassable(snapshot)}
    for cell in _prop_cells(grid):
        assert cell in imp, f"painted prop cell {cell} missing from rest-mode impassable (#1461)"


def test_the_logs_are_impassable_at_rest(tmp_path):
    """The felt-bug, pinned explicitly: the fallen-log seat cells (6,6) and (6,7) — the ones the
    owner watched an actor walk straight over — must both be blocked on the rest surface."""
    snapshot, _grid = _seed_camp(tmp_path)
    imp = {tuple(c) for c in surface_impassable(snapshot)}
    assert (6, 6) in imp and (6, 7) in imp, "the log seat (the owner's walking-over-logs cells) must block"


def test_rest_surface_matches_engine_rest_blocked_cells(tmp_path):
    """The surface's rest impassable is EXACTLY the engine's rest_blocked_cells() set — the same
    function walk_to validates against. Byte-agreement between the client's collision picture and the
    engine's mover is the whole point (a divergence IS the walking-over-logs class of bug)."""
    snapshot, _grid = _seed_camp(tmp_path)
    campaign = server._require(seed.CID)
    loc = campaign.locations.get(campaign.current_location_id)
    _w, _h, blocked = server.rest_blocked_cells(campaign, loc)
    imp = {tuple(c) for c in surface_impassable(snapshot)}
    assert imp == {(int(x), int(y)) for (x, y) in blocked}


def test_combat_surface_impassable_is_byte_identical(tmp_path):
    """BYTE-IDENTITY: the rest branch must never touch a combat surface. When combat is active the
    `impassable` field stays EXACTLY `combat.grid_impassable` (the pre-W6.2 expression)."""
    combat_impassable = [[3, 3], [4, 4], [9, 1]]
    snapshot = {
        "current_location_id": "loc1",
        "locations": {"loc1": {"name": "A Room", "scene_grid": {"grid": {"cols": 14, "rows": 11}}}},
        "combat": {
            "active": True, "turn_index": 0, "grid_enabled": True,
            "grid_impassable": combat_impassable,
            "order": [{"character_id": "pc", "name": "Hero", "kind": "player", "initiative": 12}],
        },
    }
    assert _surface(snapshot)["impassable"] == combat_impassable


def surface_impassable(snapshot: dict) -> list:
    return _surface(snapshot)["impassable"]
