import sys
import tempfile
import unittest
from pathlib import Path

QA_DIR = Path(__file__).resolve().parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))

from app_failure_buckets import (
    APP_FAILURE_BUCKETS,
    classify_browser_probe,
    classify_native_failure,
    classify_part_b_failure_from_artifacts,
    classify_part_b_readiness_failure,
    classify_part_b_score_failure,
)


class AppFailureBucketTests(unittest.TestCase):
    def test_native_buckets_cover_stable_external_contract(self):
        self.assertEqual(
            APP_FAILURE_BUCKETS,
            (
                "no_app",
                "no_launcher",
                "no_provider",
                "no_art",
                "no_actor",
                "no_actions",
                "move_rejected",
                "no_narration",
                "console_error",
                "permission_prompt",
            ),
        )

    def test_native_build_and_launcher_failures(self):
        self.assertEqual(classify_native_failure(result="build_failed", can_act=False).bucket, "no_app")
        self.assertEqual(classify_native_failure(result="app_not_running", can_act=False).bucket, "no_app")
        self.assertEqual(classify_native_failure(result="no_launcher", can_act=False).bucket, "no_launcher")

    def test_native_status_payload_drives_specific_buckets(self):
        base = {
            "art": {"private_root_present": True},
            "viewer": {"chat_lines": 1},
            "live": {
                "actor": {"id": "hero", "name": "Hero"},
                "enabled_action_count": 5,
            },
        }
        missing_art = {**base, "art": {"private_root_present": False}}
        self.assertEqual(classify_native_failure(result="FAIL", can_act=True, app_status=missing_art).bucket, "no_art")

        self.assertEqual(classify_native_failure(result="FAIL", can_act=False, app_status=base).bucket, "no_provider")

        no_actor = {**base, "live": {"actor": {}, "enabled_action_count": 5}}
        self.assertEqual(classify_native_failure(result="FAIL", can_act=True, app_status=no_actor).bucket, "no_actor")

        no_actions = {**base, "live": {"actor": {"name": "Hero"}, "enabled_action_count": 0}}
        self.assertEqual(classify_native_failure(result="FAIL", can_act=True, app_status=no_actions).bucket, "no_actions")

        no_narration = {**base, "viewer": {"chat_lines": 0}}
        self.assertEqual(classify_native_failure(result="FAIL", can_act=True, app_status=no_narration).bucket, "no_narration")

    def test_app_status_readiness_bucket_takes_precedence(self):
        status = {
            "readiness": {
                "failure_bucket": "no_actions",
                "failure_detail": "readiness found no enabled actions",
            }
        }
        result = classify_native_failure(result="FAIL", can_act=False, app_status=status)
        self.assertEqual(result.bucket, "no_actions")
        self.assertIn("readiness", result.detail)

    def test_part_b_readiness_buckets(self):
        self.assertEqual(classify_part_b_readiness_failure(saw_canact=0, saw_pc=1, chat_lines=1).bucket, "no_provider")
        self.assertEqual(classify_part_b_readiness_failure(saw_canact=1, saw_pc=0, chat_lines=1).bucket, "no_actor")
        self.assertEqual(classify_part_b_readiness_failure(saw_canact=1, saw_pc=1, chat_lines=0).bucket, "no_narration")
        self.assertEqual(classify_part_b_readiness_failure(saw_canact=1, saw_pc=1, chat_lines=1).bucket, "no_actions")

    def test_part_b_artifact_buckets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "console.ndjson").write_text("pageerror: uncaught exception\n", encoding="utf-8")
            self.assertEqual(classify_part_b_failure_from_artifacts(root, "FAIL").bucket, "console_error")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "actions.ndjson").write_text("POST /move returned 500\n", encoding="utf-8")
            self.assertEqual(classify_part_b_failure_from_artifacts(root, "FAIL").bucket, "move_rejected")

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "summary.md").write_text("AXIsProcessTrusted false; screen recording permission missing\n", encoding="utf-8")
            self.assertEqual(classify_part_b_failure_from_artifacts(root, "FAIL").bucket, "permission_prompt")

    def test_part_b_score_failure_maps_to_stable_bucket_contract(self):
        with tempfile.TemporaryDirectory() as td:
            score = Path(td) / "score.json"
            score.write_text('{"pass": false, "completed_intro_flow": true, "reached_play_screen": true, "persona_satisfaction": 5}\n', encoding="utf-8")
            result = classify_part_b_score_failure(score)

        self.assertEqual(result.bucket, "no_provider")
        self.assertIn("satisfaction=5/10", result.detail)

    def test_visible_stale_browser_without_same_port_status_is_no_launcher(self):
        result = classify_browser_probe(
            tab_url="http://127.0.0.1:8899/openworlds/",
            status_url="http://127.0.0.1:8899/app-status",
            app_status_ok=False,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.bucket, "no_launcher")
        self.assertIn("same-port /app-status", result.detail)

        self.assertIsNone(classify_browser_probe(tab_url="http://127.0.0.1:8899/openworlds/", app_status_ok=True))


if __name__ == "__main__":
    unittest.main()
