#!/usr/bin/env python3
"""Self-checks for the T3 NATIVE-window playtester palette (issue #1436 U2 / #1322).

These are GUI-FREE: they prove the tool CONTRACT, the runner's permission discipline, and that
qa/ui_playtest_score.py scores a native run UNCHANGED — WITHOUT launching WorldOSPlayer.app or
needing a TCC grant. The pieces that need a Mac toolchain (swiftc compile, the node MCP boot) are
skipped where absent, so this stays green in Linux CI and on the fast gate.

Run: python3 -m pytest -q qa/test_native_palette.py   (or: python3 qa/test_native_palette.py)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NPT = ROOT / "qa" / "native_palette"
SERVER = NPT / "native_palette_server.js"
SWIFT = NPT / "native_input.swift"
PERSONA = NPT / "play_player_native_t3.txt"
RUNNER = ROOT / "qa" / "ui_playtest_player.sh"

EXPECTED_TOOLS = {"screenshot", "a11y_tree", "click", "type", "key", "wait",
                  "report_bug", "give_up", "finish"}


def _node_modules_with_sdk() -> str | None:
    """A node_modules dir that has the MCP SDK (worktrees don't install deps — fall back to the
    canonical checkout's qa/playwright install)."""
    for base in (NPT / "node_modules",
                 ROOT / "qa" / "playwright" / "node_modules",
                 Path("/Users/lume/WorldOS/qa/playwright/node_modules")):
        if (base / "@modelcontextprotocol").is_dir():
            return str(base)
    return None


class NativePaletteContractTests(unittest.TestCase):
    def test_files_exist(self):
        for p in (SERVER, SWIFT, PERSONA, RUNNER):
            self.assertTrue(p.is_file(), f"missing {p}")
        self.assertTrue(os.access(RUNNER, os.X_OK), "runner must be executable")

    def test_server_registers_the_nine_tool_contract(self):
        src = SERVER.read_text(encoding="utf-8")
        registered = set(__import__("re").findall(r'registerTool\(\s*"([a-z0-9_]+)"', src))
        self.assertEqual(registered, EXPECTED_TOOLS,
                         f"native palette must expose exactly the 9-tool contract; got {sorted(registered)}")

    def test_a11y_is_a_pixels_only_stub(self):
        src = SERVER.read_text(encoding="utf-8")
        self.assertIn("pixels-only", src)
        self.assertIn("A11Y_STUB", src)

    def test_artifact_layout_matches_scorer(self):
        # The scorer reads bugs.ndjson (run root) + player/{actions,console,network}.ndjson + status.json.
        src = SERVER.read_text(encoding="utf-8")
        for token in ('"bugs.ndjson"', '"actions.ndjson"', '"status.json"',
                      '"console.ndjson"', '"network.ndjson"'):
            self.assertIn(token, src, f"server must write {token}")

    def test_persona_is_pixels_and_quest_loop(self):
        brief = PERSONA.read_text(encoding="utf-8")
        self.assertIn("click(x, y)", brief)
        self.assertIn("QUEST LOOP", brief)
        self.assertIn("finish(", brief)


class RunnerPermissionDisciplineTests(unittest.TestCase):
    def test_header_names_both_settings_panes(self):
        text = RUNNER.read_text(encoding="utf-8")
        self.assertIn("Screen Recording", text)
        self.assertIn("Accessibility", text)
        self.assertIn("Privacy & Security", text)

    def test_fails_loud_on_missing_permission(self):
        text = RUNNER.read_text(encoding="utf-8")
        # A missing grant must be a LOUD exit, never a silent skip.
        self.assertIn("FATAL: Screen Recording NOT granted", text)
        self.assertIn("FATAL: Accessibility NOT granted", text)
        self.assertIn("--preflight", text)

    def test_boots_the_seed_viewer_player_score_recipe(self):
        text = RUNNER.read_text(encoding="utf-8")
        for token in ("seed_gfx_combat.py", "viewer/server.py", "native_palette_server.js",
                      "WORLDOS_ENGINE_BASE_URL", "WORLDOS_CAMPAIGN_ID", "ui_playtest_score.py",
                      "WorldOSPlayer"):
            self.assertIn(token, text, f"runner must reference {token}")


class SwiftHelperTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("swiftc"), "swiftc not available (non-macOS CI)")
    def test_helper_compiles(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "native_input"
            r = subprocess.run(["swiftc", str(SWIFT), "-o", str(out)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"swiftc failed:\n{r.stderr}")
            self.assertTrue(out.is_file())
            # checkperms must emit the gate keys the palette reads.
            p = subprocess.run([str(out), "checkperms"], capture_output=True, text=True)
            data = json.loads(p.stdout.strip().splitlines()[-1])
            self.assertIn("screen_recording", data)
            self.assertIn("accessibility", data)
            # winfind on a bogus owner is a clean not-found (never a crash).
            w = subprocess.run([str(out), "winfind", "NoSuchApp_ZZZ"], capture_output=True, text=True)
            self.assertEqual(json.loads(w.stdout.strip().splitlines()[-1]).get("found"), False)


class ServerBootTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_selfcheck_boots_and_matches_contract(self):
        nm = _node_modules_with_sdk()
        if not nm:
            self.skipTest("MCP SDK not installed (cd qa/playwright && npm install)")
        with tempfile.TemporaryDirectory() as td:
            env = {**os.environ, "WORLDOS_NPT_RUNDIR": td, "WORLDOS_NPT_NODE_MODULES": nm}
            r = subprocess.run(["node", str(SERVER), "--selfcheck"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, f"selfcheck failed:\n{r.stdout}\n{r.stderr}")
            data = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertTrue(data["ok"])
            self.assertTrue(data["tool_contract_match"])
            self.assertEqual(set(data["tools"]), EXPECTED_TOOLS)


class ScorerCompatibilityTests(unittest.TestCase):
    """Prove qa/ui_playtest_score.py consumes the EXACT artifact shape the native palette writes,
    UNCHANGED — the load-bearing point of Unit 2."""

    def test_score_reads_native_palette_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            rundir = Path(td)
            (rundir / "player").mkdir()
            # bugs.ndjson — one player bug, exactly as writeBug() emits it.
            (rundir / "bugs.ndjson").write_text(json.dumps({
                "ts": "2026-07-09T00:00:00Z", "action_seq": 0, "persona": "t3-native",
                "screen": "player", "category": "ux", "severity": "minor", "title": "quiet NPC",
                "expected": "npc greets", "actual": "silence", "screenshot": "", "evidence": {},
                "tried_alternatives": [], "blocks_progress": False, "source": "player",
            }) + "\n", encoding="utf-8")
            # actions.ndjson — a submitted turn (type submit=true counts as an in-story turn).
            (rundir / "player" / "actions.ndjson").write_text(
                json.dumps({"ts": "2026-07-09T00:00:01Z", "seq": 1, "action": "screenshot", "screen": "player"}) + "\n" +
                json.dumps({"ts": "2026-07-09T00:00:02Z", "seq": 2, "action": "type",
                            "text": "attack the goblin", "submit": True, "ok": True, "screen": "player"}) + "\n",
                encoding="utf-8")
            (rundir / "player" / "console.ndjson").write_text("", encoding="utf-8")
            (rundir / "player" / "network.ndjson").write_text("", encoding="utf-8")
            # status.json — a finish() with a structured satisfaction (the T3 signal).
            (rundir / "player" / "status.json").write_text(json.dumps({
                "ended": True, "reason": "finish", "satisfaction": 7,
                "detail": "Completed the loop; readable.", "at": "2026-07-09T00:00:03Z"}), encoding="utf-8")
            (rundir / "meta.json").write_text(json.dumps({
                "run": "t3-unit", "persona": "t3-native", "world": "camp_gfxdemo01"}), encoding="utf-8")

            r = subprocess.run([sys.executable, str(ROOT / "qa" / "ui_playtest_score.py"), str(rundir), ""],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"scorer errored:\n{r.stderr}")
            score = json.loads((rundir / "score.json").read_text(encoding="utf-8"))
            self.assertEqual(score["persona"], "t3-native")
            self.assertEqual(score["in_story_turns"], 1)
            self.assertEqual(score["persona_satisfaction"], 7)
            self.assertEqual(score["satisfaction_source"], "self-reported")
            self.assertEqual(score["bug_reports_minor"], 1)
            self.assertEqual(score["console_errors"], 0)
            self.assertEqual(score["network_failures"], 0)
            self.assertTrue((rundir / "summary.md").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
