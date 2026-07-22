// build_room_kit.cs — 3D-FIRST ROOM ASSEMBLY FROM THE SYNTY KIT, geometry-JSON-driven (#83 spike).
//
// The sibling of build_room_unified.cs: where the unified script renders a GREYBOX (molded primitive
// boxes) to condition the paint, this one assembles a REAL 3D room from Synty modular PREFABS — driven by
// the SAME authored geometry JSON that builds the engine walkmask. Because both the walkmask and this kit
// room read the same cells / wall runs / prop footprints through the SAME CellToWorld map, paint↔collision
// registration is correct BY CONSTRUCTION (the floor tile an actor stands on and the collision cell the
// engine blocks are the same cell).
//
// Contract borrowed verbatim from build_room_unified.cs (cited inline by line number):
//   * JSON schema + parsing ......... build_room_unified.cs L26-34, L166-179 (props), L310-315 (door_cells)
//   * CellToWorld (CellSize=2) ....... build_room_unified.cs L33-34  (== build_atelier_crypt.cs L26-27)
//   * fit-ortho (camera_fit) ......... build_room_unified.cs L40-53
//   * CONTRACT camera (Euler 30/45, pos=-(rot*fwd)*80) ... build_room_unified.cs L54-56
// Prefab loader / worldBounds / footprint-seating / material-defensive walk are lifted from the earlier
// atelier-kit spike build_atelier_crypt.cs (prefab loader L38-45, worldBounds L48-51, place/seat L55-89,
// material normalization L267-293). Brazier point-light values mirror paint_combat_scene.cs L34.
//
// Two Editor menu items:
//   Tools/WorldOS/Kit/Build Room From Kit   — assemble "KitRoom_<roomId>" at the plate-contract origin,
//                                             MarkSceneDirty + SaveScene (no render-and-forget).
//   Tools/WorldOS/Kit/Capture Kit Room      — render the room through the CONTRACT camera to a 2560x1600
//                                             PNG at <CaptureDir>/kit_<roomId>_<yyyyMMddTHHmmZ>.png.
//
// SELF-CONTAINED: no new dependencies. Reads MiniJson (same assembly). Geometry path + capture dir are the
// same box defaults build_room_unified.cs uses (L24), env-overridable so a non-box host can point elsewhere.
// C# is uncompilable on the authoring Mac — this is authored to mirror the proven sibling idioms exactly and
// is compiled + run on the GEX44 Unity box.
#if UNITY_EDITOR
using UnityEngine;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine.SceneManagement;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;

public static class BuildRoomKit
{
    // CellSize=2 — the plate contract (build_room_unified.cs L34 uses (c-cx0)*2.0 / (cy0-r)*2.0).
    const float CELL = 2.0f;

    // ── fallback-material cache (material-defensive rule) ──────────────────────────────────────────
    static Material _stoneMat;                 // PolygonGeneric stone-ish (structure / tombs)
    static Material _woodMat;                  // PolygonGeneric wood-ish (furniture / crates / barrels)
    static bool _matResolved;
    static HashSet<string> _loggedBadMats = new HashSet<string>();

    // ── paths (box defaults, env-overridable) — mirror build_room_unified.cs L24 ────────────────────
    static string GeoPath() =>
        Environment.GetEnvironmentVariable("WORLDOS_ROOM_GEO") ?? "/home/unity/worldos-unity/room_geometry.json";
    static string CaptureDir() =>
        Environment.GetEnvironmentVariable("WORLDOS_CAPTURE_DIR") ?? "/home/unity/worldos-unity/Captures-Durable";

    // ════════════════════════════════════════════════════════════════════════════════════════════════
    //  BUILD
    // ════════════════════════════════════════════════════════════════════════════════════════════════
    [MenuItem("Tools/WorldOS/Kit/Build Room From Kit")]
    public static void BuildRoom()
    {
        string geoPath = GeoPath();
        if (!File.Exists(geoPath)) { Debug.LogError($"[KitRoom] no geometry json: {geoPath}"); return; }
        var geo = MiniJson.Parse(File.ReadAllText(geoPath)) as Dictionary<string, object>;
        if (geo == null) { Debug.LogError("[KitRoom] geometry parse failed"); return; }

        int cols = GetInt(geo, "cols", 14), rows = GetInt(geo, "rows", 11);
        bool camFit = GetBool(geo, "camera_fit");
        string roomId = RoomId(geo, geoPath);

        // reset the material-defensive caches for a clean run
        _matResolved = false; _stoneMat = null; _woodMat = null; _loggedBadMats = new HashSet<string>();

        // idempotent: drop any prior root of this room
        string rootName = "KitRoom_" + roomId;
        var prev = GameObject.Find(rootName); if (prev != null) UnityEngine.Object.DestroyImmediate(prev);
        var root = new GameObject(rootName);
        root.transform.position = Vector3.zero;  // SAME world origin as the plate contract (cellToWorld centers the grid at 0,0,0)

        int nFloor = 0, nWall = 0, nDoor = 0, nPillar = 0, nSarc = 0, nBrazier = 0, nKit = 0, nFallback = 0, nMatFix = 0;

        // door cells → a set (gaps in the wall ring; framed doorways placed after)
        var doorSet = new HashSet<long>();
        var doorCells = GetList(geo, "door_cells");
        if (doorCells != null)
            foreach (var dco in doorCells) { if (TryCell(dco, out int dc, out int dr)) doorSet.Add(CellKey(dc, dr)); }

        // ── prefabs (verify-by-name loader; NEVER throw on a miss — log + fall back) ────────────────
        var pFloor = LoadPrefab("SM_Bld_Base_Floor_01");
        var pWall  = LoadPrefab("SM_Bld_Base_Wall_01");
        var pDoor  = LoadPrefab("SM_Bld_Base_45_Wall_Door_01");     // author's-choice framed doorway (optional)
        var pTomb  = LoadPrefab("SM_Prop_Tomb_01");
        var pLid   = LoadPrefab("SM_Prop_Tomb_Lid_01");
        var pillars = new GameObject[5];
        for (int i = 0; i < 5; i++) pillars[i] = LoadPrefab("SM_Bld_Base_Pillar_0" + (i + 1));
        Debug.Log($"[KitRoom] prefabs: Floor={pFloor != null} Wall={pWall != null} Door={pDoor != null} " +
                  $"Tomb={pTomb != null} Lid={pLid != null} Pillars=[{PillarMask(pillars)}]");

        // ── FLOOR: one SM_Bld_Base_Floor_01 per cell, MEASURED and scaled to exactly CELL×CELL ──────
        // (task rule: measure the prefab's bounds at runtime; do NOT assume 5m.) Floor tiles may STRETCH
        // (a tile is allowed to fill its cell); everything else fits uniformly to preserve proportions.
        if (pFloor != null)
        {
            var floorParent = Child(root, "Floor");
            for (int r = 0; r < rows; r++)
                for (int c = 0; c < cols; c++)
                {
                    var w = CellToWorld(c, r, cols, rows);
                    if (Place(pFloor, $"Floor_{c}_{r}", floorParent, new Vector3(w.x, 0f, w.z),
                              CELL, CELL, 0f, true, false, ref nMatFix) != null) nFloor++;   // stretch=true (floor tile), woodish=false
                }
        }
        else Debug.LogWarning("[KitRoom] SM_Bld_Base_Floor_01 missing — floor omitted (no primitive fallback for the base tile).");

        // ── WALLS from the wall_run props (CUTAWAY iso-CRPG rule) ──────────────────────────────────
        // build_room_unified.cs renders EVERY wall_run as a full box (it is a depth/normal greybox). A
        // BEAUTY room viewed from the contract camera (which sits at the −x,−z near corner, Euler 30/45)
        // would be occluded by its own near walls, so we borrow build_atelier_crypt.cs's cutaway (L125-163):
        // keep only the FAR walls the camera sees — the +z BACK row (grid r==0) and the +x RIGHT col
        // (grid c==cols-1) — and omit the −z FRONT row and −x LEFT col. Registration is unaffected: the
        // engine's collision still blocks the full ring; the renderer merely doesn't DRAW the near walls
        // (standard iso convention). Door cells stay GAPS; framed doorways are placed in the pass below.
        var wallParent = Child(root, "Walls");
        var props = GetList(geo, "props");
        if (props != null && pWall != null)
        {
            foreach (var po in props)
            {
                var p = po as Dictionary<string, object>; if (p == null) continue;
                if ((GetStr(p, "kind", "") ?? "").ToLowerInvariant() != "wall_run") continue;
                var cells = GetList(p, "cells"); if (cells == null) continue;
                // orientation of the run (dominant axis) for interior walls
                int minC = int.MaxValue, maxC = int.MinValue, minR = int.MaxValue, maxR = int.MinValue;
                foreach (var co in cells) if (TryCell(co, out int cc, out int rr))
                { minC = Mathf.Min(minC, cc); maxC = Mathf.Max(maxC, cc); minR = Mathf.Min(minR, rr); maxR = Mathf.Max(maxR, rr); }
                bool runAlongX = (maxC - minC) >= (maxR - minR);
                foreach (var co in cells)
                {
                    if (!TryCell(co, out int c, out int r)) continue;
                    if (doorSet.Contains(CellKey(c, r))) continue;             // GAP at door cell
                    bool isBack = (r == 0), isRight = (c == cols - 1);
                    bool isNearEdge = (c == 0) || (r == rows - 1);
                    var w = CellToWorld(c, r, cols, rows);
                    if (isBack)       PlaceWall(pWall, $"WallBack_{c}",  wallParent, new Vector3(w.x, 0f, w.z + CELL * 0.5f), 0f,  ref nMatFix);   // yaw 0: face −z inward
                    else if (isRight) PlaceWall(pWall, $"WallRight_{r}", wallParent, new Vector3(w.x + CELL * 0.5f, 0f, w.z), 90f, ref nMatFix);   // yaw 90: face −x inward
                    else if (isNearEdge) continue;                             // cutaway: omit near (front/left) perimeter walls
                    else PlaceWall(pWall, $"Wall_{c}_{r}", wallParent, new Vector3(w.x, 0f, w.z), runAlongX ? 0f : 90f, ref nMatFix); // interior wall
                    nWall++;
                }
            }
        }
        else if (pWall == null) Debug.LogWarning("[KitRoom] SM_Bld_Base_Wall_01 missing — walls omitted.");

        // ── FRAMED DOORWAYS at door cells on KEPT (far) walls (author's choice; gap if variant absent) ─
        // build_room_unified.cs L308-331 places a door frame at every door cell; here we place the Synty
        // door-wall variant (SM_Bld_Base_45_Wall_Door_01) only where a far wall would otherwise gap, so the
        // doorway READS as a framed opening rather than a bare hole. No variant → leave the gap.
        if (doorCells != null && pDoor != null)
        {
            var doorParent = Child(root, "Doors");
            int dn = 0;
            foreach (var dco in doorCells)
            {
                if (!TryCell(dco, out int c, out int r)) continue;
                bool isBack = (r == 0), isRight = (c == cols - 1);
                if (!(isBack || isRight)) continue;                            // near-wall doors are already cut away
                var w = CellToWorld(c, r, cols, rows);
                if (isBack)  PlaceWall(pDoor, $"Door_{dn}", doorParent, new Vector3(w.x, 0f, w.z + CELL * 0.5f), 0f,  ref nMatFix);
                else         PlaceWall(pDoor, $"Door_{dn}", doorParent, new Vector3(w.x + CELL * 0.5f, 0f, w.z), 90f, ref nMatFix);
                dn++; nDoor++;
            }
        }

        // ── PROPS: pillars / sarcophagus / braziers / kit-or-fallback ──────────────────────────────
        var propParent = Child(root, "Props");
        var braziers = new List<Vector3>();       // world positions for the fire-anchor point lights
        if (props != null)
        {
            foreach (var po in props)
            {
                var p = po as Dictionary<string, object>; if (p == null) continue;
                string kind = (GetStr(p, "kind", "prop") ?? "prop").ToLowerInvariant();
                if (kind == "wall_run") continue;                              // handled above
                string pid = GetStr(p, "id", "prop") ?? "prop";
                var cells = GetList(p, "cells"); if (cells == null || cells.Count == 0) continue;
                Footprint fp = FootprintOf(cells, cols, rows); if (!fp.valid) continue;

                if (kind.Contains("pillar") || kind.Contains("column"))
                {
                    // cycle SM_Bld_Base_Pillar_01..05 DETERMINISTICALLY by cell coords (no RNG); native
                    // footprint (a column must stay slender), seated on the floor at the run centroid.
                    int idx = Mathf.Abs(fp.anchorC * 7 + fp.anchorR * 13) % 5;
                    var pf = pillars[idx];
                    if (pf == null) { for (int k = 0; k < 5 && pf == null; k++) pf = pillars[k]; }   // first available
                    if (pf != null && Place(pf, $"{pid}_Pillar0{idx + 1}", propParent, fp.center, 0f, 0f, 0f, false, false, ref nMatFix) != null) nPillar++;
                    else if (FallbackBox($"{pid}_pillar", propParent, fp, 3.0f, false, ref nMatFix)) nFallback++;
                    continue;
                }
                if (kind.Contains("sarcophagus"))
                {
                    // SM_Prop_Tomb_01 + SM_Prop_Tomb_Lid_01, scaled to the authored footprint; long axis
                    // laid along the wider footprint axis. Lid seated on the tomb's measured top so it
                    // reads CLOSED (build_atelier_crypt.cs L177-196).
                    bool wideX = fp.spanX >= fp.spanZ; float yaw = wideX ? 90f : 0f;
                    if (pTomb != null)
                    {
                        var tomb = Place(pTomb, $"{pid}_Tomb", propParent, fp.center, fp.spanX, fp.spanZ, yaw, false, false, ref nMatFix);
                        if (tomb != null)
                        {
                            nSarc++;
                            if (pLid != null)
                            {
                                var tb = WorldBounds(tomb);
                                var lid = (GameObject)PrefabUtility.InstantiatePrefab(pLid);
                                lid.name = $"{pid}_TombLid"; lid.transform.SetParent(propParent.transform, true);
                                lid.transform.position = Vector3.zero; lid.transform.rotation = Quaternion.Euler(0f, yaw, 0f);
                                // fit the lid's footprint to the tomb top, then seat its base on tb.max.y
                                var lb0 = WorldBounds(lid);
                                float ls = FitScale(lb0, tb.size.x, tb.size.z);
                                lid.transform.localScale = new Vector3(ls, ls, ls);
                                var lb = WorldBounds(lid); Vector3 lpo = lid.transform.position - lb.center;
                                lid.transform.position = new Vector3(tb.center.x + lpo.x, (lpo.y - lb.min.y) + tb.max.y - 0.02f, tb.center.z + lpo.z);
                                FixMaterials(lid, false, ref nMatFix);
                            }
                        }
                    }
                    else if (FallbackBox($"{pid}_sarcophagus", propParent, fp, 1.4f, false, ref nMatFix)) nFallback++;
                    continue;
                }
                if (kind.Contains("brazier"))
                {
                    // braziers are FIRE-VFX ANCHORS at runtime — placement matters more than the mesh, so a
                    // simple built-in fallback: a stone pedestal cylinder + an emissive bowl + an empty
                    // "FireAnchor" child (the runtime flicker/glow convention, CombatSurfaceClient.cs L275,
                    // L3317: fire_anchors → warm glow quads + brazier-light flicker). A warm point light is
                    // added per brazier in the lighting rig below.
                    var b = BuildBrazier(pid, propParent, fp.center);
                    braziers.Add(new Vector3(fp.center.x, 1.7f, fp.center.z));
                    nBrazier++;
                    continue;
                }

                // ── other prop kinds: plausible PolygonGeneric prefab IF present, else primitive fallback ─
                var kit = LoadFirst(Candidates(kind));
                bool woodish = kind.Contains("barrel") || kind.Contains("crate") || kind.Contains("wood") || kind.Contains("cart");
                if (kit != null)
                {
                    if (Place(kit, $"{pid}_{kit.name}", propParent, fp.center, fp.spanX, fp.spanZ, 0f, false, woodish, ref nMatFix) != null) nKit++;
                    else if (FallbackBox(pid, propParent, fp, KindHeight(kind), woodish, ref nMatFix)) nFallback++;
                }
                else if (FallbackBox(pid, propParent, fp, KindHeight(kind), woodish, ref nMatFix)) nFallback++;
            }
        }

        // ── LIGHTING: warm fire-anchor point pools (matching the brazier cells) + one dim cool directional ─
        // Mirrors how the painted crypt reads — warm pools on cool stone (build_atelier_crypt.cs L306-332).
        var lightParent = Child(root, "Lights");
        foreach (var bp in braziers)
        {
            var g = new GameObject("KitRoom_Fire"); g.transform.SetParent(lightParent.transform, true);
            var L = g.AddComponent<Light>(); L.type = LightType.Point;
            L.color = new Color(1f, 0.48f, 0.18f); L.range = 8.5f; L.intensity = 3.4f;   // paint_combat_scene.cs L34 values
            L.shadows = LightShadows.None; g.transform.position = bp;
        }
        {
            var g = new GameObject("KitRoom_CoolKey"); g.transform.SetParent(lightParent.transform, true);
            var L = g.AddComponent<Light>(); L.type = LightType.Directional;
            L.color = new Color(0.55f, 0.62f, 0.80f); L.intensity = 0.35f;               // dim cool key
            L.shadows = LightShadows.Soft; L.shadowStrength = 0.6f;
            g.transform.rotation = Quaternion.Euler(50f, 205f, 0f);
        }
        RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
        RenderSettings.ambientLight = new Color(0.10f, 0.11f, 0.14f);                     // cool stone ambient

        // ── camera (paint contract, 1344x768 fit) so the SAVED scene carries the plate-contract rig ──
        var cam = MainCam(create: true);
        SetupContractCamera(cam, cols, rows, camFit, 1344f / 768f);

        // ── CANONICAL discipline: MarkSceneDirty + SaveScene (no render-and-forget) ──────────────────
        EditorUtility.SetDirty(root);
        var scene = SceneManager.GetActiveScene();
        EditorSceneManager.MarkSceneDirty(scene);
        if (!string.IsNullOrEmpty(scene.path))
        {
            EditorSceneManager.SaveScene(scene);
            Debug.Log($"[KitRoom] scene saved: {scene.path}");
        }
        else Debug.LogWarning("[KitRoom] active scene is untitled — MarkSceneDirty only; save the scene once to persist the kit room.");

        Debug.Log($"[KitRoom] BUILT {rootName} @ {cols}x{rows}: floor={nFloor} walls={nWall} doors={nDoor} " +
                  $"pillars={nPillar} sarcophagi={nSarc} braziers={nBrazier} kit_props={nKit} fallbacks={nFallback} material_fixes={nMatFix}");
    }

    // ════════════════════════════════════════════════════════════════════════════════════════════════
    //  CAPTURE
    // ════════════════════════════════════════════════════════════════════════════════════════════════
    [MenuItem("Tools/WorldOS/Kit/Capture Kit Room")]
    public static void CaptureRoom()
    {
        string geoPath = GeoPath();
        int cols = 14, rows = 11; bool camFit = true; string roomId = "room";
        if (File.Exists(geoPath))
        {
            var geo = MiniJson.Parse(File.ReadAllText(geoPath)) as Dictionary<string, object>;
            if (geo != null) { cols = GetInt(geo, "cols", 14); rows = GetInt(geo, "rows", 11); camFit = GetBool(geo, "camera_fit"); roomId = RoomId(geo, geoPath); }
        }

        var root = GameObject.Find("KitRoom_" + roomId);
        if (root == null) root = FindAnyKitRoom();
        if (root == null) { Debug.LogError("[KitRoom] no KitRoom_* root in the scene — run Build Room From Kit first."); return; }

        var cam = MainCam(create: true);
        // CONTRACT camera + fit-ortho reused from build_room_unified.cs L40-56, but ASPECT set to the
        // 2560x1600 review-render frame so the room diamond fills ~96% of the ACTUAL output width.
        const int W = 2560, H = 1600;
        SetupContractCamera(cam, cols, rows, camFit, (float)W / H);

        // isolate: hide renderers NOT under the kit root + disable lights NOT under it, so the capture is
        // clean (build_atelier_crypt.cs L300-304). Restore afterward — capture never saves the scene.
        var hidRends = new List<Renderer>();
        foreach (var rr in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None))
        { if (rr == null || rr.transform.IsChildOf(root.transform)) continue; if (rr.enabled) { rr.enabled = false; hidRends.Add(rr); } }
        var disLights = new List<Light>();
        foreach (var ll in UnityEngine.Object.FindObjectsByType<Light>(FindObjectsSortMode.None))
        { if (ll == null || ll.transform.IsChildOf(root.transform)) continue; if (ll.enabled) { ll.enabled = false; disLights.Add(ll); } }

        string outPath = null;
        try
        {
            string stamp = DateTime.UtcNow.ToString("yyyyMMdd'T'HHmm'Z'", CultureInfo.InvariantCulture);
            outPath = Path.Combine(CaptureDir(), $"kit_{roomId}_{stamp}.png");
            RenderToPng(cam, W, H, outPath);
        }
        finally
        {
            foreach (var rr in hidRends) if (rr != null) rr.enabled = true;
            foreach (var ll in disLights) if (ll != null) ll.enabled = true;
        }
        Debug.Log($"[KitRoom] captured {W}x{H} → {outPath}");
    }

    // ════════════════════════════════════════════════════════════════════════════════════════════════
    //  GEOMETRY / CONTRACT
    // ════════════════════════════════════════════════════════════════════════════════════════════════
    // CellToWorld — build_room_unified.cs L33-34 (cx0=(cols-1)/2, cy0=(rows-1)/2, CellSize=2).
    static Vector3 CellToWorld(int c, int r, int cols, int rows)
    {
        float cx0 = (cols - 1) / 2.0f, cy0 = (rows - 1) / 2.0f;
        return new Vector3((c - cx0) * CELL, 0f, (cy0 - r) * CELL);
    }

    // Contract camera + fit-ortho — build_room_unified.cs L40-56 (ASPECT parameterized to the render frame).
    static void SetupContractCamera(Camera cam, int cols, int rows, bool camFit, float aspect)
    {
        Quaternion crot = Quaternion.Euler(30f, 45f, 0f);                       // L40
        float FILL = 0.96f;                                                     // L41
        float ortho = 13f;                                                      // L42 default
        if (camFit)
        {
            Vector3 rightAx = crot * Vector3.right, upAx = crot * Vector3.up;   // L44
            float maxR = 0f, maxU = 0f;
            float hx = (cols / 2f) * CELL, hz = (rows / 2f) * CELL;             // L46
            foreach (var sgn in new[] { new Vector2(1, 1), new Vector2(1, -1), new Vector2(-1, 1), new Vector2(-1, -1) })
            {
                Vector3 corner = new Vector3(hx * sgn.x, 0f, hz * sgn.y);       // L48
                maxR = Mathf.Max(maxR, Mathf.Abs(Vector3.Dot(corner, rightAx)));// L49
                maxU = Mathf.Max(maxU, Mathf.Abs(Vector3.Dot(corner, upAx)));   // L50
            }
            ortho = Mathf.Max(maxR / (aspect * FILL), maxU / FILL);            // L52
        }
        cam.orthographic = true; cam.orthographicSize = ortho; cam.nearClipPlane = 0.3f; cam.farClipPlane = 500f;  // L54
        cam.transform.rotation = crot; cam.transform.position = -(crot * Vector3.forward) * 80f;                    // L55
        cam.clearFlags = CameraClearFlags.SolidColor; cam.backgroundColor = new Color(0.05f, 0.05f, 0.07f);         // L56
    }

    // ════════════════════════════════════════════════════════════════════════════════════════════════
    //  PREFAB PLACEMENT (build_atelier_crypt.cs L38-89)
    // ════════════════════════════════════════════════════════════════════════════════════════════════
    static GameObject LoadPrefab(string nm)
    {
        var guids = AssetDatabase.FindAssets(nm + " t:Prefab");
        foreach (var g in guids)
        {
            var pth = AssetDatabase.GUIDToAssetPath(g);
            if (Path.GetFileNameWithoutExtension(pth) == nm) return AssetDatabase.LoadAssetAtPath<GameObject>(pth);
        }
        if (guids.Length > 0) return AssetDatabase.LoadAssetAtPath<GameObject>(AssetDatabase.GUIDToAssetPath(guids[0]));
        return null;
    }

    static GameObject LoadFirst(string[] names)
    {
        if (names == null) return null;
        foreach (var n in names) { var pf = LoadPrefab(n); if (pf != null) return pf; }
        return null;
    }

    // world-space renderer bounds (Synty pivots are often base/corner-based — MEASURE, never assume).
    static Bounds WorldBounds(GameObject go)
    {
        var rends = go.GetComponentsInChildren<Renderer>();
        if (rends.Length == 0) return new Bounds(go.transform.position, Vector3.zero);
        var b = rends[0].bounds; for (int i = 1; i < rends.Length; i++) b.Encapsulate(rends[i].bounds); return b;
    }

    static float FitScale(Bounds b, float spanX, float spanZ)
    {
        float sx = (spanX > 0f && b.size.x > 1e-4f) ? spanX / b.size.x : float.MaxValue;
        float sz = (spanZ > 0f && b.size.z > 1e-4f) ? spanZ / b.size.z : float.MaxValue;
        float s = Mathf.Min(sx, sz);
        return (s <= 0f || s == float.MaxValue) ? 1f : s;
    }

    // Instantiate `prefab`, scale to a `spanX×spanZ` footprint (0 = keep native), seat base on the floor
    // (min.y → 0) centered on worldXZ, apply the material-defensive walk. `stretch` allows a non-uniform
    // fit (floor tiles only); everything else fits UNIFORMLY to preserve proportions.
    static GameObject Place(GameObject prefab, string name, GameObject parent, Vector3 worldXZ,
                            float spanX, float spanZ, float yaw, bool stretch, bool woodish, ref int matFix)
    {
        if (prefab == null) { Debug.LogWarning($"[KitRoom] MISSING prefab for {name}"); return null; }
        var inst = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
        inst.name = name; inst.transform.SetParent(parent.transform, true);
        inst.transform.position = Vector3.zero; inst.transform.rotation = Quaternion.Euler(0f, yaw, 0f); inst.transform.localScale = Vector3.one;
        var b0 = WorldBounds(inst);
        if (spanX > 0f && spanZ > 0f && b0.size.x > 1e-4f && b0.size.z > 1e-4f)
        {
            if (stretch) { float sx = spanX / b0.size.x, sz = spanZ / b0.size.z; inst.transform.localScale = new Vector3(sx, (sx + sz) * 0.5f, sz); }
            else { float s = FitScale(b0, spanX, spanZ); inst.transform.localScale = new Vector3(s, s, s); }
        }
        var b1 = WorldBounds(inst);
        Vector3 pivotOffset = inst.transform.position - b1.center;
        inst.transform.position = new Vector3(worldXZ.x + pivotOffset.x, pivotOffset.y - b1.min.y, worldXZ.z + pivotOffset.z);
        FixMaterials(inst, woodish, ref matFix);
        return inst;
    }

    // Wall placer — build_atelier_crypt.cs L140-153: scale the LONG local axis so the footprint spans one
    // CELL edge, keep NATIVE height + thickness (the kit is designed to tile at native height), yaw so the
    // face points inward, then seat base on the floor at edgePos.
    static void PlaceWall(GameObject prefab, string name, GameObject parent, Vector3 edgePos, float yaw, ref int matFix)
    {
        if (prefab == null) return;
        var inst = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
        inst.name = name; inst.transform.SetParent(parent.transform, true);
        inst.transform.position = Vector3.zero; inst.transform.rotation = Quaternion.identity; inst.transform.localScale = Vector3.one;
        var b0 = WorldBounds(inst);
        float longAxis = Mathf.Max(b0.size.x, b0.size.z);
        float s = longAxis > 1e-4f ? CELL / longAxis : 1f;
        inst.transform.localScale = new Vector3(s, 1f, 1f);   // long axis → one CELL edge; height + thickness native
        inst.transform.rotation = Quaternion.Euler(0f, yaw, 0f);
        var b1 = WorldBounds(inst);
        Vector3 pivotOffset = inst.transform.position - b1.center;
        inst.transform.position = new Vector3(edgePos.x + pivotOffset.x, pivotOffset.y - b1.min.y, edgePos.z + pivotOffset.z);
        FixMaterials(inst, false, ref matFix);
    }

    // ════════════════════════════════════════════════════════════════════════════════════════════════
    //  MATERIAL-DEFENSIVE RULE (build_atelier_crypt.cs L267-293)
    // ════════════════════════════════════════════════════════════════════════════════════════════════
    // DungeonMap props ship with NULL / error-shader material references → Unity renders them MAGENTA. After
    // instantiating ANY kit piece, walk its renderers; every null / missing / error-shader material slot is
    // reassigned a working PolygonGeneric material (stone-ish for structure/tombs, wood-ish for furniture).
    // Lookups are cached; each distinct bad material is logged ONCE.
    static void FixMaterials(GameObject inst, bool woodish, ref int matFix)
    {
        ResolveFallbackMaterials();
        Material repl = woodish ? _woodMat : _stoneMat;
        if (repl == null) return;
        foreach (var rend in inst.GetComponentsInChildren<Renderer>(true))
        {
            if (rend == null) continue;
            var mats = rend.sharedMaterials; bool changed = false;
            for (int i = 0; i < mats.Length; i++)
            {
                var m = mats[i];
                bool bad = m == null || m.shader == null || m.shader.name == "Hidden/InternalErrorShader";
                if (!bad) continue;
                string key = (m == null) ? "<null>" : m.name;
                mats[i] = repl; changed = true; matFix++;
                if (_loggedBadMats.Add(key))
                    Debug.Log($"[KitRoom] material fix: '{key}' → '{repl.name}' (first occurrence; repeats silenced)");
            }
            if (changed) rend.sharedMaterials = mats;
        }
    }

    static void ResolveFallbackMaterials()
    {
        if (_matResolved) return; _matResolved = true;
        string genDir = "Assets/Synty/PolygonGeneric";
        string[] dirs = AssetDatabase.IsValidFolder(genDir) ? new[] { genDir } : null;
        _stoneMat = FindMaterial(dirs, new[] { "stone", "rock", "concrete", "cobble", "granite", "brick" });
        _woodMat  = FindMaterial(dirs, new[] { "wood", "plank", "timber", "oak", "bark" });
        if (_stoneMat == null) { _stoneMat = new Material(Shader.Find("Standard")); _stoneMat.color = new Color(0.50f, 0.49f, 0.47f); _stoneMat.SetFloat("_Glossiness", 0.05f); }
        if (_woodMat == null)  { _woodMat  = new Material(Shader.Find("Standard")); _woodMat.color  = new Color(0.45f, 0.33f, 0.20f); _woodMat.SetFloat("_Glossiness", 0.05f); }
    }

    static Material FindMaterial(string[] dirs, string[] prefs)
    {
        string[] guids = dirs != null ? AssetDatabase.FindAssets("t:Material", dirs) : AssetDatabase.FindAssets("t:Material");
        Material first = null;
        foreach (var g in guids)
        {
            var pth = AssetDatabase.GUIDToAssetPath(g);
            var m = AssetDatabase.LoadAssetAtPath<Material>(pth);
            if (m == null || m.shader == null || m.shader.name == "Hidden/InternalErrorShader") continue;
            if (first == null) first = m;
            string low = Path.GetFileNameWithoutExtension(pth).ToLowerInvariant();
            foreach (var pref in prefs) if (low.Contains(pref)) return m;
        }
        return first;
    }

    // ════════════════════════════════════════════════════════════════════════════════════════════════
    //  BRAZIER + FALLBACK PRIMITIVES
    // ════════════════════════════════════════════════════════════════════════════════════════════════
    static GameObject BuildBrazier(string pid, GameObject parent, Vector3 center)
    {
        var g = new GameObject($"{pid}_Brazier"); g.transform.SetParent(parent.transform, true); g.transform.position = center;
        var ped = GameObject.CreatePrimitive(PrimitiveType.Cylinder); ped.name = "Pedestal";
        UnityEngine.Object.DestroyImmediate(ped.GetComponent<Collider>());
        ped.transform.SetParent(g.transform, false); ped.transform.localPosition = new Vector3(0f, 0.85f, 0f);
        ped.transform.localScale = new Vector3(0.7f, 0.85f, 0.7f);
        ped.GetComponent<Renderer>().sharedMaterial = MakeStd(new Color(0.40f, 0.38f, 0.35f), 0.05f);
        var bowl = GameObject.CreatePrimitive(PrimitiveType.Sphere); bowl.name = "Bowl";
        UnityEngine.Object.DestroyImmediate(bowl.GetComponent<Collider>());
        bowl.transform.SetParent(g.transform, false); bowl.transform.localPosition = new Vector3(0f, 1.75f, 0f);
        bowl.transform.localScale = new Vector3(1.3f, 0.7f, 1.3f);
        var bm = MakeStd(new Color(0.86f, 0.55f, 0.28f), 0.15f); bm.EnableKeyword("_EMISSION"); bm.SetColor("_EmissionColor", new Color(1f, 0.45f, 0.15f) * 1.2f);
        bowl.GetComponent<Renderer>().sharedMaterial = bm;
        // empty anchor child at the flame (runtime fire-VFX / glow-quad attach point)
        var anchor = new GameObject("FireAnchor"); anchor.transform.SetParent(g.transform, false); anchor.transform.localPosition = new Vector3(0f, 2.0f, 0f);
        return g;
    }

    static bool FallbackBox(string pid, GameObject parent, Footprint fp, float height, bool woodish, ref int matFix)
    {
        var b = GameObject.CreatePrimitive(PrimitiveType.Cube); b.name = $"{pid}_fallback";
        UnityEngine.Object.DestroyImmediate(b.GetComponent<Collider>());
        b.transform.SetParent(parent.transform, true);
        b.transform.position = new Vector3(fp.center.x, height * 0.5f, fp.center.z);
        b.transform.localScale = new Vector3(Mathf.Max(1.2f, fp.spanX * 0.9f), height, Mathf.Max(1.2f, fp.spanZ * 0.9f));
        ResolveFallbackMaterials();
        b.GetComponent<Renderer>().sharedMaterial = woodish ? _woodMat : _stoneMat;
        Debug.Log($"[KitRoom] no kit prefab for '{pid}' — primitive fallback ({fp.spanX:F1}x{fp.spanZ:F1}, h={height:F1}).");
        return true;
    }

    static Material MakeStd(Color c, float gloss = 0.05f)
    {
        var m = new Material(Shader.Find("Standard")); m.color = c; m.SetFloat("_Metallic", 0f); m.SetFloat("_Glossiness", gloss); return m;
    }

    // candidate PolygonGeneric prefab names per kind (first found wins; none → primitive fallback).
    static string[] Candidates(string kind)
    {
        if (kind.Contains("barrel")) return new[] { "SM_Prop_Barrel_01", "SM_Prop_Barrel_02", "SM_Generic_Barrel_01" };
        if (kind.Contains("crate") || kind.Contains("box")) return new[] { "SM_Prop_Crate_01", "SM_Prop_Crate_02", "SM_Generic_Crate_01" };
        if (kind.Contains("rubble") || kind.Contains("debris")) return new[] { "SM_Prop_Rubble_01", "SM_Env_Rubble_01", "SM_Prop_Rocks_01", "SM_Prop_Rock_01" };
        if (kind.Contains("urn") || kind.Contains("pot") || kind.Contains("vase")) return new[] { "SM_Prop_Urn_01", "SM_Prop_Pot_01", "SM_Prop_Vase_01" };
        if (kind.Contains("well")) return new[] { "SM_Prop_Well_01", "SM_Prop_Coffin_01", "SM_Prop_Tomb_01" };
        return new string[0];
    }

    static float KindHeight(string kind)
    {
        if (kind.Contains("barrel")) return 1.5f;
        if (kind.Contains("crate") || kind.Contains("box")) return 1.4f;
        if (kind.Contains("rubble") || kind.Contains("debris")) return 0.6f;
        if (kind.Contains("urn") || kind.Contains("pot") || kind.Contains("vase")) return 1.2f;
        if (kind.Contains("well")) return 1.3f;
        return 1.4f;
    }

    // ════════════════════════════════════════════════════════════════════════════════════════════════
    //  CAPTURE / CAMERA / SCENE HELPERS
    // ════════════════════════════════════════════════════════════════════════════════════════════════
    static void RenderToPng(Camera cam, int W, int H, string outPath)
    {
        var rt = new RenderTexture(W, H, 24, RenderTextureFormat.ARGB32); rt.Create();
        float pa = cam.aspect; var pt = cam.targetTexture; Texture2D t2 = null;
        try
        {
            cam.targetTexture = rt; cam.aspect = (float)W / H; cam.Render();
            var pAct = RenderTexture.active;
            try { RenderTexture.active = rt; t2 = new Texture2D(W, H, TextureFormat.RGB24, false); t2.ReadPixels(new Rect(0, 0, W, H), 0, 0); t2.Apply(); }
            finally { RenderTexture.active = pAct; }
            Directory.CreateDirectory(Path.GetDirectoryName(outPath));
            File.WriteAllBytes(outPath, t2.EncodeToPNG());
        }
        finally
        {
            cam.targetTexture = pt; cam.aspect = pa;
            if (t2 != null) UnityEngine.Object.DestroyImmediate(t2);
            rt.Release(); UnityEngine.Object.DestroyImmediate(rt);
        }
    }

    static Camera MainCam(bool create)
    {
        Camera cam = Camera.main;
        if (cam == null && Camera.allCameras.Length > 0) cam = Camera.allCameras[0];
        if (cam == null && create) { var cg = new GameObject("Main Camera"); cam = cg.AddComponent<Camera>(); cg.tag = "MainCamera"; }
        return cam;
    }

    static GameObject FindAnyKitRoom()
    {
        foreach (var g in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None))
            if (g != null && g.transform.parent == null && g.name.StartsWith("KitRoom_")) return g;
        return null;
    }

    static GameObject Child(GameObject root, string name)
    {
        var g = new GameObject(name); g.transform.SetParent(root.transform, false); return g;
    }

    // ════════════════════════════════════════════════════════════════════════════════════════════════
    //  JSON HELPERS (schema mirrors build_room_unified.cs L26-34, L166-179, L310-315)
    // ════════════════════════════════════════════════════════════════════════════════════════════════
    struct Footprint { public bool valid; public Vector3 center; public float spanX, spanZ; public int anchorC, anchorR; }

    static Footprint FootprintOf(List<object> cells, int cols, int rows)
    {
        float minX = float.MaxValue, maxX = float.MinValue, minZ = float.MaxValue, maxZ = float.MinValue;
        int anchorC = 0, anchorR = 0; bool got = false;
        foreach (var co in cells)
        {
            if (!TryCell(co, out int c, out int r)) continue;
            if (!got) { anchorC = c; anchorR = r; got = true; }
            var w = CellToWorld(c, r, cols, rows);
            minX = Mathf.Min(minX, w.x); maxX = Mathf.Max(maxX, w.x); minZ = Mathf.Min(minZ, w.z); maxZ = Mathf.Max(maxZ, w.z);
        }
        if (!got) return new Footprint { valid = false };
        return new Footprint
        {
            valid = true,
            center = new Vector3((minX + maxX) * 0.5f, 0f, (minZ + maxZ) * 0.5f),
            spanX = (maxX - minX) + CELL, spanZ = (maxZ - minZ) + CELL,
            anchorC = anchorC, anchorR = anchorR
        };
    }

    static long CellKey(int c, int r) => ((long)c << 20) ^ (uint)r;

    static bool TryCell(object co, out int c, out int r)
    {
        c = 0; r = 0;
        var cc = co as List<object>; if (cc == null || cc.Count < 2) return false;
        c = Convert.ToInt32(cc[0]); r = Convert.ToInt32(cc[1]); return true;
    }

    static int GetInt(Dictionary<string, object> d, string k, int def) => d != null && d.TryGetValue(k, out var v) ? Convert.ToInt32(v) : def;
    static bool GetBool(Dictionary<string, object> d, string k) => d != null && d.TryGetValue(k, out var v) && v is bool b && b;
    static string GetStr(Dictionary<string, object> d, string k, string def) => d != null && d.TryGetValue(k, out var v) ? (v as string ?? def) : def;
    static List<object> GetList(Dictionary<string, object> d, string k) => d != null && d.TryGetValue(k, out var v) ? v as List<object> : null;

    static string RoomId(Dictionary<string, object> geo, string geoPath)
    {
        string env = Environment.GetEnvironmentVariable("WORLDOS_KIT_ROOM_ID");
        if (!string.IsNullOrEmpty(env)) return Sanitize(env);
        string loc = GetStr(geo, "location", null);
        if (!string.IsNullOrEmpty(loc)) return Sanitize(loc);
        return Sanitize(Path.GetFileNameWithoutExtension(geoPath).Replace("_geometry", ""));
    }

    static string Sanitize(string s)
    {
        var sb = new System.Text.StringBuilder();
        foreach (var ch in s) sb.Append(char.IsLetterOrDigit(ch) || ch == '_' || ch == '-' ? ch : '_');
        return sb.ToString();
    }

    static string PillarMask(GameObject[] p)
    {
        var sb = new System.Text.StringBuilder();
        for (int i = 0; i < p.Length; i++) sb.Append(p[i] != null ? (i + 1).ToString() : "-");
        return sb.ToString();
    }
}
#endif // UNITY_EDITOR
