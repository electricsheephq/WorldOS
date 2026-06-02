"""Route + projection tests for /bestiary-surface intel-tiering (#263).

The bestiary surface is the read-only bridge to the engine's player-safe bestiary projection.
This lane adds campaign scope: when the request resolves a campaign with earned
``bestiary_intel`` (creature_slug -> max tier), the engine reveals stats per intel tier
(1=sighted, 2=engaged, 3=slain). With NO campaign (or no snapshot / no recorded intel) the
surface stays the honest global SRD browse — an empty/new game is never a stat dump.

Mirrors the wired-surface test harness (test_readmodel_surfaces.py): a threaded server over a
temp state dir, snapshots written to campaigns/<id>/snapshot.json, GETs over http.client.
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
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


class _QuietHandler(server._Handler):
    def log_message(self, *args, **kwargs):  # silence access logging in tests
        pass


class BestiarySurfaceTests(unittest.TestCase):
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

    def _write(self, campaign_id: str, payload: dict) -> None:
        cdir = self._tmp / "campaigns" / campaign_id
        cdir.mkdir(parents=True)
        (cdir / "snapshot.json").write_text(json.dumps(payload), encoding="utf-8")

    def _get_json(self, path: str) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, (json.loads(body.decode("utf-8")) if body else {})
        finally:
            conn.close()

    # ── no campaign → honest global preview (no tiering, no leakage) ─────────────

    def test_no_campaign_is_global_preview(self):
        status, surface = self._get_json("/bestiary-surface?q=goblin")
        self.assertEqual(status, 200)
        items = surface.get("items", [])
        self.assertTrue(items)
        for item in items:
            # the pre-#263 preview shape: identity + cr + known_actions, never tier / ac / hp
            self.assertNotIn("tier", item)
            self.assertNotIn("ac", item)
            self.assertNotIn("hp", item)
            self.assertIn("name", item)

    # ── campaign with intel → tiered reveal ──────────────────────────────────────

    def test_campaign_intel_reveals_tiers(self):
        self._write("camp_intel", {"id": "camp_intel", "bestiary_intel": {"goblin-warrior": 2}})
        status, surface = self._get_json("/bestiary-surface?campaign=camp_intel&q=goblin")
        self.assertEqual(status, 200)
        by_name = {i.get("name"): i for i in surface.get("items", [])}
        gw = by_name.get("Goblin Warrior")
        self.assertIsNotNone(gw)
        # engaged (tier 2): AC + speed + senses revealed, vitals still gated
        self.assertEqual(gw["tier"], 2)
        self.assertIn("ac", gw)
        self.assertIn("speed", gw)
        self.assertIn("senses", gw)
        self.assertNotIn("hp", gw)
        self.assertNotIn("saves", gw)

    def test_campaign_intel_tier3_full_reveal(self):
        self._write("camp_slain", {"id": "camp_slain", "bestiary_intel": {"aboleth": 3}})
        status, surface = self._get_json("/bestiary-surface?campaign=camp_slain&q=aboleth")
        self.assertEqual(status, 200)
        ab = {i.get("name"): i for i in surface["items"]}.get("Aboleth")
        self.assertIsNotNone(ab)
        self.assertEqual(ab["tier"], 3)
        for k in ("ac", "speed", "senses", "hp", "hit_dice", "abilities", "saves"):
            self.assertIn(k, ab)

    def test_unencountered_creatures_are_rumour_rows(self):
        # intel records only the wolf; a goblin search returns blurred tier-0 rumours.
        self._write("camp_rumour", {"id": "camp_rumour", "bestiary_intel": {"wolf": 1}})
        status, surface = self._get_json("/bestiary-surface?campaign=camp_rumour&q=goblin")
        self.assertEqual(status, 200)
        items = surface.get("items", [])
        self.assertTrue(items)
        # none of the goblins were encountered → all tier-0 unknowns, no stats leaked
        for item in items:
            self.assertEqual(item.get("tier"), 0)
            self.assertTrue(item.get("unknown"))
            self.assertNotIn("ac", item)
            self.assertNotIn("cr", item)
            self.assertNotIn("name", item)   # the real name is withheld (#263)
            self.assertIn("id_hint", item)   # only an opaque render key rides along

    def test_reference_mode_browses_all_bypassing_intel(self):
        # BE-depth (optimizer #1): ?reference=1 must BYPASS earned intel and return the public
        # SRD browse — NAMED creatures with preview stats — even for a campaign that has slain
        # nothing. Without this the codex is perpetually fog-of-war ("zero creature names").
        self._write("camp_ref", {"id": "camp_ref", "bestiary_intel": {"wolf": 1}})
        # The same campaign+query returns redacted rumour rows WITHOUT reference:
        _s0, gated = self._get_json("/bestiary-surface?campaign=camp_ref&q=goblin")
        self.assertTrue(gated.get("items"))
        self.assertTrue(all(i.get("unknown") for i in gated["items"]))  # fog-of-war
        # WITH reference=1 the goblins come back NAMED, not redacted.
        status, ref = self._get_json("/bestiary-surface?campaign=camp_ref&q=goblin&reference=1")
        self.assertEqual(status, 200)
        items = ref.get("items", [])
        self.assertTrue(items)
        self.assertTrue(any(i.get("name") for i in items))       # real names present
        self.assertFalse(any(i.get("unknown") for i in items))   # nothing redacted

    def test_tier0_rumour_rows_carry_no_creature_name(self):
        # #263 redaction hygiene: the whole point of a rumour row is progressive reveal, so the
        # real creature name must never ship on a tier-0 row — a player reading the network tab
        # would otherwise see the names of as-yet-unencountered creatures matching their query.
        # The row carries only an opaque render key (``id_hint``); the name itself is withheld.
        self._write("camp_redact", {"id": "camp_redact", "bestiary_intel": {"wolf": 1}})
        status, surface = self._get_json("/bestiary-surface?campaign=camp_redact&q=goblin")
        self.assertEqual(status, 200)
        items = surface.get("items", [])
        self.assertTrue(items)
        for item in items:
            self.assertEqual(item.get("tier"), 0)
            self.assertTrue(item.get("unknown"))
            self.assertNotIn("name", item)     # the leak being closed
            self.assertIn("id_hint", item)     # a stable, name-free render key remains

        # The names the *global* browse reveals for this query are exactly what a rumour row must
        # withhold. Fetch them via a campaign id with no snapshot on disk — that falls back to the
        # honest global SRD preview (intel=None), the same path as test_empty_or_missing_snapshot_*.
        # (A bare no-campaign request would instead resolve to the newest campaign on disk — here
        # camp_redact — and redact, so we force the global path with a non-existent campaign.)
        # Pulled dynamically so this never hard-codes SRD content.
        _, glob = self._get_json("/bestiary-surface?campaign=camp_no_such_global_probe&q=goblin")
        withheld = [str(i.get("name", "")) for i in glob.get("items", []) if i.get("name")]
        self.assertTrue(withheld, "global browse should reveal goblin names that must be withheld")
        blob = json.dumps(items).lower()
        for name in withheld:
            self.assertNotIn(name.lower(), blob)

    def test_empty_or_missing_snapshot_falls_back_to_preview(self):
        # campaign id with no snapshot on disk → honest global preview (intel=None path)
        status, surface = self._get_json("/bestiary-surface?campaign=camp_absent&q=goblin")
        self.assertEqual(status, 200)
        items = surface.get("items", [])
        self.assertTrue(items)
        for item in items:
            self.assertNotIn("tier", item)
            self.assertNotIn("ac", item)


if __name__ == "__main__":
    unittest.main()
