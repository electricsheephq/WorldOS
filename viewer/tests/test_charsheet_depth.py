"""Character-sheet data-depth surface tests (optimizer-persona gaps).

Two read-model gaps an optimizer flagged in a built-.app playtest:

  Bug 1 - the Spells tab never exposed the *character-level* Spell Save DC and Spell
          Attack Bonus (only per-spell saveDc from PR #410). A caster could not plan.
          A non-caster must NOT get a fabricated DC.

  Bug 2 - the Abilities tab read "No active abilities recorded" for a L3 wizard even
          though the engine populates class/subclass features as NAMES in
          Character.features (already surfaced by the read-model as `classFeatures`).
          The fix surfaces those on the character surface so the Abilities tab can
          render them; feature DESCRIPTIONS and RACIAL TRAITS are not modeled by the
          engine, so they stay absent (never fabricated).

Mirrors test_readmodel_surfaces.py: load server.py via importlib, drive the real
/character-surface route against a model-conformant snapshot written to a temp state dir.
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
_SPEC = importlib.util.spec_from_file_location("viewer_server_charsheet", _SERVER_PATH)
assert _SPEC is not None
server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(server)


# A model-conformant snapshot: a level-3 evocation Wizard (INT caster) + a level-4 Fighter
# (non-caster). The Wizard carries engine-populated class features (NAMES) in `features`.
_SNAPSHOT = {
    "id": "camp_depth",
    "title": "The Tower at Dusk",
    "world_id": "stolen-marches",
    "day": 3,
    "party": ["elara", "thornwick"],
    "characters": {
        "elara": {
            "id": "elara", "name": "Elara Moonwhisper", "kind": "player", "race": "High Elf",
            "alignment": "Neutral Good",
            "classes": [{"name": "Wizard", "level": 3, "subclass": "School of Evocation"}],
            "abilities": {"strength": 8, "dexterity": 14, "constitution": 13,
                          "intelligence": 16, "wisdom": 12, "charisma": 10},
            "proficiency_bonus": 2, "armor_class": 12, "max_hp": 17, "current_hp": 17,
            "spell_slots": {"1": {"maximum": 4, "used": 0}, "2": {"maximum": 2, "used": 0}},
            "spells_known": ["Fire Bolt", "Magic Missile", "Shield"],
            "spells_prepared": ["Magic Missile", "Scorching Ray"],
            # Engine-populated class/subclass feature NAMES (srd_tables.features_through).
            "features": ["Arcane Recovery", "Evocation Savant", "Sculpt Spells"],
        },
        "thornwick": {
            "id": "thornwick", "name": "Thornwick", "kind": "player", "race": "Human",
            "alignment": "Lawful Neutral",
            "classes": [{"name": "Fighter", "level": 4, "subclass": "Champion"}],
            "abilities": {"strength": 16, "dexterity": 12, "constitution": 14,
                          "intelligence": 10, "wisdom": 11, "charisma": 9},
            "proficiency_bonus": 2, "armor_class": 18, "max_hp": 36, "current_hp": 36,
            "features": ["Second Wind", "Action Surge", "Improved Critical"],
        },
        # A second caster whose "Spellcasting" feature is the SAME NAME as the Wizard's but a
        # DIFFERENT class+ability (Charisma/bard). Used to prove the read-model resolves the
        # description against the character's ACTUAL class, not whichever class loaded first.
        "lyric": {
            "id": "lyric", "name": "Lyric Songsteel", "kind": "player", "race": "Half-Elf",
            "alignment": "Chaotic Good",
            "classes": [{"name": "Bard", "level": 3, "subclass": "College of Lore"}],
            "abilities": {"strength": 9, "dexterity": 14, "constitution": 12,
                          "intelligence": 11, "wisdom": 10, "charisma": 16},
            "proficiency_bonus": 2, "armor_class": 13, "max_hp": 18, "current_hp": 18,
            "spell_slots": {"1": {"maximum": 4, "used": 0}, "2": {"maximum": 2, "used": 0}},
            # Both casters carry the shared-name "Spellcasting" class feature.
            "features": ["Spellcasting", "Bardic Inspiration", "Expertise"],
        },
    },
}

# Give the Wizard the same shared-name "Spellcasting" feature so the cross-class guard can
# compare the two side by side (the engine populates it for every caster at L1).
_SNAPSHOT["characters"]["elara"]["features"] = [
    "Spellcasting", "Arcane Recovery", "Evocation Savant", "Sculpt Spells",
]
_SNAPSHOT["party"].append("lyric")


class _QuietHandler(server._Handler):
    def log_message(self, fmt: str, *args: object) -> None:
        return


class CharsheetDepthTests(unittest.TestCase):
    # ── direct unit coverage of the new read-model helpers ──────────────────────

    def test_caster_spellcasting_summary(self):
        """Wizard L3, INT 16: DC = 8 + prof(2) + int_mod(+3) = 13; attack = prof + mod = +5."""
        cast = server._character_spellcasting(_SNAPSHOT["characters"]["elara"])
        self.assertIsNotNone(cast)
        self.assertEqual(cast["ability"], "intelligence")
        self.assertEqual(cast["abilityShort"], "int")
        self.assertEqual(cast["spellSaveDc"], 13)
        self.assertEqual(cast["spellAttackBonus"], 5)

    def test_noncaster_has_no_fabricated_spellcasting(self):
        """A Fighter has no SRD caster class -> summary is None (no fake DC/attack)."""
        self.assertIsNone(server._character_spellcasting(_SNAPSHOT["characters"]["thornwick"]))
        self.assertIsNone(server._spell_save_dc(_SNAPSHOT["characters"]["thornwick"]))
        self.assertIsNone(server._spell_attack_bonus(_SNAPSHOT["characters"]["thornwick"]))

    def test_dc_and_attack_track_proficiency_and_ability(self):
        """Higher level + ability => higher DC/attack, proving derivation (not hardcoded)."""
        higher = copy.deepcopy(_SNAPSHOT["characters"]["elara"])
        higher["classes"][0]["level"] = 5
        higher["proficiency_bonus"] = 3
        higher["abilities"]["intelligence"] = 18
        cast = server._character_spellcasting(higher)
        # prof 3, int_mod(18) = +4 -> DC 8+3+4 = 15; attack 3+4 = +7
        self.assertEqual(cast["spellSaveDc"], 15)
        self.assertEqual(cast["spellAttackBonus"], 7)

    # ── prepared-spell CAP (Rest & Prepare budget) ──────────────────────────────

    def test_prepared_cap_full_caster_is_level_plus_ability_mod(self):
        """A FULL prepared caster (Wizard L3, INT 16 -> +3) prepares level + mod = 3 + 3 = 6."""
        self.assertEqual(server._prepared_spell_cap(_SNAPSHOT["characters"]["elara"]), 6)

    def test_prepared_cap_half_caster_paladin_is_floor_half_level_plus_mod(self):
        """A HALF-caster Paladin (L10, CHA 16 -> +3) prepares floor(10/2) + 3 = 8 — the formula the
        Rest & Prepare task specifies for the L10 Paladin in the complaint."""
        paladin = {
            "id": "wyll", "name": "Wyll",
            "classes": [{"name": "Paladin", "level": 10, "subclass": "Oath of Vengeance"}],
            "abilities": {"strength": 16, "dexterity": 10, "constitution": 14,
                          "intelligence": 10, "wisdom": 11, "charisma": 16},
            "proficiency_bonus": 4,
        }
        self.assertEqual(server._prepared_spell_cap(paladin), 8)

    def test_prepared_cap_never_below_one(self):
        """The cap floors at 1 even when level + a NEGATIVE ability mod would compute lower (a L1
        half-caster Ranger with WIS 8 -> floor(1/2)=0 + (-1) = -1, clamped to 1)."""
        ranger = {
            "id": "r", "name": "Ranger",
            "classes": [{"name": "Ranger", "level": 1}],
            "abilities": {"wisdom": 8, "strength": 14, "dexterity": 16,
                          "constitution": 12, "intelligence": 10, "charisma": 10},
        }
        self.assertEqual(server._prepared_spell_cap(ranger), 1)

    def test_prepared_cap_none_for_known_caster_and_non_caster(self):
        """A KNOWN-caster (Bard — never re-prepares) and a non-caster (Fighter) have NO cap, so the
        Rest & Prepare UI shows no budget (never a fabricated one)."""
        self.assertIsNone(server._prepared_spell_cap(_SNAPSHOT["characters"]["lyric"]))   # Bard
        self.assertIsNone(server._prepared_spell_cap(_SNAPSHOT["characters"]["thornwick"]))  # Fighter

    # ── end-to-end via the real /character-surface route ────────────────────────

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

    def _party(self, surface: dict) -> dict:
        return {c["id"]: c for c in surface["party"]}

    def test_surface_exposes_caster_spell_dc_and_attack(self):
        self._write("camp_depth", _SNAPSHOT)
        status, surface = self._get_json("/character-surface?campaign=camp_depth")
        self.assertEqual(status, 200)
        elara = self._party(surface)["elara"]
        cast = elara["spellcasting"]
        self.assertIsNotNone(cast)
        self.assertEqual(cast["spellSaveDc"], 13)
        self.assertEqual(cast["spellAttackBonus"], 5)
        self.assertEqual(cast["abilityShort"], "int")

    def test_surface_exposes_hit_dice_and_passive_perception(self):
        # #depth regression guard: the read-model must emit hitDice/hitDiceRemaining +
        # passivePerception (derivable from the character model) so the Defense block can render
        # them — the exact "engine has it, viewer silently drops it" class the depth audit found.
        self._write("camp_depth", _SNAPSHOT)
        status, surface = self._get_json("/character-surface?campaign=camp_depth")
        self.assertEqual(status, 200)
        stats = self._party(surface)["elara"]["stats"]
        self.assertIn("hitDice", stats)
        self.assertIn("hitDiceRemaining", stats)
        self.assertIsInstance(stats["passivePerception"], int)

    def test_surface_exposes_currency_for_market_purse(self):
        # #depth regression guard: the character-surface must emit live `currency`
        # (cp/sp/ep/gp/pp) so the Market reads the SAME purse as the Stash — fixes the
        # coin contradiction where the merchant showed a hardcoded 232gp. If this emit
        # drops, the Market silently diverges from the engine's currency again.
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["elara"]["currency"] = {"gp": 50, "sp": 7, "cp": 3}
        self._write("camp_cur", snap)
        status, surface = self._get_json("/character-surface?campaign=camp_cur")
        self.assertEqual(status, 200)
        cur = self._party(surface)["elara"]["currency"]
        self.assertEqual(cur["gp"], 50)
        self.assertEqual(cur["sp"], 7)
        self.assertEqual(cur["cp"], 3)
        # zero-fill for denominations not set -> stable shape for the UI
        self.assertEqual(cur["pp"], 0)
        self.assertEqual(cur["ep"], 0)

    def test_surface_omits_spellcasting_for_non_caster(self):
        self._write("camp_depth", _SNAPSHOT)
        _status, surface = self._get_json("/character-surface?campaign=camp_depth")
        thornwick = self._party(surface)["thornwick"]
        # Key present for a stable shape, value None -> the Spells tab header omits itself.
        self.assertIn("spellcasting", thornwick)
        self.assertIsNone(thornwick["spellcasting"])

    def test_surface_exposes_prepared_cap_for_prepared_caster(self):
        # The Rest & Prepare "N / cap selected" budget: the read-model emits preparedCap so the
        # spell-selection UI can enforce the cap. Wizard L3 INT+3 -> 6; a Fighter has no cap (None).
        self._write("camp_depth", _SNAPSHOT)
        _status, surface = self._get_json("/character-surface?campaign=camp_depth")
        party = self._party(surface)
        self.assertEqual(party["elara"]["preparedCap"], 6)
        self.assertIn("preparedCap", party["thornwick"])
        self.assertIsNone(party["thornwick"]["preparedCap"])

    def test_surface_surfaces_engine_class_features(self):
        """Bug 2: the engine's `features` NAMES reach the surface (as classFeatures) so the
        Abilities tab can render them instead of 'No active abilities recorded'."""
        self._write("camp_depth", _SNAPSHOT)
        _status, surface = self._get_json("/character-surface?campaign=camp_depth")
        elara = self._party(surface)["elara"]
        names = [c["name"] for c in elara["classFeatures"]]
        self.assertIn("Arcane Recovery", names)
        self.assertIn("Evocation Savant", names)
        # subclass (School of Magic) is surfaced as the archetype, so the tab has context
        self.assertEqual(elara["archetype"], "School of Evocation")
        # #depth: the read-model now JOINS the SRD class-feature descriptions
        # (data/srd/class_features.json), so a known class feature carries real detail
        # (was blank before — the engine authored 260 descs the surface was dropping).
        arcane = next(c for c in elara["classFeatures"] if c["name"] == "Arcane Recovery")
        self.assertTrue(arcane["detail"], "Arcane Recovery should carry its SRD description")
        self.assertIn("slot", arcane["detail"].lower())

    def test_shared_feature_desc_is_class_aware(self):
        """Cross-class feature-description bug (dc0d625 sweep): "Spellcasting" is one feature NAME
        shared by 7 caster classes with 7 DISTINCT descriptions. Keying the SRD desc map by NAME
        alone collapsed them onto whichever class loaded first (bard), so a Wizard's Spellcasting
        read out the BARD's text — wrong class AND wrong ability. The read-model must resolve the
        description against the character's ACTUAL class: a Wizard's references Intelligence/wizard,
        a Bard's references Charisma/bard."""
        self._write("camp_depth", _SNAPSHOT)
        _status, surface = self._get_json("/character-surface?campaign=camp_depth")
        party = self._party(surface)

        def _spellcasting_detail(char):
            return next(c["detail"] for c in char["classFeatures"] if c["name"] == "Spellcasting")

        wiz_detail = _spellcasting_detail(party["elara"]).lower()
        bard_detail = _spellcasting_detail(party["lyric"]).lower()

        # Wizard: Intelligence + wizard list (NOT the bard's Charisma text).
        self.assertIn("intelligence", wiz_detail)
        self.assertIn("wizard", wiz_detail)
        self.assertNotIn("charisma", wiz_detail)
        self.assertNotIn("bard", wiz_detail)

        # Bard: Charisma + bard list.
        self.assertIn("charisma", bard_detail)
        self.assertIn("bard", bard_detail)
        self.assertNotIn("intelligence", bard_detail)

        # And the two descriptions are genuinely different (proves the lookup is class-aware,
        # not just that one class happens to match the global-first entry).
        self.assertNotEqual(wiz_detail, bard_detail)

    def test_unique_named_feature_desc_not_regressed(self):
        """Guard against over-correction: a UNIQUELY-named class feature (one class, one desc)
        must still carry its SRD description. The class-aware fix must not blank these out."""
        self._write("camp_depth", _SNAPSHOT)
        _status, surface = self._get_json("/character-surface?campaign=camp_depth")
        elara = self._party(surface)["elara"]
        arcane = next(c for c in elara["classFeatures"] if c["name"] == "Arcane Recovery")
        self.assertTrue(arcane["detail"], "Arcane Recovery should still carry its SRD description")
        self.assertIn("slot", arcane["detail"].lower())

    def test_surface_class_features_empty_when_engine_has_none(self):
        """A character with no engine-populated features surfaces an empty list (honest),
        not fabricated feature text."""
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["elara"]["features"] = []
        self._write("camp_empty", snap)
        _status, surface = self._get_json("/character-surface?campaign=camp_empty")
        elara = self._party(surface)["elara"]
        self.assertEqual(elara["classFeatures"], [])

    def test_surface_flags_pending_subclass_choice(self):
        # #397 (read-model increment 1): a character at/above its subclass-selection level (3;
        # warlock 6) with NO subclass set must flag pendingSubclass=True so the character screen
        # can offer the build-choice picker. Detect, not auto-fill (#624 proved auto-fill pre-empts
        # the choice). With a subclass set, or below the level, it's False.
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["elara"]["classes"][0]["subclass"] = None  # L3 wizard, no Arcane Tradition
        self._write("camp_pend", snap)
        _s, surface = self._get_json("/character-surface?campaign=camp_pend")
        self.assertTrue(self._party(surface)["elara"]["pendingSubclass"])
        # the default snapshot: elara has "School of Evocation", thornwick (L4 fighter) has "Champion"
        self._write("camp_set", copy.deepcopy(_SNAPSHOT))
        _s2, surface2 = self._get_json("/character-surface?campaign=camp_set")
        self.assertFalse(self._party(surface2)["elara"]["pendingSubclass"])
        self.assertFalse(self._party(surface2)["thornwick"]["pendingSubclass"])

    def test_surface_exposes_fighting_style_name(self):
        """Fighting Style display (3582dc2 sweep, veteran/optimizer): the engine now records a
        canon martial's chosen Fighting Style in `fighting_style`; the read-model must surface it
        as `fightingStyle` so the FeatsTab can render a NAMED style instead of a blank stub."""
        ch = copy.deepcopy(_SNAPSHOT["characters"]["thornwick"])  # L4 Fighter
        ch["fighting_style"] = "Defense"
        sheet = server._character_sheet("thornwick", ch)
        self.assertEqual(sheet["fightingStyle"], "Defense")

    def test_surface_fighting_style_empty_for_non_martial(self):
        """A non-martial (Wizard) carries no Fighting Style -> the key is present (stable shape)
        but empty, so the FeatsTab omits the section honestly rather than showing a blank style."""
        ch = copy.deepcopy(_SNAPSHOT["characters"]["elara"])  # Wizard, no fighting_style field
        sheet = server._character_sheet("elara", ch)
        self.assertIn("fightingStyle", sheet)
        self.assertEqual(sheet["fightingStyle"], "")

    def test_capitalized_skill_proficiencies_still_project(self):
        """QA 2026-06-03 (optimizer crit, sat=4): a canon-seated character can carry CAPITALIZED
        skill names on a stale snapshot (['Arcana','History',...]). The Skills tab must still mark
        them proficient WITH the proficiency bonus, not '0 proficient' — a case mismatch against the
        lowercase SKILL_ABILITIES keys. Mirrors the engine Character._normalize_skill_case."""
        ch = copy.deepcopy(_SNAPSHOT["characters"]["elara"])  # INT 16 (+3), proficiency_bonus 2
        ch["skill_proficiencies"] = ["Arcana", "History", "Investigation", "Perception"]
        ch["skill_expertise"] = []
        sheet = server._character_sheet("elara", ch)
        by_name = {s["name"]: s for s in sheet["skills"]}
        proficient = [s for s in sheet["skills"] if s["proficient"]]
        self.assertEqual(len(proficient), 4, "all 4 capitalized proficiencies must project as proficient")
        self.assertTrue(by_name["Arcana"]["proficient"])
        # Arcana (INT) = INT mod (+3) + proficiency (+2) = +5, NOT the raw +3 of the case-broken bug.
        self.assertEqual(by_name["Arcana"]["mod"], 5)
        self.assertFalse(by_name["Acrobatics"]["proficient"])


if __name__ == "__main__":
    unittest.main()
