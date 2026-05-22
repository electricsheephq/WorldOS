---
description: Read a "Previously on…" recap of the story so far, voiced by the narrator.
argument-hint: "[campaign id] (optional; defaults to your most recent campaign)"
---
The player wants a recap of the story so far.

Target campaign (optional id): $ARGUMENTS

Call `clawdnd-engine` `session_recap` for the campaign (the id in the arguments, or the most recent from `list_campaigns`). Read the returned "Previously on your adventure…" text aloud through `clawdnd-voice` using the narrator voice, then briefly note where the party stands right now — location, active quest, and party HP.

Don't advance the story — this is only a recap. When you're done, the player can continue with `/session-start`.
