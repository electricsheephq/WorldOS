import json
import tempfile
import unittest
from pathlib import Path

import qa.support_vm_preflight as preflight


class FakeRunner:
    def __init__(
        self,
        repo: Path,
        *,
        head: str = "deadbeefcafebabe1234567890abcdef12345678",
        status: str = "",
        codex_auth_output: str = "Authenticated as codex-test",
        chromium_path: Path | None = None,
        create_chromium: bool = True,
        remote_query_ok: bool = True,
        remote_query_head: str | None = None,
        remote_query_stderr: str = "could not read Username for 'https://github.com'",
    ):
        self.repo = repo
        self.head = head
        self.status = status
        self.codex_auth_output = codex_auth_output
        self.chromium_path = chromium_path or repo / ".fake-chromium"
        self.remote_query_ok = remote_query_ok
        self.remote_query_head = remote_query_head or head
        self.remote_query_stderr = remote_query_stderr
        if create_chromium:
            self.chromium_path.parent.mkdir(parents=True, exist_ok=True)
            self.chromium_path.write_text("fake browser", encoding="utf-8")

    def __call__(self, cmd, cwd=None, timeout=8):
        base = Path(cmd[0]).name
        args = tuple(cmd[1:])
        if base == "git":
            if args == ("rev-parse", "--show-toplevel"):
                return ok(str(self.repo))
            if args == ("rev-parse", "--abbrev-ref", "HEAD"):
                return ok("main")
            if args == ("rev-parse", "HEAD"):
                return ok(self.head)
            if args == ("rev-parse", "--short", "HEAD"):
                return ok(self.head[:7])
            if args == ("rev-parse", "origin/main"):
                return ok(self.head)
            if args == ("status", "--short"):
                return ok(self.status)
            if args == ("remote", "get-url", "origin"):
                return ok("https://github.com/electricsheephq/WorldOS.git")
            if args == ("ls-remote", "origin", "refs/heads/main"):
                if self.remote_query_ok:
                    return ok(f"{self.remote_query_head}\trefs/heads/main")
                return fail(self.remote_query_stderr)
            if args == ("--version",):
                return ok("git version 2.50.0")
        if args == ("--version",):
            versions = {
                "python3": "Python 3.13.0",
                "uv": "uv 0.11.17",
                "node": "v22.22.1",
                "npm": "10.9.4",
                "npx": "10.9.4",
                "codex": "codex-cli 0.120.0",
                "claude": "claude 0.0.0-test",
                "jq": "jq-1.7",
                "curl": "curl 8.0.0",
                "lsof": "lsof test",
                "timeout": "timeout test",
                "pkill": "pkill test",
                "pgrep": "pgrep test",
                "ps": "ps test",
            }
            return ok(versions.get(base, f"{base} test-version"))
        if base == "node" and args[:1] == ("-e",) and "chromium.executablePath" in args[1]:
            return ok(str(self.chromium_path))
        if base == "node" and args[:1] == ("-e",):
            return ok("/fake/node_modules/playwright/index.js")
        if base == "codex" and args == ("auth", "status"):
            return ok(self.codex_auth_output)
        return fail("unexpected command")


def ok(stdout=""):
    return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": "", "timed_out": False}


def fail(stderr="failed"):
    return {"ok": False, "exit_code": 1, "stdout": "", "stderr": stderr, "timed_out": False}


def fake_which(name: str) -> str | None:
    return f"/fake/{name}"


def make_art_root(tmp: Path) -> Path:
    images = tmp / "content" / "worlds" / "_private" / "baldurs-gate" / "images"
    for scope in ("portrait_alfira", "scene_camp"):
        scope_dir = images / scope
        scope_dir.mkdir(parents=True, exist_ok=True)
        (scope_dir / "image.png").write_text("fake", encoding="utf-8")
    return tmp


def make_config(tmp: Path, *, expected_sha: str = "deadbee") -> preflight.PreflightConfig:
    repo = tmp / "WorldOS"
    repo.mkdir()
    for rel in (
        "qa/ui_playtest_app.sh",
        "qa/run_duo.sh",
        "qa/assert_behavioral.py",
        "qa/ui_audit_health.sh",
        "qa/release_readiness.py",
        "qa/play_player_duo.txt",
        "qa/playwright/palette_server.js",
        "scripts/play.sh",
        "scripts/play_party.sh",
        "qa/play_player_browser_newbie.txt",
        "qa/play_player_browser_veteran.txt",
        "qa/play_player_browser_adversarial.txt",
        "qa/play_player_browser_narrative.txt",
        "qa/play_player_browser_optimizer.txt",
    ):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test", encoding="utf-8")
    art_root = make_art_root(tmp / "art")
    return preflight.PreflightConfig(
        repo=repo,
        expected_sha=expected_sha,
        artifact_dir=tmp / "artifacts",
        artifact_return_target="/Volumes/LEXAR/Codex/worldos-support-vm-rri/test",
        art_root=art_root,
        private_art_mode="required",
        personas=list(preflight.CANONICAL_PERSONAS),
        budget="12.00",
        concurrency=1,
        port=8785,
    )


class SupportVMPreflightTests(unittest.TestCase):
    def test_redaction_scrubs_common_secret_forms(self):
        raw = (
            "OPENAI_API_KEY=sk-proj-abc123456789 "
            "ANTHROPIC_API_KEY=anthropic-secret-value "
            "Bearer live-token-123456789"
        )
        redacted = preflight.redact(raw)
        self.assertNotIn("abc123456789", redacted)
        self.assertNotIn("anthropic-secret-value", redacted)
        self.assertNotIn("live-token-123456789", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_build_sha_matches_requires_seven_characters(self):
        self.assertFalse(preflight.build_sha_matches("deadbe", "deadbeef"))
        self.assertFalse(preflight.build_sha_matches("deadbeef", "deadbe"))
        self.assertTrue(preflight.build_sha_matches("deadbeefcafebabe", "deadbee"))
        self.assertTrue(preflight.build_sha_matches("deadbee", "deadbeefcafebabe"))

    def test_private_art_required_blocks_and_optional_warns(self):
        with tempfile.TemporaryDirectory() as td:
            missing_root = Path(td) / "missing-art"
            info, blockers, warnings = preflight.inspect_private_art(missing_root, "required")
            self.assertFalse(info["private_root_present"])
            self.assertTrue(blockers)
            self.assertFalse(warnings)

            info, blockers, warnings = preflight.inspect_private_art(missing_root, "optional")
            self.assertFalse(info["private_root_present"])
            self.assertFalse(blockers)
            self.assertTrue(warnings)

            art_root = make_art_root(Path(td) / "art")
            info, blockers, warnings = preflight.inspect_private_art(art_root, "required")
            self.assertTrue(info["private_root_present"])
            self.assertGreaterEqual(info["image_png_count"], 2)
            self.assertFalse(blockers)

    def test_cli_defaults_require_private_art(self):
        args = preflight.parse_args([])
        self.assertEqual(args.private_art_mode, "required")

    def test_env_snapshot_redacts_secret_values_but_keeps_safe_paths(self):
        snapshot = preflight.env_snapshot(
            {
                "OPENAI_API_KEY": "sk-proj-supersecret123456",
                "ANTHROPIC_API_KEY": "anthropic-supersecret",
                "CODEX_TOKEN": "codex-token-secret",
                "WORLDOS_ART_REPO_ROOT": "/Users/lume/ClawDnD-val",
                "UNRELATED_SECRET": "not-inspected",
            }
        )
        blob = json.dumps(snapshot)
        self.assertNotIn("supersecret123456", blob)
        self.assertNotIn("anthropic-supersecret", blob)
        self.assertNotIn("codex-token-secret", blob)
        self.assertNotIn("not-inspected", blob)
        self.assertEqual(snapshot["WORLDOS_ART_REPO_ROOT"]["value"], "/Users/lume/ClawDnD-val")

    def test_teardown_commands_are_record_only(self):
        with tempfile.TemporaryDirectory() as td:
            commands = preflight.teardown_commands(Path(td) / "worldos", 8785, "deadbee")
            self.assertTrue(commands)
            self.assertTrue(any("8785" in command for command in commands))
            self.assertFalse(any(command == "pkill -f 'viewer/server.py' || true" for command in commands))
            config = make_config(Path(td))
            report = preflight.build_report(config, runner=FakeRunner(config.repo), which=fake_which, env={})
            self.assertFalse(report["teardown"]["executed"])

    def test_report_contains_required_sections_with_canonical_personas(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            report = preflight.build_report(
                config,
                runner=FakeRunner(config.repo),
                which=fake_which,
                env={"WORLDOS_ART_REPO_ROOT": str(config.art_root)},
            )
            self.assertTrue(report["ready_for_rri"])
            self.assertFalse(report["release_verdict"])
            for section in (
                "host",
                "repo",
                "tools",
                "repo_files",
                "private_art",
                "environment",
                "rri_plan",
                "artifact_return",
                "teardown",
            ):
                self.assertIn(section, report)
            self.assertEqual(report["rri_plan"]["expected_personas"], preflight.CANONICAL_PERSONAS)
            self.assertEqual(report["blockers"], [])

    def test_report_flags_dirty_repo_and_expected_sha_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td), expected_sha="1234567")
            report = preflight.build_report(
                config,
                runner=FakeRunner(config.repo, head="deadbeefcafebabe", status=" M viewer/app.js"),
                which=fake_which,
                env={},
            )
            self.assertFalse(report["ready_for_rri"])
            blocker_text = "\n".join(report["blockers"])
            self.assertIn("dirty", blocker_text)
            self.assertIn("does not match expected SHA", blocker_text)

    def test_origin_main_query_failure_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            report = preflight.build_report(
                config,
                runner=FakeRunner(
                    config.repo,
                    remote_query_ok=False,
                    remote_query_stderr="fatal: could not read Username for 'https://token@example.com'",
                ),
                which=fake_which,
                env={},
            )
            self.assertFalse(report["ready_for_rri"])
            self.assertFalse(report["repo"]["origin_main_query"]["ok"])
            self.assertIn("repo origin/main is not queryable", "\n".join(report["blockers"]))
            self.assertIn("[REDACTED]@", report["repo"]["origin_main_query"]["error_redacted"])
            self.assertNotIn("token@example.com", json.dumps(report))

    def test_origin_main_query_records_remote_head(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            remote_head = "1111111222222233333334444444555555566666"
            report = preflight.build_report(
                config,
                runner=FakeRunner(config.repo, remote_query_head=remote_head),
                which=fake_which,
                env={},
            )
            self.assertTrue(report["ready_for_rri"])
            self.assertEqual(report["repo"]["origin_main_query"]["head"], remote_head)
            self.assertIn("queried origin/main", "\n".join(report["warnings"]).lower())

    def test_missing_expected_sha_blocks_rri_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td), expected_sha="")
            report = preflight.build_report(config, runner=FakeRunner(config.repo), which=fake_which, env={})
            self.assertFalse(report["ready_for_rri"])
            self.assertIn("expected SHA is required", "\n".join(report["blockers"]))

    def test_optional_private_art_mode_blocks_release_rri_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            config.private_art_mode = "optional"
            report = preflight.build_report(config, runner=FakeRunner(config.repo), which=fake_which, env={})
            self.assertFalse(report["ready_for_rri"])
            self.assertIn("private art mode must be 'required'", "\n".join(report["blockers"]))

    def test_vm_plan_does_not_suggest_mac_release_gate_command(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            report = preflight.build_report(config, runner=FakeRunner(config.repo), which=fake_which, env={})
            plan_blob = json.dumps(report["rri_plan"])
            self.assertIn("qa/ui_playtest_app.sh", plan_blob)
            self.assertIn("WOS_APP_PART=B", plan_blob)
            self.assertNotIn("qa/release_gate.sh --personas", plan_blob)
            self.assertTrue(report["rri_plan"]["mac_handoff_required"])

    def test_missing_persona_lane_tool_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))

            def which_without_claude(name: str) -> str | None:
                return None if name == "claude" else f"/fake/{name}"

            report = preflight.build_report(config, runner=FakeRunner(config.repo), which=which_without_claude, env={})
            self.assertFalse(report["ready_for_rri"])
            self.assertIn("required VM tool missing: claude", report["blockers"])

    def test_missing_playwright_chromium_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            missing_browser = Path(td) / "missing-chromium"
            report = preflight.build_report(
                config,
                runner=FakeRunner(config.repo, chromium_path=missing_browser, create_chromium=False),
                which=fake_which,
                env={},
            )
            self.assertFalse(report["ready_for_rri"])
            self.assertIn("Playwright Chromium executable is not installed", "\n".join(report["blockers"]))

    def test_missing_persona_brief_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            (config.repo / "qa" / "play_player_browser_optimizer.txt").unlink()
            report = preflight.build_report(config, runner=FakeRunner(config.repo), which=fake_which, env={})
            self.assertFalse(report["ready_for_rri"])
            self.assertIn("qa/play_player_browser_optimizer.txt", "\n".join(report["blockers"]))

    def test_codex_not_authenticated_output_does_not_false_green(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            report = preflight.build_report(
                config,
                runner=FakeRunner(config.repo, codex_auth_output="Not authenticated as user@example.com"),
                which=fake_which,
                env={},
            )
            self.assertFalse(report["ready_for_rri"])
            self.assertEqual(report["tools"]["codex_auth"]["auth_status"], "not_proven")
            self.assertIn("Codex CLI auth/profile status is not proven", report["blockers"])
            self.assertNotIn("auth_probe_excerpt", report["tools"]["codex_auth"])
            self.assertNotIn("user@example.com", json.dumps(report))

    def test_codex_auth_classifier_uses_word_boundaries(self):
        self.assertFalse(preflight.has_auth_marker("inactive", ("active",)))
        self.assertFalse(preflight.has_auth_marker("notauthenticated", ("authenticated",)))
        self.assertTrue(preflight.has_auth_marker("authenticated as codex", ("authenticated",)))

    def test_codex_active_alone_does_not_prove_auth(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            report = preflight.build_report(
                config,
                runner=FakeRunner(config.repo, codex_auth_output="profile active"),
                which=fake_which,
                env={},
            )
            self.assertFalse(report["ready_for_rri"])
            self.assertEqual(report["tools"]["codex_auth"]["auth_status"], "command_ok_unclassified")


if __name__ == "__main__":
    unittest.main()
