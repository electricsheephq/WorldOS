#!/usr/bin/env python3
"""Offline synthetic tests for qa/visual_pregate.py's spec-facing CLI.

Run single-process from the repo root:
    uv run --directory servers/engine python -m pytest ../../qa/test_visual_pregate.py -q -p no:xdist

The fixtures are tiny stdlib-generated PNGs: no Unity, no private art, no LLM, no game state.
"""

from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

QA_DIR = Path(__file__).resolve().parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))

import visual_pregate as vp  # noqa: E402


FRAME_SIZE = (100, 100)
FLOOR_Y = 80
NORMAL_BBOX = [40, 50, 55, FLOOR_Y]  # 15px wide x 30px tall (aspect 2.0) — standing humanoid silhouette
# #1397: a PRONE/tilted actor at the SAME floor cell + SAME screen-scale band as NORMAL_BBOX, but
# wide+short instead of narrow+tall (aspect 0.24) — isolates the pose-uprightness defect from
# floor-contact/screen-scale (both still PASS for this bbox; only pose-uprightness should FAIL).
PRONE_BBOX = [20, 68, 70, FLOOR_Y]


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _write_rgb_png(path: Path, pixels: list[list[tuple[int, int, int]]]) -> Path:
    height = len(pixels)
    width = len(pixels[0])
    rows = bytearray()
    for row in pixels:
        rows.append(0)
        for r, g, b in row:
            rows += bytes([r, g, b])
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _chunk(b"IEND", b"")
    )
    return path


def _base_pixels() -> list[list[tuple[int, int, int]]]:
    width, height = FRAME_SIZE
    pixels: list[list[tuple[int, int, int]]] = []
    for y in range(height):
        row = []
        for x in range(width):
            shade = 84 if ((x // 5) + (y // 5)) % 2 == 0 else 132
            row.append((shade, shade, shade))
        pixels.append(row)
    return pixels


def _write_frame(path: Path, *, bbox: list[int] | None = NORMAL_BBOX, black: bool = False) -> Path:
    width, height = FRAME_SIZE
    pixels = [[(0, 0, 0) for _ in range(width)] for _ in range(height)] if black else _base_pixels()
    if bbox is not None:
        x0, y0, x1, y1 = bbox
        for y in range(max(0, y0), min(height, y1)):
            for x in range(max(0, x0), min(width, x1)):
                pixels[y][x] = (40, 220, 80)
    return _write_rgb_png(path, pixels)


def _manifest(path: Path, *, bbox: list[int] | None = NORMAL_BBOX) -> Path:
    actor: dict = {"name": "Hero", "expected_cell": [1, 1]}
    if bbox is not None:
        actor["screen_bbox"] = bbox
    path.write_text(json.dumps({
        "actors": [actor],
        "grid": {"origin": [0, 0], "cell_px": [50, 50], "rows": 2, "cols": 2},
        "floor_y_px": FLOOR_Y,
        "checks": {
            "frame_lit": {
                "min_mean_luma": 0.08,
                "max_mean_luma": 0.95,
                "max_single_color_frac": 0.90,
            },
            "floor_contact": {"tolerance_px": 4},
            "screen_scale": {"min_height_frac": 0.04, "max_height_frac": 0.40},
            "pose_uprightness": {"min_aspect_ratio": 1.25},
            "diff": {"threshold": 20, "min_area_px": 8},
        },
    }))
    return path


def _run_cli(tmp_path: Path, frame: Path, manifest: Path, *extra: str) -> tuple[int, dict]:
    report = tmp_path / f"report-{len(list(tmp_path.glob('report-*.json')))}.json"
    rc = vp.main([str(frame), str(manifest), "--json-out", str(report), *extra])
    assert report.exists(), "spec CLI should always write --json-out"
    return rc, json.loads(report.read_text())


def _check(report: dict, name: str) -> dict:
    matches = [c for c in report["checks"] if c["check"] == name]
    assert matches, f"missing check {name}: {report}"
    return matches[0]


def test_manifest_bbox_grounded_actor_passes(tmp_path):
    frame = _write_frame(tmp_path / "grounded.png", bbox=NORMAL_BBOX)
    manifest = _manifest(tmp_path / "manifest.json", bbox=NORMAL_BBOX)

    rc, report = _run_cli(tmp_path, frame, manifest)

    assert rc == 0
    assert report["verdict"] == "PASS"
    assert _check(report, "floor-contact")["status"] == "PASS"
    assert _check(report, "screen-scale")["status"] == "PASS"
    assert _check(report, "occupancy")["status"] == "PASS"
    assert report["bbox_source"] == "manifest"


def test_manifest_bbox_floating_actor_fails_floor_contact(tmp_path):
    floating = [40, 35, 55, 65]
    frame = _write_frame(tmp_path / "floating.png", bbox=floating)
    manifest = _manifest(tmp_path / "manifest.json", bbox=floating)

    rc, report = _run_cli(tmp_path, frame, manifest)

    assert rc != 0
    assert report["verdict"] == "FAIL"
    floor = _check(report, "floor-contact")
    assert floor["status"] == "FAIL"
    assert "floating" in floor["detail"].lower()


def test_black_frame_fails_frame_lit(tmp_path):
    frame = _write_frame(tmp_path / "black.png", bbox=None, black=True)
    manifest = _manifest(tmp_path / "manifest.json", bbox=NORMAL_BBOX)

    rc, report = _run_cli(tmp_path, frame, manifest)

    assert rc != 0
    lit = _check(report, "frame-lit")
    assert lit["status"] == "FAIL"
    assert "luma" in lit["detail"].lower()


def test_giant_actor_fails_screen_scale(tmp_path):
    giant = [20, 10, 80, 80]
    frame = _write_frame(tmp_path / "giant.png", bbox=giant)
    manifest = _manifest(tmp_path / "manifest.json", bbox=giant)

    rc, report = _run_cli(tmp_path, frame, manifest)

    assert rc != 0
    scale = _check(report, "screen-scale")
    assert scale["status"] == "FAIL"
    assert "height" in scale["detail"].lower()


def test_missing_manifest_bbox_fails_occupancy(tmp_path):
    frame = _write_frame(tmp_path / "missing.png", bbox=None)
    manifest = _manifest(tmp_path / "manifest.json", bbox=None)

    rc, report = _run_cli(tmp_path, frame, manifest)

    assert rc != 0
    occupancy = _check(report, "occupancy")
    assert occupancy["status"] == "FAIL"
    assert occupancy["value"]["found"] == 0
    assert occupancy["value"]["expected"] == 1


def test_prone_actor_fails_pose_uprightness(tmp_path):
    # #1397 red-first: the binding felt defect — a skinned actor rendered prone/tilted reads as a
    # wide/short bbox even though it stands at the correct cell/scale (floor-contact + screen-scale
    # PASS in isolation; only the new pose check should catch the geometry).
    frame = _write_frame(tmp_path / "prone.png", bbox=PRONE_BBOX)
    manifest = _manifest(tmp_path / "manifest.json", bbox=PRONE_BBOX)

    rc, report = _run_cli(tmp_path, frame, manifest)

    assert rc != 0
    assert report["verdict"] == "FAIL"
    pose = _check(report, "pose-uprightness")
    assert pose["status"] == "FAIL"
    assert "prone" in pose["detail"].lower()
    assert _check(report, "floor-contact")["status"] == "PASS"
    assert _check(report, "screen-scale")["status"] == "PASS"


def test_upright_actor_passes_pose_uprightness(tmp_path):
    frame = _write_frame(tmp_path / "upright.png", bbox=NORMAL_BBOX)
    manifest = _manifest(tmp_path / "manifest.json", bbox=NORMAL_BBOX)

    rc, report = _run_cli(tmp_path, frame, manifest)

    assert rc == 0
    assert report["verdict"] == "PASS"
    assert _check(report, "pose-uprightness")["status"] == "PASS"


def test_diff_vs_baseline_detects_grounded_actor_bbox(tmp_path):
    baseline = _write_frame(tmp_path / "baseline.png", bbox=None)
    frame = _write_frame(tmp_path / "with_actor.png", bbox=NORMAL_BBOX)
    manifest = _manifest(tmp_path / "manifest.json", bbox=None)

    rc, report = _run_cli(tmp_path, frame, manifest, "--baseline", str(baseline))

    assert rc == 0
    assert report["verdict"] == "PASS"
    assert report["bbox_source"] == "baseline-diff"
    assert len(report["bboxes"]) == 1
    assert _check(report, "occupancy")["status"] == "PASS"
    assert _check(report, "floor-contact")["status"] == "PASS"


def test_diff_vs_baseline_missing_actor_fails_occupancy(tmp_path):
    baseline = _write_frame(tmp_path / "baseline.png", bbox=None)
    frame = _write_frame(tmp_path / "same.png", bbox=None)
    manifest = _manifest(tmp_path / "manifest.json", bbox=None)

    rc, report = _run_cli(tmp_path, frame, manifest, "--baseline", str(baseline))

    assert rc != 0
    assert report["bbox_source"] == "baseline-diff"
    assert _check(report, "occupancy")["status"] == "FAIL"
