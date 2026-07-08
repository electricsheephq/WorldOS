using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// CombatSurfaceClient (S2/A4; W5b #1433 wiring) — the RUNTIME consumer that renders the ENGINE's
/// combat surface on the built painterly scene. It is the player-build analogue of the editor-only
/// paint_combat_v1.cs capture flow: paint_combat_v1 composes + names the actors in the editor and
/// saves the scene, but does NOT run in the standalone player (it's UnityEditor code). This
/// MonoBehaviour is what keeps the shipped scene LIVE — it polls /combat-surface, repositions the
/// already-placed actors at the engine's authoritative cells, and POSTs the player's click as a
/// /move intent. Engine stays the SOLE WRITER; this renderer is a pure consumer (it only animates
/// engine-confirmed paths).
///
/// #1433: actor resolution now matches the CURRENT asset-registry naming — paint_combat_v1 spawns
/// each token as GameObject "Actor_" + token.id (e.g. Actor_char_f50d226067d4), so we resolve per
/// token by that name instead of the stale, pre-registry GameObject.Find("HeroFighter") lookups.
/// Cell<->world mirrors paint_combat_v1's cellToWorld EXACTLY (grid from the surface, cell 2.0,
/// origin (n-1)/2, row-flipped) so a repositioned token lands on the same painted floor cell the
/// editor placed it on — no jump on the first poll.
///
/// #1436 (W5c Unit 1) RUNTIME SPAWNING: pre-#1436 the player could only REPOSITION actors baked into
/// the static scene by paint_combat_v1 — a token with no matching Actor_<id> GameObject rendered
/// nothing, so any campaign other than the one the scene was baked from showed an empty board. This
/// client now SPAWNS the missing actor at runtime through a spawn path that MIRRORS paint_combat_v1's
/// registry-resolved spawn (SLOT lookup + default fallback per the registry invariant, bind-pose scale
/// lock #1422/#1418, SkinnedMeshRenderer pitch guard #1397, albedo binding #1423/#1425, humanoid idle
/// retarget #1408/#1411, AO/ring siblings), so ANY campaign renders. Packaging (player builds cannot
/// AssetDatabase.Load an Assets/ path): the registry-referenced models/albedos/anim clips are baked
/// into a StandaloneOSX AssetBundle at StreamingAssets/worldos_actors, keyed by their EXACT registry
/// asset path — so the runtime loader passes registry model_ref verbatim (zero path transform, the
/// registry invariant intact), and registry.json is copied verbatim to StreamingAssets/registry.json
/// so the SLOT resolution reads the same manifest the editor baked from. Both are produced by
/// BuildMacOSPlayer.EnsurePackaged(); the editor capture path (paint_combat_v1.cs) is untouched.
/// Engine stays the SOLE WRITER: spawning reads engine cells + the asset registry only, never writes
/// game state. Deterministic: on every surface, tokens present are spawned/repositioned and
/// runtime-spawned actors no longer present are despawned (baked actors are never despawned).
/// </summary>
public class CombatSurfaceClient : MonoBehaviour
{
    [Header("Viewer (reverse-tunnel to the Mac engine)")]
    public string ViewerUrl = "http://127.0.0.1:8765";
    public string CampaignId = "";
    public float PollInterval = 1.5f;

    [Header("Grid — mirror paint_combat_v1 (14x11, cell 2.0); overridden by the surface `grid` block")]
    public int Cols = 14;
    public int Rows = 11;
    public float CellSize = 2f;
    public float FloorY = 0f;

    string _turnToken = "";
    string _foeId = "";
    int _foeX = -1, _foeY = -1;
    bool _busy = false;

    // #1436 runtime-spawn state. _spawned tracks ONLY actors this client instantiated at runtime, so
    // despawn-on-token-removal never touches an actor baked into the scene by paint_combat_v1.
    readonly System.Collections.Generic.HashSet<string> _spawned = new System.Collections.Generic.HashSet<string>();
    AssetBundle _bundle; bool _bundleTried;
    // Parsed registry (StreamingAssets/registry.json), mirroring paint_combat_v1's regAssets/Defaults/Aliases.
    System.Collections.Generic.Dictionary<string, object> _regAssets, _regDefaults, _regAliases;
    bool _regTried;
    Texture2D _blobT, _ringT;                 // procedural AO blob + selection ring, built once, shared
    AnimationClip _donorIdle; bool _donorTried; // goblin.fbx embedded Idle, for clipless-humanoid retarget

    // #1441 W5d player interactivity: grounded reposition + engine-confirmed glide + walk clips + click
    // pre-validation. GlideSpeed tunes the cell->cell walk tween; the maps below track per-actor state.
    [Header("Glide (#1441 W5d)")]
    public float GlideSpeed = 6f;             // world units/sec for the cell->cell walk tween
    AnimationClip _donorWalk; bool _donorWalkTried; // goblin.fbx embedded Walk, for clipless-humanoid glide
    // Each actor's CURRENT engine cell (arrived-at or gliding-toward). A poll reporting the SAME cell is a
    // no-op; a CHANGED cell starts a glide. Seeded on spawn and on a baked actor's first sighting.
    readonly System.Collections.Generic.Dictionary<string, int[]> _cellOf = new System.Collections.Generic.Dictionary<string, int[]>();
    readonly System.Collections.Generic.Dictionary<string, Coroutine> _glide = new System.Collections.Generic.Dictionary<string, Coroutine>();
    // The registry fbx we spawned each actor from, so a glide can play the actor's OWN walk/run clip.
    readonly System.Collections.Generic.Dictionary<string, string> _fbxOf = new System.Collections.Generic.Dictionary<string, string>();
    // Click pre-validation sets (cell key = c*10000+r): impassable = engine grid_impassable (walls/props),
    // parsed from the surface; occupied = every token's cell, rebuilt each ApplySurf.
    readonly System.Collections.Generic.HashSet<int> _impassable = new System.Collections.Generic.HashSet<int>();
    readonly System.Collections.Generic.HashSet<int> _occupied = new System.Collections.Generic.HashSet<int>();
    static int CellKey(int c, int r) { return c * 10000 + r; }  // grids are <14x11 -> collision-free
    // The engine-confirmed route of the most recent move (surface `lastPath` == combat.last_move_path,
    // list of [x,y] incl. the from-cell). The glide follows THIS polyline; empty -> straight-line fallback.
    readonly System.Collections.Generic.List<int[]> _lastPath = new System.Collections.Generic.List<int[]>();

    // #1441 named actor heights — ONE source of truth. These mirror paint_combat_v1.cs's #1418-calibrated
    // LIVE baked-scene heights (foe 4.2 / character 3.2), which is what this client repositions, so a
    // runtime-spawned actor matches its baked twin. NOTE: paint_combat_replay_v1.cs still carries a stale
    // pre-#1418 character height of 5.0 (the editor reel, out of this player-path change's scope) — flagged.
    const float ActorHeightFoe = 4.2f;
    const float ActorHeightChar = 3.2f;

    [System.Serializable] public class Tok { public string id; public string name; public string team; public int x; public int y; public bool isCurrent; }
    [System.Serializable] public class Grid { public int cols; public int rows; }
    [System.Serializable] public class Surf { public string turnToken; public bool can_act; public Grid grid; public Tok[] tokens; }
    [System.Serializable] public class MoveResp { public bool ok; public string reason; public Surf combat; }

    // cellToWorld mirrors paint_combat_v1.cs EXACTLY:
    //   new Vector3((cx-(cols-1)/2)*2.0, 0, ((rows-1)/2-cy)*2.0)
    Vector3 CellToWorld(int c, int r)
    {
        return new Vector3((c - (Cols - 1f) / 2f) * CellSize, FloorY, ((Rows - 1f) / 2f - r) * CellSize);
    }
    bool WorldToCell(Vector3 w, out int c, out int r)
    {
        c = Mathf.RoundToInt(w.x / CellSize + (Cols - 1f) / 2f);
        r = Mathf.RoundToInt((Rows - 1f) / 2f - w.z / CellSize);
        return c >= 0 && c < Cols && r >= 0 && r < Rows;
    }

    void Start()
    {
        // Additive config resolution (#1322 W5a): the standalone player build has no Inspector to
        // hand-edit, so the app host (NSWorkspace launch w/ configuration.environment, mirroring
        // native-bridge.js) hands the engine origin + campaign through the PROCESS ENVIRONMENT.
        // Absent env vars ⇒ today's Inspector-set defaults, byte-identical to pre-#1322 behavior.
        string envUrl = System.Environment.GetEnvironmentVariable("WORLDOS_ENGINE_BASE_URL");
        if (!string.IsNullOrEmpty(envUrl)) ViewerUrl = envUrl;
        string envCampaign = System.Environment.GetEnvironmentVariable("WORLDOS_CAMPAIGN_ID");
        if (!string.IsNullOrEmpty(envCampaign)) CampaignId = envCampaign;

        Debug.Log("[CSC] start: campaign=" + CampaignId + " url=" + ViewerUrl);
        StartCoroutine(PollLoop());
    }

    // Resolve the token's already-placed actor by the registry naming (Actor_ + token.id).
    Transform FindActor(string id)
    {
        if (string.IsNullOrEmpty(id)) return null;
        var go = GameObject.Find("Actor_" + id);
        return go ? go.transform : null;
    }

    IEnumerator PollLoop()
    {
        while (true)
        {
            if (!_busy) yield return Fetch();
            yield return new WaitForSeconds(PollInterval);
        }
    }

    static bool Ok(UnityWebRequest r)
    {
#if UNITY_2020_2_OR_NEWER
        return r.result == UnityWebRequest.Result.Success;
#else
        return !r.isNetworkError && !r.isHttpError;
#endif
    }

    IEnumerator Fetch()
    {
        using (var req = UnityWebRequest.Get(ViewerUrl + "/combat-surface?campaign=" + CampaignId))
        {
            req.timeout = 6;
            yield return req.SendWebRequest();
            if (!Ok(req)) { Debug.LogWarning("[CSC] GET failed: " + req.error); yield break; }
            ApplyJson(req.downloadHandler.text);
        }
    }

    void ApplyJson(string json)
    {
        Surf s = null;
        try { s = JsonUtility.FromJson<Surf>(json); }
        catch (System.Exception e) { Debug.LogWarning("[CSC] parse: " + e.Message); return; }
        // #1441: `impassable` (grid_impassable walls/props) and `lastPath` (the engine-confirmed move
        // route) ride the surface as lists-of-[x,y] that JsonUtility cannot model — parse them with the
        // runtime map/array parser used for registry.json. Impassable is static per location, so caching
        // from the poll covers the move-response path too.
        ParseSurfaceExtras(json);
        ApplySurf(s);
    }

    // Populate _impassable + _lastPath from a raw /combat-surface OR /move response JSON (the latter nests
    // the surface under `combat`). Absent/corrupt leaves the sets empty (clicks unfiltered client-side and
    // a straight-line glide — the engine still rejects illegal moves authoritatively).
    void ParseSurfaceExtras(string json)
    {
        try
        {
            var root = Json.Parse(json) as System.Collections.Generic.Dictionary<string, object>;
            if (root == null) return;
            // /move responses wrap the surface as { ok, arbiter, combat:{...} }; unwrap it.
            if (root.ContainsKey("combat") && root["combat"] is System.Collections.Generic.Dictionary<string, object> inner) root = inner;
            if (root.ContainsKey("impassable"))
            {
                _impassable.Clear();
                var list = root["impassable"] as System.Collections.Generic.List<object>;
                if (list != null) foreach (var ce in list) { var cell = ce as System.Collections.Generic.List<object>; if (cell == null || cell.Count < 2) continue; _impassable.Add(CellKey(System.Convert.ToInt32(cell[0]), System.Convert.ToInt32(cell[1]))); }
            }
            _lastPath.Clear();
            if (root.ContainsKey("lastPath"))
            {
                var lp = root["lastPath"] as System.Collections.Generic.List<object>;
                if (lp != null) foreach (var ce in lp) { var cell = ce as System.Collections.Generic.List<object>; if (cell == null || cell.Count < 2) continue; _lastPath.Add(new[] { System.Convert.ToInt32(cell[0]), System.Convert.ToInt32(cell[1]) }); }
            }
        }
        catch (System.Exception e) { Debug.LogWarning("[CSC] surface-extras parse: " + e.Message); }
    }

    void ApplySurf(Surf s)
    {
        if (s == null || s.tokens == null) return;
        _turnToken = s.turnToken;
        // #1318/#1433: honor the surface's own grid extents (rest-mode rooms can be non-14x11) so
        // cellToWorld stays aligned to what paint_combat_v1 baked. Absent ⇒ the 14x11 default.
        if (s.grid != null && s.grid.cols > 0 && s.grid.rows > 0) { Cols = s.grid.cols; Rows = s.grid.rows; }
        // #1441: rebuild the occupied-cell set (every token's cell) for client-side click pre-validation.
        _occupied.Clear();
        foreach (var t in s.tokens) if (t != null) _occupied.Add(CellKey(t.x, t.y));
        var present = new System.Collections.Generic.HashSet<string>();
        foreach (var t in s.tokens)
        {
            if (!string.IsNullOrEmpty(t.id)) present.Add(t.id);
            bool foe = (t.team == "foe");
            if (foe) { _foeId = t.id; _foeX = t.x; _foeY = t.y; }
            Transform a = FindActor(t.id);
            // #1441: reposition through UpdateActor — grounds+snaps on first sight, GLIDES on a changed
            // engine cell (walk clip + moving rings), no-ops on the same cell. Only engine-confirmed cells.
            if (a != null) UpdateActor(a, t.id, t.x, t.y);
            // #1436: no baked/prior actor for this token -> spawn it at runtime (SpawnActor grounds +
            // centers it on the cell itself, mirroring paint_combat_v1's spawn).
            else SpawnActor(t.id, t.name, t.team, t.x, t.y);
        }
        // #1436 despawn-on-removal: an actor WE spawned that the engine no longer reports is destroyed
        // (with its AO/ring siblings) so a moved-away/removed token never leaves a stale instance.
        // Deterministic; baked actors (not in _spawned) are left untouched.
        if (_spawned.Count > 0)
        {
            var stale = new System.Collections.Generic.List<string>();
            foreach (var id in _spawned) if (!present.Contains(id)) stale.Add(id);
            foreach (var id in stale) Despawn(id);
        }
    }

    // ---- #1436 runtime spawn path (mirrors paint_combat_v1.cs's editor spawn; runtime-safe loads) ----

    // The registry-referenced models/albedos/clips are baked into a StandaloneOSX AssetBundle keyed by
    // their EXACT registry asset path (see BuildMacOSPlayer.EnsurePackaged), so a registry model_ref
    // like "Assets/chars_v2/goblin/goblin.fbx" loads verbatim — zero path transform, registry invariant
    // intact. Loaded once; absent bundle (e.g. a legacy build) -> spawning no-ops, repositioning still works.
    AssetBundle Bundle()
    {
        if (_bundleTried) return _bundle;
        _bundleTried = true;
        try
        {
            string p = System.IO.Path.Combine(Application.streamingAssetsPath, "worldos_actors");
            if (System.IO.File.Exists(p)) _bundle = AssetBundle.LoadFromFile(p);
            Debug.Log("[CSC] actor bundle " + (_bundle != null ? "loaded" : "absent") + " @" + p);
        }
        catch (System.Exception e) { Debug.LogWarning("[CSC] bundle load: " + e.Message); }
        return _bundle;
    }

    T LoadAsset<T>(string assetPath) where T : Object
    {
        var b = Bundle();
        if (b == null || string.IsNullOrEmpty(assetPath)) return null;
        return b.LoadAsset<T>(assetPath);
    }

    // Parse StreamingAssets/registry.json into the same assets/defaults/aliases maps paint_combat_v1
    // reads. Uses a self-contained parser (MiniJson lives in the editor-only assembly and is not
    // available to this runtime MonoBehaviour). Absent/corrupt -> null maps -> resolve falls to the
    // in-code team default (goblin/hero), never null (byte-identical to paint's registry==null branch).
    void LoadRegistry()
    {
        if (_regTried) return;
        _regTried = true;
        try
        {
            string p = System.IO.Path.Combine(Application.streamingAssetsPath, "registry.json");
            if (!System.IO.File.Exists(p)) { Debug.LogWarning("[CSC] no registry.json @" + p); return; }
            var root = Json.Parse(System.IO.File.ReadAllText(p)) as System.Collections.Generic.Dictionary<string, object>;
            if (root != null)
            {
                _regAssets = root.ContainsKey("assets") ? root["assets"] as System.Collections.Generic.Dictionary<string, object> : null;
                _regDefaults = root.ContainsKey("defaults") ? root["defaults"] as System.Collections.Generic.Dictionary<string, object> : null;
                _regAliases = root.ContainsKey("aliases") ? root["aliases"] as System.Collections.Generic.Dictionary<string, object> : null;
            }
        }
        catch (System.Exception e) { Debug.LogWarning("[CSC] registry parse: " + e.Message); }
    }

    static string Slugify(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        var b = new System.Text.StringBuilder();
        foreach (char c in s.ToLower())
        {
            if (char.IsLetterOrDigit(c)) b.Append(c);
            else if (b.Length > 0 && b[b.Length - 1] != '-') b.Append('-');
        }
        return b.ToString().Trim('-');
    }

    // Returns [model_ref, albedo_ref, anim_ref] — EXACTLY mirrors paint_combat_v1.cs's resolveAsset,
    // including the in-code team default (monster->goblin / character->hero, NOT AssetRegistry's
    // hero-for-everything floor) and the #1423 albedo nuance (only substitute the template albedo when
    // this token fell through to a default; a real resolved row with empty albedo means "own material").
    string[] ResolveAsset(string slug, string kind)
    {
        LoadRegistry();
        string fbxDef = kind == "monster" ? "Assets/chars_v2/goblin/goblin.fbx" : "Assets/painterly/models/hero.fbx";
        string albDef = kind == "monster" ? "Assets/chars_v2/goblin/albedo.png" : "Assets/painterly/models/hero_albedo.png";
        if (_regAssets == null) return new[] { fbxDef, albDef, "" };
        string id = slug;
        bool exactOrAlias = _regAssets.ContainsKey(id);
        if (!exactOrAlias && _regAliases != null && _regAliases.ContainsKey(id)) { id = _regAliases[id] as string; exactOrAlias = id != null && _regAssets.ContainsKey(id); }
        if (!exactOrAlias && _regDefaults != null) { if (_regDefaults.ContainsKey(kind)) id = _regDefaults[kind] as string; else if (_regDefaults.ContainsKey("__any__")) id = _regDefaults["__any__"] as string; }
        if (id != null && _regAssets.ContainsKey(id))
        {
            var a = _regAssets[id] as System.Collections.Generic.Dictionary<string, object>;
            if (a != null)
            {
                string m = a.ContainsKey("model_ref") ? a["model_ref"] as string : null;
                string al = a.ContainsKey("albedo_ref") ? a["albedo_ref"] as string : null;
                string an = a.ContainsKey("anim_ref") ? a["anim_ref"] as string : null;
                string alOut = string.IsNullOrEmpty(al) ? (exactOrAlias ? null : albDef) : al;
                return new[] { string.IsNullOrEmpty(m) ? fbxDef : m, alOut, an ?? "" };
            }
        }
        return new[] { fbxDef, albDef, "" };
    }

    // goblin.fbx carries its OWN embedded Idle on a HUMANOID avatar (#1408 donor). Loaded once from the
    // bundle by the registry's "goblin" model_ref, reused to retarget every clipless-humanoid actor.
    AnimationClip DonorIdle()
    {
        if (_donorTried) return _donorIdle;
        _donorTried = true;
        var aref = ResolveAsset("goblin", "monster");
        var b = Bundle(); if (b == null) return null;
        foreach (var o in b.LoadAssetWithSubAssets<AnimationClip>(aref[0]))
        {
            if (o == null || o.name.StartsWith("__")) continue;
            if (o.name.ToLower().Contains("idle")) { _donorIdle = o; break; }
            if (_donorIdle == null) _donorIdle = o;
        }
        return _donorIdle;
    }

    Texture2D BlobTex()
    {
        if (_blobT != null) return _blobT;
        // aiShadowSoftness=0.9, aiShadowIntensity=1.0 baseline (paint_combat_v1's no-config-file defaults).
        _blobT = new Texture2D(256, 256, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp };
        var px = new Color[256 * 256]; float c = 127.5f;
        for (int y = 0; y < 256; y++) for (int x = 0; x < 256; x++) { float d = Mathf.Clamp01(Mathf.Sqrt((x - c) * (x - c) + (y - c) * (y - c)) / c); px[y * 256 + x] = new Color(0.02f, 0.02f, 0.03f, Mathf.Clamp01(Mathf.Pow(1f - d, 0.9f))); }
        _blobT.SetPixels(px); _blobT.Apply();
        return _blobT;
    }

    Texture2D RingTex()
    {
        if (_ringT != null) return _ringT;
        _ringT = new Texture2D(256, 256, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp };
        var px = new Color[256 * 256]; float c = 127.5f;
        for (int y = 0; y < 256; y++) for (int x = 0; x < 256; x++) { float d = Mathf.Sqrt((x - c) * (x - c) + (y - c) * (y - c)) / c; float a = (d > 0.78f && d < 0.93f) ? 1f : 0f; px[y * 256 + x] = new Color(1f, 1f, 1f, a); }
        _ringT.SetPixels(px); _ringT.Apply();
        return _ringT;
    }

    // World-space bounds of a renderer. Skinned: BakeMesh the POSED verts and transform by
    // TRS(pos,rot,Vector3.one) — scale is DROPPED (#1412: BakeMesh already reflects lossyScale, so the
    // full matrix double-applies it). MeshRenderer.bounds is accurate as-is. Mirrors paint_combat_v1.
    static Bounds WorldBounds(Renderer r)
    {
        var smr = r as SkinnedMeshRenderer;
        if (smr == null) return r.bounds;
        var bk = new Mesh(); smr.BakeMesh(bk); var vs = bk.vertices;
        if (vs.Length == 0) { Object.DestroyImmediate(bk); return r.bounds; }
        var m = Matrix4x4.TRS(smr.transform.position, smr.transform.rotation, Vector3.one);
        var wb = new Bounds(m.MultiplyPoint3x4(vs[0]), Vector3.zero);
        for (int i = 1; i < vs.Length; i++) wb.Encapsulate(m.MultiplyPoint3x4(vs[i]));
        Object.DestroyImmediate(bk);
        return wb;
    }

    static Bounds Measure(GameObject go, Renderer[] rends)
    {
        Bounds b = new Bounds(go.transform.position, Vector3.zero); bool a = false;
        foreach (var r in rends) { var rb = WorldBounds(r); if (!a) { b = rb; a = true; } else b.Encapsulate(rb); }
        return b;
    }

    // Spawn one actor for a token that has no baked/prior GameObject. Mirrors paint_combat_v1.cs's
    // spawn() lambda: registry-resolve -> load prefab from the bundle -> pitch guard -> BIND-POSE scale
    // lock -> idle pose (embedded clip, else humanoid donor retarget) -> ground+center on the cell ->
    // albedo -> AO blob + selection ring. Returns the placed transform, or null if the model is missing.
    Transform SpawnActor(string id, string tokName, string team, int cx, int cy)
    {
        if (string.IsNullOrEmpty(id)) return null;
        bool foe = (team == "foe");
        string kind = foe ? "monster" : "character";
        var aref = ResolveAsset(Slugify(tokName), kind);
        string fbx = aref[0], alb = aref[1];
        var prefab = LoadAsset<GameObject>(fbx);
        if (prefab == null) { Debug.LogWarning("[CSC] spawn MISSING model " + fbx + " for token " + id + " (bundle stale?)"); return null; }

        string nm = "Actor_" + id;
        var existing = GameObject.Find(nm); if (existing != null) Object.DestroyImmediate(existing);
        var go = (GameObject)Object.Instantiate(prefab); go.name = nm;

        var cam = Camera.main;
        float camYaw = cam != null ? cam.transform.eulerAngles.y : 45f;
        // #1397 pitch guard: a skinned Meshy Y-up rig needs pitch 0; only a static non-skinned mesh keeps
        // the legacy -90 Z-up stand-up. Set before posing (depends only on rig type).
        float pitchX = go.GetComponentInChildren<SkinnedMeshRenderer>() != null ? 0f : -90f;
        go.transform.rotation = Quaternion.Euler(pitchX, camYaw + 180f, 0f);

        var rends = go.GetComponentsInChildren<Renderer>();
        foreach (var r in rends)
        {
            r.enabled = true; r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.On; r.receiveShadows = true;
            var smr = r as SkinnedMeshRenderer; if (smr != null) { smr.updateWhenOffscreen = true; smr.forceMatrixRecalculationPerRender = true; }
        }

        // #1418 scale lock from the BIND POSE (measured BEFORE any clip is sampled), so a wide/leaning
        // idle first frame can't inflate curH and over-scale the actor.
        Bounds bb = Measure(go, rends); float curH = bb.size.y > 0.001f ? bb.size.y : 1f;
        float height = foe ? ActorHeightFoe : ActorHeightChar;   // #1441: named, single-source heights
        float sc = height / curH; go.transform.localScale = go.transform.localScale * sc;

        // Pose to a neutral idle for the VISUAL now that scale is locked: prefer an embedded 'idle' clip
        // on the model; else, for a clipless HUMANOID rig, one-shot retarget the goblin donor idle.
        bool posedByClip = false;
        var b2 = Bundle();
        if (b2 != null)
        {
            foreach (var clip in b2.LoadAssetWithSubAssets<AnimationClip>(fbx))
            {
                if (clip == null || clip.name.StartsWith("__")) continue;
                if (clip.name.ToLower().Contains("idle")) { clip.SampleAnimation(go, 0f); posedByClip = true; break; }
                if (!posedByClip) { clip.SampleAnimation(go, 0f); posedByClip = true; }
            }
        }
        if (!posedByClip)
        {
            var anim = go.GetComponentInChildren<Animator>();
            if (anim != null && anim.avatar != null && anim.avatar.isHuman)
            {
                var donor = DonorIdle();
                if (donor != null)
                {
                    var graph = UnityEngine.Playables.PlayableGraph.Create("HumanoidIdleRetarget_" + nm);
                    var clipPlayable = UnityEngine.Animations.AnimationClipPlayable.Create(graph, donor);
                    var outp = UnityEngine.Animations.AnimationPlayableOutput.Create(graph, "Output", anim);
                    UnityEngine.Playables.PlayableOutputExtensions.SetSourcePlayable(outp, clipPlayable);
                    graph.Evaluate(0f); graph.Destroy();
                }
            }
        }

        // Ground + center on the cell: feet to FloorY, bounds-center X/Z to the cell.
        Vector3 p = CellToWorld(cx, cy); go.transform.position = p; bb = Measure(go, rends); Vector3 ctr = bb.center;
        go.transform.position += new Vector3(p.x - ctr.x, FloorY - bb.min.y, p.z - ctr.z);

        // Albedo (#1423/#1425): Standard material off the resolved albedo; null alb -> keep the model's
        // own imported material (a real resolved row with no albedo_ref).
        if (!string.IsNullOrEmpty(alb))
        {
            var al = LoadAsset<Texture2D>(alb);
            if (al != null)
            {
                var mm = new Material(Shader.Find("Standard")); mm.mainTexture = al; mm.SetFloat("_Glossiness", 0.2f); mm.SetFloat("_Metallic", 0f);
                foreach (var r in rends) r.sharedMaterial = mm;
            }
        }

        // AO blob + selection ring siblings (Actor_<id>_AO / _Ring), laid flat on the floor. Baseline
        // aiShadowScale=2.0, ring 2.6, no core shadow (paint's no-config defaults). MoveActorAndShadows
        // moves these by the same delta on every reposition/glide frame, so they track the feet.
        MakeGroundQuad(nm + "_AO", p, 0.04f, 2.0f, BlobTex(), Color.white, 1950);
        MakeGroundQuad(nm + "_Ring", p, 0.06f, 2.6f, RingTex(), foe ? new Color(1f, 0.13f, 0.10f, 1f) : new Color(0.4f, 0.95f, 1f, 1f), 1955);

        _spawned.Add(id);
        // #1441: remember the fbx (so a glide can play this actor's own walk clip) and seed the cell (so
        // the first poll doesn't spuriously glide a just-spawned actor already on its engine cell).
        _fbxOf[id] = fbx;
        _cellOf[id] = new[] { cx, cy };
        Debug.Log("[CSC] spawned " + nm + " model=" + fbx + " x" + sc.ToString("F2") + " @cell(" + cx + "," + cy + ") rends=" + rends.Length);
        return go.transform;
    }

    void MakeGroundQuad(string name, Vector3 p, float yOff, float scale, Texture2D tex, Color col, int queue)
    {
        var old = GameObject.Find(name); if (old != null) Object.DestroyImmediate(old);
        var q = GameObject.CreatePrimitive(PrimitiveType.Quad); q.name = name; Object.DestroyImmediate(q.GetComponent<Collider>());
        q.transform.position = new Vector3(p.x, FloorY + yOff, p.z); q.transform.localEulerAngles = new Vector3(90f, 0f, 0f); q.transform.localScale = new Vector3(scale, scale, 1f);
        var m = new Material(Shader.Find("Unlit/Transparent")); m.mainTexture = tex; m.color = col; m.renderQueue = queue;
        var r = q.GetComponent<Renderer>(); r.sharedMaterial = m; r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
    }

    void Despawn(string id)
    {
        foreach (var suf in new[] { "", "_AO", "_Core", "_Ring" })
        {
            var g = GameObject.Find("Actor_" + id + suf);
            if (g != null) Object.Destroy(g);
        }
        _spawned.Remove(id);
        // #1441: tear down any in-flight glide + per-actor state so a re-spawn starts clean.
        if (_glide.TryGetValue(id, out var co) && co != null) StopCoroutine(co);
        _glide.Remove(id); _cellOf.Remove(id); _fbxOf.Remove(id);
        Debug.Log("[CSC] despawned Actor_" + id);
    }

    // Self-contained JSON parser for the registry map-of-maps (arbitrary asset_id keys, which
    // JsonUtility cannot model). A runtime-assembly twin of the editor-only MiniJson.cs — same
    // object->Dictionary / array->List / number->double shape — so this MonoBehaviour has no
    // editor-assembly dependency. Parse only.
    static class Json
    {
        public static object Parse(string json)
        {
            if (string.IsNullOrEmpty(json)) return null;
            int i = 0; return ParseValue(json, ref i);
        }
        static object ParseValue(string s, ref int i)
        {
            SkipWs(s, ref i); if (i >= s.Length) return null;
            switch (s[i])
            {
                case '{': return ParseObject(s, ref i);
                case '[': return ParseArray(s, ref i);
                case '"': return ParseString(s, ref i);
                case 't': i += 4; return true;
                case 'f': i += 5; return false;
                case 'n': i += 4; return null;
                default: return ParseNumber(s, ref i);
            }
        }
        static System.Collections.Generic.Dictionary<string, object> ParseObject(string s, ref int i)
        {
            var o = new System.Collections.Generic.Dictionary<string, object>(); i++;
            while (true)
            {
                SkipWs(s, ref i); if (i >= s.Length) break;
                if (s[i] == '}') { i++; break; }
                if (s[i] == ',') { i++; continue; }
                string key = ParseString(s, ref i); SkipWs(s, ref i);
                if (i < s.Length && s[i] == ':') i++;
                o[key] = ParseValue(s, ref i);
            }
            return o;
        }
        static System.Collections.Generic.List<object> ParseArray(string s, ref int i)
        {
            var a = new System.Collections.Generic.List<object>(); i++;
            while (true)
            {
                SkipWs(s, ref i); if (i >= s.Length) break;
                if (s[i] == ']') { i++; break; }
                if (s[i] == ',') { i++; continue; }
                a.Add(ParseValue(s, ref i));
            }
            return a;
        }
        static string ParseString(string s, ref int i)
        {
            var sb = new System.Text.StringBuilder(); i++;
            while (i < s.Length)
            {
                char c = s[i++]; if (c == '"') break;
                if (c == '\\' && i < s.Length)
                {
                    char e = s[i++];
                    switch (e)
                    {
                        case '"': sb.Append('"'); break;
                        case '\\': sb.Append('\\'); break;
                        case '/': sb.Append('/'); break;
                        case 'b': sb.Append('\b'); break;
                        case 'f': sb.Append('\f'); break;
                        case 'n': sb.Append('\n'); break;
                        case 'r': sb.Append('\r'); break;
                        case 't': sb.Append('\t'); break;
                        case 'u': if (i + 4 <= s.Length) { sb.Append((char)int.Parse(s.Substring(i, 4), System.Globalization.NumberStyles.HexNumber, System.Globalization.CultureInfo.InvariantCulture)); i += 4; } break;
                        default: sb.Append(e); break;
                    }
                }
                else sb.Append(c);
            }
            return sb.ToString();
        }
        static object ParseNumber(string s, ref int i)
        {
            int start = i;
            while (i < s.Length && (char.IsDigit(s[i]) || s[i] == '-' || s[i] == '+' || s[i] == '.' || s[i] == 'e' || s[i] == 'E')) i++;
            double d; return double.TryParse(s.Substring(start, i - start), System.Globalization.NumberStyles.Any, System.Globalization.CultureInfo.InvariantCulture, out d) ? d : 0.0;
        }
        static void SkipWs(string s, ref int i) { while (i < s.Length && char.IsWhiteSpace(s[i])) i++; }
    }

    // #1441: engine-confirmed reposition. INVARIANT — the renderer animates ONLY engine-confirmed cells:
    // callers pass cells straight from the authoritative surface and the client never moves an actor
    // before the /move response. First sighting -> ground+snap (unifies grounding with SpawnActor, the
    // float fix); a CHANGED engine cell -> start a glide; the SAME cell -> no-op so a poll never restarts
    // or interrupts an in-flight glide (poll pauses reposition for a gliding actor).
    void UpdateActor(Transform a, string id, int cx, int cy)
    {
        int[] cur;
        if (!_cellOf.TryGetValue(id, out cur))
        {
            _cellOf[id] = new[] { cx, cy };
            GroundSnap(a, cx, cy);
            return;
        }
        if (cur[0] == cx && cur[1] == cy) return;      // already at / gliding toward this cell
        int fromCx = cur[0], fromCy = cur[1];
        _cellOf[id] = new[] { cx, cy };
        if (_glide.TryGetValue(id, out var running) && running != null) StopCoroutine(running);
        _glide[id] = StartCoroutine(GlideTo(a, id, fromCx, fromCy, cx, cy));
    }

    // Instant grounded placement: feet -> FloorY + bounds-center on the cell, the SAME BakeMesh math as
    // SpawnActor. #1441 FLOAT FIX: the pre-#1441 reposition preserved the actor's raw Y and only
    // re-centered X/Z, so any actor whose pivot Y wasn't already grounded (baked actors; post-retarget
    // bounds shifts) floated after a move — this re-grounds Y on every reposition.
    void GroundSnap(Transform a, int cx, int cy) { MoveActorAndShadows(a, GroundedPivot(a, cx, cy)); }

    // The pivot position that lands the actor's posed bounds-center on (cx,cy) with feet (bb.min.y) on
    // FloorY — mirrors SpawnActor's ground+center, via the static Measure (BakeMesh, scale-correct).
    Vector3 GroundedPivot(Transform a, int cx, int cy)
    {
        var rends = a.GetComponentsInChildren<Renderer>();
        Bounds bb = Measure(a.gameObject, rends);
        Vector3 ctr = bb.center, cell = CellToWorld(cx, cy);
        return new Vector3(a.position.x + (cell.x - ctr.x), a.position.y + (FloorY - bb.min.y), a.position.z + (cell.z - ctr.z));
    }

    // Move the actor to newPos and drag its AO/ring/core siblings by the same delta so they track the feet.
    void MoveActorAndShadows(Transform a, Vector3 newPos)
    {
        Vector3 delta = newPos - a.position;
        a.position = newPos;
        foreach (var suf in new[] { "_AO", "_Core", "_Ring" })
        {
            var g = GameObject.Find(a.name + suf);
            if (g != null) g.transform.position += delta;
        }
    }

    // #1441 GLIDE: tween the actor cell->cell at GlideSpeed, playing a walk clip while moving and
    // returning to idle at rest. Follows the ENGINE-CONFIRMED lastPath polyline when it matches this
    // move (start==lastPath[0] && target==lastPath[-1]); otherwise a straight-line fallback. Rings/AO
    // follow every frame. Presentation-only: only ever called with an engine-confirmed target.
    IEnumerator GlideTo(Transform a, string id, int fromCx, int fromCy, int cx, int cy)
    {
        var go = a.gameObject;
        float pitchX = go.GetComponentInChildren<SkinnedMeshRenderer>() != null ? 0f : -90f;

        // Build the world-space route. Default: straight line start->target. If the engine's lastPath is
        // this actor's move, follow its cells (each cell grounded to feet on FloorY via the same offset).
        Vector3 startPos = a.position;
        Vector3 endPos = GroundedPivot(a, cx, cy);
        var route = new System.Collections.Generic.List<Vector3> { startPos };
        bool usePath = _lastPath.Count >= 2
            && _lastPath[0][0] == fromCx && _lastPath[0][1] == fromCy
            && _lastPath[_lastPath.Count - 1][0] == cx && _lastPath[_lastPath.Count - 1][1] == cy;
        if (usePath)
        {
            // ground offset that maps the from-cell's CellToWorld to the actor's current grounded pivot,
            // reused for every intermediate cell so the whole walk stays foot-planted on the flat floor.
            Vector3 fromCellW = CellToWorld(fromCx, fromCy);
            Vector3 gOff = new Vector3(startPos.x - fromCellW.x, startPos.y - fromCellW.y, startPos.z - fromCellW.z);
            for (int i = 1; i < _lastPath.Count; i++) route.Add(CellToWorld(_lastPath[i][0], _lastPath[i][1]) + gOff);
        }
        else route.Add(endPos);

        // face the first heading (game feel), pitch-guarded.
        Vector3 h0 = route[1] - route[0]; h0.y = 0f;
        if (h0.sqrMagnitude > 1e-4f) a.rotation = Quaternion.Euler(pitchX, Mathf.Atan2(h0.x, h0.z) * Mathf.Rad2Deg, 0f);

        // resolve a walk animation: the actor's OWN walk/run clip (SampleAnimation), else a humanoid
        // donor-walk retarget graph, else glide with no clip (non-humanoid fallback).
        AnimationClip ownWalk = FindOwnClip(id, "walk", "run");
        UnityEngine.Playables.PlayableGraph walkGraph = default; bool haveGraph = false;
        if (ownWalk == null)
        {
            var anim = go.GetComponentInChildren<Animator>();
            var donor = DonorWalk();
            if (anim != null && anim.avatar != null && anim.avatar.isHuman && donor != null)
            {
                walkGraph = UnityEngine.Playables.PlayableGraph.Create("Walk_" + a.name);
                var clipP = UnityEngine.Animations.AnimationClipPlayable.Create(walkGraph, donor);
                var outp = UnityEngine.Animations.AnimationPlayableOutput.Create(walkGraph, "Out", anim);
                UnityEngine.Playables.PlayableOutputExtensions.SetSourcePlayable(outp, clipP);
                haveGraph = true;
            }
        }

        // total planar length for even-speed sampling across the (possibly multi-segment) route.
        float total = 0f;
        var segLen = new float[route.Count - 1];
        for (int i = 0; i < segLen.Length; i++) { Vector3 d = route[i + 1] - route[i]; d.y = 0f; segLen[i] = d.magnitude; total += segLen[i]; }
        float dur = GlideSpeed > 0.01f ? total / GlideSpeed : 0f;
        float elapsed = 0f, animT = 0f;
        while (elapsed < dur && total > 1e-4f)
        {
            elapsed += Time.deltaTime; animT += Time.deltaTime;
            float travelled = Mathf.Clamp01(elapsed / dur) * total;
            // find the current segment + interpolant.
            int si = 0; float acc = 0f;
            while (si < segLen.Length - 1 && acc + segLen[si] < travelled) { acc += segLen[si]; si++; }
            float sf = segLen[si] > 1e-4f ? (travelled - acc) / segLen[si] : 1f;
            Vector3 p = Vector3.Lerp(route[si], route[si + 1], sf);
            // face this segment's heading; advance the walk animation.
            Vector3 hd = route[si + 1] - route[si]; hd.y = 0f;
            if (hd.sqrMagnitude > 1e-4f) a.rotation = Quaternion.Euler(pitchX, Mathf.Atan2(hd.x, hd.z) * Mathf.Rad2Deg, 0f);
            if (ownWalk != null) { float len = ownWalk.length > 0.01f ? ownWalk.length : 1f; ownWalk.SampleAnimation(go, animT % len); }
            else if (haveGraph) walkGraph.Evaluate(Time.deltaTime);
            MoveActorAndShadows(a, p);
            yield return null;
        }
        // arrive: snap exact, tear down walk, return to a grounded idle facing the camera.
        MoveActorAndShadows(a, endPos);
        if (haveGraph && walkGraph.IsValid()) walkGraph.Destroy();
        PoseIdle(go);
        var cam = Camera.main; float camYaw = cam != null ? cam.transform.eulerAngles.y : 45f;
        a.rotation = Quaternion.Euler(pitchX, camYaw + 180f, 0f);
        MoveActorAndShadows(a, GroundedPivot(a, cx, cy));   // re-ground the final idle pose (idempotent)
        _glide.Remove(id);
    }

    // Pose `go` to a neutral idle: its own embedded 'idle' clip if present, else a humanoid donor-idle
    // retarget — the same idle SpawnActor establishes, so a glide returns to it at rest.
    void PoseIdle(GameObject go)
    {
        string nm = go.name;
        string id = nm.StartsWith("Actor_") ? nm.Substring(6) : nm;
        string fbx; _fbxOf.TryGetValue(id, out fbx);
        bool posed = false;
        var b = Bundle();
        if (b != null && !string.IsNullOrEmpty(fbx))
        {
            foreach (var clip in b.LoadAssetWithSubAssets<AnimationClip>(fbx))
            {
                if (clip == null || clip.name.StartsWith("__")) continue;
                if (clip.name.ToLower().Contains("idle")) { clip.SampleAnimation(go, 0f); posed = true; break; }
                if (!posed) { clip.SampleAnimation(go, 0f); posed = true; }
            }
        }
        if (!posed)
        {
            var anim = go.GetComponentInChildren<Animator>();
            if (anim != null && anim.avatar != null && anim.avatar.isHuman)
            {
                var donor = DonorIdle();
                if (donor != null)
                {
                    var graph = UnityEngine.Playables.PlayableGraph.Create("Idle_" + nm);
                    var clipPlayable = UnityEngine.Animations.AnimationClipPlayable.Create(graph, donor);
                    var outp = UnityEngine.Animations.AnimationPlayableOutput.Create(graph, "Output", anim);
                    UnityEngine.Playables.PlayableOutputExtensions.SetSourcePlayable(outp, clipPlayable);
                    graph.Evaluate(0f); graph.Destroy();
                }
            }
        }
    }

    // The actor's OWN embedded clip whose name contains any of `names` (walk/run), from the bundle by its
    // spawn fbx. Null for baked actors (no _fbxOf) or when no such clip exists -> donor / no-clip fallback.
    AnimationClip FindOwnClip(string id, params string[] names)
    {
        string fbx;
        if (!_fbxOf.TryGetValue(id, out fbx) || string.IsNullOrEmpty(fbx)) return null;
        var b = Bundle(); if (b == null) return null;
        foreach (var clip in b.LoadAssetWithSubAssets<AnimationClip>(fbx))
        {
            if (clip == null || clip.name.StartsWith("__")) continue;
            string ln = clip.name.ToLower();
            foreach (var n in names) if (ln.Contains(n)) return clip;
        }
        return null;
    }

    // A donor WALK clip from goblin.fbx (embedded moveset) — the walk analogue of DonorIdle, retargeted
    // onto any clipless humanoid during a glide. Null if goblin carries no walk/run clip (-> glide, no clip).
    AnimationClip DonorWalk()
    {
        if (_donorWalkTried) return _donorWalk;
        _donorWalkTried = true;
        var aref = ResolveAsset("goblin", "monster");
        var b = Bundle(); if (b == null) return null;
        foreach (var o in b.LoadAssetWithSubAssets<AnimationClip>(aref[0]))
        {
            if (o == null || o.name.StartsWith("__")) continue;
            string ln = o.name.ToLower();
            if (ln.Contains("walk") || ln.Contains("run")) { _donorWalk = o; break; }
        }
        return _donorWalk;
    }

    void Update()
    {
        if (_busy) return;
        if (Input.GetMouseButtonDown(0)) HandleClick();
    }

    // Minimal input: raycast the click onto the floor plane -> cell -> POST the existing /move kinds
    // ONLY (move_to_cell, or an on-turn attack when the clicked cell holds the foe). Payload mirrors
    // the viewer driver (qa/drive_gfx_combat.py) exactly.
    void HandleClick()
    {
        var cam = Camera.main; if (cam == null) return;
        Ray ray = cam.ScreenPointToRay(Input.mousePosition);
        if (Mathf.Abs(ray.direction.y) < 1e-4f) return;
        float tt = (FloorY - ray.origin.y) / ray.direction.y; if (tt < 0) return;
        Vector3 hit = ray.origin + ray.direction * tt;
        if (!WorldToCell(hit, out int c, out int r)) return;
        // on-turn attack when the clicked cell holds the foe (allowed even though the foe occupies it).
        if (c == _foeX && r == _foeY && _foeId.Length > 0) { StartCoroutine(PostAttack()); return; }
        // #1441 click pre-validation (UX pre-filter ONLY; the engine stays authoritative and independently
        // rejects illegal moves): a click on an impassable (wall/prop) or token-occupied cell flashes a red
        // ring instead of firing a doomed POST.
        int key = CellKey(c, r);
        if (_impassable.Contains(key) || _occupied.Contains(key)) { StartCoroutine(FlashReject(c, r)); return; }
        StartCoroutine(PostMove(c, r));
    }

    // Brief red ring flash at a rejected cell — immediate "you can't go there" with no server round-trip.
    IEnumerator FlashReject(int c, int r)
    {
        Vector3 p = CellToWorld(c, r);
        var q = GameObject.CreatePrimitive(PrimitiveType.Quad); q.name = "RejectFlash"; Object.DestroyImmediate(q.GetComponent<Collider>());
        q.transform.position = new Vector3(p.x, FloorY + 0.07f, p.z); q.transform.localEulerAngles = new Vector3(90f, 0f, 0f); q.transform.localScale = new Vector3(2.6f, 2.6f, 1f);
        var m = new Material(Shader.Find("Unlit/Transparent")); m.mainTexture = RingTex(); m.renderQueue = 1960;
        q.GetComponent<Renderer>().sharedMaterial = m; q.GetComponent<Renderer>().shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        float t = 0f;
        while (t < 0.35f) { t += Time.deltaTime; m.color = new Color(1f, 0.15f, 0.12f, Mathf.Clamp01(1f - t / 0.35f)); yield return null; }
        Object.Destroy(q);
    }

    // ---- public, for headless/programmatic driving (the box has no mouse) ----
    public void DoMove(int x, int y) { if (!_busy) StartCoroutine(PostMove(x, y)); }
    public void DoAttack() { if (!_busy && _foeId.Length > 0) StartCoroutine(PostAttack()); }

    IEnumerator PostMove(int x, int y)
    {
        _busy = true;
        yield return Post("{\"kind\":\"move_to_cell\",\"x\":" + x + ",\"y\":" + y + ",\"turn_token\":\"" + _turnToken + "\",\"campaign\":\"" + CampaignId + "\"}");
        _busy = false;
    }
    IEnumerator PostAttack()
    {
        _busy = true;
        yield return Post("{\"kind\":\"attack\",\"target_id\":\"" + _foeId + "\",\"turn_token\":\"" + _turnToken + "\",\"campaign\":\"" + CampaignId + "\"}");
        _busy = false;
    }

    IEnumerator Post(string body)
    {
        using (var req = new UnityWebRequest(ViewerUrl + "/move", "POST"))
        {
            req.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(body));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout = 8;
            yield return req.SendWebRequest();
            if (!Ok(req)) { Debug.LogWarning("[CSC] /move failed: " + req.error + " body=" + req.downloadHandler.text); yield break; }
            MoveResp resp = null;
            try { resp = JsonUtility.FromJson<MoveResp>(req.downloadHandler.text); }
            catch (System.Exception e) { Debug.LogWarning("[CSC] move parse: " + e.Message); yield break; }
            // #1441: parse lastPath/impassable from the RAW response (nested under `combat`) BEFORE
            // ApplySurf so the glide can follow the engine-confirmed route of the move just resolved.
            if (resp != null && resp.ok && resp.combat != null) { Debug.Log("[CSC] move ok -> re-render"); ParseSurfaceExtras(req.downloadHandler.text); ApplySurf(resp.combat); }
            else Debug.LogWarning("[CSC] move rejected: " + (resp != null ? resp.reason : "null"));
        }
    }
}
