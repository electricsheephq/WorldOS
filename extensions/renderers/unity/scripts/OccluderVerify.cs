#if UNITY_EDITOR
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using System.Collections.Generic;

/// <summary>
/// W6.1 (#1460) — OCCLUDER-vs-PainterlyActor ZTest VERIFICATION harness (editor-only; excluded from the
/// player assembly). The W6 design doc flagged the interplay as UNVERIFIED: PainterlyActor is a
/// Transparent-queue 2-pass shader (a ZWrite depth-prime pass + an alpha-blended color pass, both
/// ZTest LEqual); the runtime occluder is WorldOS/OccluderDepth — Queue=Geometry-1, ColorMask 0,
/// ZWrite On. This proves the interplay EMPIRICALLY on the GEX44 box (box-drive contract: MenuItem
/// wrappers only, no execute_code):
///
///   REASONED EXPECTATION (what the capture must confirm): the occluder renders in the OPAQUE phase
///   (Geometry-1 = 1999, before the Transparent actors at 3000) and writes DEPTH but no color. On
///   Metal TBDR opaque depth (queue < 2500) is retained into the transparent phase. An actor fragment
///   BEHIND the occluder therefore has greater depth than the occluder's already-written depth, so both
///   the PainterlyActor color pass (ZTest LEqual, ZWrite Off) AND its depth-prime pass FAIL the depth
///   test there and the fragment is discarded — the actor is HIDDEN, revealing the painted plate behind
///   the invisible slab. A Standard-material (opaque, Queue Geometry) actor is hidden the same way. If
///   the occluder shader were the buggy visible black box (the OccluderDepthOnly name bug) you'd instead
///   see a solid dark slab, not the backdrop.
///
/// Flow: "1 - Setup" forces one actor to PainterlyActor + the rest to Standard so BOTH material paths
/// are on screen, no occluder — CAPTURE this (baseline: actors visible). "2 - Add occluder slabs" drops
/// an invisible WorldOS/OccluderDepth slab between the camera and each actor — CAPTURE this (actors gone).
/// "0 - Reset" reopens the scene unsaved. Scene-mutation only; nothing here is saved or touches the
/// shipped CombatSurfaceClient.
/// </summary>
public static class OccluderVerify
{
    const string ScenePath = "Assets/Scenes/M1CombatV1_canonical.unity";
    const string OccShaderName = "WorldOS/OccluderDepth";
    const string PainterlyShaderName = "WorldOS/PainterlyActor";

    [MenuItem("Tools/WorldOS/W6.1/1 - Setup (painterly + standard actor, no occluder)")]
    public static void Setup()
    {
        EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
        ClearVerifyOccluders();
        var actors = ActorRoots();
        if (actors.Count == 0) { Debug.LogError("[OCCV] no Actor_* roots in " + ScenePath); return; }
        var pa = Shader.Find(PainterlyShaderName);
        var std = Shader.Find("Standard");
        // actor[0] -> PainterlyActor (Transparent 2-pass); actor[1..] -> Standard (opaque). Both paths
        // must be occluded by the SAME slab pass, so force one of each when the scene has >=2 actors.
        for (int ai = 0; ai < actors.Count; ai++)
        {
            bool wantPainterly = (ai == 0 && pa != null);
            var sh = wantPainterly ? pa : std;
            foreach (var r in actors[ai].GetComponentsInChildren<Renderer>())
            {
                var src = r.sharedMaterial;
                var m = new Material(sh);
                if (src != null && src.mainTexture != null) m.mainTexture = src.mainTexture;
                if (wantPainterly)
                {
                    // visible defaults so the baseline actor is unambiguously present pre-occluder.
                    m.SetColor("_BaseColor", Color.white);
                    m.SetFloat("_KeyStrength", 1.6f);
                    m.SetFloat("_AmbientLift", 0.32f);
                    m.SetFloat("_MaxLuma", 0.85f);
                }
                else { m.SetFloat("_Glossiness", 0.2f); m.SetFloat("_Metallic", 0f); }
                r.sharedMaterial = m;
            }
            Debug.Log("[OCCV] actor " + actors[ai].name + " -> " + (wantPainterly ? PainterlyShaderName : "Standard"));
        }
        Debug.Log("[OCCV] setup done: " + actors.Count + " actors, no occluder. CAPTURE baseline now (actors visible).");
    }

    [MenuItem("Tools/WorldOS/W6.1/2 - Add occluder slabs IN FRONT of actors")]
    public static void AddOccluders()
    {
        var occShader = Shader.Find(OccShaderName);
        if (occShader == null) { Debug.LogError("[OCCV] " + OccShaderName + " not found — import Assets/Shaders/OccluderDepth.shader first."); return; }
        var cam = Camera.main;
        if (cam == null) { Debug.LogError("[OCCV] no Camera.main in scene."); return; }
        ClearVerifyOccluders();
        var mat = new Material(occShader);   // depth-only material; inherits the shader's Geometry-1 queue
        int n = 0;
        foreach (var a in ActorRoots())
        {
            var b = MeasureBounds(a);
            // Slab BETWEEN the camera and the actor: push the actor-center toward the camera along the
            // view axis (-forward), so its depth is NEARER than the actor across the shared silhouette.
            Vector3 pos = b.center - cam.transform.forward * 3.0f;
            var box = GameObject.CreatePrimitive(PrimitiveType.Cube);
            box.name = "Occluder_verify_" + a.name;
            Object.DestroyImmediate(box.GetComponent<Collider>());
            box.transform.position = pos;
            box.transform.rotation = Quaternion.LookRotation(cam.transform.forward, Vector3.up); // face the camera
            // over-cover the actor's screen silhouette (a touch wider/taller than its bounds); thin in Z.
            float wide = Mathf.Max(b.size.x, b.size.z) * 1.6f + 1.0f;
            float tall = b.size.y * 1.4f + 1.0f;
            box.transform.localScale = new Vector3(wide, tall, 0.5f);
            var r = box.GetComponent<Renderer>();
            r.sharedMaterial = mat;
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off; r.receiveShadows = false;
            n++;
        }
        Debug.Log("[OCCV] added " + n + " invisible " + OccShaderName + " slabs between camera and each actor. Actors should now be HIDDEN. CAPTURE now.");
    }

    [MenuItem("Tools/WorldOS/W6.1/0 - Reset (reopen scene, discard)")]
    public static void Reset()
    {
        var sc = SceneManager.GetActiveScene();
        if (!string.IsNullOrEmpty(sc.path)) EditorSceneManager.OpenScene(sc.path);
        Debug.Log("[OCCV] scene reopened (verify occluders + material overrides discarded).");
    }

    static void ClearVerifyOccluders()
    {
        foreach (var go in Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None))
            if (go != null && go.name.StartsWith("Occluder_verify_")) Object.DestroyImmediate(go);
    }

    // Actor-root discovery mirrors CohesionProbe.ActorRoots (Actor_* roots, excluding sibling props +
    // nested child renderers) so the verify slabs target the same actors the runtime path repositions.
    static readonly string[] Sufs = { "_AO", "_Ring", "_Core", "_Cast", "_HP", "_bg", "_fg" };
    static List<GameObject> ActorRoots()
    {
        var outl = new List<GameObject>();
        foreach (var go in Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None))
        {
            if (go == null || !go.activeInHierarchy || !go.name.StartsWith("Actor_")) continue;
            bool suf = false; foreach (var s in Sufs) if (go.name.EndsWith(s)) { suf = true; break; }
            if (suf) continue;
            if (go.transform.parent != null && go.transform.parent.name.StartsWith("Actor_")) continue;
            outl.Add(go);
        }
        return outl;
    }

    static Bounds MeasureBounds(GameObject a)
    {
        var rends = a.GetComponentsInChildren<Renderer>();
        if (rends.Length == 0) return new Bounds(a.transform.position, Vector3.one);
        Bounds b = rends[0].bounds; foreach (var r in rends) b.Encapsulate(r.bounds);
        return b;
    }
}
#endif
