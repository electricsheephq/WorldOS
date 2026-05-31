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
    },
}


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

    def test_surface_omits_spellcasting_for_non_caster(self):
        self._write("camp_depth", _SNAPSHOT)
        _status, surface = self._get_json("/character-surface?campaign=camp_depth")
        thornwick = self._party(surface)["thornwick"]
        # Key present for a stable shape, value None -> the Spells tab header omits itself.
        self.assertIn("spellcasting", thornwick)
        self.assertIsNone(thornwick["spellcasting"])

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
        # honest: the engine models feature NAMES, not descriptions -> detail is empty
        arcane = next(c for c in elara["classFeatures"] if c["name"] == "Arcane Recovery")
        self.assertEqual(arcane["detail"], "")

    def test_surface_class_features_empty_when_engine_has_none(self):
        """A character with no engine-populated features surfaces an empty list (honest),
        not fabricated feature text."""
        snap = copy.deepcopy(_SNAPSHOT)
        snap["characters"]["elara"]["features"] = []
        self._write("camp_empty", snap)
        _status, surface = self._get_json("/character-surface?campaign=camp_empty")
        elara = self._party(surface)["elara"]
        self.assertEqual(elara["classFeatures"], [])


if __name__ == "__main__":
    unittest.main()
