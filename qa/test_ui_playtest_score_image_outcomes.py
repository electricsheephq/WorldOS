"""ui_playtest_score.py expectation-classed image-404 split (audit F11-1b).

The viewer's /image responses carry an additive X-Image-Outcome header which the
capture harness records into network rows as `image_outcome`. The scorer must:
  - keep `image_404s` as the raw total (wire-compatible with every old consumer);
  - count `image_404s_designed` (no-art / placeholder = designed degradation);
  - claim a KNOWN `image_404s_unexpected` only when EVERY 404 row carries a class
    (a partial capture must not present unclassified 404s as proven-clean);
  - emit `image_404s_unexpected: null` when the split is unknown (legacy captures).
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "qa" / "ui_playtest_score.py"


class UiPlaytestScoreImageOutcomeTests(unittest.TestCase):
    def score_run(self, network_rows: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "run"
            player = run / "player"
            player.mkdir(parents=True)
            (run / "meta.json").write_text(
                json.dumps({"run": "run", "persona": "newbie", "world": "baldurs-gate"}),
                encoding="utf-8",
            )
            (player / "network.ndjson").write_text(
                "\n".join(json.dumps(row) for row in network_rows) + "\n",
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), str(run)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            return json.loads((run / "score.json").read_text(encoding="utf-8"))

    def test_fully_classified_rows_split_designed_from_unexpected(self):
        score = self.score_run(
            [
                {"url": "http://127.0.0.1/image?scope=a", "status": 404, "image_outcome": "no-art"},
                {"url": "http://127.0.0.1/image?scope=b", "status": 404, "image_outcome": "placeholder"},
                {"url": "http://127.0.0.1/image?scope=c", "status": 404, "image_outcome": "error"},
            ]
        )
        self.assertEqual(score["image_404s"], 3)
        self.assertEqual(score["image_404s_designed"], 2)
        self.assertEqual(score["image_404s_unexpected"], 1)

    def test_all_designed_rows_report_zero_unexpected(self):
        score = self.score_run(
            [
                {"url": "http://127.0.0.1/image?scope=a", "status": 404, "image_outcome": "no-art"},
                {"url": "http://127.0.0.1/image?scope=b", "status": 404, "image_outcome": "no-art"},
            ]
        )
        self.assertEqual(score["image_404s"], 2)
        self.assertEqual(score["image_404s_designed"], 2)
        self.assertEqual(score["image_404s_unexpected"], 0)

    def test_unclassified_rows_leave_unexpected_unknown(self):
        # One 404 row lacks the outcome class (older viewer / mixed capture): the raw
        # total stands, and the unexpected count must be null (unknown), NOT a guess.
        score = self.score_run(
            [
                {"url": "http://127.0.0.1/image?scope=a", "status": 404, "image_outcome": "no-art"},
                {"url": "http://127.0.0.1/image?scope=b", "status": 404},
            ]
        )
        self.assertEqual(score["image_404s"], 2)
        self.assertEqual(score["image_404s_designed"], 1)
        self.assertIsNone(score["image_404s_unexpected"])

    def test_non_image_failures_keep_existing_counters(self):
        score = self.score_run(
            [
                {"url": "http://127.0.0.1/session-surface", "status": 500},
                {"url": "http://127.0.0.1/image?scope=a", "status": 404, "image_outcome": "no-art"},
            ]
        )
        self.assertEqual(score["image_404s"], 1)
        self.assertEqual(score["network_failures"], 1)


if __name__ == "__main__":
    unittest.main()
