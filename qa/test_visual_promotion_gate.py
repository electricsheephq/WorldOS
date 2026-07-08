#!/usr/bin/env python3
"""test_visual_promotion_gate.py — the VISUAL promotion gate (GATE_STRATEGIES["room"]="visual").

Pins the delta-anchored visual gate ratified in docs/roadmap/VISUAL-PROMOTION-GATE-DECISION.md:
  * PASS iff pre-gate HARD FLOOR PASSes + a REGISTERED in-band control + candidate-vs-control delta
    >= -noise_law — with NO absolute threshold.
  * a daylight plate's G6 staging-law FLAG is NOT a promotion floor (the load-bearing design point).
  * the strategy dispatch leaves the TEXT path byte-identical (a text nomination still uses the DB
    threshold gate); the shared control-band helper is byte-identical on the 1-5 scale.
  * the registry builder is deterministic and excludes the disclosed-defective frame.

All offline: fixture panel dicts + a fixture registry; no live scorer, no image decode.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

_QA_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _QA_DIR.parent
sys.path.insert(0, str(_QA_DIR))
sys.path.insert(0, str(_REPO_ROOT / "tools" / "library"))

import scores_db  # noqa: E402
import promote  # noqa: E402  (tools/library/promote.py)
import control_band as cb  # noqa: E402
import build_artifact_controls  # noqa: E402
import build_visual_controls  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────────────────────────────
REGISTRY = {
    "noise_law": 1.2, "scale_max": 10.0,
    "controls": {
        "control:visual:poe2:market": {
            "class": "room", "world": "reference", "anchor": 8.0, "band": [6.8, 9.2],
            "file": "poe2_market_interior_lighting_04.jpg",
        },
    },
}


def _floor_pass_pregate() -> dict:
    return {"verdict": "PASS", "gates": [
        {"gate": "G1_frame_lit", "severity": "PASS", "value": 0.45}]}


def _daylight_g6_only_pregate() -> dict:
    # The real market_square case: only G6 (stylistic staging-law) fires on a bright daylight plaza;
    # the promotion hard floor still PASSes. run_pregates' overall verdict is FLAG, but G6 is not a floor.
    return {"verdict": "FLAG", "gates": [
        {"gate": "G1_frame_lit", "severity": "PASS", "value": 0.46},
        {"gate": "G6_luma_staging_law", "severity": "HIGH", "value": {"lit_frac": 0.85}}]}


def _floor_fail_pregate() -> dict:
    return {"verdict": "FLAG", "gates": [
        {"gate": "G1_frame_lit", "severity": "CRITICAL", "value": 0.01}]}


def _panel(*, control_id="control:visual:poe2:market", control_median=8.0, candidate_median=7.5,
           delta="derive", pregate="floor_pass") -> dict:
    p = {
        "room": "test_room", "panel_id": "panel-test",
        "control_id": control_id, "control_median": control_median,
        "candidate_median": candidate_median,
        "scores": [{"scorer": i, "control": control_median, "candidate": candidate_median}
                   for i in range(1, 6)],
    }
    if delta != "derive":
        p["delta_candidate_minus_control"] = delta
    p["pregate"] = {"floor_pass": _floor_pass_pregate(), "g6_only": _daylight_g6_only_pregate(),
                    "floor_fail": _floor_fail_pregate(), None: None}.get(pregate, pregate)
    return p


def _gate(panel):
    return promote.evaluate_visual_gate(panel, registry=REGISTRY, noise_law=REGISTRY["noise_law"])


# ── the gate: PASS / FAIL / no-control / pre-gate-fail ─────────────────────────────────────────────
def test_visual_gate_passes_floor_registered_and_in_delta():
    g = _gate(_panel(candidate_median=7.5, control_median=8.0))  # delta -0.5 >= -1.2
    assert g.passed is True and g.tier == "stable" and g.control_valid is True
    assert g.reasons == []


def test_visual_gate_daylight_g6_flag_is_not_a_promotion_floor():
    """LOAD-BEARING: a bright daylight plate FLAGs run_pregates on G6 staging-law only; the promotion
    hard floor (G1 + occupancy + pin) still PASSes, so the gate does not reject on the floor."""
    g = _gate(_panel(candidate_median=7.5, control_median=8.0, pregate="g6_only"))
    assert g.passed is True, g.reasons


def test_visual_gate_rejects_delta_below_noise_law():
    g = _gate(_panel(candidate_median=4.0, control_median=9.0))  # delta -5.0 < -1.2
    assert g.passed is False and g.tier is None
    assert any("delta" in r for r in g.reasons)


def test_visual_gate_rejects_delta_just_below_threshold():
    g = _gate(_panel(candidate_median=6.79, control_median=8.0))  # delta -1.21 < -1.2
    assert g.passed is False
    g_ok = _gate(_panel(candidate_median=6.8, control_median=8.0))  # delta -1.2 == -1.2 → OK
    assert g_ok.passed is True


def test_visual_gate_rejects_unregistered_control():
    g = _gate(_panel(control_id="control:visual:poe2:NOT_REGISTERED"))
    assert g.passed is False and g.control_valid is False
    assert any("not in the visual control registry" in r for r in g.reasons)


def test_visual_gate_rejects_missing_control():
    p = _panel()
    p.pop("control_id")
    g = _gate(p)
    assert g.passed is False
    assert any("not control-anchored" in r for r in g.reasons)


def test_visual_gate_rejects_out_of_band_control_instrument_invalid():
    """The #1416 defect: a registered control that scored anomalously (2.0, far below its band) means
    the instrument was not valid this panel — reject even a candidate that beats it on delta."""
    g = _gate(_panel(control_median=2.0, candidate_median=6.0))  # delta +4.0 but control out of band
    assert g.passed is False and g.control_valid is False
    assert any("outside its band" in r for r in g.reasons)


def test_visual_gate_rejects_missing_pregate():
    g = _gate(_panel(pregate=None))
    assert g.passed is False
    assert any("pre-gate" in r for r in g.reasons)


def test_visual_gate_rejects_hard_floor_failure():
    g = _gate(_panel(pregate="floor_fail"))
    assert g.passed is False
    assert any("hard floor" in r for r in g.reasons)


def test_visual_gate_derives_delta_from_medians_when_absent():
    p = _panel(candidate_median=7.0, control_median=8.0, delta="derive")
    assert "delta_candidate_minus_control" not in p
    g = _gate(p)
    assert g.passed is True  # derived delta -1.0 >= -1.2
    assert g.dims["delta_candidate_minus_control"] == pytest.approx(-1.0)


def test_visual_gate_no_absolute_threshold():
    """A LOW absolute candidate median still PASSes if it is within the noise law of the control —
    the visual gate never applies an absolute floor (unlike the text gate's overall>=4.0)."""
    g = _gate(_panel(candidate_median=3.0, control_median=4.0))  # both low, but control also low & in-band? no
    # control 4.0 is BELOW the fixture band [6.8,9.2] → instrument invalid; use an in-band low-delta case:
    g2 = _gate(_panel(candidate_median=6.9, control_median=8.0))  # delta -1.1, absolute 6.9 (< text 4-scale n/a)
    assert g2.passed is True


# ── end-to-end promote_batch dispatch ──────────────────────────────────────────────────────────────
def _write_panel(tmp_path: Path, panel: dict) -> Path:
    p = tmp_path / "panel.json"
    p.write_text(json.dumps(panel), encoding="utf-8")
    return p


def test_promote_batch_routes_room_to_visual_gate_and_writes_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(promote, "load_visual_registry", lambda *a, **k: REGISTRY)
    lib, noms = tmp_path / "library", tmp_path / "noms.jsonl"
    panel_path = _write_panel(tmp_path, _panel(candidate_median=7.5, control_median=8.0))
    noms.write_text(json.dumps({
        "artifact_id": "room:test:v1", "class": "room", "source_path": str(panel_path),
        "room_ref": {"recipe_key": "market", "asset_ids": ["asset_x"]},
        "curation_note": "visual-gate e2e",
    }) + "\n", encoding="utf-8")

    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=tmp_path / "s.db")
    assert rep["promoted"] == 1 and rep["rejected"] == 0
    entry = json.loads(next((lib / "rooms").glob("*.json")).read_text())
    assert entry["class"] == "room" and entry["tier"] == "stable"
    assert entry["room_ref"] == {"recipe_key": "market", "asset_ids": ["asset_x"]}
    assert entry["scores"]["overall"] == 7.5  # candidate median, carried for provenance only


def test_promote_batch_room_reject_writes_no_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(promote, "load_visual_registry", lambda *a, **k: REGISTRY)
    lib, noms = tmp_path / "library", tmp_path / "noms.jsonl"
    panel_path = _write_panel(tmp_path, _panel(candidate_median=4.0, control_median=9.0))  # delta -5.0
    noms.write_text(json.dumps({
        "artifact_id": "room:test:bad", "class": "room", "source_path": str(panel_path),
        "room_ref": {"recipe_key": "market", "asset_ids": ["asset_x"]},
    }) + "\n", encoding="utf-8")
    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=tmp_path / "s.db")
    assert rep["promoted"] == 0 and rep["rejected"] == 1
    assert not (lib / "rooms").exists() or not list((lib / "rooms").glob("*.json"))


def test_promote_batch_room_missing_panel_is_score_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(promote, "load_visual_registry", lambda *a, **k: REGISTRY)
    lib, noms = tmp_path / "library", tmp_path / "noms.jsonl"
    noms.write_text(json.dumps({
        "artifact_id": "room:test:nopanel", "class": "room",
        "source_path": str(tmp_path / "does_not_exist.json"),
    }) + "\n", encoding="utf-8")
    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=tmp_path / "s.db")
    assert rep["skipped"] == 1 and rep["promoted"] == 0
    assert rep["details"][0]["verdict"] == "score-failed"


def test_dispatch_leaves_text_path_working_alongside_a_room(tmp_path, monkeypatch):
    """The strategy dispatch must not disturb the TEXT path: a quest nomination in the SAME batch as a
    room nomination still promotes through the DB threshold gate."""
    monkeypatch.setattr(promote, "load_visual_registry", lambda *a, **k: REGISTRY)
    db = tmp_path / "s.db"
    lib, noms = tmp_path / "library", tmp_path / "noms.jsonl"
    scores_db.add_artifact("ctrl:quest:cal", db_path=db, **{"class": "quest"}, overall=4.0,
                           dims_json={"d": 4.0}, panel_id="cal", is_control=1, control_anchor=4.0)
    scores_db.add_artifact("quest:bg:q1", db_path=db, **{"class": "quest"}, overall=4.4,
                           dims_json={"clarity": 4}, panel_id="cal", is_control=0)
    panel_path = _write_panel(tmp_path, _panel(candidate_median=7.5, control_median=8.0))
    noms.write_text("\n".join([
        json.dumps({"artifact_id": "quest:bg:q1"}),
        json.dumps({"artifact_id": "room:test:v1", "class": "room", "source_path": str(panel_path),
                    "room_ref": {"recipe_key": "market", "asset_ids": ["a"]}}),
    ]) + "\n", encoding="utf-8")
    rep = promote.promote_batch(library_dir=lib, nominations_path=noms, db_path=db)
    assert rep["promoted"] == 2
    assert json.loads(next((lib / "quests").glob("*.json")).read_text())["class"] == "quest"
    assert json.loads(next((lib / "rooms").glob("*.json")).read_text())["class"] == "room"


def test_strategy_router_defaults_text_for_classless_nomination():
    assert promote._strategy_for({"artifact_id": "x"}) == "text"
    assert promote._strategy_for({"artifact_id": "x", "class": "quest"}) == "text"
    assert promote._strategy_for({"artifact_id": "x", "class": "room"}) == "visual"


def test_visual_registry_absent_fails_closed(tmp_path):
    reg = promote.load_visual_registry(tmp_path / "no_such.json")
    assert reg["controls"] == {}
    g = promote.evaluate_visual_gate(_panel(), registry=reg, noise_law=1.2)
    assert g.passed is False  # no control can be registered → fail closed


# ── shared band helper: TEXT byte-identity + scale-parametrization ─────────────────────────────────
def test_control_band_text_scale_byte_identical():
    # The exact expression build_artifact_controls used inline before extraction.
    assert cb.control_band(4.0, scale_max=5.0) == [round(4.0 - 1.2, 1), round(min(5.0, 4.0 + 1.2), 1)]
    assert cb.control_band(4.0, scale_max=5.0) == [2.8, 5.0]


def test_control_band_scale_parametrized_for_visual():
    assert cb.control_band(8.0, scale_max=10.0) == [6.8, 9.2]
    # cap engages at the scale ceiling:
    assert cb.control_band(9.5, scale_max=10.0) == [8.3, 10.0]
    assert cb.control_band(4.5, scale_max=5.0) == [3.3, 5.0]


def test_text_control_bands_unchanged_after_refactor():
    """Regression: build_artifact_controls.build() still writes the a-priori [2.8, 5.0] band for every
    control (anchor 4.0 on the 1-5 scale) — the shared-helper extraction changed no bytes."""
    controls, identity = build_artifact_controls.build("baldurs-gate", Path(tempfile.mkdtemp()))
    bands = {tuple(v["band"]) for v in identity["controls"].values()}
    assert bands == {(2.8, 5.0)}
    assert all(v["anchor"] == 4.0 for v in identity["controls"].values())


# ── visual registry builder: determinism + exclusion + 0-10 bands ──────────────────────────────────
def test_visual_registry_builder_deterministic():
    r1 = build_visual_controls.build(build_visual_controls.DEFAULT_REFS_DIR)
    r2 = build_visual_controls.build(build_visual_controls.DEFAULT_REFS_DIR)
    # Ignore reference_frame_present (depends on whether LEXAR is mounted) — the identity is intrinsic.
    for r in (r1, r2):
        for c in r["controls"].values():
            c.pop("reference_frame_present", None)
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_visual_registry_excludes_defective_frame():
    r = build_visual_controls.build(build_visual_controls.DEFAULT_REFS_DIR)
    files = {c["file"] for c in r["controls"].values()}
    assert "bg2ee_fortress_party_tactical_02.jpg" not in files
    assert "bg2ee_fortress_party_tactical_02.jpg" in r["excluded"]


def test_visual_registry_bands_are_0_10_scale():
    r = build_visual_controls.build(build_visual_controls.DEFAULT_REFS_DIR)
    assert r["scale_max"] == 10.0 and r["noise_law"] == 1.2
    for c in r["controls"].values():
        assert c["band"] == cb.control_band(c["anchor"], noise=1.2, scale_max=10.0)
        assert c["band"][1] <= 10.0 and c["class"] == "room"
    assert len(r["controls"]) == 12  # 13 frames minus the 1 excluded defective


def test_committed_visual_registry_matches_builder():
    """The committed qa/visual_controls_identity.json is exactly what the builder produces (no drift)."""
    committed = json.loads((_QA_DIR / "visual_controls_identity.json").read_text())
    built = build_visual_controls.build(build_visual_controls.DEFAULT_REFS_DIR)
    # Compare the intrinsic control identity (drop the mount-dependent presence flag).
    def _strip(reg):
        reg = json.loads(json.dumps(reg))
        for c in reg["controls"].values():
            c.pop("reference_frame_present", None)
        return reg
    assert _strip(committed)["controls"] == _strip(built)["controls"]
