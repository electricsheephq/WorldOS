"""Manual Kokoro voice smoke test (NOT a pytest test).

Synthesizes a line of D&D narration/dialogue in several distinct character
voices and writes WAV files, proving the local Kokoro backend produces real,
nonzero, multi-voice audio. The first run downloads the Kokoro model.

Run (point the HF cache at LEXAR to spare the small main disk):
    HF_HOME=/Volumes/LEXAR/.cache/huggingface \
      uv run --directory servers/voice --group kokoro python smoke_test.py
"""

import contextlib
import os
import sys
import wave
from pathlib import Path

from adapters.kokoro import KokoroBackend

OUT = Path("/tmp/clawdnd_smoke")
OUT.mkdir(parents=True, exist_ok=True)

LINES = [
    ("narrator-dm", "am_michael", "The tavern door groans open, spilling lantern light into the rain."),
    ("companion-default", "af_heart", "Stay close. I don't like the look of those shadows."),
    ("npc-elder", "bm_george", "Ye seek the old crypt? Then ye seek yer own grave, traveler."),
]


def duration(path: str) -> float:
    with contextlib.closing(wave.open(path, "r")) as w:
        return w.getnframes() / float(w.getframerate())


def main() -> int:
    backend = KokoroBackend()
    print("available voices:", [v.id for v in backend.list_voices()])
    ok = True
    for logical, voice, text in LINES:
        path = str(OUT / f"{voice}.wav")
        res = backend.speak(text, voice, out_path=path, play=False)
        if res.ok and Path(path).exists() and os.path.getsize(path) > 1000:
            print(f"  OK  {voice:12s} {duration(path):5.2f}s  {os.path.getsize(path):>8} bytes  -> {path}")
        else:
            ok = False
            print(f"  FAIL {voice}: {res.detail}")
    print("SMOKE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
