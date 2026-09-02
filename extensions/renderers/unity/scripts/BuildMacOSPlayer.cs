using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

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
    const string ReportFileName = "build-report.txt";

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
                // UNIFY-THE-FRAMES (#1575): per-plate occluder-box sidecars (boxes/*.json emitted by
                // build_room_unified.cs) ship exactly like plates — a manifest `boxes` entry with no
                // packaged file silently degrades to footprint proxies (the proof build hit this).
                string boxesSrcDir = Path.Combine(projectRoot, "boxes");
                if (Directory.Exists(boxesSrcDir))
                {
                    string boxesDstDir = Path.Combine(saDir, "boxes"); Directory.CreateDirectory(boxesDstDir);
                    foreach (var f in Directory.GetFiles(boxesSrcDir, "*.json")) File.Copy(f, Path.Combine(boxesDstDir, Path.GetFileName(f)), true);
                    Debug.Log("[Package] boxes/*.json occluder sidecars -> StreamingAssets (UNIFY-THE-FRAMES)");
                }
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

        // Kit rooms (build_room_kit.cs) are QA constructions; a capture flow that saved the scene while one
        // existed shipped it inside the player, drawing grey kit masses over every plate. Strip them for
        // THIS BUILD ONLY — buildScenePath is redirected to a temp copy, never the tracked scene.
        string buildScenePath = SceneToBuild;
        string[] strippedQARoots = StripQAConstructions(ref buildScenePath);

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

        // #1674: GUARANTEE the runtime-resolved shaders (Shader.Find, referenced by no asset) survive
        // player-build variant stripping. Previously ONLY the manual Tools/WorldOS/W5b menu item added these
        // to Always-Included; a box rebuild that skipped W5b shipped WITHOUT WorldOS/ActorSilhouette, so
        // CombatSurfaceClient.EnsureSilhouetteMaterial warned once and the walk-behind mask silently no-op'd
        // (#1674 / #1651 player_cert). Baking the registration into the build itself means the class can never
        // ship silently again; qa/check_always_included_shaders.py is the pre-flight hard gate on the source.
        string[] includedShaders = EnsureAlwaysIncludedShaders();

        string projectRoot = Directory.GetParent(Application.dataPath).FullName;
        string outDir = Path.Combine(projectRoot, OutputDir);
        Directory.CreateDirectory(outDir);
        string appPath = Path.Combine(outDir, AppName);

        var options = new BuildPlayerOptions
        {
            scenes = new[] { buildScenePath },
            locationPathName = appPath,
            target = BuildTarget.StandaloneOSX,
            targetGroup = BuildTargetGroup.Standalone,
            options = BuildOptions.None
        };

        Debug.Log("[BuildMacOSPlayer] Starting build -> " + appPath + " (arch=" + archResult + ")");
        BuildReport report;
        try { report = BuildPipeline.BuildPlayer(options); }
        finally { if (buildScenePath != SceneToBuild) AssetDatabase.DeleteAsset(buildScenePath); }
        var s = report.summary;

        string reportPath = Path.Combine(outDir, ReportFileName);
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
            "alwaysIncludedShaders=" + string.Join(",", includedShaders) + "\n" +
            "strippedQARoots=" + (strippedQARoots.Length == 0 ? "(none)" : string.Join(",", strippedQARoots)) + "\n" +
            "scenesBuilt=" + string.Join(",", options.scenes) + "\n");

        Debug.Log("[BuildMacOSPlayer] DONE result=" + s.result + " errors=" + s.totalErrors
            + " warnings=" + s.totalWarnings + " size=" + s.totalSize + " time=" + s.totalTime
            + " report=" + reportPath);
    }

    // QA-construction roots that must NEVER ship inside a player build. build_room_kit.cs assembles kit
    // rooms as "KitRoom_<roomId>" roots in whatever scene is open; a capture/lighting flow that saved the
    // scene mid-session baked them in, and the built player then rendered grey kit masses (fallback boxes,
    // brazier plinths, parapets) in front of every plate. Measured three times (kit-crypt cleankit trap ×2,
    // kit-tavern 2026-07-23 — the withheld tavern install). qa/qa_sandbox.py is the independent detector.
    //
    // The strip is a BUILD-LOCAL transform, never an edit of the tracked scene. Saving the canonical scene
    // as a build side effect is exactly the bug this gate exists to prevent, so it must not be the fix for
    // it: the scene is snapshotted to a temp scene asset (SaveScene saveAsCopy — the editor's own scene is
    // neither dirtied nor reloaded, so unsaved in-editor work survives untouched), the KitRoom_* roots are
    // destroyed in THAT copy, and BuildPlayerOptions.scenes points at the copy for this build only.
    const string QARootPrefix = "KitRoom_";
    const string StripScenePath = "Assets/Scenes/__BuildStripped_QARoots.unity";

    // Returns the stripped root names (empty = nothing to strip) and, when a strip happened, redirects
    // buildScenePath at the temp scene. A strip that cannot be completed FAILS the build (FailBuild) —
    // a silent failure here would ship the contamination under a green build stamp.
    static string[] StripQAConstructions(ref string buildScenePath)
    {
        var active = EditorSceneManager.GetActiveScene();
        Scene src;
        string restorePath = null;
        if (active.IsValid() && active.path == SceneToBuild)
        {
            src = active;
        }
        else
        {
            // OpenScene(Single) DISCARDS unsaved changes in the open scene. Refuse loudly rather than
            // throwing editor work away; a save prompt is not an option here, because this MenuItem runs
            // in a headed-but-remotely-driven editor where a modal deadlocks the session (BOX.md #1196).
            if (active.IsValid() && (active.isDirty || string.IsNullOrEmpty(active.path)))
                FailBuild("refusing to build: the open scene '"
                    + (string.IsNullOrEmpty(active.path) ? "Untitled" : active.path)
                    + "' has UNSAVED changes and this build must open " + SceneToBuild
                    + ". Save or discard those changes, then rebuild.");
            restorePath = active.IsValid() ? active.path : null;
            src = EditorSceneManager.OpenScene(SceneToBuild, OpenSceneMode.Single);
        }

        var qaRoots = new List<string>();
        foreach (var go in src.GetRootGameObjects())
            if (go != null && go.name.StartsWith(QARootPrefix, StringComparison.Ordinal)) qaRoots.Add(go.name);

        // Route through the copy whenever the built content could differ from the scene asset on disk —
        // i.e. there is something to strip, or the open scene has unsaved edits. Otherwise build the
        // canonical path directly (the long-standing, well-exercised path).
        if (qaRoots.Count > 0 || src.isDirty)
        {
            if (!EditorSceneManager.SaveScene(src, StripScenePath, true) || !File.Exists(StripScenePath))
                FailBuild("QA-root strip FAILED: could not write the temp build scene " + StripScenePath
                    + " (roots found: " + string.Join(",", qaRoots.ToArray())
                    + "). Refusing to ship a build that would contain them.");
            AssetDatabase.Refresh();

            var tmp = EditorSceneManager.OpenScene(StripScenePath, OpenSceneMode.Additive);
            int destroyed = 0;
            foreach (var go in tmp.GetRootGameObjects())
                if (go != null && go.name.StartsWith(QARootPrefix, StringComparison.Ordinal))
                { UnityEngine.Object.DestroyImmediate(go); destroyed++; }
            bool saved = EditorSceneManager.SaveScene(tmp);
            EditorSceneManager.CloseScene(tmp, true);
            if (!saved || destroyed != qaRoots.Count)
                FailBuild("QA-root strip FAILED: saved=" + saved + " destroyed=" + destroyed + "/" + qaRoots.Count
                    + " in " + StripScenePath + ". Refusing to ship a build that would contain "
                    + string.Join(",", qaRoots.ToArray()) + ".");

            buildScenePath = StripScenePath;
            if (qaRoots.Count > 0)
                Debug.LogWarning("[BuildMacOSPlayer] stripped QA construction roots for THIS BUILD ONLY ("
                    + SceneToBuild + " on disk is unchanged): " + string.Join(",", qaRoots.ToArray()));
        }

        // Put the editor back on whatever scene it was showing (only reachable when it was clean).
        if (!string.IsNullOrEmpty(restorePath) && restorePath != SceneToBuild && File.Exists(restorePath))
            EditorSceneManager.OpenScene(restorePath, OpenSceneMode.Single);
        return qaRoots.ToArray();
    }

    // A precondition failure must be LOUD and must not leave a green-looking artifact behind: stamp the
    // build report red before throwing, so a consumer reading build-report.txt (qa_sandbox, the runbooks)
    // cannot mistake the previous run's stamp beside a stale .app for a successful build.
    static void FailBuild(string reason)
    {
        StampFailedReport(reason);
        Debug.LogError("[BuildMacOSPlayer] " + reason);
        throw new Exception("[BuildMacOSPlayer] " + reason);
    }

    // Best effort, and deliberately narrow: a report the build could not write must never become the
    // exception the caller sees in place of `reason`, so the two failure modes that actually occur here
    // (a read-only/locked BuildOutput, a full disk) are swallowed with a log. Anything else is a real
    // bug and is allowed to surface — the build still fails, which is the invariant that matters.
    static void StampFailedReport(string reason)
    {
        try
        {
            string outDir = Path.Combine(Directory.GetParent(Application.dataPath).FullName, OutputDir);
            Directory.CreateDirectory(outDir);
            File.WriteAllText(Path.Combine(outDir, ReportFileName),
                "result=Failed\ntotalErrors=1\nfailedPrecondition=" + reason + "\n");
        }
        catch (UnauthorizedAccessException e) { Debug.LogError("[BuildMacOSPlayer] could not write failure report: " + e.Message); }
        catch (IOException e) { Debug.LogError("[BuildMacOSPlayer] could not write failure report: " + e.Message); }
    }

    // #1674: shaders CombatSurfaceClient resolves at runtime via Shader.Find and that NO asset references, so
    // the player build strips them unless they are listed in Graphics -> Always-Included Shaders. The player
    // build MUST carry both or the runtime feature (occluder proxies / walk-behind silhouette) silently no-ops
    // in the shipped .app. Keep this list in sync with qa/check_always_included_shaders.py (the pre-flight gate).
    static readonly string[] RequiredAlwaysIncluded = { "WorldOS/OccluderDepth", "WorldOS/ActorSilhouette" };

    // Ensure every RequiredAlwaysIncluded shader is registered in Graphics -> Always-Included Shaders
    // (idempotent — mirrors W5bWireScene.EnsureAlwaysIncluded). Returns the resolved list for the build-report
    // evidence. A shader missing from the project (not yet imported) is logged but does NOT abort the build;
    // qa/check_always_included_shaders.py is the hard gate that fails the box build pre-flight in that case.
    static string[] EnsureAlwaysIncludedShaders()
    {
        var so = new SerializedObject(GraphicsSettings.GetGraphicsSettings());
        var arr = so.FindProperty("m_AlwaysIncludedShaders");
        var present = new List<string>();
        foreach (var name in RequiredAlwaysIncluded)
        {
            var sh = Shader.Find(name);
            if (sh == null) { Debug.LogWarning("[BuildMacOSPlayer] #1674 required shader NOT FOUND (import it before ship): " + name); continue; }
            bool already = false;
            for (int i = 0; i < arr.arraySize; i++)
                if (arr.GetArrayElementAtIndex(i).objectReferenceValue == sh) { already = true; break; }
            if (!already)
            {
                arr.InsertArrayElementAtIndex(arr.arraySize);
                arr.GetArrayElementAtIndex(arr.arraySize - 1).objectReferenceValue = sh;
                Debug.Log("[BuildMacOSPlayer] #1674 +Always-Included " + name);
            }
            present.Add(name);
        }
        so.ApplyModifiedProperties();
        AssetDatabase.SaveAssets();
        return present.ToArray();
    }
}
