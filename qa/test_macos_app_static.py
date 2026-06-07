import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MacOSAppStaticContractTests(unittest.TestCase):
    def read(self, rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_native_app_has_separate_art_repo_setting(self):
        repository = self.read("macos/WorldOSApp/Sources/WorldOSApp/Services/RepositoryLocator.swift")
        root_view = self.read("macos/WorldOSApp/Sources/WorldOSApp/Views/RootView.swift")
        settings = self.read("macos/WorldOSApp/Sources/WorldOSApp/Views/SettingsView.swift")

        self.assertIn("defaultArtRepoPath()", repository)
        self.assertIn("launchRepoPathOverride()", repository)
        self.assertIn("WorldOSRepoRoot", repository)
        self.assertIn("WorldOSPreferLaunchRoots", repository)
        self.assertIn("looksLikeArtRepo", repository)
        self.assertIn('@AppStorage("artRepoPath")', root_view)
        self.assertIn("activeRepoPath", root_view)
        self.assertIn("activeArtRepoPath", root_view)
        self.assertIn("artRepoPath: activeArtRepoPathBinding", root_view)
        self.assertIn('ValidatedTextField("Private art repo path"', settings)

    def test_debug_control_center_honors_launch_root_overrides(self):
        root_view = self.read("macos/WorldOSApp/Sources/WorldOSApp/Views/RootView.swift")

        self.assertIn("struct DebugControlCenterView", root_view)
        self.assertGreaterEqual(root_view.count("launchRepoPathOverride"), 2)
        self.assertGreaterEqual(root_view.count("launchArtRepoPathOverride"), 2)
        self.assertIn("private var activeRepoPathBinding: Binding<String>", root_view)
        self.assertIn("private var activeArtRepoPathBinding: Binding<String>", root_view)
        self.assertIn("StatusStrip(repoPath: activeRepoPath", root_view)
        self.assertIn("repoPath: activeRepoPathBinding", root_view)
        self.assertIn("artRepoPath: activeArtRepoPathBinding", root_view)
        self.assertIn("campaignStore.reload(repoPath: activeRepoPath)", root_view)

    def test_native_viewer_and_provider_forward_art_repo_env(self):
        app_process = self.read("macos/WorldOSApp/Sources/WorldOSApp/Services/AppProcessService.swift")
        providers = self.read("macos/WorldOSApp/Sources/WorldOSApp/Services/ProviderAdapters.swift")
        models = self.read("macos/WorldOSApp/Sources/WorldOSApp/Models/ProviderModels.swift")

        self.assertIn("artRepoPath: String", app_process)
        self.assertIn('env["WORLDOS_ART_REPO_ROOT"]', app_process)
        self.assertIn("Private art repo path does not contain content/worlds/_private", app_process)
        self.assertIn("let artRepoPath: String", models)
        self.assertIn('env["WORLDOS_ART_REPO_ROOT"] = preferences.artRepoPath', providers)
        self.assertIn('env["CLAWDND_ART_REPO_ROOT"] = preferences.artRepoPath', providers)

    def test_built_app_playtest_can_keep_minted_backend_for_manual_gameplay(self):
        harness = self.read("qa/ui_playtest_app.sh")

        self.assertIn("WOS_APP_KEEP_MINTED_BACKEND=1", harness)
        self.assertIn("WOS_APP_SELECTED_PROVIDER=codex|scripted|claude|openclaw", harness)
        self.assertIn("WOS_APP_PLAYER_AGENT=claude|codex", harness)
        self.assertIn('PLAYER_AGENT="${WOS_APP_PLAYER_AGENT:-claude}"', harness)
        self.assertIn('PART_B_PROVIDER="${SELECTED_PROVIDER:-claude}"', harness)
        self.assertIn('defaults write dev.clawdnd.app selectedProvider "$SELECTED_PROVIDER"', harness)
        self.assertIn("requires WOS_APP_PART=A", harness)
        self.assertIn('KEEP_MINTED_BACKEND="${WOS_APP_KEEP_MINTED_BACKEND:-0}"', harness)
        self.assertIn("keeping minted backend alive for gameplay proof", harness)
        self.assertIn("kept_backend_alive", harness)
        self.assertIn("PART_A_KEPT_BACKEND", harness)
        self.assertIn("first_turn_ready", harness)
        self.assertIn("waiting for first-turn readiness", harness)
        self.assertIn("/app-status", harness)
        self.assertIn("app-status.launcher.json", harness)
        self.assertIn("app-status.minted.json", harness)
        self.assertIn("app_status_after", harness)
        self.assertIn('.actionModel.actor.name', harness)
        self.assertIn('play_party.sh .* $minted_run', harness)
        self.assertIn('play.sh .* $minted_run', harness)
        self.assertNotIn('play_party.sh $WORLD $minted_run', harness)
        self.assertNotIn('play.sh $WORLD $minted_run', harness)

    def test_built_app_part_b_supports_codex_provider_and_player_agent(self):
        harness = self.read("qa/ui_playtest_app.sh")

        self.assertIn("scripts/play_codex_dm.sh", harness)
        self.assertIn("CLAWDND_PROVIDER=codex", harness)
        self.assertIn('provider_family() {', harness)
        self.assertIn('"provider_family": provider_family', harness)
        self.assertIn('"dm_model": dm_model', harness)
        self.assertIn('"player_model": player_model', harness)
        self.assertIn('"scorer_provider": scorer_provider', harness)
        self.assertIn('"scorer_model": scorer_model', harness)
        self.assertIn("codex exec", harness)
        self.assertIn("codex_supports_mcp_override_config", harness)
        self.assertIn("Codex CLI >= 0.120.0", harness)
        self.assertIn("mcp_servers.clawdnd-uiplayer.command", harness)
        self.assertIn("palette_server.js", harness)
        self.assertIn("player_agent", harness)
        self.assertIn("provider", harness)

    def test_codex_provider_wrappers_pin_supported_default_model(self):
        dm = self.read("scripts/play_codex_dm.sh")
        actor = self.read("scripts/play_codex_actor.sh")

        for script in (dm, actor):
            self.assertIn("WORLDOS_CODEX_MODEL", script)
            self.assertIn('CODEX_MODEL="${WORLDOS_CODEX_MODEL:-${CLAWDND_CODEX_MODEL:-gpt-5.5}}"', script)
            self.assertIn('"provider_family": "codex-openai"', script)
            self.assertIn('"auth_surface": "codex-cli"', script)
            self.assertIn("auto|default|cli-default", script)
            self.assertNotIn('CODEX_MODEL="${WORLDOS_CODEX_MODEL:-${CLAWDND_CODEX_MODEL:-}}"', script)
            self.assertIn('MODEL_ARGS=(--model "$CODEX_MODEL")', script)
            self.assertIn('--cd "$ROOT"', script)
            self.assertIn("-c \"mcp_servers.", script)

    def test_model_agnostic_provider_config_preserves_anthropic_and_codex_defaults(self):
        models = self.read("macos/WorldOSApp/Sources/WorldOSApp/Models/ProviderModels.swift")
        providers = self.read("macos/WorldOSApp/Sources/WorldOSApp/Services/ProviderAdapters.swift")
        root = self.read("macos/WorldOSApp/Sources/WorldOSApp/Views/RootView.swift")
        settings = self.read("macos/WorldOSApp/Sources/WorldOSApp/Views/SettingsView.swift")
        provider_view = self.read("macos/WorldOSApp/Sources/WorldOSApp/Views/ProvidersView.swift")
        diagnostics = self.read("macos/WorldOSApp/Sources/WorldOSApp/Services/Diagnostics.swift")

        self.assertIn('case .claude: "anthropic"', models)
        self.assertIn('case .codex: "codex-openai"', models)
        self.assertIn('case .claude: "claude-cli"', models)
        self.assertIn('case .codex: "codex-cli"', models)
        self.assertIn('static let defaultClaudeDMModel = "opus"', models)
        self.assertIn('static let defaultCodexDMModel = "gpt-5.5"', models)
        self.assertIn('static let defaultClaudePlayerModel = "sonnet"', models)
        self.assertIn('static let defaultCodexPlayerModel = "gpt-5.5"', models)

        self.assertIn('environment["WORLDOS_PROVIDER"] = kind.rawValue', providers)
        self.assertIn('environment["CLAWDND_PROVIDER"] = kind.rawValue', providers)
        self.assertIn('environment["WORLDOS_DM_MODEL"] = dmModel', providers)
        self.assertIn('environment["CLAWDND_DM_MODEL"] = dmModel', providers)
        self.assertIn('environment["WORLDOS_CODEX_MODEL"] = dmModel', providers)
        self.assertIn('environment["CLAWDND_CODEX_MODEL"] = dmModel', providers)
        self.assertIn('environment["WORLDOS_PROVIDER_FAMILY"] = kind.providerFamily', providers)
        self.assertIn('environment["WORLDOS_AUTH_SURFACE"] = kind.authSurface', providers)

        self.assertIn('@AppStorage("claudeDMModel")', root)
        self.assertIn('@AppStorage("codexDMModel")', root)
        self.assertIn('"providerFamilyConfig": providerFamilyConfigPayload()', root)
        self.assertIn('"providerFamily": $0.providerFamily', root)
        self.assertIn('"authSurface": $0.authSurface', root)
        self.assertIn('"dmModel": $0.dmModel', root)
        self.assertIn('"playerModel": $0.playerModel', root)
        self.assertIn('"scorerModel": $0.scorerModel', root)

        self.assertIn("Provider models", settings)
        self.assertIn("QA player model", settings)
        self.assertIn("QA scorer model", settings)
        self.assertIn("Provider model defaults", provider_view)
        self.assertIn("Provider family:", diagnostics)
        self.assertIn("DM model:", diagnostics)

    def test_codex_scorer_is_drop_in_and_never_invokes_claude(self):
        score_codex = self.read("qa/score_codex.sh")
        run_duo = self.read("qa/run_duo.sh")
        run_party = self.read("qa/run_party.sh")
        sprint = self.read("qa/run_combat_sprint.sh")

        self.assertIn("qa/score_codex.sh <transcript.md> <state.json> <rubric.md> <schema.json> <out.json> [budget]", score_codex)
        self.assertIn("codex exec", score_codex)
        self.assertIn("codex-openai", score_codex)
        self.assertNotIn("claude -p", score_codex)
        self.assertIn("SCORE_SCRIPT=\"$(worldos_env SCORE_SCRIPT qa/score.sh)\"", run_duo)
        self.assertIn('"$SCORE_SCRIPT" "$T/$RUN.md"', run_duo)
        self.assertIn("SCORE_SCRIPT=\"$(worldos_env SCORE_SCRIPT qa/score.sh)\"", run_party)
        self.assertIn("SCORE_SCRIPT=\"$(worldos_env SCORE_SCRIPT qa/score.sh)\"", sprint)

    def test_codex_dm_provider_feeds_live_progress_events(self):
        script = self.read("scripts/play_codex_dm.sh")
        app = self.read("viewer/openworlds/app.jsx")

        self.assertIn("LIVE_PROGRESS_LOG_RULE", script)
        self.assertIn("LIVE_DIALOGUE_LOG_RULE", script)
        self.assertIn("DM_CONTRACT_RULE", script)
        self.assertIn("DM_VOICE_RULE", script)
        self.assertIn("Self-contained DM contract", script)
        self.assertIn("Do not read skill files", script)
        self.assertIn("warm, fair, generous storyteller voice", script)
        self.assertIn("never invent dice, rules outcomes, or campaign state", script)
        self.assertIn("visible story progress while your turn is still running", script)
        self.assertIn("log_event(kind=\\\"narration\\\", text=\\\"...\\\")", script)
        self.assertIn("do not call log_event(kind=\\\"dialogue\\\")", script)
        self.assertIn("without hiding dialogue from the player", script)
        self.assertIn("the wrapper records the final reply through the engine after the turn", script)
        self.assertNotIn("skills/dungeon-master/SKILL.md", script)
        self.assertNotIn("/Users/lume/.codex/skills/dungeon-master", script)
        self.assertIn("OPENING_PROGRESS_TEXT=", script)
        self.assertIn("MOVE_PROGRESS_TEXTS=(", script)
        self.assertIn("choose_move_progress_text() {", script)
        self.assertNotIn("The Lower City resolves around you", script)
        self.assertIn('log_engine_narration "$HERO_CAMP" "$OPENING_PROGRESS_TEXT"', script)
        self.assertIn('MOVE_PROGRESS_TEXT="$(choose_move_progress_text "$DM_TURNS")"', script)
        self.assertIn('log_engine_narration "$ACTIVE_CAMPAIGN_ID" "$MOVE_PROGRESS_TEXT"', script)
        self.assertLess(
            script.index('log_engine_narration "$HERO_CAMP" "$OPENING_PROGRESS_TEXT"'),
            script.index('if [ -n "$HERO_CAMP" ]'),
        )
        self.assertLess(
            script.index('MOVE_PROGRESS_TEXT="$(choose_move_progress_text "$DM_TURNS")"'),
            script.index('log_engine_narration "$ACTIVE_CAMPAIGN_ID" "$MOVE_PROGRESS_TEXT"'),
        )
        self.assertLess(
            script.index('log_engine_narration "$ACTIVE_CAMPAIGN_ID" "$MOVE_PROGRESS_TEXT"'),
            script.index('codex_dm_turn "You are the Dungeon Master mid-session.'),
        )
        self.assertGreaterEqual(script.count("$LIVE_PROGRESS_LOG_RULE"), 3)
        self.assertGreaterEqual(script.count("$LIVE_DIALOGUE_LOG_RULE"), 3)
        self.assertNotIn(
            "Do not call log_event for player-facing narration or dialogue in this provider wrapper",
            script,
        )
        self.assertNotIn(
            "Only call log_event during the opening if you need one short non-duplicate system/roll row",
            script,
        )
        self.assertIn("Codex DM wrapper now writes an immediate wrapper-authored engine progress row", app)
        self.assertIn("provider to write one short engine-owned progress narration", app)
        self.assertIn("when /events progress arrives, it keeps a healthy turn alive", app)

    def test_codex_dm_provider_publishes_turn_cap_stop_status(self):
        script = self.read("scripts/play_codex_dm.sh")
        server = self.read("viewer/server.py")

        self.assertIn('PROVIDER_STATUS="$RUN_DIR/provider_status.json"', script)
        self.assertIn("write_provider_status() {", script)
        self.assertIn('"schema": "worldos.provider-status.v1"', script)
        self.assertIn('write_provider_status "running" "active"', script)
        self.assertIn('write_provider_status "stopped" "turn_cap"', script)
        self.assertIn("WORLDOS_PROVIDER_STOP_GRACE_SECONDS", script)
        self.assertIn('tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")', script)
        self.assertIn("os.fsync(handle.fileno())", script)
        self.assertIn("tmp_path.replace(path)", script)
        move_turn_increment = 'DM_TURNS=$((DM_TURNS + 1))'
        self.assertIn(move_turn_increment, script)
        self.assertLess(
            script.index(move_turn_increment),
            script.index('write_provider_status "running" "active"', script.index(move_turn_increment)),
        )

        self.assertIn("def _provider_status_summary() -> dict:", server)
        self.assertIn('"provider_status": provider_status', server)
        self.assertIn('provider_lifecycle in {"stopped", "failed", "exhausted"}', server)
        self.assertIn("DM provider is no longer running", server)

    def test_scripted_provider_is_dev_gated_and_model_free(self):
        models = self.read("macos/WorldOSApp/Sources/WorldOSApp/Models/ProviderModels.swift")
        providers = self.read("macos/WorldOSApp/Sources/WorldOSApp/Services/ProviderAdapters.swift")
        root_view = self.read("macos/WorldOSApp/Sources/WorldOSApp/Views/RootView.swift")
        play_view = self.read("macos/WorldOSApp/Sources/WorldOSApp/Views/PlayView.swift")
        script = self.read("scripts/play_scripted_dm.sh")
        build_script = self.read("script/build_and_run.sh")

        self.assertIn("case scripted", models)
        self.assertIn("WORLDOS_ENABLE_SCRIPTED_PROVIDER", models)
        self.assertIn('Bundle.main.object(forInfoDictionaryKey: "WorldOSEnableScriptedProvider")', models)
        self.assertIn("static var allCases", models)
        self.assertIn("cases.append(.scripted)", models)
        self.assertIn("var isLaunchEnabled", models)

        self.assertIn('ENABLE_SCRIPTED_PROVIDER="${WORLDOS_ENABLE_SCRIPTED_PROVIDER:-0}"', build_script)
        self.assertIn("WorldOSEnableScriptedProvider", build_script)
        self.assertIn('ENABLE_SCRIPTED_PROVIDER_PLIST="$(plist_bool "$ENABLE_SCRIPTED_PROVIDER")"', build_script)

        self.assertIn("struct ScriptedProvider", providers)
        self.assertIn("ProviderKind.scriptedProviderEnabled", providers)
        self.assertIn("scripts/play_scripted_dm.sh", providers)
        self.assertIn('Shell.which("python3")', providers)
        self.assertIn('Shell.which("uv")', providers)
        self.assertNotIn('Shell.which("python") != nil || Shell.which("python3")', providers)
        self.assertIn(".scripted: ScriptedProvider()", providers)
        self.assertIn("no Claude, Codex, or OpenClaw required", providers)

        self.assertIn("guard provider.isLaunchEnabled", root_view)
        self.assertIn("guard provider.isLaunchEnabled", play_view)

        self.assertIn("WORLDOS_ENABLE_SCRIPTED_PROVIDER=1 is required", script)
        self.assertIn("WORLDOS_PROVIDER_FAMILY=scripted", script)
        self.assertIn('"provider_family": "scripted"', script)
        self.assertIn("for cmd in python3 uv", script)
        self.assertIn('"${CLAWDND_PLAY_HERO:-}"', script)
        self.assertIn('if spec.get("canon")', script)
        self.assertIn("server.create_character", script)
        self.assertIn('uv run --directory "$ROOT/servers/engine"', script)
        self.assertIn("server.start_world", script)
        self.assertIn("server.load_canon_character", script)
        self.assertIn("server.log_event", script)
        self.assertIn("invalid chat extra_json", script)
        self.assertIn('json_append "$CHAT" "dm" "$OPENING" \'{"engine_logged":true}\'', script)
        self.assertIn('json_append "$CHAT" "dm" "$reply" \'{"engine_logged":true}\'', script)
        self.assertIn('sed -n "$((processed + 1)),${count}p" "$MOVES"', script)
        self.assertIn("python3 viewer/server.py", script)
        self.assertIn("WORLDOS_PLAYER_MOVES", script)
        self.assertNotIn("claude -p", script)
        self.assertNotIn("codex -p", script)

    def test_built_app_playtest_emits_split_failure_buckets(self):
        harness = self.read("qa/ui_playtest_app.sh")

        for bucket in (
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
        ):
            self.assertIn(f'"{bucket}"', harness)

        self.assertIn("PART_A_FAILURE_BUCKET", harness)
        self.assertIn("PART_A_FAILURE_DETAIL", harness)
        self.assertIn("PART_B_FAILURE_BUCKET", harness)
        self.assertIn("PART_B_FAILURE_DETAIL", harness)
        self.assertIn("classify_native_failure", harness)
        self.assertIn("classify_part_b_readiness_failure", harness)
        self.assertIn("classify_part_b_failure_from_artifacts", harness)
        self.assertIn("classify_part_b_score_failure", harness)
        self.assertIn("failure_bucket", harness)
        self.assertIn("failure_detail", harness)
        self.assertIn("original_result", harness)
        self.assertIn("move_rejected", harness)
        self.assertIn("console_error", harness)
        self.assertNotIn('"score_failed"', harness)

    def test_provider_viewer_stays_attached_during_native_restarts(self):
        root_view = self.read("macos/WorldOSApp/Sources/WorldOSApp/Views/RootView.swift")
        app_process = self.read("macos/WorldOSApp/Sources/WorldOSApp/Services/AppProcessService.swift")

        self.assertIn("activeProviderOpenWorldsURL", app_process)
        self.assertIn('endpoint.name == "Provider viewer"', app_process)
        self.assertIn("markProviderViewerReady()", app_process)
        self.assertIn("keepActiveProviderViewerAttached()", root_view)
        self.assertRegex(
            root_view,
            r"private func startOpenWorlds\(\) \{\s*if keepActiveProviderViewerAttached\(\)",
        )
        self.assertIn('launchMessage = "Provider session active"', root_view)

if __name__ == "__main__":
    unittest.main()
