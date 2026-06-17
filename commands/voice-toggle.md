---
description: Toggle spoken voice on or off for this session (text-only fallback when off).
argument-hint: "[on|off] (optional; toggles if omitted)"
---
The player wants to change voice output: $ARGUMENTS

WorldOS's voice backend is chosen when the MCP servers launch (`WORLDOS_TTS_BACKEND` — `kokoro` for spoken audio, `null` for silent/text-only), so you can't relaunch it mid-session. Honor this as a **session preference**:

- **off / silent:** stop calling `clawdnd-voice` `speak`. Keep narrating and voicing characters in **text only**, still labeling each speaker so the player knows who's talking. Confirm voice is off.
- **on / voiced:** resume calling `clawdnd-voice` `speak(text, voice_id)` for narration, NPCs, and the companion in their own voices. If the active backend is `null`, tell the player to set `WORLDOS_TTS_BACKEND=kokoro` and restart the session for actual audio.
- **no argument:** flip the current preference.

This is the reliable text fallback the experience promises — the game stays fully playable with voice off.
