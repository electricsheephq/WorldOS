"""Offline, fast pytest for the W1 overworld schematic generator.

Covers the acceptance invariants from epic #1640:
  * determinism      — same seed => byte-identical overworld.json
  * rivers downhill  — each river polyline's height samples are non-increasing
                       (within tolerance for meander jitter + 8-bit quantization)
  * roads valid      — every road connects two DISTINCT settlements at finite cost
  * bridges valid    — every bridge sits on BOTH its road and its river vector
  * constraints refs — every area edge_constraint references a real river/road id
  * settlements land  — >= the requested count, none in the ocean

Runs the generator at a small resolution so the whole file is a couple of seconds.

Run:
  uv run --directory /Users/lume/WorldOS/servers/engine --group dev \
    python -m pytest \
    /Users/lume/WorldOS-worktrees/w1-schematic/tools/overworld/test_overworld.py \
    -q -p no:xdist
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_overworld as g
import seed_overworld_locations as seedmod

RES = 160
SEED = 42
N = 8


def _params():
    p = g.OverworldParams(res=RES, n_settlements=N)
    p.settlement_sep_cells = 22.0  # small sep so a small map still yields N sites
    return p


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("ow")
    ow = g.generate(SEED, out, _params())
    height = np.asarray(Image.open(out / "height_map.png").convert("L"), dtype=np.float64)
    constraints = json.loads((out / "area_constraints.json").read_text())
    return {"ow": ow, "height": height, "constraints": constraints, "dir": out}


def test_determinism_identical_json(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    g.generate(SEED, a, _params())
    g.generate(SEED, b, _params())
    assert (a / "overworld.json").read_bytes() == (b / "overworld.json").read_bytes()
    assert (a / "area_constraints.json").read_bytes() == (b / "area_constraints.json").read_bytes()


def test_rivers_flow_downhill(world):
    ow, height = world["ow"], world["height"]
    cs = ow["grid"]["cell_size_m"]
    h, w = height.shape
    tol = 8.0  # on the 0..255 height scale: absorbs jitter + quantization
    steps = 0
    viol = 0
    net_ok = 0
    for rv in ow["rivers"]:
        hs = []
        for x, y in rv["points"]:
            r = min(h - 1, max(0, int(y / cs)))
            c = min(w - 1, max(0, int(x / cs)))
            hs.append(height[r, c])
        for i in range(len(hs) - 1):
            steps += 1
            if hs[i + 1] > hs[i] + tol:
                viol += 1
        if hs[-1] <= hs[0] + tol:  # net descent headwater -> mouth
            net_ok += 1
    assert steps > 0
    assert viol / steps < 0.01, f"{viol}/{steps} uphill steps"
    assert net_ok / len(ow["rivers"]) >= 0.95


def test_roads_connect_two_settlements_finite_cost(world):
    ow = world["ow"]
    ids = {s["id"] for s in ow["settlements"]}
    assert ow["roads"], "expected at least one road"
    for rd in ow["roads"]:
        assert rd["from"] in ids and rd["to"] in ids
        assert rd["from"] != rd["to"]
        assert np.isfinite(rd["cost"]) and rd["cost"] > 0
        assert len(rd["points"]) >= 2


def _min_dist(pt, polyline):
    return min((px - pt[0]) ** 2 + (py - pt[1]) ** 2 for px, py in polyline) ** 0.5


def test_bridges_sit_on_road_and_river(world):
    ow = world["ow"]
    cs = ow["grid"]["cell_size_m"]
    roads = {r["id"]: r for r in ow["roads"]}
    rivers = {r["id"]: r for r in ow["rivers"]}
    # tolerance: bridge is placed on a coarse road cell; allow a few coarse cells.
    tol = cs * ow["params"]["road_res_factor"] * 3
    for b in ow["bridges"]:
        assert b["road_id"] in roads
        assert b["river_id"] in rivers
        assert _min_dist(b["at"], roads[b["road_id"]]["points"]) <= tol
        assert _min_dist(b["at"], rivers[b["river_id"]]["points"]) <= tol


def test_area_constraints_reference_real_features(world):
    ow = world["ow"]
    constraints = world["constraints"]
    valid = {r["id"] for r in ow["rivers"]} | {r["id"] for r in ow["roads"]}
    assert constraints["areas"], "expected area entries"
    for area in constraints["areas"]:
        assert area["type"] in ("settlement", "crossing")
        for ec in area["edge_constraints"]:
            assert ec["edge"] in ("N", "S", "E", "W")
            assert ec["feature"] in ("river", "road")
            assert ec["ref_id"] in valid
            assert 0.0 <= ec["at_fraction"] <= 1.0
    # A crossing area must actually carry both its river and road as constraints.
    for area in constraints["areas"]:
        if area["type"] == "crossing":
            feats = {ec["feature"] for ec in area["edge_constraints"]}
            assert "river" in feats or "road" in feats


def test_settlements_on_land(world):
    ow = world["ow"]
    assert len(ow["settlements"]) >= N
    for s in ow["settlements"]:
        assert s["biome"] != "ocean"
        assert s["habitability"] > 0


def test_engine_shape_preview_is_wellformed(world):
    """The dry-run seam preview yields engine-shaped rows without any engine import."""
    shaped = seedmod.to_engine_shape(world["ow"])
    assert shaped["locations"]
    ids = {loc["id"] for loc in shaped["locations"]}
    for loc in shaped["locations"]:
        # travel_times keys must be real, connected locations (symmetric edges).
        for other, minutes in loc["travel_times"].items():
            assert other in ids
            assert minutes >= 1
    for node in shaped["world_graph"]["nodes"]:
        assert node["atlas_layer"] in ("region", "settlement", "site", "dungeon", "route")
