// build_room_greybox.cs — render a camera-pinned GREYBOX from a room's authored scene_grid geometry,
// so img2img paints a room whose props sit EXACTLY where the combat pathing obstacles are (gfx M-E,
// the authored-pathing fix: one scene_grid -> the painted room AND the pathing, never decoupled).
//
// Reads /home/unity/worldos-unity/room_geometry.json (exported by qa/export_scene_grid.py):
//   { cols, rows, walls:[[c,r]], props:[{kind, cells:[[c,r]]}] }
// Builds floor + perimeter walls + a box per prop cell at the CANONICAL contract camera (the SAME
// cell->world the combat renderer uses, so the painted props land on the combat cells), captures a
// lit greybox control image. generate_room.py then img2img's it into the painted room.
//   unity-mcp code execute --no-safety-checks -f build_room_greybox.cs
AssetDatabase.Refresh();
var sb=new System.Text.StringBuilder();
Camera cam=Camera.main; if(cam==null && Camera.allCameras.Length>0) cam=Camera.allCameras[0]; if(cam==null) return "no cam";

// --- read the authored geometry (the SINGLE source for both the painted room and the pathing) ---
string GEO="/home/unity/worldos-unity/room_geometry.json";
if(!System.IO.File.Exists(GEO)) return "no geometry json: "+GEO;
var geo=MiniJson.Parse(System.IO.File.ReadAllText(GEO)) as System.Collections.Generic.Dictionary<string,object>;
if(geo==null) return "geometry parse failed";
int cols=geo.ContainsKey("cols")?System.Convert.ToInt32(geo["cols"]):14;
int rows=geo.ContainsKey("rows")?System.Convert.ToInt32(geo["rows"]):11;
// MATERIAL axis: "stone" (masonry coursing, grey) or "wood" (horizontal plank coursing + warm grain, brown).
bool wood = geo.ContainsKey("material") && (geo["material"] as string)=="wood";
// CONTRACT cell->world (cx0=(cols-1)/2, cy0=(rows-1)/2, isotropic cell 2.0) — matches paint_combat_v1.cs.
float cx0=(cols-1)/2.0f, cy0=(rows-1)/2.0f;
System.Func<int,int,Vector3> cellToWorld=(c,r)=> new Vector3((c-cx0)*2.0f, 0f, (cy0-r)*2.0f);

// --- contract camera (dimetric, ortho 13, elevation 30 / yaw 45, pulled back 80) ---
cam.orthographic=true; cam.orthographicSize=13f; cam.nearClipPlane=0.3f; cam.farClipPlane=500f;
{ Quaternion _crot=Quaternion.Euler(30f,45f,0f); cam.transform.rotation=_crot; cam.transform.position=-(_crot*Vector3.forward)*80f; }
cam.clearFlags=CameraClearFlags.SolidColor; cam.backgroundColor=new Color(0.05f,0.05f,0.07f);

// sweep prior greybox parts AND all existing lights (leftover combat lights blow the greybox out to white).
{ var _k=new System.Collections.Generic.List<GameObject>(); foreach(var g in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None)){ if(g==null) continue; if(g.name.StartsWith("GB_") || g.GetComponent<Light>()!=null) _k.Add(g); } foreach(var g in _k){ if(g!=null) UnityEngine.Object.DestroyImmediate(g); } }
int hidden=0; foreach(var r in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None)){ if(r.enabled){r.enabled=false;hidden++;} }

// --- PROCEDURAL STONE TEXTURE (the textured-greybox lever, gfx M-A ≥8 push) ---
// A noise stone ALBEDO + NORMAL so the greybox base is ROUGH STONE, not flat grey. The root cause of the
// alignment↔quality tradeoff was a GRAY base: low-strength img2img kept props on-cell but under-painted
// (washout), high-strength repainted but DRIFTED. A textured base lets LOW strength preserve composition
// (alignment) AND gives the LoRA painterly stone grain/relief to enhance — so we get ≥8 AND on-cell.
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
      // WOOD PLANKS — long HORIZONTAL boards: only horizontal seams (no running-bond joints), fine grain
      // streaks running along the board, warm brown tone. Gives the img2img wood-plank structure to paint.
      float plankH=0.115f;
      float pf=v/plankH; float pFrac=pf-Mathf.Floor(pf);
      float seamW=0.045f/plankH;
      float seam=(pFrac<seamW||pFrac>1f-seamW)?0.32f:0f;     // dark gap between planks
      float grain=(Mathf.PerlinNoise(u*2.5f, v*26f)-0.5f)*0.18f;  // grain along the board
      hh[x,y]=n - seam*0.8f + grain*0.6f;
      float g=Mathf.Clamp01(0.46f + (n-0.5f)*0.34f - dark*0.6f - seam + grain);
      stoneAlb.SetPixel(x,y,new Color(g, g*0.72f, g*0.46f));  // warm brown timber
    } else {
      // MASONRY COURSING — darken at block joints (running-bond) so the img2img reads stone BLOCKS, not a
      // smooth surface. This is why the FLOOR (grout grid) painted into flagstones but the WALLS (smooth
      // texture) stayed greybox-flat at score 6.5 — the walls had no block structure for the LoRA to paint.
      float courseH=0.135f, brickW=0.17f, jointW=0.05f;
      float cf=v/courseH; float cFrac=cf-Mathf.Floor(cf); int courseI=(int)Mathf.Floor(cf);
      float uo=u + (courseI%2==0?0f:0.085f);               // running-bond half-offset per course
      float bf=uo/brickW; float bFrac=bf-Mathf.Floor(bf);
      float horiz=(cFrac<jointW/courseH||cFrac>1f-jointW/courseH)?1f:0f;
      float vert =(bFrac<jointW/brickW||bFrac>1f-jointW/brickW)?1f:0f;
      float seam=Mathf.Max(horiz,vert)*0.26f;              // recessed mortar joint = darker
      hh[x,y]=n - seam*0.7f;                               // joints also dent the normal (carved depth)
      float g=Mathf.Clamp01(0.5f + (n-0.5f)*0.55f - dark - seam);  // mid-grey + mottle + cracks + block joints
      stoneAlb.SetPixel(x,y,new Color(g,g*0.985f,g*0.96f));  // faint warm stone
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

System.Func<string,Vector3,Vector3,Color,GameObject> box=(nm,center,size,col)=>{
  var b=GameObject.CreatePrimitive(PrimitiveType.Cube); b.name="GB_"+nm; UnityEngine.Object.DestroyImmediate(b.GetComponent<Collider>());
  b.transform.position=center; b.transform.localScale=size;
  var m=new Material(Shader.Find("Standard")); m.color=col; m.SetFloat("_Glossiness",0.04f);
  m.mainTexture=stoneAlb;                                   // textured stone base, NOT flat grey
  m.SetTexture("_BumpMap", stoneNrm); m.EnableKeyword("_NORMALMAP"); m.SetFloat("_BumpScale", 1.0f);
  float tx=Mathf.Max(1.5f,(size.x+size.z)*0.35f), ty=Mathf.Max(1.5f,(size.x+size.y+size.z)*0.18f);
  m.mainTextureScale=new Vector2(tx,ty); m.SetTextureScale("_BumpMap", new Vector2(tx,ty));
  b.GetComponent<Renderer>().sharedMaterial=m; return b; };

// floor — mid-grey (NOT light: light + bright key blows out to white, ruining the img2img form).
{ var f=box("Floor", new Vector3(0f,-0.05f,0f), new Vector3(cols*2.0f, 0.1f, rows*2.0f), new Color(0.42f,0.41f,0.40f)); }
// CARVED flagstone grout — recessed grid lines per cell boundary give the LoRA a stone-floor grid to
// paint into mortar/flagstones (the ≥8 carved-geometry lever, NOT a prompt change). Thin dark inset
// strips just above the floor at each interior cell boundary; they read as grout shadow at the camera.
{ Color grout=new Color(0.20f,0.19f,0.18f); float gy=0.015f, gw=0.13f;
  for(int c=1;c<cols;c++){ float x=(c-cx0)*2.0f-1.0f; box("FloorGroutV"+c, new Vector3(x,gy,0f), new Vector3(gw,0.05f,rows*2.0f), grout); }
  for(int r=1;r<rows;r++){ float z=(cy0-r)*2.0f+1.0f; box("FloorGroutH"+r, new Vector3(0f,gy,z), new Vector3(cols*2.0f,0.05f,gw), grout); }
}
// ★ NO CEILING / NO ROOF GEOMETRY — EVER, for interior rooms (the universal iso-CRPG convention: PoE1/2,
// Infinity Engine, Disco Elysium, Diablo all omit ceilings so the top-down camera sees the floor + actors).
// Do NOT add a ceiling/roof box here to "enclose" a room — it would occlude the interior. (Guard tripwire.)
// enclosing walls — build only the FAR walls so the camera SEES IN (iso-CRPG cutaway). The camera sits at
// the -x,-z near corner looking toward +x,+z, so the FAR walls are +z (back) and +x (right); the NEAR walls
// are -x (left) and -z (front). The front (-z) was already open; now we also OMIT the near -x/LEFT wall so
// it doesn't occlude the interior floor + the actors + the pathing (the owner's #1 fix: "the wall comes
// down so you can fully see in"). A transparent-able near-wall LAYER is a later polish.
bool cutNear = true;   // INDOOR default: omit the camera-near (-x/left) wall for full interior visibility.
float backH=11f, sideH=9f;
box("WallBack",  new Vector3(0f,backH/2f,(cy0+0.5f)*2.0f), new Vector3(cols*2.0f,backH,0.5f), new Color(0.5f,0.49f,0.48f));
if(!cutNear) box("WallLeft",  new Vector3(-(cx0+0.5f)*2.0f,sideH/2f,0f), new Vector3(0.5f,sideH,rows*2.0f), new Color(0.46f,0.45f,0.44f));
box("WallRight", new Vector3((cx0+0.5f)*2.0f,sideH/2f,0f), new Vector3(0.5f,sideH,rows*2.0f), new Color(0.44f,0.43f,0.42f));
// CARVED wall relief — raised pilasters/buttresses every ~3 cells protruding INTO the room, plus a
// header course band near the top. Walls fill most of the dimetric frame, so this carved architecture
// is the biggest ≥8 lever: it gives the LoRA shadowed stone columns + a cornice to paint (NOT a prompt).
{ float pilW=0.7f, pilD=0.6f; Color pilC=new Color(0.55f,0.54f,0.52f); Color bandC=new Color(0.4f,0.39f,0.38f);
  // back-wall pilasters (face into the room: z just inside the back wall)
  for(int c=2;c<cols-1;c+=3){ float x=(c-cx0)*2.0f; box("PilBack"+c, new Vector3(x,backH*0.46f,(cy0+0.35f)*2.0f), new Vector3(pilW,backH*0.92f,pilD), pilC); }
  // side-wall pilasters
  for(int r=2;r<rows-1;r+=3){ float z=(cy0-r)*2.0f;
    if(!cutNear) box("PilLeft"+r,  new Vector3(-(cx0+0.35f)*2.0f,sideH*0.46f,z), new Vector3(pilD,sideH*0.92f,pilW), pilC);
    box("PilRight"+r, new Vector3((cx0+0.35f)*2.0f, sideH*0.46f,z), new Vector3(pilD,sideH*0.92f,pilW), pilC); }
  // header course band along the back wall top (a cornice line for the LoRA)
  box("BackCornice", new Vector3(0f,backH*0.84f,(cy0+0.32f)*2.0f), new Vector3(cols*2.0f,0.7f,0.5f), bandC);
}
// GEOMETRIC masonry coursing on the WALL FACES — the score plateaued at 6.5 because the FLOOR has
// geometric grout boxes (→ painted flagstones) but the walls had only TEXTURE (→ the LoRA smoothed them to
// a "value-pass, not carved stone"). Add proud block seams as GEOMETRY (like the floor grout) so the wall
// faces read as stone COURSES the LoRA paints into carved masonry, not a flat gradient.
{ Color seamC=new Color(0.34f,0.33f,0.31f); float sw=0.14f;
  float backZ=(cy0+0.5f)*2.0f-0.30f, lX=-(cx0+0.5f)*2.0f+0.30f, rX=(cx0+0.5f)*2.0f-0.30f;
  // horizontal courses on all three walls (the dominant masonry read)
  for(float yy=1.3f; yy<backH-0.7f; yy+=1.45f){ box("CrsBackH"+(int)(yy*10f), new Vector3(0f,yy,backZ), new Vector3(cols*2.0f,sw,0.14f), seamC); }
  for(float yy=1.3f; yy<sideH-0.7f; yy+=1.45f){
    if(!cutNear) box("CrsLeftH"+(int)(yy*10f),  new Vector3(lX,yy,0f), new Vector3(0.14f,sw,rows*2.0f), seamC);
    box("CrsRightH"+(int)(yy*10f), new Vector3(rX,yy,0f), new Vector3(0.14f,sw,rows*2.0f), seamC); }
  // sparse vertical joints (running bond) on the back wall so courses break into blocks, not stripes
  for(int c=1;c<cols;c+=2){ float x=(c-cx0)*2.0f-1.0f; box("CrsBackV"+c, new Vector3(x,backH*0.42f,backZ), new Vector3(0.10f,backH*0.78f,0.14f), seamC); }
}

// props at their AUTHORED cells — height by kind so the control reads as a furnished room.
var props=geo.ContainsKey("props")?geo["props"] as System.Collections.Generic.List<object>:null;
int np=0;
if(props!=null) foreach(var po in props){ var p=po as System.Collections.Generic.Dictionary<string,object>; if(p==null) continue; string kind=(p.ContainsKey("kind")?p["kind"] as string:"prop")??"prop"; var cells=p.ContainsKey("cells")?p["cells"] as System.Collections.Generic.List<object>:null; if(cells==null) continue;
  float ph=2.6f, pw=1.4f; Color pc=new Color(0.52f,0.5f,0.48f);
  if(kind.Contains("pillar")||kind.Contains("column")){ ph=7.5f; pw=1.6f; pc=new Color(0.56f,0.55f,0.53f); }
  else if(kind.Contains("sarcophagus")||kind.Contains("altar")||kind.Contains("bar")||kind.Contains("table")||kind.Contains("pew")){ ph=2.0f; pw=1.8f; pc=new Color(0.6f,0.58f,0.55f); }
  else if(kind.Contains("brazier")){ ph=2.2f; pw=0.8f; pc=new Color(0.38f,0.36f,0.34f); }
  else if(kind.Contains("rubble")||kind.Contains("barrel")||kind.Contains("crate")){ ph=1.4f; pw=1.5f; pc=new Color(0.45f,0.43f,0.4f); }
  foreach(var co in cells){ var cc=co as System.Collections.Generic.List<object>; if(cc==null||cc.Count<2) continue; int c=System.Convert.ToInt32(cc[0]); int r=System.Convert.ToInt32(cc[1]); var w=cellToWorld(c,r); box("Prop_"+np+"_"+kind, new Vector3(w.x,ph/2f,w.z), new Vector3(pw,ph,pw), pc); np++; }
}

// readable greybox lighting (warm key + STRONG cool fill so the carved+textured WALLS read, not crush to
// black). The score panel found the textured floor painterly (5.75 vs flat 4.75) but the walls crushed to
// near-black ("no secondary cool fill" → invisible wall carving) → lift the shadow-side walls with a
// brighter cool fill + a cool wall-wash + higher cool ambient, so the img2img sees blue-violet carved
// stone on the walls (NOT pure black) at low strength. Softer key shadow so cast shadows aren't pure black.
foreach(var ln in new[]{"GB_Key","GB_Fill","GB_WallWash"}){ var o=GameObject.Find(ln); if(o!=null) UnityEngine.Object.DestroyImmediate(o); }
{ var lg=new GameObject("GB_Key"); var L=lg.AddComponent<Light>(); L.type=LightType.Directional; L.color=new Color(1f,0.93f,0.82f); L.intensity=0.78f; L.shadows=LightShadows.Soft; L.shadowStrength=0.55f; lg.transform.rotation=Quaternion.Euler(50f,35f,0f); }
{ var fg=new GameObject("GB_Fill"); var F=fg.AddComponent<Light>(); F.type=LightType.Directional; F.color=new Color(0.52f,0.58f,0.74f); F.intensity=0.62f; fg.transform.rotation=Quaternion.Euler(40f,210f,0f); }
// a cool wall-wash from the camera side so the back + side walls (which face the camera) catch blue-violet
// light and their carved relief/pilasters read instead of crushing to black.
{ var wg=new GameObject("GB_WallWash"); var Wl=wg.AddComponent<Light>(); Wl.type=LightType.Directional; Wl.color=new Color(0.46f,0.52f,0.70f); Wl.intensity=0.42f; Wl.shadows=LightShadows.None; wg.transform.rotation=Quaternion.Euler(18f,225f,0f); }
RenderSettings.ambientMode=UnityEngine.Rendering.AmbientMode.Flat; RenderSettings.ambientLight=new Color(0.28f,0.30f,0.36f);

// capture the greybox control at the contract aspect (1344x768 like the plates).
int W=1344,Hh=768; var rt=new RenderTexture(W,Hh,24,RenderTextureFormat.ARGB32); rt.Create();
float pa=cam.aspect; var pt=cam.targetTexture; cam.targetTexture=rt; cam.aspect=(float)W/Hh; cam.Render();
var pAct=RenderTexture.active; RenderTexture.active=rt; var t2=new Texture2D(W,Hh,TextureFormat.RGB24,false); t2.ReadPixels(new Rect(0,0,W,Hh),0,0); t2.Apply(); RenderTexture.active=pAct; cam.targetTexture=pt; cam.aspect=pa;
System.IO.Directory.CreateDirectory("/home/unity/worldos-unity/Captures-Durable");
System.IO.File.WriteAllBytes("/home/unity/worldos-unity/Captures-Durable/room_greybox.png", t2.EncodeToPNG());
// ★ G-buffer passes (PoE2-faithful, Phase A): render the greybox's view-space NORMAL + linear DEPTH at the SAME
// contract camera. The greybox is a real 3D scene, so these are FREE (like PoE2's Maya depth/normal passes) — they
// let a FLAT-lit painterly diffuse be relit IN-ENGINE (deferred lighting) + give per-pixel occlusion. Additive:
// room_greybox.png (the img2img control) is unchanged; these are extra sidecar layers.
System.Action<string,string> _capPass=(shaderName,outName)=>{
  var sh=Shader.Find(shaderName); if(sh==null){ sb.AppendLine("MISSING shader "+shaderName); return; }
  var _prt=cam.targetTexture; var _pa=cam.aspect; cam.targetTexture=rt; cam.aspect=(float)W/Hh;
  cam.RenderWithShader(sh, "");
  var _pa2=RenderTexture.active; RenderTexture.active=rt; var _tp=new Texture2D(W,Hh,TextureFormat.RGB24,false); _tp.ReadPixels(new Rect(0,0,W,Hh),0,0); _tp.Apply(); RenderTexture.active=_pa2;
  cam.targetTexture=_prt; cam.aspect=_pa;
  System.IO.File.WriteAllBytes("/home/unity/worldos-unity/Captures-Durable/"+outName, _tp.EncodeToPNG());
  UnityEngine.Object.DestroyImmediate(_tp);
};
_capPass("WOS/ViewNormal","room_greybox_normal.png");
_capPass("WOS/LinDepth","room_greybox_depth.png");
UnityEngine.Object.DestroyImmediate(t2); rt.Release(); UnityEngine.Object.DestroyImmediate(rt);
// CLEANUP (capture is done; the greybox is transient + the scene is NOT saved): destroy this run's GB_*
// GameObjects + their per-box Materials + the shared procedural stone Textures, so repeated invocations
// don't leak native assets (CodeRabbit #1210). The shared stoneAlb/stoneNrm are deduped via the set + the
// explicit destroy below; the next run's start-sweep is then a no-op safety net.
{ var _gb=new System.Collections.Generic.List<GameObject>(); var _mat=new System.Collections.Generic.HashSet<Material>();
  foreach(var g in UnityEngine.Object.FindObjectsByType<GameObject>(FindObjectsSortMode.None)){ if(g==null) continue; if(g.name.StartsWith("GB_")){ _gb.Add(g); var _r=g.GetComponent<Renderer>(); if(_r!=null && _r.sharedMaterial!=null) _mat.Add(_r.sharedMaterial); } }
  foreach(var g in _gb){ if(g!=null) UnityEngine.Object.DestroyImmediate(g); }
  foreach(var m in _mat){ if(m!=null) UnityEngine.Object.DestroyImmediate(m); }
  if(stoneAlb!=null) UnityEngine.Object.DestroyImmediate(stoneAlb);
  if(stoneNrm!=null) UnityEngine.Object.DestroyImmediate(stoneNrm);
}
sb.AppendLine("greybox "+cols+"x"+rows+" props="+np+" -> room_greybox.png (hidden="+hidden+")");
return sb.ToString();
