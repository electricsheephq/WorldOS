// build_atelier_crypt.cs — ATELIER-KIT SPIKE: assemble a REAL 3D crypt from Synty modular prefabs at the
// authored scene_grid, light it with a PoE2 "staging-law" rig, and capture a 4-pass buffer set (beauty /
// albedo / depth / normal) at the byte-identical CONTRACT camera. This is the graphics spike scaffold — the
// beauty pass needs crisp GEOMETRY + correct staging-law VALUE STRUCTURE, not a painterly surface (that comes
// from a later LoRA albedo paint-over). Run:  python3 /tmp/mcprun.py --file /tmp/build_atelier_crypt.cs
//
// Reads /home/unity/worldos-unity/room_geometry.json (14x11 crypt: walls perimeter, pillars (4,4)/(9,4),
// sarcophagus (6,5)-(7,5)). CONTRACT cell->world (cx0=(cols-1)/2, cy0=(rows-1)/2, cell 2.0) — the SAME map
// the combat renderer uses. Writes a bounded report to /home/unity/worldos-unity/atelier_report.txt (mcprun
// truncates stdout) and the 4 PNGs to Captures-Durable/.
// NB: run as a roslyn method BODY (execute_code) — NO top-level `using` directives; use fully-qualified names.
var sb = new System.Text.StringBuilder();
System.Action<string> LOG = (s)=>{ sb.AppendLine(s); };

Camera cam = Camera.main; if(cam==null && Camera.allCameras.Length>0) cam=Camera.allCameras[0];
if(cam==null){ var cg=new GameObject("AtelierCam"); cam=cg.AddComponent<Camera>(); cg.tag="MainCamera"; }

// --- read the authored geometry ---
string GEO="/home/unity/worldos-unity/room_geometry.json";
if(!System.IO.File.Exists(GEO)) return "no geometry json: "+GEO;
var geo=MiniJson.Parse(System.IO.File.ReadAllText(GEO)) as System.Collections.Generic.Dictionary<string,object>;
if(geo==null) return "geometry parse failed";
int cols=geo.ContainsKey("cols")?System.Convert.ToInt32(geo["cols"]):14;
int rows=geo.ContainsKey("rows")?System.Convert.ToInt32(geo["rows"]):11;
float cx0=(cols-1)/2.0f, cy0=(rows-1)/2.0f, CELL=2.0f;
System.Func<int,int,Vector3> cellToWorld=(c,r)=> new Vector3((c-cx0)*CELL, 0f, (cy0-r)*CELL);

// --- idempotent: delete any prior AtelierCrypt root + its lights ---
{ var prev=GameObject.Find("AtelierCrypt"); if(prev!=null) UnityEngine.Object.DestroyImmediate(prev);
  foreach(var ln in new[]{"AK_MoonKey","AK_TorchSarc","AK_TorchWall","AK_ArchVoid","AK_RimFill"}){ var o=GameObject.Find(ln); if(o!=null) UnityEngine.Object.DestroyImmediate(o); } }
var root=new GameObject("AtelierCrypt");

// --- prefab loader by asset name (verify exact names via AssetDatabase.FindAssets) ---
System.Func<string,GameObject> loadPrefab=(nm)=>{
  var guids=AssetDatabase.FindAssets(nm+" t:Prefab");
  foreach(var g in guids){ var pth=AssetDatabase.GUIDToAssetPath(g);
    if(System.IO.Path.GetFileNameWithoutExtension(pth)==nm) return AssetDatabase.LoadAssetAtPath<GameObject>(pth); }
  // fall back to first match if no exact filename hit
  if(guids.Length>0) return AssetDatabase.LoadAssetAtPath<GameObject>(AssetDatabase.GUIDToAssetPath(guids[0]));
  return null;
};
// world-space renderer bounds of an instantiated prefab (Synty pivots are often corner/base-based —
// MEASURE, never assume). Returns (bounds, hasBounds).
System.Func<GameObject,Bounds> worldBounds=(go)=>{
  var rends=go.GetComponentsInChildren<Renderer>(); if(rends.Length==0) return new Bounds(go.transform.position,Vector3.zero);
  var b=rends[0].bounds; for(int i=1;i<rends.Length;i++) b.Encapsulate(rends[i].bounds); return b;
};

// place a prefab centered at worldXZ, sitting on the floor (min.y -> 0), optionally scaled so its
// footprint spans `spanX x spanZ` world units (0 = keep native), with a yaw. Returns the instance.
System.Func<GameObject,string,Vector3,float,float,float,GameObject> place=(prefab,name,worldXZ,spanX,spanZ,yaw)=>{
  if(prefab==null){ LOG("  MISSING prefab for "+name); return null; }
  var inst=(GameObject)PrefabUtility.InstantiatePrefab(prefab); inst.name=name; inst.transform.SetParent(root.transform,true);
  inst.transform.position=Vector3.zero; inst.transform.rotation=Quaternion.Euler(0f,yaw,0f); inst.transform.localScale=Vector3.one;
  var b0=worldBounds(inst);
  // scale to requested footprint. spanX/spanZ==0 => keep that axis native.
  //  - BOTH given (props/floor): scale x,z to fit; y = mean of the two ratios (keeps proportions sane).
  //  - ONE given (walls): scale ONLY the edge axis to the span; keep thickness + height native (scale 1).
  if(spanX>0f && b0.size.x>1e-4f && spanZ>0f && b0.size.z>1e-4f){
    float sx=spanX/b0.size.x, sz=spanZ/b0.size.z;
    inst.transform.localScale=new Vector3(sx, (sx+sz)*0.5f, sz);
  } else if(spanX>0f && b0.size.x>1e-4f){
    float sx=spanX/b0.size.x; inst.transform.localScale=new Vector3(sx,1f,1f);
  } else if(spanZ>0f && b0.size.z>1e-4f){
    float sz=spanZ/b0.size.z; inst.transform.localScale=new Vector3(1f,1f,sz);
  }
  // re-measure after scale, then position: center on worldXZ, base on floor (y=0)
  var b1=worldBounds(inst);
  Vector3 pivotOffset=inst.transform.position - b1.center;         // pivot relative to bounds center
  float baseY=b1.min.y;                                            // current world min.y
  inst.transform.position=new Vector3(worldXZ.x + pivotOffset.x, pivotOffset.y - baseY, worldXZ.z + pivotOffset.z);
  return inst;
};

var pWall  = loadPrefab("SM_Bld_Base_Wall_01");
var pFloor = loadPrefab("SM_Bld_Base_Floor_01");
var pPil1  = loadPrefab("SM_Bld_Base_Pillar_01");
var pPil2  = loadPrefab("SM_Bld_Base_Pillar_02");
var pTomb  = loadPrefab("SM_Prop_Tomb_Royal_01");            // sarcophagus (royal tomb, spans two cells)
if(pTomb==null) pTomb=loadPrefab("SM_Prop_Tomb_01");
LOG("prefabs: Wall="+(pWall!=null)+" Floor="+(pFloor!=null)+" Pillar01="+(pPil1!=null)+" Pillar02="+(pPil2!=null)+" Tomb="+(pTomb!=null));

// --- MEASURE native bounds of each modular piece (report the Synty pivot/size gotchas) ---
System.Func<GameObject,string,string> measure=(pf,label)=>{
  if(pf==null) return label+"=<null>";
  var t=(GameObject)PrefabUtility.InstantiatePrefab(pf); t.transform.position=Vector3.zero; t.transform.rotation=Quaternion.identity; t.transform.localScale=Vector3.one;
  var b=worldBounds(t); var piv=t.transform.position; string s=label+" size=("+b.size.x.ToString("F2")+","+b.size.y.ToString("F2")+","+b.size.z.ToString("F2")+") min=("+b.min.x.ToString("F2")+","+b.min.y.ToString("F2")+","+b.min.z.ToString("F2")+") pivot=("+piv.x.ToString("F2")+","+piv.y.ToString("F2")+","+piv.z.ToString("F2")+")";
  UnityEngine.Object.DestroyImmediate(t); return s;
};
LOG(measure(pWall,"Wall_01")); LOG(measure(pFloor,"Floor_01")); LOG(measure(pPil1,"Pillar_01")); LOG(measure(pPil2,"Pillar_02")); LOG(measure(pTomb,"Tomb"));

// --- FLOOR: one Floor_01 tiled per interior cell (spans 2.0x2.0), base on y=0 ---
int nFloor=0;
for(int r=0;r<rows;r++) for(int c=0;c<cols;c++){ var w=cellToWorld(c,r); if(place(pFloor,"Floor_"+c+"_"+r,new Vector3(w.x,0,w.z),CELL,CELL,0f)!=null) nFloor++; }

// --- WALLS: CUTAWAY iso-CRPG rule. Camera sits at the -x,-z near corner looking toward +x,+z.
//   FAR (visible) walls  = +z BACK row (grid r==0)  and  +x RIGHT col (grid c==cols-1).
//   NEAR (omitted) walls = -z FRONT row (grid r==rows-1) and -x LEFT col (grid c==0).
// Read the grid's perimeter walls and keep ONLY the far ones so the camera sees the interior.
// A wall piece spans one 2.0 cell edge; face inward. Wall_01's face lies in a plane — we scale its
// footprint span to 2.0 along the edge axis and yaw it so its outward normal points AWAY from the room.
var wallCells=geo.ContainsKey("walls")?geo["walls"] as System.Collections.Generic.List<object>:null;
int nWall=0;
// Wall placer: scale the wall's LOCAL long axis (x, pre-yaw) so its footprint spans one 2.0 cell edge,
// keep thickness+height native, THEN yaw so the face points inward, then seat base on floor (y=0) at edgePos.
System.Action<string,Vector3,float> placeWall=(name,edgePos,yaw)=>{
  if(pWall==null){ LOG("  MISSING Wall prefab"); return; }
  var inst=(GameObject)PrefabUtility.InstantiatePrefab(pWall); inst.name=name; inst.transform.SetParent(root.transform,true);
  inst.transform.position=Vector3.zero; inst.transform.rotation=Quaternion.identity; inst.transform.localScale=Vector3.one;
  var b0=worldBounds(inst);
  // long axis of the wall in local space (Synty base walls run along X). Scale x so long axis == CELL.
  float longAxis=Mathf.Max(b0.size.x,b0.size.z);
  float s = longAxis>1e-4f ? CELL/longAxis : 1f;
  inst.transform.localScale=new Vector3(s,1f,1f); // scale only the footprint long axis (x); keep height+thickness native
  inst.transform.rotation=Quaternion.Euler(0f,yaw,0f);
  var b1=worldBounds(inst);
  Vector3 pivotOffset=inst.transform.position - b1.center; float baseY=b1.min.y;
  inst.transform.position=new Vector3(edgePos.x+pivotOffset.x, pivotOffset.y-baseY, edgePos.z+pivotOffset.z);
  nWall++;
};
if(wallCells!=null) foreach(var wo in wallCells){ var wc=wo as System.Collections.Generic.List<object>; if(wc==null||wc.Count<2) continue;
  int c=System.Convert.ToInt32(wc[0]), r=System.Convert.ToInt32(wc[1]);
  bool isBack = (r==0);            // +z far row  (grid r==0 -> +z, the visible back wall)
  bool isRight= (c==cols-1);       // +x far col  (grid c==cols-1 -> +x, the visible right wall)
  if(!(isBack||isRight)) continue; // OMIT -z near row (r==rows-1) and -x left col (c==0) => cutaway
  var w=cellToWorld(c,r);
  // dedupe the shared far corner (cols-1,0): count it once as back.
  if(isBack){ placeWall("WallBack_"+c, new Vector3(w.x, 0, w.z+CELL*0.5f), 0f); }   // yaw 0: face -z inward
  else if(isRight){ placeWall("WallRight_"+r, new Vector3(w.x+CELL*0.5f, 0, w.z), 90f); } // yaw 90: face -x inward
}

// --- PILLARS: two DIFFERENT variants (anti-clone) at (4,4) and (9,4), native footprint, base on floor ---
int nPil=0;
{ var w=cellToWorld(4,4); if(place(pPil1,"Pillar_A",new Vector3(w.x,0,w.z),0f,0f,0f)!=null) nPil++; }
{ var w=cellToWorld(9,4); if(place(pPil2,"Pillar_B",new Vector3(w.x,0,w.z),0f,0f,0f)!=null) nPil++; }

// --- SARCOPHAGUS: royal tomb spanning cells (6,5)-(7,5). Center between the two cells; span 2 cells in X. ---
int nSarc=0;
{ var w6=cellToWorld(6,5); var w7=cellToWorld(7,5); Vector3 mid=(w6+w7)*0.5f;
  // GOTCHA: SM_Prop_Tomb_Royal_01 native LONG axis is Z (size ~1.05 x 0.63 x 2.43). The sarcophagus
  // spans cells (6,5)-(7,5) which are adjacent in X, so yaw 90 to lay the long axis along X, then the
  // post-yaw footprint's long axis (now X) is scaled to span ~2 cells; keep it low (native ~0.63 tall).
  // With yaw, place()'s spanX applies to world-X (the long axis after rotation) and spanZ to world-Z.
  var inst=place(pTomb,"Sarcophagus",new Vector3(mid.x,0,mid.z), CELL*2f*0.9f, CELL*0.82f, 90f);
  if(inst!=null){ nSarc++; LOG("sarcophagus: SM_Prop_Tomb_Royal_01 yaw90 spanning (6,5)-(7,5)"); }
  else LOG("sarcophagus: NO tomb prefab found -> using scaled Base piece note");
}

// --- SET-DRESSING props along walls/corners (pathing-safe: edges only, never interior lanes) ---
// PolygonDungeonMap is a library/study pack (bookcases, knight-stands, globe, book piles) — a crypt-library.
int nProp=0;
System.Func<string,int,int,float,float,float,bool> dress=(prefabNm,c,r,spanX,spanZ,yaw)=>{
  var pf=loadPrefab(prefabNm); if(pf==null){ LOG("  MISSING dressing "+prefabNm); return false; }
  var w=cellToWorld(c,r); var i=place(pf,"Dress_"+prefabNm+"_"+c+"_"+r,new Vector3(w.x,0,w.z),spanX,spanZ,yaw);
  return i!=null;
};
// along the back wall (r=1, just inside) — bookcases facing -z (into room)
if(dress("SM_Prop_Bookcase_Grand_01",2,1,0f,0f,180f)) nProp++;
if(dress("SM_Prop_Bookcase_01",       11,1,0f,0f,180f)) nProp++;
// right wall (c=12, just inside) — knight stands facing -x
if(dress("SM_Prop_KnightStand_Royal_01",12,3,0f,0f,-90f)) nProp++;
if(dress("SM_Prop_KnightStand_01",       12,7,0f,0f,-90f)) nProp++;
// corners / floor near edges — globe + book piles (small footprint, edge cells)
if(dress("SM_Prop_Globe_01",     2,8,0f,0f,45f)) nProp++;
if(dress("SM_Prop_Book_Pile_01", 11,8,0f,0f,-30f)) nProp++;

// ================= MATERIAL NORMALIZATION (geometry spike) =================
// The box's Synty import is PARTIAL: PolygonDungeonMap prefab renderers have NULL materials (missing
// material/texture refs) -> Unity renders them MAGENTA; and some PolygonGeneric materials (e.g. the pillar's
// Generic_Concrete) compile "supported" yet still render magenta in the builtin forward path while the floor's
// Generic_Wood (same Synty/Generic_Basic shader) renders fine. This is a CLEAN GEOMETRY / VALUE-STRUCTURE
// spike (the painterly SURFACE comes from a later LoRA albedo paint-over), so assign a clean Standard stone
// material to EVERY renderer under AtelierCrypt, per-kind tinted. The Synty MESHES still supply all the
// carved geometric detail (fluted pillars, royal tomb, bookcases); only the surface is swapped to a
// value-correct matte stone so the beauty/albedo/depth/normal passes are crisp.
System.Func<Color,float,Material> stoneMat=(col,gloss)=>{ var m=new Material(Shader.Find("Standard")); m.color=col; m.SetFloat("_Glossiness",gloss); m.SetFloat("_Metallic",0f); return m; };
Color colFloor=new Color(0.32f,0.31f,0.30f), colWall=new Color(0.40f,0.39f,0.38f), colPillar=new Color(0.46f,0.45f,0.43f), colTomb=new Color(0.52f,0.50f,0.47f), colProp=new Color(0.36f,0.34f,0.32f);
{ int nm2=0;
  foreach(var r in root.GetComponentsInChildren<Renderer>(true)){ if(r==null) continue; string tn=r.transform.root==null?r.name:"";
    // classify by the top-of-root instance name (walk up to the AtelierCrypt direct child)
    var t=r.transform; while(t.parent!=null && t.parent!=root.transform) t=t.parent; string nm3=t.name;
    Color c=colProp; float g=0.05f;
    if(nm3.StartsWith("Floor_")) c=colFloor; else if(nm3.StartsWith("Wall")) c=colWall;
    else if(nm3.StartsWith("Pillar")) { c=colPillar; g=0.08f; } else if(nm3.StartsWith("Sarcophagus")) { c=colTomb; g=0.10f; }
    else if(nm3=="AK_RimFill") continue;  // keep the emissive rim material
    int slots=r.sharedMaterials.Length; var arr=new Material[slots]; var one=stoneMat(c,g); for(int i=0;i<slots;i++) arr[i]=one; r.sharedMaterials=arr; nm2++;
  }
  LOG("materials: normalized "+nm2+" renderers to Standard stone");
}

// ================= SCENE ISOLATION =================
// The box scene carries leftover combat objects (a painterly backdrop plate, Hero3D/Goblin3D/FIGHTER
// actors, Occluder_* boxes, brazier/combat lights). This is a CLEAN GEOMETRY spike — hide every renderer
// NOT under AtelierCrypt and disable every light NOT in our AK_ rig, so only our crypt + rig is captured.
// (Scene is not saved, so no restore is needed.)
int hidRend=0; foreach(var rr in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None)){
  if(rr==null) continue; if(rr.transform.IsChildOf(root.transform)) continue; if(rr.enabled){ rr.enabled=false; hidRend++; } }
int hidLight=0; foreach(var ll in UnityEngine.Object.FindObjectsByType<Light>(FindObjectsSortMode.None)){
  if(ll==null) continue; if(ll.gameObject.name.StartsWith("AK_")) continue; if(ll.enabled){ ll.enabled=false; hidLight++; } }
LOG("isolation: hid "+hidRend+" foreign renderers, "+hidLight+" foreign lights");

// ================= STAGING-LAW LIGHT RIG (the law: frame 66-80% near-black, 2-4% lit) =================
// ambient nearly void (a hair above void so the walls/pillars aren't pure crush; tuned in the gate loop)
RenderSettings.ambientMode=UnityEngine.Rendering.AmbientMode.Flat;
RenderSettings.ambientLight=new Color(0.058f,0.062f,0.080f);
RenderSettings.reflectionIntensity=0f;
// NO bright directional — a faint cool moon key only
{ var g=new GameObject("AK_MoonKey"); var L=g.AddComponent<Light>(); L.type=LightType.Directional;
  L.color=new Color(0.5f,0.6f,0.9f); L.intensity=0.24f; L.shadows=LightShadows.Soft; L.shadowStrength=0.85f;
  g.transform.rotation=Quaternion.Euler(55f,40f,0f); }
// 2-3 TIGHT warm point lights (range 8-14, warm 1,0.55,0.25). Values tuned in the gate loop.
System.Action<string,Vector3,float,float,Color> pt=(nm,pos,rng,inten,col)=>{
  var g=new GameObject(nm); var L=g.AddComponent<Light>(); L.type=LightType.Point; L.color=col;
  L.range=rng; L.intensity=inten; L.shadows=LightShadows.Soft; L.shadowStrength=0.7f; g.transform.position=pos; };
Color warm=new Color(1f,0.55f,0.25f);
// spread over peaky: wider range + lower peak intensity pushes pixels into the 26-60 MIDTONE band
// (neither dark nor lit), so the visible floor lifts above L=26 without the bright cores exceeding the 5% lit cap.
// spread over peaky: wide range lifts the floor midtones (dark% down); modest peak keeps bright cores
// under the 5% lit cap. Raising each light a bit HIGHER also softens the near-source hotspot.
{ var w6=cellToWorld(6,5); var w7=cellToWorld(7,5); Vector3 mid=(w6+w7)*0.5f;
  pt("AK_TorchSarc", new Vector3(mid.x, 3.4f, mid.z), 15f, 3.7f, warm); }          // over the sarcophagus
{ var wl=cellToWorld(4,4); pt("AK_TorchWall", new Vector3(wl.x-1.4f, 4.0f, wl.z), 14f, 3.0f, warm); } // wall torch by pillar A
{ var wa=cellToWorld(6,0); pt("AK_ArchVoid",  new Vector3(wa.x, 4.0f, wa.z+1.2f), 13f, 2.1f, new Color(1f,0.5f,0.2f)); } // faint archway/void
// ONE emissive-plane rim-fill (PoE recipe): a thin quad with an emissive URP material behind/above the
// sarcophagus as a COOL rim (does NOT cast — it's just emissive geometry).
{ var w6=cellToWorld(6,5); var w7=cellToWorld(7,5); Vector3 mid=(w6+w7)*0.5f;
  var q=GameObject.CreatePrimitive(PrimitiveType.Quad); q.name="AK_RimFill"; UnityEngine.Object.DestroyImmediate(q.GetComponent<Collider>());
  q.transform.SetParent(root.transform,true);
  q.transform.position=new Vector3(mid.x, 3.2f, mid.z+1.6f);   // behind (+z) and above the sarcophagus
  q.transform.rotation=Quaternion.Euler(18f,180f,0f); q.transform.localScale=new Vector3(4.5f,2.2f,1f);
  // Runtime pipeline is BUILTIN here (GraphicsSettings.currentRenderPipeline==null; URP/Lit is NOT
  // Shader.Find-able) — use the Standard shader with emission, matching build_room_greybox.cs.
  var lit=Shader.Find("Standard"); var m=new Material(lit);
  m.EnableKeyword("_EMISSION"); m.globalIlluminationFlags=UnityEngine.MaterialGlobalIlluminationFlags.RealtimeEmissive;
  m.SetColor("_EmissionColor", new Color(0.30f,0.42f,0.62f)*1.05f);
  m.SetColor("_Color", new Color(0.02f,0.03f,0.05f)); m.SetFloat("_Glossiness",0f);
  q.GetComponent<Renderer>().sharedMaterial=m; }

// ================= CONTRACT CAMERA (byte-identical) =================
cam.orthographic=true; cam.orthographicSize=13f; cam.nearClipPlane=0.3f; cam.farClipPlane=500f;
{ Quaternion crot=Quaternion.Euler(30f,45f,0f); cam.transform.rotation=crot; cam.transform.position=-(crot*Vector3.forward)*80f; }
cam.clearFlags=CameraClearFlags.SolidColor; cam.backgroundColor=new Color(0f,0f,0f,1f);
int W=1344,Hh=768;

// capture helper: render current cam to a PNG (respects lit scene or a replacement shader).
System.Action<string,Shader> capture=(fname,replShader)=>{
  var rt=new RenderTexture(W,Hh,24,RenderTextureFormat.ARGB32); rt.Create();
  float pa=cam.aspect; var pt2=cam.targetTexture; cam.targetTexture=rt; cam.aspect=(float)W/Hh;
  if(replShader!=null) cam.RenderWithShader(replShader,""); else cam.Render();
  var pAct=RenderTexture.active; RenderTexture.active=rt; var t2=new Texture2D(W,Hh,TextureFormat.RGB24,false);
  t2.ReadPixels(new Rect(0,0,W,Hh),0,0); t2.Apply(); RenderTexture.active=pAct; cam.targetTexture=pt2; cam.aspect=pa;
  System.IO.Directory.CreateDirectory("/home/unity/worldos-unity/Captures-Durable");
  System.IO.File.WriteAllBytes("/home/unity/worldos-unity/Captures-Durable/"+fname, t2.EncodeToPNG());
  UnityEngine.Object.DestroyImmediate(t2); rt.Release(); UnityEngine.Object.DestroyImmediate(rt);
};

// (a) BEAUTY — normal lit render
capture("atelier_beauty.png", null);

// (b) ALBEDO — all lights off + flat white ambient (so URP/Lit shows base color unlit-ish).
var allLights=UnityEngine.Object.FindObjectsByType<Light>(FindObjectsSortMode.None);
var savedEnabled=new System.Collections.Generic.Dictionary<Light,bool>();
foreach(var L in allLights){ savedEnabled[L]=L.enabled; L.enabled=false; }
var savedAmbMode=RenderSettings.ambientMode; var savedAmb=RenderSettings.ambientLight;
RenderSettings.ambientMode=UnityEngine.Rendering.AmbientMode.Flat; RenderSettings.ambientLight=Color.white;
capture("atelier_albedo.png", null);
// restore lights + ambient
foreach(var kv in savedEnabled) if(kv.Key!=null) kv.Key.enabled=kv.Value;
RenderSettings.ambientMode=savedAmbMode; RenderSettings.ambientLight=savedAmb;

// (c) DEPTH + (d) NORMAL via the existing replacement shaders
var shDepth=Shader.Find("WOS/LinDepth"); var shNorm=Shader.Find("WOS/ViewNormal");
LOG("shaders: LinDepth="+(shDepth!=null)+" ViewNormal="+(shNorm!=null));
if(shDepth!=null) capture("atelier_depth.png", shDepth); else LOG("  MISSING WOS/LinDepth");
if(shNorm!=null)  capture("atelier_normal.png", shNorm); else LOG("  MISSING WOS/ViewNormal");

LOG("BUILT floor="+nFloor+" walls="+nWall+" pillars="+nPil+" sarc="+nSarc+" props="+nProp);
LOG("captures -> Captures-Durable/atelier_{beauty,albedo,depth,normal}.png");
System.IO.File.WriteAllText("/home/unity/worldos-unity/atelier_report.txt", sb.ToString());
return "OK atelier: floor="+nFloor+" walls="+nWall+" pillars="+nPil+" sarc="+nSarc+" props="+nProp+" (report -> atelier_report.txt)";
