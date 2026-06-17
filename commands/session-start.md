---
description: Begin or resume play — loads your campaign, recaps "Previously on…", and hands the table to the DM.
argument-hint: "[campaign id] (optional; defaults to your most recent campaign)"
---
The player wants to start or resume a WorldOS session.

Target campaign (optional id): $ARGUMENTS

Do this:
1. Call `worldos-engine` `list_campaigns`. If none exist, tell the player to run `/campaign-new` and stop here.
2. Pick the campaign — the id in the arguments, or the most recently played — and call `get_state` to re-ground on the live truth: party, current location, open quests, and whether a combat is mid-flight.
3. Call `start_session` to begin this play session. It returns `previously_on` — a recap of the **previous** session. Read it aloud as **"Previously on your adventure…"** via `worldos-voice` (narrator voice). (On a brand-new campaign it returns the new-adventure message instead — just open the story.)
4. Activate the **dungeon-master** skill and resume the turn loop from the current scene. If a combat was in progress, pick it back up at the correct initiative turn rather than restarting it.
5. When the player stops for the night, call `end_session` (with a one-line summary) so the next `/session-start` recaps this session.

Keep the handoff seamless — the player should feel like they sat back down at the same table.
