---
description: Begin or resume play — loads your campaign, recaps "Previously on…", and hands the table to the DM.
argument-hint: "[campaign id] (optional; defaults to your most recent campaign)"
---
The player wants to start or resume a ClawDnD session.

Target campaign (optional id): $ARGUMENTS

Do this:
1. Call `clawdnd-engine` `list_campaigns`. If none exist, tell the player to run `/campaign-new` and stop here.
2. Pick the campaign — the id in the arguments, or the most recently played — and call `get_state` to re-ground on the live truth: party, current location, open quests, and whether a combat is mid-flight.
3. If there's prior history, call `session_recap` and read the **"Previously on your adventure…"** aloud via `clawdnd-voice` (narrator voice).
4. Activate the **dungeon-master** skill and resume the turn loop from the current scene. If a combat was in progress, pick it back up at the correct initiative turn rather than restarting it.

Keep the handoff seamless — the player should feel like they sat back down at the same table.
