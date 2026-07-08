using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

/// <summary>
/// W5a (#1322) — macOS standalone PLAYER build, batch-mode-free (the box forbids `-batchmode`,
/// see extensions/renderers/unity/BOX.md #1196: it silently kills the live MCP HTTP server).
/// Run this MenuItem from the already-running headed editor (via execute_menu_item or the
/// Unity MCP `code execute`), never via a separate `-batchmode` process launch.
///
/// Scope: PURE packaging — no renderer/gameplay code changes. Sets company/product/bundle id,
/// confirms the Universal (x64+AppleSilicon) architecture, registers the current-best combat
/// scene in Build Settings (was empty), and builds the .app to BuildOutput/ beside the project.
/// Writes a build-report text file for evidence (qa/evidence/1322-build/).
/// </summary>
public static class BuildMacOSPlayer
{
    // The current-best LIVE combat scene per CANONICAL.md (#1418 close-out, 2026-07-08) — the
    // one that consumes /combat-surface via paint_combat_v1.cs's engine-driven actor placement.
    // TavernTier1.unity is explicitly DEPRECATED in CANONICAL.md; do not build from it.
    const string SceneToBuild = "Assets/Scenes/M1CombatV1_canonical.unity";
    const string OutputDir = "BuildOutput";
    const string AppName = "WorldOSPlayer.app";

    [MenuItem("Tools/WorldOS/Build/macOS Player (Universal)")]
    public static void Build()
    {
        // --- Player identity (was DefaultCompany/WorldOS-Unity-spike) ---
        PlayerSettings.companyName = "worldos";
        PlayerSettings.productName = "WorldOSPlayer";
        PlayerSettings.applicationIdentifier = "com.worldos.WorldOSPlayer";

        // --- Architecture: Universal (Intel + Apple Silicon) if the module supports it,
        //     else fall back to x64 only. Never fail the build over this. ---
        string archResult;
        try
        {
            UnityEditor.OSXStandalone.UserBuildSettings.architecture = UnityEditor.Build.OSArchitecture.x64ARM64;
            archResult = UnityEditor.OSXStandalone.UserBuildSettings.architecture.ToString();
        }
        catch (Exception e)
        {
            Debug.LogWarning("[BuildMacOSPlayer] Universal architecture unavailable (" + e.Message + "); falling back to x64.");
            try { UnityEditor.OSXStandalone.UserBuildSettings.architecture = UnityEditor.Build.OSArchitecture.x64; }
            catch { /* leave editor default */ }
            archResult = "x64 (fallback)";
        }

        // --- Build Settings scene list was EMPTY on this project; register the runtime scene. ---
        var scenes = new[] { new EditorBuildSettingsScene(SceneToBuild, true) };
        EditorBuildSettings.scenes = scenes;

        string projectRoot = Directory.GetParent(Application.dataPath).FullName;
        string outDir = Path.Combine(projectRoot, OutputDir);
        Directory.CreateDirectory(outDir);
        string appPath = Path.Combine(outDir, AppName);

        var options = new BuildPlayerOptions
        {
            scenes = new[] { SceneToBuild },
            locationPathName = appPath,
            target = BuildTarget.StandaloneOSX,
            targetGroup = BuildTargetGroup.Standalone,
            options = BuildOptions.None
        };

        Debug.Log("[BuildMacOSPlayer] Starting build -> " + appPath + " (arch=" + archResult + ")");
        BuildReport report = BuildPipeline.BuildPlayer(options);
        var s = report.summary;

        string reportPath = Path.Combine(outDir, "build-report.txt");
        File.WriteAllText(reportPath,
            "result=" + s.result + "\n" +
            "totalErrors=" + s.totalErrors + "\n" +
            "totalWarnings=" + s.totalWarnings + "\n" +
            "totalSize=" + s.totalSize + " bytes\n" +
            "totalTime=" + s.totalTime + "\n" +
            "outputPath=" + s.outputPath + "\n" +
            "buildStartedAt=" + s.buildStartedAt + "\n" +
            "buildEndedAt=" + s.buildEndedAt + "\n" +
            "platform=" + s.platform + "\n" +
            "architecture=" + archResult + "\n" +
            "scenesBuilt=" + string.Join(",", options.scenes) + "\n");

        Debug.Log("[BuildMacOSPlayer] DONE result=" + s.result + " errors=" + s.totalErrors
            + " warnings=" + s.totalWarnings + " size=" + s.totalSize + " time=" + s.totalTime
            + " report=" + reportPath);
    }
}
