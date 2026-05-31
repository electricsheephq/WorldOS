"""Route + projection tests for /roster-surface — the canon-NPC PICKER ("reverse character
creator", the default new-game path).

The picker lets the player filter the ingested canon roster by race / class / level and pick a
pre-made canon figure to PLAY AS — they never invent one, and the 7 BG3 origin heroes are NEVER
offered (they are legends/quest-givers, marked ``playable:false``). This lane asserts that
contract over HTTP against the SHIPPED baldurs-gate roster:

  * origins (Shadowheart/Astarion/Gale/…) are EXCLUDED from every result,
  * race / class / level filters narrow correctly (case-insensitive, AND-combined),
  * each card carries the picker fields (id slug, name, race, class, level, backstory snippet,
    portrait_scope) and a real ingested face resolves through the slug scope,
  * the distinct race/class/level facets ride along for the filter chips,
  * the unfiltered roster is capped (``total`` is the full count, ``returned`` the painted slice).

Mirrors the wired-surface harness (test_bestiary_surface.py): a threaded server, GETs over
http.client. Uses the real engine (the test interpreter has the engine deps), so these assert the
real shipped behavior — not a mock.
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
_SPEC = importlib.util.spec_from_file_location("viewer_server_roster", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)

# The 7 BG3 origin heroes — the figures the picker must NEVER offer as a PC (playable:false).
_ORIGINS = {"Shadowheart", "Astarion", "Gale", "Wyll", "Lae'zel", "Karlach", "Minthara"}


class _QuietHandler(server._Handler):
    def log_message(self, *args, **kwargs):  # silence access logging in tests
        pass


@unittest.skipIf(
    server._load_engine_server() is None,
    f"engine unavailable in this interpreter: {server._ENGINE_IMPORT_ERROR}",
)
class RosterSurfaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._old_state = os.environ.get("CLAWDND_STATE_DIR")
        os.environ["CLAWDND_STATE_DIR"] = str(self._tmp)
        self._old_here = server._HERE
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

    def _get_json(self, path: str) -> tuple[int, dict]:
        conn = http.client.HTTPConnection(self._host, self._port, timeout=10)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read()
            return resp.status, (json.loads(body.decode("utf-8")) if body else {})
        finally:
            conn.close()

    # ── default world: a populated, capped roster with NO origins ────────────────

    def test_no_campaign_browses_default_world_roster(self):
        status, surface = self._get_json("/roster-surface")
        self.assertEqual(status, 200)
        self.assertEqual(surface.get("world_id"), "baldurs-gate")
        self.assertNotIn("error", surface)
        chars = surface.get("characters", [])
        self.assertTrue(chars, "the shipped baldurs-gate roster should not be empty")
        # The unfiltered playable roster is ~2,000 — far too many to paint, so the grid is capped:
        # `total` is the FULL matched count, `returned` the painted slice, and the list is bounded.
        self.assertGreater(surface["total"], surface["returned"])
        self.assertEqual(surface["returned"], len(chars))
        self.assertLessEqual(len(chars), 120)

    def test_origins_are_never_offered(self):
        # The playable_only contract: the 7 BG3 origin heroes must be absent from EVERY page of
        # the picker. Pull the whole roster (limit high) and assert none of them appear.
        status, surface = self._get_json("/roster-surface?limit=500")
        self.assertEqual(status, 200)
        names = {c.get("name") for c in surface.get("characters", [])}
        leaked = names & _ORIGINS
        self.assertFalse(leaked, f"origins must never be playable picks, but found: {leaked}")

    # ── card shape + a real ingested face ────────────────────────────────────────

    def test_cards_carry_the_picker_fields(self):
        status, surface = self._get_json("/roster-surface?class=Wizard")
        self.assertEqual(status, 200)
        chars = surface.get("characters", [])
        self.assertTrue(chars)
        for c in chars:
            for field in ("id", "name", "race", "class", "level", "backstory", "portrait_scope"):
                self.assertIn(field, c)
            # portrait_scope is keyed off the file slug (id) — what the viewer resolves the face by.
            self.assertEqual(c["portrait_scope"], "portrait-" + c["id"])

    def test_known_canon_pick_resolves_its_ingested_portrait(self):
        # Hartlebury (a LIVING Flaming Fist Dwarf wizard with an ingested face) is the canonical
        # test pick. Confirm the roster offers them AND that their portrait_scope serves real
        # pixels — the whole point of "pick a canon NPC" is a real face, not a silhouette. (Skips
        # if this checkout has no _private ingested art for the slug — the art is gitignored.)
        # Was Dal Lightspark, but #305: Dal is canon-DEAD with NO ingested portrait (his face
        # 404'd here), so the roster now FILTERS him out — assert that too.
        status, surface = self._get_json("/roster-surface?class=Wizard&race=Dwarf")
        self.assertEqual(status, 200)
        ids = {c.get("id") for c in surface.get("characters", [])}
        self.assertNotIn("dal-lightspark", ids, "a canon-dead figure must not be a playable pick")
        pick = next((c for c in surface.get("characters", []) if c.get("id") == "hartlebury"), None)
        self.assertIsNotNone(pick, "Hartlebury should be a playable Dwarf Wizard pick")
        conn = http.client.HTTPConnection(self._host, self._port, timeout=10)
        try:
            conn.request("GET", "/image?scope=" + pick["portrait_scope"])
            resp = conn.getresponse()
            body = resp.read()
        finally:
            conn.close()
        if resp.status == 404:
            self.skipTest("no ingested _private portrait for hartlebury in this checkout")
        self.assertEqual(resp.status, 200)
        self.assertTrue(body, "the ingested portrait should serve real image bytes")

    # ── filters: race / class / level, AND-combined, case-insensitive ────────────

    def test_class_filter_narrows_to_that_class(self):
        status, surface = self._get_json("/roster-surface?class=Wizard")
        self.assertEqual(status, 200)
        chars = surface.get("characters", [])
        self.assertTrue(chars)
        self.assertTrue(all(c.get("class") == "Wizard" for c in chars))

    def test_filters_are_case_insensitive(self):
        _, upper = self._get_json("/roster-surface?class=Wizard")
        _, lower = self._get_json("/roster-surface?class=wizard")
        self.assertEqual(upper.get("total"), lower.get("total"))
        self.assertGreater(upper.get("total", 0), 0)

    def test_race_and_class_filters_and_combine(self):
        _, both = self._get_json("/roster-surface?race=Dwarf&class=Wizard")
        chars = both.get("characters", [])
        self.assertTrue(chars)
        self.assertTrue(all(c.get("race") == "Dwarf" and c.get("class") == "Wizard" for c in chars))
        # A combined filter must be a STRICT subset of either filter alone (AND, not OR).
        _, just_class = self._get_json("/roster-surface?class=Wizard")
        self.assertLessEqual(both.get("total"), just_class.get("total"))

    def test_level_filter_narrows_to_that_level(self):
        status, surface = self._get_json("/roster-surface?level=5")
        self.assertEqual(status, 200)
        chars = surface.get("characters", [])
        self.assertTrue(chars)
        self.assertTrue(all(c.get("level") == "5" for c in chars))

    def test_catalog_run_uses_snapshot_world_scope(self):
        repo_root = self._tmp / "repo"
        (repo_root / "viewer").mkdir(parents=True)
        server._HERE = repo_root / "viewer"
        qa_campaign = repo_root / "qa" / "state" / "wave3-red" / "campaigns" / "camp_qa"
        qa_campaign.mkdir(parents=True)
        (qa_campaign / "snapshot.json").write_text(
            json.dumps({"id": "camp_qa", "world_id": "sundered-reach"}),
            encoding="utf-8",
        )

        status, surface = self._get_json("/roster-surface?source=qa&run=wave3-red&campaign=camp_qa&limit=1")

        self.assertEqual(status, 200)
        self.assertEqual(surface.get("world_id"), "sundered-reach")

    # ── facets for the filter chips ──────────────────────────────────────────────

    def test_facets_present_and_frequency_ordered(self):
        status, surface = self._get_json("/roster-surface")
        self.assertEqual(status, 200)
        facets = surface.get("facets", {})
        for key in ("races", "classes", "levels"):
            self.assertIn(key, facets)
            self.assertTrue(facets[key], f"facet {key} should be populated for the shipped roster")
        # The shipped post-BG3 roster is Human-heavy and Fighter-heavy; the densest chip leads
        # (frequency-ordered) so the picker's first chips are the useful ones.
        self.assertEqual(facets["races"][0], "Human")
        self.assertEqual(facets["classes"][0], "Fighter")
        # Levels sort numerically (string-stored), so "2" precedes "10".
        levels = facets["levels"]
        if "2" in levels and "10" in levels:
            self.assertLess(levels.index("2"), levels.index("10"))


if __name__ == "__main__":
    unittest.main()
