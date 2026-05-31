import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "qa" / "release_readiness.py"


class ReleaseReadinessContractTests(unittest.TestCase):
    def run_rri(self, tmp: Path, *args: str) -> tuple[int, str, dict]:
        out = tmp / "RRI.json"
        cmd = [sys.executable, str(SCRIPT), *args, "--out", str(out)]
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
        payload = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        return proc.returncode, proc.stdout + proc.stderr, payload

    def test_missing_expected_persona_score_marks_partial_and_fails_release(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            newbie = tmp / "gate-newbie"
            veteran = tmp / "gate-veteran"
            newbie.mkdir()
            veteran.mkdir()
            (newbie / "score.json").write_text(
                json.dumps(
                    {
                        "run": "gate-newbie",
                        "persona": "newbie",
                        "completed_intro_flow": True,
                        "persona_satisfaction": 9,
                        "gave_up": False,
                        "bug_reports_critical": 0,
                        "image_404s": 0,
                    }
                ),
                encoding="utf-8",
            )
            (newbie / "run.json").write_text(
                json.dumps({"build_sha": "deadbee", "part_a": {"result": "PASS"}, "part_b": {"persona_loop": "PASS", "score_pass": True}}),
                encoding="utf-8",
            )
            (veteran / "run.json").write_text(
                json.dumps({"part_b": {"persona_loop": "backend_not_ready"}}),
                encoding="utf-8",
            )
            story = tmp / "story.json"
            mech = tmp / "mech.json"
            story.write_text(json.dumps({"overall": 5}), encoding="utf-8")
            mech.write_text(json.dumps({"overall": 5}), encoding="utf-8")

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                f"{newbie},{veteran}",
                "--expected-personas",
                "newbie,veteran",
                "--story",
                str(story),
                "--mech",
                str(mech),
                "--behavioral",
                "GREEN",
                "--ui-audit",
                "PASS",
                "--palette-live",
                "true",
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertEqual(payload["release_verdict_gate"], "full_five_persona_rri")
            self.assertFalse(payload["gate_split_contract"]["deterministic_built_app_smoke"]["release_verdict"])
            self.assertFalse(payload["gate_split_contract"]["short_real_provider_playtest"]["release_verdict"])
            self.assertTrue(payload["gate_split_contract"]["full_five_persona_rri"]["release_verdict"])
            self.assertTrue(payload["partial"])
            self.assertTrue(payload["harness_contaminated"])
            self.assertEqual(payload["expected_personas"], ["newbie", "veteran"])
            self.assertEqual(payload["completed_personas"], ["newbie"])
            self.assertEqual(payload["missing_personas"], ["veteran"])
            self.assertIn("missing_personas", payload["failed_gates"])
            self.assertIn("artifact_sources", payload)
            self.assertEqual(payload["artifact_sources"]["behavioral"], "argument")

    def test_image_rate_reads_player_network_ndjson(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = tmp / "gate-newbie"
            player = run / "player"
            player.mkdir(parents=True)
            (run / "score.json").write_text(
                json.dumps(
                    {
                        "run": "gate-newbie",
                        "persona": "newbie",
                        "completed_intro_flow": True,
                        "persona_satisfaction": 9,
                        "gave_up": False,
                        "bug_reports_critical": 0,
                        "image_404s": 0,
                    }
                ),
                encoding="utf-8",
            )
            (run / "run.json").write_text(
                json.dumps({"build_sha": "deadbee", "part_a": {"result": "PASS"}, "part_b": {"persona_loop": "PASS", "score_pass": True}}),
                encoding="utf-8",
            )
            (player / "network.ndjson").write_text(
                "\n".join(
                    [
                        json.dumps({"url": "http://127.0.0.1/image?scope=a", "status": 200}),
                        json.dumps({"url": "http://127.0.0.1/image?scope=b", "status": 200}),
                        json.dumps({"url": "http://127.0.0.1/image?scope=c", "status": 404}),
                    ]
                ),
                encoding="utf-8",
            )
            story = tmp / "story.json"
            mech = tmp / "mech.json"
            story.write_text(json.dumps({"overall": 5}), encoding="utf-8")
            mech.write_text(json.dumps({"overall": 5}), encoding="utf-8")

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                str(run),
                "--expected-personas",
                "newbie",
                "--story",
                str(story),
                "--mech",
                str(mech),
                "--behavioral",
                "GREEN",
                "--ui-audit",
                "PASS",
                "--palette-live",
                "true",
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertAlmostEqual(payload["signals"]["image_render_rate"], 0.6667)
            self.assertIn("image_render", payload["failed_gates"])

    def test_console_errors_fail_zero_critical_gate(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = tmp / "gate-newbie"
            run.mkdir()
            (run / "score.json").write_text(
                json.dumps(
                    {
                        "run": "gate-newbie",
                        "persona": "newbie",
                        "completed_intro_flow": True,
                        "persona_satisfaction": 9,
                        "gave_up": False,
                        "bug_reports_critical": 0,
                        "console_errors": 1,
                        "image_404s": 0,
                    }
                ),
                encoding="utf-8",
            )
            (run / "run.json").write_text(
                json.dumps({"build_sha": "deadbee", "part_a": {"result": "PASS"}, "part_b": {"persona_loop": "PASS", "score_pass": True}}),
                encoding="utf-8",
            )
            story = tmp / "story.json"
            mech = tmp / "mech.json"
            story.write_text(json.dumps({"overall": 5}), encoding="utf-8")
            mech.write_text(json.dumps({"overall": 5}), encoding="utf-8")

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                str(run),
                "--expected-personas",
                "newbie",
                "--story",
                str(story),
                "--mech",
                str(mech),
                "--behavioral",
                "GREEN",
                "--ui-audit",
                "PASS",
                "--palette-live",
                "true",
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertIn("zero_critical", payload["failed_gates"])
            self.assertEqual(payload["signals"]["total_console_errors"], 1)
            self.assertIn("console_errors=1", payload["gate_detail"]["zero_critical"])

    def test_no_image_denominator_is_an_evidence_gap(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = tmp / "gate-newbie"
            run.mkdir()
            (run / "score.json").write_text(
                json.dumps(
                    {
                        "run": "gate-newbie",
                        "persona": "newbie",
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
            (run / "run.json").write_text(
                json.dumps({"build_sha": "deadbee", "part_a": {"result": "PASS"}, "part_b": {"persona_loop": "PASS", "score_pass": True}}),
                encoding="utf-8",
            )
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

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                str(run),
                "--expected-personas",
                "newbie",
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
            )

            self.assertEqual(rc, 1)
            self.assertTrue(payload["partial"])
            self.assertIn("image_render", payload["failed_gates"])
            self.assertEqual(payload["signals"]["image_request_denominator"], 0)
            self.assertIn(
                {"gate": "image_render", "missing": "network.ndjson image denominator", "detail": "no /image requests recorded for: newbie"},
                payload["evidence_gaps"],
            )

    def test_complete_single_persona_evidence_is_still_not_release_ready(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = tmp / "gate-newbie"
            player = run / "player"
            player.mkdir(parents=True)
            (run / "score.json").write_text(
                json.dumps(
                    {
                        "run": "gate-newbie",
                        "persona": "newbie",
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
            (run / "run.json").write_text(
                json.dumps({"build_sha": "deadbee", "part_a": {"result": "PASS"}, "part_b": {"persona_loop": "PASS", "score_pass": True}}),
                encoding="utf-8",
            )
            (player / "network.ndjson").write_text(
                "\n".join(
                    [
                        json.dumps({"url": "http://127.0.0.1/image?scope=a", "status": 200}),
                        json.dumps({"url": "http://127.0.0.1/image?scope=b", "status": 200}),
                    ]
                ),
                encoding="utf-8",
            )
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

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                str(run),
                "--expected-personas",
                "newbie",
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
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertTrue(payload["partial"])
            self.assertTrue(payload["harness_contaminated"])
            self.assertIn("missing_release_personas", payload["failed_gates"])
            self.assertEqual(payload["missing_release_personas"], ["veteran", "adversarial", "narrative", "optimizer"])
            for gate in ("cross_persona_sat", "no_give_up", "zero_critical", "image_render"):
                self.assertIn(gate, payload["failed_gates"])
            self.assertIn("canonical five-persona release set", {gap["missing"] for gap in payload["evidence_gaps"]})
            self.assertTrue({"cross_persona_sat", "no_give_up", "zero_critical", "image_render"}.issubset({gap["gate"] for gap in payload["evidence_gaps"]}))
            self.assertEqual(payload["signals"]["image_request_denominator"], 2)

    def test_complete_five_persona_evidence_can_release_ready(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = []
            for persona in ("newbie", "veteran", "adversarial", "narrative", "optimizer"):
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
                (run / "run.json").write_text(
                    json.dumps({"build_sha": "deadbee", "part_a": {"result": "PASS"}, "part_b": {"persona_loop": "PASS", "score_pass": True}}),
                    encoding="utf-8",
                )
                (player / "network.ndjson").write_text(
                    json.dumps({"url": f"http://127.0.0.1/image?scope={persona}", "status": 200}),
                    encoding="utf-8",
                )
                runs.append(run)

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

            rc, _text, payload = self.run_rri(
                tmp,
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
            )

            self.assertEqual(rc, 0)
            self.assertTrue(payload["release_ready"])
            self.assertFalse(payload["partial"])
            self.assertFalse(payload["harness_contaminated"])
            self.assertEqual(payload["missing_release_personas"], [])
            self.assertEqual(payload["evidence_gaps"], [])
            self.assertEqual(payload["signals"]["image_request_denominator"], 5)

    def test_low_product_score_is_clean_red_not_harness_contaminated(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = []
            for persona in ("newbie", "veteran", "adversarial", "narrative", "optimizer"):
                run = tmp / f"gate-{persona}"
                player = run / "player"
                player.mkdir(parents=True)
                (run / "score.json").write_text(
                    json.dumps(
                        {
                            "run": f"gate-{persona}",
                            "persona": persona,
                            "pass": False,
                            "completed_intro_flow": True,
                            "persona_satisfaction": 5,
                            "gave_up": False,
                            "bug_reports_critical": 0,
                            "console_errors": 0,
                            "image_404s": 0,
                        }
                    ),
                    encoding="utf-8",
                )
                (run / "run.json").write_text(
                    json.dumps({"build_sha": "deadbee", "part_a": {"result": "PASS"}, "part_b": {"persona_loop": "PASS", "score_pass": False}}),
                    encoding="utf-8",
                )
                (player / "network.ndjson").write_text(
                    json.dumps({"url": f"http://127.0.0.1/image?scope={persona}", "status": 200}),
                    encoding="utf-8",
                )
                runs.append(run)

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

            rc, _text, payload = self.run_rri(
                tmp,
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
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertFalse(payload["partial"])
            self.assertFalse(payload["harness_contaminated"])
            self.assertEqual(payload["evidence_gaps"], [])
            self.assertEqual(payload["failed_gates"], ["cross_persona_sat"])
            self.assertEqual(payload["signals"]["image_request_denominator"], 5)

    def test_green_arguments_without_evidence_paths_are_not_release_ready(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = []
            for persona in ("newbie", "veteran", "adversarial", "narrative", "optimizer"):
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
                (run / "run.json").write_text(
                    json.dumps({"build_sha": "deadbee", "part_a": {"result": "PASS"}, "part_b": {"persona_loop": "PASS", "score_pass": True}}),
                    encoding="utf-8",
                )
                (player / "network.ndjson").write_text(
                    json.dumps({"url": f"http://127.0.0.1/image?scope={persona}", "status": 200}),
                    encoding="utf-8",
                )
                runs.append(run)

            story = tmp / "story.json"
            mech = tmp / "mech.json"
            story.write_text(json.dumps({"overall": 5}), encoding="utf-8")
            mech.write_text(json.dumps({"overall": 5}), encoding="utf-8")

            rc, _text, payload = self.run_rri(
                tmp,
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
                "--ui-audit",
                "PASS",
                "--palette-live",
                "true",
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertLess(payload["gates_passed"], payload["gates_total"])
            self.assertTrue(payload["partial"])
            self.assertIn("behavioral", {gap["gate"] for gap in payload["evidence_gaps"]})
            self.assertIn("ui_audit", {gap["gate"] for gap in payload["evidence_gaps"]})
            self.assertIn("palette_live", {gap["gate"] for gap in payload["evidence_gaps"]})
            self.assertIn("behavioral", payload["failed_gates"])
            self.assertIn("ui_audit", payload["failed_gates"])
            self.assertIn("palette_live", payload["failed_gates"])

    def test_existing_score_does_not_override_part_b_failure(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = []
            for persona in ("newbie", "veteran", "adversarial", "narrative", "optimizer"):
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
                part_b = {"persona_loop": "PASS", "score_pass": True}
                if persona == "veteran":
                    part_b = {
                        "persona_loop": "FAIL",
                        "score_pass": False,
                        "failure_bucket": "move_rejected",
                        "failure_detail": "POST /move returned 500",
                    }
                (run / "run.json").write_text(
                    json.dumps({"build_sha": "deadbee", "part_a": {"result": "PASS"}, "part_b": part_b}),
                    encoding="utf-8",
                )
                (player / "network.ndjson").write_text(
                    json.dumps({"url": f"http://127.0.0.1/image?scope={persona}", "status": 200}),
                    encoding="utf-8",
                )
                runs.append(run)

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

            rc, _text, payload = self.run_rri(
                tmp,
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
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("arc_completed", {gap["gate"] for gap in payload["evidence_gaps"]})
            self.assertIn("veteran", " ".join(gap["detail"] for gap in payload["evidence_gaps"]))
            self.assertIn("move_rejected", " ".join(gap["detail"] for gap in payload["evidence_gaps"]))

    def test_mixed_build_sha_blocks_release_ready(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = []
            for persona in ("newbie", "veteran", "adversarial", "narrative", "optimizer"):
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
                sha = "deadbee" if persona != "optimizer" else "badcafe"
                (run / "run.json").write_text(
                    json.dumps({"build_sha": sha, "part_a": {"result": "PASS"}, "part_b": {"persona_loop": "PASS", "score_pass": True}}),
                    encoding="utf-8",
                )
                (player / "network.ndjson").write_text(
                    json.dumps({"url": f"http://127.0.0.1/image?scope={persona}", "status": 200}),
                    encoding="utf-8",
                )
                runs.append(run)

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

            rc, _text, payload = self.run_rri(
                tmp,
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
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("native_gate", {gap["gate"] for gap in payload["evidence_gaps"]})
            self.assertEqual(payload["signals"]["run_build_shas"], ["badcafe", "deadbee"])

    def test_missing_run_build_sha_blocks_release_ready(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = []
            for persona in ("newbie", "veteran", "adversarial", "narrative", "optimizer"):
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
                (run / "run.json").write_text(
                    json.dumps({"part_a": {"result": "PASS"}, "part_b": {"persona_loop": "PASS", "score_pass": True}}),
                    encoding="utf-8",
                )
                (player / "network.ndjson").write_text(
                    json.dumps({"url": f"http://127.0.0.1/image?scope={persona}", "status": 200}),
                    encoding="utf-8",
                )
                runs.append(run)

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

            rc, _text, payload = self.run_rri(
                tmp,
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
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("native_gate", {gap["gate"] for gap in payload["evidence_gaps"]})
            self.assertIn("missing run build_sha", " ".join(gap["detail"] for gap in payload["evidence_gaps"]))

    def test_missing_palette_source_file_blocks_release_ready(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            runs = []
            for persona in ("newbie", "veteran", "adversarial", "narrative", "optimizer"):
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
                (run / "run.json").write_text(
                    json.dumps({"build_sha": "deadbee", "part_a": {"result": "PASS"}, "part_b": {"persona_loop": "PASS", "score_pass": True}}),
                    encoding="utf-8",
                )
                (player / "network.ndjson").write_text(
                    json.dumps({"url": f"http://127.0.0.1/image?scope={persona}", "status": 200}),
                    encoding="utf-8",
                )
                runs.append(run)

            story = tmp / "story.json"
            mech = tmp / "mech.json"
            behavioral = tmp / "behavioral.txt"
            audit = tmp / "audit.log"
            missing_palette = tmp / "missing-session-surface.json"
            story.write_text(json.dumps({"overall": 5}), encoding="utf-8")
            mech.write_text(json.dumps({"overall": 5}), encoding="utf-8")
            behavioral.write_text("GREEN\n", encoding="utf-8")
            audit.write_text("PASS\n", encoding="utf-8")

            rc, _text, payload = self.run_rri(
                tmp,
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
                str(missing_palette),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("palette_live", {gap["gate"] for gap in payload["evidence_gaps"]})
            self.assertIn(str(missing_palette), {gap["missing"] for gap in payload["evidence_gaps"]})

    def test_palette_source_label_does_not_require_filesystem_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = tmp / "gate-newbie"
            player = run / "player"
            player.mkdir(parents=True)
            (run / "score.json").write_text(
                json.dumps(
                    {
                        "run": "gate-newbie",
                        "persona": "newbie",
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
            (run / "run.json").write_text(
                json.dumps({"build_sha": "deadbee", "part_a": {"result": "PASS"}, "part_b": {"persona_loop": "PASS", "score_pass": True}}),
                encoding="utf-8",
            )
            (player / "network.ndjson").write_text(
                json.dumps({"url": "http://127.0.0.1/image?scope=newbie", "status": 200}),
                encoding="utf-8",
            )
            story = tmp / "story.json"
            mech = tmp / "mech.json"
            behavioral = tmp / "behavioral.txt"
            audit = tmp / "audit.log"
            story.write_text(json.dumps({"overall": 5}), encoding="utf-8")
            mech.write_text(json.dumps({"overall": 5}), encoding="utf-8")
            behavioral.write_text("GREEN\n", encoding="utf-8")
            audit.write_text("PASS\n", encoding="utf-8")

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                str(run),
                "--expected-personas",
                "newbie",
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
                "session-surface-final",
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertNotIn("palette_live", {gap["gate"] for gap in payload["evidence_gaps"]})
            self.assertEqual(payload["artifact_sources"]["palette_live"], "session-surface-final")


if __name__ == "__main__":
    unittest.main()
