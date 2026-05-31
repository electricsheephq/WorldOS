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
        self.assertIn("failure_bucket", harness)
        self.assertIn("failure_detail", harness)
        self.assertIn("original_result", harness)
        self.assertIn("move_rejected", harness)
        self.assertIn("console_error", harness)

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
