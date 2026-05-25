import json
import shutil
import subprocess
import unittest
from pathlib import Path


_DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "dashboard.html"


def _planner_source() -> str:
    html = _DASHBOARD_PATH.read_text(encoding="utf-8")
    start = html.index("const esc =")
    helper_start = html.index("function buildPlannerHTML", start)
    end = html.index("function renderProgression", helper_start)
    return html[start:helper_start] + html[helper_start:end]


@unittest.skipIf(shutil.which("node") is None, "node is required for dashboard renderer fixture tests")
class ProgressionPlannerDashboardTests(unittest.TestCase):
    NODE_BIN = shutil.which("node")

    def _render(self, planner_response):
        program = (
            _planner_source()
            + "\nconst response = "
            + json.dumps(planner_response)
            + ";\nconsole.log(buildPlannerHTML(response));\n"
        )
        proc = subprocess.run(
            [self.NODE_BIN],
            input=program,
            text=True,
            capture_output=True,
            check=True,
        )
        return proc.stdout

    def test_build_planner_html_renders_engine_options_and_blockers(self):
        html = self._render({
            "ok": True,
            "source": "engine.build_options",
            "planner": {
                "choices": {"asi_required": True, "feat_allowed": False, "multiclass_allowed": False},
                "options": [
                    {
                        "class_name": "fighter",
                        "legal": True,
                        "to": {"level": 4, "class": "fighter"},
                        "hp_gain": 7,
                        "features_gained": [{"name": "Ability Score Improvement"}],
                        "spell_slots_delta": {"2": {"from_max": 0, "to_max": 2, "delta": 2}},
                        "resources_delta": {"ki": {"from_max": 0, "to_max": 2, "delta": 2, "recharge": "short"}},
                        "choices": {"asi_required": True, "feat_allowed": False},
                        "errors": [],
                    }
                ],
                "blocked_options": [
                    {
                        "class_name": "wizard",
                        "to": {"level": 4, "class": "wizard"},
                        "errors": ["multiclassing is disabled by campaign house rules"],
                    }
                ],
            },
        })

        self.assertIn("Engine build planner", html)
        self.assertIn("Fighter", html)
        self.assertIn("+7 HP", html)
        self.assertIn("Ability Score Improvement", html)
        self.assertIn("slot L2 +2", html)
        self.assertIn("Ki +2", html)
        self.assertIn("ASI required", html)
        self.assertIn("Feat blocked", html)
        self.assertIn("Wizard", html)
        self.assertIn("multiclassing is disabled", html)
        self.assertNotIn("level_up", html)

    def test_build_planner_html_renders_explicit_degraded_errors(self):
        html = self._render({
            "ok": False,
            "code": "engine_unavailable",
            "errors": ["engine build planner unavailable: missing dependency"],
        })

        self.assertIn("engine_unavailable", html)
        self.assertIn("missing dependency", html)


if __name__ == "__main__":
    unittest.main()
