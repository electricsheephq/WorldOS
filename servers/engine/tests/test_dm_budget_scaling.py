"""Guard: the per-DM-turn budget default scales to the DM model.

#682 flipped the default DM model to Opus. The Opus max-effort cold-open world-build costs ~5x a
Sonnet turn, so the old Sonnet-tuned $1.50 per-turn cap tripped `error_max_budget_usd` on the very
first (cold-open) turn and the faithful backend never seated a player character (observed on the VM
2026-06-06: failure_bucket=no_actor, spend $0). Both the production play scripts AND the .app QA
harness must therefore scale the per-turn cap to the model. These are CAPS, not spends — routine
beats spend far less; the session ceiling bounds any runaway turn.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class DmBudgetScalingTests(unittest.TestCase):
    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_production_play_scripts_scale_budget_to_model(self):
        for rel in ("scripts/play.sh", "scripts/play_party.sh"):
            src = self._read(rel)
            self.assertIn("*opus*)", src, f"{rel} must branch the budget default on an opus model")
            # an Opus per-turn default well above the Sonnet $1.50 cap (covers the cold-open)
            self.assertRegex(
                src, r"_PT_DEF=1[0-9]\.00",
                f"{rel} must set an Opus per-turn default >= $10",
            )
            self.assertIn(
                "CLAWDND_PLAY_BUDGET:-$_PT_DEF", src,
                f"{rel} must consume the model-aware per-turn default",
            )
            self.assertIn(
                "CLAWDND_PLAY_SESSION_BUDGET:-$_SESS_DEF", src,
                f"{rel} must consume the model-aware session default",
            )

    def test_app_harness_scales_per_turn_budget_to_model(self):
        src = self._read("qa/ui_playtest_app.sh")
        self.assertIn(
            'case "$DM_MODEL" in *opus*)', src,
            "ui_playtest_app.sh (claude lane) must scale the per-turn DM budget to the model",
        )
        self.assertRegex(
            src, r"CLAWDND_PLAY_BUDGET:=1[0-9]\.00",
            "ui_playtest_app.sh must set an Opus per-turn cap >= $10",
        )

    def test_no_unconditional_sonnet_cap_in_claude_lane(self):
        """The claude backend lane must not pin the per-turn cap to $1.50 unconditionally."""
        src = self._read("qa/ui_playtest_app.sh")
        # locate the Part B provider launch block and assert the model-aware
        # Claude branch precedes the export. Earlier helper case statements may
        # also contain a `claude)` label for metadata mapping.
        play_party_idx = src.find('exec "$ROOT/scripts/play_party.sh"')
        self.assertGreater(play_party_idx, -1, "Claude play_party launch not found")
        claude_idx = src.rfind("    claude)", 0, play_party_idx)
        self.assertGreater(claude_idx, -1, "claude backend lane not found")
        # within ~600 chars of the claude lane, the opus branch must appear before the export
        window = src[claude_idx:claude_idx + 700]
        self.assertIn("*opus*)", window, "claude lane must contain the model-aware budget branch")
        branch = window.find("*opus*)")
        export = window.find("export CLAWDND_PLAY_BUDGET")
        self.assertTrue(0 <= branch < export, "the opus branch must set the cap before it is exported")

    def test_run_duo_floors_opus_per_turn_budget(self):
        """run_duo must floor a low per-turn budget for an Opus DM so the cold-open survives.

        The sweep passes \\$2.00 and fast_probe \\$0.80 to run_duo; the Opus cold-open costs ~\\$2.4,
        so without a floor the duo cold-open trips error_max_budget_usd just like the .app backend did.
        """
        src = self._read("qa/run_duo.sh")
        self.assertIn("*opus*)", src, "run_duo must branch the per-turn budget on an opus model")
        self.assertRegex(src, r"BUDGET=4\.00", "run_duo must floor the Opus per-turn budget >= the cold-open cost")

    def test_combat_sprint_scales_budget_to_model(self):
        """run_combat_sprint runs the whole multi-round fight on ONE budget (no cold-open); the Opus
        combat needs >$1.50 (observed: error_max_budget_usd mid-Round 2 cut coverage and the mech score).
        """
        src = self._read("qa/run_combat_sprint.sh")
        self.assertIn("*opus*)", src, "run_combat_sprint must branch the combat budget on an opus model")
        self.assertRegex(src, r"CS_BUDGET=\"\$\{CLAWDND_PLAY_BUDGET:-5\.00\}\"", "Opus combat budget must be >= $5")
        self.assertIn('--max-budget-usd "$CS_BUDGET"', src, "must consume the model-aware combat budget")

    def test_auxiliary_harnesses_scale_budget_to_model(self):
        """ui_playtest.sh (per-DM-turn) + run_party.sh (per-call) must scale their Opus budgets too."""
        uipt = self._read("qa/ui_playtest.sh")
        self.assertIn("*opus*)", uipt, "ui_playtest must branch the DM budget on an opus model")
        self.assertRegex(uipt, r"_uipt_dm_def=12\.00", "ui_playtest Opus per-DM-turn default must be >= $12")
        party = self._read("qa/run_party.sh")
        self.assertIn("*opus*)", party, "run_party must branch/floor the per-call budget on an opus model")
        self.assertRegex(party, r"BUDGET=4\.00", "run_party must floor the Opus per-call budget")


if __name__ == "__main__":
    unittest.main()
