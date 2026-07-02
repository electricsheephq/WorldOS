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

// --- idempotent: delete any prior AtelierCrypt root + ALL prior AK_* rig lights (prefix match, NOT a
// fixed name list — a stale list silently LEAKS renamed lights across runs, which double-counts LIT). ---
{ var prev=GameObject.Find("AtelierCrypt"); if(prev!=null) UnityEngine.Object.DestroyImmediate(prev);
  var _stale=new System.Collections.Generic.List<GameObject>();
  foreach(var g in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None)){ if(g!=null && g.name.StartsWith("AK_")) _stale.Add(g); }
  foreach(var g in _stale){ if(g!=null) UnityEngine.Object.DestroyImmediate(g); } }
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

// v2: explicit-scale placer — apply a localScale vector, then re-measure and seat base on floor at worldXZ.
// Used for the monumental pillars (non-uniform XZ vs Y) and the uniformly-scaled hero tomb.
System.Func<GameObject,string,Vector3,Vector3,float,GameObject> placeScaled=(prefab,name,worldXZ,scale,yaw)=>{
  if(prefab==null){ LOG("  MISSING prefab for "+name); return null; }
  var inst=(GameObject)PrefabUtility.InstantiatePrefab(prefab); inst.name=name; inst.transform.SetParent(root.transform,true);
  inst.transform.position=Vector3.zero; inst.transform.rotation=Quaternion.Euler(0f,yaw,0f); inst.transform.localScale=scale;
  var b1=worldBounds(inst);
  Vector3 pivotOffset=inst.transform.position - b1.center; float baseY=b1.min.y;
  inst.transform.position=new Vector3(worldXZ.x+pivotOffset.x, pivotOffset.y-baseY, worldXZ.z+pivotOffset.z);
  return inst;
};

var pWall  = loadPrefab("SM_Bld_Base_Wall_01");
var pFloor = loadPrefab("SM_Bld_Base_Floor_01");
var pPil1  = loadPrefab("SM_Bld_Base_Pillar_01");
var pPil2  = loadPrefab("SM_Bld_Base_Pillar_02");
var pTomb  = loadPrefab("SM_Prop_Tomb_Royal_01");            // sarcophagus (royal tomb, spans two cells)
if(pTomb==null) pTomb=loadPrefab("SM_Prop_Tomb_01");
var pLid   = loadPrefab("SM_Prop_Tomb_Royal_Lid_01");       // v2 fix 5: lid so the tomb reads CLOSED/solid
if(pLid==null) pLid=loadPrefab("SM_Prop_Tomb_Lid_01");
LOG("prefabs: Wall="+(pWall!=null)+" Floor="+(pFloor!=null)+" Pillar01="+(pPil1!=null)+" Pillar02="+(pPil2!=null)+" Tomb="+(pTomb!=null)+" Lid="+(pLid!=null));

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
// v3 fix 1: HEROIC walls. Scale local long axis (x) to one 2.0-cell edge, scale Y by heightScale
// (~3.5x -> ~10.5 world units tall from native 3.01 — PoE interiors are deliberately over-scaled so the
// walls DOMINATE the frame, not read as low fins), keep thickness native, THEN yaw + seat on floor.
float WALL_H=3.5f;   // v3: HEROIC walls ~10.5 world units tall (native 3.01 * 3.5)
System.Action<string,Vector3,float> placeWall=(name,edgePos,yaw)=>{
  if(pWall==null){ LOG("  MISSING Wall prefab"); return; }
  var inst=(GameObject)PrefabUtility.InstantiatePrefab(pWall); inst.name=name; inst.transform.SetParent(root.transform,true);
  inst.transform.position=Vector3.zero; inst.transform.rotation=Quaternion.identity; inst.transform.localScale=Vector3.one;
  var b0=worldBounds(inst);
  float longAxis=Mathf.Max(b0.size.x,b0.size.z);
  float s = longAxis>1e-4f ? CELL/longAxis : 1f;
  inst.transform.localScale=new Vector3(s,WALL_H,1f); // long axis -> 2.0 cell; Y -> monumental; thickness native
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

// --- PILLARS (v3 fix 1): HEROIC load-bearing columns, two DIFFERENT variants (anti-clone).
// Native ~0.43 wide x 3.02 tall -> XZ x4.9 (~2.1 wide), Y x3.0 (~9.0 tall) so they read as massive
// stone columns against the 10.5u walls. Anti-clone: B a touch beefier + yawed 45.
int nPil=0;
{ var w=cellToWorld(4,4); if(placeScaled(pPil1,"Pillar_A",new Vector3(w.x,0,w.z), new Vector3(4.9f,3.0f,4.9f), 0f)!=null) nPil++; }
{ var w=cellToWorld(9,4); if(placeScaled(pPil2,"Pillar_B",new Vector3(w.x,0,w.z), new Vector3(5.2f,3.05f,5.2f), 45f)!=null) nPil++; }

// --- SARCOPHAGUS (v3 fix 1 + 5): royal tomb spanning cells (6,5)-(7,5), the HEROIC monument.
// Native ~1.05 x 0.63 x 2.43 (long axis Z). yaw 90 lays the long axis along X (cells 6,7 adjacent in X);
// uniform x2.2 makes it a hero monument. A ROYAL LID placed on top makes it read CLOSED/solid, not an open tray.
int nSarc=0;
{ var w6=cellToWorld(6,5); var w7=cellToWorld(7,5); Vector3 mid=(w6+w7)*0.5f;
  float TOMB_S=2.2f;
  var inst=placeScaled(pTomb,"Sarcophagus",new Vector3(mid.x,0,mid.z), new Vector3(TOMB_S,TOMB_S,TOMB_S), 90f);
  if(inst!=null){ nSarc++; LOG("sarcophagus: SM_Prop_Tomb_Royal_01 yaw90 x"+TOMB_S.ToString("F1")+" spanning (6,5)-(7,5)");
    var tb=worldBounds(inst);   // measure the placed tomb so the lid sits ON its top face
    if(pLid!=null){
      var lid=(GameObject)PrefabUtility.InstantiatePrefab(pLid); lid.name="SarcophagusLid"; lid.transform.SetParent(root.transform,true);
      lid.transform.position=Vector3.zero; lid.transform.rotation=Quaternion.Euler(0f,90f,0f); lid.transform.localScale=new Vector3(TOMB_S,TOMB_S,TOMB_S);
      var lb=worldBounds(lid); Vector3 lpo=lid.transform.position-lb.center;
      // center the lid on the tomb XZ, seat its base on the tomb's top (tb.max.y)
      lid.transform.position=new Vector3(tb.center.x+lpo.x, (lpo.y - lb.min.y) + tb.max.y - 0.02f, tb.center.z+lpo.z);
      LOG("sarcophagus lid: "+(pLid.name)+" seated on tomb top y="+tb.max.y.ToString("F2"));
    } else LOG("sarcophagus lid: NO lid prefab -> tomb reads as open tray");
  }
  else LOG("sarcophagus: NO tomb prefab found -> using scaled Base piece note");
}

// --- SET-DRESSING (v3 fix 1+4): DENSITY x3 (19 props), edges only (pathing-safe), ANTI-CLONE jitter,
// scaled UP x1.65 base so bookcases read ~4u tall against the 10.5u HEROIC walls (they were dwarfed at v2).
// Each prop gets a per-instance yaw jitter (+-10 deg) and scale jitter (0.95-1.10) so no two read identical.
float DRESS_S=1.65f;   // v3: heroic-scale base multiplier for all dressing
int nProp=0; int jseed=0;
System.Func<string,int,int,float,bool> dress=(prefabNm,c,r,baseYaw)=>{
  var pf=loadPrefab(prefabNm); if(pf==null){ LOG("  MISSING dressing "+prefabNm); return false; }
  // deterministic jitter from an incrementing seed (repeatable renders)
  jseed++; float jy=((jseed*37)%21)-10f; float js=DRESS_S*(0.95f+(((jseed*53)%16)/100f));   // yaw +-10, scale x1.65 * 0.95-1.10
  var w=cellToWorld(c,r);
  var i=placeScaled(pf,"Dress_"+prefabNm+"_"+c+"_"+r,new Vector3(w.x,0,w.z), new Vector3(js,js,js), baseYaw+jy);
  return i!=null;
};
// ROW of bookcases along the back wall (r=1, facing -z into room) — 4 across, varied yaw/scale via jitter
if(dress("SM_Prop_Bookcase_Grand_01", 2,1, 180f)) nProp++;
if(dress("SM_Prop_Bookcase_01",        4,1, 180f)) nProp++;
if(dress("SM_Prop_Bookcase_02",        8,1, 180f)) nProp++;
if(dress("SM_Prop_Bookcase_Grand_02", 11,1, 180f)) nProp++;
// right wall (c=12, facing -x) — v4 DRESSING FIX: the 3 identical knight statuettes read as literal
// clones, so keep ONE knight and replace the other two with DIFFERENT PolygonDungeonMap props (a leaning
// ladder + a globe) for distinct silhouettes. Varied yaw/scale preserved via the jitter.
if(dress("SM_Prop_KnightStand_Royal_01", 12,2, -90f)) nProp++;   // the one kept knight (royal)
if(dress("SM_Prop_Ladder_01",            12,5, -90f)) nProp++;   // was KnightStand_01 -> leaning ladder
if(dress("SM_Prop_Globe_01",             12,8, -90f)) nProp++;   // was KnightStand_Broken -> globe
// tomb-flanking props (the -z open side, cells r=7 either side of x): one knight + one lectern-ish stand
if(dress("SM_Prop_KnightStand_01",       4,7, 0f)) nProp++;
if(dress("SM_Prop_Book_Stand_01",        9,7, 0f)) nProp++;      // was 2nd knight -> book stand (distinct)
// book piles + globes scattered at WALL BASES (back wall r=1 gaps, right wall c=12 gaps) + a couple corners
if(dress("SM_Prop_Globe_01",      2,8, 45f)) nProp++;
if(dress("SM_Prop_Book_Pile_01",  6,1, -30f)) nProp++;
if(dress("SM_Prop_Book_Pile_02", 10,1, 20f)) nProp++;
if(dress("SM_Prop_Book_Pile_03",  2,4, 60f)) nProp++;
if(dress("SM_Prop_Book_Pile_01", 12,3, -90f)) nProp++;
if(dress("SM_Prop_Book_Pile_02", 11,8, -120f)) nProp++;
if(dress("SM_Prop_Book_Stand_01", 3,9, 15f)) nProp++;
if(dress("SM_Prop_Book_Stand_02", 8,8, -20f)) nProp++;
if(dress("SM_Prop_Bookcase_Small_01", 5,1, 180f)) nProp++;
if(dress("SM_Prop_Papers_01",     7,9, 30f)) nProp++;

// --- PLINTH + STEPS (v3 fix 2): the room floor sits on a raised platform ~1.4u tall. Add platform SIDE
// faces (skirt) along the two CUT/NEAR edges (-x/left col and -z/front row) so the bottom of frame reads
// as platform edge, NOT black void triangles; plus a 3-4 step staircase descending toward the camera at
// the near-left. Simple scaled cubes with the stone material (normalized below via the "Plinth"/"Step" prefix).
// Crib: build_room_greybox.cs uses primitive cubes for greybox; here they're the platform skirt + steps.
int nPlinth=0; float PLINTH_H=1.4f;
System.Func<string,Vector3,Vector3,GameObject> plbox=(nm,center,size)=>{
  var b=GameObject.CreatePrimitive(PrimitiveType.Cube); b.name=nm; UnityEngine.Object.DestroyImmediate(b.GetComponent<Collider>());
  b.transform.SetParent(root.transform,true); b.transform.position=center; b.transform.localScale=size; return b; };
{ float roomW=cols*CELL, roomD=rows*CELL;
  float leftX = -(cx0+0.5f)*CELL;   // -x near edge (world x of the left room boundary)
  float frontZ= -(cy0+0.5f)*CELL;   // -z near edge (world z of the front room boundary)
  float skirtT=0.6f;                 // skirt thickness (into the platform)
  // LEFT (-x) skirt: a slab hanging from y=0 down to y=-PLINTH_H along the whole left edge
  plbox("Plinth_Left",  new Vector3(leftX+skirtT*0.5f, -PLINTH_H*0.5f, 0f), new Vector3(skirtT, PLINTH_H, roomD)); nPlinth++;
  // FRONT (-z) skirt: same along the whole front edge
  plbox("Plinth_Front", new Vector3(0f, -PLINTH_H*0.5f, frontZ+skirtT*0.5f), new Vector3(roomW, PLINTH_H, skirtT)); nPlinth++;
  // a solid corner block so the -x/-z corner isn't a gap
  plbox("Plinth_Corner", new Vector3(leftX+skirtT*0.5f, -PLINTH_H*0.5f, frontZ+skirtT*0.5f), new Vector3(skirtT, PLINTH_H, skirtT)); nPlinth++;
  // STAIRCASE at the near-left corner descending toward the camera (-x,-z direction). 4 steps, each drops
  // 0.35u and steps out 0.7u from the platform's -x face, centred near the front-left.
  int nSteps=4; float stepDrop=PLINTH_H/nSteps, stepRun=0.8f, stepW=5.0f;
  float sz = frontZ + roomD*0.28f;   // steps centred toward the front-left of the left edge
  for(int i2=0;i2<nSteps;i2++){
    float topY=-(i2)*stepDrop; float sx=leftX - (i2+0.5f)*stepRun; // march out in -x, each lower
    plbox("Step_"+i2, new Vector3(sx, topY-stepDrop*0.5f, sz), new Vector3(stepRun+0.02f, (PLINTH_H-i2*stepDrop), stepW));
    nPlinth++;
  }
}

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
// v2: walls darkened (0.40->0.28) so the wall-torch wash reads as a warm MIDTONE glow (fix 3: walls catch
// light) rather than a blown L>60 highlight — the tall walls are big camera-facing surfaces, so a bright
// albedo there floods the LIT budget. Floor kept low for the same reason.
Color colFloor=new Color(0.30f,0.29f,0.28f), colWall=new Color(0.28f,0.275f,0.265f), colPillar=new Color(0.42f,0.41f,0.39f), colTomb=new Color(0.52f,0.50f,0.47f), colProp=new Color(0.34f,0.32f,0.30f);
{ int nm2=0;
  foreach(var r in root.GetComponentsInChildren<Renderer>(true)){ if(r==null) continue; string tn=r.transform.root==null?r.name:"";
    // classify by the top-of-root instance name (walk up to the AtelierCrypt direct child)
    var t=r.transform; while(t.parent!=null && t.parent!=root.transform) t=t.parent; string nm3=t.name;
    Color c=colProp; float g=0.05f;
    if(nm3.StartsWith("Floor_")) c=colFloor; else if(nm3.StartsWith("Wall")) c=colWall;
    else if(nm3.StartsWith("Pillar")) { c=colPillar; g=0.08f; } else if(nm3.StartsWith("Sarcophagus")) { c=colTomb; g=0.10f; }
    else if(nm3.StartsWith("Plinth")||nm3.StartsWith("Step")) c=colWall;  // platform skirt + steps = stone
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

// ================= LIGHT RIG v4 — COMMITTED KEY + 3 POOLS (aim near-black 68-78%, lit 2.5-4.5%) ==========
// Panel verdict on v3: the 5-6 pools read as a FLAT WASH — floor lit almost uniformly, wall-top rim read as
// a baked-AO strip, NO committed key, NO true-black anchor. v4 reverses that: ONE strong warm DIRECTIONAL
// KEY from the upper-left raking toward camera IS the main light (long legible cast shadows from the piers/
// tomb/bookcases across the floor), ambient DOWN so the corners fall to legible near-black, and only THREE
// pools (hero + one back-wall torch + arch ember). MoonKey / RimCool / the other 2 wall torches DELETED.
RenderSettings.ambientMode=UnityEngine.Rendering.AmbientMode.Flat;
RenderSettings.ambientLight=new Color(0.040f,0.043f,0.056f);   // v4: ambient DOWN — key + pools carry the frame (tuned)
RenderSettings.reflectionIntensity=0f;
// FIX 1: COMMITTED KEY — one strong warm directional from the upper-left, raking across the room toward the
// camera, SHADOWS ON (soft, strong) so the piers/tomb/bookcases throw long legible cast shadows on the floor.
{ var g=new GameObject("AK_Key"); var L=g.AddComponent<Light>(); L.type=LightType.Directional;
  L.color=new Color(1.0f,0.72f,0.45f); L.intensity=0.86f; L.shadows=LightShadows.Soft; L.shadowStrength=0.85f;
  g.transform.rotation=Quaternion.Euler(50f,205f,0f); }
System.Action<string,Vector3,float,float,Color,bool> pt=(nm,pos,rng,inten,col,shadow)=>{
  var g=new GameObject(nm); var L=g.AddComponent<Light>(); L.type=LightType.Point; L.color=col;
  L.range=rng; L.intensity=inten; L.shadows=shadow?LightShadows.Soft:LightShadows.None; L.shadowStrength=0.7f; g.transform.position=pos; };
Color warm=new Color(1f,0.55f,0.25f);
Vector3 backZ_edge=new Vector3(0f,0f,(cy0+0.5f)*CELL);  // z of the visible back(+z) wall face
// FIX 2: THREE pools only.
// hero pool over the tomb (shadowed):
{ var w6=cellToWorld(6,5); var w7=cellToWorld(7,5); Vector3 mid=(w6+w7)*0.5f;
  pt("AK_TorchSarc", new Vector3(mid.x, 5.2f, mid.z), 11f, 2.8f, warm, true); }
// ONE back-wall torch (washes the back wall face):
{ var wc=cellToWorld(4,1); pt("AK_TorchBack", new Vector3(wc.x, 6.5f, backZ_edge.z-1.3f), 13f, 2.4f, warm, false); }
// arch-void ember near the back-center door:
{ var wa=cellToWorld(6,0); pt("AK_ArchVoid",  new Vector3(wa.x, 5.0f, wa.z+1.0f), 13f, 1.8f, new Color(1f,0.5f,0.2f), false); }

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
capture("atelier_beauty_v4.png", null);

// (b) ALBEDO — all lights off + flat white ambient (so URP/Lit shows base color unlit-ish).
var allLights=UnityEngine.Object.FindObjectsByType<Light>(FindObjectsSortMode.None);
var savedEnabled=new System.Collections.Generic.Dictionary<Light,bool>();
foreach(var L in allLights){ savedEnabled[L]=L.enabled; L.enabled=false; }
var savedAmbMode=RenderSettings.ambientMode; var savedAmb=RenderSettings.ambientLight;
RenderSettings.ambientMode=UnityEngine.Rendering.AmbientMode.Flat; RenderSettings.ambientLight=Color.white;
capture("atelier_albedo_v4.png", null);
// restore lights + ambient
foreach(var kv in savedEnabled) if(kv.Key!=null) kv.Key.enabled=kv.Value;
RenderSettings.ambientMode=savedAmbMode; RenderSettings.ambientLight=savedAmb;

// (c) DEPTH (v2 fix 6: PER-SCENE near/far remap) + (d) NORMAL via replacement shaders.
// Measure the scene's view-space depth range at the contract camera, then feed it to WOS/LinDepthRemap
// via global uniforms so the depth pass has a usable full-range gradient (WOS/LinDepth's hardcoded /80
// saturates this ~64-96 scene to near-white). Fall back to WOS/LinDepth if the remap shader isn't present.
{ var mv=cam.worldToCameraMatrix; float mn=1e9f, mx=-1e9f;
  foreach(var r in root.GetComponentsInChildren<Renderer>()){ var b=r.bounds; for(int i=0;i<8;i++){ Vector3 c=new Vector3((i&1)==0?b.min.x:b.max.x,(i&2)==0?b.min.y:b.max.y,(i&4)==0?b.min.z:b.max.z); float d=-(mv.MultiplyPoint(c)).z; if(d<mn)mn=d; if(d>mx)mx=d; } }
  float pad=(mx-mn)*0.04f; mn-=pad; mx+=pad;   // small margin so extremes don't clip to pure 0/1
  Shader.SetGlobalFloat("_WOSDepthNear", mn); Shader.SetGlobalFloat("_WOSDepthFar", mx);
  var shRemap=Shader.Find("WOS/LinDepthRemap"); var shDepth=Shader.Find("WOS/LinDepth"); var shNorm=Shader.Find("WOS/ViewNormal");
  LOG("depth remap near="+mn.ToString("F2")+" far="+mx.ToString("F2")+" (Remap="+(shRemap!=null)+" LinDepth="+(shDepth!=null)+" ViewNormal="+(shNorm!=null)+")");
  if(shRemap!=null) capture("atelier_depth_v4.png", shRemap);
  else if(shDepth!=null){ capture("atelier_depth_v4.png", shDepth); LOG("  WARN: remap shader missing -> used hardcoded /80 WOS/LinDepth"); }
  else LOG("  MISSING both depth shaders");
  if(shNorm!=null) capture("atelier_normal_v4.png", shNorm); else LOG("  MISSING WOS/ViewNormal");
}

LOG("BUILT floor="+nFloor+" walls="+nWall+" pillars="+nPil+" sarc="+nSarc+" props="+nProp+" plinth/steps="+nPlinth);
LOG("captures -> Captures-Durable/atelier_{beauty,albedo,depth,normal}_v4.png");
System.IO.File.WriteAllText("/home/unity/worldos-unity/atelier_report.txt", sb.ToString());
return "OK atelier v4: floor="+nFloor+" walls="+nWall+" pillars="+nPil+" sarc="+nSarc+" props="+nProp+" plinth="+nPlinth+" (report -> atelier_report.txt)";
