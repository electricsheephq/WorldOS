"""Unit tests for the FELT REST-SCENE panel logger (qa/felt_rest_panel.py, W1 #1318).

Covers the ONLY citable metric — the control-anchored delta verdict — and the calibration-law
guards (a panel with no disguised control is NO_CONTROL; a CRITICAL pre-gate short-circuits).
Pure aggregation; no scores_db write here (that path is exercised via --dry-run off).
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


_MOD_PATH = Path(__file__).resolve().parent / "felt_rest_panel.py"
_SPEC = importlib.util.spec_from_file_location("felt_rest_panel", _MOD_PATH)
assert _SPEC is not None and _SPEC.loader is not None
frp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(frp)


def _panel(ours: list[float], control: list[float], pregate: str = "PASS") -> dict:
    frames = [{"id": f"ours_{i}", "kind": "ours", "overall": v} for i, v in enumerate(ours)]
    frames += [{"id": f"ctl_{i}", "kind": "control", "overall": v} for i, v in enumerate(control)]
    return {"scene": "rest:tavern-innkeeper", "backend": "unity-cl", "round": 1,
            "pregate": pregate, "frames": frames}


class SummarizeTests(unittest.TestCase):
    def test_pass_when_ours_meets_or_beats_control(self):
        s = frp.summarize(_panel(ours=[6.0, 6.4], control=[5.4]))
        self.assertEqual(s["verdict"], "PASS")
        self.assertGreaterEqual(s["delta"], 0)

    def test_fail_when_below_control(self):
        s = frp.summarize(_panel(ours=[4.0], control=[5.6]))
        self.assertEqual(s["verdict"], "FAIL")
        self.assertLess(s["delta"], 0)

    def test_no_control_is_flagged(self):
        s = frp.summarize(_panel(ours=[7.0], control=[]))
        self.assertEqual(s["verdict"], "NO_CONTROL")

    def test_critical_pregate_short_circuits(self):
        s = frp.summarize(_panel(ours=[8.0], control=[5.0], pregate="FLAG"))
        self.assertEqual(s["verdict"], "PREGATE_BLOCKED")

    def test_delta_is_the_only_citable_metric(self):
        s = frp.summarize(_panel(ours=[6.0, 6.2], control=[5.5, 5.7]))
        # medians reported for the ledger, but the verdict rides the delta, not the absolute.
        self.assertEqual(s["ours_median"], 6.1)
        self.assertEqual(s["control_median"], 5.6)
        self.assertAlmostEqual(s["delta"], 0.5, places=3)


if __name__ == "__main__":
    unittest.main()
