"""#265: POST /portrait-gen route tests — opt-in "Generate a unique face".

These tests NEVER hit the OpenClaw gateway and NEVER open a real socket. The route
inherits the process env (provider unset -> null), so on a normal box the call returns
a placeholder with no network. To keep the suite hermetic AND fast we stub the engine
subprocess (subprocess.run) so the route's plumbing — payload validation, provisional
scope keying, env-inheritance, verdict shaping — is exercised without spawning uv.

The end-to-end null-provider-returns-placeholder-with-no-network property is proven on
the ENGINE side (servers/engine/tests/test_imagegen.py): the default provider is null and
touches no network. Here we assert the route NEVER forces CLAWDND_IMAGE_PROVIDER=openclaw,
which is the one thing that could engage the gateway.
"""

import http.client
import importlib.util
import json
import os
import subprocess
import tempfile
import threading
import types
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
        return


class PortraitGenRouteTests(unittest.TestCase):
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

    # --- route presence + payload validation ------------------------------------ #

    def test_bad_payload_returns_ok_false(self):
        status, res = self._post("/portrait-gen", b"{ not json")
        self.assertEqual(status, 200)
        self.assertFalse(res.get("ok"))
        self.assertIn("reason", res)

    def test_missing_race_class_returns_ok_false(self):
        status, res = self._post("/portrait-gen", {"name": "Eira"})
        self.assertEqual(status, 200)
        self.assertFalse(res.get("ok"))

    def test_route_is_distinct_from_404(self):
        # An unknown POST route 404s; /portrait-gen must be handled (a JSON verdict, 200).
        status_unknown, _ = self._post("/nope-not-a-route", {})
        self.assertEqual(status_unknown, 404)
        status, res = self._post("/portrait-gen", {"race": "human", "class": "fighter"})
        self.assertEqual(status, 200)
        self.assertIsInstance(res, dict)

    # --- StepPortrait UI wiring (#265) ----------------------------------------- #

    def _get_source(self, path: str) -> str:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            self.assertEqual(response.status, 200)
            return response.read().decode("utf-8")
        finally:
            conn.close()

    def test_screen_create_has_generate_unique_face_affordance(self):
        src = self._get_source("/openworlds/screen-create.jsx")
        # The opt-in button + its in-flight ("generating") label must be present.
        self.assertIn("Generate a unique face", src)
        self.assertIn("Summoning a face", src)
        # It POSTs the dedicated route (not /move) and reads the appearance cues.
        self.assertIn("/portrait-gen", src)
        self.assertIn("appearance", src)

    def test_screen_create_spec_carries_portrait_choice(self):
        src = self._get_source("/openworlds/screen-create.jsx")
        # bindHero's spec must carry the portrait choice (the gap #265 closes) with both modes.
        self.assertIn("portraitMode", src)
        self.assertIn('mode: "gen"', src)
        self.assertIn('mode: "gallery"', src)
        # The gallery stays the DEFAULT (initial hero state).
        self.assertIn('portraitMode: "gallery"', src)

    def test_screen_create_preview_uses_gen_scope(self):
        src = self._get_source("/openworlds/screen-create.jsx")
        # The summary/review render the generated face when in gen mode (heroPortraitScope).
        self.assertIn("heroPortraitScope", src)
        self.assertIn("portraitGenScope", src)


class PortraitGenEngineTests(unittest.TestCase):
    """Exercise the _portrait_gen plumbing with a STUBBED engine subprocess so no uv is
    spawned and no socket is ever opened — while still asserting the no-gateway guarantee."""

    def setUp(self):
        self._old_state = os.environ.get("CLAWDND_STATE_DIR")
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)
        # Capture what env the route would hand the engine subprocess.
        self._captured = {}

        def _fake_run(cmd, input=None, capture_output=None, text=None, timeout=None, env=None):
            self._captured["cmd"] = cmd
            self._captured["env"] = env or {}
            self._captured["input"] = input
            req = json.loads(input) if input else {}
            verdict = {
                "ok": True,
                "scope": req.get("scope"),
                "generated": False,
                "placeholder": True,  # null provider on a normal box -> placeholder
                "provider": "null",
                "degraded_from": None,
                "prompt": "stub",
            }
            return types.SimpleNamespace(stdout=json.dumps(verdict) + "\n", stderr="", returncode=0)

        self._old_run = subprocess.run
        subprocess.run = _fake_run  # type: ignore[assignment]
        server.subprocess.run = _fake_run  # the module-level reference the route uses

    def tearDown(self):
        subprocess.run = self._old_run  # type: ignore[assignment]
        server.subprocess.run = self._old_run
        if self._old_state is None:
            os.environ.pop("CLAWDND_STATE_DIR", None)
        else:
            os.environ["CLAWDND_STATE_DIR"] = self._old_state

    def test_no_provider_returns_placeholder_verdict(self):
        os.environ.pop("CLAWDND_IMAGE_PROVIDER", None)
        res = server._portrait_gen({"race": "half", "class": "wizard", "name": "Eira"})
        self.assertTrue(res.get("ok"))
        self.assertTrue(res.get("placeholder"))
        self.assertFalse(res.get("generated"))
        self.assertTrue(str(res.get("scope", "")).startswith("portrait-pc-"))

    def test_route_never_forces_openclaw_provider(self):
        # THE guardrail: the route must not set CLAWDND_IMAGE_PROVIDER=openclaw. With no
        # provider in the parent env, the child env must NOT carry an openclaw provider.
        os.environ.pop("CLAWDND_IMAGE_PROVIDER", None)
        server._portrait_gen({"race": "human", "class": "fighter"})
        child_env = self._captured["env"]
        self.assertNotEqual(child_env.get("CLAWDND_IMAGE_PROVIDER", ""), "openclaw")
        # It DOES bound the interactive poll so a slow gateway can't hang the call.
        self.assertEqual(child_env.get("CLAWDND_OPENCLAW_POLL_TIMEOUT"), "60")

    def test_route_inherits_host_provider_unchanged(self):
        # If the HOST already configured a provider, the route inherits it verbatim — it
        # neither forces nor strips it (so a deliberately-configured box still works).
        os.environ["CLAWDND_IMAGE_PROVIDER"] = "openclaw"
        try:
            server._portrait_gen({"race": "elf", "class": "rogue"})
            self.assertEqual(self._captured["env"].get("CLAWDND_IMAGE_PROVIDER"), "openclaw")
        finally:
            os.environ.pop("CLAWDND_IMAGE_PROVIDER", None)

    def test_provisional_scope_is_deterministic(self):
        a = server._portrait_gen_scope("human", "fighter", "Eira", None)
        b = server._portrait_gen_scope("human", "fighter", "Eira", None)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("portrait-pc-"))
        # Different inputs -> different scope (so distinct PCs don't collide).
        c = server._portrait_gen_scope("elf", "fighter", "Eira", None)
        self.assertNotEqual(a, c)

    def test_engine_dir_points_at_servers_engine(self):
        self.assertEqual(server._ENGINE_DIR.name, "engine")
        self.assertTrue((server._ENGINE_DIR / "imagegen.py").is_file())


class PortraitGenRealSubprocessTests(unittest.TestCase):
    """One real end-to-end check: run the actual engine subprocess with the DEFAULT (null)
    provider and assert it returns a placeholder verdict — the strongest proof the route
    opens NO socket on a normal box. Skipped if `uv` isn't available."""

    def setUp(self):
        if not __import__("shutil").which("uv"):
            self.skipTest("uv not installed")
        self._old_state = os.environ.get("CLAWDND_STATE_DIR")
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)
        self._old_provider = os.environ.pop("CLAWDND_IMAGE_PROVIDER", None)

    def tearDown(self):
        if self._old_state is None:
            os.environ.pop("CLAWDND_STATE_DIR", None)
        else:
            os.environ["CLAWDND_STATE_DIR"] = self._old_state
        if self._old_provider is not None:
            os.environ["CLAWDND_IMAGE_PROVIDER"] = self._old_provider

    def test_null_default_returns_placeholder_no_network(self):
        res = server._portrait_gen({"race": "half", "class": "wizard", "name": "Eira", "appearance": "scarred"})
        # No provider configured -> null -> placeholder, generated False, no exception.
        self.assertTrue(res.get("ok"), res)
        self.assertTrue(res.get("placeholder"), res)
        self.assertFalse(res.get("generated"), res)
        self.assertEqual(res.get("provider"), "null")
        # The descriptor landed in the provisional scope's cache dir (a derived write).
        scope = res["scope"]
        cache_dir = self._tmp / "images" / server._safe_scope(scope)
        self.assertTrue(any(cache_dir.glob("*.json")), "expected a cached placeholder descriptor")


if __name__ == "__main__":
    unittest.main()
