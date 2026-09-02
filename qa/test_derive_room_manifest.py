"""Regression for the greybox-geometry -> manifest DERIVATION (tools/derive_room_manifest.py).

Owner playtest #5 architecture decision: manifests are DERIVED from greybox geometry (the single source
of truth for footprint + occlusion + walkable), not hand-authored. These tests pin the derivation math
and close the loop — the derived forest_road manifest is COHERENT against the very greybox its geometry
describes:
  1. occlusion strictly CONTAINS the footprint, and for a tall prop it is strictly LARGER (the silhouette
     rises up-screen off the floor footprint — the point-in-polygon derivation, #1505 generalised),
  2. the walkable set excludes every prop footprint,
  3. the forest_road manifest is REGENERATABLE (a fresh derivation equals the committed file),
  4. the coherence gate reads the derived manifest COHERENT against the committed forest_road greybox.

Deterministic, no LLM. Needs Pillow + numpy (the qa image lane) only for the coherence-gate assertion.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_QA_DIR = Path(__file__).resolve().parent
_ROOT = _QA_DIR.parent
for _p in (_QA_DIR, _ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import derive_room_manifest as drm  # noqa: E402

_FOREST_GEO = _QA_DIR / "evidence" / "plate-sprint" / "forest-road" / "forest_road_geometry.json"
_FOREST_MANIFEST = _QA_DIR / "room_manifests" / "forest_road.cells.json"
_FOREST_GREYBOX = _QA_DIR / "evidence" / "plate-sprint" / "forest-road" / "forest_road_greybox.png"


def _geo() -> dict:
    return json.loads(_FOREST_GEO.read_text(encoding="utf-8"))


def _derived() -> dict:
    return drm.derive_manifest(_geo(), room="forest_road", recipe_key="forest_road",
                               source_geometry=str(_FOREST_GEO))


# ── 1. occlusion contains the footprint; a tall prop's occlusion is strictly larger ────────────────
def test_occlusion_contains_footprint_and_rises_for_tall_props():
    m = _derived()
    assert m["derivation"] == "derived" and m["source_geometry"]
    tall_bigger = 0
    for p in m["props"]:
        fp = {tuple(c) for c in p["footprint"]}
        occ = {tuple(c) for c in p["occlusion"]}
        assert fp <= occ, f"{p['id']} occlusion must contain its footprint"
        if p["kind"] == "large_tree" and len(occ) > len(fp):
            tall_bigger += 1
    assert tall_bigger >= 1, "a tall prop's silhouette must extend beyond its floor footprint"


# ── 2. walkable excludes every footprint ────────────────────────────────────────────────────────────
def test_walkable_excludes_footprints():
    m = _derived()
    walkable = {tuple(c) for c in m["walkable"]}
    assert walkable, "walkable set must be non-empty"
    for p in m["props"]:
        for cell in p["footprint"]:
            assert tuple(cell) not in walkable, f"{p['id']} footprint cell {cell} must be non-walkable"


# ── 3. the committed forest_road manifest is REGENERATABLE ──────────────────────────────────────────
def test_forest_road_manifest_is_regeneratable():
    committed = json.loads(_FOREST_MANIFEST.read_text(encoding="utf-8"))
    fresh = json.loads(json.dumps(_derived()))  # normalise tuple/list for comparison
    assert committed == fresh, "forest_road.cells.json is stale — re-run tools/derive_room_manifest.py"


# ── 4. loop closed: the derived manifest is COHERENT against its own greybox ────────────────────────
def test_derived_manifest_is_coherent_against_its_greybox():
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    import check_grid_paint_coherence as coh  # noqa: PLC0415
    import check_plate_drift as drift  # noqa: PLC0415
    res = coh.check_grid_paint_coherence(_FOREST_GREYBOX, drift.load_manifest(_FOREST_MANIFEST))
    assert res.passed, res.summary()
    off = [p["id"] for p in res.props if p["status"] in ("DRIFT", "UNLOCATED")]
    assert not off, f"the greybox the geometry describes must be coherent with the derived manifest: {off}"
