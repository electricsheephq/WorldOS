#!/usr/bin/env python3
"""Tests for qa/motion_reel.py — the L7 motion-reel contact-sheet + sidecar builder.

Covers:
  1. MODE B (timeline) — assemble a reel from a list of frame PNGs: the contact-sheet PNG is
     written, decodable, and laid out in a near-square grid; the sidecar JSON has one record per
     frame with the documented shape (frame_idx / label / is_move / centroid_px).
  2. MODE B from a directory of PNGs (sorted by name).
  3. is_move inference from a move label (walk -> is_move True; idle -> False).
  4. The sidecar round-trips into qa/visual_pregate.py G5 (the reel the critic actually consumes).
  5. MODE A (engine) — orchestration with an INJECTED renderer (the live Unity capture is a TODO
     hook): produced frames assemble into the same contact-sheet + sidecar with engine-beat labels;
     no produced frames -> a clean "no_render" status (not a crash).

Run (single-process; NEVER xdist):
    uv run --directory servers/engine python -m pytest qa/test_motion_reel.py -q -p no:xdist

Pure stdlib PNGs in tmp_path; no Pillow required, no engine import, no game-state writes.
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

import motion_reel as mr  # noqa: E402
import visual_pregate as vp  # noqa: E402


# ---------------------------------------------------------------------------
# Helper — a tiny valid 8-bit RGB PNG with a bright block at (bx,by) so the centroid moves.
# ---------------------------------------------------------------------------
def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def _png(path: Path, w: int, h: int, *, bright_at: tuple[int, int] | None = None,
         block: int = 4, base: int = 10) -> Path:
    """A w×h dark PNG with an optional bright block whose top-left is bright_at (moves the centroid)."""
    rows = bytearray()
    for y in range(h):
        rows.append(0)  # filter None
        for x in range(w):
            v = base
            if bright_at is not None:
                bx, by = bright_at
                if bx <= x < bx + block and by <= y < by + block:
                    v = 240
            rows += bytes([v, v, v])
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + _chunk(b"IEND", b"")
    )
    return path


# ---------------------------------------------------------------------------
# 1. MODE B — assemble from an explicit frame list
# ---------------------------------------------------------------------------
class TestTimelineReel:
    def test_contact_sheet_and_sidecar_built(self, tmp_path):
        frames = [
            _png(tmp_path / "f0.png", 16, 16, bright_at=(2, 6)),
            _png(tmp_path / "f1.png", 16, 16, bright_at=(6, 6)),
            _png(tmp_path / "f2.png", 16, 16, bright_at=(10, 6)),
        ]
        out = tmp_path / "reel.png"
        res = mr.build_timeline_reel([str(f) for f in frames], out,
                                     labels=["walk", "walk", "walk"], scene="fixture:tavern")
        # contact sheet exists + is a decodable PNG
        assert Path(res["contact_sheet"]).exists()
        decoded = mr._read_rgb(res["contact_sheet"])
        assert decoded is not None, "the assembled contact sheet must be a decodable PNG"
        # sidecar exists + has the documented shape
        sc = json.loads(Path(res["sidecar"]).read_text())
        assert sc["schema"] == mr.SCHEMA
        assert sc["mode"] == "timeline"
        assert sc["scene"] == "fixture:tavern"
        assert len(sc["frames"]) == 3
        f0 = sc["frames"][0]
        assert f0["frame_idx"] == 0
        assert f0["label"] == "walk"
        assert f0["is_move"] is True  # "walk" is a move label
        assert "centroid_px" in f0
        # near-square grid for 3 frames -> cols 2, rows 2
        assert sc["cols"] == 2 and sc["rows"] == 2

    def test_centroid_moves_across_walk_frames(self, tmp_path):
        """The bright block slides right -> the recorded per-frame centroid x increases."""
        frames = [
            _png(tmp_path / "a.png", 32, 16, bright_at=(2, 6)),
            _png(tmp_path / "b.png", 32, 16, bright_at=(24, 6)),
        ]
        out = tmp_path / "reel.png"
        res = mr.build_timeline_reel([str(f) for f in frames], out, labels=["walk", "walk"])
        cs = [fr["centroid_px"] for fr in res["frames"]]
        assert cs[0] is not None and cs[1] is not None
        assert cs[1][0] > cs[0][0], f"centroid x should increase as the block slides right: {cs}"

    def test_idle_label_is_not_move(self, tmp_path):
        f = _png(tmp_path / "idle.png", 16, 16, bright_at=(6, 6))
        out = tmp_path / "reel.png"
        res = mr.build_timeline_reel([str(f)], out, labels=["idle"])
        assert res["frames"][0]["is_move"] is False

    def test_frames_from_dir_sorted(self, tmp_path):
        d = tmp_path / "seq"
        d.mkdir()
        _png(d / "frame_02.png", 16, 16, bright_at=(8, 6))
        _png(d / "frame_00.png", 16, 16, bright_at=(2, 6))
        _png(d / "frame_01.png", 16, 16, bright_at=(5, 6))
        paths = mr._frames_from_dir(d)
        assert [Path(p).name for p in paths] == ["frame_00.png", "frame_01.png", "frame_02.png"]

    def test_no_frames_raises(self, tmp_path):
        import pytest
        with pytest.raises(ValueError):
            mr.build_timeline_reel([], tmp_path / "x.png")

    def test_undecodable_frame_raises(self, tmp_path):
        bad = tmp_path / "bad.png"
        bad.write_bytes(b"not a png")
        import pytest
        with pytest.raises(ValueError):
            mr.build_timeline_reel([str(bad)], tmp_path / "x.png")


# ---------------------------------------------------------------------------
# 2. The sidecar feeds qa/visual_pregate.py G5 (the integration the loop relies on)
# ---------------------------------------------------------------------------
class TestSidecarFeedsG5:
    def test_frozen_idle_reel_fires_g5_critical(self, tmp_path):
        """Two IDENTICAL idle frames -> G5 frozen-idle CRITICAL when fed the reel sidecar."""
        f0 = _png(tmp_path / "i0.png", 16, 16, bright_at=(6, 6))
        f1 = _png(tmp_path / "i1.png", 16, 16, bright_at=(6, 6))  # identical
        out = tmp_path / "reel.png"
        res = mr.build_timeline_reel([str(f0), str(f1)], out, labels=["idle", "idle"])
        sidecar = json.loads(Path(res["sidecar"]).read_text())
        gates = vp.gate_motion_liveness(sidecar["frames"])
        crit = [g for g in gates if g["severity"] == "CRITICAL"]
        assert crit, f"identical idle frames should fire G5 frozen-idle CRITICAL; got {gates}"

    def test_moving_idle_reel_passes_g5(self, tmp_path):
        """Two DIFFERENT idle frames (the idle breathes) -> G5 idle PASS."""
        f0 = _png(tmp_path / "i0.png", 16, 16, bright_at=(6, 6))
        f1 = _png(tmp_path / "i1.png", 16, 16, bright_at=(7, 7))  # shifted -> non-zero delta
        out = tmp_path / "reel.png"
        res = mr.build_timeline_reel([str(f0), str(f1)], out, labels=["idle", "idle"])
        sidecar = json.loads(Path(res["sidecar"]).read_text())
        gates = vp.gate_motion_liveness(sidecar["frames"])
        assert any(g["severity"] == "PASS" and g["metric"] == "idle_interframe_delta" for g in gates), \
            f"a breathing idle should PASS G5 idle check; got {gates}"

    def test_static_move_reel_fires_g5_high(self, tmp_path):
        """Two identical MOVE frames (engine said move, render didn't displace) -> G5 HIGH."""
        f0 = _png(tmp_path / "m0.png", 16, 16, bright_at=(6, 6))
        f1 = _png(tmp_path / "m1.png", 16, 16, bright_at=(6, 6))  # identical -> no centroid shift
        out = tmp_path / "reel.png"
        res = mr.build_timeline_reel([str(f0), str(f1)], out, labels=["walk", "walk"], moves=[True, True])
        sidecar = json.loads(Path(res["sidecar"]).read_text())
        gates = vp.gate_motion_liveness(sidecar["frames"])
        high = [g for g in gates if g["severity"] == "HIGH" and g["metric"] == "move_centroid_px"]
        assert high, f"a non-displacing MOVE should fire G5 HIGH; got {gates}"


# ---------------------------------------------------------------------------
# 3. MODE A — engine reel orchestration with an injected renderer (capture is a TODO hook)
# ---------------------------------------------------------------------------
class TestEngineReel:
    def test_engine_reel_with_injected_renderer(self, tmp_path):
        """MODE A assembles the same contact-sheet + sidecar when a renderer produces per-beat PNGs."""
        produced_dir = tmp_path / "renders"
        produced_dir.mkdir()

        def fake_renderer(state, out_png):
            # one bright block sliding right per beat -> a believable engine reel
            beat = state["beat"]
            return str(_png(Path(out_png), 24, 16, bright_at=(2 + beat * 4, 6)))

        res = mr.build_engine_reel("demo-crypt", 3, tmp_path / "engine_reel.png",
                                   _renderer=fake_renderer)
        assert res["mode"] == "engine"
        assert res["scene"] == "demo-crypt"
        assert Path(res["contact_sheet"]).exists()
        sc = json.loads(Path(res["sidecar"]).read_text())
        assert sc["mode"] == "engine"
        assert len(sc["frames"]) == 3
        assert [f["label"] for f in sc["frames"]] == ["beat0", "beat1", "beat2"]

    def test_engine_reel_no_render_status(self, tmp_path):
        """With the default stub renderer (returns None) and no frame_dir -> clean no_render status."""
        res = mr.build_engine_reel("demo-crypt", 2, tmp_path / "reel.png")
        assert res.get("status") == "no_render"
        assert res["frames"] == []

    def test_engine_reel_uses_prerendered_frame_dir(self, tmp_path):
        """A directory of beat_<i>.png is used in place of the live capture hook."""
        fdir = tmp_path / "beats"
        fdir.mkdir()
        _png(fdir / "beat_0.png", 16, 16, bright_at=(2, 6))
        _png(fdir / "beat_1.png", 16, 16, bright_at=(8, 6))
        res = mr.build_engine_reel("c1", 2, tmp_path / "reel.png", frame_dir=fdir)
        assert res["mode"] == "engine"
        assert len(res["frames"]) == 2


# ---------------------------------------------------------------------------
# 4. CLI smoke (MODE B) — proves the __main__ path assembles end-to-end
# ---------------------------------------------------------------------------
class TestCli:
    def test_cli_timeline_from_dir(self, tmp_path, capsys):
        d = tmp_path / "seq"
        d.mkdir()
        _png(d / "0.png", 16, 16, bright_at=(2, 6))
        _png(d / "1.png", 16, 16, bright_at=(8, 6))
        out = tmp_path / "reel.png"
        rc = mr.main(["--mode", "timeline", "--frames-dir", str(d), "--out", str(out), "--label", "walk", "--move"])
        assert rc == 0
        assert out.exists()
        assert (tmp_path / "reel.json").exists()

    def test_cli_timeline_missing_input_returns_2(self, tmp_path):
        rc = mr.main(["--mode", "timeline", "--out", str(tmp_path / "x.png")])
        assert rc == 2
