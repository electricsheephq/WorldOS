#if UNITY_EDITOR
using UnityEngine;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine.SceneManagement;
using System.Collections.Generic;
using System.Linq;

/// <summary>
/// COHESION PROBE (docs/roadmap/COHESION-SEAM-DECISION.md) — the cumulative ladder that measures how much
/// each layer of the (already-built, offline-only) ClosedLoopBuilder cohesion stack buys when applied to the
/// CANONICAL combat scene: baseline -> +B plate-sampled light rig -> +D directional contact shadows ->
/// +A' PainterlyActor materials (CL r10-tuned params, plate-sampled colors). Each rung is a MenuItem
/// (box-drive contract: menu wrappers, no execute_code); capture a frame after each rung, panel-score the
/// set. Rungs are cumulative + idempotent; Reset reopens the scene unsaved. Scene-mutation only — nothing
/// here touches the shipped CombatSurfaceClient; the runtime port happens AFTER the panel verdict.
/// </summary>
public static class CohesionProbe
{
    // plate analysis shared by the rungs (B computes it; D/A' reuse).
    static Color _key = new Color(1f, 0.73f, 0.44f);
    static Color _amb = new Color(0.30f, 0.25f, 0.21f);
    static Vector3 _fromDir = new Vector3(-1f, 0f, 0f);   // horizontal dir of the light SOURCE from scene center
    static Vector3 _hearthAnchor;                          // floor point shadows are cast AWAY from
    static bool _analyzed;

    // ---------- rung B: plate-sampled per-scene light rig ----------
    [MenuItem("WorldOS/Cohesion Probe/1 - Rung B: plate-sampled light rig")]
    public static void RungB()
    {
        if (!Analyze()) return;
        var keyGo = GameObject.Find("KeyLight"); var fillGo = GameObject.Find("FillLight");
        if (keyGo == null || fillGo == null) { Debug.LogError("[PROBE] KeyLight/FillLight not found — is the canonical combat scene loaded?"); return; }
        var key = keyGo.GetComponent<Light>(); var fill = fillGo.GetComponent<Light>();
        key.color = _key; key.intensity = 1.5f; key.shadows = LightShadows.Soft; key.shadowStrength = 0.55f;
        keyGo.transform.rotation = Quaternion.LookRotation((-_fromDir + Vector3.down * 0.9f).normalized);
        fill.color = _amb; fill.intensity = 0.5f;
        fillGo.transform.rotation = Quaternion.LookRotation((_fromDir + Vector3.down * 0.6f).normalized);
        RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
        RenderSettings.ambientLight = _amb * 0.85f;
        Debug.Log("[PROBE] RungB applied: key " + ColorUtility.ToHtmlStringRGB(_key) + " fromDir " + _fromDir.ToString("F2")
                  + " amb " + ColorUtility.ToHtmlStringRGB(_amb));
    }

    // ---------- rung D: directional contact shadows (blob AO off) ----------
    [MenuItem("WorldOS/Cohesion Probe/2 - Rung D: directional contact shadows")]
    public static void RungD()
    {
        if (!Analyze()) return;
        int n = 0;
        foreach (var a in ActorRoots())
        {
            foreach (var suf in new[] { "_AO", "_Core" }) { var s = GameObject.Find(a.name + suf); if (s != null) s.SetActive(false); }
            var old = GameObject.Find(a.name + "_Cast"); if (old != null) Object.DestroyImmediate(old);
            var b = MeasureBounds(a);
            var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
            go.name = a.name + "_Cast"; Object.DestroyImmediate(go.GetComponent<Collider>());
            Vector3 away = a.transform.position - _hearthAnchor; away.y = 0f;
            if (away.sqrMagnitude < 1e-4f) away = new Vector3(1f, 0f, 0.3f);
            away.Normalize();
            float footX = Mathf.Clamp(b.size.x * 1.0f, 1.8f, 3.4f);
            float castLen = footX * 1.7f, footZ = footX * 0.55f;
            float yaw = Mathf.Atan2(away.x, away.z) * Mathf.Rad2Deg;
            float floorY = FloorYOf(a);
            Vector3 footPos = new Vector3(b.center.x, floorY + 0.02f, b.center.z - 0.3f);
            go.transform.localScale = new Vector3(footZ * 1.05f, castLen, 1f);
            go.transform.position = footPos + away * (castLen * 0.18f);
            go.transform.eulerAngles = new Vector3(90f, yaw, 0f);
            var sh = Shader.Find("WorldOS/ContactShadow");
            var m = new Material(sh != null ? sh : Shader.Find("Sprites/Default"));
            m.mainTexture = RadialTex(); m.color = new Color(0.05f, 0.03f, 0.02f, 0.78f);
            var r = go.GetComponent<Renderer>(); r.sharedMaterial = m; r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            n++;
        }
        Debug.Log("[PROBE] RungD applied: " + n + " directional cast shadows (blob AO/core disabled), away-from " + _hearthAnchor.ToString("F1"));
    }

    // ---------- rung A': PainterlyActor materials with the CL r10-tuned params ----------
    [MenuItem("WorldOS/Cohesion Probe/3 - Rung A': painterly actor materials")]
    public static void RungA()
    {
        if (!Analyze()) return;
        var sh = Shader.Find("WorldOS/PainterlyActor");
        if (sh == null) { Debug.LogError("[PROBE] WorldOS/PainterlyActor shader not found in project."); return; }
        var actors = ActorRoots();
        if (actors.Count == 0) { Debug.LogError("[PROBE] no Actor_* roots in scene."); return; }
        var cam = Camera.main;
        float zMin = float.MaxValue, zMax = float.MinValue;
        var zOf = new Dictionary<GameObject, float>();
        foreach (var a in actors)
        {
            float z = cam != null ? Vector3.Dot(a.transform.position - cam.transform.position, cam.transform.forward) : 0f;
            zOf[a] = z; zMin = Mathf.Min(zMin, z); zMax = Mathf.Max(zMax, z);
        }
        Vector3 keyDir = (_fromDir + Vector3.up * 0.22f).normalized;
        int n = 0;
        foreach (var a in actors)
        {
            bool isHero = a.name.ToLower().Contains("hero");
            float depth01 = (zMax - zMin) > 1e-3f ? 1f - Mathf.InverseLerp(zMin, zMax, zOf[a]) : 1f;  // 1=near, 0=far
            foreach (var r in a.GetComponentsInChildren<Renderer>())
            {
                var src = r.sharedMaterial; if (src == null) continue;
                var fmat = new Material(sh);
                if (src.HasProperty("_MainTex") && src.mainTexture != null) fmat.SetTexture("_MainTex", src.mainTexture);
                fmat.SetColor("_BaseColor", Color.white);
                fmat.SetColor("_KeyColor", _key);
                fmat.SetColor("_AmbientColor", _amb);
                fmat.SetVector("_KeyDir", keyDir);
                // the ClosedLoopBuilder r10 consensus values (ClosedLoopBuilder.cs:1013-1078), verbatim.
                fmat.SetFloat("_KeyStrength", isHero ? Mathf.Lerp(1.35f, 1.55f, depth01) : 1.2f);
                fmat.SetFloat("_RimStrength", isHero ? 0.16f : 0.20f);
                fmat.SetFloat("_Desat", isHero ? 0.24f : 0.36f);
                fmat.SetFloat("_BounceStrength", Mathf.Lerp(0.10f, 0.22f, depth01));
                fmat.SetFloat("_Kuwahara", isHero ? 4.0f : 5.5f);
                fmat.SetFloat("_Posterize", isHero ? 5.0f : 4.0f);
                fmat.SetFloat("_BrushStrength", isHero ? 0.22f : 0.04f);
                fmat.SetFloat("_BrushScale", isHero ? 15.0f : 11.0f);
                fmat.SetFloat("_EdgeSoften", isHero ? 0.22f : 0.30f);
                fmat.SetFloat("_PaletteSnap", isHero ? 0.42f : 0.55f);
                fmat.SetFloat("_PaintLift", 0.06f);
                fmat.SetFloat("_AmbientLift", isHero ? 0.16f : 0.20f);
                fmat.SetFloat("_MaxLuma", isHero ? 0.78f : 0.56f);
                fmat.SetFloat("_TermSharp", 0.30f);
                float atm = Mathf.Clamp01((1f - depth01) * 0.85f);
                fmat.SetFloat("_AtmDepth", isHero ? atm * 0.35f : atm);
                fmat.SetColor("_AtmColor", _amb * 1.4f);
                r.sharedMaterial = fmat;
            }
            n++;
        }
        Debug.Log("[PROBE] RungA' applied: PainterlyActor on " + n + " actors, keyDir " + keyDir.ToString("F2"));
    }

    [MenuItem("WorldOS/Cohesion Probe/0 - Reset (reopen scene, discard)")]
    public static void ResetScene()
    {
        var sc = SceneManager.GetActiveScene();
        EditorSceneManager.OpenScene(sc.path);
        _analyzed = false;
        Debug.Log("[PROBE] scene reopened (all rungs discarded): " + sc.path);
    }

    // ---------- plate analysis ----------
    static bool Analyze()
    {
        if (_analyzed) return true;
        var bd = GameObject.Find("PaintedBackdrop");
        var mat = bd != null ? bd.GetComponent<Renderer>().sharedMaterial : null;
        var tex = mat != null ? mat.mainTexture as Texture2D : null;
        if (tex == null) { Debug.LogError("[PROBE] PaintedBackdrop plate texture not found."); return false; }
        // readable downsample via RT blit (works regardless of import flags).
        var rt = RenderTexture.GetTemporary(256, 256, 0, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
        Graphics.Blit(tex, rt);
        var prev = RenderTexture.active; RenderTexture.active = rt;
        varsmall = new Texture2D(256, 256, TextureFormat.RGBA32, false);
        small.ReadPixels(new Rect(0, 0, 256, 256), 0, 0); small.Apply();
        RenderTexture.active = prev; RenderTexture.ReleaseTemporary(rt);
        var px = small.GetPixels();
        var lum = px.Select(c => 0.299f * c.r + 0.587f * c.g + 0.114f * c.b).ToArray();
        var sorted = (float[])lum.Clone(); System.Array.Sort(sorted);
        float p75 = sorted[(int)(sorted.Length * 0.75f)], p40 = sorted[(int)(sorted.Length * 0.40f)], p90 = sorted[(int)(sorted.Length * 0.90f)];
        _key = MedianColor(px, lum, p75, float.MaxValue);
        _amb = MedianColor(px, lum, -1f, p40);
        // bright-region centroid -> which screen side the key light lives on.
        double cu = 0; int cn = 0;
        for (int i = 0; i < px.Length; i++) if (lum[i] >= p90) { cu += (i % 256) / 255.0; cn++; }
        float u = cn > 0 ? (float)(cu / cn) : 0.5f;
        var cam = Camera.main;
        Vector3 camRight = cam != null ? cam.transform.right : Vector3.right; camRight.y = 0f; camRight.Normalize();
        // NOTE the display flip: paint_combat_v1 rotates the plate quad 180° AND mirrors U — the two cancel
        // (ClosedLoopBuilder key-dir recipe), so plate-U maps to screen/world right directly.
        _fromDir = camRight * Mathf.Sign(u - 0.5f);
        Vector3 center = SceneCenter();
        _hearthAnchor = center + _fromDir * 12f;
        _analyzed = true;
        Object.DestroyImmediate(small);
        Debug.Log("[PROBE] plate analyzed: key " + ColorUtility.ToHtmlStringRGB(_key) + " amb " + ColorUtility.ToHtmlStringRGB(_amb)
                  + " brightU " + u.ToString("F2") + " fromDir " + _fromDir.ToString("F2") + " hearth " + _hearthAnchor.ToString("F1"));
        return true;
    }

    static Color MedianColor(Color[] px, float[] lum, float lo, float hi)
    {
        var rs = new List<float>(); var gs = new List<float>(); var bs = new List<float>();
        for (int i = 0; i < px.Length; i++) if (lum[i] > lo && lum[i] < hi) { rs.Add(px[i].r); gs.Add(px[i].g); bs.Add(px[i].b); }
        if (rs.Count == 0) return Color.gray;
        rs.Sort(); gs.Sort(); bs.Sort();
        return new Color(rs[rs.Count / 2], gs[gs.Count / 2], bs[bs.Count / 2]);
    }

    // ---------- scene helpers ----------
    static readonly string[] Sufs = { "_AO", "_Ring", "_Core", "_Cast", "_HP", "_bg", "_fg" };
    static List<GameObject> ActorRoots()
    {
        var outl = new List<GameObject>();
        foreach (var go in Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None))
        {
            if (!go.activeInHierarchy || !go.name.StartsWith("Actor_")) continue;
            if (Sufs.Any(s => go.name.EndsWith(s))) continue;
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
    static float FloorYOf(GameObject a)
    {
        var ring = GameObject.Find(a.name + "_Ring");
        if (ring != null) return ring.transform.position.y - 0.06f;
        return MeasureBounds(a).min.y;
    }
    static Vector3 SceneCenter()
    {
        var roots = ActorRoots();
        if (roots.Count == 0) return Vector3.zero;
        Vector3 c = Vector3.zero; foreach (var a in roots) c += a.transform.position;
        return c / roots.Count;
    }
    static Texture2D _radial;
    static Texture2D RadialTex()
    {
        if (_radial != null) return _radial;
        const int N = 256;
        _radial = new Texture2D(N, N, TextureFormat.RGBA32, false);
        for (int y = 0; y < N; y++) for (int x = 0; x < N; x++)
        {
            float dx = (x - N / 2f) / (N / 2f), dy = (y - N / 2f) / (N / 2f);
            float d = Mathf.Sqrt(dx * dx + dy * dy);
            float aA = Mathf.Pow(Mathf.Clamp01(1f - d), 1.6f);
            _radial.SetPixel(x, y, new Color(1f, 1f, 1f, aA));
        }
        _radial.Apply();
        return _radial;
    }
}
#endif
