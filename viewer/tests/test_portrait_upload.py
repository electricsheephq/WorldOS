"""#376 / #315 AC4: POST /portrait-upload route tests — "Bring your own" portrait.

The Create wizard's StepPortrait advertised "Bring your own — drop a PNG onto any frame" with no
implementation behind it. /portrait-upload is the honest write lane: it lands a user-uploaded
PNG/JPEG as a derived-cache descriptor under a provisional content-scope (portrait-pc-<hash>, the
same shape /portrait-gen mints), so the wizard renders it via <Img scope=…> and bindHero threads
{mode:"gen", scope} through the existing seam (play.sh re-keys it onto the real PC at mint time).

These tests open a loopback socket but NEVER hit any gateway or engine subprocess — the upload is a
pure stdlib descriptor write. They assert:
  - route plumbing: distinct from 404; bad payload -> ok:false (200, never a 500);
  - a valid PNG / JPEG lands a descriptor that GET /image serves back byte-for-byte;
  - oversize / wrong-MIME / not-actually-an-image are REJECTED inline (ok:false, nothing cached);
  - the StepPortrait JSX wires the drop zone + file picker + keyboard reach + the bindHero seam.
"""

import base64
import http.client
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import zlib
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


def _tiny_png() -> bytes:
    """A minimal valid 1x1 PNG (real signature + IHDR/IDAT/IEND), built without Pillow."""
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        return (len(data)).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")

    ihdr = chunk(b"IHDR", (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + bytes([8, 2, 0, 0, 0]))
    raw = b"\x00\xff\x00\x00"  # one scanline: filter byte + one RGB pixel
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _tiny_jpeg() -> bytes:
    """Bytes that START with the JPEG SOI/marker signature (FF D8 FF) — enough for the magic-byte
    guard; the route never decodes pixels."""
    return b"\xff\xd8\xff\xe0" + b"\x00" * 32


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
        return


class PortraitUploadRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("CLAWDND_STATE_DIR")
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)
        _QuietHandler.campaign_id = ""
        _QuietHandler.transcript_path = ""
        _QuietHandler.chat_path = ""
        _QuietHandler.pinned = False
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

    def _post(self, path: str, body: object) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=10)
        try:
            raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode("utf-8")
            conn.request("POST", path, body=raw, headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            data = response.read()
            try:
                parsed = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                parsed = {}
            return response.status, parsed
        finally:
            conn.close()

    def _get(self, path: str) -> tuple[int, str, bytes]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.getheader("Content-Type") or "", response.read()
        finally:
            conn.close()

    # --- route presence + payload validation ----------------------------------- #

    def test_route_is_distinct_from_404(self):
        status_unknown, _ = self._post("/nope-not-a-route", {})
        self.assertEqual(status_unknown, 404)
        status, res = self._post("/portrait-upload", {})  # empty payload still returns a verdict
        self.assertEqual(status, 200)
        self.assertIsInstance(res, dict)
        self.assertFalse(res.get("ok"))

    def test_bad_payload_returns_ok_false(self):
        status, res = self._post("/portrait-upload", b"{ not json")
        self.assertEqual(status, 200)
        self.assertFalse(res.get("ok"))
        self.assertIn("reason", res)

    # --- happy path: a valid image lands + is servable ------------------------- #

    def test_valid_png_lands_and_is_servable_by_image_route(self):
        png = _tiny_png()
        b64 = base64.b64encode(png).decode("ascii")
        status, res = self._post("/portrait-upload", {
            "mime": "image/png", "bytes": b64,
            "race": "human", "class": "fighter", "name": "Eira",
        })
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"), res)
        scope = res.get("scope", "")
        self.assertTrue(scope.startswith("portrait-pc-"), res)
        # The descriptor landed in the provisional scope's cache dir (a derived write).
        cache_dir = self._tmp / "images" / server._safe_scope(scope)
        self.assertTrue(any(cache_dir.glob("*.json")), "expected a cached upload descriptor")
        # GET /image?scope=… serves the EXACT uploaded bytes back (end-to-end render path).
        gstatus, ctype, body = self._get(f"/image?scope={scope}")
        self.assertEqual(gstatus, 200)
        self.assertEqual(ctype, "image/png")
        self.assertEqual(body, png)

    def test_valid_jpeg_accepted(self):
        b64 = base64.b64encode(_tiny_jpeg()).decode("ascii")
        status, res = self._post("/portrait-upload", {"mime": "image/jpeg", "bytes": b64})
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"), res)

    def test_data_url_prefix_is_stripped(self):
        b64 = base64.b64encode(_tiny_png()).decode("ascii")
        status, res = self._post("/portrait-upload", {
            "mime": "image/png", "bytes": "data:image/png;base64," + b64,
        })
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"), res)

    # --- rejections: nothing is cached, an inline reason is returned ----------- #

    def test_unsupported_mime_rejected(self):
        b64 = base64.b64encode(b"hello").decode("ascii")
        status, res = self._post("/portrait-upload", {"mime": "text/plain", "bytes": b64})
        self.assertEqual(status, 200)
        self.assertFalse(res.get("ok"))
        self.assertIn("reason", res)

    def test_non_image_bytes_with_image_mime_rejected(self):
        # A renamed .txt/.exe claiming image/png — the magic-byte guard must reject it.
        b64 = base64.b64encode(b"this is definitely not a PNG").decode("ascii")
        status, res = self._post("/portrait-upload", {"mime": "image/png", "bytes": b64})
        self.assertEqual(status, 200)
        self.assertFalse(res.get("ok"), res)

    def test_oversize_rejected(self):
        # 6 MB of valid PNG-prefixed bytes -> over the 5 MB cap -> rejected, nothing cached.
        big = _tiny_png() + b"\x00" * (6 * 1024 * 1024)
        res = server._portrait_upload({"mime": "image/png", "bytes": base64.b64encode(big).decode("ascii")})
        self.assertFalse(res.get("ok"))
        self.assertIn("5 MB", res.get("reason", ""))

    def test_empty_bytes_rejected(self):
        status, res = self._post("/portrait-upload", {"mime": "image/png", "bytes": ""})
        self.assertEqual(status, 200)
        self.assertFalse(res.get("ok"))

    def test_bad_base64_rejected(self):
        res = server._portrait_upload({"mime": "image/png", "bytes": "!!!not base64!!!"})
        self.assertFalse(res.get("ok"))

    # --- determinism + idempotence -------------------------------------------- #

    def test_same_image_same_inputs_is_idempotent(self):
        png = _tiny_png()
        b64 = base64.b64encode(png).decode("ascii")
        payload = {"mime": "image/png", "bytes": b64, "race": "elf", "class": "rogue", "name": "Sable"}
        r1 = server._portrait_upload(dict(payload))
        r2 = server._portrait_upload(dict(payload))
        self.assertEqual(r1.get("scope"), r2.get("scope"))
        cache_dir = self._tmp / "images" / server._safe_scope(r1["scope"])
        self.assertEqual(len(list(cache_dir.glob("*.json"))), 1)  # one descriptor, not a pile

    # --- StepPortrait UI wiring (#376) ----------------------------------------- #

    def _get_source(self, path: str) -> str:
        status, _ctype, body = self._get(path)
        self.assertEqual(status, 200)
        return body.decode("utf-8")

    def test_screen_create_has_byo_upload_affordance(self):
        src = self._get_source("/openworlds/screen-create.jsx")
        # A real drop zone + file input exist and POST the dedicated upload route.
        self.assertIn('data-worldos-testid="portrait-byo-dropzone"', src)
        self.assertIn('type="file"', src)
        self.assertIn('accept="image/png,image/jpeg"', src)
        self.assertIn("/portrait-upload", src)
        self.assertIn("Choose file", src)

    def test_byo_dropzone_is_keyboard_accessible(self):
        src = self._get_source("/openworlds/screen-create.jsx")
        # AC4: focusable, role=button, aria-label, Space/Enter open the picker; drop is captured.
        self.assertIn('role="button"', src)
        self.assertIn("tabIndex={0}", src)
        self.assertIn('aria-label="Bring your own portrait"', src)
        self.assertIn("onByoKeyDown", src)
        self.assertIn("onByoDrop", src)
        self.assertIn("e.preventDefault()", src)

    def test_byo_threads_through_bindhero_seam(self):
        src = self._get_source("/openworlds/screen-create.jsx")
        # An accepted upload joins the generated-face seam so bindHero carries it as {mode:"gen"}.
        self.assertIn('portraitMode: "gen"', src)
        self.assertIn("portraitGenScope: res.scope", src)

    def test_broken_promise_text_removed(self):
        src = self._get_source("/openworlds/screen-create.jsx")
        # AC6: the old advertise-but-don't-implement hand-text must be gone now that it works.
        self.assertNotIn("drop a PNG onto any frame to replace it", src)


if __name__ == "__main__":
    unittest.main()
