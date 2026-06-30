// paint_combat_v1.cs — P0 FIRST multi-actor combat frame: hero + goblin on the painterly crypt plate,
// gold/red selection rings, contact AO, an impact VFX burst + a floating "-8" damage number.
// Built off the PROVEN paint_3d_spike.cs (same unqualified UnityEngine/UnityEditor style the wrapper injects).
// NO AnimatorController (its assembly isn't referenced by code-execute); actors are placed (pose-sampling = v2).
// Run: unity-mcp code execute --no-safety-checks -f paint_combat_v1.cs
AssetDatabase.Refresh();
// Room-agnostic plate: read the active room's plate filename from a box config (written by the seed/driver);
// default = the crypt. Lets the SAME renderer play combat on ANY generated room (tavern/church/...) by swapping
// the plate with no code edit — the modular-room analogue of the asset registry.
string PLATE="crypt_firelit_v2.png"; { var _abs="/home/unity/worldos-unity/Assets/painterly/backdrops/_active_combat.txt"; if(System.IO.File.Exists(_abs)){ var _n=System.IO.File.ReadAllText(_abs).Trim(); if(_n.Length>0) PLATE=_n; } }
string PLATE_PATH="Assets/painterly/backdrops/"+PLATE;
// New backdrop plates default to NPOT=ToNearest, which square-distorts a 1344x768 plate and breaks the
// camera-pin aspect. Force NPOT=None so the plate keeps native dims (idempotent — only reimports if needed).
{ var _ti=AssetImporter.GetAtPath(PLATE_PATH) as TextureImporter; if(_ti!=null && _ti.npotScale!=TextureImporterNPOTScale.None){ _ti.npotScale=TextureImporterNPOTScale.None; _ti.maxTextureSize=2048; _ti.SaveAndReimport(); } }
var sb=new System.Text.StringBuilder();
Camera cam=Camera.main; if(cam==null && Camera.allCameras.Length>0) cam=Camera.allCameras[0]; if(cam==null) return "no cam";
// validate the plate BEFORE mutating camera/renderers — a missing plate must not leave the editor scene corrupted.
var bdTex=AssetDatabase.LoadAssetAtPath<Texture2D>(PLATE_PATH); if(bdTex==null) return "no plate: "+PLATE_PATH;
cam.orthographic=true; cam.orthographicSize=13f; cam.nearClipPlane=0.3f; cam.farClipPlane=500f;
{ Quaternion _crot=Quaternion.Euler(30f,45f,0f); cam.transform.rotation=_crot; cam.transform.position=-(_crot*Vector3.forward)*80f; }
cam.clearFlags=CameraClearFlags.SolidColor; cam.backgroundColor=new Color(0.02f,0.02f,0.03f);
int hidden=0; foreach(var r in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None)){ if(r.enabled){r.enabled=false;hidden++;} }

var oldBd=GameObject.Find("PaintedBackdrop"); if(oldBd!=null) UnityEngine.Object.DestroyImmediate(oldBd);
var bd=GameObject.CreatePrimitive(PrimitiveType.Quad); bd.name="PaintedBackdrop"; UnityEngine.Object.DestroyImmediate(bd.GetComponent<Collider>());
bd.transform.SetParent(cam.transform,false); float texAsp=(float)bdTex.width/bdTex.height; float oh=cam.orthographicSize*2f; float ow=oh*texAsp;
bd.transform.localPosition=new Vector3(0,0,160f); bd.transform.localScale=new Vector3(ow,oh,1f);
var bm=new Material(Shader.Find("Unlit/Texture")); bm.mainTexture=bdTex; bm.renderQueue=1900; var bdr=bd.GetComponent<Renderer>(); bdr.sharedMaterial=bm; bdr.enabled=true; bdr.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; bdr.receiveShadows=false;

System.Func<int,int,Vector3> cellToWorld=(cx,cy)=> new Vector3((cx-6.5f)*2.0f,0f,(5.0f-cy)*2.0f);

// PoE2 lighting rig (from spike)
foreach(var ln in new[]{"KeyLight","FillLight","BrazierL","BrazierR"}){ var o=GameObject.Find(ln); if(o!=null) UnityEngine.Object.DestroyImmediate(o); }
var lg=new GameObject("KeyLight"); var L=lg.AddComponent<Light>(); L.type=LightType.Directional; L.color=new Color(1f,0.73f,0.44f); L.intensity=1.35f; L.shadows=LightShadows.Soft; L.shadowStrength=0.75f; lg.transform.rotation=Quaternion.Euler(48f,35f,0f);
var fg=new GameObject("FillLight"); var F=fg.AddComponent<Light>(); F.type=LightType.Directional; F.color=new Color(0.36f,0.44f,0.64f); F.intensity=0.55f; F.shadows=LightShadows.None; fg.transform.rotation=Quaternion.Euler(34f,215f,0f);
// warm-neutral ambient (was cool 0.24,0.28,0.40) so the 3D actors read FIRELIT, not cool-studio-lit (critic L3/L4).
RenderSettings.ambientMode=UnityEngine.Rendering.AmbientMode.Flat; RenderSettings.ambientLight=new Color(0.30f,0.25f,0.21f);
// brazier range 7.5->18 so the firelight actually REACHES the combatants (they sit ~11 units from a brazier).
System.Action<string,int,int,bool> brazier=(nm,cx,cy,sh)=>{ var bg=new GameObject(nm); var B=bg.AddComponent<Light>(); B.type=LightType.Point; B.color=new Color(1f,0.48f,0.18f); B.range=18f; B.intensity=3.6f; B.shadows=sh?LightShadows.Soft:LightShadows.None; var wp=cellToWorld(cx,cy); bg.transform.position=new Vector3(wp.x,1.7f,wp.z); };
brazier("BrazierL",4,1,true); brazier("BrazierR",9,1,false);
// a warm combat key above center so actors take a strong firelight rim (they were reading net-cool).
{ var ck=new GameObject("CombatKey"); var CK=ck.AddComponent<Light>(); CK.type=LightType.Point; CK.color=new Color(1f,0.6f,0.32f); CK.range=26f; CK.intensity=2.2f; CK.shadows=LightShadows.None; ck.transform.position=new Vector3(0f,8f,3f); }

// shared AO blob + ring textures
var blobT=new Texture2D(256,256,TextureFormat.RGBA32,false); blobT.wrapMode=TextureWrapMode.Clamp; { var px=new Color[256*256]; float c=127.5f; for(int y=0;y<256;y++)for(int x=0;x<256;x++){ float d=Mathf.Clamp01(Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c))/c); px[y*256+x]=new Color(0.02f,0.02f,0.03f,Mathf.Pow(1f-d,0.9f)); } blobT.SetPixels(px); blobT.Apply(); }
var ringT=new Texture2D(256,256,TextureFormat.RGBA32,false); ringT.wrapMode=TextureWrapMode.Clamp; { var px=new Color[256*256]; float c=127.5f; for(int y=0;y<256;y++)for(int x=0;x<256;x++){ float d=Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c))/c; float a=(d>0.78f&&d<0.93f)?1f:0f; px[y*256+x]=new Color(1f,1f,1f,a); } ringT.SetPixels(px); ringT.Apply(); }

// actor spawner (generalizes the spike's hero block): load fbx, stand up, scale to height, place at cell, foot-snap, albedo, AO, ring.
bool missingActor=false;
System.Func<string,string,string,int,int,float,Color,string,Vector3> spawn=(fbxPath,albedoPath,poseClipPath,cx,cy,height,ringCol,nm)=>{
  var prefab=AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath); if(prefab==null){ sb.AppendLine("MISSING "+fbxPath); missingActor=true; return cellToWorld(cx,cy); }
  var old=GameObject.Find(nm); if(old!=null) UnityEngine.Object.DestroyImmediate(old);
  var go=(GameObject)UnityEngine.Object.Instantiate(prefab); go.name=nm;
  if(poseClipPath!=null){ var pas=AssetDatabase.LoadAllAssetsAtPath(poseClipPath); foreach(var clipAsset in pas){ var clip=clipAsset as AnimationClip; if(clip!=null && !clip.name.StartsWith("__")){ clip.SampleAnimation(go, clip.length*0.45f); sb.AppendLine(nm+" posed by "+clip.name); break; } } }
  go.transform.rotation=Quaternion.Euler(-90f, cam.transform.eulerAngles.y+180f, 0f);
  var rends=go.GetComponentsInChildren<Renderer>(); foreach(var r in rends){ r.enabled=true; r.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.On; r.receiveShadows=true; }
  System.Func<Bounds> measure=()=>{ Bounds b=new Bounds(go.transform.position,Vector3.zero); bool a=false; foreach(var r in rends){ if(!a){b=r.bounds;a=true;} else b.Encapsulate(r.bounds);} return b; };
  Bounds bb=measure(); float curH=bb.size.y>0.001f?bb.size.y:1f; float s=height/curH; go.transform.localScale=go.transform.localScale*s;
  // ground + CENTER on the cell: snap feet to Y=0 AND align bounds-center X/Z to the cell (fixes the critic's
  // "actor decoupled from its ring" — meshes whose geometry is offset from their transform origin drifted off-ring).
  var p=cellToWorld(cx,cy); go.transform.position=p; bb=measure(); Vector3 ctr=bb.center; go.transform.position+=new Vector3(p.x-ctr.x,-bb.min.y,p.z-ctr.z);
  if(albedoPath!=null){ var al=AssetDatabase.LoadAssetAtPath<Texture2D>(albedoPath); if(al!=null){ var mm=new Material(Shader.Find("Standard")); mm.mainTexture=al; mm.SetFloat("_Glossiness",0.2f); mm.SetFloat("_Metallic",0f); foreach(var r in rends) r.sharedMaterial=mm; } }
  var oldAo=GameObject.Find(nm+"_AO"); if(oldAo!=null) UnityEngine.Object.DestroyImmediate(oldAo);
  var oldRg=GameObject.Find(nm+"_Ring"); if(oldRg!=null) UnityEngine.Object.DestroyImmediate(oldRg);
  var ao=GameObject.CreatePrimitive(PrimitiveType.Quad); ao.name=nm+"_AO"; UnityEngine.Object.DestroyImmediate(ao.GetComponent<Collider>()); ao.transform.position=new Vector3(p.x,0.04f,p.z); ao.transform.localEulerAngles=new Vector3(90f,0f,0f); ao.transform.localScale=new Vector3(2.2f,1.4f,1f); var aom=new Material(Shader.Find("Unlit/Transparent")); aom.mainTexture=blobT; aom.renderQueue=1950; ao.GetComponent<Renderer>().sharedMaterial=aom; ao.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;
  var rg=GameObject.CreatePrimitive(PrimitiveType.Quad); rg.name=nm+"_Ring"; UnityEngine.Object.DestroyImmediate(rg.GetComponent<Collider>()); rg.transform.position=new Vector3(p.x,0.06f,p.z); rg.transform.localEulerAngles=new Vector3(90f,0f,0f); rg.transform.localScale=new Vector3(2.7f,1.7f,1f); var rgm=new Material(Shader.Find("Unlit/Transparent")); rgm.mainTexture=ringT; rgm.color=ringCol; rgm.renderQueue=1955; rg.GetComponent<Renderer>().sharedMaterial=rgm; rg.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;
  sb.AppendLine(nm+" x"+s.ToString("F2")+" @cell("+cx+","+cy+") rends="+rends.Length);
  return go.transform.position;
};

// LIVE engine combat-surface (engine = SOLE WRITER; this renderer is READ-ONLY — positions come from the engine cells).
string CID="camp_gfxdemo01"; string surfJson="";
try { surfJson=new System.Net.WebClient().DownloadString("http://127.0.0.1:8765/combat-surface?campaign="+CID); } catch (System.Exception e) { return "surface GET failed: "+e.Message; }
var root=MiniJson.Parse(surfJson) as System.Collections.Generic.Dictionary<string,object>;
if(root==null) return "surface parse failed";
var toks=root.ContainsKey("tokens")?(root["tokens"] as System.Collections.Generic.List<object>):null;
if(toks==null||toks.Count==0) return "no tokens on surface";
// sweep prior actors/overlays so a moved/removed token never leaves a stale instance (deterministic rerun).
// COLLECT then destroy with null-checks: destroying an actor root also destroys its children still in the
// FindObjectsByType array, so a single-loop destroy would access a destroyed child (Unity throws).
{ var _toKill=new System.Collections.Generic.List<GameObject>();
  foreach(var g in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None)){ if(g==null) continue; var gn=g.name; if(gn.StartsWith("Actor_")||gn.EndsWith("_AO")||gn.EndsWith("_Ring")||gn=="ImpactFX"||gn=="DmgNum") _toKill.Add(g); }
  foreach(var g in _toKill){ if(g!=null) UnityEngine.Object.DestroyImmediate(g); } }
// place an actor per token by SLOT (foe -> goblin template / ally -> hero template); cyan party / red foe ring (critic L5).
var posByName=new System.Collections.Generic.Dictionary<string,Vector3>(); int spawned=0; string celldbg="";
// Asset registry (modular default-template-on-miss): read registry.json via MiniJson; resolve each token's
// SLOT (name-slug + kind) -> exact asset OR default template, so a monster with no model auto-falls to the
// demon template with ZERO renderer edits (the asset analogue of engine=sole-writer). Absent registry -> the
// team default fbx (today's behavior), never null.
System.Collections.Generic.Dictionary<string,object> regAssets=null, regDefaults=null, regAliases=null;
{ var _rp="/home/unity/worldos-unity/registry.json"; if(System.IO.File.Exists(_rp)){ var _rr=MiniJson.Parse(System.IO.File.ReadAllText(_rp)) as System.Collections.Generic.Dictionary<string,object>; if(_rr!=null){ regAssets=_rr.ContainsKey("assets")?_rr["assets"] as System.Collections.Generic.Dictionary<string,object>:null; regDefaults=_rr.ContainsKey("defaults")?_rr["defaults"] as System.Collections.Generic.Dictionary<string,object>:null; regAliases=_rr.ContainsKey("aliases")?_rr["aliases"] as System.Collections.Generic.Dictionary<string,object>:null; } } }
System.Func<string,string> slugify=(s)=>{ if(string.IsNullOrEmpty(s)) return ""; var _b=new System.Text.StringBuilder(); foreach(char c in s.ToLower()){ if(char.IsLetterOrDigit(c)) _b.Append(c); else if(_b.Length>0 && _b[_b.Length-1]!='-') _b.Append('-'); } return _b.ToString().Trim('-'); };
System.Func<string,string,string[]> resolveAsset=(slug,kind)=>{
  string fbxDef=kind=="monster"?"Assets/chars_v2/goblin/goblin.fbx":"Assets/painterly/models/hero.fbx";
  string albDef=kind=="monster"?"Assets/chars_v2/goblin/albedo.png":"Assets/painterly/models/hero_albedo.png";
  if(regAssets==null) return new string[]{fbxDef,albDef};
  string id=slug;
  if(!regAssets.ContainsKey(id) && regAliases!=null && regAliases.ContainsKey(id)) id=regAliases[id] as string;
  if((id==null||!regAssets.ContainsKey(id)) && regDefaults!=null){ if(regDefaults.ContainsKey(kind)) id=regDefaults[kind] as string; else if(regDefaults.ContainsKey("__any__")) id=regDefaults["__any__"] as string; }
  if(id!=null && regAssets.ContainsKey(id)){ var a=regAssets[id] as System.Collections.Generic.Dictionary<string,object>; if(a!=null){ string m=a.ContainsKey("model_ref")?a["model_ref"] as string:null; string al=a.ContainsKey("albedo_ref")?a["albedo_ref"] as string:null; return new string[]{ string.IsNullOrEmpty(m)?fbxDef:m, string.IsNullOrEmpty(al)?albDef:al }; } }
  return new string[]{fbxDef,albDef};
};
foreach(var o in toks){ var t=o as System.Collections.Generic.Dictionary<string,object>; if(t==null||!t.ContainsKey("x")||t["x"]==null) continue;
  int cx=System.Convert.ToInt32(t["x"]); int cy=System.Convert.ToInt32(t["y"]); string team=t["team"] as string; string nm=t["name"] as string;
  string tid=t.ContainsKey("id")?(t["id"] as string):null; if(string.IsNullOrEmpty(tid)) tid=nm;
  bool foe=(team=="foe");
  string kind=foe?"monster":"character";
  var aref=resolveAsset(slugify(nm),kind); string fbx=aref[0]; string alb=aref[1];
  float h=foe?4.2f:5.0f; Color ring=foe?new Color(1f,0.13f,0.10f,1f):new Color(0.4f,0.95f,1f,1f);
  var pos=spawn(fbx,alb,null,cx,cy,h,ring,"Actor_"+tid);
  if(nm!=null) posByName[nm]=pos; spawned++; celldbg+=" "+nm+"("+team+")@"+cx+","+cy;
}
if(missingActor){ sb.AppendLine("ABORT capture — a required actor prefab was missing (no PNG written)"); return sb.ToString(); }
sb.AppendLine("LIVE "+CID+": spawned "+spawned+" actors:"+celldbg);
// latest damage from the battleLog -> floating "-N" + impact burst over the struck token (skip if no recent hit).
string dmgTarget=""; int dmgN=0; var blog=root.ContainsKey("battleLog")?(root["battleLog"] as System.Collections.Generic.List<object>):null;
if(blog!=null){ foreach(var e in blog){ string tx=null; var ed=e as System.Collections.Generic.Dictionary<string,object>; if(ed!=null&&ed.ContainsKey("text")) tx=ed["text"] as string; else tx=e as string;
  if(tx!=null&&tx.Contains(" hits ")&&tx.Contains(" for ")&&tx.Contains("damage")){ int hi=tx.IndexOf(" hits "); int fi=tx.IndexOf(" for ",hi); if(hi>=0&&fi>hi){ dmgTarget=tx.Substring(hi+6,fi-(hi+6)).Trim(); var aft=tx.Substring(fi+5).TrimStart().Split(' '); if(aft.Length>0) int.TryParse(aft[0],out dmgN); } } } }

// impact VFX burst + floating "-N" over the STRUCK token — ONLY when the battleLog has a recent hit (no false VFX, no dead air).
if(dmgN>0 && !string.IsNullOrEmpty(dmgTarget) && posByName.ContainsKey(dmgTarget)){
  Vector3 tpos=posByName[dmgTarget];
  var fx=GameObject.CreatePrimitive(PrimitiveType.Quad); fx.name="ImpactFX"; UnityEngine.Object.DestroyImmediate(fx.GetComponent<Collider>()); fx.transform.position=tpos+new Vector3(0f,2.0f,0f); fx.transform.rotation=cam.transform.rotation; fx.transform.localScale=new Vector3(3.4f,3.4f,1f);
  var fxT=new Texture2D(128,128,TextureFormat.RGBA32,false); { var px=new Color[128*128]; float c=63.5f; for(int y=0;y<128;y++)for(int x=0;x<128;x++){ float d=Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c))/c; float a=Mathf.Clamp01(1f-d); px[y*128+x]=new Color(1f,0.62f,0.16f,a*a); } fxT.SetPixels(px); fxT.Apply(); }
  var fxm=new Material(Shader.Find("Unlit/Transparent")); fxm.mainTexture=fxT; fxm.color=new Color(1f,1f,1f,0.92f); fxm.renderQueue=3000; fx.GetComponent<Renderer>().sharedMaterial=fxm; fx.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;
  var dmgGo=new GameObject("DmgNum"); dmgGo.transform.position=tpos+new Vector3(0f,3.7f,0f); dmgGo.transform.rotation=cam.transform.rotation; var tm=dmgGo.AddComponent<TextMesh>(); tm.text="-"+dmgN; tm.fontSize=90; tm.characterSize=0.22f; tm.anchor=TextAnchor.MiddleCenter; tm.alignment=TextAlignment.Center; tm.color=new Color(1f,0.95f,0.45f,1f); var tmr=dmgGo.GetComponent<MeshRenderer>(); if(tmr!=null && tmr.sharedMaterial!=null) tmr.sharedMaterial.renderQueue=3100;
  sb.AppendLine("VFX: "+dmgTarget+" -"+dmgN);
}

// capture
int W=1920,Hh=Mathf.RoundToInt(1920f*(float)bdTex.height/bdTex.width); var rt=new RenderTexture(W,Hh,24,RenderTextureFormat.ARGB32); rt.Create();
float pa=cam.aspect; var pt=cam.targetTexture; cam.targetTexture=rt; cam.aspect=(float)W/Hh; cam.Render();
var pAct=RenderTexture.active; RenderTexture.active=rt; var t2=new Texture2D(W,Hh,TextureFormat.RGB24,false); t2.ReadPixels(new Rect(0,0,W,Hh),0,0); t2.Apply(); RenderTexture.active=pAct; cam.targetTexture=pt; cam.aspect=pa;
System.IO.Directory.CreateDirectory("/home/unity/worldos-unity/Captures-Durable");
System.IO.File.WriteAllBytes("/home/unity/worldos-unity/Captures-Durable/m1_combat_v1.png", t2.EncodeToPNG());
UnityEngine.Object.DestroyImmediate(t2); rt.Release(); UnityEngine.Object.DestroyImmediate(rt);
sb.AppendLine("captured "+W+"x"+Hh+" -> m1_combat_v1.png hidden="+hidden);
// Persist the combat frame (anti render-and-forget — a documented WorldOS regression cause) to a DEDICATED
// scene file, mirroring paint_3d_spike.cs. Safe vs the paint_combat_scene.cs:18 corruption class: the
// renderer-hide above is idempotent (only disables already-enabled renderers) and we save to a dedicated
// M1CombatV1 scene, never overwriting a shared/source scene, so reruns don't compound-corrupt canonical state.
try { var _scn=UnityEngine.SceneManagement.SceneManager.GetActiveScene(); System.IO.Directory.CreateDirectory("Assets/Scenes"); UnityEditor.SceneManagement.EditorSceneManager.SaveScene(_scn, "Assets/Scenes/M1CombatV1_canonical.unity"); sb.AppendLine("scene SAVED -> Assets/Scenes/M1CombatV1_canonical.unity"); } catch(System.Exception _e){ sb.AppendLine("SaveScene FAILED: "+_e.Message); }
return sb.ToString();
