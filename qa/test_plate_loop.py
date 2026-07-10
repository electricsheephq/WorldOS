"""Unit tests for qa/plate_loop.py — the plate-sprint harness (NO API calls, tiny synthetic images).

Covers the three deterministic surfaces the module owns:
  1. REGISTRATION GATE MATH — edge-alignment recall passes on an identical plate and fails on a plate
     that dropped the greybox structure (the >=0.95 gate); the shared plate_overlays primitives.
  2. GALLERY APPEND IDEMPOTENCE — upserting the same run id twice yields ONE row (never a duplicate),
     newest-first ordering holds, and the HTML re-renders.
  3. CONFIG PARSING — the JSON schema -> PlateConfig -> generate_room.py argv (incl. the forward-looking
     style_pass passthrough), plus the missing-room fail-loud.

Deterministic, single-process. Needs Pillow (the qa image lane); numpy is only touched by the optional
NCC drift path, which these tests do not exercise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

_QA_DIR = Path(__file__).resolve().parent
if str(_QA_DIR) not in sys.path:
    sys.path.insert(0, str(_QA_DIR))

import plate_loop as pl  # noqa: E402
import plate_overlays as po  # noqa: E402


# ── fixtures ──────────────────────────────────────────────────────────────────────────────────────
def _structured(path: Path, size=(128, 96)) -> Path:
    """A tiny image with strong structural edges (a greybox-like frame: rectangle + diagonal)."""
    im = Image.new("RGB", size, (20, 20, 26))
    d = ImageDraw.Draw(im)
    d.rectangle([12, 10, size[0] - 12, size[1] - 10], outline=(210, 210, 210), width=3)
    d.line([12, 10, size[0] - 12, size[1] - 10], fill=(230, 230, 230), width=3)
    d.line([size[0] - 12, 10, 12, size[1] - 10], fill=(180, 180, 180), width=3)
    im.save(path)
    return path


def _flat(path: Path, size=(128, 96)) -> Path:
    """A near-flat image: NO structural edges (the greybox structure was dropped / outpainted)."""
    Image.new("RGB", size, (40, 40, 46)).save(path)
    return path


# ── 1. registration gate math ───────────────────────────────────────────────────────────────────
def test_recall_identical_plate_is_high(tmp_path):
    grey = _structured(tmp_path / "greybox.png")
    r = po.registration_recall(grey, grey, size=(128, 96))
    assert r >= 0.95, f"identical plate should register near-perfectly, got {r}"


def test_recall_flat_plate_fails_gate(tmp_path):
    grey = _structured(tmp_path / "greybox.png")
    flat = _flat(tmp_path / "flat.png")
    r = po.registration_recall(grey, flat, size=(128, 96))
    assert r < 0.95, f"a structure-dropped plate must miss the 0.95 gate, got {r}"


def test_registration_gate_pass_and_fail(tmp_path):
    grey = _structured(tmp_path / "greybox.png")
    good = _structured(tmp_path / "good.png")
    bad = _flat(tmp_path / "bad.png")
    cfg = pl.PlateConfig(name="t", room="crypt", registration={"greybox": str(grey), "min_recall": 0.95})

    gpass = pl.registration_gate(good, cfg)
    assert gpass["recall_pass"] is True and gpass["passed"] is True
    assert gpass["drift"] is None  # no manifest configured -> NCC skipped, does not block

    gfail = pl.registration_gate(bad, cfg)
    assert gfail["recall_pass"] is False and gfail["passed"] is False


def test_registration_gate_ungated_without_greybox(tmp_path):
    """No greybox/base_plate -> recall cannot be measured; the gate must NOT falsely pass."""
    plate = _structured(tmp_path / "p.png")
    cfg = pl.PlateConfig(name="t", room="crypt", registration={})
    res = pl.registration_gate(plate, cfg)
    assert res["passed"] is False and res["recall"] is None


# ── 2. gallery append idempotence ─────────────────────────────────────────────────────────────────
def _row(rid: str, ts: str, **kw) -> dict:
    base = {"id": rid, "ts": ts, "config_name": rid, "room": "crypt",
            "config_summary": "cn=depth", "registration": {"recall": 0.97, "min_recall": 0.95, "passed": True},
            "pregate": {"verdict": "PASS"}, "panel": None}
    base.update(kw)
    return base


def test_gallery_append_is_idempotent(tmp_path):
    gallery = tmp_path / "gallery.html"
    pl.append_gallery_row(gallery, _row("armA", "2026-07-11T01:00:00+00:00"))
    pl.append_gallery_row(gallery, _row("armA", "2026-07-11T02:00:00+00:00"))  # same id -> upsert
    rows = pl.load_rows(gallery)
    assert len(rows) == 1, "re-appending the same run id must replace, not duplicate"
    assert rows[0]["ts"] == "2026-07-11T02:00:00+00:00", "the newer write should win"
    assert gallery.is_file() and "WorldOS Plate Sprint" in gallery.read_text()


def test_gallery_newest_first_ordering(tmp_path):
    gallery = tmp_path / "gallery.html"
    pl.append_gallery_row(gallery, _row("old", "2026-07-11T01:00:00+00:00"))
    pl.append_gallery_row(gallery, _row("new", "2026-07-11T03:00:00+00:00"))
    pl.append_gallery_row(gallery, _row("mid", "2026-07-11T02:00:00+00:00"))
    rows = pl.load_rows(gallery)
    assert [r["id"] for r in rows] == ["new", "mid", "old"]


def test_gallery_panel_pending_then_finished(tmp_path):
    gallery = tmp_path / "gallery.html"
    pl.append_gallery_row(gallery, _row("armA", "2026-07-11T01:00:00+00:00"))
    assert "panel pending" in gallery.read_text()
    # finish the SAME row with panel medians (phase-2 upsert)
    finished = _row("armA", "2026-07-11T02:00:00+00:00",
                    panel={"medians": {"A": 6.2, "B": 5.8, "C": 8.7}, "delta_vs_control": -2.5})
    pl.append_gallery_row(gallery, finished)
    rows = pl.load_rows(gallery)
    assert len(rows) == 1 and rows[0]["panel"]["medians"]["A"] == 6.2


# ── 3. config parsing ──────────────────────────────────────────────────────────────────────────────
def test_load_config_and_generate_argv(tmp_path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({
        "name": "armA-crypt-cn-depth",
        "room": "crypt",
        "generate": {"base_plate": "/g.png", "strength": 0.3, "seed": 7,
                     "controlnet": "depth", "control_strength": 0.7, "control_model": "model_bfl-flux-1-dev",
                     "layered": True, "extra_flags": ["--lighting", "firelit"]},
    }))
    cfg = pl.load_config(cfg_path)
    assert cfg.room == "crypt" and cfg.name == "armA-crypt-cn-depth"
    argv = pl.build_generate_argv(cfg, tmp_path / "gen")
    assert argv[:2] == ["--room", "crypt"]
    for expect in (["--base-plate", "/g.png"], ["--strength", "0.3"], ["--seed", "7"],
                   ["--controlnet", "depth"], ["--control-strength", "0.7"],
                   ["--control-model", "model_bfl-flux-1-dev"]):
        joined = " ".join(argv)
        assert " ".join(expect) in joined, f"missing {expect} in {argv}"
    assert "--layered" in argv
    assert "--lighting" in argv and "firelit" in argv


def test_generate_argv_always_forwards_no_drift_gate(tmp_path):
    # PLATE SPRINT Phase 3 (#1462 follow-up): generate_room.py's --drift-gate defaults ON and FAILS
    # LOUD for crypt/camp_clearing_night (both have a committed manifest) — plate_loop must opt out
    # unconditionally since it runs its OWN non-fatal drift check via registration_gate() instead.
    cfg = pl.PlateConfig(name="t", room="crypt", generate={"base_plate": "/g.png"})
    assert "--no-drift-gate" in pl.build_generate_argv(cfg, tmp_path / "gen")
    cfg2 = pl.PlateConfig(name="t2", room="tavern", generate={"base_plate": "/g.png"})
    assert "--no-drift-gate" in pl.build_generate_argv(cfg2, tmp_path / "gen")


def test_panel_prompt_carries_factual_defect_checklist(tmp_path):
    """Eval-upgrade amendment C: the blind panel packet must ask every scorer the 5-item factual defect
    checklist (on-prop / T-pose / floating / duplicate / missing) BEFORE scoring, so the FACTS a beauty
    score scored around are captured as machine-readable flags. Additive — the 0-10 rubric is unchanged."""
    candidate = _structured(tmp_path / "cand.png")
    cfg = pl.PlateConfig(name="c-check", room="crypt")
    panel = pl.prepare_panel(candidate, cfg, tmp_path / "out", n_scorers=3)
    prompts = json.loads((Path(panel["panel_dir"]) / "prompts.json").read_text())
    flags = {q["flag"] for q in prompts["factual_defect_checklist"]}
    assert {"on_prop", "t_pose", "floating", "duplicate", "missing"} == flags
    assert "defects" in prompts["instructions"] and "FIRST" in prompts["instructions"]
    # the scoring scale is untouched (still 0-10 on the same rubric)
    assert "0-10" in prompts["rubric"]


def test_style_pass_forwarded_when_present(tmp_path):
    """The forward-looking style_pass block (ARM A) is forwarded as --style-pass <json>; absent by
    default (the common config) it never appears."""
    cfg = pl.PlateConfig(name="t", room="crypt", generate={"strength": 0.4},
                         style_pass={"model": "model_z-image", "loras": ["model_MB22"], "strength": 0.35})
    spf = tmp_path / "sp.json"
    argv = pl.build_generate_argv(cfg, tmp_path / "gen", style_pass_file=spf)
    assert "--style-pass" in argv and str(spf) in argv
    # absent style_pass -> no flag
    cfg2 = pl.PlateConfig(name="t2", room="crypt", generate={"strength": 0.4})
    assert "--style-pass" not in pl.build_generate_argv(cfg2, tmp_path / "gen")


def test_empty_style_pass_dict_is_enabled(tmp_path):
    """A present-but-empty style_pass ({}) means "run the style pass with defaults" — run_generate must
    forward it (is-not-None, not a falsey check), and inject the gate's greybox/min_recall so the
    selector matches the gate."""
    cfg = pl.PlateConfig(name="t", room="crypt", generate={"base_plate": "/tmp/gb.png"},
                         style_pass={}, registration={"greybox": "/tmp/gate_gb.png", "min_recall": 0.9})
    pl.run_generate(cfg, tmp_path, dry_run=True)  # dry-run writes the temp json, skips the API
    spf = tmp_path / "style_pass.json"
    assert spf.is_file()
    written = json.loads(spf.read_text())
    assert written["greybox"] == "/tmp/gate_gb.png"  # gate greybox forwarded to the selector
    assert written["min_recall"] == 0.9


def test_config_summary_reflects_levers():
    cfg = pl.PlateConfig(name="t", room="crypt",
                         generate={"controlnet": "depth", "control_strength": 0.7, "strength": 0.3, "seed": 9},
                         style_pass={"model": "model_z-image", "strength": 0.35})
    s = pl.config_summary(cfg)
    assert "cn=depth@0.7" in s and "str=0.3" in s and "seed=9" in s and "style=model_z-image@0.35" in s


def test_missing_room_fails_loud(tmp_path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({"name": "noroom", "generate": {}}))
    with pytest.raises(ValueError, match="room"):
        pl.load_config(cfg_path)


def test_bad_style_pass_type_fails_loud(tmp_path):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({"room": "crypt", "style_pass": "not-an-object"}))
    with pytest.raises(ValueError, match="style_pass"):
        pl.load_config(cfg_path)


# ── verdict ingest (blind-slot remap) ───────────────────────────────────────────────────────────────
def test_ingest_verdict_maps_blind_slots_and_computes_delta():
    """A verdict keyed by BLIND slot names is remapped to A/B/C via the contract; medians + control
    delta are computed. This is the phase-2 normalisation the scores_db row + gallery consume."""
    contract = {"slot_to_label": {"image_1": "B", "image_2": "A", "image_3": "C"}}
    verdict = {"scores": {"image_1": [5.8, 6.0, 5.5], "image_2": [6.0, 6.4, 6.2],
                          "image_3": [8.5, 8.7, 9.0]},
               "control_valid": True, "verdict": "candidate below control"}
    out = pl.ingest_verdict(verdict, contract)
    assert out["medians"]["A"] == 6.2 and out["medians"]["B"] == 5.8 and out["medians"]["C"] == 8.7
    assert out["delta_vs_control"] == pytest.approx(6.2 - 8.7, abs=1e-6)
    assert out["control_valid"] is True


def test_ingest_verdict_accepts_direct_medians():
    contract = {"slot_to_label": {}}
    out = pl.ingest_verdict({"medians": {"A": 6.0, "B": 5.5, "C": 7.0}}, contract)
    assert out["medians"]["A"] == 6.0 and out["delta_vs_control"] == pytest.approx(-1.0)
