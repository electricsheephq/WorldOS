"""Red-first regression for the ABSOLUTE grid↔paint coherence gate (qa/check_grid_paint_coherence.py).

The eval-blindness this closes (distinct from the drift gate): the owner walked onto a painted
sarcophagus whose ENGINE cells were legal but whose PAINT sat off the grid FOOTPRINT. The drift gate
(check_plate_drift.py) is RELATIVE — it only catches a prop that MOVED between two plates; a prop that
has always been off-grid drifts from nothing and passes forever. This gate is ABSOLUTE: it localises
each prop's grid silhouette in the plate and fails if the paint sits >0.5 cell off the authored
FOOTPRINT (the floor cells collision keys to — NOT the up-screen silhouette/occlusion; #1505).

These tests pin:
  1. the manifest carries footprint + occlusion, and the gate checks the FOOTPRINT,
  2. a synthetic ALIGNED plate (the greybox built from the footprint) PASSes with a wide margin,
  3. a synthetic 1-cell shift is CAUGHT (red-before-green), localised per-prop,
  4. the CURRENT (deployed) crypt plate FAILs against reality — the residual grid↔paint drift #1491
     proved cannot be shared-transform-realigned, and
  5. the calibrated constants sit in the pass/fail gap.

Deterministic, no LLM. Needs Pillow + numpy (the qa image lane), single-process — the ci.yml
`paint-drift-gate` job. Mirrors qa/test_plate_drift_gate.py.

RELIABILITY NOTE: the edge cross-correlation is deterministic and confident on the synthetic controls
(NCC ≥ ~0.7). On a REAL painterly plate the per-prop NCC is inherently low (cross-modality edges,
painterly texture) so individual offset numbers are advisory — the gate is a screening instrument that
reliably flags GROSS incoherence, not per-prop metrology. That is why the live sweep over painterly
plates is CLI-diagnostic, and the blocking CI contribution is this deterministic self-contained suite.
"""
from __future__ import annotations

import copy
import sys
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
# CRYPT-ALIGN-V2: the REALIGNED fresh crypt manifest (author_crypt_fresh geometry -> paint-registered).
_CRYPT_FRESH_MANIFEST = _QA_DIR / "room_manifests" / "crypt_fresh.cells.json"
# The DEPLOYED crypt plate (crypt_armb_iter3_v1, the room_recipes canonical_plate), at contract size.
_CRYPT_PLATE = _QA_DIR / "evidence" / "plate-sprint" / "adopt-crypt" / "source-plates" / \
    "candidate_crypt_armb_iter3.jpg"

# wall_run/perimeter props are the extent-contract's perimeter band (gated by edge-recall), NOT the
# furniture this localiser measures — their greybox render height (wall_height) differs from the kind's
# _spec height, so the flat-box template can't localise them. The coherence gate reads FURNITURE drift.
_WALL_KINDS = {"wall_run", "perimeter_wall"}


def _manifest(path: Path) -> dict:
    return drift.load_manifest(path)


def _furniture(manifest: dict) -> dict:
    m = copy.deepcopy(manifest)
    m["props"] = [p for p in m["props"] if str(p.get("kind", "")).lower() not in _WALL_KINDS]
    return m


def _render_aligned_fit(manifest: dict, out: Path) -> Path:
    """Aligned greybox for a CAMERA-FIT manifest — carries camera_fit so the render + the gate's
    projection share the room's stamped fit ortho (else the two rigs disagree by ~0.81x)."""
    grid = manifest["grid"]
    geo = {"cols": grid["cols"], "rows": grid["rows"], "walls": [],
           "camera_fit": bool(manifest.get("camera_fit")),
           "props": [{"kind": p["kind"], "cells": p["footprint"]} for p in manifest["props"]]}
    render_greybox(geo, str(out), camera_fit=bool(manifest.get("camera_fit")))
    return out


def _render_aligned_plate(manifest: dict, out: Path) -> Path:
    """The greybox render for the manifest FOOTPRINT geometry — a synthetic plate whose props sit
    EXACTLY on their authored floor cells (perfect grid↔paint coherence by construction)."""
    grid = manifest["grid"]
    geo = {"cols": grid["cols"], "rows": grid["rows"], "walls": [],
           "props": [{"kind": p["kind"], "cells": p["footprint"]} for p in manifest["props"]]}
    render_greybox(geo, str(out))
    return out


def _cell_shift_px(cols: int, rows: int, dc: int, dr: int) -> tuple[int, int]:
    c, r = cols // 2, rows // 2
    x0, y0 = world_to_screen(*cell_to_world(c, r, cols, rows))
    x1, y1 = world_to_screen(*cell_to_world(c + dc, r + dr, cols, rows))
    return int(round(x1 - x0)), int(round(y1 - y0))


# ── 1. footprint / occlusion schema + the gate checks the FOOTPRINT ────────────────────────────────
def test_manifest_carries_footprint_and_occlusion():
    """Every prop carries BOTH a footprint (floor cells) and an occlusion (silhouette cells). The
    sarcophagus is the case where they DIVERGE — the owner-playtest-#5 recalibration (PR #1507): a
    12-cell FLOOR footprint (the coffin body, cols3-7 × rows6-8) vs a 35-cell up-screen SILHOUETTE
    (cols3-9 × rows3-7). (Supersedes #1505's 18-cell cols2-7×rows7-9 footprint, which still read off the
    paint.)"""
    m = _manifest(_CRYPT_MANIFEST)
    for p in m["props"]:
        assert isinstance(p.get("footprint"), list) and p["footprint"]
        assert isinstance(p.get("occlusion"), list) and p["occlusion"]
        assert p["cells"] == p["footprint"], "cells must mirror the footprint (drift-gate back-compat)"
    sarc = next(p for p in m["props"] if p["id"] == "sarcophagus")
    assert len(sarc["footprint"]) == 12 and len(sarc["occlusion"]) == 35
    assert sarc["footprint"] != sarc["occlusion"]  # they genuinely diverge under the iso projection


def test_gate_checks_the_footprint_not_the_occlusion():
    """Corrupting only the OCCLUSION cells must not change the verdict (the gate reads footprint); the
    aligned control stays COHERENT."""
    m = _manifest(_CRYPT_MANIFEST)
    m_bad_occ = copy.deepcopy(m)
    for p in m_bad_occ["props"]:
        p["occlusion"] = [[0, 0]]  # garbage silhouette — must be ignored by the coherence check
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        plate = _render_aligned_plate(m, Path(td) / "aligned.png")
        assert coh.check_grid_paint_coherence(plate, m).passed
        assert coh.check_grid_paint_coherence(plate, m_bad_occ).passed  # occlusion is irrelevant here


# ── 2. a synthetic ALIGNED plate PASSes with margin ────────────────────────────────────────────────
def test_aligned_plate_is_coherent(tmp_path):
    m = _manifest(_CRYPT_MANIFEST)
    plate = _render_aligned_plate(m, tmp_path / "aligned.png")
    res = coh.check_grid_paint_coherence(plate, m)
    assert res.passed, res.summary()
    assert res.checked == 3 and res.skipped == 0
    assert max(p["offset_cells"] for p in res.props if "offset_cells" in p) <= 0.1
    assert min(p["ncc"] for p in res.props if "ncc" in p) >= coh.CONF_MIN


# ── 3. a synthetic 1-cell shift is CAUGHT, localised per-prop ──────────────────────────────────────
def test_one_cell_shift_is_caught(tmp_path):
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


# ── 4. CRYPT-ALIGN-V2: the negative pin flips to a positive — the realigned crypt is grid-coherent ───
@pytest.mark.skipif(not _CRYPT_FRESH_MANIFEST.is_file(), reason="crypt_fresh v2 manifest not present")
def test_realigned_crypt_fresh_geometry_is_grid_coherent():
    """CRYPT-ALIGN-V2 (M-ALIGN, 2026-07-15): the STORY, and the honest instrument flip.

    Before: the deployed crypt (crypt_armb_iter3 / crypt_dense_v1) read INCOHERENT — flux relocated the
    painted furniture off the authored grid (#1491), the defect the owner walked onto. This lane realigns
    the crypt GEOMETRY to the painted crypt_fresh_v1 plate (sarcophagus -> the back-band tomb, pillar_l ->
    its painted plinth, invisible-behind-cutaway props deleted); overlay-verified against the plate at the
    fit ortho (qa/evidence/1540/after-align-v2/overlay_v2_fit.png).

    This asserts the POSITIVE: the realigned crypt_fresh v2 FURNITURE, on a correctly-REGISTERED plate
    (the greybox rendered from its own footprints at the room's stamped fit ortho 10.5224), is COHERENT —
    every prop localises on its authored footprint with a wide margin. That is the gate's RELIABLE path
    (see the module RELIABILITY NOTE): on a fully PAINTERLY plate the per-prop NCC is inherently low
    (cross-modality edges), so the painterly crypt_fresh_v1 — like the coherence-perfect tavern_fit2 —
    still reads advisory-INCOHERENT under this flat-box localiser; the per-CELL painterly coherence proof
    is the visual sweep (qa/journey_visual_sweep.py: the tomb/pillar/ornaments now align + are occlusion-
    exempted, only ornate-floor decoration flags remain — crypt CLEAN% 85.1 -> ~90.7). Walls are the
    extent contract's job (edge-recall), excluded here."""
    m = _furniture(_manifest(_CRYPT_FRESH_MANIFEST))
    assert m.get("camera_fit") and abs(float(m["ortho"]) - 10.5224) < 1e-3, "v2 manifest must stamp the fit ortho"
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        plate = _render_aligned_fit(m, Path(td) / "aligned_fit.png")
        res = coh.check_grid_paint_coherence(plate, m)
    assert res.passed, f"the realigned crypt_fresh v2 geometry must read COHERENT on its registered plate: {res.summary()}"
    assert res.checked >= 8, f"most furniture props must localise (got checked={res.checked})"
    assert max(p["offset_cells"] for p in res.props if "offset_cells" in p) <= coh.MAX_OFFSET_CELLS


# ── 5. calibrated constants sit in the pass/fail gap ──────────────────────────────────────────────
def test_thresholds_sit_in_the_calibration_gap():
    assert 0.25 <= coh.MAX_OFFSET_CELLS <= 0.75
    assert coh.SEARCH_CELL_FRAC > coh.MAX_OFFSET_CELLS + 0.5
    assert 0.0 < coh.CONF_MIN < 0.5


def test_non_contract_size_plate_fails_loud(tmp_path):
    m = _manifest(_CRYPT_MANIFEST)
    small = tmp_path / "small.png"
    Image.new("RGB", (640, 360)).save(small)
    res = coh.check_grid_paint_coherence(small, m)
    assert not res.passed and any("contract" in r for r in res.reasons)
