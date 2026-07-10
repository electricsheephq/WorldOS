// dungen_generate_and_export.cs — box-run companion to DunGenLayoutExporter.cs (epic #1508 box phase).
// Runs IN-EDITOR via execute_code. execute_code's roslyn does NOT reference the DunGen asmdef, so this
// mirrors the committed exporter's REFLECTION approach (loads DunGen types at runtime) — no Assets/*.cs
// deploy + editor restart. Generates one small dungeon from the Basic Sample flow and writes the SAME
// dungen_layout.json contract. Self-cleaning: destroys everything it creates, never saves the scene.
System.Func<string, System.Type> findType = full => { foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies()) { var tt = asm.GetType(full); if (tt != null) return tt; } return null; };
var BF = System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.IgnoreCase;
System.Func<object, string, object> gm = (obj, name) => { if (obj == null) return null; var t = obj.GetType(); var p = t.GetProperty(name, BF); if (p != null) return p.GetValue(obj); var f = t.GetField(name, BF); if (f != null) return f.GetValue(obj); return null; };
System.Action<object, string, object> sm = (obj, name, val) => { if (obj == null) return; var t = obj.GetType(); var p = t.GetProperty(name, BF); if (p != null && p.CanWrite) { p.SetValue(obj, val); return; } var f = t.GetField(name, BF); if (f != null) f.SetValue(obj, val); };

var flow = UnityEditor.AssetDatabase.LoadAssetAtPath<UnityEngine.Object>("Assets/DunGen/Samples/Basic/Basic Sample Dungeon.asset");
if (flow == null) return "FAIL: no flow asset";
var rtType = findType("DunGen.RuntimeDungeon");
if (rtType == null) return "FAIL: DunGen.RuntimeDungeon type not found";
foreach (var old in UnityEngine.Object.FindObjectsByType(rtType, UnityEngine.FindObjectsSortMode.None)) { var oc = old as UnityEngine.Component; if (oc != null && oc.gameObject.name == "DunGenSpike") UnityEngine.Object.DestroyImmediate(oc.gameObject); }
var go = new UnityEngine.GameObject("DunGenSpike");
var rt = go.AddComponent(rtType);
var gen = gm(rt, "Generator");
// The top-level Generator.DungeonFlow/Seed/... fields are DEPRECATED (DunGen 2.19); the pipeline reads
// Generator.Settings (a DungeonGeneratorSettings). Set the flow + seed THERE or the archetype validator
// fails with "No Dungeon Flow is assigned".
var settings = gm(gen, "Settings");
sm(settings, "DungeonFlow", flow); sm(settings, "Seed", 12345); sm(settings, "ShouldRandomizeSeed", false); sm(settings, "LengthMultiplier", 1.0f);
foreach (var m in rt.GetType().GetMethods()) { if (m.Name == "Generate" && m.GetParameters().Length == 1) { m.Invoke(rt, new object[] { null }); break; } }
var d = gm(gen, "CurrentDungeon");
if (d == null) { UnityEngine.Object.DestroyImmediate(go); return "FAIL: no CurrentDungeon status=" + gm(gen, "Status"); }

var tiles = new System.Collections.Generic.List<object>(); foreach (var x in (System.Collections.IEnumerable)gm(d, "AllTiles")) tiles.Add(x);
var idOf = new System.Collections.Generic.Dictionary<object, string>(); for (int i = 0; i < tiles.Count; i++) idOf[tiles[i]] = "room_" + i;
System.Func<UnityEngine.Vector3, string> V3 = v => "[" + v.x.ToString("0.####") + ", " + v.y.ToString("0.####") + ", " + v.z.ToString("0.####") + "]";
System.Func<UnityEngine.Bounds, string> BB = b => "{ \"min\": " + V3(b.min) + ", \"max\": " + V3(b.max) + " }";
System.Func<string, string, string> SHP = (mn, on) => { var n = ((mn ?? "") + " " + (on ?? "")).ToLower(); if (n.Contains("cone") || n.Contains("tree") || n.Contains("spike") || n.Contains("stalag")) return "cone"; if (n.Contains("cylinder") || n.Contains("pillar") || n.Contains("column") || n.Contains("barrel") || n.Contains("brazier") || n.Contains("torch") || n.Contains("pot") || n.Contains("well")) return "cylinder"; return "box"; };
var omin = new UnityEngine.Vector3(1e9f, 1e9f, 1e9f); var omax = new UnityEngine.Vector3(-1e9f, -1e9f, -1e9f);
var rooms = new System.Text.StringBuilder(); var props = new System.Text.StringBuilder(); int pc = 0;
for (int i = 0; i < tiles.Count; i++) {
  var t = tiles[i]; var pl = gm(t, "Placement"); var b = (UnityEngine.Bounds)gm(pl, "Bounds");
  omin = UnityEngine.Vector3.Min(omin, b.min); omax = UnityEngine.Vector3.Max(omax, b.max);
  bool onMain = pl != null && gm(pl, "IsOnMainPath") != null && (bool)gm(pl, "IsOnMainPath");
  if (rooms.Length > 0) rooms.Append(",\n");
  rooms.Append("  { \"id\": \"" + idOf[t] + "\", \"tags\": [], \"is_main_path\": " + (onMain ? "true" : "false") + ", \"bounds\": " + BB(b) + " }");
  var tgo = ((UnityEngine.Component)t).gameObject; var ttf = ((UnityEngine.Component)t).transform;
  foreach (var mf in tgo.GetComponentsInChildren<UnityEngine.MeshFilter>()) {
    if (mf == null || mf.sharedMesh == null) continue; if (mf.transform == ttf) continue;
    var rend = mf.GetComponent<UnityEngine.Renderer>(); var pb = rend != null ? rend.bounds : new UnityEngine.Bounds(mf.transform.position, mf.transform.lossyScale);
    if (props.Length > 0) props.Append(",\n");
    props.Append("  { \"id\": \"prop_" + pc + "\", \"room\": \"" + idOf[t] + "\", \"shape_class\": \"" + SHP(mf.sharedMesh.name, mf.name) + "\", \"kind_hint\": \"" + mf.name.Replace("\\", "").Replace("\"", "") + "\", \"position\": " + V3(mf.transform.position) + ", \"bounds\": " + BB(pb) + " }");
    pc++;
  }
}
var doors = new System.Text.StringBuilder(); int di = 0;
foreach (var conn in (System.Collections.IEnumerable)gm(d, "Connections")) {
  var a = gm(conn, "A"); var bd = gm(conn, "B"); if (a == null || bd == null) continue;
  var at = ((UnityEngine.Component)a).transform; var bt = ((UnityEngine.Component)bd).transform;
  var ta = gm(a, "Tile"); var tb = gm(bd, "Tile");
  string ra = ta != null && idOf.ContainsKey(ta) ? idOf[ta] : ""; string rb = tb != null && idOf.ContainsKey(tb) ? idOf[tb] : "";
  if (doors.Length > 0) doors.Append(",\n");
  doors.Append("  { \"id\": \"door_" + di + "\", \"room_a\": \"" + ra + "\", \"room_b\": \"" + rb + "\", \"position\": " + V3((at.position + bt.position) * 0.5f) + ", \"forward\": " + V3(at.forward) + " }");
  di++;
}
var outp = new System.Text.StringBuilder();
outp.Append("{\n");
outp.Append("  \"generator\": { \"seed\": 12345, \"world_units_per_cell\": 2.0, \"tile_count\": " + tiles.Count + " },\n");
outp.Append("  \"bounds\": " + BB(new UnityEngine.Bounds((omin + omax) * 0.5f, omax - omin)) + ",\n");
outp.Append("  \"rooms\": [\n" + rooms + "\n  ],\n");
outp.Append("  \"doorways\": [\n" + doors + "\n  ],\n");
outp.Append("  \"props\": [\n" + props + "\n  ]\n}\n");
System.IO.File.WriteAllText("/home/unity/worldos-unity/dungen_layout.json", outp.ToString());
var root = gm(gen, "Root") as UnityEngine.GameObject;
UnityEngine.Object.DestroyImmediate(go);
if (root != null) UnityEngine.Object.DestroyImmediate(root);
return "OK tiles=" + tiles.Count + " doors=" + di + " props=" + pc + " status=" + gm(gen, "Status");
