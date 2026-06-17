---
description: Start a brand-new WorldOS campaign — create your character, meet your AI companion, and begin an adventure.
argument-hint: "[campaign name or theme] (optional; defaults to the \"Cellar Rats\" starter)"
---
The player wants to start a NEW WorldOS campaign.

Arguments (optional campaign name / theme): $ARGUMENTS

Do this:
1. Use the **campaign-author** skill to set up the campaign. If the player named a theme, generate an original SRD-based adventure for it; otherwise seed the bundled **"The Cellar Rats"** starter (`content/campaigns/cellar-rats`).
2. Call `clawdnd-engine` `start_adventure(adventure_id)` — it CREATES and seeds the campaign (locations / NPCs / the companion) in one call and returns its `campaign_id`. Do NOT call `create_campaign` first: `start_adventure` mints its own campaign, so calling both leaves an empty orphan campaign separate from the seeded one. Use the returned `campaign_id` for every following call.
3. Walk the player through **character creation** — use the engine's `generate_ability_scores` and `create_character`, then ability/skill choices. Keep it quick and friendly, and offer a pregenerated character for players who just want to dive in.
4. Confirm the **AI companion** has joined the party (its own sheet + `voice_id`). Introduce it in-character through the **companion** skill, voiced via `clawdnd-voice`.
5. Hand off to the **dungeon-master** skill to narrate the opening scene and begin the turn loop.

Persist everything through the engine as you go — the campaign must survive a quit/reload. When setup is done, remind the player they can resume any time with `/session-start`.
