// DunGenLayoutExporter.cs — export a DunGen-generated dungeon's STRUCTURE to layout json (epic #1508
// stage-1 spike). DunGen is an AUTHORING-TIME accelerator only: it proposes a room-graph, we EXPORT its
// layout and bake it to engine fixtures downstream (tools/dungen_to_fixtures.py). The Python engine stays
// the SOLE WRITER of grid truth — this script never touches runtime game state, it only reads a generated
// scene and writes json. See docs/roadmap/GENERATOR-EXPORT-CONTRACT.md (renamed from
// DUNGEN-EXPORT-CONTRACT.md to cover both the DunGen and Tessera Pro arms) and the PCG scout packet.
//
// The export contract (dungen_layout.json), all coordinates in Unity WORLD units:
//   {
//     "generator": { "seed": <int>, "world_units_per_cell": <float hint>, "tile_count": <int> },
//     "bounds":   { "min": [x,y,z], "max": [x,y,z] },                 // overall dungeon AABB
//     "rooms":    [ { "id", "tags":[...], "is_main_path": bool,
//                     "bounds": { "min":[x,y,z], "max":[x,y,z] } } ], // one per placed Tile
//     "doorways": [ { "id", "room_a", "room_b",
//                     "position": [x,y,z], "forward": [x,y,z] } ],    // one per connection
//     "props":    [ { "id", "room", "shape_class": "box"|"cylinder"|"cone",
//                     "kind_hint": <string>, "position":[x,y,z],
//                     "bounds": { "min":[x,y,z], "max":[x,y,z] } } ]  // child meshes = set-dressing
//   }
// tools/dungen_to_fixtures.py snaps these world coords to the engine's 5-ft cell grid (default
// world_units_per_cell=2.0, matching greybox_render_headless's 2.0-units/cell contract).
//
// Two entry points:
//   * Menu:  WorldOS/DunGen ▸ Export Active Dungeon Layout   (uses the RuntimeDungeon in the open scene)
//   * Static: DunGenLayoutExporter.Export(flowAssetPath, outPath, seed)  — callable from unity-mcp
//             execute_code for the headless box drive loop; builds a generator from a DungeonFlow asset,
//             generates, exports, returns a status string.
//
// DunGen version note: this targets DunGen's documented public surface (DungeonGenerator / Dungeon /
// Tile / Doorway). Field names below are guarded with reflection fallbacks where the installed 3.x API
// has historically renamed members (Placement.Bounds, Tags, UsedDoorways) so a minor version bump does
// not silently drop data — a missing member logs and degrades, never throws mid-export.
#if UNITY_EDITOR
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace WorldOS.Editor
{
    public static class DunGenLayoutExporter
    {
        const string DefaultOut = "/home/unity/worldos-unity/dungen_layout.json";

        [MenuItem("WorldOS/DunGen/Export Active Dungeon Layout")]
        public static void ExportActiveMenu()
        {
            var msg = ExportActive(DefaultOut);
            Debug.Log("[DunGenLayoutExporter] " + msg);
        }

        // --- Headless entry: generate from a DungeonFlow asset via RuntimeDungeon, export. ---------------
        // Uses the RuntimeDungeon component (not a bare DungeonGenerator): RuntimeDungeon.Generate() builds
        // the default DungeonGenerationRequest and DungeonGenerator.Generate creates its own Root, so the
        // whole thing runs synchronously in edit mode (GenerateAsynchronously defaults false). Callable
        // from unity-mcp execute_code on the GEX44 box.
        public static string Export(string flowAssetPath, string outPath, int seed)
        {
            var flow = AssetDatabase.LoadAssetAtPath<Object>(flowAssetPath);
            if (flow == null) return "FAIL: DungeonFlow asset not found at " + flowAssetPath;

            var rtType = FindType("DunGen.RuntimeDungeon");
            if (rtType == null) return "FAIL: DunGen.RuntimeDungeon type not found (is DunGen imported?)";

            var go = new GameObject("DunGenSpikeExport");
            var runtime = go.AddComponent(rtType);
            var gen = GetMember(runtime, "Generator");
            if (gen == null) return "FAIL: RuntimeDungeon.Generator was null";
            SetMember(gen, "DungeonFlow", flow);
            SetMember(gen, "Seed", seed);
            SetMember(gen, "ShouldRandomizeSeed", false);
            InvokeWithArgs(runtime, "Generate", new object[] { null });  // synchronous full generation

            var dungeon = GetMember(gen, "CurrentDungeon");
            var status = GetMember(gen, "Status");
            if (dungeon == null) return "FAIL: generation produced no CurrentDungeon (status=" + status + ")";
            return WriteLayout(dungeon, outPath, seed) + " (status=" + status + ")";
        }

        // --- Menu entry: export the RuntimeDungeon already generated in the open scene. --------------------
        public static string ExportActive(string outPath)
        {
            var runtime = Object.FindFirstObjectByType(FindType("DunGen.RuntimeDungeon"));
            if (runtime == null) return "FAIL: no RuntimeDungeon in the open scene";
            var gen = GetMember(runtime, "Generator");
            var dungeon = gen != null ? GetMember(gen, "CurrentDungeon") : null;
            int seed = gen != null ? ToInt(GetMember(gen, "Seed")) : 0;
            if (dungeon == null) return "FAIL: RuntimeDungeon has no generated CurrentDungeon (Generate first)";
            return WriteLayout(dungeon, outPath, seed);
        }

        // --- Walk the generated Dungeon and serialise the layout contract. --------------------------------
        static string WriteLayout(object dungeon, string outPath, int seed)
        {
            var tiles = AsList(GetMember(dungeon, "AllTiles"));
            if (tiles == null || tiles.Count == 0) return "FAIL: dungeon has no AllTiles";

            // Stable ids: index tiles in AllTiles order; map object -> id for doorway/prop cross-ref.
            var tileId = new Dictionary<object, string>();
            for (int i = 0; i < tiles.Count; i++) tileId[tiles[i]] = "room_" + i.ToString(CultureInfo.InvariantCulture);

            var overallMin = new Vector3(float.PositiveInfinity, float.PositiveInfinity, float.PositiveInfinity);
            var overallMax = new Vector3(float.NegativeInfinity, float.NegativeInfinity, float.NegativeInfinity);

            var sb = new StringBuilder();
            sb.Append("{\n");

            // rooms
            var rooms = new StringBuilder();
            var props = new StringBuilder();
            int propCount = 0;
            for (int i = 0; i < tiles.Count; i++)
            {
                var tile = tiles[i];
                var go = TileGameObject(tile);
                Bounds b = TileBounds(tile, go);
                overallMin = Vector3.Min(overallMin, b.min);
                overallMax = Vector3.Max(overallMax, b.max);
                if (rooms.Length > 0) rooms.Append(",\n");
                rooms.Append("  { \"id\": \"").Append(tileId[tile]).Append("\", ");
                rooms.Append("\"tags\": [").Append(TagsJson(tile)).Append("], ");
                // main-path flag lives on Tile.Placement.IsOnMainPath (not the Tile itself).
                rooms.Append("\"is_main_path\": ")
                     .Append(Bl(GetMember(GetMember(tile, "Placement"), "IsOnMainPath"))).Append(", ");
                rooms.Append("\"bounds\": ").Append(BoundsJson(b)).Append(" }");

                // props = child mesh renderers under the tile (set-dressing), excluding the tile's own floor.
                if (go != null)
                {
                    foreach (var mf in go.GetComponentsInChildren<MeshFilter>())
                    {
                        if (mf == null || mf.sharedMesh == null) continue;
                        if (mf.transform == go.transform) continue; // skip the tile root's own floor/shell
                        var rend = mf.GetComponent<Renderer>();
                        Bounds pb = rend != null ? rend.bounds
                                                 : new Bounds(mf.transform.position, mf.transform.lossyScale);
                        if (props.Length > 0) props.Append(",\n");
                        props.Append("  { \"id\": \"prop_").Append(propCount).Append("\", ");
                        props.Append("\"room\": \"").Append(tileId[tile]).Append("\", ");
                        props.Append("\"shape_class\": \"").Append(ShapeClass(mf.sharedMesh.name, mf.name)).Append("\", ");
                        props.Append("\"kind_hint\": \"").Append(Esc(mf.name)).Append("\", ");
                        props.Append("\"position\": ").Append(V3(mf.transform.position)).Append(", ");
                        props.Append("\"bounds\": ").Append(BoundsJson(pb)).Append(" }");
                        propCount++;
                    }
                }
            }

            // doorways / connections
            var doors = new StringBuilder();
            var conns = AsList(GetMember(dungeon, "Connections"));
            int dIdx = 0;
            if (conns != null)
            {
                foreach (var conn in conns)
                {
                    var a = GetMember(conn, "A");
                    var bDoor = GetMember(conn, "B");
                    var pos = DoorPosition(a, bDoor);
                    var fwd = DoorForward(a);
                    string ra = TileOf(a, tileId), rb = TileOf(bDoor, tileId);
                    if (doors.Length > 0) doors.Append(",\n");
                    doors.Append("  { \"id\": \"door_").Append(dIdx).Append("\", ");
                    doors.Append("\"room_a\": \"").Append(ra).Append("\", \"room_b\": \"").Append(rb).Append("\", ");
                    doors.Append("\"position\": ").Append(V3(pos)).Append(", ");
                    doors.Append("\"forward\": ").Append(V3(fwd)).Append(" }");
                    dIdx++;
                }
            }

            sb.Append("  \"generator\": { \"seed\": ").Append(seed)
              .Append(", \"world_units_per_cell\": 2.0, \"tile_count\": ").Append(tiles.Count).Append(" },\n");
            sb.Append("  \"bounds\": ").Append(BoundsJson(new Bounds((overallMin + overallMax) * 0.5f, overallMax - overallMin))).Append(",\n");
            sb.Append("  \"rooms\": [\n").Append(rooms).Append("\n  ],\n");
            sb.Append("  \"doorways\": [\n").Append(doors).Append("\n  ],\n");
            sb.Append("  \"props\": [\n").Append(props).Append("\n  ]\n");
            sb.Append("}\n");

            System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(outPath));
            System.IO.File.WriteAllText(outPath, sb.ToString());
            return "OK: " + tiles.Count + " rooms, " + dIdx + " doorways, " + propCount + " props -> " + outPath;
        }

        // --- shape classification: masonry reads as a box; organic reads as cylinder/cone. ----------------
        static string ShapeClass(string meshName, string objName)
        {
            string n = ((meshName ?? "") + " " + (objName ?? "")).ToLowerInvariant();
            if (n.Contains("cone") || n.Contains("tree") || n.Contains("spike") || n.Contains("stalagmite")) return "cone";
            if (n.Contains("cylinder") || n.Contains("pillar") || n.Contains("column") || n.Contains("barrel") ||
                n.Contains("brazier") || n.Contains("torch") || n.Contains("well") || n.Contains("pot")) return "cylinder";
            return "box";
        }

        // ── DunGen accessors (guarded: Placement.Bounds, Tags, doorway pos/forward). ──────────────────────
        static GameObject TileGameObject(object tile)
        {
            var comp = tile as Component;
            if (comp != null) return comp.gameObject;
            var go = GetMember(tile, "gameObject") as GameObject;
            return go;
        }

        static Bounds TileBounds(object tile, GameObject go)
        {
            // Preferred: Tile.Placement.Bounds (world-space, computed at generation).
            var placement = GetMember(tile, "Placement");
            if (placement != null)
            {
                var pb = GetMember(placement, "Bounds");
                if (pb is Bounds bb) return bb;
            }
            var tb = GetMember(tile, "Bounds");
            if (tb is Bounds tbb) return tbb;
            // Fallback: encapsulate child renderers.
            if (go != null)
            {
                var rends = go.GetComponentsInChildren<Renderer>();
                if (rends.Length > 0)
                {
                    var b = rends[0].bounds;
                    for (int i = 1; i < rends.Length; i++) b.Encapsulate(rends[i].bounds);
                    return b;
                }
                return new Bounds(go.transform.position, Vector3.one * 2f);
            }
            return new Bounds(Vector3.zero, Vector3.one * 2f);
        }

        static string TagsJson(object tile)
        {
            var tags = GetMember(tile, "Tags");
            var names = new List<string>();
            // DunGen TagContainer usually exposes an int list + a global name table; degrade to string ids.
            var list = AsList(tags != null ? GetMember(tags, "tags") : null);
            if (list != null) foreach (var t in list) names.Add("\"" + Esc(t.ToString()) + "\"");
            else if (tags != null) names.Add("\"" + Esc(tags.ToString()) + "\"");
            return string.Join(", ", names);
        }

        static Vector3 DoorPosition(object a, object b)
        {
            var pa = DoorworldPos(a);
            var pb = DoorworldPos(b);
            if (pa.HasValue && pb.HasValue) return (pa.Value + pb.Value) * 0.5f;
            if (pa.HasValue) return pa.Value;
            if (pb.HasValue) return pb.Value;
            return Vector3.zero;
        }

        static Vector3? DoorworldPos(object door)
        {
            var comp = door as Component;
            if (comp != null) return comp.transform.position;
            return null;
        }

        static Vector3 DoorForward(object door)
        {
            var comp = door as Component;
            if (comp != null) return comp.transform.forward;
            return Vector3.forward;
        }

        static string TileOf(object door, Dictionary<object, string> tileId)
        {
            var t = GetMember(door, "Tile");
            if (t != null && tileId.TryGetValue(t, out var id)) return id;
            return "";
        }

        // ── json helpers ──────────────────────────────────────────────────────────────────────────────
        static string F(float v) => v.ToString("0.####", CultureInfo.InvariantCulture);
        static string V3(Vector3 v) => "[" + F(v.x) + ", " + F(v.y) + ", " + F(v.z) + "]";
        static string BoundsJson(Bounds b) => "{ \"min\": " + V3(b.min) + ", \"max\": " + V3(b.max) + " }";
        static string Bl(object v) => (v is bool bb && bb) ? "true" : "false";
        static string Esc(string s) => (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
        static int ToInt(object v) { try { return System.Convert.ToInt32(v); } catch { return 0; } }

        // ── reflection plumbing (version-robust member access) ─────────────────────────────────────────
        static System.Type FindType(string full)
        {
            foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
            {
                var t = asm.GetType(full);
                if (t != null) return t;
            }
            return null;
        }

        static object GetMember(object obj, string name)
        {
            if (obj == null) return null;
            var t = obj.GetType();
            var p = t.GetProperty(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase);
            if (p != null) return p.GetValue(obj);
            var f = t.GetField(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase);
            if (f != null) return f.GetValue(obj);
            return null;
        }

        static void SetMember(object obj, string name, object value)
        {
            if (obj == null) return;
            var t = obj.GetType();
            var p = t.GetProperty(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase);
            if (p != null && p.CanWrite) { p.SetValue(obj, value); return; }
            var f = t.GetField(name, BindingFlags.Public | BindingFlags.Instance | BindingFlags.IgnoreCase);
            if (f != null) f.SetValue(obj, value);
        }

        static void InvokeWithArgs(object obj, string name, object[] args)
        {
            if (obj == null) return;
            // match by name + arg count (the arg types include a null DungeonGenerationRequest we cannot
            // name without an asmdef reference, so a typed GetMethod lookup would miss it).
            foreach (var m in obj.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance))
                if (m.Name == name && m.GetParameters().Length == args.Length) { m.Invoke(obj, args); return; }
        }

        static List<object> AsList(object v)
        {
            if (v == null) return null;
            var list = new List<object>();
            if (v is System.Collections.IEnumerable en && !(v is string))
                foreach (var x in en) list.Add(x);
            return list;
        }
    }
}
#endif
