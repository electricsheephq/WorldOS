// paint_backdrop_render.cs — Unity painted-backdrop renderer (r3: scene-relight).
// Built-in pipeline. Backdrop quad + dimetric billboards relit by WorldOS/PaintedSpriteScene
// (UV-based scene key/fill/rim/edge-dissolve/atm) + perspective ground rings + darker contact shadows.
AssetDatabase.Refresh();   // M2/M3: import freshly-deployed goblin/ally facings + vfx before LoadAssetAtPath
float KEYFROMLEFT = 1f;   // billboard is now UN-flipped (identity rot) so texture-left = screen-left = hearth side
var sb = new System.Text.StringBuilder();
Camera cam = Camera.main; if (cam==null && Camera.allCameras.Length>0) cam=Camera.allCameras[0];
if (cam==null) return "no camera";
// P0 contract camera: orthographic, elevation 30deg (asin .5 -> true 2:1), yaw 45 corner-iso. MUST match the greybox that conditioned the plate.
cam.orthographic=true; cam.orthographicSize=13f; cam.nearClipPlane=0.3f; cam.farClipPlane=500f;
{ Quaternion _crot=Quaternion.Euler(30f,45f,0f); cam.transform.rotation=_crot; cam.transform.position=-(_crot*Vector3.forward)*80f; }
var bdTex   = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/backdrops/crypt_pinned_v1.png");
var heroTex = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/sprites/hero_idle.png");
var gobTex  = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/sprites/goblin_idle.png");
var allyTex = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/sprites/ally_idle.png");
// M2: 8-facing painterly hero turnaround (sliced) — the renderer picks the facing by movement direction
var heroFacings=new System.Collections.Generic.Dictionary<string,Texture2D>();
foreach(var fc in new[]{"S","SE","E","NE","N","NW","W","SW"}){ var ft=AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/sprites/hero/hero_"+fc+".png"); if(ft!=null) heroFacings[fc]=ft; }
// M3: combat VFX (on-black -> additive). slash/impact = melee, bolt = magic, blood = death.
var vfxImpact=AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/vfx/vfx_impact.png");
var vfxSlash =AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/vfx/vfx_slash.png");
var vfxBolt  =AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/vfx/vfx_bolt.png");
var vfxBlood =AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/painterly/vfx/vfx_blood.png");
if (bdTex==null||heroTex==null||gobTex==null) return "TEX MISSING";

int hidden=0;
foreach (var r in UnityEngine.Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None)) { if (r.enabled){ r.enabled=false; hidden++; } }
var hf=GameObject.Find("HeroFighter"); var mg=GameObject.Find("MonsterGoblin");
// LIVE engine combat-surface (engine = SOLE WRITER; this renderer is READ-ONLY — positions come from the engine cells)
string CID="camp_gfxdemo01"; string surfJson="";
try { surfJson=new System.Net.WebClient().DownloadString("http://127.0.0.1:8765/combat-surface?campaign="+CID); } catch (System.Exception e) { return "surface GET failed: "+e.Message; }
var root=MiniJson.Parse(surfJson) as System.Collections.Generic.Dictionary<string,object>;
if (root==null) return "surface parse failed";
var toks=root["tokens"] as System.Collections.Generic.List<object>;
System.Func<int,int,Vector3> cellToWorld=(cx,cy)=> new Vector3((cx-6.5f)*2.0f, 0f, (5.0f-cy)*2.0f);  // P0 contract: isotropic cell 2.0; cols14 rows11 -> cx0=6.5 cy0=5
System.Func<string,float> labelFrac=(h)=>{ if(string.IsNullOrEmpty(h)) return 1f; h=h.ToLower();
  if(h.Contains("dead")||h.Contains("dying")||h.Contains("critical")) return 0.10f;
  if(h.Contains("badly")||h.Contains("heavily")) return 0.25f;
  if(h.Contains("bloodied")) return 0.40f;
  if(h.Contains("wounded")||h.Contains("hurt")) return 0.62f;
  if(h.Contains("scratched")||h.Contains("grazed")) return 0.82f;
  return 0.95f; };  // healthy/steady/unhurt
var actors=new System.Collections.Generic.List<object[]>(); var actorPos=new System.Collections.Generic.Dictionary<string,Vector3>(); var actorTop=new System.Collections.Generic.Dictionary<string,float>(); string celldbg="";
foreach (var o in toks){ var t=o as System.Collections.Generic.Dictionary<string,object>; if(t==null||!t.ContainsKey("x")||t["x"]==null) continue;
  int cx=System.Convert.ToInt32(t["x"]); int cy=System.Convert.ToInt32(t["y"]); string team=t["team"] as string; string nm=t["name"] as string;
  float frac; if(t.ContainsKey("hp")&&t["hp"]!=null&&t.ContainsKey("hpMax")&&t["hpMax"]!=null){ double hp=System.Convert.ToDouble(t["hp"]); double hpm=System.Convert.ToDouble(t["hpMax"]); frac=(hpm>0)?(float)(hp/hpm):1f; } else { frac=labelFrac(t.ContainsKey("health")?t["health"] as string:null); }
  actors.Add(new object[]{nm, team, cellToWorld(cx,cy), frac}); celldbg+=" "+nm+"("+team+")@"+cx+","+cy+" hp"+frac.ToString("F2"); }
// latest damage from the battleLog -> floating "-N" above the target
string dmgTarget=""; int dmgN=0; var blog=root.ContainsKey("battleLog")?root["battleLog"] as System.Collections.Generic.List<object>:null;
if(blog!=null){ foreach(var e in blog){ string tx=null; var ed=e as System.Collections.Generic.Dictionary<string,object>; if(ed!=null&&ed.ContainsKey("text")) tx=ed["text"] as string; else tx=e as string;
  if(tx!=null&&tx.Contains(" hits ")&&tx.Contains(" for ")&&tx.Contains("damage")){ int hi=tx.IndexOf(" hits "); int fi=tx.IndexOf(" for ",hi); if(hi>=0&&fi>hi){ dmgTarget=tx.Substring(hi+6,fi-(hi+6)).Trim(); var aft=tx.Substring(fi+5).TrimStart().Split(' '); if(aft.Length>0) int.TryParse(aft[0],out dmgN); } } } }
sb.AppendLine("LIVE surface cells:"+celldbg);

// backdrop quad (native plate aspect)
var oldBd=GameObject.Find("PaintedBackdrop"); if (oldBd!=null) UnityEngine.Object.DestroyImmediate(oldBd);
var bd=GameObject.CreatePrimitive(PrimitiveType.Quad); bd.name="PaintedBackdrop"; UnityEngine.Object.DestroyImmediate(bd.GetComponent<Collider>());
bd.transform.SetParent(cam.transform,false);
float texAsp=(float)bdTex.width/bdTex.height; float oh=cam.orthographicSize*2f; float ow=oh*texAsp;
bd.transform.localPosition=new Vector3(0,0,160f); bd.transform.localRotation=Quaternion.identity; bd.transform.localScale=new Vector3(ow,oh,1f);
var bm=new Material(Shader.Find("Unlit/Texture")); bm.mainTexture=bdTex; bm.renderQueue=1900;
var bdr=bd.GetComponent<Renderer>(); bdr.sharedMaterial=bm; bdr.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; bdr.receiveShadows=false;
// P2 OCCLUSION: invisible depth-only boxes at the painted props (queue 1950: after the backdrop 1900, before
// the rings/billboards) -> characters behind a pillar/sarcophagus are occluded by the (painted) prop. Dims match
// the greybox that conditioned the plate, so the depth box aligns with the painted prop.
var occMat=new Material(Shader.Find("WorldOS/DepthMask")); occMat.renderQueue=1950;
System.Action<string,Vector3,Vector3> occ=(nm,center,size)=>{ var o=GameObject.Find(nm); if(o!=null) UnityEngine.Object.DestroyImmediate(o); var g=GameObject.CreatePrimitive(PrimitiveType.Cube); g.name=nm; UnityEngine.Object.DestroyImmediate(g.GetComponent<Collider>()); g.transform.position=center; g.transform.localScale=size; var rr=g.GetComponent<Renderer>(); rr.sharedMaterial=occMat; rr.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; rr.receiveShadows=false; };
occ("OCC_pillarL", cellToWorld(2,3)+new Vector3(0,4f,0),    new Vector3(1.4f,8f,1.4f));
occ("OCC_pillarR", cellToWorld(11,3)+new Vector3(0,4f,0),   new Vector3(1.4f,8f,1.4f));
occ("OCC_sarc",    cellToWorld(7,1)+new Vector3(-1f,1.5f,0),new Vector3(4f,3f,2f));
occ("OCC_brazL",   cellToWorld(4,1)+new Vector3(0,1.2f,0),  new Vector3(0.9f,2.4f,0.9f));
occ("OCC_brazR",   cellToWorld(9,1)+new Vector3(0,1.2f,0),  new Vector3(0.9f,2.4f,0.9f));

// procedural ground textures
System.Func<int,Color,Texture2D> mkRing=(size,col)=>{
  var t=new Texture2D(size,size,TextureFormat.RGBA32,false); t.wrapMode=TextureWrapMode.Clamp; var px=new Color[size*size]; float c=(size-1)/2f, ro=c*0.96f, ri=c*0.80f;
  for(int y=0;y<size;y++)for(int x=0;x<size;x++){ float d=Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c)); float a=0f; if(d<=ro&&d>=ri){ float e=Mathf.Min(ro-d,d-ri); a=Mathf.Clamp01(e/4f);} px[y*size+x]=new Color(col.r,col.g,col.b,a*col.a);} t.SetPixels(px); t.Apply(); return t; };
System.Func<Texture2D> mkBlob=()=>{ int size=256; var t=new Texture2D(size,size,TextureFormat.RGBA32,false); t.wrapMode=TextureWrapMode.Clamp; var px=new Color[size*size]; float c=(size-1)/2f;
  for(int y=0;y<size;y++)for(int x=0;x<size;x++){ float d=Mathf.Clamp01(Mathf.Sqrt((x-c)*(x-c)+(y-c)*(y-c))/c); float a=Mathf.Pow(1f-d,0.85f)*1.0f; px[y*size+x]=new Color(0.02f,0.015f,0.01f,a);} t.SetPixels(px); t.Apply(); return t; };  // L2: slower falloff + full alpha = a darker, wider contact pool that reads beyond the figure
var ringHero=mkRing(256,new Color(1f,0.80f,0.43f,0.72f)); var ringGob=mkRing(256,new Color(0.90f,0.27f,0.29f,0.72f)); var blob=mkBlob();  // L2: dimmer rings so the contact shadow reads as the ground anchor (ring = highlight on top)
// P1: a soft gold DOT for the routed-path breadcrumbs (the engine's detour around props)
System.Func<Color,Texture2D> mkDot=(col)=>{ int s=96; var t=new Texture2D(s,s,TextureFormat.RGBA32,false); t.wrapMode=TextureWrapMode.Clamp; var px=new Color[s*s]; float cc=(s-1)/2f; for(int yy=0;yy<s;yy++)for(int xx=0;xx<s;xx++){ float d=Mathf.Sqrt((xx-cc)*(xx-cc)+(yy-cc)*(yy-cc))/cc; float a=d<0.62f?col.a:(d<1f?col.a*(1f-(d-0.62f)/0.38f):0f); px[yy*s+xx]=new Color(col.r,col.g,col.b,a);} t.SetPixels(px); t.Apply(); return t; };
var dotTex=mkDot(new Color(1f,0.82f,0.35f,0.9f));
var lpath = root.ContainsKey("lastPath") ? root["lastPath"] as System.Collections.Generic.List<object> : null;
// M2: pick the hero's facing from its last move direction (world heading -> 8-way bake facing). PHI0/OOFF = calibration.
string[] FORDER={"S","SE","E","NE","N","NW","W","SW"}; float PHI0=0f; int OOFF=0; string heroFace="S";
if(lpath!=null && lpath.Count>=2){
  var pcA=lpath[lpath.Count-2] as System.Collections.Generic.List<object>; var pcB=lpath[lpath.Count-1] as System.Collections.Generic.List<object>;
  if(pcA!=null && pcB!=null){ var wa=cellToWorld(System.Convert.ToInt32(pcA[0]),System.Convert.ToInt32(pcA[1])); var wb=cellToWorld(System.Convert.ToInt32(pcB[0]),System.Convert.ToInt32(pcB[1]));
    float hdx=wb.x-wa.x, hdz=wb.z-wa.z; float hd=Mathf.Atan2(hdx,hdz)*Mathf.Rad2Deg; int hk=Mathf.RoundToInt((hd-PHI0)/45f); hk=((hk%8)+8)%8; heroFace=FORDER[(hk+OOFF)%8]; }
}
Texture2D heroFacingTex = heroFacings.ContainsKey(heroFace)?heroFacings[heroFace]:heroTex;
sb.AppendLine("HERO facing="+heroFace+" (facings loaded="+heroFacings.Count+")");

System.Func<string,float,float,Texture2D,float,float,int,GameObject> ground=(nm,wx,wz,tex,dx,dz,q)=>{
  var old=GameObject.Find(nm); if(old!=null) UnityEngine.Object.DestroyImmediate(old);
  var g=GameObject.CreatePrimitive(PrimitiveType.Quad); g.name=nm; UnityEngine.Object.DestroyImmediate(g.GetComponent<Collider>());
  g.transform.position=new Vector3(wx,0.05f,wz); g.transform.localEulerAngles=new Vector3(90f,0f,0f); g.transform.localScale=new Vector3(dx,dz,1f);
  var m=new Material(Shader.Find("Unlit/Transparent")); m.mainTexture=tex; m.renderQueue=q;
  var r=g.GetComponent<Renderer>(); r.sharedMaterial=m; r.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; r.receiveShadows=false; return g; };

// ground-projected directional CAST shadow: a dark elongated ellipse raking DOWN-RIGHT (away from the upper-left hearth)
System.Action<string,float,float,float,int> projShadow=(nm,wx,wz,sz,q)=>{
  var old=GameObject.Find(nm); if(old!=null) UnityEngine.Object.DestroyImmediate(old);
  var g=GameObject.CreatePrimitive(PrimitiveType.Quad); g.name=nm; UnityEngine.Object.DestroyImmediate(g.GetComponent<Collider>());
  Vector3 away=new Vector3(0.7f,0f,-0.7f);                                                       // hearth upper-left -> shadow rakes down-right
  g.transform.rotation=Quaternion.AngleAxis(135f,Vector3.up)*Quaternion.AngleAxis(90f,Vector3.right);  // flat on ground, long axis -> away
  g.transform.localScale=new Vector3(sz*0.85f, sz*2.1f, 1f);                                     // narrow + long elongated rake
  g.transform.position=new Vector3(wx,0.025f,wz)+away*(sz*0.75f);
  var m=new Material(Shader.Find("Unlit/Transparent")); m.mainTexture=blob; m.renderQueue=q;
  var r=g.GetComponent<Renderer>(); r.sharedMaterial=m; r.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; r.receiveShadows=false; };

// swap actors -> scene-relit billboards (PaintedSpriteScene) + per-depth scale + ring + darker shadow
// world-space HP BAR above an actor (engine surface hp; faces cam, draws over everything)
System.Action<string,float,float,float,float,Color> hpbar=(pfx,wx,wz,topY,frac,col)=>{
  foreach (var n2 in new[]{pfx+"_hudbg",pfx+"_hudfill"}){ var ex=GameObject.Find(n2); if(ex!=null) UnityEngine.Object.DestroyImmediate(ex); }
  float bw=3.4f, bh=0.5f;
  var b2=GameObject.CreatePrimitive(PrimitiveType.Quad); b2.name=pfx+"_hudbg"; UnityEngine.Object.DestroyImmediate(b2.GetComponent<Collider>());
  b2.transform.position=new Vector3(wx,topY,wz); b2.transform.localEulerAngles=Vector3.zero; b2.transform.localScale=new Vector3(bw,bh,1f);
  var mb=new Material(Shader.Find("Unlit/Color")); mb.color=new Color(0.05f,0.05f,0.06f,1f); mb.renderQueue=4000; b2.GetComponent<Renderer>().sharedMaterial=mb;
  float fw=bw*Mathf.Clamp01(frac);
  var fl=GameObject.CreatePrimitive(PrimitiveType.Quad); fl.name=pfx+"_hudfill"; UnityEngine.Object.DestroyImmediate(fl.GetComponent<Collider>());
  fl.transform.position=new Vector3(wx-bw/2f+fw/2f,topY,wz-0.02f); fl.transform.localEulerAngles=Vector3.zero; fl.transform.localScale=new Vector3(Mathf.Max(0.001f,fw),bh*0.64f,1f);
  var mf=new Material(Shader.Find("Unlit/Color")); mf.color=col; mf.renderQueue=4001; fl.GetComponent<Renderer>().sharedMaterial=mf;
};
// world-space TEXT label (nameplate / damage number) — TextMesh, faces cam, over everything
Font fnt=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
System.Action<string,string,float,float,float,Color,float> textLabel=(objn,txt,wx,wy,wz,col,sz)=>{
  var ex=GameObject.Find(objn); if(ex!=null) UnityEngine.Object.DestroyImmediate(ex);
  var g=new GameObject(objn); g.transform.position=new Vector3(wx,wy,wz); g.transform.localEulerAngles=Vector3.zero;
  var tm=g.AddComponent<TextMesh>(); tm.text=txt; tm.font=fnt; tm.fontSize=72; tm.characterSize=sz; tm.anchor=TextAnchor.MiddleCenter; tm.alignment=TextAlignment.Center; tm.color=col;
  var mr=g.GetComponent<MeshRenderer>(); var tmat=new Material(fnt.material); tmat.renderQueue=4003; mr.sharedMaterial=tmat;
};
var shd=Shader.Find("WorldOS/PaintedSpriteScene"); if (shd==null) return "PaintedSpriteScene shader missing";
float ATH=6.0f;   // L4: bigger actors (was 4.7) so the painterly figure reads as a character (silhouette/weapon) at combat zoom, per critic "roughly double scale"
// spawn ONE actor -> scene-relit billboard at its engine cell + ring(team) + cast shadow + AO + HP bar + nameplate.
// N-TOKEN + data-driven (no scene GameObject): sprite by name (Hero/Goblin/else->ally), ring/bar color by team.
System.Action<string,string,Vector3,float> spawnActor=(nm,team,p,frac)=>{
  bool foe=(team=="foe"||team=="enemy"||team=="monster");
  Texture2D tex=(nm=="Goblin"||foe)?gobTex:(nm=="Hero"?heroFacingTex:(allyTex!=null?allyTex:heroTex));
  Texture2D ring=foe?ringGob:ringHero;
  float keyStr=foe?0.72f:0.68f; float atm=foe?0.08f:0.06f; float exp=foe?1.62f:1.45f; if(frac<=0.06f) exp*=0.45f;  // L3/L4: strong warm torch key + brighter exposure so figures read as the brightest warm elements over the brazier (not dim cutouts)
  string pfx="AC_"+nm;
  var old=GameObject.Find(pfx+"_S"); if (old!=null) UnityEngine.Object.DestroyImmediate(old);
  var go=GameObject.CreatePrimitive(PrimitiveType.Quad); go.name=pfx+"_S"; UnityEngine.Object.DestroyImmediate(go.GetComponent<Collider>());
  float depth01=Mathf.Clamp01((p.z-14f)/10f);
  float sH=ATH*(foe?0.88f:1.0f)*(1f-0.15f*depth01); float asp=tex.height>0?(float)tex.width/tex.height:0.667f;
  go.transform.rotation=Quaternion.Euler(0f, cam.transform.eulerAngles.y, 0f); go.transform.localScale=new Vector3(sH*asp,sH,1f);   // face the yaw-45 contract camera
  go.transform.position=new Vector3(p.x,sH*0.5f,p.z);
  var bb=go.GetComponent<Renderer>().bounds; if (Mathf.Abs(bb.min.y)>0.02f) go.transform.position+=new Vector3(0f,-bb.min.y,0f);
  go.transform.position+=new Vector3(0f,-0.18f,0f);  // L2: seat feet slightly INTO the floor so they read as planted (kills the perceived air-gap)
  if(frac<=0.06f){ go.transform.rotation=Quaternion.Euler(80f, cam.transform.eulerAngles.y, 0f); go.transform.position=new Vector3(p.x,0.25f,p.z); }  // M3: death = toppled to the ground
  var m=new Material(shd); m.SetTexture("_MainTex",tex);
  m.SetColor("_KeyColor",new Color(1f,0.62f,0.30f)); m.SetColor("_CoolColor",new Color(0.30f,0.36f,0.50f)); m.SetColor("_AtmColor",new Color(0.13f,0.13f,0.16f));
  m.SetFloat("_KeyStrength",keyStr); m.SetFloat("_CoolStrength",0.22f); m.SetFloat("_KeyFromLeft",KEYFROMLEFT);
  m.SetFloat("_RimStrength",1.35f); m.SetFloat("_EdgeW",0.24f); m.SetFloat("_GrainAmt",0.32f); m.SetFloat("_GrainScale",30f);  // L4: warm rim so figures pop off the floor
  m.SetFloat("_AtmDepth",atm); m.SetFloat("_MaxLuma",0.80f); m.SetFloat("_Exposure",exp); m.SetFloat("_Cutoff",0.30f);
  m.renderQueue=2100; var r=go.GetComponent<Renderer>(); r.sharedMaterial=m; r.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; r.receiveShadows=false;
  float fr=(foe?2.8f:3.2f)*(1f-0.12f*depth01);
  projShadow(pfx+"_Cast", p.x, p.z, fr, 2008);                                   // directional cast shadow raking away from the hearth
  ground(pfx+"_Shadow", p.x, p.z, blob, fr*1.7f, fr*1.05f, 2010);                // L2 CRITICAL: large dark AO contact pool centered under the feet — the #1 grounding cue
  ground(pfx+"_Ring",   p.x, p.z, ring, fr*1.5f, fr*1.5f, 2030);                 // team ground ring (ally gold, foe red)
  float top=go.GetComponent<Renderer>().bounds.max.y+0.7f;
  actorPos[nm]=new Vector3(p.x,0f,p.z); actorTop[nm]=top;
  hpbar(pfx, p.x, p.z, top, frac, foe?new Color(0.90f,0.27f,0.27f,1f):new Color(0.30f,0.85f,0.32f,1f));
  float npY=top+0.5f+(Mathf.Abs(Mathf.RoundToInt(p.x/2f+6.5f))%2)*0.9f;
  { var teth=GameObject.Find(pfx+"_teth"); if(teth!=null) UnityEngine.Object.DestroyImmediate(teth); var tq=GameObject.CreatePrimitive(PrimitiveType.Quad); tq.name=pfx+"_teth"; UnityEngine.Object.DestroyImmediate(tq.GetComponent<Collider>()); tq.transform.rotation=Quaternion.Euler(0f,cam.transform.eulerAngles.y,0f); tq.transform.position=new Vector3(p.x,(top+npY)/2f,p.z); tq.transform.localScale=new Vector3(0.05f,Mathf.Max(0.01f,npY-top),1f); var tmth=new Material(Shader.Find("Unlit/Color")); tmth.color=foe?new Color(1f,0.42f,0.38f,0.5f):new Color(1f,0.85f,0.42f,0.5f); tmth.renderQueue=3999; var tr=tq.GetComponent<Renderer>(); tr.sharedMaterial=tmth; tr.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; }  // L5: team-colored tether line so each nameplate unambiguously belongs to its actor
  textLabel(pfx+"_name", nm, p.x, npY, p.z, foe?new Color(1f,0.42f,0.38f,1f):new Color(1f,0.85f,0.42f,1f), 0.30f);  // L5: team-color (foe RED / ally GOLD) + cell-parity stagger
  sb.AppendLine(pfx+" @"+go.transform.position.ToString("F1")+" h="+sH.ToString("F2")+" tex="+(tex==heroTex?"hero":tex==gobTex?"gob":"ally"));
};
// N-TOKEN render loop: one fully-decorated billboard per engine token (party scales to any count)
foreach (var a in actors){ spawnActor((string)a[0],(string)a[1],(Vector3)a[2],(float)a[3]); }
// P1: draw the routed path as gold breadcrumb dots on the floor (the detour AROUND the pillar)
if(lpath!=null){ for(int i=0;i<lpath.Count;i++){ var pc=lpath[i] as System.Collections.Generic.List<object>; if(pc==null||pc.Count<2) continue; int pcx=System.Convert.ToInt32(pc[0]); int pcy=System.Convert.ToInt32(pc[1]); var wp=cellToWorld(pcx,pcy); ground("PathDot"+i, wp.x, wp.z, dotTex, 0.85f, 0.85f, 2034); } sb.AppendLine("PATH dots="+lpath.Count); }
// floating damage number above the hit target (from the battleLog)
var dnum=GameObject.Find("DmgNum"); if(dnum!=null) UnityEngine.Object.DestroyImmediate(dnum);
if(dmgN>0 && actorPos.ContainsKey(dmgTarget)){ var tp=actorPos[dmgTarget]; float tt=actorTop.ContainsKey(dmgTarget)?actorTop[dmgTarget]:7f; float dny=ATH*0.6f+1.9f; textLabel("DmgNumBg","-"+dmgN,tp.x+0.08f,dny-0.08f,tp.z,new Color(0.04f,0f,0f,1f),0.5f); textLabel("DmgNum","-"+dmgN,tp.x,dny,tp.z,new Color(1f,0.96f,0.9f,1f),0.5f); }  // L5: damage number rises from the target's UPPER BODY (~burst height), not the tall billboard top / frame edge
sb.AppendLine("HUD actors="+actors.Count+" dmg "+dmgTarget+" -"+dmgN);
// DM NARRATION bar (bottom, cam-anchored) — the latest combat-action line from the engine battleLog
string narrLine="";
if(blog!=null){ foreach(var e in blog){ var ed=e as System.Collections.Generic.Dictionary<string,object>; string tx=(ed!=null&&ed.ContainsKey("text"))?ed["text"] as string:e as string; if(!string.IsNullOrEmpty(tx)&&(tx.Contains("hits")||tx.Contains("damage")||tx.Contains("strikes")||tx.Contains("attacks")||tx.Contains("misses"))) narrLine=tx; }
  if(narrLine==""&&blog.Count>0){ var ed=blog[blog.Count-1] as System.Collections.Generic.Dictionary<string,object>; narrLine=(ed!=null&&ed.ContainsKey("text"))?ed["text"] as string:blog[blog.Count-1] as string; } }
if(string.IsNullOrEmpty(narrLine)) narrLine="The battle is joined.";
{ var ob=GameObject.Find("NarrBar"); if(ob!=null) UnityEngine.Object.DestroyImmediate(ob); var ot=GameObject.Find("NarrText"); if(ot!=null) UnityEngine.Object.DestroyImmediate(ot);
  var bar=GameObject.CreatePrimitive(PrimitiveType.Quad); bar.name="NarrBar"; UnityEngine.Object.DestroyImmediate(bar.GetComponent<Collider>());
  bar.transform.SetParent(cam.transform,false); bar.transform.localPosition=new Vector3(0,-15.6f,40f); bar.transform.localRotation=Quaternion.identity; bar.transform.localScale=new Vector3(cam.orthographicSize*2f*texAsp,4.2f,1f);
  var bm2=new Material(Shader.Find("Unlit/Color")); bm2.color=new Color(0.03f,0.03f,0.05f,1f); bm2.renderQueue=4005; bar.GetComponent<Renderer>().sharedMaterial=bm2;
  var tg=new GameObject("NarrText"); tg.transform.SetParent(cam.transform,false); tg.transform.localPosition=new Vector3(0,-15.6f,39.6f); tg.transform.localRotation=Quaternion.identity;
  var tm2=tg.AddComponent<TextMesh>(); tm2.text=narrLine; tm2.font=fnt; tm2.fontSize=72; tm2.characterSize=0.34f; tm2.anchor=TextAnchor.MiddleCenter; tm2.alignment=TextAlignment.Center; tm2.color=new Color(0.93f,0.91f,0.83f,1f);
  var tmat2=new Material(fnt.material); tmat2.renderQueue=4006; tg.GetComponent<MeshRenderer>().sharedMaterial=tmat2; }
sb.AppendLine("NARR: "+narrLine);

// M3: combat VFX flash at the hit target (additive painterly overlay; on-black sprites). melee=slash+impact, magic=bolt, death=blood.
foreach(var n3 in new[]{"VFX_impact","VFX_slash","VFX_bolt","VFX_blood"}){ var ex=GameObject.Find(n3); if(ex!=null) UnityEngine.Object.DestroyImmediate(ex); }
Shader vfxSh=Shader.Find("Legacy Shaders/Particles/Additive"); if(vfxSh==null) vfxSh=Shader.Find("Particles/Additive"); if(vfxSh==null) vfxSh=Shader.Find("Mobile/Particles/Additive"); if(vfxSh==null) vfxSh=Shader.Find("Unlit/Transparent");
System.Action<string,Texture2D,float,float,float,float> vfxAt=(nm,tex,wx,wy,wz,sz)=>{ if(tex==null) return; var g=GameObject.CreatePrimitive(PrimitiveType.Quad); g.name=nm; UnityEngine.Object.DestroyImmediate(g.GetComponent<Collider>()); g.transform.position=new Vector3(wx,wy,wz); g.transform.rotation=Quaternion.Euler(0f,cam.transform.eulerAngles.y,0f); float asp=tex.height>0?(float)tex.width/tex.height:1f; g.transform.localScale=new Vector3(sz*asp,sz,1f); var m=new Material(vfxSh); m.mainTexture=tex; m.renderQueue=2200; var r=g.GetComponent<Renderer>(); r.sharedMaterial=m; r.shadowCastingMode=UnityEngine.Rendering.ShadowCastingMode.Off; r.receiveShadows=false; };
string vfxDbg="none";
if(dmgN>0 && actorPos.ContainsKey(dmgTarget)){ var tp=actorPos[dmgTarget]; float ch=ATH*0.55f; string nl=(narrLine==null?"":narrLine).ToLower();
  bool magic=nl.Contains("bolt")||nl.Contains("fire")||nl.Contains("flame")||nl.Contains("magic")||nl.Contains("eldritch")||nl.Contains("ray")||nl.Contains("spell")||nl.Contains("arcane")||nl.Contains("scorch");
  bool death=nl.Contains("dies")||nl.Contains("falls")||nl.Contains("slain")||nl.Contains("drops")||nl.Contains("collapses")||nl.Contains("crumples");
  if(magic){ vfxAt("VFX_bolt",vfxBolt,tp.x,ch,tp.z,4.2f); vfxDbg="bolt"; }
  else { vfxAt("VFX_impact",vfxImpact,tp.x,ch,tp.z,3.6f); vfxAt("VFX_slash",vfxSlash,tp.x,ch+0.5f,tp.z,4.4f); vfxDbg="slash+impact"; }
  if(death){ vfxAt("VFX_blood",vfxBlood,tp.x,1.0f,tp.z,3.2f); vfxDbg+="+blood"; }  // L5: smaller VFX so it doesn't swallow the actor + team ring
}
sb.AppendLine("VFX@"+dmgTarget+"="+vfxDbg);

// capture (native plate aspect)
int W=1920,Hh=Mathf.RoundToInt(1920f*(float)bdTex.height/bdTex.width); var rt=new RenderTexture(W,Hh,24,RenderTextureFormat.ARGB32); rt.Create();
float pa=cam.aspect; var pt=cam.targetTexture; cam.targetTexture=rt; cam.aspect=(float)W/Hh; cam.Render();
var pAct=RenderTexture.active; RenderTexture.active=rt; var t2=new Texture2D(W,Hh,TextureFormat.RGB24,false);
t2.ReadPixels(new Rect(0,0,W,Hh),0,0); t2.Apply(); RenderTexture.active=pAct; cam.targetTexture=pt; cam.aspect=pa;
System.IO.Directory.CreateDirectory("/home/unity/worldos-unity/Captures-Durable");
System.IO.File.WriteAllBytes("/home/unity/worldos-unity/Captures-Durable/painted_backdrop_r3.png", t2.EncodeToPNG());
UnityEngine.Object.DestroyImmediate(t2); rt.Release(); UnityEngine.Object.DestroyImmediate(rt);
sb.AppendLine("hidden="+hidden+" KEYFROMLEFT="+KEYFROMLEFT+" captured "+W+"x"+Hh+" -> painted_backdrop_r3.png");
return sb.ToString();
