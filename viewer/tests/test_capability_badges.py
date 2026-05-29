"""
test_capability_badges.py — assert that each display-only OpenWorlds screen
contains a <CapabilityBadge> component, signalling to players that the screen
is a prototype backed by demo data rather than a live engine read-model.

Mirrors the style of test_openworlds_static.py.
"""
import http.client
import importlib.util
import os
import tempfile
import threading
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


class CapabilityBadgeTests(unittest.TestCase):
    """Each display-only screen must declare a CapabilityBadge so players know
    the screen is not yet backed by live engine state."""

    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("CLAWDND_STATE_DIR")
        self._old_here = server._HERE
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
        server._HERE = self._old_here

    def _get(self, path: str) -> tuple[int, str, bytes]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.headers.get("Content-Type", ""), response.read()
        finally:
            conn.close()

    def _assert_has_capability_badge(self, screen_path: str) -> None:
        status, ctype, body = self._get(screen_path)
        self.assertEqual(status, 200, f"{screen_path} should return HTTP 200")
        self.assertIn("text/babel", ctype, f"{screen_path} should be served as text/babel")
        source = body.decode("utf-8")
        self.assertIn(
            "CapabilityBadge",
            source,
            f"{screen_path} must render a <CapabilityBadge> so players know it is display-only",
        )

    # v1.0.2: the Merchant screen's "Preview" CapabilityBadge banner was removed
    # as part of the UI honesty cleanup (Phase-4 wiring already lifted BUY →
    # POST /move on can_act, so the surface is no longer prototype-only; the
    # global TitleBar badges were retired in app.jsx capabilityForScreen).
    #
    # v1.0.2 (honesty cleanup, cont.): the bestiary / create / seed banners were
    # likewise retired — bestiary is a live /bestiary-surface read model and
    # create binds the hero through the engine, so neither is prototype-only;
    # the seed screen keeps its disabled "Sow the change" button (with a quiet
    # tooltip) to stay honest about re-seeding not being wired, but no longer
    # carries the loud banner.
    #
    # UI audit (2026-05-29): the FORGE banner was the last surface-level
    # CapabilityBadge and is now retired too — the "Forge it" action relays a real
    # `check` move to the DM via POST /move when can_act (the wiring has landed;
    # see screen-forge.jsx `craft`), so the surface is no longer prototype-only.
    # The craft button itself stays honest with a can_act-aware label + tooltip
    # (live → "relays to the DM"; not-live → "simulated locally, not saved"),
    # which is the endorsed per-button gating, not a loud surface badge. With this,
    # NO OpenWorlds screen carries a surface CapabilityBadge banner; the component
    # survives only for the genuine native-bridge capability notice on the Settings
    # screen (screen-settings.jsx), which is intentionally NOT a prototype badge.
    # Hence there is no longer a per-screen banner test to assert here.

    def test_no_surface_capability_badge_on_play_screens(self):
        # Regression lock for the honesty cleanup: the play surfaces must NOT
        # reintroduce a surface-level CapabilityBadge banner. (Settings keeps a
        # genuine native-bridge badge and is intentionally excluded.)
        for screen in (
            "/openworlds/screen-forge.jsx",
            "/openworlds/screen-merchant.jsx",
            "/openworlds/screen-bestiary.jsx",
            "/openworlds/screen-map.jsx",
            "/openworlds/screen-combat.jsx",
            "/openworlds/screen-dialogue.jsx",
            "/openworlds/camp-sidebar.jsx",
        ):
            status, ctype, body = self._get(screen)
            self.assertEqual(status, 200, f"{screen} should return HTTP 200")
            self.assertNotIn(
                "CapabilityBadge",
                body.decode("utf-8"),
                f"{screen} must NOT carry a surface CapabilityBadge banner "
                f"(honesty cleanup — use per-button can_act gating instead)",
            )


if __name__ == "__main__":
    unittest.main()
