---
name: campaign-author
description: Create or import a ClawDnD campaign — generate an original adventure (regions, factions, quest beats, NPC roster with assigned voices) from SRD primitives, or import a campaign module. Use when the player wants to start a new adventure, generate a campaign, or load authored content. Ships only original/CC content; copyrighted adventures are imported privately and never redistributed.
---

# Campaign Author

Create the world the player will adventure in. Two modes:

## Generate
Build an original campaign from SRD primitives: a premise and hook; a region with a few locations; factions with motives; a quest arc with branching beats; an NPC roster (each with a name, personality, and an assigned `voice_id`); and CR-appropriate encounters. Write it as a module under `content/campaigns/<id>/` and seed initial state in `clawdnd-engine`.

### Authoring workflow
1. **Start from a skeleton.** Call `scaffold_adventure(title, premise, level_range)` (in `servers/engine/generator.py`) to get a schema-correct skeleton with the right top-level shape and empty `locations`/`npcs`/`scenes` lists.
2. **Fill it in with original/CC-only content built on SRD primitives** — locations, scenes, encounters, rewards, and an NPC roster. Reproduce no copyrighted adventure text.
3. **Assign every NPC a logical `voice_id`** from the six known voices: `narrator-dm`, `companion-default`, `npc-male-1`, `npc-female-1`, `npc-elder`, `npc-rogue` (see `content/voices/voice-map.json`). Make each scene's `location_id` point at a real location id.
4. **Validate before saving.** Call `validate_adventure(adv)` — it returns a list of problem strings (empty means valid: no duplicate ids, no unknown voice ids, no dangling scene references, title present). Fix every reported problem, then write the module under `content/campaigns/<id>/adventure.json`.

## Import
Load an authored campaign module. **Licensing matters:** ClawDnD ships only original or CC-licensed content. Adventures the player legally owns may be imported for **private** local play — these live under a git-ignored directory and are never committed or redistributed. Never reproduce copyrighted adventure text into the repository.

## Voices
Assign every NPC and the companion a stable logical `voice_id` at creation (see `content/voices/voice-map.json`) so each character sounds consistent across sessions.
