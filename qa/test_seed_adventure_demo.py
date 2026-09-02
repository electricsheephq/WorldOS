"""Offline proof for qa/seed_adventure_demo.py — the ONE-CALL Diablo-1 adventure fixture (A-series A0.2).

Runs the seeder into a throwaway state dir (subprocess, exactly as the eval lanes invoke it — which
also proves the campaign_id is the LAST printed line, the contract other harnesses read) and asserts
the seeded world is a complete quest loop: the five certified rooms with scene_grids, the two NPCs +
the boss + the crypt goblins placed on WALKABLE cells, the four-objective quest active and linked to
its giver, and the party starting in the camp.

Run with the engine venv (pydantic + pytest live there):
  uv run --directory servers/engine --group dev python -m pytest ../../qa/test_seed_adventure_demo.py -q -p no:xdist
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_QA = _ROOT / "qa"
_ENGINE = _ROOT / "servers" / "engine"
_SEEDER = _QA / "seed_adventure_demo.py"
_GEO = _QA / "room_geometries"

sys.path.insert(0, str(_QA))
sys.path.insert(0, str(_ENGINE))

import server  # noqa: E402  imported FIRST resolves the models<->scene_grid import cycle
import seed_adventure_demo as sad  # noqa: E402

# room name (as add_location titles it) -> the geometry file it was seeded from
_GEO_OF = {
    "Camp Clearing": "camp_clearing_geometry.json",
    "Tavern Snug": "tavern_snug_geometry.json",
    "Shop": "shop_geometry.json",
    "Crypt": "crypt_v36_geometry.json",
    "Throne Hall": "throne_hall_geometry.json",
}


def _blocked(geo: dict) -> set:
    """The impassable set the seed path (build_grid_from_geometry) actually blocks: walls UNION every
    non-wall_run prop footprint, minus door cells."""
    props = {tuple(c) for p in geo.get("props", []) if p.get("kind") != "wall_run"
             for c in p.get("cells", [])}
    doors = {tuple(d) for d in geo.get("door_cells", [])}
    return ({tuple(c) for c in geo.get("walls", [])} | props) - doors


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    """Run the seeder as a subprocess into a fresh state dir; load the campaign back in-process
    (store.state_dir() reads WORLDOS_STATE_DIR dynamically per call, so pointing the env at the
    same dir loads exactly what the subprocess wrote)."""
    state = tmp_path_factory.mktemp("adventure_state")
    proc = subprocess.run(
        [sys.executable, str(_SEEDER), str(state)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    assert proc.returncode == 0, f"seeder failed:\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr}"
    # the campaign_id contract: the LAST non-empty stdout line is the bare campaign id
    last = [ln for ln in proc.stdout.splitlines() if ln.strip()][-1]
    assert last == sad.CID, f"last stdout line {last!r} != campaign id {sad.CID!r}"

    # evaOS #1632 P3: save/restore the env var — a module-scoped fixture that leaves
    # WORLDOS_STATE_DIR pointing at a throwaway dir contaminates sibling test modules in the
    # same pytest process (flaky, collection-order-dependent).
    prev = os.environ.get("WORLDOS_STATE_DIR")
    os.environ["WORLDOS_STATE_DIR"] = str(state)  # store.state_dir() reads this per call
    c = server._require(sad.CID)
    yield c
    if prev is None:
        os.environ.pop("WORLDOS_STATE_DIR", None)
    else:
        os.environ["WORLDOS_STATE_DIR"] = prev


def _by_kind(c, kind):
    return [ch for ch in c.characters.values() if ch.kind == kind]


def test_campaign_is_a_sandbox_with_five_rooms(seeded):
    assert seeded.is_sandbox is True
    assert len(seeded.locations) == 5
    names = sorted(lc.name for lc in seeded.locations.values())
    assert names == ["Camp Clearing", "Crypt", "Shop", "Tavern Snug", "Throne Hall"]


def test_every_room_has_a_scene_grid(seeded):
    for loc in seeded.locations.values():
        assert loc.scene_grid is not None, f"{loc.name} has no scene_grid"
        assert loc.scene_grid.grid.cols > 0 and loc.scene_grid.grid.rows > 0


def test_party_starts_in_the_camp(seeded):
    camp = next(lc for lc in seeded.locations.values() if lc.name == "Camp Clearing")
    assert seeded.current_location_id == camp.id
    players = _by_kind(seeded, "player")
    assert len(players) == 1
    assert players[0].id in seeded.party  # the PC is in the party (protagonist invariant)


def test_two_npcs_placed_on_walkable_cells(seeded):
    loc = {lc.id: lc for lc in seeded.locations.values()}
    npcs = {n.name: n for n in _by_kind(seeded, "npc")}
    assert set(npcs) == {"Keeper Maera", "Merchant Oswin"}
    assert loc[npcs["Keeper Maera"].location_id].name == "Tavern Snug"
    assert loc[npcs["Merchant Oswin"].location_id].name == "Shop"
    for n in npcs.values():
        assert n.stage_cell is not None, f"{n.name} has no stage_cell"
        geo = json.loads((_GEO / _GEO_OF[loc[n.location_id].name]).read_text())
        assert tuple(n.stage_cell) not in _blocked(geo), f"{n.name} on a blocked cell {n.stage_cell}"


def test_boss_and_goblins_placed_on_walkable_cells(seeded):
    loc = {lc.id: lc for lc in seeded.locations.values()}
    monsters = _by_kind(seeded, "monster")
    boss = [m for m in monsters if m.name == "Goblin Boss"]
    goblins = [m for m in monsters if "Boss" not in m.name]
    assert len(boss) == 1, "expected exactly one Goblin Boss"
    assert len(goblins) == sad.N_GOBLINS, f"expected {sad.N_GOBLINS} goblins, got {len(goblins)}"
    assert loc[boss[0].location_id].name == "Throne Hall"
    assert all(loc[g.location_id].name == "Crypt" for g in goblins)
    for m in boss + goblins:
        assert m.stage_cell is not None, f"{m.name} has no stage_cell"
        geo = json.loads((_GEO / _GEO_OF[loc[m.location_id].name]).read_text())
        assert tuple(m.stage_cell) not in _blocked(geo), f"{m.name} on a blocked cell {m.stage_cell}"
    # goblins occupy distinct cells (no two stacked in a barrel)
    assert len({tuple(g.stage_cell) for g in goblins}) == len(goblins)


def test_quest_active_with_four_objectives_and_giver(seeded):
    loc = {lc.id: lc for lc in seeded.locations.values()}
    quests = list(seeded.quests.values())
    assert len(quests) == 1
    q = quests[0]
    assert q.title == "The Crypt Below"
    assert q.status == "active"
    assert len(q.objectives) == 4
    maera = next(n for n in _by_kind(seeded, "npc") if n.name == "Keeper Maera")
    assert q.giver_id == maera.id, "quest giver is not Keeper Maera"
    assert loc[q.location_id].name == "Crypt", "quest is not anchored to the crypt"
    # the reward is staged on the giver (handed over on 'Return to Maera')
    assert any(i.name == "Ring of Protection" for i in maera.inventory)


def test_camp_manifest_entries_stay_in_sync():
    """evaOS #1632 P3: camp_clearing is a deliberate MIRROR of camp_clearing_night (two campaign
    namespaces key the same hub). A byte-duplicate drifts silently; this lint pins them identical
    (modulo the cross-reference comment keys) so a VFX-anchor or ortho change to one without the
    other fails loud."""
    m = json.loads((_ROOT / "extensions" / "renderers" / "unity" / "plates_manifest.json").read_text())
    a = {k: v for k, v in m["plates"]["camp_clearing"].items() if k != "_sync_comment"}
    b = {k: v for k, v in m["plates"]["camp_clearing_night"].items() if k != "_sync_comment"}
    assert a == b, "camp_clearing and camp_clearing_night drifted — edit BOTH (they mirror one hub)"
