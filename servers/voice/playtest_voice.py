"""Voiced playtest (NOT a pytest test): speak real lines from the Cellar Rats
adventure, each in its assigned character voice, proving the voice layer plays
actual campaign content with distinct voices.

Run (optionally point HF_HOME at a large local cache directory):
    HF_HOME="$HOME/.cache/huggingface" \
      uv run --directory servers/voice --group kokoro python playtest_voice.py
"""

import contextlib
import os
import sys
import wave
from pathlib import Path

import registry
from adapters.kokoro import KokoroBackend

OUT = Path("/tmp/clawdnd_playtest")
OUT.mkdir(parents=True, exist_ok=True)

# Verbatim / representative lines from content/campaigns/cellar-rats/adventure.json
LINES = [
    ("narrator-dm", "Rain comes down the chimney in spits and hisses on the fire. The Sodden Crown is warm, and dry, and the only roof for a day in any direction."),
    ("npc-rogue", "Stay back! You — you go back up. Is not safe down. Not for you, not for nobody."),
    ("companion-default", "Easy now. Whatever's down there, we face it together — and I've still got a spell or two left if it bites."),
]


def duration(path: str) -> float:
    with contextlib.closing(wave.open(path, "r")) as w:
        return w.getnframes() / float(w.getframerate())


def main() -> int:
    backend = KokoroBackend()
    ok = True
    for logical, text in LINES:
        voice = registry.resolve(logical, "kokoro")
        path = str(OUT / f"{logical}.wav")
        res = backend.speak(text, voice, out_path=path, play=False)
        if res.ok and Path(path).exists() and os.path.getsize(path) > 1000:
            print(f"  OK  {logical:18s} -> {voice:11s} {duration(path):5.2f}s  {os.path.getsize(path):>8} bytes  {path}")
        else:
            ok = False
            print(f"  FAIL {logical}: {res.detail}")
    print("VOICED PLAYTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
