// TesseraLayoutExporter.cs — export a Tessera Pro WFC generation's STRUCTURE to layout json (epic #1508
// stage-2, the Tessera arm of the generator comparison). Tessera is an AUTHORING-TIME tile-WFC generator,
// same fence as DunGen: it proposes a tile layout, we EXPORT that layout and bake it to engine fixtures
// downstream (tools/dungen_to_fixtures.py, UNCHANGED for the core rooms/props/bounds path — see the
// additive-schema note below). The Python engine stays the SOLE WRITER of grid truth — this script never
// touches runtime game state, it only reads a generated scene and writes json. See
// docs/roadmap/GENERATOR-EXPORT-CONTRACT.md (renamed from DUNGEN-EXPORT-CONTRACT.md).
//
// ⚠ API VERIFICATION STATUS — READ BEFORE TRUSTING THIS AGAINST THE REAL PACKAGE ⚠
// This was written REPO-SIDE with no box access and no vendored Tessera source in this repo (grep found
// nothing under extensions/ or elsewhere). Every member name below was checked against the PUBLIC Tessera
// Pro docs (https://www.boristhebrave.com/docs/tessera/6/api/, "Tessera" namespace, version 6 — the
// current documented major as of 2026-07-11) — NOT against the actual installed package. Two categories:
//   VERIFIED (member exists + signature matches the docs page, high confidence):
//     TesseraGenerator: bounds (Bounds), cellSize (Vector3), Generate(TesseraGenerateOptions=null) ->
//       TesseraCompletion, GetGrid() -> IGrid.
//     TesseraGenerateOptions: seed (int?), onCreate/onComplete/progress/multithreaded/cancellationToken
//       (only `seed` is used here).
//     TesseraCompletion: success (bool), tileInstances (IList<TesseraTileInstance>), contradictionLocation
//       (Vector3Int?), contradictionReason (string).
//     TesseraTileInstance: Cell (Vector3Int), Cells (Vector3Int[]), Position (Vector3, WORLD space),
//       Rotation (Quaternion, WORLD space), CellRotation, Tile (TesseraTileBase, a UnityEngine.Object).
//     Default instantiation: docs confirm "TesseraGenerator will instantiate copies of all the tiles as
//       child objects" of the generator — i.e. parented under the generator's own transform.
//   FLAGGED / UNVERIFIED (inferred, could not confirm from docs — validate on the box before trusting):
//     (1) The exact default `parent` Transform and whether the spawned child's world position/rotation
//         is set EXACTLY equal to TesseraTileInstance.Position/.Rotation. We rely on this to associate each
//         TesseraTileInstance with its instantiated GameObject (nearest-position match against the
//         generator's direct children) so we can walk child MeshFilters for props (mirrors DunGen's prop
//         scan). If the real default instantiate offsets/parents differently, PROPS WILL SILENTLY COME UP
//         EMPTY for that tile — rooms/tiles/bounds are unaffected since those come straight off
//         TesseraCompletion, not off the matched GameObject.
//     (2) Anisotropic / non-square grids (hex, triangle, deformed Sylves grids): this exporter assumes a
//         plain square/rectangular cell grid (cellSize.x == cellSize.z), matching DunGen's own scale-
//         mapping assumption. A hex/triangle Tessera generator is OUT OF SCOPE here — flag to the box
//         session if the comparison uses one.
//     (3) `PathConstraint` "on critical path" membership is NOT exposed on TesseraCompletion/TileInstance
//         in anything documented; `is_main_path` is always emitted `false` for the Tessera arm. This is a
//         genuine capability gap vs DunGen (which gets IsOnMainPath from the room graph) — call it out in
//         the comparison rubric's "constraint expressiveness" row, not something to fake here.
//     (4) Tessera has no native doorway/connection object (WFC connects tiles by face-matching, not an
//         explicit Doorway type). `doorways` is emitted as an empty array — tools/dungen_to_fixtures.py
//         already tolerates a missing/empty doorways list (verified: `layout.get("doorways", [])`), so no
//         converter change was needed for that. A tagged "door" child prop (if the tile prefab has one)
//         still round-trips through the generic props path like any other kind_hint. This is the
//         "door/connection handling" gap the comparison rubric should score.
//
// ── Additive layout-json schema (extends, does not fork, the DunGen contract) ─────────────────────────
// Same top-level shape as dungen_layout.json (generator/bounds/rooms/doorways/props), so
// tools/dungen_to_fixtures.py's core conversion is UNCHANGED for this exporter's output. One tile
// instance = one `rooms[]` entry (keeps 1:1 parity with DunGen's one-tile=one-room mapping). Two
// ADDITIVE fields where Tessera's tile-WFC model doesn't map 1:1 onto DunGen's continuous room-graph:
//   rooms[].cell_positions : [[x,y,z], ...] — WORLD-space center of EVERY grid cell this tile instance
//     occupies (length 1 for a normal single-cell tile, >1 for a Tessera "big tile"). Needed because a
//     multi-cell tile's true footprint can be non-rectangular (e.g. an L-shape); rasterizing its bounds
//     AABB would over-include cells that aren't actually part of the tile. When present, the converter
//     carves the EXACT cell set from this list instead of the AABB. `rooms[].bounds` is still ALWAYS also
//     emitted (the AABB across cell_positions) for full backward-compat with any bounds-only consumer.
//   rooms[].tile_name, rooms[].cell_rotation — purely descriptive (the source TesseraTile's name / the
//     placed rotation), ignored by the converter, useful for the comparison rubric / box-session debugging.
//   generator.kind : "tessera_wfc" — informational tag distinguishing this arm from DunGen's implicit
//     default, ignored by the converter.
//
// Two entry points (mirrors DunGenLayoutExporter.cs):
//   * Menu:  WorldOS/Tessera ▸ Export Active Tessera Layout   (first TesseraGenerator in the open scene)
//   * Static: TesseraLayoutExporter.Export(generatorObjectName, outPath, seed) — callable from unity-mcp
//             execute_code for the headless box drive loop. `generatorObjectName` may be empty/null to
//             fall back to the first TesseraGenerator found (matters for a comparison scene that hosts
//             BOTH a DunGen RuntimeDungeon and a Tessera generator side by side — pass the name to
//             disambiguate). Always calls Generate() itself (synchronous, deterministic via `seed`)
//             rather than assuming a cached "already generated" state, since TesseraGenerator does not
//             document an accessible "last completion" property — see FLAGGED (1).
#if UNITY_EDITOR
using System.Collections.Generic;
using System.Globalization;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace WorldOS.Editor
{
    public static class TesseraLayoutExporter
    {
        const string DefaultOut = "/home/unity/worldos-unity/tessera_layout.json";
        // Prop-association match tolerance, as a FRACTION of the generator's own cell pitch (not a fixed
        // world-unit constant — a tiny dungeon and a huge one need different absolute tolerances). See
        // FLAGGED (1): if the real default instantiate doesn't set position to exact equality, this is
        // the slack allowed before we give up and skip props for that tile rather than risk a wrong match.
        const float PositionMatchCellFraction = 0.25f;

        [MenuItem("WorldOS/Tessera/Export Active Tessera Layout")]
        public static void ExportActiveMenu()
        {
            var msg = Export(null, DefaultOut, 0);
            Debug.Log("[TesseraLayoutExporter] " + msg);
        }

        // --- Static entry: find (or load-by-name) a TesseraGenerator, generate deterministically, export.
        public static string Export(string generatorObjectName, string outPath, int seed)
        {
            var genType = FindType("Tessera.TesseraGenerator");
            if (genType == null) return "FAIL: Tessera.TesseraGenerator type not found (is Tessera Pro imported?)";

            Component generator = FindGenerator(genType, generatorObjectName);
            if (generator == null)
            {
                return "FAIL: no TesseraGenerator found" +
                       (string.IsNullOrEmpty(generatorObjectName) ? " in the open scene"
                                                                   : " named '" + generatorObjectName + "'");
            }

            object options = BuildOptions(seed);
            object completion = InvokeWithArgsReturn(generator, "Generate", new object[] { options });
            if (completion == null) return "FAIL: Generate() returned null (Tessera API mismatch? see FLAGGED notes)";

            bool success = GetMember(completion, "success") is bool sb && sb;
            var tileInstances = AsList(GetMember(completion, "tileInstances"));
            if (tileInstances == null) tileInstances = new List<object>();

            string status = success ? "OK"
                : "WFC-FAILED: " + (GetMember(completion, "contradictionReason") ?? "unknown reason");
            return WriteLayout(generator, tileInstances, outPath, seed) + " (status=" + status + ")";
        }

        static Component FindGenerator(System.Type genType, string name)
        {
            if (!string.IsNullOrEmpty(name))
            {
                var go = GameObject.Find(name);
                if (go != null) return go.GetComponent(genType);
                return null;
            }
            return Object.FindFirstObjectByType(genType) as Component;
        }

        static object BuildOptions(int seed)
        {
            var optType = FindType("Tessera.TesseraGenerateOptions");
            if (optType == null) return null; // Generate(null) still runs with library defaults.
            object opts = System.Activator.CreateInstance(optType);
            // seed is `int?` (Nullable<int>) per the docs — box a plain int, reflection SetValue handles
            // the implicit conversion to Nullable<int> for a field/property of that type.
            SetMember(opts, "seed", seed);
            return opts;
        }

        // --- Walk the TesseraCompletion and serialise the (additively extended) layout contract. --------
        static string WriteLayout(object generator, List<object> tileInstances, string outPath, int seed)
        {
            var genComponent = generator as Component;
            Vector3 cellSize = GetMember(generator, "cellSize") is Vector3 cs ? cs : new Vector3(2f, 2f, 2f);
            Bounds overall = GetMember(generator, "bounds") is Bounds ob ? ob
                : new Bounds(Vector3.zero, Vector3.one * 2f);

            // Candidate spawned children for best-effort prop scanning — see FLAGGED (1).
            var candidates = new List<Transform>();
            if (genComponent != null)
            {
                var t = genComponent.transform;
                for (int i = 0; i < t.childCount; i++) candidates.Add(t.GetChild(i));
            }
            var claimed = new HashSet<Transform>();
            float matchMaxDist = Mathf.Min(Mathf.Abs(cellSize.x), Mathf.Abs(cellSize.z)) * PositionMatchCellFraction;

            var rooms = new StringBuilder();
            var props = new StringBuilder();
            int propCount = 0;
            var overallMin = overall.min;
            var overallMax = overall.max;

            for (int i = 0; i < tileInstances.Count; i++)
            {
                var inst = tileInstances[i];
                string id = "room_" + i.ToString(CultureInfo.InvariantCulture);

                Vector3 position = GetMember(inst, "Position") is Vector3 p ? p : Vector3.zero;
                Vector3Int cell = GetMember(inst, "Cell") is Vector3Int c ? c : Vector3Int.zero;
                Vector3Int[] cells = GetMember(inst, "Cells") is Vector3Int[] arr && arr.Length > 0
                    ? arr : new[] { cell };

                // cell_positions: world-space center per footprint cell (position offset by the grid
                // delta * cellSize, unrotated approximation — see FLAGGED (1) for the rotated-big-tile
                // caveat, same axis-aligned-approximation fidelity DunGen itself uses for room bounds).
                var cellPositions = new List<Vector3>(cells.Length);
                var roomMin = new Vector3(float.PositiveInfinity, float.PositiveInfinity, float.PositiveInfinity);
                var roomMax = new Vector3(float.NegativeInfinity, float.NegativeInfinity, float.NegativeInfinity);
                foreach (var cr in cells)
                {
                    var delta = cr - cell;
                    var wp = position + Vector3.Scale(new Vector3(delta.x, delta.y, delta.z), cellSize);
                    cellPositions.Add(wp);
                    var half = cellSize * 0.5f;
                    roomMin = Vector3.Min(roomMin, wp - half);
                    roomMax = Vector3.Max(roomMax, wp + half);
                }
                overallMin = Vector3.Min(overallMin, roomMin);
                overallMax = Vector3.Max(overallMax, roomMax);

                string tileName = "";
                var tileRef = GetMember(inst, "Tile") as Object;
                if (tileRef != null) tileName = tileRef.name;
                string cellRotation = SafeStr(GetMember(inst, "CellRotation"));

                if (rooms.Length > 0) rooms.Append(",\n");
                rooms.Append("  { \"id\": \"").Append(id).Append("\", ");
                rooms.Append("\"tags\": [], \"is_main_path\": false, ");   // see FLAGGED (3)
                rooms.Append("\"tile_name\": \"").Append(Esc(tileName)).Append("\", ");
                rooms.Append("\"cell_rotation\": \"").Append(Esc(cellRotation)).Append("\", ");
                rooms.Append("\"bounds\": ").Append(BoundsMinMaxJson(roomMin, roomMax)).Append(", ");
                rooms.Append("\"cell_positions\": [").Append(V3List(cellPositions)).Append("] }");

                // Best-effort prop scan: nearest unclaimed spawned child by world position (FLAGGED (1)).
                Transform matched = NearestUnclaimed(candidates, claimed, position, matchMaxDist);
                if (matched != null)
                {
                    claimed.Add(matched);
                    foreach (var mf in matched.GetComponentsInChildren<MeshFilter>())
                    {
                        if (mf == null || mf.sharedMesh == null) continue;
                        if (mf.transform == matched) continue; // skip the tile root's own floor/shell mesh
                        var rend = mf.GetComponent<Renderer>();
                        Bounds pb = rend != null ? rend.bounds
                                                 : new Bounds(mf.transform.position, mf.transform.lossyScale);
                        if (props.Length > 0) props.Append(",\n");
                        props.Append("  { \"id\": \"prop_").Append(propCount).Append("\", ");
                        props.Append("\"room\": \"").Append(id).Append("\", ");
                        props.Append("\"shape_class\": \"").Append(ShapeClass(mf.sharedMesh.name, mf.name)).Append("\", ");
                        props.Append("\"kind_hint\": \"").Append(Esc(mf.name)).Append("\", ");
                        props.Append("\"position\": ").Append(V3(mf.transform.position)).Append(", ");
                        props.Append("\"bounds\": ").Append(BoundsJson(pb)).Append(" }");
                        propCount++;
                    }
                }
            }

            var sb = new StringBuilder();
            sb.Append("{\n");
            sb.Append("  \"generator\": { \"kind\": \"tessera_wfc\", \"seed\": ").Append(seed)
              .Append(", \"world_units_per_cell\": ").Append(F(cellSize.x))
              .Append(", \"tile_count\": ").Append(tileInstances.Count).Append(" },\n");
            sb.Append("  \"bounds\": ").Append(BoundsMinMaxJson(overallMin, overallMax)).Append(",\n");
            sb.Append("  \"rooms\": [\n").Append(rooms).Append("\n  ],\n");
            sb.Append("  \"doorways\": [\n\n  ],\n");   // no native Tessera doorway object — see FLAGGED (4)
            sb.Append("  \"props\": [\n").Append(props).Append("\n  ]\n");
            sb.Append("}\n");

            System.IO.Directory.CreateDirectory(System.IO.Path.GetDirectoryName(outPath));
            System.IO.File.WriteAllText(outPath, sb.ToString());
            return "OK: " + tileInstances.Count + " tiles, " + propCount + " props -> " + outPath;
        }

        static Transform NearestUnclaimed(List<Transform> candidates, HashSet<Transform> claimed, Vector3 pos, float maxDist)
        {
            Transform best = null;
            float bestDist = float.PositiveInfinity;
            foreach (var t in candidates)
            {
                if (t == null || claimed.Contains(t)) continue;
                float d = (t.position - pos).sqrMagnitude;
                if (d < bestDist) { bestDist = d; best = t; }
            }
            // Only trust a CONFIDENT match (within maxDist of the tile's Position). Beyond that, skip
            // props for this tile rather than risk silently attaching an unrelated GameObject's meshes —
            // "no props" degrades gracefully; "wrong props" does not (see FLAGGED (1)).
            if (best != null && bestDist <= maxDist * maxDist) return best;
            return null;
        }

        // --- shape classification: same taxonomy as DunGenLayoutExporter.ShapeClass (kept independent —
        // no shared assembly between the two exporters — but MUST stay in sync by inspection). ------------
        static string ShapeClass(string meshName, string objName)
        {
            string n = ((meshName ?? "") + " " + (objName ?? "")).ToLowerInvariant();
            if (n.Contains("cone") || n.Contains("tree") || n.Contains("spike") || n.Contains("stalagmite")) return "cone";
            if (n.Contains("cylinder") || n.Contains("pillar") || n.Contains("column") || n.Contains("barrel") ||
                n.Contains("brazier") || n.Contains("torch") || n.Contains("well") || n.Contains("pot")) return "cylinder";
            return "box";
        }

        // ── json helpers (mirrors DunGenLayoutExporter's hand-rolled writer — no Newtonsoft dependency) ──
        static string F(float v) => v.ToString("0.####", CultureInfo.InvariantCulture);
        static string V3(Vector3 v) => "[" + F(v.x) + ", " + F(v.y) + ", " + F(v.z) + "]";
        static string V3List(List<Vector3> vs)
        {
            var parts = new List<string>(vs.Count);
            foreach (var v in vs) parts.Add(V3(v));
            return string.Join(", ", parts);
        }
        static string BoundsJson(Bounds b) => "{ \"min\": " + V3(b.min) + ", \"max\": " + V3(b.max) + " }";
        static string BoundsMinMaxJson(Vector3 min, Vector3 max) => "{ \"min\": " + V3(min) + ", \"max\": " + V3(max) + " }";
        static string Esc(string s) => (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
        static string SafeStr(object v) { try { return v == null ? "" : v.ToString(); } catch { return ""; } }

        // ── reflection plumbing (version-robust member access; identical approach to DunGenLayoutExporter
        // so a Tessera minor-version rename degrades with a log rather than throwing mid-export) ──────────
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

        static object InvokeWithArgsReturn(object obj, string name, object[] args)
        {
            if (obj == null) return null;
            // match by name + arg count (mirrors DunGenLayoutExporter.InvokeWithArgs — the options arg can
            // be null so a typed GetMethod lookup requiring an exact Type would miss it without an
            // assembly reference to Tessera).
            foreach (var m in obj.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance))
                if (m.Name == name && m.GetParameters().Length == args.Length) return m.Invoke(obj, args);
            return null;
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
