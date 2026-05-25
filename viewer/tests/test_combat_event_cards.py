import json
import shutil
import subprocess
import unittest
from pathlib import Path


_DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "dashboard.html"


def _renderer_source() -> str:
    html = _DASHBOARD_PATH.read_text(encoding="utf-8")
    start = html.index("const esc =")
    status_start = html.index("const statusClass =", start)
    end = html.index("const countNames =", status_start)
    return html[start:end]


@unittest.skipIf(shutil.which("node") is None, "node is required for dashboard renderer fixture tests")
class CombatEventCardTests(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    def _render_cards(self) -> dict:
        fixtures = {
            "attack": {
                "kind": "combat",
                "text": "Vela hits Goblin.",
                "payload": {
                    "schema": "clawdnd.combat_event.v1",
                    "event": "attack",
                    "outcome": "crit",
                    "actor": {"name": 'Vela <script>alert("x")</script>'},
                    "target": {"name": "Goblin", "ac": 13},
                    "roll": {"total": 24, "natural": 20},
                    "damage": {"total": 11, "type": "slashing"},
                    "target_state": {"current_hp": 2},
                },
            },
            "zone_movement": {
                "kind": "combat",
                "text": "Vela moves.",
                "payload": {
                    "schema": "clawdnd.combat_event.v1",
                    "event": "zone_movement",
                    "actor": {"name": "Vela"},
                    "from_zone": "Doorway",
                    "to_zone": "Altar <img src=x onerror=alert(1)>",
                    "opportunity_attack": True,
                    "provokers": [{"name": "Cultist"}],
                    "warnings": ['"Altar" is not adjacent'],
                },
            },
            "turn_advanced": {
                "kind": "combat",
                "text": "Turn advances.",
                "payload": {
                    "schema": "clawdnd.combat_event.v1",
                    "event": "turn_advanced",
                    "round": 3,
                    "new_round": True,
                    "current": {"name": "Goblin"},
                    "turn_index": 1,
                    "death_save_due": True,
                    "expired_effects": [{"name": "Bless"}],
                },
            },
            "death_save": {
                "kind": "combat",
                "text": "Vela rolls a death save.",
                "payload": {
                    "schema": "clawdnd.combat_event.v1",
                    "event": "death_save",
                    "target": {"name": "Vela"},
                    "roll": {"total": 1, "natural": 1},
                    "result": "dead",
                    "successes": 0,
                    "failures": 3,
                    "state": {"current_hp": 0, "dead": True, "stable": False, "dying": False},
                },
            },
            "unknown": {
                "kind": "combat",
                "text": "Plain fallback text.",
                "payload": {"schema": "clawdnd.combat_event.v1", "event": "new_future_event"},
            },
            "plain": {"kind": "combat", "text": "Plain combat text."},
        }
        program = (
            _renderer_source()
            + "\nconst fixtures = "
            + json.dumps(fixtures)
            + ";\nconst out = Object.fromEntries(Object.entries(fixtures).map(([k, v]) => [k, combatEventCard(v)]));\nconsole.log(JSON.stringify(out));\n"
        )
        proc = subprocess.run(
            [self.NODE_BIN],
            input=program,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(proc.stdout)

    def test_new_combat_payloads_render_as_cards(self):
        cards = self._render_cards()

        self.assertIn("outcome-crit", cards["attack"])
        self.assertIn("Attack 24 vs AC 13", cards["attack"])
        self.assertIn("Damage 11 slashing", cards["attack"])

        self.assertIn("outcome-move", cards["zone_movement"])
        self.assertIn("moves from Doorway to Altar", cards["zone_movement"])
        self.assertIn("OA Cultist", cards["zone_movement"])
        self.assertIn("class=\"warn\"", cards["zone_movement"])

        self.assertIn("outcome-turn", cards["turn_advanced"])
        self.assertIn("Turn advances to Goblin", cards["turn_advanced"])
        self.assertIn("Death save due", cards["turn_advanced"])
        self.assertIn("Expired Bless", cards["turn_advanced"])

        self.assertIn("outcome-death-save", cards["death_save"])
        self.assertIn("Vela rolls 1: dead", cards["death_save"])
        self.assertIn("Saves 0 success / 3 fail", cards["death_save"])
        self.assertIn("State dead", cards["death_save"])

    def test_cards_escape_payload_strings_and_preserve_fallbacks(self):
        cards = self._render_cards()
        joined = "\n".join(cards.values())

        self.assertNotIn("<script>", joined)
        self.assertNotIn("<img", joined)
        self.assertIn("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", cards["attack"])
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", cards["zone_movement"])
        self.assertEqual(cards["unknown"], "")
        self.assertEqual(cards["plain"], "")


if __name__ == "__main__":
    unittest.main()
