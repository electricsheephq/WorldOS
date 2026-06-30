// M1.0 SPIKE — the make-or-break: a REAL 3D hero (FBX), scene-lit + grounded, over the painterly crypt plate.
// Question: does a lit, dimensional 3D actor read as BELONGING in the painting (vs a flat pasted billboard)?
AssetDatabase.Refresh();
var sb=new System.Text.StringBuilder();
Camera cam=Camera.main; if(cam==null && Camera.allCameras.Length>0) cam=Camera.allCameras[0]; if(cam==null) return "no cam";
// frozen dimetric contract camera (same as the billboard renderer)
cam.orthographic=true; cam.orthographicSize=13f; cam.nearClipPlane=0.3f; cam.farClipPlane=500f;
{ Quaternion _crot=Quaternion.Euler(30f,45f,0f); cam.transform.rotation=_crot; cam.transform.position=-(_crot*Vector3.forward)*80f; }
cam.clearFlags=CameraClearFlags.SolidColor; cam.backgroundColor=new Color(0.02f,0.02f,0.03f);
int hidden=0; foreach(var r in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None)){ if(r.enabled){r.enabled=false;hidden++;} }

var bdTex=AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/backdrops/crypt_pinned_v1.png"); if(bdTex==null) return "no plate";
var oldBd=GameObject.Find("PaintedBackdrop"); if(oldBd!=null) UnityEngine.Object.DestroyImmediate(oldBd);
var bd=GameObject.CreatePrimitive(PrimitiveType.Quad); bd.name="PaintedBackdrop"; UnityEngine.Object.DestroyImmediate(bd.GetComponent<Collider>());
bd.transform.SetParent(cam.transform,false); float texAsp=(float)bdTex.width/bdTex.height; float oh=cam.orthographicSize*2f; float ow=oh*texAsp;
bd.transform.localPosition=new Vector3(0,0,160f); bd.transform.localScale=new Vector3(ow,oh,1f);
var bm=new Material(Shader.Find("Unlit/Texture")); bm.mainTexture=bdTex; bm.renderQueue=1900; var bdr=bd.GetComponent<Renderer>(); bdr.sharedMaterial=bm; bdr.enabled=true; bdr.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; bdr.receiveShadows=false;

System.Func<int,int,Vector3> cellToWorld=(cx,cy)=> new Vector3((cx-6.5f)*2.0f,0f,(5.0f-cy)*2.0f);

// PoE2 LIGHTING RIG (Phase 1.1): warm key + cool fill + cool ambient + warm brazier point lights at the painted braziers (warm/cool contrast; warm uplight near the fire)
foreach(var ln in new[]{"KeyLight","FillLight","BrazierL","BrazierR"}){ var o=GameObject.Find(ln); if(o!=null) UnityEngine.Object.DestroyImmediate(o); }
var lg=new GameObject("KeyLight"); var L=lg.AddComponent<Light>(); L.type=LightType.Directional; L.color=new Color(1f,0.73f,0.44f); L.intensity=1.35f; L.shadows=LightShadows.Soft; L.shadowStrength=0.75f; lg.transform.rotation=Quaternion.Euler(48f,35f,0f);
var fg=new GameObject("FillLight"); var F=fg.AddComponent<Light>(); F.type=LightType.Directional; F.color=new Color(0.36f,0.44f,0.64f); F.intensity=0.55f; F.shadows=LightShadows.None; fg.transform.rotation=Quaternion.Euler(34f,215f,0f);
RenderSettings.ambientMode=UnityEngine.Rendering.AmbientMode.Flat; RenderSettings.ambientLight=new Color(0.24f,0.28f,0.40f);  // cool fill ambient (blue-violet, never black)
System.Action<string,int,int,bool> brazier=(nm,cx,cy,sh)=>{ var bg=new GameObject(nm); var B=bg.AddComponent<Light>(); B.type=LightType.Point; B.color=new Color(1f,0.48f,0.18f); B.range=7.5f; B.intensity=3.6f; B.shadows=sh?LightShadows.Soft:LightShadows.None; var wp=cellToWorld(cx,cy); bg.transform.position=new Vector3(wp.x,1.7f,wp.z); };
brazier("BrazierL",4,1,true); brazier("BrazierR",9,1,false);

// REAL 3D hero
var heroPrefab=AssetDatabase.LoadAssetAtPath<GameObject>("Assets/painterly/models/hero.fbx"); if(heroPrefab==null) return "no hero.fbx (import failed?)";
var oldH=GameObject.Find("Hero3D"); if(oldH!=null) UnityEngine.Object.DestroyImmediate(oldH);
var hero=(GameObject)UnityEngine.Object.Instantiate(heroPrefab); hero.name="Hero3D";
hero.transform.rotation=Quaternion.Euler(-90f, cam.transform.eulerAngles.y+180f, 0f);  // stand the lying Z-up T-pose UPRIGHT + face the camera
var rends=hero.GetComponentsInChildren<Renderer>();
foreach(var r in rends){ r.enabled=true; r.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.On; r.receiveShadows=true; }
System.Func<Bounds> measure=()=>{ Bounds b=new Bounds(hero.transform.position,Vector3.zero); bool a=false; foreach(var r in rends){ if(!a){b=r.bounds;a=true;} else b.Encapsulate(r.bounds);} return b; };
Bounds bb=measure(); float curH=bb.size.y>0.001f?bb.size.y:1f; float s=5.0f/curH; hero.transform.localScale=hero.transform.localScale*s;  // MULTIPLY (preserve FBX import scale); 5.0 = more combat-readable presence (was 3.7=contract)
var p=cellToWorld(7,6); hero.transform.position=p; bb=measure(); hero.transform.position+=new Vector3(0f,-bb.min.y,0f);  // feet to y=0
sb.AppendLine("Hero3D x"+s.ToString("F2")+" @"+p.ToString("F1")+" standH(preScale)="+curH.ToString("F2")+" rends="+rends.Length);
// Phase 1.3: the FBX import dropped the GLB textures (white hero) -> assign the extracted albedo on a Standard material
var heroAlbedo=AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/models/hero_albedo.png");
if(heroAlbedo!=null){ var hmat=new Material(Shader.Find("Standard")); hmat.mainTexture=heroAlbedo; hmat.SetFloat("_Glossiness",0.2f); hmat.SetFloat("_Metallic",0f); foreach(var r in rends) r.sharedMaterial=hmat; sb.AppendLine("albedo ASSIGNED "+heroAlbedo.width); } else sb.AppendLine("NO albedo found");

// grounding AO blob under the feet
var blobT=new Texture2D(256,256,TextureFormat.RGBA32,false); blobT.wrapMode=TextureWrapMode.Clamp; { var px=new Color[256*256]; float c=127.5f; for(int y=0;y<256;y++)for(int x=0;x<256;x++){ float d=Mathf.Clamp01(Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c))/c); px[y*256+x]=new Color(0.02f,0.02f,0.03f,Mathf.Pow(1f-d,0.9f)); } blobT.SetPixels(px); blobT.Apply(); }
var ao=GameObject.CreatePrimitive(PrimitiveType.Quad); ao.name="HeroAO"; UnityEngine.Object.DestroyImmediate(ao.GetComponent<Collider>()); ao.transform.position=new Vector3(p.x,0.05f,p.z); ao.transform.localEulerAngles=new Vector3(90f,0f,0f); ao.transform.localScale=new Vector3(2.4f,1.5f,1f);
var aom=new Material(Shader.Find("Unlit/Transparent")); aom.mainTexture=blobT; aom.renderQueue=1950; var aor=ao.GetComponent<Renderer>(); aor.sharedMaterial=aom; aor.enabled=true; aor.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off;

bb=measure(); sb.AppendLine("HERO final bbox size="+bb.size.ToString("F2")+" center="+bb.center.ToString("F2")+" matShader="+(rends.Length>0&&rends[0].sharedMaterial!=null?rends[0].sharedMaterial.shader.name:"NULL"));

int W=1920,Hh=Mathf.RoundToInt(1920f*(float)bdTex.height/bdTex.width); var rt=new RenderTexture(W,Hh,24,RenderTextureFormat.ARGB32); rt.Create();
float pa=cam.aspect; var pt=cam.targetTexture; cam.targetTexture=rt; cam.aspect=(float)W/Hh; cam.Render();
var pAct=RenderTexture.active; RenderTexture.active=rt; var t2=new Texture2D(W,Hh,TextureFormat.RGB24,false); t2.ReadPixels(new Rect(0,0,W,Hh),0,0); t2.Apply(); RenderTexture.active=pAct; cam.targetTexture=pt; cam.aspect=pa;
System.IO.Directory.CreateDirectory("/home/unity/worldos-unity/Captures-Durable");
System.IO.File.WriteAllBytes("/home/unity/worldos-unity/Captures-Durable/m10_spike.png", t2.EncodeToPNG());
UnityEngine.Object.DestroyImmediate(t2); rt.Release(); UnityEngine.Object.DestroyImmediate(rt);
sb.AppendLine("captured "+W+"x"+Hh+" -> m10_spike.png hidden="+hidden);
// PERSIST the built scene (anti render-and-forget — CANONICAL.md discipline). Best-effort Save-As.
try { var _scn=UnityEngine.SceneManagement.SceneManager.GetActiveScene(); System.IO.Directory.CreateDirectory("Assets/Scenes"); UnityEditor.SceneManagement.EditorSceneManager.SaveScene(_scn, "Assets/Scenes/CombatCrypt_canonical.unity"); sb.AppendLine("scene SAVED -> Assets/Scenes/CombatCrypt_canonical.unity"); } catch(System.Exception _e){ sb.AppendLine("SaveScene FAILED: "+_e.Message); }
return sb.ToString();
