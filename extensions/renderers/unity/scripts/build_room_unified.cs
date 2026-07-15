// build_room_unified.cs — UNIFY-THE-FRAMES (Option 2, owner-ratified 2026-07-15): render a room's
// greybox + depth + normal FROM THE CLIENT'S OWN CAMERA, and emit the box list as data so the
// runtime occluders ARE the boxes the depth was rendered from. One source (the authored geometry),
// one camera (this scene's), one box list (render conditioning == runtime occlusion == the same
// footprints the engine blocks). This supersedes qa/greybox_render_headless.py as the render source
// for NEW rooms — the Python renderer stays as the QA re-projection tool only.
//
// Reads /home/unity/worldos-unity/room_geometry.json (tools/author_room_geometry.py output):
//   { cols, rows, material, camera_fit, wall_height?, walls:[[c,r]],
//     props:[{id, kind, cells:[[c,r]]}], door_cells:[[c,r]] }
// Writes to /home/unity/worldos-unity/Captures-Durable/:
//   room_greybox.png            lit greybox (recall gate + optional img2img base)
//   room_greybox_depth.png      linear depth (the flux ControlNet conditioning)
//   room_greybox_normal.png     view-space normals (sidecar)
//   room_boxes.json             EVERY rendered box: {name, kind, center:[x,y,z], size:[x,y,z]}
//                               -> becomes the client's depth-proxy occluder set for this room.
//   unity-mcp code execute --no-safety-checks -f build_room_unified.cs
AssetDatabase.Refresh();
var sb=new System.Text.StringBuilder();
Camera cam=Camera.main; if(cam==null && Camera.allCameras.Length>0) cam=Camera.allCameras[0]; if(cam==null) return "no cam";

// GEX44 box paths by default (this is a box-side editor tool, precedent build_room_greybox.cs);
// env-overridable so a non-box host can point it elsewhere (evaos review, #1575).
string GEO=System.Environment.GetEnvironmentVariable("WORLDOS_ROOM_GEO") ?? "/home/unity/worldos-unity/room_geometry.json";
if(!System.IO.File.Exists(GEO)) return "no geometry json: "+GEO;
var geo=MiniJson.Parse(System.IO.File.ReadAllText(GEO)) as System.Collections.Generic.Dictionary<string,object>;
if(geo==null) return "geometry parse failed";
int cols=geo.ContainsKey("cols")?System.Convert.ToInt32(geo["cols"]):14;
int rows=geo.ContainsKey("rows")?System.Convert.ToInt32(geo["rows"]):11;
bool wood = geo.ContainsKey("material") && ((geo["material"] as string)??"").ToLowerInvariant().Contains("wood"); // substring: authored values are e.g. "worn wooden planks" (codex review, #1575)
bool camFit = geo.ContainsKey("camera_fit") && geo["camera_fit"] is bool && (bool)geo["camera_fit"];
float wallH = geo.ContainsKey("wall_height") ? System.Convert.ToSingle(geo["wall_height"]) : (camFit ? 5f : 9f);
float cx0=(cols-1)/2.0f, cy0=(rows-1)/2.0f;
System.Func<int,int,Vector3> cellToWorld=(c,r)=> new Vector3((c-cx0)*2.0f, 0f, (cy0-r)*2.0f);

// --- THE CLIENT CAMERA (dimetric 30/45, pulled back 80) at the per-room ortho -------------------
// camera_fit: fit the ortho so the grid diamond fills ~96% of frame width — the EXACT math of
// qa/greybox_render_headless._fit_ortho_size, ported so the render and the runtime cameraPin agree
// to the digit. Basis identical to the fixed rig; only the ortho scale changes (the #1543 contract).
Quaternion crot=Quaternion.Euler(30f,45f,0f);
float ASPECT=1344f/768f, FILL=0.96f;
float ortho=13f;
if(camFit){
  Vector3 rightAx=crot*Vector3.right, upAx=crot*Vector3.up;
  float maxR=0f, maxU=0f;
  float hx=(cols/2f)*2.0f, hz=(rows/2f)*2.0f;
  foreach(var sgn in new[]{ new Vector2(1,1), new Vector2(1,-1), new Vector2(-1,1), new Vector2(-1,-1) }){
    Vector3 corner=new Vector3(hx*sgn.x, 0f, hz*sgn.y);
    maxR=Mathf.Max(maxR, Mathf.Abs(Vector3.Dot(corner,rightAx)));
    maxU=Mathf.Max(maxU, Mathf.Abs(Vector3.Dot(corner,upAx)));
  }
  ortho=Mathf.Max(maxR/(ASPECT*FILL), maxU/FILL);
}
cam.orthographic=true; cam.orthographicSize=ortho; cam.nearClipPlane=0.3f; cam.farClipPlane=500f;
cam.transform.rotation=crot; cam.transform.position=-(crot*Vector3.forward)*80f;
cam.clearFlags=CameraClearFlags.SolidColor; cam.backgroundColor=new Color(0.05f,0.05f,0.07f);

// sweep prior parts + lights
{ var _k=new System.Collections.Generic.List<GameObject>(); foreach(var g in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None)){ if(g==null) continue; if(g.name.StartsWith("GB_") || g.GetComponent<Light>()!=null) _k.Add(g); } foreach(var g in _k){ if(g!=null) UnityEngine.Object.DestroyImmediate(g); } }
int hidden=0; foreach(var r in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None)){ if(r.enabled){r.enabled=false;hidden++;} }

// --- procedural stone/wood texture (unchanged from build_room_greybox.cs — proven paint base) ---
int TS=256;
var stoneAlb=new Texture2D(TS,TS,TextureFormat.RGB24,true);
var stoneNrm=new Texture2D(TS,TS,TextureFormat.RGB24,true);
{
  float[,] hh=new float[TS,TS];
  for(int y=0;y<TS;y++) for(int x=0;x<TS;x++){
    float u=(float)x/TS, v=(float)y/TS;
    float n=0f, amp=0.5f, freq=5f;
    for(int o=0;o<5;o++){ n+=Mathf.PerlinNoise(u*freq+o*13.1f, v*freq+o*7.7f)*amp; amp*=0.5f; freq*=2f; }
    float crack=Mathf.PerlinNoise(u*3.1f+40f, v*3.1f+40f);
    float dark = crack<0.30f ? (0.30f-crack)*1.4f : 0f;
    if(wood){
      float plankH=0.115f; float pf=v/plankH; float pFrac=pf-Mathf.Floor(pf);
      float seamW=0.045f/plankH; float seam=(pFrac<seamW||pFrac>1f-seamW)?0.32f:0f;
      float grain=(Mathf.PerlinNoise(u*2.5f, v*26f)-0.5f)*0.18f;
      hh[x,y]=n - seam*0.8f + grain*0.6f;
      float g=Mathf.Clamp01(0.46f + (n-0.5f)*0.34f - dark*0.6f - seam + grain);
      stoneAlb.SetPixel(x,y,new Color(g, g*0.72f, g*0.46f));
    } else {
      float courseH=0.135f, brickW=0.17f, jointW=0.05f;
      float cf=v/courseH; float cFrac=cf-Mathf.Floor(cf); int courseI=(int)Mathf.Floor(cf);
      float uo=u + (courseI%2==0?0f:0.085f);
      float bf=uo/brickW; float bFrac=bf-Mathf.Floor(bf);
      float horiz=(cFrac<jointW/courseH||cFrac>1f-jointW/courseH)?1f:0f;
      float vert =(bFrac<jointW/brickW||bFrac>1f-jointW/brickW)?1f:0f;
      float seam=Mathf.Max(horiz,vert)*0.26f;
      hh[x,y]=n - seam*0.7f;
      float g=Mathf.Clamp01(0.5f + (n-0.5f)*0.55f - dark - seam);
      stoneAlb.SetPixel(x,y,new Color(g,g*0.985f,g*0.96f));
    }
  }
  for(int y=0;y<TS;y++) for(int x=0;x<TS;x++){
    int xp=(x+1)%TS, yp=(y+1)%TS;
    float dx=(hh[xp,y]-hh[x,y])*6f, dy=(hh[x,yp]-hh[x,y])*6f;
    Vector3 nv=new Vector3(-dx,-dy,1f).normalized;
    stoneNrm.SetPixel(x,y,new Color(nv.x*0.5f+0.5f, nv.y*0.5f+0.5f, nv.z*0.5f+0.5f));
  }
  stoneAlb.wrapMode=TextureWrapMode.Repeat; stoneNrm.wrapMode=TextureWrapMode.Repeat;
  stoneAlb.Apply(); stoneNrm.Apply();
}

// --- primitive factories: every volume is RECORDED (as its AABB) for room_boxes.json ------------
// MOLDED FORMS (owner, post-v3.3 "everything is squares"): pillars get plinth+cylindrical shaft+
// capital, tombs get curved lid ridges, braziers get pedestal+bowl, niches and doors get ARCHES.
// The depth map then carries CURVES, so flux paints molded stone instead of box salad. Occluders
// stay AABBs of these forms (box granularity is fine for masking).
var boxRecords=new System.Collections.Generic.List<string>();
System.Func<string,string,Vector3,Vector3,Color,GameObject> box=(nm,kindTag,center,size,col)=>{
  var b=GameObject.CreatePrimitive(PrimitiveType.Cube); b.name="GB_"+nm; UnityEngine.Object.DestroyImmediate(b.GetComponent<Collider>());
  b.transform.position=center; b.transform.localScale=size;
  var m=new Material(Shader.Find("Standard")); m.color=col; m.SetFloat("_Glossiness",0.04f);
  m.mainTexture=stoneAlb;
  m.SetTexture("_BumpMap", stoneNrm); m.EnableKeyword("_NORMALMAP"); m.SetFloat("_BumpScale", 1.0f);
  float tx=Mathf.Max(1.5f,(size.x+size.z)*0.35f), ty=Mathf.Max(1.5f,(size.x+size.y+size.z)*0.18f);
  m.mainTextureScale=new Vector2(tx,ty); m.SetTextureScale("_BumpMap", new Vector2(tx,ty));
  b.GetComponent<Renderer>().sharedMaterial=m;
  boxRecords.Add("{\"name\":\""+nm+"\",\"kind\":\""+kindTag+"\",\"center\":["
    +center.x.ToString("F3")+","+center.y.ToString("F3")+","+center.z.ToString("F3")+"],\"size\":["
    +size.x.ToString("F3")+","+size.y.ToString("F3")+","+size.z.ToString("F3")+"]}");
  return b; };

// elliptical CYLINDER (rx/rz half-extents, full height h) — recorded as its AABB.
System.Func<string,string,Vector3,float,float,float,Color,GameObject> cyl=(nm,kindTag,baseCenter,rx,rz,h,col)=>{
  var b=GameObject.CreatePrimitive(PrimitiveType.Cylinder); b.name="GB_"+nm; UnityEngine.Object.DestroyImmediate(b.GetComponent<Collider>());
  b.transform.position=new Vector3(baseCenter.x, baseCenter.y+h/2f, baseCenter.z);
  b.transform.localScale=new Vector3(rx*2f, h/2f, rz*2f);
  var m=new Material(Shader.Find("Standard")); m.color=col; m.SetFloat("_Glossiness",0.04f);
  m.mainTexture=stoneAlb; m.SetTexture("_BumpMap", stoneNrm); m.EnableKeyword("_NORMALMAP");
  m.mainTextureScale=new Vector2(Mathf.Max(1.5f,(rx+rz)*1.4f), Mathf.Max(1.5f,h*0.35f)); m.SetTextureScale("_BumpMap", m.mainTextureScale);
  b.GetComponent<Renderer>().sharedMaterial=m;
  boxRecords.Add("{\"name\":\""+nm+"\",\"kind\":\""+kindTag+"\",\"center\":["
    +baseCenter.x.ToString("F3")+","+(baseCenter.y+h/2f).ToString("F3")+","+baseCenter.z.ToString("F3")+"],\"size\":["
    +(rx*2f).ToString("F3")+","+h.ToString("F3")+","+(rz*2f).ToString("F3")+"]}");
  return b; };
// HORIZONTAL cylinder (arch/lid ridge): axis along X or Z, radius r, length len, centered at c.
System.Func<string,string,Vector3,float,float,bool,Color,GameObject> hcyl=(nm,kindTag,c2,r2,len,alongX,col)=>{
  var b=GameObject.CreatePrimitive(PrimitiveType.Cylinder); b.name="GB_"+nm; UnityEngine.Object.DestroyImmediate(b.GetComponent<Collider>());
  b.transform.position=c2;
  b.transform.rotation = alongX ? Quaternion.Euler(0f,0f,90f) : Quaternion.Euler(90f,0f,0f);
  b.transform.localScale = new Vector3(r2*2f, len/2f, r2*2f);
  var m=new Material(Shader.Find("Standard")); m.color=col; m.SetFloat("_Glossiness",0.04f);
  m.mainTexture=stoneAlb; m.SetTexture("_BumpMap", stoneNrm); m.EnableKeyword("_NORMALMAP");
  m.mainTextureScale=new Vector2(2f,2f); m.SetTextureScale("_BumpMap", m.mainTextureScale);
  b.GetComponent<Renderer>().sharedMaterial=m;
  float ax = alongX?len:r2*2f, az = alongX?r2*2f:len;
  boxRecords.Add("{\"name\":\""+nm+"\",\"kind\":\""+kindTag+"\",\"center\":["
    +c2.x.ToString("F3")+","+c2.y.ToString("F3")+","+c2.z.ToString("F3")+"],\"size\":["
    +ax.ToString("F3")+","+(r2*2f).ToString("F3")+","+az.ToString("F3")+"]}");
  return b; };

// floor + carved flagstone grout (floor boxes are recorded but flagged kind=floor so the client
// skips them as occluders — the floor never occludes an actor).
{ box("Floor","floor", new Vector3(0f,-0.05f,0f), new Vector3(cols*2.0f, 0.1f, rows*2.0f), new Color(0.42f,0.41f,0.40f)); }
{ Color grout=new Color(0.20f,0.19f,0.18f); float gy=0.015f, gw=0.13f;
  for(int c=1;c<cols;c++){ float x=(c-cx0)*2.0f-1.0f; box("FloorGroutV"+c,"floor", new Vector3(x,gy,0f), new Vector3(gw,0.05f,rows*2.0f), grout); }
  for(int r=1;r<rows;r++){ float z=(cy0-r)*2.0f+1.0f; box("FloorGroutH"+r,"floor", new Vector3(0f,gy,z), new Vector3(cols*2.0f,0.05f,gw), grout); }
}
// ★ NO CEILING — EVER (iso-CRPG convention; guard tripwire kept from build_room_greybox.cs).

// --- walls + props FROM THE AUTHORED GEOMETRY ONLY --------------------------------------------
// The old script built its own monolithic walls + pilasters; under the extent contract (#1543) the
// geometry's wall_run props ARE the walls (continuous per-run boxes, cutaway height, split at
// doors). No wall_runs in the geometry -> ERROR (fix the geometry, don't invent walls here).
var props=geo.ContainsKey("props")?geo["props"] as System.Collections.Generic.List<object>:null;
int np=0, nWallRuns=0;
if(props!=null) foreach(var po in props){ var p=po as System.Collections.Generic.Dictionary<string,object>; if(p==null) continue;
  string kind=(p.ContainsKey("kind")?p["kind"] as string:"prop")??"prop";
  string pid=(p.ContainsKey("id")?p["id"] as string:("prop"+np))??("prop"+np);
  var cells=p.ContainsKey("cells")?p["cells"] as System.Collections.Generic.List<object>:null; if(cells==null||cells.Count==0) continue;
  // collect world extents of the run
  float minX=float.MaxValue,maxX=float.MinValue,minZ=float.MaxValue,maxZ=float.MinValue;
  float rSum=0f; int nC=0;
  foreach(var co in cells){ var cc=co as System.Collections.Generic.List<object>; if(cc==null||cc.Count<2) continue;
    int c=System.Convert.ToInt32(cc[0]); int r=System.Convert.ToInt32(cc[1]); var w=cellToWorld(c,r);
    minX=Mathf.Min(minX,w.x); maxX=Mathf.Max(maxX,w.x); minZ=Mathf.Min(minZ,w.z); maxZ=Mathf.Max(maxZ,w.z);
    rSum+=r; nC++; }
  if(nC==0) continue;
  if(kind=="wall_run"){
    // ONE CONTINUOUS box spanning the run at wallH (never per-cell crenellation, #1539).
    float bx=(minX+maxX)/2f, bz=(minZ+maxZ)/2f;
    float sx=(maxX-minX)+2.0f, sz=(maxZ-minZ)+2.0f;
    // thin the run along its short axis so doorway gaps read (a run is 1 cell thick);
    // EXTEND 0.6 past each end along the long axis so perpendicular runs OVERLAP at corners
    // (owner v3.4 punch list: "the corners of the walls don't connect").
    if(sx>sz){ sz=Mathf.Min(sz,1.2f); sx+=1.2f; } else { sx=Mathf.Min(sx,1.2f); sz+=1.2f; }
    box(pid,"wall_run", new Vector3(bx,wallH/2f,bz), new Vector3(sx,wallH,sz), new Color(0.5f,0.49f,0.48f));
    nWallRuns++;
    continue;
  }
  // prop kinds — MOLDED composites (owner post-v3.3: "not just squares"); heights mirror _KIND_SPECS.
  float ph=2.6f, pw=1.4f; Color pc=new Color(0.52f,0.5f,0.48f);
  {
    float bx0=(minX+maxX)/2f, bz0=(minZ+maxZ)/2f;
    float exX=(maxX-minX)/2f, exZ=(maxZ-minZ)/2f;
    if(kind.Contains("pillar")||kind.Contains("column")){
      // plinth + ELLIPTICAL shaft + capital — a molded pier, not a slab.
      // SLIM plinth + NO slab capital (owner v3.4: "block with a pillar in the block" — the fat
      // plinth/capital slabs read as separate blocks). Low chamfer base + a thin ring collar top.
      float hx=exX+1.0f, hz=exZ+1.0f;
      box(pid+"_plinth",kind,new Vector3(bx0,0.14f,bz0),new Vector3(hx*1.7f,0.28f,hz*1.7f),new Color(0.60f,0.59f,0.56f));
      cyl(pid+"_shaft",kind,new Vector3(bx0,0.28f,bz0),hx*0.78f,hz*0.78f,6.8f,new Color(0.64f,0.63f,0.60f));
      cyl(pid+"_collar",kind,new Vector3(bx0,6.85f,bz0),hx*0.92f,hz*0.92f,0.45f,new Color(0.60f,0.59f,0.56f));
      np++; continue;
    }
    if(kind.Contains("sarcophagus")){
      // stepped base + inset tier + CURVED lid ridge along the long axis.
      float hx=exX+0.95f, hz=exZ+0.95f;
      box(pid+"_base",kind,new Vector3(bx0,0.55f,bz0),new Vector3(hx*2f,1.1f,hz*2f),new Color(0.62f,0.60f,0.56f));
      box(pid+"_tier",kind,new Vector3(bx0,1.35f,bz0),new Vector3(hx*1.7f,0.5f,hz*1.7f),new Color(0.66f,0.64f,0.60f));
      bool longX = hx>=hz;
      hcyl(pid+"_lid",kind,new Vector3(bx0,1.75f,bz0),Mathf.Min(hx,hz)*0.55f,(longX?hx:hz)*1.5f,longX,new Color(0.70f,0.68f,0.63f));
      np++; continue;
    }
    if(kind.Contains("stone_well")){
      // raised tier w/ curved cap (the tomb's upper tier when authored separately).
      float hx=exX+0.9f, hz=exZ+0.9f; bool longX = hx>=hz;
      box(pid+"_tier",kind,new Vector3(bx0,1.55f,bz0),new Vector3(hx*1.6f,0.9f,hz*1.6f),new Color(0.66f,0.64f,0.60f));
      hcyl(pid+"_cap",kind,new Vector3(bx0,2.15f,bz0),Mathf.Min(hx,hz)*0.5f,(longX?hx:hz)*1.35f,longX,new Color(0.70f,0.68f,0.63f));
      np++; continue;
    }
    if(kind.Contains("table")){
      // tavern table: slim pedestal + WIDE overhanging oval top (molded — the stone_well trough
      // read failed the tavern v1 design gate; a table is pedestal+disc, not tier+lid).
      // heights are CONDITIONING CUES: measured (tavern v3 base) a 1.33-high top = 0 grey delta in
      // the remapped depth -> flux dropped/moved every table. 2.0-surface chunky tables match the
      // h2.0 flat proxies that SURVIVED in the promoted truegrey tavern.
      float hx=exX+0.85f, hz=exZ+0.85f; float pr=Mathf.Min(hx,hz)*0.55f;
      cyl(pid+"_pedestal",kind,new Vector3(bx0,0f,bz0),pr,pr,1.6f,new Color(0.46f,0.38f,0.28f));
      cyl(pid+"_top",kind,new Vector3(bx0,1.6f,bz0),hx*1.10f,hz*1.10f,0.4f,new Color(0.68f,0.56f,0.40f));
      np++; continue;
    }
    if(kind=="bar"){
      // bar counter: solid dark body + LIGHT overhanging countertop lip (v1 gate: a plain dark box
      // edge-on read as a floor stripe — the lip + albedo contrast is the depth/paint cue).
      float hx=exX+0.9f, hz=exZ+0.9f;
      box(pid+"_body",kind,new Vector3(bx0,1.3f,bz0),new Vector3(hx*1.7f,2.6f,hz*1.7f),new Color(0.40f,0.32f,0.23f));
      box(pid+"_top",kind,new Vector3(bx0,2.73f,bz0),new Vector3(hx*2.1f,0.26f,hz*2.1f),new Color(0.72f,0.60f,0.42f));
      np++; continue;
    }
    if(kind.Contains("brazier")){
      // pedestal column + fire BOWL — reads as ironwork, not a crate.
      cyl(pid+"_pedestal",kind,new Vector3(bx0,0f,bz0),0.38f,0.38f,1.7f,new Color(0.42f,0.39f,0.34f));
      var bowl=GameObject.CreatePrimitive(PrimitiveType.Sphere); bowl.name="GB_"+pid+"_bowl"; UnityEngine.Object.DestroyImmediate(bowl.GetComponent<Collider>());
      bowl.transform.position=new Vector3(bx0,1.85f,bz0); bowl.transform.localScale=new Vector3(1.5f,0.75f,1.5f);
      var bm=new Material(Shader.Find("Standard")); bm.color=new Color(0.86f,0.62f,0.30f); bm.SetFloat("_Glossiness",0.15f);
      bowl.GetComponent<Renderer>().sharedMaterial=bm;
      boxRecords.Add("{\"name\":\""+pid+"_bowl\",\"kind\":\""+kind+"\",\"center\":["+bx0.ToString("F3")+",1.85,"+bz0.ToString("F3")+"],\"size\":[1.5,0.75,1.5]}");
      np++; continue;
    }
    if(kind.Contains("altar")){
      // wall niche: recess slab + ARCHED header (half-sunk horizontal cylinder).
      float hx=exX+0.9f, hz=exZ+0.9f; bool longX = hx>=hz;
      box(pid+"_slab",kind,new Vector3(bx0,1.0f,bz0),new Vector3(hx*2f,2.0f,hz*2f),new Color(0.60f,0.58f,0.55f));
      hcyl(pid+"_arch",kind,new Vector3(bx0,2.35f,bz0),Mathf.Min(hx,hz)*0.75f,(longX?hx:hz)*1.9f,longX,new Color(0.62f,0.60f,0.57f));
      np++; continue;
    }
    if(kind.Contains("barrel")){
      cyl(pid,kind,new Vector3(bx0,0f,bz0),0.62f,0.62f,1.5f,new Color(0.64f,0.56f,0.44f)); // light oak — dark-on-dark barrels vanished at the tavern v1 gate
      np++; continue;
    }
  }
  // remaining flat kinds (the molded block above already consumed pillar/sarcophagus/stone_well/
  // brazier/altar/barrel and `continue`d) — plain box proxies:
  if(kind.Contains("large_tree")){ ph=9.0f; pw=1.8f; pc=new Color(0.23f,0.29f,0.20f); }
  else if(kind.Contains("bar")||kind.Contains("table")||kind.Contains("pew")||kind.Contains("market_stall")){ ph=2.0f; pw=1.8f; pc=new Color(0.6f,0.58f,0.55f); }
  else if(kind.Contains("campfire")){ ph=0.6f; pw=1.1f; pc=new Color(0.78f,0.43f,0.16f); }
  else if(kind.Contains("bedroll")){ ph=0.28f; pw=1.1f; pc=new Color(0.43f,0.38f,0.31f); }
  else if(kind.Contains("fallen_log")){ ph=0.8f; pw=1.1f; pc=new Color(0.35f,0.29f,0.21f); }
  else if(kind.Contains("boulder")){ ph=2.0f; pw=1.4f; pc=new Color(0.43f,0.44f,0.42f); }
  else if(kind.Contains("supply_crates")||kind.Contains("cart")){ ph=1.5f; pw=1.4f; pc=new Color(0.45f,0.43f,0.38f); }
  else if(kind.Contains("rubble")||kind.Contains("crate")){ ph=1.4f; pw=1.5f; pc=new Color(0.45f,0.43f,0.4f); }
  // MULTI-CELL props render as ONE box spanning their cells (a 5x2 tomb is one monument, not ten
  // cubes) — this is what makes the depth cue STRONG for low props (the crypt-escape lesson: the
  // per-cell 2x2 coffin was invisible to the CN; a single spanning box is not).
  {
    float bx=(minX+maxX)/2f, bz=(minZ+maxZ)/2f;
    float sx=Mathf.Max(pw,(maxX-minX)+pw), sz=Mathf.Max(pw,(maxZ-minZ)+pw);
    box(pid,kind, new Vector3(bx,ph/2f,bz), new Vector3(sx,ph,sz), pc);
    np++;
  }
}
// DOOR ARCHES (owner: doors must READ as doorways, and molded — not bare gaps): jambs + a
// horizontal arch cylinder + lintel cap over every authored door gap, oriented by its wall.
{
  var dcs=geo.ContainsKey("door_cells")?geo["door_cells"] as System.Collections.Generic.List<object>:null;
  int dn=0;
  if(dcs!=null) foreach(var dco in dcs){
    var dc=dco as System.Collections.Generic.List<object>; if(dc==null||dc.Count<2) continue;
    int c=System.Convert.ToInt32(dc[0]); int r=System.Convert.ToInt32(dc[1]);
    var w=cellToWorld(c,r);
    bool onNS = (r==0)||(r==rows-1);            // door in a north/south wall -> arch spans X
    Color jc=new Color(0.63f,0.62f,0.59f);
    if(onNS){
      box("door"+dn+"_jambL","door_frame",new Vector3(w.x-1.25f,wallH*0.45f,w.z),new Vector3(0.5f,wallH*0.9f,1.3f),jc);
      box("door"+dn+"_jambR","door_frame",new Vector3(w.x+1.25f,wallH*0.45f,w.z),new Vector3(0.5f,wallH*0.9f,1.3f),jc);
      hcyl("door"+dn+"_arch","door_frame",new Vector3(w.x,wallH*0.82f,w.z),0.55f,2.6f,true,jc);
      box("door"+dn+"_lintel","door_frame",new Vector3(w.x,wallH*0.98f,w.z),new Vector3(3.1f,0.45f,1.35f),jc);
    } else {
      box("door"+dn+"_jambL","door_frame",new Vector3(w.x,wallH*0.45f,w.z-1.25f),new Vector3(1.3f,wallH*0.9f,0.5f),jc);
      box("door"+dn+"_jambR","door_frame",new Vector3(w.x,wallH*0.45f,w.z+1.25f),new Vector3(1.3f,wallH*0.9f,0.5f),jc);
      hcyl("door"+dn+"_arch","door_frame",new Vector3(w.x,wallH*0.82f,w.z),0.55f,2.6f,false,jc);
      box("door"+dn+"_lintel","door_frame",new Vector3(w.x,wallH*0.98f,w.z),new Vector3(1.35f,0.45f,3.1f),jc);
    }
    dn++;
  }
}
if(nWallRuns==0){
  // cleanup before erroring: this run already hid scene renderers + destroyed lights + spawned GB_ parts —
  // leaving them makes the ERROR path corrupt the editor scene state (codex review, #1575).
  foreach(var g in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None)){ if(g!=null && g.name.StartsWith("GB_")) UnityEngine.Object.DestroyImmediate(g); }
  foreach(var r in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None)){ if(r!=null) r.enabled=true; }
  if(stoneAlb!=null) UnityEngine.Object.DestroyImmediate(stoneAlb);
  if(stoneNrm!=null) UnityEngine.Object.DestroyImmediate(stoneNrm);
  sb.AppendLine("ERROR: geometry has no wall_run props — author under the extent contract (#1543) first (scene restored).");
  return sb.ToString();
}

// lighting (proven greybox rig from build_room_greybox.cs)
foreach(var ln in new[]{"GB_Key","GB_Fill","GB_WallWash"}){ var o=GameObject.Find(ln); if(o!=null) UnityEngine.Object.DestroyImmediate(o); }
{ var lg=new GameObject("GB_Key"); var L=lg.AddComponent<Light>(); L.type=LightType.Directional; L.color=new Color(1f,0.93f,0.82f); L.intensity=0.78f; L.shadows=LightShadows.Soft; L.shadowStrength=0.55f; lg.transform.rotation=Quaternion.Euler(50f,35f,0f); }
{ var fg=new GameObject("GB_Fill"); var F=fg.AddComponent<Light>(); F.type=LightType.Directional; F.color=new Color(0.52f,0.58f,0.74f); F.intensity=0.62f; fg.transform.rotation=Quaternion.Euler(40f,210f,0f); }
{ var wg=new GameObject("GB_WallWash"); var Wl=wg.AddComponent<Light>(); Wl.type=LightType.Directional; Wl.color=new Color(0.46f,0.52f,0.70f); Wl.intensity=0.42f; Wl.shadows=LightShadows.None; wg.transform.rotation=Quaternion.Euler(18f,225f,0f); }
RenderSettings.ambientMode=UnityEngine.Rendering.AmbientMode.Flat; RenderSettings.ambientLight=new Color(0.28f,0.30f,0.36f);

// capture greybox + depth + normal at 1344x768 through THIS camera at THIS ortho
int W=1344,Hh=768; var rt=new RenderTexture(W,Hh,24,RenderTextureFormat.ARGB32); rt.Create();
float pa2=cam.aspect; var pt=cam.targetTexture; cam.targetTexture=rt; cam.aspect=(float)W/Hh; cam.Render();
var pAct=RenderTexture.active; RenderTexture.active=rt; var t2=new Texture2D(W,Hh,TextureFormat.RGB24,false); t2.ReadPixels(new Rect(0,0,W,Hh),0,0); t2.Apply(); RenderTexture.active=pAct; cam.targetTexture=pt; cam.aspect=pa2;
System.IO.Directory.CreateDirectory("/home/unity/worldos-unity/Captures-Durable");
System.IO.File.WriteAllBytes("/home/unity/worldos-unity/Captures-Durable/room_greybox.png", t2.EncodeToPNG());
System.Action<string,string> _capPass=(shaderName,outName)=>{
  var sh=Shader.Find(shaderName); if(sh==null){ sb.AppendLine("MISSING shader "+shaderName); return; }
  var _prt=cam.targetTexture; var _pa=cam.aspect; cam.targetTexture=rt; cam.aspect=(float)W/Hh;
  cam.RenderWithShader(sh, "");
  var _pa3=RenderTexture.active; RenderTexture.active=rt; var _tp=new Texture2D(W,Hh,TextureFormat.RGB24,false); _tp.ReadPixels(new Rect(0,0,W,Hh),0,0); _tp.Apply(); RenderTexture.active=_pa3;
  cam.targetTexture=_prt; cam.aspect=_pa;
  System.IO.File.WriteAllBytes("/home/unity/worldos-unity/Captures-Durable/"+outName, _tp.EncodeToPNG());
  UnityEngine.Object.DestroyImmediate(_tp);
};
_capPass("WOS/ViewNormal","room_greybox_normal.png");
// DEPTH: WOS/LinDepthRemap over the scene's measured view-depth range (the atelier lane already
// solved LinDepth's hardcoded-/80 near-white saturation — global _WOSDepthNear/_WOSDepthFar
// uniforms, near=WHITE per the CN depth convention). Range = min/max distance of the room's
// bounding corners along the view forward, padded.
{
  Vector3 fwd=crot*Vector3.forward;
  // hy2 = the ACTUAL scene ceiling (wallH), not a fixed 8+ pad — padding the remap range with
  // unused headroom flattens low furniture into a sliver of the depth band (tavern v3 base: tables
  // at h1.3 in a 9-unit range conditioned so weakly that flux relocated/dropped them; crypt only
  // survived because 6.8-unit pillars filled the range).
  float hx2=(cols/2f)*2.0f, hz2=(rows/2f)*2.0f, hy2=wallH+0.5f;
  float dMin=float.MaxValue, dMax=float.MinValue;
  for(int i=0;i<8;i++){
    Vector3 p=new Vector3(((i&1)==0?-hx2:hx2), ((i&2)==0?0f:hy2), ((i&4)==0?-hz2:hz2));
    float d=80f+Vector3.Dot(p,fwd); dMin=Mathf.Min(dMin,d); dMax=Mathf.Max(dMax,d);
  }
  Shader.SetGlobalFloat("_WOSDepthNear", dMin-1f);
  Shader.SetGlobalFloat("_WOSDepthFar",  dMax+1f);
  _capPass("WOS/LinDepthRemap","room_greybox_depth.png");
}
UnityEngine.Object.DestroyImmediate(t2); rt.Release(); UnityEngine.Object.DestroyImmediate(rt);

// ★ THE UNIFICATION PAYLOAD: the exact box list the depth/paint was rendered from, as data.
// The client builds its depth-proxy occluders from THIS (kind!=floor entries), so what masks an
// actor at runtime is byte-derived from what conditioned the paint.
System.IO.File.WriteAllText("/home/unity/worldos-unity/Captures-Durable/room_boxes.json",
  "{\"version\":1,\"ortho\":"+ortho.ToString("F4")+",\"cols\":"+cols+",\"rows\":"+rows+",\"boxes\":[\n"
  + string.Join(",\n", boxRecords) + "\n]}");

// cleanup (scene NOT saved; boxes persist as JSON)
{ var _gb=new System.Collections.Generic.List<GameObject>(); var _mat=new System.Collections.Generic.HashSet<Material>();
  foreach(var g in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None)){ if(g==null) continue; if(g.name.StartsWith("GB_")){ _gb.Add(g); var _r=g.GetComponent<Renderer>(); if(_r!=null && _r.sharedMaterial!=null) _mat.Add(_r.sharedMaterial); } }
  foreach(var g in _gb){ if(g!=null) UnityEngine.Object.DestroyImmediate(g); }
  foreach(var m in _mat){ if(m!=null) UnityEngine.Object.DestroyImmediate(m); }
  if(stoneAlb!=null) UnityEngine.Object.DestroyImmediate(stoneAlb);
  if(stoneNrm!=null) UnityEngine.Object.DestroyImmediate(stoneNrm);
}
sb.AppendLine("unified greybox "+cols+"x"+rows+" ortho="+ortho.ToString("F4")+" wall_runs="+nWallRuns+" props="+np+" boxes="+boxRecords.Count+" (hidden="+hidden+")");
return sb.ToString();
