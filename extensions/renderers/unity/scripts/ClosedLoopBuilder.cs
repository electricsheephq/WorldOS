using UnityEngine;
using UnityEditor;
using System.Collections.Generic;
using System.IO;

/// <summary>
/// CLOSED-LOOP painterly pipeline — WorldOS Unity sprint.
///
/// REPEATABLE / FIXTURE-PARAMETERIZED. The whole pipeline is driven from a
/// SceneGrid fixture JSON (see fixtures/*.scenegrid.json). The game CAMERA is the
/// single registration authority; the next scene is a button-press pointed at a
/// different fixture.
///
///   1. Load fixture -> grid (cols/rows/cell), props (occluders+bands), spawns,
///      lighting. (LoadFixture)
///   2. Lock camera (ortho, dimetric pitch atan(0.5)=26.565deg, fixed 16:9). (LockCamera)
///   3. Render blockout THROUGH that camera -> off-screen 16:9 DEPTH + SEG (+CANNY
///      structure) targets = the ControlNet conditioning. (CaptureConditioning / CaptureStructure)
///   4. (external) Scenario ControlNet paints the plate FROM those captures
///      (tavern_plate_cl.png). See CLOSED-LOOP-PIPELINE.md for exact model+params.
///   5. Display the plate as a CAMERA-LOCKED full-frame quad filling the frustum at
///      the camera's aspect (painted floor lands on grid floor); hide blockout; place
///      invisible depth-only occluder proxies at prop cells; place actors by spawn cell
///      with SCENE-LIT painterly faction mats + soft contact shadows. (AssembleFinal)
///   6. Critic-panel gate (external 3-lens adversarial panel).
///
/// ONE-BUTTON ENTRY: Tools/WorldOS/CL/0 Build Closed-Loop Scene (fixture) runs
/// LockCamera -> CaptureConditioning -> CaptureStructure -> AssembleFinal in order.
/// </summary>
public static class ClosedLoopBuilder
{
    // ==================================================================
    // FIXTURE — single source of truth. Point this at any *.scenegrid.json.
    // ==================================================================
    public const string FixturePath = "/home/unity/worldos-unity/fixtures/tavern.scenegrid.json";

    // ---- camera authority (constant across scenes) ----
    const int   CAP_W = 1344;          // 16:9
    const int   CAP_H = 756;
    const float ORTHO = 18f;
    static readonly Vector3 CAM_POS = new Vector3(0f, 40.25f, -55.5f);

    // ---- segmentation palette (constant) ----
    static readonly Color SEG_FLOOR = new Color(0.20f, 0.55f, 0.25f);
    static readonly Color SEG_WALL  = new Color(0.30f, 0.35f, 0.55f);
    static readonly Color SEG_PROP  = new Color(0.85f, 0.55f, 0.20f);
    static readonly Color SEG_ACTOR = new Color(0.90f, 0.10f, 0.40f);
    static readonly Color SEG_BACK  = new Color(0.04f, 0.04f, 0.06f);

    // height bands (world units)
    const float TALL_H = 3.0f, MID_H = 1.8f, LOW_H = 1.0f;

    // #1284: per-scene FLOOR plane (the painterly plate is FLAT). Actor feet anchor to this constant,
    // NOT a raycast against prop meshes (the sarcophagus-top-wins-the-cast bug). Greybox floor sits at 0.
    const float FLOOR_Y = 0f;

    // ---- actor render tuning (round-1 fixes: belong-in-scene) ----
    const float ACTOR_TARGET_H = 9.6f;   // r11 L4 (BOTH runs, CRITICAL): on the wider v3 plate the actors
                                         // read as thumbnails dwarfed by the hall = "props, not dramatis
                                         // personae". Bump so the hero occupies a PoE2-like fraction of frame
                                         // height (face/armor masses read). Sidecar emits the real world-height
                                         // so the pregate G4 (measured-vs-expected px) re-validates at the new scale.

    // ==================================================================
    // FIXTURE MODEL — parsed from the SceneGrid JSON.
    // ==================================================================
    public class PropDef { public string id; public string band="mid"; public bool occluder=true;
                           public int minC, maxC, minR, maxR; }
    public class Fixture
    {
        public string sceneId="scene";
        public int cols=14, rows=10; public float cell=5f;
        public float OriginX { get { return -(cols * cell) / 2f; } }
        public float OriginZ = 0f;
        public List<PropDef> props = new List<PropDef>();
        // walls: cells flagged type=="wall" (for the enclosure line-art); we derive
        // the room rectangle from grid bounds, so we only need props + spawns + lighting.
        public List<int[]> party = new List<int[]>();   // [c,r]
        public List<int[]> foes  = new List<int[]>();
        public Color keyColor = new Color(1.000f, 0.604f, 0.271f, 1f);     // #ff9a45
        public Color ambientColor = new Color(0.227f, 0.247f, 0.333f, 1f); // #3a3f55
        public float keyDirDeg = 210f;
    }

    static Fixture _fx;
    public static Fixture Fx { get { if (_fx == null) _fx = LoadFixture(FixturePath); return _fx; } }

    public static Fixture LoadFixture(string path)
    {
        var fx = new Fixture();
        if (!File.Exists(path)) { Debug.LogError("[CL] fixture not found: " + path); return fx; }
        var root = MiniJson.Parse(File.ReadAllText(path)) as Dictionary<string, object>;
        if (root == null) { Debug.LogError("[CL] fixture parse failed: " + path); return fx; }

        fx.sceneId = Str(root, "scene_id", "scene");
        if (root.TryGetValue("grid", out var go) && go is Dictionary<string, object> grid)
        {
            fx.cols = (int)Num(grid, "cols", 14);
            fx.rows = (int)Num(grid, "rows", 10);
            fx.cell = (float)Num(grid, "cell_size_ft", 5);
        }
        // props: each has id, height_band, occluder, and a cells[] of [c,r]
        if (root.TryGetValue("props", out var po) && po is List<object> plist)
        {
            foreach (var pe in plist)
            {
                if (pe is Dictionary<string, object> pd)
                {
                    var def = new PropDef { id = Str(pd, "id", "prop"),
                                           band = Str(pd, "height_band", "mid"),
                                           occluder = Bool(pd, "occluder", true) };
                    int minC=int.MaxValue,maxC=int.MinValue,minR=int.MaxValue,maxR=int.MinValue;
                    if (pd.TryGetValue("cells", out var co) && co is List<object> cells)
                    {
                        foreach (var ce in cells)
                            if (ce is List<object> cr && cr.Count >= 2)
                            {
                                int c = (int)System.Convert.ToDouble(cr[0]);
                                int r = (int)System.Convert.ToDouble(cr[1]);
                                minC=Mathf.Min(minC,c); maxC=Mathf.Max(maxC,c);
                                minR=Mathf.Min(minR,r); maxR=Mathf.Max(maxR,r);
                            }
                    }
                    def.minC=minC; def.maxC=maxC; def.minR=minR; def.maxR=maxR;
                    if (minC != int.MaxValue) fx.props.Add(def);
                }
            }
        }
        // spawns
        if (root.TryGetValue("spawns", out var so) && so is Dictionary<string, object> spawns)
        {
            fx.party = CellList(spawns, "party");
            fx.foes  = CellList(spawns, "foes");
        }
        // lighting
        if (root.TryGetValue("lighting", out var lo) && lo is Dictionary<string, object> lit)
        {
            fx.keyDirDeg = (float)Num(lit, "key_dir_deg", 210);
            fx.keyColor = HexColor(Str(lit, "key_color", "#ff9a45"), fx.keyColor);
            fx.ambientColor = HexColor(Str(lit, "ambient_color", "#3a3f55"), fx.ambientColor);
        }
        Debug.Log("[CL] Loaded fixture '" + fx.sceneId + "': grid " + fx.cols + "x" + fx.rows +
                  " cell " + fx.cell + ", props " + fx.props.Count + ", party " + fx.party.Count +
                  ", foes " + fx.foes.Count + ", keyDir " + fx.keyDirDeg);
        return fx;
    }

    static List<int[]> CellList(Dictionary<string, object> d, string key)
    {
        var outl = new List<int[]>();
        if (d.TryGetValue(key, out var o) && o is List<object> list)
            foreach (var e in list)
                if (e is List<object> cr && cr.Count >= 2)
                    outl.Add(new[] { (int)System.Convert.ToDouble(cr[0]), (int)System.Convert.ToDouble(cr[1]) });
        return outl;
    }
    static string Str(Dictionary<string, object> d, string k, string def)
        => d.TryGetValue(k, out var v) && v != null ? v.ToString() : def;
    static double Num(Dictionary<string, object> d, string k, double def)
        => d.TryGetValue(k, out var v) && v != null ? System.Convert.ToDouble(v) : def;
    static bool Bool(Dictionary<string, object> d, string k, bool def)
        => d.TryGetValue(k, out var v) && v is bool b ? b : def;
    static Color HexColor(string hex, Color def)
        => ColorUtility.TryParseHtmlString(hex, out var c) ? c : def;

    // convenience accessors (fixture-driven)
    static int   COLS      { get { return Fx.cols; } }
    static int   ROWS      { get { return Fx.rows; } }
    static float CELL_SIZE { get { return Fx.cell; } }
    static float OriginX   { get { return Fx.OriginX; } }
    static float OriginZ   { get { return Fx.OriginZ; } }

    // ---- grid -> world helpers (R2 fix: the Scenario plate paints the BACK wall
    // (row 0: bar/hearth/door) at the TOP of the frame, so row 0 must map to the
    // FAR Z (top) and the entrance row (rows-1) to the NEAR Z (bottom/front near
    // camera). Earlier code mapped row 0 -> near Z, inverting actors+occluders
    // relative to the painted plate. CellX is unchanged; CellZ is flipped. ----
    static float CellX(float col) { return OriginX + (col + 0.5f) * CELL_SIZE; }
    static float CellZ(float row) { return OriginZ + (ROWS - row - 0.5f) * CELL_SIZE; }
    // span-center for a multi-cell prop (min..max inclusive), flipped on Z
    static float PropCenterX(PropDef p) { return OriginX + (p.minC + p.maxC + 1) * CELL_SIZE * 0.5f; }
    static float PropCenterZ(PropDef p) { return OriginZ + (ROWS - (p.minR + p.maxR + 1) * 0.5f) * CELL_SIZE; }

    // #1284: presentation-only prop-cell NUDGE. A cell is BLOCKED if it is out of bounds or inside any
    // prop footprint (the fixture's props ARE the impassable set — sarcophagus/columns/altar). If an
    // actor's LOGICAL cell is blocked, render it at the nearest walkable neighbor so it never stands ON
    // the painted prop. Deterministic lowest-index pick: orthogonal (dist 1) before diagonal, fixed order.
    // The logical spawn cell (Fx.party/foes) is never mutated — a pure view nudge (engine = sole writer).
    static bool IsBlockedCell(int c, int r)
    {
        if (c < 0 || c >= COLS || r < 0 || r >= ROWS) return true;
        foreach (var p in Fx.props)
            if (c >= p.minC && c <= p.maxC && r >= p.minR && r <= p.maxR) return true;
        return false;
    }
    static readonly int[][] NUDGE_NB = new int[][] {
        new[]{0,-1}, new[]{-1,0}, new[]{1,0}, new[]{0,1}, new[]{-1,-1}, new[]{1,-1}, new[]{-1,1}, new[]{1,1} };
    static int[] NudgeCell(int c, int r)
    {
        if (!IsBlockedCell(c, r)) return new[] { c, r };
        foreach (var d in NUDGE_NB) { int nc = c + d[0], nr = r + d[1]; if (!IsBlockedCell(nc, nr)) return new[] { nc, nr }; }
        return new[] { c, r };   // no free neighbor -> leave in place (deterministic fallback)
    }

    // ==================================================================
    // ARM toggle (bake-off) — sets which actor pipeline AssembleFinal uses.
    // ==================================================================
    [MenuItem("Tools/WorldOS/CL/Arm 1B (live 3D)")]
    public static void SetArm1B() { Arm = "1B"; Debug.Log("[CL] Arm = 1B (live rigged 3D actors)"); }
    [MenuItem("Tools/WorldOS/CL/Arm 1A (sprite)")]
    public static void SetArm1A() { Arm = "1A"; Debug.Log("[CL] Arm = 1A (painterly sprite billboards)"); }

    // ==================================================================
    // ONE-BUTTON ENTRY POINT — full pipeline from the fixture.
    // ==================================================================
    [MenuItem("Tools/WorldOS/CL/0 Build Closed-Loop Scene (fixture)")]
    public static void BuildClosedLoopScene()
    {
        _fx = LoadFixture(FixturePath);
        BuildBlockout();        // fixture-driven blockout (walls + props as boxes)
        LockCamera();
        CaptureConditioning();
        CaptureStructure();
        AssembleFinal();
        EditorUtility.SetDirty(GameObject.Find("BlockoutRoot_" + Fx.sceneId) ?? new GameObject());
        UnityEditor.SceneManagement.EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo();
        Debug.Log("[CL] One-button build complete for fixture '" + Fx.sceneId +
                  "'. (Plate must be (re)generated by Scenario from the captures for a painted result.)");
    }

    // ==================================================================
    // BUILD BLOCKOUT from fixture — walls + floor + prop boxes
    // Creates "BlockoutRoot_<scene_id>" (replaces any prior blockout).
    // CaptureConditioning searches for this root OR the legacy "TavernTier1".
    // ==================================================================
    [MenuItem("Tools/WorldOS/CL/0a Build Blockout Only (fixture)")]
    public static void BuildBlockout()
    {
        var f = Fx;
        string rootName = "BlockoutRoot_" + f.sceneId;
        var old = GameObject.Find(rootName);
        if (old != null) Object.DestroyImmediate(old);

        var root = new GameObject(rootName);

        // Floor material (flat grey floor)
        var floorMat = AssetDatabase.LoadAssetAtPath<Material>("Assets/FloorMat_T1.mat")
                       ?? new Material(Shader.Find("Standard"));
        // Wall material
        var wallMat  = AssetDatabase.LoadAssetAtPath<Material>("Assets/WallMat_T1.mat")
                       ?? new Material(Shader.Find("Standard"));
        // Prop material
        var propMat  = new Material(Shader.Find("Standard"));
        propMat.color = new Color(0.60f, 0.48f, 0.30f);

        float wallH = TALL_H;
        float floorThick = 0.05f;

        // 1. Floor plane — one big quad at y=0
        var floor = GameObject.CreatePrimitive(PrimitiveType.Cube);
        floor.name = "Floor";
        floor.transform.SetParent(root.transform, false);
        Object.DestroyImmediate(floor.GetComponent<Collider>());
        float floorW = f.cols * f.cell;
        float floorD = f.rows * f.cell;
        floor.transform.localPosition = new Vector3(0f, -floorThick * 0.5f, floorD * 0.5f + f.OriginZ);
        floor.transform.localScale = new Vector3(floorW, floorThick, floorD);
        floor.GetComponent<Renderer>().sharedMaterial = floorMat;

        // 2. Perimeter walls — derive from cells flagged wall (or use the fixture border)
        // We parse the full fixture JSON for wall cells.
        var wallParent = new GameObject("Walls");
        wallParent.transform.SetParent(root.transform, false);

        string rawJson = File.ReadAllText(FixturePath);
        var parsed = MiniJson.Parse(rawJson) as System.Collections.Generic.Dictionary<string, object>;
        if (parsed != null && parsed.TryGetValue("cells", out var co) && co is System.Collections.Generic.List<object> cellList)
        {
            foreach (var ce in cellList)
            {
                if (ce is System.Collections.Generic.Dictionary<string, object> cd)
                {
                    string typ = cd.TryGetValue("type", out var tv) ? tv?.ToString() : "floor";
                    if (typ != "wall") continue;
                    int c = (int)System.Convert.ToDouble(cd.TryGetValue("c", out var cv) ? cv : 0);
                    int r = (int)System.Convert.ToDouble(cd.TryGetValue("r", out var rv) ? rv : 0);
                    float wx = f.OriginX + (c + 0.5f) * f.cell;
                    float wz = f.OriginZ + (f.rows - r - 0.5f) * f.cell;
                    var wall = GameObject.CreatePrimitive(PrimitiveType.Cube);
                    wall.name = "Wall_" + c + "_" + r;
                    wall.transform.SetParent(wallParent.transform, false);
                    Object.DestroyImmediate(wall.GetComponent<Collider>());
                    wall.transform.localPosition = new Vector3(wx, wallH * 0.5f, wz);
                    wall.transform.localScale = new Vector3(f.cell * 0.98f, wallH, f.cell * 0.98f);
                    wall.GetComponent<Renderer>().sharedMaterial = wallMat;
                }
            }
        }

        // 3. Prop boxes from fixture props
        var propParent = new GameObject("Props");
        propParent.transform.SetParent(root.transform, false);
        foreach (var p in f.props)
        {
            float hh = Band(p.band);
            float spanX = (p.maxC - p.minC + 1) * f.cell * 0.85f;
            float spanZ = (p.maxR - p.minR + 1) * f.cell * 0.85f;
            float cx = PropCenterX(p);
            float cz = PropCenterZ(p);
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = "Prop_" + p.id;
            go.transform.SetParent(propParent.transform, false);
            Object.DestroyImmediate(go.GetComponent<Collider>());
            go.transform.localPosition = new Vector3(cx, hh * 0.5f, cz);
            go.transform.localScale = new Vector3(spanX, hh, spanZ);
            var pmat = new Material(Shader.Find("Standard"));
            // colour by kind: brazier=orange, pillar=grey, rubble=brown, sarcophagus=light stone
            Color pc = p.id.Contains("brazier") ? new Color(0.8f, 0.4f, 0.1f)
                     : p.id.Contains("pillar")  ? new Color(0.55f, 0.52f, 0.48f)
                     : p.id.Contains("rubble")  ? new Color(0.42f, 0.38f, 0.30f)
                     : p.id.Contains("sarcophagus") ? new Color(0.70f, 0.65f, 0.55f)
                     : new Color(0.50f, 0.45f, 0.35f);
            pmat.color = pc;
            go.GetComponent<Renderer>().sharedMaterial = pmat;
        }

        // Mark dirty + save
        EditorUtility.SetDirty(root);
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(
            UnityEngine.SceneManagement.SceneManager.GetActiveScene());
        AssetDatabase.Refresh();
        Debug.Log("[CL] BuildBlockout: '" + rootName + "' walls=" + wallParent.transform.childCount +
                  " props=" + propParent.transform.childCount);
    }

    // ==================================================================
    // CAMERA LOCK
    // ==================================================================
    static Camera LockCamera()
    {
        var cam = Camera.main;
        if (cam == null)
        {
            var go = new GameObject("Main Camera");
            go.tag = "MainCamera";
            cam = go.AddComponent<Camera>();
        }
        cam.orthographic = true;
        cam.orthographicSize = ORTHO;
        float pitch = Mathf.Rad2Deg * Mathf.Atan(0.5f); // 26.565 (dimetric 2:1)
        cam.transform.position = CAM_POS;
        cam.transform.eulerAngles = new Vector3(pitch, 0f, 0f);
        cam.nearClipPlane = 0.1f;
        cam.farClipPlane  = 200f;
        cam.aspect = (float)CAP_W / CAP_H;
        return cam;
    }

    // ==================================================================
    // STEP 3 — render conditioning maps THROUGH the camera
    // ==================================================================
    [MenuItem("Tools/WorldOS/CL/1 Capture Conditioning (depth+seg)")]
    public static void CaptureConditioning()
    {
        var cam = LockCamera();
        // Find the blockout root — fixture-parameterized name first, legacy TavernTier1 fallback.
        var t1 = GameObject.Find("BlockoutRoot_" + Fx.sceneId)
                 ?? GameObject.Find("TavernTier1");
        if (t1 == null) { Debug.LogError("[CL] Blockout root not found (run 'Build Blockout Only' first)."); return; }

        var rends = t1.GetComponentsInChildren<Renderer>(true);
        var prevEnabled = new Dictionary<Renderer, bool>();
        var prevMats = new Dictionary<Renderer, Material[]>();
        foreach (var r in rends) { prevEnabled[r] = r.enabled; prevMats[r] = r.sharedMaterials; }

        var bd = GameObject.Find("Backdrop_DepthBlit");
        bool bdPrev = false; Renderer bdR = null;
        if (bd != null) { bdR = bd.GetComponent<Renderer>(); if (bdR != null) { bdPrev = bdR.enabled; bdR.enabled = false; } }
        var intRoot = GameObject.Find("IntegrationRoot");
        bool intPrev = false; if (intRoot != null) { intPrev = intRoot.activeSelf; intRoot.SetActive(false); }

        var rt = new RenderTexture(CAP_W, CAP_H, 24, RenderTextureFormat.ARGB32);
        rt.Create();
        var prevTgt = cam.targetTexture; var prevCF = cam.clearFlags; var prevBG = cam.backgroundColor;
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.targetTexture = rt;

        var segSh = Shader.Find("WorldOS/CaptureSeg");
        var mFloor = new Material(segSh); mFloor.SetColor("_SegColor", SEG_FLOOR);
        var mWall  = new Material(segSh); mWall.SetColor("_SegColor", SEG_WALL);
        var mProp  = new Material(segSh); mProp.SetColor("_SegColor", SEG_PROP);
        var mActor = new Material(segSh); mActor.SetColor("_SegColor", SEG_ACTOR);
        ApplyCategoryMaterials(rends, mFloor, mWall, mProp, mActor);
        cam.backgroundColor = SEG_BACK;
        cam.Render();
        SaveRT(rt, "Assets/painterly/cap_seg.png");

        var depthSh = Shader.Find("WorldOS/CaptureDepth");
        var mDepth = new Material(depthSh);
        mDepth.SetFloat("_NearD", 38f);
        mDepth.SetFloat("_FarD", 92f);
        ApplyUniformMaterial(rends, mDepth);
        cam.backgroundColor = Color.black;
        cam.Render();
        SaveRT(rt, "Assets/painterly/cap_depth.png");

        cam.targetTexture = prevTgt; cam.clearFlags = prevCF; cam.backgroundColor = prevBG;
        rt.Release();
        foreach (var r in rends) { r.enabled = prevEnabled[r]; r.sharedMaterials = prevMats[r]; }
        if (bdR != null) bdR.enabled = bdPrev;
        if (intRoot != null) intRoot.SetActive(intPrev);
        AssetDatabase.Refresh();
        Debug.Log("[CL] Captured cap_depth.png + cap_seg.png at " + CAP_W + "x" + CAP_H + " (16:9).");
    }

    // ==================================================================
    // STRUCTURE capture — bright dimetric floor lattice + prop/wall edges
    // on black, for CANNY ControlNet. Floor lattice now extends to the
    // FRAME-BOTTOM cells so the painted floor is continuous (R1 L1 fix).
    // ==================================================================
    [MenuItem("Tools/WorldOS/CL/1b Capture Structure (canny line-art)")]
    public static void CaptureStructure()
    {
        var cam = LockCamera();

        var tmp = new GameObject("__StructCap");
        var lineSh = Shader.Find("WorldOS/CaptureSeg");
        var lineMat = new Material(lineSh);
        lineMat.SetColor("_SegColor", Color.white);

        for (int c = 0; c <= COLS; c++)
        {
            float x = OriginX + c * CELL_SIZE;
            AddLine(tmp, lineMat, new Vector3(x, 0.02f, OriginZ),
                    new Vector3(x, 0.02f, OriginZ + ROWS * CELL_SIZE), 0.10f);
        }
        for (int r = 0; r <= ROWS; r++)
        {
            float z = OriginZ + r * CELL_SIZE;
            AddLine(tmp, lineMat, new Vector3(OriginX, 0.02f, z),
                    new Vector3(OriginX + COLS * CELL_SIZE, 0.02f, z), 0.10f);
        }
        foreach (var p in Fx.props)
        {
            float hh = Band(p.band);
            float x0 = OriginX + p.minC * CELL_SIZE, x1 = OriginX + (p.maxC + 1) * CELL_SIZE;
            float z0 = OriginZ + p.minR * CELL_SIZE, z1 = OriginZ + (p.maxR + 1) * CELL_SIZE;
            AddBoxEdges(tmp, lineMat, x0, x1, z0, z1, hh, 0.14f);
        }
        float wx0 = OriginX, wx1 = OriginX + COLS * CELL_SIZE;
        float wz0 = OriginZ, wz1 = OriginZ + ROWS * CELL_SIZE;
        float wh = TALL_H;
        AddLine(tmp, lineMat, new Vector3(wx0, wh, wz0), new Vector3(wx1, wh, wz0), 0.18f);
        AddLine(tmp, lineMat, new Vector3(wx0, 0, wz0), new Vector3(wx0, wh, wz0), 0.18f);
        AddLine(tmp, lineMat, new Vector3(wx1, 0, wz0), new Vector3(wx1, wh, wz0), 0.18f);
        AddLine(tmp, lineMat, new Vector3(wx0, wh, wz0), new Vector3(wx0, wh, wz1), 0.18f);
        AddLine(tmp, lineMat, new Vector3(wx1, wh, wz0), new Vector3(wx1, wh, wz1), 0.18f);

        var t1 = GameObject.Find("BlockoutRoot_" + Fx.sceneId) ?? GameObject.Find("TavernTier1");
        var rends = t1 != null ? t1.GetComponentsInChildren<Renderer>(true) : new Renderer[0];
        var prev = new Dictionary<Renderer, bool>();
        foreach (var r in rends) { prev[r] = r.enabled; r.enabled = false; }
        var intRoot = GameObject.Find("IntegrationRoot");
        bool ip = false; if (intRoot != null) { ip = intRoot.activeSelf; intRoot.SetActive(false); }
        var clRoot = GameObject.Find("ClosedLoopRoot");
        bool cp = false; if (clRoot != null) { cp = clRoot.activeSelf; clRoot.SetActive(false); }

        var rt = new RenderTexture(CAP_W, CAP_H, 24, RenderTextureFormat.ARGB32);
        rt.Create();
        var pT = cam.targetTexture; var pCF = cam.clearFlags; var pBG = cam.backgroundColor;
        cam.clearFlags = CameraClearFlags.SolidColor;
        cam.backgroundColor = Color.black;
        cam.targetTexture = rt;
        cam.Render();
        SaveRT(rt, "Assets/painterly/cap_struct.png");

        cam.targetTexture = pT; cam.clearFlags = pCF; cam.backgroundColor = pBG;
        rt.Release();
        foreach (var r in rends) r.enabled = prev[r];
        if (intRoot != null) intRoot.SetActive(ip);
        if (clRoot != null) clRoot.SetActive(cp);
        Object.DestroyImmediate(tmp);
        AssetDatabase.Refresh();
        Debug.Log("[CL] Captured cap_struct.png (dimetric lattice + prop/wall edges) for canny.");
    }

    static void AddLine(GameObject parent, Material m, Vector3 a, Vector3 b, float w)
    {
        var go = new GameObject("ln");
        go.transform.SetParent(parent.transform, false);
        var lr = go.AddComponent<LineRenderer>();
        lr.useWorldSpace = true;
        lr.sharedMaterial = m;
        lr.startWidth = w; lr.endWidth = w;
        lr.positionCount = 2;
        lr.SetPosition(0, a); lr.SetPosition(1, b);
        lr.numCapVertices = 0;
        lr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
    }

    static void AddBoxEdges(GameObject parent, Material m, float x0, float x1, float z0, float z1, float h, float w)
    {
        AddLine(parent, m, new Vector3(x0, 0.03f, z0), new Vector3(x1, 0.03f, z0), w);
        AddLine(parent, m, new Vector3(x1, 0.03f, z0), new Vector3(x1, 0.03f, z1), w);
        AddLine(parent, m, new Vector3(x1, 0.03f, z1), new Vector3(x0, 0.03f, z1), w);
        AddLine(parent, m, new Vector3(x0, 0.03f, z1), new Vector3(x0, 0.03f, z0), w);
        AddLine(parent, m, new Vector3(x0, h, z0), new Vector3(x1, h, z0), w);
        AddLine(parent, m, new Vector3(x1, h, z0), new Vector3(x1, h, z1), w);
        AddLine(parent, m, new Vector3(x1, h, z1), new Vector3(x0, h, z1), w);
        AddLine(parent, m, new Vector3(x0, h, z1), new Vector3(x0, h, z0), w);
        AddLine(parent, m, new Vector3(x0, 0.03f, z0), new Vector3(x0, h, z0), w);
        AddLine(parent, m, new Vector3(x1, 0.03f, z0), new Vector3(x1, h, z0), w);
        AddLine(parent, m, new Vector3(x1, 0.03f, z1), new Vector3(x1, h, z1), w);
        AddLine(parent, m, new Vector3(x0, 0.03f, z1), new Vector3(x0, h, z1), w);
    }

    static string CatOf(Renderer r)
    {
        var t = r.transform;
        while (t != null)
        {
            string n = t.name.ToLower();
            if (n.Contains("backdrop")) return "back";
            if (n.Contains("floor")) return "floor";
            if (n.Contains("wall")) return "wall";
            if (n.Contains("prop")) return "prop";
            if (n.Contains("hero") || n.Contains("monster") || n.Contains("party") ||
                n.Contains("foe") || n.Contains("actor")) return "actor";
            t = t.parent;
        }
        return "floor";
    }

    static void ApplyCategoryMaterials(Renderer[] rends, Material f, Material w, Material p, Material a)
    {
        foreach (var r in rends)
        {
            string c = CatOf(r);
            if (c == "back") { r.enabled = false; continue; }
            r.enabled = true;
            Material m = c == "wall" ? w : c == "prop" ? p : c == "actor" ? a : f;
            var arr = new Material[r.sharedMaterials.Length];
            for (int i = 0; i < arr.Length; i++) arr[i] = m;
            r.sharedMaterials = arr;
        }
    }

    static void ApplyUniformMaterial(Renderer[] rends, Material m)
    {
        foreach (var r in rends)
        {
            if (CatOf(r) == "back") { r.enabled = false; continue; }
            r.enabled = true;
            var arr = new Material[r.sharedMaterials.Length];
            for (int i = 0; i < arr.Length; i++) arr[i] = m;
            r.sharedMaterials = arr;
        }
    }

    static void SaveRT(RenderTexture rt, string path)
    {
        var prev = RenderTexture.active;
        RenderTexture.active = rt;
        var tex = new Texture2D(rt.width, rt.height, TextureFormat.RGB24, false);
        tex.ReadPixels(new Rect(0, 0, rt.width, rt.height), 0, 0);
        tex.Apply();
        File.WriteAllBytes(path, tex.EncodeToPNG());
        RenderTexture.active = prev;
        Object.DestroyImmediate(tex);
    }

    // ==================================================================
    // STEP 5-6 — assemble the final closed-loop frame (fixture-driven)
    // ==================================================================
    [MenuItem("Tools/WorldOS/CL/2 Assemble Final Frame")]
    public static void AssembleFinal()
    {
        var cam = LockCamera();

        var oldRoot = GameObject.Find("ClosedLoopRoot");
        if (oldRoot != null) Object.DestroyImmediate(oldRoot);
        var legacy = GameObject.Find("IntegrationRoot");
        if (legacy != null) legacy.SetActive(false);

        var root = new GameObject("ClosedLoopRoot");

        // Hide both the legacy TavernTier1 blockout and any fixture-parameterized blockout root.
        foreach (string bname in new[] { "TavernTier1", "BlockoutRoot_" + Fx.sceneId })
        {
            var bt = GameObject.Find(bname);
            if (bt != null)
                foreach (var r in bt.GetComponentsInChildren<Renderer>(true))
                    r.enabled = false;
        }

        BuildCameraLockedPlate(root, cam);
        BuildOccluderProxies(root);
        SetupLighting(); // lights BEFORE actors so the relit material samples them
        PlaceActors(root);

        EditorUtility.SetDirty(root);
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(
            UnityEngine.SceneManagement.SceneManager.GetActiveScene());
        Debug.Log("[CL] Final frame assembled — camera-locked plate + occluders + scene-lit actors.");
    }

    // ---- camera-locked full-frame plate ----
    static void BuildCameraLockedPlate(GameObject root, Camera cam)
    {
        // Load the scene-specific plate (e.g. dungeon_plate_cl.png), fallback to tavern, then cap_seg.
        string sceneId = Fx.sceneId.Replace("world-multiscene:", "").Replace(":", "_");
        Texture2D tex = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/" + sceneId + "_plate_cl.png");
        if (tex == null) tex = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/tavern_plate_cl.png");
        if (tex == null) tex = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/cap_seg.png");

        // LEVER 2 — paint the plate with the SAME painterly recipe as the actors (kuwahara flatten +
        // soft value-posterize + matched directional brush-grain + palette-snap) so the floor and the
        // figures read as ONE painting, not "posterized actors on a flat photo floor". Falls back to
        // the flat BackdropUnlit if the painterly shader fails to compile.
        var sh = Shader.Find("WorldOS/PainterlyBackdrop");
        bool painterlyPlate = (sh != null && sh.isSupported);
        if (!painterlyPlate) { sh = Shader.Find("WorldOS/BackdropUnlit"); Debug.LogWarning("[CL] PainterlyBackdrop unavailable; flat plate fallback."); }
        var mat = new Material(sh);
        if (tex != null) mat.mainTexture = tex;
        if (painterlyPlate)
        {
            // Matched to PainterlyActor's plate-side numbers but LOWER strength (the plate already has
            // Scenario paint DNA — we add brush tooth + value masses, not a full repaint).
            // R5 L6 CRITICAL: kuwahara 1.4 still read as "plastic mush, no brush hierarchy" -> drop to 0.9
            // (stop the blob-melt, keep crisp form edges); the directional grain carries the stroke read.
            mat.SetFloat("_Kuwahara",       0.9f);
            mat.SetFloat("_Posterize",      14.0f);  // R5 L6: more planes so a MID value tier survives (masses)
            mat.SetFloat("_PosterStrength", 0.32f);  // R5 L6: lighter mix so mids aren't collapsed
            mat.SetFloat("_BrushStrength",  0.18f);  // R5 L6: a bit more directional stroke read
            mat.SetFloat("_BrushScale",     52.0f);
            mat.SetFloat("_PaletteSnap",    0.14f);
            mat.SetFloat("_Contrast",       1.08f);
            mat.SetFloat("_Saturation",     1.08f);
            // R5 L5/L6 TONAL REPAIR (stronger cool counterpoint + broader floor lift):
            mat.SetFloat("_Exposure",       1.40f);  // keep the playable floor; -0.02 to ease the blown center
            mat.SetFloat("_ShadowLift",     0.075f); // R5 L5: lift the PERIMETER void more (was crushed at edges)
            mat.SetColor("_ShadowTint",     new Color(0.22f, 0.34f, 0.46f, 1f)); // R5 L6: real desaturated TEAL/SLATE (was near-neutral)
            mat.SetFloat("_ShadowTintAmt",  0.78f);  // R5 L6 CRITICAL: much stronger cool to BREAK the monochrome amber
        }
        mat.renderQueue = 900;
        AssetDatabase.DeleteAsset("Assets/BackdropMat_CL.mat");
        AssetDatabase.CreateAsset(mat, "Assets/BackdropMat_CL.mat");

        var plate = GameObject.CreatePrimitive(PrimitiveType.Quad);
        plate.name = "Plate_CL";
        Object.DestroyImmediate(plate.GetComponent<Collider>());
        plate.transform.SetParent(root.transform, false);
        var rend = plate.GetComponent<Renderer>();
        rend.sharedMaterial = mat;
        rend.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        rend.receiveShadows = false;

        // CAMERA-LOCKED: parent to camera, size to exactly fill the ortho frustum.
        plate.transform.SetParent(cam.transform, false);
        float dist = 80f;
        float h = 2f * cam.orthographicSize;
        float w = h * cam.aspect;
        plate.transform.localPosition = new Vector3(0f, 0f, dist);
        plate.transform.localScale = new Vector3(w, h, 1f);
        plate.transform.localRotation = Quaternion.Euler(0f, 180f, 0f); // textured face -> camera
        mat.mainTextureScale = new Vector2(-1f, 1f);
        mat.mainTextureOffset = new Vector2(1f, 0f);

        Debug.Log("[CL] Camera-locked plate: " + (tex != null ? tex.name : "NONE") +
                  " size=(" + w.ToString("F1") + "," + h.ToString("F1") + ") dist=" + dist);
    }

    // ---- invisible depth-only occluder proxies (fixture props) ----
    static void BuildOccluderProxies(GameObject root)
    {
        var sh = Shader.Find("WorldOS/OccluderDepthOnly");
        Material mat;
        if (sh != null && sh.isSupported)
        {
            mat = new Material(sh);
            mat.renderQueue = 1999;
            Debug.Log("[CL] Occluders: depth-only shader OK.");
        }
        else
        {
            mat = new Material(Shader.Find("Standard"));
            mat.color = new Color(0.05f, 0.04f, 0.03f, 1f);
            mat.renderQueue = 1999;
            Debug.LogWarning("[CL] Occluders: depth-only shader unavailable; visible boxes.");
        }
        AssetDatabase.DeleteAsset("Assets/OccluderMat.mat");
        AssetDatabase.CreateAsset(mat, "Assets/OccluderMat.mat");

        var parent = new GameObject("OccluderProxies");
        parent.transform.SetParent(root.transform, false);
        foreach (var p in Fx.props)
        {
            if (!p.occluder) continue;
            float hh = Band(p.band);
            float spanX = (p.maxC - p.minC + 1) * CELL_SIZE * 0.92f;
            float spanZ = (p.maxR - p.minR + 1) * CELL_SIZE * 0.92f;
            float cx = PropCenterX(p);
            float cz = PropCenterZ(p);
            var go = GameObject.CreatePrimitive(PrimitiveType.Cube);
            go.name = "Occluder_" + p.id;
            go.transform.SetParent(parent.transform, false);
            go.transform.localPosition = new Vector3(cx, hh * 0.5f, cz);
            go.transform.localScale = new Vector3(spanX, hh, spanZ);
            var r = go.GetComponent<Renderer>();
            r.sharedMaterial = mat;
            r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            r.receiveShadows = false;
            Object.DestroyImmediate(go.GetComponent<BoxCollider>());
        }
        Debug.Log("[CL] Built occluder proxies (" + Fx.props.Count + " props).");
    }

    // ---- ARM SELECTOR — 1B (live 3D rigged actors) vs 1A (painterly sprite billboards) ----
    // Set via EditorPrefs "CL.Arm" ("1B" default | "1A"); the bake-off drives this.
    public static string Arm { get { return EditorPrefs.GetString("CL.Arm", "1B"); } set { EditorPrefs.SetString("CL.Arm", value); } }

    // R3 LEVER 1 — use the chars_v3 hi-tier Meshy mesh (default ON). Toggle for A/B vs chars_v2.
    public static bool UseV3 { get { return EditorPrefs.GetBool("CL.UseV3", true); } set { EditorPrefs.SetBool("CL.UseV3", value); } }
    [MenuItem("Tools/WorldOS/CL/Use chars_v3 (hi-tier mesh) ON")]
    public static void SetV3On() { UseV3 = true; Debug.Log("[CL] UseV3 = true (chars_v3 hi-tier)"); }
    [MenuItem("Tools/WorldOS/CL/Use chars_v2 (legacy mesh) OFF")]
    public static void SetV3Off() { UseV3 = false; Debug.Log("[CL] UseV3 = false (chars_v2 legacy)"); }

    // sidecar accumulator (per-actor measured boxes for the pre-gate G2/G3/G4)
    static List<Dictionary<string, object>> _sidecar;

    // ---- actors (fixture spawns) ----
    static void PlaceActors(GameObject root)
    {
        var parent = new GameObject("Actors");
        parent.transform.SetParent(root.transform, false);
        _sidecar = new List<Dictionary<string, object>>();

        int[] heroCell = Fx.party.Count > 0 ? Fx.party[0] : new[] { 6, 8 };
        int[] foeCell  = Fx.foes.Count  > 0 ? Fx.foes[0]  : new[] { 6, 2 };

        if (Arm == "1A")
        {
            // ARM 1A — painterly stylized billboards (3D->sprite). Sprites at:
            //   Assets/painterly/sprites/<id>_idle.png  (Scenario-stylized, transparent bg)
            PlaceSpriteActor(parent, "HeroFighter", "Assets/painterly/sprites/hero_idle.png",
                             heroCell[0], heroCell[1], true);
            PlaceSpriteActor(parent, "MonsterGoblin", "Assets/painterly/sprites/goblin_idle.png",
                             foeCell[0], foeCell[1], false);
        }
        else
        {
            // ARM 1B — live rigged 3D, posed to Idle, scene-lit with PainterlyActor.
            // R3 LEVER 1: swap to chars_v3 (Meshy hi-tier: meshy-6, 4K hd_texture, remove_lighting,
            // 140K remeshed mesh). The mesh source is idle.fbx (has mesh + Idle clip); Walk/Attack live
            // in sibling FBXs (folder-wide clip search). 4K base_color is the actor albedo.
            // v3b = same hi-tier geometry/UVs but BAKED-LIGHTING 4K texture (remove_lighting:False) so
            // the albedo carries form for the relight to read. The rig is on the v3 mesh (same preview
            // UVs => the v3b texture maps correctly onto the v3 rigged FBX).
            string heroFbx = UseV3 ? "Assets/chars_v3/hero/glb/idle.fbx"   : "Assets/chars_v2/hero/hero.fbx";
            string heroTex = UseV3 ? "Assets/chars_v3b/hero/tex0_base_color.png" : "Assets/chars_v2/hero/albedo.png";
            string gobFbx  = UseV3 ? "Assets/chars_v3/goblin/glb/idle.fbx" : "Assets/chars_v2/goblin/goblin.fbx";
            string gobTex  = UseV3 ? "Assets/chars_v3b/goblin/tex0_base_color.png" : "Assets/chars_v2/goblin/albedo.png";
            PlaceActor(parent, "HeroFighter", heroFbx, heroTex, heroCell[0], heroCell[1], true);
            PlaceActor(parent, "MonsterGoblin", gobFbx, gobTex, foeCell[0], foeCell[1], false);
        }

        WriteSidecar();
    }

    // ---- project a world point through the LOCKED camera to pixel coords (top-left origin,
    // +y DOWN), at the sidecar's CAP_W x CAP_H resolution. Mirror of visual_pregate.CameraSpec. ----
    static float[] WorldToScreenPx(Vector3 w)
    {
        var cam = LockCamera();
        // Unity viewport: (0,0) bottom-left .. (1,1) top-left; z = world dist.
        Vector3 vp = cam.WorldToViewportPoint(w);
        float sx = vp.x * CAP_W;
        float sy = (1f - vp.y) * CAP_H;   // flip to top-left origin (image convention)
        return new[] { sx, sy };
    }

    static void RecordActor(string id, int c, int r, Bounds worldBounds)
    {
        // feet = bottom-center of the world bounds; head = top-center.
        Vector3 feetW = new Vector3(worldBounds.center.x, worldBounds.min.y, worldBounds.center.z);
        Vector3 headW = new Vector3(worldBounds.center.x, worldBounds.max.y, worldBounds.center.z);
        var feetPx = WorldToScreenPx(feetW);
        var headPx = WorldToScreenPx(headW);
        float pxH = Mathf.Abs(feetPx[1] - headPx[1]);
        var d = new Dictionary<string, object>();
        d["id"] = id;
        d["cell"] = new object[] { c, r };
        d["feet_px"] = new object[] { Mathf.Round(feetPx[0]), Mathf.Round(feetPx[1]) };
        d["head_px"] = new object[] { Mathf.Round(headPx[0]), Mathf.Round(headPx[1]) };
        d["px_height"] = Mathf.Round(pxH);
        d["world_height_ft"] = worldBounds.size.y;
        if (_sidecar != null) _sidecar.Add(d);
        Debug.Log("[CL] sidecar " + id + " cell[" + c + "," + r + "] feet_px(" +
                  feetPx[0].ToString("F0") + "," + feetPx[1].ToString("F0") + ") px_h=" + pxH.ToString("F0"));
    }

    static void WriteSidecar()
    {
        if (_sidecar == null) return;
        var sb = new System.Text.StringBuilder();
        sb.Append("[\n");
        for (int i = 0; i < _sidecar.Count; i++)
        {
            var d = _sidecar[i];
            var cell = (object[])d["cell"];
            var fp = (object[])d["feet_px"];
            var hp = (object[])d["head_px"];
            sb.Append("  {\"id\":\"" + d["id"] + "\",\"cell\":[" + cell[0] + "," + cell[1] + "],");
            sb.Append("\"feet_px\":[" + fp[0] + "," + fp[1] + "],");
            sb.Append("\"head_px\":[" + hp[0] + "," + hp[1] + "],");
            sb.Append("\"px_height\":" + d["px_height"] + ",");
            sb.Append("\"world_height_ft\":" + ((float)d["world_height_ft"]).ToString("F2") + "}");
            sb.Append(i < _sidecar.Count - 1 ? ",\n" : "\n");
        }
        sb.Append("]\n");
        string outPath = "Assets/painterly/final_cl.actors.json";
        File.WriteAllText(outPath, sb.ToString());
        AssetDatabase.Refresh();
        Debug.Log("[CL] Wrote actor sidecar -> " + outPath + " (" + _sidecar.Count + " actors)");
    }

    // ---- ARM 1A: painterly stylized billboard (sprite quad) at a spawn cell ----
    static void PlaceSpriteActor(GameObject parent, string label, string spritePath, int c, int r, bool isHero)
    {
        var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(spritePath);
        if (tex == null) { Debug.LogError("[CL] sprite not found: " + spritePath); return; }
        // #1284 (2): presentation-only prop-cell nudge (see PlaceActor) — logical spawn cell untouched.
        { int[] rc = NudgeCell(c, r); c = rc[0]; r = rc[1]; }

        var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
        go.name = label;
        Object.DestroyImmediate(go.GetComponent<Collider>());
        go.transform.SetParent(parent.transform, false);

        // species height in world units (hero ~5.2u; goblin shorter). Quad sized to the
        // sprite aspect so the painted figure isn't squashed.
        float speciesH = isHero ? ACTOR_TARGET_H : ACTOR_TARGET_H * 0.78f;
        float aspect = tex.height > 0 ? (float)tex.width / tex.height : 0.5f;
        float worldH = speciesH;
        float worldW = worldH * aspect;

        // WORLD-VERTICAL billboard tilted to FACE the dimetric camera (pitch back so the flat
        // sprite is perpendicular to the camera ray, not lying toward the floor). The quad's
        // textured face normal is -Z (Unity Quad), so to face the camera (which looks +Z, pitched
        // down) we point the quad's normal back along the camera ray: rotate +pitch about X so the
        // sprite leans toward the camera and the feet (texture v=0, bottom edge) sit on the floor.
        // WORLD-VERTICAL camera-facing billboard (standard iso-CRPG sprite). Quad textured face
        // normal is -Z; yaw 180 turns it to face the camera (which looks +Z). NO pitch tilt, so the
        // bottom edge (feet, texture v=0) plants flat on the floor cell and projects to exactly the
        // floor point — no float. The dimetric camera naturally foreshortens it.
        go.transform.localEulerAngles = new Vector3(0f, 180f, 0f);
        go.transform.localScale = new Vector3(worldW, worldH, 1f);

        float wx = CellX(c);
        float wz = CellZ(r);
        go.transform.position = new Vector3(wx, FLOOR_Y + worldH * 0.5f, wz); // center; bottom edge -> FLOOR_Y
        var preBounds = Bounds(go);
        if (Mathf.Abs(preBounds.min.y - FLOOR_Y) > 0.02f)
            go.transform.position += new Vector3(0f, FLOOR_Y - preBounds.min.y, 0f); // plant feet on the FLOOR plane

        // ALPHA-BLENDED unlit billboard (the sprite is pre-lit by Scenario). WorldOS/SpriteBillboard
        // honors the PNG alpha + applies a SUBTLE scene-key/ambient tint + depth exposure so it sits
        // in the room's light without double-lighting.
        float depth01b = Mathf.InverseLerp(0f, ROWS - 1, r);  // 0 back .. 1 front
        var sh = Shader.Find("WorldOS/SpriteBillboard");
        Material mat;
        if (sh != null && sh.isSupported)
        {
            mat = new Material(sh);
            mat.SetTexture("_MainTex", tex);
            mat.SetColor("_KeyColor", Fx.keyColor);
            mat.SetColor("_AmbientColor", Fx.ambientColor);
            mat.SetFloat("_KeyTint", 0.18f);
            mat.SetFloat("_AmbientTint", Mathf.Lerp(0.16f, 0.06f, depth01b)); // back cooler
            mat.SetFloat("_Exposure", Mathf.Lerp(0.78f, 1.08f, depth01b));    // front brighter focal
            mat.SetFloat("_Desat", Mathf.Lerp(0.22f, 0.04f, depth01b));        // back hazier
            mat.SetFloat("_Cutoff", 0.35f);
        }
        else { mat = new Material(Shader.Find("Sprites/Default")); mat.SetTexture("_MainTex", tex); }
        mat.renderQueue = 2100;
        var rend = go.GetComponent<Renderer>();
        rend.sharedMaterial = mat;
        rend.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        rend.receiveShadows = false;

        float depth01 = Mathf.InverseLerp(0f, ROWS - 1, r);
        var sb2 = Bounds(go);
        BuildContactShadow(parent, label + "_Shadow", c, r, sb2, isHero, depth01);
        RecordActor(label, c, r, sb2);
        Debug.Log("[CL] Placed SPRITE " + label + " @(" + c + "," + r + ") worldH=" + worldH.ToString("F1"));
    }

    static void PlaceActor(GameObject parent, string label, string path, string albedoPath,
                           int c, int r, bool isHero)
    {
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path);
        if (prefab == null) { Debug.LogError("[CL] FBX not found: " + path); return; }
        // #1284 (2): presentation-only prop-cell nudge — render off a blocked (prop) cell onto the nearest
        // walkable neighbor. The caller's logical spawn cell (Fx.party/foes) is untouched; c,r below are the
        // RENDER cell used for placement + ring (part 3), so the actor never stands ON the painted prop.
        { int[] rc = NudgeCell(c, r); c = rc[0]; r = rc[1]; }
        // WRAPPER controls placement + scale + FACING. The FBX is a child so that
        // PoseToClip's SampleAnimation (which bakes the Idle clip's ROOT rotation onto the FBX
        // root, clobbering any yaw we set on it) does NOT override our facing — the wrapper yaw
        // is applied OUTSIDE the clip's root track. (R7c: this was the back-facing bug.)
        var wrap = new GameObject(label);
        wrap.transform.SetParent(parent.transform, false);
        var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
        go.name = label + "_fbx";
        go.transform.SetParent(wrap.transform, false);

        // POSE the FBX into the Idle clip first (reads/clobbers its own root); facing is the wrapper's.
        PoseToClip(go, path, "Idle", 0.4f);

        // Face the actor TOWARD the game camera. Verified empirically below the clip bake:
        // the model's FRONT ends up facing +Z after the Idle bake, and the camera (z=-55.5,
        // looking +Z) sees the -Z side, so wrapper yaw 180 turns the FACE to the camera.
        // Gentle 3/4 offset so the two figures angle at each other.
        wrap.transform.localEulerAngles = new Vector3(0f, 180f + (isHero ? 18f : -18f), 0f);
        wrap.transform.position = new Vector3(9999f, 0f, 9999f);

        var b = Bounds(wrap);
        float curH = b.size.y > 0.01f ? b.size.y : 2f;
        // mild ortho depth-read scale: front-of-room actors a touch bigger so the eye
        // reads depth even though the ortho camera keeps true size constant.
        float depth01 = Mathf.InverseLerp(0f, ROWS - 1, r);          // 0 back .. 1 front
        float depthScale = Mathf.Lerp(0.88f, 1.05f, depth01);        // front (depth01->1) bigger
        // species height: a goblin is shorter than a human fighter. Hero -> ACTOR_TARGET_H, goblin 0.78x.
        // GRAPHICS-FORK R6 (L5 critic): the goblin read too SMALL/hard-to-find (the #1 residual). Bump it
        // ~1.2x (0.78 -> 0.94) so its silhouette is large enough to separate from the prop mass.
        float speciesH = isHero ? ACTOR_TARGET_H : ACTOR_TARGET_H * 0.94f;
        float mult = (speciesH / curH) * depthScale;
        var es = wrap.transform.localScale;
        wrap.transform.localScale = new Vector3(es.x * mult, es.y * mult, es.z * mult);

        float wx = CellX(c);
        float wz = CellZ(r);
        var staging = Bounds(wrap);                            // bounds at the (9999) staging pos
        float floorY = FLOOR_Y - staging.min.y;                // #1284 (1): anchor feet to the FLOOR plane (min.y -> FLOOR_Y)
        wrap.transform.position = new Vector3(wx, floorY, wz);
        var sb = Bounds(wrap);                                // RE-READ after final placement
        // safety: plant feet exactly on the FLOOR plane
        if (Mathf.Abs(sb.min.y - FLOOR_Y) > 0.02f)
        {
            wrap.transform.position += new Vector3(0f, FLOOR_Y - sb.min.y, 0f);
            sb = Bounds(wrap);
        }

        // TEXTURED + SCENE-LIT painterly material. Back-of-room actors are exposed
        // LOWER so they recede into the dark mid-plane (R3 L3: goblin was too hot for
        // its depth); the front hero gets a brighter key so it reads as the focal token.
        ApplyActorMaterial(go, albedoPath, isHero, depth01);

        BuildContactShadow(parent, label + "_Shadow", c, r, sb, isHero, depth01);
        RecordActor(label, c, r, sb);   // sidecar (post-scale, post-place world bounds)
        Debug.Log("[CL] Placed " + label + " at (" + c + "," + r + ") mult=" + mult.ToString("F2") +
                  " floorY=" + floorY.ToString("F2") + " facing=" + wrap.transform.localEulerAngles.y.ToString("F0"));
    }

    // ---- pose a rigged FBX instance into one of its imported clips (edit-mode sampling) ----
    static void PoseToClip(GameObject go, string fbxPath, string clipName, float time)
    {
        AnimationClip clip = FindClipInDir(fbxPath, clipName);
        if (clip == null) { Debug.LogWarning("[CL] clip '" + clipName + "' not found near " + fbxPath + " (bind pose)."); return; }
        // SampleAnimation poses the hierarchy at `time` without an Animator/play-mode.
        clip.SampleAnimation(go, time);
        Debug.Log("[CL] Posed " + go.name + " -> clip '" + clip.name + "' @ t=" + time + "s");
    }

    // Find an animation clip by name, first in the given FBX, then across sibling FBXs in the same
    // folder (chars_v3 emits one FBX per clip: idle.fbx/walk.fbx/attack.fbx). Public so AnimProof reuses it.
    public static AnimationClip FindClipInDir(string fbxPath, string clipName)
    {
        // 1) inside the given fbx (chars_v2 single multi-take fbx)
        foreach (var o in AssetDatabase.LoadAllAssetsAtPath(fbxPath))
        {
            var cl = o as AnimationClip;
            if (cl != null && !cl.name.StartsWith("__preview") && cl.name.Contains(clipName)) return cl;
        }
        // 2) sibling fbxs in the same folder (chars_v3 per-clip fbx)
        string dir = System.IO.Path.GetDirectoryName(fbxPath).Replace('\\', '/');
        foreach (var guid in AssetDatabase.FindAssets("t:AnimationClip", new[] { dir }))
        {
            string p = AssetDatabase.GUIDToAssetPath(guid);
            foreach (var o in AssetDatabase.LoadAllAssetsAtPath(p))
            {
                var cl = o as AnimationClip;
                if (cl != null && !cl.name.StartsWith("__preview") && cl.name.Contains(clipName)) return cl;
            }
        }
        return null;
    }

    static void ApplyActorMaterial(GameObject go, string albedoPath, bool isHero, float depth01)
    {
        var tex = AssetDatabase.LoadAssetAtPath<Texture2D>(albedoPath);
        if (tex == null) Debug.LogWarning("[CL] albedo not found: " + albedoPath + " (will look flat).");
        var sh = Shader.Find("WorldOS/PainterlyActor");
        Material fmat;
        if (sh != null && sh.isSupported)
        {
            fmat = new Material(sh);
            if (tex != null) fmat.SetTexture("_MainTex", tex);
            fmat.SetColor("_BaseColor", Color.white);
            fmat.SetColor("_KeyColor", Fx.keyColor);
            fmat.SetColor("_AmbientColor", Fx.ambientColor);
            // GRAPHICS-FORK R4b (2026-06-23): ★ the plate display is DETERMINISTIC = un-mirrored (the 180°
            // quad rotation + mainTextureScale=(-1,1) cancel). Verified over 3 re-assembles: the active plate
            // (gen_dim_r3/controlnet_1, raw hearth LEFT) DISPLAYS hearth LEFT consistently (R-B warmth left-third
            // 53 vs right -4). (The one hearth-RIGHT frame earlier was a transient stale-material read before the
            // reimport settled — ignore it.) So the warm KEY comes from screen-LEFT (-X) to face the displayed
            // fire. RECIPE: after any plate swap, render once it has SETTLED, measure displayed warmth L-vs-R,
            // set _KeyDir.x sign to match (negative=hearth-left, positive=hearth-right).
            // GRAPHICS-FORK R6/R7 (2026-06-23): the new depth-controlnet plate (gen_dim_r6/depth_2, raw hearth
            // on the LEFT) DISPLAYS hearth-LEFT once the texture import SETTLES — because the camera-locked
            // U-flip (180deg quad + mainTextureScale=(-1,1)) cancels, same as the r3 plate. A transient
            // PRE-settle read showed hearth-RIGHT and I briefly flipped _KeyDir.x +0.92; the SETTLED frame
            // measured L-third R-B +118 vs R-third -15 => hearth-LEFT. So _KeyDir.x stays NEGATIVE (warm key
            // from screen-LEFT, facing the fire). ★ LESSON: render -> let import SETTLE -> measure L-vs-R
            // -> THEN set _KeyDir.x sign. Never trust the first post-swap read.
            Vector3 keyDir = new Vector3(-0.92f, 0.22f, 0.10f).normalized;
            fmat.SetVector("_KeyDir", keyDir);
            // R10/r6: the NEW plate's forge is genuinely the brightest source (p99~215 vs hero ~125),
            // so there's headroom to lift the hero key for focal readability without out-glowing it.
            float key = isHero ? Mathf.Lerp(1.35f, 1.55f, depth01) : 1.2f;
            fmat.SetFloat("_KeyStrength", key);
            fmat.SetFloat("_RimStrength", isHero ? 0.16f : 0.20f);  // r3: rim hotspot on head/shoulders was the last "lantern" tell — cut hard
            fmat.SetFloat("_Desat", isHero ? 0.24f : 0.36f);        // match the muted plate; goblin hazier (depth)
            // warm floor bounce on the lower body (L4: actors stand between fires, legs should warm).
            fmat.SetFloat("_BounceStrength", Mathf.Lerp(0.10f, 0.22f, depth01));
            // ---- v2 HYBRID real-time painterly pass (the L4 maquette-ceiling breaker) ----
            // Kuwahara flattens CG micro-detail; posterize -> brushy value steps; brush-grain ->
            // visible strokes; edge-feather dissolves the razor silhouette; palette-snap pulls sat
            // into the plate range; ambient-lift keeps the shadow side a matte painted value (not
            // 3D black). Goblin gets a hazier/softer treatment (depth) per L4 depth-desaturation.
            // r9 L4: small kuwahara fragmented the figure into "same-value pebbles" + grain on the SMALL
            // goblin "amplified noise" (disintegrated it). Fix: BIGGER kuwahara (merge into 3-4 BROAD
            // value masses), FEWER posterize levels, and a screen-SIZE LOD — the goblin (far/small) gets
            // a much bigger kuwahara + near-ZERO grain so it stays a SOLID simplified shape, not noise.
            // r10 L4: the 6.5/9 kuwahara + 4-plane RGB posterize made a "mosaic checker"; with the new
            // value-only soft-posterize, a MODERATE kuwahara + a touch more planes reads as smooth broad
            // masses. Goblin: bigger kuwahara + grain OFF so it stays a solid simplified shape.
            fmat.SetFloat("_Kuwahara",      isHero ? 4.0f : 5.5f);
            fmat.SetFloat("_Posterize",     isHero ? 5.0f : 4.0f);   // soft-posterize => a couple more planes is fine now
            fmat.SetFloat("_BrushStrength", isHero ? 0.22f : 0.04f); // goblin grain ~off
            fmat.SetFloat("_BrushScale",    isHero ? 15.0f : 11.0f);
            fmat.SetFloat("_EdgeSoften",    isHero ? 0.22f : 0.30f); // r3: wide feather let the plate's warm floor-pool bleed THROUGH the silhouette as a false halo; keep edges mostly opaque with a thin feather
            fmat.SetFloat("_PaletteSnap",   isHero ? 0.42f : 0.55f); // pull actor sat INTO the plate range (L4)
            fmat.SetFloat("_PaintLift",     0.06f);
            fmat.SetFloat("_AmbientLift",   isHero ? 0.16f : 0.20f); // no pure-black mass; matte the shadow plane
            // r2 CONSENSUS CRITICAL: clamp actor luma below the hearth (actor must not be brightest in frame).
            fmat.SetFloat("_MaxLuma",       isHero ? 0.78f : 0.56f); // r6: new plate's forge p99~0.84 gives headroom; lift hero for focal read, still below forge
            fmat.SetFloat("_TermSharp",     isHero ? 0.30f : 0.30f); // r10 L3: hard seam read as a 2-tone mask; SOFTEN the wrap
            // ATMOSPHERIC DEPTH WASH (R2 L4 CRITICAL): the back goblin held full foreground sat/contrast
            // = the #1 "pasted sprite" tell. Drive _AtmDepth by row depth: front hero ~0 (untouched),
            // back goblin washes toward the ambient. depth01: 0 back .. 1 front -> wash = (1-depth01).
            float atm = Mathf.Clamp01((1f - depth01) * 0.85f);   // back actors get up to ~0.85 wash
            fmat.SetFloat("_AtmDepth", isHero ? atm * 0.35f : atm);   // hero barely washed; goblin fully
            fmat.SetColor("_AtmColor", Fx.ambientColor * 1.4f);      // the room's cool ambient fog

            // R3 LEVER 1 — hi-tier (v3) re-tune: the 4K meshy-6 albedo is DARKER (mean ~0.20 vs v2 0.27)
            // and carries FINE detail. The v2-tuned posterize/palette-snap/MaxLuma under-exposed it to mud
            // (A/B crop proved it). For v3: (a) LIFT exposure/key + raise MaxLuma so the detail reads,
            // (b) SOFTEN posterize + palette-snap + kuwahara so the 4K detail isn't smeared into flat
            // masses, (c) crush specular harder. Keeps the painterly family but lets the hi-tier detail show.
            if (UseV3)
            {
                // R5 L3 dual-CRITICAL: the hero was under-lit + face-dark, RECEDING against the lit floor.
                // Push the key HARD so mid-body luma lands ~0.30-0.38 (clearly above the 0.207 floor) and
                // the head/face catches warm light; keep MaxLuma below the forge so it doesn't out-glow.
                // R4 L4 (hero_value_too_hot_vs_plate): the hero was the brightest object after the fire —
                // pull the key DOWN ~25% and cap MaxLuma lower so the actor sits INSIDE the plate's value
                // envelope, not studio-lit above it. Still clearly above the lit floor for focal read.
                // GRAPHICS-FORK R5 (L4 value-compress): the hero was still a HIGH-KEY ISLAND on a mid-dark
                // floor (the contrast cliff, not the geometric edge, popped him out as a cutout). Compress his
                // dynamic range INTO the local plate envelope: drop the key further, cap MaxLuma to ~0.68 (no
                // out-glowing the floor), and LIFT the shadow floor (AmbientLift up + PaintLift up) so the
                // hero's darkest value >= the local floor value. Keep a clear terminator (chiaroscuro on the
                // form) but within a compressed range.
                fmat.SetFloat("_KeyStrength", isHero ? Mathf.Lerp(1.30f, 1.50f, depth01) : 1.6f);
                fmat.SetFloat("_MaxLuma",     isHero ? 0.68f : 0.58f);  // R5: clamp below the floor highlights so no high-key island
                fmat.SetFloat("_PaintLift",   0.18f);                   // R5: lift the darkest hero value off black into the floor band
                fmat.SetFloat("_AmbientLift", isHero ? 0.40f : 0.34f);  // R5: hero shadow side >= local floor value (kill the contrast cliff)
                fmat.SetFloat("_TermSharp",   0.40f);                   // R5 L3: a clear warm-to-cool terminator across the form
                // R5 L4 CRITICAL: the actor painterly post was NOT engaging (hero read photoreal-baked while
                // the plate was stylized => the #1 paste-on tell). RESTORE a real painterly treatment so the
                // hero shares the plate's brush vocabulary — but balanced to keep the hi-tier detail:
                // GRAPHICS-FORK R3 (L4 CRITICAL hero_specular_plastic + hero_edge_too_crisp): push the
                // painterly flatten HARDER so the actor's brush vocabulary matches the loose oil plate.
                // This custom shader has NO specular term — the "plastic 3D" read is smooth gradients +
                // razor edges, killed by bigger kuwahara (broad value masses), fewer posterize planes
                // (painted value steps), a WIDER edge feather (fray the silhouette like the backdrop),
                // and a stronger palette-snap + desat (pull into the plate palette).
                // GRAPHICS-FORK R4 (L4 panel-corrected): the R3 posterize REDUCTION backfired (fewer planes
                // left smooth interpolated gradients between them = plastic read). The L4 specialist's exact
                // levers: (a) MORE posterize planes (~7) so values land in deliberate painted STEPS not a
                // smooth gradient; (b) MUCH wider edge-feather (~0.60) to break the razor 3D silhouette (the
                // #1 CRITICAL pasted-on tell); (c) keep kuwahara moderate; (d) strong palette-snap + graded desat.
                fmat.SetFloat("_Kuwahara",    isHero ? 3.5f : 4.5f);    // moderate flatten -> broad masses
                fmat.SetFloat("_Posterize",   isHero ? 7.0f : 6.0f);    // R4 REVERSE: MORE tight value steps kill the smooth gradient (painted planes)
                fmat.SetFloat("_PaletteSnap", isHero ? 0.60f : 0.68f);  // pull actor sat INTO the scene palette (L4 mismatch)
                fmat.SetFloat("_Desat",       isHero ? 0.32f : 0.50f);  // depth-graded: hero near 0.32, goblin far 0.50
                fmat.SetFloat("_BrushStrength", isHero ? 0.40f : 0.14f);// R8 L4: a touch more visible stroke so the hero shares the floor's brush language (was 0.34, mild "plasticky" residual)
                fmat.SetFloat("_BrushScale",  isHero ? 16.0f : 12.0f);
                fmat.SetFloat("_EdgeSoften",  isHero ? 0.60f : 0.56f);  // R4 L4 CRITICAL: break the razor silhouette much harder (frayed painted edge)

                // ============================================================================
                // GRAPHICS-FORK R2 (2026-06-23) — actor LIGHTING/INTEGRATION cluster fix.
                // Against the NEW dimetric+even-lit plate the panel (L2/L3/L4) said the actor still
                // reads COOL-GREY pasted onto a WARM floor: (a) shadow side crushes to cold near-black
                // (want warm-chromatic), (b) NO warm hearth RIM on the right silhouette, (c) palette
                // divorced from the warm plate. These are all the shader-faked relight — fix here.
                // ----------------------------------------------------------------------------
                // (a) shadow side warm, not black: lift the ambient floor harder + warmer so the
                //     left/shade side reads a painted warm-grey (firelit interior), never 3D-black.
                fmat.SetFloat("_AmbientLift", isHero ? 0.42f : 0.40f);  // L3/L4: off pure black, warm-chromatic shade
                // (b) HEARTH RIM: a warm kicker on the hearth-facing silhouette fuses the figure into the
                //     lit room (every ref has it). The shader rims on the KEY-facing side, and _KeyDir is now
                //     flipped to screen-LEFT (the hearth), so the rim now correctly lands on the LEFT edge.
                fmat.SetFloat("_RimStrength", isHero ? 0.78f : 0.45f);  // R4 L3 HIGH: stronger warm hearth rim on the hearth-facing (left) silhouette (the "lit by the scene" cue)
                // (c) warm floor bounce (L4 "palette divorced"): warm the lower body from the lit floor.
                //     (PaletteSnap is set HARDER above in the R3 painterly cluster; don't weaken it here.)
                fmat.SetFloat("_BounceStrength", Mathf.Lerp(0.24f, 0.42f, depth01)); // L4: warm floor bounce on legs
                // (d) GRAPHICS-FORK R3 (L4 CRITICAL goblin_no_atmospheric_depth + L2 goblin_floating_cutout):
                //     the far goblin sits in the COOL archway and must RECEDE — push the atmospheric wash much
                //     harder toward the cool doorway ambient, desaturate + value-compress, drop the key, and
                //     soften its edges more than the hero so it reads as a far body, not a saturated cutout.
                if (!isHero)
                {
                    // GRAPHICS-FORK R6 (L5 critic CRITICAL — goblin value-camouflaged, "have to hunt for it"):
                    // it was crushed so dark (MaxLuma 0.46, key 1.10) it merged into the dim props. LIFT its
                    // value enough to read as a distinct COOL mass against the warm scene (the cool body vs warm
                    // floor is itself the separation), and strengthen the silhouette rim so its edge catches a
                    // cool counter-light. Still kept clearly below the hero + the hearth (focal hierarchy).
                    // R8 (L5 critic: r7 goblin over-corrected to chalky/ghost): pull the midtone back ~15%
                    // and re-introduce a COOL local skin hue so it reads as a creature-mass, not a pale prop.
                    // Still readable (bigger now) + clearly below the hero/hearth.
                    fmat.SetFloat("_KeyStrength", 1.20f);               // R8: was 1.32 (too hot/chalky) -> midground
                    fmat.SetFloat("_MaxLuma",     0.52f);              // R8: was 0.60 -> pull the value down ~15% (off ghost-white)
                    fmat.SetColor("_BaseColor",   new Color(0.74f, 0.82f, 0.90f, 1f)); // R8: cool blue-grey skin tint = creature hue, breaks the chalky read
                    fmat.SetFloat("_Desat",       0.46f);             // R8: less desat so the cool hue actually reads as color, not grey
                    fmat.SetFloat("_AtmDepth",    0.66f);             // R8: ease the wash a touch more so the form holds
                    fmat.SetColor("_AtmColor",    new Color(0.32f, 0.44f, 0.60f, 1f) * 1.22f); // cool archway ambient (separation from warm scene)
                    fmat.SetFloat("_AmbientLift", 0.34f);             // R8: lower slightly with the value pull-down
                    fmat.SetFloat("_RimStrength", 0.80f);             // strong silhouette rim = the cool edge that pops it from the props
                    fmat.SetFloat("_EdgeSoften",  0.46f);             // crisp enough that the bigger goblin holds a readable shape
                    fmat.SetFloat("_TermSharp",   0.24f);             // firmer wrap -> internal value planes (creature form, not a flat blob)
                }
            }
        }
        else
        {
            fmat = new Material(Shader.Find("Standard"));
            if (tex != null) fmat.mainTexture = tex;
            fmat.SetFloat("_Glossiness", 0.12f);
            fmat.SetFloat("_Metallic", 0f);
            Debug.LogWarning("[CL] PainterlyActor shader unavailable; Standard fallback.");
        }
        foreach (var rend in go.GetComponentsInChildren<Renderer>())
        {
            var arr = new Material[rend.sharedMaterials.Length];
            for (int i = 0; i < arr.Length; i++) arr[i] = fmat;
            rend.sharedMaterials = arr;
        }
    }

    static Bounds Bounds(GameObject go)
    {
        var rs = go.GetComponentsInChildren<Renderer>();
        if (rs.Length == 0) return new Bounds(Vector3.zero, Vector3.one * 1.8f);
        var b = rs[0].bounds;
        for (int i = 1; i < rs.Length; i++) b.Encapsulate(rs[i].bounds);
        return b;
    }

    // ---- soft elliptical contact shadow directly under feet (R1/R2/R3 L2 fix) ----
    static void BuildContactShadow(GameObject parent, string name, int c, int r, Bounds actorBounds, bool isHero, float depth01)
    {
        var go = GameObject.CreatePrimitive(PrimitiveType.Quad);
        go.name = name;
        go.transform.SetParent(parent.transform, false);
        Object.DestroyImmediate(go.GetComponent<Collider>());

        // DIRECTIONAL CAST SHADOW (v2 L3/L2): the prior centered ellipse read as a detached radial
        // "self-pool" under the actor (the L3 critic's loudest tell). A real shadow is cast AWAY from
        // the hearth key. Compute the floor direction from the hearth (back-right) to the actor and
        // EXTEND the shadow along it (elongated), anchored at the feet, so it reads as the body's own
        // shadow thrown by the firelight rather than a glow pool the actor emits.
        float ax = CellX(c), az = CellZ(r);
        // GRAPHICS-FORK R6/R7: the new depth_2 plate DISPLAYS hearth on screen-LEFT (settled) = world -X.
        // The cast shadow is thrown AWAY from the hearth = toward screen-RIGHT (world +X). Anchor the hearth
        // at a back-LEFT cell (col ~1.5) so `away = actor - hearth` points +X. (Matches _KeyDir.x negative.)
        float hx = CellX(1.5f), hz = CellZ(1.0f);
        Vector3 away = new Vector3(ax - hx, 0f, az - hz);
        if (away.sqrMagnitude < 0.0001f) away = new Vector3(1f, 0f, 0.3f);
        away.Normalize();
        float footX = Mathf.Clamp(actorBounds.size.x * 1.0f, 1.8f, 3.4f);
        float castLen = footX * 1.7f;                       // shadow elongated along the cast axis
        float footZ = footX * 0.55f;
        // orient the ellipse so its long axis points along `away` (rotate the flat quad around Y)
        float yaw = Mathf.Atan2(away.x, away.z) * Mathf.Rad2Deg;
        go.transform.localScale = new Vector3(footZ * 1.05f, castLen, 1f);   // quad: x=width, y=length(pre-rot)
        // anchor at the feet, shifted half the cast length DOWN the away axis so the shadow starts at
        // the feet and stretches outward (not centered on the body).
        // r9 L2: a big offset left a GAP of lit floor between the soles and the shadow (= floating). Pull
        // the cast ellipse back so its NEAR end fuses with the feet (offset only ~0.18*len, and the AO
        // below sits right at the soles to read as the dark contact core the cast grows out of).
        Vector3 footPos = new Vector3(ax, 0.02f, az - 0.3f);
        Vector3 shadowCenter = footPos + away * (castLen * 0.18f);
        go.transform.localPosition = shadowCenter;
        go.transform.localEulerAngles = new Vector3(90f, yaw, 0f); // flat on floor, long axis along `away`

        // soft radial-gradient blob composited via WorldOS/ContactShadow (ZTest Always
        // so it reliably darkens the camera-locked plate; queue 1000 = after the plate
        // (900) but BEFORE the actors (2000) so the actor's feet draw ON TOP of it).
        // R4 L2: a Sprites/Default quad measured ZERO darkening under the feet; this
        // dedicated alpha-composite shader forces the contact shadow to actually land.
        float alpha = Mathf.Lerp(0.82f, 0.72f, depth01); // firm contact, slightly softer than pure black
        var tex = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/soft_shadow.png");
        var sh = Shader.Find("WorldOS/ContactShadow");
        var mat = new Material(sh != null ? sh : Shader.Find("Sprites/Default"));
        // r2 L2: warm-tinted shadow (not pure black) so it sits in the firelit ambient like a painted
        // shadow, not a black decal. A deep warm-brown reads as firelight occlusion.
        Color shadowCol = new Color(0.05f, 0.03f, 0.02f, alpha);
        mat.SetColor("_Color", shadowCol);
        mat.color = shadowCol;
        if (tex != null) mat.mainTexture = tex;
        mat.renderQueue = 1000;
        var rr = go.GetComponent<Renderer>();
        rr.sharedMaterial = mat;
        rr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        rr.receiveShadows = false;

        // TIGHT CONTACT-AO directly under the soles (r2 L2 HIGH: grounding reads from the dark where
        // foot meets floor; the directional cast alone left the plant point ambiguous + the seam washed
        // bright). A small, high-opacity, near-circular dark spot anchored at the feet, drawn UNDER the
        // directional cast — this is the ambient-occlusion "weight" that locks the figure to the stone.
        var ao = GameObject.CreatePrimitive(PrimitiveType.Quad);
        ao.name = name + "_AO";
        ao.transform.SetParent(parent.transform, false);
        Object.DestroyImmediate(ao.GetComponent<Collider>());
        float aoR = Mathf.Clamp(actorBounds.size.x * 0.52f, 0.9f, 1.8f); // r9 L2: TIGHTER = a dark contact CORE
        ao.transform.localScale = new Vector3(aoR, aoR * 0.55f, 1f);      // depth-squashed circle
        ao.transform.localPosition = new Vector3(ax, 0.015f, az - 0.3f);  // right at the soles
        ao.transform.localEulerAngles = new Vector3(90f, 0f, 0f);
        float aoAlpha = Mathf.Lerp(0.92f, 0.78f, depth01);               // r9 L2: DARKER core so weight reads
        var aoMat = new Material(sh != null ? sh : Shader.Find("Sprites/Default"));
        Color aoCol = new Color(0.04f, 0.025f, 0.015f, aoAlpha);  // deep warm-brown AO, not pure black
        aoMat.SetColor("_Color", aoCol);
        aoMat.color = aoCol;
        if (tex != null) aoMat.mainTexture = tex;
        aoMat.renderQueue = 1001;   // after the directional cast (1000), still before actors
        var aor = ao.GetComponent<Renderer>();
        aor.sharedMaterial = aoMat;
        aor.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        aor.receiveShadows = false;

        // R2 L2 CRITICAL — GROUNDING HALO for low-ambient (back/dark) actors. The goblin sits in the
        // dark doorway where the AO is dark-on-dark => NO value break => it reads as floating. Draw a
        // slightly LIGHTER warm ring of floor value JUST OUTSIDE the AO core so a contact value-break
        // exists even on a near-black floor (the "is it on the floor?" cue is a value EDGE, not darkness).
        // Only for back actors (depth01 < ~0.45, i.e. dim cells); the lit front hero already has contrast.
        if (depth01 < 0.45f)
        {
            // R5 L2: the symmetric warm ring "read as a glow emitter, not contact." Make it a directional
            // crescent biased to the HEARTH-LIT side + push the AO core DARKER + tighter so a HARD near-edge
            // value-break exists at the feet (that hard break is what reads as floor contact, per L2).
            var halo = GameObject.CreatePrimitive(PrimitiveType.Quad);
            halo.name = name + "_Halo";
            halo.transform.SetParent(parent.transform, false);
            Object.DestroyImmediate(halo.GetComponent<Collider>());
            float haloR = aoR * 1.7f;
            // shift the warm patch toward the hearth (screen-LEFT/-X for the depth_2 plate) so it's an
            // ASYMMETRIC crescent on the lit side, not a symmetric ring; the AO core then sits as a dark
            // spot on its near (camera) edge. R7: hearth is screen-LEFT now -> bias -X.
            halo.transform.localScale = new Vector3(haloR * 1.15f, haloR * 0.5f, 1f);
            halo.transform.localPosition = new Vector3(ax - aoR * 0.35f, 0.012f, az - 0.3f); // biased -X (hearth side, screen-left)
            halo.transform.localEulerAngles = new Vector3(90f, 0f, 0f);
            var haloMat = new Material(sh != null ? sh : Shader.Find("Sprites/Default"));
            // R8 (L2 top fix): the goblin's contact read faint (dark-on-dark). STRONGEN the warm crescent
            // value-break just outside the dark AO core so a HARD contact edge reads even on the dim back floor.
            float haloA = Mathf.Lerp(0.62f, 0.34f, depth01 / 0.45f);     // R8: was 0.50..0.28 -> brighter break
            Color haloCol = new Color(0.30f, 0.20f, 0.11f, haloA);       // R8: a touch warmer/brighter dim floor glow, hearth side only
            haloMat.SetColor("_Color", haloCol);
            haloMat.color = haloCol;
            if (tex != null) haloMat.mainTexture = tex;
            haloMat.renderQueue = 999;
            var hr = halo.GetComponent<Renderer>();
            hr.sharedMaterial = haloMat;
            hr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            hr.receiveShadows = false;

            // a SECOND tight, very-dark AO core for back actors so the contact value-break is unmistakable
            // against the warm halo (the hard near-edge L2 demanded). Drawn last (over the halo + base AO).
            var ao2 = GameObject.CreatePrimitive(PrimitiveType.Quad);
            ao2.name = name + "_AO2";
            ao2.transform.SetParent(parent.transform, false);
            Object.DestroyImmediate(ao2.GetComponent<Collider>());
            float ao2R = aoR * 0.7f;
            ao2.transform.localScale = new Vector3(ao2R, ao2R * 0.5f, 1f);
            ao2.transform.localPosition = new Vector3(ax, 0.018f, az - 0.32f);  // right at the soles, near edge
            ao2.transform.localEulerAngles = new Vector3(90f, 0f, 0f);
            var ao2Mat = new Material(sh != null ? sh : Shader.Find("Sprites/Default"));
            Color ao2Col = new Color(0.02f, 0.012f, 0.008f, 0.95f);   // near-black hard contact core
            ao2Mat.SetColor("_Color", ao2Col); ao2Mat.color = ao2Col;
            if (tex != null) ao2Mat.mainTexture = tex;
            ao2Mat.renderQueue = 1002;
            var ao2r = ao2.GetComponent<Renderer>();
            ao2r.sharedMaterial = ao2Mat;
            ao2r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            ao2r.receiveShadows = false;
        }
    }

    static void SetupLighting()
    {
        var lightGO = GameObject.Find("Directional Light");
        if (lightGO == null) { lightGO = new GameObject("Directional Light"); lightGO.AddComponent<Light>().type = LightType.Directional; }
        var lt = lightGO.GetComponent<Light>();
        lt.color = Fx.keyColor;
        lt.intensity = 1.6f;
        lt.shadows = LightShadows.Soft;
        lt.shadowStrength = 0.5f;
        // key from the hearth side (key_dir_deg), tilted down
        lightGO.transform.eulerAngles = new Vector3(42f, Fx.keyDirDeg + 5f, 0f);

        RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
        RenderSettings.ambientLight = Fx.ambientColor;
        RenderSettings.ambientIntensity = 0.46f;  // R2 L5: lift actor/occluder ambient to match the brighter plate
        Debug.Log("[CL] Lighting: key " + ColorUtility.ToHtmlStringRGB(Fx.keyColor) +
                  " int1.6 dir" + Fx.keyDirDeg + ", ambient " + ColorUtility.ToHtmlStringRGB(Fx.ambientColor) + ".");
    }

    static float Band(string b)
    {
        if (b == "tall") return TALL_H;
        if (b == "low") return LOW_H;
        return MID_H;
    }

    // ==================================================================
    // capture final frame at 16:9
    // ==================================================================
    [MenuItem("Tools/WorldOS/CL/3 Capture Final Frame")]
    public static void CaptureFinal()
    {
        var cam = LockCamera();
        var rt = new RenderTexture(CAP_W, CAP_H, 24, RenderTextureFormat.ARGB32);
        rt.Create();
        var prevTgt = cam.targetTexture;
        cam.targetTexture = rt;
        cam.Render();
        SaveRT(rt, "Assets/painterly/final_cl.png");
        cam.targetTexture = prevTgt;
        rt.Release();
        AssetDatabase.Refresh();
        Debug.Log("[CL] Final frame captured -> Assets/painterly/final_cl.png");
    }
}

// graphics-fork: force-reload bump 1782210117
