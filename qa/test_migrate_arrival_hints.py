#!/usr/bin/env python3
"""Live-save migration proof for migrate_arrival_hints.py (#1647 wave-2).

On a SYNTHETIC save (never the owner's real state): a location whose room has a paint-coherence report
gets ``scene_grid.arrival_hints`` baked in ADDITIVELY, and a party token standing on a coherence-COVERED
cell is relocated to the nearest visually-OPEN cell — so the "Aldric standing ON the tavern bar" state
ends the instant the migration runs. Also proves --dry-run writes nothing.

Run: python3 -m pytest qa/test_migrate_arrival_hints.py -q -p no:xdist
"""
import os
import sys
from pathlib import Path

import pytest

QA = Path(__file__).resolve().parent
ENGINE = QA.parent / "servers" / "engine"
for p in (str(QA), str(ENGINE)):
    if p not in sys.path:
        sys.path.insert(0, p)

COHERENCE = QA / "evidence" / "paint-coherence"
COVERED_CELL = (7, 1)   # verdict 'covered' in the checked-in tavern_coherence_report.json
COLS, ROWS = 14, 10
DOOR = (0, 5)


def _seed_synthetic(state_dir: str) -> tuple[str, str, str]:
    """A one-room 'tavern' campaign with a hint-less scene_grid and a PC parked on a COVERED cell."""
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    import server  # noqa: PLC0415
    from scene_grid import SceneGrid, SceneGridSpec, SceneCellDefault  # noqa: PLC0415

    cid = server.create_campaign("migrate-test")["id"]
    tavern = server.add_location(campaign_id=cid, name="Tavern", location_id="tavern",
                                 make_current=True)["id"]
    c = server._require(cid)
    c.locations[tavern].scene_grid = SceneGrid(
        scene_id=f"{cid}:tavern", location_id="tavern", kind="town_room", biome="tavern",
        grid=SceneGridSpec(cols=COLS, rows=ROWS, cell_size_ft=5, projection="dimetric-2to1"),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=[], props=[], door_cells=[DOOR],
    )
    server.save_campaign(c)
    hero = server.create_character(cid, "Aldric", kind="player", max_hp=30, add_to_party=True,
                                   location_id=tavern)["id"]
    c = server._require(cid)
    c.characters[hero].stage_cell = COVERED_CELL   # standing ON the bar
    server.save_campaign(c)
    return cid, tavern, hero


def test_dry_run_writes_nothing(tmp_path):
    import server  # noqa: PLC0415
    from migrate_arrival_hints import migrate  # noqa: PLC0415
    from seed_gfx_town import load_cell_verdicts  # noqa: PLC0415

    cid, tavern, hero = _seed_synthetic(str(tmp_path))
    summary = migrate(str(tmp_path), cid, str(COHERENCE), dry_run=True)

    assert summary["total_hint_doors"] >= 1
    assert summary["total_relocations"] == 1   # computed, but NOT persisted

    reloaded = server.load_campaign(cid)
    grid = reloaded.locations[tavern].scene_grid
    assert not (getattr(grid, "arrival_hints", None) or {})     # nothing written
    assert tuple(reloaded.characters[hero].stage_cell) == COVERED_CELL  # token unmoved


def test_migration_bakes_hints_and_relocates_off_covered(tmp_path):
    import server  # noqa: PLC0415
    from migrate_arrival_hints import migrate  # noqa: PLC0415
    from seed_gfx_town import load_cell_verdicts  # noqa: PLC0415

    cid, tavern, hero = _seed_synthetic(str(tmp_path))
    verdicts = load_cell_verdicts(str(COHERENCE), "tavern")
    assert verdicts.get(COVERED_CELL) == "covered"

    summary = migrate(str(tmp_path), cid, str(COHERENCE), dry_run=False)

    # (1) arrival hints baked additively for the door.
    reloaded = server.load_campaign(cid)
    grid = reloaded.locations[tavern].scene_grid
    hints = getattr(grid, "arrival_hints", None) or {}
    assert f"{DOOR[0]},{DOOR[1]}" in hints
    assert hints[f"{DOOR[0]},{DOOR[1]}"], "door should have at least one open hint cell"
    for cell in hints[f"{DOOR[0]},{DOOR[1]}"]:
        assert verdicts.get(tuple(cell)) == "open"

    # (2) the PC was relocated OFF the covered cell to an OPEN cell.
    new_cell = tuple(reloaded.characters[hero].stage_cell)
    assert new_cell != COVERED_CELL
    assert verdicts.get(new_cell) == "open"
    assert summary["total_relocations"] == 1


def test_second_run_is_idempotent_additive(tmp_path):
    """Re-running the migration adds no new door keys (already present) and relocates nothing (the PC
    is already on an open cell) — additive + safe to re-run."""
    import server  # noqa: PLC0415
    from migrate_arrival_hints import migrate  # noqa: PLC0415

    cid, tavern, hero = _seed_synthetic(str(tmp_path))
    migrate(str(tmp_path), cid, str(COHERENCE), dry_run=False)
    summary2 = migrate(str(tmp_path), cid, str(COHERENCE), dry_run=False)

    assert summary2["total_hint_doors"] == 0     # no NEW door keys added
    assert summary2["total_relocations"] == 0    # already on open floor
