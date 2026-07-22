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
// r3 (round-3 beauty pass, #83): fixes measured against evidence frames kit_crypt_r1.png / kit_crypt_r2.png
// vs plates/crypt_v36_registered.png — (1) walls given real height+thickness MASS (were paper-thin, person-
// height planes); (2) pillars widened to fat carved columns (were toothpicks); (3) lighting rebaked brighter
// (raised ambient, real cool key, warm fire pools, + a warm sarcophagus centre glow); (4) braziers reshaped
// to a dark bowl + narrow stem + ember-orange emissive disc (were glowing mushroom orbs); (5) sarcophagus
// raised to waist-high mass. Deterministic, material-defensive, SaveScene — all preserved.
//
// r5 (#83 r4-probe fixes, measured live on the box): the r3/r4 pillar scaler produced wide thin SLABS —
// SM_Bld_Base_Pillar_01 natural bounds (0.43,3.02,0.43) went to world (1.80,5.00,0.90), x/z asymmetric (z
// half x) and default GREY. r5 — (1) PILLAR SCALING switched to the proven MEASURED-MULTIPLIER method:
// measure the instance's own renderer world bounds, multiply localScale per-axis by target/measured for a
// world target (1.2,4.0,1.2), then RE-MEASURE and Debug.Log the achieved size + loud warning if any axis
// is off >10% (both x/z share one target → asymmetry gone); (2) PILLAR MATERIAL — force the stone/brick
// material onto pillars (they shipped a valid-but-grey material FixMaterials would skip, so r4b left them
// default grey); (3) WALL HEIGHT baked to 1.5× the r3 value (kit_crypt_r4b.png read RIGHT at 1.5×). r3
// lighting / braziers / tomb unchanged.
//
// r7 (#83 r6-measurement fixes; kit_crypt_r6 scored 58.85% vs the 99% bar — 60 invisible-wall + 19
// walk-through cells): six placement/lighting fixes driven by qa/registration_score.py per-cell attribution.
// (1) IMPASSABLE COVERAGE — after walls/doors/props, every geometry `impassable` cell the builder left
// visually OPEN (interior buttress cells (2,1),(3,1),(9,1),(11,1),(12,1) that live in walls/impassable ONLY,
// never in a wall_run prop) gets a deterministic stone mass: a BUTTRESS if orthogonally adjacent to a
// wall_run cell, else a low rubble PLINTH. (2) WALL BASE — walls thickened to ≥1.4u and CENTERED on their
// cell so the perimeter cell floor no longer shows bare (was a thin outer-edge skin). (3) CAMERA-SIDE
// PARAPET — the cut-away near (front/left) perimeter walls now render a LOW 0.55u parapet so those edge
// cells read blocked while the interior stays visible. (4) PILLAR FOOTPRINT-DRIVEN SIZE — pillar world x/z
// derived from the authored footprint (span × 0.85, floored 1.2) so a 1×2 footprint reads covered on BOTH
// cells (was a fixed 1.2×1.2 covering ~one cell). (5) DOORWAYS OPEN — any closed door LEAF in a door cell is
// disabled so the doorway reads walkable (r6 walk-through at (15,5)). (6) FLAT-LIGHT SCORE FRAME — CaptureRoom
// emits a THIRD 1344x768 contract PNG (kit_<room>_<ts>_score.png) with the room lights off + flat grey
// ambient, so registration reads object PLACEMENT not brazier LIGHT POOLS (the r6 19 walk-through false
// positives). r3/r5 lighting / braziers / tomb otherwise unchanged; still deterministic + material-defensive.
//
// r8 (#83/#84 albedo separation): the r7 flat-light score frame scored 67.19% (lit 72.40%) vs the 99 bar,
// and ALL 35 shared invisible-wall residuals were ONE class — a correctly-placed dark stone MASS on a
// similar-toned floor, scoring 0.33-1.73 against BLOCK_T=1.85 (need ~2× contrast). The masses ARE placed;
// they just don't SEPARATE tonally. r8 makes the flat capture ALBEDO-SEPARATED so it measures PLACEMENT:
// (1) FLOOR ALBEDO — one shared KitFloor_PaleStone (pale warm-grey, cloned from the tile's own material so
// the kit texture survives) on every floor tile. (2) MASS ALBEDO — one shared KitMass_DarkMasonry forced
// onto walls / parapets / buttresses / plinths / pillars / rubble (clearly dark vs the floor). (3) BRAZIER
// BASES — a dark stone plinth (1.3×0.45×1.3) under each brazier so the impassable brazier cell reads massed
// (the thin stem covered almost none of the cell quad — r7 brazier cells read as floor). (4) RUBBLE raised
// to 0.85u + dark masonry (r7 rubble read as floor). (5) wooden fallback boxes get KitProp_DarkWood (r7
// barrel (10,7) scored 0.33). (2b) the TOMB — its natural material stays UNLESS it lands near the floor; the
// r7 tomb footprint (7-9,5-6) was the WORST residual cluster (cells 0.13-0.84, dead-on-floor), so it is
// darkened with KitProp_Stone (a focal-prop mid-stone, not the full mass dark). Lighting rig / braziers /
// capture logic / pillar sizing / buttress pass are UNCHANGED from r7. The lit render now reads
// darker-walled / paler-floored — acceptable; beauty is tuned in the painterly post stage, not here. All r8
// materials are runtime-only shared instances created once per build (NEVER written to the AssetDatabase),
// reset at the top of BuildRoom; still deterministic (no RNG) + material-defensive.
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
    // r3 mass targets (world units) — see round-3 defect notes in the header, keyed to kit_crypt_r1/r2.png.
    const float WALL_H = 5.4f;       // r5: wall height baked to 1.5× the r3 value (3.6→5.4) — r4b frame read RIGHT at 1.5×
    const float WALL_T = 1.4f;       // r7: wall thickness ≥1.4u so a wall occupies its CELL row (r6: thin skin left the cell floor bare)
    const float PARAPET_H = 0.55f;   // r7: camera-side cut-away walls render a LOW parapet at this height (edge reads blocked, interior visible)
    // r7 pillar targets — FOOTPRINT-DRIVEN (was the fixed r5 symmetric 1.2×1.2, which covered ~one cell of a
    // 1×2 footprint → the second footprint cell read open). r7 derives the world x/z target from the authored
    // footprint span (× PILLAR_FOOT_FILL, floored at PILLAR_MIN_XZ) so both footprint cells read covered; the
    // r5 measured-multiplier scaling + min.y grounding + achieved-size log/warning are all preserved.
    const float PILLAR_FOOT_FILL = 0.85f; // fraction of the authored footprint span the pillar fills on x and z
    const float PILLAR_MIN_XZ    = 1.2f;  // floor for the per-axis pillar world target (preserves the r5 minimum)
    const float PILLAR_TGT_H     = 4.0f;  // pillar world height target (unchanged)

    // r8 (#83/#84) ALBEDO SEPARATION targets — so the FLAT-LIGHT score frame measures PLACEMENT, not tone.
    // r7 flat score 67.19%: ALL 35 shared invisible-wall residuals were one class — a dark stone MASS that
    // is correctly placed but scores 0.33-1.73 against BLOCK_T=1.85 because it sits on a similar-toned floor
    // (need ~2× contrast). r8 makes the floor pale and the masonry clearly dark so the mass reads as placed.
    static readonly Color FLOOR_PALE = new Color(0.72f, 0.70f, 0.66f); // pale warm-grey floor (mean luminance ~0.62-0.72 flat)
    static readonly Color MASS_DARK  = new Color(0.34f, 0.30f, 0.27f); // walls/parapets/buttresses/plinths/pillars/rubble (~0.25-0.35)
    static readonly Color PROP_STONE = new Color(0.42f, 0.40f, 0.38f); // focal-prop stone — tomb ONLY if it lands near the pale floor
    static readonly Color PROP_DWOOD = new Color(0.30f, 0.24f, 0.18f); // dark wood for wooden fallback boxes (barrel/crate/cart)
    const float RUBBLE_H = 0.85f;                                      // r8: rubble raised so the pile reads as a placed mass, not floor

    // ── fallback-material cache (material-defensive rule) ──────────────────────────────────────────
    static Material _stoneMat;                 // PolygonGeneric stone-ish (structure / tombs)
    static Material _woodMat;                  // PolygonGeneric wood-ish (furniture / crates / barrels)
    static bool _matResolved;
    static HashSet<string> _loggedBadMats = new HashSet<string>();
    // r8 shared albedo material instances — built ONCE per build (lazily, on first use) from an existing
    // base material so the kit shader/texture is preserved and the color multiplies over it. Runtime-only
    // (NEVER written to the AssetDatabase); reset each run below since the room root is rebuilt each run.
    static Material _floorPaleMat;             // KitFloor_PaleStone
    static Material _massDarkMat;              // KitMass_DarkMasonry
    static Material _propStoneMat;             // KitProp_Stone   (tomb, conditional)
    static Material _propWoodMat;              // KitProp_DarkWood (wooden fallback boxes)

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
        // r8: reset the shared albedo material instances so each build creates fresh runtime materials
        _floorPaleMat = null; _massDarkMat = null; _propStoneMat = null; _propWoodMat = null;

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
        GameObject floorParent = null;
        if (pFloor != null)
        {
            floorParent = Child(root, "Floor");
            for (int r = 0; r < rows; r++)
                for (int c = 0; c < cols; c++)
                {
                    var w = CellToWorld(c, r, cols, rows);
                    if (Place(pFloor, $"Floor_{c}_{r}", floorParent, new Vector3(w.x, 0f, w.z),
                              CELL, CELL, 0f, true, false, ref nMatFix) != null) nFloor++;   // stretch=true (floor tile), woodish=false
                }
        }
        else Debug.LogWarning("[KitRoom] SM_Bld_Base_Floor_01 missing — floor omitted (no primitive fallback for the base tile).");

        // ── r8 item 1: PALE-STONE FLOOR ALBEDO ───────────────────────────────────────────────────────
        // The flat score frame reads placement as CONTRAST against the floor; r7's floor was mid-grey so a
        // dark mass barely separated. Build ONE shared material from an already-placed tile's OWN material
        // (preserves the kit floor shader/texture — the pale color multiplies over it) and assign it to every
        // floor renderer so the tiles render light (target mean luminance ~0.62-0.72 in the flat capture).
        if (floorParent != null)
        {
            Material floorSrc = null;
            foreach (var rend in floorParent.GetComponentsInChildren<Renderer>(true))
                if (rend != null && rend.sharedMaterial != null) { floorSrc = rend.sharedMaterial; break; }
            _floorPaleMat = Recolor(floorSrc, "KitFloor_PaleStone", FLOOR_PALE);
            int nFloorMat = 0;
            foreach (var rend in floorParent.GetComponentsInChildren<Renderer>(true))
            {
                if (rend == null) continue;
                var mats = rend.sharedMaterials;
                for (int i = 0; i < mats.Length; i++) mats[i] = _floorPaleMat;
                rend.sharedMaterials = mats; nFloorMat++;
            }
            Debug.Log($"[KitRoom] floor albedo: KitFloor_PaleStone ({FLOOR_PALE.r:F2},{FLOOR_PALE.g:F2},{FLOOR_PALE.b:F2}) → {nFloorMat} tile renderer(s).");
        }

        // ── WALLS from the wall_run props (CUTAWAY iso-CRPG rule + r7 parapet) ──────────────────────
        // build_room_unified.cs renders EVERY wall_run as a full box (it is a depth/normal greybox). A
        // BEAUTY room viewed from the contract camera (which sits at the −x,−z near corner, Euler 30/45)
        // would be occluded by its own near walls, so we borrow build_atelier_crypt.cs's cutaway (L125-163):
        // draw FULL walls only on the FAR sides the camera sees — the +z BACK row (grid r==0) and the +x
        // RIGHT col (grid c==cols-1). r7: the −z FRONT row and −x LEFT col are NO LONGER fully omitted — they
        // render a LOW parapet (PARAPET_H) so those edge cells read BLOCKED for registration while the
        // interior stays unoccluded. r7 also CENTERS every wall on its cell (was outer-edge, which left the
        // perimeter cell floor bare) and thickens it to ≥ WALL_T. Registration is unaffected by the cutaway:
        // the engine's collision still blocks the full ring. Door cells stay GAPS; framed doorways below.
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
                    // r7: CENTER perimeter walls on their cell (was offset to the outer edge, which left the
                    // cell floor bare — r6 read those cells painted-OPEN). Near (camera-side) walls are no
                    // longer cut away — they render a LOW parapet so the edge reads blocked, interior visible.
                    // r8 item 2: force the dark-masonry albedo on every wall/parapet so the mass reads clearly
                    // dark vs the pale floor (r7 grey-on-grey walls were the bulk of the invisible-wall residuals).
                    if (isBack)       PlaceWall(pWall, $"WallBack_{c}",  wallParent, new Vector3(w.x, 0f, w.z), 0f,  ref nMatFix, forceMat: MassMat());   // yaw 0: face −z inward
                    else if (isRight) PlaceWall(pWall, $"WallRight_{r}", wallParent, new Vector3(w.x, 0f, w.z), 90f, ref nMatFix, forceMat: MassMat());   // yaw 90: face −x inward
                    else if (isNearEdge) PlaceWall(pWall, $"Parapet_{c}_{r}", wallParent, new Vector3(w.x, 0f, w.z), (c == 0) ? 90f : 0f, ref nMatFix, PARAPET_H, true, MassMat()); // r7 camera-side low parapet
                    else PlaceWall(pWall, $"Wall_{c}_{r}", wallParent, new Vector3(w.x, 0f, w.z), runAlongX ? 0f : 90f, ref nMatFix, forceMat: MassMat()); // interior wall
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
                // r7: center the doorway on its cell (matches the r7 wall centering) and STRIP any closed door
                // LEAF so the opening reads WALKABLE (packet item 5; r6 walk-through at (15,5) was a leaf).
                var dInst = PlaceWall(pDoor, $"Door_{dn}", doorParent, new Vector3(w.x, 0f, w.z), isBack ? 0f : 90f, ref nMatFix);
                StripDoorLeaf(dInst);
                dn++; nDoor++;
            }
        }

        // ── PROPS: pillars / sarcophagus / braziers / kit-or-fallback ──────────────────────────────
        var propParent = Child(root, "Props");
        var braziers = new List<Vector3>();       // world positions for the fire-anchor point lights
        Vector3 sarcGlowPos = Vector3.zero; bool haveSarcGlow = false;   // r3: warm centre-glow over the tomb
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
                    // cycle SM_Bld_Base_Pillar_01..05 DETERMINISTICALLY by cell coords (no RNG). r7: the pillar
                    // world x/z target is FOOTPRINT-DRIVEN — the authored footprint span × PILLAR_FOOT_FILL
                    // (floored at PILLAR_MIN_XZ) — so a 1×2 authored footprint reads covered on BOTH cells (r5's
                    // fixed 1.2×1.2 covered ~one cell → the other read open). Height stays PILLAR_TGT_H.
                    // (Footprint.spanX/spanZ are the FULL world spans of the footprint; the packet's "span×2×0.85"
                    // is the same target expressed from the half-span — see the deviation note in the PR.)
                    int idx = Mathf.Abs(fp.anchorC * 7 + fp.anchorR * 13) % 5;
                    var pf = pillars[idx];
                    if (pf == null) { for (int k = 0; k < 5 && pf == null; k++) pf = pillars[k]; }   // first available
                    float ptx = Mathf.Max(PILLAR_MIN_XZ, fp.spanX * PILLAR_FOOT_FILL);
                    float ptz = Mathf.Max(PILLAR_MIN_XZ, fp.spanZ * PILLAR_FOOT_FILL);
                    if (pf != null && PlacePillar(pf, $"{pid}_Pillar0{idx + 1}", propParent, fp.center, ptx, ptz, PILLAR_TGT_H, ref nMatFix) != null) nPillar++;
                    else if (FallbackBox($"{pid}_pillar", propParent, fp, 4.2f, false, ref nMatFix, MassMat())) nFallback++;   // r3: taller fat fallback; r8: dark-masonry albedo
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
                            // r3: the painted sarcophagus is waist-high MASS; r1/r2's tomb read as a flat slab.
                            // If the fitted tomb is under 1.2u tall, raise ONLY its vertical scale so it lands
                            // ~1.3u and re-seat its base on the floor (footprint unchanged). Done BEFORE the lid
                            // pass so the lid seats on the raised top.
                            var tb0 = WorldBounds(tomb);
                            if (tb0.size.y > 1e-4f && tb0.size.y < 1.2f)
                            {
                                float ky = 1.3f / tb0.size.y;
                                var ls0 = tomb.transform.localScale;
                                tomb.transform.localScale = new Vector3(ls0.x, ls0.y * ky, ls0.z);
                                var tbR = WorldBounds(tomb);
                                Vector3 tOff = tomb.transform.position - tbR.center;
                                tomb.transform.position = new Vector3(tbR.center.x + tOff.x, tOff.y - tbR.min.y, tbR.center.z + tOff.z);
                            }
                            // r8 item 2 (VERIFIED, conditional): the tomb footprint (7-9,5-6) was the WORST
                            // residual cluster in the r7 flat frame — every cell an invisible wall scoring
                            // 0.13-0.84 (kit_crypt_r7_registration.json), i.e. the tomb read essentially AS the
                            // floor. Its natural material lands near the (now pale) floor, so darken it with
                            // KitProp_Stone (a mid stone — keeps the focal-prop character, not the full mass dark)
                            // so it reads as a placed mass. Applied to the tomb AND its lid so they read as one.
                            FixMaterials(tomb, false, ref nMatFix, force: true, overrideMat: PropStoneMat());
                            // remember the tomb centre for the warm rim light baked in the lighting rig below
                            var tbNow = WorldBounds(tomb);
                            sarcGlowPos = new Vector3(tbNow.center.x, tbNow.max.y + 1.6f, tbNow.center.z); haveSarcGlow = true;
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
                                FixMaterials(lid, false, ref nMatFix, force: true, overrideMat: PropStoneMat());   // r8 item 2: match the tomb (one mass)
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
                    // r8 item 3: a dark stone plinth (1.3 × 0.45h × 1.3) centered on the brazier cell, seated on
                    // the floor. The brazier cell is impassable but the thin stem covers almost none of the cell
                    // quad in the flat view (r7 brazier cells read as floor); the plinth gives the cell a mass.
                    PlaceMassBox($"{pid}_BrazierBase", propParent, fp.center, 1.3f, 0.45f);
                    braziers.Add(new Vector3(fp.center.x, 1.9f, fp.center.z));   // r3: anchor at the ember disc
                    nBrazier++;
                    continue;
                }

                if (kind.Contains("rubble") || kind.Contains("debris"))
                {
                    // r8 item 4: r7 rubble read as floor (cells (2,9),(2,10),(3,10) scored 0.92-1.49) — too low
                    // and stone-grey. Raise the pile to RUBBLE_H and force the dark-masonry albedo so it reads as
                    // a placed mass in the flat gate (a deterministic primitive mass; any kit rubble mesh is not
                    // used here, so the height + contrast are guaranteed regardless of the pack contents).
                    if (FallbackBox($"{pid}_rubble", propParent, fp, RUBBLE_H, false, ref nMatFix, MassMat())) nFallback++;
                    continue;
                }

                // ── other prop kinds: plausible PolygonGeneric prefab IF present, else primitive fallback ─
                var kit = LoadFirst(Candidates(kind));
                bool woodish = kind.Contains("barrel") || kind.Contains("crate") || kind.Contains("wood") || kind.Contains("cart");
                // r8 item 5: wooden fallback BOXES (e.g. barrel (10,7) scored 0.33) get the dark-wood albedo so
                // they read as a placed prop, not floor. Non-wood fallbacks keep the material-defensive stone default.
                Material fbMat = woodish ? PropWoodMat() : null;
                if (kit != null)
                {
                    if (Place(kit, $"{pid}_{kit.name}", propParent, fp.center, fp.spanX, fp.spanZ, 0f, false, woodish, ref nMatFix) != null) nKit++;
                    else if (FallbackBox(pid, propParent, fp, KindHeight(kind), woodish, ref nMatFix, fbMat)) nFallback++;
                }
                else if (FallbackBox(pid, propParent, fp, KindHeight(kind), woodish, ref nMatFix, fbMat)) nFallback++;
            }
        }

        // ── r7 IMPASSABLE COVERAGE: render every ENGINE-collision cell the passes above left visually OPEN ─
        // PACKET item 1. The geometry `impassable` array is the ENGINE truth for what blocks movement, but the
        // builder only draws wall_runs + prop footprints + door frames — interior buttress cells like
        // (2,1),(9,1),(12,1) live in walls/impassable ONLY, so registration reads them as invisible walls
        // (r6: 60 invisible-wall cells → 58.85%). Compute the set of cells the passes above visually COVER
        // (every wall_run cell ∪ every prop footprint cell ∪ every door cell), then for each impassable cell
        // NOT covered place a deterministic stone mass: a BUTTRESS if orthogonally adjacent to a wall_run cell,
        // else a low rubble PLINTH. Fallback to `walls` if `impassable` is absent. Idempotent: masses are
        // children of the room root, which is destroyed+rebuilt each run.
        int nButtress = 0, nPlinth = 0;
        {
            var covered = new HashSet<long>();
            var wallRunCells = new HashSet<long>();
            if (props != null)
                foreach (var po in props)
                {
                    var p = po as Dictionary<string, object>; if (p == null) continue;
                    var cells = GetList(p, "cells"); if (cells == null) continue;
                    bool isWallRun = (GetStr(p, "kind", "") ?? "").ToLowerInvariant() == "wall_run";
                    foreach (var co in cells)
                    {
                        if (!TryCell(co, out int c, out int r)) continue;
                        covered.Add(CellKey(c, r));
                        if (isWallRun) wallRunCells.Add(CellKey(c, r));
                    }
                }
            foreach (var dk in doorSet) covered.Add(dk);                       // door frames occupy the doorway cell

            var impassable = GetList(geo, "impassable") ?? GetList(geo, "walls");
            if (impassable != null)
            {
                var impParent = Child(root, "Impassable");
                foreach (var ico in impassable)
                {
                    if (!TryCell(ico, out int c, out int r)) continue;
                    if (covered.Contains(CellKey(c, r))) continue;
                    var w = CellToWorld(c, r, cols, rows);
                    bool adjWall = wallRunCells.Contains(CellKey(c - 1, r)) || wallRunCells.Contains(CellKey(c + 1, r))
                                || wallRunCells.Contains(CellKey(c, r - 1)) || wallRunCells.Contains(CellKey(c, r + 1));
                    if (adjWall)
                    {
                        PlaceMassBox($"Buttress_{c}_{r}", impParent, w, 1.7f, 2.2f);   // brick/stone buttress against the wall
                        nButtress++;
                        Debug.Log($"[KitRoom] impassable buttress @ ({c},{r})");
                    }
                    else
                    {
                        PlaceMassBox($"Plinth_{c}_{r}", impParent, w, 1.6f, 0.9f);      // low rubble/plinth mass
                        nPlinth++;
                        Debug.Log($"[KitRoom] impassable plinth @ ({c},{r})");
                    }
                }
                Debug.Log($"[KitRoom] impassable coverage: +{nButtress} buttress +{nPlinth} plinth (uncovered impassable cells)");
            }
        }

        // ── LIGHTING (r3): baked warm-pool-on-cool-stone rig matching the painted crypt read ──────────────
        // r1/r2 were far too dark (near-black floor, no ambient fill, weak cool key). r3 raises the flat
        // ambient, gives the cool directional key real presence, brightens the fire pools, and adds a low
        // warm point light over the sarcophagus (the painted crypt's centre glow). Lights live under the room
        // root, which is destroyed+rebuilt each run (idempotent); PurgeStrayKitLights also sweeps any orphaned
        // prior-build KitRoom_* lights so a re-run never doubles the rig. (build_atelier_crypt.cs L306-332.)
        PurgeStrayKitLights(root);
        var lightParent = Child(root, "Lights");
        foreach (var bp in braziers)
        {
            var g = new GameObject("KitRoom_Fire"); g.transform.SetParent(lightParent.transform, true);
            var L = g.AddComponent<Light>(); L.type = LightType.Point;
            L.color = new Color(1.0f, 0.62f, 0.28f); L.range = 10f; L.intensity = 3.2f;   // r3 warm fire pool
            L.shadows = LightShadows.None; g.transform.position = bp;
        }
        if (haveSarcGlow)   // r3: subtle warm rim over the tomb — the painted crypt's centre glow
        {
            var g = new GameObject("KitRoom_TombGlow"); g.transform.SetParent(lightParent.transform, true);
            var L = g.AddComponent<Light>(); L.type = LightType.Point;
            L.color = new Color(1.0f, 0.66f, 0.34f); L.range = 7f; L.intensity = 1.4f;     // low warm centre rim
            L.shadows = LightShadows.None; g.transform.position = sarcGlowPos;
        }
        {
            var g = new GameObject("KitRoom_CoolKey"); g.transform.SetParent(lightParent.transform, true);
            var L = g.AddComponent<Light>(); L.type = LightType.Directional;
            L.color = new Color(0.65f, 0.72f, 0.90f); L.intensity = 0.7f;                  // r3 cool directional key
            L.shadows = LightShadows.Soft; L.shadowStrength = 0.6f;
            g.transform.rotation = Quaternion.Euler(55f, 30f, 0f);
        }
        RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
        RenderSettings.ambientLight = new Color(0.20f, 0.22f, 0.30f);                      // r3 cool stone ambient fill

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
                  $"pillars={nPillar} sarcophagi={nSarc} braziers={nBrazier} buttresses={nButtress} plinths={nPlinth} " +
                  $"kit_props={nKit} fallbacks={nFallback} material_fixes={nMatFix}");
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

        string outPath = null; string contractPath = null; string scorePath = null;
        try
        {
            string stamp = DateTime.UtcNow.ToString("yyyyMMdd'T'HHmm'Z'", CultureInfo.InvariantCulture);
            outPath = Path.Combine(CaptureDir(), $"kit_{roomId}_{stamp}.png");
            RenderToPng(cam, W, H, outPath);
            // r6: ALSO emit the 1344x768 CONTRACT frame — qa/registration_score.py refuses any other
            // size (its projection math is defined in that frame), so every capture yields both.
            contractPath = Path.Combine(CaptureDir(), $"kit_{roomId}_{stamp}_contract.png");
            SetupContractCamera(cam, cols, rows, camFit, 1344f / 768f);
            RenderToPng(cam, 1344, 768, contractPath);

            // r7 FLAT-LIGHT SCORING FRAME (packet item 6): a THIRD 1344x768 contract frame with the room's
            // OWN lights OFF and a flat grey ambient. Rationale: registration measures object PLACEMENT, not
            // luminance — the r6 19 walk-through false positives were brazier LIGHT POOLS (bright floor read
            // as objects). Disable ALL lights under the room root, force Flat ambient (0.6,0.6,0.6), render,
            // then RESTORE the ambient settings + lights in a finally block so the capture stays side-effect-free.
            scorePath = Path.Combine(CaptureDir(), $"kit_{roomId}_{stamp}_score.png");
            var roomLights = new List<Light>();
            foreach (var ll in root.GetComponentsInChildren<Light>(true))
                if (ll != null && ll.enabled) { ll.enabled = false; roomLights.Add(ll); }
            var savedAmbMode = RenderSettings.ambientMode;
            var savedAmbLight = RenderSettings.ambientLight;
            try
            {
                RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
                RenderSettings.ambientLight = new Color(0.6f, 0.6f, 0.6f);
                SetupContractCamera(cam, cols, rows, camFit, 1344f / 768f);
                RenderToPng(cam, 1344, 768, scorePath);
            }
            finally
            {
                RenderSettings.ambientMode = savedAmbMode;
                RenderSettings.ambientLight = savedAmbLight;
                foreach (var ll in roomLights) if (ll != null) ll.enabled = true;
            }
        }
        finally
        {
            foreach (var rr in hidRends) if (rr != null) rr.enabled = true;
            foreach (var ll in disLights) if (ll != null) ll.enabled = true;
        }
        Debug.Log($"[KitRoom] captured {W}x{H} → {outPath} + contract 1344x768 → {contractPath} + flat-light score → {scorePath}");
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

    // Wall placer — r3 MASS fix. r1/r2 walls read as paper-thin planes barely taller than a person because
    // the old placer kept native height + thickness and only scaled length (and assumed length==local-x).
    // r3: detect which local axis is the length, fit it to one CELL edge, and give the wall real MASS —
    // scale HEIGHT up to ~heightTarget (never shrink a natively-taller wall) and thicken the short horizontal
    // axis up to ~WALL_T (a thin panel gets bulked; a natively-thick wall is left alone). Yaw so the face
    // points inward, then seat base on the floor at edgePos. (build_atelier_crypt.cs L140-153 + r3 mass.)
    // r7: WALL_T raised to 1.4u so the wall occupies its cell row; a `parapet` call (heightTarget=PARAPET_H)
    // is ALLOWED to shrink the height below native so the camera-side low parapet actually reads low. Returns
    // the instance so callers (the door pass) can post-process it (leaf-strip).
    // r8: `forceMat` (item 2) force-assigns the dark-masonry albedo to every renderer via the FixMaterials
    // force path; null (the door pass) keeps the material-defensive default so doorways keep their natural look.
    static GameObject PlaceWall(GameObject prefab, string name, GameObject parent, Vector3 edgePos, float yaw,
                               ref int matFix, float heightTarget = WALL_H, bool parapet = false, Material forceMat = null)
    {
        if (prefab == null) return null;
        var inst = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
        inst.name = name; inst.transform.SetParent(parent.transform, true);
        inst.transform.position = Vector3.zero; inst.transform.rotation = Quaternion.identity; inst.transform.localScale = Vector3.one;
        var b0 = WorldBounds(inst);
        bool xLong = b0.size.x >= b0.size.z;
        float lenSize = xLong ? b0.size.x : b0.size.z;
        float thkSize = xLong ? b0.size.z : b0.size.x;
        float sLen = lenSize > 1e-4f ? CELL / lenSize : 1f;                       // length → one CELL edge
        float sThk = thkSize > 1e-4f ? Mathf.Max(1f, WALL_T / thkSize) : 3f;      // bulk thin walls to ≈WALL_T (never shrink)
        // full walls raise height to wall-mass and never shrink; a parapet is allowed to shrink to heightTarget
        float sy   = b0.size.y > 1e-4f ? (parapet ? heightTarget / b0.size.y : Mathf.Max(1f, heightTarget / b0.size.y)) : 1f;
        inst.transform.localScale = xLong ? new Vector3(sLen, sy, sThk) : new Vector3(sThk, sy, sLen);
        inst.transform.rotation = Quaternion.Euler(0f, yaw, 0f);
        var b1 = WorldBounds(inst);
        Vector3 pivotOffset = inst.transform.position - b1.center;
        inst.transform.position = new Vector3(edgePos.x + pivotOffset.x, pivotOffset.y - b1.min.y, edgePos.z + pivotOffset.z);
        FixMaterials(inst, false, ref matFix, force: forceMat != null, overrideMat: forceMat);   // r8: force mass albedo when supplied
        return inst;
    }

    // r7: strip any closed door LEAF/panel so the doorway reads as an OPENING (packet item 5). The Synty
    // door-wall prefab may include a swinging leaf mesh that fills the opening → the door cell reads
    // painted-BLOCKED but is WALKABLE in the engine (r6 walk-through at (15,5)). Keep frame/jamb/arch/lintel;
    // disable only leaf/panel children. Heuristic by name (prefab internals are unknown on the authoring Mac):
    // a child is a leaf if its name contains "leaf"/"panel", or "door" WITHOUT any structural qualifier.
    // Disabling (not destroying) the GameObject removes it from the render and stays reversible.
    static int StripDoorLeaf(GameObject doorInst)
    {
        if (doorInst == null) return 0;
        int stripped = 0;
        foreach (var t in doorInst.GetComponentsInChildren<Transform>(true))
        {
            if (t == null || t.gameObject == doorInst) continue;
            string n = t.gameObject.name.ToLowerInvariant();
            bool isLeaf = n.Contains("leaf") || n.Contains("panel")
                       || (n.Contains("door") && !n.Contains("wall") && !n.Contains("frame")
                           && !n.Contains("jamb") && !n.Contains("arch") && !n.Contains("lintel"));
            if (isLeaf && t.gameObject.activeSelf) { t.gameObject.SetActive(false); stripped++; }
        }
        if (stripped > 0) Debug.Log($"[KitRoom] door leaf stripped: {stripped} child(ren) disabled on '{doorInst.name}' (doorway reads open)");
        else Debug.Log($"[KitRoom] door '{doorInst.name}': no leaf/panel child matched — doorway already open (or prefab is a bare frame).");
        return stripped;
    }

    // r5/r7 PILLAR PLACER — MEASURED-MULTIPLIER method (#83 r4 probe). Measure the instance's OWN renderer
    // world bounds at localScale=1, then multiply localScale PER-AXIS by target/measured so world lands
    // ≈(tgtX, tgtH, tgtZ). r7: the x/z targets are FOOTPRINT-DRIVEN and per-axis (was the r5 symmetric
    // 1.2×1.2), so a 1×2 authored footprint yields a DEEPER column that covers BOTH footprint cells; the
    // per-axis multiply handles the asymmetry by construction (no return of the r3 z≈half-x slab — that was a
    // scaling bug, this is a deliberate footprint match). RE-MEASURE, Debug.Log the achieved world size, and
    // warn loudly if any axis is off target by >10%. Pillars ship a valid-but-grey material (FixMaterials only
    // replaces null/error slots), so force the stone/brick material on. Seat the base on the floor at the run
    // centroid via min.y (r6: the pivotOffset.y term double-counted the bounds centre for base-pivot meshes).
    static GameObject PlacePillar(GameObject prefab, string name, GameObject parent, Vector3 center,
                                  float tgtX, float tgtZ, float tgtH, ref int matFix)
    {
        if (prefab == null) return null;
        var inst = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
        inst.name = name; inst.transform.SetParent(parent.transform, true);
        inst.transform.position = Vector3.zero; inst.transform.rotation = Quaternion.identity; inst.transform.localScale = Vector3.one;
        // measure THIS instance's renderer world bounds, then per-axis multiply localScale by target/measured
        var b0 = WorldBounds(inst);
        float sx = b0.size.x > 1e-4f ? tgtX / b0.size.x : 1f;
        float sy = b0.size.y > 1e-4f ? tgtH / b0.size.y : 1f;
        float sz = b0.size.z > 1e-4f ? tgtZ / b0.size.z : 1f;
        inst.transform.localScale = new Vector3(sx, sy, sz);
        // RE-MEASURE and assert the achieved world size is within ±10% of target on every axis
        var b1 = WorldBounds(inst);
        Debug.Log($"[KitRoom] pillar '{name}': measured world=({b0.size.x:F2},{b0.size.y:F2},{b0.size.z:F2}) " +
                  $"scale=({sx:F2},{sy:F2},{sz:F2}) → achieved world=({b1.size.x:F2},{b1.size.y:F2},{b1.size.z:F2}) " +
                  $"target=({tgtX:F1},{tgtH:F1},{tgtZ:F1})");
        if (Mathf.Abs(b1.size.x - tgtX) > 0.1f * tgtX ||
            Mathf.Abs(b1.size.y - tgtH) > 0.1f * tgtH ||
            Mathf.Abs(b1.size.z - tgtZ) > 0.1f * tgtZ)
            Debug.LogWarning($"[KitRoom] ⚠⚠ pillar '{name}' achieved world=({b1.size.x:F2},{b1.size.y:F2},{b1.size.z:F2}) " +
                             $"is OFF target ({tgtX:F1},{tgtH:F1},{tgtZ:F1}) by >10% — check prefab bounds/pivot.");
        Vector3 pivotOffset = inst.transform.position - b1.center;
        // r6: ground on min.y alone — the pivotOffset.y term double-counts the bounds centre for
        // base-pivot meshes (measured: pillars sat at pos.y=-1.98, bounds -1.99..2.01, half underground).
        inst.transform.position = new Vector3(center.x + pivotOffset.x, inst.transform.position.y - b1.min.y, center.z + pivotOffset.z);
        FixMaterials(inst, false, ref matFix, force: true, overrideMat: MassMat());   // r5 forced stone; r8 forces the dark-masonry albedo (pillars were invisible-wall residuals)
        return inst;
    }

    // idempotency belt-and-suspenders (r3): the room root is destroyed+rebuilt each run, but sweep any
    // orphaned KitRoom_* lights that may survive outside the new root so a re-run never doubles the rig.
    static void PurgeStrayKitLights(GameObject keepRoot)
    {
        foreach (var L in UnityEngine.Object.FindObjectsByType<Light>(FindObjectsSortMode.None))
        {
            if (L == null) continue;
            var go = L.gameObject;
            if (keepRoot != null && go.transform.IsChildOf(keepRoot.transform)) continue;
            if (go.name.StartsWith("KitRoom_")) UnityEngine.Object.DestroyImmediate(go);
        }
    }

    // ════════════════════════════════════════════════════════════════════════════════════════════════
    //  MATERIAL-DEFENSIVE RULE (build_atelier_crypt.cs L267-293)
    // ════════════════════════════════════════════════════════════════════════════════════════════════
    // DungeonMap props ship with NULL / error-shader material references → Unity renders them MAGENTA. After
    // instantiating ANY kit piece, walk its renderers; every null / missing / error-shader material slot is
    // reassigned a working PolygonGeneric material (stone-ish for structure/tombs, wood-ish for furniture).
    // Lookups are cached; each distinct bad material is logged ONCE.
    // r5: `force` also overrides VALID-but-default-grey slots — the pillar prefabs ship a legit (non-error)
    // grey material FixMaterials would otherwise skip, leaving them default grey (r4b). Force=true assigns the
    // stone/brick material to every slot regardless, so pillars read stone like the walls.
    // r8: `overrideMat` lets a caller name the exact replacement (e.g. KitMass_DarkMasonry for walls/pillars,
    // KitProp_Stone for the tomb) instead of the woodish/stone default — the packet's "FixMaterials force path".
    static void FixMaterials(GameObject inst, bool woodish, ref int matFix, bool force = false, Material overrideMat = null)
    {
        ResolveFallbackMaterials();
        Material repl = overrideMat ?? (woodish ? _woodMat : _stoneMat);
        if (repl == null) return;
        foreach (var rend in inst.GetComponentsInChildren<Renderer>(true))
        {
            if (rend == null) continue;
            var mats = rend.sharedMaterials; bool changed = false;
            for (int i = 0; i < mats.Length; i++)
            {
                var m = mats[i];
                bool bad = m == null || m.shader == null || m.shader.name == "Hidden/InternalErrorShader";
                if (!bad && !force) continue;          // default: only touch null/error-shader slots
                if (!bad && force && m == repl) continue;   // force: already the replacement — leave it
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

    // r8 ALBEDO HELPERS (#83/#84). Clone a base material (preserving its shader + any albedo texture) and set
    // its color so the color MULTIPLIES over the texture. Runtime-only instances (never written to the
    // AssetDatabase); each shared instance is created ONCE per build (lazily) and reset at the top of BuildRoom.
    static Material Recolor(Material src, string nm, Color col)
    {
        Material m = (src != null && src.shader != null && src.shader.name != "Hidden/InternalErrorShader")
            ? new Material(src) : new Material(Shader.Find("Standard"));
        m.name = nm; m.color = col;
        return m;
    }
    // KitMass_DarkMasonry — walls / parapets / buttresses / plinths / pillars / rubble (item 2, based on the brick/stone base).
    static Material MassMat()
    { if (_massDarkMat == null) { ResolveFallbackMaterials(); _massDarkMat = Recolor(_stoneMat, "KitMass_DarkMasonry", MASS_DARK); } return _massDarkMat; }
    // KitProp_Stone — focal-prop stone, applied to the tomb ONLY (item 2 conditional; verified vs r7 evidence below).
    static Material PropStoneMat()
    { if (_propStoneMat == null) { ResolveFallbackMaterials(); _propStoneMat = Recolor(_stoneMat, "KitProp_Stone", PROP_STONE); } return _propStoneMat; }
    // KitProp_DarkWood — wooden fallback boxes (item 5, based on the wood base).
    static Material PropWoodMat()
    { if (_propWoodMat == null) { ResolveFallbackMaterials(); _propWoodMat = Recolor(_woodMat, "KitProp_DarkWood", PROP_DWOOD); } return _propWoodMat; }

    // ════════════════════════════════════════════════════════════════════════════════════════════════
    //  BRAZIER + FALLBACK PRIMITIVES
    // ════════════════════════════════════════════════════════════════════════════════════════════════
    // r3: shape the primitive-fallback brazier like the painted crypt's — a DARK metal BOWL on a NARROWER
    // stem, topped by a flat ember-orange emissive DISC (r1/r2 rendered a glowing mushroom SPHERE that read
    // as a floating orb). The emissive disc uses a fresh Standard material instance with _EmissionColor; the
    // FireAnchor empty stays the runtime fire-VFX / glow-quad attach point.
    static GameObject BuildBrazier(string pid, GameObject parent, Vector3 center)
    {
        var g = new GameObject($"{pid}_Brazier"); g.transform.SetParent(parent.transform, true); g.transform.position = center;
        // narrow stem (dark metal cylinder): height ≈1.5u, top at ~1.5
        var stem = GameObject.CreatePrimitive(PrimitiveType.Cylinder); stem.name = "Stem";
        UnityEngine.Object.DestroyImmediate(stem.GetComponent<Collider>());
        stem.transform.SetParent(g.transform, false); stem.transform.localPosition = new Vector3(0f, 0.75f, 0f);
        stem.transform.localScale = new Vector3(0.32f, 0.75f, 0.32f);
        stem.GetComponent<Renderer>().sharedMaterial = MakeStd(new Color(0.16f, 0.15f, 0.14f), 0.10f);
        // dark bowl (wide, shallow cylinder) seated on the stem top
        var bowl = GameObject.CreatePrimitive(PrimitiveType.Cylinder); bowl.name = "Bowl";
        UnityEngine.Object.DestroyImmediate(bowl.GetComponent<Collider>());
        bowl.transform.SetParent(g.transform, false); bowl.transform.localPosition = new Vector3(0f, 1.66f, 0f);
        bowl.transform.localScale = new Vector3(0.95f, 0.22f, 0.95f);
        bowl.GetComponent<Renderer>().sharedMaterial = MakeStd(new Color(0.20f, 0.17f, 0.15f), 0.10f);
        // ember-orange emissive DISC (thin cylinder) sitting inside the bowl rim
        var ember = GameObject.CreatePrimitive(PrimitiveType.Cylinder); ember.name = "Embers";
        UnityEngine.Object.DestroyImmediate(ember.GetComponent<Collider>());
        ember.transform.SetParent(g.transform, false); ember.transform.localPosition = new Vector3(0f, 1.90f, 0f);
        ember.transform.localScale = new Vector3(0.72f, 0.05f, 0.72f);
        var em = MakeStd(new Color(0.85f, 0.42f, 0.16f), 0.20f);
        em.EnableKeyword("_EMISSION"); em.SetColor("_EmissionColor", new Color(1.0f, 0.55f, 0.22f) * 2.0f);
        em.globalIlluminationFlags = MaterialGlobalIlluminationFlags.RealtimeEmissive;
        ember.GetComponent<Renderer>().sharedMaterial = em;
        // empty anchor child at the flame (runtime fire-VFX / glow-quad attach point)
        var anchor = new GameObject("FireAnchor"); anchor.transform.SetParent(g.transform, false); anchor.transform.localPosition = new Vector3(0f, 2.05f, 0f);
        return g;
    }

    // r7: place a single-cell impassable MASS — a stone primitive cube seated base-on-floor at a cell center,
    // with explicit world dims (buttress ~1.7×2.2×1.7, plinth ~1.6×0.9×1.6). Mirrors the FallbackBox
    // primitive-fallback idiom (cube + stone material + collider stripped); a primitive cube's pivot is
    // centred so grounding the base is simply y = height*0.5 (min.y → 0), matching the r6 grounding intent.
    static void PlaceMassBox(string name, GameObject parent, Vector3 center, float sizeXZ, float height)
    {
        var b = GameObject.CreatePrimitive(PrimitiveType.Cube); b.name = name;
        UnityEngine.Object.DestroyImmediate(b.GetComponent<Collider>());
        b.transform.SetParent(parent.transform, true);
        b.transform.localScale = new Vector3(sizeXZ, height, sizeXZ);
        b.transform.position = new Vector3(center.x, height * 0.5f, center.z);
        b.GetComponent<Renderer>().sharedMaterial = MassMat();   // r8: dark-masonry albedo (buttresses / plinths / brazier bases)
    }

    // r8: `overrideMat` (items 4/5) names the exact material for the box — dark-masonry for rubble, dark-wood
    // for wooden props — instead of the woodish/stone default. null keeps the prior material-defensive default.
    static bool FallbackBox(string pid, GameObject parent, Footprint fp, float height, bool woodish, ref int matFix, Material overrideMat = null)
    {
        var b = GameObject.CreatePrimitive(PrimitiveType.Cube); b.name = $"{pid}_fallback";
        UnityEngine.Object.DestroyImmediate(b.GetComponent<Collider>());
        b.transform.SetParent(parent.transform, true);
        b.transform.position = new Vector3(fp.center.x, height * 0.5f, fp.center.z);
        b.transform.localScale = new Vector3(Mathf.Max(1.2f, fp.spanX * 0.9f), height, Mathf.Max(1.2f, fp.spanZ * 0.9f));
        ResolveFallbackMaterials();
        b.GetComponent<Renderer>().sharedMaterial = overrideMat ?? (woodish ? _woodMat : _stoneMat);
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
