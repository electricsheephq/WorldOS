"""ElevenLabs TTS backend (placeholder for Epic 10).

The interface matches the Kokoro backend, so switching is a config change
(CLAWDND_TTS_BACKEND=elevenlabs) once this is implemented. Until then it fails
gracefully so selecting it never crashes the server.
"""

from __future__ import annotations

from interface import SpeakResult, VoiceInfo


class ElevenLabsBackend:
    name = "elevenlabs"

    def list_voices(self) -> list[VoiceInfo]:
        return []

    def supports(self, backend_voice: str) -> bool:
        return False

    def speak(self, text, backend_voice, *, speed=1.0, out_path=None, play=False) -> SpeakResult:
        return SpeakResult(
            ok=False,
            backend=self.name,
            backend_voice=backend_voice,
            text=text,
            detail="ElevenLabs backend not yet implemented (use CLAWDND_TTS_BACKEND=kokoro)",
        )
