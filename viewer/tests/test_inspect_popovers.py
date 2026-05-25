import json
import shutil
import subprocess
import unittest
from pathlib import Path


_DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "dashboard.html"


def _renderer_source() -> str:
    html = _DASHBOARD_PATH.read_text(encoding="utf-8")
    start = html.index("const esc =")
    end = html.index("// ---- campaign switcher wiring", start)
    return html[start:end]


@unittest.skipIf(shutil.which("node") is None, "node is required for dashboard renderer fixture tests")
class InspectPopoverTests(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    def _render_popovers(self) -> dict:
        program = (
            _renderer_source()
            + """
const state = {
  current_location_id: "square",
  locations: {
    square: {
      id: "square",
      name: "Market <script>alert(\\"x\\")</script>",
      description: "Lantern stalls.",
      region: "Old Ward",
      visited: true,
      connections: ["gate"],
      travel_times: { gate: 12 },
    },
    gate: { id: "gate", name: "North Gate", visited: false, connections: ["square"] },
  },
  characters: {
    pc: {
      id: "pc",
      name: "Vela",
      kind: "player",
      race: "Human",
      classes: [{ name: "Wizard", level: 3 }],
      current_hp: 12,
      max_hp: 18,
      armor_class: 13,
      saving_throw_proficiencies: ["int", "wis"],
      class_resources: { arcane_recovery: { max: 1, used: 0, recharge: "long" } },
    },
  },
};
const fixtures = {
  character: inspectPopoverHTML(buildInspectPayload("character", state.characters.pc, { state })),
  item: inspectPopoverHTML(buildInspectPayload("item", {
    name: "Potion <b>of Healing</b>",
    quantity: 2,
    equipped: false,
    requires_attunement: true,
    attuned: false,
    weight: 0.5,
    description: "Restores a little health.",
  }, { state })),
  spell: inspectPopoverHTML(buildInspectPayload("spell", {
    name: "Magic Missile",
    level: 1,
    casting_time: "1 action",
    range: "120 ft",
    prepared: true,
    slots: "2/4",
  }, { state })),
  quest: inspectPopoverHTML(buildInspectPayload("quest", {
    id: "quest_1234567",
    title: "Find the Bell",
    status: "active",
    objectives: [{ text: "Reach the tower", done: false }],
    location_id: "square",
    giver_id: "pc",
    note: "DM-only seed detail",
    arc_back: "hidden arc note",
  }, { state, kind: "tracked" })),
  location: inspectPopoverHTML(buildInspectPayload("location", state.locations.square, { state, id: "square" })),
};
console.log(JSON.stringify(fixtures));
"""
        )
        proc = subprocess.run(
            [self.NODE_BIN],
            input=program,
            text=True,
            capture_output=True,
        )
        if proc.returncode:
            self.fail(proc.stderr)
        return json.loads(proc.stdout)

    def test_renders_rich_popovers_for_snapshot_entities(self):
        popovers = self._render_popovers()

        self.assertIn("Vela", popovers["character"])
        self.assertIn("HP</span><b>12/18</b>", popovers["character"])
        self.assertIn("Saves</span><b>INT, WIS</b>", popovers["character"])
        self.assertIn("Arcane Recovery 1/1", popovers["character"])

        self.assertIn("Potion &lt;b&gt;of Healing&lt;/b&gt;", popovers["item"])
        self.assertIn("Qty</span><b>2</b>", popovers["item"])
        self.assertIn("needs attunement", popovers["item"])

        self.assertIn("Magic Missile", popovers["spell"])
        self.assertIn("Level</span><b>1</b>", popovers["spell"])
        self.assertIn("Slots</span><b>2/4</b>", popovers["spell"])

        self.assertIn("Find the Bell", popovers["quest"])
        self.assertIn("Reach the tower", popovers["quest"])
        self.assertNotIn("DM-only seed detail", popovers["quest"])
        self.assertNotIn("hidden arc note", popovers["quest"])

        self.assertIn("Market &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;", popovers["location"])
        self.assertIn("Region</span><b>Old Ward</b>", popovers["location"])
        self.assertIn("Connected</span><b>North Gate", popovers["location"])
        self.assertNotIn("<script>", popovers["location"])


if __name__ == "__main__":
    unittest.main()
