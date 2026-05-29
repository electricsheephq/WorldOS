"""Swappable speech-to-text (STT) layer for WorldOS — Epic 10.

Mirrors the TTS design (see interface.py / adapters/): the server calls one tool,
transcribe(audio_path), and a backend selected by CLAWDND_STT_BACKEND turns a
recorded audio file into text. Backends are interchangeable — a local engine
(macOS dictation / whisper.cpp / faster-whisper) or a silent null backend for
tests/CI — so swapping one out never touches game state.

Unlike the TTS adapters (split across adapters/*.py), the STT backends are small
and self-contained, so they live together here. Heavy deps (torch, whisper) are
imported lazily *inside* transcribe and stay OUT of the base install; selecting a
real backend without its deps/inputs returns a clear "not configured" message
rather than crashing the server.

Selector: `select_backend()` reads CLAWDND_STT_BACKEND (default "null").
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from _env import env_var

# Returned by real backends when their optional deps or the audio input are
# missing — keeps `transcribe()` graceful (the MCP tool surfaces this as `text`).
NOT_CONFIGURED = "[stt not configured]"
# Returned by the null backend; stable so tests/CI can assert on it.
PLACEHOLDER = "[transcription unavailable]"


@runtime_checkable
class SttBackend(Protocol):
    name: str

    def transcribe(self, audio_path: str) -> str:
        """Transcribe the audio file at `audio_path` to text."""
        ...


class NullSttBackend:
    """Silent / text-only STT backend — used in CI, headless runs, and tests.

    Implements the SttBackend protocol but does no audio decoding, so the full
    STT code path (selection, tool wiring) is exercised without torch/whisper.
    """

    name = "null"

    def transcribe(self, audio_path: str) -> str:
        return PLACEHOLDER


class MacosSttBackend:
    """macOS on-device speech recognition (stub for Epic 10).

    Real wiring would use Apple's Speech framework (SFSpeechRecognizer) for
    on-device dictation, reached via PyObjC (`pyobjc-framework-Speech`) or a
    small Swift/`osascript` helper. Those deps are NOT in the base install; they
    would live in an `stt-macos` dependency group and be imported lazily below.

    Until implemented (or when run off-macOS / on an absent file) this fails
    gracefully with a clear message instead of crashing the server.
    """

    name = "macos"

    def transcribe(self, audio_path: str) -> str:
        if not audio_path or not Path(audio_path).is_file():
            return f"{NOT_CONFIGURED}: audio file not found ({audio_path!r})"
        try:
            import sys

            if sys.platform != "darwin":
                return f"{NOT_CONFIGURED}: macOS Speech backend requires macOS"
            # Lazy, optional dependency (kept out of the base install):
            import Speech  # type: ignore  # noqa: F401  (pyobjc-framework-Speech)
        except Exception as exc:  # ImportError, or PyObjC bridge unavailable
            return (
                f"{NOT_CONFIGURED}: macOS Speech backend not implemented "
                f"(needs pyobjc-framework-Speech; {type(exc).__name__})"
            )
        # Real SFSpeechRecognizer transcription would go here.
        return f"{NOT_CONFIGURED}: macOS Speech transcription not implemented yet"


class WhisperSttBackend:
    """Local Whisper STT (stub for Epic 10).

    Real wiring would use faster-whisper (CTranslate2) or whisper.cpp to
    transcribe locally on Apple Silicon. The model + torch/ctranslate2 are heavy
    and would live in an `stt-whisper` dependency group, imported lazily below so
    this module (and backend selection) load without them.

    Model size is read from CLAWDND_WHISPER_MODEL (default "base"). Until wired
    (or when deps/inputs are absent) it fails gracefully with a clear message.
    """

    name = "whisper"

    def transcribe(self, audio_path: str) -> str:
        if not audio_path or not Path(audio_path).is_file():
            return f"{NOT_CONFIGURED}: audio file not found ({audio_path!r})"
        try:
            # Lazy, optional dependency (kept out of the base install):
            from faster_whisper import WhisperModel  # type: ignore  # noqa: F401
        except Exception as exc:  # ImportError if the stt-whisper group isn't installed
            return (
                f"{NOT_CONFIGURED}: whisper backend not implemented "
                f"(needs faster-whisper; {type(exc).__name__})"
            )
        # Real transcription would go here, e.g.:
        #   model = WhisperModel(env_var("WHISPER_MODEL", "base"))
        #   segments, _ = model.transcribe(audio_path)
        #   return " ".join(s.text for s in segments).strip()
        _model = env_var("WHISPER_MODEL", "base")
        return f"{NOT_CONFIGURED}: whisper transcription not implemented yet (model={_model})"


def backend_name() -> str:
    """The selected STT backend name (env WORLDOS_STT_BACKEND, default 'null')."""
    return (env_var("STT_BACKEND", "null") or "null").lower()


def select_backend() -> SttBackend:
    """Construct the STT backend selected by CLAWDND_STT_BACKEND.

    Unknown values fall back to the null backend so a misconfiguration never
    crashes the server. Backends are cheap to build (no heavy imports at
    construction time — those are lazy inside transcribe).
    """
    name = backend_name()
    if name == "macos":
        return MacosSttBackend()
    if name == "whisper":
        return WhisperSttBackend()
    return NullSttBackend()
