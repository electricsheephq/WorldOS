---
description: Author a new original world seed to adventure in — its history, regions, factions, and a cast.
argument-hint: "[world concept / vibe] (e.g. 'a sunless dwarven empire after the forges died')"
---
The player wants to create a NEW original world seed.

World concept (optional): $ARGUMENTS

Do this:
1. Use the **world-author** skill to design an original world bible at `content/worlds/<id>/world.json`: `premise`, `era` (chronology), `tone`, `history[]`, `standing_threads[]`, `story_seeds[]`, `regions[]` (with `connections`), `factions[]`, an `npc_roster[]`, and `dm_guidance`.
2. Build it on SRD primitives and keep it **original / clean-room** (or, for the owner's private play, a setting they're fine using — drop it under `content/worlds/_private/<id>/` so it stays out of the public tree). Validate it loads with `start_world`.
3. Optionally seed a starter lore corpus (a few `content/worlds/<id>/lore/*.md` pages) so `lookup_lore` has canon from day one — note that more can be ingested later (`tools/ingest/`).
4. When it's ready, offer to play it with `/world-play <id>`.

Aim for the Baldur's-Gate bar: deep history, a larger force stirring beneath a human-scale crisis, and a cast with real wounds.
