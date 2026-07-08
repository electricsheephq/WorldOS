#!/usr/bin/env python3
"""seed_gfx_rest_tavern.py — the REST-SCENE demo fixture for #1398 (charter #1386 item 3).

Unlike qa/seed_gfx_tavern.py (a COMBAT seed — start_combat + a hand-authored SceneGrid with no
`npcs` spawn bucket), this seed builds a W1 (#1318) SCENE-AT-REST: party + 1-2 present NPCs at a
tavern, NO combat ever started, so /combat-surface's additive `stage` block naturally reports
mode:"rest" (viewer/server.py::_scene_stage) and paint_combat_v1.cs paints the idle-posed cast
with no damage-VFX pass.

The location is created via server.add_location(name=...containing "tavern"...) so the engine's
OWN scene_grid.py::_gen_tavern generator attaches (ensure_scene_grid, called from add_location) —
this generator already carries the `npcs` at-rest spawn bucket (added by #1318 to all 5
generators), unlike the hand-authored grid in seed_gfx_tavern.py. We do NOT author a scene_grid
by hand here; we let the engine's sole-writer path own it.

  # uv --directory cd's into servers/engine first, so pass the script by ABSOLUTE path:
  WORLDOS_STATE_DIR=<dir> uv run --directory servers/engine python "$PWD/qa/seed_gfx_rest_tavern.py" <state_dir>

Then point the box's _active_campaign.txt at CID and _active_combat.txt at the tavern plate
(tavern_layered_v1.png), and run paint_combat_v1.cs — it will render the REST cast on the warm
tavern plate.
"""
import json
import os
import sys

CID = "camp_gfxrest01"


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: seed_gfx_rest_tavern.py <state_dir>", file=sys.stderr)
        sys.exit(2)
    state_dir = sys.argv[1]
    os.environ["WORLDOS_STATE_DIR"] = state_dir
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servers", "engine"))
    import server  # noqa: PLC0415
    from models import Campaign  # noqa: PLC0415

    server.save_campaign(Campaign(id=CID, title="GFX Rest Scene Demo",
                                  summary="W1 scene-at-rest demo — a party resting at a warm tavern (#1398)."))
    # "tavern" in the name -> scene_grid.py::_infer_kind picks _gen_tavern (npcs bucket included,
    # #1318). make_current=True arrives the party here; add_location's ensure_scene_grid call
    # attaches the grid (engine sole-writer, no hand-authored grid here).
    loc = server.add_location(
        campaign_id=CID, name="The Wooden Tavern (at rest)", make_current=True,
        description="A warm, low-lit tavern common room: a hearth fire, a bar, a couple of "
                     "trestle tables. The party has stopped here to rest for the night.",
    )
    loc_id = loc["id"] if isinstance(loc, dict) and "id" in loc else None

    hero = server.create_character(
        campaign_id=CID, name="Aldric", kind="player",
        race="human", class_name="fighter", level=4,
        abilities={"strength": 18, "dexterity": 14, "constitution": 16,
                   "intelligence": 10, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    mage = server.create_character(
        campaign_id=CID, name="Wizard", kind="player",
        race="elf", class_name="wizard", level=4,
        abilities={"strength": 8, "dexterity": 14, "constitution": 12,
                   "intelligence": 18, "wisdom": 12, "charisma": 10},
        apply_srd_defaults=True, add_to_party=True,
    )
    innkeeper = server.create_character(
        campaign_id=CID, name="Innkeeper", kind="npc", race="human",
        location_id=loc_id, add_to_party=False,
    )
    patron = server.create_character(
        campaign_id=CID, name="Patron", kind="npc", race="human",
        location_id=loc_id, add_to_party=False,
    )
    server.start_session(CID, title="GFX Rest Scene Demo")

    print(json.dumps({
        "campaign_id": CID, "location_id": loc_id,
        "hero_id": hero.get("id"), "mage_id": mage.get("id"),
        "innkeeper_id": innkeeper.get("id"), "patron_id": patron.get("id"),
    }))


if __name__ == "__main__":
    main()
