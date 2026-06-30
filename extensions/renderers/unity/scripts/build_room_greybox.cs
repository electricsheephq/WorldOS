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

System.Func<string,Vector3,Vector3,Color,GameObject> box=(nm,center,size,col)=>{
  var b=GameObject.CreatePrimitive(PrimitiveType.Cube); b.name="GB_"+nm; UnityEngine.Object.DestroyImmediate(b.GetComponent<Collider>());
  b.transform.position=center; b.transform.localScale=size; var m=new Material(Shader.Find("Standard")); m.color=col; m.SetFloat("_Glossiness",0.05f); b.GetComponent<Renderer>().sharedMaterial=m; return b; };

// floor — mid-grey (NOT light: light + bright key blows out to white, ruining the img2img form).
{ var f=box("Floor", new Vector3(0f,-0.05f,0f), new Vector3(cols*2.0f, 0.1f, rows*2.0f), new Color(0.42f,0.41f,0.40f)); }
// CARVED flagstone grout — recessed grid lines per cell boundary give the LoRA a stone-floor grid to
// paint into mortar/flagstones (the ≥8 carved-geometry lever, NOT a prompt change). Thin dark inset
// strips just above the floor at each interior cell boundary; they read as grout shadow at the camera.
{ Color grout=new Color(0.20f,0.19f,0.18f); float gy=0.015f, gw=0.13f;
  for(int c=1;c<cols;c++){ float x=(c-cx0)*2.0f-1.0f; box("FloorGroutV"+c, new Vector3(x,gy,0f), new Vector3(gw,0.05f,rows*2.0f), grout); }
  for(int r=1;r<rows;r++){ float z=(cy0-r)*2.0f+1.0f; box("FloorGroutH"+r, new Vector3(0f,gy,z), new Vector3(cols*2.0f,0.05f,gw), grout); }
}
// enclosing walls — TALL back + sides so the room fills the upper frame (no black sky); NO front wall
// (the corner-iso camera looks in over the open front edge). Heights tuned for the dimetric down-look.
float backH=11f, sideH=9f;
box("WallBack",  new Vector3(0f,backH/2f,(cy0+0.5f)*2.0f), new Vector3(cols*2.0f,backH,0.5f), new Color(0.5f,0.49f,0.48f));
box("WallLeft",  new Vector3(-(cx0+0.5f)*2.0f,sideH/2f,0f), new Vector3(0.5f,sideH,rows*2.0f), new Color(0.46f,0.45f,0.44f));
box("WallRight", new Vector3((cx0+0.5f)*2.0f,sideH/2f,0f), new Vector3(0.5f,sideH,rows*2.0f), new Color(0.44f,0.43f,0.42f));
// CARVED wall relief — raised pilasters/buttresses every ~3 cells protruding INTO the room, plus a
// header course band near the top. Walls fill most of the dimetric frame, so this carved architecture
// is the biggest ≥8 lever: it gives the LoRA shadowed stone columns + a cornice to paint (NOT a prompt).
{ float pilW=0.7f, pilD=0.6f; Color pilC=new Color(0.55f,0.54f,0.52f); Color bandC=new Color(0.4f,0.39f,0.38f);
  // back-wall pilasters (face into the room: z just inside the back wall)
  for(int c=2;c<cols-1;c+=3){ float x=(c-cx0)*2.0f; box("PilBack"+c, new Vector3(x,backH*0.46f,(cy0+0.35f)*2.0f), new Vector3(pilW,backH*0.92f,pilD), pilC); }
  // side-wall pilasters
  for(int r=2;r<rows-1;r+=3){ float z=(cy0-r)*2.0f;
    box("PilLeft"+r,  new Vector3(-(cx0+0.35f)*2.0f,sideH*0.46f,z), new Vector3(pilD,sideH*0.92f,pilW), pilC);
    box("PilRight"+r, new Vector3((cx0+0.35f)*2.0f, sideH*0.46f,z), new Vector3(pilD,sideH*0.92f,pilW), pilC); }
  // header course band along the back wall top (a cornice line for the LoRA)
  box("BackCornice", new Vector3(0f,backH*0.84f,(cy0+0.32f)*2.0f), new Vector3(cols*2.0f,0.7f,0.5f), bandC);
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

// readable greybox lighting (warm key + cool fill so the img2img has form to repaint).
foreach(var ln in new[]{"GB_Key","GB_Fill"}){ var o=GameObject.Find(ln); if(o!=null) UnityEngine.Object.DestroyImmediate(o); }
{ var lg=new GameObject("GB_Key"); var L=lg.AddComponent<Light>(); L.type=LightType.Directional; L.color=new Color(1f,0.93f,0.82f); L.intensity=0.72f; L.shadows=LightShadows.Soft; L.shadowStrength=0.8f; lg.transform.rotation=Quaternion.Euler(50f,35f,0f); }
{ var fg=new GameObject("GB_Fill"); var F=fg.AddComponent<Light>(); F.type=LightType.Directional; F.color=new Color(0.5f,0.55f,0.68f); F.intensity=0.3f; fg.transform.rotation=Quaternion.Euler(40f,210f,0f); }
RenderSettings.ambientMode=UnityEngine.Rendering.AmbientMode.Flat; RenderSettings.ambientLight=new Color(0.2f,0.21f,0.24f);

// capture the greybox control at the contract aspect (1344x768 like the plates).
int W=1344,Hh=768; var rt=new RenderTexture(W,Hh,24,RenderTextureFormat.ARGB32); rt.Create();
float pa=cam.aspect; var pt=cam.targetTexture; cam.targetTexture=rt; cam.aspect=(float)W/Hh; cam.Render();
var pAct=RenderTexture.active; RenderTexture.active=rt; var t2=new Texture2D(W,Hh,TextureFormat.RGB24,false); t2.ReadPixels(new Rect(0,0,W,Hh),0,0); t2.Apply(); RenderTexture.active=pAct; cam.targetTexture=pt; cam.aspect=pa;
System.IO.Directory.CreateDirectory("/home/unity/worldos-unity/Captures-Durable");
System.IO.File.WriteAllBytes("/home/unity/worldos-unity/Captures-Durable/room_greybox.png", t2.EncodeToPNG());
UnityEngine.Object.DestroyImmediate(t2); rt.Release(); UnityEngine.Object.DestroyImmediate(rt);
sb.AppendLine("greybox "+cols+"x"+rows+" props="+np+" -> room_greybox.png (hidden="+hidden+")");
return sb.ToString();
