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

    def run_exporter(self, out: Path, app_status_url: str, extra: list[str] | None = None) -> tuple[int, str, dict]:
        cmd = [
            sys.executable,
            str(SCRIPT),
            "--app-status-url",
            app_status_url,
            "--out",
            str(out),
        ]
        if extra:
            cmd.extend(extra)
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
            app_status = {
                "schema": "worldos.app-status.v1",
                "build": {"sha": "abc1234", "version": "v1-test"},
                "viewer": {"chat_path": str(chat), "transcript_path": ""},
                "live": {"moves_path": str(moves), "campaign_id": "camp_test"},
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
                rc, text, payload = self.run_exporter(out, url)
            finally:
                server.shutdown()

            self.assertEqual(rc, 0, text)
            self.assertEqual(payload["schema"], "worldos.app-evidence.v1")
            self.assertEqual(payload["build"], {"sha": "abc1234", "version": "v1-test"})
            self.assertEqual(payload["art"]["private_root_present"], True)
            self.assertEqual(payload["sources"]["app_status"]["path"], "app-status.json")
            self.assertEqual(payload["sources"]["session_surface"]["path"], "session-surface.json")
            copied = {entry["kind"]: entry for entry in payload["copied_files"]}
            self.assertEqual(set(copied), {"chat", "moves"})
            self.assertEqual((out / copied["chat"]["path"]).read_text(encoding="utf-8"), chat.read_text(encoding="utf-8"))
            self.assertEqual((out / copied["moves"]["path"]).read_text(encoding="utf-8"), moves.read_text(encoding="utf-8"))
            self.assertEqual(payload["evidence_gaps"], [])

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

    def test_run_dir_artifacts_failure_bucket_and_provider_summary_are_exported(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            run_dir = tmp / "qa-run"
            native = run_dir / "native"
            screenshots = run_dir / "player" / "screenshots"
            native.mkdir(parents=True)
            screenshots.mkdir(parents=True)
            (native / "before.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (screenshots / "step-001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (run_dir / "console.ndjson").write_text('{"type":"console","text":"ok"}\n', encoding="utf-8")
            (run_dir / "network.ndjson").write_text('{"type":"request","url":"/openworlds/"}\n', encoding="utf-8")
            (native / "app-status.minted.json").write_text('{"schema":"worldos.app-status.v1"}\n', encoding="utf-8")
            (native / "transition.json").write_text(
                json.dumps({"failure_bucket": "no_actions", "failure_detail": "action palette was empty"}),
                encoding="utf-8",
            )
            transcript = tmp / "dm.jsonl"
            transcript.write_text(
                '\n'.join([
                    '{"type":"assistant","message":"Opening."}',
                    '{"type":"result","total_cost_usd":0.1234}',
                    "",
                ]),
                encoding="utf-8",
            )
            app_status = {
                "schema": "worldos.app-status.v1",
                "build": {"sha": "abc9999", "version": "v1-artifacts"},
                "viewer": {"chat_path": "", "transcript_path": str(transcript)},
                "live": {"moves_path": "", "campaign_id": "camp_artifacts"},
                "art": {"private_root_present": True},
                "endpoints": {"session_surface": "/session-surface"},
            }
            server, url = self.serve(app_status, {"campaign_id": "camp_artifacts"})
            out = tmp / "bundle"
            try:
                rc, text, payload = self.run_exporter(out, url, ["--run-dir", str(run_dir)])
            finally:
                server.shutdown()

            self.assertEqual(rc, 0, text)
            self.assertEqual(payload["failure_bucket"], "no_actions")
            self.assertEqual(payload["failure"]["detail"], "action palette was empty")
            self.assertEqual(payload["provider_trace_summary"]["line_count"], 2)
            self.assertEqual(payload["provider_trace_summary"]["result_count"], 1)
            self.assertEqual(payload["provider_trace_summary"]["total_cost_usd"], 0.1234)
            self.assertEqual(len(payload["run_artifacts"]["screenshots"]), 2)
            self.assertEqual(len(payload["run_artifacts"]["app_status_snapshots"]), 1)
            self.assertEqual(len(payload["run_artifacts"]["logs"]), 2)
            for entry in payload["run_artifacts"]["screenshots"] + payload["run_artifacts"]["logs"]:
                self.assertTrue((out / entry["path"]).exists(), entry)


if __name__ == "__main__":
    unittest.main()
