import json
import sys
import tempfile
import unittest
from pathlib import Path

QA_DIR = Path(__file__).resolve().parent
if str(QA_DIR) not in sys.path:
    sys.path.insert(0, str(QA_DIR))

import app_smoke_scripted as smoke


class AppSmokeScriptedTests(unittest.TestCase):
    def test_surface_url_preserves_campaign_from_app_status(self):
        status = {
            "live": {"campaign_id": "camp_123"},
            "endpoints": {"session_surface": "/session-surface"},
        }

        self.assertEqual(
            smoke.surface_url("http://127.0.0.1:8899/openworlds/", status),
            "http://127.0.0.1:8899/session-surface?campaign=camp_123",
        )

    def test_classify_status_returns_stable_failure_buckets(self):
        self.assertEqual(smoke.classify_status({"art": {"private_root_present": False}})[0], "no_art")
        self.assertEqual(
            smoke.classify_status({
                "art": {"private_root_present": True},
                "live": {"can_act": False},
            })[0],
            "no_provider",
        )
        self.assertEqual(
            smoke.classify_status({
                "art": {"private_root_present": True},
                "live": {"can_act": True, "actor": {}, "enabled_action_count": 5},
                "viewer": {"chat_lines": 1},
            })[0],
            "no_actor",
        )

    def test_provider_summary_synthesizes_trace_counts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            trace = root / "scripted-provider" / "trace.ndjson"
            trace.parent.mkdir()
            trace.write_text(
                "\n".join([
                    json.dumps({"event": "bootstrap"}),
                    json.dumps({"event": "move_resolved", "beat": 1}),
                    json.dumps({"event": "move_resolved", "beat": 2}),
                ]) + "\n",
                encoding="utf-8",
            )

            summary = smoke.provider_summary(root)

        self.assertEqual(summary["schema"], "worldos.scripted-provider-summary.v1")
        self.assertEqual(summary["move_resolved_count"], 2)
        self.assertEqual(summary["event_count"], 3)


if __name__ == "__main__":
    unittest.main()
