#!/usr/bin/env python3
"""test_journey_visual_sweep.py — deterministic unit tests for the VISUAL JOURNEY instrument's PURE
checks (#1540). No engine, no HTTP, no live viewer: every function here is exercised against synthetic
plates / manifests so the paint-truth logic is pinned independent of the player.

SWEEP-PRECISION additions (this file): the occlusion-exemption tests at the bottom, both synthetic
(resolve_occlusion_cells unit coverage) and RED-FIRST on REAL committed assets:
  * fresh-crypt (qa/evidence/crypt-fresh/crypt_fresh_v1.png + qa/room_manifests/crypt_fresh.cells.json —
    both canonical since #1565 merged): 15 baseline flags on its OWN derived manifest, all 15 fall inside
    authored occlusion -> 0 remain after the fix.
  * tavern_truegrey vs tavern_fit2 (both already committed): a genuinely-invented-furniture NEGATIVE
    control -- cross-referencing a DIFFERENT room generation's occlusion data must not paper over real
    invented furniture; 4 of 5 baseline flags have no authored occlusion anywhere and must still flag.

  uv run --directory servers/engine python -m pytest qa/test_journey_visual_sweep.py -q
  (or plain: python -m pytest qa/test_journey_visual_sweep.py -q  — PIL is the only dependency)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

_QA = Path(__file__).resolve().parent
if str(_QA) not in sys.path:
    sys.path.insert(0, str(_QA))

from journey_visual_sweep import (  # noqa: E402
    cell_floor_quad, cell_silhouette_quad, feet_screen, point_in_quad, cell_edge_density,
    inverse_coherence_flags, reciprocal_door_check, hero_feet_check, chebyshev, build_report,
    resolve_occlusion_cells, load_plate_edges,
)

COLS, ROWS = 12, 10


# ── geometry ────────────────────────────────────────────────────────────────────────────────────────
def test_feet_project_inside_own_floor_quad_for_every_cell():
    # The contract-camera projection of a cell centre must land inside that cell's own floor quad — the
    # registration-basis invariant hero_feet_check leans on. If this ever fails, the camera basis drifted.
    for c in range(COLS):
        for r in range(ROWS):
            assert point_in_quad(feet_screen(c, r, COLS, ROWS), cell_floor_quad(c, r, COLS, ROWS))


def test_point_in_quad_rejects_a_far_point():
    quad = cell_floor_quad(5, 5, COLS, ROWS)
    assert not point_in_quad((0, 0), quad)          # top-left corner of the frame, far from a mid cell
    assert not point_in_quad((5000, 5000), quad)


# ── edge density ──────────────────────────────────────────────────────────────────────────────────
def _edge_field(painted_cells) -> Image.Image:
    """A synthetic BINARY edge mask (mode 'L', 0/255 — the shape a hard-edge mask emits) with every pixel
    inside the given cells' STANDING-SILHOUETTE bands (where the detector samples) set to 255. This
    isolates the density/flag LOGIC from plate-authoring edge-bleed; whether real paint actually produces
    such hard edges is validated by the live run, not this unit test."""
    im = Image.new("L", (1344, 768), 0)
    d = ImageDraw.Draw(im)
    for (c, r) in painted_cells:
        d.polygon(cell_silhouette_quad(c, r, COLS, ROWS), fill=255)
    return im


def test_edge_density_zero_on_flat_floor_high_on_a_painted_object():
    edges = _edge_field([(5, 5)])
    clean = cell_edge_density(edges, cell_silhouette_quad(2, 2, COLS, ROWS))
    painted = cell_edge_density(edges, cell_silhouette_quad(5, 5, COLS, ROWS))
    assert clean < 0.05
    assert painted > 0.9 and painted > clean * 5


# ── inverse coherence (the painted-object detector) ─────────────────────────────────────────────────
def test_inverse_coherence_flags_the_invented_object_and_not_distant_clean_floor():
    # A wide walkable patch; an unauthored "bench" paints a hard silhouette onto ONE cell. The detector
    # must flag that cell (a tall object's band overlaps its immediate neighbours, so a small local
    # CLUSTER flagging is expected + fine — it's a region), and must NOT flag clean floor across the room.
    walkable = [(c, r) for c in range(2, 10) for r in range(2, 8)]
    bench = (8, 3)
    far_clean = (2, 7)
    edges = _edge_field([bench])
    res = inverse_coherence_flags(edges, [list(c) for c in walkable], set(), COLS, ROWS, "synthetic")
    flagged = {tuple(f["cell"]) for f in res.flagged}
    assert bench in flagged, f"the painted bench cell must flag; flagged={flagged}"
    assert far_clean not in flagged, f"distant clean floor must not flag; flagged={flagged}"
    assert len(flagged) <= 8, f"an object should flag a small local cluster, not the room; flagged={flagged}"


def test_inverse_coherence_ignores_authored_prop_cells():
    # A cell the manifest ALREADY authors as a prop footprint is excluded (it's supposed to be painted).
    prop = (5, 5)
    walkable = [(c, r) for c in range(3, 8) for r in range(3, 8)]
    edges = _edge_field([prop])
    res = inverse_coherence_flags(edges, [list(c) for c in walkable], {prop}, COLS, ROWS, "synthetic")
    assert prop not in {tuple(f["cell"]) for f in res.flagged}


# ── reciprocal door ────────────────────────────────────────────────────────────────────────────────
def test_reciprocal_door_pass_when_arrival_is_on_the_return_door():
    doors = [{"cell": [5, 0], "to": "crypt"}, {"cell": [11, 9], "to": "cellar"}]
    res = reciprocal_door_check((5, 1), doors, "crypt", max_cheb=2)
    assert res["pass"] and res["cheb"] == 1


def test_reciprocal_door_fails_when_dumped_across_the_room():
    # crossing crypt->camp today lands the party at the camp spawn, far from the door back to the crypt.
    doors = [{"cell": [5, 0], "to": "crypt"}]
    res = reciprocal_door_check((6, 9), doors, "crypt", max_cheb=2)
    assert not res["pass"] and res["cheb"] == 9 and "across the room" in res["reason"]


def test_reciprocal_door_fails_when_no_door_back_exists():
    res = reciprocal_door_check((4, 4), [{"cell": [1, 1], "to": "elsewhere"}], "crypt", max_cheb=2)
    assert not res["pass"] and "reciprocal door missing" in res["reason"]


def test_chebyshev():
    assert chebyshev((0, 0), (3, 2)) == 3 and chebyshev((6, 9), (5, 0)) == 9


# ── hero position ────────────────────────────────────────────────────────────────────────────────
def test_hero_feet_pass_on_clean_cell():
    res = hero_feet_check((3, 5), COLS, ROWS, flagged_cells=set())
    assert res["pass"] and res["feet_in_quad"] and not res["on_flagged_cell"]


def test_hero_feet_fail_when_standing_on_a_flagged_object_cell():
    res = hero_feet_check((3, 5), COLS, ROWS, flagged_cells={(3, 5)})
    assert not res["pass"] and res["on_flagged_cell"] and "actor-inside-the-object" in res["reason"]


def test_hero_feet_fail_when_no_token():
    assert not hero_feet_check(None, COLS, ROWS, set())["pass"]


# ── CLEAN% aggregation ────────────────────────────────────────────────────────────────────────────
def test_build_report_clean_pct_and_finding_counts():
    room_recs = [{"room": "r1", "plate_status": "resolved", "n_walkable_floor": 10,
                  "flagged_cells": [[6, 3]],  # 1 invented-furniture flag
                  "hero_checks": [{"step": 0, "pass": True}]}]
    steps = [{"step": 0, "kind": "spawn", "room": "r1", "hero_check": {"pass": True}},
             {"step": 1, "kind": "arrive", "room": "r1", "hero_check": {"pass": False}}]
    transitions = [{"from": "r1", "to": "r2", "crossed": True,
                    "reciprocal": {"pass": False, "reason": "across the room"}}]
    rep = build_report(room_recs, steps, transitions, "r1")
    row = rep["per_room"][0]
    # num = hero_pass(1) + clean_cells(10-1=9) = 10 ; den = hero_steps(2) + floor(10) = 12 -> 83.3%
    assert row["clean_pct"] == 83.3 and not row["meets_95"]
    assert rep["findings_by_class"] == {"invented_furniture_flags": 1,
                                        "reciprocal_door_failures": 1, "hero_position_failures": 1}


# ── occlusion resolution (SWEEP-PRECISION) ──────────────────────────────────────────────────────────
def test_resolve_occlusion_cells_exact_match_uses_committed_occlusion():
    # A live prop {id, footprint} straight off manifest_from_surface (no kind, no occlusion — the live
    # scene_grid never carries either). A committed manifest has the SAME id + footprint (the same
    # authored prop) WITH an occlusion list -> that list is used verbatim.
    live_props = [{"id": "pillar_l", "footprint": [[3, 3], [3, 4]]}]
    manifest = {"room": "fixture_room", "props": [
        {"id": "pillar_l", "kind": "stone_pillar", "footprint": [[3, 3], [3, 4]],
         "occlusion": [[3, 3], [3, 4], [3, 2], [4, 2]]},
    ]}
    occ, notes = resolve_occlusion_cells(live_props, COLS, ROWS, manifests=[manifest])
    assert occ == {(3, 3), (3, 4), (3, 2), (4, 2)}
    assert any("fixture_room" in n and "pillar_l" in n for n in notes)


def test_resolve_occlusion_cells_derives_when_matched_manifest_has_no_occlusion_field():
    # The matched prop entry carries a `kind` but no `occlusion` (an older/measured manifest) -- derive
    # on the fly. A tall kind's derived silhouette must be a strict SUPERSET of its own footprint (the
    # occlusion band always contains the footprint plus the up-screen cells its box paints over).
    live_props = [{"id": "sarcophagus", "footprint": [[4, 6], [5, 6]]}]
    manifest = {"room": "fixture_measured", "props": [
        {"id": "sarcophagus", "kind": "sarcophagus", "footprint": [[4, 6], [5, 6]]},  # no "occlusion" key
    ]}
    occ, notes = resolve_occlusion_cells(live_props, 14, 11, manifests=[manifest])
    assert {(4, 6), (5, 6)} <= occ, "derived occlusion must at least contain the footprint"
    assert len(occ) > 2, "a real prop's silhouette should rise off its own footprint, not equal it"
    assert any("DERIVED" in n and "sarcophagus" in n for n in notes)


def test_resolve_occlusion_cells_falls_back_when_no_manifest_matches():
    # No committed manifest has this id+footprint anywhere (a cold room, or a prop whose geometry
    # changed) -> current (pre-#1565) behaviour: no exemption, and the gap is LOGGED, not silent.
    live_props = [{"id": "brand_new_prop", "footprint": [[1, 1]]}]
    occ, notes = resolve_occlusion_cells(live_props, COLS, ROWS, manifests=[])
    assert occ == set()
    assert any("NOT exempted" in n and "brand_new_prop" in n for n in notes)


def test_resolve_occlusion_cells_skips_props_with_no_footprint():
    occ, notes = resolve_occlusion_cells([{"id": "ghost", "footprint": []}], COLS, ROWS, manifests=[])
    assert occ == set() and notes == []


# ── inverse coherence WITH occlusion exemption (SWEEP-PRECISION) ───────────────────────────────────
def test_inverse_coherence_exempts_a_cell_inside_authored_occlusion():
    # Same shape as test_inverse_coherence_flags_the_invented_object_and_not_distant_clean_floor, but
    # the "bench" cell is now inside an AUTHORED prop's occlusion band (a tall prop's silhouette
    # legitimately painting over it) -- it must move to `exempted`, not `flagged`, and CLEAN% must not
    # count it against the room.
    walkable = [(c, r) for c in range(2, 10) for r in range(2, 8)]
    silhouette_cell = (8, 3)
    edges = _edge_field([silhouette_cell])
    res = inverse_coherence_flags(edges, [list(c) for c in walkable], set(), COLS, ROWS, "synthetic",
                                  occlusion_cells={silhouette_cell})
    assert silhouette_cell not in {tuple(f["cell"]) for f in res.flagged}
    assert silhouette_cell in {tuple(f["cell"]) for f in res.exempted}


def test_inverse_coherence_still_flags_invented_furniture_outside_every_occlusion_band():
    # The exemption set is non-empty (some OTHER prop's occlusion), but does not cover the painted
    # object's cell -- genuinely invented furniture (unauthored anywhere) must still flag. This is the
    # #1552 tavern-benches guarantee: occlusion exemption must never become a blanket suppressor.
    walkable = [(c, r) for c in range(2, 10) for r in range(2, 8)]
    invented = (8, 3)
    unrelated_occlusion = {(2, 2), (2, 3)}   # nowhere near the invented cell
    edges = _edge_field([invented])
    res = inverse_coherence_flags(edges, [list(c) for c in walkable], set(), COLS, ROWS, "synthetic",
                                  occlusion_cells=unrelated_occlusion)
    assert invented in {tuple(f["cell"]) for f in res.flagged}
    assert res.exempted == []


def test_inverse_coherence_baseline_unchanged_by_occlusion_cells():
    # occlusion_cells is a POST-HOC partition only -- it must never move the median/MAD baseline itself
    # (co-calibrated against the camp clean-floor anchor pre-#1565; a room where occlusion covers the
    # ENTIRE walkable floor, #1565's fresh crypt, would otherwise leave nothing to calibrate against).
    walkable = [(c, r) for c in range(2, 10) for r in range(2, 8)]
    tall_prop_cell = (8, 3)
    edges = _edge_field([tall_prop_cell])
    with_occ = inverse_coherence_flags(edges, [list(c) for c in walkable], set(), COLS, ROWS, "synthetic",
                                       occlusion_cells={tall_prop_cell})
    without_occ = inverse_coherence_flags(edges, [list(c) for c in walkable], set(), COLS, ROWS, "synthetic")
    assert with_occ.baseline_median == without_occ.baseline_median
    assert with_occ.mad == without_occ.mad
    assert f"{tall_prop_cell[0]},{tall_prop_cell[1]}" in with_occ.densities


# ── RED-FIRST: real committed assets (SWEEP-PRECISION) ──────────────────────────────────────────────
# #1565 (FRESH-CRYPT) merged to main while this PR was in flight — qa/room_manifests/crypt_fresh.cells.json
# and qa/evidence/crypt-fresh/crypt_fresh_v1.png are now the CANONICAL committed assets (no need for a
# PR-scoped fixture copy anymore; verified byte-identical to the pre-merge copies this test used).
_FRESH_CRYPT_PLATE = _QA / "evidence" / "crypt-fresh" / "crypt_fresh_v1.png"
_FRESH_CRYPT_MANIFEST = _QA / "room_manifests" / "crypt_fresh.cells.json"
_TAVERN_TRUEGREY_PLATE = _QA / "evidence" / "new-tavern" / "tavern_truegrey_v1.png"
_TAVERN_TRUEGREY_MANIFEST = _QA / "room_manifests" / "tavern_truegrey.cells.json"
_TAVERN_FIT2_MANIFEST = _QA / "room_manifests" / "tavern_fit2.cells.json"


def _needs(*paths):
    import pytest
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        pytest.skip(f"evidence fixture(s) not present: {missing}")


def test_red_first_fresh_crypt_dense_room_flags_drop_after_occlusion_exemption():
    # THE #1552 FOLLOW-UP + CRYPT-ALIGN-V2 (M-ALIGN): the fresh crypt (17 authored props, richer than the
    # sparse incumbent's 3) scores its OWN derived manifest a batch of baseline flags -- every single one
    # a legitimate authored-occlusion silhouette (a denser room paints more up-screen silhouette, not more
    # invented furniture). Before occlusion-exemption ALL count against CLEAN%; after it, ZERO do. This
    # is now sampled at the manifest's stamped CAMERA-FIT ortho (10.5224) -- the room is painted at the fit
    # scale, so sampling at the fixed 13 would shrink every quad ~0.81x toward centre (the M-ALIGN QA-stack
    # drift this PR fixes). The count re-pinned to the measured 14 under correct fit sampling.
    _needs(_FRESH_CRYPT_PLATE, _FRESH_CRYPT_MANIFEST)
    manifest = json.loads(_FRESH_CRYPT_MANIFEST.read_text(encoding="utf-8"))
    cols, rows = manifest["grid"]["cols"], manifest["grid"]["rows"]
    ortho = float(manifest["ortho"]) if manifest.get("camera_fit") else None
    walkable = manifest["walkable"]
    prop_cells = {(c, r) for p in manifest["props"] for (c, r) in p["footprint"]}
    live_props = [{"id": p["id"], "footprint": p["footprint"]} for p in manifest["props"]]

    edges = load_plate_edges(_FRESH_CRYPT_PLATE)
    baseline = inverse_coherence_flags(edges, walkable, prop_cells, cols, rows, "crypt_fresh_baseline",
                                       ortho=ortho)
    assert len(baseline.flagged) == 14, (
        f"baseline (pre-fix) flag count drifted from the measured 14 (fit ortho): {baseline.flagged}")

    occlusion_cells, notes = resolve_occlusion_cells(live_props, cols, rows, manifests=[manifest],
                                                     preferred=manifest, ortho=ortho)
    assert len(notes) == len(live_props), "every authored prop must resolve (this IS its own manifest)"
    fixed = inverse_coherence_flags(edges, walkable, prop_cells, cols, rows, "crypt_fresh_fixed",
                                    occlusion_cells=occlusion_cells, ortho=ortho)
    assert fixed.flagged == [], (
        f"every fresh-crypt manifest flag is a verified authored-occlusion silhouette; still flagged: {fixed.flagged}")
    assert len(fixed.exempted) == 14, f"expected all 14 baseline flags to move to exempted: {fixed.exempted}"


def test_red_first_genuinely_invented_tavern_furniture_still_flags():
    # THE NEGATIVE CONTROL: the OLD tavern_truegrey plate (painted before the fit2 density-law props
    # existed) evaluated on its OWN grid, with occlusion cross-referenced against tavern_fit2's manifest
    # (a LATER room generation with 8 more props, including benches truegrey's own grid never authored).
    # Per-prop id+footprint matching only pulls in occlusion for the 6 props truegrey and fit2 share
    # VERBATIM (hearth/bar_counter/table_nw/table_ne/table_s/barrels) -- fit2's bench/stool/shelf/cask
    # occlusion can never apply here because no LIVE prop in truegrey's own grid has those ids at all.
    # 4 of the 5 baseline flags have no authored occlusion anywhere and MUST still flag.
    _needs(_TAVERN_TRUEGREY_PLATE, _TAVERN_TRUEGREY_MANIFEST, _TAVERN_FIT2_MANIFEST)
    truegrey = json.loads(_TAVERN_TRUEGREY_MANIFEST.read_text(encoding="utf-8"))
    fit2 = json.loads(_TAVERN_FIT2_MANIFEST.read_text(encoding="utf-8"))
    cols, rows = truegrey["grid"]["cols"], truegrey["grid"]["rows"]
    walkable = truegrey["walkable"]
    prop_cells = {(c, r) for p in truegrey["props"] for (c, r) in p["footprint"]}
    live_props = [{"id": p["id"], "footprint": p["footprint"]} for p in truegrey["props"]]

    edges = load_plate_edges(_TAVERN_TRUEGREY_PLATE)
    baseline = inverse_coherence_flags(edges, walkable, prop_cells, cols, rows, "tavern_truegrey_baseline")
    baseline_cells = {tuple(f["cell"]) for f in baseline.flagged}
    assert baseline_cells == {(2, 2), (2, 1), (3, 1), (7, 1), (1, 3)}, (
        f"baseline (pre-fix) flag set drifted from the measured set: {baseline_cells}")

    # deliberately cross-reference the LATER fit2 manifest -- the mismatched-generation stress case.
    occlusion_cells, _notes = resolve_occlusion_cells(live_props, cols, rows, manifests=[fit2, truegrey])
    fixed = inverse_coherence_flags(edges, walkable, prop_cells, cols, rows, "tavern_truegrey_fixed",
                                    occlusion_cells=occlusion_cells)
    still_flagged = {tuple(f["cell"]) for f in fixed.flagged}
    assert still_flagged == {(2, 2), (2, 1), (3, 1), (1, 3)}, (
        f"genuinely-invented furniture must still flag; got {still_flagged}")
    assert {tuple(f["cell"]) for f in fixed.exempted} == {(7, 1)}, (
        "exactly one baseline flag (inside the shared hearth's occlusion band) should exempt")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
