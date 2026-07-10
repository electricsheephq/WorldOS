#if UNITY_EDITOR
using UnityEngine;
using UnityEditor;
using System.Collections.Generic;
using System.Linq;

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

    // =====================================================================================
    // RERUN (#1503 gate re-verdict) — the two BOUNDED failures from the first run get fixed:
    //   (i)  cohesion 1.0: bare Cap_* capsules never went through the CohesionProbe painterly
    //        stack. Spawn the SAME 6-unit formation as REAL owned meshes named Actor_* so the
    //        existing Cohesion Probe rungs (RungB light rig -> RungD contact shadows -> RungA'
    //        PainterlyActor materials) treat all 6 — no new asset spend (fighter/goblin are owned).
    //   (ii) Hovl zero pixels: the Shader Graphs/HS_Blend_CG materials only render under URP/HDRP.
    //        Diag the ACTIVE pipeline + re-point to a pipeline-compatible shader when needed.
    // =====================================================================================

    // owned free meshes reused verbatim from CohesionProbe.SpawnBaseline (Assets/cast + Assets/chars_v2).
    const string HeroFbx = "Assets/cast/fighter/fighter.fbx";
    const string HeroAlbedo = "Assets/cast/fighter/albedo.jpg";
    const string FoeFbx = "Assets/chars_v2/goblin/goblin.fbx";
    const string FoeAlbedo = "Assets/chars_v2/goblin/albedo.png";

    // Second persistent (looping) Hovl VFX for the "two effects render" check — a magic-circle loop
    // reads in a still capture alongside the fire one-shot (same rationale as the Populate warm-up).
    const string HovlPrefabPath2 = "Assets/Hovl Studio/Magic circles/Prefabs/Magic circle fire loop.prefab";

    [MenuItem("Tools/WorldOS/M1.5 Spend Gate/2 - Populate 6 REAL-MESH painterly actors (Actor_*)")]
    public static void PopulatePainterly()
    {
        // clear both the capsule set and any prior real-mesh set so the frame is a clean 6-actor read.
        foreach (var u in Units) DestroyExisting(u.name);
        foreach (var nm in ActorNames()) foreach (var suf in new[] { "", "_AO", "_Ring", "_Cast" }) { var o = GameObject.Find(nm + suf); if (o != null) Object.DestroyImmediate(o); }
        int n = 0;
        foreach (var u in Units)
        {
            // party -> "hero" substring so CohesionProbe.RungA' gives them the hero painterly params.
            string nm = u.foe ? "Actor_M15_foe" + (n % 3 + 1) : "Actor_M15_hero" + (n % 3 + 1);
            SpawnRealActor(nm, u.foe ? FoeFbx : HeroFbx, u.foe ? FoeAlbedo : HeroAlbedo, u.cx, u.cy, u.foe, u.foe ? 4.2f : 3.2f);
            n++;
        }
        Debug.Log("[M15GATE] populated 6 REAL-MESH actors (3 hero fighter + 3 foe goblin) as Actor_M15_* — "
                  + "now run Cohesion Probe rungs 1(B) -> 2(D) -> 3(A') to apply the painterly stack to all 6.");
    }

    static string[] ActorNames() => new[] { "Actor_M15_hero1", "Actor_M15_hero2", "Actor_M15_hero3", "Actor_M15_foe1", "Actor_M15_foe2", "Actor_M15_foe3" };

    // mirrors CohesionProbe.SpawnBaseline (that method is private) — pose own idle, scale to target
    // height from posed bounds, ground+center feet on FLOOR_Y=0, upright facing camera, Standard+albedo
    // baseline (the exact runtime spawn material the rungs then upgrade), + blob AO + team ring.
    static void SpawnRealActor(string nm, string fbxPath, string albedoPath, int cx, int cy, bool foe, float targetH)
    {
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath);
        if (prefab == null) { Debug.LogError("[M15GATE] fbx not found: " + fbxPath); return; }
        var go = (GameObject)Object.Instantiate(prefab);
        go.name = nm;
        var anim = go.GetComponentInChildren<Animator>();
        foreach (var clip in AssetDatabase.LoadAllAssetRepresentationsAtPath(fbxPath).OfType<AnimationClip>())
            if (clip.name.ToLower().Contains("idle") && anim != null && anim.avatar != null)
            {
                var g = UnityEngine.Playables.PlayableGraph.Create("Pose_" + nm);
                var cp = UnityEngine.Animations.AnimationClipPlayable.Create(g, clip);
                var op = UnityEngine.Animations.AnimationPlayableOutput.Create(g, "Out", anim);
                UnityEngine.Playables.PlayableOutputExtensions.SetSourcePlayable(op, cp);
                g.Evaluate(0f); g.Destroy();
                break;
            }
        var b = MeasureBounds(go);
        if (b.size.y > 0.01f) go.transform.localScale *= targetH / b.size.y;
        Vector3 cell = CellToWorld(cx, cy);
        b = MeasureBounds(go);
        go.transform.position += new Vector3(cell.x - b.center.x, 0f - b.min.y, cell.z - b.center.z);
        var cam = Camera.main; float camYaw = cam != null ? cam.transform.eulerAngles.y : 45f;
        float pitchX = go.GetComponentInChildren<SkinnedMeshRenderer>() != null ? 0f : -90f;
        go.transform.rotation = Quaternion.Euler(pitchX, camYaw + 180f, 0f);
        b = MeasureBounds(go);
        go.transform.position += new Vector3(cell.x - b.center.x, 0f - b.min.y, cell.z - b.center.z);
        var al = AssetDatabase.LoadAssetAtPath<Texture2D>(albedoPath);
        if (al != null)
        {
            var mm = new Material(Shader.Find("Standard"));
            mm.mainTexture = al; mm.SetFloat("_Glossiness", 0.2f); mm.SetFloat("_Metallic", 0f);
            foreach (var r in go.GetComponentsInChildren<Renderer>()) { r.sharedMaterial = mm; r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On; r.receiveShadows = true; }
        }
        else Debug.LogWarning("[M15GATE] albedo not found (" + albedoPath + ") — actor renders untextured Standard");
        MakeGroundQuad(nm + "_AO", cell, 2.0f, RadialTex(), Color.white, 1950);
        MakeGroundQuad(nm + "_Ring", cell, 2.6f, RingTex(), foe ? new Color(1f, 0.13f, 0.10f, 1f) : new Color(0.4f, 0.95f, 1f, 1f), 1955);
    }

    static Bounds MeasureBounds(GameObject a)
    {
        var rends = a.GetComponentsInChildren<Renderer>();
        if (rends.Length == 0) return new Bounds(a.transform.position, Vector3.one);
        Bounds b = rends[0].bounds; foreach (var r in rends) b.Encapsulate(r.bounds);
        return b;
    }

    // Spawn TWO owned Hovl effects (fire one-shot + magic-circle loop) beside the foe cluster for the
    // "two effects render visible pixels" check. Both warmed via Simulate() so they read in a still.
    [MenuItem("Tools/WorldOS/M1.5 Spend Gate/2b - Populate 2 Hovl VFX (visibility check)")]
    public static void PopulateTwoVFX()
    {
        DestroyExisting("M15_VFX"); DestroyExisting("M15_VFX2");
        SpawnVFX(HovlPrefabPath, "M15_VFX", CellToWorld(2, 7));
        SpawnVFX(HovlPrefabPath2, "M15_VFX2", CellToWorld(3, 8));
    }

    static void SpawnVFX(string prefabPath, string nm, Vector3 wp)
    {
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
        if (prefab == null) { Debug.LogError("[M15GATE] Hovl VFX prefab not found: " + prefabPath); return; }
        var vfx = (GameObject)Object.Instantiate(prefab);
        vfx.name = nm;
        vfx.transform.position = new Vector3(wp.x, 0.05f, wp.z);
        int nps = 0;
        foreach (var ps in vfx.GetComponentsInChildren<ParticleSystem>(true)) { ps.Simulate(0.08f, true, true, true); nps++; }
        Debug.Log("[M15GATE] spawned Hovl VFX '" + nm + "' (" + prefabPath + ") @" + wp.ToString("F1") + ", warmed " + nps + " PS");
    }

    // ---- Hovl VFX render fix (issue: Shader Graphs/HS_Blend_CG => 0 px). DECISIVE = active pipeline. ----
    [MenuItem("Tools/WorldOS/M1.5 Spend Gate/diag - Pipeline + Hovl shader support")]
    public static void DiagPipeline()
    {
        var rp = UnityEngine.Rendering.GraphicsSettings.currentRenderPipeline;
        Debug.Log("[M15GATE][DIAG] currentRenderPipeline=" + (rp == null ? "NULL (Built-in RP => Shader Graph shaders will NOT render)" : rp.GetType().Name + " (" + rp.name + ")"));
        int i = 0;
        foreach (var r in HovlRenderers())
        {
            var m = r.sharedMaterial;
            var sh = m != null ? m.shader : null;
            Debug.Log("[M15GATE][DIAG] hovlRenderer[" + i + "]=" + r.gameObject.name
                      + " shader=" + (sh != null ? sh.name : "(null)")
                      + " isSupported=" + (sh != null ? sh.isSupported.ToString() : "n/a")
                      + " renderQueue=" + (m != null ? m.renderQueue : -1)
                      + " enabled=" + r.enabled + " tex=" + (m != null && m.HasProperty("_MainTex") && m.mainTexture != null ? m.mainTexture.name : "-"));
            i++;
        }
        Debug.Log("[M15GATE][DIAG] " + i + " Hovl-shader renderer(s) in scene");
    }

    // All ParticleSystem/Mesh renderers whose material uses a Hovl Shader-Graph shader (HS_*) OR lives
    // under a "Hovl" GameObject subtree — the set that renders 0 px outside URP.
    static IEnumerable<Renderer> HovlRenderers()
    {
        foreach (var r in Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None))
        {
            var m = r.sharedMaterial;
            var name = m != null && m.shader != null ? m.shader.name : "";
            if (name.Contains("HS_") || name.StartsWith("Shader Graphs/HS") || name.StartsWith("Hovl")) yield return r;
        }
    }

    // Re-point Hovl Shader-Graph particle materials to a pipeline-compatible shader so they render.
    // Built-in RP branch (the observed #1503 case if currentRenderPipeline==null): swap the SG shader
    // for Unity's built-in Legacy Particles shader (Additive for fire/energy, keeps mainTexture+tint) —
    // this IS the unity-asset-stack "URP-convert check" applied to the ACTIVE pipeline. URP branch:
    // the SG should render; log guidance to enable Opaque/Depth textures (HS_Distortion) or reimport
    // the Hovl URP-support package instead of blindly re-pointing.
    [MenuItem("Tools/WorldOS/M1.5 Spend Gate/3 - Fix Hovl VFX (pipeline-compatible shaders)")]
    public static void FixHovlVFX()
    {
        var rp = UnityEngine.Rendering.GraphicsSettings.currentRenderPipeline;
        bool builtin = rp == null;
        if (!builtin)
        {
            Debug.LogWarning("[M15GATE][FIX] URP/HDRP active (" + rp.name + "): the Shader Graph shaders SHOULD render. "
                + "If still 0px, enable Opaque Texture + Depth Texture on the URP Renderer asset (HS_Distortion needs them) "
                + "or reimport the Hovl 'URP support' package / Edit>Rendering>Materials>Convert to URP. NOT re-pointing shaders under URP.");
            return;
        }
        var addBlend = Shader.Find("Legacy Shaders/Particles/Additive");
        var alphaBlend = Shader.Find("Legacy Shaders/Particles/Alpha Blended");
        if (addBlend == null) { Debug.LogError("[M15GATE][FIX] Legacy Shaders/Particles/Additive not found."); return; }
        int fixedN = 0;
        foreach (var r in HovlRenderers().ToList())
        {
            var src = r.sharedMaterial; if (src == null) continue;
            bool distort = src.shader != null && src.shader.name.Contains("Distort");
            var tgt = new Material(distort && alphaBlend != null ? alphaBlend : addBlend);
            tgt.name = "M15HovlFix_" + r.gameObject.name;
            // preserve the emissive texture: SG particle materials commonly expose _MainTex / _BaseMap.
            Texture tex = null;
            foreach (var p in new[] { "_MainTex", "_BaseMap", "_BaseColorMap" }) if (src.HasProperty(p) && src.GetTexture(p) != null) { tex = src.GetTexture(p); break; }
            if (tex != null) tgt.mainTexture = tex;
            foreach (var p in new[] { "_TintColor", "_BaseColor", "_Color" }) if (src.HasProperty(p)) { tgt.color = src.GetColor(p); break; }
            r.sharedMaterial = tgt;
            fixedN++;
        }
        Debug.Log("[M15GATE][FIX] Built-in RP: re-pointed " + fixedN + " Hovl SG material(s) -> Legacy Particles (Additive/AlphaBlended), texture+tint preserved. Re-capture to confirm visible pixels.");
    }
}
#endif
