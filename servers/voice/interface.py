"""Swappable text-to-speech interface for WorldOS.

The DM and companion only ever call speak(text, voice_id). A TtsBackend turns a
backend-native voice into spoken audio. Backends are interchangeable — Kokoro
(local, default), ElevenLabs (later), or a silent null backend for tests/CI —
so switching one out never touches game state or character data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass
class VoiceInfo:
    id: str  # backend-native voice id / name
    name: str  # human-friendly label
    gender: str = ""
    tags: Optional[list[str]] = None


@dataclass
class SpeakResult:
    ok: bool
    backend: str
    backend_voice: str
    text: str
    audio_path: Optional[str] = None
    played: bool = False
    detail: str = ""


@runtime_checkable
class TtsBackend(Protocol):
    name: str

    def list_voices(self) -> list[VoiceInfo]:
        ...

    def supports(self, backend_voice: str) -> bool:
        ...

    def speak(
        self,
        text: str,
        backend_voice: str,
        *,
        speed: float = 1.0,
        out_path: Optional[str] = None,
        play: bool = False,
    ) -> SpeakResult:
        ...
