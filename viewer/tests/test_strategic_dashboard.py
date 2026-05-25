import json
import shutil
import subprocess
import unittest
from pathlib import Path


_DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "dashboard.html"


def _strategic_source() -> str:
    html = _DASHBOARD_PATH.read_text(encoding="utf-8")
    start = html.index("const esc =")
    end = html.index("function renderState", start)
    return html[start:end]


@unittest.skipIf(shutil.which("node") is None, "node is required for dashboard renderer fixture tests")
class StrategicDashboardTests(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    def _render(self, state):
        program = (
            _strategic_source()
            + "\nconst state = "
            + json.dumps(state)
            + ";\nconsole.log(JSON.stringify({"
            + "board: strategicDashboardHTML(state),"
            + "loc: strategicLocationBadges(state, 'loc-market'),"
            + "classes: strategicLocationClasses(state, 'loc-market')"
            + "}));\n"
        )
        proc = subprocess.run(
            [self.NODE_BIN],
            input=program,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(proc.stdout)

    def test_strategic_board_renders_assets_and_urgency_sorted_work(self):
        out = self._render({
            "current_location_id": "loc-market",
            "locations": {
                "loc-market": {"id": "loc-market", "name": "Salt Market"},
                "loc-docks": {"id": "loc-docks", "name": "The Docks"},
            },
            "factions": {
                "fac-watch": {"id": "fac-watch", "name": "Harbor Watch", "reputation": 1},
                "fac-guild": {"id": "fac-guild", "name": "Gilded Knife", "reputation": -2},
            },
            "strategic_state": {
                "regions": {
                    "loc-market": {
                        "location_id": "loc-market",
                        "controller_id": "fac-watch",
                        "status": "contested",
                        "stability": 35,
                        "unrest": 72,
                        "note": "A tense square.",
                    }
                },
                "assets": {
                    "asset-watch": {
                        "id": "asset-watch",
                        "faction_id": "fac-watch",
                        "name": "Market Wardens",
                        "kind": "army",
                        "location_id": "loc-market",
                        "strength": 3,
                    }
                },
                "clocks": {
                    "clock-slow": {
                        "id": "clock-slow",
                        "title": "Quiet investigation",
                        "kind": "mystery",
                        "region_id": "loc-docks",
                        "progress": 1,
                        "target": 6,
                    },
                    "clock-urgent": {
                        "id": "clock-urgent",
                        "title": "Guild coup <tonight>",
                        "kind": "threat",
                        "region_id": "loc-market",
                        "progress": 5,
                        "target": 6,
                    },
                },
                "projects": {
                    "proj-ready": {
                        "id": "proj-ready",
                        "title": "Raise barricades across the really long market lane",
                        "kind": "construction",
                        "location_id": "loc-market",
                        "faction_id": "fac-watch",
                        "progress_days": 6,
                        "duration_days": 7,
                        "status": "active",
                    },
                    "proj-later": {
                        "id": "proj-later",
                        "title": "Catalog informants",
                        "kind": "research",
                        "location_id": "loc-docks",
                        "faction_id": "fac-guild",
                        "progress_days": 0,
                        "duration_days": 10,
                        "status": "planned",
                    },
                },
            },
        })

        board = out["board"]
        self.assertIn("Strategic board", board)
        self.assertIn("Harbor Watch", board)
        self.assertIn("Salt Market", board)
        self.assertIn("army", board)
        self.assertIn("strength 3", board)
        self.assertIn("Guild coup &lt;tonight&gt;", board)
        self.assertLess(board.index("Guild coup"), board.index("Quiet investigation"))
        self.assertLess(board.index("Raise barricades"), board.index("Catalog informants"))
        self.assertIn("contested", out["loc"])
        self.assertIn("assets 1", out["loc"])
        self.assertIn("urgent 2", out["loc"])
        self.assertIn("ctrl-contested", out["classes"])
        self.assertIn("has-strategy-urgent", out["classes"])

    def test_strategic_board_degrades_for_worlds_without_strategy_state(self):
        out = self._render({"locations": {}, "factions": {}})

        self.assertIn("No strategic board yet", out["board"])
        self.assertEqual(out["loc"], "")
        self.assertEqual(out["classes"], "")


if __name__ == "__main__":
    unittest.main()
