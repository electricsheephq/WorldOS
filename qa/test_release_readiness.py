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

    def write_handoff_bundle(
        self,
        tmp: Path,
        *,
        sha: str = "deadbee",
        manifest_sha: str | None = None,
        reuse_manifest: bool = False,
        app_status_overrides: dict | None = None,
    ) -> Path:
        handoff_root = tmp / "handoff"
        handoff_root.mkdir()
        gates = []
        first_manifest_path: Path | None = None
        for gate_name in ("web_scripted_smoke", "built_app_scripted_smoke", "built_app_codex_playtest"):
            evidence_dir = handoff_root / gate_name
            manifest_path = first_manifest_path if reuse_manifest and first_manifest_path else evidence_dir / "app-evidence" / "manifest.json"
            first_manifest_path = first_manifest_path or manifest_path
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            if not manifest_path.exists():
                manifest_path.write_text(
                    json.dumps(
                        {
                            "schema": "worldos.app-evidence.v1",
                            "verdict": "passed",
                            "dirty": False,
                            "app_build_sha": manifest_sha or sha,
                            "provider": "codex" if gate_name == "built_app_codex_playtest" else "scripted",
                            "provider_family": "codex-openai" if gate_name == "built_app_codex_playtest" else "scripted",
                            "auth_surface": "codex-cli" if gate_name == "built_app_codex_playtest" else "dev-scripted",
                            "dm_model": "gpt-5.5" if gate_name == "built_app_codex_playtest" else "scripted",
                            "player_agent": "codex" if gate_name == "built_app_codex_playtest" else "scripted",
                            "player_model": "gpt-5.5" if gate_name == "built_app_codex_playtest" else "scripted",
                            "scorer_provider": "codex-openai" if gate_name == "built_app_codex_playtest" else "deterministic-scripted",
                            "scorer_model": "gpt-5.5" if gate_name == "built_app_codex_playtest" else "scripted",
                            "provider_metadata": {
                                "provider": "codex" if gate_name == "built_app_codex_playtest" else "scripted",
                                "provider_family": "codex-openai" if gate_name == "built_app_codex_playtest" else "scripted",
                                "auth_surface": "codex-cli" if gate_name == "built_app_codex_playtest" else "dev-scripted",
                                "dm_model": "gpt-5.5" if gate_name == "built_app_codex_playtest" else "scripted",
                                "player_agent": "codex" if gate_name == "built_app_codex_playtest" else "scripted",
                                "player_model": "gpt-5.5" if gate_name == "built_app_codex_playtest" else "scripted",
                                "scorer_provider": "codex-openai" if gate_name == "built_app_codex_playtest" else "deterministic-scripted",
                                "scorer_model": "gpt-5.5" if gate_name == "built_app_codex_playtest" else "scripted",
                            },
                            "evidence_gaps": [],
                            "failure_bucket": "",
                            "failure_detail": "",
                            "failure": {"failure_bucket": "", "failure_detail": ""},
                            "gate_kind": gate_name,
                            "art": {"private_root_present": True},
                            "live": {"can_act": True, "enabled_action_count": 5},
                            "handoff_gate": {
                                "ok": True,
                                "app_status_ok": True,
                                "session_surface_ok": True,
                                "move_sink_present": True,
                                "private_art_present": True,
                                "can_act": True,
                                "enabled_action_count": 5,
                                "evidence_gap_count": 0,
                            },
                            "evidence_files": {
                                "screenshots": ["screenshots/final.png"],
                                "app_status_snapshots": ["app-status.final.json"],
                                "session_surface_snapshots": ["session-surface.final.json"],
                                "moves": ["moves.ndjson"],
                                "provider_trace": ["provider-trace-summary.json"],
                                "console_logs": ["console.ndjson"],
                                "network_logs": ["network.ndjson"],
                                "action_logs": ["actions.ndjson"],
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            for evidence_file in (
                "screenshots/final.png",
                "session-surface.final.json",
                "moves.ndjson",
                "provider-trace-summary.json",
                "console.ndjson",
                "network.ndjson",
                "actions.ndjson",
            ):
                path = manifest_path.parent / evidence_file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("ok\n", encoding="utf-8")
            app_status = {
                "schema": "worldos.app-status.v1",
                "state_authority": "engine",
                "write_lane": "/move",
                "build": {"sha": manifest_sha or sha},
                "live": {
                    "campaign_id": "camp_test",
                    "can_act": True,
                    "enabled_action_count": 5,
                    "moves_writable": True,
                },
            }
            if app_status_overrides:
                app_status.update(app_status_overrides)
            (manifest_path.parent / "app-status.final.json").write_text(json.dumps(app_status), encoding="utf-8")
            gates.append(
                {
                    "name": gate_name,
                    "status": "passed",
                    "build_sha": sha,
                    "evidence_gaps": [],
                    "evidence_manifest": str(manifest_path),
                }
            )
        handoff = handoff_root / "handoff.json"
        handoff.write_text(
            json.dumps(
                {
                    "schema": "worldos.app-handoff.v1",
                    "status": "passed",
                    "handoff_score": 100,
                    "dirty": False,
                    "commit_sha": sha,
                    "release_verdict": False,
                    "gates": gates,
                }
            ),
            encoding="utf-8",
        )
        return handoff

    def write_support_preflight(
        self,
        tmp: Path,
        *,
        sha: str = "deadbee",
        ready: bool = True,
        blocking_categories: list[str] | None = None,
    ) -> Path:
        preflight = tmp / "support_vm_preflight.json"
        blocking_categories = blocking_categories or []
        preflight.write_text(
            json.dumps(
                {
                    "schema": "worldos.support-vm-preflight.v1",
                    "verdict": "passed" if ready else "blocked",
                    "ready_for_rri": ready,
                    "release_verdict": False,
                    "blockers": [] if ready else ["host memory below required capacity"],
                    "repo": {
                        "head_short": sha,
                        "expected_sha": sha,
                        "expected_sha_match": True,
                        "dirty": False,
                        "origin_main_query": {"ok": True, "head_short": sha},
                    },
                    "readiness": {
                        "safe_to_run_personas": ready,
                        "release_verdict": False,
                        "expected_sha": sha,
                        "repo_head_short": sha,
                        "same_sha_ready": ready,
                        "provider": "codex",
                        "player_agent": "codex",
                        "provider_auth_ready": ready,
                        "player_agent_auth_ready": ready,
                        "required_tools_ready": ready,
                        "persona_briefs_ready": ready,
                        "private_art_ready": ready,
                        "artifact_return_ready": ready,
                        "host_capacity_ready": ready,
                        "min_memory_gb": 24,
                        "mac_handoff_required": True,
                        "blocking_categories": blocking_categories,
                    },
                    "rri_plan": {
                        "support_preflight_json": str(preflight),
                        "support_preflight_required_for_split_rollup": True,
                        "rri_rollup_command_template": (
                            "python3 qa/release_readiness.py --runs VM_PERSONA_RUN_DIRS_CSV "
                            "--handoff-json SAME_SHA_MAC_HANDOFF_JSON "
                            "--support-preflight-json support_vm_preflight.json "
                            f"--build-sha {sha}"
                        ),
                    },
                }
            ),
            encoding="utf-8",
        )
        return preflight

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
        include_image_traffic: bool = True,
        image_status: int = 200,
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
        (run / "run.json").write_text(json.dumps(payload), encoding="utf-8")
        if include_image_traffic:
            (player / "network.ndjson").write_text(
                json.dumps({"url": f"http://127.0.0.1/image?scope={persona}", "status": image_status}),
                encoding="utf-8",
            )
        else:
            # A VM sweep that never recorded /image traffic at all (the gitignored
            # _private art case): network.ndjson exists but carries no image rows.
            (player / "network.ndjson").write_text("", encoding="utf-8")
        return run

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
                        "console_errors": 0,
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
            self.assertEqual(payload["signals"]["image_render_source"], "none")
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

    def test_split_vm_persona_evidence_uses_handoff_json_for_native_gate(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp)
            support_preflight = self.write_support_preflight(tmp)

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--support-preflight-json",
                str(support_preflight),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 0)
            self.assertTrue(payload["release_ready"])
            self.assertEqual(payload["signals"]["native_gate"], "PASS")
            self.assertEqual(payload["signals"]["native_gate_source"], str(handoff))
            self.assertEqual(payload["artifact_sources"]["handoff_json"], str(handoff))
            self.assertTrue(payload["signals"]["handoff_proof"]["valid"])
            self.assertEqual(payload["artifact_sources"]["support_preflight_json"], str(support_preflight))
            self.assertTrue(payload["signals"]["support_preflight"]["valid"])
            self.assertTrue(payload["support_preflight_evidence"]["valid"])
            self.assertEqual(payload["support_preflight_evidence"]["evidence_gaps"], [])
            self.assertEqual(payload["handoff_evidence"]["path"], str(handoff))
            self.assertTrue(payload["handoff_evidence"]["valid"])
            self.assertEqual(payload["handoff_evidence"]["evidence_gaps"], [])
            self.assertEqual(
                sorted(payload["handoff_evidence"]["gates"]),
                ["built_app_codex_playtest", "built_app_scripted_smoke", "web_scripted_smoke"],
            )
            self.assertEqual(payload["handoff_evidence"]["gates"]["built_app_codex_playtest"]["manifest_verdict"], "passed")
            self.assertIn("handoff_json=", payload["gate_detail"]["native_gate"])
            self.assertEqual(payload["signals"]["image_render_source"], "vm-network")

    def test_image_render_accepts_same_sha_mac_handoff_when_vm_has_no_denominator(self):
        # The split VM+Mac lane: the VM cannot serve gitignored _private art, so its
        # network.ndjson never records /image traffic. A valid same-SHA Mac handoff whose
        # app-status snapshots prove health.image_probe_ok (and whose manifests prove the
        # private art root) is accepted as the image_render gate source.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [
                self.write_persona_run(tmp, persona, include_part_a=False, include_image_traffic=False)
                for persona in personas
            ]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(
                tmp,
                app_status_overrides={"health": {"image_probe_ok": True}},
            )
            support_preflight = self.write_support_preflight(tmp)

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--support-preflight-json",
                str(support_preflight),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 0)
            self.assertTrue(payload["release_ready"])
            self.assertNotIn("image_render", payload["failed_gates"])
            self.assertEqual(payload["signals"]["image_render_source"], "mac-handoff")
            self.assertEqual(payload["signals"]["image_request_denominator"], 0)
            # Honest record: the per-persona VM gap is still visible in signals even
            # though the Mac handoff carries the gate.
            self.assertEqual(sorted(payload["signals"]["image_missing_personas"]), sorted(personas))
            self.assertTrue(payload["handoff_evidence"]["image_evidence"]["image_probe_ok"])
            self.assertTrue(payload["handoff_evidence"]["image_evidence"]["art_root_present"])
            self.assertNotIn("image_render", {gap["gate"] for gap in payload["evidence_gaps"]})
            self.assertIn("mac-handoff", payload["gate_detail"]["image_render"])

    def test_image_render_handoff_without_image_probe_keeps_evidence_gap(self):
        # A handoff that is valid for native_gate but whose app-status snapshots never
        # proved health.image_probe_ok must NOT carry the image_render gate.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [
                self.write_persona_run(tmp, persona, include_part_a=False, include_image_traffic=False)
                for persona in personas
            ]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp)  # no health.image_probe_ok in app-status
            support_preflight = self.write_support_preflight(tmp)

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--support-preflight-json",
                str(support_preflight),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("image_render", payload["failed_gates"])
            self.assertEqual(payload["signals"]["image_render_source"], "none")
            self.assertFalse(payload["handoff_evidence"]["image_evidence"]["image_probe_ok"])
            self.assertIn("image_render", {gap["gate"] for gap in payload["evidence_gaps"]})
            # native_gate is still allowed to ride the handoff — only image_render
            # demanded the extra image evidence.
            self.assertEqual(payload["signals"]["native_gate"], "PASS")

    def test_vm_image_denominators_take_precedence_over_mac_handoff(self):
        # When the VM DID record /image traffic, the real rate is the gate source —
        # a same-SHA Mac handoff with image evidence cannot paper over recorded 404s.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [
                self.write_persona_run(tmp, persona, include_part_a=False, image_status=404)
                for persona in personas
            ]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(
                tmp,
                app_status_overrides={"health": {"image_probe_ok": True}},
            )
            support_preflight = self.write_support_preflight(tmp)

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--support-preflight-json",
                str(support_preflight),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("image_render", payload["failed_gates"])
            self.assertEqual(payload["signals"]["image_render_source"], "vm-network")
            self.assertEqual(payload["signals"]["image_request_denominator"], 5)
            self.assertEqual(payload["signals"]["image_render_rate"], 0.0)

    def test_split_vm_persona_evidence_requires_support_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp)

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("native_gate", payload["failed_gates"])
            self.assertIn("support_preflight", {gap["gate"] for gap in payload["evidence_gaps"]})
            self.assertIn("--support-preflight-json", {gap["missing"] for gap in payload["evidence_gaps"]})

    def test_blocked_support_preflight_blocks_split_vm_release_ready(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp)
            support_preflight = self.write_support_preflight(tmp, ready=False, blocking_categories=["host_capacity"])

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--support-preflight-json",
                str(support_preflight),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("native_gate", payload["failed_gates"])
            self.assertFalse(payload["support_preflight_evidence"]["valid"])
            self.assertIn("host_capacity", payload["support_preflight_evidence"]["readiness"]["blocking_categories"])
            self.assertIn("support_preflight", {gap["gate"] for gap in payload["evidence_gaps"]})

    def test_stale_support_preflight_blocks_split_vm_release_ready(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp)
            support_preflight = self.write_support_preflight(tmp, sha="badcafe")

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--support-preflight-json",
                str(support_preflight),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("native_gate", payload["failed_gates"])
            self.assertFalse(payload["support_preflight_evidence"]["valid"])
            support_gaps = [gap for gap in payload["evidence_gaps"] if gap["gate"] == "support_preflight"]
            self.assertTrue(support_gaps)
            self.assertIn("badcafe", " ".join(gap["detail"] for gap in support_gaps))

    def test_split_vm_support_preflight_requires_rollup_contract(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp)
            support_preflight = self.write_support_preflight(tmp)
            preflight_payload = json.loads(support_preflight.read_text(encoding="utf-8"))
            preflight_payload.pop("rri_plan", None)
            support_preflight.write_text(json.dumps(preflight_payload), encoding="utf-8")

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--support-preflight-json",
                str(support_preflight),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("native_gate", payload["failed_gates"])
            self.assertFalse(payload["support_preflight_evidence"]["valid"])
            support_gaps = [gap for gap in payload["evidence_gaps"] if gap["gate"] == "support_preflight"]
            self.assertTrue(support_gaps)
            self.assertIn("support_preflight_required_for_split_rollup", " ".join(gap["detail"] for gap in support_gaps))
            self.assertIn("rri_rollup_command_template", " ".join(gap["detail"] for gap in support_gaps))

    def test_direct_native_evidence_ignores_optional_stale_support_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            support_preflight = self.write_support_preflight(tmp, sha="badcafe")

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--support-preflight-json",
                str(support_preflight),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 0)
            self.assertTrue(payload["release_ready"])
            self.assertNotIn("support_preflight", {gap["gate"] for gap in payload["evidence_gaps"]})
            self.assertNotIn("native_gate", payload["failed_gates"])
            self.assertFalse(payload["support_preflight_evidence"]["valid"])

    def test_support_preflight_origin_main_query_must_be_object(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp)
            support_preflight = self.write_support_preflight(tmp)
            payload = json.loads(support_preflight.read_text(encoding="utf-8"))
            payload["repo"]["origin_main_query"] = "not-a-dict"
            support_preflight.write_text(json.dumps(payload), encoding="utf-8")

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--support-preflight-json",
                str(support_preflight),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertFalse(payload["support_preflight_evidence"]["valid"])
            self.assertIn("support_preflight", {gap["gate"] for gap in payload["evidence_gaps"]})
            self.assertIn("origin/main query", " ".join(gap["detail"] for gap in payload["evidence_gaps"]))

    def test_handoff_json_must_prove_engine_state_authority(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp, app_status_overrides={"state_authority": "viewer"})

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertFalse(payload["signals"]["handoff_proof"]["valid"])
            self.assertIn("app-status state_authority viewer does not prove engine authority", " ".join(gap["detail"] for gap in payload["evidence_gaps"]))

    def test_handoff_json_must_prove_move_write_lane(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp, app_status_overrides={"write_lane": "/snapshot"})

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertFalse(payload["signals"]["handoff_proof"]["valid"])
            self.assertIn("app-status write_lane /snapshot does not prove /move intent writes", " ".join(gap["detail"] for gap in payload["evidence_gaps"]))

    def test_handoff_json_must_prove_app_status_build_sha(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp, app_status_overrides={"build": {"sha": "badcafe"}})

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertFalse(payload["signals"]["handoff_proof"]["valid"])
            self.assertIn("app-status build.sha badcafe does not match --build-sha deadbee", " ".join(gap["detail"] for gap in payload["evidence_gaps"]))

    def test_missing_handoff_json_blocks_native_gate_when_persona_runs_have_no_part_a(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
            self.assertIn("native_gate", payload["failed_gates"])
            self.assertIn("run.json part_a.result or --handoff-json", {gap["missing"] for gap in payload["evidence_gaps"]})

    def test_handoff_sha_mismatch_blocks_native_gate(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp, sha="badcafe")

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("native_gate", payload["failed_gates"])
            self.assertIn("handoff commit_sha badcafe does not match --build-sha deadbee", " ".join(gap["detail"] for gap in payload["evidence_gaps"]))
            self.assertFalse(payload["signals"]["handoff_proof"]["valid"])

    def test_short_build_sha_prefix_cannot_mix_stale_vm_and_mac_handoff_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, sha="4524b3e", include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp, sha="4a0efe1")

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--build-sha",
                "4",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            self.assertIn("native_gate", payload["failed_gates"])
            self.assertTrue(payload["evidence_gaps"])

    def test_handoff_manifest_must_match_gate_and_not_be_reused(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona, include_part_a=False) for persona in personas]
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)
            handoff = self.write_handoff_bundle(tmp, reuse_manifest=True)

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
                "--handoff-json",
                str(handoff),
                "--build-sha",
                "deadbee",
            )

            self.assertEqual(rc, 1)
            self.assertFalse(payload["release_ready"])
            details = " ".join(gap["detail"] for gap in payload["evidence_gaps"])
            self.assertIn("reuses another gate's manifest", details)
            self.assertIn("manifest gate_kind web_scripted_smoke does not match built_app_scripted_smoke", details)

    def test_part_b_score_pass_false_blocks_release_even_with_high_scores(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona) for persona in personas]
            run_json = json.loads((runs[2] / "run.json").read_text(encoding="utf-8"))
            run_json["part_b"]["score_pass"] = False
            (runs[2] / "run.json").write_text(json.dumps(run_json), encoding="utf-8")
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
            self.assertEqual(payload["failed_gates"], ["cross_persona_sat"])
            self.assertEqual(payload["signals"]["score_pass_failed_personas"], ["adversarial"])

    def test_malformed_score_json_is_harness_contaminated_and_incomplete(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            personas = ("newbie", "veteran", "adversarial", "narrative", "optimizer")
            runs = [self.write_persona_run(tmp, persona) for persona in personas]
            (runs[1] / "score.json").write_text("{not json", encoding="utf-8")
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

            rc, _text, payload = self.run_rri(
                tmp,
                "--runs",
                ",".join(str(r) for r in runs),
                "--expected-personas",
                ",".join(personas),
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
            self.assertTrue(payload["harness_contaminated"])
            self.assertIn("veteran", payload["missing_personas"])
            self.assertEqual(payload["harness_failures"][0]["missing"], "score.json invalid")
            self.assertIn("invalid JSON", payload["harness_failures"][0]["detail"])

    def test_score_missing_console_errors_is_harness_contaminated(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = self.write_persona_run(tmp, "newbie")
            score = json.loads((run / "score.json").read_text(encoding="utf-8"))
            score.pop("console_errors")
            (run / "score.json").write_text(json.dumps(score), encoding="utf-8")
            story, mech, behavioral, audit, palette = self.write_release_inputs(tmp)

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
            self.assertTrue(payload["harness_contaminated"])
            self.assertEqual(payload["completed_personas"], [])
            self.assertEqual(payload["harness_failures"][0]["missing"], "score.json required fields")
            self.assertIn("console_errors must be integer", payload["harness_failures"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
