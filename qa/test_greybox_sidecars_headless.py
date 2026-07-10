"""Smoke test for qa/greybox_sidecars_headless.py — the headless depth+normal sidecar renderer.

Asserts the renderer emits a contract-sized (1344x768) grayscale depth map + RGB normal map from a
room_geometry.json, co-registered with qa/greybox_render_headless.py (same camera basis, imported).
Deterministic, offline, no Unity/box. Run:
    python3 -m pytest qa/test_greybox_sidecars_headless.py -q -p no:xdist
"""
from __future__ import annotations

import sys
from pathlib import Path

_QA = Path(__file__).resolve().parent
sys.path.insert(0, str(_QA))

import greybox_sidecars_headless as gs  # noqa: E402
from greybox_render_headless import PX_W, PX_H  # noqa: E402


def _geo():
    return {
        "cols": 16, "rows": 12,
        "walls": [[c, r] for c in (0, 15) for r in range(12)],  # a couple of flank columns
        "props": [
            {"kind": "large_tree", "cells": [[2, 3], [2, 4]]},
            {"kind": "boulder", "cells": [[10, 7]]},
            {"kind": "fallen_log", "cells": [[5, 4], [6, 4]]},
        ],
    }


def test_emits_contract_sized_depth_and_normal(tmp_path):
    from PIL import Image

    dpth = tmp_path / "d.png"
    nrml = tmp_path / "n.png"
    gs.render(_geo(), str(dpth), str(nrml), wall_height=9.0)
    assert dpth.is_file() and nrml.is_file()
    di, ni = Image.open(dpth), Image.open(nrml)
    assert di.size == (PX_W, PX_H) and di.mode == "L"
    assert ni.size == (PX_W, PX_H) and ni.mode == "RGB"


def test_depth_has_near_bright_far_dark_range(tmp_path):
    """Depth must span a real range (not a flat fill) with the near=bright convention — the box faces
    closer to the camera read brighter than the far background."""
    from PIL import Image

    dpth = tmp_path / "d.png"
    gs.render(_geo(), str(dpth), str(tmp_path / "n.png"))
    lo, hi = Image.open(dpth).convert("L").getextrema()
    assert lo == 0            # far background stays black
    assert hi >= 200          # near geometry reaches bright
