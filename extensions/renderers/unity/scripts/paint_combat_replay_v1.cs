// paint_combat_replay_v1.cs — #1303 S2: /events Action-Replay -> Animator/VFX wiring.
//
// SIBLING to paint_combat_v1.cs (the proven LIVE single-frame /combat-surface painter). This driver
// makes turn-based combat READ AS MOTION: it consumes the engine's Action-Replay envelope from
// /events ({seq, actor_fk, verb, target_fk, result, anim_hint}, docs/roadmap/contracts/
// action-replay-envelope.md), sorts by seq, and plays each beat as an ANIMATED presentation beat on
// the SAME actors paint_combat_v1 spawns — verb->clip on a real Animator, DOTween glide along the
// engine's lastPath, flinch/topple/fade, VFX at the struck engine cell (registry default-on-miss),
// and the proven G1 damage-number + HP-bar drop folded into the animated timeline.
//
// INVARIANT (the one this whole surface exists for): the engine is the SOLE WRITER. This is a PURE,
// READ-ONLY projection — every field except anim_hint is engine-decided (an FK or an outcome the
// engine already rolled/applied); we read `result` to choose numbers/states to SHOW and NEVER
// recompute. anim_hint is advisory (unknown hint -> generic beat). DOTween is presentation-only: it
// glides only ENGINE-CONFIRMED paths (surface.lastPath), never a client-predicted route.
//
// CAPTURE MODEL: a `code execute` snippet runs synchronously in the editor (no Play mode, mirroring
// AnimFrameCapture.cs / paint_3d_spike.cs). We build the scene once, then step the beat timeline
// deterministically: for each beat we sample the actor Animator via Animator.Play(clip,-1,nt) +
// Update(0) at a few normalized peaks, evaluate the glide position along lastPath at t, place the
// VFX/damage overlays, and render a durable PNG per captured frame -> an animated REEL. No coroutine,
// no wall-clock, so a rerun is byte-deterministic.
//
// Run on the GEX44 box: unity-mcp code execute --no-safety-checks -f paint_combat_replay_v1.cs
// NO top-level `using`, NO LINQ (the wrapper injects unqualified UnityEngine/UnityEditor; `using` is
// illegal in the wrapped method body — same discipline as paint_combat_v1.cs).
AssetDatabase.Refresh();

// ---- config: plate + campaign + grid extents (IDENTICAL resolution to paint_combat_v1.cs) --------
string PLATE="crypt_dense_v1.png"; string _locPlate="";
float _gridCols=14f, _gridRows=11f;
string CID="camp_gfxdemo01"; { var _ac="/home/unity/worldos-unity/Assets/painterly/backdrops/_active_campaign.txt"; if(System.IO.File.Exists(_ac)){ var _c=System.IO.File.ReadAllText(_ac).Trim(); if(_c.Length>0) CID=_c; } }
try {
  var _sj=new System.Net.WebClient().DownloadString("http://127.0.0.1:8765/combat-surface?campaign="+CID);
  var _r=MiniJson.Parse(_sj) as System.Collections.Generic.Dictionary<string,object>;
  var _loc=(_r!=null && _r.ContainsKey("location"))?_r["location"] as System.Collections.Generic.Dictionary<string,object>:null;
  var _lid=(_loc!=null && _loc.ContainsKey("id"))?_loc["id"] as string:null;
  var _mapF="/home/unity/worldos-unity/Assets/painterly/backdrops/_location_plates.json";
  if(!string.IsNullOrEmpty(_lid) && System.IO.File.Exists(_mapF)){ var _m=MiniJson.Parse(System.IO.File.ReadAllText(_mapF)) as System.Collections.Generic.Dictionary<string,object>; if(_m!=null && _m.ContainsKey(_lid)) _locPlate=_m[_lid] as string; }
  var _grid=(_r!=null && _r.ContainsKey("grid"))?_r["grid"] as System.Collections.Generic.Dictionary<string,object>:null;
  if(_grid!=null){ if(_grid.ContainsKey("cols")) _gridCols=System.Convert.ToSingle(_grid["cols"]); if(_grid.ContainsKey("rows")) _gridRows=System.Convert.ToSingle(_grid["rows"]); }
} catch {}
if(!string.IsNullOrEmpty(_locPlate)) PLATE=_locPlate;
else { var _abs="/home/unity/worldos-unity/Assets/painterly/backdrops/_active_combat.txt"; if(System.IO.File.Exists(_abs)){ var _n=System.IO.File.ReadAllText(_abs).Trim(); if(_n.Length>0) PLATE=_n; } }
string PLATE_PATH="Assets/painterly/backdrops/"+PLATE;
{ var _ti=AssetImporter.GetAtPath(PLATE_PATH) as TextureImporter; if(_ti!=null && _ti.npotScale!=TextureImporterNPOTScale.None){ _ti.npotScale=TextureImporterNPOTScale.None; _ti.maxTextureSize=2048; _ti.SaveAndReimport(); } }
var sb=new System.Text.StringBuilder();

// ---- camera contract (CANONICAL.md: dimetric 30deg / yaw45 / cell 2.0 / ortho 13) ----------------
Camera cam=Camera.main; if(cam==null && Camera.allCameras.Length>0) cam=Camera.allCameras[0]; if(cam==null) return "no cam";
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

System.Func<int,int,Vector3> cellToWorld=(cx,cy)=> new Vector3((cx-(_gridCols-1f)/2f)*2.0f,0f,((_gridRows-1f)/2f-cy)*2.0f);
// float-cell variant for smooth DOTween glide sampling between engine cells (presentation-only).
System.Func<float,float,Vector3> cellToWorldF=(cx,cy)=> new Vector3((cx-(_gridCols-1f)/2f)*2.0f,0f,((_gridRows-1f)/2f-cy)*2.0f);

// ---- lighting rig (IDENTICAL to paint_combat_v1.cs) ----------------------------------------------
foreach(var ln in new[]{"KeyLight","FillLight","BrazierL","BrazierR","CombatKey"}){ var o=GameObject.Find(ln); if(o!=null) UnityEngine.Object.DestroyImmediate(o); }
var lg=new GameObject("KeyLight"); var L=lg.AddComponent<Light>(); L.type=LightType.Directional; L.color=new Color(1f,0.73f,0.44f); L.intensity=1.35f; L.shadows=LightShadows.Soft; L.shadowStrength=0.75f; lg.transform.rotation=Quaternion.Euler(48f,35f,0f);
var fg=new GameObject("FillLight"); var F=fg.AddComponent<Light>(); F.type=LightType.Directional; F.color=new Color(0.36f,0.44f,0.64f); F.intensity=0.55f; F.shadows=LightShadows.None; fg.transform.rotation=Quaternion.Euler(34f,215f,0f);
RenderSettings.ambientMode=UnityEngine.Rendering.AmbientMode.Flat; RenderSettings.ambientLight=new Color(0.30f,0.25f,0.21f);
System.Action<string,int,int,bool> brazier=(nm,cx,cy,sh)=>{ var bg=new GameObject(nm); var B=bg.AddComponent<Light>(); B.type=LightType.Point; B.color=new Color(1f,0.48f,0.18f); B.range=18f; B.intensity=3.6f; B.shadows=sh?LightShadows.Soft:LightShadows.None; var wp=cellToWorld(cx,cy); bg.transform.position=new Vector3(wp.x,1.7f,wp.z); };
brazier("BrazierL",4,1,true); brazier("BrazierR",9,1,false);
{ var ck=new GameObject("CombatKey"); var CK=ck.AddComponent<Light>(); CK.type=LightType.Point; CK.color=new Color(1f,0.6f,0.32f); CK.range=26f; CK.intensity=2.2f; CK.shadows=LightShadows.None; ck.transform.position=new Vector3(0f,8f,3f); }

// ---- AO blob + ring textures (IDENTICAL to paint_combat_v1.cs, baseline params) ------------------
var blobT=new Texture2D(256,256,TextureFormat.RGBA32,false); blobT.wrapMode=TextureWrapMode.Clamp; { var px=new Color[256*256]; float c=127.5f; for(int y=0;y<256;y++)for(int x=0;x<256;x++){ float d=Mathf.Clamp01(Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c))/c); px[y*256+x]=new Color(0.02f,0.02f,0.03f,Mathf.Clamp01(Mathf.Pow(1f-d,0.9f))); } blobT.SetPixels(px); blobT.Apply(); }
var ringT=new Texture2D(256,256,TextureFormat.RGBA32,false); ringT.wrapMode=TextureWrapMode.Clamp; { var px=new Color[256*256]; float c=127.5f; for(int y=0;y<256;y++)for(int x=0;x<256;x++){ float d=Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c))/c; float a=(d>0.78f&&d<0.93f)?1f:0f; px[y*256+x]=new Color(1f,1f,1f,a); } ringT.SetPixels(px); ringT.Apply(); }

// ---- CombatActor Animator controller (built by build_combat_animator.cs; idle/attack/cast/block/
//      dodge/hit/death states, doAttack trigger + IsWalking bool). Load once; assigned per actor.
string CTRL_PATH="Assets/Animations/CombatActor.controller";
var combatCtrl=AssetDatabase.LoadAssetAtPath<UnityEditor.Animations.AnimatorController>(CTRL_PATH);
if(combatCtrl==null) sb.AppendLine("WARN no CombatActor.controller — actors will pose without an Animator (run build_combat_animator.cs first)");

// The single moveset the controller is built from — used to SAMPLE a clip by NAME at a normalized
// time for a captured frame (Animator.Play needs a live controller; sampling a clip onto the mesh is
// the deterministic editor path AnimFrameCapture.cs uses). Map verb/anim_hint -> clip name.
string MOVESET_DIR="Assets/painterly/models/moveset/";
System.Func<string,AnimationClip> clipByName=(m)=>{ var pas=AssetDatabase.LoadAllAssetsAtPath(MOVESET_DIR+"anim_"+m+".fbx"); foreach(var a in pas){ var ac=a as AnimationClip; if(ac!=null && !ac.name.StartsWith("__")) return ac; } return null; };

// Which spawned actors are SKINNED Meshy/Generic rigs (goblin cast) whose moveset clips bake a Y-up
// bind pose -> sampling a clip FLATTENS them (owner-observed, and why paint_combat_v1.cs deliberately
// leaves poseClip null for the goblin). For these we drive the beat with TRANSFORM motion (lunge /
// knockback+tilt / topple, the CombatBeatDriver-proven approach) instead of clip-posing, so the actor
// stays upright + still READS AS MOTION. Static meshes (hero.fbx, no skinned rig) clip-pose cleanly.
var actorSkinned=new System.Collections.Generic.Dictionary<string,bool>();

// ---- actor spawner (spawn geometry IDENTICAL to paint_combat_v1.cs, PLUS an Animator so the actor
//      can play verb clips). Returns the actor root GO so the beat player can pose/glide/flinch it.
bool missingActor=false;
System.Func<string,string,int,int,float,Color,string,GameObject> spawn=(fbxPath,albedoPath,cx,cy,height,ringCol,nm)=>{
  var prefab=AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath); if(prefab==null){ sb.AppendLine("MISSING "+fbxPath); missingActor=true; return null; }
  var old=GameObject.Find(nm); if(old!=null) UnityEngine.Object.DestroyImmediate(old);
  var go=(GameObject)UnityEngine.Object.Instantiate(prefab); go.name=nm;
  go.transform.rotation=Quaternion.Euler(-90f, cam.transform.eulerAngles.y+180f, 0f);
  var rends=go.GetComponentsInChildren<Renderer>(); foreach(var r in rends){ r.enabled=true; r.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.On; r.receiveShadows=true; }
  // Grounding via BakeMesh -> true posed world bounds (SkinnedMeshRenderer.bounds is an inflated AABB
  // whose min.y floats the actor) — IDENTICAL to paint_combat_v1.cs.
  System.Func<Renderer,Bounds> worldBounds=(r)=>{ var smr=r as SkinnedMeshRenderer; if(smr==null) return r.bounds; var bk=new Mesh(); smr.BakeMesh(bk); var vs=bk.vertices; if(vs.Length==0){ UnityEngine.Object.DestroyImmediate(bk); return r.bounds; } var m=smr.transform.localToWorldMatrix; var wb=new Bounds(m.MultiplyPoint3x4(vs[0]),Vector3.zero); for(int i=1;i<vs.Length;i++) wb.Encapsulate(m.MultiplyPoint3x4(vs[i])); UnityEngine.Object.DestroyImmediate(bk); return wb; };
  System.Func<Bounds> measure=()=>{ Bounds b=new Bounds(go.transform.position,Vector3.zero); bool a=false; foreach(var r in rends){ var rb=worldBounds(r); if(!a){b=rb;a=true;} else b.Encapsulate(rb);} return b; };
  Bounds bb=measure(); float curH=bb.size.y>0.001f?bb.size.y:1f; float s=height/curH; go.transform.localScale=go.transform.localScale*s;
  var p=cellToWorld(cx,cy); go.transform.position=p; bb=measure(); Vector3 ctr=bb.center; go.transform.position+=new Vector3(p.x-ctr.x,-bb.min.y,p.z-ctr.z);
  if(albedoPath!=null){ var al=AssetDatabase.LoadAssetAtPath<Texture2D>(albedoPath); if(al!=null){ var mm=new Material(Shader.Find("Standard")); mm.mainTexture=al; mm.SetFloat("_Glossiness",0.2f); mm.SetFloat("_Metallic",0f); foreach(var r in rends) r.sharedMaterial=mm; } }
  // attach the CombatActor Animator so verb beats can drive real clips.
  if(combatCtrl!=null){ var anim=go.GetComponentInChildren<Animator>(); if(anim==null) anim=go.AddComponent<Animator>(); anim.runtimeAnimatorController=combatCtrl; anim.applyRootMotion=false; }
  // AO blob + selection ring (foot-anchored, camera foreshortens the flat circle to the iso ellipse).
  var ao=GameObject.CreatePrimitive(PrimitiveType.Quad); ao.name=nm+"_AO"; UnityEngine.Object.DestroyImmediate(ao.GetComponent<Collider>()); ao.transform.position=new Vector3(p.x,0.04f,p.z); ao.transform.localEulerAngles=new Vector3(90f,0f,0f); ao.transform.localScale=new Vector3(2.0f,2.0f,1f); var aom=new Material(Shader.Find("Unlit/Transparent")); aom.mainTexture=blobT; aom.renderQueue=1950; ao.GetComponent<Renderer>().sharedMaterial=aom; ao.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;
  var rg=GameObject.CreatePrimitive(PrimitiveType.Quad); rg.name=nm+"_Ring"; UnityEngine.Object.DestroyImmediate(rg.GetComponent<Collider>()); rg.transform.position=new Vector3(p.x,0.06f,p.z); rg.transform.localEulerAngles=new Vector3(90f,0f,0f); rg.transform.localScale=new Vector3(2.6f,2.6f,1f); var rgm=new Material(Shader.Find("Unlit/Transparent")); rgm.mainTexture=ringT; rgm.color=ringCol; rgm.renderQueue=1955; rg.GetComponent<Renderer>().sharedMaterial=rgm; rg.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;
  sb.AppendLine(nm+" x"+s.ToString("F2")+" @cell("+cx+","+cy+") rends="+rends.Length+" animator="+(combatCtrl!=null));
  return go;
};

// ---- LIVE surface: spawn one actor per token by SLOT (registry default-on-miss) ------------------
string surfJson="";
try { surfJson=new System.Net.WebClient().DownloadString("http://127.0.0.1:8765/combat-surface?campaign="+CID); } catch (System.Exception e) { return "surface GET failed: "+e.Message; }
var root=MiniJson.Parse(surfJson) as System.Collections.Generic.Dictionary<string,object>;
if(root==null) return "surface parse failed";
var toks=root.ContainsKey("tokens")?(root["tokens"] as System.Collections.Generic.List<object>):null;
if(toks==null||toks.Count==0) return "no tokens on surface";
// sweep prior actors/overlays (deterministic rerun) — same class list paint_combat_v1 sweeps.
{ var _toKill=new System.Collections.Generic.List<GameObject>();
  foreach(var g in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None)){ if(g==null) continue; var gn=g.name; if(gn.StartsWith("Actor_")||gn.EndsWith("_AO")||gn.EndsWith("_Ring")||gn.EndsWith("_Core")||gn.StartsWith("ImpactFX")||gn.StartsWith("DmgNum")||gn.StartsWith("Occluder_")) _toKill.Add(g); }
  foreach(var g in _toKill){ if(g!=null) UnityEngine.Object.DestroyImmediate(g); } }

// asset registry (identical inline resolver to paint_combat_v1.cs).
System.Collections.Generic.Dictionary<string,object> regAssets=null, regDefaults=null, regAliases=null;
{ var _rp="/home/unity/worldos-unity/registry.json"; if(System.IO.File.Exists(_rp)){ var _rr=MiniJson.Parse(System.IO.File.ReadAllText(_rp)) as System.Collections.Generic.Dictionary<string,object>; if(_rr!=null){ regAssets=_rr.ContainsKey("assets")?_rr["assets"] as System.Collections.Generic.Dictionary<string,object>:null; regDefaults=_rr.ContainsKey("defaults")?_rr["defaults"] as System.Collections.Generic.Dictionary<string,object>:null; regAliases=_rr.ContainsKey("aliases")?_rr["aliases"] as System.Collections.Generic.Dictionary<string,object>:null; } } }
System.Func<string,string> slugify=(str)=>{ if(string.IsNullOrEmpty(str)) return ""; var _b=new System.Text.StringBuilder(); foreach(char ch in str.ToLower()){ if(char.IsLetterOrDigit(ch)) _b.Append(ch); else if(_b.Length>0 && _b[_b.Length-1]!='-') _b.Append('-'); } return _b.ToString().Trim('-'); };
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

// token id (== engine actor_fk) -> {go, cell, team}. Both the spawn cell (for glide origin) and the
// live GameObject are kept so beats can pose/move/flinch the actor the envelope names.
var actorGo=new System.Collections.Generic.Dictionary<string,GameObject>();
var actorCell=new System.Collections.Generic.Dictionary<string,int[]>();
var actorFoe=new System.Collections.Generic.Dictionary<string,bool>();
// token id -> engine hpMax (from the surface), so an attack/damage beat carrying only hp_after (the
// live engine's common shape) can still compute the HP-bar fraction. Pure read of engine truth.
var _actorMaxHp=new System.Collections.Generic.Dictionary<string,int>();
int spawned=0; string celldbg="";
foreach(var o in toks){ var t=o as System.Collections.Generic.Dictionary<string,object>; if(t==null||!t.ContainsKey("x")||t["x"]==null) continue;
  int cx=System.Convert.ToInt32(t["x"]); int cy=System.Convert.ToInt32(t["y"]); string team=t["team"] as string; string nm=t["name"] as string;
  string tid=t.ContainsKey("id")?(t["id"] as string):null; if(string.IsNullOrEmpty(tid)) tid=nm;
  bool foe=(team=="foe");
  string kind=foe?"monster":"character";
  var aref=resolveAsset(slugify(nm),kind); string fbx=aref[0]; string alb=aref[1];
  float h=foe?4.2f:5.0f; Color ring=foe?new Color(1f,0.13f,0.10f,1f):new Color(0.4f,0.95f,1f,1f);
  var go=spawn(fbx,alb,cx,cy,h,ring,"Actor_"+tid);
  if(go!=null){ actorGo[tid]=go; actorCell[tid]=new int[]{cx,cy}; actorFoe[tid]=foe;
    bool skinned=false; foreach(var r in go.GetComponentsInChildren<Renderer>()){ if(r is SkinnedMeshRenderer){ skinned=true; break; } } actorSkinned[tid]=skinned; }
  if(t.ContainsKey("hpMax") && t["hpMax"]!=null){ try { _actorMaxHp[tid]=System.Convert.ToInt32(t["hpMax"]); } catch {} }
  spawned++; celldbg+=" "+nm+"("+team+")@"+cx+","+cy;
}
if(missingActor){ sb.AppendLine("ABORT — a required actor prefab was missing (no reel written)"); return sb.ToString(); }
sb.AppendLine("LIVE "+CID+": spawned "+spawned+" actors:"+celldbg);

// engine-confirmed lastPath (presentation glide route; [] == straight-line). List of [x,y] cells.
var lastPath=root.ContainsKey("lastPath")?(root["lastPath"] as System.Collections.Generic.List<object>):null;
System.Func<int[][]> parsePath=()=>{ if(lastPath==null) return new int[0][]; var outp=new System.Collections.Generic.List<int[]>(); foreach(var pc in lastPath){ var cell=pc as System.Collections.Generic.List<object>; if(cell==null||cell.Count<2) continue; outp.Add(new int[]{System.Convert.ToInt32(cell[0]),System.Convert.ToInt32(cell[1])}); } return outp.ToArray(); };
int[][] pathCells=parsePath();

// VFX slash sprite via registry effect slot (default-on-miss = fx_default_slash). We resolve the
// PATH but paint a procedural burst (same look as paint_combat_v1) so the reel never blocks on a
// missing PNG import; a real vfx_slash.png swap is a registry edit, zero renderer change.
string fxPath="Assets/painterly/vfx/vfx_slash.png";
{ if(regAssets!=null && regAssets.ContainsKey("fx_default_slash")){ var fa=regAssets["fx_default_slash"] as System.Collections.Generic.Dictionary<string,object>; if(fa!=null && fa.ContainsKey("model_ref") && fa["model_ref"] is string) fxPath=(string)fa["model_ref"]; } }
var fxTex=AssetDatabase.LoadAssetAtPath<Texture2D>(fxPath);
if(fxTex==null){ fxTex=new Texture2D(128,128,TextureFormat.RGBA32,false); var px=new Color[128*128]; float c=63.5f; for(int y=0;y<128;y++)for(int x=0;x<128;x++){ float d=Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c))/c; float a=Mathf.Clamp01(1f-d); px[y*128+x]=new Color(1f,0.62f,0.16f,a*a); } fxTex.SetPixels(px); fxTex.Apply(); }

// ---- the Action-Replay envelope from /events (SORT BY seq; idempotent; pure projection) ----------
var beats=new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string,object>>();
try {
  var evJson=new System.Net.WebClient().DownloadString("http://127.0.0.1:8765/events?campaign="+CID+"&since=0");
  var evRoot=MiniJson.Parse(evJson) as System.Collections.Generic.Dictionary<string,object>;
  var entries=(evRoot!=null && evRoot.ContainsKey("entries"))?evRoot["entries"] as System.Collections.Generic.List<object>:null;
  if(entries!=null) foreach(var e in entries){ var ed=e as System.Collections.Generic.Dictionary<string,object>; if(ed==null) continue;
    // only records with a verb are animated beats; the enriched /events feed sets verb on combat rows.
    if(!ed.ContainsKey("verb")||!(ed["verb"] is string)) continue;
    beats.Add(ed);
  }
} catch (System.Exception e) { sb.AppendLine("events GET failed: "+e.Message+" (falling back to STATIC frame)"); }
// stable sort by seq (envelope's total-order guarantee); records with no seq keep arrival order.
beats.Sort((a,b)=>{ int sa=a.ContainsKey("seq")?System.Convert.ToInt32(a["seq"]):-1; int sb2=b.ContainsKey("seq")?System.Convert.ToInt32(b["seq"]):-1; return sa.CompareTo(sb2); });
sb.AppendLine("envelope: "+beats.Count+" animated beats (verb-bearing), lastPath cells="+pathCells.Length);

// ---- capture rig ---------------------------------------------------------------------------------
int W=1920,Hh=Mathf.RoundToInt(1920f*(float)bdTex.height/bdTex.width);
var rt=new RenderTexture(W,Hh,24,RenderTextureFormat.ARGB32); rt.Create();
string OUTDIR="/home/unity/worldos-unity/Captures-Durable"; System.IO.Directory.CreateDirectory(OUTDIR);
int reelIdx=0; var reel=new System.Collections.Generic.List<string>();
System.Action<string> capture=(label)=>{
  float pa=cam.aspect; var pt=cam.targetTexture; cam.targetTexture=rt; cam.aspect=(float)W/Hh; cam.Render();
  var pAct=RenderTexture.active; RenderTexture.active=rt; var t2=new Texture2D(W,Hh,TextureFormat.RGB24,false); t2.ReadPixels(new Rect(0,0,W,Hh),0,0); t2.Apply(); RenderTexture.active=pAct; cam.targetTexture=pt; cam.aspect=pa;
  string fn=OUTDIR+"/replay_"+reelIdx.ToString("D2")+"_"+label+".png"; System.IO.File.WriteAllBytes(fn, t2.EncodeToPNG()); UnityEngine.Object.DestroyImmediate(t2); reel.Add(fn); reelIdx++;
};

// facing: rotate an actor about world-up so it looks at a target world pos (heading), preserving the
// stood-up -90 X. The base facing is cam.yaw+180; add the yaw delta from base-forward to the target.
System.Action<GameObject,Vector3> faceAt=(go,targetPos)=>{ if(go==null) return; Vector3 d=targetPos-go.transform.position; d.y=0f; if(d.sqrMagnitude<0.0001f) return; float yaw=Mathf.Atan2(d.x,d.z)*Mathf.Rad2Deg; go.transform.rotation=Quaternion.Euler(-90f, yaw, 0f); };

// TRANSFORM-motion beats (CombatBeatDriver-proven) for SKINNED actors whose clips would flatten them.
// These read as motion without touching the skin pose: a forward LUNGE (attack), a knockback+tilt
// (flinch). Presentation-only, applied to the actor + its AO/ring rig; restored to the cell each beat.
System.Action<GameObject,Vector3,float> lungeToward=(go,targetPos,frac)=>{ if(go==null) return; Vector3 d=targetPos-go.transform.position; d.y=0f; if(d.sqrMagnitude<0.0001f) return; d=d.normalized; float keepYaw=go.transform.eulerAngles.y; go.transform.position+=d*frac; go.transform.rotation=Quaternion.Euler(-90f,keepYaw,0f); };
System.Action<GameObject,GameObject,float> knockTilt=(go,fromGo,dist)=>{ if(go==null) return; Vector3 dir=(fromGo!=null)?(go.transform.position-fromGo.transform.position):Vector3.back; dir.y=0f; if(dir.sqrMagnitude<0.0001f) dir=Vector3.back; dir=dir.normalized; float keepYaw=go.transform.eulerAngles.y; go.transform.position+=dir*dist; go.transform.rotation=Quaternion.Euler(-90f,keepYaw,18f); };

// pose an actor's Animator to a clip (by verb) at a normalized time — the deterministic editor
// sampling AnimFrameCapture.cs uses (Play + Update(0), NO Play mode). CRITICAL: the Meshy/Generic
// moveset clips bake ROOT curves (the FBX Y-up->Z-up stand-up lives in the root), so a raw
// Play+Update REWRITES the actor root and topples/flattens the stood-up -90 actor (owner-observed
// "goblin lying on its side"). We SNAPSHOT the root's stood-up rotation + grounded XZ BEFORE sampling
// and RESTORE them AFTER Update(0): the clip still poses the CHILD bones (the real animation), but the
// body stays upright + on its cell. Y is re-grounded from the posed bounds so the feet stay on 0.
System.Action<GameObject,string,float> poseClip=(go,clipName,nt)=>{ if(go==null) return; var anim=go.GetComponentInChildren<Animator>();
  // Restore to the CANONICAL stand-up (Euler(-90, yaw, 0)), NOT to "whatever the root was before this
  // pose" — a prior beat's clip may have already flattened the root, and snapshotting that flat state
  // would keep it flat forever (owner-observed: the goblin stayed on its side across the whole reel).
  // We keep only the actor's current YAW (its facing) and force the -90 X + 0 Z stand-up back.
  float keepYaw=go.transform.eulerAngles.y; Vector3 keepPos=go.transform.position;
  if(anim!=null && anim.runtimeAnimatorController!=null){ anim.Play(clipName,-1,Mathf.Clamp01(nt)); anim.Update(0f); }
  else { var clip=clipByName(clipName); if(clip!=null) clip.SampleAnimation(go, Mathf.Clamp01(nt)*clip.length); }
  // restore the stood-up root transform (canonical -90 X + kept yaw + XZ); re-ground Y from posed bounds.
  go.transform.rotation=Quaternion.Euler(-90f,keepYaw,0f); go.transform.position=new Vector3(keepPos.x,go.transform.position.y,keepPos.z);
  var rends=go.GetComponentsInChildren<Renderer>(); if(rends.Length>0){ Bounds b=new Bounds(go.transform.position,Vector3.zero); bool a=false; foreach(var r in rends){ var smr=r as SkinnedMeshRenderer; Bounds rb; if(smr!=null){ var bk=new Mesh(); smr.BakeMesh(bk); var vs=bk.vertices; if(vs.Length>0){ var m=smr.transform.localToWorldMatrix; rb=new Bounds(m.MultiplyPoint3x4(vs[0]),Vector3.zero); for(int i=1;i<vs.Length;i++) rb.Encapsulate(m.MultiplyPoint3x4(vs[i])); } else rb=r.bounds; UnityEngine.Object.DestroyImmediate(bk); } else rb=r.bounds; if(!a){b=rb;a=true;} else b.Encapsulate(rb); } go.transform.position+=new Vector3(0f,-b.min.y,0f); }
};

// verb -> clip name on CombatActor.controller (no walk clip exists -> move uses idle + glide).
System.Func<string,string,string> verbToClip=(verb,hint)=>{
  if(verb=="attack") return "attack";
  if(verb=="cast") return "cast";
  if(verb=="damage"||verb=="condition") return "hit";  // flinch = the hit clip on the STRUCK actor
  if(verb=="death") return "death";
  if(verb=="move_to_zone") return "idle";               // locomotion is the DOTween glide (no walk clip)
  return "idle";
};

// float a "-N" / "+N" damage/heal number over a token (TextMesh, camera-facing) — G1, folded in.
System.Action<Vector3,string,Color> floatNum=(atPos,text,col)=>{ var g=new GameObject("DmgNum"); g.transform.position=atPos+new Vector3(0f,3.7f,0f); g.transform.rotation=cam.transform.rotation; var tm=g.AddComponent<TextMesh>(); tm.text=text; tm.fontSize=90; tm.characterSize=0.22f; tm.anchor=TextAnchor.MiddleCenter; tm.alignment=TextAlignment.Center; tm.color=col; var tmr=g.GetComponent<MeshRenderer>(); if(tmr!=null && tmr.sharedMaterial!=null) tmr.sharedMaterial.renderQueue=3100; };

// draw/update an HP bar above a token from the engine result (hp_after/hp_max) — G1 HP-bar drop.
System.Action<string,GameObject,float> hpBar=(tid,go,frac)=>{ if(go==null) return; var bn="HP_"+tid; var old=GameObject.Find(bn); if(old!=null) UnityEngine.Object.DestroyImmediate(old); frac=Mathf.Clamp01(frac);
  var root2=new GameObject(bn); Vector3 wp=go.transform.position+new Vector3(0f,5.6f,0f); root2.transform.position=wp; root2.transform.rotation=cam.transform.rotation;
  System.Action<string,float,float,Color,int> bar=(sfx,w,cxo,col,q)=>{ var q2=GameObject.CreatePrimitive(PrimitiveType.Quad); q2.name=bn+sfx; UnityEngine.Object.DestroyImmediate(q2.GetComponent<Collider>()); q2.transform.SetParent(root2.transform,false); q2.transform.localScale=new Vector3(w,0.35f,1f); q2.transform.localPosition=new Vector3(cxo,0f,0f); var m=new Material(Shader.Find("Unlit/Color")); m.color=col; m.renderQueue=q; q2.GetComponent<Renderer>().sharedMaterial=m; q2.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; };
  float full=3.2f; bar("_bg",full,0f,new Color(0.08f,0.03f,0.03f,1f),3080); bar("_fg",full*frac,-full*(1f-frac)/2f,new Color(0.85f,0.15f,0.12f,1f),3090);
};
// remember each actor's live HP fraction so a bar persists across frames until the next damage beat.
var hpFrac=new System.Collections.Generic.Dictionary<string,float>();

// spawn a VFX burst at a world cell (the STRUCK actor's engine cell), camera-facing. ADDITIVE blend:
// the registry slash PNG (fx_default_slash) is authored on an OPAQUE-BLACK bg with no alpha, which an
// Unlit/Transparent material rendered as a black square (owner-observed). Under ADDITIVE blending
// (SrcAlpha One) black adds nothing -> only the bright slash reads, so the effect is correct for both
// the registry PNG and the procedural fallback. This is a one-material-per-run additive shader.
Shader _addShader=null;
{ string addSrc="Shader \"WorldOS/VfxAdditive\" {\n Properties { _MainTex(\"T\",2D)=\"white\"{} _Tint(\"Tint\",Color)=(1,1,1,1) }\n SubShader {\n Tags{ \"Queue\"=\"Transparent\" \"RenderType\"=\"Transparent\" }\n Blend SrcAlpha One\n ZWrite Off Cull Off\n Pass {\n CGPROGRAM\n #pragma vertex vert\n #pragma fragment frag\n #include \"UnityCG.cginc\"\n sampler2D _MainTex; float4 _MainTex_ST; fixed4 _Tint;\n struct v2f { float4 pos:SV_POSITION; float2 uv:TEXCOORD0; };\n v2f vert(appdata_base v){ v2f o; o.pos=UnityObjectToClipPos(v.vertex); o.uv=TRANSFORM_TEX(v.texcoord,_MainTex); return o; }\n fixed4 frag(v2f i):SV_Target { fixed4 c=tex2D(_MainTex,i.uv)*_Tint; return c; }\n ENDCG\n }\n }\n}\n"; _addShader=UnityEditor.ShaderUtil.CreateShaderAsset(addSrc); }
System.Action<Vector3,float> vfxAt=(atPos,scale)=>{ var fx=GameObject.CreatePrimitive(PrimitiveType.Quad); fx.name="ImpactFX"; UnityEngine.Object.DestroyImmediate(fx.GetComponent<Collider>()); fx.transform.position=atPos+new Vector3(0f,2.0f,0f); fx.transform.rotation=cam.transform.rotation; fx.transform.localScale=new Vector3(scale,scale,1f); var fxm=new Material(_addShader); fxm.mainTexture=fxTex; fxm.SetColor("_Tint",new Color(1f,0.85f,0.55f,1f)); fxm.renderQueue=3000; fx.GetComponent<Renderer>().sharedMaterial=fxm; fx.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; };
System.Action clearOverlays=()=>{ foreach(var g in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None)){ if(g==null) continue; if(g.name.StartsWith("ImpactFX")||g.name.StartsWith("DmgNum")) UnityEngine.Object.DestroyImmediate(g); } };

// result helpers (pure reads of engine-decided values).
System.Func<System.Collections.Generic.Dictionary<string,object>,int> dmgOf=(result)=>{ if(result==null||!result.ContainsKey("damage")) return 0; var d=result["damage"] as System.Collections.Generic.Dictionary<string,object>; if(d==null||!d.ContainsKey("total")) return 0; try { return System.Convert.ToInt32(d["total"]); } catch { return 0; } };
System.Func<System.Collections.Generic.Dictionary<string,object>,string,int> intOf=(result,key)=>{ if(result==null||!result.ContainsKey(key)||result[key]==null) return -1; try { return System.Convert.ToInt32(result[key]); } catch { return -1; } };
System.Func<System.Collections.Generic.Dictionary<string,object>,string> outcomeOf=(result)=>{ if(result==null||!result.ContainsKey("outcome")) return ""; return result["outcome"] as string ?? ""; };

// initial resting HP bars, then the opening frame. Static (non-skinned) actors settle into a clean
// idle pose; SKINNED actors stay in their bind stand-up (any clip sample flattens them).
foreach(var kv in actorGo){ if(!(actorSkinned.ContainsKey(kv.Key)&&actorSkinned[kv.Key])) poseClip(kv.Value,"idle",0.25f); hpFrac[kv.Key]=1f; hpBar(kv.Key,kv.Value,1f); }
capture("00_open");

// ---- DRAIN the envelope in seq order, one beat at a time, capturing peaks -> the animated reel ----
if(beats.Count==0){
  // No live envelope (e.g. tunnel not carrying /events yet): still prove the animated poses render by
  // sampling attack/hit peaks on the two front actors — a STATIC-fallback reel, clearly labelled.
  string atk=null, tgt=null; foreach(var kv in actorFoe){ if(!kv.Value && atk==null) atk=kv.Key; if(kv.Value && tgt==null) tgt=kv.Key; }
  if(atk!=null && tgt!=null){ faceAt(actorGo[atk], actorGo[tgt].transform.position); faceAt(actorGo[tgt], actorGo[atk].transform.position);
    poseClip(actorGo[atk],"attack",0.45f); capture("01_attack_fallback");
    poseClip(actorGo[tgt],"hit",0.35f); var tp=actorGo[tgt].transform.position; vfxAt(tp,3.4f); floatNum(tp,"-8",new Color(1f,0.95f,0.45f,1f)); hpBar(tgt,actorGo[tgt],0.45f); capture("02_hit_fallback"); clearOverlays();
    poseClip(actorGo[tgt],"death",0.8f); capture("03_death_fallback"); }
  sb.AppendLine("NOTE: no live /events envelope — rendered a STATIC-FALLBACK animated reel (poses proven; wire the tunnel's /events for a live-driven reel).");
} else {
  foreach(var beat in beats){
    string verb=beat["verb"] as string; string hint=beat.ContainsKey("anim_hint")?(beat["anim_hint"] as string):"";
    string actor=beat.ContainsKey("actor_fk")?(beat["actor_fk"] as string):null;
    string target=beat.ContainsKey("target_fk")?(beat["target_fk"] as string):null;
    var result=beat.ContainsKey("result")?(beat["result"] as System.Collections.Generic.Dictionary<string,object>):null;
    int seq=beat.ContainsKey("seq")?System.Convert.ToInt32(beat["seq"]):-1;
    clearOverlays();
    GameObject aGo=(actor!=null && actorGo.ContainsKey(actor))?actorGo[actor]:null;
    GameObject tGo=(target!=null && actorGo.ContainsKey(target))?actorGo[target]:null;

    if(verb=="attack"){
      if(aGo==null) continue;
      if(tGo!=null){ faceAt(aGo,tGo.transform.position); faceAt(tGo,aGo.transform.position); }
      // a miss still swings — VFX default-on-miss (the slash reads even when the roll whiffs).
      // Skinned Meshy attackers LUNGE (their clips flatten); the static hero clip-poses the swing.
      if(actorSkinned.ContainsKey(actor)&&actorSkinned[actor] && tGo!=null){ lungeToward(aGo,tGo.transform.position,0.9f); }
      else poseClip(aGo,"attack",0.45f);
      if(tGo!=null){
        vfxAt(tGo.transform.position,3.2f);
        // The LIVE engine often FOLDS damage into the attack beat's result (result.damage/hp_after),
        // rather than emitting a separate `damage` beat (the fixture's shape). When it does, flinch the
        // target + float the G1 number + drop its HP bar in this same beat (pure reads of engine truth).
        int dmg=dmgOf(result); int hpa=intOf(result,"hp_after"); int hpm=intOf(result,"hp_max");
        if(dmg>0){ if(actorSkinned.ContainsKey(target)&&actorSkinned[target]) knockTilt(tGo,aGo,0.5f); else poseClip(tGo,"hit",0.35f); floatNum(tGo.transform.position,"-"+dmg,new Color(1f,0.95f,0.45f,1f)); }
        if(hpa>=0){ int max=(hpm>0)?hpm:_actorMaxHp.ContainsKey(target)?_actorMaxHp[target]:0; if(max>0){ hpFrac[target]=Mathf.Clamp01((float)hpa/max); hpBar(target,tGo,hpFrac[target]); } }
      }
      capture("attack_s"+seq);
    } else if(verb=="cast"){
      if(aGo==null) continue;
      if(tGo!=null) faceAt(aGo,tGo.transform.position);
      if(!(actorSkinned.ContainsKey(actor)&&actorSkinned[actor])) poseClip(aGo,"cast",0.5f);  // skinned casters stay upright (clip flattens); the pulse+number carry the read
      if((hint=="heal_pulse"||outcomeOf(result)=="heal") && tGo!=null){ int amt=intOf(result,"amount"); if(amt<0) amt=intOf(result,"heal"); int hpa=intOf(result,"hp_after"); int hpm=intOf(result,"hp_max"); if(hpa>=0&&hpm>0){ hpFrac[target]=(float)hpa/hpm; hpBar(target,tGo,hpFrac[target]); } floatNum(tGo.transform.position, amt>0?("+"+amt):"heal", new Color(0.5f,1f,0.55f,1f)); }
      capture("cast_s"+seq);
    } else if(verb=="damage"){
      if(tGo==null) continue;
      if(actorSkinned.ContainsKey(target)&&actorSkinned[target]) knockTilt(tGo,aGo,0.5f); else poseClip(tGo,"hit",0.35f);  // flinch on the STRUCK actor
      int dmg=dmgOf(result); Vector3 tp=tGo.transform.position;
      vfxAt(tp,3.4f);                                   // impact VFX at the struck engine cell
      if(dmg>0) floatNum(tp,"-"+dmg,new Color(1f,0.95f,0.45f,1f));
      int hpa=intOf(result,"hp_after"); int hpm=intOf(result,"hp_max"); int max=(hpm>0)?hpm:(_actorMaxHp.ContainsKey(target)?_actorMaxHp[target]:0); if(hpa>=0&&max>0){ hpFrac[target]=Mathf.Clamp01((float)hpa/max); } hpBar(target,tGo,hpFrac.ContainsKey(target)?hpFrac[target]:1f);
      capture("damage_s"+seq);
    } else if(verb=="condition"){
      if(tGo==null) continue;
      if(actorSkinned.ContainsKey(target)&&actorSkinned[target]) knockTilt(tGo,aGo,0.25f); else poseClip(tGo,"hit",0.25f);
      int hpa=intOf(result,"hp_after"); int hpm=intOf(result,"hp_max"); int cmax=(hpm>0)?hpm:(_actorMaxHp.ContainsKey(target)?_actorMaxHp[target]:0); if(hpa>=0&&cmax>0){ hpFrac[target]=Mathf.Clamp01((float)hpa/cmax); hpBar(target,tGo,hpFrac[target]); }
      capture("condition_s"+seq);
    } else if(verb=="death"){
      // topple + fade the dier (envelope's death beat). actor_fk is usually the dier; else target_fk.
      GameObject dGo=aGo!=null?aGo:tGo; string dId=(aGo!=null)?actor:target;
      if(dGo==null) continue;
      // topple: pitch ~80deg about the camera-right axis + squish + sink (CombatBeatDriver's death shape).
      Vector3 baseE=dGo.transform.eulerAngles; dGo.transform.rotation=Quaternion.Euler(baseE.x, baseE.y, 80f); Vector3 bs=dGo.transform.localScale; dGo.transform.localScale=new Vector3(bs.x,bs.y*0.4f,bs.z); dGo.transform.position+=new Vector3(0f,-0.4f,0f);
      // fade the mesh (alpha -> low). Standard mat: switch to transparent-ish tint.
      foreach(var r in dGo.GetComponentsInChildren<Renderer>()){ var m=r.sharedMaterial; if(m!=null && m.HasProperty("_Color")){ var c2=m.color; c2.a=0.35f; m.color=c2; } }
      if(dId!=null){ var hb=GameObject.Find("HP_"+dId); if(hb!=null) UnityEngine.Object.DestroyImmediate(hb); }
      capture("death_s"+seq);
    } else if(verb=="move_to_zone"){
      // DOTween-style GLIDE along the engine-confirmed lastPath (presentation-only; the renderer never
      // predicts a route — pathCells IS the engine's committed last_move_path). We evaluate the glide at
      // a couple of normalized t along the polyline and capture, so the reel reads as a WALK, not a pop.
      bool aSkin=actor!=null&&actorSkinned.ContainsKey(actor)&&actorSkinned[actor];
      if(aGo==null || pathCells.Length<2){ if(aGo!=null && !aSkin){ poseClip(aGo,"idle",0.25f); } capture("move_s"+seq); }
      else {
        // move the actor's whole AO/ring rig with it: helper to place actor + its overlays at a world pos.
        System.Action<Vector3> placeActor=(wp)=>{ var d0=aGo.transform.position; aGo.transform.position=new Vector3(wp.x,aGo.transform.position.y,wp.z); var aoG=GameObject.Find(aGo.name+"_AO"); if(aoG!=null) aoG.transform.position=new Vector3(wp.x,0.04f,wp.z); var rgG=GameObject.Find(aGo.name+"_Ring"); if(rgG!=null) rgG.transform.position=new Vector3(wp.x,0.06f,wp.z); };
        // total polyline length (in cell-steps) for even-speed sampling.
        System.Func<float,Vector3> along=(tt)=>{ int segs=pathCells.Length-1; float f=Mathf.Clamp01(tt)*segs; int si=Mathf.Min((int)f,segs-1); float sf=f-si; var a0=pathCells[si]; var a1=pathCells[si+1]; return Vector3.Lerp(cellToWorldF(a0[0],a0[1]),cellToWorldF(a1[0],a1[1]),sf); };
        // face along the heading (start->end) + idle-glide (no walk clip exists in the moveset).
        var startW=cellToWorldF(pathCells[0][0],pathCells[0][1]); var endW=cellToWorldF(pathCells[pathCells.Length-1][0],pathCells[pathCells.Length-1][1]);
        faceAt(aGo,endW); if(!aSkin) poseClip(aGo,"idle",0.3f);
        placeActor(startW); capture("move_s"+seq+"a");           // depart
        placeActor(along(0.5f)); faceAt(aGo,endW); capture("move_s"+seq+"b"); // mid-glide
        placeActor(endW); capture("move_s"+seq+"c");             // arrive (engine cell)
      }
    } else {
      // save/check/travel/narrate/unknown -> accept-and-ignore (no beat), per the envelope contract.
      continue;
    }
  }
}
clearOverlays();
capture("99_final");

rt.Release(); UnityEngine.Object.DestroyImmediate(rt);
sb.AppendLine("REEL "+reel.Count+" frames -> "+OUTDIR+"/replay_*.png hidden="+hidden);
foreach(var f in reel) sb.AppendLine("  "+f);
// Persist the scene (anti render-and-forget) to a DEDICATED scene file — never a shared/source scene.
try { var _scn=UnityEngine.SceneManagement.SceneManager.GetActiveScene(); System.IO.Directory.CreateDirectory("Assets/Scenes"); UnityEditor.SceneManagement.EditorSceneManager.SaveScene(_scn, "Assets/Scenes/CombatReplayV1_canonical.unity"); sb.AppendLine("scene SAVED -> Assets/Scenes/CombatReplayV1_canonical.unity"); } catch(System.Exception _e){ sb.AppendLine("SaveScene FAILED: "+_e.Message); }
return sb.ToString();
