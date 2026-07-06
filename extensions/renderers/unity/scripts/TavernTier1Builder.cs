using UnityEngine;
using UnityEditor;
using System.Collections.Generic;

/// <summary>
/// Workstream B — WorldOS Unity Spike, 2026-06-22
///
/// Builds the Tier-1 dimetric block-out from fixture: tavern.scenegrid.json (14×10).
/// Run via:  Tools → WorldOS → Build Tier-1 Tavern Block-out
///           Tools → WorldOS → Clear Tier-1 Scene
///
/// DESIGN:
///   • Floor plane: 14×10 cells @ cell_size=5 ft. In Unity units we use 1 unit = 1 ft.
///   • Grid lies flat at Y=0. Camera is NOT above the grid (spike "grid floats" fix).
///   • Wall cells: thin dark-stone cubes (h=0.15, tinted dark blue-grey).
///   • Prop cells: boxes sized by height_band (tall=3.0, mid=1.8, low=1.0 units),
///     coloured by kind.
///   • Actor tokens: HERO = Capsule (warm amber, scale 0.82); MONSTER = Sphere+Cube
///     stack (muted red-violet). Both at spawn cells, Y offset so they sit on floor.
///   • Elliptical contact shadow: a flattened dark disc under each actor.
///   • 2:1 Dimetric camera: orthographic, positioned high-back, rotation ~26.57° pitch.
///   • Lighting: warm key light from ~210° (hearth side) + ambient tinted #3a3f55.
///   • Backdrop quad: uses existing tavern_backdrop.png with DepthBlitBackdrop shader
///     for Tier-2 depth-blit; falls back to Unlit/Texture if shader not compiled yet.
///   • All GameObjects are parented under a root "TavernTier1" for clean tear-down.
/// </summary>
public class TavernTier1Builder : MonoBehaviour
{
    // ── fixture constants ──────────────────────────────────────────────────
    const int   COLS      = 14;
    const int   ROWS      = 10;
    const float CELL_SIZE = 5f;   // 1 Unity unit = 1 ft, cell = 5 ft square

    // height bands (Y scale in units)
    const float TALL_H = 3.0f;
    const float MID_H  = 1.8f;
    const float LOW_H  = 1.0f;
    const float WALL_H = 4.0f;   // walls: tall enough to be visible above props

    // actor scale vs props
    const float ACTOR_SCALE = 0.82f;

    // Grid origin: we centre the grid so x ∈ [-COLS/2*CELL … +COLS/2*CELL], z ∈ [0…ROWS*CELL]
    // (camera looks from -Z toward +Z)
    static float OriginX => -(COLS * CELL_SIZE) / 2f;
    static float OriginZ = 0f;

    // ── wall cells (hand-parsed from fixture) ─────────────────────────────
    static readonly HashSet<(int c, int r)> WallCells = new HashSet<(int, int)>
    {
        // Row 0 — full top wall
        (0,0),(1,0),(2,0),(3,0),(4,0),(5,0),(6,0),(7,0),(8,0),(9,0),(10,0),(11,0),(12,0),(13,0),
        // Side walls
        (0,1),(13,1),(0,2),(13,2),(0,3),(13,3),(0,4),(13,4),(0,5),(13,5),
        (0,6),(13,6),(0,7),(13,7),(0,8),(13,8),
    };

    // ── prop definitions (from fixture) ───────────────────────────────────
    struct PropDef
    {
        public string id;
        public string kind;
        public (int c, int r)[] cells;
        public string height_band;
    }

    static readonly PropDef[] Props = new PropDef[]
    {
        new PropDef { id="bar",    kind="bar_counter",   cells=new[]{(1,1),(2,1),(3,1),(4,1)},   height_band="tall" },
        new PropDef { id="hearth", kind="stone_hearth",  cells=new[]{(11,1),(12,1)},              height_band="tall" },
        new PropDef { id="table1", kind="round_table",   cells=new[]{(4,4),(5,4)},                height_band="mid"  },
        new PropDef { id="table2", kind="long_table",    cells=new[]{(8,5),(9,5)},                height_band="mid"  },
        new PropDef { id="barrels",kind="barrels",       cells=new[]{(2,7)},                      height_band="low"  },
    };

    // ── spawn cells ───────────────────────────────────────────────────────
    static readonly (int c, int r)[] PartySpawns = { (6,8),(7,8),(8,8) };
    static readonly (int c, int r)[] FoeSpawns   = { (6,2),(8,3) };

    // ── lighting ──────────────────────────────────────────────────────────
    static readonly Color KEY_COLOR     = new Color(1.000f, 0.604f, 0.271f, 1f); // #ff9a45
    static readonly Color AMBIENT_COLOR = new Color(0.227f, 0.247f, 0.333f, 1f); // #3a3f55

    // ─────────────────────────────────────────────────────────────────────
    [MenuItem("Tools/WorldOS/Build Tier-1 Tavern Block-out")]
    public static void Build()
    {
        // Tear down any previous build
        var existing = GameObject.Find("TavernTier1");
        if (existing != null) DestroyImmediate(existing);

        var root = new GameObject("TavernTier1");

        BuildFloor(root);
        BuildWalls(root);
        BuildProps(root);
        BuildActors(root);
        SetupCamera();
        SetupLighting();
        BuildBackdrop(root);

        // Flush
        EditorUtility.SetDirty(root);
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(
            UnityEngine.SceneManagement.SceneManager.GetActiveScene());

        Debug.Log("[Tier-1] Build complete — floor/walls/props/actors/camera/lighting/backdrop.");
    }

    [MenuItem("Tools/WorldOS/Clear Tier-1 Scene")]
    public static void Clear()
    {
        var existing = GameObject.Find("TavernTier1");
        if (existing != null)
        {
            DestroyImmediate(existing);
            Debug.Log("[Tier-1] Cleared TavernTier1 root.");
        }
        else
        {
            Debug.Log("[Tier-1] Nothing to clear.");
        }
    }

    // ── FLOOR ─────────────────────────────────────────────────────────────
    static void BuildFloor(GameObject root)
    {
        var go = GameObject.CreatePrimitive(PrimitiveType.Plane);
        go.name = "Floor_Grid";
        go.transform.SetParent(root.transform, false);

        // Plane primitive is 10×10 by default (10 Unity units).
        // We want COLS*CELL_SIZE × ROWS*CELL_SIZE = 70×50.
        // Scale: Plane 10 units → scale 7 in X, 5 in Z.
        go.transform.localPosition = new Vector3(0f, 0f, (ROWS * CELL_SIZE) / 2f);
        go.transform.localScale = new Vector3(COLS * CELL_SIZE / 10f, 1f, ROWS * CELL_SIZE / 10f);

        var mat = new Material(Shader.Find("Standard"));
        // Dark stone floor with faint grid tint
        mat.color = new Color(0.20f, 0.18f, 0.16f, 1f);
        mat.SetFloat("_Metallic", 0f);
        mat.SetFloat("_Glossiness", 0.06f);
        go.GetComponent<Renderer>().material = mat;
        var r = go.GetComponent<Renderer>();
        r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        r.receiveShadows = true;

        // Save material
        AssetDatabase.CreateAsset(mat, "Assets/FloorMat_T1.mat");
    }

    // ── WALLS ─────────────────────────────────────────────────────────────
    static void BuildWalls(GameObject root)
    {
        var wallParent = new GameObject("Walls");
        wallParent.transform.SetParent(root.transform, false);

        var mat = MakeMat("WallMat_T1",
            new Color(0.18f, 0.17f, 0.22f, 1f), // dark blue-grey stone
            metallic: 0f, smoothness: 0.05f);

        foreach (var (c, r) in WallCells)
        {
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = $"Wall_{c}_{r}";
            go.transform.SetParent(wallParent.transform, false);
            go.transform.localPosition = CellCenter(c, r, WALL_H);
            go.transform.localScale = new Vector3(CELL_SIZE * 0.98f, WALL_H, CELL_SIZE * 0.98f);
            go.GetComponent<Renderer>().sharedMaterial = mat;
            // Walls cast no shadow (they're thin markers)
            go.GetComponent<Renderer>().shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            go.GetComponent<Renderer>().receiveShadows = false;
        }
    }

    // ── PROPS ─────────────────────────────────────────────────────────────
    static void BuildProps(GameObject root)
    {
        var propParent = new GameObject("Props");
        propParent.transform.SetParent(root.transform, false);

        foreach (var prop in Props)
        {
            float h = HeightFromBand(prop.height_band);
            Color col = PropColor(prop.kind);
            var mat = MakeMat($"PropMat_{prop.id}", col, metallic: 0.05f, smoothness: 0.15f);

            // Build a single merged box spanning all cells of the prop
            if (prop.cells.Length == 1)
            {
                var (c, r) = prop.cells[0];
                var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
                go.name = $"Prop_{prop.id}";
                go.transform.SetParent(propParent.transform, false);
                go.transform.localPosition = CellCenter(c, r, h);
                go.transform.localScale = new Vector3(CELL_SIZE * 0.88f, h, CELL_SIZE * 0.88f);
                go.GetComponent<Renderer>().sharedMaterial = mat;
                go.GetComponent<Renderer>().shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On;
                go.GetComponent<Renderer>().receiveShadows = true;
            }
            else
            {
                // Span a bounding box across all cells
                int minC = int.MaxValue, maxC = int.MinValue;
                int minR = int.MaxValue, maxR = int.MinValue;
                foreach (var (cc, rr) in prop.cells)
                {
                    if (cc < minC) minC = cc; if (cc > maxC) maxC = cc;
                    if (rr < minR) minR = rr; if (rr > maxR) maxR = rr;
                }
                float spanX = (maxC - minC + 1) * CELL_SIZE * 0.90f;
                float spanZ = (maxR - minR + 1) * CELL_SIZE * 0.90f;
                float cx = OriginX + (minC + maxC + 1) * CELL_SIZE * 0.5f;
                float cz = OriginZ + (minR + maxR + 1) * CELL_SIZE * 0.5f;

                var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
                go.name = $"Prop_{prop.id}";
                go.transform.SetParent(propParent.transform, false);
                go.transform.localPosition = new Vector3(cx, h * 0.5f, cz);
                go.transform.localScale = new Vector3(spanX, h, spanZ);
                go.GetComponent<Renderer>().sharedMaterial = mat;
                go.GetComponent<Renderer>().shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On;
                go.GetComponent<Renderer>().receiveShadows = true;
            }
        }
    }

    // ── ACTORS ────────────────────────────────────────────────────────────
    static void BuildActors(GameObject root)
    {
        var actorParent = new GameObject("Actors");
        actorParent.transform.SetParent(root.transform, false);

        // HERO: warm amber capsule (party spawns 0..2)
        // Add emission so faction is clear under flat ambient lighting
        var heroMat = MakeMatWithEmission("HeroMat_T1",
            LerpTowardKey(new Color(0.52f, 0.44f, 0.62f), KEY_COLOR, 0.45f),
            emissionColor: new Color(0.5f, 0.3f, 0.0f) * 0.4f,  // warm amber glow
            metallic: 0f, smoothness: 0.25f, "HeroMat_T1.mat");

        for (int i = 0; i < PartySpawns.Length; i++)
        {
            var (c, r) = PartySpawns[i];
            string label = i == 0 ? "HERO" : $"Party_{i}";
            BuildCapsuleActor(actorParent, label, c, r, heroMat, isHero: true);
        }

        // MONSTER: muted red-violet, cool emission to contrast with warm heroes
        var foeMat = MakeMatWithEmission("FoeMat_T1",
            LerpTowardAmbient(new Color(0.72f, 0.25f, 0.25f), AMBIENT_COLOR, 0.3f),
            emissionColor: new Color(0.1f, 0.1f, 0.4f) * 0.4f,  // cool blue-violet glow
            metallic: 0f, smoothness: 0.20f, "FoeMat_T1.mat");

        for (int i = 0; i < FoeSpawns.Length; i++)
        {
            var (c, r) = FoeSpawns[i];
            string label = i == 0 ? "MONSTER" : $"Foe_{i}";
            BuildSphereActor(actorParent, label, c, r, foeMat);
        }
    }

    static void BuildCapsuleActor(GameObject parent, string name, int c, int r, Material mat, bool isHero)
    {
        var go = GameObject.CreatePrimitive(PrimitiveType.Capsule);
        go.name = name;
        go.transform.SetParent(parent.transform, false);

        float s = CELL_SIZE * ACTOR_SCALE * 0.28f;
        float capH = s * 2.2f;  // capsule is 2 units tall by default; scale accordingly
        go.transform.localScale = new Vector3(s, s * 1.1f, s);
        float yCenter = capH * 0.5f;  // sit on floor
        go.transform.localPosition = new Vector3(
            OriginX + (c + 0.5f) * CELL_SIZE,
            yCenter,
            OriginZ + (r + 0.5f) * CELL_SIZE);

        go.GetComponent<Renderer>().sharedMaterial = mat;
        go.GetComponent<Renderer>().shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On;
        go.GetComponent<Renderer>().receiveShadows = true;

        // Elliptical contact shadow disc
        BuildContactShadow(parent, name + "_Shadow", c, r, s * 1.4f, s * 0.8f);
    }

    static void BuildSphereActor(GameObject parent, string name, int c, int r, Material mat)
    {
        // Monster = sphere on a thin cube "body"
        var body = new GameObject(name);
        body.transform.SetParent(parent.transform, false);

        float s = CELL_SIZE * ACTOR_SCALE * 0.26f;
        float yBase = 0f;

        // Cube body
        var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
        cube.name = name + "_Body";
        cube.transform.SetParent(body.transform, false);
        cube.transform.localScale = new Vector3(s, s * 1.5f, s * 0.9f);
        cube.transform.localPosition = new Vector3(0f, s * 0.75f, 0f);
        cube.GetComponent<Renderer>().sharedMaterial = mat;
        cube.GetComponent<Renderer>().shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On;

        // Sphere head
        var sphere = GameObject.CreatePrimitive(PrimitiveType.Sphere);
        sphere.name = name + "_Head";
        sphere.transform.SetParent(body.transform, false);
        sphere.transform.localScale = new Vector3(s * 0.85f, s * 0.85f, s * 0.85f);
        sphere.transform.localPosition = new Vector3(0f, s * 1.8f, 0f);
        sphere.GetComponent<Renderer>().sharedMaterial = mat;
        sphere.GetComponent<Renderer>().shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On;

        body.transform.localPosition = new Vector3(
            OriginX + (c + 0.5f) * CELL_SIZE,
            yBase,
            OriginZ + (r + 0.5f) * CELL_SIZE);

        // Contact shadow
        BuildContactShadow(parent, name + "_Shadow", c, r, s * 1.3f, s * 0.65f);
    }

    static void BuildContactShadow(GameObject parent, string name, int c, int r, float rx, float rz)
    {
        // Flattened sphere (ovaloid) just above the floor — NOT a big circular blob.
        var go = GameObject.CreatePrimitive(PrimitiveType.Sphere);
        go.name = name;
        go.transform.SetParent(parent.transform, false);

        // Keep ry tiny so it's a flat ellipse, not a blob sphere
        go.transform.localScale = new Vector3(rx * 2f, 0.04f, rz * 2f);
        go.transform.localPosition = new Vector3(
            OriginX + (c + 0.5f) * CELL_SIZE,
            0.01f,   // just above floor plane Y=0
            OriginZ + (r + 0.5f) * CELL_SIZE);

        var mat = new Material(Shader.Find("Standard"));
        mat.color = new Color(0.0f, 0.0f, 0.0f, 0.55f);
        mat.SetFloat("_Mode", 3f);             // Transparent blend mode
        mat.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
        mat.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
        mat.SetInt("_ZWrite", 0);
        mat.DisableKeyword("_ALPHATEST_ON");
        mat.EnableKeyword("_ALPHABLEND_ON");
        mat.DisableKeyword("_ALPHAPREMULTIPLY_ON");
        mat.renderQueue = 3000;

        go.GetComponent<Renderer>().sharedMaterial = mat;
        go.GetComponent<Renderer>().shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        go.GetComponent<Renderer>().receiveShadows = false;
    }

    // ── CAMERA — 2:1 Dimetric ─────────────────────────────────────────────
    static void SetupCamera()
    {
        var camGO = GameObject.Find("Main Camera");
        if (camGO == null)
        {
            camGO = new GameObject("Main Camera");
            camGO.AddComponent<Camera>();
            camGO.tag = "MainCamera";
        }

        var cam = camGO.GetComponent<Camera>();

        // 2:1 dimetric = ~26.57° pitch (atan(0.5))
        // Orthographic projection gives exact dimetric ratios.
        cam.orthographic = true;

        // Grid world bounds:
        //   X: OriginX = -35 to +35 (70 units wide)
        //   Z: 0 to 50  (10 rows × 5)
        //   Camera looks from -Z toward +Z, pitched down 26.57°.
        //
        // CRITICAL: viewport rect must be full-screen to avoid letterbox.
        cam.rect = new Rect(0, 0, 1, 1);

        // OrthoSize: half the projected grid height.
        // Grid depth (Z) projected onto screen height with 26.57° pitch:
        //   50 * cos(26.57°) ≈ 44.7; half = 22.3 → use 18 for tight framing.
        cam.orthographicSize = 18f;

        cam.nearClipPlane = 0.5f;
        cam.farClipPlane  = 400f;

        // 2:1 dimetric pitch = atan(0.5) ≈ 26.565°
        float pitchDeg = Mathf.Rad2Deg * Mathf.Atan(0.5f);  // 26.565°
        float pitchRad = Mathf.Atan(0.5f);

        // Grid centre in world: (0, 0, 25). Camera pulls back 90 units:
        float pullBack = 90f;
        float gridCentreZ = ROWS * CELL_SIZE * 0.5f;   // = 25

        float camY = pullBack * Mathf.Sin(pitchRad);
        float camZ = gridCentreZ - pullBack * Mathf.Cos(pitchRad);

        camGO.transform.position     = new Vector3(0f, camY, camZ);
        camGO.transform.eulerAngles  = new Vector3(pitchDeg, 0f, 0f);

        // Background: dark tavern (backdrop quad covers most of this)
        cam.backgroundColor = new Color(0.06f, 0.05f, 0.07f, 1f);
        cam.clearFlags = CameraClearFlags.SolidColor;
    }

    // ── LIGHTING ──────────────────────────────────────────────────────────
    static void SetupLighting()
    {
        // Key light: warm amber from the hearth side (key_dir_deg=210 → from left-back)
        var lightGO = GameObject.Find("Directional Light");
        if (lightGO == null)
        {
            lightGO = new GameObject("Directional Light");
            lightGO.AddComponent<Light>().type = LightType.Directional;
        }
        var lt = lightGO.GetComponent<Light>();
        lt.color = KEY_COLOR;
        lt.intensity = 1.2f;
        lt.shadows = LightShadows.Soft;
        lt.shadowStrength = 0.55f;
        lt.shadowBias = 0.04f;
        // 210° from North in scene = azimuth rotated: pitch ~35°, yaw +210° (or -150°)
        lightGO.transform.eulerAngles = new Vector3(38f, 210f - 180f, 0f); // pitch 38°, yaw 30°

        // Ambient: cool purple-blue fill
        RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
        RenderSettings.ambientLight = AMBIENT_COLOR;
        RenderSettings.ambientIntensity = 0.9f;
    }

    // ── BACKDROP ──────────────────────────────────────────────────────────
    static void BuildBackdrop(GameObject root)
    {
        // Remove the old floating Backdrop if it exists
        var old = GameObject.Find("Backdrop");
        if (old != null) DestroyImmediate(old);

        var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
        go.name = "Backdrop_DepthBlit";
        go.transform.SetParent(root.transform, false);

        // The backdrop needs to be visible through the camera — it should fill the upper
        // portion of the screen (behind the floor and props, acting as wall+ceiling).
        //
        // Strategy: tilt the backdrop quad to be PERPENDICULAR TO THE CAMERA VIEW DIRECTION.
        // Camera pitch = 26.565° (atan 0.5). Backdrop should pitch by (90° - 26.565°) = 63.435°
        // so its face is parallel to the camera image plane → it fills the screen properly.
        //
        // Position: at the back of the grid (z≈0), centred in X, raised in Y so it
        // covers the wall area and above:
        float pitchRad = Mathf.Atan(0.5f);  // 26.565°
        float backdropTiltDeg = 90f - Mathf.Rad2Deg * pitchRad; // 63.435° — face toward camera

        // Place it at the back wall (z=0) centered vertically
        float backdropZ = -2f;  // slightly behind back wall (row 0)
        float backdropW = COLS * CELL_SIZE * 1.2f;  // 84 units wide
        float backdropH = ROWS * CELL_SIZE * 1.4f;  // 70 units tall
        float backdropY = backdropH * 0.5f - 5f;   // centred, slightly lowered
        go.transform.localPosition = new Vector3(0f, backdropY, backdropZ);
        go.transform.localEulerAngles = new Vector3(backdropTiltDeg, 0f, 0f); // tilt to face camera
        go.transform.localScale    = new Vector3(backdropW, backdropH, 1f);

        // Try the depth-blit shader first; fall back to Unlit/Texture
        var shader = Shader.Find("WorldOS/DepthBlitBackdrop");
        Material mat;
        bool depthBlitAvailable = (shader != null);

        if (depthBlitAvailable)
        {
            mat = new Material(shader);
            mat.SetFloat("_NearClip", 0.1f);
            mat.SetFloat("_FarClip",  400f);
            Debug.Log("[Tier-1] DepthBlitBackdrop shader FOUND — Tier-2 path active.");
        }
        else
        {
            mat = new Material(Shader.Find("Unlit/Texture"));
            Debug.LogWarning("[Tier-1] DepthBlitBackdrop shader not yet compiled — using Unlit/Texture fallback. Real-3D box props provide occlusion.");
        }

        // Load backdrop texture (spike's existing or painterly/)
        Texture2D backdropTex = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/tavern_backdrop.png");
        if (backdropTex == null)
            backdropTex = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/tavern_backdrop.png");
        if (backdropTex != null)
        {
            mat.mainTexture = tex2DSlot(mat, backdropTex, depthBlitAvailable ? "_BackdropTex" : "_MainTex");
        }
        else
        {
            Debug.LogWarning("[Tier-1] No backdrop texture found. Assign Assets/painterly/tavern_backdrop.png.");
        }

        // Render before all geometry so depth-blit populates the Z-buffer first
        mat.renderQueue = depthBlitAvailable ? 999 : 900;
        AssetDatabase.CreateAsset(mat, "Assets/BackdropMat_T1.mat");

        var r = go.GetComponent<Renderer>();
        r.sharedMaterial = mat;
        r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        r.receiveShadows = false;
    }

    // Helper: set texture by property name (handles both shader paths)
    static Texture2D tex2DSlot(Material m, Texture2D tex, string prop)
    {
        if (m.HasProperty(prop)) m.SetTexture(prop, tex);
        else m.mainTexture = tex;
        return tex;
    }

    // ── HELPERS ───────────────────────────────────────────────────────────
    static Vector3 CellCenter(int c, int r, float h)
    {
        float x = OriginX + (c + 0.5f) * CELL_SIZE;
        float z = OriginZ + (r + 0.5f) * CELL_SIZE;
        return new Vector3(x, h * 0.5f, z);
    }

    static float HeightFromBand(string band) => band switch
    {
        "tall" => TALL_H,
        "mid"  => MID_H,
        "low"  => LOW_H,
        _      => MID_H,
    };

    static Color PropColor(string kind) => kind switch
    {
        "bar_counter"  => new Color(0.42f, 0.28f, 0.14f, 1f),  // dark oak
        "stone_hearth" => new Color(0.50f, 0.44f, 0.40f, 1f),  // stone grey-tan
        "round_table"  => new Color(0.48f, 0.32f, 0.16f, 1f),  // mid wood
        "long_table"   => new Color(0.44f, 0.30f, 0.14f, 1f),  // mid wood
        "barrels"      => new Color(0.38f, 0.26f, 0.12f, 1f),  // dark wood
        _              => new Color(0.45f, 0.35f, 0.25f, 1f),
    };

    // Tint a colour toward the key light colour (cohesion pass)
    static Color LerpTowardKey(Color base_, Color key, float t) =>
        Color.Lerp(base_, key, t);

    // Tint a colour toward ambient (shadow-side cohesion)
    static Color LerpTowardAmbient(Color base_, Color amb, float t) =>
        Color.Lerp(base_, amb, t);

    static Material MakeMat(string assetName, Color col, float metallic, float smoothness, string filename = null)
    {
        var mat = new Material(Shader.Find("Standard"));
        mat.color = col;
        mat.SetFloat("_Metallic", metallic);
        mat.SetFloat("_Glossiness", smoothness);
        string path = $"Assets/{filename ?? assetName + ".mat"}";
        AssetDatabase.DeleteAsset(path);
        AssetDatabase.CreateAsset(mat, path);
        return mat;
    }

    static Material MakeMatWithEmission(string assetName, Color col, Color emissionColor,
                                         float metallic, float smoothness, string filename = null)
    {
        var mat = new Material(Shader.Find("Standard"));
        mat.color = col;
        mat.SetFloat("_Metallic", metallic);
        mat.SetFloat("_Glossiness", smoothness);
        mat.EnableKeyword("_EMISSION");
        mat.SetColor("_EmissionColor", emissionColor);
        string path = $"Assets/{filename ?? assetName + ".mat"}";
        AssetDatabase.DeleteAsset(path);
        AssetDatabase.CreateAsset(mat, path);
        return mat;
    }
}
