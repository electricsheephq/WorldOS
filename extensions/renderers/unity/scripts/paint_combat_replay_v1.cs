// paint_combat_replay_v1.cs — #1303 S2: /events Action-Replay -> Animator/VFX wiring.
//
// SIBLING to paint_combat_v1.cs (the proven LIVE single-frame /combat-surface painter). This driver
// makes turn-based combat READ AS MOTION: it consumes the engine's Action-Replay envelope from
// /events ({seq, actor_fk, verb, target_fk, result, anim_hint}, docs/roadmap/contracts/
// action-replay-envelope.md), sorts by seq, and plays each beat as an ANIMATED presentation beat on
// the SAME actors paint_combat_v1 spawns — verb->motion (attack lunge / hit flinch / death topple),
// DOTween-style glide along the engine's lastPath, VFX at the struck engine cell (registry
// default-on-miss), and the proven G1 damage-number + HP-bar drop folded into the animated timeline.
//
// INVARIANT (the one this whole surface exists for): the engine is the SOLE WRITER. This is a PURE,
// READ-ONLY projection — every field except anim_hint is engine-decided (an FK or an outcome the
// engine already rolled/applied); we read `result` to choose numbers/states to SHOW and NEVER
// recompute. anim_hint is advisory (unknown hint -> generic beat). The glide is presentation-only: it
// traverses only ENGINE-CONFIRMED paths (surface.lastPath), never a client-predicted route.
//
// CAPTURE MODEL: a `code execute` snippet runs synchronously in the editor (no Play mode, mirroring
// AnimFrameCapture.cs / paint_3d_spike.cs). We build the scene once, then step the beat timeline
// deterministically: for each beat we apply the verb's TRANSFORM MOTION (lunge/knockback/topple) and
// facing, evaluate the glide position along lastPath at t, place the VFX/damage/HP overlays, and render
// a durable PNG per captured frame -> an animated REEL. No coroutine, no wall-clock, so a rerun is
// byte-deterministic. (Why transform motion and not live clips: see the animation-strategy NOTE below.)
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

// ---- #1284 grounding v2: per-scene FLOOR plane + presentation-only prop-cell NUDGE ----------------
// (1) FLOOR-Y grounding: the painterly plate is FLAT, so the floor is a per-scene CONSTANT (default 0),
//     NOT a raycast against prop meshes (the sarcophagus-top-wins-the-cast bug). Feet anchor to FLOOR_Y.
// (2) PROP-CELL nudge: the /combat-surface carries `impassable` (== combat.grid_impassable). If an actor's
//     LOGICAL cell is impassable (a prop, e.g. the sarcophagus), we RENDER it at the nearest walkable
//     adjacent cell so it never stands ON the painted prop. Presentation-only — the logical cell (kept in
//     actorCell) is NEVER mutated and NEVER written back: the engine stays SOLE WRITER.
// (3) RING under feet: the AO blob + selection ring anchor to this same render foot cell (below).
float FLOOR_Y=0f;
var _impass=new System.Collections.Generic.HashSet<int>();
System.Func<int,int,int> cellKey=(c,r)=> c*10000+r;                       // grids are <14x11 -> collision-free
System.Func<int,int,bool> inBounds=(c,r)=> c>=0 && c<(int)_gridCols && r>=0 && r<(int)_gridRows;
System.Func<int,int,bool> isWalkable=(c,r)=> inBounds(c,r) && !_impass.Contains(cellKey(c,r));
// #1284 H2: cross-actor stacking guard. Render cells claimed by an EARLIER-spawned actor join the blocked
// set so two actors never render on one cell. Filled per actor in the token loop (deterministic order).
var _occupied=new System.Collections.Generic.HashSet<int>();
System.Func<int,int,bool> available=(c,r)=> isWalkable(c,r) && !_occupied.Contains(cellKey(c,r));
// nearest AVAILABLE neighbor, DETERMINISTIC lowest-index pick: orthogonal (dist 1) before diagonal, in a
// FIXED neighbor order. Returns the logical cell unchanged if it is already free (or nothing is free).
int[][] _NB=new int[][]{ new int[]{0,-1}, new int[]{-1,0}, new int[]{1,0}, new int[]{0,1},
                          new int[]{-1,-1}, new int[]{1,-1}, new int[]{-1,1}, new int[]{1,1} };
System.Func<int,int,int[]> nudgeCell=(c,r)=>{ if(available(c,r)) return new int[]{c,r};
  foreach(var d in _NB){ int nc=c+d[0], nr=r+d[1]; if(available(nc,nr)) return new int[]{nc,nr}; }
  return new int[]{c,r}; };  // #1284 H2 fallback: every candidate blocked/occupied -> keep the logical cell (may overlap; documented)
// honest floor-contact sidecar: the ACTUAL grounded baked-mesh feet/head world points per placed actor,
// projected to px after the capture resolution is known (see the sidecar writer below the capture rig).
var _repSidecar=new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string,object>>();

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

// NOTE on animation strategy: the wave-1 cast are SKINNED Meshy/Generic rigs authored Y-up, whose
// moveset clips bake a near-prone bind pose. Driving a live Animator (anim.Play+Update) in the editor's
// SYNCHRONOUS multi-actor capture DESYNCS the GPU skin from the CPU bones (BakeMesh reads an upright
// 12.9/16-tall pose, but the rendered mesh reverts to its prone bind — a per-actor render artifact that
// reproduces only when a skinned actor is transform-mutated inside the capture loop; the proven static
// renderer shows the same FBXs UPRIGHT). So this driver renders actors in their CLEAN UPRIGHT BIND POSE
// (no live controller) and conveys each beat's "which animation" via TRANSFORM MOTION — lunge (swing),
// knockback (flinch), topple (death) — plus VFX-at-cell + damage number + HP-bar. All engine-driven
// and deterministic. (The FLAG in the report tracks the residual skinned-render artifact for a Play-mode
// or bake-to-static follow-up; see build_combat_animator.cs / CombatActor.controller for the clip lane.)

// ---- actor spawner (spawn geometry IDENTICAL to paint_combat_v1.cs, PLUS an Animator so the actor
//      can play verb clips). Returns the actor root GO so the beat player can pose/glide/flinch it.
bool missingActor=false;
System.Func<string,string,int,int,float,Color,string,GameObject> spawn=(fbxPath,albedoPath,cx,cy,height,ringCol,nm)=>{
  var prefab=AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath); if(prefab==null){ sb.AppendLine("MISSING "+fbxPath); missingActor=true; return null; }
  var old=GameObject.Find(nm); if(old!=null) UnityEngine.Object.DestroyImmediate(old);
  // Spawn in the PROVEN paint_combat_v1.cs form: direct FBX instantiate, -90 X stand-up, NO live Animator
  // controller. WHY no controller: these Meshy/Generic rigs are authored Y-up and their clips BAKE a
  // near-prone bind — and driving a skinned Animator synchronously (anim.Play+Update) in the editor
  // capture DESYNCS the GPU skin from the CPU bones (BakeMesh reads an upright 12.9-tall pose, but the
  // rendered mesh reverts to its prone bind — owner-observed "goblin on its side", verified vs the proven
  // static renderer which shows BOTH actors UPRIGHT with no controller). So the actor renders in its clean
  // upright bind pose and the beat is conveyed by TRANSFORM MOTION (lunge / knockback / topple, the
  // CombatBeatDriver approach) + VFX + damage number + HP-bar — all of which read as motion, deterministically.
  var go=(GameObject)UnityEngine.Object.Instantiate(prefab); go.name=nm;
  go.transform.rotation=Quaternion.Euler(-90f, cam.transform.eulerAngles.y+180f, 0f);
  var rends=go.GetComponentsInChildren<Renderer>(); foreach(var r in rends){ r.enabled=true; r.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.On; r.receiveShadows=true;
    // Force the skinned mesh to re-skin from its LIVE bone transforms on every render + regardless of
    // culling bounds. Without this, the editor's synchronous multi-actor capture can render an actor's
    // STALE (prone, Y-up bind) GPU skin even though its CPU bones are upright (BakeMesh height 16, but the
    // pixels show prone — owner-observed). These two flags are the exact fix for that GPU/CPU skin desync.
    var smrF=r as SkinnedMeshRenderer; if(smrF!=null){ smrF.updateWhenOffscreen=true; smrF.forceMatrixRecalculationPerRender=true; } }
  // Grounding via BakeMesh -> true posed world bounds (SkinnedMeshRenderer.bounds is an inflated AABB
  // whose min.y floats the actor) — IDENTICAL to paint_combat_v1.cs.
  System.Func<Renderer,Bounds> worldBounds=(r)=>{ var smr=r as SkinnedMeshRenderer; if(smr==null) return r.bounds; var bk=new Mesh(); smr.BakeMesh(bk); var vs=bk.vertices; if(vs.Length==0){ UnityEngine.Object.DestroyImmediate(bk); return r.bounds; } var m=smr.transform.localToWorldMatrix; var wb=new Bounds(m.MultiplyPoint3x4(vs[0]),Vector3.zero); for(int i=1;i<vs.Length;i++) wb.Encapsulate(m.MultiplyPoint3x4(vs[i])); UnityEngine.Object.DestroyImmediate(bk); return wb; };
  System.Func<Bounds> measure=()=>{ Bounds b=new Bounds(go.transform.position,Vector3.zero); bool a=false; foreach(var r in rends){ var rb=worldBounds(r); if(!a){b=rb;a=true;} else b.Encapsulate(rb);} return b; };
  Bounds bb=measure(); float curH=bb.size.y>0.001f?bb.size.y:1f; float s=height/curH; go.transform.localScale=go.transform.localScale*s;
  // #1284 (2) prop-cell render NUDGE (presentation-only): if the LOGICAL cell (cx,cy) is impassable
  // (a prop, e.g. the sarcophagus), render at the nearest walkable neighbor. The logical cell is kept in
  // actorCell by the caller and NEVER written back (engine = sole writer).
  int[] _rc=nudgeCell(cx,cy); int rcx=_rc[0], rcy=_rc[1];
  _occupied.Add(cellKey(rcx,rcy));  // #1284 H2: claim this render cell so later actors won't stack on it
  var p=cellToWorld(rcx,rcy); go.transform.position=p; bb=measure(); Vector3 ctr=bb.center;
  // #1284 (1) FLOOR-Y grounding: anchor feet to the per-scene FLOOR plane (flat plate; NOT a prop-mesh
  // raycast). foot = the posed baked-mesh bounds min.y -> FLOOR_Y.
  go.transform.position+=new Vector3(p.x-ctr.x, FLOOR_Y-bb.min.y, p.z-ctr.z);
  bb=measure();   // re-read the grounded bounds for the honest feet/head floor-contact record
  if(albedoPath!=null){ var al=AssetDatabase.LoadAssetAtPath<Texture2D>(albedoPath); if(al!=null){ var mm=new Material(Shader.Find("Standard")); mm.mainTexture=al; mm.SetFloat("_Glossiness",0.2f); mm.SetFloat("_Metallic",0f); foreach(var r in rends) r.sharedMaterial=mm; } }
  // #1284 (3) AO blob + selection ring anchored to the RENDER foot cell (p, post-nudge) on the FLOOR
  // plane, so the ring sits UNDER the feet and never detaches onto the prop the actor was nudged off.
  var ao=GameObject.CreatePrimitive(PrimitiveType.Quad); ao.name=nm+"_AO"; UnityEngine.Object.DestroyImmediate(ao.GetComponent<Collider>()); ao.transform.position=new Vector3(p.x,FLOOR_Y+0.04f,p.z); ao.transform.localEulerAngles=new Vector3(90f,0f,0f); ao.transform.localScale=new Vector3(2.0f,2.0f,1f); var aom=new Material(Shader.Find("Unlit/Transparent")); aom.mainTexture=blobT; aom.renderQueue=1950; ao.GetComponent<Renderer>().sharedMaterial=aom; ao.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;
  var rg=GameObject.CreatePrimitive(PrimitiveType.Quad); rg.name=nm+"_Ring"; UnityEngine.Object.DestroyImmediate(rg.GetComponent<Collider>()); rg.transform.position=new Vector3(p.x,FLOOR_Y+0.06f,p.z); rg.transform.localEulerAngles=new Vector3(90f,0f,0f); rg.transform.localScale=new Vector3(2.6f,2.6f,1f); var rgm=new Material(Shader.Find("Unlit/Transparent")); rgm.mainTexture=ringT; rgm.color=ringCol; rgm.renderQueue=1955; rg.GetComponent<Renderer>().sharedMaterial=rgm; rg.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;
  // #1284 floor-contact record: feet/head are the ACTUAL grounded baked-mesh bounds (min.y/max.y). If a
  // regression ever grounds onto prop-top (raycast bug), feetW.y != FLOOR_Y and the pre-gate FAILS.
  var _sd=new System.Collections.Generic.Dictionary<string,object>();
  _sd["id"]=nm; _sd["logical_cell"]=new int[]{cx,cy}; _sd["render_cell"]=new int[]{rcx,rcy};
  _sd["feetW"]=new Vector3(bb.center.x,bb.min.y,bb.center.z);
  _sd["headW"]=new Vector3(bb.center.x,bb.max.y,bb.center.z);
  _repSidecar.Add(_sd);
  sb.AppendLine(nm+" x"+s.ToString("F2")+" logical("+cx+","+cy+")->render("+rcx+","+rcy+") rends="+rends.Length);
  return go;
};

// ---- LIVE surface: spawn one actor per token by SLOT (registry default-on-miss) ------------------
string surfJson="";
try { surfJson=new System.Net.WebClient().DownloadString("http://127.0.0.1:8765/combat-surface?campaign="+CID); } catch (System.Exception e) { return "surface GET failed: "+e.Message; }
var root=MiniJson.Parse(surfJson) as System.Collections.Generic.Dictionary<string,object>;
if(root==null) return "surface parse failed";
// #1284 (2): the impassable cell set (engine-owned combat.grid_impassable, surfaced as `impassable`) —
// the source for the presentation-only prop-cell nudge. [] == no obstacles (nudge is then a no-op).
{ var _imp=root.ContainsKey("impassable")?(root["impassable"] as System.Collections.Generic.List<object>):null;
  if(_imp!=null) foreach(var ce in _imp){ var cell=ce as System.Collections.Generic.List<object>; if(cell==null||cell.Count<2) continue; _impass.Add(cellKey(System.Convert.ToInt32(cell[0]),System.Convert.ToInt32(cell[1]))); }
  sb.AppendLine("impassable cells: "+_impass.Count); }
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
// per-beat baseline (CodeRabbit #1/#2 fix): the spawn-time position/rotation/scale, so each beat can
// RESET-then-mutate instead of accumulating lunge/knockback deltas onto an already-displaced actor.
// deadActors gates re-toppling a corpse (death beat is applied once, then later beats naming the same
// actor/target are skipped — makes death idempotent without changing the transform-motion approach).
var actorBase=new System.Collections.Generic.Dictionary<string,Vector3[]>(); // [0]=pos [1]=scale
var actorBaseYaw=new System.Collections.Generic.Dictionary<string,float>();
var deadActors=new System.Collections.Generic.HashSet<string>();
// Reset position/scale/yaw to the spawn baseline before a beat mutates them (never restores the
// pitch/roll — the -90 X stand-up pivot is a gimbal singularity; see the knockBack note below (translation only, no rotation)).
System.Action<string,GameObject> resetToBaseline=(tid,go)=>{ if(go==null||!actorBase.ContainsKey(tid)) return; var b=actorBase[tid]; go.transform.position=b[0]; go.transform.localScale=b[1]; go.transform.rotation=Quaternion.Euler(-90f, actorBaseYaw.ContainsKey(tid)?actorBaseYaw[tid]:go.transform.eulerAngles.y, 0f); };
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
  if(go!=null){ actorGo[tid]=go; actorCell[tid]=new int[]{cx,cy}; actorFoe[tid]=foe; actorBase[tid]=new Vector3[]{go.transform.position,go.transform.localScale}; actorBaseYaw[tid]=go.transform.eulerAngles.y; }
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
  // since=0 replays the FULL cumulative campaign history on every run (unbounded beat count / disk /
  // capture time on a long campaign). This capture is a synchronous one-shot with no persisted cursor
  // between runs, so the minimal fix is a client-side cap to the most recent turn's beats (below) —
  // NOT a `since` cursor redesign, which would need engine-side cursor storage this driver doesn't have.
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
// bound to the most recent turn's window (cap: last 40 beats) so a long campaign's cumulative history
// doesn't balloon the reel — this is the current-turn action-replay, not a full-campaign scrub.
const int MAX_REPLAY_BEATS=40;
if(beats.Count>MAX_REPLAY_BEATS) beats=beats.GetRange(beats.Count-MAX_REPLAY_BEATS,MAX_REPLAY_BEATS);
sb.AppendLine("envelope: "+beats.Count+" animated beats (verb-bearing, capped at "+MAX_REPLAY_BEATS+"), lastPath cells="+pathCells.Length);

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

// ---- #1284 floor-contact MANIFEST: project each placed actor's grounded feet/head to px at the CAPTURE
// resolution and emit a qa/visual_pregate.py-ready manifest (per-actor screen_bbox + independent floor_y_px
// projected at the render cell). The pre-gate's FLOOR-CONTACT check compares rendered feet-Y to that floor
// plane — this file is the deterministic #1284 acceptance tripwire (regression -> feet != floor -> FAIL). --
{
  float _pa2=cam.aspect; var _pt2=cam.targetTexture; cam.aspect=(float)W/Hh;
  System.Func<Vector3,float[]> w2p=(w)=>{ var vp=cam.WorldToViewportPoint(w); return new float[]{ vp.x*W, (1f-vp.y)*Hh }; };
  // #1284 review P3 (3541403920): the actor id/name comes from /combat-surface tokens[] — JSON-escape it
  // so a name containing '"', '\\', or a newline can never produce an invalid manifest the pre-gate can't parse.
  System.Func<object,string> _jesc=(o)=>{ var st=o==null?"":o.ToString(); return st.Replace("\\","\\\\").Replace("\"","\\\"").Replace("\n"," ").Replace("\r"," "); };
  var msb=new System.Text.StringBuilder();
  msb.Append("{\n  \"frame_w\":"+W+", \"frame_h\":"+Hh+",\n");
  msb.Append("  \"checks\": {\"floor_contact\": {\"tolerance_px\": 8}, \"screen_scale\": {\"min_height_frac\":0.03,\"max_height_frac\":0.45}},\n");
  msb.Append("  \"actors\": [\n");
  for(int i=0;i<_repSidecar.Count;i++){ var d=_repSidecar[i];
    var lc=(int[])d["logical_cell"]; var rc2=(int[])d["render_cell"];
    var fW=(Vector3)d["feetW"]; var hW=(Vector3)d["headW"];
    var fp=w2p(fW); var hp=w2p(hW);
    var floorPx=w2p(new Vector3(fW.x,FLOOR_Y,fW.z));                       // floor plane at the render cell
    float half=Mathf.Max(4f,Mathf.Abs(fp[1]-hp[1])*0.22f);
    msb.Append("    {\"name\":\""+_jesc(d["id"])+"\",\"logical_cell\":["+lc[0]+","+lc[1]+"],\"expected_cell\":["+rc2[0]+","+rc2[1]+"],");
    msb.Append("\"screen_bbox\":["+Mathf.Round(fp[0]-half)+","+Mathf.Round(hp[1])+","+Mathf.Round(fp[0]+half)+","+Mathf.Round(fp[1])+"],");
    msb.Append("\"floor_y_px\":"+Mathf.Round(floorPx[1])+"}");
    msb.Append(i<_repSidecar.Count-1?",\n":"\n");
  }
  msb.Append("  ]\n}\n");
  System.IO.File.WriteAllText(OUTDIR+"/replay_actors_manifest.json", msb.ToString());
  cam.aspect=_pa2; cam.targetTexture=_pt2;
  sb.AppendLine("wrote floor-contact manifest -> "+OUTDIR+"/replay_actors_manifest.json ("+_repSidecar.Count+" actors)");
}

// facing: rotate an actor about world-up so it looks at a target world pos (heading), preserving the
// stood-up -90 X. The base facing is cam.yaw+180; add the yaw delta from base-forward to the target.
System.Action<GameObject,Vector3> faceAt=(go,targetPos)=>{ if(go==null) return; Vector3 d=targetPos-go.transform.position; d.y=0f; if(d.sqrMagnitude<0.0001f) return; float yaw=Mathf.Atan2(d.x,d.z)*Mathf.Rad2Deg; go.transform.rotation=Quaternion.Euler(-90f, yaw, 0f); };

// TRANSFORM-motion beats (CombatBeatDriver-proven) for SKINNED actors whose clips would flatten them.
// These read as motion without touching the skin pose: a forward LUNGE (attack), a knockback
// (flinch). Presentation-only, applied to the actor + its AO/ring rig; restored to the cell each beat.
// POSITION-ONLY translations — CRITICAL: never round-trip the pivot rotation through eulerAngles.y.
// The pivot sits at the X=-90 pitch, which is a gimbal-lock SINGULARITY; reading eulerAngles.y and
// rebuilding Euler(-90, thatY, tilt) decomposes ambiguously and can FLATTEN the actor (owner-observed:
// the goblin went prone on knockback). We only translate (+optionally roll IN LOCAL SPACE via Rotate,
// which composes on the quaternion without decomposition), so the stand-up is never corrupted.
System.Action<GameObject,Vector3,float> lungeToward=(go,targetPos,frac)=>{ if(go==null) return; Vector3 d=targetPos-go.transform.position; d.y=0f; if(d.sqrMagnitude<0.0001f) return; go.transform.position+=d.normalized*frac; };
System.Action<GameObject,GameObject,float> knockBack=(go,fromGo,dist)=>{ if(go==null) return; Vector3 dir=(fromGo!=null)?(go.transform.position-fromGo.transform.position):Vector3.back; dir.y=0f; if(dir.sqrMagnitude<0.0001f) dir=Vector3.back; go.transform.position+=dir.normalized*dist; };

// pose an actor's clip (by verb) at a normalized time — the deterministic editor sampling
// AnimFrameCapture.cs uses (Play + Update(0), NO Play mode). `go` is the PIVOT; the Animator lives on
// its child mesh, so the clip animates the rig in its native Y-up local frame while the pivot's -90 X
// stand-up holds the actor UPRIGHT (the pivot fix — no root-flatten). After posing we re-ground the
// PIVOT Y from the posed skinned bounds so the feet stay on the floor as the silhouette height changes.
// verb -> the intended moveset clip name (the DESIGN mapping this driver realizes via transform motion,
// and the exact map a Play-mode/clip driver would feed to CombatActor.controller). Documents the
// verb->clip contract even though the render path is transform-based (see the animation-strategy note).
System.Func<string,string,string> verbToClip=(verb,hint)=>{
  if(verb=="attack") return "attack";      // -> lunge toward target
  if(verb=="cast") return "cast";          // -> cast gesture / heal pulse on target
  if(verb=="damage"||verb=="condition") return "hit";  // -> knockback flinch on the STRUCK actor
  if(verb=="death") return "death";        // -> topple + squish + fade
  if(verb=="move_to_zone") return "walk";  // -> DOTween glide along lastPath (no walk clip yet -> glide)
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
foreach(var kv in actorGo){ hpFrac[kv.Key]=1f; hpBar(kv.Key,kv.Value,1f); }
capture("00_open");

// ---- DRAIN the envelope in seq order, one beat at a time, capturing peaks -> the animated reel ----
if(beats.Count==0){
  // No live envelope (e.g. tunnel not carrying /events yet): still prove the animated poses render by
  // sampling attack/hit peaks on the two front actors — a STATIC-fallback reel, clearly labelled.
  string atk=null, tgt=null; foreach(var kv in actorFoe){ if(!kv.Value && atk==null) atk=kv.Key; if(kv.Value && tgt==null) tgt=kv.Key; }
  if(atk!=null && tgt!=null){ faceAt(actorGo[atk], actorGo[tgt].transform.position); faceAt(actorGo[tgt], actorGo[atk].transform.position);
    lungeToward(actorGo[atk],actorGo[tgt].transform.position,0.9f); capture("01_attack_fallback");
    knockBack(actorGo[tgt],actorGo[atk],0.5f); var tp=actorGo[tgt].transform.position; vfxAt(tp,3.4f); floatNum(tp,"-8",new Color(1f,0.95f,0.45f,1f)); hpBar(tgt,actorGo[tgt],0.45f); capture("02_hit_fallback"); clearOverlays();
    actorGo[tgt].transform.Rotate(0f,0f,85f,Space.Self); { var ds=actorGo[tgt].transform.localScale; actorGo[tgt].transform.localScale=new Vector3(ds.x,ds.y*0.4f,ds.z); } capture("03_death_fallback"); }
  sb.AppendLine("NOTE: no live /events envelope — rendered a STATIC-FALLBACK animated reel (poses proven; wire the tunnel's /events for a live-driven reel).");
} else {
  foreach(var beat in beats){
    string verb=beat["verb"] as string; string hint=beat.ContainsKey("anim_hint")?(beat["anim_hint"] as string):"";
    string actor=beat.ContainsKey("actor_fk")?(beat["actor_fk"] as string):null;
    string target=beat.ContainsKey("target_fk")?(beat["target_fk"] as string):null;
    var result=beat.ContainsKey("result")?(beat["result"] as System.Collections.Generic.Dictionary<string,object>):null;
    int seq=beat.ContainsKey("seq")?System.Convert.ToInt32(beat["seq"]):-1;
    clearOverlays();
    // dead-actor guard (#2 fix): a toppled/faded corpse is terminal — don't let a later beat naming the
    // same actor/target re-lunge, re-knockback, or re-topple it (keeps the death mutation idempotent).
    if((actor!=null && deadActors.Contains(actor)) || (target!=null && deadActors.Contains(target))) continue;
    GameObject aGo=(actor!=null && actorGo.ContainsKey(actor))?actorGo[actor]:null;
    GameObject tGo=(target!=null && actorGo.ContainsKey(target))?actorGo[target]:null;
    // reset-then-mutate (#1 fix): restore each touched actor to its spawn baseline BEFORE this beat's
    // lunge/knockback is applied, so deltas never compound across beats (actorCell/actorBase read here,
    // not just written at spawn — closes the "no per-beat restore" gap CodeRabbit flagged).
    if(aGo!=null) resetToBaseline(actor,aGo);
    if(tGo!=null) resetToBaseline(target,tGo);

    if(verb=="attack"){
      if(aGo==null) continue;
      if(tGo!=null){ faceAt(aGo,tGo.transform.position); faceAt(tGo,aGo.transform.position); }
      // a miss still swings — VFX default-on-miss (the slash reads even when the roll whiffs). The
      // pivot fix lets the ATTACK clip play upright on every actor; a small additive LUNGE toward the
      // target adds punch (CombatBeatDriver-style) on top of the clip.
      if(tGo!=null) lungeToward(aGo,tGo.transform.position,0.9f);  // the swing = a lunge toward the target on the upright bind pose
      if(tGo!=null){
        vfxAt(tGo.transform.position,3.2f);
        // The LIVE engine often FOLDS damage into the attack beat's result (result.damage/hp_after),
        // rather than emitting a separate `damage` beat (the fixture's shape). When it does, flinch the
        // target + float the G1 number + drop its HP bar in this same beat (pure reads of engine truth).
        int dmg=dmgOf(result); int hpa=intOf(result,"hp_after"); int hpm=intOf(result,"hp_max");
        if(dmg>0){ knockBack(tGo,aGo,0.5f); floatNum(tGo.transform.position,"-"+dmg,new Color(1f,0.95f,0.45f,1f)); }  // flinch = re-assert upright idle + knockback recoil (the "hit" clip lays these Generic rigs prone)
        if(hpa>=0){ int max=(hpm>0)?hpm:_actorMaxHp.ContainsKey(target)?_actorMaxHp[target]:0; if(max>0){ hpFrac[target]=Mathf.Clamp01((float)hpa/max); hpBar(target,tGo,hpFrac[target]); } }
      }
      capture("attack_s"+seq);
    } else if(verb=="cast"){
      if(aGo==null) continue;
      if(tGo!=null) faceAt(aGo,tGo.transform.position);
      if((hint=="heal_pulse"||outcomeOf(result)=="heal") && tGo!=null){ int amt=intOf(result,"amount"); if(amt<0) amt=intOf(result,"heal"); int hpa=intOf(result,"hp_after"); int hpm=intOf(result,"hp_max"); if(hpa>=0&&hpm>0){ hpFrac[target]=(float)hpa/hpm; hpBar(target,tGo,hpFrac[target]); } floatNum(tGo.transform.position, amt>0?("+"+amt):"heal", new Color(0.5f,1f,0.55f,1f)); }
      capture("cast_s"+seq);
    } else if(verb=="damage"){
      if(tGo==null) continue;
      knockBack(tGo,aGo,0.5f);  // flinch = knockback recoil on the upright bind pose
      int dmg=dmgOf(result); Vector3 tp=tGo.transform.position;
      vfxAt(tp,3.4f);                                   // impact VFX at the struck engine cell
      if(dmg>0) floatNum(tp,"-"+dmg,new Color(1f,0.95f,0.45f,1f));
      int hpa=intOf(result,"hp_after"); int hpm=intOf(result,"hp_max"); int max=(hpm>0)?hpm:(_actorMaxHp.ContainsKey(target)?_actorMaxHp[target]:0); if(hpa>=0&&max>0){ hpFrac[target]=Mathf.Clamp01((float)hpa/max); } hpBar(target,tGo,hpFrac.ContainsKey(target)?hpFrac[target]:1f);
      capture("damage_s"+seq);
    } else if(verb=="condition"){
      if(tGo==null) continue;
      knockBack(tGo,aGo,0.3f);  // condition = a lighter knockback recoil
      int hpa=intOf(result,"hp_after"); int hpm=intOf(result,"hp_max"); int cmax=(hpm>0)?hpm:(_actorMaxHp.ContainsKey(target)?_actorMaxHp[target]:0); if(hpa>=0&&cmax>0){ hpFrac[target]=Mathf.Clamp01((float)hpa/cmax); hpBar(target,tGo,hpFrac[target]); }
      capture("condition_s"+seq);
    } else if(verb=="death"){
      // death clip + topple + fade the dier (envelope's death beat). actor_fk is usually the dier; else target_fk.
      GameObject dGo=aGo!=null?aGo:tGo; string dId=(aGo!=null)?actor:target;
      if(dGo==null) continue;
      // TOPPLE + squish + sink (CombatBeatDriver's death shape) on the upright bind pose. Rotate in LOCAL
      // space (composes on the quaternion, no eulerAngles decomposition) so the fall is deterministic.
      dGo.transform.Rotate(0f,0f,85f,Space.Self); Vector3 bs=dGo.transform.localScale; dGo.transform.localScale=new Vector3(bs.x,bs.y*0.4f,bs.z); dGo.transform.position+=new Vector3(0f,-0.4f,0f);
      // fade the mesh (alpha -> low). Standard mat: switch to transparent-ish tint.
      foreach(var r in dGo.GetComponentsInChildren<Renderer>()){ var m=r.sharedMaterial; if(m!=null && m.HasProperty("_Color")){ var c2=m.color; c2.a=0.35f; m.color=c2; } }
      if(dId!=null){ var hb=GameObject.Find("HP_"+dId); if(hb!=null) UnityEngine.Object.DestroyImmediate(hb); if(!deadActors.Contains(dId)) deadActors.Add(dId); } // terminal — no later beat re-topples this actor
      capture("death_s"+seq);
    } else if(verb=="move_to_zone"){
      // DOTween-style GLIDE along the engine-confirmed lastPath (presentation-only; the renderer never
      // predicts a route — pathCells IS the engine's committed last_move_path). We evaluate the glide at
      // a couple of normalized t along the polyline and capture, so the reel reads as a WALK, not a pop.
      if(aGo==null || pathCells.Length<2){ capture("move_s"+seq); }
      else {
        // move the actor's whole AO/ring rig with it: helper to place actor + its overlays at a world pos.
        System.Action<Vector3> placeActor=(wp)=>{ var d0=aGo.transform.position; aGo.transform.position=new Vector3(wp.x,aGo.transform.position.y,wp.z); var aoG=GameObject.Find(aGo.name+"_AO"); if(aoG!=null) aoG.transform.position=new Vector3(wp.x,0.04f,wp.z); var rgG=GameObject.Find(aGo.name+"_Ring"); if(rgG!=null) rgG.transform.position=new Vector3(wp.x,0.06f,wp.z); };
        // total polyline length (in cell-steps) for even-speed sampling.
        System.Func<float,Vector3> along=(tt)=>{ int segs=pathCells.Length-1; float f=Mathf.Clamp01(tt)*segs; int si=Mathf.Min((int)f,segs-1); float sf=f-si; var a0=pathCells[si]; var a1=pathCells[si+1]; return Vector3.Lerp(cellToWorldF(a0[0],a0[1]),cellToWorldF(a1[0],a1[1]),sf); };
        // face along the heading (start->end) + idle-glide (no walk clip exists in the moveset).
        var startW=cellToWorldF(pathCells[0][0],pathCells[0][1]); var endW=cellToWorldF(pathCells[pathCells.Length-1][0],pathCells[pathCells.Length-1][1]);
        faceAt(aGo,endW);
        placeActor(startW); capture("move_s"+seq+"a");           // depart
        placeActor(along(0.5f)); faceAt(aGo,endW); capture("move_s"+seq+"b"); // mid-glide
        placeActor(endW); capture("move_s"+seq+"c");             // arrive (engine cell)
        // re-anchor the baseline to the new engine cell so the NEXT beat's reset-to-baseline (the
        // #1 drift fix above) restores here, not back at the original spawn cell — a real move must
        // stick; only the transient lunge/knockback flinches should be reset away each beat.
        if(actorBase.ContainsKey(actor)) actorBase[actor]=new Vector3[]{aGo.transform.position,actorBase[actor][1]};
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
