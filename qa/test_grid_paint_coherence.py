"""Red-first regression for the ABSOLUTE grid<->paint coherence gate (qa/check_grid_paint_coherence.py).

The eval-blindness this closes (distinct from the drift gate): the owner walked onto a painted
sarcophagus whose ENGINE cells were legal but whose PAINT sat ~3/4 cell off the grid footprint. The
drift gate (check_plate_drift.py) is RELATIVE — it only catches a prop that MOVED between two plates; a
prop that has always been off-grid drifts from nothing and passes forever. This gate is ABSOLUTE: it
localises each authored prop's grid silhouette in the plate and fails if the paint sits >0.5 cell off
the authored footprint, using the greybox (regenerated from the manifest geometry) as the ground truth.

These tests pin:
  1. a synthetic ALIGNED plate (the greybox itself) PASSes with a wide margin (calibration floor),
  2. a synthetic 1-cell shift is CAUGHT (red-before-green), localised per-prop,
  3. the CURRENT crypt plate FAILs against reality (the red-first-against-reality anchor — the plate the
     player-alignment lane measured at 0.708 registration), and
  4. the calibrated constants sit in the pass/fail gap.

Deterministic, no LLM. Needs Pillow + numpy (the qa image lane), single-process — the ci.yml
`paint-drift-gate` job. Mirrors qa/test_plate_drift_gate.py.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

import check_grid_paint_coherence as coh  # noqa: E402
import check_plate_drift as drift  # noqa: E402
from greybox_render_headless import cell_to_world, render as render_greybox, world_to_screen  # noqa: E402

_CRYPT_MANIFEST = _QA_DIR / "room_manifests" / "crypt_dense_v1.cells.json"
# The plate the player-alignment lane measured at 0.708 registration (left half shifted off the greybox).
_CRYPT_PLATE = _QA_DIR / "evidence" / "1469" / "iter3" / "plate_conditioned_crypt.png"


def _manifest(path: Path) -> dict:
    return drift.load_manifest(path)


def _render_aligned_plate(manifest: dict, out: Path) -> Path:
    """The greybox render for the manifest geometry — a synthetic plate whose props sit EXACTLY on their
    authored cells (perfect grid<->paint coherence by construction)."""
    grid = manifest["grid"]
    geo = {"cols": grid["cols"], "rows": grid["rows"], "walls": [],
           "props": [{"kind": p["kind"], "cells": p["cells"]} for p in manifest["props"]]}
    render_greybox(geo, str(out))
    return out


def _cell_shift_px(cols: int, rows: int, dc: int, dr: int) -> tuple[int, int]:
    c, r = cols // 2, rows // 2
    x0, y0 = world_to_screen(*cell_to_world(c, r, cols, rows))
    x1, y1 = world_to_screen(*cell_to_world(c + dc, r + dr, cols, rows))
    return int(round(x1 - x0)), int(round(y1 - y0))


# ── 1. a synthetic ALIGNED plate PASSes with margin ────────────────────────────────────────────────
def test_aligned_plate_is_coherent(tmp_path):
    m = _manifest(_CRYPT_MANIFEST)
    plate = _render_aligned_plate(m, tmp_path / "aligned.png")
    res = coh.check_grid_paint_coherence(plate, m)
    assert res.passed, res.summary()
    assert res.checked == 3 and res.skipped == 0
    offsets = [p["offset_cells"] for p in res.props if "offset_cells" in p]
    assert max(offsets) <= 0.1, f"aligned props must localise on-cell, got {offsets}"
    nccs = [p["ncc"] for p in res.props if "ncc" in p]
    assert min(nccs) >= coh.CONF_MIN, f"aligned props must localise confidently, got {nccs}"


# ── 2. a synthetic 1-cell shift is CAUGHT, localised per-prop ──────────────────────────────────────
def test_one_cell_shift_is_caught(tmp_path):
    """Shift the whole aligned plate by +1 col +1 row: every painted prop is now ~1 cell off its
    authored footprint, so the gate must FAIL with props reading DRIFT/UNLOCATED (both are coherence
    failures). This is exactly the class the sarcophagus incident belongs to."""
    m = _manifest(_CRYPT_MANIFEST)
    aligned = _render_aligned_plate(m, tmp_path / "aligned.png")
    arr = np.asarray(Image.open(aligned).convert("L"), dtype=np.float32)
    dx, dy = _cell_shift_px(m["grid"]["cols"], m["grid"]["rows"], 1, 1)
    shifted = np.roll(np.roll(arr, dy, axis=0), dx, axis=1)
    out = tmp_path / "shifted.png"
    Image.fromarray(shifted.astype(np.uint8)).convert("RGB").resize((coh.PX_W, coh.PX_H)).save(out)

    res = coh.check_grid_paint_coherence(out, m)
    assert not res.passed, "a 1-cell whole-plate shift must be caught as incoherent"
    bad = {p["id"] for p in res.props if p["status"] in ("DRIFT", "UNLOCATED")}
    assert len(bad) >= 2, f"most props should read off-grid, got {[(p['id'], p['status']) for p in res.props]}"


# ── 3. the CURRENT crypt plate FAILs against reality (red-first-against-reality) ───────────────────
@pytest.mark.skipif(not _CRYPT_PLATE.is_file(), reason="crypt evidence plate not committed locally")
def test_current_crypt_plate_is_incoherent():
    """The honest reality anchor: the shipped crypt plate the player-alignment lane measured at 0.708
    registration MUST fail this gate — the left half (pillar_l/pillar_r) drifted >0.5 cell off the
    authored cells. This is the defect the owner walked onto, now machine-caught."""
    res = coh.check_grid_paint_coherence(_CRYPT_PLATE, _manifest(_CRYPT_MANIFEST))
    assert not res.passed, f"the current crypt paint must read INCOHERENT: {res.summary()}"
    off_grid = {p["id"] for p in res.props if p["status"] in ("DRIFT", "UNLOCATED")}
    assert off_grid, "at least one prop must be flagged off-grid on the real crypt"


# ── 4. calibrated constants sit in the pass/fail gap ──────────────────────────────────────────────
def test_thresholds_sit_in_the_calibration_gap():
    """Guard the calibration: the fail threshold is a strict fraction of a cell, the search window is
    wide enough to MEASURE a ~1-cell drift, and the confidence floor stays low (edge-NCC is inherently
    low-magnitude cross-modality) but positive."""
    assert 0.25 <= coh.MAX_OFFSET_CELLS <= 0.75
    assert coh.SEARCH_CELL_FRAC > coh.MAX_OFFSET_CELLS + 0.5  # can localise a drift past the threshold
    assert 0.0 < coh.CONF_MIN < 0.5


def test_non_contract_size_plate_fails_loud(tmp_path):
    """A plate that is not the contract 1344x768 cannot be reprojection-checked (the bboxes live in that
    frame) — the gate fails loud rather than silently mis-aligning."""
    m = _manifest(_CRYPT_MANIFEST)
    small = tmp_path / "small.png"
    Image.new("RGB", (640, 360)).save(small)
    res = coh.check_grid_paint_coherence(small, m)
    assert not res.passed and any("contract" in r for r in res.reasons)
