"""Guard: the lean re-ground directive + the duo cold-open carry OUTPUT DISCIPLINE (anti-leak/summary).

Opus lean routine beats intermittently shortcut into bookkeeping (VM 2026-06-06, run vm2-opushi-narr):
a planning-leak preamble ("I'll play that as a held beat...") and a 3rd-person stage-direction summary
("The scene is resolved and persisted...") instead of narration. The SKILL.md has the anti-scaffolding
rules, but the lean re-ground directive only re-grounded STATE and never restated the OUTPUT discipline,
so the fresh lean session narrated loosely. The duo cold-open prompt separately induced a process-
narration leak ("State is grounded... on the dashboard. Closing my turn..."). Both now carry an explicit
discipline clause. Validated: lean beats clean; the duo cold-open opens in-fiction.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class OutputDisciplineTests(unittest.TestCase):
    def test_lean_reground_directive_has_output_discipline(self):
        src = (ROOT / "qa" / "lib_beat_driver.sh").read_text(encoding="utf-8")
        self.assertIn("OUTPUT DISCIPLINE", src, "the lean re-ground directive must restate output discipline")
        self.assertIn("3rd-person scene SUMMARY", src, "must name the summary-not-narration failure mode")
        self.assertIn("planning/intention note", src, "must name the planning-leak failure mode")

    def test_duo_coldopen_has_output_discipline(self):
        src = (ROOT / "qa" / "run_duo.sh").read_text(encoding="utf-8")
        self.assertIn("OUTPUT DISCIPLINE", src, "the duo cold-open must restate output discipline")
        self.assertIn("NEVER narrate your own setup", src, "must forbid the setup-process narration leak")


if __name__ == "__main__":
    unittest.main()
