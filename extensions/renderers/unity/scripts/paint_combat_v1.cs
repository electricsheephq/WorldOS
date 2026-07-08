// paint_combat_v1.cs — P0 FIRST multi-actor combat frame: hero + goblin on the painterly crypt plate,
// gold/red selection rings, contact AO, an impact VFX burst + a floating "-8" damage number.
// Built off the PROVEN paint_3d_spike.cs (same unqualified UnityEngine/UnityEditor style the wrapper injects).
// NO AnimatorController (its assembly isn't referenced by code-execute); actors are placed (pose-sampling = v2).
// #1281 (FELT): OPT-IN active-room viewport framing (frameActiveRoom, default OFF -> byte-identical). ON crops the
//   camera to the active room's grid bounds (ortho + view-axis pan ONLY; the Euler rotation contract is inviolable)
//   so multi-room plates read as a played moment, not a level-select diorama. Toggle via _frame_active_room.txt.
// Run: unity-mcp code execute --no-safety-checks -f paint_combat_v1.cs
AssetDatabase.Refresh();
// Room-agnostic plate: read the active room's plate filename from a box config (written by the seed/driver);
// default = the crypt. Lets the SAME renderer play combat on ANY generated room (tavern/church/...) by swapping
// the plate with no code edit — the modular-room analogue of the asset registry.
// #1230: pick the plate by the CURRENT engine location (a per-location plate map written by deploy_room.sh),
// so cross_door AUTO-SWAPS the rendered room with no manual re-deploy. Reads the current location.id off the
// SAME /combat-surface the renderer uses for tokens; ANY failure (no map, tunnel down, parse) falls back to
// _active_combat.txt -> today's single-room behavior (back-compat, additive). The per-location fetch is as
// reliable as the token fetch below (same endpoint).
string PLATE="crypt_firelit_v2.png"; string _locPlate="";
// grid extents for cellToWorld below: default to the 14x11-era origin (cols=14,rows=11) so an
// absent/malformed grid block reproduces today's fixed (cx-6.5,5.0-cy) transform byte-for-byte.
float _gridCols=14f, _gridRows=11f;
try {
  var _cidF="/home/unity/worldos-unity/Assets/painterly/backdrops/_active_campaign.txt";
  string _cid="camp_gfxdemo01"; if(System.IO.File.Exists(_cidF)){ var _c=System.IO.File.ReadAllText(_cidF).Trim(); if(_c.Length>0) _cid=_c; }
  var _sj=new System.Net.WebClient().DownloadString("http://127.0.0.1:8765/combat-surface?campaign="+_cid);
  var _r=MiniJson.Parse(_sj) as System.Collections.Generic.Dictionary<string,object>;
  var _loc=(_r!=null && _r.ContainsKey("location"))?_r["location"] as System.Collections.Generic.Dictionary<string,object>:null;
  var _lid=(_loc!=null && _loc.ContainsKey("id"))?_loc["id"] as string:null;
  var _mapF="/home/unity/worldos-unity/Assets/painterly/backdrops/_location_plates.json";
  if(!string.IsNullOrEmpty(_lid) && System.IO.File.Exists(_mapF)){ var _m=MiniJson.Parse(System.IO.File.ReadAllText(_mapF)) as System.Collections.Generic.Dictionary<string,object>; if(_m!=null && _m.ContainsKey(_lid)) _locPlate=_m[_lid] as string; }
  // #1318 rest-mode grids can be a non-14x11 size (e.g. forest/town at 16-19 cols x 12-15 rows);
  // read the surface's own grid block so cellToWorld places tokens on their real spawn cells
  // instead of the fixed legacy origin. Any parse miss silently keeps the 14x11 default above.
  var _grid=(_r!=null && _r.ContainsKey("grid"))?_r["grid"] as System.Collections.Generic.Dictionary<string,object>:null;
  if(_grid!=null){ if(_grid.ContainsKey("cols")) _gridCols=System.Convert.ToSingle(_grid["cols"]); if(_grid.ContainsKey("rows")) _gridRows=System.Convert.ToSingle(_grid["rows"]); }
} catch {}
if(!string.IsNullOrEmpty(_locPlate)) PLATE=_locPlate;
else { var _abs="/home/unity/worldos-unity/Assets/painterly/backdrops/_active_combat.txt"; if(System.IO.File.Exists(_abs)){ var _n=System.IO.File.ReadAllText(_abs).Trim(); if(_n.Length>0) PLATE=_n; } }
string PLATE_PATH="Assets/painterly/backdrops/"+PLATE;
// New backdrop plates default to NPOT=ToNearest, which square-distorts a 1344x768 plate and breaks the
// camera-pin aspect. Force NPOT=None so the plate keeps native dims (idempotent — only reimports if needed).
{ var _ti=AssetImporter.GetAtPath(PLATE_PATH) as TextureImporter; if(_ti!=null && _ti.npotScale!=TextureImporterNPOTScale.None){ _ti.npotScale=TextureImporterNPOTScale.None; _ti.maxTextureSize=2048; _ti.SaveAndReimport(); } }
var sb=new System.Text.StringBuilder();
// #1280 actor-integration levers (FELT gap: contact shadows weak, actors don't take scene light, stiff poses).
// ALL params are ADDITIVE. Baseline (no _actor_integration.json present) is BYTE-IDENTICAL to the pre-#1280 render —
// aiCoreShadow stays 0 (core-shadow block skipped entirely, see line ~96) and every other knob reproduces the prior
// values exactly. The moment the config file is present (opt-in), missing keys fall back to the box-validated v2
// "when-enabled" defaults (PR #1282 evidence: shadow_scale 2.4 / shadow_intensity 1.3 / core_shadow 0.85 read
// correctly at frame scale — the original v1 checklist values were too subtle to see).
// Config file (optional):
//   /home/unity/worldos-unity/Assets/painterly/backdrops/_actor_integration.json
//   { "shadow_scale":2.4, "shadow_intensity":1.3, "shadow_softness":0.9,   // grounding contact shadow (v2-validated)
//     "core_shadow":0.85, "core_scale":0.55,                               // 2nd tighter core shadow (v2-validated)
//     "light_tint":0.0, "warmth":1.0,                                      // scene-light take on actor mats (0=off)
//     "pose_yaw":0.0, "pose_time":0.0 }                                    // per-capture pose variety (0=today)
float aiShadowScale=2.0f, aiShadowIntensity=1.0f, aiShadowSoftness=0.9f, aiCoreShadow=0.0f, aiCoreScale=0.55f;
float aiLightTint=0.0f, aiWarmth=1.0f, aiPoseYaw=0.0f, aiPoseTime=0.0f;
try {
  var _aip="/home/unity/worldos-unity/Assets/painterly/backdrops/_actor_integration.json";
  if(System.IO.File.Exists(_aip)){ var _a=MiniJson.Parse(System.IO.File.ReadAllText(_aip)) as System.Collections.Generic.Dictionary<string,object>;
    if(_a!=null){ System.Func<string,float,float> gf=(k,d)=>{ if(_a.ContainsKey(k)&&_a[k]!=null){ try{ return (float)System.Convert.ToDouble(_a[k]); }catch{} } return d; };
      // Config file present -> opt-in: missing keys fall back to the v2-validated when-enabled defaults, not the
      // byte-identical-baseline defaults declared above.
      // P3 hardening (PR #1282 review): clamp operator-controlled knobs to a non-negative range so a malformed
      // JSON value (e.g. a typo'd negative shadow_scale) fails safe to 0 instead of flipping a Quad's winding
      // (negative localScale -> back-face cull) or feeding Pow() a negative exponent.
      aiShadowScale=Mathf.Max(0f,gf("shadow_scale",2.4f)); aiShadowIntensity=Mathf.Max(0f,gf("shadow_intensity",1.3f)); aiShadowSoftness=Mathf.Max(0f,gf("shadow_softness",0.9f));
      aiCoreShadow=Mathf.Max(0f,gf("core_shadow",0.85f)); aiCoreScale=Mathf.Max(0f,gf("core_scale",0.55f));
      aiLightTint=Mathf.Clamp01(gf("light_tint",aiLightTint)); aiWarmth=Mathf.Max(0f,gf("warmth",aiWarmth));
      aiPoseYaw=gf("pose_yaw",aiPoseYaw); aiPoseTime=Mathf.Clamp01(gf("pose_time",aiPoseTime));
      sb.AppendLine("actor-integration cfg: shadow(s="+aiShadowScale.ToString("F2")+",i="+aiShadowIntensity.ToString("F2")+",soft="+aiShadowSoftness.ToString("F2")+",core="+aiCoreShadow.ToString("F2")+") tint(t="+aiLightTint.ToString("F2")+",w="+aiWarmth.ToString("F2")+") pose(yaw="+aiPoseYaw.ToString("F1")+",t="+aiPoseTime.ToString("F2")+")");
    }
  }
} catch {}
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

// Origin derived from the surface's own grid extents (fetched above): (_gridCols-1)/2 and
// (_gridRows-1)/2 reproduce the prior hardcoded 6.5/5.0 EXACTLY at 14x11 (13/2=6.5, 10/2=5.0), so
// this is byte-identical on today's rooms and only shifts the origin for a differently-sized grid.
System.Func<int,int,Vector3> cellToWorld=(cx,cy)=> new Vector3((cx-(_gridCols-1f)/2f)*2.0f,0f,((_gridRows-1f)/2f-cy)*2.0f);

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
// #1280 contact shadow: the alpha falloff exponent is aiShadowSoftness (higher = softer/tighter core, lower = wider
// dark spread) and peak alpha scales by aiShadowIntensity; defaults (0.9, 1.0) reproduce the prior blobT byte-for-byte.
var blobT=new Texture2D(256,256,TextureFormat.RGBA32,false); blobT.wrapMode=TextureWrapMode.Clamp; { var px=new Color[256*256]; float c=127.5f; for(int y=0;y<256;y++)for(int x=0;x<256;x++){ float d=Mathf.Clamp01(Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c))/c); px[y*256+x]=new Color(0.02f,0.02f,0.03f,Mathf.Clamp01(Mathf.Pow(1f-d,aiShadowSoftness)*aiShadowIntensity)); } blobT.SetPixels(px); blobT.Apply(); }
// #1280 optional tighter CORE shadow: a small, denser near-black ellipse under the feet that makes the actor read as
// SITTING on the floor (the wide soft blob alone reads floaty at frame scale — the FELT panel's "weak contact shadow").
// Built ONLY when aiCoreShadow>0 (default 0 = not created, no change to the baseline frame).
Texture2D coreT=null; if(aiCoreShadow>0f){ coreT=new Texture2D(128,128,TextureFormat.RGBA32,false); coreT.wrapMode=TextureWrapMode.Clamp; var px=new Color[128*128]; float c=63.5f; for(int y=0;y<128;y++)for(int x=0;x<128;x++){ float d=Mathf.Clamp01(Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c))/c); px[y*128+x]=new Color(0.01f,0.01f,0.015f,Mathf.Clamp01(Mathf.Pow(1f-d,1.6f)*aiCoreShadow)); } coreT.SetPixels(px); coreT.Apply(); }
var ringT=new Texture2D(256,256,TextureFormat.RGBA32,false); ringT.wrapMode=TextureWrapMode.Clamp; { var px=new Color[256*256]; float c=127.5f; for(int y=0;y<256;y++)for(int x=0;x<256;x++){ float d=Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c))/c; float a=(d>0.78f&&d<0.93f)?1f:0f; px[y*256+x]=new Color(1f,1f,1f,a); } ringT.SetPixels(px); ringT.Apply(); }

// actor spawner (generalizes the spike's hero block): load fbx, stand up, scale to height, place at cell, foot-snap, albedo, AO, ring.
bool missingActor=false;
// #1408 (ports #1392's replay-lane grounding to this REST/combat-still driver): feet anchor to a
// per-scene FLOOR-Y CONSTANT (default 0), NOT a raycast against prop meshes — IDENTICAL semantics to
// paint_combat_replay_v1.cs's FLOOR_Y. _repSidecar carries each placed actor's grounded feet/head/baked
// verts so the manifest writer below (after the token loop) can emit a qa/visual_pregate.py-ready
// per-actor manifest (real projected screen_bbox — #1397's fix for the pose-uprightness pre-gate's
// blind spot — not a synthesized formula).
float FLOOR_Y=0f;
var _repSidecar=new System.Collections.Generic.List<System.Collections.Generic.Dictionary<string,object>>();
// capture resolution decided HERE (not at the tail) so the manifest projection below and the final
// RenderTexture capture share the exact same W/Hh — mirrors the replay's ordering.
int W=1920,Hh=Mathf.RoundToInt(1920f*(float)bdTex.height/bdTex.width);
// #1408 humanoid idle RETARGET donor: a clipless cast (registry anim_ref FBX not yet generated for this
// asset — the "cast variety" asset lane is separate, #1408) leaves poseClipPath's SampleAnimation a
// no-op below, so the actor renders its raw bind pose (T-pose for this Meshy humanoid rig family).
// Resolve the donor FBX from the registry's OWN "goblin" entry (self-contained read, mirroring this
// file's PLATE resolution above rather than depending on the resolveAsset block declared further down)
// so a future donor swap is a registry edit, zero renderer edit; goblin.fbx is also this file's existing
// hardcoded monster-default fallback. goblin.fbx carries its OWN embedded Idle clip on a HUMANOID avatar
// (#1397-confirmed: upright, not the prone/tilted bind). Loaded ONCE, reused for every clipless actor.
string _donorFbx="Assets/chars_v2/goblin/goblin.fbx";
try { var _rp2="/home/unity/worldos-unity/registry.json"; if(System.IO.File.Exists(_rp2)){ var _rr2=MiniJson.Parse(System.IO.File.ReadAllText(_rp2)) as System.Collections.Generic.Dictionary<string,object>; var _as2=(_rr2!=null&&_rr2.ContainsKey("assets"))?_rr2["assets"] as System.Collections.Generic.Dictionary<string,object>:null; var _gob=(_as2!=null&&_as2.ContainsKey("goblin"))?_as2["goblin"] as System.Collections.Generic.Dictionary<string,object>:null; if(_gob!=null && _gob.ContainsKey("model_ref") && _gob["model_ref"] is string) _donorFbx=(string)_gob["model_ref"]; } } catch {}
AnimationClip _donorIdleClip=null; bool _donorIdleTried=false;
System.Func<AnimationClip> loadDonorIdle=()=>{
  if(_donorIdleTried) return _donorIdleClip; _donorIdleTried=true;
  foreach(var _a in AssetDatabase.LoadAllAssetsAtPath(_donorFbx)){ var _cl=_a as AnimationClip; if(_cl==null||_cl.name.StartsWith("__")) continue; if(_cl.name.ToLower().Contains("idle")){ _donorIdleClip=_cl; break; } if(_donorIdleClip==null) _donorIdleClip=_cl; }
  return _donorIdleClip;
};
System.Func<string,string,string,int,int,float,Color,string,Vector3> spawn=(fbxPath,albedoPath,poseClipPath,cx,cy,height,ringCol,nm)=>{
  var prefab=AssetDatabase.LoadAssetAtPath<GameObject>(fbxPath); if(prefab==null){ sb.AppendLine("MISSING "+fbxPath); missingActor=true; return cellToWorld(cx,cy); }
  var old=GameObject.Find(nm); if(old!=null) UnityEngine.Object.DestroyImmediate(old);
  var go=(GameObject)UnityEngine.Object.Instantiate(prefab); go.name=nm;
  // #1280: aiPoseYaw adds a per-capture yaw offset so actors can face slightly off-axis onto a more readable
  // silhouette (default 0 = the prior fixed facing).
  // #1397 (pixel-bbox CONFIRMED on GEX44, Assets/Editor/Probe1397Pixel.cs + Probe1397Fighter.cs): the
  // "-90 X stand-up" pitch is a LEGACY Z-up correction. This whole cast is authored Y-up
  // (registry.json gen_recipe: "meshy --moveset (Y-up)") — applying -90X to an already-upright Y-up
  // pose tips it onto its back. Measured via rendered PIXEL bbox: goblin.fbx (Humanoid avatar)
  // pitch=-90 -> aspect 1.12 PRONE vs pitch=0 -> 1.31-1.35 UPRIGHT; fighter.fbx (NO Animator/avatar at
  // all, but SkinnedMeshRenderer-rigged) pitch=0 -> 1.65 UPRIGHT vs pitch=-90 -> 1.39, confirming it is
  // ALSO Y-up despite not being Humanoid-classified. So the guard is "is this a skinned Meshy Y-up
  // rig at all" (SkinnedMeshRenderer present), not "is this Humanoid" — -90 is kept only for a
  // genuinely static/non-skinned mesh (no rig to be mis-pitched). MOVED ahead of posing (#1418): pitch
  // depends only on rig type, never on the sampled pose, so it can be set immediately after Instantiate.
  { float _pitchX=go.GetComponentInChildren<SkinnedMeshRenderer>()!=null?0f:-90f;
    go.transform.rotation=Quaternion.Euler(_pitchX, cam.transform.eulerAngles.y+180f+aiPoseYaw, 0f); }
  var rends=go.GetComponentsInChildren<Renderer>(); foreach(var r in rends){ r.enabled=true; r.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.On; r.receiveShadows=true;
    // #1408 (ports #1392's replay-lane fix): force the skinned mesh to re-skin from its LIVE bone
    // transforms on every render + regardless of culling bounds — IDENTICAL to paint_combat_replay_v1.cs.
    // Without this, the editor's synchronous multi-actor capture can render an actor's STALE GPU skin
    // even though its CPU bones (and the retarget/SampleAnimation bake above) are already posed correctly.
    var smrF=r as SkinnedMeshRenderer; if(smrF!=null){ smrF.updateWhenOffscreen=true; smrF.forceMatrixRecalculationPerRender=true; } }
  // Grounding/scale uses TRUE posed geometry. SkinnedMeshRenderer.bounds is a conservative/inflated culling AABB whose
  // min.y sits BELOW the real feet -> grounding to min.y=0 leaves the actor FLOATING (owner-observed "goblin walking
  // in the air"). BakeMesh snapshots the ACTUAL posed verts (renderer-local space); transform by localToWorldMatrix
  // for an exact world min.y/center. Plain MeshRenderer.bounds are already accurate, so pass those through.
  // #1412 (found while re-rendering the full wave-2 cast): BakeMesh's output ALREADY reflects the renderer's
  // CURRENT lossyScale (measured empirically on Unity 6000.5.1f1 — bind-pose bounds re-baked after a runtime
  // localScale change grow by scale^2, not scale, when multiplied by the FULL localToWorldMatrix below). Any
  // actor whose spawn-time scale multiplier != 1 (i.e. every SkinnedMeshRenderer actor, since `s=height/curH`
  // is almost never exactly 1) double-applies scale -> a wildly inflated bbox -> the actor is placed floating
  // and oversized (measured on mage/patron_commoner/innkeeper: postGround bbox height 13-20 world units vs the
  // intended 5.0). FIX: drop scale from the matrix used to place the ALREADY-scaled baked verts — position +
  // rotation only. (Static, non-skinned actors like hero.fbx use `r.bounds`, which is unaffected — that path's
  // grounding was already correct, which is why only the wave-2 skinned cast exposed this.)
  System.Func<Renderer,Bounds> worldBounds=(r)=>{ var smr=r as SkinnedMeshRenderer; if(smr==null) return r.bounds; var bk=new Mesh(); smr.BakeMesh(bk); var vs=bk.vertices; if(vs.Length==0){ UnityEngine.Object.DestroyImmediate(bk); return r.bounds; } var m=Matrix4x4.TRS(smr.transform.position, smr.transform.rotation, Vector3.one); var wb=new Bounds(m.MultiplyPoint3x4(vs[0]),Vector3.zero); for(int i=1;i<vs.Length;i++) wb.Encapsulate(m.MultiplyPoint3x4(vs[i])); UnityEngine.Object.DestroyImmediate(bk); return wb; };
  System.Func<Bounds> measure=()=>{ Bounds b=new Bounds(go.transform.position,Vector3.zero); bool a=false; foreach(var r in rends){ var rb=worldBounds(r); if(!a){b=rb;a=true;} else b.Encapsulate(rb);} return b; };
  // #1418 FIX: `curH` used to be the full-mesh AABB height of whatever pose the actor landed in AFTER
  // clip-posing/retargeting. A WIDE/leaning/forward-hunched idle first frame (measured: innkeeper bbox
  // aspect 0.85 "prone/tilted") has a SMALLER Y-extent than a clean standing pose, which forced
  // s=height/curH UP to compensate -> the whole actor over-scaled (measured 55-72% frame height vs the
  // 3-45% pre-gate band). Of the 3 fix directions the issue proposed, direction 1 is used here: measure
  // the BIND POSE height — right here, BEFORE any clip is sampled or donor-retargeted — then apply the
  // idle pose for the visual only AFTER scale is locked. A Meshy-generated rig's bind pose is a
  // conventional upright rest stance (by construction of the gen pipeline), so it's a far more reliable
  // "standing height" reference than an arbitrary idle clip's first frame, for every actor in this cast.
  // REJECTED alternative (direction 2, tried + measured on this box, do NOT re-attempt): a fixed
  // head-to-foot BONE-PAIR height (Animator.GetBoneTransform(Head/LeftFoot/RightFoot)) sampled from the
  // POSED skeleton. It sounded pose-invariant but empirically made every actor WORSE, not better (e.g.
  // innkeeper 71.6%->110% of frame height) — a genuine forward lean/hunch drops the head bone's world Y
  // by roughly the SAME amount it drops the AABB's max.y (both track the same skeletal rotation), so the
  // bone pair inherits the exact defect it was meant to dodge, with no compensating benefit.
  Bounds bb=measure(); float curH=bb.size.y>0.001f?bb.size.y:1f;
  float s=height/curH; go.transform.localScale=go.transform.localScale*s;
  // ---- pose to a NEUTRAL IDLE stance for the VISUAL, now that scale is locked from the bind pose ----
  // A skinned actor with no clip sampled sits in its FBX bind pose, which for gen'd meshes (Meshy goblin)
  // is often a dynamic action pose -> reads as "unstable/floating" even when grounded (owner-observed).
  // PREFER a clip named 'idle' (fall back to the first real clip); sample at mid-clip for a settled frame.
  // Static meshes (hero.fbx, no clips) are untouched. Grounding re-measures AFTER this.
  // #1280 pose variety: sample the pose clip at aiPoseTime (0..1 of clip length) instead of always f0, so captures can
  // land on a readable ACTION-pose peak (the FELT panel's "stiff mid-leap / static" note) rather than one frozen frame.
  // Default aiPoseTime=0 reproduces the prior @f0 sampling exactly.
  bool posedByClip=false;
  if(poseClipPath!=null){ var pas=AssetDatabase.LoadAllAssetsAtPath(poseClipPath); AnimationClip pick=null; foreach(var clipAsset in pas){ var clip=clipAsset as AnimationClip; if(clip==null||clip.name.StartsWith("__")) continue; if(clip.name.ToLower().Contains("idle")){ pick=clip; break; } if(pick==null) pick=clip; } if(pick!=null){ float _pt=Mathf.Clamp01(aiPoseTime)*pick.length; pick.SampleAnimation(go, _pt); sb.AppendLine(nm+" posed by "+pick.name+"@t"+_pt.ToString("F2")); posedByClip=true; } }
  // #1408 humanoid idle RETARGET for clipless casts (issue #1408 item 2): the above no-ops when
  // poseClipPath has no usable embedded clip (the anim_ref moveset FBX not yet generated for this
  // asset). If this actor's own Animator carries a HUMANOID avatar, sample the DONOR idle clip
  // (goblin.fbx's embedded Idle) onto THIS avatar via a one-shot PlayableGraph evaluate — Unity's
  // cross-skeleton Humanoid retargeting works because both rigs are avatar-classified Humanoid, even
  // though their skeletons differ. No Play mode, no persistent controller asset, no live ticking after
  // the bake (graph is destroyed immediately) — same one-time-pose-then-static discipline as the
  // SampleAnimation call above. Non-humanoid / already clip-posed actors are untouched.
  if(!posedByClip){
    var _anim=go.GetComponentInChildren<Animator>();
    if(_anim!=null && _anim.avatar!=null && _anim.avatar.isHuman){
      var _donor=loadDonorIdle();
      if(_donor!=null){
        // A one-shot PlayableGraph evaluate — Unity's cross-skeleton Humanoid retargeting: both rigs
        // are avatar-classified Humanoid, so a donor clip authored on a DIFFERENT skeleton still maps
        // onto this actor's own avatar. SetSourcePlayable is an EXTENSION method (UnityEngine.Playables.
        // PlayableOutputExtensions) — called via its fully-qualified STATIC form (not `.` sugar) so it
        // resolves with NO `using UnityEngine.Playables;` needed in this wrapped body. (AnimationPlayableUtilities,
        // the higher-level one-liner, isn't referenced by this project's assemblies — CS0234, tried first.)
        var _graph=UnityEngine.Playables.PlayableGraph.Create("HumanoidIdleRetarget_"+nm);
        var _clipPlayable=UnityEngine.Animations.AnimationClipPlayable.Create(_graph,_donor);
        var _outp=UnityEngine.Animations.AnimationPlayableOutput.Create(_graph,"Output",_anim);
        UnityEngine.Playables.PlayableOutputExtensions.SetSourcePlayable(_outp,_clipPlayable);
        _graph.Evaluate(0f);
        _graph.Destroy();
        sb.AppendLine(nm+" clipless humanoid -> retargeted donor idle ("+_donor.name+")");
      } else { sb.AppendLine(nm+" clipless humanoid but NO donor idle clip found — bind pose kept"); }
    }
  }
  sb.AppendLine(nm+" #1418 curH from BIND POSE="+curH.ToString("F2")+" -> scale x"+s.ToString("F2"));
  // ground + CENTER on the cell: snap feet to Y=0 AND align bounds-center X/Z to the cell (fixes the critic's
  // "actor decoupled from its ring" — meshes whose geometry is offset from their transform origin drifted off-ring).
  var p=cellToWorld(cx,cy); go.transform.position=p; bb=measure(); Vector3 ctr=bb.center;
  // #1408 (#1392 port): anchor feet to FLOOR_Y (a per-scene constant; NOT a raycast against prop
  // meshes) — IDENTICAL to paint_combat_replay_v1.cs's grounding.
  go.transform.position+=new Vector3(p.x-ctr.x, FLOOR_Y-bb.min.y, p.z-ctr.z);
  bb=measure();   // re-read the grounded bounds for the honest feet/head floor-contact record (mirrors replay)
  // #1408 item 3: record this actor's grounded feet/head + baked world verts for the post-loop manifest
  // writer (real projected screen_bbox, per #1397's fix) — render_cell==logical_cell here (this driver
  // has no #1284 prop-cell nudge, unlike the replay lane), documented honestly rather than faked.
  { var _sd=new System.Collections.Generic.Dictionary<string,object>();
    _sd["id"]=nm; _sd["logical_cell"]=new int[]{cx,cy}; _sd["render_cell"]=new int[]{cx,cy};
    _sd["feetW"]=new Vector3(bb.center.x,bb.min.y,bb.center.z);
    _sd["headW"]=new Vector3(bb.center.x,bb.max.y,bb.center.z);
    var _vertsW=new System.Collections.Generic.List<Vector3>();
    foreach(var rv in rends){ var smrV=rv as SkinnedMeshRenderer; if(smrV==null) continue; var bkV=new Mesh(); smrV.BakeMesh(bkV); var vsV=bkV.vertices; var mV=smrV.transform.localToWorldMatrix; foreach(var vv in vsV) _vertsW.Add(mV.MultiplyPoint3x4(vv)); UnityEngine.Object.DestroyImmediate(bkV); }
    _sd["vertsW"]=_vertsW;
    _repSidecar.Add(_sd); }
  if(albedoPath!=null){ var al=AssetDatabase.LoadAssetAtPath<Texture2D>(albedoPath); if(al!=null){ var mm=new Material(Shader.Find("Standard")); mm.mainTexture=al; mm.SetFloat("_Glossiness",0.2f); mm.SetFloat("_Metallic",0f);
    // #1280 scene-light take: the FELT panel read actors as "flatly/differently lit — don't sit in the scene light".
    // Approximate "lit by the scene" WITHOUT re-lighting geometry: nudge the albedo tint toward the plate's warm
    // KEY (aiWarmth>1 = warmer) and add a faint warm emission rim on the key side so the actor picks up the firelight.
    // aiLightTint is the 0..1 blend strength; default 0 leaves _Color=white / no emission -> baseline material unchanged.
    if(aiLightTint>0f){ float k=Mathf.Clamp01(aiLightTint);
      Color warm=new Color(1f, Mathf.Clamp01(0.86f/Mathf.Max(0.6f,aiWarmth)), Mathf.Clamp01(0.66f/Mathf.Max(0.6f,aiWarmth*aiWarmth)), 1f);
      mm.SetColor("_Color", Color.Lerp(Color.white, warm, k*0.6f));
      mm.EnableKeyword("_EMISSION"); mm.globalIlluminationFlags=MaterialGlobalIlluminationFlags.RealtimeEmissive;
      mm.SetColor("_EmissionColor", new Color(1f,0.55f,0.22f,1f)*(k*0.18f)); }
    foreach(var r in rends) r.sharedMaterial=mm; } }
  var oldAo=GameObject.Find(nm+"_AO"); if(oldAo!=null) UnityEngine.Object.DestroyImmediate(oldAo);
  var oldRg=GameObject.Find(nm+"_Ring"); if(oldRg!=null) UnityEngine.Object.DestroyImmediate(oldRg);
  // AO blob + selection ring are UNIFORM ground circles (equal X/Z), laid FLAT on the ground plane; the orthographic
  // 30deg-pitch / 45deg-yaw camera foreshortens a true circle into the correct clean 2:1 dimetric ellipse aligned to
  // the view. (Prior code pre-squished them anisotropically in WORLD X/Z (2.7x1.7); because the camera is yawed 45deg
  // its foreshortening runs along the world DIAGONAL, so a world-axis pre-squish produced the skewed oval that did not
  // match the view -- owner-observed "the ring is off, like an oval, not matching the camera". Let the camera do it.)
  // #1280: contact-shadow footprint scales with aiShadowScale (default 2.0 = unchanged). A larger, softer blob spreads
  // the grounding AO so the actor reads planted, not pasted.
  var ao=GameObject.CreatePrimitive(PrimitiveType.Quad); ao.name=nm+"_AO"; UnityEngine.Object.DestroyImmediate(ao.GetComponent<Collider>()); ao.transform.position=new Vector3(p.x,FLOOR_Y+0.04f,p.z); ao.transform.localEulerAngles=new Vector3(90f,0f,0f); ao.transform.localScale=new Vector3(aiShadowScale,aiShadowScale,1f); var aom=new Material(Shader.Find("Unlit/Transparent")); aom.mainTexture=blobT; aom.renderQueue=1950; ao.GetComponent<Renderer>().sharedMaterial=aom; ao.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;
  // #1280: optional tighter CORE shadow directly under the feet (only when coreT was built, i.e. aiCoreShadow>0).
  if(coreT!=null){ var coreGo=GameObject.Find(nm+"_Core"); if(coreGo!=null) UnityEngine.Object.DestroyImmediate(coreGo);
    var core=GameObject.CreatePrimitive(PrimitiveType.Quad); core.name=nm+"_Core"; UnityEngine.Object.DestroyImmediate(core.GetComponent<Collider>()); core.transform.position=new Vector3(p.x,FLOOR_Y+0.05f,p.z); core.transform.localEulerAngles=new Vector3(90f,0f,0f); core.transform.localScale=new Vector3(aiShadowScale*aiCoreScale,aiShadowScale*aiCoreScale,1f); var cm=new Material(Shader.Find("Unlit/Transparent")); cm.mainTexture=coreT; cm.renderQueue=1951; core.GetComponent<Renderer>().sharedMaterial=cm; core.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; }
  var rg=GameObject.CreatePrimitive(PrimitiveType.Quad); rg.name=nm+"_Ring"; UnityEngine.Object.DestroyImmediate(rg.GetComponent<Collider>()); rg.transform.position=new Vector3(p.x,FLOOR_Y+0.06f,p.z); rg.transform.localEulerAngles=new Vector3(90f,0f,0f); rg.transform.localScale=new Vector3(2.6f,2.6f,1f); var rgm=new Material(Shader.Find("Unlit/Transparent")); rgm.mainTexture=ringT; rgm.color=ringCol; rgm.renderQueue=1955; rg.GetComponent<Renderer>().sharedMaterial=rgm; rg.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;
  sb.AppendLine(nm+" x"+s.ToString("F2")+" @cell("+cx+","+cy+") rends="+rends.Length);
  return go.transform.position;
};

// LIVE engine combat-surface (engine = SOLE WRITER; this renderer is READ-ONLY — positions come from the engine cells).
// CID is configurable (mirrors the active-plate config), so ANY room's campaign drives the render — not just
// the crypt demo. deploy_room.sh writes _active_campaign.txt alongside _active_combat.txt.
string CID="camp_gfxdemo01"; { var _ac="/home/unity/worldos-unity/Assets/painterly/backdrops/_active_campaign.txt"; if(System.IO.File.Exists(_ac)){ var _c=System.IO.File.ReadAllText(_ac).Trim(); if(_c.Length>0) CID=_c; } }
string surfJson="";
try { surfJson=new System.Net.WebClient().DownloadString("http://127.0.0.1:8765/combat-surface?campaign="+CID); } catch (System.Exception e) { return "surface GET failed: "+e.Message; }
var root=MiniJson.Parse(surfJson) as System.Collections.Generic.Dictionary<string,object>;
if(root==null) return "surface parse failed";
// W1 (#1318) SCENE-AT-REST: when the surface's additive `stage` block says mode:"rest" and carries
// tokens, this renderer paints the room AT REST — party + present NPCs at their spawn cells in idle
// poses — instead of a combat board. In combat mode stage.tokens is [] (the engine guarantees no
// double-paint), so we fall through to the authoritative top-level `tokens`. `restMode` gates the
// idle-pose default below. Absent `stage` (an old surface) -> combat path, today's behavior.
bool restMode=false;
var toks=root.ContainsKey("tokens")?(root["tokens"] as System.Collections.Generic.List<object>):null;
{ var stage=root.ContainsKey("stage")?(root["stage"] as System.Collections.Generic.Dictionary<string,object>):null;
  if(stage!=null){ string smode=stage.ContainsKey("mode")?stage["mode"] as string:null;
    var stoks=stage.ContainsKey("tokens")?(stage["tokens"] as System.Collections.Generic.List<object>):null;
    if(smode=="rest" && stoks!=null && stoks.Count>0){ toks=stoks; restMode=true; } } }
if(toks==null||toks.Count==0) return "no tokens on surface";
// sweep prior actors/overlays so a moved/removed token never leaves a stale instance (deterministic rerun).
// COLLECT then destroy with null-checks: destroying an actor root also destroys its children still in the
// FindObjectsByType array, so a single-loop destroy would access a destroyed child (Unity throws).
{ var _toKill=new System.Collections.Generic.List<GameObject>();
  foreach(var g in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None)){ if(g==null) continue; var gn=g.name; if(gn.StartsWith("Actor_")||gn.EndsWith("_AO")||gn.EndsWith("_Ring")||gn.EndsWith("_Core")||gn=="ImpactFX"||gn=="DmgNum"||gn.StartsWith("Occluder_")) _toKill.Add(g); }
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
// Returns [model_ref, albedo_ref, anim_ref] — anim_ref is the registry's OWN idle/moveset clip
// (e.g. hero@moveset.fbx), separate from the mesh (#1318 thread: rest mode must sample idle from
// anim_ref, not from the mesh fbx itself). "" (not null) when the registry has no anim_ref for
// this asset (e.g. the static hero.fbx entry, which documents no clips) so the caller can fall
// back to the prior mesh-as-poseClip behavior for those assets.
System.Func<string,string,string[]> resolveAsset=(slug,kind)=>{
  string fbxDef=kind=="monster"?"Assets/chars_v2/goblin/goblin.fbx":"Assets/painterly/models/hero.fbx";
  string albDef=kind=="monster"?"Assets/chars_v2/goblin/albedo.png":"Assets/painterly/models/hero_albedo.png";
  if(regAssets==null) return new string[]{fbxDef,albDef,""};
  string id=slug; bool exactOrAlias=regAssets.ContainsKey(id);
  if(!exactOrAlias && regAliases!=null && regAliases.ContainsKey(id)){ id=regAliases[id] as string; exactOrAlias = id!=null && regAssets.ContainsKey(id); }
  if(!exactOrAlias && regDefaults!=null){ if(regDefaults.ContainsKey(kind)) id=regDefaults[kind] as string; else if(regDefaults.ContainsKey("__any__")) id=regDefaults["__any__"] as string; }
  if(id!=null && regAssets.ContainsKey(id)){ var a=regAssets[id] as System.Collections.Generic.Dictionary<string,object>; if(a!=null){ string m=a.ContainsKey("model_ref")?a["model_ref"] as string:null; string al=a.ContainsKey("albedo_ref")?a["albedo_ref"] as string:null; string an=a.ContainsKey("anim_ref")?a["anim_ref"] as string:null;
    // #1423 FIX: only substitute the DEFAULT TEMPLATE's albedo when this token fell through to a template
    // default (no exact/alias asset row exists for it). A REAL resolved asset row (exact id or alias hit)
    // whose own albedo_ref is null/empty means "use this model's own imported material, no override" (the
    // registry's documented convention, e.g. fighter before #1423 extracted its albedo) -- silently painting
    // an UNRELATED default mesh's texture (mismatched UVs) onto a real, distinct asset is WORSE than leaving
    // its native material: this exact substitution produced the garbled "camo" read on the fighter (#1423).
    string alOut = string.IsNullOrEmpty(al) ? (exactOrAlias ? null : albDef) : al;
    return new string[]{ string.IsNullOrEmpty(m)?fbxDef:m, alOut, an??"" }; } }
  return new string[]{fbxDef,albDef,""};
};
foreach(var o in toks){ var t=o as System.Collections.Generic.Dictionary<string,object>; if(t==null||!t.ContainsKey("x")||t["x"]==null) continue;
  int cx=System.Convert.ToInt32(t["x"]); int cy=System.Convert.ToInt32(t["y"]); string team=t["team"] as string; string nm=t["name"] as string;
  string tid=t.ContainsKey("id")?(t["id"] as string):null; if(string.IsNullOrEmpty(tid)) tid=nm;
  bool foe=(team=="foe");
  string kind=foe?"monster":"character";
  var aref=resolveAsset(slugify(nm),kind); string fbx=aref[0]; string alb=aref[1]; string anim=aref.Length>2?aref[2]:"";
  // #1418 calibration: the character target height was 5.0 (vs monster's 4.2) — measured on THIS
  // camera/frame (ortho=13, 1920x1097) with the #1418 bind-pose curH fix already applied, a
  // character token STILL rendered 50-68% of frame height (well over the 45% screen-scale gate) at
  // BOTH a near-camera cell (rest fixture, row 9) and a mid-board cell (combat calibration check,
  // row 6) — proving the residual overscale is NOT the near-camera #1403 framing gap (out of scope,
  // not re-litigated here) but a plain height-constant miscalibration: these wave-2/wave-3 Meshy
  // rigs import at a realistic human-metric bind-pose scale (curH ~1.3-1.9), so scaling them UP to
  // "5.0 world units" overshoots badly, unlike the older hero.fbx this constant was tuned against.
  // 3.2 is calibrated against the SHORTEST/most-compact bind pose in the current cast (innkeeper,
  // curH=1.32, the worst case) so every character in this cast clears the 45% gate with margin
  // (measured: 32-44% across fighter/mage/patron_commoner/innkeeper) while staying comfortably
  // in scale with the already-passing monster/goblin height (4.2, 41.5%).
  float h=foe?4.2f:3.2f; Color ring=foe?new Color(1f,0.13f,0.10f,1f):new Color(0.4f,0.95f,1f,1f);
  // poseClip: pass the fbx to auto-pose to a NEUTRAL IDLE (spawn prefers an 'idle' clip).
  // #1397 (probe-ladder CONFIRMED on GEX44, Assets/Editor/Probe1397.cs): the RAW BIND POSE this
  // combat path previously rendered (poseClipPath left null, on the theory bind was "the most
  // proportionate placeholder") measures PRONE/TILTED — goblin.fbx world-bounds aspect (vert/horiz)
  // 0.67 — while the SAME fbx's embedded Idle clip sampled at t=0 measures UPRIGHT (aspect 1.32).
  // That prior assumption is falsified by the measurement, so combat mode now poses to Idle on
  // EVERY spawn, same as restMode already did. This is a ONE-TIME SampleAnimation bake at spawn (no
  // live Animator component, no Update() loop), so the flagged GPU/CPU skin desync from driving a
  // live controller during a synchronous multi-actor capture (paint_combat_replay_v1.cs's note on
  // this) does not apply — grounding via BakeMesh re-measures the POSED bounds either way.
  // W1 (#1318): AT REST, prefer the registry's OWN anim_ref (e.g. hero@moveset.fbx) over the mesh
  // fbx when the asset has one, so a rigged demo-cast actor settles into its real idle clip.
  var pos=spawn(fbx,alb,(restMode && !string.IsNullOrEmpty(anim))?anim:fbx,cx,cy,h,ring,"Actor_"+tid);
  if(nm!=null) posByName[nm]=pos; spawned++; celldbg+=" "+nm+"("+team+")@"+cx+","+cy;
}
if(missingActor){ sb.AppendLine("ABORT capture — a required actor prefab was missing (no PNG written)"); return sb.ToString(); }
sb.AppendLine("LIVE "+CID+": spawned "+spawned+" actors:"+celldbg);

// ---- #1408 floor-contact MANIFEST (ports #1392/#1397's replay-lane manifest to this driver): project
// each placed actor's grounded feet/head + baked verts to px at the CAPTURE resolution and emit a
// qa/visual_pregate.py-ready manifest — the rest path was previously NOT pre-gate measurable at all
// (issue #1408 item 3). Real projected screen_bbox (every baked vertex, post-grounding), not a
// synthesized half=0.22*height formula (#1397 found that blind to true pose). floor_y_px is the SAME
// single-center-point approximation #1402 flagged as advisory-only under the oblique camera — kept
// identical to the replay lane (a real fix for #1402 is a separate follow-up, not this issue). -------
{
  string OUTDIR="/home/unity/worldos-unity/Captures-Durable"; System.IO.Directory.CreateDirectory(OUTDIR);
  float _pa2=cam.aspect; var _pt2=cam.targetTexture; cam.aspect=(float)W/Hh;
  System.Func<Vector3,float[]> w2p=(w)=>{ var vp=cam.WorldToViewportPoint(w); return new float[]{ vp.x*W, (1f-vp.y)*Hh }; };
  System.Func<object,string> _jesc=(o)=>{ var st=o==null?"":o.ToString(); return st.Replace("\\","\\\\").Replace("\"","\\\"").Replace("\n"," ").Replace("\r"," "); };
  var msb=new System.Text.StringBuilder();
  msb.Append("{\n  \"frame_w\":"+W+", \"frame_h\":"+Hh+",\n");
  msb.Append("  \"checks\": {\"floor_contact\": {\"tolerance_px\": 8}, \"screen_scale\": {\"min_height_frac\":0.03,\"max_height_frac\":0.45}, \"pose_uprightness\": {\"min_aspect_ratio\":1.25}},\n");
  msb.Append("  \"actors\": [\n");
  for(int i=0;i<_repSidecar.Count;i++){ var d=_repSidecar[i];
    var lc=(int[])d["logical_cell"]; var rc2=(int[])d["render_cell"];
    var fW=(Vector3)d["feetW"];
    var floorPx=w2p(new Vector3(fW.x,FLOOR_Y,fW.z));
    var vertsW=(System.Collections.Generic.List<Vector3>)d["vertsW"];
    float bx0=float.MaxValue,by0=float.MaxValue,bx1=float.MinValue,by1=float.MinValue;
    foreach(var vw in vertsW){ var sp=w2p(vw); if(sp[0]<bx0)bx0=sp[0]; if(sp[0]>bx1)bx1=sp[0]; if(sp[1]<by0)by0=sp[1]; if(sp[1]>by1)by1=sp[1]; }
    if(vertsW.Count==0){ var hW=(Vector3)d["headW"]; var fp0=w2p(fW); var hp0=w2p(hW); bx0=fp0[0]-4f; bx1=fp0[0]+4f; by0=hp0[1]; by1=fp0[1]; } // fallback: no baked verts (non-skinned actor)
    msb.Append("    {\"name\":\""+_jesc(d["id"])+"\",\"logical_cell\":["+lc[0]+","+lc[1]+"],\"expected_cell\":["+rc2[0]+","+rc2[1]+"],");
    msb.Append("\"screen_bbox\":["+Mathf.Round(bx0)+","+Mathf.Round(by0)+","+Mathf.Round(bx1)+","+Mathf.Round(by1)+"],");
    msb.Append("\"floor_y_px\":"+Mathf.Round(floorPx[1])+"}");
    msb.Append(i<_repSidecar.Count-1?",\n":"\n");
  }
  msb.Append("  ]\n}\n");
  System.IO.File.WriteAllText(OUTDIR+"/combat_actors_manifest.json", msb.ToString());
  cam.aspect=_pa2; cam.targetTexture=_pt2;
  sb.AppendLine("wrote floor-contact manifest -> "+OUTDIR+"/combat_actors_manifest.json ("+_repSidecar.Count+" actors)");
}

// ACTIVE-ROOM VIEWPORT FRAMING (#1281, FELT gap). Multi-room plates read as a "level-select diorama" — the
// whole painted layout floats in void because the fixed contract camera (ortho 13) frames the ENTIRE plate,
// not the room the fight is in. ADDITIVE + flag-gated (frameActiveRoom): DEFAULT OFF -> byte-identical to the
// current render (the plate stays a camera-child billboard, camera contract untouched). When ON: crop the
// camera to the ACTIVE room's grid bounds (from the surface `grid` block: the party's current room-unit) so the
// room fills the frame like a game camera. The camera CONTRACT is INVIOLABLE — we change ONLY orthographicSize
// and the camera POSITION along its fixed view axis; the Euler(30,45,0) rotation is NEVER touched (zooming, not
// re-angling). Mechanism: the plate (a camera-child billboard sized to fill the frame at ortho 13) is DETACHED
// to a WORLD anchor baked from the DEFAULT camera pose, so it projects to identical pixels at zoom=1 but stays
// put as the camera zooms/pans — the crop then rides into the painted image instead of re-filling the frame.
// Soft edge: the framed rect is CLAMPED to the plate's world rect (ortho never exceeds 13, pan never runs past
// the plate) so a tight room prefers a slightly wider view over showing plate-void at the margins.
bool frameActiveRoom=false; // #1281: default OFF (byte-identical). Flip via _frame_active_room.txt (see below).
{ var _ff="/home/unity/worldos-unity/Assets/painterly/backdrops/_frame_active_room.txt";
  if(System.IO.File.Exists(_ff)){ var _v=System.IO.File.ReadAllText(_ff).Trim().ToLower(); if(_v=="1"||_v=="true"||_v=="on") frameActiveRoom=true; } }
if(frameActiveRoom){
  // 1) active-room grid extents from the surface `grid` block (engine-authored current room-unit); fall back to
  //    the 14x11 contract grid -> a full-grid room needs no crop (reqOrtho ~= 13), keeping the render unchanged.
  int gCols=14, gRows=11;
  { var _g=root.ContainsKey("grid")?root["grid"] as System.Collections.Generic.Dictionary<string,object>:null;
    if(_g!=null){ if(_g.ContainsKey("cols")&&_g["cols"]!=null) gCols=System.Convert.ToInt32(_g["cols"]); if(_g.ContainsKey("rows")&&_g["rows"]!=null) gRows=System.Convert.ToInt32(_g["rows"]); }
    if(gCols<1) gCols=14; if(gRows<1) gRows=11; }
  // 2) world-space room bounds = cellToWorld of the 4 grid corners + a ~1.5-cell (3.0u) margin. cellToWorld is
  //    affine, so the axis-aligned world AABB of the four corners is the full cell span.
  Vector3 c00=cellToWorld(0,0), c10=cellToWorld(gCols-1,0), c01=cellToWorld(0,gRows-1), c11=cellToWorld(gCols-1,gRows-1);
  float MARGIN=3.0f;
  float wMinX=Mathf.Min(Mathf.Min(c00.x,c10.x),Mathf.Min(c01.x,c11.x))-MARGIN;
  float wMaxX=Mathf.Max(Mathf.Max(c00.x,c10.x),Mathf.Max(c01.x,c11.x))+MARGIN;
  float wMinZ=Mathf.Min(Mathf.Min(c00.z,c10.z),Mathf.Min(c01.z,c11.z))-MARGIN;
  float wMaxZ=Mathf.Max(Mathf.Max(c00.z,c10.z),Mathf.Max(c01.z,c11.z))+MARGIN;
  Vector3 roomCtr=new Vector3((wMinX+wMaxX)*0.5f,0f,(wMinZ+wMaxZ)*0.5f);
  // 3) project the room AABB corners onto the camera's RIGHT/UP axes to get the required half-extents in view
  //    space (rotation fixed -> right/up are constants). newOrtho covers the taller of the vertical need and the
  //    horizontal need / aspect (capture aspect = plate W/H, computed the same way as the render below).
  Vector3 camR=cam.transform.right, camU=cam.transform.up;
  float capAsp=(float)bdTex.width/bdTex.height;
  float halfH=0f, halfW=0f;
  foreach(var wp in new[]{ new Vector3(wMinX,0,wMinZ), new Vector3(wMaxX,0,wMinZ), new Vector3(wMinX,0,wMaxZ), new Vector3(wMaxX,0,wMaxZ) }){
    Vector3 d=wp-roomCtr; halfW=Mathf.Max(halfW,Mathf.Abs(Vector3.Dot(d,camR))); halfH=Mathf.Max(halfH,Mathf.Abs(Vector3.Dot(d,camU))); }
  float reqOrtho=Mathf.Max(halfH, halfW/capAsp);
  // 4) CLAMP: never wider than the full plate (ortho 13 already frames the whole plate -> the crop never reveals
  //    void beyond it), and a floor so a tiny room isn't zoomed to absurdity. reqOrtho>=13 (grid ~ full frame)
  //    -> newOrtho=13 == today's framing (belt-and-braces byte-identity for a full-grid room).
  float MIN_ORTHO=6.0f, MAX_ORTHO=13.0f;
  float newOrtho=Mathf.Clamp(reqOrtho, MIN_ORTHO, MAX_ORTHO);
  // 5) DETACH the plate to a WORLD anchor baked from the DEFAULT (pre-zoom) camera pose so it projects to the
  //    SAME pixels at zoom=1 but stays fixed as we zoom/pan (the camera then crops INTO the painting). Capture
  //    the plate's current world transform (it is still the camera-child billboard) and re-anchor in world.
  Vector3 plateWPos=bd.transform.position; Quaternion plateWRot=bd.transform.rotation; Vector3 plateWScale=bd.transform.lossyScale;
  bd.transform.SetParent(null,true); bd.transform.position=plateWPos; bd.transform.rotation=plateWRot; bd.transform.localScale=plateWScale;
  // plate world rect (view-space half-extents about its own center) for the pan clamp: the billboard was oh=26
  // tall (ortho13*2), ow=26*texAsp wide, centered on the default forward ray at dist 160.
  Vector3 plateCtr=plateWPos; float plateHalfH=13.0f, plateHalfW=13.0f*capAsp;
  // 6) SHIFT the camera along its FIXED view axis to recenter on the room, then CLAMP the pan so the framed rect
  //    (half-extents newOrtho x newOrtho*capAsp about the pan target) stays inside the plate rect -> never void.
  Vector3 defCamPos=cam.transform.position; // contract pos (unchanged so far)
  float panU=Vector3.Dot(roomCtr-plateCtr,camU); float panR=Vector3.Dot(roomCtr-plateCtr,camR);
  float frH=newOrtho, frW=newOrtho*capAsp;
  float limU=Mathf.Max(0f, plateHalfH-frH); float limR=Mathf.Max(0f, plateHalfW-frW);
  panU=Mathf.Clamp(panU,-limU,limU); panR=Mathf.Clamp(panR,-limR,limR);
  // new camera position = default pos shifted by the clamped view-space pan (NEVER along forward -> no rotation,
  // no dolly through the scene; a pure in-view-plane recenter, exactly like sliding an ortho viewport rect).
  cam.transform.position=defCamPos + camU*panU + camR*panR;
  cam.orthographicSize=newOrtho;
  sb.AppendLine("frameActiveRoom ON: grid "+gCols+"x"+gRows+" reqOrtho="+reqOrtho.ToString("F2")+" -> ortho="+newOrtho.ToString("F2")+" panR="+panR.ToString("F2")+" panU="+panU.ToString("F2"));
}

// OCCLUSION (owner: "can they move BEHIND columns / behind items?"). The surface `occluders` field carries the
// engine-authored OCCLUDER props (footprint cells + height band). Place an INVISIBLE depth-only box at each
// occluder cell (ColorMask 0 -> writes DEPTH, not color; queue Geometry-1 -> renders BEFORE actors). A 3D actor
// that stands BEHIND a painted column (greater camera depth) then fails the depth test where they overlap and is
// correctly HIDDEN by it. The box aligns with the PAINTED column because both derive from the SAME cell + the
// SAME contract camera (the greybox column was built at cellToWorld(cell)). [] occluders -> today's behavior.
{ var occRoot = root.ContainsKey("occluders") ? root["occluders"] as System.Collections.Generic.List<object> : null;
  int occN=0;
  if(occRoot!=null && occRoot.Count>0){
    string occSrc =
      "Shader \"WorldOS/OccluderDepth\" {\n"+
      "  SubShader {\n"+
      "    Tags { \"RenderType\"=\"Opaque\" \"Queue\"=\"Geometry-1\" }\n"+
      "    Pass {\n"+
      "      ColorMask 0\n      ZWrite On\n"+
      "      CGPROGRAM\n      #pragma vertex vert\n      #pragma fragment frag\n      #include \"UnityCG.cginc\"\n"+
      "      float4 vert(float4 v:POSITION):SV_POSITION { return UnityObjectToClipPos(v); }\n"+
      "      fixed4 frag():SV_Target { return fixed4(0,0,0,0); }\n"+
      "      ENDCG\n    }\n  }\n}\n";
    var occShader=UnityEditor.ShaderUtil.CreateShaderAsset(occSrc);
    var occMat=new Material(occShader);
    System.Func<string,float> bandH=(b)=> b=="tall"?7.5f : (b=="low"?1.4f : 3.8f);
    foreach(var oo in occRoot){ var od=oo as System.Collections.Generic.Dictionary<string,object>; if(od==null) continue;
      string band=od.ContainsKey("band")?od["band"] as string:"mid"; float H=bandH(band);
      var ocells=od.ContainsKey("cells")?od["cells"] as System.Collections.Generic.List<object>:null; if(ocells==null) continue;
      foreach(var cc in ocells){ var cell=cc as System.Collections.Generic.List<object>; if(cell==null||cell.Count<2) continue;
        int ccx=System.Convert.ToInt32(cell[0]); int ccy=System.Convert.ToInt32(cell[1]);
        var wp=cellToWorld(ccx,ccy);
        var box=GameObject.CreatePrimitive(PrimitiveType.Cube); box.name="Occluder_"+ccx+"_"+ccy;
        UnityEngine.Object.DestroyImmediate(box.GetComponent<Collider>());
        box.transform.position=new Vector3(wp.x, H*0.5f, wp.z);
        box.transform.localScale=new Vector3(2.0f, H, 2.0f);
        var br=box.GetComponent<Renderer>(); br.sharedMaterial=occMat; br.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; br.receiveShadows=false;
        occN++;
      }
    }
  }
  sb.AppendLine("occluders: "+occN+" depth-proxy boxes");
}
// latest damage from the battleLog -> floating "-N" + impact burst over the struck token (skip if no recent hit).
// W1 (#1318): AT REST there is no combat, so skip the damage-VFX pass entirely (blog left null) — a rest
// scene never shows an impact burst / "-N" over a peaceful innkeeper.
string dmgTarget=""; int dmgN=0; var blog=restMode?null:(root.ContainsKey("battleLog")?(root["battleLog"] as System.Collections.Generic.List<object>):null);
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
// W/Hh declared earlier (before spawn) so the #1408 manifest projection above shares this exact resolution.
var rt=new RenderTexture(W,Hh,24,RenderTextureFormat.ARGB32); rt.Create();
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
