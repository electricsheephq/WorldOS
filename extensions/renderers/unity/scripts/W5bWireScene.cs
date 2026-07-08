#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

/// <summary>
/// W5b (#1433) — one-shot scene migration MenuItem (editor-only; excluded from the player assembly).
/// Run from the live headed editor (execute_menu_item), NOT -batchmode (BOX.md #1196). It closes the
/// two gaps between the running WorldOSPlayer.app and the T3 gate, WITHOUT re-running the full
/// paint_combat_v1 capture (so no live-engine dependency and byte-identical color output):
///
///  1) MAGENTA FIX: reassign every Occluder_* renderer's material to the COMMITTED
///     WorldOS/OccluderDepth asset. The scene shipped these materials pointing at a runtime
///     ShaderUtil.CreateShaderAsset shader (serialized inline), which is not compiled into a
///     standalone player build -> pink error shader = the magenta blocks (#1433). The committed
///     asset is depth-only/ColorMask-0 (identical source), so color output is unchanged.
///  2) Add WorldOS/OccluderDepth to Always-Included Shaders (belt-and-braces vs variant stripping).
///  3) WIRING: attach CombatSurfaceClient to a runtime GameObject so the player consumes
///     /combat-surface + /events live and POSTs /move (it resolves actors by the Actor_<token.id>
///     registry naming). The client only acts at RUNTIME, so edit-mode captures are unaffected.
///  4) Save the scene.
/// </summary>
public static class W5bWireScene
{
    const string ScenePath = "Assets/Scenes/M1CombatV1_canonical.unity";
    const string OccShaderName = "WorldOS/OccluderDepth";

    [MenuItem("Tools/WorldOS/W5b/Wire scene (occluder shader + CombatSurfaceClient)")]
    public static void Run()
    {
        var occShader = Shader.Find(OccShaderName);
        if (occShader == null) { Debug.LogError("[W5b] committed shader not found: " + OccShaderName + " (import Assets/Shaders/OccluderDepth.shader first)"); return; }

        var scene = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);

        // 1) reassign occluder materials to the committed shader
        int occFixed = 0;
        foreach (var go in Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None))
        {
            if (go == null || !go.name.StartsWith("Occluder_")) continue;
            var r = go.GetComponent<Renderer>();
            if (r == null) continue;
            foreach (var m in r.sharedMaterials)
                if (m != null && m.shader != occShader) { m.shader = occShader; occFixed++; }
        }

        // 2) Always-Included Shaders
        bool addedAlways = EnsureAlwaysIncluded(occShader);

        // 3) attach CombatSurfaceClient (idempotent)
        var host = GameObject.Find("CombatSurfaceClient");
        if (host == null) host = new GameObject("CombatSurfaceClient");
        bool attached = false;
        if (host.GetComponent<CombatSurfaceClient>() == null) { host.AddComponent<CombatSurfaceClient>(); attached = true; }

        // 4) save
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene, ScenePath);
        AssetDatabase.SaveAssets();
        Debug.Log("[W5b] DONE occMaterialsFixed=" + occFixed + " alwaysIncludedAdded=" + addedAlways + " clientAttached=" + attached);
    }

    static bool EnsureAlwaysIncluded(Shader sh)
    {
        var so = new SerializedObject(GraphicsSettings.GetGraphicsSettings());
        var arr = so.FindProperty("m_AlwaysIncludedShaders");
        for (int i = 0; i < arr.arraySize; i++)
            if (arr.GetArrayElementAtIndex(i).objectReferenceValue == sh) return false;
        arr.InsertArrayElementAtIndex(arr.arraySize);
        arr.GetArrayElementAtIndex(arr.arraySize - 1).objectReferenceValue = sh;
        so.ApplyModifiedProperties();
        return true;
    }
}
#endif
