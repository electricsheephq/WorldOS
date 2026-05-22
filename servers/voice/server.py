"""ClawDnD voice MCP server.

A swappable text-to-speech layer. The DM and companion call one tool —
speak(text, voice_id) — and a backend (selected by the CLAWDND_TTS_BACKEND env
var) synthesizes and plays the audio. Each character/NPC carries a logical
voice_id; a registry maps it to the active backend's real voice, so switching
Kokoro -> ElevenLabs never touches character data.

Epic 0 skeleton: only a health check today. The TtsBackend interface, the
Kokoro / null / ElevenLabs adapters, the voice registry, and the speak() tool
land with the voice work — see the repo issues.
"""

import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("clawdnd-voice")

_BACKEND = os.environ.get("CLAWDND_TTS_BACKEND", "kokoro")


@mcp.tool()
def ping() -> str:
    """Health check. Returns ok and the configured TTS backend name."""
    return f"clawdnd-voice: ok (v0.0.1, backend={_BACKEND})"


if __name__ == "__main__":
    mcp.run()
