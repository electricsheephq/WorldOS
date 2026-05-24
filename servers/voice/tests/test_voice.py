"""Voice-layer tests that do NOT require PyTorch/Kokoro.

They exercise resolution, the null backend, and the Kokoro backend's static
metadata (list_voices/supports never import torch). Real Kokoro synthesis is
covered by smoke_test.py, run locally with the `kokoro` dependency group.
"""

import registry
from adapters.kokoro import KokoroBackend
from adapters.null import NullBackend


def test_registry_resolves_known():
    assert registry.resolve("narrator-dm", "kokoro") == "am_michael"
    assert registry.resolve("companion-default", "kokoro") == "af_heart"
    assert registry.resolve("npc-elder", "kokoro") == "bm_george"


def test_registry_unknown_logical_falls_back_to_a_voice():
    out = registry.resolve("totally-unknown-voice", "kokoro")
    assert isinstance(out, str) and out  # some real kokoro voice, not a crash


def test_registry_unknown_backend_passes_through():
    assert registry.resolve("narrator-dm", "no-such-backend") == "narrator-dm"


def test_null_backend_speaks_silently():
    r = NullBackend().speak("Hello, adventurer.", "narrator-dm")
    assert r.ok and r.backend == "null" and r.audio_path is None


def test_kokoro_static_metadata_without_torch():
    b = KokoroBackend()
    ids = {v.id for v in b.list_voices()}
    assert {"am_michael", "af_heart", "bm_george"} <= ids
    assert b.supports("af_heart")
    assert not b.supports("nonexistent_voice")


def test_server_module_imports():
    import server  # must not error; backends are constructed lazily

    assert server.mcp is not None
    assert server._backend_name() in ("kokoro", "null", "elevenlabs")


def test_issue55_tts_failure_falls_back_to_text_only(monkeypatch):
    # A TTS backend that raises (missing deps, model-load, WAV/playback error) must DEGRADE to
    # the silent null backend, never raise out of speak() and break the story loop (pre-release #55).
    import server

    class Boom:
        name = "kokoro"
        def speak(self, *a, **k):
            raise ImportError("kokoro unavailable")

    monkeypatch.setenv("CLAWDND_TTS_BACKEND", "kokoro")
    monkeypatch.setitem(server._backends, "kokoro", Boom())
    out = server.speak("hello")
    assert out["backend"] == "null" and out["audio_path"] is None and out["ok"] is True
    assert "fail" in out["detail"].lower()
