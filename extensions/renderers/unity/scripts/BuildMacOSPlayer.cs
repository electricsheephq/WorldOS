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
///
/// AFTER EVERY REBUILD (#1443): run `qa/player_smoke.sh` against the fresh .app before trusting
/// it for a T3 gate run. It is the free (~30-60s, no LLM) deterministic post-build smoke — boots
/// the camp fixture + this player, scripts a move + attack through the SAME native-palette
/// primitives the T3 blind-player agent uses, and asserts the engine actually moved/damaged
/// something and the captured frames actually changed. This is the standing regression check for
/// the bug the smoke exists to catch: WorldOSPlayer opening on a different Mission Control Space
/// silently blinded every screenshot/click for the T3 gate (see docs/RUNBOOK-INDEX.md's "player
/// smoke" row).
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

            // 1b) #1463 W6.4 OPTIONAL stage manifest -> StreamingAssets (verbatim). Present => the built player
            //     animates the scene (fire flicker + glow anchors, CombatSurfaceClient.LoadStageManifest);
            //     ABSENT => not copied, and the runtime finds no stage.json => byte-identical scene. Not fatal.
            string stageSrc = Path.Combine(projectRoot, "stage.json");
            if (File.Exists(stageSrc)) { File.Copy(stageSrc, Path.Combine(saDir, "stage.json"), true); Debug.Log("[Package] stage.json -> StreamingAssets (#1463)"); }

            // 1c) WALKABLE-SLICE-V1 (item 6) OPTIONAL plate registry -> StreamingAssets (verbatim). Present =>
            //     the built player swaps the backdrop per engine location at runtime (CombatSurfaceClient
            //     LoadPlateManifest/ApplyPlate); the referenced plates/*.png ride along under plates/. ABSENT
            //     => not copied, the runtime finds no manifest => the scene's baked single plate stands. Not fatal.
            string platesManifestSrc = Path.Combine(projectRoot, "plates_manifest.json");
            if (File.Exists(platesManifestSrc))
            {
                File.Copy(platesManifestSrc, Path.Combine(saDir, "plates_manifest.json"), true);
                string platesSrcDir = Path.Combine(projectRoot, "plates");
                if (Directory.Exists(platesSrcDir))
                {
                    string platesDstDir = Path.Combine(saDir, "plates"); Directory.CreateDirectory(platesDstDir);
                    foreach (var f in Directory.GetFiles(platesSrcDir, "*.png")) File.Copy(f, Path.Combine(platesDstDir, Path.GetFileName(f)), true);
                    Debug.Log("[Package] plates_manifest.json + plates/*.png -> StreamingAssets (W5e item 6)");
                }
                else Debug.LogWarning("[Package] plates_manifest.json present but no plates/ dir at " + platesSrcDir);
            }

            // 1d) VFX-ANCHORS OPTIONAL effects registry -> StreamingAssets (verbatim). Present => the built
            //     player resolves per-plate `effects` types to prefabs (CombatSurfaceClient.LoadEffectsRegistry
            //     / SpawnPlateEffects); the referenced prefabs ride in the actor bundle (collected in step 2b).
            //     ABSENT => not copied, the runtime finds no registry => no anchored VFX spawn. Not fatal.
            string effectsRegSrc = Path.Combine(projectRoot, "effects_registry.json");
            if (File.Exists(effectsRegSrc)) { File.Copy(effectsRegSrc, Path.Combine(saDir, "effects_registry.json"), true); Debug.Log("[Package] effects_registry.json -> StreamingAssets (VFX-ANCHORS)"); }

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
            // #anim-pack: also bake the SHARED humanoid AnimatorController so the runtime can retarget it onto
            // humanoid actors (CombatSurfaceClient.HumanoidController). BuildAssetBundles pulls its dependencies
            // — the RPG-pack Idle/Walk/Run/Attack/Hit/Death clips + blend tree — in automatically, so only the
            // .controller path is listed. Absent on disk (controller not yet built by
            // build_worldos_humanoid_controller.cs) -> skipped, and the runtime falls back to the per-frame
            // graph path (byte-identical). Keep this path in sync with CombatSurfaceClient.HumanoidControllerPath.
            const string HumanoidCtrl = "Assets/Animations/WorldOSHumanoid.controller";
            if (!string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(HumanoidCtrl)))
            {
                if (!names.Contains(HumanoidCtrl)) names.Add(HumanoidCtrl);
                Debug.Log("[Package] +humanoid controller " + HumanoidCtrl);
            }
            else Debug.LogWarning("[Package] humanoid controller missing on disk, skipped: " + HumanoidCtrl);

            // 2b) VFX-ANCHORS: bake the effect prefabs (effects_registry.json type->prefab) into the SAME
            //     bundle so the runtime can LoadAsset them by their exact asset path. Dependencies (Hovl
            //     materials/textures) are pulled in automatically. A path missing on disk (repo-only build, no
            //     Hovl vendored) is skipped -> that effect type simply won't spawn (byte-identical). Keep the
            //     paths in sync with the CombatSurfaceClient runtime resolver (both read effects_registry.json).
            if (File.Exists(effectsRegSrc))
            {
                var fxRoot = MiniJson.Parse(File.ReadAllText(effectsRegSrc)) as Dictionary<string, object>;
                var fxMap = (fxRoot != null && fxRoot.ContainsKey("effects")) ? fxRoot["effects"] as Dictionary<string, object> : null;
                if (fxMap != null)
                    foreach (var kv in fxMap)
                    {
                        var row = kv.Value as Dictionary<string, object>; if (row == null) continue;
                        string path = row.ContainsKey("prefab") ? row["prefab"] as string : null;
                        if (string.IsNullOrEmpty(path)) continue;
                        if (string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(path))) { Debug.LogWarning("[Package] effect prefab missing on disk, skipped: " + path + " (type " + kv.Key + ")"); continue; }
                        if (!names.Contains(path)) { names.Add(path); Debug.Log("[Package] +effect prefab " + path + " (type " + kv.Key + ")"); }
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

        // #1466: RUN IN BACKGROUND. The no-hijack QA/beauty launch (#1456/#1458) never activates the
        // window, and a macOS player with this OFF PAUSES its player loop (Update/coroutines/input) while
        // backgrounded — so the surface poll + QA click channel froze and every input path silently did
        // nothing. Bake it on so the player keeps running from frame 0 whether or not it has focus.
        // (CombatSurfaceClient also sets Application.runInBackground=true at runtime as a backstop.)
        PlayerSettings.runInBackground = true;

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
