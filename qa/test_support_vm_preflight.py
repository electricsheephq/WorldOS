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
        codex_version: str = "codex-cli 0.120.0",
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
        self.codex_version = codex_version
        self.chromium_path = chromium_path or repo / ".fake-chromium"
        self.remote_query_ok = remote_query_ok
        self.remote_query_head = remote_query_head or head
        self.remote_query_stderr = remote_query_stderr
        self.commands = []
        if create_chromium:
            self.chromium_path.parent.mkdir(parents=True, exist_ok=True)
            self.chromium_path.write_text("fake browser", encoding="utf-8")

    def __call__(self, cmd, cwd=None, timeout=8):
        self.commands.append(tuple(cmd))
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
                "codex": self.codex_version,
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
        if base == "codex" and args == ("login", "status"):
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
        "scripts/play_codex_dm.sh",
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
        min_memory_gb=0,
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

    def test_codex_service_tier_parser_ignores_commented_default(self):
        text = '\n# service_tier = "default"\nservice_tier = "fast"\n'
        self.assertEqual(preflight.parse_codex_service_tier(text), "fast")

    def test_codex_cli_0128_blocks_stale_default_service_tier(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            codex_home = Path(td) / "codex-home"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text('service_tier = "default"\n', encoding="utf-8")

            report = preflight.build_report(
                config,
                runner=FakeRunner(config.repo, codex_version="codex-cli 0.128.0"),
                which=fake_which,
                env={"CODEX_HOME": str(codex_home)},
            )

            self.assertFalse(report["ready_for_rri"])
            self.assertFalse(report["readiness"]["codex_config_ready"])
            self.assertIn("codex_config", report["readiness"]["blocking_categories"])
            self.assertIn("service_tier", "\n".join(report["blockers"]))
            self.assertEqual(report["tools"]["codex_auth"]["config"]["service_tier"], "default")

    def test_codex_cli_0128_allows_fast_or_unset_service_tier(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            codex_home = Path(td) / "codex-home"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text('service_tier = "flex"\n', encoding="utf-8")

            report = preflight.build_report(
                config,
                runner=FakeRunner(config.repo, codex_version="codex-cli 0.128.0"),
                which=fake_which,
                env={"CODEX_HOME": str(codex_home)},
            )

            self.assertTrue(report["ready_for_rri"])
            self.assertTrue(report["readiness"]["codex_config_ready"])
            self.assertEqual(report["tools"]["codex_auth"]["config"]["service_tier"], "flex")

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
        self.assertEqual(args.provider, "codex")
        self.assertEqual(args.player_agent, "codex")
        self.assertEqual(args.min_memory_gb, 24)

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
            self.assertEqual(report["rri_plan"]["provider"], "codex")
            self.assertEqual(report["rri_plan"]["player_agent"], "codex")
            self.assertIn("codex", report["rri_plan"]["required_tools"])
            self.assertNotIn("claude", report["rri_plan"]["required_tools"])
            self.assertEqual(report["blockers"], [])
            markdown = preflight.markdown_report(report)
            self.assertIn("- Required tools: `git,python3,uv,node,npm,npx,jq,curl,lsof,timeout,pkill,pgrep,ps,codex`", markdown)

    def test_report_includes_redacted_readiness_summary_for_agent_routing(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            report = preflight.build_report(config, runner=FakeRunner(config.repo), which=fake_which, env={})

            readiness = report["readiness"]
            self.assertTrue(readiness["safe_to_run_personas"])
            self.assertTrue(readiness["same_sha_ready"])
            self.assertTrue(readiness["provider_auth_ready"])
            self.assertTrue(readiness["player_agent_auth_ready"])
            self.assertTrue(readiness["codex_config_ready"])
            self.assertTrue(readiness["required_tools_ready"])
            self.assertTrue(readiness["persona_briefs_ready"])
            self.assertTrue(readiness["private_art_ready"])
            self.assertTrue(readiness["artifact_return_ready"])
            self.assertTrue(readiness["host_capacity_ready"])
            self.assertTrue(readiness["mac_handoff_required"])
            self.assertFalse(readiness["release_verdict"])
            self.assertEqual(readiness["blocking_categories"], [])

            readiness_blob = json.dumps(readiness)
            self.assertNotIn(str(config.repo), readiness_blob)
            self.assertNotIn(str(config.art_root), readiness_blob)
            self.assertNotIn(config.artifact_return_target, readiness_blob)

    def test_report_blocks_when_host_memory_is_below_required_capacity(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            config.min_memory_gb = 999

            report = preflight.build_report(config, runner=FakeRunner(config.repo), which=fake_which, env={})

            self.assertFalse(report["ready_for_rri"])
            self.assertFalse(report["readiness"]["host_capacity_ready"])
            self.assertIn("host memory", "\n".join(report["blockers"]))
            self.assertIn("host_capacity", report["readiness"]["blocking_categories"])

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
            self.assertIn("WOS_APP_SELECTED_PROVIDER=codex", plan_blob)
            self.assertIn("WOS_APP_PLAYER_AGENT=codex", plan_blob)
            self.assertIn("--support-preflight-json", plan_blob)
            self.assertIn("--behavioral-path", plan_blob)
            self.assertIn("--ui-audit-log", plan_blob)
            self.assertIn("--palette-source", plan_blob)
            self.assertIn("support_vm_preflight.json", report["rri_plan"]["support_preflight_json"])
            self.assertTrue(report["rri_plan"]["support_preflight_required_for_split_rollup"])
            command_template = report["rri_plan"]["rri_rollup_command_template"]
            self.assertNotIn("<", command_template)
            self.assertIn("VM_PERSONA_RUN_DIRS_CSV", command_template)
            self.assertIn("SAME_SHA_MAC_HANDOFF_JSON", command_template)
            self.assertNotIn("qa/release_gate.sh --personas", plan_blob)
            self.assertTrue(report["rri_plan"]["mac_handoff_required"])
            markdown = preflight.markdown_report(report)
            self.assertIn("Split rollup requires support preflight JSON: `true`", markdown)
            self.assertIn("--support-preflight-json", markdown)

    def test_default_codex_lane_does_not_require_claude(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))

            def which_without_claude(name: str) -> str | None:
                return None if name == "claude" else f"/fake/{name}"

            report = preflight.build_report(config, runner=FakeRunner(config.repo), which=which_without_claude, env={})
            self.assertTrue(report["ready_for_rri"])
            self.assertNotIn("required VM tool missing: claude", report["blockers"])

    def test_codex_lane_blocks_when_codex_missing(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))

            def which_without_codex(name: str) -> str | None:
                return None if name == "codex" else f"/fake/{name}"

            report = preflight.build_report(config, runner=FakeRunner(config.repo), which=which_without_codex, env={})
            self.assertFalse(report["ready_for_rri"])
            self.assertIn("required VM tool missing: codex", report["blockers"])

    def test_explicit_claude_lane_requires_claude_and_marks_command(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            config.provider = "claude"
            config.player_agent = "claude"

            def which_without_claude(name: str) -> str | None:
                return None if name == "claude" else f"/fake/{name}"

            report = preflight.build_report(config, runner=FakeRunner(config.repo), which=which_without_claude, env={})
            self.assertFalse(report["ready_for_rri"])
            self.assertIn("required VM tool missing: claude", report["blockers"])
            plan_blob = json.dumps(report["rri_plan"])
            self.assertIn("WOS_APP_SELECTED_PROVIDER=claude", plan_blob)
            self.assertIn("WOS_APP_PLAYER_AGENT=claude", plan_blob)

    def test_explicit_claude_lane_does_not_report_auth_ready_without_probe(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            config.provider = "claude"
            config.player_agent = "claude"

            report = preflight.build_report(config, runner=FakeRunner(config.repo), which=fake_which, env={})

            self.assertFalse(report["ready_for_rri"])
            self.assertFalse(report["readiness"]["provider_auth_ready"])
            self.assertFalse(report["readiness"]["player_agent_auth_ready"])
            self.assertIn("Claude CLI auth/profile status is not proven", "\n".join(report["blockers"]))

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

    def test_codex_not_signed_in_output_does_not_false_green(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            report = preflight.build_report(
                config,
                runner=FakeRunner(config.repo, codex_auth_output="Not signed in"),
                which=fake_which,
                env={},
            )
            self.assertFalse(report["ready_for_rri"])
            self.assertEqual(report["tools"]["codex_auth"]["auth_status"], "not_proven")
            self.assertIn("Codex CLI auth/profile status is not proven", report["blockers"])

    def test_codex_auth_probe_uses_supported_login_status(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            runner = FakeRunner(config.repo)
            report = preflight.build_report(config, runner=runner, which=fake_which, env={})
            self.assertTrue(report["ready_for_rri"])
            self.assertEqual(report["tools"]["codex_auth"]["auth_probe_command"], "codex login status")
            self.assertTrue(any(tuple(command[1:]) == ("login", "status") for command in runner.commands))
            self.assertFalse(any(tuple(command[1:]) == ("auth", "status") for command in runner.commands))

    def test_codex_auth_classifier_uses_word_boundaries(self):
        self.assertFalse(preflight.has_auth_marker("inactive", ("active",)))
        self.assertFalse(preflight.has_auth_marker("notauthenticated", ("authenticated",)))
        self.assertTrue(preflight.has_auth_marker("authenticated as codex", ("authenticated",)))

    def test_codex_mcp_override_version_gate(self):
        self.assertTrue(preflight.supports_codex_mcp_overrides("codex-cli 0.120.0"))
        self.assertTrue(preflight.supports_codex_mcp_overrides("codex-cli 1.0.0"))
        self.assertFalse(preflight.supports_codex_mcp_overrides("codex-cli 0.119.9"))
        self.assertFalse(preflight.supports_codex_mcp_overrides("codex-cli unknown"))

    def test_old_codex_version_blocks_codex_lane(self):
        with tempfile.TemporaryDirectory() as td:
            config = make_config(Path(td))
            report = preflight.build_report(
                config,
                runner=FakeRunner(config.repo, codex_version="codex-cli 0.119.9"),
                which=fake_which,
                env={},
            )
            self.assertFalse(report["ready_for_rri"])
            blocker_text = "\n".join(report["blockers"])
            self.assertIn("codex exec -c mcp_servers.* overrides", blocker_text)
            self.assertFalse(report["tools"]["codex_auth"]["mcp_override_supported"])

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
