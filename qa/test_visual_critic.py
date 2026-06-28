#!/usr/bin/env python3
"""Tests for the visual-critic v2 deterministic layer.

Covers:
  1. CameraSpec.world_to_screen — projection matches Unity's locked dimetric spec to ≤1px
     (using the known camera params: orthoSize=18, pitch=atan(0.5), pos=(0,40.25,-55.5),
     aspect=1344/756). Validated against Unity WorldToViewportPoint ground truth during the
     bake-off (the camera-fix from right×fwd to fwd×right cross-product).
  2. G1 FRAME-LIT verdicts on synthetic PNG inputs (black frame → CRITICAL, white-lit → PASS).
  3. G3 FLOOR-CONTACT verdicts on synthetic actor inputs (floating actor → CRITICAL, grounded → PASS).
  4. G4 SCREEN-SCALE verdicts on synthetic actor inputs (giant actor → HIGH, correct → PASS).
  5. qa/visual_regression.py — detect_visual_regression() verdict: REGRESSED, IMPROVED,
     WITHIN_NOISE, NO_BASELINE.

Run (single-process; NEVER xdist):
    uv run --directory servers/engine python -m pytest qa/test_visual_critic.py -q -p no:xdist

These tests synthesize tiny PNG bytes in tmp_path only — pure stdlib, no LLM calls, no game
state, no committed data artifact mutations.
"""

from __future__ import annotations

import json
import math
import struct
import sys
import zlib
from pathlib import Path

# ---------------------------------------------------------------------------
# Path fixup so imports work from the worktree root
# ---------------------------------------------------------------------------
QA_DIR = Path(__file__).resolve().parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))

import visual_pregate as vp  # noqa: E402
import visual_regression as vr  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers — synthesize tiny valid PNGs with stdlib only
# ---------------------------------------------------------------------------

def _make_png(width: int, height: int, r: int, g: int, b: int) -> bytes:
    """Return a minimal valid 8-bit RGB PNG filled with (r,g,b). No external libs."""
    raw_rows = bytearray()
    for _ in range(height):
        raw_rows.append(0)  # filter type 0 (None)
        for _ in range(width):
            raw_rows += bytes([r, g, b])
    compressed = zlib.compress(bytes(raw_rows), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        ln = struct.pack(">I", len(data))
        payload = tag + data
        crc = struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
        return ln + payload + crc

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def _write_png(tmp_path: Path, name: str, r: int, g: int, b: int, w: int = 8, h: int = 8) -> Path:
    p = tmp_path / name
    p.write_bytes(_make_png(w, h, r, g, b))
    return p


def _minimal_scenegrid(cols: int = 15, rows: int = 12) -> vp.SceneGrid:
    """A 15×12 grid, 5 ft cells, all floor/walkable. Suitable for G3/G4 geometry tests."""
    return vp.SceneGrid(
        cols=cols, rows=rows, cell_size_ft=5.0,
        cells={}, props=[], spawns={}, lighting={},
        cell_default={"type": "floor", "walkable": True},
    )


# ---------------------------------------------------------------------------
# 1. Camera projection — ≤1px vs Unity ground truth for specific world points
# ---------------------------------------------------------------------------
# Ground-truth screen pixel coords from the analytic ortho projection (yaw=0, so the
# geometry is deterministic: right = (cos0,0,-sin0)=(1,0,0), fwd×right gives a pure-pitch up).
# These encode the bake-off camera-fix invariant: up=cross(fwd,right), NOT right×fwd.
# Format: (wx, wy, wz) -> expected (sx, sy) in image-space pixels (top-left origin, +y down).
# Tolerance: ≤1px — matches the "≤1px vs Unity WorldToViewportPoint" claim in the SKILL.md.
def _analytic_gt(wx: float, wy: float, wz: float) -> tuple[float, float]:
    """Analytic ortho projection for the locked spec (yaw=0, pitch=atan(0.5))."""
    import math as _math
    p = _math.radians(_math.degrees(_math.atan(0.5)))  # == atan(0.5) exactly
    fwd = (_math.sin(0) * _math.cos(p), -_math.sin(p), _math.cos(0) * _math.cos(p))
    right = (1.0, 0.0, 0.0)
    up = (
        fwd[1] * right[2] - fwd[2] * right[1],
        fwd[2] * right[0] - fwd[0] * right[2],
        fwd[0] * right[1] - fwd[1] * right[0],
    )
    pos = (0.0, 40.25, -55.5)
    dx, dy, dz = wx - pos[0], wy - pos[1], wz - pos[2]
    cam_r = dx * right[0] + dy * right[1] + dz * right[2]
    cam_u = dx * up[0] + dy * up[1] + dz * up[2]
    half_h = 18.0
    half_w = 18.0 * (1344.0 / 756.0)
    sx = (cam_r / half_w) * (1344.0 / 2.0) + 1344.0 / 2.0
    sy = 756.0 / 2.0 - (cam_u / half_h) * (756.0 / 2.0)
    return sx, sy


# Analytic GT tuples: (wx, wy, wz) and the analytically expected (sx, sy).
# These are the same formula as CameraSpec.world_to_screen — the test validates that
# the implementation matches (and that the cross-product direction is correct).
_CAMERA_GT: list[tuple[tuple[float, float, float], tuple[float, float]]] = [
    ((0.0, 0.0, 0.0), _analytic_gt(0.0, 0.0, 0.0)),
    ((10.0, 0.0, 0.0), _analytic_gt(10.0, 0.0, 0.0)),
    ((0.0, 5.2, 30.0), _analytic_gt(0.0, 5.2, 30.0)),   # actor head at its floor cell
]


class TestCameraProjection:
    """Validate the locked dimetric camera's world_to_screen projection.

    Key invariant from the bake-off camera fix:
      up = cross(fwd, right)   ← correct (fwd×right)
      NOT: up = -(cross(right, fwd))  ← the old bug (negated up, flipped Y depth axis)

    Test 1: far cells (high Z) project ABOVE near cells (lower Z) — i.e. sy decreases with wz.
    Test 2: right-side points project to higher sx (positive cam_r).
    Test 3: a known grid-centre cell produces sx near 672, sy near 378 (≤1px tol).
    """

    def test_far_cell_projects_above_near_cell(self):
        """Depth axis: increasing world-Z should yield decreasing screen-Y (higher in image)."""
        cam = vp.CameraSpec.LOCKED
        _, sy_near = cam.world_to_screen(0.0, 0.0, 5.0)    # near cell
        _, sy_far = cam.world_to_screen(0.0, 0.0, 50.0)   # far cell
        assert sy_far < sy_near, (
            f"Far cell (wz=50) should project above near cell (wz=5) on screen: "
            f"sy_far={sy_far:.1f} sy_near={sy_near:.1f}. "
            "This would be reversed by the old right×fwd bug — check the cross-product order."
        )

    def test_right_side_maps_to_higher_sx(self):
        """Positive world-X → larger screen-X (right side of image)."""
        cam = vp.CameraSpec.LOCKED
        sx_left, _ = cam.world_to_screen(-10.0, 0.0, 30.0)
        sx_right, _ = cam.world_to_screen(10.0, 0.0, 30.0)
        assert sx_right > sx_left, (
            f"Positive world-X should yield larger screen-X: sx_right={sx_right:.1f} sx_left={sx_left:.1f}"
        )

    def test_projection_matches_analytic_gt_within_1px(self):
        """CameraSpec.world_to_screen must match the analytic reference ≤1px (the bake-off claim)."""
        cam = vp.CameraSpec.LOCKED
        for (wx, wy, wz), (exp_sx, exp_sy) in _CAMERA_GT:
            got_sx, got_sy = cam.world_to_screen(wx, wy, wz)
            assert abs(got_sx - exp_sx) <= 1.0, (
                f"world_to_screen({wx},{wy},{wz}) sx={got_sx:.2f} vs expected {exp_sx:.2f} — "
                "delta >1px; camera cross-product or projection formula may be wrong"
            )
            assert abs(got_sy - exp_sy) <= 1.0, (
                f"world_to_screen({wx},{wy},{wz}) sy={got_sy:.2f} vs expected {exp_sy:.2f} — "
                "delta >1px; camera cross-product or projection formula may be wrong"
            )

    def test_floor_px_per_cell_y_positive(self):
        """floor_px_per_cell_y must be a positive number (sanity: the projection works)."""
        cam = vp.CameraSpec.LOCKED
        sg = _minimal_scenegrid()
        pcy = cam.floor_px_per_cell_y(sg)
        assert pcy > 0, f"floor_px_per_cell_y should be positive, got {pcy}"

    def test_camera_spec_locked_singleton(self):
        """CameraSpec.LOCKED should match the known bake-off spec values."""
        c = vp.CameraSpec.LOCKED
        assert abs(c.ortho_size - 18.0) < 1e-9
        assert abs(c.pos[0] - 0.0) < 1e-9
        assert abs(c.pos[1] - 40.25) < 1e-9
        assert abs(c.pos[2] - (-55.5)) < 1e-9
        assert abs(c.aspect - 1344.0 / 756.0) < 1e-6
        expected_pitch = math.degrees(math.atan(0.5))
        assert abs(c.pitch_deg - expected_pitch) < 1e-9
        assert c.px_w == 1344
        assert c.px_h == 756


# ---------------------------------------------------------------------------
# 2. G1 FRAME-LIT — luminance verdicts on synthetic PNGs
# ---------------------------------------------------------------------------

class TestG1FrameLit:
    def test_black_frame_critical(self, tmp_path):
        """A completely black PNG (mean lum=0) should fire CRITICAL."""
        png = _write_png(tmp_path, "black.png", 0, 0, 0, w=16, h=16)
        gates = vp.gate_frame_lit(png)
        crit = [g for g in gates if g["severity"] == "CRITICAL"]
        assert crit, f"Black frame should be CRITICAL; got {gates}"
        assert "effectively black" in crit[0]["detail"].lower() or "mean lum" in crit[0]["detail"]

    def test_white_frame_critical(self, tmp_path):
        """A fully white PNG (mean lum=1) should fire CRITICAL (blown out)."""
        png = _write_png(tmp_path, "white.png", 255, 255, 255, w=16, h=16)
        gates = vp.gate_frame_lit(png)
        crit = [g for g in gates if g["severity"] == "CRITICAL"]
        assert crit, f"White frame should be CRITICAL (blown out); got {gates}"

    def test_midgrey_frame_pass(self, tmp_path):
        """A midgrey frame (mean lum ~0.5) with good variance should PASS."""
        # Use a checkerboard-like alternation to ensure variance > LUM_VARIANCE_FLAT.
        # We can't easily make a true checkerboard with the simple helper, but 128 grey
        # is within the pass range for both mean and variance (variance=0 but mean passes the lit
        # checks; a solid grey will PASS G1_mean checks, potentially HIGH on variance — OK for test).
        # Actually a flat grey has near-zero variance so it might hit HIGH. Use two values instead.
        p = tmp_path / "grey.png"
        # Alternate rows: dark then bright to ensure variance.
        rows = bytearray()
        for row_i in range(16):
            rows.append(0)  # filter
            val = 60 if (row_i % 2) == 0 else 200
            for _ in range(16):
                rows += bytes([val, val, val])
        p.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + _chunk(b"IEND", b"")
        )
        gates = vp.gate_frame_lit(p)
        assert any(g["severity"] == "PASS" for g in gates), (
            f"Mixed grey frame should PASS G1; got {gates}"
        )

    def test_missing_file_skipped(self, tmp_path):
        """A non-existent file should return SKIPPED (graceful degradation)."""
        gates = vp.gate_frame_lit(tmp_path / "nofile.png")
        assert any(g["severity"] == "SKIPPED" for g in gates)


def _chunk(tag: bytes, data: bytes) -> bytes:
    ln = struct.pack(">I", len(data))
    payload = tag + data
    crc = struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
    return ln + payload + crc


# ---------------------------------------------------------------------------
# 3. G3 FLOOR-CONTACT — floating and grounded actor verdicts
# ---------------------------------------------------------------------------

class TestG3FloorContact:
    def _run(self, actors: list[dict], sg: vp.SceneGrid | None = None) -> list[dict]:
        if sg is None:
            sg = _minimal_scenegrid()
        return vp.gate_floor_contact_and_scale(sg, vp.CameraSpec.LOCKED, actors)

    def test_no_actors_skipped(self):
        gates = self._run([])
        assert any(g["severity"] == "SKIPPED" for g in gates)

    def test_grounded_actor_pass(self):
        """An actor whose measured feet_px match the projected floor → PASS."""
        sg = _minimal_scenegrid()
        cam = vp.CameraSpec.LOCKED
        c, r = sg.cols // 2, sg.rows // 2
        wx, wz = sg.cell_world_x(c), sg.cell_world_z(r)
        _, floor_sy = cam.world_to_screen(wx, 0.0, wz)
        # Perfectly grounded: feet_px[1] == floor_sy.
        actor = {"id": "hero", "cell": [c, r], "feet_px": [cam.px_w // 2, round(floor_sy)], "px_height": 100}
        gates = self._run([actor], sg)
        g3 = [g for g in gates if g["gate"] == "G3_floor_contact"]
        assert g3, "G3 should have run"
        assert all(g["severity"] == "PASS" for g in g3), f"Grounded actor should PASS; got {g3}"

    def test_floating_actor_critical(self):
        """An actor whose feet are far above the projected floor → CRITICAL."""
        sg = _minimal_scenegrid()
        cam = vp.CameraSpec.LOCKED
        c, r = sg.cols // 2, sg.rows // 2
        wx, wz = sg.cell_world_x(c), sg.cell_world_z(r)
        _, floor_sy = cam.world_to_screen(wx, 0.0, wz)
        pcy = cam.floor_px_per_cell_y(sg)
        # Push feet 0.6 cells ABOVE the floor (negative delta_cells < -FLOAT_CELL_CRIT=0.45).
        float_offset_px = -int(0.6 * pcy) - 5   # well above the floor
        actor = {
            "id": "floater",
            "cell": [c, r],
            "feet_px": [cam.px_w // 2, round(floor_sy + float_offset_px)],
            "px_height": 100,
        }
        gates = self._run([actor], sg)
        g3 = [g for g in gates if g["gate"] == "G3_floor_contact"]
        crit = [g for g in g3 if g["severity"] == "CRITICAL"]
        assert crit, (
            f"A floating actor 0.6 cells above floor should fire CRITICAL; got {g3}"
        )

    def test_missing_feet_px_skipped(self):
        """An actor dict without feet_px should produce SKIPPED, not crash."""
        actor = {"id": "nofeet", "cell": [5, 5]}
        gates = self._run([actor])
        assert any(g["severity"] == "SKIPPED" for g in gates)


# ---------------------------------------------------------------------------
# 4. G4 SCREEN-SCALE — scale-break verdicts
# ---------------------------------------------------------------------------

class TestG4ScreenScale:
    def _run_scale(self, px_height: float, cell: list[int] | None = None) -> list[dict]:
        sg = _minimal_scenegrid()
        cam = vp.CameraSpec.LOCKED
        if cell is None:
            cell = [sg.cols // 2, sg.rows // 2]
        c, r = cell
        wx, wz = sg.cell_world_x(c), sg.cell_world_z(r)
        _, floor_sy = cam.world_to_screen(wx, 0.0, wz)
        actor = {
            "id": "a",
            "cell": [c, r],
            "feet_px": [cam.px_w // 2, round(floor_sy)],
            "px_height": px_height,
        }
        return vp.gate_floor_contact_and_scale(sg, cam, [actor])

    def _expected_px(self, cell: list[int] | None = None) -> float:
        sg = _minimal_scenegrid()
        cam = vp.CameraSpec.LOCKED
        if cell is None:
            cell = [sg.cols // 2, sg.rows // 2]
        c, r = cell
        wx, wz = sg.cell_world_x(c), sg.cell_world_z(r)
        _, sy_feet = cam.world_to_screen(wx, 0.0, wz)
        _, sy_head = cam.world_to_screen(wx, vp.DEFAULT_ACTOR_WORLD_H, wz)
        return abs(sy_head - sy_feet)

    def test_correct_scale_pass(self):
        """An actor at the spec-expected pixel height → PASS on G4."""
        expected = self._expected_px()
        gates = self._run_scale(expected)
        g4 = [g for g in gates if g["gate"] == "G4_screen_scale"]
        assert g4, "G4 should run when px_height is supplied"
        assert all(g["severity"] == "PASS" for g in g4), f"Correct scale should PASS; got {g4}"

    def test_giant_actor_high(self):
        """An actor 2× the expected height → HIGH (SCALE_REL_HIGH > 0.32)."""
        expected = self._expected_px()
        gates = self._run_scale(expected * 2.5)   # 150% error >> 32%
        g4 = [g for g in gates if g["gate"] == "G4_screen_scale"]
        assert any(g["severity"] == "HIGH" for g in g4), (
            f"Giant actor (2.5× expected) should be HIGH; got {g4}"
        )

    def test_tiny_actor_high(self):
        """An actor at 30% of expected height → HIGH."""
        expected = self._expected_px()
        gates = self._run_scale(expected * 0.3)
        g4 = [g for g in gates if g["gate"] == "G4_screen_scale"]
        assert any(g["severity"] == "HIGH" for g in g4), (
            f"Tiny actor (0.3× expected) should be HIGH; got {g4}"
        )


# ---------------------------------------------------------------------------
# 5. run_pregates — orchestrator verdict
# ---------------------------------------------------------------------------

class TestRunPregates:
    def test_black_frame_verdict_flag(self, tmp_path):
        """Black frame → overall verdict FLAG, exit code 2."""
        png = _write_png(tmp_path, "b.png", 0, 0, 0)
        res = vp.run_pregates(str(png))
        assert res["verdict"] == "FLAG"
        assert res["blocking"]

    def test_nonexistent_file_skipped(self, tmp_path):
        """Non-existent file with no scenegrid → SKIPPED (nothing ran)."""
        res = vp.run_pregates(str(tmp_path / "missing.png"))
        assert res["verdict"] == "SKIPPED"

    def test_no_reel_g5_skips_still_only_round_unchanged(self, tmp_path):
        """ADDITIVITY: a still-only round (no reel) leaves G5 SKIPPED — empty == today."""
        # A lit, varied frame so G1 PASSes; no scenegrid, no reel.
        p = tmp_path / "lit.png"
        rows = bytearray()
        for row_i in range(16):
            rows.append(0)
            val = 60 if (row_i % 2) == 0 else 200
            for _ in range(16):
                rows += bytes([val, val, val])
        p.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + _chunk(b"IEND", b"")
        )
        res = vp.run_pregates(str(p))  # no reel kwarg
        g5 = [g for g in res["gates"] if g["gate"] == "G5_motion_liveness"]
        assert g5 and g5[0]["severity"] == "SKIPPED", f"G5 must SKIP with no reel; got {g5}"
        # G1 PASSes on this lit/varied frame, so the overall verdict is deterministically PASS — the
        # still round is unchanged by G5 being SKIPPED (additivity: empty reel == today's behavior).
        assert res["verdict"] == "PASS", f"a lit still-only round should PASS; got {res['verdict']}"
        assert any(g["gate"] == "G1_frame_lit" and g["severity"] == "PASS" for g in res["gates"])


# ---------------------------------------------------------------------------
# 5b. G5 MOTION-LIVENESS — frozen-idle CRITICAL + no-displacement HIGH on synthetic reels
# ---------------------------------------------------------------------------

class TestG5MotionLiveness:
    def _reel(self, tmp_path, specs: list[tuple[str, tuple[int, int], bool]]) -> list[dict]:
        """specs: list of (label, bright_top_left, is_move). Writes PNGs + returns reel frame dicts."""
        frames = []
        for i, (label, (bx, by), is_move) in enumerate(specs):
            p = tmp_path / f"r{i}.png"
            rows = bytearray()
            for y in range(16):
                rows.append(0)
                for x in range(16):
                    v = 240 if (bx <= x < bx + 4 and by <= y < by + 4) else 10
                    rows += bytes([v, v, v])
            p.write_bytes(
                b"\x89PNG\r\n\x1a\n"
                + _chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
                + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
                + _chunk(b"IEND", b"")
            )
            frames.append({"frame": str(p), "label": label, "is_move": is_move})
        return frames

    def test_no_reel_skipped(self):
        gates = vp.gate_motion_liveness(None)
        assert any(g["severity"] == "SKIPPED" for g in gates)

    def test_frozen_idle_critical(self, tmp_path):
        """Two identical idle frames → frozen-idle CRITICAL."""
        reel = self._reel(tmp_path, [("idle", (6, 6), False), ("idle", (6, 6), False)])
        gates = vp.gate_motion_liveness(reel)
        crit = [g for g in gates if g["severity"] == "CRITICAL"]
        assert crit, f"identical idle frames should fire CRITICAL; got {gates}"
        assert "frozen" in crit[0]["detail"].lower()

    def test_breathing_idle_passes(self, tmp_path):
        """Two differing idle frames → idle PASS (the idle is alive)."""
        reel = self._reel(tmp_path, [("idle", (6, 6), False), ("idle", (7, 7), False)])
        gates = vp.gate_motion_liveness(reel)
        assert any(g["severity"] == "PASS" and g["metric"] == "idle_interframe_delta" for g in gates), \
            f"a moving idle should PASS; got {gates}"

    def test_static_move_high(self, tmp_path):
        """A MOVE beat whose centroid does not displace → HIGH (slide/teleport)."""
        reel = self._reel(tmp_path, [("walk", (6, 6), True), ("walk", (6, 6), True)])
        gates = vp.gate_motion_liveness(reel)
        high = [g for g in gates if g["severity"] == "HIGH" and g["metric"] == "move_centroid_px"]
        assert high, f"a non-displacing move should be HIGH; got {gates}"

    def test_real_move_passes(self, tmp_path):
        """A MOVE beat that displaces the centroid >= threshold → move PASS."""
        reel = self._reel(tmp_path, [("walk", (1, 6), True), ("walk", (11, 6), True)])
        gates = vp.gate_motion_liveness(reel)
        assert any(g["severity"] == "PASS" and g["metric"] == "move_centroid_px" for g in gates), \
            f"a displacing move should PASS; got {gates}"

    def test_run_pregates_frozen_idle_flags(self, tmp_path):
        """End-to-end: a frozen-idle reel makes run_pregates verdict FLAG."""
        # G1 needs a real render png too (use a lit one).
        lit = _write_png(tmp_path, "lit.png", 120, 120, 120, w=16, h=16)
        reel = self._reel(tmp_path, [("idle", (6, 6), False), ("idle", (6, 6), False)])
        res = vp.run_pregates(str(lit), reel=reel)
        assert res["verdict"] == "FLAG", f"frozen idle reel should FLAG; got {res['verdict']}"
        assert any(g["gate"] == "G5_motion_liveness" and g["severity"] == "CRITICAL"
                   for g in res["gates"])


# ---------------------------------------------------------------------------
# 6. visual_regression.detect_visual_regression — verdict logic (no DB, stub rows)
# ---------------------------------------------------------------------------

class TestDetectVisualRegression:
    """Test the regression logic directly with synthetic candidate/baseline dicts, bypassing DB."""

    def _baseline(self, overall: float, dims: dict, blocking: str = "") -> dict:
        return {
            "run_id": "vc-tavern-r1-base",
            "surface": "visual",
            "is_canonical_baseline": 1,
            "visual_scene": "fixture:tavern",
            "visual_backend": "unity-cl",
            "visual_overall": overall,
            "visual_dims_json": json.dumps(dims),
            "visual_blocking": blocking,
        }

    def _candidate(self, overall: float, dims: dict, blocking: str = "") -> dict:
        return {
            "run_id": "vc-tavern-r2-cand",
            "surface": "visual",
            "visual_scene": "fixture:tavern",
            "visual_backend": "unity-cl",
            "visual_overall": overall,
            "visual_dims_json": json.dumps(dims),
            "visual_blocking": blocking,
        }

    def _run(self, cand: dict, base: dict) -> dict:
        """Call detect_visual_regression with a stub that returns our baseline directly."""
        # Patch _visual_baseline to return the baseline dict without hitting a DB.
        original = vr._visual_baseline
        try:
            vr._visual_baseline = lambda scene, backend, db_path: base  # type: ignore[attr-defined]
            return vr.detect_visual_regression(cand, db_path=":memory:")
        finally:
            vr._visual_baseline = original  # type: ignore[attr-defined]

    def test_no_baseline_verdict(self, monkeypatch):
        """When no baseline exists → NO_BASELINE verdict."""
        monkeypatch.setattr(vr, "_visual_baseline", lambda s, b, db: None)
        cand = self._candidate(7.0, {"registration": 7})
        res = vr.detect_visual_regression(cand, db_path=":memory:")
        assert res["verdict"] == "NO_BASELINE"

    def test_overall_regression(self):
        """Overall drop >0.7 → REGRESSED."""
        dims = {"registration": 7, "occlusion_grounding": 7}
        base = self._baseline(8.0, dims)
        cand = self._candidate(7.0, dims)   # drop = -1.0 > OVERALL_FLOOR=0.7
        res = self._run(cand, base)
        assert res["verdict"] == "REGRESSED", f"Expected REGRESSED; got {res['verdict']}: {res}"

    def test_within_noise(self):
        """Overall drop ≤0.7 with no dim regression → WITHIN_NOISE."""
        dims = {"registration": 7, "occlusion_grounding": 7}
        base = self._baseline(8.0, dims)
        cand = self._candidate(7.5, dims)   # drop = -0.5 < OVERALL_FLOOR
        res = self._run(cand, base)
        assert res["verdict"] == "WITHIN_NOISE", f"Expected WITHIN_NOISE; got {res['verdict']}: {res}"

    def test_improved(self):
        """Overall rise >0.7 with no regression → IMPROVED."""
        dims = {"registration": 6}
        base = self._baseline(6.0, dims)
        cand = self._candidate(7.5, {"registration": 7})   # +1.5 overall, +1.0 dim
        res = self._run(cand, base)
        assert res["verdict"] == "IMPROVED", f"Expected IMPROVED; got {res['verdict']}: {res}"

    def test_dim_regression_triggers_regressed(self):
        """A per-dim drop ≥1.0 (DIM_FLOOR) → REGRESSED even if overall is fine."""
        base_dims = {"registration": 8, "occlusion_grounding": 8}
        cand_dims = {"registration": 7, "occlusion_grounding": 6}   # occlusion drops 2.0
        base = self._baseline(8.0, base_dims)
        cand = self._candidate(7.6, cand_dims)   # overall only -0.4 (within noise)
        res = self._run(cand, base)
        assert res["verdict"] == "REGRESSED", (
            f"Per-dim drop of 2.0 should REGRESS even if overall drop is within noise; "
            f"got {res['verdict']}: {res}"
        )

    def test_new_blocking_defect_triggers_regressed(self):
        """A new CRITICAL/HIGH defect id not in the baseline → REGRESSED."""
        dims = {"registration": 8}
        base = self._baseline(8.0, dims, blocking="")
        cand = self._candidate(8.0, dims, blocking="new_float_defect")
        res = self._run(cand, base)
        assert res["verdict"] == "REGRESSED", (
            f"New blocking defect should cause REGRESSED; got {res['verdict']}: {res}"
        )

    def test_baseline_had_same_blocking_not_regressed(self):
        """If the baseline already had a blocking defect and the candidate still has it, no NEW regression."""
        dims = {"registration": 7}
        base = self._baseline(7.5, dims, blocking="old_defect")
        cand = self._candidate(7.5, dims, blocking="old_defect")
        res = self._run(cand, base)
        # No overall/dim regression, no NEW blocking defect → not REGRESSED.
        assert res["verdict"] in ("WITHIN_NOISE", "IMPROVED"), (
            f"Same pre-existing blocking should not trigger REGRESSED; got {res['verdict']}: {res}"
        )

    # --- L7 motion-regression arm (mirrors the still arm; motion_overall drop > 0.7) ---

    def test_motion_overall_regression(self):
        """A motion_overall drop >0.7 → REGRESSED even when the still overall holds."""
        dims = {"registration": 7}
        base = self._baseline(7.5, dims)
        base["motion_overall"] = 8.0
        cand = self._candidate(7.5, dims)   # still overall flat
        cand["motion_overall"] = 6.5        # motion drops 1.5 > 0.7
        res = self._run(cand, base)
        assert res["verdict"] == "REGRESSED", f"motion drop should REGRESS; got {res['verdict']}: {res}"
        assert res["motion_overall"]["classification"] == "REGRESSED"

    def test_motion_overall_within_noise(self):
        """A motion_overall drop ≤0.7 with no still regression → WITHIN_NOISE."""
        dims = {"registration": 7}
        base = self._baseline(7.5, dims)
        base["motion_overall"] = 8.0
        cand = self._candidate(7.5, dims)
        cand["motion_overall"] = 7.5        # -0.5, within the 0.7 floor
        res = self._run(cand, base)
        assert res["verdict"] == "WITHIN_NOISE", f"got {res['verdict']}: {res}"
        assert res["motion_overall"]["classification"] == "WITHIN_NOISE"

    def test_motion_no_data_when_still_only(self):
        """ADDITIVITY: a still-only candidate/baseline (no motion_overall) leaves motion NO_DATA and
        never falsely flags — the still arm is unchanged."""
        dims = {"registration": 7}
        base = self._baseline(7.5, dims)
        cand = self._candidate(7.5, dims)   # identical still metrics; neither carries motion_overall
        res = self._run(cand, base)
        assert res["motion_overall"]["classification"] == "NO_DATA"
        # identical still overall + dims => no regression AND no improvement => deterministically
        # WITHIN_NOISE (the motion arm contributes nothing when both rows lack motion_overall).
        assert res["verdict"] == "WITHIN_NOISE", f"identical still metrics should be WITHIN_NOISE; got {res['verdict']}"
