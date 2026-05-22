"""Silent / text-only TTS backend — used in CI and headless runs.

Implements the TtsBackend protocol but synthesizes no audio, so the full voice
code path (resolution, tool wiring) is exercised without PyTorch or model loads.
"""

from __future__ import annotations

from interface import SpeakResult, VoiceInfo


class NullBackend:
    name = "null"

    def list_voices(self) -> list[VoiceInfo]:
        return [VoiceInfo(id="null", name="Silent")]

    def supports(self, backend_voice: str) -> bool:
        return True

    def speak(self, text, backend_voice, *, speed=1.0, out_path=None, play=False) -> SpeakResult:
        return SpeakResult(
            ok=True,
            backend=self.name,
            backend_voice=backend_voice,
            text=text,
            audio_path=None,
            played=False,
            detail="null backend (no audio synthesized)",
        )
