"""Regression: the permanent action-lock wedge (G2 release blocker).

The OpenWorlds play screen (Table/Session) gates every action on the read model's
``can_act = live AND is_live_view``, where ``is_live_view`` is true only when the campaign
the browser is VIEWING equals the campaign the move sink is ATTACHED to. The attached
campaign is recency-resolved and re-evaluated every request (the viewer launches UNPINNED
for ``scripts/play.sh`` and the native app), while the browser sends a STICKY ``?campaign=``
from its catalog pick. When those drift — the catalog poll briefly drops ``current``, the
native provider process exits so the client's auto-follow stops re-syncing, or a second save
out-ranks the live run on recency — the client keeps posting a STALE ``viewed``. Before the
fix, ``is_live_view`` latched False with NO recovery, so the table read "live provider move
sink is not ready" / "viewing non-live campaign" forever and every button/the composer was
disabled even though the live move sink was perfectly healthy.

These tests drive the REAL ``/app-status`` + ``/session-surface`` routes (a live HTTP server
on a throwaway port, exactly like ``test_readmodel_surfaces``) and assert:

  1. the wedge reproduces — the stale/empty ``?campaign=`` view that desyncs from the attached
     campaign WOULD report ``is_live_view=False`` under the raw gate; and
  2. the routes RECOVER it — ``can_act`` is true again once the sink is live and the attached
     campaign is the single live run (the self-healing follow); and
  3. legitimate read-only gating is PRESERVED — a PINNED director's view, and a genuinely
     non-live store (no move sink), STAY gated (``can_act=False``).
"""

import http.client
import importlib.util
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path


_SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"
_SPEC = importlib.util.spec_from_file_location("viewer_server", _SERVER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
server = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(server)


# A minimal model-conformant snapshot with a seated PLAYER actor — enough for the action model
# to offer the exploration verbs (so the only thing gating them is the live/live-view state).
def _snap(title: str) -> dict:
    return {
        "title": title,
        "party": ["pc"],
        "characters": {"pc": {"id": "pc", "name": "Vela", "kind": "player"}},
    }


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class LiveViewRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._tmp = Path(self._tmpdir.name)
        self._saved = {k: os.environ.get(k) for k in
                       ("CLAWDND_STATE_DIR", "WORLDOS_STATE_DIR",
                        "CLAWDND_PLAYER_MOVES", "WORLDOS_PLAYER_MOVES")}
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)
        os.environ["WORLDOS_STATE_DIR"] = str(self._tmp)
        # Reset the catalog cache so each test sees its own freshly-written campaigns.
        server._openworlds_catalog_cache = None
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
        for key, val in self._saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    # -- helpers ---------------------------------------------------------------
    def _write(self, campaign_id: str, payload: dict, *, age_seconds: float = 0.0) -> None:
        cdir = self._tmp / "campaigns" / campaign_id
        cdir.mkdir(parents=True, exist_ok=True)
        snap = cdir / "snapshot.json"
        snap.write_text(json.dumps(payload), encoding="utf-8")
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(snap, (old, old))

    def _enable_move_sink(self) -> None:
        moves = self._tmp / "player_moves.jsonl"
        moves.touch()
        os.environ["WORLDOS_PLAYER_MOVES"] = str(moves)
        os.environ["CLAWDND_PLAYER_MOVES"] = str(moves)
        self.assertTrue(server._live_play(), "move sink should read as live")

    def _get(self, path: str) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, (json.loads(body.decode("utf-8")) if body else {})
        finally:
            conn.close()

    # -- the wedge + its recovery ---------------------------------------------
    def test_stale_viewed_campaign_recovers_can_act_on_live_run(self):
        """A stale ?campaign= (the client latched an OLD, non-current save while the live run
        is the attached/current one) recovers to can_act by following the live run."""
        self._enable_move_sink()
        # The live run is the most-recently-active campaign (its session/snapshot advances as the
        # DM narrates) → it is the recency-attached run. A prior, OLDER save also sits in the
        # store; the browser is latched on THAT stale id (e.g. its catalog pick went stale, or the
        # native auto-follow stopped after the provider process exited). Both are real campaigns.
        self._write("old_save", _snap("Old Save"), age_seconds=600)
        self._write("live_run", _snap("Embergloom"), age_seconds=0)
        # Sanity: the live run is the recency-attached campaign the move sink is feeding.
        self.assertEqual(server._pick_campaign(None), "live_run")

        # RAW GATE (pre-fix behavior): viewing the stale old_save while attached==live_run desyncs
        # → exactly what the old route produced (is_live_view=False ⇒ the permanent lock).
        wedged = server.build_session_surface(
            _snap("Old Save"), campaign_id="old_save", live=True, is_live_view=False)
        self.assertFalse(wedged["can_act"], "raw desynced gate must be locked (the wedge)")

        # FIXED ROUTE: /session-surface with the STALE id recovers — the move sink is live and the
        # single current/live run is the attached one, so the gate follows it and unlocks.
        status, surface = self._get("/session-surface?campaign=old_save")
        self.assertEqual(status, 200)
        self.assertTrue(surface["live"])
        self.assertTrue(surface["is_live_view"], "live view must self-heal, not stay desynced")
        self.assertTrue(surface["can_act"], "can_act must recover when the sink is healthy")
        self.assertEqual(surface["campaign_id"], "live_run", "recovery follows the live run")
        # The exploration verbs are enabled again (no "viewing non-live campaign" lock).
        enabled = {a.get("id") for a in surface.get("enabledActions", [])}
        self.assertIn("continue", enabled)

        # /app-status agrees: no_provider bucket cleared, can_act true.
        status, payload = self._get("/app-status?campaign=old_save")
        self.assertEqual(status, 200)
        self.assertTrue(payload["live"]["can_act"])
        self.assertTrue(payload["live"]["is_live_view"])
        self.assertNotEqual(payload["readiness"]["failure_bucket"], "no_provider")

    def test_empty_viewed_campaign_recovers_to_attached_live_run(self):
        """The client sent NO ?campaign= (catalog hadn't latched) — follow the live run."""
        self._enable_move_sink()
        self._write("live_run", _snap("Embergloom"))
        self.assertEqual(server._pick_campaign(None), "live_run")

        status, surface = self._get("/session-surface")  # no ?campaign=
        self.assertEqual(status, 200)
        self.assertTrue(surface["can_act"])
        self.assertEqual(surface["campaign_id"], "live_run")

        status, payload = self._get("/app-status")
        self.assertTrue(payload["live"]["can_act"])
        self.assertEqual(payload["live"]["campaign_id"], "live_run")

    # -- gating that MUST stay intact -----------------------------------------
    def test_pinned_view_stays_gated_on_a_different_campaign(self):
        """A PINNED director's view (launched for a specific id) must NOT auto-snap — a
        genuine read-only view of a different campaign stays honestly gated."""
        self._enable_move_sink()
        self._write("the_pinned_run", _snap("Pinned"))
        self._write("a_different_save", _snap("Other"))
        # Pin the attached campaign explicitly (mirrors `viewer/server.py main()` pinned launch).
        _QuietHandler.pinned = True
        _QuietHandler.campaign_id = "the_pinned_run"

        status, surface = self._get("/session-surface?campaign=a_different_save")
        self.assertEqual(status, 200)
        self.assertEqual(surface["campaign_id"], "a_different_save")
        self.assertFalse(surface["is_live_view"], "pinned view of another campaign stays gated")
        self.assertFalse(surface["can_act"])

    def test_no_move_sink_stays_read_only(self):
        """No live move sink at all → genuinely read-only; recovery must not fabricate can_act."""
        # No _enable_move_sink(): the sink env is unset → _live_play() is False.
        os.environ.pop("WORLDOS_PLAYER_MOVES", None)
        os.environ.pop("CLAWDND_PLAYER_MOVES", None)
        self.assertFalse(server._live_play())
        self._write("some_run", _snap("Read Only"))

        status, surface = self._get("/session-surface?campaign=some_run")
        self.assertEqual(status, 200)
        self.assertFalse(surface["live"])
        self.assertFalse(surface["can_act"], "no live sink → must stay read-only")


if __name__ == "__main__":
    unittest.main()
