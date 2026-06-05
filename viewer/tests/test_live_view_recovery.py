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

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            body = json.dumps(payload).encode("utf-8")
            conn.request("POST", path, body=body,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, (json.loads(data.decode("utf-8")) if data else {})
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

    # -- symmetric heal across the OTHER play surfaces (the #640 Parley lockout) ----
    def test_play_surfaces_heal_symmetrically_with_session(self):
        """The Parley-tab read-only lockout: /combat-, /atlas-, /parley-, /character-surface must
        self-heal a stale ?campaign to the live run EXACTLY like /session-surface. Before the fix
        they resolved via the non-healing ``_view_campaign`` and latched ``is_live_view=False``
        ("viewing non-live campaign"), so navigating to Parley/Combat during live play stranded the
        player read-only with every social/combat button disabled even though the sink was healthy."""
        self._enable_move_sink()
        self._write("old_save", _snap("Old Save"), age_seconds=600)
        self._write("live_run", _snap("Embergloom"), age_seconds=0)
        self.assertEqual(server._pick_campaign(None), "live_run")

        for route in ("/combat-surface", "/atlas-surface",
                      "/parley-surface", "/character-surface"):
            status, surface = self._get(f"{route}?campaign=old_save")
            self.assertEqual(status, 200, route)
            self.assertTrue(surface.get("is_live_view"),
                            f"{route} must self-heal is_live_view to the live run (not latch False)")
            self.assertEqual(surface.get("campaign_id"), "live_run",
                             f"{route} recovery must follow the live run")
            # atlas/parley/character gate can_act purely on (live AND is_live_view) → it unlocks
            # with the heal. combat additionally needs an active fight, so its recovery is the
            # is_live_view assertion above (a stale view would have latched it False).
            if route != "/combat-surface":
                self.assertTrue(surface.get("can_act"),
                                f"{route} can_act must recover once is_live_view heals")

    def test_play_surfaces_pinned_view_stays_gated(self):
        """The heal must preserve legitimate read-only gating on the other play surfaces too: a
        PINNED director's view of a different campaign stays honestly gated (no auto-snap)."""
        self._enable_move_sink()
        self._write("the_pinned_run", _snap("Pinned"))
        self._write("a_different_save", _snap("Other"))
        _QuietHandler.pinned = True
        _QuietHandler.campaign_id = "the_pinned_run"

        for route in ("/combat-surface", "/atlas-surface",
                      "/parley-surface", "/character-surface"):
            status, surface = self._get(f"{route}?campaign=a_different_save")
            self.assertEqual(status, 200, route)
            self.assertEqual(surface.get("campaign_id"), "a_different_save", route)
            self.assertFalse(surface.get("is_live_view"),
                             f"{route} pinned view of another campaign must stay gated")
            self.assertFalse(surface.get("can_act"), f"{route} pinned view must stay read-only")

    # -- the detach-locks-the-action-bar P0 (dc0d625 re-baseline sweep) --------
    # The slow-but-alive beat: the DM is narrating, the move sink is healthy, but the
    # snapshot/session mtimes have aged past the 90s recency window (a long quiet beat,
    # or the player navigated away for several hops). `_list_campaigns` derives each
    # card's `live` flag purely from recency (`(now - recency) < 90`, server.py:1007),
    # so EVERY card flips live=False — which empties the heal's `live_current` guard
    # (server.py:6401-6404) and the self-heal no-ops, latching is_live_view=False and
    # leaking "live provider move sink is not ready". This is the confirmed trigger.
    def test_stale_snapshot_but_live_sink_recovers_can_act(self):
        """REPRO (sub-case a): a single live run whose snapshot is stale >90s while the move
        sink is live (a long quiet DM beat) must STILL be live + actable — not latch the lock."""
        self._enable_move_sink()
        # One real run, recency aged past the 90s window (DM has been narrating quietly).
        self._write("live_run", _snap("Embergloom"), age_seconds=300)
        self.assertEqual(server._pick_campaign(None), "live_run")
        # Pre-fix: the recency-only live flag is False for the only campaign.
        cards = server._list_campaigns("live_run")
        self.assertTrue(any(c["id"] == "live_run" for c in cards))

        status, surface = self._get("/session-surface?campaign=live_run")
        self.assertEqual(status, 200)
        self.assertTrue(surface["live"],
                        "a live move sink keeps the attached run live despite a stale snapshot")
        self.assertTrue(surface["is_live_view"],
                        "viewing the attached live run must report is_live_view (no false lock)")
        self.assertTrue(surface["can_act"],
                        "can_act must hold while the sink is live — the slow-beat lockout")

        status, payload = self._get("/app-status?campaign=live_run")
        self.assertEqual(status, 200)
        self.assertTrue(payload["live"]["can_act"], "app-status can_act must hold for the live run")
        self.assertNotEqual(payload["readiness"]["failure_bucket"], "no_provider",
                            "the no_provider bucket / raw 'move sink not ready' must NOT fire")

    def test_stale_snapshot_two_campaigns_heals_to_live_run(self):
        """REPRO (sub-case c): two play-store campaigns BOTH aged past 90s while the sink is
        live (player nav-hopped to 'the other Live chronicle'). The stale ?campaign on the
        non-attached run must heal to the attached live run, not latch the lock."""
        self._enable_move_sink()
        # The attached/live run is the most-recently-active; a second chronicle also exists.
        self._write("other_chronicle", _snap("Chapter Two"), age_seconds=200)
        self._write("live_run", _snap("Embergloom"), age_seconds=120)
        self.assertEqual(server._pick_campaign(None), "live_run")

        status, surface = self._get("/session-surface?campaign=other_chronicle")
        self.assertEqual(status, 200)
        self.assertTrue(surface["is_live_view"],
                        "a stale view of the other chronicle must heal to the live run")
        self.assertEqual(surface["campaign_id"], "live_run", "recovery follows the live run")
        self.assertTrue(surface["can_act"], "can_act recovers after the heal")

        # /app-status agrees — the no_provider bucket clears and the action lane is actable again.
        status, payload = self._get("/app-status?campaign=other_chronicle")
        self.assertEqual(status, 200)
        self.assertTrue(payload["live"]["can_act"])
        self.assertTrue(payload["live"]["is_live_view"])
        self.assertEqual(payload["live"]["campaign_id"], "live_run")
        self.assertNotEqual(payload["readiness"]["failure_bucket"], "no_provider")

    def test_dead_sink_with_stale_snapshot_stays_read_only(self):
        """GUARD: when the sink is genuinely dead (file removed/unwritable) AND the snapshot is
        stale, it MUST stay read-only — the slow-beat heal must not fabricate can_act for a dead
        provider. Complements test_no_move_sink_stays_read_only with the stale-snapshot case."""
        os.environ.pop("WORLDOS_PLAYER_MOVES", None)
        os.environ.pop("CLAWDND_PLAYER_MOVES", None)
        self.assertFalse(server._live_play())
        self._write("dead_run", _snap("Disconnected"), age_seconds=300)

        status, surface = self._get("/session-surface?campaign=dead_run")
        self.assertEqual(status, 200)
        self.assertFalse(surface["live"], "no live sink → not live")
        self.assertFalse(surface["can_act"], "dead provider stays honestly read-only")

        status, payload = self._get("/app-status?campaign=dead_run")
        self.assertFalse(payload["live"]["can_act"],
                         "a genuinely dead sink must report can_act=False")
        # The dead sink is honestly NOT ready (degraded). We don't pin the exact bucket here —
        # in the bare test harness `no_art` (missing private art root) can pre-empt `no_provider`
        # in the readiness ladder; either way the run is degraded, never falsely ready.
        self.assertNotEqual(payload["readiness"]["failure_bucket"], "none",
                            "a genuinely dead sink must stay degraded, never ready")
        self.assertFalse(payload["readiness"]["ready_for_play"])

    # -- the /move POST gate is the real write authority for a client retry ----
    def test_move_accepts_untagged_retry_while_sink_live(self):
        """The /move gate (server.py:7035-7062) accepts an UNTAGGED move whenever the sink is
        writable — it never gates on is_live_view. This is what lets a STUCK-turn client retry
        recover: the frozen action bar is a CLIENT gate only; the server would take the re-POST."""
        self._enable_move_sink()
        self._write("live_run", _snap("Embergloom"), age_seconds=300)
        server._Handler.campaign_id = "live_run"

        # An untagged say-move (no `campaign` field): the gate never consults is_live_view, so a
        # stuck-turn client retry lands as long as the sink is writable.
        status, body = self._post("/move", {"kind": "say", "text": "I press on."})
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"), f"untagged retry must be accepted: {body}")

    def test_move_refuses_when_sink_dead(self):
        """The honest-dead-provider path: with no sink, /move refuses — so the client must NOT
        promise a working retry; it surfaces 'Resume from Chronicles' instead of a silent freeze."""
        os.environ.pop("WORLDOS_PLAYER_MOVES", None)
        os.environ.pop("CLAWDND_PLAYER_MOVES", None)
        self.assertFalse(server._live_play())
        server._Handler.campaign_id = "live_run"

        status, body = self._post("/move", {"kind": "say", "text": "I press on."})
        self.assertEqual(status, 200)
        self.assertFalse(body.get("ok"), "a dead sink must refuse the move")
        self.assertIn("read-only", str(body.get("reason", "")))


if __name__ == "__main__":
    unittest.main()
