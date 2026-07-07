"""W4 (#1321) The Living Stage — the NO-TELEPORT continuity between rest and combat.

Combat becomes a MODE of the stage, not a separate screen: a fight starts from where everyone
was standing (Character.stage_cell → Combatant.x/y) and, at end_combat, writes the survivors'
final cells back to stage_cell so the party stays where the fight ended. No actor jumps.

INVARIANTS pinned here (mirroring test_walk_to.py's idiom):
  * additive / default-off — seed_from_stage=False (the default) is BYTE-IDENTICAL to today:
    combatants stay unplaced on the grid (x/y None) and end_combat writes NO stage_cell. The
    round-trip byte-identity test below FAILS if the default ever seeds/writes.
  * engine sole-writer — the seed reads stage_cell (set by walk_to); end_combat writes it. Both
    happen in-engine under the campaign lock; the viewer only projects.
  * gates-read-gauges — combat READS stage at entry, WRITES only at exit.
  * grid-mismatch ruling — seeding activates ONLY when the combat grid extents match the
    location's scene_grid; on mismatch it degrades to today's (unplaced) placement.

Reuses the W2 wall-column rest fixture idiom (test_walk_to.py / test_combat_scene_obstacles.py).
"""

from __future__ import annotations

import json

import pytest

import server
from scene_grid import (
    SceneGrid,
    SceneGridSpec,
    SceneCell,
    SceneCellDefault,
    SceneProp,
)


# ── fixtures ──────────────────────────────────────────────────────────────────────────


def _wall_column_room(location_id: str = "loc") -> SceneGrid:
    """A 5x3 scene with a solid wall down column x=2 (rows 1..2) — same geometry as the W2
    walk_to fixture, so the rest board and the derived combat grid are the SAME 5x3 grid."""
    cells = [SceneCell(c=2, r=r, type="wall", walkable=False) for r in (1, 2)]
    props = [SceneProp(id="crate", kind="crates", cells=[(4, 2)], anchor_cell=(4, 2))]
    cells.append(SceneCell(c=4, r=2, type="prop", walkable=False, prop_ref="crate"))
    return SceneGrid(
        scene_id=f"w:{location_id}", location_id=location_id, kind="dungeon",
        grid=SceneGridSpec(cols=5, rows=3, cell_size_ft=5),
        cell_default=SceneCellDefault(type="floor", walkable=True, cost=1),
        cells=cells, props=props,
    )


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """A campaign whose current location carries the 5x3 wall-column scene, a Hero at (0,0) and
    an Ally at (1,0) (their rest positions), plus a Goblin foe with NO stage_cell."""
    monkeypatch.setenv("WORLDOS_STATE_DIR", str(tmp_path))
    cid = server.create_campaign("W4 living stage")["id"]
    loc_id = server.add_location(cid, "The Rest Chamber")["id"]
    hero = server.create_character(cid, "Hero", kind="player", max_hp=30)["id"]
    ally = server.create_character(cid, "Ally", kind="companion", max_hp=20)["id"]
    goblin = server.create_character(cid, "Goblin", kind="monster", max_hp=7)["id"]
    c = server._require(cid)
    c.locations[loc_id].scene_grid = _wall_column_room(loc_id)
    c.characters[hero].stage_cell = (0, 0)
    c.characters[ally].stage_cell = (1, 0)
    # goblin has NO stage_cell — a foe the DM staged, unplaced at rest.
    server.save_campaign(c)
    return cid, hero, ally, goblin, loc_id


# ── (1) NO-TELEPORT entry: combat cells == prior rest stage_cells ─────────────────────


def test_seed_from_stage_opens_combat_on_rest_cells(staged):
    cid, hero, ally, goblin, _loc = staged
    view = server.start_combat(cid, [hero, ally, goblin], seed_from_stage=True)
    c = server._require(cid)

    def cell(char_id):
        cb = next(cb for cb in c.combat.order if cb.character_id == char_id)
        return (cb.x, cb.y)

    # Party members seeded ONTO their exact rest cells — no teleport.
    assert cell(hero) == (0, 0)
    assert cell(ally) == (1, 0)
    # The foe had no stage_cell → stays unplaced (today's behavior; never fails the start).
    assert cell(goblin) == (None, None)
    # The engine surfaces who it seeded so the DM/renderer trusts the in-place opener.
    assert set(view.get("seeded_from_stage", [])) == {hero, ally}


def test_seed_from_stage_matches_stage_cells_exactly(staged):
    """Directly assert the eval gate: EVERY seeded combatant's combat-entry cell equals the
    character's stage_cell as it stood at rest — the no-teleport contract, cell-for-cell."""
    cid, hero, ally, goblin, _loc = staged
    before = {
        h: tuple(server._require(cid).characters[h].stage_cell)
        for h in (hero, ally)
    }
    server.start_combat(cid, [hero, ally, goblin], seed_from_stage=True)
    c = server._require(cid)
    for cb in c.combat.order:
        rest = before.get(cb.character_id)
        if rest is None:
            continue  # the foe, unplaced
        assert (cb.x, cb.y) == rest, f"{cb.character_id} teleported: {(cb.x, cb.y)} != rest {rest}"


# ── (2) NO-TELEPORT exit: post-combat rest cells == combat-end cells ──────────────────


def test_end_combat_writes_survivors_final_cells_back_to_stage(staged):
    cid, hero, ally, goblin, _loc = staged
    server.start_combat(cid, [hero, ally, goblin], seed_from_stage=True)
    # The Hero repositions during the fight (engine-authoritative grid move).
    server.move_to_coords(cid, hero, 0, 2)
    c = server._require(cid)
    hero_end = next(cb for cb in c.combat.order if cb.character_id == hero)
    assert (hero_end.x, hero_end.y) == (0, 2)  # moved on the grid

    server.end_combat(cid, resolution="the goblin fled")
    c = server._require(cid)
    # The party stays where the fight ENDED — stage_cell now carries the combat-end cells.
    assert tuple(c.characters[hero].stage_cell) == (0, 2)   # moved-to cell persisted
    assert tuple(c.characters[ally].stage_cell) == (1, 0)   # never moved → unchanged


def test_end_combat_skips_the_dead(staged):
    """A downed combatant has no rest position — its cell is NOT written back to stage_cell."""
    cid, hero, ally, goblin, _loc = staged
    server.start_combat(cid, [hero, ally, goblin], seed_from_stage=True)
    c = server._require(cid)
    # Kill the ally in place (engine death), leave it on its grid cell.
    c.characters[ally].current_hp = 0
    c.characters[ally].dead = True
    server.save_campaign(c)
    server.end_combat(cid)
    c = server._require(cid)
    # The living hero keeps its rest position; the dead ally's stage_cell is left untouched
    # (still its pre-combat seed — the write-back skipped it).
    assert tuple(c.characters[hero].stage_cell) == (0, 0)
    assert tuple(c.characters[ally].stage_cell) == (1, 0)


def test_full_loop_rest_to_combat_to_rest_continuity(staged):
    """The scripted NO-TELEPORT loop end to end: rest (walk) → combat (seed) → combat move →
    rest (write-back). Assert continuity at BOTH seams with no actor jump."""
    cid, hero, ally, goblin, _loc = staged
    # REST: the Hero walks to a new rest cell (walk_to is the sole writer of stage_cell).
    out = server.walk_to(cid, hero, 4, 0)
    assert out["walked"] is True
    rest_cells = {
        h: tuple(server._require(cid).characters[h].stage_cell) for h in (hero, ally)
    }
    assert rest_cells[hero] == (4, 0)

    # COMBAT ENTRY: cells == the rest cells (no teleport in).
    server.start_combat(cid, [hero, ally, goblin], seed_from_stage=True)
    c = server._require(cid)
    entry = {cb.character_id: (cb.x, cb.y) for cb in c.combat.order}
    assert entry[hero] == rest_cells[hero]
    assert entry[ally] == rest_cells[ally]

    # COMBAT MOVE + EXIT: survivors' rest cells == their combat-end cells (no teleport out).
    server.move_to_coords(cid, ally, 3, 0)
    c = server._require(cid)
    end_cells = {
        cb.character_id: (cb.x, cb.y) for cb in c.combat.order if cb.x is not None
    }
    server.end_combat(cid, resolution="cleared")
    c = server._require(cid)
    for char_id, cell in end_cells.items():
        assert tuple(c.characters[char_id].stage_cell) == cell, (
            f"{char_id} teleported out: rest {c.characters[char_id].stage_cell} != end {cell}"
        )


# ── (3) additive default: seed_from_stage=False is byte-identical to today ─────────────


def test_default_does_not_seed_grid_cells(staged):
    """The DEFAULT (seed_from_stage omitted) leaves combatants unplaced on the grid — today's
    behavior, byte-for-byte. FAILS if the default ever starts seeding from stage_cell."""
    cid, hero, ally, goblin, _loc = staged
    server.start_combat(cid, [hero, ally, goblin])  # no seed_from_stage
    c = server._require(cid)
    for cb in c.combat.order:
        assert (cb.x, cb.y) == (None, None), f"{cb.character_id} was seeded without seed_from_stage"


def test_default_end_combat_writes_no_stage_cell(staged):
    """A default (unseeded) fight leaves x/y None → end_combat writes NO stage_cell. The party's
    rest positions are exactly what they were before the fight (byte-for-byte today's behavior)."""
    cid, hero, ally, goblin, _loc = staged
    before = {
        h: tuple(server._require(cid).characters[h].stage_cell) for h in (hero, ally)
    }
    server.start_combat(cid, [hero, ally, goblin])  # default: no seeding
    server.end_combat(cid, resolution="done")
    c = server._require(cid)
    assert tuple(c.characters[hero].stage_cell) == before[hero]
    assert tuple(c.characters[ally].stage_cell) == before[ally]


def test_default_start_combat_view_omits_seeded_key(staged):
    """The additive `seeded_from_stage` key is ABSENT on a default fight (no key delta)."""
    cid, hero, ally, goblin, _loc = staged
    view = server.start_combat(cid, [hero, ally, goblin])
    assert "seeded_from_stage" not in view


def test_default_snapshot_round_trips_byte_identical(staged):
    """A default fight's snapshot round-trips byte-identically — no stage_cell null-emission and
    no phantom grid seeding leaks into the dump (the omit-none contract holds through combat)."""
    cid, hero, ally, goblin, _loc = staged
    server.start_combat(cid, [hero, ally, goblin])
    c = server._require(cid)
    raw = c.model_dump_json()
    from models import Campaign
    restored = Campaign.model_validate_json(raw)
    assert restored.model_dump_json() == raw
    # the foe never had a stage_cell → the key stays ABSENT (not null) through a combat cycle.
    dumped = json.loads(raw)
    assert "stage_cell" not in dumped["characters"][goblin]


# ── (4) grid-mismatch ruling: seeding degrades gracefully (unplaced) ──────────────────


def test_grid_mismatch_degrades_to_unplaced(staged, monkeypatch):
    """When the derived combat grid extents DON'T match the scene_grid (the epic's grid-mismatch
    ruling), seed_from_stage does NOT seed — it degrades to today's (unplaced) placement rather
    than teleporting onto a mismatched coordinate space."""
    cid, hero, ally, goblin, loc_id = staged

    # Force the derived grid to disagree with the scene_grid by shrinking the combat extents
    # after derivation (simulating a legacy/degraded grid). We patch _derive_grid_from_scene to
    # set mismatched extents so the seed guard trips.
    real_derive = server._derive_grid_from_scene

    def _mismatched(camp):
        real_derive(camp)
        if camp.combat.grid_enabled:
            camp.combat.grid_width = 99  # != scene_grid.cols (5) → mismatch
    monkeypatch.setattr(server, "_derive_grid_from_scene", _mismatched)

    view = server.start_combat(cid, [hero, ally, goblin], seed_from_stage=True)
    c = server._require(cid)
    for cb in c.combat.order:
        assert (cb.x, cb.y) == (None, None), "mismatched grid must NOT seed (would teleport)"
    assert "seeded_from_stage" not in view


def test_out_of_extents_stage_cell_is_skipped(staged):
    """A stage_cell outside the grid extents is skipped (never seeds an off-grid/overlapping
    cell) — the guard protects against a teleport onto an invalid coordinate."""
    cid, hero, ally, goblin, loc_id = staged
    c = server._require(cid)
    c.characters[hero].stage_cell = (10, 10)  # off the 5x3 grid
    server.save_campaign(c)
    server.start_combat(cid, [hero, ally, goblin], seed_from_stage=True)
    c = server._require(cid)
    hero_cb = next(cb for cb in c.combat.order if cb.character_id == hero)
    ally_cb = next(cb for cb in c.combat.order if cb.character_id == ally)
    assert (hero_cb.x, hero_cb.y) == (None, None)  # out-of-extents → unplaced
    assert (ally_cb.x, ally_cb.y) == (1, 0)        # the valid one still seeds
