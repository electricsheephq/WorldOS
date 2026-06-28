// paint_combat_scene.cs — M1 combat render: crypt plate + animated 3D hero (attack pose) + goblin,
// gold/red selection rings, contact shadows, a spell/impact VFX flash, and a floating damage number.
// Extends the PROVEN paint_3d_spike (crypt + textured/lit/grounded 3D hero). Targets the visual-critic
// levers that capped the old render: L4 (lit grounded 3D actor), L5 (rings + VFX + 2 readable actors),
// L2 (contact shadow). Run on the box via: unity-mcp code execute --no-safety-checks -f paint_combat_scene.cs
// DEPENDS ON: Assets/painterly/models/{hero.fbx OR rigged.fbx, hero_albedo.png}, the CombatActor controller
// (build_combat_animator.cs), and optionally Assets/painterly/models/goblin.fbx (+ goblin_albedo.png).
// NOTE: untested off-box — expect 1-2 iterations live (poses/ring scale/VFX placement).
using System.Linq;
AssetDatabase.Refresh();
var sb = new System.Text.StringBuilder();
Camera cam = Camera.main; if (cam == null && Camera.allCameras.Length > 0) cam = Camera.allCameras[0]; if (cam == null) return "no cam";

// --- frozen dimetric contract camera (cell 2.0, 14x11, elev30 yaw45 ortho13) ---
cam.orthographic = true; cam.orthographicSize = 13f; cam.nearClipPlane = 0.3f; cam.farClipPlane = 500f;
{ Quaternion r = Quaternion.Euler(30f, 45f, 0f); cam.transform.rotation = r; cam.transform.position = -(r * Vector3.forward) * 80f; }
cam.clearFlags = CameraClearFlags.SolidColor; cam.backgroundColor = new Color(0.02f, 0.02f, 0.03f);
int hidden = 0; foreach (var r in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None)) { if (r.enabled) { r.enabled = false; hidden++; } }
System.Func<int, int, Vector3> cellToWorld = (cx, cy) => new Vector3((cx - 6.5f) * 2.0f, 0f, (5.0f - cy) * 2.0f);

// --- painted crypt plate ---
var bdTex = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/backdrops/crypt_pinned_v1.png"); if (bdTex == null) return "no plate";
var oldBd = GameObject.Find("PaintedBackdrop"); if (oldBd != null) UnityEngine.Object.DestroyImmediate(oldBd);
var bd = GameObject.CreatePrimitive(PrimitiveType.Quad); bd.name = "PaintedBackdrop"; UnityEngine.Object.DestroyImmediate(bd.GetComponent<Collider>());
bd.transform.SetParent(cam.transform, false); float texAsp = (float)bdTex.width / bdTex.height; float oh = cam.orthographicSize * 2f; float ow = oh * texAsp;
bd.transform.localPosition = new Vector3(0, 0, 160f); bd.transform.localScale = new Vector3(ow, oh, 1f);
{ var bm = new Material(Shader.Find("Unlit/Texture")); bm.mainTexture = bdTex; bm.renderQueue = 1900; var bdr = bd.GetComponent<Renderer>(); bdr.sharedMaterial = bm; bdr.enabled = true; bdr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off; bdr.receiveShadows = false; }

// --- PoE2 lighting rig (warm key + cool fill + cool ambient + 2 brazier point lights) ---
foreach (var ln in new[] { "KeyLight", "FillLight", "BrazierL", "BrazierR" }) { var o = GameObject.Find(ln); if (o != null) UnityEngine.Object.DestroyImmediate(o); }
{ var lg = new GameObject("KeyLight"); var L = lg.AddComponent<Light>(); L.type = LightType.Directional; L.color = new Color(1f, 0.73f, 0.44f); L.intensity = 1.35f; L.shadows = LightShadows.Soft; L.shadowStrength = 0.8f; lg.transform.rotation = Quaternion.Euler(48f, 35f, 0f); }
{ var fg = new GameObject("FillLight"); var F = fg.AddComponent<Light>(); F.type = LightType.Directional; F.color = new Color(0.36f, 0.44f, 0.64f); F.intensity = 0.55f; F.shadows = LightShadows.None; fg.transform.rotation = Quaternion.Euler(34f, 215f, 0f); }
RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat; RenderSettings.ambientLight = new Color(0.24f, 0.28f, 0.40f);
System.Action<string, int, int> brazier = (nm, cx, cy) => { var bg = new GameObject(nm); var B = bg.AddComponent<Light>(); B.type = LightType.Point; B.color = new Color(1f, 0.48f, 0.18f); B.range = 7.5f; B.intensity = 3.6f; var wp = cellToWorld(cx, cy); bg.transform.position = new Vector3(wp.x, 1.7f, wp.z); };
brazier("BrazierL", 4, 1); brazier("BrazierR", 9, 1);

// --- helper: spawn a textured 3D actor (FBX) at a cell, stand-up + scale-MULTIPLY + foot-snap + albedo ---
System.Func<string, string, string, int, int, float, GameObject> spawnActor = (fbxPath, albedoPath, nm, cx, cy, targetH) => {
    var pf = AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath); if (pf == null) { sb.AppendLine("MISSING " + fbxPath); return null; }
    var old = GameObject.Find(nm); if (old != null) UnityEngine.Object.DestroyImmediate(old);
    var go = (GameObject)UnityEngine.Object.Instantiate(pf); go.name = nm;
    go.transform.rotation = Quaternion.Euler(-90f, cam.transform.eulerAngles.y + 180f, 0f); // stand the lying Z-up upright + face cam
    var rends = go.GetComponentsInChildren<Renderer>();
    foreach (var r in rends) { r.enabled = true; r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On; r.receiveShadows = true; }
    System.Func<Bounds> measure = () => { Bounds b = new Bounds(go.transform.position, Vector3.zero); bool a = false; foreach (var r in rends) { if (!a) { b = r.bounds; a = true; } else b.Encapsulate(r.bounds); } return b; };
    Bounds bb = measure(); float curH = bb.size.y > 0.001f ? bb.size.y : 1f; go.transform.localScale = go.transform.localScale * (targetH / curH);
    var p = cellToWorld(cx, cy); go.transform.position = p; bb = measure(); go.transform.position += new Vector3(0f, -bb.min.y, 0f);
    var alb = AssetDatabase.LoadAssetAtPath<Texture2D>(albedoPath);
    if (alb != null) { var m = new Material(Shader.Find("Standard")); m.mainTexture = alb; m.SetFloat("_Glossiness", 0.2f); m.SetFloat("_Metallic", 0f); foreach (var r in rends) r.sharedMaterial = m; }
    return go;
};

// --- contact-shadow AO blob under feet ---
var blobT = new Texture2D(256, 256, TextureFormat.RGBA32, false); blobT.wrapMode = TextureWrapMode.Clamp;
{ var px = new Color[256 * 256]; float c = 127.5f; for (int y = 0; y < 256; y++) for (int x = 0; x < 256; x++) { float d = Mathf.Clamp01(Mathf.Sqrt((x - c) * (x - c) + (y - c) * (y - c)) / c); px[y * 256 + x] = new Color(0.02f, 0.02f, 0.03f, Mathf.Pow(1f - d, 0.9f)); } blobT.SetPixels(px); blobT.Apply(); }
System.Action<string, int, int, float> aoBlob = (nm, cx, cy, sz) => { var ao = GameObject.CreatePrimitive(PrimitiveType.Quad); ao.name = nm; UnityEngine.Object.DestroyImmediate(ao.GetComponent<Collider>()); var wp = cellToWorld(cx, cy); ao.transform.position = new Vector3(wp.x, 0.05f, wp.z); ao.transform.localEulerAngles = new Vector3(90f, 0f, 0f); ao.transform.localScale = new Vector3(sz, sz * 0.62f, 1f); var aom = new Material(Shader.Find("Unlit/Transparent")); aom.mainTexture = blobT; aom.renderQueue = 1950; var aor = ao.GetComponent<Renderer>(); aor.sharedMaterial = aom; aor.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off; };

// --- selection ring (gold ally / red foe), flat ~2:1 ellipse concentric with the cell ---
System.Action<string, int, int, Color> ring = (nm, cx, cy, col) => { var q = GameObject.CreatePrimitive(PrimitiveType.Quad); q.name = nm; UnityEngine.Object.DestroyImmediate(q.GetComponent<Collider>()); var wp = cellToWorld(cx, cy); q.transform.position = new Vector3(wp.x, 0.06f, wp.z); q.transform.localEulerAngles = new Vector3(90f, 0f, 0f); q.transform.localScale = new Vector3(2.6f, 2.6f * 0.6f, 1f);
    // ring texture: a soft annulus
    var rt = new Texture2D(128, 128, TextureFormat.RGBA32, false); rt.wrapMode = TextureWrapMode.Clamp; var rp = new Color[128 * 128]; float cc = 63.5f; for (int y = 0; y < 128; y++) for (int x = 0; x < 128; x++) { float d = Mathf.Sqrt((x - cc) * (x - cc) + (y - cc) * (y - cc)) / cc; float a = Mathf.Clamp01(1f - Mathf.Abs(d - 0.82f) / 0.16f); rp[y * 128 + x] = new Color(col.r, col.g, col.b, a * 0.9f); } rt.SetPixels(rp); rt.Apply();
    var rm = new Material(Shader.Find("Unlit/Transparent")); rm.mainTexture = rt; rm.renderQueue = 1960; var rr = q.GetComponent<Renderer>(); rr.sharedMaterial = rm; rr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off; };

// === spawn HERO (attack pose) + GOBLIN (recoil) ===
int hx = 6, hy = 6, gx = 8, gy = 5;
string heroFbx = System.IO.File.Exists("/home/unity/worldos-unity/Assets/painterly/models/rigged.fbx") ? "Assets/painterly/models/rigged.fbx" : "Assets/painterly/models/hero.fbx";
var hero = spawnActor(heroFbx, "Assets/painterly/models/hero_albedo.png", "Hero3D", hx, hy, 5.0f);
// sample the ATTACK clip onto the hero (static mid-attack pose) if the CombatActor controller exists
if (hero != null) {
    var ctrl = AssetDatabase.LoadAssetAtPath<UnityEditor.Animations.AnimatorController>("Assets/Animations/CombatActor.controller");
    var atkClip = AssetDatabase.LoadAllAssetsAtPath("Assets/painterly/models/moveset/anim_attack.fbx").OfType<AnimationClip>().FirstOrDefault(c => !c.name.StartsWith("__"));
    if (atkClip != null) { atkClip.SampleAnimation(hero, atkClip.length * 0.45f); sb.AppendLine("sampled attack pose t=" + (atkClip.length * 0.45f).ToString("F2")); }
    else sb.AppendLine("no attack clip (idle pose)");
}
aoBlob("HeroAO", hx, hy, 2.4f); ring("HeroRing", hx, hy, new Color(1f, 0.80f, 0.42f));

var goblin = spawnActor("Assets/painterly/models/goblin.fbx", "Assets/painterly/models/goblin_albedo.png", "Goblin3D", gx, gy, 4.2f);
if (goblin != null) { aoBlob("GoblinAO", gx, gy, 2.0f); ring("GoblinRing", gx, gy, new Color(0.88f, 0.28f, 0.30f)); }
else sb.AppendLine("goblin.fbx not present -> hero-only (clean+deploy goblin for the full combat read)");

// === impact VFX flash at the goblin (Hovl prefab if available; else an additive slash quad) ===
{ var wp = cellToWorld(gx, gy); Object vpf = null;
  foreach (var g in AssetDatabase.FindAssets("t:GameObject impact").Take(1)) { vpf = AssetDatabase.LoadAssetAtPath<GameObject>(AssetDatabase.GUIDToAssetPath(g)); }
  if (vpf != null) { var v = (GameObject)UnityEngine.Object.Instantiate(vpf); v.name = "ImpactVFX"; v.transform.position = new Vector3(wp.x, 1.6f, wp.z); sb.AppendLine("Hovl impact VFX placed"); }
  else { var fl = GameObject.CreatePrimitive(PrimitiveType.Quad); fl.name = "SlashVFX"; UnityEngine.Object.DestroyImmediate(fl.GetComponent<Collider>()); fl.transform.position = new Vector3(wp.x, 1.7f, wp.z); fl.transform.rotation = cam.transform.rotation; fl.transform.localScale = Vector3.one * 2.2f;
    var ft = new Texture2D(128, 128, TextureFormat.RGBA32, false); var fp = new Color[128 * 128]; float fc = 63.5f; for (int y = 0; y < 128; y++) for (int x = 0; x < 128; x++) { float d = Mathf.Sqrt((x - fc) * (x - fc) + (y - fc) * (y - fc)) / fc; fp[y * 128 + x] = new Color(1f, 0.6f, 0.2f, Mathf.Clamp01(1f - d) * 0.85f); } ft.SetPixels(fp); ft.Apply();
    var fm = new Material(Shader.Find("Unlit/Transparent")); fm.mainTexture = ft; fm.renderQueue = 3000; fl.GetComponent<Renderer>().sharedMaterial = fm; sb.AppendLine("fallback slash VFX placed"); } }

// === floating damage number "-8" over the goblin (white, outlined-ish via a dark backing quad) ===
// (kept simple: a TextMesh billboarded to the camera)
{ var dn = new GameObject("DmgNumber"); var tm = dn.AddComponent<TextMesh>(); tm.text = "-8"; tm.fontSize = 64; tm.characterSize = 0.18f; tm.anchor = TextAnchor.MiddleCenter; tm.color = Color.white; tm.fontStyle = FontStyle.Bold; var wp = cellToWorld(gx, gy); dn.transform.position = new Vector3(wp.x + 0.8f, 3.6f, wp.z); dn.transform.rotation = cam.transform.rotation; dn.GetComponent<Renderer>().sharedMaterial.renderQueue = 4000; }

// === capture (1920-wide, plate aspect) ===
int W = 1920, Hh = Mathf.RoundToInt(1920f * (float)bdTex.height / bdTex.width); var rtex = new RenderTexture(W, Hh, 24, RenderTextureFormat.ARGB32); rtex.Create();
float pa = cam.aspect; var pt = cam.targetTexture; cam.targetTexture = rtex; cam.aspect = (float)W / Hh; cam.Render();
var pAct = RenderTexture.active; RenderTexture.active = rtex; var t2 = new Texture2D(W, Hh, TextureFormat.RGB24, false); t2.ReadPixels(new Rect(0, 0, W, Hh), 0, 0); t2.Apply(); RenderTexture.active = pAct; cam.targetTexture = pt; cam.aspect = pa;
System.IO.Directory.CreateDirectory("/home/unity/worldos-unity/Captures-Durable");
System.IO.File.WriteAllBytes("/home/unity/worldos-unity/Captures-Durable/m1_combat_v1.png", t2.EncodeToPNG());
UnityEngine.Object.DestroyImmediate(t2); rtex.Release(); UnityEngine.Object.DestroyImmediate(rtex);
sb.AppendLine("captured " + W + "x" + Hh + " -> m1_combat_v1.png");

// === PERSIST the scene (anti render-and-forget — CANONICAL.md discipline) ===
try { var scn = UnityEngine.SceneManagement.SceneManager.GetActiveScene(); System.IO.Directory.CreateDirectory("Assets/Scenes"); UnityEditor.SceneManagement.EditorSceneManager.SaveScene(scn, "Assets/Scenes/M1Combat_canonical.unity"); sb.AppendLine("scene SAVED"); } catch (System.Exception e) { sb.AppendLine("SaveScene failed: " + e.Message); }
return sb.ToString();
