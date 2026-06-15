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

import copy
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
    "camp_beats": {
        "solo_cooldown_days": 2,
        "pair_cooldown_days": 3,
        "max_records": 200,
        "records": [
            {
                "id": "camp:solo:mira:wry",
                "day": 11,
                "companion_ids": ["mira"],
                "kind": "solo",
                "tags": ["wry"],
                "resolved": True,
                "note": "Mira asked Cassian why the sealed gate felt familiar.",
                "cooldown_key": "solo:mira:wry",
                "pair_key": "",
            }
        ],
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
        self.assertEqual(surface["threads"], [])
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

    def test_journal_surface_quest_carries_evolution_badge_and_threads_callback(self):
        # Quest-evolution / callback (#120): a resolved quest carrying `evolves_to` +
        # `callback_in_days`, plus the engine's scheduled `evolves_from:<id>` Consequence,
        # surfaces as both a per-quest badge AND a "Threads & Callbacks" thread row.
        snap = copy.deepcopy(_SNAPSHOT)
        snap["quests"]["q2"]["evolves_to"] = "h-reckoning"  # q2 is the completed quest
        snap["quests"]["q2"]["callback_in_days"] = 3
        # the engine schedules this on resolve (note == "evolves_from:<quest_id>"); day=12
        snap["consequences"].append({
            "id": "c_evo", "trigger_day": 15, "fired": False, "thread_id": "",
            "text": "Bring back / evolve the resolved thread 'The Ferryman's Tab'.",
            "note": "evolves_from:q2",
        })
        self._write("camp_marches", snap)
        status, surface = self._get_json("/journal-surface?campaign=camp_marches")
        self.assertEqual(status, 200)

        # (a) per-quest badge fields
        q2 = {q["id"]: q for q in surface["quests"]}["q2"]
        self.assertEqual(q2["evolvesTo"], "h-reckoning")
        self.assertEqual(q2["callbackInDays"], 3)
        # a quest WITHOUT an evolves_to hook carries neither
        q1 = {q["id"]: q for q in surface["quests"]}["q1"]
        self.assertEqual(q1["evolvesTo"], "")
        self.assertEqual(q1["callbackInDays"], 0)

        # (b) the Threads & Callbacks sub-list projects the scheduled evolution
        threads = {t["id"]: t for t in surface["threads"]}
        self.assertIn("c_evo", threads)
        thread = threads["c_evo"]
        self.assertEqual(thread["questId"], "q2")
        self.assertEqual(thread["questTitle"], "The Ferryman's Tab")
        self.assertEqual(thread["evolvesTo"], "h-reckoning")
        self.assertEqual(thread["triggerDay"], 15)
        self.assertFalse(thread["fired"])
        self.assertFalse(thread["due"])  # trigger_day 15 > current day 12
        self.assertEqual(thread["status"], "pending")
        self.assertTrue(thread["note"])
        self.assert_no_private_keys(surface)

    def test_journal_threads_marks_due_and_skips_worldsim_beats(self):
        # A pending evolution whose trigger_day has arrived is `due`; a worldsim background
        # beat (a non-empty thread_id, even with an evolves_from-looking note) is NOT an
        # evolution and must be skipped.
        snap = copy.deepcopy(_SNAPSHOT)  # day = 12
        snap["quests"]["q2"]["evolves_to"] = "h-reckoning"
        snap["consequences"].append({
            "id": "c_due", "trigger_day": 12, "fired": False, "thread_id": "",
            "text": "It returns now.", "note": "evolves_from:q2",
        })
        snap["consequences"].append({
            "id": "c_ws", "trigger_day": 12, "fired": False, "thread_id": "standing-war",
            "text": "A world beat.", "note": "evolves_from:q2",
        })
        self._write("camp_marches", snap)
        _status, surface = self._get_json("/journal-surface?campaign=camp_marches")
        threads = {t["id"]: t for t in surface["threads"]}
        self.assertIn("c_due", threads)
        self.assertTrue(threads["c_due"]["due"])
        self.assertEqual(threads["c_due"]["status"], "due")
        self.assertNotIn("c_ws", threads)  # worldsim beat excluded
        self.assertEqual(len(surface["threads"]), 1)

    def test_journal_threads_empty_when_no_quest_evolution_scheduled(self):
        # The baseline conformant snapshot schedules no evolution -> no threads, and the
        # completed quest carries an empty evolvesTo.
        self._write("camp_marches", _SNAPSHOT)
        _status, surface = self._get_json("/journal-surface?campaign=camp_marches")
        self.assertEqual(surface["threads"], [])
        self.assertEqual({q["id"]: q for q in surface["quests"]}["q2"]["evolvesTo"], "")

    # ── acts / chronicle payoff ───────────────────────────────────────────────

    def test_acts_surface_degrades_to_untracked_without_path_state(self):
        self._write("camp_marches", _SNAPSHOT)
        status, surface = self._get_json("/acts-surface?campaign=camp_marches")
        self.assertEqual(status, 200)
        self.assert_envelope(surface, "camp_marches")
        self.assertFalse(surface["tracked"])
        self.assertEqual(surface["acts"], [])
        self.assertIn("not tracked", surface["emptyState"]["title"].lower())
        self.assertEqual(surface["threads"], [])
        self.assertEqual(surface["state_authority"], "engine")
        self.assert_no_private_keys(surface)

    def test_acts_surface_projects_adventure_path_choices_threads_and_debts(self):
        snap = copy.deepcopy(_SNAPSHOT)
        snap["adventure_path"] = {
            "current_act_id": "act-1",
            "acts": [
                {
                    "id": "act-1",
                    "title": "The Lanternrest",
                    "status": "active",
                    "summary": "The road reaches the impossible inn.",
                    "beats": [
                        {"id": "b1", "title": "Reach the courtyard", "status": "resolved"},
                        {"id": "b2", "title": "Open the eastern door", "status": "active"},
                    ],
                    "dm_notes": "hidden twist",
                }
            ],
            "diagnostics": ["unknown beat ref: missing"],
        }
        snap["decisions"] = [
            {"id": "d1", "day": 11, "summary": "Spared Falgrim", "chosen": "let him ride", "rationale": "Mira asked for mercy"},
        ]
        snap["quests"]["q2"]["evolves_to"] = "h-reckoning"
        snap["consequences"].append({
            "id": "c_evo", "trigger_day": 12, "fired": False, "thread_id": "",
            "text": "It returns now.", "note": "evolves_from:q2",
        })
        self._write("camp_marches", snap)
        status, surface = self._get_json("/acts-surface?campaign=camp_marches")
        self.assertEqual(status, 200)
        self.assertTrue(surface["tracked"])
        self.assertEqual(surface["currentActId"], "act-1")
        self.assertEqual(surface["acts"][0]["title"], "The Lanternrest")
        self.assertEqual([b["status"] for b in surface["acts"][0]["beats"]], ["resolved", "active"])
        self.assertEqual(surface["majorChoices"][0]["summary"], "Spared Falgrim")
        self.assertEqual(surface["threads"][0]["status"], "due")
        self.assertEqual(surface["diagnostics"][0]["message"], "unknown beat ref: missing")
        self.assert_no_private_keys(surface)

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
        # 5e CON save = +2 (mod) + 2 (proficient) = +4
        self.assertEqual(hero["stats"]["saves"]["con"], 4)
        # persuasion = CHA mod (+4) + 2x proficiency (expertise) = +8
        persuasion = next(s for s in hero["skills"] if s["name"] == "Persuasion")
        self.assertEqual(persuasion["mod"], 8)
        self.assertTrue(persuasion["expertise"])
        self.assertEqual(hero["conditions"], ["Poisoned"])
        self.assertEqual(hero["deathSaves"], {"successes": 0, "failures": 0})
        self.assertTrue(any(r["id"] == "second_wind" for r in hero["classResources"]))
        self.assert_no_private_keys(surface)

    def test_character_surface_marks_skill_proficiency_expertise_and_untrained(self):
        # Inspector-depth (optimizer/veteran): every skill carries explicit proficient/expertise
        # flags so the Skills tab can mark trained vs untrained — not just a raw modifier.
        self._write("camp_marches", _SNAPSHOT)
        _status, surface = self._get_json("/character-surface?campaign=camp_marches")
        hero = {c["id"]: c for c in surface["party"]}["cassian"]
        skills = {s["name"]: s for s in hero["skills"]}
        # expertise (persuasion): both flags true, double-proficiency mod
        self.assertTrue(skills["Persuasion"]["proficient"])
        self.assertTrue(skills["Persuasion"]["expertise"])
        # proficient-only (athletics): STR +3 mod + 2 prof = +5, expertise false
        self.assertTrue(skills["Athletics"]["proficient"])
        self.assertFalse(skills["Athletics"]["expertise"])
        self.assertEqual(skills["Athletics"]["mod"], 5)
        # untrained (stealth): neither flag; DEX +1 mod, no proficiency
        self.assertFalse(skills["Stealth"]["proficient"])
        self.assertFalse(skills["Stealth"]["expertise"])
        self.assertEqual(skills["Stealth"]["mod"], 1)

    def test_character_surface_spell_rules_text_for_a_real_caster(self):
        # Inspector-depth bug #3: each known/prepared spell carries its REAL srd524 rules block
        # (level/school/range/duration/concentration/save/damage), and the caster's spell save DC
        # is computed (8 + prof + casting mod) for save-forcing spells. A Wizard (INT caster).
        snap = copy.deepcopy(_SNAPSHOT)
        snap["party"].append("elara")
        snap["characters"]["elara"] = {
            "id": "elara", "name": "Elara", "kind": "player", "race": "Elf",
            "classes": [{"name": "Wizard", "level": 5}],
            "abilities": {"intelligence": 18, "dexterity": 14, "constitution": 12},
            "proficiency_bonus": 3, "armor_class": 12, "max_hp": 22, "current_hp": 22,
            "spells_known": ["Fire Bolt", "Magic Missile"], "spells_prepared": ["Fireball"],
            "spell_slots": {"3": {"maximum": 2, "used": 0}},
        }
        self._write("camp_caster", snap)
        _status, surface = self._get_json("/character-surface?campaign=camp_caster")
        elara = {c["id"]: c for c in surface["party"]}["elara"]
        by_name = {sp["name"]: sp for grp in elara["spells"] for sp in grp["list"]}
        # Fireball: L3 evocation, 150-foot range, DEX save, 8d6, DC = 8 + 3 prof + 4 INT = 15
        fb = by_name["Fireball"]
        self.assertEqual(fb["level"], 3)
        self.assertEqual(fb["school"], "Evocation")
        self.assertEqual(fb["range"], "150 feet")
        self.assertEqual(fb["save"], "dexterity")
        self.assertEqual(fb["saveDc"], 15)
        self.assertEqual(fb["damage"], "8d6")
        self.assertIn("fire", fb["damageType"])
        # Fire Bolt: a cantrip (level 0) with an attack roll, no save -> saveDc omitted (None)
        fbolt = by_name["Fire Bolt"]
        self.assertEqual(fbolt["level"], 0)
        self.assertEqual(fbolt["levelLabel"], "Cantrip")
        self.assertTrue(fbolt["attack"])
        self.assertIsNone(fbolt["saveDc"])
        self.assert_no_private_keys(surface)

    def test_character_surface_surfaces_browsable_preparable_pool_for_a_prepared_caster(self):
        # #754 (optimizer): the Spellbook must let a prepared caster BROWSE the full class spell
        # list (what they can prepare FROM), not just the few currently prepared. The surface
        # projects `preparableSpells` — the whole Paladin list, capped to the caster's highest
        # slot level (L10 Paladin -> L1–3), enriched with the same SRD rules cards.
        snap = copy.deepcopy(_SNAPSHOT)
        snap["party"].append("wyll")
        snap["characters"]["wyll"] = {
            "id": "wyll", "name": "Wyll", "kind": "player", "race": "Human",
            "classes": [{"name": "Paladin", "level": 10}],
            "abilities": {"charisma": 18, "strength": 16, "constitution": 14},
            "proficiency_bonus": 4, "armor_class": 18, "max_hp": 84, "current_hp": 84,
            "spells_known": ["Bless", "Cure Wounds", "Shield of Faith"],
            "spells_prepared": ["Bless", "Cure Wounds", "Shield of Faith"],
            "spell_slots": {"1": {"maximum": 4, "used": 0}, "2": {"maximum": 3, "used": 0},
                            "3": {"maximum": 2, "used": 0}},
        }
        self._write("camp_paladin", snap)
        _status, surface = self._get_json("/character-surface?campaign=camp_paladin")
        wyll = {c["id"]: c for c in surface["party"]}["wyll"]
        pool = wyll["preparableSpells"]
        names = {sp["name"] for sp in pool}
        # the browsable pool is far larger than the 3 prepared, and includes Paladin spells the
        # character has NOT prepared (the whole point — a planning surface).
        self.assertGreater(len(pool), len(wyll["spells"][0]["list"]))
        self.assertIn("Divine Smite", names)   # a Paladin spell not in the prepared 3
        self.assertIn("Bless", names)
        # capped to the highest slot level (L3) — no L4/L5 spells the L10 Paladin can't slot
        self.assertEqual(max(sp["level"] for sp in pool), 3)
        # each pool entry is enriched with its SRD rules card (level + school, not just a name)
        smite = next(sp for sp in pool if sp["name"] == "Divine Smite")
        self.assertIn("levelLabel", smite)
        self.assert_no_private_keys(surface)

    def test_character_surface_preparable_pool_empty_for_non_caster(self):
        # A Fighter (Cassian) has no caster class -> no preparable pool is fabricated.
        self._write("camp_marches", _SNAPSHOT)
        _status, surface = self._get_json("/character-surface?campaign=camp_marches")
        hero = {c["id"]: c for c in surface["party"]}["cassian"]
        self.assertEqual(hero["preparableSpells"], [])

    def test_character_surface_omits_spell_dc_for_non_caster_but_keeps_rules(self):
        # HONEST data: Cassian is a Fighter (no SRD caster class), so no spell save DC is
        # fabricated — but the spell's own rules text (school/range/duration) still resolves,
        # since those are class-independent SRD facts. Bless (a buff) forces no save anyway.
        self._write("camp_marches", _SNAPSHOT)
        _status, surface = self._get_json("/character-surface?campaign=camp_marches")
        hero = {c["id"]: c for c in surface["party"]}["cassian"]
        by_name = {sp["name"]: sp for grp in hero["spells"] for sp in grp["list"]}
        self.assertEqual(by_name["Bless"]["school"], "Enchantment")
        self.assertEqual(by_name["Bless"]["range"], "30 feet")
        self.assertTrue(by_name["Bless"]["concentration"])
        self.assertIsNone(by_name["Bless"]["saveDc"])  # Fighter -> no DC, not fabricated
        self.assertEqual(by_name["Shield"]["school"], "Abjuration")

    def test_character_surface_unknown_spell_degrades_to_name_only(self):
        # A spell name the SRD doesn't carry surfaces as just its name (today's behavior) —
        # the read-model never invents rules text it doesn't have.
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["cassian"]["spells_prepared"] = ["Totally Made Up Spell"]
        self._write("camp_unknown", snap)
        _status, surface = self._get_json("/character-surface?campaign=camp_unknown")
        hero = {c["id"]: c for c in surface["party"]}["cassian"]
        sp = next(s for grp in hero["spells"] for s in grp["list"] if s["name"] == "Totally Made Up Spell")
        self.assertEqual(sp["school"], "—")
        self.assertNotIn("range", sp)  # no SRD block was merged in

    def test_character_surface_equipped_carries_real_catalog_stats(self):
        # Inspector-depth bug #2 backing: the heroes-screen paper-doll gets each equipped item's
        # real catalog stats (kind / damage / ac) so a slot tooltip can read "1d8 piercing".
        self._write("camp_marches", _SNAPSHOT)
        _status, surface = self._get_json("/character-surface?campaign=camp_marches")
        mira = {c["id"]: c for c in surface["party"]}["mira"]
        rapier = next(e for e in mira["equipped"] if e["name"] == "Rapier")
        self.assertEqual(rapier["kind"], "weapon")
        self.assertEqual(rapier["damage"], "1d8")
        self.assertEqual(rapier["damageType"], "piercing")

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

    def test_inventory_surface_surfaces_real_item_stats_from_catalog(self):
        # Inspector-depth bug #4: an item the SRD catalog resolves carries its real stat block
        # (damage dice / type for a weapon, base AC for armor). Mira's Rapier resolves to 1d8
        # piercing; a Plate Armor resolves to AC 18 — straight from the engine's item catalog.
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["mira"]["inventory"].append(
            {"name": "Plate Armor", "quantity": 1, "equipped": False})
        self._write("camp_marches", snap)
        _status, surface = self._get_json("/inventory-surface?campaign=camp_marches")
        mira = {m["id"]: m for m in surface["party"]}["mira"]
        items = {i["name"]: i for i in mira["items"]}
        self.assertEqual(items["Rapier"]["damage"], "1d8")
        self.assertEqual(items["Rapier"]["damageType"], "piercing")
        self.assertEqual(items["Rapier"]["kind"], "weapon")
        self.assertEqual(items["Plate Armor"]["ac"], 18)
        self.assertEqual(items["Plate Armor"]["kind"], "armor")

    def test_inventory_surface_unresolved_item_keeps_empty_stats(self):
        # HONEST data: a free-text item the catalog can't resolve ("Longsword +1", "Healing
        # Potion") surfaces NO fabricated damage/ac — empty stat fields, exactly today's
        # behavior. We never invent a number the engine didn't produce.
        self._write("camp_marches", _SNAPSHOT)
        _status, surface = self._get_json("/inventory-surface?campaign=camp_marches")
        cassian = {m["id"]: m for m in surface["party"]}["cassian"]
        items = {i["name"]: i for i in cassian["items"]}
        self.assertEqual(items["Longsword +1"]["damage"], "")
        self.assertIsNone(items["Longsword +1"]["ac"])
        self.assertEqual(items["Healing Potion"]["damage"], "")
        self.assertIsNone(items["Healing Potion"]["ac"])

    def test_inventory_surface_item_properties_include_attunement(self):
        # An attuned magic item surfaces an "Attuned" property chip; a catalog item that
        # requires attunement carries the attunement flag for the detail's stat block.
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["cassian"]["inventory"].append(
            {"name": "Cloak of Protection", "quantity": 1, "requires_attunement": True, "attuned": True})
        self._write("camp_attune", snap)
        _status, surface = self._get_json("/inventory-surface?campaign=camp_attune")
        cassian = {m["id"]: m for m in surface["party"]}["cassian"]
        cloak = {i["name"]: i for i in cassian["items"]}["Cloak of Protection"]
        self.assertTrue(cloak["attunement"])
        self.assertIn("Attuned", cloak["properties"])

    def test_inventory_surface_prefers_persisted_stats_when_catalog_cannot_resolve(self):
        # #756 / F09-7 root-cause fix: a renamed/enchanted item the SRD catalog can NOT resolve
        # by name still renders its REAL stats — because the engine now persists them on the
        # Item at grant time (add_item/buy_item). The viewer prefers those persisted fields over
        # the (failing) by-name catalog re-resolve. The by-name path used to drop these entirely.
        self.assertEqual(server._catalog_meta("Moonsteel Saber +1"), {})  # the catalog truly misses
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["cassian"]["inventory"].append({
            "name": "Moonsteel Saber +1", "quantity": 1, "equipped": False,
            "kind": "weapon", "rarity": "very rare", "cost_gp": 750,
            "damage": "1d6", "damage_type": "slashing", "properties": ["finesse"],
        })
        self._write("camp_persist", snap)
        _status, surface = self._get_json("/inventory-surface?campaign=camp_persist")
        cassian = {m["id"]: m for m in surface["party"]}["cassian"]
        blade = {i["name"]: i for i in cassian["items"]}["Moonsteel Saber +1"]
        self.assertEqual(blade["damage"], "1d6")
        self.assertEqual(blade["damageType"], "slashing")
        self.assertEqual(blade["kind"], "weapon")
        self.assertEqual(blade["rarity"], "very rare")
        self.assertEqual(blade["value"], "750 gp")          # cost_gp -> the "value" gp string
        self.assertIn("finesse", blade["properties"])       # persisted SRD tag surfaces as a chip

    def test_inventory_surface_persisted_stats_win_over_resolvable_catalog(self):
        # Preference is PERSISTED-FIRST, not catalog-first: a "Dwarven Plate" the catalog DOES
        # resolve (base AC 18) but whose snapshot persists a different AC (20) renders the
        # persisted 20 — the engine, not the read-time catalog, is the source of truth (F09-7).
        self.assertEqual(server._catalog_meta("Dwarven Plate").get("ac"), 18)  # catalog says 18
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["cassian"]["inventory"].append({
            "name": "Dwarven Plate", "quantity": 1, "kind": "armor", "ac": 20,
            "armor_category": "heavy", "ac_dex_mod": "none",
        })
        self._write("camp_persistwin", snap)
        _status, surface = self._get_json("/inventory-surface?campaign=camp_persistwin")
        cassian = {m["id"]: m for m in surface["party"]}["cassian"]
        plate = {i["name"]: i for i in cassian["items"]}["Dwarven Plate"]
        self.assertEqual(plate["ac"], 20)
        self.assertEqual(plate["acDisplay"], "AC 20")

    def test_inventory_surface_armor_dex_rule_display(self):
        # F09-6 armor dex rule, surfaced from PERSISTED fields (names the catalog can't resolve).
        # Medium armor caps the DEX bonus -> "AC 14 + DEX (max +2)"; a shield grants a BONUS, so
        # it reads "+2" (NOT the misleading flat "AC 2"); heavy armor adds no DEX -> flat "AC 20";
        # light armor adds the full DEX -> "AC 11 + DEX".
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["cassian"]["inventory"].extend([
            {"name": "Bronze Breastplate", "quantity": 1, "kind": "armor", "ac": 14,
             "armor_category": "medium", "ac_dex_mod": "capped", "ac_dex_cap": 2},
            {"name": "Battered Shield", "quantity": 1, "kind": "armor", "ac": 2,
             "armor_category": "shield", "ac_dex_mod": "none"},
            {"name": "Ironwall Plate", "quantity": 1, "kind": "armor", "ac": 20,
             "armor_category": "heavy", "ac_dex_mod": "none"},
            {"name": "Quilted Jerkin", "quantity": 1, "kind": "armor", "ac": 11,
             "armor_category": "light", "ac_dex_mod": "full"},
        ])
        self._write("camp_armor", snap)
        _status, surface = self._get_json("/inventory-surface?campaign=camp_armor")
        cassian = {m["id"]: m for m in surface["party"]}["cassian"]
        items = {i["name"]: i for i in cassian["items"]}
        bp = items["Bronze Breastplate"]
        self.assertEqual(bp["acDisplay"], "AC 14 + DEX (max +2)")
        self.assertEqual(bp["armorCategory"], "medium")
        self.assertEqual(bp["acDexCap"], 2)
        self.assertEqual(items["Battered Shield"]["acDisplay"], "+2")
        self.assertEqual(items["Battered Shield"]["armorCategory"], "shield")
        self.assertEqual(items["Ironwall Plate"]["acDisplay"], "AC 20")
        self.assertEqual(items["Quilted Jerkin"]["acDisplay"], "AC 11 + DEX")

    def test_inventory_surface_armor_dex_rule_falls_back_to_catalog(self):
        # Pre-F09-7 snapshot: a free-text armor with NO persisted stat fields still renders the
        # F09-6 dex rule via the by-name catalog re-resolve — so old saves don't regress. The
        # real catalog "Breastplate" is medium/cap-2; the real "Shield" is a +2 bonus.
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["cassian"]["inventory"].extend([
            {"name": "Breastplate", "quantity": 1},
            {"name": "Shield", "quantity": 1},
        ])
        self._write("camp_armorfb", snap)
        _status, surface = self._get_json("/inventory-surface?campaign=camp_armorfb")
        cassian = {m["id"]: m for m in surface["party"]}["cassian"]
        items = {i["name"]: i for i in cassian["items"]}
        self.assertEqual(items["Breastplate"]["acDisplay"], "AC 14 + DEX (max +2)")
        self.assertEqual(items["Shield"]["acDisplay"], "+2")

    def test_equipped_items_prefer_persisted_stats_and_dex_rule(self):
        # The heroes-screen paper-doll projection (_equipped_items) gets the same persisted-first
        # treatment: an EQUIPPED renamed blade the catalog can't resolve still carries its damage,
        # and an equipped shield carries the "+2" dex-rule display (not "AC 2").
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["cassian"]["inventory"].extend([
            {"name": "Moonsteel Saber +1", "quantity": 1, "equipped": True,
             "kind": "weapon", "damage": "1d6", "damage_type": "slashing"},
            {"name": "Battered Shield", "quantity": 1, "equipped": True, "kind": "armor",
             "ac": 2, "armor_category": "shield", "ac_dex_mod": "none"},
        ])
        self._write("camp_equip", snap)
        _status, surface = self._get_json("/inventory-surface?campaign=camp_equip")
        cassian = {m["id"]: m for m in surface["party"]}["cassian"]
        equipped = {e["name"]: e for e in cassian["equipped"]}
        self.assertEqual(equipped["Moonsteel Saber +1"]["damage"], "1d6")
        self.assertEqual(equipped["Moonsteel Saber +1"]["damageType"], "slashing")
        self.assertEqual(equipped["Battered Shield"]["acDisplay"], "+2")

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

        camp = surface["campBeats"]
        self.assertEqual(camp["summary"]["records"], 1)
        self.assertEqual(camp["summary"]["solo_cooldown_days"], 2)
        self.assertEqual(camp["summary"]["pair_cooldown_days"], 3)
        self.assertEqual(camp["recent"][0]["participants"], [{"id": "mira", "name": "Mira of the Inkstain"}])
        self.assertEqual(camp["recent"][0]["cooldown"], {"days": 2, "ready_day": 13, "remaining_days": 1})
        self.assertEqual(camp["recent"][0]["note"], "Mira asked Cassian why the sealed gate felt familiar.")
        self.assert_no_private_keys(surface)
        # baseline companion (no arc) carries no betrayal warning
        self.assertIsNone(npcs["mira"]["betrayalWarning"])

    def _snapshot_with_companion_agenda(self, *, attitude_value, threshold, fired=False,
                                        trigger="attitude_below", decision_flag="", flags=None):
        """A model-conformant copy of _SNAPSHOT where the companion `mira` carries a sealed
        attitude_below agenda + a given attitude, so the betrayal-warning band (#118) can be
        exercised. Round-trips through the engine Campaign model (strict), matching the
        established conformant-snapshot pattern."""
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["mira"]["attitude_value"] = attitude_value
        agenda = {"trigger": trigger, "value": threshold, "fired": fired, "note": "sealed: turns on the party"}
        if decision_flag:
            agenda["decision_flag"] = decision_flag
        snap["characters"]["mira"]["arc"] = {"arc_gates": [], "agenda": agenda}
        if flags is not None:
            snap["flags"] = flags
        return snap

    def test_relations_surface_betrayal_warning_present_when_companion_in_danger_band(self):
        # mira at -28 with a live attitude_below agenda (threshold -10) sits in the engine's
        # danger band [-40, -20] AND below the breaking point -> the advisory surfaces.
        snap = self._snapshot_with_companion_agenda(attitude_value=-28, threshold=-10)
        self._write("camp_marches", snap)
        status, surface = self._get_json("/relations-surface?campaign=camp_marches")
        self.assertEqual(status, 200)
        npcs = {n["id"]: n for n in surface["npcs"]}
        warning = npcs["mira"]["betrayalWarning"]
        self.assertIsNotNone(warning)
        self.assertEqual(warning["attitude_value"], -28)
        self.assertEqual(warning["threshold"], -10)
        self.assertEqual(warning["band"], [-40, -20])
        self.assertFalse(warning["decision_active"])
        self.assertTrue(warning["note"])
        # the sealed agenda's private intent never leaks into the surface
        self.assert_no_private_keys(surface)

    def test_relations_surface_betrayal_warning_flags_a_recorded_decision(self):
        # A decision_flag set+True in Campaign.flags marks the rift as choice-deepened.
        snap = self._snapshot_with_companion_agenda(
            attitude_value=-35, threshold=-10, decision_flag="took_bribe", flags={"took_bribe": True})
        self._write("camp_marches", snap)
        _status, surface = self._get_json("/relations-surface?campaign=camp_marches")
        warning = {n["id"]: n for n in surface["npcs"]}["mira"]["betrayalWarning"]
        self.assertIsNotNone(warning)
        self.assertTrue(warning["decision_active"])

    def test_relations_surface_omits_betrayal_warning_outside_the_band(self):
        # Every off-band case must omit the warning (mirror companion_arc._betrayal_warning):
        #  - attitude above the band (not yet fracturing)
        #  - attitude in band but still at/above the agenda's breaking point
        #  - agenda already fired (the betrayal is the event, not a warning)
        #  - a non-attitude_below trigger sitting in the band
        cases = [
            ("above_band", dict(attitude_value=-10, threshold=-5)),
            ("at_or_above_threshold", dict(attitude_value=-30, threshold=-35)),
            ("already_fired", dict(attitude_value=-30, threshold=-10, fired=True)),
            ("wrong_trigger", dict(attitude_value=-30, threshold=20, trigger="day_reached")),
        ]
        for name, kwargs in cases:
            with self.subTest(case=name):
                cid = f"camp_{name}"  # a distinct campaign dir per case (no rewrite clash)
                snap = self._snapshot_with_companion_agenda(**kwargs)
                self._write(cid, snap)
                _status, surface = self._get_json(f"/relations-surface?campaign={cid}")
                self.assertIsNone({n["id"]: n for n in surface["npcs"]}["mira"]["betrayalWarning"])

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

    # ── parley: Layer 3 stumble-into Event block ───────────────────────────────

    def test_parley_surface_omits_event_block_without_live_event(self):
        # the base snapshot has no `events` key -> no event block (today's freeform parley)
        self._write("camp_marches", _SNAPSHOT)
        _status, surface = self._get_json("/parley-surface?campaign=camp_marches")
        self.assertNotIn("event", surface)
        self.assertTrue(surface["free_form"])

    def test_parley_surface_attaches_live_event_options(self):
        # a manual-trigger, unresolved Event surfaces its authored options as the menu slots
        snap = dict(_SNAPSHOT)
        snap["events"] = {
            "event_bribe": {
                "id": "event_bribe", "trigger": "manual", "prompt": "Raphael offers a deal.",
                "anchor_npc_id": "olwen",
                "options": [
                    {"label": "Take the bribe", "tag": "CN", "skill": "deception", "dc": 15},
                    {"label": "Refuse", "tag": "LG"},
                ],
            }
        }
        self._write("camp_marches", snap)
        _status, surface = self._get_json("/parley-surface?campaign=camp_marches")
        self.assertTrue(surface["free_form"])  # free-form path STAYS (never a closed set)
        self.assertIn("event", surface)
        block = surface["event"]
        self.assertEqual(block["id"], "event_bribe")
        self.assertEqual(block["prompt"], "Raphael offers a deal.")
        self.assertEqual(block["resolve_with"], "resolve_event")
        self.assertEqual(block["options"][0], {"label": "Take the bribe", "tag": "CN", "skill": "deception", "dc": 15})
        self.assertEqual(block["options"][1], {"label": "Refuse", "tag": "LG", "skill": "", "dc": 0})
        self.assert_no_private_keys(surface)

    def test_parley_surface_hides_resolved_and_trigger_unmet_events(self):
        snap = dict(_SNAPSHOT)
        snap["day"] = 4
        snap["events"] = {
            "event_done": {"id": "event_done", "trigger": "manual", "resolved": True,
                            "prompt": "spent", "options": [{"label": "X"}]},
            "event_future": {"id": "event_future", "trigger": "day_reached", "trigger_threshold": 99,
                              "prompt": "not yet", "options": [{"label": "Y"}]},
        }
        self._write("camp_marches", snap)
        _status, surface = self._get_json("/parley-surface?campaign=camp_marches")
        self.assertNotIn("event", surface)  # nothing live

    def test_parley_surface_event_trigger_reads_snapshot_flag(self):
        snap = dict(_SNAPSHOT)
        snap["flags"] = {"met_raphael": True}
        snap["events"] = {
            "event_gated": {"id": "event_gated", "trigger": "flag_set", "trigger_value": "met_raphael",
                             "prompt": "He returns.", "options": [{"label": "Greet"}]},
        }
        self._write("camp_marches", snap)
        _status, surface = self._get_json("/parley-surface?campaign=camp_marches")
        self.assertIn("event", surface)
        self.assertEqual(surface["event"]["id"], "event_gated")

    # ── parley: #751 NPC binding (header identifies the NPC, pinned) ────────────

    def test_parley_surface_omits_npc_block_without_a_target(self):
        # No ?npc and no live anchored event -> no npc block (byte-identical to today's
        # freeform parley). The actor stays the lead PC; the header has nothing to repoint.
        self._write("camp_marches", _SNAPSHOT)
        _status, surface = self._get_json("/parley-surface?campaign=camp_marches")
        self.assertNotIn("npc", surface)
        self.assertEqual(surface["actor"], "Cassian Frostbreaker")  # actor unchanged

    def test_parley_surface_binds_explicit_npc_id_and_pins_it(self):
        # #751: ?npc=<id> binds the parley to the NPC the player opened the conversation with,
        # so the header names the NPC (not the player) and the surface carries a STABLE id the
        # frontend pins for the interaction.
        self._write("camp_marches", _SNAPSHOT)
        _status, surface = self._get_json("/parley-surface?campaign=camp_marches&npc=olwen")
        self.assertIn("npc", surface)
        npc = surface["npc"]
        self.assertEqual(npc["id"], "olwen")
        self.assertEqual(npc["name"], "Toll-keeper Olwen")
        self.assertTrue(npc["met"])
        # The lead PC is still the ACTOR (whose sheet drives the skill modifiers); the NPC is
        # the conversation TARGET. Both are carried — the header renders the npc, the slots the actor.
        self.assertEqual(surface["actor"], "Cassian Frostbreaker")
        self.assert_no_private_keys(surface)

    def test_parley_surface_npc_falls_back_to_live_event_anchor(self):
        # With no explicit ?npc but a live Event anchored on an NPC, the surface binds to that
        # anchor NPC (the engine already chose WHO the stumble-into is about).
        snap = dict(_SNAPSHOT)
        snap["events"] = {
            "event_toll": {
                "id": "event_toll", "trigger": "manual", "prompt": "Olwen bars the way.",
                "anchor_npc_id": "olwen",
                "options": [{"label": "Bargain", "tag": "N", "skill": "persuasion", "dc": 14}],
            }
        }
        self._write("camp_marches", snap)
        _status, surface = self._get_json("/parley-surface?campaign=camp_marches")
        self.assertIn("npc", surface)
        self.assertEqual(surface["npc"]["id"], "olwen")
        self.assertEqual(surface["npc"]["name"], "Toll-keeper Olwen")

    def test_parley_surface_explicit_npc_wins_over_event_anchor(self):
        # An explicit ?npc pins THAT NPC even when a live event anchors a different one — the
        # player's chosen interlocutor is authoritative, fixing the "switch mid-interaction" half.
        snap = dict(_SNAPSHOT)
        snap["characters"]["mira"]["met"] = True
        snap["events"] = {
            "event_toll": {
                "id": "event_toll", "trigger": "manual", "prompt": "Olwen bars the way.",
                "anchor_npc_id": "olwen", "options": [{"label": "Bargain"}],
            }
        }
        self._write("camp_marches", snap)
        _status, surface = self._get_json("/parley-surface?campaign=camp_marches&npc=mira")
        self.assertIn("npc", surface)
        self.assertEqual(surface["npc"]["id"], "mira")  # the pinned target, not the event anchor

    def test_parley_surface_unknown_npc_id_degrades_gracefully(self):
        # An unknown id never raises mid-scene — it degrades to a freeform parley (no npc block),
        # exactly like an unknown event_id does.
        self._write("camp_marches", _SNAPSHOT)
        _status, surface = self._get_json("/parley-surface?campaign=camp_marches&npc=nobody")
        self.assertNotIn("npc", surface)
        self.assertEqual(surface["actor"], "Cassian Frostbreaker")

    # ── parley: #615 disposition meter (attitude band + value on the npc block) ──

    def test_parley_surface_npc_carries_disposition_band_and_value(self):
        # #615: the bound NPC block carries the canonical disposition bucket (reusing
        # _attitude_disposition) + the raw attitude_value, so the Dialogue screen can render a
        # live disposition meter reusing DispositionDot.
        self._write("camp_marches", _SNAPSHOT)
        _status, surface = self._get_json("/parley-surface?campaign=camp_marches&npc=olwen")
        npc = surface["npc"]
        # olwen is guarded / attitude_value -10 -> _attitude_disposition -> "cool"
        self.assertEqual(npc["disposition"], server._attitude_disposition(_SNAPSHOT["characters"]["olwen"]))
        self.assertEqual(npc["disposition"], "cool")
        self.assertEqual(npc["attitude_value"], -10)
        self.assertEqual(npc["attitude"], "guarded")

    def test_parley_surface_disposition_renders_even_at_zero_attitude(self):
        # The meter reads the existing attitude_value and is fine when it's 0 (a freshly-met NPC
        # with no recorded stance still gets a "neutral" band — never a missing meter).
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["greeter"] = {
            "id": "greeter", "name": "Gate Greeter", "kind": "npc", "met": True,
            "location_id": "lanternrest", "attitude": "", "attitude_value": 0,
        }
        self._write("camp_marches", snap)
        _status, surface = self._get_json("/parley-surface?campaign=camp_marches&npc=greeter")
        npc = surface["npc"]
        self.assertEqual(npc["attitude_value"], 0)
        self.assertEqual(npc["disposition"], "neutral")


if __name__ == "__main__":
    unittest.main()
