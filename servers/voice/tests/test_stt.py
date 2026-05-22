"""STT-layer tests that do NOT require whisper/torch.

They exercise the null backend, env-var backend selection, the graceful-failure
behavior of the stub backends (which is exactly what happens when their optional
deps are absent — i.e. in this base/dev install), and the server's transcribe
tool wiring. Real macOS/whisper transcription is intentionally out of scope here.
"""

import stt
from stt import (
    MacosSttBackend,
    NullSttBackend,
    WhisperSttBackend,
)


def test_null_backend_transcribes_placeholder():
    b = NullSttBackend()
    assert b.name == "null"
    assert b.transcribe("/any/path.wav") == stt.PLACEHOLDER
    # Works even with no real file / empty path — it never touches the audio.
    assert b.transcribe("") == stt.PLACEHOLDER


def test_null_backend_satisfies_protocol():
    assert isinstance(NullSttBackend(), stt.SttBackend)
    assert isinstance(MacosSttBackend(), stt.SttBackend)
    assert isinstance(WhisperSttBackend(), stt.SttBackend)


def test_default_backend_is_null(monkeypatch):
    monkeypatch.delenv("CLAWDND_STT_BACKEND", raising=False)
    assert stt.backend_name() == "null"
    assert isinstance(stt.select_backend(), NullSttBackend)


def test_select_backend_via_env(monkeypatch):
    monkeypatch.setenv("CLAWDND_STT_BACKEND", "null")
    assert isinstance(stt.select_backend(), NullSttBackend)

    monkeypatch.setenv("CLAWDND_STT_BACKEND", "macos")
    assert stt.backend_name() == "macos"
    assert isinstance(stt.select_backend(), MacosSttBackend)

    monkeypatch.setenv("CLAWDND_STT_BACKEND", "whisper")
    assert isinstance(stt.select_backend(), WhisperSttBackend)


def test_select_backend_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("CLAWDND_STT_BACKEND", "WHISPER")
    assert isinstance(stt.select_backend(), WhisperSttBackend)


def test_unknown_backend_falls_back_to_null(monkeypatch):
    monkeypatch.setenv("CLAWDND_STT_BACKEND", "no-such-backend")
    assert isinstance(stt.select_backend(), NullSttBackend)


def test_stub_backends_fail_gracefully_without_file():
    # Missing input must not crash — returns a clear, non-empty message.
    for backend in (MacosSttBackend(), WhisperSttBackend()):
        out = backend.transcribe("/no/such/audio/file.wav")
        assert isinstance(out, str) and out
        assert stt.NOT_CONFIGURED in out


def test_stub_backends_fail_gracefully_when_deps_absent(tmp_path):
    # With a real file present but optional deps not installed (the dev install),
    # the stubs still return a graceful "not configured" message, never raise.
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"")  # contents irrelevant; deps are what's missing
    for backend in (MacosSttBackend(), WhisperSttBackend()):
        out = backend.transcribe(str(audio))
        assert isinstance(out, str) and stt.NOT_CONFIGURED in out


def test_server_transcribe_tool_null(monkeypatch):
    monkeypatch.setenv("CLAWDND_STT_BACKEND", "null")
    import server

    # Force re-selection so a backend cached by an earlier test/env is dropped.
    server._stt_backend = None
    result = server.transcribe("/some/recording.wav")
    assert result == {"text": stt.PLACEHOLDER, "backend": "null"}


def test_server_transcribe_tool_reselects_on_env_change(monkeypatch):
    import server

    server._stt_backend = None
    monkeypatch.setenv("CLAWDND_STT_BACKEND", "null")
    assert server._get_stt_backend().name == "null"

    # Changing the env var should re-point the cached backend.
    monkeypatch.setenv("CLAWDND_STT_BACKEND", "whisper")
    assert server._get_stt_backend().name == "whisper"
