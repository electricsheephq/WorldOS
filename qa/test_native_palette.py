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
import time
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

    @unittest.skipUnless(shutil.which("swiftc"), "swiftc not available (non-macOS CI)")
    def test_winfind_reports_on_screen_and_has_all_spaces_fallback(self):
        """#1443: winFind must (a) always report an `on_screen` bool when found (so callers know
        whether `screencapture -l` will work from here), and (b) fall back to an all-Spaces search
        when the owner isn't in the on-screen-only pass — proven here by finding Finder (whose
        actual on-screen window content is environment-dependent, but the owner is ALWAYS present
        in one pass or the other on a running Mac, since it's always running)."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "native_input"
            subprocess.run(["swiftc", str(SWIFT), "-o", str(out)], check=True, capture_output=True)
            w = subprocess.run([str(out), "winfind", "Finder"], capture_output=True, text=True)
            data = json.loads(w.stdout.strip().splitlines()[-1])
            self.assertTrue(data.get("found"), f"Finder should always be findable: {data}")
            self.assertIn("on_screen", data, "winfind must report on_screen so callers can decide to activate")
            self.assertIsInstance(data["on_screen"], bool)

    def test_source_has_all_spaces_fallback(self):
        src = SWIFT.read_text(encoding="utf-8")
        self.assertIn("allSpacesOpts", src, "winFind must retry without .optionOnScreenOnly (#1443)")
        self.assertIn("on_screen", src, "winFind must emit on_screen so capture callers can pick the path")

    def test_source_has_screencapturekit_capture_subcommand(self):
        """#1456: the helper must expose a `capture` subcommand backed by ScreenCaptureKit
        (SCScreenshotManager), the no-activation cross-Space capture path."""
        src = SWIFT.read_text(encoding="utf-8")
        self.assertIn("import ScreenCaptureKit", src)
        self.assertIn("SCScreenshotManager", src, "capture must use SCScreenshotManager (no activation)")
        self.assertIn('case "capture":', src, "helper must dispatch a `capture` subcommand")

    def test_source_input_is_pid_targeted_with_flag_gated_activate_fallback(self):
        """#1466 (completes #1456): input delivery is the no-activation twin of capture. When an
        owner is supplied the helper must resolve its PID (kCGWindowOwnerPID) and deliver the event
        DIRECTLY to that process (CGEvent.postToPid) so an unfocused player still receives it — a
        plain global HID tap only reaches the ACTIVE app. The activate->post->restore path stays a
        FLAG (--activate-fallback) at the swift-helper level; #1483 flips the HARNESS callers to pass
        that flag BY DEFAULT (pid-only delivery produced zero Unity input) — see the harness test
        below. The helper itself never activates unless the flag is passed."""
        src = SWIFT.read_text(encoding="utf-8")
        self.assertIn("kCGWindowOwnerPID", src, "input must resolve the owner PID for direct delivery")
        self.assertIn("postToPid", src, "#1466: input must PID-target via CGEvent.postToPid")
        self.assertIn("--owner", src, "click/key/type must accept --owner for PID delivery")
        self.assertIn("--activate-fallback", src, "the activate fallback must be a flag")
        # The fallback must be gated on the flag in the helper, not run unconditionally.
        self.assertIn("activateFallback, let owner", src,
                      "activate fallback must be guarded by the flag + an owner")

    def test_harness_defaults_activate_fallback_on_1483(self):
        """#1483: pid-posted CGEvents deliver to the player PID but produce ZERO Unity input (Unity's
        Input samples only the FOREGROUND app), so the smoke lane was red on the pure-PID default since
        w6batch. The working path is the brief activate->click->restore escape — now ON by DEFAULT in
        BOTH the T3 palette server and the scripted smoke driver, with WORLDOS_CLICK_ACTIVATE_FALLBACK=0
        as the opt-out (pure PID delivery). The `!== "0"` default-on shape is the guarantee."""
        srv = SERVER.read_text(encoding="utf-8")
        drv = SMOKE_DRIVER.read_text(encoding="utf-8")
        for name, src in (("native_palette_server.js", srv), ("player_smoke_driver.js", drv)):
            self.assertIn('WORLDOS_CLICK_ACTIVATE_FALLBACK !== "0"', src,
                          f"{name}: activate-fallback must default ON (opt-out via =0), not opt-in (=1)")
            self.assertNotIn('WORLDOS_CLICK_ACTIVATE_FALLBACK === "1"', src,
                             f"{name}: the opt-IN (=1) default must be gone — it left the smoke lane red")

    @unittest.skipUnless(shutil.which("swiftc"), "swiftc not available (non-macOS CI)")
    def test_click_pid_delivery_selects_pid_for_running_owner_and_hid_otherwise(self):
        """#1466: end-to-end delivery SELECTION. A `--owner` that resolves to a running app (Finder,
        always present) reports delivery:"pid"; a bogus owner has no window/PID so it gracefully
        falls back to delivery:"hid" (never a crash). Coordinates are a harmless top-left corner."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "native_input"
            subprocess.run(["swiftc", str(SWIFT), "-o", str(out)], check=True, capture_output=True)
            # bogus owner -> no PID -> HID fallback, still ok:true
            b = subprocess.run([str(out), "click", "2", "2", "--owner", "NoSuchApp_ZZZ"],
                               capture_output=True, text=True)
            bd = json.loads(b.stdout.strip().splitlines()[-1])
            self.assertTrue(bd.get("ok"), f"bogus-owner click must not fail: {bd}")
            self.assertEqual(bd.get("delivery"), "hid", f"unresolved owner must fall back to HID: {bd}")
            # running owner -> PID resolves -> direct postToPid delivery
            f = subprocess.run([str(out), "click", "2", "2", "--owner", "Finder"],
                               capture_output=True, text=True)
            fd = json.loads(f.stdout.strip().splitlines()[-1])
            self.assertTrue(fd.get("ok"), f"Finder click must not fail: {fd}")
            self.assertEqual(fd.get("delivery"), "pid",
                             f"a running owner must PID-target (Finder is always running): {fd}")
            # legacy path (no owner) stays HID — byte-compatible with pre-#1466 callers
            n = subprocess.run([str(out), "click", "2", "2"], capture_output=True, text=True)
            self.assertEqual(json.loads(n.stdout.strip().splitlines()[-1]).get("delivery"), "hid")

    @unittest.skipUnless(shutil.which("swiftc"), "swiftc not available (non-macOS CI)")
    def test_capture_subcommand_images_a_window_without_activation(self):
        """Prove the SCK `capture` path actually images an EXISTING window (Finder is always
        running) to a real PNG — WITHOUT activating anything. Skipped where Screen Recording is not
        granted (SCK returns ok:false), since the grant is an owner action, not a test outcome."""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "native_input"
            subprocess.run(["swiftc", str(SWIFT), "-o", str(out)], check=True, capture_output=True)
            png = Path(td) / "cap.png"
            r = subprocess.run([str(out), "capture", "Finder", str(png)], capture_output=True, text=True)
            data = json.loads(r.stdout.strip().splitlines()[-1])
            if data.get("ok") is not True:
                self.skipTest(f"SCK capture unavailable (grant/OS): {data}")
            self.assertTrue(png.is_file() and png.stat().st_size > 0, "capture must write a non-empty PNG")
            for k in ("px_w", "px_h", "scale", "window_id"):
                self.assertIn(k, data, f"capture JSON must report {k}")


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


CORE = NPT / "native_palette_core.js"
LIB_BOOT = ROOT / "qa" / "lib_native_player_boot.sh"
SMOKE_RUNNER = ROOT / "qa" / "player_smoke.sh"
SMOKE_DRIVER = NPT / "player_smoke_driver.js"
SEED_SMOKE = ROOT / "qa" / "seed_gfx_camp_smoke.py"


class CoreModuleTests(unittest.TestCase):
    """native_palette_core.js (#1443) — the capture/activate/click primitives shared by
    native_palette_server.js (the T3 MCP tool) and player_smoke_driver.js (the scripted smoke)."""

    def test_core_module_exists_and_is_required_by_the_server(self):
        self.assertTrue(CORE.is_file())
        src = SERVER.read_text(encoding="utf-8")
        self.assertIn('require("./native_palette_core.js")', src,
                      "native_palette_server.js must share the #1443 capture fix, not fork it")

    def test_core_module_exports_the_capture_primitives(self):
        src = CORE.read_text(encoding="utf-8")
        for name in ("resolveHelper", "findWindow", "captureSCK",
                     "captureWindow", "clickAt", "fullscreenCropWindow"):
            self.assertIn(name, src, f"native_palette_core.js must export {name}")

    def test_capture_window_is_sck_primary_and_never_activates(self):
        """#1456: capture goes through ScreenCaptureKit (any Space, no activation) as the PRIMARY
        path, with screencapture -l / fullscreen-crop only as fallbacks — and the activate-before-
        capture behavior is GONE, so QA never steals the user's focus or switches Spaces."""
        src = CORE.read_text(encoding="utf-8")
        self.assertIn("captureSCK(helperCmd, owner, outFile)", src, "#1456: SCK must be the primary capture")
        self.assertIn("fullscreenCropWindow", src, "must still fall back to a cropped full-screen grab")
        # No activation anywhere in the module — the whole point of #1456.
        self.assertNotIn("activateOwner", src, "#1456: activate-before-capture must be removed (no focus theft)")
        self.assertNotIn("waitForOnScreen", src, "#1456: cross-Space activation polling must be removed")
        self.assertNotIn("to activate", src, "#1456: the module must not osascript-activate the owner")

    def test_clickAt_forwards_owner_for_pid_delivery_and_bypasses_cliclick(self):
        """#1466: clickAt must forward `--owner` to the swift helper so the click is PID-delivered to
        the unfocused player, and it must NOT use cliclick when an owner is set (cliclick can only
        post global HID taps, which a no-activation window never receives)."""
        src = CORE.read_text(encoding="utf-8")
        self.assertIn("function clickAt(helperCmd, useCliclick, gx, gy, doubleClick, owner", src,
                      "clickAt must accept an owner (and activateFallback) for PID delivery")
        self.assertIn("useCliclick && !owner", src,
                      "clickAt must skip cliclick when an owner is set (cliclick cannot PID-target)")
        self.assertIn('args.push("--owner", String(owner))', src,
                      "clickAt must forward --owner to the helper")

    @unittest.skipUnless(shutil.which("swiftc") and shutil.which("node"), "swiftc/node not available (non-macOS CI)")
    def test_stale_compiled_binary_is_rebuilt_on_source_edit(self):
        """The T3 finding: an in-run source patch to native_input.swift didn't take effect until the
        server restarted, because the compiled binary was reused whenever it already existed. This
        proves the FIX (mtime staleness check) actually recompiles — not just that the code exists."""
        with tempfile.TemporaryDirectory() as td:
            work = Path(td) / "work"; work.mkdir()
            src_copy = Path(td) / "native_input.swift"
            src_copy.write_text(SWIFT.read_text(encoding="utf-8"), encoding="utf-8")

            def resolve():
                script = (
                    f'const core = require({json.dumps(str(CORE))});\n'
                    f'const h = core.resolveHelper({json.dumps(str(work))}, "", {json.dumps(str(src_copy))});\n'
                    f'console.log(JSON.stringify(h));\n'
                )
                r = subprocess.run(["node", "-e", script], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"resolveHelper failed:\n{r.stderr}")
                return json.loads(r.stdout.strip().splitlines()[-1])

            h1 = resolve()
            mtime1 = os.stat(h1["bin"]).st_mtime
            # No source change -> the binary must NOT be recompiled (mtime unchanged).
            h2 = resolve()
            self.assertEqual(os.stat(h2["bin"]).st_mtime, mtime1, "resolveHelper recompiled an up-to-date binary")
            # Edit the source and push its mtime forward -> MUST recompile.
            with src_copy.open("a", encoding="utf-8") as f:
                f.write("\n// touched for test\n")
            future = time.time() + 5
            os.utime(src_copy, (future, future))
            h3 = resolve()
            self.assertGreater(os.stat(h3["bin"]).st_mtime, mtime1,
                                "a stale compiled binary was NOT rebuilt after the source changed (#1443 T3 bug)")


class NoHijackLaunchTests(unittest.TestCase):
    """#1456: player QA must not hijack the owner's session — the shared boot lib defines an
    owner-active guard (HIDIdleTime) + a WINDOWED launch, and BOTH runners wire them in while never
    re-activating the player or switching Spaces (the deleted #1443 activate-before-launch pin)."""

    def test_shared_lib_defines_owner_guard_and_windowed_launch(self):
        self.assertTrue(LIB_BOOT.is_file())
        src = LIB_BOOT.read_text(encoding="utf-8")
        self.assertIn("owner_active_guard()", src)
        self.assertIn("HIDIdleTime", src, "guard must read console-user idle from ioreg HIDIdleTime")
        self.assertIn("FORCE_PLAYER_QA", src, "guard must honor a FORCE_PLAYER_QA=1 override")
        self.assertIn("SMOKE-DEFERRED (owner active)", src)
        self.assertIn("player_windowed_launch_args()", src)
        self.assertIn("-screen-fullscreen", src, "windowed launch must force -screen-fullscreen 0")
        # The old activate-before-launch Space pin is GONE (never re-activate / switch Spaces).
        self.assertNotIn("activate_current_space_context", src)

    def _assert_runner_no_hijack(self, text):
        self.assertIn("lib_native_player_boot.sh", text)
        self.assertIn("owner_active_guard", text, "runner must gate on the owner-active guard")
        self.assertIn("player_windowed_launch_args", text, "runner must launch WINDOWED")
        self.assertNotIn("activate_current_space_context", text, "#1456: no re-activation / Space switch")
        # force-recompile discipline: the runner deletes a stale compiled binary before launch.
        self.assertIn('rm -f "$PLAYERDIR/native_input"', text)

    def test_ui_playtest_player_is_no_hijack(self):
        self._assert_runner_no_hijack((ROOT / "qa" / "ui_playtest_player.sh").read_text(encoding="utf-8"))

    def test_player_smoke_is_no_hijack(self):
        self._assert_runner_no_hijack(SMOKE_RUNNER.read_text(encoding="utf-8"))


class PlayerSmokeTests(unittest.TestCase):
    """qa/player_smoke.sh (#1443) — the deterministic, headless-of-agents post-build check."""

    def test_files_exist_and_are_executable(self):
        for p in (SMOKE_RUNNER, SMOKE_DRIVER, SEED_SMOKE):
            self.assertTrue(p.is_file(), f"missing {p}")
        self.assertTrue(os.access(SMOKE_RUNNER, os.X_OK), "player_smoke.sh must be executable")

    def test_seed_smoke_is_sandboxed_and_forces_hits_deterministically(self):
        src = SEED_SMOKE.read_text(encoding="utf-8")
        self.assertIn("is_sandbox=True", src)
        self.assertIn("force_hit = True", src)
        self.assertIn("_author_camp_grid", src, "must reuse seed_gfx_camp.py's grid, not fork it")

    def test_smoke_drives_the_shared_core_primitives(self):
        src = SMOKE_DRIVER.read_text(encoding="utf-8")
        self.assertIn('require("./native_palette_core.js")', src)
        for name in ("captureWindow", "clickAt"):
            self.assertIn(name, src)

    def test_smoke_asserts_cell_change_hp_drop_and_motion_liveness(self):
        text = SMOKE_RUNNER.read_text(encoding="utf-8")
        self.assertIn("hero cell did not change", text)
        self.assertIn("goblin HP did not drop", text)
        self.assertIn("motion-liveness failed", text)

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_driver_reports_glide_frame_distinctness_fields(self):
        src = SMOKE_DRIVER.read_text(encoding="utf-8")
        self.assertIn("glide_move_distinct", src)
        self.assertIn("glide_attack_distinct", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
