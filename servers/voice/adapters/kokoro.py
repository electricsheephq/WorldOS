"""Kokoro local TTS backend (Apache-2.0).

Multi-voice neural TTS that runs locally on Apple Silicon. Heavy deps (PyTorch)
live in the `kokoro` dependency group and are imported lazily inside methods, so
this module (and list_voices/supports) load without PyTorch — keeping CI light.

Voice naming: first letter is accent (a=American, b=British), second is gender
(m/f). A separate KPipeline is built per accent so phonemization is correct.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from interface import SpeakResult, VoiceInfo

_VOICES = [
    VoiceInfo(id="am_michael", name="Michael", gender="m", tags=["american", "narrator"]),
    VoiceInfo(id="am_adam", name="Adam", gender="m", tags=["american"]),
    VoiceInfo(id="af_heart", name="Heart", gender="f", tags=["american", "warm"]),
    VoiceInfo(id="af_bella", name="Bella", gender="f", tags=["american"]),
    VoiceInfo(id="bm_george", name="George", gender="m", tags=["british", "elder"]),
    VoiceInfo(id="bf_emma", name="Emma", gender="f", tags=["british"]),
]
_DEFAULT = "am_michael"
_SAMPLE_RATE = 24000


def _to_numpy(audio):
    import numpy as np

    if hasattr(audio, "detach"):  # torch tensor
        return audio.detach().cpu().numpy()
    return np.asarray(audio)


def _play(path: str) -> bool:
    afplay = shutil.which("afplay")  # macOS
    if not afplay:
        return False
    try:
        subprocess.run([afplay, path], check=True)
        return True
    except Exception:
        return False


class KokoroBackend:
    name = "kokoro"

    def __init__(self) -> None:
        self._pipelines: dict[str, object] = {}

    def _pipe(self, voice: str):
        lang = voice[0]  # 'a' American, 'b' British, ...
        if lang not in self._pipelines:
            from kokoro import KPipeline

            self._pipelines[lang] = KPipeline(lang_code=lang)
        return self._pipelines[lang]

    def list_voices(self) -> list[VoiceInfo]:
        return list(_VOICES)

    def supports(self, backend_voice: str) -> bool:
        return any(v.id == backend_voice for v in _VOICES)

    def speak(self, text, backend_voice, *, speed=1.0, out_path=None, play=False) -> SpeakResult:
        import numpy as np
        import soundfile as sf

        voice = backend_voice if self.supports(backend_voice) else _DEFAULT
        chunks = [
            _to_numpy(audio) for _, _, audio in self._pipe(voice)(text, voice=voice, speed=speed)
        ]
        if not chunks:
            return SpeakResult(
                ok=False, backend=self.name, backend_voice=voice, text=text, detail="no audio produced"
            )
        audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
        path = out_path or str(
            Path(tempfile.gettempdir()) / f"clawdnd_{abs(hash((text, voice))) % 10**10}.wav"
        )
        sf.write(path, audio, _SAMPLE_RATE)
        played = _play(path) if play else False
        return SpeakResult(
            ok=True,
            backend=self.name,
            backend_voice=voice,
            text=text,
            audio_path=path,
            played=played,
            detail="kokoro",
        )
