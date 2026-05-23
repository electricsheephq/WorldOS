---
description: Drop into a living world and adventure — the DM generates the story live within its canon.
argument-hint: "[world id] (e.g. sundered-reach) — optional; lists worlds if omitted"
---
The player wants to play in a living world (the generative / sandbox mode — the engine at its best).

Target world (optional id): $ARGUMENTS

Do this:
1. If no world id was given, call `clawdnd-engine` `list_worlds` and let the player pick one.
2. Call `start_world(world_id)`. If it returns `existing_campaigns`, ask whether to **continue** one — `start_world(world_id, resume=<campaign_id>)` — or start fresh, so you never orphan a living world.
3. Read the returned bible: premise, **era** (respect the chronology — no raising the long-dead), tone, standing threads, story seeds, dm_guidance, and the seeded regions / factions / NPC roster.
4. Quick **character creation** (`generate_ability_scores` + `create_character`), and bring in a **companion** — a roster legend or an original — with a real wound and a distinct voice.
5. Activate the **dungeon-master** skill and run its **"Generating a world live"** mode: drop the party at a starting option; generate each scene on arrival and PERSIST it (`add_location`, `create_character`, `remember`); pull canon on demand with `lookup_lore`; let the standing threads move on their own (weave in the `world_beats` from `travel_to`/`downtime`/`world_tick`); hold the story-craft bar (felt menace, a wounded companion, the unforgettable beat).
6. `start_session` (for the recap + continuity) and `end_session` when the player stops.

This is ClawDnD at its best — an epic, living world you inhabit, different every time. Make it sing.
