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
    static Color _warmAmb = new Color(0.55f, 0.35f, 0.18f);   // lit-ground band; near-fire wrap ambient
    static Vector3 _fromDir = new Vector3(-1f, 0f, 0f);   // horizontal dir of the light SOURCE from scene center
    static Vector3 _hearthAnchor;                          // floor point shadows are cast AWAY from
    static bool _analyzed;
    static string _analyzedScene;   // cache key: re-analyze when the active scene changes (review P2)

    // ---------- rung 0b: populate the BASELINE cast (mirrors the runtime spawn look exactly) ----------
    // fighter@(6,6) + goblin@(9,5) — the seed_gfx_combat cells on the crypt plate. Standard shader
    // (_Glossiness .2/_Metallic 0) + blob AO + team ring, target heights hero 3.2/foe 4.2, feet on
    // FLOOR_Y=0, bounds-center on the cell: the paint_combat_v1/CombatSurfaceClient baseline, so rung
    // deltas measure the COHESION STACK and not spawn drift.
    [MenuItem("Tools/WorldOS/Cohesion Probe/0b - Populate baseline cast (fighter+goblin)")]
    public static void Populate()
    {
        SpawnBaseline("Actor_hero", "Assets/cast/fighter/fighter.fbx", "Assets/cast/fighter/albedo.jpg", 6, 6, false, 3.2f);
        SpawnBaseline("Actor_goblin", "Assets/chars_v2/goblin/goblin.fbx", "Assets/chars_v2/goblin/albedo.png", 9, 5, true, 4.2f);
        Debug.Log("[PROBE] baseline cast populated: Actor_hero (6,6) + Actor_goblin (9,5)");
    }

    static void SpawnBaseline(string nm, string fbxPath, string albedoPath, int cx, int cy, bool foe, float targetH)
    {
        foreach (var suf in new[] { "", "_AO", "_Ring", "_Cast" }) { var o = GameObject.Find(nm + suf); if (o != null) Object.DestroyImmediate(o); }
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath);
        if (prefab == null) { Debug.LogError("[PROBE] fbx not found: " + fbxPath); return; }
        var go = (GameObject)Object.Instantiate(prefab);
        go.name = nm;
        // pose own idle if the fbx carries one (one-shot graph — the SampleClipRuntime pattern).
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
        // scale to target height from posed bounds, then ground + center on the cell (feet on FLOOR_Y=0).
        var b = MeasureBounds(go);
        if (b.size.y > 0.01f) go.transform.localScale *= targetH / b.size.y;
        Vector3 cell = new Vector3((cx - 6.5f) * 2.0f, 0f, (5f - cy) * 2.0f);
        b = MeasureBounds(go);
        go.transform.position += new Vector3(cell.x - b.center.x, 0f - b.min.y, cell.z - b.center.z);
        // upright facing camera-ish (the spawn convention: yaw toward camera, pitch-guarded).
        var cam = Camera.main; float camYaw = cam != null ? cam.transform.eulerAngles.y : 45f;
        float pitchX = go.GetComponentInChildren<SkinnedMeshRenderer>() != null ? 0f : -90f;
        go.transform.rotation = Quaternion.Euler(pitchX, camYaw + 180f, 0f);
        b = MeasureBounds(go);
        go.transform.position += new Vector3(cell.x - b.center.x, 0f - b.min.y, cell.z - b.center.z);
        // baseline material: Standard + albedo (the exact runtime spawn material).
        var al = AssetDatabase.LoadAssetAtPath<Texture2D>(albedoPath);
        if (al != null)
        {
            var mm = new Material(Shader.Find("Standard"));
            mm.mainTexture = al; mm.SetFloat("_Glossiness", 0.2f); mm.SetFloat("_Metallic", 0f);
            foreach (var r in go.GetComponentsInChildren<Renderer>()) { r.sharedMaterial = mm; r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On; r.receiveShadows = true; }
        }
        // blob AO + team ring, flat on the floor (paint_combat_v1 conventions: queues 1950/1955).
        MakeGroundQuad(nm + "_AO", cell, 2.0f, RadialTex(), Color.white, 1950);
        MakeGroundQuad(nm + "_Ring", cell, 2.6f, RingTex(), foe ? new Color(1f, 0.13f, 0.10f, 1f) : new Color(0.4f, 0.95f, 1f, 1f), 1955);
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

    static Texture2D _ring;
    static Texture2D RingTex()
    {
        if (_ring != null) return _ring;
        const int N = 256;
        _ring = new Texture2D(N, N, TextureFormat.RGBA32, false);
        for (int y = 0; y < N; y++) for (int x = 0; x < N; x++)
        {
            float dx = (x - N / 2f) / (N / 2f), dy = (y - N / 2f) / (N / 2f);
            float d = Mathf.Sqrt(dx * dx + dy * dy);
            float aA = (d > 0.78f && d < 0.93f) ? 1f : 0f;
            _ring.SetPixel(x, y, new Color(1f, 1f, 1f, aA));
        }
        _ring.Apply();
        return _ring;
    }

    // ---------- rung B: plate-sampled per-scene light rig ----------
    [MenuItem("Tools/WorldOS/Cohesion Probe/1 - Rung B: plate-sampled light rig")]
    public static void RungB()
    {
        if (!Analyze()) return;
        // v2: the saved canonical scene accumulates one "CombatKey" point light PER BAKE (paint_combat_v1
        // deletes KeyLight/FillLight/Braziers but never CombatKey) — ~25 stacked lights = the chalk-white
        // actor blowout in both the editor scene AND the shipped player (issue filed). Dedupe them all;
        // ONE plate-anchored fire key replaces the pile.
        int killed = 0;
        GameObject ck;
        while ((ck = GameObject.Find("CombatKey")) != null) { Object.DestroyImmediate(ck); killed++; }
        var keyGo = GameObject.Find("KeyLight"); var fillGo = GameObject.Find("FillLight");
        if (keyGo == null || fillGo == null) { Debug.LogError("[PROBE] KeyLight/FillLight not found — is the canonical combat scene loaded?"); return; }
        var key = keyGo.GetComponent<Light>(); var fill = fillGo.GetComponent<Light>();
        key.color = _key; key.intensity = 1.2f; key.shadows = LightShadows.Soft; key.shadowStrength = 0.55f;
        keyGo.transform.rotation = Quaternion.LookRotation((-_fromDir + Vector3.down * 0.9f).normalized);
        fill.color = _amb; fill.intensity = 0.45f;
        fillGo.transform.rotation = Quaternion.LookRotation((_fromDir + Vector3.down * 0.6f).normalized);
        RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
        RenderSettings.ambientLight = _amb * 0.9f;
        // the fire itself: one warm point light AT the plate's bright-region floor anchor (what the 25
        // stacked CombatKeys were trying to be) — fire-adjacent actors pick up a hot warm side.
        var fk = GameObject.Find("ProbeFireKey");
        if (fk == null) { fk = new GameObject("ProbeFireKey"); fk.AddComponent<Light>().type = LightType.Point; }
        var fl = fk.GetComponent<Light>();
        fl.color = _key; fl.intensity = 3.2f; fl.range = 16f; fl.shadows = LightShadows.None;
        fk.transform.position = _hearthAnchor + Vector3.up * 2.5f;
        Debug.Log("[PROBE] RungB v2 applied: killed " + killed + " CombatKey clones; key " + ColorUtility.ToHtmlStringRGB(_key)
                  + " fromDir " + _fromDir.ToString("F2") + " amb " + ColorUtility.ToHtmlStringRGB(_amb) + " fireKey@" + _hearthAnchor.ToString("F1"));
    }

    // ---------- rung D: directional contact shadows (blob AO off) ----------
    [MenuItem("Tools/WorldOS/Cohesion Probe/2 - Rung D: directional contact shadows")]
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
            m.mainTexture = RadialTex(); m.color = new Color(0.05f, 0.03f, 0.02f, 0.78f); m.renderQueue = 1990;   // before actors (2000+): the ZTest-Always blob must never draw over feet (review P3)
            var r = go.GetComponent<Renderer>(); r.sharedMaterial = m; r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            n++;
        }
        Debug.Log("[PROBE] RungD applied: " + n + " directional cast shadows (blob AO/core disabled), away-from " + _hearthAnchor.ToString("F1"));
    }

    // ---------- rung A': PainterlyActor materials with the CL r10-tuned params ----------
    [MenuItem("Tools/WorldOS/Cohesion Probe/3 - Rung A': painterly actor materials")]
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
        int n = 0;
        foreach (var a in actors)
        {
            bool isHero = a.name.ToLower().Contains("hero");
            float depth01 = (zMax - zMin) > 1e-3f ? 1f - Mathf.InverseLerp(zMin, zMax, zOf[a]) : 1f;  // 1=near, 0=far
            // v2: per-ACTOR key direction — from this actor TOWARD the fire anchor (actors surround a
            // central fire; a single global key-dir lights half the ring from behind).
            Vector3 keyDir = ((_hearthAnchor + Vector3.up * 2f) - a.transform.position).normalized;
            // v3: FIRE-PROXIMITY scaling — the CL values were tuned for a dim interior; an actor standing
            // IN the fire pool must read as bright as the painted logs beside it (the plate's own value
            // logic). near01: 1 at the fire, 0 by ~12 units out.
            float distFire = Vector3.Distance(a.transform.position, _hearthAnchor);
            float near01 = 1f - Mathf.Clamp01((distFire - 3f) / 9f);
            foreach (var r in a.GetComponentsInChildren<Renderer>())
            {
                var src = r.sharedMaterial; if (src == null) continue;
                var fmat = new Material(sh);
                fmat.name = "ProbeMat_" + a.name;
                if (src.name.StartsWith("ProbeMat_")) Object.DestroyImmediate(src, true);   // re-run leak guard (review P3)
                if (src.HasProperty("_MainTex") && src.mainTexture != null) fmat.SetTexture("_MainTex", src.mainTexture);
                fmat.SetColor("_BaseColor", Color.white);
                fmat.SetColor("_KeyColor", _key);
                // v4: near-fire actors live in the painting's warm wrap-around bounce — blend the ambient
                // from the scene's cool shadow color to the lit-ground warm band by fire proximity.
                fmat.SetColor("_AmbientColor", Color.Lerp(_amb, _warmAmb, near01));
                fmat.SetVector("_KeyDir", keyDir);
                // the ClosedLoopBuilder r10 consensus values (ClosedLoopBuilder.cs:1013-1078), verbatim.
                fmat.SetFloat("_KeyStrength", Mathf.Lerp(0.9f, isHero ? 1.8f : 1.6f, near01));
                fmat.SetFloat("_RimStrength", isHero ? 0.16f : 0.20f);
                fmat.SetFloat("_Desat", isHero ? 0.24f : 0.36f);
                fmat.SetFloat("_BounceStrength", Mathf.Lerp(0.10f, 0.30f, near01));
                fmat.SetFloat("_Kuwahara", isHero ? 4.0f : 5.5f);
                fmat.SetFloat("_Posterize", isHero ? 5.0f : 4.0f);
                fmat.SetFloat("_BrushStrength", isHero ? 0.22f : 0.04f);
                fmat.SetFloat("_BrushScale", isHero ? 15.0f : 11.0f);
                fmat.SetFloat("_EdgeSoften", isHero ? 0.22f : 0.30f);
                fmat.SetFloat("_PaletteSnap", isHero ? 0.42f : 0.55f);
                fmat.SetFloat("_PaintLift", Mathf.Lerp(0.06f, 0.11f, near01));
                fmat.SetFloat("_AmbientLift", Mathf.Lerp(isHero ? 0.16f : 0.20f, 0.36f, near01));
                fmat.SetFloat("_MaxLuma", Mathf.Lerp(0.56f, isHero ? 0.85f : 0.75f, near01));
                fmat.SetFloat("_TermSharp", 0.30f);
                // halved vs CL: the camp is a shallow scene — the full interior wash flattened everyone.
                float atm = Mathf.Clamp01((1f - depth01) * 0.42f);
                fmat.SetFloat("_AtmDepth", isHero ? atm * 0.35f : atm);
                fmat.SetColor("_AtmColor", _amb * 1.4f);
                r.sharedMaterial = fmat;
            }
            n++;
        }
        Debug.Log("[PROBE] RungA' applied: PainterlyActor on " + n + " actors, per-actor keyDir toward " + _hearthAnchor.ToString("F1"));
    }

    // ---------- rung R: relit backdrop plate (WOSRelight) — the W6.0 unified light stage ----------
    // Swaps PaintedBackdrop's flat Unlit/Texture material for WOS/Relight (PR #1236, dormant), fed by the
    // greybox G-buffer sidecars (Captures-Durable/room_greybox_{depth,normal}.png, captured by
    // build_room_greybox.cs) and the plate as diffuse, driven by the SAME plate-sampled key/fill/ambient
    // RungB computes — so ONE rig lights the plate AND the actors (the Obsidian/PoE2 plate-GI relight,
    // amendment W6.0). RungB runs first so the actor rig matches the plate's relight. Metal/built-in-RP
    // spike: additive material swap only; Reset reopens the scene unsaved.
    [MenuItem("Tools/WorldOS/Cohesion Probe/4 - Rung R: relit backdrop (WOSRelight)")]
    public static void RungR()
    {
        RungB();                       // shared rig lights the actors (idempotent) + guarantees Analyze() ran
        if (!Analyze()) return;
        var bd = GameObject.Find("PaintedBackdrop");
        if (bd == null) { Debug.LogError("[PROBE] PaintedBackdrop not found."); return; }
        var rend = bd.GetComponent<Renderer>();
        var plate = rend != null && rend.sharedMaterial != null ? rend.sharedMaterial.mainTexture : null;
        if (plate == null) { Debug.LogError("[PROBE] PaintedBackdrop plate texture not found."); return; }
        var sh = Shader.Find("WOS/Relight");
        if (sh == null) { Debug.LogError("[PROBE] WOS/Relight shader not found in project."); return; }
        var nrm = LoadSidecar("room_greybox_normal.png");
        var dep = LoadSidecar("room_greybox_depth.png");
        if (nrm == null || dep == null) return;
        // re-run leak guard: destroy the previous ProbeRelightMat before replacing.
        if (rend.sharedMaterial != null && rend.sharedMaterial.name.StartsWith("ProbeRelightMat")) Object.DestroyImmediate(rend.sharedMaterial, false);
        var m = new Material(sh); m.name = "ProbeRelightMat";
        m.SetTexture("_MainTex", plate);
        m.SetTexture("_NormalTex", nrm);
        m.SetTexture("_DepthTex", dep);
        // key/fill DIRECTIONS = the RungB rig, transformed into the greybox-camera VIEW space the normal
        // sidecar is encoded in (WOS/ViewNormal = UNITY_MATRIX_IT_MV, +z toward camera). toward-light L is
        // the negative of the RungB light's forward: KeyLight fwd = (-_fromDir + down*0.9), FillLight fwd =
        // (_fromDir + down*0.6) -> L_key = (_fromDir + up*0.9), L_fill = (-_fromDir + up*0.6).
        var cam = Camera.main;
        Vector3 keyW = (_fromDir + Vector3.up * 0.9f).normalized;
        Vector3 fillW = (-_fromDir + Vector3.up * 0.6f).normalized;
        Vector3 keyV = cam != null ? cam.worldToCameraMatrix.MultiplyVector(keyW).normalized : keyW;
        Vector3 fillV = cam != null ? cam.worldToCameraMatrix.MultiplyVector(fillW).normalized : fillW;
        m.SetVector("_KeyDir", keyV);
        m.SetVector("_KeyCol", (Vector4)(_key * 1.1f));     // plate fire-core color, RungB key intensity
        m.SetVector("_FillDir", fillV);
        m.SetVector("_FillCol", (Vector4)(_amb * 0.9f));    // cool shadow fill, RungB fill
        // hemisphere ambient: cool shadow "sky", warm lit-ground "bounce" band (the plate-GI wrap).
        m.SetVector("_SkyAmb", (Vector4)(_amb * 0.9f));
        m.SetVector("_GroundAmb", (Vector4)(_warmAmb * 0.7f));
        m.SetFloat("_Bounce", 0.12f);
        // point lights are out of scope for the spike (greybox-space P reconstruction) — zero their colors.
        m.SetVector("_P0Col", Vector4.zero); m.SetVector("_P1Col", Vector4.zero); m.SetVector("_P2Col", Vector4.zero);
        // ortho extents for the point-light P reconstruction: contract camera ortho 13, ~1.75 aspect.
        m.SetVector("_OrthoExt", new Vector4(13f * 1.75f, 13f, 80f, 0.3f));
        // iter3 (#1469): the sole remaining defect was WOSRelight crushing away-facing verticals (pillars,
        // sarcophagus, walls) to solid black under the lone cool key. Three additive terms fix it, all
        // default-0 in the shader (byte-compatible when unset):
        //   (1) AMBIENT FLOOR — no vertical normal may render below a readable painted value (mirror
        //       PainterlyActor _AmbientLift). >=80% of the flat/painted value everywhere kills the voids.
        m.SetFloat("_AmbLift", 0.80f);
        //   (2) WARM BOUNCE wrap — side/down-facing verticals (N.y~0) pick up the plate's warm lit-ground
        //       band, so fire-adjacent stone reads warm instead of dead-cool.
        m.SetVector("_WarmBounceCol", (Vector4)_warmAmb);
        m.SetFloat("_WarmBounce", 0.25f);
        //   (3) HEARTH POINT fill — a warm point light at the plate's fire anchor (what RungB's ProbeFireKey
        //       is for the actors), placed in the greybox VIEW space the normals/P live in. Z is pinned to a
        //       mid-scene reconstructed depth so the warmth pools by SCREEN proximity to the fire (robust to
        //       the P-vs-ViewNormal z-convention), giving fire-adjacent verticals a warm gradient.
        Vector3 hv = cam != null ? cam.worldToCameraMatrix.MultiplyPoint(_hearthAnchor) : _hearthAnchor;
        m.SetVector("_Hearth", new Vector4(hv.x, hv.y, -40f, 60f));   // (x,y view-space, z mid-depth, w range)
        m.SetVector("_HearthCol", (Vector4)(_key * 0.6f));            // warm fire fill
        rend.sharedMaterial = m;
        Debug.Log("[PROBE] RungR applied: PaintedBackdrop -> WOS/Relight, keyV " + keyV.ToString("F2")
                  + " keyCol " + ColorUtility.ToHtmlStringRGB(_key) + " ambLift 0.80 warmBounce 0.25 hearth@" + hv.ToString("F1")
                  + " sidecars " + nrm.width + "x" + nrm.height);
    }

    // load a greybox G-buffer sidecar PNG off disk (they live at <projectRoot>/Captures-Durable/, OUTSIDE
    // Assets/ — no AssetDatabase import needed). linear=true: normal/depth bytes are DATA, not sRGB color.
    static Texture2D LoadSidecar(string fname)
    {
        string root = System.IO.Directory.GetParent(Application.dataPath).FullName;
        string path = System.IO.Path.Combine(root, "Captures-Durable", fname);
        if (!System.IO.File.Exists(path)) { Debug.LogError("[PROBE] greybox sidecar missing: " + path); return null; }
        var t = new Texture2D(2, 2, TextureFormat.RGB24, false, true);
        t.LoadImage(System.IO.File.ReadAllBytes(path));
        t.wrapMode = TextureWrapMode.Clamp; t.filterMode = FilterMode.Bilinear; t.Apply();
        return t;
    }

    [MenuItem("Tools/WorldOS/Cohesion Probe/0 - Reset (reopen scene, discard)")]
    public static void ResetScene()
    {
        var sc = SceneManager.GetActiveScene();
        if (string.IsNullOrEmpty(sc.path)) { Debug.LogError("[PROBE] active scene has no saved path - cannot discard-reopen; save it first."); return; }
        EditorSceneManager.OpenScene(sc.path);
        _analyzed = false;
        Debug.Log("[PROBE] scene reopened (all rungs discarded): " + sc.path);
    }

    // ---------- plate analysis ----------
    static bool Analyze()
    {
        if (_analyzed && _analyzedScene == SceneManager.GetActiveScene().path) return true;
        var bd = GameObject.Find("PaintedBackdrop");
        var mat = bd != null ? bd.GetComponent<Renderer>().sharedMaterial : null;
        var tex = mat != null ? mat.mainTexture as Texture2D : null;
        if (tex == null) { Debug.LogError("[PROBE] PaintedBackdrop plate texture not found."); return false; }
        // readable downsample via RT blit (works regardless of import flags).
        var rt = RenderTexture.GetTemporary(256, 256, 0, RenderTextureFormat.ARGB32, RenderTextureReadWrite.sRGB);
        Graphics.Blit(tex, rt);
        var prev = RenderTexture.active; RenderTexture.active = rt;
        var small = new Texture2D(256, 256, TextureFormat.RGBA32, false);
        small.ReadPixels(new Rect(0, 0, 256, 256), 0, 0); small.Apply();
        RenderTexture.active = prev; RenderTexture.ReleaseTemporary(rt);
        var px = small.GetPixels();
        var lum = px.Select(c => 0.299f * c.r + 0.587f * c.g + 0.114f * c.b).ToArray();
        var sorted = (float[])lum.Clone(); System.Array.Sort(sorted);
        float p98 = sorted[(int)(sorted.Length * 0.98f)], p40 = sorted[(int)(sorted.Length * 0.40f)], p90 = sorted[(int)(sorted.Length * 0.90f)];
        // v2 lessons (first ladder read near-black): the KEY is the fire CORE (top-2% luma), lifted to a
        // fully-chromatic value (V=1) — energy comes from light INTENSITY, not a dark color; the AMBIENT
        // keeps the plate's shadow HUE but is floored at V=0.30 so shadow masses stay a readable painted
        // value (the CL "_AmbientLift / no black mass" principle applied at the light level).
        _key = LiftV(MedianColor(px, lum, p98, float.MaxValue), 1.0f);
        _amb = LiftV(MedianColor(px, lum, -1f, p40), 0.30f);
        // v4: the WARM ambient near the fire — the painting's own trick is wrap-around warm bounce on
        // everything inside the fire pool; sample the lit-ground band (p60..p90) for it.
        float p60 = sorted[(int)(sorted.Length * 0.60f)];
        _warmAmb = LiftV(MedianColor(px, lum, p60, p90), 0.42f);
        // bright-region centroid (u,v) -> the fire's FLOOR position via a viewport ray (works for centered
        // fires, where a "which side" sign is degenerate) + which side the directional key leans.
        double cu = 0, cv = 0; int cn = 0;
        for (int i = 0; i < px.Length; i++) if (lum[i] >= p90) { cu += (i % 256) / 255.0; cv += (i / 256) / 255.0; cn++; }
        float u = cn > 0 ? (float)(cu / cn) : 0.5f;
        float v = cn > 0 ? (float)(cv / cn) : 0.5f;
        var cam = Camera.main;
        Vector3 camRight = cam != null ? cam.transform.right : Vector3.right; camRight.y = 0f; camRight.Normalize();
        // NOTE the display flip: paint_combat_v1 rotates the plate quad 180° AND mirrors U — the two cancel
        // (ClosedLoopBuilder key-dir recipe), so plate-U maps to screen/world right directly.
        _fromDir = camRight * (Mathf.Abs(u - 0.5f) < 0.06f ? 1f : Mathf.Sign(u - 0.5f));
        _hearthAnchor = SceneCenter();
        if (cam != null)
        {
            var ray = cam.ViewportPointToRay(new Vector3(u, v, 0f));
            if (Mathf.Abs(ray.direction.y) > 1e-4f)
            {
                float t = -ray.origin.y / ray.direction.y;
                if (t > 0f) _hearthAnchor = ray.origin + ray.direction * t;
            }
        }
        _analyzed = true; _analyzedScene = SceneManager.GetActiveScene().path;
        Object.DestroyImmediate(small);
        Debug.Log("[PROBE] plate analyzed v2: key " + ColorUtility.ToHtmlStringRGB(_key) + " amb " + ColorUtility.ToHtmlStringRGB(_amb)
                  + " brightUV (" + u.ToString("F2") + "," + v.ToString("F2") + ") fromDir " + _fromDir.ToString("F2") + " hearth " + _hearthAnchor.ToString("F1"));
        return true;
    }

    // keep hue/sat, lift value to at least `minV` (V=1 for the key: a fully-chromatic light color whose
    // energy comes from intensity — a dark sampled color as light color double-darkens the scene).
    static Color LiftV(Color c, float minV)
    {
        float h, s, vv; Color.RGBToHSV(c, out h, out s, out vv);
        return Color.HSVToRGB(h, s, Mathf.Max(vv, minV));
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
