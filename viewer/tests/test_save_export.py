"""ST-02 / ST-03 viewer wiring: GET /export (chronicle download) + the POST /save-slot and
/load-slot write bridge.

The viewer stays the documented pure reader EXCEPT for the move lane and this new save/load
intent: /save-slot and /load-slot call the engine-owned save_slot/load_slot tools in-process
(the same in-process engine bridge /build-options uses), so the engine remains the sole writer.
These tests exercise the real engine bridge end-to-end (no mocks) plus the export download path.
"""

import http.client
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(server)


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class SaveExportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("CLAWDND_STATE_DIR")
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)
        # Mint a real, model-conformant campaign via the engine so the snapshot on disk is valid
        # for the save/load round-trip (mirrors test_build_options_bridge).
        engine = server._engine_server()
        self.campaign_id = engine.create_campaign("Anchors in Time")["id"]
        # The viewer is "launched on" this campaign so the /move-style live binding allows writes.
        _QuietHandler.campaign_id = self.campaign_id
        _QuietHandler.transcript_path = ""
        _QuietHandler.chat_path = ""
        _QuietHandler.pinned = True
        self._httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self._host, self._port = self._httpd.server_address

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
        if self._old_state is None:
            os.environ.pop("CLAWDND_STATE_DIR", None)
        else:
            os.environ["CLAWDND_STATE_DIR"] = self._old_state

    def _get(self, path: str):
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def _post(self, path: str, payload) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            body = json.dumps(payload).encode("utf-8")
            conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, (json.loads(raw.decode("utf-8")) if raw else {})
        finally:
            conn.close()

    def _snapshot_path(self) -> Path:
        return self._tmp / "campaigns" / self.campaign_id / "snapshot.json"

    # ---- GET /export -----------------------------------------------------

    def test_export_streams_snapshot_verbatim(self):
        status, headers, body = self._get(f"/export?campaign={self.campaign_id}")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/json")
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertIn(f"{self.campaign_id}-chronicle.json", headers.get("Content-Disposition", ""))
        # Byte-for-byte the on-disk snapshot.
        self.assertEqual(body, self._snapshot_path().read_bytes())
        parsed = json.loads(body.decode("utf-8"))
        self.assertEqual(parsed["id"], self.campaign_id)

    def test_export_defaults_to_attached_campaign(self):
        # No ?campaign arg → falls back to the launched/attached campaign.
        status, headers, body = self._get("/export")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode("utf-8"))["id"], self.campaign_id)

    def test_export_unknown_campaign_404s(self):
        status, _headers, _body = self._get("/export?campaign=camp_does_not_exist")
        self.assertEqual(status, 404)

    def test_export_unsafe_campaign_404s(self):
        status, _headers, _body = self._get("/export?campaign=../../etc/passwd")
        self.assertEqual(status, 404)

    # ---- POST /save-slot + /load-slot ------------------------------------

    def test_save_then_load_roundtrip_through_engine(self):
        # Save a quicksave.
        status, payload = self._post("/save-slot", {"campaign": self.campaign_id, "slot": "quicksave"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"], payload)
        slot_file = self._tmp / "campaigns" / self.campaign_id / "slots" / "quicksave.json"
        self.assertTrue(slot_file.exists())

        # Mutate live (advance the in-world day) by editing the snapshot through the engine store.
        snap = json.loads(self._snapshot_path().read_text(encoding="utf-8"))
        snap["day"] = 7
        self._snapshot_path().write_text(json.dumps(snap), encoding="utf-8")

        # Quickload restores day 1.
        status, payload = self._post("/load-slot", {"campaign": self.campaign_id, "slot": "quicksave"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["day"], 1)
        restored = json.loads(self._snapshot_path().read_text(encoding="utf-8"))
        self.assertEqual(restored["day"], 1)

    def test_save_slot_defaults_slot_name(self):
        status, payload = self._post("/save-slot", {"campaign": self.campaign_id})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["slot"], "quicksave")

    def test_load_missing_slot_returns_clean_reason(self):
        status, payload = self._post("/load-slot", {"campaign": self.campaign_id, "slot": "neverwritten"})
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertIn("restore", payload["reason"].lower())

    def test_save_unsafe_campaign_id_refused(self):
        status, payload = self._post("/save-slot", {"campaign": "../secret", "slot": "quicksave"})
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertIn("campaign", payload["reason"])

    def test_save_for_non_live_campaign_refused(self):
        # A save tagged for a DIFFERENT campaign than the attached live run is refused (#49),
        # never misrouted into the live campaign's store.
        status, payload = self._post("/save-slot", {"campaign": "camp_other_live", "slot": "quicksave"})
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertIn("non-live", payload["reason"])

    def test_bad_payload_returns_verdict(self):
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("POST", "/save-slot", body=b"{ not json", headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
