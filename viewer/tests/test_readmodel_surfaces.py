"""Route + projection tests for the 5 OpenWorlds read-model surfaces wired in this
lane: /journal-surface, /character-surface, /inventory-surface, /relations-surface, and
/parley-surface.

These mirror the existing wired-surface tests (test_session_surface.py /
test_openworlds_static.py): each surface (a) resolves the campaign the same way (explicit
?campaign view override, else the attached campaign), (b) projects only player-facing
fields (never dm_notes / sealed agendas / raw notes), (c) carries the engine state-authority
envelope, and (d) degrades to a graceful empty when there is no snapshot. The snapshot here
is model-conformant (it round-trips through the engine's Campaign model), so the journal's
Campaign Director advisory exercises the engine.director detection path.
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


# A single model-conformant snapshot reused across the surface tests: a level-3 fighter PC
# with expertise + a companion bard with a dossier + a met NPC + an unmet roster NPC + two
# factions + an active spine hook + an overdue consequence + an active quest (the structural
# debts the Campaign Director should detect).
_SNAPSHOT = {
    "id": "camp_marches",
    "title": "The Long Road to Odrun",
    "summary": "The party makes the Lanternrest at dusk.",
    "world_id": "stolen-marches",
    "day": 12,
    "time_of_day": "dusk",
    "current_location_id": "lanternrest",
    "house_rules": {"difficulty": "standard"},
    "locations": {
        "lanternrest": {"id": "lanternrest", "name": "Lanternrest", "region": "Outskirts of Odrun",
                         "description": "An inn that should not still be standing.",
                         "connections": ["thornford"], "visited": True, "notes": "private route note"},
        "thornford": {"id": "thornford", "name": "Thorn Ford", "region": "Thorn River", "connections": ["lanternrest"]},
    },
    "party": ["cassian", "mira"],
    "characters": {
        "cassian": {
            "id": "cassian", "name": "Cassian Frostbreaker", "kind": "player", "race": "Human",
            "alignment": "Neutral Good", "classes": [{"name": "Fighter", "level": 3, "subclass": "Champion"}],
            "abilities": {"strength": 16, "dexterity": 12, "constitution": 14, "intelligence": 10, "wisdom": 8, "charisma": 18},
            "proficiency_bonus": 2, "skill_proficiencies": ["persuasion", "athletics"], "skill_expertise": ["persuasion"],
            "saving_throw_proficiencies": ["str", "con"], "armor_class": 17, "max_hp": 28, "current_hp": 22,
            "speed": 30, "initiative_bonus": 1, "conditions": ["poisoned"], "death_saves": {"successes": 0, "failures": 0},
            "xp": 3880,
            "inventory": [
                {"name": "Longsword +1", "quantity": 1, "equipped": True, "weight": 3.0, "description": "A reliable blade."},
                {"name": "Healing Potion", "quantity": 4, "weight": 0.5, "description": "Restores 2d4+2."},
            ],
            "currency": {"gp": 232, "sp": 68, "cp": 14, "pp": 2}, "spells_known": ["Shield", "Bless"], "spells_prepared": ["Bless"],
            "class_resources": {"second_wind": {"max": 1, "used": 0, "recharge": "short"}},
            "features": ["Second Wind", "Action Surge"], "backstory": "Born to a stonemason in Frostbreak.",
            "notes": "private character note",
        },
        "mira": {
            "id": "mira", "name": "Mira of the Inkstain", "kind": "companion", "race": "Halfling",
            "classes": [{"name": "Bard", "level": 3}], "abilities": {"charisma": 18, "dexterity": 16, "wisdom": 12},
            "proficiency_bonus": 2, "skill_proficiencies": ["deception"], "armor_class": 15, "max_hp": 22, "current_hp": 18,
            "inventory": [{"name": "Rapier", "quantity": 1, "equipped": True, "weight": 2.0}],
            "currency": {"gp": 40}, "attitude": "warm", "attitude_value": 45,
            "companion_dossier": {"banter_tags": ["wry", "curious"], "relationships": {"cassian": "old ally"}, "values": ["freedom"]},
            "memory": ["Met at the Thorn Ford."],
        },
        "olwen": {"id": "olwen", "name": "Toll-keeper Olwen", "kind": "npc", "met": True, "location_id": "thornford",
                   "attitude": "guarded", "attitude_value": -10, "backstory": "Weighs every word.",
                   "memory": ["The seal is correct."]},
        "stranger": {"id": "stranger", "name": "Unmet Roster NPC", "kind": "npc", "met": False, "location_id": "thornford"},
    },
    "quests": {
        "q1": {"id": "q1", "title": "The Lanternrest", "status": "active",
                "objectives": ["Reach the courtyard", "Survive the night"], "completed_objectives": ["Reach the courtyard"],
                "location_id": "lanternrest", "description": "Investigate the still inn."},
        "q2": {"id": "q2", "title": "The Ferryman's Tab", "status": "completed", "objectives": ["Pay the ferryman"],
                "completed_objectives": ["Pay the ferryman"], "location_id": "thornford"},
    },
    "quest_hooks": [
        {"id": "h1", "title": "Whispers at the Saltwell", "status": "open", "spine": False, "note": "Songs in a dead language."},
        {"id": "h2", "title": "The Sealed Gate", "status": "active", "spine": True, "note": "The gate of Tines is sealed."},
    ],
    "factions": {
        "wardens": {"id": "wardens", "name": "Road Wardens", "reputation": 64, "description": "Keep the roads."},
        "stag": {"id": "stag", "name": "The Stag Lord's Company", "reputation": -60, "description": "Bandits."},
    },
    "consequences": [
        {"id": "c1", "trigger_day": 10, "fired": False, "thread_id": "", "text": "Word reaches the fort.", "note": "Olwen reports."},
    ],
    "companion_quest_arcs": {
        "a1": {"id": "a1", "companion_id": "mira", "title": "The Lost Hymn", "status": "active",
                "stages": [{"id": "s1", "title": "Find the score", "status": "active", "note": "in the cellar"},
                           {"id": "s2", "title": "Sing it once", "status": "locked"}]},
    },
}


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class ReadModelSurfaceTests(unittest.TestCase):
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

    def assert_no_private_keys(self, value) -> None:
        private_keys = {"notes", "scenes", "lore", "dm_notes", "sealed_agenda", "agenda"}
        if isinstance(value, dict):
            for key, child in value.items():
                self.assertNotIn(key, private_keys)
                self.assert_no_private_keys(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_no_private_keys(child)

    def assert_envelope(self, surface: dict, campaign_id: str) -> None:
        self.assertEqual(surface["campaign_id"], campaign_id)
        self.assertEqual(surface["state_authority"], "engine")
        self.assertEqual(surface["write_lane"], "/move")

    # ── journal ───────────────────────────────────────────────────────────────

    def test_journal_surface_projects_quests_hooks_and_director_advisory(self):
        self._write("camp_marches", _SNAPSHOT)
        status, surface = self._get_json("/journal-surface?campaign=camp_marches")
        self.assertEqual(status, 200)
        self.assert_envelope(surface, "camp_marches")

        quests = {q["id"]: q for q in surface["quests"]}
        self.assertEqual(quests["q1"]["status"], "active")
        self.assertEqual(quests["q2"]["status"], "complete")
        # the active quest's next-undone objective leads, completed one is flagged done
        self.assertEqual(quests["q1"]["objective"], "Survive the night")
        self.assertTrue(any(o["done"] for o in quests["q1"]["objectives"]))
        # unresolved hooks surface as rumors; the spine hook is flagged
        self.assertEqual(quests["h1"]["status"], "rumor")
        self.assertTrue(quests["h2"]["spine"])

        advisory = surface["directorAdvisory"]
        kinds = {d["kind"] for d in advisory["debts"]}
        self.assertIn("hook_untracked", kinds)  # the engaged-but-untracked spine hook
        self.assertIn("due_consequence", kinds)  # the overdue authored consequence
        self.assertTrue(all(d["nudge"] for d in advisory["debts"]))
        self.assert_no_private_keys(surface)

    def test_journal_surface_empty_without_snapshot(self):
        status, surface = self._get_json("/journal-surface")
        self.assertEqual(status, 200)
        self.assertEqual(surface["campaign_id"], "")
        self.assertEqual(surface["quests"], [])
        self.assertEqual(surface["directorAdvisory"]["debts"], [])

    def test_director_advisory_uses_engine_detection_path(self):
        # With a model-conformant snapshot the advisory should come from the engine's own
        # scene_debt/director modules (the same get_campaign_director logic), not the fallback.
        advisory = server._director_advisory(_SNAPSHOT)
        self.assertEqual(advisory["source"], "engine.director")

    def test_director_advisory_heuristic_matches_on_nonconformant_snapshot(self):
        # A snapshot the strict Campaign model rejects (an unknown extra field) must still
        # yield a usable advisory via the snapshot-only heuristic.
        bad = json.loads(json.dumps(_SNAPSHOT))
        bad["factions"]["wardens"]["tags"] = ["lawful"]  # Faction has no tags field (extra=forbid)
        advisory = server._director_advisory(bad)
        self.assertEqual(advisory["source"], "viewer.heuristic")
        self.assertIn("hook_untracked", {d["kind"] for d in advisory["debts"]})

    # ── character ───────────────────────────────────────────────────────────────

    def test_character_surface_projects_full_party_sheets(self):
        self._write("camp_marches", _SNAPSHOT)
        status, surface = self._get_json("/character-surface?campaign=camp_marches")
        self.assertEqual(status, 200)
        self.assert_envelope(surface, "camp_marches")

        hero = {c["id"]: c for c in surface["party"]}["cassian"]
        self.assertEqual(hero["class"], "Fighter")
        self.assertEqual(hero["level"], 3)
        self.assertEqual(hero["stats"]["ac"], 17)
        self.assertEqual(hero["hp"], 22)
        self.assertEqual(hero["hpMax"], 28)
        # CON save = +2 (mod) + 2 (proficient) = +4
        self.assertEqual(hero["stats"]["fort"], 4)
        # persuasion = CHA mod (+4) + 2x proficiency (expertise) = +8
        persuasion = next(s for s in hero["skills"] if s["name"] == "Persuasion")
        self.assertEqual(persuasion["mod"], 8)
        self.assertTrue(persuasion["expertise"])
        self.assertEqual(hero["conditions"], ["Poisoned"])
        self.assertEqual(hero["deathSaves"], {"successes": 0, "failures": 0})
        self.assertTrue(any(r["id"] == "second_wind" for r in hero["classResources"]))
        self.assert_no_private_keys(surface)

    # ── inventory ───────────────────────────────────────────────────────────────

    def test_inventory_surface_projects_packs_and_currency(self):
        self._write("camp_marches", _SNAPSHOT)
        status, surface = self._get_json("/inventory-surface?campaign=camp_marches")
        self.assertEqual(status, 200)
        self.assert_envelope(surface, "camp_marches")

        cassian = {m["id"]: m for m in surface["party"]}["cassian"]
        self.assertEqual(cassian["currency"]["gp"], 232)
        items = {i["name"]: i for i in cassian["items"]}
        self.assertEqual(items["Longsword +1"]["type"], "weapon")
        self.assertTrue(items["Longsword +1"]["equipped"])
        self.assertEqual(items["Healing Potion"]["qty"], 4)
        self.assertEqual(items["Healing Potion"]["type"], "spell")
        # the flat shared-stash view spans every party member's items
        self.assertTrue(len(surface["stash"]) >= len(cassian["items"]))
        self.assert_no_private_keys(surface)

    # ── relations ───────────────────────────────────────────────────────────────

    def test_relations_surface_projects_factions_npcs_and_arcs(self):
        self._write("camp_marches", _SNAPSHOT)
        status, surface = self._get_json("/relations-surface?campaign=camp_marches")
        self.assertEqual(status, 200)
        self.assert_envelope(surface, "camp_marches")

        factions = {f["id"]: f for f in surface["factions"]}
        self.assertEqual(factions["wardens"]["reputation"], 64)
        # -100..100 reputation maps onto the 0..100 RepBar
        self.assertEqual(factions["wardens"]["rep"], 82)
        self.assertEqual(factions["stag"]["standing"], "Hostile")

        npcs = {n["id"]: n for n in surface["npcs"]}
        self.assertIn("mira", npcs)  # the companion
        self.assertIn("olwen", npcs)  # a met NPC
        self.assertNotIn("stranger", npcs)  # an unmet roster NPC is NOT listed
        self.assertNotIn("cassian", npcs)  # players are excluded
        self.assertTrue(npcs["mira"]["companion"])
        self.assertEqual(npcs["mira"]["banter_tags"], ["wry", "curious"])
        self.assertEqual(npcs["mira"]["relationships"], {"cassian": "old ally"})

        arcs = {a["id"]: a for a in surface["companionArcs"]}
        self.assertEqual(arcs["a1"]["companion"], "Mira of the Inkstain")
        self.assertEqual([s["status"] for s in arcs["a1"]["stages"]], ["active", "locked"])
        self.assert_no_private_keys(surface)

    # ── parley ───────────────────────────────────────────────────────────────

    def test_parley_surface_projects_sheet_correct_slots_for_lead_pc(self):
        self._write("camp_marches", _SNAPSHOT)
        status, surface = self._get_json("/parley-surface?campaign=camp_marches")
        self.assertEqual(status, 200)
        self.assert_envelope(surface, "camp_marches")

        self.assertEqual(surface["actor"], "Cassian Frostbreaker")  # the lead PC
        self.assertEqual(surface["alignment"], "Neutral Good")
        self.assertTrue(surface["free_form"])
        skills = {s["skill"]: s for s in surface["skills"]}
        # the four core social skills are always present
        for core in ("persuasion", "deception", "intimidation", "insight"):
            self.assertIn(core, skills)
        # sheet-correct: persuasion expertise = +8, medium DC band = 14
        self.assertEqual(skills["persuasion"]["modifier"], 8)
        self.assertEqual(skills["persuasion"]["suggested_dc"], 14)
        self.assert_no_private_keys(surface)

    def test_parley_surface_difficulty_shifts_dc_band(self):
        self._write("camp_marches", _SNAPSHOT)
        _status, surface = self._get_json("/parley-surface?campaign=camp_marches&difficulty=hard")
        self.assertTrue(all(s["suggested_dc"] == 18 for s in surface["skills"]))

    def test_parley_surface_empty_without_snapshot(self):
        status, surface = self._get_json("/parley-surface")
        self.assertEqual(status, 200)
        self.assertEqual(surface["campaign_id"], "")
        self.assertEqual(surface["skills"], [])
        self.assertTrue(surface["free_form"])


if __name__ == "__main__":
    unittest.main()
