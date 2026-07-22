#!/usr/bin/env python3
"""Tests for qa/check_always_included_shaders.py — the #1674 Always-Included-Shaders pre-flight gate."""
import unittest
from pathlib import Path

from check_always_included_shaders import (
    BUILD_SCRIPT_REL,
    REQUIRED_SHADERS,
    SHADER_DIR_REL,
    SHADER_FILES,
    evaluate_build_report,
    evaluate_build_source,
    run,
)

ROOT = Path(__file__).resolve().parents[1]

# A minimal build source that satisfies the gate: calls the ensure helper and names both shaders.
GOOD_SOURCE = """
        string[] includedShaders = EnsureAlwaysIncludedShaders();
    static readonly string[] RequiredAlwaysIncluded = { "WorldOS/OccluderDepth", "WorldOS/ActorSilhouette" };
    static string[] EnsureAlwaysIncludedShaders() { return RequiredAlwaysIncluded; }
"""


class EvaluateBuildSourceTests(unittest.TestCase):
    def test_good_source_is_clean(self):
        self.assertEqual(evaluate_build_source(GOOD_SOURCE), [])

    def test_missing_ensure_call_fails(self):
        src = GOOD_SOURCE.replace("EnsureAlwaysIncludedShaders();", "SomethingElse();")
        problems = evaluate_build_source(src)
        self.assertTrue(any("EnsureAlwaysIncludedShaders" in p for p in problems), problems)

    def test_missing_silhouette_shader_fails(self):
        # Drop ActorSilhouette entirely from the source -> the regression #1674 would recur.
        src = GOOD_SOURCE.replace('"WorldOS/ActorSilhouette"', '"WorldOS/SomethingElse"')
        problems = evaluate_build_source(src)
        self.assertTrue(any("WorldOS/ActorSilhouette" in p for p in problems), problems)

    def test_missing_occluder_shader_fails(self):
        src = GOOD_SOURCE.replace('"WorldOS/OccluderDepth"', '"WorldOS/SomethingElse"')
        problems = evaluate_build_source(src)
        self.assertTrue(any("WorldOS/OccluderDepth" in p for p in problems), problems)


class EvaluateBuildReportTests(unittest.TestCase):
    def test_report_listing_both_is_clean(self):
        report = "result=Succeeded\nalwaysIncludedShaders=WorldOS/OccluderDepth,WorldOS/ActorSilhouette\n"
        self.assertEqual(evaluate_build_report(report), [])

    def test_report_missing_silhouette_fails(self):
        report = "result=Succeeded\nalwaysIncludedShaders=WorldOS/OccluderDepth\n"
        problems = evaluate_build_report(report)
        self.assertTrue(any("WorldOS/ActorSilhouette" in p for p in problems), problems)

    def test_report_without_line_fails(self):
        report = "result=Succeeded\narchitecture=x64ARM64\n"
        problems = evaluate_build_report(report)
        self.assertTrue(any("no alwaysIncludedShaders" in p for p in problems), problems)


class RealRepoTests(unittest.TestCase):
    """The actual repo state must PASS the gate (this batch wired the guarantee)."""

    def test_required_shader_files_exist(self):
        for name in REQUIRED_SHADERS:
            fname = SHADER_FILES[name]
            self.assertTrue((ROOT / SHADER_DIR_REL / fname).is_file(), f"missing {fname}")

    def test_build_script_present(self):
        self.assertTrue((ROOT / BUILD_SCRIPT_REL).is_file(), BUILD_SCRIPT_REL)

    def test_repo_passes_gate(self):
        code, messages = run(ROOT)
        self.assertEqual(code, 0, f"gate should pass on this repo; problems: {messages}")


if __name__ == "__main__":
    unittest.main()
