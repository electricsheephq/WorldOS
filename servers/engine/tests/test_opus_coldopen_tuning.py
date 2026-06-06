"""Guard: the Opus DM cold-open is tuned to finish in time WITH narration.

#682 made Opus the default DM. The Opus ``--effort max`` cold-open world-build is generation-bound and
overruns the cold-open timeout (measured on the VM 2026-06-06: ``max`` never finishes <400s, the turn is
killed before it writes narration, the backend grace-proceeds into an empty scene; ``--effort high``
finishes ~300s WITH a full BG-caliber opening — "the lamps of Sorcerous Sundries burn low and blue…").

So the cold-open effort + deadline + the QA narration-wait are MODEL-AWARE: Opus gets high / 500s /
a longer narration grace; Sonnet is unchanged (max / 400s / the historic grace).
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class OpusColdOpenTuningTests(unittest.TestCase):
    def _read(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_coldopen_effort_is_high_for_opus(self):
        src = self._read("qa/lib_beat_driver.sh")
        self.assertRegex(src, r"_co_default=max", "non-opus cold-open effort stays max")
        self.assertRegex(src, r"\*opus\*\)\s*_co_default=high", "Opus cold-open effort must default to high")
        self.assertIn(
            'worldos_env DM_EFFORT_COLDOPEN "$_co_default"', src,
            "the cold-open effort must consume the model-aware default",
        )

    def test_coldopen_timeout_has_opus_margin(self):
        src = self._read("qa/lib_beat_driver.sh")
        self.assertRegex(src, r"_co_timeout=400", "non-opus cold-open timeout stays 400s")
        self.assertRegex(src, r"\*opus\*\)\s*_co_timeout=5\d\d", "Opus cold-open timeout must have margin (>=500s)")
        self.assertIn(
            'worldos_env COLDOPEN_TIMEOUT "$_co_timeout"', src,
            "the cold-open timeout must consume the model-aware default",
        )

    def test_harness_narration_wait_longer_for_opus(self):
        src = self._read("qa/ui_playtest_app.sh")
        self.assertIn("WOS_APP_NARRATION_GRACE_POLLS", src, "narration grace must be env-configurable")
        self.assertRegex(
            src, r"\*opus\*\).*WOS_APP_NARRATION_GRACE_POLLS:-1\d\d",
            "Opus narration grace must default longer (>=100 polls, i.e. >300s)",
        )


if __name__ == "__main__":
    unittest.main()
