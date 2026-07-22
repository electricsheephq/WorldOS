#!/usr/bin/env python3
"""Red-first units for qa/player_cert.py — the LIVE-half certification skeleton + the two primitives.

Every PURE core (tri-state aggregate, silhouette diff verdict, tall-occluder picker, behind-occluder
cell, spawn/coherence-open verdict, cell<->world geometry) is proven here with NO box and NO player;
the live drive is monkeypatched through walk_test's transport. The silhouette RED case reproduces the
WORLDOS_SILHOUETTE=0 shape (departure blob present, arrival blob absent).

Run: python3 -m pytest qa/test_player_cert.py -q -p no:xdist
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import player_cert as PC  # noqa: E402
import walk_test as W  # noqa: E402


# ── tri-state aggregate ────────────────────────────────────────────────────────────────────────────
def test_cert_verdict_red_wins_over_error():
    rep = {"assertions": [{"verdict": "RED"}, {"verdict": "ERROR"}, {"verdict": "GREEN"}],
           "harness_errors": ["x"]}
    assert PC.classify_cert_verdict(rep) == ("RED", 1)


def test_cert_verdict_error_when_no_red():
    rep = {"assertions": [{"verdict": "GREEN"}, {"verdict": "ERROR"}], "harness_errors": []}
    assert PC.classify_cert_verdict(rep) == ("ERROR", 2)


def test_cert_verdict_top_level_harness_is_error():
    rep = {"assertions": [{"verdict": "GREEN"}], "harness_errors": ["endpoint down"]}
    assert PC.classify_cert_verdict(rep) == ("ERROR", 2)


def test_cert_verdict_green_when_all_clean():
    rep = {"assertions": [{"verdict": "GREEN"}, {"verdict": "SKIP"}], "harness_errors": []}
    assert PC.classify_cert_verdict(rep) == ("GREEN", 0)


def test_cert_verdict_all_skip_is_error_never_vacuous_green():
    """A run that asserted NOTHING (every assertion skipped) must never read GREEN."""
    rep = {"assertions": [{"verdict": "SKIP"}, {"verdict": "SKIP"}], "harness_errors": []}
    assert PC.classify_cert_verdict(rep) == ("ERROR", 2)


# ── silhouette head-density verdict (the red-first core of Primitive A) ──────────────────────────────
def test_silhouette_green_when_head_density_high():
    """The occluded actor's head band shows the walk-behind silhouette tint (density >= min) -> GREEN."""
    rec = {"moved": True, "min_density": 0.045, "head_density": 0.22}
    assert PC.silhouette_verdict(rec) == "GREEN"


def test_silhouette_red_when_head_density_near_zero():
    """WORLDOS_SILHOUETTE=0 (or a stripped ActorSilhouette shader): the occluded actor's head band is
    unchanged column — density ~0 -> RED. The exact #1572/#1545 regression the primitive exists to catch."""
    rec = {"moved": True, "min_density": 0.045, "head_density": 0.0}
    assert PC.silhouette_verdict(rec) == "RED"


def test_silhouette_error_when_frames_frozen():
    """Two byte-identical frames -> no evidence about the silhouette -> ERROR, never a false RED."""
    rec = {"moved": True, "min_density": 0.045, "head_density": 0.0, "frames_identical": True}
    assert PC.silhouette_verdict(rec) == "ERROR"


def test_silhouette_error_when_setup_incomplete():
    assert PC.silhouette_verdict({"no_occluder": True}) == "ERROR"
    assert PC.silhouette_verdict({"no_behind_cell": True}) == "ERROR"
    assert PC.silhouette_verdict({"moved": False}) == "ERROR"
    assert PC.silhouette_verdict({"harness_errors": ["surface: boom"]}) == "ERROR"
    assert PC.silhouette_verdict({"moved": True, "min_density": 0.045}) == "ERROR"  # no density measured


def test_head_window_diff_density_measures_changed_fraction():
    """head_window_diff_density is ~0 for identical frames and high where pixels changed a lot."""
    import numpy as np
    from PIL import Image
    a = Image.fromarray(np.zeros((60, 60, 3), dtype=np.uint8))
    same = PC.head_window_diff_density(a, a, (30, 30), 15, thresh=28)
    assert same == 0.0
    b_arr = np.zeros((60, 60, 3), dtype=np.uint8)
    b_arr[20:40, 20:40] = 200          # a bright patch inside the window
    dens = PC.head_window_diff_density(a, Image.fromarray(b_arr), (30, 30), 15, thresh=28)
    assert dens > 0.3


# ── tall-occluder picker + behind-cell (Primitive A geometry) ───────────────────────────────────────
def _boxes(cols, rows):
    return {"cols": cols, "rows": rows, "ortho": 11.7851, "boxes": [
        {"name": "Floor", "kind": "floor", "center": [0, -0.05, 0], "size": [26, 0.1, 20]},
        {"name": "short_table", "kind": "table", "center": [-7, 1.0, 4], "size": [2, 2.0, 2]},
        {"name": "pillar_nw_shaft", "kind": "stone_pillar", "center": [-7, 3.7, 4], "size": [1.6, 6.8, 3.1]},
        {"name": "wall_n", "kind": "wall_run", "center": [-9, 2.5, 11], "size": [15.2, 5.0, 1.2]},
    ]}


def test_find_tall_occluders_filters_and_sorts():
    occ = PC.find_tall_occluders(_boxes(16, 12))
    names = [o["name"] for o in occ]
    assert "Floor" not in names            # floor is never an occluder
    assert "short_table" not in names      # 2.0 < OCCLUDER_MIN_HEIGHT (3.0)
    assert names[0] == "pillar_nw_shaft"   # tallest (6.8) first
    assert occ[0]["cell"] == [4, 4]        # center (-7,·,4) -> cell (4,4) at cols16/rows12


def _pillar(cell, cols, rows):
    """A tall pillar box centred on `cell` (exercises the 3D ray-vs-box occlusion test)."""
    wx, wz = PC.cell_to_world(cell, cols, rows)
    return {"name": "pillar", "kind": "stone_pillar", "height": 6.8,
            "center": [wx, 3.7, wz], "size": [1.6, 6.8, 3.1], "cell": list(cell)}


def test_choose_occluded_cell_is_head_masked():
    """choose_occluded_cell returns a cell whose head band is masked by the occluder union (and reports
    the primary occluder). A wide wall in front of the room masks cells behind it."""
    cols, rows = 16, 12
    walkable = {(c, r) for c in range(cols) for r in range(rows)}
    cam = W.contract_cam_pos()
    occs = PC.find_tall_occluders(_boxes(cols, rows))
    pick = PC.choose_occluded_cell(occs, walkable, cols, rows, cam)
    assert pick is not None
    assert PC.cell_head_masked(pick["cell"], cols, rows, cam, PC._aabbs(occs)), \
        f"{pick['cell']} head is not masked by the occluder union"


def test_cell_head_masked_false_for_open_cell():
    """A cell plainly not behind any occluder is not head-masked."""
    cols, rows = 16, 12
    cam = W.contract_cam_pos()
    occs = PC.find_tall_occluders(_boxes(cols, rows))
    assert PC.cell_head_masked((14, 10), cols, rows, cam, PC._aabbs(occs)) is False


def test_choose_occluded_cell_none_when_nothing_walkable():
    cols, rows = 16, 12
    cam = W.contract_cam_pos()
    occs = PC.find_tall_occluders(_boxes(cols, rows))
    assert PC.choose_occluded_cell(occs, set(), cols, rows, cam) is None


def test_cell_world_roundtrip():
    for cell in [(0, 0), (4, 4), (15, 11), (7, 5)]:
        wx, wz = PC.cell_to_world(cell, 16, 12)
        assert PC.world_to_cell(wx, wz, 16, 12) == cell


# ── spawn/coherence-open verdict (Primitive B) ──────────────────────────────────────────────────────
def _surface(cols, rows, tokens, blocked=frozenset(), location="crypt"):
    cells = [{"c": c, "r": r, "walkable": (c, r) not in blocked}
             for r in range(rows) for c in range(cols)]
    return {"location": {"id": location},
            "grid": {"cols": cols, "rows": rows, "cellDefault": {"walkable": True}, "cells": cells},
            "tokens": tokens}


def test_spawn_green_when_all_tokens_clean():
    surf = _surface(6, 6, [{"name": "Aldric", "team": "party", "x": 1, "y": 1},
                           {"name": "Goblin", "team": "foe", "x": 4, "y": 4}])
    res = PC.spawn_state_results(surf)
    assert res["verdict"] == "GREEN"
    assert all(t["ok"] for t in res["tokens"])


def test_spawn_red_when_token_on_a_prop():
    surf = _surface(6, 6, [{"name": "Aldric", "team": "party", "x": 1, "y": 1},
                           {"name": "Goblin", "team": "foe", "x": 2, "y": 2}], blocked={(2, 2)})
    res = PC.spawn_state_results(surf)
    assert res["verdict"] == "RED"
    goblin = next(t for t in res["tokens"] if t["name"] == "Goblin")
    assert goblin["prop_clear"] is False


def test_spawn_red_when_token_on_covered_cell():
    surf = _surface(6, 6, [{"name": "Aldric", "team": "party", "x": 1, "y": 1},
                           {"name": "Maera", "team": "npc", "x": 4, "y": 4}])
    verdicts = {(4, 4): "covered"}
    res = PC.spawn_state_results(surf, cell_verdicts=verdicts)
    assert res["verdict"] == "RED"
    maera = next(t for t in res["tokens"] if t["name"] == "Maera")
    assert maera["coherence_ok"] is False


def test_spawn_open_and_ambiguous_pass_coherence():
    surf = _surface(6, 6, [{"name": "Aldric", "team": "party", "x": 1, "y": 1},
                           {"name": "Maera", "team": "npc", "x": 4, "y": 4}])
    for v in ("open", "ambiguous", None):
        verdicts = {(4, 4): v} if v is not None else {}
        assert PC.spawn_state_results(surf, cell_verdicts=verdicts)["verdict"] == "GREEN"


def test_spawn_red_when_no_party():
    surf = _surface(6, 6, [{"name": "Goblin", "team": "foe", "x": 4, "y": 4}])
    res = PC.spawn_state_results(surf)
    assert res["verdict"] == "RED" and "no PARTY" in res["detail"]


def test_spawn_red_when_empty():
    res = PC.spawn_state_results(_surface(6, 6, []))
    assert res["verdict"] == "RED"


# ── live drive: probe_silhouette through a monkeypatched transport ──────────────────────────────────
def _wire_silhouette(monkeypatch, tmp_path, head_density):
    """Monkeypatch walk_test's transport + the head-density measure so probe_silhouette runs with no
    player. The surface is a 16x12 all-walkable crypt with the party out front; the room's calibrated
    occluded cell (VERIFIED_OCCLUDER_CELLS['crypt']) is used, and head_window_diff_density is stubbed to
    the scripted value (silhouette present vs vanished)."""
    surf = _surface(16, 12, [{"name": "Aldric", "team": "party", "x": 1, "y": 10}])

    def fake_get(url, timeout=5.0):
        if "combat-surface" in url:
            return surf
        if "health" in url:
            return {"screenW": 1344, "screenH": 768}
        raise AssertionError(url)

    # distinct baseline/behind frames so the frozen-frame guard (np.array_equal) reads False
    from PIL import Image
    import numpy as np
    pa, pb = tmp_path / "a.png", tmp_path / "b.png"
    Image.fromarray(np.zeros((8, 8, 3), dtype=np.uint8)).save(pa)
    Image.fromarray(np.full((8, 8, 3), 50, dtype=np.uint8)).save(pb)

    def fake_shot(qa, out, label, timeout=6.0):
        return str(pa) if "baseline" in label else str(pb)

    monkeypatch.setattr(W, "_get", fake_get)
    monkeypatch.setattr(W, "_post", lambda url, body=None, timeout=5.0: {})
    monkeypatch.setattr(W, "_capture_shot", fake_shot)
    monkeypatch.setattr(W, "_drive_and_check",
                        lambda qa, e, c, r, s, t, expect_move: (True, [c, r], None))
    monkeypatch.setattr(PC, "head_window_diff_density", lambda a, b, px, rad, thresh: head_density)
    monkeypatch.setattr(PC, "_boxes_for_room", lambda room: _boxes(16, 12))
    return {"engine": "http://e", "qa": "http://q", "out": tmp_path,
            "settle": 0.0, "move_timeout": 1.0}


def test_probe_silhouette_green(monkeypatch, tmp_path):
    ctx = _wire_silhouette(monkeypatch, tmp_path, head_density=0.22)
    rec = PC.probe_silhouette(ctx)
    assert rec["moved"] is True and rec["behind_cell"] == [13, 3]   # crypt calibrated cell
    assert rec["cell_source"] == "calibrated"
    assert PC.silhouette_verdict(rec) == "GREEN"


def test_probe_silhouette_red_reproduces_kill_switch(monkeypatch, tmp_path):
    """head density ~0 at the occluded cell -> the WORLDOS_SILHOUETTE=0 / stripped-shader regression -> RED."""
    ctx = _wire_silhouette(monkeypatch, tmp_path, head_density=0.0)
    rec = PC.probe_silhouette(ctx)
    assert PC.silhouette_verdict(rec) == "RED"


# ── the registry runner ─────────────────────────────────────────────────────────────────────────────
def test_run_cert_skips_player_assertion_without_live(monkeypatch, tmp_path):
    """Engine-only run: the spawn assertion runs GREEN; the silhouette assertion is SKIP (reported, not
    gating) and the overall verdict is GREEN."""
    surf = _surface(6, 6, [{"name": "Aldric", "team": "party", "x": 1, "y": 1}])
    monkeypatch.setattr(W, "_get", lambda url, timeout=5.0: surf)
    monkeypatch.setattr(W, "_post", lambda url, body=None, timeout=5.0: {})
    rep = PC.run_cert("http://e", "http://q", tmp_path, campaign="c", app="a", live=False,
                      settle=0.0, move_timeout=1.0)
    ids = {a["id"]: a["verdict"] for a in rep["assertions"]}
    assert ids["spawn_coherence_open"] == "GREEN"
    assert ids["silhouette_behind_occluder"] == "SKIP"
    assert rep["verdict"] == "GREEN"


def test_run_cert_live_silhouette_red_makes_suite_red(monkeypatch, tmp_path):
    ctx_wire = _wire_silhouette(monkeypatch, tmp_path, head_density=0.0)
    # _wire_silhouette pointed W._get at a 16x12 crypt surface with a party token — spawn assert passes,
    # silhouette assert goes RED (density 0) -> suite RED.
    rep = PC.run_cert(ctx_wire["engine"], ctx_wire["qa"], tmp_path, campaign="c", app="a", live=True,
                      settle=0.0, move_timeout=1.0)
    ids = {a["id"]: a["verdict"] for a in rep["assertions"]}
    assert ids["silhouette_behind_occluder"] == "RED"
    assert rep["verdict"] == "RED"
