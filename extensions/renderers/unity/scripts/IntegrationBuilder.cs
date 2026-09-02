#if UNITY_EDITOR
using UnityEngine;
using UnityEditor;
using System.Collections.Generic;

/// <summary>
/// INTEGRATION STEP — WorldOS Unity sprint, 2026-06-22
/// Combines WS-B block-out with WS-C painterly assets:
///   1. Painted backdrop fills the scene
///   2. Invisible occluder proxies at fixture prop cells
///   3. GLB hero_fighter + monster_goblin actors at spawn cells
///   4. Tactical grid overlay on the painted floor
///   5. Correct warm-key lighting
/// Run via: Tools → WorldOS → Build Integration Frame
/// </summary>
public class IntegrationBuilder
{
    // ── fixture constants (matches TavernTier1Builder) ─────────────────────
    const int   COLS      = 14;
    const int   ROWS      = 10;
    const float CELL_SIZE = 5f;

    static float OriginX => -(COLS * CELL_SIZE) / 2f;  // = -35
    const float OriginZ  = 0f;

    // height bands
    const float TALL_H = 3.0f;
    const float MID_H  = 1.8f;
    const float LOW_H  = 1.0f;
    const float ACTOR_SCALE = 0.82f;

    // ── fixture props (anchor_cell + height_band) ──────────────────────────
    struct PropProxy
    {
        public string id;
        public int minC, maxC, minR, maxR;
        public string height_band;
    }

    static readonly PropProxy[] PropProxies = new PropProxy[]
    {
        new PropProxy { id="bar",     minC=1,maxC=4,  minR=1,maxR=1, height_band="tall" },
        new PropProxy { id="hearth",  minC=11,maxC=12,minR=1,maxR=1, height_band="tall" },
        new PropProxy { id="table1",  minC=4,maxC=5,  minR=4,maxR=4, height_band="mid"  },
        new PropProxy { id="table2",  minC=8,maxC=9,  minR=5,maxR=5, height_band="mid"  },
        new PropProxy { id="barrels", minC=2,maxC=2,  minR=7,maxR=7, height_band="low"  },
    };

    // ── spawns ─────────────────────────────────────────────────────────────
    static readonly (int c, int r) PartySpawn0 = (6, 8);   // HERO
    static readonly (int c, int r) FoeSpawn0   = (6, 2);   // MONSTER (near bar — behind bar for occlusion test)

    // ── lighting ──────────────────────────────────────────────────────────
    static readonly Color KEY_COLOR     = new Color(1.000f, 0.604f, 0.271f, 1f); // #ff9a45
    static readonly Color AMBIENT_COLOR = new Color(0.227f, 0.247f, 0.333f, 1f); // #3a3f55

    // ─────────────────────────────────────────────────────────────────────
    [MenuItem("Tools/WorldOS/Build Integration Frame")]
    public static void Build()
    {
        // ── 1. Tear down previous integration root if any ─────────────────
        var oldRoot = GameObject.Find("IntegrationRoot");
        if (oldRoot != null) Object.DestroyImmediate(oldRoot);

        // ── 2. Find TavernTier1 B-workstream children ─────────────────────
        // Hide visible primitives: Walls, Floor_Grid, and primitive Props/Actors
        // KEEP the Backdrop_DepthBlit (has the painterly texture) — we'll replace its
        // material with Unlit/Texture since depth-blit NO-OPs on Metal TBDR.
        HidePrimitiveBGeometry();

        // ── 3. Fix up backdrop: Unlit/Texture, full-screen quad ───────────
        FixBackdrop();

        // ── 4. Create integration root ────────────────────────────────────
        var root = new GameObject("IntegrationRoot");

        // ── 5. Invisible occluder proxies ─────────────────────────────────
        BuildOccluderProxies(root);

        // ── 6. Place GLB actors ───────────────────────────────────────────
        PlaceActors(root);

        // ── 7. Tactical grid overlay ──────────────────────────────────────
        BuildGridOverlay(root);

        // ── 8. Lighting ───────────────────────────────────────────────────
        SetupLighting();

        // ── 9. Flush ──────────────────────────────────────────────────────
        EditorUtility.SetDirty(root);
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(
            UnityEngine.SceneManagement.SceneManager.GetActiveScene());

        Debug.Log("[Integration] Build complete — painterly backdrop + occluders + GLB actors + grid.");
    }

    // ── HIDE B primitive geometry ─────────────────────────────────────────
    static void HidePrimitiveBGeometry()
    {
        var t1Root = GameObject.Find("TavernTier1");
        if (t1Root == null) { Debug.LogWarning("[Integration] TavernTier1 not found."); return; }

        // Disable renderers on Walls, Floor_Grid, Props, Actors children
        string[] hideNames = { "Floor_Grid", "Walls", "Props", "Actors" };
        foreach (string n in hideNames)
        {
            var child = t1Root.transform.Find(n);
            if (child == null) continue;
            foreach (var r in child.GetComponentsInChildren<Renderer>())
                r.enabled = false;
        }
        Debug.Log("[Integration] Hidden B primitive geometry (Floor/Walls/Props/Actors).");
    }

    // ── FIX BACKDROP: switch from depth-blit (TBDR no-op) to Unlit/Texture ─
    static void FixBackdrop()
    {
        // The B workstream placed a Backdrop_DepthBlit quad; we reuse it.
        // Switch material to Unlit/Texture + correct sizing/positioning.
        var t1Root = GameObject.Find("TavernTier1");
        GameObject bdGO = null;
        if (t1Root != null)
        {
            var bdChild = t1Root.transform.Find("Backdrop_DepthBlit");
            if (bdChild != null) bdGO = bdChild.gameObject;
        }
        if (bdGO == null) bdGO = GameObject.Find("Backdrop_DepthBlit");

        if (bdGO == null)
        {
            // Create a new backdrop quad
            bdGO = GameObject.CreatePrimitive(PrimitiveType.Quad);
            bdGO.name = "Backdrop_DepthBlit";
            var t1 = GameObject.Find("TavernTier1");
            if (t1 != null) bdGO.transform.SetParent(t1.transform, false);
            Debug.LogWarning("[Integration] Backdrop_DepthBlit not found; created new quad.");
        }

        // Build unlit material with painterly texture
        Texture2D tex = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/tavern_backdrop.png");
        if (tex == null) tex = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/tavern_backdrop.png");

        // Use BackdropUnlit shader: ZWrite=Off, ZTest=Always — no depth blocking of actors
        var backdropShader = Shader.Find("WorldOS/BackdropUnlit");
        if (backdropShader == null) backdropShader = Shader.Find("Unlit/Texture");
        var mat = new Material(backdropShader);
        if (tex != null)
        {
            mat.mainTexture = tex;
            Debug.Log("[Integration] Painterly backdrop texture loaded: " + tex.width + "x" + tex.height + " shader=" + backdropShader.name);
        }
        else
        {
            Debug.LogError("[Integration] Could not load tavern_backdrop.png!");
        }
        // Render before geometry (sky/backdrop layer); ZWrite=Off ensures actors render on top
        mat.renderQueue = 900;
        AssetDatabase.DeleteAsset("Assets/BackdropMat_T1.mat");
        AssetDatabase.CreateAsset(mat, "Assets/BackdropMat_T1.mat");

        var rend = bdGO.GetComponent<Renderer>();
        rend.sharedMaterial = mat;
        rend.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        rend.receiveShadows = false;

        // ── Reposition: the backdrop is a camera-facing full-screen quad.
        // Camera: ortho, pitch 26.565°, position (0, ~40.25, -55.2) looking at grid centre.
        // The backdrop should be placed at the back of the scene (z≈0) tilted to face camera
        // so its face is perpendicular to the view direction.
        // 
        // 1024×1024 texture on a dimetric grid that's 14×10 cells @ 5 ft.
        // The backdrop was generated from the fixture plate at these grid proportions.
        // We want it to cover the full camera viewport.
        //
        // STRATEGY: place a large quad at the back wall (z=-5), tilt it to face the camera
        // (pitch = 90 - 26.565 = 63.435°), and scale it to fill the ortho viewport.
        // OrthoSize=18 means ±18 units tall in screen-space.
        // Camera aspect ~ 16:9 (game view), so ±32 wide.
        // We need the quad to be larger to avoid cropping.
        //
        // But 1024² is square; the grid's dimetric frame as seen by the camera has a
        // specific aspect. We scale the quad so the PAINTING fills the camera frustum.
        // 
        // The painterly image was generated FROM the 2:1 dimetric plate, so the painted
        // room occupies the full 1024×1024. The camera's ortho view has aspect ~16:9 but
        // orthoSize=18 → half-height=18 units in projected space.
        // 
        // In world space the backdrop quad scales map to projected screen extents.
        // Scale X = 2 * orthoSize * aspect (full width at ortho projection)
        // Scale Y = 2 * orthoSize (full height)
        // But since the quad is tilted 63.435°, its worldspace Y needs to be larger:
        // worldH = screenH / cos(63.435°)   ... the foreshortening factor.
        // cos(63.435°) ≈ 0.4472 → worldH ≈ screenH / 0.4472
        //
        // orthoSize = 18 → screenH = 36 units.
        // worldH = 36 / 0.4472 ≈ 80.5
        // screenW ~ 36 * (16/9) = 64 units. (assume 16:9 game view)
        // worldW = 64 (no tilt in X)
        //
        // But the painterly image is 1024×1024 (square), so if worldW=64 and worldH=80.5,
        // the image will look stretched vertically. 
        //
        // CORRECT APPROACH: the backdrop captures the full scene in the camera's 2:1 view.
        // The texture IS what the camera sees in a flat-2D sense (it's basically a 
        // pre-rendered frame). So we want it to fill the CAMERA FRUSTUM exactly.
        // Scale the quad to match the camera's near-clip projected rectangle,
        // PERPENDICULAR TO THE CAMERA VIEW DIRECTION.
        //
        // Simpler: scale worldW = gameViewAspect * 2*orthoSize * 1.1 (10% bleed)
        //          worldH = 2*orthoSize / cos(tiltRad) * 1.1
        // Position: at z of back wall, Y raised to centre of scene.
        //
        // Actually simplest for this spike: just set transform to fill screen.
        // We'll position it in world space so it's behind all geometry.

        float pitchRad = Mathf.Atan(0.5f);   // 26.565°
        float tiltDeg  = 90f - Mathf.Rad2Deg * pitchRad;  // 63.435°
        float tiltRad  = Mathf.Deg2Rad * tiltDeg;

        float orthoSize    = 18f;
        float gameAspect   = 16f / 9f;  // approximate; will fill in any case
        float screenHWorld = 2f * orthoSize;
        float screenWWorld = screenHWorld * gameAspect;

        // World-space scale of the tilted quad to cover screen:
        // X: no foreshortening → match screen width + bleed
        // Y: foreshortened by cos(tiltDeg); divide to compensate
        float quadW = screenWWorld * 1.15f;
        float quadH = (screenHWorld / Mathf.Cos(tiltRad)) * 1.15f;

        // But texture is 1024×1024 — if we distort aspect, painting looks wrong.
        // The painted room fills the frame correctly at the grid's dimetric aspect.
        // The dimetric view of a 14×10 grid has a NATURAL aspect ratio:
        //   projected width  = 14 * cos(30°) * CELL  (2:1 dimetric, 30° hex angle)
        //   Actually 2:1 dimetric: each cell projects at 2:1 so the grid appears as
        //   a 2*(14)*1 : (10)*1 = 28:10 = 2.8:1 horizontal.
        // The scene from the camera IS roughly 16:9 but let's just use the quad's
        // scale to match the camera frustum width and accept the image fills it.
        // 
        // 1024×1024 → if worldW != worldH in screen-projected pixels, the image stretches.
        // For now: make quad aspect match screen aspect (16:9 if game view is 16:9).
        // Alignment will be approximate; owner will calibrate.

        // Grid centre in world: X=0, Z=25 (half of ROWS*CELL_SIZE)
        // Camera pulls back 90 units from grid centre.
        // Backdrop z: at the back wall z = 0 - 2 = -2 (behind row 0 wall).
        float backdropZ = -2f;
        // Y position: the backdrop's world Y = some value so it appears centred.
        // In camera space the quad centre should be at Y that maps to screen centre.
        // Camera points at (0, 0, 25) in world space... but with pitchDeg = 26.565°
        // and position at (0, ~40, -55), the view ray hits the grid at Y~0.
        // The backdrop's visual centre in screen-space is at roughly the scene mid-point.
        // The upper half of the backdrop should show the back wall/ceiling of the painting.
        // Empirically, raising the quad Y to ~ ROWS*CELL*0.6 = 30 puts the painted
        // floor in the lower portion, painted wall in the upper — matching what the 
        // camera sees of the block-out.
        float backdropY = ROWS * CELL_SIZE * 0.55f;  // ~27.5

        bdGO.transform.localPosition   = new Vector3(0f, backdropY, backdropZ);
        bdGO.transform.localEulerAngles = new Vector3(tiltDeg, 0f, 0f);
        bdGO.transform.localScale       = new Vector3(quadW, quadH, 1f);

        Debug.Log($"[Integration] Backdrop: pos=(0,{backdropY:F1},{backdropZ}), rot=({tiltDeg:F1},0,0), scale=({quadW:F1},{quadH:F1},1)");
    }

    // ── OCCLUDER PROXIES ──────────────────────────────────────────────────
    static void BuildOccluderProxies(GameObject root)
    {
        // Depth-write only (colour mask = 0) material so proxies write to Z-buffer
        // but are invisible. Actors behind a proxy will be z-rejected.
        // On Metal TBDR, depth writes from opaque pass (queue < 2500) ARE retained.
        // We use an opaque shader with ColorMask 0 via a custom material tweak.
        //
        // Since we can't inline HLSL here, we use Standard with full alpha=1 black,
        // renderQueue=2499, and set ColorMask via SetInt. The key is it must be
        // in the opaque queue so TBDR's depth tile is NOT discarded.
        //
        // Actually the simplest TBDR-safe occluder: Standard material, black color,
        // renderQueue = 1500 (before geometry), ZWrite On, ColorMask 0.
        // In Built-in RP: set material.SetInt("_ColorMask", 0) disables color writes.
        
        // Prefer depth-only shader (invisible + ZWrite=On). Falls back to disabled renderer.
        var depthShader = Shader.Find("WorldOS/OccluderDepth");  // #1460: was "…OccluderDepthOnly" (never resolved -> visible black boxes); committed shader is "WorldOS/OccluderDepth" (#1433)
        Material occluderMat;
        bool useDepthShader = (depthShader != null);
        if (useDepthShader)
        {
            occluderMat = new Material(depthShader);
            occluderMat.renderQueue = 1999;  // just before actors (2000)
            Debug.Log("[Integration] Using depth-only occluder shader.");
        }
        else
        {
            // Fallback: standard opaque black -- will be visible but provides depth for actors
            occluderMat = new Material(Shader.Find("Standard"));
            occluderMat.color = new Color(0.05f, 0.04f, 0.03f, 1f);  // near-black
            occluderMat.SetFloat("_Metallic", 0f);
            occluderMat.SetFloat("_Glossiness", 0f);
            occluderMat.renderQueue = 1999;
            Debug.LogWarning("[Integration] Depth-only shader unavailable; occluders will be visible as dark boxes.");
        }
        AssetDatabase.DeleteAsset("Assets/OccluderMat.mat");
        AssetDatabase.CreateAsset(occluderMat, "Assets/OccluderMat.mat");

        var occParent = new GameObject("OccluderProxies");
        occParent.transform.SetParent(root.transform, false);

        foreach (var p in PropProxies)
        {
            float h = HeightFromBand(p.height_band);
            float spanX = (p.maxC - p.minC + 1) * CELL_SIZE * 0.92f;
            float spanZ = (p.maxR - p.minR + 1) * CELL_SIZE * 0.92f;
            float cx = OriginX + (p.minC + p.maxC + 1) * CELL_SIZE * 0.5f;
            float cz = OriginZ + (p.minR + p.maxR + 1) * CELL_SIZE * 0.5f;

            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = $"Occluder_{p.id}";
            go.transform.SetParent(occParent.transform, false);
            go.transform.localPosition = new Vector3(cx, h * 0.5f, cz);
            go.transform.localScale    = new Vector3(spanX, h, spanZ);

            var r = go.GetComponent<Renderer>();
            r.sharedMaterial = occluderMat;
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            r.receiveShadows    = false;

            // Verify: remove collider (not needed)
            Object.DestroyImmediate(go.GetComponent<BoxCollider>());
        }

        Debug.Log($"[Integration] Built {PropProxies.Length} occluder proxies.");
    }

    // ── PLACE GLB ACTORS ──────────────────────────────────────────────────
    static void PlaceActors(GameObject root)
    {
        var actorParent = new GameObject("GLBActors");
        actorParent.transform.SetParent(root.transform, false);

        // Hero fighter — party spawn 0 = (6, 8)
        PlaceGLBAt(actorParent, "HeroFighter",
            "Assets/painterly/hero_fighter/model.fbx",
            PartySpawn0.c, PartySpawn0.r,
            KEY_COLOR, AMBIENT_COLOR, isHero: true);

        // Monster goblin — foe spawn 0 = (6, 2)  → this cell is NEAR the bar occluder at row 1
        // → useful occlusion test: monster partially behind bar
        PlaceGLBAt(actorParent, "MonsterGoblin",
            "Assets/painterly/monster_goblin/model.fbx",
            FoeSpawn0.c, FoeSpawn0.r,
            KEY_COLOR, AMBIENT_COLOR, isHero: false);

        Debug.Log("[Integration] Placed GLB actors.");
    }

    static void PlaceGLBAt(GameObject parent, string label, string glbPath,
                            int c, int r, Color keyColor, Color ambColor, bool isHero)
    {
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(glbPath);
        if (prefab == null)
        {
            Debug.LogError($"[Integration] GLB not found: {glbPath}");
            return;
        }

        var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
        go.name = label;
        go.transform.SetParent(parent.transform, false);

        // Scale: actor should read as ~human height in the painted tavern.
        // Grid cell = 5 ft. A human is ~6 ft = 6 units.
        // FBX models from Meshy/Blender export embed a root scale of 100 (cm→m).
        // We want world height ≈ 4.5 units.
        // Strategy: measure current world bounds at the FBX prefab's natural scale,
        // then MULTIPLY the existing localScale to reach targetHeight.
        float targetHeight = 4.5f;
        // Temporarily place far away to measure bounds at natural scale
        go.transform.position = new Vector3(9999f, 0f, 9999f);
        var bounds = GetBoundsLocal(go);
        float currentH = bounds.size.y > 0.01f ? bounds.size.y : 2.0f;
        float scaleMult = targetHeight / currentH;
        // MULTIPLY existing localScale (don't override — FBX root may already have scale)
        var existing = go.transform.localScale;
        go.transform.localScale = new Vector3(existing.x * scaleMult,
                                               existing.y * scaleMult,
                                               existing.z * scaleMult);

        // Position: cell centre, sit bottom on floor (Y=0)
        float wx = OriginX + (c + 0.5f) * CELL_SIZE;
        float wz = OriginZ + (r + 0.5f) * CELL_SIZE;
        // Re-measure bounds after scale applied to get the floor Y offset
        var scaledBounds = GetBoundsLocal(go);
        float floorY = scaledBounds.min.y < -0.1f ? -scaledBounds.min.y : 0f;
        go.transform.position = new Vector3(wx, floorY, wz);

        // Face camera (roughly): rotate 180° so front faces -Z (camera direction)
        // Rotate to stand upright: Blender GLB→FBX has Z-up, Unity expects Y-up
        go.transform.localEulerAngles = new Vector3(-90f, 180f, 0f);

        // Warm lighting via material tint on all renderers
        // (directional key light already lit them; boost cohesion with a slight tint)
        foreach (var rend in go.GetComponentsInChildren<Renderer>())
        {
            foreach (var m in rend.sharedMaterials)
            {
                if (m == null) continue;
                // Nudge base color toward key for cohesion (don't overwrite if Standard)
                if (m.HasProperty("_Color"))
                {
                    Color orig = m.color;
                    // Subtle warm tint: lerp 15% toward key light color
                    m.color = Color.Lerp(orig, keyColor, isHero ? 0.12f : 0.08f);
                }
            }
        }

        // Contact shadow disc under actor
        BuildContactShadow(parent, label + "_Shadow", c, r,
            scaleMult * 0.9f, scaleMult * 0.65f);

        Debug.Log($"[Integration] Placed {label} at ({c},{r}) scaleMult={scaleMult:F2} h={currentH:F2}");
    }

    static Bounds GetBoundsLocal(GameObject go)
    {
        var renderers = go.GetComponentsInChildren<Renderer>();
        if (renderers.Length == 0) return new Bounds(Vector3.zero, Vector3.one * 1.8f);
        var b = renderers[0].bounds;
        for (int i = 1; i < renderers.Length; i++)
            b.Encapsulate(renderers[i].bounds);
        return b;
    }

    static void BuildContactShadow(GameObject parent, string name, int c, int r, float rx, float rz)
    {
        var go = GameObject.CreatePrimitive(PrimitiveType.Sphere);
        go.name = name;
        go.transform.SetParent(parent.transform, false);
        go.transform.localScale    = new Vector3(rx * 2f, 0.04f, rz * 2f);
        go.transform.localPosition = new Vector3(
            OriginX + (c + 0.5f) * CELL_SIZE,
            0.02f,
            OriginZ + (r + 0.5f) * CELL_SIZE);
        Object.DestroyImmediate(go.GetComponent<SphereCollider>());

        var mat = new Material(Shader.Find("Standard"));
        mat.color = new Color(0f, 0f, 0f, 0.55f);
        mat.SetFloat("_Mode", 3f);
        mat.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
        mat.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
        mat.SetInt("_ZWrite", 0);
        mat.DisableKeyword("_ALPHATEST_ON");
        mat.EnableKeyword("_ALPHABLEND_ON");
        mat.DisableKeyword("_ALPHAPREMULTIPLY_ON");
        mat.renderQueue = 3000;
        go.GetComponent<Renderer>().sharedMaterial = mat;
        go.GetComponent<Renderer>().shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        go.GetComponent<Renderer>().receiveShadows    = false;
    }

    // ── TACTICAL GRID OVERLAY ─────────────────────────────────────────────
    static void BuildGridOverlay(GameObject root)
    {
        // Draw walkable dimetric grid lines on the floor using LineRenderer objects.
        // Colour: semi-transparent warm yellow, thin lines.
        // Toggle-able: we parent them under "GridOverlay" which can be enabled/disabled.
        var gridRoot = new GameObject("GridOverlay");
        gridRoot.transform.SetParent(root.transform, false);

        Color walkableColor = new Color(0.9f, 0.75f, 0.3f, 0.35f); // semi-transparent gold
        float lineW = 0.12f;
        float yFloor = 0.05f; // just above floor

        // Build a set of blocked cells from fixture
        var blocked = new HashSet<(int, int)>();
        // Walls (row 0 full, column 0 and 13 rows 1-8)
        for (int c2 = 0; c2 < COLS; c2++) blocked.Add((c2, 0));
        for (int r2 = 1; r2 <= 8; r2++) { blocked.Add((0, r2)); blocked.Add((13, r2)); }
        // Props
        foreach (var p in PropProxies)
            for (int c2 = p.minC; c2 <= p.maxC; c2++)
                for (int r2 = p.minR; r2 <= p.maxR; r2++)
                    blocked.Add((c2, r2));

        int lineIdx = 0;
        // Draw a small diamond/square at each WALKABLE cell centre
        for (int col = 0; col < COLS; col++)
        {
            for (int row = 0; row < ROWS; row++)
            {
                if (blocked.Contains((col, row))) continue;
                DrawCellMarker(gridRoot, col, row, yFloor, lineW, walkableColor, ref lineIdx);
            }
        }
        Debug.Log($"[Integration] Grid overlay: {lineIdx} cell markers.");
    }

    static void DrawCellMarker(GameObject parent, int c, int r, float y,
                                float lw, Color col, ref int idx)
    {
        // Draw a small cross at the cell centre (like a tactical grid tile indicator)
        float cx = OriginX + (c + 0.5f) * CELL_SIZE;
        float cz = OriginZ + (r + 0.5f) * CELL_SIZE;
        float size = CELL_SIZE * 0.35f;  // half-size of tick marks

        // Horizontal tick
        var goH = new GameObject($"GridTick_{idx}_H");
        goH.transform.SetParent(parent.transform, false);
        var lrH = goH.AddComponent<LineRenderer>();
        lrH.useWorldSpace = true;
        lrH.startWidth = lw; lrH.endWidth = lw;
        lrH.material = GridLineMat(col);
        lrH.startColor = col; lrH.endColor = col;
        lrH.positionCount = 2;
        lrH.SetPosition(0, new Vector3(cx - size, y, cz));
        lrH.SetPosition(1, new Vector3(cx + size, y, cz));
        lrH.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        lrH.receiveShadows    = false;

        // Vertical tick (Z axis)
        var goV = new GameObject($"GridTick_{idx}_V");
        goV.transform.SetParent(parent.transform, false);
        var lrV = goV.AddComponent<LineRenderer>();
        lrV.useWorldSpace = true;
        lrV.startWidth = lw; lrV.endWidth = lw;
        lrV.material = GridLineMat(col);
        lrV.startColor = col; lrV.endColor = col;
        lrV.positionCount = 2;
        lrV.SetPosition(0, new Vector3(cx, y, cz - size));
        lrV.SetPosition(1, new Vector3(cx, y, cz + size));
        lrV.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        lrV.receiveShadows    = false;

        idx++;
    }

    static Material _gridMat;
    static Material GridLineMat(Color c)
    {
        if (_gridMat != null) return _gridMat;
        _gridMat = new Material(Shader.Find("Sprites/Default"));
        _gridMat.color = c;
        return _gridMat;
    }

    // ── LIGHTING ──────────────────────────────────────────────────────────
    static void SetupLighting()
    {
        var lightGO = GameObject.Find("Directional Light");
        if (lightGO == null)
        {
            lightGO = new GameObject("Directional Light");
            lightGO.AddComponent<Light>().type = LightType.Directional;
        }
        var lt = lightGO.GetComponent<Light>();
        lt.color     = KEY_COLOR;
        lt.intensity = 1.2f;
        lt.shadows   = LightShadows.Soft;
        lt.shadowStrength = 0.55f;
        lt.shadowBias = 0.04f;
        lightGO.transform.eulerAngles = new Vector3(38f, 30f, 0f);

        RenderSettings.ambientMode      = UnityEngine.Rendering.AmbientMode.Flat;
        RenderSettings.ambientLight     = AMBIENT_COLOR;
        RenderSettings.ambientIntensity = 0.9f;

        Debug.Log("[Integration] Lighting set: key=#ff9a45, ambient=#3a3f55.");
    }

    // ── HELPERS ───────────────────────────────────────────────────────────
    static float HeightFromBand(string band) => band switch
    {
        "tall" => TALL_H,
        "mid"  => MID_H,
        "low"  => LOW_H,
        _      => MID_H,
    };

    // ── SCREENSHOT HELPER ─────────────────────────────────────────────────
    [MenuItem("Tools/WorldOS/Capture Integration Frame")]
    public static void CaptureFrame()
    {
        System.IO.Directory.CreateDirectory("Captures");
        string fname = "unity_integration_frame";
        ScreenCapture.CaptureScreenshot($"Captures/{fname}.png", 2);
        Debug.Log($"[Integration] Screenshot saved: Captures/{fname}.png");
    }
}

#endif // UNITY_EDITOR
