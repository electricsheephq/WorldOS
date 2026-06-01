import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
