#!/usr/bin/env python3
"""seed_gfx_camp_smoke.py — seeds camp_gfxdemo01 IDENTICALLY to seed_gfx_camp.py (imports + reuses
its `_author_camp_grid`, so the grid/props/obstacles stay ONE source), but additionally marks the
campaign `is_sandbox` + `house_rules.force_hit` so qa/player_smoke.sh's scripted attack step is
DETERMINISTIC.

Why: the #1443 smoke test drives a REAL synthetic click on the goblin's cell to trigger the
engine's grid-combat arbiter (viewer/server.py::_resolve_player_combat_turn -> attack Intent), then
asserts the goblin's current_hp dropped. A real attack roll can MISS (a hero attack bonus of +6 vs
a goblin AC 15 has a real miss chance), which would make the smoke flaky. `force_hit` is a
TEST-ONLY, DOUBLE-GUARDED toggle in servers/engine/server.py (`_combat_test_mode_enabled`: requires
BOTH env WORLDOS_COMBAT_TEST=1 AND Campaign.is_sandbox — see servers/engine/server.py) that forces
the HIT BOOLEAN only; damage is still rolled normally (still >=1), so current_hp genuinely drops by
a real amount without faking a crit or the damage number. This keeps seed_gfx_camp.py itself
byte-for-byte unchanged (the T3 LLM-player harness wants an honest, missable fight) while giving the
headless smoke test a deterministic pass/fail.

  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python \
    "$PWD/qa/seed_gfx_camp_smoke.py" <state_dir>

(qa/player_smoke.sh additionally exports WORLDOS_COMBAT_TEST=1 on the VIEWER process — the engine
bridge that actually resolves the attack — not needed here at seed time.)

Engine = SOLE WRITER: writes only via server.* engine calls + save_campaign, exactly like
seed_gfx_camp.py. Additive: a new seed script, touches no existing seed/contract.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_gfx_camp as base  # noqa: E402  (reuse _author_camp_grid — ONE grid source)

CID = base.CID
GRID_W, GRID_H = base.GRID_W, base.GRID_H
HERO_CELL, GOBLIN_CELL = base.HERO_CELL, base.GOBLIN_CELL


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_camp_smoke.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    server.save_campaign(Campaign(
        id=CID, title="GFX Camp Demo (smoke)",
        summary="Deterministic headless smoke fixture (#1443) — sandbox force_hit, no LLM player.",
        is_sandbox=True,
    ))
    server.add_location(campaign_id=CID, name="Campfire Clearing", make_current=True,
                        description="A moonlit forest clearing: a low campfire, bedrolls, a fallen-log "
                                    "seat, supply crates, boulders, and a loose tree-line boundary.")

    # SAME grid/props/obstacles as seed_gfx_camp.py — one source (#1441 P2 scene<->grid coherence).
    grid = base._author_camp_grid(server, CID)  # noqa: SLF001 (deliberate reuse; see module docstring)

    server.start_session(CID, title="GFX Camp Demo (smoke)")
    hero = server.create_character(
        campaign_id=CID, name="Aldric", kind="player", race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    hero_id = hero["id"]
    gob = server.spawn_monster(CID, name="Goblin", count=1)
    goblin_id = gob["spawned"][0]["id"]

    import scene_grid as sg  # noqa: PLC0415
    impassable = sg.impassable_cells(grid, GRID_W, GRID_H)
    server.start_combat(CID, [hero_id, goblin_id], surpriser_ids=[hero_id])
    server.set_grid(CID, width=GRID_W, height=GRID_H, obstacles=impassable)
    server.place_combatant_at_coords(CID, hero_id, HERO_CELL[0], HERO_CELL[1])
    server.place_combatant_at_coords(CID, goblin_id, GOBLIN_CELL[0], GOBLIN_CELL[1])

    # TEST-ONLY, double-guarded (see servers/engine/server.py::_combat_test_mode_enabled): a
    # deterministic HIT so the scripted smoke attack always drops the goblin's HP. Damage is
    # still rolled normally (>=1) — only the hit/miss coin flip is forced.
    c = server._require(CID)  # noqa: SLF001
    c.house_rules.force_hit = True
    server.save_campaign(c)

    print(json.dumps({
        "campaign_id": CID, "hero_id": hero_id, "goblin_id": goblin_id,
        "grid": f"{GRID_W}x{GRID_H}", "impassable": impassable,
        "hero_cell": HERO_CELL, "goblin_cell": GOBLIN_CELL,
        "sandbox": True, "force_hit": True,
    }))


if __name__ == "__main__":
    main()
