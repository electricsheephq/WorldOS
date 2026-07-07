#!/usr/bin/env python3
"""No-LLM resume tests for qa/run_duo.sh.

The test stubs claude/uv and the scorer, then drives the real bash runner so the
checkpoint, resume, throttle, and artifact-pruning paths are exercised without a
live model or real engine process.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DuoResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.bin_dir = self.tmp_path / "bin"
        self.bin_dir.mkdir()
        self.log_path = self.tmp_path / "stub-calls.jsonl"
        self.score_script = self.tmp_path / "score_stub.sh"
        self.assert_script = self.tmp_path / "assert_green.py"
        self._write_stubs()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_id(self) -> str:
        return f"duo-resume-test-{uuid.uuid4().hex[:10]}"

    def _cleanup_run(self, run_id: str) -> None:
        shutil.rmtree(ROOT / "qa" / "state" / run_id, ignore_errors=True)
        transcript_dir = ROOT / "qa" / "transcripts"
        for path in transcript_dir.glob(f"{run_id}*"):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                shutil.rmtree(path, ignore_errors=True)

    def _write_stubs(self) -> None:
        claude = self.bin_dir / "claude"
        claude.write_text(
            textwrap.dedent(
                r"""
                #!/usr/bin/env python3
                import json
                import os
                import sys
                from pathlib import Path

                argv = sys.argv[1:]

                def arg_after(flag, default=""):
                    try:
                        return argv[argv.index(flag) + 1]
                    except (ValueError, IndexError):
                        return default

                cfg_path = arg_after("--mcp-config")
                cfg = json.loads(Path(cfg_path).read_text()) if cfg_path else {}
                log = Path(os.environ["WORLDOS_DUO_STUB_LOG"])

                if arg_after("--output-format") == "json":
                    moves = Path(cfg["mcpServers"]["worldos-player"]["env"]["WORLDOS_PLAYER_MOVES"])
                    moves.parent.mkdir(parents=True, exist_ok=True)
                    count = 1
                    if moves.exists() and moves.read_text().strip():
                        count = len(moves.read_text().splitlines()) + 1
                    text = f"player move {count}"
                    with moves.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({"kind": "say", "text": text}) + "\n")
                    with log.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({"role": "player", "text": text}) + "\n")
                    print(json.dumps({"result": text}))
                    sys.exit(0)

                engine = cfg["mcpServers"]["worldos-engine"]
                state = Path(engine["env"]["WORLDOS_STATE_DIR"])
                state.mkdir(parents=True, exist_ok=True)
                campaigns = state / "campaigns"
                camp = campaigns / "camp_stub"
                camp.mkdir(parents=True, exist_ok=True)
                snap = camp / "snapshot.json"
                if not snap.exists():
                    snap.write_text(json.dumps({
                        "id": "camp_stub",
                        "title": "Stub Campaign",
                        "world_id": "baldurs-gate",
                        "day": 1,
                        "time_of_day": "morning",
                        "locations": {"loc_stub": {"id": "loc_stub", "name": "Stub Room", "visited": True}},
                        "current_location_id": "loc_stub",
                        "characters": {},
                        "party": [],
                        "combat": {"active": False}
                    }) + "\n", encoding="utf-8")

                prompt = arg_after("-p")
                phase = "dm"
                if "Begin the session" in prompt:
                    phase = "cold_open"
                elif "We are out of time" in prompt:
                    phase = "wrap"
                elif "The player does:" in prompt:
                    phase = "beat"

                beat = 0
                if phase == "beat":
                    import re
                    match = re.search(r"beat (\d+)", prompt)
                    if match:
                        beat = int(match.group(1))
                    else:
                        beat_file = state / ".stub_beat_count"
                        beat = int(beat_file.read_text()) + 1 if beat_file.exists() else 1
                        beat_file.write_text(str(beat), encoding="utf-8")

                throttle = int(os.environ.get("WORLDOS_DUO_STUB_THROTTLE_BEAT", "0") or "0")
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"role": "dm", "phase": phase, "beat": beat}) + "\n")
                if phase == "beat" and beat == throttle:
                    print(json.dumps({
                        "type": "result",
                        "is_error": True,
                        "api_error_status": 429,
                        "result": "HTTP 429 hit your session limit"
                    }))
                    sys.exit(0)

                if phase == "beat":
                    data = json.loads(snap.read_text())
                    data["day"] = int(data.get("day") or 1) + 1
                    snap.write_text(json.dumps(data) + "\n", encoding="utf-8")

                if phase == "cold_open":
                    result = "DM opened the stub scene."
                elif phase == "wrap":
                    result = "DM wrapped the stub session."
                elif phase == "beat":
                    result = f"DM beat {beat} resolved."
                else:
                    result = "DM response."
                print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result}))
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        claude.chmod(0o755)

        uv = self.bin_dir / "uv"
        uv.write_text(
            textwrap.dedent(
                r"""
                #!/usr/bin/env python3
                import os
                import shutil
                import sys
                from pathlib import Path

                sys.stdin.read()
                state = Path(os.environ.get("WORLDOS_STATE_DIR", ""))
                args = sys.argv[1:]
                tail = args[args.index("-") + 1:] if "-" in args else []
                if tail and tail[0] in {"save", "load"}:
                    action, campaign_id, slot = tail[:3]
                    camp = state / "campaigns" / campaign_id
                    snap = camp / "snapshot.json"
                    slot_path = camp / "slots" / f"{slot}.json"
                    slot_path.parent.mkdir(parents=True, exist_ok=True)
                    if action == "save":
                        shutil.copyfile(snap, slot_path)
                    else:
                        shutil.copyfile(slot_path, snap)
                    sys.exit(0)
                if len(tail) == 1:
                    snap = state / "campaigns" / "camp_stub" / "snapshot.json"
                    if snap.exists():
                        print("camp_stub")
                    sys.exit(0)
                sys.exit(0)
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        uv.chmod(0o755)

        self.score_script.write_text(
            "#!/usr/bin/env bash\nprintf '{\"overall\":4.0,\"scores\":{}}\\n' > \"$5\"\n",
            encoding="utf-8",
        )
        self.score_script.chmod(0o755)

        self.assert_script.write_text(
            "#!/usr/bin/env python3\nprint('[PASS] stub behavioral gate')\n",
            encoding="utf-8",
        )
        self.assert_script.chmod(0o755)

    def _env(self, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin_dir}:{env.get('PATH', '')}",
                "WORLDOS_DM_MODEL": "sonnet",
                "WORLDOS_ACTOR_MODEL": "sonnet",
                "WORLDOS_SCORE_SCRIPT": str(self.score_script),
                "WORLDOS_ASSERT_BEHAVIORAL_SCRIPT": str(self.assert_script),
                "WORLDOS_DUO_STUB_LOG": str(self.log_path),
                "WORLDOS_DM_MAX_ATTEMPTS": "1",
                "WORLDOS_LEAN_BEATS": "0",
                "WORLDOS_COLDOPEN_TIMEOUT": "5",
                "WORLDOS_BEAT_TIMEOUT": "5",
                "WORLDOS_SCORE_TIMEOUT": "5",
            }
        )
        env.update(overrides)
        return env

    def _run_duo(self, run_id: str, **env_overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "qa/run_duo.sh", run_id, "baldurs-gate", "qa/play_player_duo.txt", "3", "0.80"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=self._env(**env_overrides),
            timeout=30,
        )

    def _checkpoint(self, run_id: str) -> Path:
        return ROOT / "qa" / "state" / run_id / ".duo_checkpoint.json"

    def _chat_rows(self, run_id: str) -> list[dict]:
        path = ROOT / "qa" / "transcripts" / f"{run_id}.chat.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_one_window_run_removes_checkpoint_and_records_three_beats(self) -> None:
        run_id = self._run_id()
        self.addCleanup(self._cleanup_run, run_id)

        proc = self._run_duo(run_id)

        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertFalse(self._checkpoint(run_id).exists())
        dm_beat_rows = [r for r in self._chat_rows(run_id) if r["role"] == "dm" and r["text"].startswith("DM beat ")]
        self.assertEqual([r["text"] for r in dm_beat_rows], [
            "DM beat 1 resolved.",
            "DM beat 2 resolved.",
            "DM beat 3 resolved.",
        ])

    def test_throttled_run_resumes_from_last_completed_beat_without_double_logging(self) -> None:
        run_id = self._run_id()
        self.addCleanup(self._cleanup_run, run_id)

        first = self._run_duo(run_id, WORLDOS_DUO_STUB_THROTTLE_BEAT="3")
        self.assertEqual(first.returncode, 75, first.stderr + first.stdout)
        self.assertIn("throttled at beat 3", first.stderr + first.stdout)
        checkpoint = json.loads(self._checkpoint(run_id).read_text())
        self.assertEqual(checkpoint["last_completed_beat"], 2)

        second = self._run_duo(run_id)
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertFalse(self._checkpoint(run_id).exists())
        rows = self._chat_rows(run_id)
        dm_beat_rows = [r for r in rows if r["role"] == "dm" and r["text"].startswith("DM beat ")]
        player_rows = [r for r in rows if r["role"] == "player"]
        self.assertEqual([r["text"] for r in dm_beat_rows], [
            "DM beat 1 resolved.",
            "DM beat 2 resolved.",
            "DM beat 3 resolved.",
        ])
        self.assertEqual(len(player_rows), 4)  # intro + exactly three beat moves

    def test_sha_mismatch_refuses_resume_without_silent_restart(self) -> None:
        run_id = self._run_id()
        self.addCleanup(self._cleanup_run, run_id)
        state = ROOT / "qa" / "state" / run_id
        state.mkdir(parents=True, exist_ok=True)
        self._checkpoint(run_id).write_text(
            json.dumps(
                {
                    "last_completed_beat": 1,
                    "player_session_id": "player-old",
                    "dm_session_id": "dm-old",
                    "campaign_id": "camp_stub",
                    "world": "baldurs-gate",
                    "persona": "qa/play_player_duo.txt",
                    "total_beats": 3,
                    "budget": "0.80",
                    "sha": "oldsha",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        proc = self._run_duo(run_id)

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("checkpoint sha oldsha != current", proc.stderr + proc.stdout)
        self.assertFalse((ROOT / "qa" / "transcripts" / f"{run_id}.chat.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
