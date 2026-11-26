using UnityEngine;
using UnityEditor;
using System.IO;

/// <summary>
/// TripoLibraryImport — one-shot importer configuration for the #1628 BG3-style demo asset
/// library (68 Tripo3D assets: 33 rigged cast/monsters + 35 static props).
///
/// CRITICAL Tripo-vs-Meshy difference (verified 2026-06-28, TRIPO_PIPELINE.md): Tripo bone
/// names (tripo::Spine_0, ...) do NOT auto-map to Unity's Humanoid avatar — a Humanoid import
/// SILENTLY DROPS the animation clips (clipAnims=0). So unlike CharsV3Import (Meshy, Humanoid),
/// everything here imports as **Generic / NoAvatar**, which preserves the embedded takes.
///
/// What it does per asset under Assets/cast/<id>/ and Assets/props/<id>/:
///   <id>.fbx        -> Generic, importAnimation on, materials InPrefab
///   anim_<name>.fbx -> Generic + the single take renamed to <Name>; Walk/Idle/Run loop
///   albedo.jpg/png  -> a saved Material asset (<id>_mat.mat, Standard shader, _MainTex = albedo)
///                      so the renderer/registry can bind it at runtime (Tripo FBX imports
///                      UNTEXTURED — the albedo lives in the source GLB and was pre-extracted
///                      Mac-side via extract_glb_albedo.py).
///
/// Idempotent: safe to re-run; it only rewrites importer settings and material assets.
/// </summary>
public static class TripoLibraryImport
{
    [MenuItem("Tools/WorldOS/Tripo Library/Configure #1628 library (Generic + clips + albedo mats)")]
    public static void Configure()
    {
        int models = 0, clips = 0, mats = 0;
        foreach (var root in new[] { "Assets/cast", "Assets/props" })
        {
            if (!Directory.Exists(root)) { Debug.LogWarning("[TripoLib] missing " + root); continue; }
            foreach (var dir in Directory.GetDirectories(root))
            {
                string id = Path.GetFileName(dir);
                string model = Path.Combine(dir, id + ".fbx");
                if (File.Exists(model)) { ConfigureModel(model); models++; }
                foreach (var anim in Directory.GetFiles(dir, "anim_*.fbx"))
                {
                    string clip = Path.GetFileNameWithoutExtension(anim).Substring("anim_".Length);
                    ConfigureClip(anim, clip, IsLoop(clip));
                    clips++;
                }
                mats += EnsureAlbedoMaterial(dir, id) ? 1 : 0;
            }
        }
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
        Debug.Log(string.Format("[TripoLib] configured {0} models, {1} clips, {2} albedo materials", models, clips, mats));
    }

    static bool IsLoop(string clip)
    {
        var c = clip.ToLowerInvariant();
        return c == "walk" || c == "idle" || c == "run";
    }

    static void ConfigureModel(string path)
    {
        var imp = AssetImporter.GetAtPath(path) as ModelImporter;
        if (imp == null) { Debug.LogWarning("[TripoLib] not a model: " + path); return; }
        imp.animationType = ModelImporterAnimationType.Generic; // NEVER Humanoid — drops Tripo clips
        imp.avatarSetup = ModelImporterAvatarSetup.NoAvatar;
        imp.importAnimation = true;
        imp.importBlendShapes = false;
        imp.materialImportMode = ModelImporterMaterialImportMode.ImportStandard;
        imp.materialLocation = ModelImporterMaterialLocation.InPrefab;
        imp.SaveAndReimport();
    }

    static void ConfigureClip(string path, string clipName, bool loop)
    {
        var imp = AssetImporter.GetAtPath(path) as ModelImporter;
        if (imp == null) { Debug.LogWarning("[TripoLib] not a model: " + path); return; }
        imp.animationType = ModelImporterAnimationType.Generic;
        imp.avatarSetup = ModelImporterAvatarSetup.NoAvatar;
        imp.importAnimation = true;
        var takes = imp.defaultClipAnimations;
        if (takes != null && takes.Length > 0)
        {
            for (int i = 0; i < takes.Length; i++)
            {
                takes[i].name = clipName;
                takes[i].loopTime = loop;
            }
            imp.clipAnimations = takes;
        }
        imp.SaveAndReimport();
    }

    static bool EnsureAlbedoMaterial(string dir, string id)
    {
        string texPath = null;
        foreach (var ext in new[] { "albedo.jpg", "albedo.png" })
        {
            var p = Path.Combine(dir, ext);
            if (File.Exists(p)) { texPath = p; break; }
        }
        if (texPath == null) return false;
        string matPath = Path.Combine(dir, id + "_mat.mat");
        var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(texPath);
        if (tex == null) return false;
        var mat = AssetDatabase.LoadAssetAtPath<Material>(matPath);
        if (mat == null)
        {
            mat = new Material(Shader.Find("Standard"));
            AssetDatabase.CreateAsset(mat, matPath);
        }
        mat.mainTexture = tex;
        EditorUtility.SetDirty(mat);
        return true;
    }
}
