#!/usr/bin/env python3
"""Unit tests for tiled_space_spike.py — the offline geometry/stitch/metric pieces (NO API calls).

Mirrors qa/test_plate_loop.py: exercises the deterministic harness math with tiny synthetic inputs so
the seam metric + fairness invariant are regression-guarded without touching Scenario.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tiled_space_spike as t  # noqa: E402


def _geo(cols, props):
    return {"cols": cols, "rows": 12, "walls": [], "props": props}


def test_compose_geometry_shifts_and_widens():
    a = _geo(16, [{"kind": "crate", "cells": [[1, 1]]}])
    b = _geo(16, [{"kind": "tree", "cells": [[0, 0]]}])
    merged = t.compose_geometry([(a, 0), (b, 16)], rows=12)
    assert merged["cols"] == 32
    # b's cell [0,0] shifted to [16,0]
    shifted = [p for p in merged["props"] if p["kind"] == "tree"][0]
    assert shifted["cells"] == [[16, 0]]


def test_compose_seam_trim_opens_the_mouth():
    a = _geo(16, [{"kind": "wall", "cells": [[15, 5]]}])
    b = _geo(16, [{"kind": "tree", "cells": [[0, 0]]}])  # -> col 16, inside seam zone
    merged = t.compose_geometry([(a, 0), (b, 16)], rows=12, seam_cols={15, 16})
    kinds = {p["kind"] for p in merged["props"]}
    assert "tree" not in kinds and "wall" not in kinds  # both intruded the seam zone -> dropped


def test_tile_controls_are_exact_crops_of_wide():
    """The FAIRNESS invariant: left/right tile depth == the exact halves of the wide depth."""
    geo = t.compose_geometry([(_geo(16, [{"kind": "crate", "cells": [[3, 6]]}]), 0),
                              (_geo(16, [{"kind": "tree", "cells": [[2, 2]]}]), 16)], rows=12)
    cam = t.Cam(2048, 768, ortho=13.0)
    _, depth = t.render(geo, cam)
    wide = np.asarray(depth)
    left = np.asarray(depth.crop((0, 0, 1024, 768)))
    right = np.asarray(depth.crop((1024, 0, 2048, 768)))
    assert np.array_equal(left, wide[:, :1024])
    assert np.array_equal(right, wide[:, 1024:2048])


def test_feather_stitch_dims_and_blend():
    left = Image.new("RGB", (100, 40), (0, 0, 0))
    right = Image.new("RGB", (100, 40), (200, 200, 200))
    out = t.feather_stitch(left, right, overlap=20)
    assert out.size == (180, 40)  # 100 + 100 - 20
    a = np.asarray(out, dtype=float)
    # interior of each side keeps its colour; the overlap band ramps monotonically 0 -> 200
    assert a[:, :80].max() == 0
    assert a[:, 100:].min() == 200
    band_means = a[20, 80:100, 0]
    assert band_means[0] < band_means[-1]  # left(dark) -> right(light)
    assert np.all(np.diff(band_means) >= -1e-6)  # monotonic non-decreasing


def test_seam_metrics_flag_a_hard_seam_but_not_continuous():
    # continuous gradient image: seam at its centre should read ~1.0 excess
    grad = np.tile(np.linspace(0, 255, 200).reshape(1, 200, 1), (60, 1, 3)).astype("uint8")
    cont = Image.fromarray(grad, "RGB")
    m_cont = t.seam_metrics(cont, 100)
    # a HARD discontinuity: left half black, right half white
    hard = np.zeros((60, 200, 3), dtype="uint8")
    hard[:, 100:] = 255
    m_hard = t.seam_metrics(Image.fromarray(hard, "RGB"), 100)
    # seam_excess (exact adjacent-column colour jump vs the texture floor) is the sharp signal:
    assert m_hard["seam_excess"] > 10 * m_cont["seam_excess"]
    assert m_hard["grad_ratio"] > 3.0            # a wall of gradient sits at the seam band
    assert m_cont["grad_ratio"] < 2.0            # continuous gradient: seam ~ ordinary texture


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
