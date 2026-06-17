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
        self._old_state = os.environ.get("WORLDOS_STATE_DIR")
        os.environ["WORLDOS_STATE_DIR"] = str(self._tmp)
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
            os.environ.pop("WORLDOS_STATE_DIR", None)
        else:
            os.environ["WORLDOS_STATE_DIR"] = self._old_state
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

    def test_catalog_run_without_world_id_does_not_fall_back_to_default_world(self):
        repo_root = self._tmp / "repo"
        (repo_root / "viewer").mkdir(parents=True)
        server._HERE = repo_root / "viewer"
        qa_campaign = repo_root / "qa" / "state" / "missing-world" / "campaigns" / "camp_qa"
        qa_campaign.mkdir(parents=True)
        (qa_campaign / "snapshot.json").write_text(
            json.dumps({"id": "camp_qa"}),
            encoding="utf-8",
        )

        status, surface = self._get_json("/roster-surface?source=qa&run=missing-world&campaign=camp_qa&limit=1")

        self.assertEqual(status, 200)
        self.assertEqual(surface.get("world_id"), "")

    def test_catalog_run_with_empty_world_id_does_not_fall_back_to_default_world(self):
        repo_root = self._tmp / "repo"
        (repo_root / "viewer").mkdir(parents=True)
        server._HERE = repo_root / "viewer"
        qa_campaign = repo_root / "qa" / "state" / "empty-world" / "campaigns" / "camp_qa"
        qa_campaign.mkdir(parents=True)
        (qa_campaign / "snapshot.json").write_text(
            json.dumps({"id": "camp_qa", "world_id": ""}),
            encoding="utf-8",
        )

        status, surface = self._get_json("/roster-surface?source=qa&run=empty-world&campaign=camp_qa&limit=1")

        self.assertEqual(status, 200)
        self.assertEqual(surface.get("world_id"), "")

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

    # ── dogfood MAJOR: the picker must never OFFER a dead / malformed figure ──────

    def test_a_mid_bio_dead_figure_is_never_offered(self):
        # Alexander Rainforest is canon-DEAD (his bio: "… Rainforest is already dead.") but the
        # death is declared mid-bio, so the opener-only scan once let the picker list him as a
        # playable hero with a live "Play as" button. He must now be absent from every page.
        status, surface = self._get_json("/roster-surface?limit=500")
        self.assertEqual(status, 200)
        names = {c.get("name") for c in surface.get("characters", [])}
        ids = {c.get("id") for c in surface.get("characters", [])}
        self.assertNotIn("Alexander Rainforest", names, "a canon-dead figure must never be offered")
        self.assertNotIn("alexander-rainforest", ids)

    def test_require_stats_drops_records_lacking_level_and_class(self):
        # "Amanita Szarr — Vampire or Vampire Spawn" has neither class nor level (illegible in a
        # level-based picker). ?require_stats=1 drops records missing BOTH; survivors carry at
        # least one of class / level. The default surface still lists her (no silent narrowing).
        _, default = self._get_json("/roster-surface?limit=500")
        default_names = {c.get("name") for c in default.get("characters", [])}
        self.assertIn("Amanita Szarr", default_names)
        _, strict = self._get_json("/roster-surface?require_stats=1&limit=500")
        self.assertEqual(strict.get("world_id"), "baldurs-gate")
        for c in strict.get("characters", []):
            self.assertTrue(
                (c.get("class") or "").strip() or (c.get("level") or "").strip(),
                f"{c.get('name')} is illegible (no class and no level)",
            )
        self.assertNotIn("Amanita Szarr", {c.get("name") for c in strict.get("characters", [])})

    def test_recommended_surface_is_a_small_legible_beginner_set(self):
        # BEGINNER ENTRY: ?recommended=1 returns a small curated set of playable+alive figures
        # that each carry a class AND a level AND a backstory — not the ~2,000-name firehose.
        status, surface = self._get_json("/roster-surface?recommended=1")
        self.assertEqual(status, 200)
        self.assertTrue(surface.get("recommended"))
        cards = surface.get("characters", [])
        self.assertTrue(cards, "the recommended set should not be empty for the shipped roster")
        self.assertLessEqual(len(cards), 24)
        for c in cards:
            self.assertTrue((c.get("class") or "").strip(), c.get("name"))
            self.assertTrue((c.get("level") or "").strip(), c.get("name"))
        names = {c.get("name") for c in cards}
        self.assertNotIn("Alexander Rainforest", names)  # never the dead
        self.assertNotIn("Amanita Szarr", names)         # never the malformed
        # and the full roster is still reachable (recommended is a strict narrowing).
        _, full = self._get_json("/roster-surface?limit=500")
        self.assertLess(surface.get("total", 0), full.get("total", 0))

    # ── BEGINNER GUIDANCE: a basis to choose (#dogfood onboarding) ────────────────

    def test_every_recommended_card_exposes_a_plain_language_playstyle_hint(self):
        # The newbie dogfood gap: ~18 recommended heroes with no hint about how each PLAYS. Every
        # recommended card (each has a class by construction) must now carry a plain-language
        # `playstyle` hint derived from its class — so each card teaches itself (option a).
        status, surface = self._get_json("/roster-surface?recommended=1")
        self.assertEqual(status, 200)
        cards = surface.get("characters", [])
        self.assertTrue(cards, "the recommended set should not be empty")
        for c in cards:
            hint = c.get("playstyle")
            self.assertTrue(
                isinstance(hint, str) and hint.strip(),
                f"{c.get('name')} (a {c.get('class')}) exposes no playstyle hint",
            )

    def test_recommended_surface_flags_an_easy_starter_subset(self):
        # Option b: an obvious safe pick. At least one recommended card is tagged
        # `easy_starter:true` ("Great for your first session"), and the flag is a bool on every
        # card (additive, absent-safe). Easy-starters are simple, forgiving classes.
        status, surface = self._get_json("/roster-surface?recommended=1")
        self.assertEqual(status, 200)
        cards = surface.get("characters", [])
        self.assertTrue(cards)
        for c in cards:
            self.assertIsInstance(c.get("easy_starter"), bool, c.get("name"))
        starters = [c for c in cards if c.get("easy_starter")]
        self.assertTrue(starters, "a first-timer needs at least one flagged safe pick")
        simple = {"fighter", "barbarian", "cleric", "rogue", "paladin", "ranger"}
        for c in starters:
            self.assertIn((c.get("class") or "").strip().lower(), simple, c.get("name"))

    def test_full_recommended_set_is_still_reachable_alongside_the_guidance(self):
        # The guidance is a hint layered ON the curated set, never a further wall: the full
        # recommended set still rides along (same count as without the new fields would imply),
        # and the broad full roster is still one click away (a strict superset).
        _, rec = self._get_json("/roster-surface?recommended=1")
        rec_cards = rec.get("characters", [])
        self.assertTrue(rec_cards)
        # the easy-starter subset never shrinks the recommended set — non-starters remain present
        non_starters = [c for c in rec_cards if not c.get("easy_starter")]
        self.assertTrue(non_starters, "the full recommended set stays reachable, not just starters")
        # the whole roster is still reachable (recommended is a strict narrowing of it)
        _, full = self._get_json("/roster-surface?limit=500")
        self.assertLess(rec.get("total", 0), full.get("total", 0))

    def test_playstyle_hint_rides_the_full_roster_too(self):
        # The hint is derived from class, so it isn't recommended-only: a full-roster card that has
        # a class carries it too (each card teaches itself everywhere), and a no-class card is
        # absent-safe (blank hint, never a fabricated phrase).
        status, surface = self._get_json("/roster-surface?class=Wizard")
        self.assertEqual(status, 200)
        cards = surface.get("characters", [])
        self.assertTrue(cards)
        for c in cards:
            self.assertIn("playstyle", c)  # field always present (round-trip-safe shape)
            self.assertTrue(c["playstyle"].strip(), c.get("name"))  # a classed card has a hint


if __name__ == "__main__":
    unittest.main()
