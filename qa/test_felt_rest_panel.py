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

    def test_pass_requires_a_cleared_pregate(self):
        """delta >= 0 but the pre-gate never cleared (SKIPPED / missing) is NOT a binding PASS —
        the W1 gate requires 'no open CRITICAL pre-gate', so an unrun pre-gate is INCONCLUSIVE."""
        s = frp.summarize(_panel(ours=[6.0], control=[5.0], pregate="SKIPPED"))
        self.assertEqual(s["verdict"], "INCONCLUSIVE")
        # ...and a below-control delta is still a hard FAIL regardless of the pre-gate.
        s2 = frp.summarize(_panel(ours=[4.0], control=[5.0], pregate="SKIPPED"))
        self.assertEqual(s2["verdict"], "FAIL")


class MultiScenePanelTests(unittest.TestCase):
    def _multi(self, tavern_ours, tavern_ctl, church_ours, church_ctl, pregate="PASS"):
        frames = []
        for i, v in enumerate(tavern_ours):
            frames.append({"id": f"t_o{i}", "scene": "rest:tavern", "kind": "ours", "overall": v})
        for i, v in enumerate(tavern_ctl):
            frames.append({"id": f"t_c{i}", "scene": "rest:tavern", "kind": "control", "overall": v})
        for i, v in enumerate(church_ours):
            frames.append({"id": f"c_o{i}", "scene": "rest:church", "kind": "ours", "overall": v})
        for i, v in enumerate(church_ctl):
            frames.append({"id": f"c_c{i}", "scene": "rest:church", "kind": "control", "overall": v})
        return {"scene": "rest:combined", "pregate": pregate, "frames": frames}

    def test_strong_scene_cannot_mask_a_below_control_scene(self):
        """A combined panel: tavern beats its control, church is below its control. The pooled
        median could hide the church miss, but per-scene grouping surfaces the worst -> FAIL."""
        s = frp.summarize(self._multi([7.0], [5.0], [4.0], [5.5]))
        self.assertEqual(s["verdict"], "FAIL")
        # The reported worst delta is the church's negative one.
        self.assertLess(s["delta"], 0)
        by_scene = {g["scene"]: g["verdict"] for g in s["scenes"]}
        self.assertEqual(by_scene["rest:tavern"], "PASS")
        self.assertEqual(by_scene["rest:church"], "FAIL")

    def test_both_scenes_beat_control_passes(self):
        s = frp.summarize(self._multi([6.5], [5.0], [6.0], [5.5]))
        self.assertEqual(s["verdict"], "PASS")


class LogFramesAndMainTests(unittest.TestCase):
    def test_log_frames_filters_dims_to_rest_lenses_and_drops_none(self):
        """_log_frames maps only the 5 rest lenses into visual_dims_json and omits None dims."""
        captured = {}

        def _fake_add_run(run_id, **kw):
            captured[run_id] = kw

        import sys as _sys
        import types as _types
        stub = _types.ModuleType("scores_db")
        stub.add_run = _fake_add_run  # type: ignore[attr-defined]
        _sys.modules["scores_db"] = stub
        try:
            panel = {
                "scene": "rest:tavern", "backend": "unity-cl", "round": 2, "pregate": "PASS",
                "frames": [
                    {"id": "f1", "kind": "ours", "overall": 6.0,
                     "dims": {"placement_plausibility": 6, "inhabitation": None, "bogus": 9}},
                    {"id": "f2", "kind": "control", "overall": 5.0, "dims": {}},
                ],
            }
            n = frp._log_frames(panel, "felt-rest", None)
        finally:
            del _sys.modules["scores_db"]
        self.assertEqual(n, 2)
        ours_row = next(v for k, v in captured.items() if "-f1-" in k)
        # only the real, non-None rest lens survives; the bogus/None dims are dropped.
        self.assertEqual(ours_row["visual_dims_json"], {"placement_plausibility": 6})
        # the control row is scene-suffixed so the delta is queryable from the ledger.
        ctl_row = next(v for k, v in captured.items() if "-f2-" in k)
        self.assertEqual(ctl_row["visual_scene"], "rest:tavern:control")
        # a frame with no surviving dims omits the visual_dims_json key entirely.
        self.assertNotIn("visual_dims_json", ctl_row)

    def test_log_frames_uses_each_frames_own_scene_and_disambiguates_same_id_rows(self):
        """Thread PRRT_...G9Xr: a combined panel (tavern + church rows in one panel) must log
        each row under ITS OWN scene, not the panel-level scene — otherwise every row lands as
        rest:combined and the ledger can no longer be queried per scene/control pair. Thread
        PRRT_...G9Xx: when the required 5 blind scorers each submit a row for the SAME shuffled
        frame id, run_id must stay unique per row so scores_db.add_run's replace-on-PK write
        doesn't drop all but the last scorer's row."""
        captured = {}

        def _fake_add_run(run_id, **kw):
            self.assertNotIn(run_id, captured, f"run_id collided: {run_id}")
            captured[run_id] = kw

        import sys as _sys
        import types as _types
        stub = _types.ModuleType("scores_db")
        stub.add_run = _fake_add_run  # type: ignore[attr-defined]
        _sys.modules["scores_db"] = stub
        try:
            panel = {
                "scene": "rest:combined", "backend": "unity-cl", "round": 1, "pregate": "PASS",
                "frames": [
                    {"id": "shared", "scene": "rest:tavern", "kind": "ours", "overall": 6.0},
                    {"id": "shared", "scene": "rest:tavern", "kind": "ours", "overall": 6.5},
                    {"id": "shared", "scene": "rest:church", "kind": "ours", "overall": 5.0},
                ],
            }
            n = frp._log_frames(panel, "felt-rest", None)
        finally:
            del _sys.modules["scores_db"]
        # all 3 rows survive (no collision-induced overwrite) and carry their OWN scene, not the
        # panel-level "rest:combined".
        self.assertEqual(n, 3)
        self.assertEqual(len(captured), 3)
        scenes = sorted(v["visual_scene"] for v in captured.values())
        self.assertEqual(scenes, ["rest:church", "rest:tavern", "rest:tavern"])

    def test_main_exit_code_is_zero_only_on_pass(self):
        """main() returns 0 ONLY on a binding PASS; INCONCLUSIVE / FAIL / NO_CONTROL are non-zero
        so a gate wrapper never reads an unscored or below-control panel as green."""
        import json
        import tempfile

        def _run(panel: dict) -> int:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
                json.dump(panel, fh)
                path = fh.name
            return frp.main(["--panel", path, "--dry-run"])

        self.assertEqual(_run(_panel(ours=[6.0], control=[5.0], pregate="PASS")), 0)
        self.assertEqual(_run(_panel(ours=[6.0], control=[5.0], pregate="SKIPPED")), 1)  # INCONCLUSIVE
        self.assertEqual(_run(_panel(ours=[4.0], control=[5.0], pregate="PASS")), 1)  # FAIL
        self.assertEqual(_run(_panel(ours=[7.0], control=[], pregate="PASS")), 1)  # NO_CONTROL


if __name__ == "__main__":
    unittest.main()
