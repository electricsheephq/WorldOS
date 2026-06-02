import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from qa import app_handoff_gate as gate


class AppHandoffGateTests(unittest.TestCase):
    def test_repo_sha_short_resolves_head(self):
        short = gate.repo_sha(short=True)
        full = gate.repo_sha(short=False)

        self.assertNotEqual(short, "unknown")
        self.assertTrue(full.startswith(short))
        self.assertGreaterEqual(len(short), 7)

    def test_run_logged_returns_timeout_exit_code(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "timeout.log"

            rc = gate.run_logged(
                [sys.executable, "-c", "import time; time.sleep(1)"],
                cwd=Path(td),
                env=os.environ.copy(),
                log_path=log,
                timeout=0.05,
            )

            text = log.read_text(encoding="utf-8")
        self.assertEqual(rc, 124)
        self.assertIn("[timeout after", text)
        self.assertIn("[exit 124]", text)

    def test_handoff_score_requires_all_mandatory_gates(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            web = gate.GateResult(name="web_scripted_smoke", provider="scripted", surface="web", build_sha="abc1234")
            web.pass_()
            built = gate.GateResult(name="built_app_scripted_smoke", provider="scripted", surface="dist/WorldOS.app", build_sha="abc1234")
            built.fail("no_app", "dist/WorldOS.app missing")

            verdict = gate.finalize_handoff(
                run_id="fixture",
                out=out,
                gates=[web, built],
                started_at="2026-06-01T00:00:00Z",
                expected_sha="abc1234",
            )

        self.assertEqual(verdict["status"], "failed")
        self.assertEqual(verdict["handoff_score"], 0)
        self.assertEqual(verdict["blockers"][0]["gate"], "built_app_scripted_smoke")
        self.assertEqual(verdict["blockers"][0]["bucket"], "no_app")

    def test_app_status_wrong_port_is_no_launcher(self):
        status = {
            "schema": "worldos.app-status.v1",
            "build": {"sha": "abc1234"},
            "viewer": {"port": 8898, "chat_lines": 1},
            "art": {"private_root_present": True},
            "live": {
                "can_act": True,
                "actor": {"id": "char_1", "name": "Abby"},
                "enabled_action_count": 5,
            },
            "readiness": {"ready_for_smoke": True, "ready_for_play": True, "failure_bucket": "none"},
            "health": {"failure_bucket": "none"},
        }

        bucket, detail = gate.validate_app_status(status, expected_port=8899, expected_sha="abc1234")

        self.assertEqual(bucket, "no_launcher")
        self.assertIn("expected same port 8899", detail)

    def test_app_status_accepts_live_recent_event_narration_when_chat_has_not_landed(self):
        status = {
            "schema": "worldos.app-status.v1",
            "build": {"sha": "abc1234"},
            "viewer": {"port": 8899, "chat_lines": 0},
            "art": {"private_root_present": True},
            "live": {
                "can_act": True,
                "actor": {"id": "char_1", "name": "Alfira"},
                "enabled_action_count": 5,
            },
            "readiness": {"ready_for_smoke": True, "ready_for_play": True, "failure_bucket": "none"},
            "health": {"failure_bucket": "none"},
        }
        surface = {
            "recentEvents": [
                {"kind": "system", "text": "Session began"},
                {"kind": "narration", "text": "The Lower City resolves around you."},
            ]
        }

        bucket, detail = gate.validate_app_status(
            status,
            expected_port=8899,
            expected_sha="abc1234",
            session_surface=surface,
        )

        self.assertEqual(bucket, "")
        self.assertEqual(detail, "")

    def test_app_status_still_requires_chat_or_live_event_narration(self):
        status = {
            "schema": "worldos.app-status.v1",
            "build": {"sha": "abc1234"},
            "viewer": {"port": 8899, "chat_lines": 0},
            "art": {"private_root_present": True},
            "live": {
                "can_act": True,
                "actor": {"id": "char_1", "name": "Alfira"},
                "enabled_action_count": 5,
            },
            "readiness": {"ready_for_smoke": True, "ready_for_play": True, "failure_bucket": "none"},
            "health": {"failure_bucket": "none"},
        }

        bucket, detail = gate.validate_app_status(
            status,
            expected_port=8899,
            expected_sha="abc1234",
            session_surface={"recentEvents": [{"kind": "system", "text": "Session began"}]},
        )

        self.assertEqual(bucket, "no_narration")
        self.assertIn("session-surface", detail)

    def test_codex_provider_trace_cancellations_fail_summary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            provider = root / "codex-provider"
            provider.mkdir()
            (provider / "codex-dm.stderr.log").write_text("tool validation error: extra_forbidden\n", encoding="utf-8")

            summary = gate.provider_trace_summary(root, "codex")

        self.assertEqual(summary["provider"], "codex")
        self.assertGreater(summary["failed_or_error_count"], 0)

    def test_codex_provider_trace_ignores_failed_word_inside_command_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            provider = root / "codex-provider"
            provider.mkdir()
            (provider / "codex-dm.stdout.jsonl").write_text(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "status": "completed",
                            "aggregated_output": "a README says a historical check failed, but this command succeeded",
                            "error": None,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            summary = gate.provider_trace_summary(root, "codex")

        self.assertEqual(summary["failed_or_error_count"], 0)
        self.assertEqual(summary["trace_exists"], True)

    def test_codex_provider_trace_classifies_safety_monitor_500_as_infra_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            provider = root / "codex-provider"
            provider.mkdir()
            (provider / "codex-dm.stderr.log").write_text(
                '2026-06-01T13:39:07.247084Z  WARN codex_core::arc_monitor: safety monitor returned non-success status status=500 Internal Server Error url=https://chatgpt.com/backend-api/codex/safety/arc response_text="{\\"detail\\":\\"safety_monitor_failed\\"}"\n',
                encoding="utf-8",
            )

            summary = gate.provider_trace_summary(root, "codex")

        self.assertEqual(summary["failed_or_error_count"], 0)
        self.assertEqual(summary["provider_infra_warning_count"], 1)
        self.assertIn("safety monitor returned non-success", summary["provider_infra_samples"][0])

    def test_codex_provider_trace_missing_is_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            summary = gate.provider_trace_summary(Path(td), "codex")

        self.assertEqual(summary["provider"], "codex")
        self.assertEqual(summary["trace_exists"], False)
        self.assertEqual(summary["failed_or_error_count"], 0)

    def test_export_evidence_persists_failure_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gate_dir = root / "gate"
            with mock.patch.object(gate, "repo_sha", return_value="abc1234"):
                with mock.patch.object(
                    gate.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(args=[], returncode=17, stdout="", stderr="export broke"),
                ):
                    manifest_path, payload = gate.export_evidence(
                        gate_dir=gate_dir,
                        run_dir=gate_dir,
                        app_status_url="",
                        transition_file=None,
                        command=["fixture"],
                        gate_kind="fixture_gate",
                        provider="scripted",
                    )

            persisted = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

        self.assertEqual(payload["failure"]["failure_bucket"], "no_provider")
        self.assertIn("export_app_evidence exited 17", payload["failure"]["failure_detail"])
        self.assertEqual(persisted, payload)

    def test_evidence_manifest_blockers_include_handoff_gate_reasons(self):
        payload = {
            "evidence_gaps": [],
            "handoff_gate": {
                "ok": False,
                "blocking_reasons": ["can_act not true", "no enabled actions"],
            },
        }

        self.assertEqual(
            gate.evidence_manifest_blockers(payload),
            ["can_act not true", "no enabled actions"],
        )

    def test_run_web_scripted_fails_on_smoke_evidence_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            args = SimpleNamespace(run_id="fixture", web_beats=1, web_port=8899, timeout=1.0, art_root=None)
            smoke_payload = {
                "status": "passed",
                "evidence_gaps": [{"source": "screenshot", "kind": "initial", "reason": "chrome_exit=None"}],
            }
            final_status = {"schema": "worldos.app-status.v1"}

            def fake_read_json(path):
                path = Path(path)
                if path.name == "smoke.json":
                    return smoke_payload
                if path.name == "app-status.final.json":
                    return final_status
                return {}

            with mock.patch.object(gate, "run_logged", return_value=0), mock.patch.object(
                gate,
                "read_json",
                side_effect=fake_read_json,
            ), mock.patch.object(
                gate,
                "validate_app_status",
                return_value=("", ""),
            ), mock.patch.object(
                gate,
                "export_evidence",
                return_value=(out / "manifest.json", {"evidence_gaps": []}),
            ):
                result = gate.run_web_scripted(args, out, "abc1234")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_bucket, "no_provider")
        self.assertIn("smoke evidence gaps: 1", result.failure_detail)
        self.assertEqual(result.evidence_gaps, smoke_payload["evidence_gaps"])

    def test_native_provider_gate_preserves_drive_move_evidence_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            args = SimpleNamespace(
                run_id="fixture",
                world="baldurs-gate",
                art_root=None,
                timeout=1.0,
                codex_timeout=1.0,
            )
            gap = {"source": "screenshot", "kind": "initial", "reason": "chrome_exit=None"}

            def fake_read_json(path):
                path = Path(path)
                if path.name == "run.json":
                    return {
                        "part_a": {
                            "result": "PASS",
                            "kept_backend_alive": True,
                            "first_turn_ready": True,
                            "minted_port": 8767,
                            "minted_run_dir": "play-fixture",
                        }
                    }
                if path.name == "transition.json":
                    return {}
                return {}

            with mock.patch.object(gate, "run_logged", return_value=0), mock.patch.object(
                gate,
                "copy_native_run",
                return_value=None,
            ), mock.patch.object(
                gate,
                "read_json",
                side_effect=fake_read_json,
            ), mock.patch.object(
                gate,
                "drive_moves",
                return_value=(False, "no_provider", "required evidence capture has gaps", {"evidence_gaps": [gap]}),
            ), mock.patch.object(
                gate,
                "export_evidence",
                return_value=(out / "manifest.json", {"evidence_gaps": []}),
            ), mock.patch.object(
                gate,
                "cleanup_run",
                return_value=None,
            ):
                result = gate.run_native_provider_gate(
                    args,
                    out,
                    provider="codex",
                    beats=1,
                    budget="3.00",
                    expected_sha="abc1234",
                )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure_detail, "required evidence capture has gaps")
        self.assertEqual(result.evidence_gaps, [gap])

    def test_hook_probe_summary_reports_exact_missing_controls(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "hook-probe.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "worldos.app-handoff-hooks.v1",
                        "ok": False,
                        "missing_required": ["table:move-submit", "settings:provider-status"],
                        "console_errors": 0,
                    }
                ),
                encoding="utf-8",
            )

            ok, detail, payload = gate.summarize_hook_probe(path)

        self.assertFalse(ok)
        self.assertIn("table:move-submit", detail)
        self.assertIn("settings:provider-status", detail)
        self.assertEqual(payload["schema"], "worldos.app-handoff-hooks.v1")

    def test_drive_moves_tolerates_transient_app_status_timeout(self):
        status_initial = {
            "schema": "worldos.app-status.v1",
            "build": {"sha": "abc1234"},
            "viewer": {"port": 8899, "chat_lines": 1, "last_chat_role": "dm"},
            "art": {"private_root_present": True},
            "live": {
                "can_act": True,
                "actor": {"id": "char_1", "name": "Alfira"},
                "enabled_action_count": 6,
            },
            "readiness": {"ready_for_smoke": True, "ready_for_play": True, "failure_bucket": "none"},
            "health": {"failure_bucket": "none"},
        }
        status_busy = {
            **status_initial,
            "viewer": {"port": 8899, "chat_lines": 2, "last_chat_role": "player"},
            "live": {
                "can_act": False,
                "actor": {"id": "char_1", "name": "Alfira"},
                "enabled_action_count": 0,
                "pending_player_turn": True,
            },
            "readiness": {
                "status": "busy",
                "ready_for_smoke": True,
                "ready_for_play": False,
                "pending_player_turn": True,
                "failure_bucket": "none",
            },
            "health": {"failure_bucket": "none", "pending_player_turn": True},
        }
        status_after = {
            **status_initial,
            "viewer": {"port": 8899, "chat_lines": 3, "last_chat_role": "dm"},
        }
        surface = {"recentEvents": [{"kind": "narration", "text": "Opening."}]}

        with tempfile.TemporaryDirectory() as td:
            gate_dir = Path(td)
            with mock.patch.object(
                gate.smoke,
                "wait_for_status",
                side_effect=[status_initial, TimeoutError("busy status probe"), status_busy, status_after, status_after],
            ), mock.patch.object(
                gate.smoke,
                "fetch_json",
                return_value=(surface, 200),
            ), mock.patch.object(
                gate.smoke,
                "html_text",
                return_value="<main>WorldOS</main>",
            ), mock.patch.object(
                gate.smoke,
                "capture_openworlds_screenshot",
                return_value=None,
            ), mock.patch.object(
                gate.smoke,
                "post_json",
                return_value=({"ok": True}, 200),
            ), mock.patch.object(
                gate.smoke,
                "copy_play_state",
                return_value=None,
            ), mock.patch.object(
                gate,
                "run_hook_probe",
                return_value=(True, "", {"ok": True}),
            ), mock.patch.object(
                gate,
                "provider_trace_summary",
                return_value={"trace_exists": True, "failed_or_error_count": 0},
            ), mock.patch.object(gate.time, "sleep", return_value=None):
                ok, bucket, detail, details = gate.drive_moves(
                    base_url="http://127.0.0.1:8899",
                    gate_dir=gate_dir,
                    run_id="fixture-run",
                    provider="codex",
                    beats=1,
                    timeout=5,
                    expected_sha="abc1234",
                    expected_port=8899,
                )

            network = (gate_dir / "network.ndjson").read_text(encoding="utf-8")
            beat_status = json.loads((gate_dir / "app-status.beat-1.json").read_text(encoding="utf-8"))

        self.assertTrue(ok, detail)
        self.assertEqual(bucket, "")
        self.assertEqual(detail, "")
        self.assertIn("busy status probe", network)
        self.assertEqual(beat_status["viewer"]["last_chat_role"], "dm")
        self.assertEqual(details["provider_trace"]["trace_exists"], True)


if __name__ == "__main__":
    unittest.main()
