"""WorldOS voice MCP server.

A swappable voice layer with two directions:

  - text-to-speech: speak(text, voice_id) -> a backend selected by
    WORLDOS_TTS_BACKEND synthesizes and plays audio. Each character/NPC carries
    a logical voice_id; the registry resolves it to the active backend's real
    voice, so switching Kokoro -> ElevenLabs never touches character data.
  - speech-to-text: transcribe(audio_path) -> a backend selected by
    CLAWDND_STT_BACKEND turns a recorded audio file into text.

TTS backends: kokoro (default, local), elevenlabs (placeholder), null (silent/CI).
STT backends: null (default, placeholder/CI), macos / whisper (stubs). See stt.py.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import registry
import stt
from _env import env_var
from interface import SpeakResult

mcp = FastMCP("clawdnd-voice")
_backends: dict[str, object] = {}
_stt_backend: stt.SttBackend | None = None


def _backend_name() -> str:
    return (env_var("TTS_BACKEND", "kokoro") or "kokoro").lower()


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


def _get_stt_backend() -> stt.SttBackend:
    """The active STT backend, rebuilt if CLAWDND_STT_BACKEND changed.

    Construction is cheap (heavy deps are imported lazily inside transcribe), so
    re-selecting on a name change is free and lets the env var drive selection at
    runtime — consistent with how the TTS selector re-reads its env var.
    """
    global _stt_backend
    if _stt_backend is None or _stt_backend.name != stt.backend_name():
        _stt_backend = stt.select_backend()
    return _stt_backend


@mcp.tool()
def ping() -> str:
    """Health check. Returns ok and the active TTS + STT backend names."""
    return (
        f"clawdnd-voice: ok (v0.0.1, tts={_backend_name()}, stt={stt.backend_name()})"
    )


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
    try:
        backend = _get_backend()
        backend_voice = registry.resolve(voice_id, backend.name)
        res: SpeakResult = backend.speak(text, backend_voice, speed=speed, play=play)
    except Exception as exc:
        # Voice is an ADAPTER — a TTS failure (missing deps, model-load, WAV write, or playback
        # error) must DEGRADE to text-only, never raise through `speak` and break the story loop
        # (the shipped .mcp.json selects Kokoro by default). Fall back to the silent null backend
        # so the call still returns cleanly with no audio. (#55)
        detail = f"TTS backend {_backend_name()!r} failed ({type(exc).__name__}: {exc}); text-only fallback"
        try:
            from adapters.null import NullBackend
            nb = NullBackend()
            res = nb.speak(text, registry.resolve(voice_id, nb.name), speed=speed, play=False)
            return {"ok": True, "voice_id": voice_id, "backend": res.backend,
                    "backend_voice": res.backend_voice, "audio_path": res.audio_path,
                    "played": res.played, "detail": detail}
        except Exception:  # even the null path failed — still never raise out of speak
            return {"ok": True, "voice_id": voice_id, "backend": "null", "backend_voice": "",
                    "audio_path": None, "played": False, "detail": detail}
    return {
        "ok": res.ok,
        "voice_id": voice_id,
        "backend": res.backend,
        "backend_voice": res.backend_voice,
        "audio_path": res.audio_path,
        "played": res.played,
        "detail": res.detail,
    }


@mcp.tool()
def transcribe(audio_path: str) -> dict:
    """Transcribe a recorded audio file to text (speech-to-text).

    Uses the STT backend selected by CLAWDND_STT_BACKEND (default 'null', which
    returns a placeholder for CI/headless). Real backends (macos, whisper) decode
    on-device; if their optional deps or the audio file are missing they return a
    clear "not configured" message in `text` rather than crashing.
    """
    backend = _get_stt_backend()
    text = backend.transcribe(audio_path)
    return {"text": text, "backend": backend.name}


if __name__ == "__main__":
    mcp.run()
