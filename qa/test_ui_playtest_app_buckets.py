import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class UIPlaytestAppBucketFixtureTests(unittest.TestCase):
    def run_classifier(self, body: str) -> str:
        script = f"""
set -euo pipefail
funcs="$(mktemp)"
trap 'rm -f "$funcs"' EXIT
sed -e 's#^ROOT=.*#ROOT="$(pwd)"; cd "$ROOT" || exit 1#' -e '/^# DRIVE$/q' qa/ui_playtest_app.sh > "$funcs"
grep -qx 'ROOT="$(pwd)"; cd "$ROOT" || exit 1' "$funcs" || {{ echo "missing rewritten ROOT= sentinel" >&2; exit 1; }}
grep -qx '# DRIVE' "$funcs" || {{ echo "missing # DRIVE sentinel" >&2; exit 1; }}
grep -q '^classify_part_b_score_failure()' "$funcs" || {{ echo "missing classifier functions before # DRIVE" >&2; exit 1; }}
source "$funcs" >/dev/null
{body}
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def test_part_b_score_failure_gets_explicit_bucket(self):
        with tempfile.TemporaryDirectory() as td:
            score = Path(td) / "score.json"
            score.write_text(
                json.dumps(
                    {
                        "pass": False,
                        "completed_intro_flow": True,
                        "reached_play_screen": True,
                        "bug_reports_critical": 0,
                        "console_errors": 0,
                        "persona_satisfaction": 5,
                    }
                ),
                encoding="utf-8",
            )

            out = self.run_classifier(f'classify_part_b_score_failure "{score}"')

        self.assertEqual(out, "no_provider|score.json failed: satisfaction=5/10")

    def test_part_b_artifact_classifier_prefers_console_error(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            player = run / "player"
            player.mkdir()
            (player / "console.ndjson").write_text(
                json.dumps({"type": "pageerror", "text": "Uncaught ReferenceError"}) + "\n",
                encoding="utf-8",
            )

            out = self.run_classifier(f'classify_part_b_failure_from_artifacts "{run}" FAIL')

        self.assertEqual(out, "console_error|browser console/page error recorded during app playtest")


if __name__ == "__main__":
    unittest.main()
