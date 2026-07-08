using System;
using System.Collections.Generic;
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

    // #1436 (W5c Unit 1) packaging: the standalone player cannot AssetDatabase.Load an Assets/ path at
    // runtime, so the registry-referenced actor assets + the registry manifest must be packaged into the
    // build. StreamingAssets is the natural home (copied verbatim into the .app, readable at runtime):
    //   - StreamingAssets/registry.json : the registry manifest, copied VERBATIM from the project root
    //     (the same file paint_combat_v1.cs reads), so CombatSurfaceClient's SLOT resolution reads the
    //     exact manifest the editor baked from. Editor render path unchanged.
    //   - StreamingAssets/worldos_actors : a StandaloneOSX AssetBundle of every registry-referenced
    //     model_ref/albedo_ref/anim_ref, keyed by their EXACT registry asset path. The runtime loader
    //     (CombatSurfaceClient.LoadAsset) passes registry model_ref VERBATIM as the bundle key — zero
    //     path transform, so an asset swap stays a registry edit + repackage, zero renderer edits (the
    //     registry invariant). AssetBundle (not Resources) was chosen precisely because its load key IS
    //     the asset path already in the registry; Resources would force copying assets under Resources/
    //     and mangling the path into a resources-relative key, breaking that invariance.
    const string BundleName = "worldos_actors";

    [MenuItem("Tools/WorldOS/Build/Package player actors (bundle + registry)")]
    public static void PackageOnly() { EnsurePackaged(); }

    // Build the actor AssetBundle + copy registry.json into StreamingAssets. Idempotent; safe to run
    // standalone (the MenuItem above) or as the first step of a player build. Never throws out — a
    // packaging failure is logged but does not abort the player build (a legacy build still works,
    // it just can't runtime-spawn until the bundle is present).
    public static void EnsurePackaged()
    {
        try
        {
            string projectRoot = Directory.GetParent(Application.dataPath).FullName;
            string saDir = Path.Combine(Application.dataPath, "StreamingAssets");
            Directory.CreateDirectory(saDir);

            // 1) registry.json -> StreamingAssets (verbatim; the file paint_combat_v1.cs reads).
            string regSrc = Path.Combine(projectRoot, "registry.json");
            if (!File.Exists(regSrc)) { Debug.LogWarning("[Package] no registry.json at " + regSrc + " — skipping packaging"); return; }
            File.Copy(regSrc, Path.Combine(saDir, "registry.json"), true);

            // 2) collect every registry-referenced asset path (model/albedo/anim) that actually exists.
            var names = new List<string>();
            var root = MiniJson.Parse(File.ReadAllText(regSrc)) as Dictionary<string, object>;
            var assets = (root != null && root.ContainsKey("assets")) ? root["assets"] as Dictionary<string, object> : null;
            if (assets != null)
            {
                foreach (var kv in assets)
                {
                    var row = kv.Value as Dictionary<string, object>; if (row == null) continue;
                    foreach (var field in new[] { "model_ref", "albedo_ref", "anim_ref" })
                    {
                        string path = (row.ContainsKey(field) ? row[field] as string : null);
                        if (string.IsNullOrEmpty(path)) continue;
                        if (string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path))) { Debug.LogWarning("[Package] registry path missing on disk, skipped: " + path); continue; }
                        if (!names.Contains(path)) names.Add(path);
                    }
                }
            }
            if (names.Count == 0) { Debug.LogWarning("[Package] no resolvable registry assets — bundle not built"); return; }

            // 3) build the bundle for StandaloneOSX to a temp dir, then copy the main bundle file into
            //    StreamingAssets (building directly into Assets/ re-imports the archive; a temp+copy is
            //    the clean pattern). Dependencies (FBX materials/textures) are pulled in automatically.
            string bundleTmp = Path.Combine(projectRoot, "AssetBundles"); Directory.CreateDirectory(bundleTmp);
            var build = new AssetBundleBuild { assetBundleName = BundleName, assetNames = names.ToArray() };
            var manifest = BuildPipeline.BuildAssetBundles(bundleTmp, new[] { build }, BuildAssetBundleOptions.None, BuildTarget.StandaloneOSX);
            if (manifest == null) { Debug.LogError("[Package] BuildAssetBundles returned null"); return; }
            string builtBundle = Path.Combine(bundleTmp, BundleName);
            if (!File.Exists(builtBundle)) { Debug.LogError("[Package] built bundle not found: " + builtBundle); return; }
            File.Copy(builtBundle, Path.Combine(saDir, BundleName), true);
            AssetDatabase.Refresh();
            Debug.Log("[Package] DONE bundle=" + BundleName + " assets=" + names.Count + " -> " + saDir);
        }
        catch (Exception e) { Debug.LogError("[Package] FAILED: " + e); }
    }

    [MenuItem("Tools/WorldOS/Build/macOS Player (Universal)")]
    public static void Build()
    {
        // #1436: package the runtime actor bundle + registry into StreamingAssets FIRST so the built
        // player can runtime-spawn actors for any campaign (not just the baked scene's cast).
        EnsurePackaged();

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
