"""Tests for tools/ingest/wiki_images.py (W2b: wiki image-ingest pipeline).

Covers:
  - write_descriptor: writes image bytes + provenance sidecar + viewer descriptor
  - _safe_scope / _private_images_dir: path sanitisation
  - _ext_for_mime: MIME → extension mapping
  - viewer _ingested_descriptor: scope → descriptor lookup
  - viewer _latest_descriptor: ingested-first resolution order
  - viewer /image endpoint: serves ingested asset; 404s when absent

All tests are offline (no network). A temp fixture PNG is used instead of real wiki
images. The viewer is imported via importlib (same pattern as test_openworlds_static.py).
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

# ---- import tools/ingest/wiki_images ----------------------------------------
_INGEST = Path(__file__).resolve().parents[3] / "tools" / "ingest"
sys.path.insert(0, str(_INGEST))
import wiki_images  # noqa: E402  (path-insert must precede import)

# ---- import viewer/server ---------------------------------------------------
_SERVER_PATH = Path(__file__).resolve().parents[3] / "viewer" / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server_wi", _SERVER_PATH)
assert _SPEC is not None
_viewer = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_viewer)

# A minimal valid 1×1 PNG (not just random bytes — keeps MIME probing honest).
_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ---------------------------------------------------------------------------
# wiki_images unit tests
# ---------------------------------------------------------------------------

class TestSafeScopeAndPaths(unittest.TestCase):
    def test_safe_scope_strips_colons(self):
        # Colons (e.g. "portrait:shadowheart") become underscores.
        safe = wiki_images._safe_scope("portrait:shadowheart")
        self.assertEqual(safe, "portrait_shadowheart")

    def test_safe_scope_allows_alnum_dash_underscore(self):
        self.assertEqual(wiki_images._safe_scope("scene-elfsong_1"), "scene-elfsong_1")

    def test_safe_scope_empty(self):
        self.assertEqual(wiki_images._safe_scope(""), "")
        self.assertEqual(wiki_images._safe_scope(None), "")

    def test_safe_scope_length_cap(self):
        long_scope = "a" * 200
        self.assertEqual(len(wiki_images._safe_scope(long_scope)), 128)

    def test_private_images_dir_is_under_private(self):
        d = wiki_images._private_images_dir("baldurs-gate", "portrait:shadowheart")
        # Must be under content/worlds/_private/baldurs-gate/images/
        self.assertIn("_private", str(d))
        self.assertIn("baldurs-gate", str(d))
        self.assertTrue(str(d).endswith("portrait_shadowheart"))


class TestExtForMime(unittest.TestCase):
    def test_jpeg(self):
        self.assertEqual(wiki_images._ext_for_mime("image/jpeg", "https://x/foo.jpg"), ".jpg")

    def test_png(self):
        ext = wiki_images._ext_for_mime("image/png", "https://x/foo.png")
        self.assertEqual(ext, ".png")

    def test_fallback_from_url(self):
        # When MIME is unknown, fall back to URL extension.
        ext = wiki_images._ext_for_mime("application/octet-stream", "https://x/foo.webp")
        self.assertEqual(ext, ".webp")

    def test_bin_when_unknown(self):
        ext = wiki_images._ext_for_mime("application/octet-stream", "https://x/foo")
        self.assertEqual(ext, ".bin")


class TestWriteDescriptor(unittest.TestCase):
    def setUp(self):
        self._tmp = self.enterContext(tempfile.TemporaryDirectory())
        # Patch _REPO_ROOT in wiki_images so writes land in our tmpdir.
        self._orig_root = wiki_images._REPO_ROOT
        wiki_images._REPO_ROOT = Path(self._tmp)

    def tearDown(self):
        wiki_images._REPO_ROOT = self._orig_root

    def test_writes_image_and_descriptor(self):
        desc_path = wiki_images.write_descriptor(
            "baldurs-gate",
            "portrait:shadowheart",
            _PNG_1X1,
            "image/png",
            source_url="https://bg3.wiki/w/images/Shadowheart.png",
            page_url="https://bg3.wiki/wiki/Shadowheart",
            license="CC BY-SA 4.0 / CC BY-NC-SA 4.0",
            attribution="Image from bg3.wiki, dual-licensed CC BY-SA 4.0 / CC BY-NC-SA 4.0.",
        )
        # Descriptor file was written.
        self.assertTrue(desc_path.exists())
        desc = json.loads(desc_path.read_text())
        self.assertEqual(desc["scope"], "portrait:shadowheart")
        self.assertIn("path", desc)
        self.assertEqual(desc["mime_type"], "image/png")
        self.assertIn("source_url", desc)
        self.assertIn("license", desc)
        self.assertIn("attribution", desc)

    def test_image_bytes_written(self):
        wiki_images.write_descriptor(
            "baldurs-gate",
            "portrait:astarion",
            _PNG_1X1,
            "image/png",
            source_url="https://bg3.wiki/w/images/Astarion.png",
            page_url="https://bg3.wiki/wiki/Astarion",
            license="CC BY-SA 4.0",
            attribution="bg3.wiki",
        )
        out_dir = wiki_images._private_images_dir("baldurs-gate", "portrait:astarion")
        img_files = list(out_dir.glob("image.*"))
        img_files = [f for f in img_files if not f.name.endswith(".provenance.json")]
        self.assertEqual(len(img_files), 1)
        self.assertEqual(img_files[0].read_bytes(), _PNG_1X1)

    def test_provenance_sidecar_written(self):
        wiki_images.write_descriptor(
            "baldurs-gate",
            "scene:elfsong",
            _PNG_1X1,
            "image/jpeg",
            source_url="https://bg3.wiki/w/images/Elfsong.jpg",
            page_url="https://bg3.wiki/wiki/Elfsong_Tavern",
            license="CC BY-SA 4.0",
            attribution="bg3.wiki",
        )
        out_dir = wiki_images._private_images_dir("baldurs-gate", "scene:elfsong")
        prov_files = list(out_dir.glob("*.provenance.json"))
        self.assertEqual(len(prov_files), 1)
        prov = json.loads(prov_files[0].read_text())
        self.assertEqual(prov["source_url"], "https://bg3.wiki/w/images/Elfsong.jpg")
        self.assertIn("license", prov)
        self.assertIn("attribution", prov)
        self.assertIn("fetched_at", prov)

    def test_descriptor_path_is_under_private(self):
        desc_path = wiki_images.write_descriptor(
            "baldurs-gate",
            "portrait:gale",
            _PNG_1X1,
            "image/png",
            source_url="https://bg3.wiki/w/images/Gale.png",
            page_url="https://bg3.wiki/wiki/Gale",
            license="CC BY-SA 4.0",
            attribution="bg3.wiki",
        )
        # The descriptor must live under _private (containment).
        self.assertIn("_private", str(desc_path))

    def test_path_traversal_neutralised_by_safe_scope(self):
        """Path-traversal characters in the scope are sanitised by _safe_scope.

        '../../etc/passwd' becomes '__etc_passwd' — it lands inside _private and
        never escapes. The protection is the sanitiser, not a raised exception.
        """
        desc_path = wiki_images.write_descriptor(
            "baldurs-gate",
            "../../etc/passwd",
            _PNG_1X1,
            "image/png",
            source_url="x",
            page_url="x",
            license="",
            attribution="",
        )
        # Output must be inside the _private tree.
        self.assertIn("_private", str(desc_path))
        # Must NOT be near /etc/.
        self.assertNotIn("/etc/", str(desc_path))

    def test_descriptor_filename_constant(self):
        desc_path = wiki_images.write_descriptor(
            "baldurs-gate",
            "portrait:wyll",
            _PNG_1X1,
            "image/png",
            source_url="https://bg3.wiki/w/images/Wyll.png",
            page_url="https://bg3.wiki/wiki/Wyll",
            license="CC BY-SA 4.0",
            attribution="bg3.wiki",
        )
        self.assertEqual(desc_path.name, wiki_images._DESCRIPTOR_FILENAME)


class TestIngestManifestDryRun(unittest.TestCase):
    def setUp(self):
        self._tmp = self.enterContext(tempfile.TemporaryDirectory())
        self._orig_root = wiki_images._REPO_ROOT
        wiki_images._REPO_ROOT = Path(self._tmp)

    def tearDown(self):
        wiki_images._REPO_ROOT = self._orig_root

    def test_dry_run_does_not_write(self):
        manifest = {
            "world_id": "baldurs-gate",
            "rate_delay_seconds": 0,
            "sources": [{
                "wiki": "bg3.wiki",
                "script_path": "/w",
                "license": "CC BY-SA 4.0",
                "attribution": "bg3.wiki",
                "images": [
                    {"title": "Shadowheart", "scope": "portrait:shadowheart", "kind": "portrait"}
                ],
            }]
        }
        mf_path = Path(self._tmp) / "manifest_images.json"
        mf_path.write_text(json.dumps(manifest))
        # dry-run must return cleanly and write nothing.
        rc = wiki_images.ingest_manifest(mf_path, max_override=None, dry_run=True)
        self.assertEqual(rc, 0)
        private_dir = Path(self._tmp) / "content" / "worlds" / "_private"
        # Nothing should have been written.
        self.assertFalse(private_dir.exists())


# ---------------------------------------------------------------------------
# Viewer _ingested_descriptor + _latest_descriptor integration
# ---------------------------------------------------------------------------

class _QuietHandler(_viewer._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class TestViewerIngestedDescriptor(unittest.TestCase):
    """_ingested_descriptor looks up wiki_ingest.json across _private world dirs."""

    def setUp(self):
        self._tmp = self.enterContext(tempfile.TemporaryDirectory())
        self._old_repo_root = _viewer._REPO_ROOT
        _viewer._REPO_ROOT = Path(self._tmp)

    def tearDown(self):
        _viewer._REPO_ROOT = self._old_repo_root

    def _write_ingested(self, world_id: str, scope: str, extra: dict | None = None) -> Path:
        """Write a minimal wiki_ingest.json descriptor for testing."""
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in scope)[:128]
        d = Path(self._tmp) / "content" / "worlds" / "_private" / world_id / "images" / safe
        d.mkdir(parents=True, exist_ok=True)
        img = d / "image.png"
        img.write_bytes(_PNG_1X1)
        desc = {
            "scope": scope,
            "path": str(img),
            "mime_type": "image/png",
            "source_url": "https://bg3.wiki/w/images/Test.png",
            "license": "CC BY-SA 4.0",
            "attribution": "bg3.wiki",
        }
        if extra:
            desc.update(extra)
        desc_path = d / "wiki_ingest.json"
        desc_path.write_text(json.dumps(desc))
        return desc_path

    def test_finds_ingested_descriptor(self):
        self._write_ingested("baldurs-gate", "portrait:shadowheart")
        result = _viewer._ingested_descriptor("portrait:shadowheart")
        self.assertIsNotNone(result)
        self.assertEqual(result["scope"], "portrait:shadowheart")
        self.assertEqual(result["mime_type"], "image/png")

    def test_returns_none_when_absent(self):
        result = _viewer._ingested_descriptor("portrait:nonexistent")
        self.assertIsNone(result)

    def test_safe_scope_used_for_lookup(self):
        # "portrait:shadowheart" safe-scopes to "portrait_shadowheart".
        self._write_ingested("baldurs-gate", "portrait:shadowheart")
        # Lookup using the original (colon) scope should still work.
        result = _viewer._ingested_descriptor("portrait:shadowheart")
        self.assertIsNotNone(result)

    def test_scope_key_normalizes_prefix_and_separator(self):
        sk = _viewer._scope_key
        self.assertEqual(sk("portrait-npc-shadowheart"), "shadowheart")
        self.assertEqual(sk("portrait:shadowheart"), "shadowheart")
        self.assertEqual(sk("portrait-shadowheart"), "shadowheart")
        self.assertEqual(sk("loc-elfsong-tavern"), "elfsong-tavern")
        self.assertEqual(sk("scene:elfsong-tavern"), "elfsong-tavern")
        self.assertEqual(sk("npc-the-emperor"), "the-emperor")
        self.assertEqual(sk(""), "")

    def test_ingested_descriptor_normalized_match(self):
        # ingested under the manifest slug; the UI fetches the ENGINE-ID scope — must resolve
        self._write_ingested("baldurs-gate", "portrait:shadowheart")
        self.assertIsNotNone(_viewer._ingested_descriptor("portrait-npc-shadowheart"))
        self.assertIsNotNone(_viewer._ingested_descriptor("portrait-shadowheart"))
        # a scene ingested as scene:<slug>; the UI fetches the location id
        self._write_ingested("baldurs-gate", "scene:elfsong-tavern")
        self.assertIsNotNone(_viewer._ingested_descriptor("loc-elfsong-tavern"))
        # an unrelated scope still misses (no false positives)
        self.assertIsNone(_viewer._ingested_descriptor("portrait-npc-gibberish"))

    def test_empty_scope_returns_none(self):
        self.assertIsNone(_viewer._ingested_descriptor(""))
        self.assertIsNone(_viewer._ingested_descriptor(None))


class TestViewerLatestDescriptorOrder(unittest.TestCase):
    """_latest_descriptor: ingested asset wins over generated cache."""

    def setUp(self):
        self._tmp = self.enterContext(tempfile.TemporaryDirectory())
        self._old_repo_root = _viewer._REPO_ROOT
        self._old_state = os.environ.get("WORLDOS_STATE_DIR")
        _viewer._REPO_ROOT = Path(self._tmp)
        os.environ["WORLDOS_STATE_DIR"] = str(self._tmp)

    def tearDown(self):
        _viewer._REPO_ROOT = self._old_repo_root
        if self._old_state is None:
            os.environ.pop("WORLDOS_STATE_DIR", None)
        else:
            os.environ["WORLDOS_STATE_DIR"] = self._old_state

    def _write_ingested(self, world_id: str, scope: str) -> None:
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in scope)[:128]
        d = Path(self._tmp) / "content" / "worlds" / "_private" / world_id / "images" / safe
        d.mkdir(parents=True, exist_ok=True)
        img = d / "image.png"
        img.write_bytes(_PNG_1X1)
        desc = {"scope": scope, "path": str(img), "mime_type": "image/png", "source": "ingested"}
        (d / "wiki_ingest.json").write_text(json.dumps(desc))

    def _write_generated(self, scope: str) -> None:
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in scope)[:128]
        d = Path(self._tmp) / "images" / safe
        d.mkdir(parents=True, exist_ok=True)
        (d / "abc123.json").write_text(json.dumps({"scope": scope, "source": "generated"}))

    def test_ingested_wins_over_generated(self):
        self._write_ingested("baldurs-gate", "portrait:shadowheart")
        self._write_generated("portrait:shadowheart")
        result = _viewer._latest_descriptor("portrait:shadowheart")
        self.assertIsNotNone(result)
        self.assertEqual(result.get("source"), "ingested")

    def test_generated_used_when_no_ingested(self):
        self._write_generated("portrait:nobody")
        result = _viewer._latest_descriptor("portrait:nobody")
        self.assertIsNotNone(result)
        self.assertEqual(result.get("source"), "generated")

    def test_returns_none_when_nothing(self):
        result = _viewer._latest_descriptor("portrait:empty")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Viewer /image endpoint: serves ingested asset; 404s on absent scope
# ---------------------------------------------------------------------------

class TestViewerImageEndpoint(unittest.TestCase):
    """/image?scope=X serves ingested asset bytes; 404s when no descriptor."""

    def setUp(self):
        self._tmp = self.enterContext(tempfile.TemporaryDirectory())
        self._old_repo_root = _viewer._REPO_ROOT
        self._old_state = os.environ.get("WORLDOS_STATE_DIR")
        _viewer._REPO_ROOT = Path(self._tmp)
        os.environ["WORLDOS_STATE_DIR"] = str(self._tmp)
        _QuietHandler.campaign_id = ""
        _QuietHandler.transcript_path = ""
        _QuietHandler.chat_path = ""
        _QuietHandler.pinned = False
        self._httpd = _viewer.ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self._host, self._port = self._httpd.server_address

    def tearDown(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)
        _viewer._REPO_ROOT = self._old_repo_root
        if self._old_state is None:
            os.environ.pop("WORLDOS_STATE_DIR", None)
        else:
            os.environ["WORLDOS_STATE_DIR"] = self._old_state

    def _get(self, path: str) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, body

    def _write_ingested(self, world_id: str, scope: str, img_bytes: bytes = _PNG_1X1) -> Path:
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in scope)[:128]
        d = Path(self._tmp) / "content" / "worlds" / "_private" / world_id / "images" / safe
        d.mkdir(parents=True, exist_ok=True)
        img = d / "image.png"
        img.write_bytes(img_bytes)
        desc = {"scope": scope, "path": str(img), "mime_type": "image/png"}
        (d / "wiki_ingest.json").write_text(json.dumps(desc))
        return img

    def test_serves_ingested_image_bytes(self):
        img_path = self._write_ingested("baldurs-gate", "portrait:shadowheart")
        status, body = self._get("/image?scope=portrait:shadowheart")
        self.assertEqual(status, 200)
        self.assertEqual(body, _PNG_1X1)

    def test_404_when_absent(self):
        status, _ = self._get("/image?scope=portrait:nobody")
        self.assertEqual(status, 404)

    def test_404_when_empty_scope(self):
        status, _ = self._get("/image?scope=")
        self.assertEqual(status, 404)

    def test_path_traversal_scope_does_not_escape(self):
        # A scope with traversal characters resolves to a sanitised safe-scope
        # and returns 404 (not a file from outside _private).
        status, body = self._get("/image?scope=../../etc/passwd")
        # Either 404 (scope not found) or 200 with our dummy bytes — never /etc/passwd.
        if status == 200:
            self.assertEqual(body, _PNG_1X1)
        else:
            self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
