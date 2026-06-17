---
description: Save your campaign now — a durable checkpoint you can quit and reload from.
---
The player wants to save.

WorldOS writes campaign state to disk after every beat, so progress is rarely lost. To honor an explicit save:
1. Make sure any pending changes from the current beat are flushed through `worldos-engine` — HP, conditions, inventory, XP, quest and NPC facts, and position.
2. Call `get_state` to confirm the persisted snapshot is current and complete.
3. Tell the player it's safe to quit, and that `/session-start` will pick up exactly here with a recap.

Don't narrate or advance the story — this is a checkpoint, not a beat.
