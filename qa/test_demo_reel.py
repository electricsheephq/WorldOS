"""test_demo_reel.py — unit cover for demo_reel.py's load-bearing frame-verification helpers.

The demo reel is a CI-adjacent artifact whose whole value is "the frames are real, not blank".
That guarantee lives in two dependency-free PNG helpers (_png_size + _mean_luma_sample); this
pins them so a regression there can't silently pass a black frame. Single-process, no engine,
no network, no browser — builds tiny in-memory PNGs and asserts the gate discriminates.

  uv run --directory servers/engine python -m pytest qa/test_demo_reel.py
  (or plain: python3 -m pytest qa/test_demo_reel.py — no engine deps are imported)
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import demo_reel  # noqa: E402  (pure stdlib import; seed()/server are lazy, not touched here)


def _write_png(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    """Minimal filter-0 truecolor (color_type 2) PNG encoder — no image deps."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    stride = width * 3
    raw = bytearray()
    for _ in range(height):
        raw.append(0)  # filter type 0 (none)
        raw.extend(bytes(rgb) * width)
    idat = zlib.compress(bytes(raw), 9)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", idat) + chunk(b"IEND", b""))
    assert len(raw) == (stride + 1) * height  # sanity on the scanline layout


def test_png_size_reads_dimensions(tmp_path: Path) -> None:
    p = tmp_path / "dims.png"
    _write_png(p, 640, 400, (200, 180, 120))
    assert demo_reel._png_size(p) == (640, 400)


def test_mean_luma_flags_black_frame(tmp_path: Path) -> None:
    black = tmp_path / "black.png"
    _write_png(black, 200, 120, (0, 0, 0))
    # A fully black frame must score under the gate threshold (8.0) so it is REJECTED.
    assert demo_reel._mean_luma_sample(black) < 8.0


def test_mean_luma_passes_rendered_frame(tmp_path: Path) -> None:
    bright = tmp_path / "bright.png"
    _write_png(bright, 200, 120, (180, 150, 90))  # a painterly-plate-ish mid tone
    # A real (non-blank) frame must score WELL above the threshold so it is ACCEPTED.
    assert demo_reel._mean_luma_sample(bright) > 50.0


def test_captions_cover_every_expected_frame() -> None:
    # captions.txt is generated from CAPTIONS keyed by EXPECTED_FRAMES — every frame must caption.
    assert set(demo_reel.EXPECTED_FRAMES) == set(demo_reel.CAPTIONS)
    assert len(demo_reel.EXPECTED_FRAMES) == 6
    assert all(demo_reel.CAPTIONS[name].strip() for name in demo_reel.EXPECTED_FRAMES)
