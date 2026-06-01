import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "qa" / "export_app_evidence.py"


class EvidenceHandler(BaseHTTPRequestHandler):
    app_status = {}
    session_surface = {}

    def log_message(self, _fmt: str, *_args: object) -> None:
        return

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/app-status"):
            self._json(200, self.app_status)
        elif self.path.startswith("/session-surface"):
            self._json(200, self.session_surface)
        else:
            self._json(404, {"error": "not found"})


class ExportAppEvidenceTests(unittest.TestCase):
    def serve(self, app_status: dict, session_surface: dict) -> tuple[HTTPServer, str]:
        handler = type("TestEvidenceHandler", (EvidenceHandler,), {})
        handler.app_status = app_status
        handler.session_surface = session_surface
        server = HTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, f"http://127.0.0.1:{server.server_port}/app-status?campaign=camp_test"

    def run_exporter(self, out: Path, app_status_url: str = "", extra_args: list[str] | None = None) -> tuple[int, str, dict]:
        cmd = [
            sys.executable,
            str(SCRIPT),
            "--out",
            str(out),
        ]
        if app_status_url:
            cmd.extend(["--app-status-url", app_status_url])
        if extra_args:
            cmd.extend(extra_args)
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
        manifest = out / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
        return proc.returncode, proc.stdout + proc.stderr, payload

    def test_creates_manifest_with_status_surface_and_local_evidence_files(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            chat = tmp / "chat.jsonl"
            moves = tmp / "player_moves.jsonl"
            chat.write_text('{"role":"dm","text":"Opening."}\n', encoding="utf-8")
            moves.write_text('{"text":"Continue."}\n', encoding="utf-8")
            run = tmp / "smoke-run"
            (run / "screenshots").mkdir(parents=True)
            (run / "scripted-provider").mkdir()
            (run / "screenshots" / "beat-001.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            (run / "app-status.final.json").write_text(json.dumps({"schema": "worldos.app-status.v1"}), encoding="utf-8")
            (run / "session-surface.final.json").write_text(json.dumps({"schema": "worldos.session-surface.v1"}), encoding="utf-8")
            (run / "console.ndjson").write_text("", encoding="utf-8")
            (run / "network.ndjson").write_text('{"status":200}\n', encoding="utf-8")
            (run / "actions.ndjson").write_text('{"action":"post_move"}\n', encoding="utf-8")
            (run / "scripted-provider" / "summary.json").write_text('{"provider":"scripted"}\n', encoding="utf-8")
            app_status = {
                "schema": "worldos.app-status.v1",
                "build": {"sha": "abc1234", "version": "v1-test"},
                "viewer": {"chat_path": str(chat), "transcript_path": ""},
                "live": {"moves_path": str(moves), "campaign_id": "camp_test", "run_id": "run_test", "can_act": True, "enabled_action_count": 5},
                "art": {"private_root": str(tmp / "art"), "private_root_present": True},
                "endpoints": {"session_surface": "/session-surface"},
            }
            session_surface = {
                "schema": "worldos.session-surface.v1",
                "campaign_id": "camp_test",
                "can_act": True,
            }
            server, url = self.serve(app_status, session_surface)
            out = tmp / "bundle"
            try:
                rc, text, payload = self.run_exporter(
                    out,
                    url,
                    [
                        "--run-dir",
                        str(run),
                        "--gate-kind",
                        "web_scripted_smoke",
                        "--provider",
                        "scripted",
                        "--command-json",
                        json.dumps(["python3", "qa/app_smoke_scripted.py", "--beats", "5"]),
                        "--started-at",
                        "2026-06-01T00:00:00Z",
                        "--verdict",
                        "passed",
                    ],
                )
            finally:
                server.shutdown()

            self.assertEqual(rc, 0, text)
            self.assertEqual(payload["schema"], "worldos.app-evidence.v1")
            self.assertEqual(payload["build"], {"sha": "abc1234", "version": "v1-test"})
            self.assertEqual(payload["art"]["private_root_present"], True)
            self.assertEqual(payload["sources"]["app_status"]["path"], "app-status.json")
            self.assertEqual(payload["sources"]["session_surface"]["path"], "session-surface.json")
            copied = {entry["kind"]: entry for entry in payload["copied_files"]}
            self.assertIn("chat", copied)
            self.assertIn("moves", copied)
            self.assertEqual((out / copied["chat"]["path"]).read_text(encoding="utf-8"), chat.read_text(encoding="utf-8"))
            self.assertEqual((out / copied["moves"]["path"]).read_text(encoding="utf-8"), moves.read_text(encoding="utf-8"))
            self.assertEqual(payload["evidence_gaps"], [])
            self.assertEqual(payload["handoff_gate"]["schema"], "worldos.app-evidence-handoff.v1")
            self.assertEqual(payload["handoff_gate"]["ok"], True)
            self.assertEqual(payload["handoff_gate"]["build_sha"], "abc1234")
            self.assertEqual(payload["handoff_gate"]["private_art_present"], True)
            self.assertEqual(payload["handoff_gate"]["campaign_id"], "camp_test")
            self.assertEqual(payload["handoff_gate"]["can_act"], True)
            self.assertEqual(payload["handoff_gate"]["run_id"], "run_test")
            self.assertEqual(payload["handoff_gate"]["enabled_action_count"], 5)
            self.assertEqual(payload["handoff_gate"]["move_sink_present"], True)
            self.assertEqual(payload["handoff_gate"]["evidence_gap_count"], 0)
            self.assertEqual(payload["command"], ["python3", "qa/app_smoke_scripted.py", "--beats", "5"])
            self.assertEqual(payload["gate_kind"], "web_scripted_smoke")
            self.assertEqual(payload["provider"], "scripted")
            self.assertEqual(payload["run_id"], "run_test")
            self.assertEqual(payload["app_build_sha"], "abc1234")
            self.assertEqual(payload["verdict"], "passed")
            self.assertEqual(payload["started_at"], "2026-06-01T00:00:00Z")
            review = payload["review_entrypoint"]
            self.assertEqual(review["schema"], "worldos.app-evidence-review-entrypoint.v1")
            self.assertEqual(review["failure_bucket"], "")
            for category in (
                "screenshots",
                "app_status_snapshots",
                "session_surface_snapshots",
                "moves",
                "provider_trace",
                "network_logs",
                "action_logs",
            ):
                self.assertIn(category, review["files"])
            self.assertIn("run-dir/screenshots/beat-001.png", review["files"]["screenshots"])
            self.assertIn("app-status.json", review["files"]["app_status_snapshots"])
            self.assertIn("session-surface.json", review["files"]["session_surface_snapshots"])
            self.assertIn("local-files/moves.jsonl", review["files"]["moves"])
            self.assertIn("run-dir/scripted-provider/summary.json", review["files"]["provider_trace"])
            self.assertIn("run-dir/network.ndjson", review["files"]["network_logs"])
            self.assertIn("run-dir/actions.ndjson", review["files"]["action_logs"])

    def test_missing_optional_local_files_are_recorded_as_evidence_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            missing_chat = tmp / "missing-chat.jsonl"
            missing_moves = tmp / "missing-moves.jsonl"
            app_status = {
                "schema": "worldos.app-status.v1",
                "build": {"sha": "def5678", "version": "v1-gap"},
                "viewer": {"chat_path": str(missing_chat), "transcript_path": ""},
                "live": {"moves_path": str(missing_moves), "campaign_id": "camp_gap"},
                "art": {"private_root": str(tmp / "art"), "private_root_present": False},
                "endpoints": {"session_surface": "/session-surface"},
            }
            server, url = self.serve(app_status, {"campaign_id": "camp_gap"})
            out = tmp / "bundle"
            try:
                rc, text, payload = self.run_exporter(out, url)
            finally:
                server.shutdown()

            self.assertEqual(rc, 0, text)
            self.assertEqual(payload["copied_files"], [])
            self.assertEqual(payload["art"]["status"], "missing")
            gaps = {(gap["source"], gap["kind"]) for gap in payload["evidence_gaps"]}
            self.assertIn(("local_file", "chat"), gaps)
            self.assertIn(("local_file", "moves"), gaps)
            self.assertEqual(payload["handoff_gate"]["ok"], False)
            self.assertEqual(payload["handoff_gate"]["evidence_gap_count"], len(payload["evidence_gaps"]))
            self.assertIn("private art not proven present", payload["handoff_gate"]["blocking_reasons"])
            self.assertIn(f"evidence gaps: {len(payload['evidence_gaps'])}", payload["handoff_gate"]["blocking_reasons"])

    def test_run_dir_mode_copies_allowlisted_artifacts_and_failure_bucket(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = tmp / "smoke-run"
            (run / "screenshots").mkdir(parents=True)
            (run / "a11y").mkdir()
            (run / "scripted-provider").mkdir()
            (run / "native").mkdir()
            (run / "smoke.json").write_text(
                json.dumps(
                    {
                        "schema": "worldos.scripted-app-smoke.v1",
                        "status": "failed",
                        "failure_bucket": "move_rejected",
                        "failure_detail": "POST /move returned 500",
                    }
                ),
                encoding="utf-8",
            )
            (run / "screenshots" / "beat-001.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            (run / "a11y" / "beat-001.html").write_text("<main>fixture</main>\n", encoding="utf-8")
            (run / "actions.ndjson").write_text('{"action":"post_move"}\n', encoding="utf-8")
            (run / "scripted-provider" / "summary.json").write_text('{"provider":"scripted"}\n', encoding="utf-8")
            (run / "scripted-provider" / "trace.ndjson").write_text('{"event":"move_resolved"}\n', encoding="utf-8")
            (run / "native" / "transition.json").write_text('{"failure_bucket":"no_launcher"}\n', encoding="utf-8")
            out = tmp / "bundle"

            rc, text, payload = self.run_exporter(out, extra_args=["--run-dir", str(run)])

            self.assertEqual(rc, 0, text)
            self.assertEqual(payload["sources"]["run_dir"]["ok"], True)
            self.assertEqual(payload["failure"]["failure_bucket"], "move_rejected")
            copied = {entry["path"] for entry in payload["copied_files"]}
            self.assertIn("run-dir/screenshots/beat-001.png", copied)
            self.assertIn("run-dir/a11y/beat-001.html", copied)
            self.assertIn("run-dir/scripted-provider/summary.json", copied)
            self.assertIn("run-dir/scripted-provider/trace.ndjson", copied)
            self.assertIn("run-dir/native/transition.json", copied)
            self.assertEqual(payload["evidence_gaps"], [])
            self.assertEqual(payload["handoff_gate"]["ok"], False)
            self.assertEqual(payload["handoff_gate"]["failure_bucket"], "move_rejected")
            self.assertIn("failure bucket: move_rejected", payload["handoff_gate"]["blocking_reasons"])

    def test_run_dir_snapshots_recover_dead_app_status_url(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run = tmp / "smoke-run"
            run.mkdir()
            (run / "moves.ndjson").write_text('{"text":"Continue."}\n', encoding="utf-8")
            (run / "app-status.final.json").write_text(
                json.dumps(
                    {
                        "schema": "worldos.app-status.v1",
                        "build": {"sha": "abc1234", "version": "v1-test"},
                        "art": {"private_root_present": True},
                        "live": {
                            "campaign_id": "camp_test",
                            "run_id": "run_test",
                            "can_act": True,
                            "enabled_action_count": 3,
                        },
                    }
                ),
                encoding="utf-8",
            )
            (run / "session-surface.final.json").write_text(
                json.dumps({"schema": "worldos.session-surface.v1", "campaign_id": "camp_test"}),
                encoding="utf-8",
            )
            out = tmp / "bundle"

            rc, text, payload = self.run_exporter(
                out,
                "http://127.0.0.1:1/app-status",
                ["--run-dir", str(run), "--gate-kind", "built_app_scripted_smoke", "--provider", "scripted"],
            )

            self.assertEqual(rc, 0, text)
            self.assertEqual(payload["evidence_gaps"], [])
            self.assertEqual(payload["sources"]["app_status"]["recovered_by"], "app_status_snapshot")
            self.assertEqual(payload["sources"]["app_status_snapshot"]["ok"], True)
            self.assertEqual(payload["sources"]["session_surface_snapshot"]["ok"], True)
            self.assertEqual(payload["handoff_gate"]["ok"], True)


if __name__ == "__main__":
    unittest.main()
