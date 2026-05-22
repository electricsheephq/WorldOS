"""ClawDnD voice MCP server.

A swappable text-to-speech layer. The DM and companion call one tool —
speak(text, voice_id) — and a backend (selected by CLAWDND_TTS_BACKEND)
synthesizes and plays the audio. Each character/NPC carries a logical voice_id;
the registry resolves it to the active backend's real voice, so switching
Kokoro -> ElevenLabs never touches character data.

Backends: kokoro (default, local), elevenlabs (placeholder), null (silent/CI).
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

import registry
from interface import SpeakResult

mcp = FastMCP("clawdnd-voice")
_backends: dict[str, object] = {}


def _backend_name() -> str:
    return os.environ.get("CLAWDND_TTS_BACKEND", "kokoro").lower()


def _get_backend():
    name = _backend_name()
    if name not in _backends:
        if name == "null":
            from adapters.null import NullBackend

            _backends[name] = NullBackend()
        elif name == "elevenlabs":
            from adapters.elevenlabs import ElevenLabsBackend

            _backends[name] = ElevenLabsBackend()
        else:
            from adapters.kokoro import KokoroBackend

            _backends[name] = KokoroBackend()
    return _backends[name]


@mcp.tool()
def ping() -> str:
    """Health check. Returns ok and the active TTS backend name."""
    return f"clawdnd-voice: ok (v0.0.1, backend={_backend_name()})"


@mcp.tool()
def list_voices() -> list[dict]:
    """List the available voices for the active TTS backend."""
    return [vars(v) for v in _get_backend().list_voices()]


@mcp.tool()
def speak(text: str, voice_id: str = "narrator-dm", speed: float = 1.0, play: bool = True) -> dict:
    """Speak a line of text in a character's voice.

    `voice_id` is the LOGICAL voice (e.g. 'narrator-dm', 'companion-default', or
    an NPC's voice_id from its character sheet); it's resolved to the active
    backend's real voice. Generates audio and plays it on macOS when play=True.
    Voice every line of narration and dialogue through this tool.
    """
    backend = _get_backend()
    backend_voice = registry.resolve(voice_id, backend.name)
    res: SpeakResult = backend.speak(text, backend_voice, speed=speed, play=play)
    return {
        "ok": res.ok,
        "voice_id": voice_id,
        "backend": res.backend,
        "backend_voice": res.backend_voice,
        "audio_path": res.audio_path,
        "played": res.played,
        "detail": res.detail,
    }


if __name__ == "__main__":
    mcp.run()
