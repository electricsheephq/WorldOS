// paint_combat_v1.cs — P0 FIRST multi-actor combat frame: hero + goblin on the painterly crypt plate,
// gold/red selection rings, contact AO, an impact VFX burst + a floating "-8" damage number.
// Built off the PROVEN paint_3d_spike.cs (same unqualified UnityEngine/UnityEditor style the wrapper injects).
// NO AnimatorController (its assembly isn't referenced by code-execute); actors are placed (pose-sampling = v2).
// Run: unity-mcp code execute --no-safety-checks -f paint_combat_v1.cs
AssetDatabase.Refresh();
// New backdrop plates default to NPOT=ToNearest, which square-distorts a 1344x768 plate and breaks the
// camera-pin aspect. Force NPOT=None so the plate keeps native dims (idempotent — only reimports if needed).
{ var _ti=AssetImporter.GetAtPath("Assets/painterly/backdrops/crypt_firelit_v2.png") as TextureImporter; if(_ti!=null && _ti.npotScale!=TextureImporterNPOTScale.None){ _ti.npotScale=TextureImporterNPOTScale.None; _ti.maxTextureSize=2048; _ti.SaveAndReimport(); } }
var sb=new System.Text.StringBuilder();
Camera cam=Camera.main; if(cam==null && Camera.allCameras.Length>0) cam=Camera.allCameras[0]; if(cam==null) return "no cam";
// validate the plate BEFORE mutating camera/renderers — a missing plate must not leave the editor scene corrupted.
var bdTex=AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/backdrops/crypt_firelit_v2.png"); if(bdTex==null) return "no plate";
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

// NOTE: rigged.fbx's UVs don't match hero_albedo (white) and its rig orientation doesn't compose with the
// stand-up rotation (collapses). Use the proven textured hero.fbx (T-pose) until a correct rig+retarget lands.
// ring colors: cyan party / saturated-red foe (gold was camouflaged on the warm flagstone — critic L5).
var heroPos=spawn("Assets/painterly/models/hero.fbx","Assets/painterly/models/hero_albedo.png",null,6,6,5.0f,new Color(0.4f,0.95f,1f,1f),"Hero3D");
var gobPos=spawn("Assets/chars_v2/goblin/goblin.fbx","Assets/chars_v2/goblin/albedo.png",null,9,5,4.2f,new Color(1f,0.13f,0.10f,1f),"Goblin3D");
// fail fast: never write a "successful" durable frame that's missing a required actor.
if(missingActor){ sb.AppendLine("ABORT capture — a required actor prefab was missing (no PNG written)"); return sb.ToString(); }

// impact VFX burst at the goblin (additive orange radial), billboarded
var oldFx=GameObject.Find("ImpactFX"); if(oldFx!=null) UnityEngine.Object.DestroyImmediate(oldFx);
var fx=GameObject.CreatePrimitive(PrimitiveType.Quad); fx.name="ImpactFX"; UnityEngine.Object.DestroyImmediate(fx.GetComponent<Collider>()); fx.transform.position=gobPos+new Vector3(0f,2.0f,0f); fx.transform.rotation=cam.transform.rotation; fx.transform.localScale=new Vector3(3.4f,3.4f,1f);
var fxT=new Texture2D(128,128,TextureFormat.RGBA32,false); { var px=new Color[128*128]; float c=63.5f; for(int y=0;y<128;y++)for(int x=0;x<128;x++){ float d=Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c))/c; float a=Mathf.Clamp01(1f-d); px[y*128+x]=new Color(1f,0.62f,0.16f,a*a); } fxT.SetPixels(px); fxT.Apply(); }
var fxm=new Material(Shader.Find("Unlit/Transparent")); fxm.mainTexture=fxT; fxm.color=new Color(1f,1f,1f,0.92f); fxm.renderQueue=3000; fx.GetComponent<Renderer>().sharedMaterial=fxm; fx.GetComponent<Renderer>().shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;

// floating "-8" damage number above the goblin (billboarded TextMesh)
var oldD=GameObject.Find("DmgNum"); if(oldD!=null) UnityEngine.Object.DestroyImmediate(oldD);
var dmgGo=new GameObject("DmgNum"); dmgGo.transform.position=gobPos+new Vector3(0f,3.7f,0f); dmgGo.transform.rotation=cam.transform.rotation; var tm=dmgGo.AddComponent<TextMesh>(); tm.text="-8"; tm.fontSize=90; tm.characterSize=0.22f; tm.anchor=TextAnchor.MiddleCenter; tm.alignment=TextAlignment.Center; tm.color=new Color(1f,0.95f,0.45f,1f); var tmr=dmgGo.GetComponent<MeshRenderer>(); if(tmr!=null && tmr.sharedMaterial!=null) tmr.sharedMaterial.renderQueue=3100;

// capture
int W=1920,Hh=Mathf.RoundToInt(1920f*(float)bdTex.height/bdTex.width); var rt=new RenderTexture(W,Hh,24,RenderTextureFormat.ARGB32); rt.Create();
float pa=cam.aspect; var pt=cam.targetTexture; cam.targetTexture=rt; cam.aspect=(float)W/Hh; cam.Render();
var pAct=RenderTexture.active; RenderTexture.active=rt; var t2=new Texture2D(W,Hh,TextureFormat.RGB24,false); t2.ReadPixels(new Rect(0,0,W,Hh),0,0); t2.Apply(); RenderTexture.active=pAct; cam.targetTexture=pt; cam.aspect=pa;
System.IO.Directory.CreateDirectory("/home/unity/worldos-unity/Captures-Durable");
System.IO.File.WriteAllBytes("/home/unity/worldos-unity/Captures-Durable/m1_combat_v1.png", t2.EncodeToPNG());
UnityEngine.Object.DestroyImmediate(t2); rt.Release(); UnityEngine.Object.DestroyImmediate(rt);
sb.AppendLine("captured "+W+"x"+Hh+" -> m1_combat_v1.png hidden="+hidden);
return sb.ToString();
