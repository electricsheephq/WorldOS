// wave2_monster_grounded_renders.cs — #1305 wave-2 monster cast: ONE grounded upright render per
// new actor (zombie, bandit, cult_leader, animated_armor, giant_spider, dire_rat) on the
// crypt_dense_v1 plate. Standalone (no live /combat-surface dependency) — reuses the PROVEN
// grounding/pitch/scale/albedo logic from paint_combat_v1.cs's spawn() closure (BakeMesh-corrected
// world bounds so scale isn't double-applied, per #1412; pitch=0 for any SkinnedMeshRenderer actor,
// since Meshy/Tripo FBX import UPRIGHT — no Euler -90, per #1305's contract), just without the
// per-frame combat-surface token loop: one actor spawned, rendered, captured, destroyed, repeat.
// Run: unity-mcp code execute --no-safety-checks -f wave2_monster_grounded_renders.cs
AssetDatabase.Refresh();
var sb = new System.Text.StringBuilder();
string PLATE_PATH = "Assets/painterly/backdrops/crypt_dense_v1.png";
var bdTex = AssetDatabase.LoadAssetAtPath<Texture2D>(PLATE_PATH);
if (bdTex == null) return "no plate: " + PLATE_PATH;

Camera cam = Camera.main; if (cam == null && Camera.allCameras.Length > 0) cam = Camera.allCameras[0];
if (cam == null) return "no cam";
cam.orthographic = true; cam.orthographicSize = 13f; cam.nearClipPlane = 0.3f; cam.farClipPlane = 500f;
{ Quaternion _crot = Quaternion.Euler(30f, 45f, 0f); cam.transform.rotation = _crot; cam.transform.position = -(_crot * Vector3.forward) * 80f; }
cam.clearFlags = CameraClearFlags.SolidColor; cam.backgroundColor = new Color(0.02f, 0.02f, 0.03f);

int hidden = 0;
foreach (var r in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None)) { if (r.enabled) { r.enabled = false; hidden++; } }

var oldBd = GameObject.Find("Wave2Backdrop"); if (oldBd != null) UnityEngine.Object.DestroyImmediate(oldBd);
var bd = GameObject.CreatePrimitive(PrimitiveType.Quad); bd.name = "Wave2Backdrop"; UnityEngine.Object.DestroyImmediate(bd.GetComponent<Collider>());
bd.transform.SetParent(cam.transform, false);
float texAsp = (float)bdTex.width / bdTex.height; float oh = cam.orthographicSize * 2f; float ow = oh * texAsp;
bd.transform.localPosition = new Vector3(0, 0, 160f); bd.transform.localScale = new Vector3(ow, oh, 1f);
var bm = new Material(Shader.Find("Unlit/Texture")); bm.mainTexture = bdTex; bm.renderQueue = 1900;
var bdr = bd.GetComponent<Renderer>(); bdr.sharedMaterial = bm; bdr.enabled = true; bdr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off; bdr.receiveShadows = false;

System.Func<int, int, Vector3> cellToWorld = (cx, cy) => new Vector3((cx - 6.5f) * 2.0f, 0f, (5.0f - cy) * 2.0f);

foreach (var ln in new[] { "KeyLight", "FillLight", "BrazierL", "BrazierR", "CombatKey" }) { var o = GameObject.Find(ln); if (o != null) UnityEngine.Object.DestroyImmediate(o); }
var lg = new GameObject("KeyLight"); var L = lg.AddComponent<Light>(); L.type = LightType.Directional; L.color = new Color(1f, 0.73f, 0.44f); L.intensity = 1.35f; L.shadows = LightShadows.Soft; L.shadowStrength = 0.75f; lg.transform.rotation = Quaternion.Euler(48f, 35f, 0f);
var fg = new GameObject("FillLight"); var F = fg.AddComponent<Light>(); F.type = LightType.Directional; F.color = new Color(0.36f, 0.44f, 0.64f); F.intensity = 0.55f; F.shadows = LightShadows.None; fg.transform.rotation = Quaternion.Euler(34f, 215f, 0f);
RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat; RenderSettings.ambientLight = new Color(0.30f, 0.25f, 0.21f);
System.Action<string, int, int, bool> brazier = (nm, cx, cy, sh) => { var bg = new GameObject(nm); var B = bg.AddComponent<Light>(); B.type = LightType.Point; B.color = new Color(1f, 0.48f, 0.18f); B.range = 18f; B.intensity = 3.6f; B.shadows = sh ? LightShadows.Soft : LightShadows.None; var wp = cellToWorld(cx, cy); bg.transform.position = new Vector3(wp.x, 1.7f, wp.z); };
brazier("BrazierL", 4, 1, true); brazier("BrazierR", 9, 1, false);
{ var ck = new GameObject("CombatKey"); var CK = ck.AddComponent<Light>(); CK.type = LightType.Point; CK.color = new Color(1f, 0.6f, 0.32f); CK.range = 26f; CK.intensity = 2.2f; CK.shadows = LightShadows.None; ck.transform.position = new Vector3(0f, 8f, 3f); }

var blobT = new Texture2D(256, 256, TextureFormat.RGBA32, false); blobT.wrapMode = TextureWrapMode.Clamp;
{ var px = new Color[256 * 256]; float c = 127.5f; for (int y = 0; y < 256; y++) for (int x = 0; x < 256; x++) { float d = Mathf.Clamp01(Mathf.Sqrt((x - c) * (x - c) + (y - c) * (y - c)) / c); px[y * 256 + x] = new Color(0.02f, 0.02f, 0.03f, Mathf.Clamp01(Mathf.Pow(1f - d, 0.9f))); } blobT.SetPixels(px); blobT.Apply(); }

// #1412-corrected world bounds: BakeMesh output already reflects lossyScale, so position+rotation only.
System.Func<Renderer, Bounds> worldBounds = (r) => {
    var smr = r as SkinnedMeshRenderer; if (smr == null) return r.bounds;
    var bk = new Mesh(); smr.BakeMesh(bk); var vs = bk.vertices;
    if (vs.Length == 0) { UnityEngine.Object.DestroyImmediate(bk); return r.bounds; }
    var m = Matrix4x4.TRS(smr.transform.position, smr.transform.rotation, Vector3.one);
    var wb = new Bounds(m.MultiplyPoint3x4(vs[0]), Vector3.zero);
    for (int i = 1; i < vs.Length; i++) wb.Encapsulate(m.MultiplyPoint3x4(vs[i]));
    UnityEngine.Object.DestroyImmediate(bk); return wb;
};

int W = 1920, Hh = Mathf.RoundToInt(1920f * (float)bdTex.height / bdTex.width);
System.IO.Directory.CreateDirectory("/home/unity/worldos-unity/Captures-Durable");

// (name, fbxPath, albedoPath, poseClipPath, height)
var actors = new (string name, string fbx, string albedo, string poseClip, float height)[] {
    ("zombie",         "Assets/chars_v2/zombie/rigged.fbx",         "Assets/chars_v2/zombie/albedo.jpg",         "Assets/chars_v2/zombie/anim_idle.fbx",         5.0f),
    ("bandit",         "Assets/chars_v2/bandit/rigged.fbx",         "Assets/chars_v2/bandit/albedo.jpg",         "Assets/chars_v2/bandit/anim_idle.fbx",         5.0f),
    ("cult_leader",    "Assets/chars_v2/cult_leader/rigged.fbx",    "Assets/chars_v2/cult_leader/albedo.jpg",    "Assets/chars_v2/cult_leader/anim_idle.fbx",    5.0f),
    ("animated_armor", "Assets/chars_v2/animated_armor/rigged.fbx", "Assets/chars_v2/animated_armor/albedo.jpg", "Assets/chars_v2/animated_armor/anim_idle.fbx", 5.0f),
    ("giant_spider",   "Assets/chars_v2/giant_spider/rigged.fbx",   "Assets/chars_v2/giant_spider/albedo.jpg",   "Assets/chars_v2/giant_spider/anim_walk.fbx",   3.2f),
    ("dire_rat",       "Assets/chars_v2/dire_rat/rigged.fbx",       "Assets/chars_v2/dire_rat/albedo.jpg",       "Assets/chars_v2/dire_rat/anim_walk.fbx",       2.0f),
};

foreach (var a in actors)
{
    var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(a.fbx);
    if (prefab == null) { sb.AppendLine(a.name + ": MISSING " + a.fbx); continue; }
    var old = GameObject.Find("Wave2Actor"); if (old != null) UnityEngine.Object.DestroyImmediate(old);
    var go = (GameObject)UnityEngine.Object.Instantiate(prefab); go.name = "Wave2Actor";

    bool hasSkin = go.GetComponentInChildren<SkinnedMeshRenderer>() != null;
    float pitchX = hasSkin ? 0f : -90f; // Meshy/Tripo FBX import UPRIGHT — no stand-up needed for skinned rigs.
    go.transform.rotation = Quaternion.Euler(pitchX, cam.transform.eulerAngles.y + 180f, 0f);

    var rends = go.GetComponentsInChildren<Renderer>();
    foreach (var r in rends) {
        r.enabled = true; r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On; r.receiveShadows = true;
        var smrF = r as SkinnedMeshRenderer; if (smrF != null) { smrF.updateWhenOffscreen = true; smrF.forceMatrixRecalculationPerRender = true; }
    }

    System.Func<Bounds> measure = () => { Bounds b = new Bounds(go.transform.position, Vector3.zero); bool init = false; foreach (var r in rends) { var rb = worldBounds(r); if (!init) { b = rb; init = true; } else b.Encapsulate(rb); } return b; };
    Bounds bb = measure(); float curH = bb.size.y > 0.001f ? bb.size.y : 1f;
    float s = a.height / curH; go.transform.localScale = go.transform.localScale * s;

    // pose to a settled stance (idle if present, else walk@t0) — bind-pose height already locked above.
    bool posed = false;
    if (a.poseClip != null && System.IO.File.Exists(a.poseClip)) {
        AnimationClip pick = null;
        foreach (var clipAsset in AssetDatabase.LoadAllAssetsAtPath(a.poseClip)) {
            var clip = clipAsset as AnimationClip; if (clip == null || clip.name.StartsWith("__")) continue;
            if (clip.name.ToLower().Contains("idle")) { pick = clip; break; }
            if (pick == null) pick = clip;
        }
        if (pick != null) { pick.SampleAnimation(go, 0f); posed = true; sb.AppendLine(a.name + " posed by " + pick.name); }
    }

    var p = cellToWorld(7, 6); go.transform.position = p; bb = measure(); Vector3 ctr = bb.center;
    go.transform.position += new Vector3(p.x - ctr.x, 0f - bb.min.y, p.z - ctr.z);
    bb = measure();

    if (a.albedo != null) {
        var al = AssetDatabase.LoadAssetAtPath<Texture2D>(a.albedo);
        if (al != null) { var mm = new Material(Shader.Find("Standard")); mm.mainTexture = al; mm.SetFloat("_Glossiness", 0.2f); mm.SetFloat("_Metallic", 0f); foreach (var r in rends) r.sharedMaterial = mm; }
    }

    var oldAo = GameObject.Find("Wave2Actor_AO"); if (oldAo != null) UnityEngine.Object.DestroyImmediate(oldAo);
    var oldRg = GameObject.Find("Wave2Actor_Ring"); if (oldRg != null) UnityEngine.Object.DestroyImmediate(oldRg);
    var ao = GameObject.CreatePrimitive(PrimitiveType.Quad); ao.name = "Wave2Actor_AO"; UnityEngine.Object.DestroyImmediate(ao.GetComponent<Collider>());
    ao.transform.position = new Vector3(p.x, 0.04f, p.z); ao.transform.localEulerAngles = new Vector3(90f, 0f, 0f); ao.transform.localScale = new Vector3(2.4f, 2.4f, 1f);
    var aom = new Material(Shader.Find("Unlit/Transparent")); aom.mainTexture = blobT; aom.renderQueue = 1950; ao.GetComponent<Renderer>().sharedMaterial = aom; ao.GetComponent<Renderer>().shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;

    bb = measure();
    sb.AppendLine(a.name + " x" + s.ToString("F2") + " bbox=" + bb.size.ToString("F2") + " center=" + bb.center.ToString("F2") + " rends=" + rends.Length);

    var rt = new RenderTexture(W, Hh, 24, RenderTextureFormat.ARGB32); rt.Create();
    float pa = cam.aspect; var pt = cam.targetTexture; cam.targetTexture = rt; cam.aspect = (float)W / Hh; cam.Render();
    var pAct = RenderTexture.active; RenderTexture.active = rt; var t2 = new Texture2D(W, Hh, TextureFormat.RGB24, false); t2.ReadPixels(new Rect(0, 0, W, Hh), 0, 0); t2.Apply(); RenderTexture.active = pAct; cam.targetTexture = pt; cam.aspect = pa;
    string outPath = "/home/unity/worldos-unity/Captures-Durable/wave2_" + a.name + "_grounded.png";
    System.IO.File.WriteAllBytes(outPath, t2.EncodeToPNG());
    UnityEngine.Object.DestroyImmediate(t2); rt.Release(); UnityEngine.Object.DestroyImmediate(rt);
    sb.AppendLine(a.name + " captured -> " + outPath);

    UnityEngine.Object.DestroyImmediate(go);
    UnityEngine.Object.DestroyImmediate(ao);
}

return sb.ToString();
