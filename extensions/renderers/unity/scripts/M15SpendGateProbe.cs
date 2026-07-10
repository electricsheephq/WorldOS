#if UNITY_EDITOR
using UnityEngine;
using UnityEditor;
using System.Collections.Generic;

/// <summary>
/// M1.5 ASSET SPEND-GATE PROBE (unity-asset-stack skill's "M1.5 spend-gate"; issue #1386, the
/// PLATE SPRINT / ADOPT-CRYPT charter). Validates the FATAL pre-spend gate: 6 FREE capsule
/// primitives (3-party + 3-foe formation, zero additional cost) + 1 owned/free Hovl VFX
/// composited on the ADOPTED crypt plate (crypt_armb_iter3_v1.png, M1CombatV1_canonical scene)
/// must hold multi-unit grounding/occlusion + read as a legible in-plate VFX BEFORE any new
/// asset spend beyond the owned stack (asset-scout packet ~/worldos-session-notes/
/// asset-scout-2026-07-10.md). Placement cells reuse qa/seed_gfx_combat.py's #1386
/// PROBE-PLACEMENT re-calibration (pillars (2,4)/(9,9), sarcophagus cols3-9 x rows3-7) so all 6
/// units + the VFX sit on verified-clear floor. Scene-mutation only (no SaveScene) — restore via
/// Tools/WorldOS/Cohesion Probe/0 - Reset (CohesionProbe.cs; same scene, any-rung discard).
/// </summary>
public static class M15SpendGateProbe
{
    // Hovl Studio is a fully OWNED pack (unity-asset-stack skill); this loop-version magic-circle
    // prefab is a persistent (non-one-shot) VFX so it reads in a STATIC capture, unlike the
    // Flash-and-hit one-shots that finish playing before a screenshot lands.
    const string HovlPrefabPath = "Assets/Hovl Studio/AAA Projectiles Vol 1/Prefabs/Flash and hits/Hit 16 fire.prefab";
    const string OutDir = "/home/unity/worldos-unity/Captures-Durable";

    struct Unit { public string name; public int cx, cy; public bool foe; public Unit(string n, int x, int y, bool f) { name = n; cx = x; cy = y; foe = f; } }

    // Party (gold/cyan ring) near the probe-verified HERO_CELL(11,3); foes (red ring) near the
    // probe-verified GOBLIN_CELL(1,8) — both clusters confirmed clear of the sarcophagus/pillars
    // by the #1386 PROBE-PLACEMENT lane (qa/seed_gfx_combat.py module docstring).
    static readonly Unit[] Units = new[] {
        new Unit("Cap_Party1", 11, 3, false),
        new Unit("Cap_Party2", 12, 3, false),
        new Unit("Cap_Party3", 11, 2, false),
        new Unit("Cap_Foe1",    1, 8, true),
        new Unit("Cap_Foe2",    2, 8, true),
        new Unit("Cap_Foe3",    1, 7, true),
    };

    static Vector3 CellToWorld(int c, int r) => new Vector3((c - 6.5f) * 2.0f, 0f, (5f - r) * 2.0f);

    const string CryptPlatePath = "Assets/painterly/backdrops/crypt_armb_iter3_v1.png";

    // The SAVED M1CombatV1_canonical.unity persists whatever PaintedBackdrop a prior LIVE-combat
    // render last baked in (paint_combat_v1.cs SaveScene's the canonical scene after every render,
    // #1489-era note) -- so the scene's backdrop can drift to whatever plate a prior lane (e.g. a
    // post-cross_door camp frame) last rendered. Force it back to the ADOPTED crypt plate before
    // gating (this dispatch's explicit test surface), mirroring paint_combat_v1.cs's own
    // Unlit/Texture assignment convention (line ~87) so the swap is byte-consistent with the LIVE path.
    [MenuItem("Tools/WorldOS/M1.5 Spend Gate/preset - Force backdrop to crypt_armb_iter3_v1")]
    public static void ForceCryptBackdrop()
    {
        var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(CryptPlatePath);
        if (tex == null) { Debug.LogError("[M15GATE] crypt plate not found: " + CryptPlatePath); return; }
        var bd = GameObject.Find("PaintedBackdrop");
        if (bd == null) { Debug.LogError("[M15GATE] PaintedBackdrop not found in the loaded scene."); return; }
        var r = bd.GetComponent<Renderer>();
        var m = new Material(Shader.Find("Unlit/Texture"));
        m.mainTexture = tex; m.renderQueue = 1900;
        r.sharedMaterial = m; r.enabled = true;
        Debug.Log("[M15GATE] PaintedBackdrop forced -> " + CryptPlatePath);
    }

    // The SAVED canonical scene also accumulates stale live-combat/camp cast objects from prior
    // lanes (the ADOPT-CRYPT release notes disabled "36 stale camp-occluder/live-combat-cast
    // objs" the same way) -- disable any leftover "Actor_*" roots so the gate frame reads as a
    // clean 6-capsule composite, not a mixed capsule+real-mesh frame.
    [MenuItem("Tools/WorldOS/M1.5 Spend Gate/preset - Disable stale Actor_* cast objects")]
    public static void DisableStaleActors()
    {
        int n = 0;
        foreach (var go in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None))
        {
            if (go.transform.parent != null) continue; // roots only
            if (!go.activeSelf) continue;
            if (go.name.StartsWith("Actor_") && go.activeSelf) { go.SetActive(false); n++; Debug.Log("[M15GATE] disabled stale: " + go.name); }
        }
        Debug.Log("[M15GATE] disabled " + n + " stale Actor_* root(s)");
    }

    [MenuItem("Tools/WorldOS/M1.5 Spend Gate/0 - Populate 6 capsules + 1 Hovl VFX")]
    public static void Populate()
    {
        foreach (var u in Units) DestroyExisting(u.name);
        DestroyExisting("M15_VFX");

        foreach (var u in Units) SpawnCapsule(u);

        var vfxPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(HovlPrefabPath);
        if (vfxPrefab == null) { Debug.LogError("[M15GATE] Hovl VFX prefab not found: " + HovlPrefabPath); return; }
        // (2,7): open floor immediately beside the foe cluster (1,8)/(2,8)/(1,7) but NOT under any
        // capsule footprint (a first placement directly on Cap_Foe1's cell was visually swallowed by
        // the capsule's own body/radius) -- clear of SARCOPHAGUS_CELLS (cols3-9) and both pillars.
        var wp = CellToWorld(2, 7);
        var vfx = (GameObject)Object.Instantiate(vfxPrefab);
        vfx.name = "M15_VFX";
        vfx.transform.position = new Vector3(wp.x, 0.05f, wp.z);
        // Editor-scene (non-play-mode) captures don't tick ParticleSystems -- a freshly Instantiate'd
        // prefab renders its t=0 (often near-empty) bind state, invisible in a still screenshot. Force
        // every child emitter to a warmed-up mid-loop state (paint_combat_scene.cs's VFX only worked
        // because it's a short one-shot flash captured immediately post-Instantiate; a LOOPING magic
        // circle needs an explicit Simulate() to read as "on" in a static capture).
        int nps = 0;
        foreach (var ps in vfx.GetComponentsInChildren<ParticleSystem>(true)) { ps.Simulate(0.08f, true, true, true); nps++; }
        Debug.Log("[M15GATE] populated 6 capsules (3 party + 3 foe) + 1 Hovl VFX (" + HovlPrefabPath + ") beside the foe cluster (2,7), warmed " + nps + " ParticleSystem(s)");
    }

    static void DestroyExisting(string nm)
    {
        foreach (var suf in new[] { "", "_AO", "_Ring" }) { var o = GameObject.Find(nm + suf); if (o != null) Object.DestroyImmediate(o); }
    }

    static void SpawnCapsule(Unit u)
    {
        var go = GameObject.CreatePrimitive(PrimitiveType.Capsule);
        go.name = u.name;
        float targetH = u.foe ? 4.2f : 3.2f; // matches the registry's monster/character target heights (#1418 recalibration)
        go.transform.localScale = Vector3.one * (targetH / 2.0f); // built-in Capsule is 2 units tall by default
        var cell = CellToWorld(u.cx, u.cy);
        go.transform.position = new Vector3(cell.x, targetH / 2.0f, cell.z); // feet on FLOOR_Y=0 (capsule pivot = geometric center)
        var mat = new Material(Shader.Find("Standard"));
        mat.color = u.foe ? new Color(0.75f, 0.16f, 0.13f) : new Color(0.85f, 0.72f, 0.22f); // saturated red foe / gold party (M-C convention)
        mat.SetFloat("_Glossiness", 0.2f); mat.SetFloat("_Metallic", 0f);
        go.GetComponent<Renderer>().sharedMaterial = mat;
        MakeGroundQuad(u.name + "_AO", cell, 2.0f, RadialTex(), Color.white, 1950);
        MakeGroundQuad(u.name + "_Ring", cell, 2.6f, RingTex(), u.foe ? new Color(1f, 0.13f, 0.10f, 1f) : new Color(0.4f, 0.95f, 1f, 1f), 1955);
    }

    static void MakeGroundQuad(string name, Vector3 cell, float scale, Texture2D tex, Color col, int queue)
    {
        var q = GameObject.CreatePrimitive(PrimitiveType.Quad); q.name = name;
        Object.DestroyImmediate(q.GetComponent<Collider>());
        q.transform.position = new Vector3(cell.x, 0.04f + (queue - 1950) * 0.01f, cell.z);
        q.transform.eulerAngles = new Vector3(90f, 0f, 0f);
        q.transform.localScale = new Vector3(scale, scale, 1f);
        var m = new Material(Shader.Find("Unlit/Transparent"));
        m.mainTexture = tex; m.color = col; m.renderQueue = queue;
        var r = q.GetComponent<Renderer>(); r.sharedMaterial = m; r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
    }

    static Texture2D _radial, _ring;
    static Texture2D RadialTex()
    {
        if (_radial != null) return _radial;
        const int N = 64;
        _radial = new Texture2D(N, N, TextureFormat.RGBA32, false);
        for (int y = 0; y < N; y++) for (int x = 0; x < N; x++)
        {
            float dx = (x - N / 2f) / (N / 2f), dy = (y - N / 2f) / (N / 2f);
            float d = Mathf.Sqrt(dx * dx + dy * dy);
            float a = Mathf.Clamp01(1f - d) * 0.5f;
            _radial.SetPixel(x, y, new Color(0f, 0f, 0f, a));
        }
        _radial.Apply();
        return _radial;
    }
    static Texture2D RingTex()
    {
        if (_ring != null) return _ring;
        const int N = 64;
        _ring = new Texture2D(N, N, TextureFormat.RGBA32, false);
        for (int y = 0; y < N; y++) for (int x = 0; x < N; x++)
        {
            float dx = (x - N / 2f) / (N / 2f), dy = (y - N / 2f) / (N / 2f);
            float d = Mathf.Sqrt(dx * dx + dy * dy);
            float a = (d > 0.78f && d < 0.93f) ? 1f : 0f;
            _ring.SetPixel(x, y, new Color(1f, 1f, 1f, a));
        }
        _ring.Apply();
        return _ring;
    }

    [MenuItem("Tools/WorldOS/M1.5 Spend Gate/diag - Inspect VFX renderers")]
    public static void DiagVfx()
    {
        var vfx = GameObject.Find("M15_VFX");
        if (vfx == null) { Debug.LogError("[M15GATE][DIAG] M15_VFX not found -- run Populate first."); return; }
        Debug.Log("[M15GATE][DIAG] M15_VFX pos=" + vfx.transform.position + " activeInHierarchy=" + vfx.activeInHierarchy);
        int i = 0;
        foreach (var r in vfx.GetComponentsInChildren<Renderer>(true))
        {
            var mat = r.sharedMaterial;
            string shaderName = mat != null && mat.shader != null ? mat.shader.name : "(null)";
            Debug.Log("[M15GATE][DIAG] renderer[" + i + "]=" + r.gameObject.name + " enabled=" + r.enabled
                + " activeInHierarchy=" + r.gameObject.activeInHierarchy + " bounds=" + r.bounds
                + " shader=" + shaderName + " matNull=" + (mat == null));
            i++;
        }
        int j = 0;
        foreach (var ps in vfx.GetComponentsInChildren<ParticleSystem>(true))
        {
            Debug.Log("[M15GATE][DIAG] ps[" + j + "]=" + ps.gameObject.name + " particleCount=" + ps.particleCount
                + " isPlaying=" + ps.isPlaying + " isEmitting=" + ps.isEmitting);
            j++;
        }
        Debug.Log("[M15GATE][DIAG] total renderers=" + i + " particleSystems=" + j);
    }

    // ---- pregate manifest: real WorldToViewportPoint-projected screen_bbox per capsule (rigid
    // primitive == plain Renderer.bounds, no BakeMesh needed) at the qa/visual_pregate.py
    // CameraSpec.LOCKED contract resolution (1920x1097) -- resolution-INDEPENDENT of whatever
    // super_size the actual capture used, since screen_bbox is viewport-normalized then scaled to
    // this declared frame_w/frame_h (same convention as paint_combat_v1.cs's #1408 manifest). ----
    [MenuItem("Tools/WorldOS/M1.5 Spend Gate/1 - Write pregate manifest (screen_bbox)")]
    public static void WriteManifest()
    {
        var cam = Camera.main;
        if (cam == null) { Debug.LogError("[M15GATE] no Camera.main"); return; }
        int W = 1920, Hh = 1097;
        float prevAspect = cam.aspect;
        cam.aspect = (float)W / Hh;
        System.Func<Vector3, float[]> w2p = (w) => { var vp = cam.WorldToViewportPoint(w); return new float[] { vp.x * W, (1f - vp.y) * Hh }; };

        var msb = new System.Text.StringBuilder();
        msb.Append("{\n  \"frame_w\":" + W + ", \"frame_h\":" + Hh + ",\n");
        msb.Append("  \"checks\": {\"floor_contact\": {\"tolerance_px\": 14}, \"screen_scale\": {\"min_height_frac\":0.03,\"max_height_frac\":0.45}},\n");
        msb.Append("  \"actors\": [\n");
        int written = 0;
        for (int i = 0; i < Units.Length; i++)
        {
            var u = Units[i];
            var go = GameObject.Find(u.name);
            if (go == null) { Debug.LogError("[M15GATE] missing " + u.name + " -- run Populate first"); continue; }
            var rend = go.GetComponent<Renderer>();
            var b = rend.bounds;
            float bx0 = float.MaxValue, by0 = float.MaxValue, bx1 = float.MinValue, by1 = float.MinValue;
            for (int cx8 = 0; cx8 < 2; cx8++) for (int cy8 = 0; cy8 < 2; cy8++) for (int cz8 = 0; cz8 < 2; cz8++)
            {
                var corner = new Vector3(cx8 == 0 ? b.min.x : b.max.x, cy8 == 0 ? b.min.y : b.max.y, cz8 == 0 ? b.min.z : b.max.z);
                var sp = w2p(corner);
                if (sp[0] < bx0) bx0 = sp[0]; if (sp[0] > bx1) bx1 = sp[0];
                if (sp[1] < by0) by0 = sp[1]; if (sp[1] > by1) by1 = sp[1];
            }
            var floorPx = w2p(new Vector3(b.center.x, 0f, b.center.z));
            if (written > 0) msb.Append(",\n");
            msb.Append("    {\"name\":\"" + u.name + "\",\"logical_cell\":[" + u.cx + "," + u.cy + "],\"expected_cell\":[" + u.cx + "," + u.cy + "],");
            msb.Append("\"screen_bbox\":[" + Mathf.Round(bx0) + "," + Mathf.Round(by0) + "," + Mathf.Round(bx1) + "," + Mathf.Round(by1) + "],");
            msb.Append("\"floor_y_px\":" + Mathf.Round(floorPx[1]) + "}");
            written++;
        }
        msb.Append("\n  ]\n}\n");
        cam.aspect = prevAspect;
        System.IO.Directory.CreateDirectory(OutDir);
        System.IO.File.WriteAllText(OutDir + "/m15_gate_manifest.json", msb.ToString());
        Debug.Log("[M15GATE] wrote manifest -> " + OutDir + "/m15_gate_manifest.json (" + written + "/" + Units.Length + " actors)");
    }
}
#endif
