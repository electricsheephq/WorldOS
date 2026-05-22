---
name: campaign-author
description: Create or import a ClawDnD campaign — generate an original adventure (regions, factions, quest beats, NPC roster with assigned voices) from SRD primitives, or import a campaign module. Use when the player wants to start a new adventure, generate a campaign, or load authored content. Ships only original/CC content; copyrighted adventures are imported privately and never redistributed.
---

# Campaign Author

Create the world the player will adventure in. Two modes:

## Generate
Build an original campaign from SRD primitives: a premise and hook; a region with a few locations; factions with motives; a quest arc with branching beats; an NPC roster (each with a name, personality, and an assigned `voice_id`); and CR-appropriate encounters. Write it as a module under `content/campaigns/<id>/` and seed initial state in `clawdnd-engine`.

## Import
Load an authored campaign module. **Licensing matters:** ClawDnD ships only original or CC-licensed content. Adventures the player legally owns may be imported for **private** local play — these live under a git-ignored directory and are never committed or redistributed. Never reproduce copyrighted adventure text into the repository.

## Voices
Assign every NPC and the companion a stable logical `voice_id` at creation (see `content/voices/voice-map.json`) so each character sounds consistent across sessions.
