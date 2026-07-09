"""Red-first regression for the W6.3 paint-drift gate (qa/check_plate_drift.py, #1462).

The eval-blindness the owner called out: a regenerated `canonical_plate` could slide a painted set
piece off its authored cell and NOTHING machine-checked it — that drift is what later reads as "actors
walking over the logs" (the engine's impassable set is keyed to the AUTHORED cells, not the paint).
Before this gate existed, a 2-cell-shifted plate promoted GREEN. These tests pin:
  1. the camp REST fixture's prop cells into the durable manifest (like qa/test_seed_gfx_camp.py pins
     the combat impassable set),
  2. the manifest is regeneratable from the seed (never silently stale),
  3. the KNOWN-GOOD camp plate PASSes, and
  4. a SYNTHETIC 2-cell shift (whole-plate + single-prop) is CAUGHT — the red-before-green.

Deterministic, no LLM. Needs Pillow + numpy (the qa image lane; the engine venv is intentionally
free of them), so this runs under the plain interpreter — the ci.yml `paint-drift-gate` job.
Single-process by construction (no fixtures shared across workers).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

import build_room_manifest as brm  # noqa: E402
import check_plate_drift as drift  # noqa: E402
import seed_gfx_camp as camp  # noqa: E402
from greybox_render_headless import cell_to_world, world_to_screen  # noqa: E402

_CAMP_PLATE = _QA_DIR / "evidence" / "plate-audit" / "camp_clearing_night_v2.jpg"
_CAMP_MANIFEST = _QA_DIR / "room_manifests" / "camp_clearing_night_v2.cells.json"
_CRYPT_MANIFEST = _QA_DIR / "room_manifests" / "crypt_dense_v1.cells.json"


def _manifest(path: Path) -> dict:
    return drift.load_manifest(path)


def _cell_shift_px(cols: int, rows: int, dc: int, dr: int) -> tuple[int, int]:
    """Screen-px delta of a (dc, dr) logical-cell move near grid centre, under the contract camera."""
    c, r = cols // 2, rows // 2
    x0, y0 = world_to_screen(*cell_to_world(c, r, cols, rows))
    x1, y1 = world_to_screen(*cell_to_world(c + dc, r + dr, cols, rows))
    return int(round(x1 - x0)), int(round(y1 - y0))


# ── 1. the manifest pins the authored REST-fixture prop cells ─────────────────────────────────────
def test_camp_manifest_cells_match_rest_fixture_seed():
    """Every camp_clearing_night prop cell in the manifest is exactly a seed_gfx_camp.py authored
    obstacle cell, and vice-versa — the manifest can never silently disagree with the grid the
    engine's impassable set is built from. Grid dims + prop count pinned too."""
    m = _manifest(_CAMP_MANIFEST)
    assert (m["grid"]["cols"], m["grid"]["rows"]) == (camp.GRID_W, camp.GRID_H) == (16, 12)
    manifest_cells = {tuple(c) for p in m["props"] for c in p["cells"]}
    seed_cells = {tuple(c) for c in camp.OBSTACLES}
    assert manifest_cells == seed_cells, "manifest prop cells drifted from the authored seed layout"
    assert len(m["props"]) == 12  # 4 trees + 2 rocks + campfire + 3 bedrolls + log + crate


def test_committed_manifests_match_the_seeds():
    """`build_room_manifest.py` is deterministic: the committed manifests must equal a fresh build from
    the seeds (the --check contract). A stale manifest — e.g. someone edited a seed's prop cell but did
    not regenerate — fails here."""
    for name, fresh in brm._manifests().items():
        committed = _manifest(_QA_DIR / "room_manifests" / f"{name}.cells.json")
        assert committed == fresh, f"{name}.cells.json is stale — re-run qa/build_room_manifest.py"


# ── 2. the known-good plate PASSes (calibration floor) ────────────────────────────────────────────
def test_known_good_camp_plate_passes():
    res = drift.check_plate_drift(_CAMP_PLATE, _manifest(_CAMP_MANIFEST))
    assert res.passed, res.summary()
    assert res.checked == 12 and res.skipped == 0
    nccs = [p["ncc"] for p in res.props if "ncc" in p]
    # Every authored prop is comfortably above NCC_MIN — the calibration margin the gate rests on.
    assert min(nccs) >= 0.90 > drift.NCC_MIN


# ── 3. a SYNTHETIC 2-cell shift is CAUGHT (the red-before-green) ───────────────────────────────────
def test_whole_plate_two_cell_shift_is_caught(tmp_path):
    """Shift the ENTIRE plate by +2 cols +2 rows (a clean synthetic drift) and re-gate against the
    unchanged manifest: every painted prop is now ~2 cells off its authored bbox, so the gate must
    FAIL. This is exactly the drift that shipped green before the gate existed."""
    arr = drift.load_luma(_CAMP_PLATE)
    dx, dy = _cell_shift_px(camp.GRID_W, camp.GRID_H, 2, 2)
    shifted = np.roll(np.roll(arr, dy, axis=0), dx, axis=1)
    out = tmp_path / "camp_shifted.png"
    Image.fromarray(shifted.astype(np.uint8)).save(out)

    res = drift.check_plate_drift(out, _manifest(_CAMP_MANIFEST))
    assert not res.passed, "a 2-cell whole-plate shift must be caught as drift"
    drifted = {p["id"] for p in res.props if p["status"] == "DRIFT"}
    assert len(drifted) >= 10, f"nearly every prop should read as drifted, got {drifted}"


def test_single_prop_move_is_localized(tmp_path):
    """Vacate ONE prop's authored cells (paint floor over the campfire) and re-gate: the gate must
    FAIL and pin the drift to THAT prop while an untouched prop (the log seat) still PASSes — proving
    the gate localizes drift per-cell, not just globally."""
    arr = drift.load_luma(_CAMP_PLATE).copy()
    cols, rows = camp.GRID_W, camp.GRID_H
    fire_bb = [int(round(v)) for v in drift.project_cell_bbox(camp.CAMPFIRE_CELLS, cols, rows)]
    floor_bb = [int(round(v)) for v in drift.project_cell_bbox([[8, 10]], cols, rows)]  # empty walkable cell
    fw = min(fire_bb[2] - fire_bb[0], floor_bb[2] - floor_bb[0])
    fh = min(fire_bb[3] - fire_bb[1], floor_bb[3] - floor_bb[1])
    arr[fire_bb[1]:fire_bb[1] + fh, fire_bb[0]:fire_bb[0] + fw] = \
        arr[floor_bb[1]:floor_bb[1] + fh, floor_bb[0]:floor_bb[0] + fw]
    out = tmp_path / "camp_fire_vacated.png"
    Image.fromarray(arr.astype(np.uint8)).save(out)

    res = drift.check_plate_drift(out, _manifest(_CAMP_MANIFEST))
    assert not res.passed
    by_id = {p["id"]: p["status"] for p in res.props}
    assert by_id["campfire"] == "DRIFT", "the moved prop must be flagged"
    assert by_id["log_seat"] == "PASS", "an untouched prop must not be a false positive"


def test_ncc_min_sits_in_the_calibration_gap():
    """Guard the calibrated threshold: known-good props sit ~0.95+, the synthetic 2-cell shift lands
    at/under ~0.6 — NCC_MIN must stay strictly between so neither margin collapses."""
    assert 0.60 < drift.NCC_MIN < 0.90


# ── 4. geometry-only manifest (crypt) is skipped, never falsely failed ────────────────────────────
def test_geometry_only_manifest_skips_without_baseline():
    """The crypt manifest ships authored geometry but no committed plate/fingerprints. Gating it with
    no baseline must SKIP every prop (checked==0) and stay passed=True — the gate covers what is
    verifiable and never fails on a plate's mere absence. (A contract-sized plate is required only to
    read the pixel frame; the camp plate stands in — its content is irrelevant when every prop skips.)"""
    res = drift.check_plate_drift(_CAMP_PLATE, _manifest(_CRYPT_MANIFEST))
    assert res.passed and res.checked == 0 and res.skipped == 3


def test_gate_room_recipes_covers_camp_and_stays_green():
    """The room_recipes.json canonical_plate gate: it must find the camp room (manifest + committed
    plate), PASS it, and report the crypt as no-plate — overall green."""
    report = drift.gate_room_recipes()
    assert report["passed"], report
    rooms = {r["recipe_key"]: r for r in report["rooms"]}
    assert rooms["camp_clearing_night"].get("passed") is True
    assert rooms["camp_clearing_night"].get("checked") == 12
    assert rooms["crypt"]["status"] == "no-plate"


# ── 5. promote.py room-class hard-floor wiring (tools/library/promote.py) ──────────────────────────
def _promote():
    if str(_QA_DIR.parent / "tools" / "library") not in sys.path:
        sys.path.insert(0, str(_QA_DIR.parent / "tools" / "library"))
    import promote  # noqa: PLC0415
    return promote


def test_promote_drift_gate_noop_without_candidate_plate():
    """Today's room nominations carry no candidate_plate — the drift gate is a non-blocking no-op, so
    the visual-gate path is byte-unchanged (additive)."""
    res = _promote()._paint_drift_gate({"room_ref": {"recipe_key": "camp_clearing_night"}})
    assert res["ran"] is False and res["passed"] is True


def test_promote_drift_gate_passes_known_good_candidate():
    res = _promote()._paint_drift_gate(
        {"candidate_plate": str(_CAMP_PLATE), "room_ref": {"recipe_key": "camp_clearing_night"}})
    assert res["ran"] is True and res["passed"] is True


def test_promote_drift_gate_rejects_drifted_candidate(tmp_path):
    """A room nomination whose candidate_plate slid 2 cells is a HARD rejection in promote.py — the
    eval that #1462 says should have caught the clipping/collision."""
    arr = drift.load_luma(_CAMP_PLATE)
    dx, dy = _cell_shift_px(camp.GRID_W, camp.GRID_H, 2, 2)
    out = tmp_path / "camp_drifted_candidate.png"
    Image.fromarray(np.roll(np.roll(arr, dy, axis=0), dx, axis=1).astype(np.uint8)).save(out)
    res = _promote()._paint_drift_gate(
        {"candidate_plate": str(out), "room_ref": {"recipe_key": "camp_clearing_night"}})
    assert res["ran"] is True and res["passed"] is False and res["reasons"]
