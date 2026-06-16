"""Phase-3 ADDITIVE coverage for release_readiness.py:

  (1) --deterministic-only mode — evaluates ONLY the gates that need no live
      LLM/persona evidence and marks the LLM/persona gates SKIPPED (NOT FAILED),
      so CI / the agent get an early "do the deterministic release gates hold?"
      signal without minting any persona/model evidence.
  (2) latency gates — s_per_beat + coldopen_s as ADDITIVE hard-gates sourced from
      the same on-disk artifacts release_readiness already reads (a run's
      latency.json sidecar / a latency block in run.json / score.json). They gate
      ONLY when latency evidence is PRESENT and exceeds the qa/latency_baseline.json
      budget; when latency data is ABSENT the gate is a documented EVIDENCE-GAP/skip,
      never a new false fail (every pre-existing RRI result is unchanged).

These are pure on-disk readers; every fixture is written under tmp_path. No
committed data artifact (qa/scores.db, qa/RRI.json, qa/INDEX.jsonl, transcripts)
is ever written — --out is always a tmp file.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "qa" / "release_readiness.py"

# The gates that need live LLM/persona evidence — in --deterministic-only they are
# SKIPPED, not FAILED. The complement (native_gate, ui_audit, image_render,
# palette_live, + the latency gates) are deterministic and still evaluated.
LLM_PERSONA_GATES = {
    "arc_completed",
    "cross_persona_sat",
    "no_give_up",
    "zero_critical",
    "story_craft",
    "mechanical",
    "behavioral",
}
DETERMINISTIC_GATES = {
    "native_gate",
    "ui_audit",
    "image_render",
    "palette_live",
}
LATENCY_GATES = ("latency_s_per_beat", "latency_coldopen")


class DeterministicAndLatencyTests(unittest.TestCase):
    def run_rri(self, tmp: Path, *args: str) -> tuple[int, str, dict]:
        out = tmp / "RRI.json"
        cmd = [sys.executable, str(SCRIPT), *args, "--out", str(out)]
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
        payload = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        return proc.returncode, proc.stdout + proc.stderr, payload

    def write_release_inputs(self, tmp: Path) -> tuple[Path, Path, Path, Path, Path]:
        story = tmp / "story.json"
        mech = tmp / "mech.json"
        behavioral = tmp / "behavioral.txt"
        audit = tmp / "audit.log"
        palette = tmp / "session_surface.final.json"
        story.write_text(json.dumps({"overall": 5}), encoding="utf-8")
        mech.write_text(json.dumps({"overall": 5}), encoding="utf-8")
        behavioral.write_text("GREEN\n", encoding="utf-8")
        audit.write_text("PASS\n", encoding="utf-8")
        palette.write_text(json.dumps({"can_act": True}), encoding="utf-8")
        return story, mech, behavioral, audit, palette

    def write_persona_run(
        self,
        tmp: Path,
        persona: str,
        *,
        sha: str = "deadbee",
        include_part_a: bool = True,
        image_status: int = 200,
        latency: dict | None = None,
        latency_in_run_json: bool = False,
    ) -> Path:
        run = tmp / f"gate-{persona}"
        player = run / "player"
        player.mkdir(parents=True)
        (run / "score.json").write_text(
            json.dumps(
                {
                    "run": f"gate-{persona}",
                    "persona": persona,
                    "completed_intro_flow": True,
                    "persona_satisfaction": 9,
                    "gave_up": False,
                    "bug_reports_critical": 0,
                    "console_errors": 0,
                    "image_404s": 0,
                }
            ),
            encoding="utf-8",
        )
        payload = {"build_sha": sha, "part_b": {"persona_loop": "PASS", "score_pass": True}}
        if include_part_a:
            payload["part_a"] = {"result": "PASS"}
        if latency is not None and latency_in_run_json:
            payload["latency"] = latency
        (run / "run.json").write_text(json.dumps(payload), encoding="utf-8")
        (player / "network.ndjson").write_text(
            json.dumps({"url": f"http://127.0.0.1/image?scope={persona}", "status": image_status}),
            encoding="utf-8",
        )
        if latency is not None and not latency_in_run_json:
            (run / "latency.json").write_text(json.dumps(latency), encoding="utf-8")
        return run

    def _five_runs(self, tmp: Path, **kwargs) -> list[Path]:
        return [
            self.write_persona_run(tmp, persona, **kwargs)
            for persona in ("newbie", "veteran", "adversarial", "narrative", "optimizer")
        ]

    def _common_args(self, runs, story, mech, behavioral, audit, palette) -> list[str]:
        return [
            "--runs",
            ",".join(str(r) for r in runs),
            "--expected-personas",
            "newbie,veteran,adversarial,narrative,optimizer",
            "--story",
            str(story),
            "--mech",
            str(mech),
            "--behavioral",
            "GREEN",
            "--behavioral-path",
            str(behavioral),
            "--ui-audit",
            "PASS",
            "--ui-audit-log",
            str(audit),
            "--palette-live",
            "true",
            "--palette-source",
            str(palette),
            "--build-sha",
            "deadbee",
        ]

    # ---- (1) --deterministic-only mode ----

    def test_deterministic_only_skips_llm_gates_and_computes_deterministic_subset(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = self._five_runs(tmp)
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

            rc, _text, payload = self.run_rri(
                tmp,
                *self._common_args(runs, story, mech, behavioral, audit, palette),
                "--deterministic-only",
            )

            # Deterministic-only is an EARLY ADVISORY signal: it is never the release
            # verdict, so it must not claim release_ready and the rc reflects "advisory".
            self.assertTrue(payload["deterministic_only"])
            self.assertFalse(payload["release_ready"])
            # The 4 deterministic gates are all evaluated and PASS on clean evidence.
            self.assertEqual(set(payload["deterministic_gates"]), DETERMINISTIC_GATES)
            for gate in DETERMINISTIC_GATES:
                self.assertNotIn(gate, payload["skipped_gates"], f"{gate} must be evaluated, not skipped")
            self.assertTrue(payload["deterministic_pass"])
            # Every LLM/persona gate is SKIPPED, not FAILED. (The two latency gates are also
            # skipped here because this fixture carries no latency evidence — an evidence-gap
            # skip, never a fail — so skipped_gates is a SUPERSET of the LLM gate set.)
            self.assertTrue(LLM_PERSONA_GATES.issubset(set(payload["skipped_gates"])))
            for gate in LLM_PERSONA_GATES:
                self.assertNotIn(gate, payload["failed_gates"], f"{gate} must be SKIPPED not FAILED")
            self.assertEqual(payload["deterministic_failed_gates"], [])

    def test_deterministic_only_reports_a_failing_deterministic_gate(self):
        # A real deterministic miss (palette-live false) must FAIL in deterministic-only,
        # while the LLM gates stay SKIPPED.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = self._five_runs(tmp)
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

            args = self._common_args(runs, story, mech, behavioral, audit, palette)
            # flip palette-live to false
            idx = args.index("--palette-live")
            args[idx + 1] = "false"

            rc, _text, payload = self.run_rri(tmp, *args, "--deterministic-only")

            self.assertTrue(payload["deterministic_only"])
            self.assertFalse(payload["deterministic_pass"])
            self.assertIn("palette_live", payload["deterministic_failed_gates"])
            self.assertIn("palette_live", payload["failed_gates"])
            # LLM gates remain skipped, NOT failed, even on a deterministic miss.
            for gate in LLM_PERSONA_GATES:
                self.assertIn(gate, payload["skipped_gates"])
                self.assertNotIn(gate, payload["failed_gates"])
            self.assertNotEqual(rc, 0)

    def test_default_mode_is_byte_identical_without_deterministic_flag(self):
        # ADDITIVE invariant: without --deterministic-only the output carries NO new
        # mode and the full gate set is evaluated exactly as before.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = self._five_runs(tmp)
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

            rc, _text, payload = self.run_rri(
                tmp, *self._common_args(runs, story, mech, behavioral, audit, palette)
            )

            self.assertEqual(rc, 0)
            self.assertFalse(payload["deterministic_only"])
            self.assertTrue(payload["release_ready"])
            # ADDITIVE invariant: with no latency evidence the two latency gates are an
            # evidence-gap skip, so gates_total stays the pre-Phase-3 count of 11 (byte-
            # identical RRI math) — they are skipped, never failed, never counted.
            self.assertEqual(set(payload["skipped_gates"]), set(LATENCY_GATES))
            self.assertEqual(payload["gates_total"], 11)
            self.assertEqual(payload["rri"], 10.0)

    # ---- (2) latency gates ----

    def test_latency_present_and_over_budget_fails_gate(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # s_per_beat 300 (>> 120 budget) and coldopen 500 (>> 240 budget)
            runs = self._five_runs(
                tmp, latency={"s_per_beat": 300.0, "coldopen_s": 500.0, "turns_per_beat": 5.0}
            )
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

            rc, _text, payload = self.run_rri(
                tmp, *self._common_args(runs, story, mech, behavioral, audit, palette)
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("latency_s_per_beat", payload["failed_gates"])
            self.assertIn("latency_coldopen", payload["failed_gates"])
            self.assertEqual(payload["signals"]["latency_s_per_beat"], 300.0)
            self.assertEqual(payload["signals"]["latency_coldopen_s"], 500.0)
            self.assertEqual(payload["signals"]["latency_s_per_beat_budget"], 120.0)
            self.assertEqual(payload["signals"]["latency_coldopen_budget"], 240.0)

    def test_latency_present_and_under_budget_passes_gate(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # healthy ledger figures: 78.2 s/beat, 157 cold-open — both under budget
            runs = self._five_runs(
                tmp, latency={"s_per_beat": 78.2, "coldopen_s": 157.0, "turns_per_beat": 4.4}
            )
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

            rc, _text, payload = self.run_rri(
                tmp, *self._common_args(runs, story, mech, behavioral, audit, palette)
            )

            self.assertEqual(rc, 0)
            self.assertTrue(payload["release_ready"])
            self.assertNotIn("latency_s_per_beat", payload["failed_gates"])
            self.assertNotIn("latency_coldopen", payload["failed_gates"])
            self.assertNotIn("latency_s_per_beat", {gap["gate"] for gap in payload["evidence_gaps"]})

    def test_latency_absent_is_skip_not_a_new_false_fail(self):
        # The ADDITIVE invariant: no latency evidence anywhere -> the latency gates are a
        # documented EVIDENCE-GAP/skip, NEVER a new fail. The run is still release_ready
        # exactly as it was before latency gates existed.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = self._five_runs(tmp)  # no latency=... -> no latency.json, no run.json latency
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

            rc, _text, payload = self.run_rri(
                tmp, *self._common_args(runs, story, mech, behavioral, audit, palette)
            )

            self.assertEqual(rc, 0)
            self.assertTrue(payload["release_ready"])
            self.assertNotIn("latency_s_per_beat", payload["failed_gates"])
            self.assertNotIn("latency_coldopen", payload["failed_gates"])
            # The gate is skipped (no evidence), recorded honestly.
            self.assertIn("latency_s_per_beat", payload["skipped_gates"])
            self.assertIn("latency_coldopen", payload["skipped_gates"])
            self.assertIsNone(payload["signals"]["latency_s_per_beat"])
            self.assertIsNone(payload["signals"]["latency_coldopen_s"])

    def test_latency_read_from_run_json_block(self):
        # Latency can also ride run.json's `latency` block (not only the sidecar) —
        # both are artifacts release_readiness already opens.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = self._five_runs(
                tmp,
                latency={"s_per_beat": 300.0, "coldopen_s": 100.0},
                latency_in_run_json=True,
            )
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

            rc, _text, payload = self.run_rri(
                tmp, *self._common_args(runs, story, mech, behavioral, audit, palette)
            )

            self.assertEqual(rc, 1)
            self.assertIn("latency_s_per_beat", payload["failed_gates"])
            self.assertNotIn("latency_coldopen", payload["failed_gates"])
            self.assertEqual(payload["signals"]["latency_s_per_beat"], 300.0)
            self.assertEqual(payload["signals"]["latency_coldopen_s"], 100.0)

    def test_latency_null_columns_are_treated_as_absent_not_zero(self):
        # latency_rollup writes NULL columns when a run has no continuing beat. A NULL
        # s_per_beat must be an evidence gap/skip, NOT a fabricated 0.0 that "passes".
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = self._five_runs(
                tmp, latency={"s_per_beat": None, "coldopen_s": None, "turns_per_beat": None}
            )
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

            rc, _text, payload = self.run_rri(
                tmp, *self._common_args(runs, story, mech, behavioral, audit, palette)
            )

            self.assertEqual(rc, 0)  # null latency does not invent a fail
            self.assertTrue(payload["release_ready"])
            self.assertIn("latency_s_per_beat", payload["skipped_gates"])
            self.assertIsNone(payload["signals"]["latency_s_per_beat"])

    def test_deterministic_only_includes_latency_gate_when_evidence_present(self):
        # Latency is a DETERMINISTIC measurement — in --deterministic-only it is
        # evaluated (not skipped) when evidence is present, and a breach fails the
        # deterministic subset.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = self._five_runs(
                tmp, latency={"s_per_beat": 300.0, "coldopen_s": 500.0}
            )
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

            rc, _text, payload = self.run_rri(
                tmp,
                *self._common_args(runs, story, mech, behavioral, audit, palette),
                "--deterministic-only",
            )

            self.assertTrue(payload["deterministic_only"])
            self.assertIn("latency_s_per_beat", payload["deterministic_gates"])
            self.assertIn("latency_s_per_beat", payload["deterministic_failed_gates"])
            self.assertFalse(payload["deterministic_pass"])
            # still no LLM gate failed
            for gate in LLM_PERSONA_GATES:
                self.assertNotIn(gate, payload["failed_gates"])


if __name__ == "__main__":
    unittest.main()
