// tessera_generate_and_export.cs — box-run companion to TesseraLayoutExporter.cs (epic #1508 Tessera lap).
// Runs IN-EDITOR via execute_code (roslyn does NOT reference the Tessera asmdef -> REFLECTION, same as
// the DunGen box script). Opens a Tessera sample scene, generates a small single-layer WFC layout, and
// writes the SAME layout-json contract (generator/bounds/rooms[+cell_positions]/doorways(empty)/props).
// Mirrors TesseraLayoutExporter.WriteLayout exactly. Self-restoring: reopens the prior scene, never saves.
System.Func<string, System.Type> findType = full => { foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies()) { var tt = asm.GetType(full); if (tt != null) return tt; } return null; };
var BF = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.IgnoreCase;
System.Func<object, string, object> gm = (obj, name) => { if (obj == null) return null; var t = obj.GetType(); var p = t.GetProperty(name, BF); if (p != null) return p.GetValue(obj); var f = t.GetField(name, BF); if (f != null) return f.GetValue(obj); return null; };
System.Action<object, string, object> sm = (obj, name, val) => { if (obj == null) return; var t = obj.GetType(); var p = t.GetProperty(name, BF); if (p != null && p.CanWrite) { p.SetValue(obj, val); return; } var f = t.GetField(name, BF); if (f != null) f.SetValue(obj, val); };

var priorScene = UnityEditor.SceneManagement.EditorSceneManager.GetActiveScene().path;
UnityEditor.SceneManagement.EditorSceneManager.OpenScene("Assets/Tessera/Sample/Castle/Castle.unity", UnityEditor.SceneManagement.OpenSceneMode.Single);
var genType = findType("Tessera.TesseraGenerator");
if (genType == null) { UnityEditor.SceneManagement.EditorSceneManager.OpenScene(priorScene); return "FAIL: Tessera.TesseraGenerator type not found"; }
var generator = UnityEngine.Object.FindFirstObjectByType(genType) as UnityEngine.Component;
if (generator == null) { UnityEditor.SceneManagement.EditorSceneManager.OpenScene(priorScene); return "FAIL: no TesseraGenerator in Castle scene"; }

// Keep the sample's native size (forcing y=1 makes the castle tileset's vertical-adjacency constraints
// unsolvable). A small 3D castle still exports a clean layout; we crop ONE room downstream for the plate.
var optType = findType("Tessera.TesseraGenerateOptions");
object options = optType != null ? System.Activator.CreateInstance(optType) : null;
if (options != null) sm(options, "seed", 12345);
object completion = null;
foreach (var m in generator.GetType().GetMethods()) { if (m.Name == "Generate" && m.GetParameters().Length == 1) { completion = m.Invoke(generator, new object[] { options }); break; } }
if (completion == null) { UnityEditor.SceneManagement.EditorSceneManager.OpenScene(priorScene); return "FAIL: Generate() returned null"; }
bool success = gm(completion, "success") is bool sb2 && sb2;
string contradiction = (gm(completion, "contradictionReason") as string) ?? "";
var tileInstances = new System.Collections.Generic.List<object>();
var ti = gm(completion, "tileInstances") as System.Collections.IEnumerable;
if (ti != null) foreach (var x in ti) tileInstances.Add(x);

var cellSize = gm(generator, "cellSize") is UnityEngine.Vector3 cs ? cs : new UnityEngine.Vector3(2f, 2f, 2f);
System.Func<UnityEngine.Vector3, string> V3 = v => "[" + v.x.ToString("0.####") + ", " + v.y.ToString("0.####") + ", " + v.z.ToString("0.####") + "]";
System.Func<string, string, string> SHP = (mn, on) => { var n = ((mn ?? "") + " " + (on ?? "")).ToLower(); if (n.Contains("cone") || n.Contains("tree") || n.Contains("spike") || n.Contains("stalag")) return "cone"; if (n.Contains("cylinder") || n.Contains("pillar") || n.Contains("column") || n.Contains("barrel") || n.Contains("brazier") || n.Contains("torch") || n.Contains("pot") || n.Contains("well")) return "cylinder"; return "box"; };

// candidate spawned children for best-effort prop scan (nearest unclaimed by Position).
var candidates = new System.Collections.Generic.List<UnityEngine.Transform>();
for (int i = 0; i < generator.transform.childCount; i++) candidates.Add(generator.transform.GetChild(i));
var claimed = new System.Collections.Generic.HashSet<UnityEngine.Transform>();
float matchMaxDist = UnityEngine.Mathf.Min(UnityEngine.Mathf.Abs(cellSize.x), UnityEngine.Mathf.Abs(cellSize.z)) * 0.25f;

var overallMin = new UnityEngine.Vector3(1e9f, 1e9f, 1e9f); var overallMax = new UnityEngine.Vector3(-1e9f, -1e9f, -1e9f);
var rooms = new System.Text.StringBuilder(); var props = new System.Text.StringBuilder(); int pc = 0;
for (int i = 0; i < tileInstances.Count; i++) {
  var inst = tileInstances[i]; string id = "room_" + i;
  var position = gm(inst, "Position") is UnityEngine.Vector3 pp ? pp : UnityEngine.Vector3.zero;
  var cell = gm(inst, "Cell") is UnityEngine.Vector3Int cc ? cc : UnityEngine.Vector3Int.zero;
  var cellsArr = gm(inst, "Cells") as UnityEngine.Vector3Int[];
  if (cellsArr == null || cellsArr.Length == 0) cellsArr = new UnityEngine.Vector3Int[] { cell };
  var cps = new System.Collections.Generic.List<string>();
  var rMin = new UnityEngine.Vector3(1e9f, 1e9f, 1e9f); var rMax = new UnityEngine.Vector3(-1e9f, -1e9f, -1e9f);
  foreach (var cr in cellsArr) {
    var delta = cr - cell;
    var wp = position + UnityEngine.Vector3.Scale(new UnityEngine.Vector3(delta.x, delta.y, delta.z), cellSize);
    cps.Add(V3(wp)); var half = cellSize * 0.5f;
    rMin = UnityEngine.Vector3.Min(rMin, wp - half); rMax = UnityEngine.Vector3.Max(rMax, wp + half);
  }
  overallMin = UnityEngine.Vector3.Min(overallMin, rMin); overallMax = UnityEngine.Vector3.Max(overallMax, rMax);
  var tileRef = gm(inst, "Tile") as UnityEngine.Object; string tileName = tileRef != null ? tileRef.name : "";
  string cellRot = (gm(inst, "CellRotation") ?? "").ToString();
  if (rooms.Length > 0) rooms.Append(",\n");
  rooms.Append("  { \"id\": \"" + id + "\", \"tags\": [], \"is_main_path\": false, \"tile_name\": \"" + tileName.Replace("\\", "").Replace("\"", "") + "\", \"cell_rotation\": \"" + cellRot.Replace("\"", "") + "\", \"bounds\": { \"min\": " + V3(rMin) + ", \"max\": " + V3(rMax) + " }, \"cell_positions\": [" + string.Join(", ", cps) + "] }");
  // nearest unclaimed child within matchMaxDist
  UnityEngine.Transform best = null; float bestD = 1e18f;
  foreach (var tr in candidates) { if (tr == null || claimed.Contains(tr)) continue; float dd = (tr.position - position).sqrMagnitude; if (dd < bestD) { bestD = dd; best = tr; } }
  if (best != null && bestD <= matchMaxDist * matchMaxDist) {
    claimed.Add(best);
    foreach (var mf in best.GetComponentsInChildren<UnityEngine.MeshFilter>()) {
      if (mf == null || mf.sharedMesh == null) continue; if (mf.transform == best) continue;
      var rend = mf.GetComponent<UnityEngine.Renderer>(); var pb = rend != null ? rend.bounds : new UnityEngine.Bounds(mf.transform.position, mf.transform.lossyScale);
      if (props.Length > 0) props.Append(",\n");
      props.Append("  { \"id\": \"prop_" + pc + "\", \"room\": \"" + id + "\", \"shape_class\": \"" + SHP(mf.sharedMesh.name, mf.name) + "\", \"kind_hint\": \"" + mf.name.Replace("\\", "").Replace("\"", "") + "\", \"position\": " + V3(mf.transform.position) + ", \"bounds\": { \"min\": " + V3(pb.min) + ", \"max\": " + V3(pb.max) + " } }");
      pc++;
    }
  }
}
var outp = new System.Text.StringBuilder();
outp.Append("{\n");
outp.Append("  \"generator\": { \"kind\": \"tessera_wfc\", \"seed\": 12345, \"world_units_per_cell\": " + cellSize.x.ToString("0.####") + ", \"tile_count\": " + tileInstances.Count + " },\n");
outp.Append("  \"bounds\": { \"min\": " + V3(overallMin) + ", \"max\": " + V3(overallMax) + " },\n");
outp.Append("  \"rooms\": [\n" + rooms + "\n  ],\n");
outp.Append("  \"doorways\": [\n\n  ],\n");
outp.Append("  \"props\": [\n" + props + "\n  ]\n}\n");
System.IO.File.WriteAllText("/home/unity/worldos-unity/tessera_layout.json", outp.ToString());
UnityEditor.SceneManagement.EditorSceneManager.OpenScene(priorScene);
return "OK success=" + success + " tiles=" + tileInstances.Count + " props=" + pc + " cellSize=" + cellSize.x + " contradiction=[" + contradiction + "] priorScene=" + priorScene;
