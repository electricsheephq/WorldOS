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

    // W6.2 (#1461) REST-MODE walk. A rest surface carries NO combat signals (empty `turnToken`, no
    // isCurrent token); its `stage` block ({mode, tokens:[{id,x,y,rest_role}]}) marks it mode:"rest".
    // In rest mode a click routes to the engine's `walk_to` verb (the `walk_to_cell` /move intent)
    // instead of the combat move — with the SAME impassable/occupied pre-validation the combat path got
    // in #1441 (rest_blocked_cells folds standers INTO the surface `impassable`, so the walkability
    // overlay + the click gate read one collision truth). `_restMoverId` is WHO walks: the first
    // rest_role:"party" stage token (the deterministic lead PC), mirroring the browser board's
    // "selected party token walks" (screen-combat.jsx). Both stay false/empty on a COMBAT surface, so
    // every combat-mode code path below is byte-identical.
    bool _restMode = false;
    string _restMoverId = "";

    // #anim-combat: the actor's ANIM_REF (moveset) fbx (registry anim_ref), so a walk/attack/hit clip that
    // lives in a SEPARATE moveset fbx rather than the model fbx is still found. Mirrors _fbxOf; both feed
    // FindOwnClip. (For the wave-2 cast the walk clip is embedded in the MODEL fbx — e.g. goblin.fbx carries
    // Idle/Walk/Attack — so _fbxOf covers those; _animOf future-proofs the separate-moveset rigs.)
    readonly System.Collections.Generic.Dictionary<string, string> _animOf = new System.Collections.Generic.Dictionary<string, string>();
    // Per-actor head-top world offset (from the pivot) for the world-space HP bar, measured once so the
    // bar rides above the silhouette without a per-frame BakeMesh.
    readonly System.Collections.Generic.Dictionary<string, float> _topOf = new System.Collections.Generic.Dictionary<string, float>();

    // #anim-combat COMBAT FEEL (paint_combat_replay_v1.cs verb map, ported to the LIVE player). Pure
    // consumer of the surface's per-token hp: a DROP flinches the target (knockback nudge), floats the
    // damage delta (world-space number, fade-up), lunges the attacker (the isCurrent combatant, + its
    // attack clip when it has one), and drops the target's HP bar; hp<=0 while the token is STILL on the
    // surface plays a DOWNED collapse (prone, dimmed ring — revivable, #1106 heals); the token VANISHING
    // from the surface is the true removal (fade-despawn). HP bars + the active-turn ring pulse are
    // world-space, camera-billboarded, and driven from surface truth. The engine stays SOLE WRITER — this
    // renders engine-decided hp/turn, never a recomputed value.
    [Header("Combat feel (#anim-combat; verb map from paint_combat_replay_v1)")]
    readonly System.Collections.Generic.Dictionary<string, int> _hpOf = new System.Collections.Generic.Dictionary<string, int>();
    readonly System.Collections.Generic.Dictionary<string, int> _hpMaxOf = new System.Collections.Generic.Dictionary<string, int>();
    readonly System.Collections.Generic.Dictionary<string, GameObject> _hpBars = new System.Collections.Generic.Dictionary<string, GameObject>();
    // DOWNED state (hp<=0 but still surface-listed — the engine keeps downed combatants in the order at
    // current_hp=0 and heals revive them, combat_loop.py; a permanent "dead" mark here made a healed ally
    // invisible forever — the #1451-review P1). _downRunning = DownCo mid-fall; _reviveWanted = a revive
    // that landed mid-fall, honored when the fall ends; _downPose = captured root pose for the stand-up.
    readonly System.Collections.Generic.HashSet<string> _downed = new System.Collections.Generic.HashSet<string>();
    readonly System.Collections.Generic.HashSet<string> _downRunning = new System.Collections.Generic.HashSet<string>();
    readonly System.Collections.Generic.HashSet<string> _reviveWanted = new System.Collections.Generic.HashSet<string>();
    class DownPose { public Vector3 scale; public Quaternion rot; }
    readonly System.Collections.Generic.Dictionary<string, DownPose> _downPose = new System.Collections.Generic.Dictionary<string, DownPose>();
    // Live walk graphs by actor id: StopCoroutine skips a stopped glide's remaining code, so the graph it
    // created can never rely on in-coroutine Destroy — every interruption path funnels through
    // KillWalkGraph instead (the #1451-review P2 leak).
    readonly System.Collections.Generic.Dictionary<string, UnityEngine.Playables.PlayableGraph> _walkGraphOf = new System.Collections.Generic.Dictionary<string, UnityEngine.Playables.PlayableGraph>();
    string _currentId = "";        // the isCurrent combatant this surface (active-turn ring-pulse anchor)
    string _pulsePrev = "";        // last-pulsed ring, reset to rest when the turn moves on

    // #Phase3 WALKABILITY OVERLAY (browser-parity with screen-combat.jsx:721-802): a toggleable per-cell
    // grid laid flat on the floor — faint gold inset on walkable cells, dark red-brown tint on
    // impassable/occupied, brighter gold hover (red on a foe cell = attack affordance). Toggled with G;
    // default ON when WORLDOS_PLAYTEST=1 (playtests), OFF otherwise (beauty captures = byte-identical
    // scene). Reads ONLY surface data (_impassable/_occupied/_foeCells); a pure consumer, no range ring.
    // Cheap + deterministic: ONE quad pool (rebuilt only when the grid extents change), colors mutated in
    // place (no per-frame allocation), hover re-tinted only when the raycast cell changes.
    [Header("Walkability overlay (#Phase3, browser-parity; G toggles)")]
    bool _overlayOn = false;
    GameObject _ovRoot;
    GameObject[] _ovQuads;
    Material[] _ovMats;
    Color[] _ovBase;                 // per-cell resting color, so a hover can restore it
    int _ovCols = 0, _ovRows = 0;
    int _ovHover = -1;               // pool index of the hovered cell, -1 = none
    Texture2D _cellTex;              // shared thin-border + faint-fill cell texture, built once
    readonly System.Collections.Generic.HashSet<int> _foeCells = new System.Collections.Generic.HashSet<int>();
    // browser-parity cell colors (mirror screen-combat.jsx:774-790's gold/red-brown affordance tints).
    static readonly Color OvWalkRest  = new Color(0.96f, 0.82f, 0.48f, 0.18f); // faint gold inset, mostly transparent
    static readonly Color OvBlockRest = new Color(0.30f, 0.12f, 0.08f, 0.55f); // dark red-brown tint (blocked/occupied)
    static readonly Color OvWalkHover = new Color(1.00f, 0.90f, 0.55f, 0.34f); // brighter gold (hover a walkable cell)
    static readonly Color OvFoeHover  = new Color(0.85f, 0.22f, 0.22f, 0.42f); // red (hover a foe cell — attack affordance)

    // #Phase4 ADVISORY VISIBILITY: the engine's advisory move notes surfaced in the player. `movement_illegal`
    // (over-budget / Speed-0 — the 5e "moved anyway" posture) shows a short fading note + amber ring pulse on
    // the mover; `move_blocked` (an engine-side reject of a non-prevalidated click) surfaces its reason text
    // the same way. Pure consumer: parsed from the /move response, engine posture unchanged.
    [Header("Advisory (#Phase4)")]
    public float AdvisoryHold = 3.2f;   // seconds the on-screen note holds before it finishes fading
    string _advMsg = "";
    float _advT = 0f;                    // fade clock; alpha = 1 - advT/AdvisoryHold
    GUIStyle _advStyle;
    int[] _lastPostCell = null;          // the cell the last /move POST targeted (for the pulse anchor)

    // #1441 named actor heights — ONE source of truth. These mirror paint_combat_v1.cs's #1418-calibrated
    // LIVE baked-scene heights (foe 4.2 / character 3.2), which is what this client repositions, so a
    // runtime-spawned actor matches its baked twin. NOTE: paint_combat_replay_v1.cs still carries a stale
    // pre-#1418 character height of 5.0 (the editor reel, out of this player-path change's scope) — flagged.
    const float ActorHeightFoe = 4.2f;
    const float ActorHeightChar = 3.2f;

    // W6.1 (#1460) RUNTIME OCCLUDER PROXIES: the runtime twin of the paint_combat_v1.cs:487-533 editor
    // bake. The engine ships `occluders` ({cells:[[c,r]...], band:"low"|"mid"|"tall"}) on /combat-surface
    // (viewer/server.py _combat_occluders) — the OCCLUDER props (columns/statues) with footprint cells +
    // height band. For each occluder cell we place an INVISIBLE depth-only box (WorldOS/OccluderDepth:
    // ColorMask 0 -> writes DEPTH not color; Queue=Geometry-1 -> renders BEFORE the actors) at the SAME
    // CellToWorld(cell) the painted column was baked at, so a 3D actor standing BEHIND the column (greater
    // camera depth) fails the depth test where they overlap and is correctly HIDDEN by it. The editor bake
    // froze this at last-save and died on any room swap; this rebuilds it every poll the occluder set (or
    // location) changes. Presentation-only, engine stays SOLE WRITER; [] occluders => today's behavior.
    // Kept out of Actor_* scans: the boxes are named Occluder_* and parented under a dedicated root.
    GameObject _occRoot;                 // container parenting every proxy box; destroyed+rebuilt on change
    Material _occMat;                    // shared WorldOS/OccluderDepth material, built once
    bool _occMatTried;                   // guards the one-time Shader.Find (a missing shader warns once)
    System.Collections.Generic.List<object> _occRaw;  // last-parsed raw occluder entries (post-unwrap)
    string _occLocId = "";               // last-parsed location id (a room swap invalidates the proxies)
    string _occSigParsed = "";           // signature of the last-PARSED occluder set + location
    string _occSigBuilt = "\0";      // signature of the last-BUILT proxies (sentinel => first build runs)

    [System.Serializable] public class Tok { public string id; public string name; public string team; public int x; public int y; public bool isCurrent; public int hp; public int hpMax; }
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

        // #Phase3: the walkability overlay defaults ON in playtests (WORLDOS_PLAYTEST=1) and OFF otherwise,
        // so beauty captures render a byte-identical scene. Built lazily on the first surface (needs grid
        // extents); a default-on overlay appears after the first /combat-surface poll.
        _overlayOn = System.Environment.GetEnvironmentVariable("WORLDOS_PLAYTEST") == "1";

        Debug.Log("[CSC] start: campaign=" + CampaignId + " url=" + ViewerUrl + " overlay=" + _overlayOn);
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
        // W6.1 (#1460): (re)build the invisible occluder proxies AFTER ApplySurf has applied this surface's
        // grid extents (CellToWorld depends on Cols/Rows). No-ops unless the occluder set/location changed.
        RebuildOccluders();
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
            // W6.1 (#1460): cache the surface's `occluders` + the current `location.id` so RebuildOccluders
            // can spawn/rebuild the depth-proxy boxes when they change. Guarded on ContainsKey (mirrors the
            // impassable branch): a response without the key leaves the prior set intact. Both /combat-surface
            // and /move (unwrapped above) carry these, so the proxies stay live on the move path too.
            if (root.ContainsKey("occluders"))
            {
                _occRaw = root["occluders"] as System.Collections.Generic.List<object>;
                _occLocId = (root.ContainsKey("location") && root["location"] is System.Collections.Generic.Dictionary<string, object> locd && locd.ContainsKey("id")) ? (locd["id"] as string ?? "") : _occLocId;
                _occSigParsed = OccSignature(_occLocId, _occRaw);
            }
            // W6.2 (#1461): the `stage` block ({mode, tokens}) tells rest from combat and names the walk
            // mover. Only re-derived when the payload actually carries `stage` (every /combat-surface poll
            // does; a walk_to_cell /move response does NOT), so a walk response never clobbers the rest
            // state the last poll established. A combat surface carries mode:"combat" -> _restMode false.
            if (root.ContainsKey("stage") && root["stage"] is System.Collections.Generic.Dictionary<string, object> stage)
            {
                _restMode = (stage.ContainsKey("mode") ? stage["mode"] as string : "") == "rest";
                _restMoverId = "";
                if (_restMode && stage.ContainsKey("tokens") && stage["tokens"] is System.Collections.Generic.List<object> stoks)
                {
                    foreach (var e in stoks)
                    {
                        var tk = e as System.Collections.Generic.Dictionary<string, object>; if (tk == null) continue;
                        string role = tk.ContainsKey("rest_role") ? tk["rest_role"] as string : "";
                        if (role != "party") continue;                        // party tokens walk; npc tokens are talk-targets
                        string id = tk.ContainsKey("id") ? tk["id"] as string : "";
                        if (!string.IsNullOrEmpty(id)) { _restMoverId = id; break; } // first party token = deterministic lead PC
                    }
                }
            }
        }
        catch (System.Exception e) { Debug.LogWarning("[CSC] surface-extras parse: " + e.Message); }
    }

    // W6.1 (#1460): a cheap order-preserving fingerprint of the occluder set + its location, so
    // RebuildOccluders is a no-op on the common poll (unchanged set) and rebuilds only on an actual change
    // (a prop added/removed, a band change, or a room swap). Malformed entries collapse to empty tokens —
    // the same guarding BuildOccluders applies — so the signature can never disagree with what is built.
    static string OccSignature(string locId, System.Collections.Generic.List<object> raw)
    {
        var sb = new System.Text.StringBuilder();
        sb.Append(locId ?? "").Append('|');
        if (raw != null)
            foreach (var oo in raw)
            {
                var od = oo as System.Collections.Generic.Dictionary<string, object>; if (od == null) continue;
                sb.Append(od.ContainsKey("band") ? od["band"] as string : "mid").Append(':');
                var cells = od.ContainsKey("cells") ? od["cells"] as System.Collections.Generic.List<object> : null;
                if (cells != null) foreach (var cc in cells) { var cell = cc as System.Collections.Generic.List<object>; if (cell == null || cell.Count < 2) continue; sb.Append(System.Convert.ToInt32(cell[0])).Append(',').Append(System.Convert.ToInt32(cell[1])).Append(' '); }
                sb.Append(';');
            }
        return sb.ToString();
    }

    // W6.1 (#1460): rebuild the invisible depth-only occluder proxies when the parsed set (or location)
    // changed since the last build. Runtime port of paint_combat_v1.cs:487-533 — same band->height map,
    // same CellToWorld-aligned 2x2 cubes, same WorldOS/OccluderDepth material (ColorMask 0, ZWrite On,
    // Queue Geometry-1) — but poll-driven and rebuildable rather than a one-time editor bake. Called from
    // ApplyJson AFTER ApplySurf so Cols/Rows reflect this surface's grid (CellToWorld depends on them).
    void RebuildOccluders()
    {
        if (_occSigParsed == _occSigBuilt) return;           // unchanged set -> no rebuild (the common poll)
        _occSigBuilt = _occSigParsed;
        // Despawn cleanly: dropping the whole container takes every Occluder_* box with it (deterministic;
        // the boxes are never in _spawned, so the actor despawn path never touches them and vice-versa).
        if (_occRoot != null) { Destroy(_occRoot); _occRoot = null; }
        if (_occRaw == null || _occRaw.Count == 0) { Debug.Log("[CSC] occluders: 0 (cleared)"); return; }

        var mat = EnsureOccluderMaterial();
        if (mat == null) return;                             // shader missing -> skip (never a visible box)
        _occRoot = new GameObject("OccluderProxies");
        System.Func<string, float> bandH = (b) => b == "tall" ? 7.5f : (b == "low" ? 1.4f : 3.8f);
        int occN = 0;
        foreach (var oo in _occRaw)
        {
            var od = oo as System.Collections.Generic.Dictionary<string, object>; if (od == null) continue;
            string band = od.ContainsKey("band") ? od["band"] as string : "mid"; float H = bandH(band);
            var ocells = od.ContainsKey("cells") ? od["cells"] as System.Collections.Generic.List<object> : null; if (ocells == null) continue;
            foreach (var cc in ocells)
            {
                var cell = cc as System.Collections.Generic.List<object>; if (cell == null || cell.Count < 2) continue;
                int ccx = System.Convert.ToInt32(cell[0]); int ccy = System.Convert.ToInt32(cell[1]);
                var wp = CellToWorld(ccx, ccy);
                var box = GameObject.CreatePrimitive(PrimitiveType.Cube);
                box.name = "Occluder_" + ccx + "_" + ccy;
                Destroy(box.GetComponent<Collider>());       // depth-only proxy, never a physics blocker
                box.transform.SetParent(_occRoot.transform, true);
                box.transform.position = new Vector3(wp.x, H * 0.5f, wp.z);
                box.transform.localScale = new Vector3(2.0f, H, 2.0f);
                var br = box.GetComponent<Renderer>();
                br.sharedMaterial = mat;
                br.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                br.receiveShadows = false;
                occN++;
            }
        }
        Debug.Log("[CSC] occluders: " + occN + " depth-proxy boxes (loc=" + _occLocId + ")");
    }

    // W6.1 (#1460): the shared depth-only material, built once from the COMMITTED WorldOS/OccluderDepth
    // shader (#1433 — a real .shader asset compiled into the player, unlike the old runtime-created string
    // that fell back to magenta). The runtime has no ShaderUtil.CreateShaderAsset (editor-only), so a
    // missing shader warns once and skips proxy spawning — invisible-but-broken occlusion beats visible
    // black boxes. WorldOS/OccluderDepth must be in Always-Included Shaders for Shader.Find to resolve.
    Material EnsureOccluderMaterial()
    {
        if (_occMat != null) return _occMat;
        if (_occMatTried) return null;
        _occMatTried = true;
        var sh = Shader.Find("WorldOS/OccluderDepth");
        if (sh == null) { Debug.LogWarning("[CSC] occluders: WorldOS/OccluderDepth not found (add to Always-Included Shaders); skipping proxies."); return null; }
        _occMat = new Material(sh);
        return _occMat;
    }

    void ApplySurf(Surf s)
    {
        if (s == null || s.tokens == null) return;
        _turnToken = s.turnToken;
        // #1318/#1433: honor the surface's own grid extents (rest-mode rooms can be non-14x11) so
        // cellToWorld stays aligned to what paint_combat_v1 baked. Absent ⇒ the 14x11 default.
        if (s.grid != null && s.grid.cols > 0 && s.grid.rows > 0) { Cols = s.grid.cols; Rows = s.grid.rows; }
        // #1441: rebuild the occupied-cell set (every token's cell) for client-side click pre-validation.
        // #Phase3: also rebuild the foe-cell set so the overlay hover reads red on an attackable cell.
        _occupied.Clear(); _foeCells.Clear();
        foreach (var t in s.tokens) if (t != null) { int k = CellKey(t.x, t.y); _occupied.Add(k); if (t.team == "foe") _foeCells.Add(k); }
        var present = new System.Collections.Generic.HashSet<string>();
        foreach (var t in s.tokens)
        {
            // #anim-combat P1 fix: a surface-listed token is ALWAYS live to the client — hp<=0 while listed
            // means DOWNED (prone on the field, revivable), never a skip. Removal from the surface is the
            // only terminal signal (the stale path below).
            if (t == null) continue;
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
            foreach (var id in stale)
            {
                // #anim-combat P1 fix: a DOWNED (prone) combatant leaving the surface is the true death —
                // shrink+sink briefly instead of blinking out. _spawned/_downed are cleared NOW so the next
                // poll can't double-fade; Despawn (at the fade's end) is idempotent on the rest. A mid-fall
                // removal (DownCo still running) despawns instantly — DownCo's null-guard unwinds it.
                if (_downed.Contains(id) && !_downRunning.Contains(id))
                {
                    _spawned.Remove(id); _downed.Remove(id);
                    StartCoroutine(FadeOutRemoveCo(id));
                }
                else Despawn(id);
            }
        }
        // #anim-combat: drive combat FEEL from the surface's engine-decided hp/turn (pure consumer). Resolve
        // the active-turn combatant, then for every combatant whose hp DROPPED since the last surface: float
        // the damage delta, flinch it, lunge its attacker (the isCurrent actor); hp<=0 plays a DOWNED
        // collapse (prone, revivable — removal from the surface is the only terminal signal). HP bars
        // are (re)created for the living; the active-turn ring pulse is anchored on _currentId (Update drives
        // the per-frame billboard + pulse).
        ApplyCombat(s);
        // #Phase3: keep the overlay in sync with the new surface — rebuild the quad pool if the grid
        // extents changed (rest rooms are non-14x11), then repaint per-cell tints for the new occupancy.
        if (_overlayOn) { EnsureOverlay(); RefreshOverlayColors(); }
    }

    // #anim-combat: the ported verb map, driven off the surface hp fields + isCurrent (engine truth only).
    void ApplyCombat(Surf s)
    {
        // active-turn combatant (attacker anchor + ring-pulse target).
        _currentId = "";
        foreach (var t in s.tokens) if (t != null && t.isCurrent && !string.IsNullOrEmpty(t.id)) { _currentId = t.id; break; }
        Transform attacker = string.IsNullOrEmpty(_currentId) ? null : FindActor(_currentId);

        foreach (var t in s.tokens)
        {
            if (t == null || string.IsNullOrEmpty(t.id) || t.hpMax <= 0) continue;   // hp only when the engine carries it
            int newHp = t.hp;
            int prevHp; bool hadPrev = _hpOf.TryGetValue(t.id, out prevHp);
            _hpMaxOf[t.id] = t.hpMax;

            if (hadPrev && newHp < prevHp && !_downed.Contains(t.id))
            {
                Transform tgt = FindActor(t.id);
                if (tgt != null)
                {
                    FloatDamage(tgt.position, "-" + (prevHp - newHp), new Color(1f, 0.95f, 0.45f, 1f));
                    if (newHp > 0) StartCoroutine(FlinchCo(tgt, attacker != null ? attacker.position : tgt.position - tgt.forward));
                    if (attacker != null && attacker != tgt) StartCoroutine(LungeCo(attacker, _currentId, tgt.position));
                }
            }
            _hpOf[t.id] = newHp;

            // hp<=0 while surface-listed = DOWNED (revivable), not dead — collapse and stay prone. hp back
            // above 0 while downed = the #1106 heal-revive: stand the actor back up (deferred to the end of
            // the fall when it lands mid-DownCo). Removal from the surface is the only terminal path.
            if (newHp <= 0 && !_downed.Contains(t.id)) StartCoroutine(DownCo(t.id, FindActor(t.id)));
            else if (newHp > 0 && _downed.Contains(t.id))
            {
                if (_downRunning.Contains(t.id)) _reviveWanted.Add(t.id);
                else RestoreDowned(t.id, FindActor(t.id));
            }
            else if (newHp > 0) EnsureHpBar(t.id, FindActor(t.id));
        }
        // prune hp/bar state for combatants no longer on the surface (moved off the board / removed).
        var goneHp = new System.Collections.Generic.List<string>();
        foreach (var id in _hpOf.Keys) { bool here = false; foreach (var t in s.tokens) if (t != null && t.id == id) { here = true; break; } if (!here) goneHp.Add(id); }
        foreach (var id in goneHp) { _hpOf.Remove(id); _hpMaxOf.Remove(id); RemoveHpBar(id); }
    }

    // ---- #Phase3 walkability overlay (browser-parity affordances; pure surface-data consumer) ----

    // Shared cell texture: a thin inset border (alpha 1) around a faint interior fill (alpha 0.7). A low-alpha
    // gold tint then reads as "faint gold inset, mostly transparent"; a higher-alpha red-brown as a filled
    // "blocked" tint — one texture serves both states, so every cell shares it and only the color differs.
    Texture2D CellTex()
    {
        if (_cellTex != null) return _cellTex;
        const int N = 64, border = 4;
        _cellTex = new Texture2D(N, N, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp };
        var px = new Color[N * N];
        for (int y = 0; y < N; y++) for (int x = 0; x < N; x++)
        {
            bool edge = x < border || x >= N - border || y < border || y >= N - border;
            px[y * N + x] = new Color(1f, 1f, 1f, edge ? 1f : 0.7f);
        }
        _cellTex.SetPixels(px); _cellTex.Apply();
        return _cellTex;
    }

    // Build the overlay only when it is on and the pool is missing or the grid extents changed. Rebuilds are
    // rare (a room swap); the common poll just recolors the existing pool via RefreshOverlayColors.
    void EnsureOverlay()
    {
        if (_ovQuads == null || _ovCols != Cols || _ovRows != Rows) BuildOverlay();
    }

    // One flat quad per cell under a single "TileOverlay" root (tidy hierarchy + one-call teardown). Quads sit
    // slightly above the floor and just BELOW the actor AO/ring (queue 1900 < 1950) so shadows draw over tiles.
    void BuildOverlay()
    {
        DestroyOverlay();
        _ovCols = Cols; _ovRows = Rows;
        int n = Mathf.Max(0, _ovCols * _ovRows);
        _ovRoot = new GameObject("TileOverlay");
        _ovQuads = new GameObject[n]; _ovMats = new Material[n]; _ovBase = new Color[n];
        var tex = CellTex();
        for (int r = 0; r < _ovRows; r++) for (int c = 0; c < _ovCols; c++)
        {
            int idx = r * _ovCols + c;
            Vector3 p = CellToWorld(c, r);
            var q = GameObject.CreatePrimitive(PrimitiveType.Quad); q.name = "Tile_" + c + "_" + r;
            Object.DestroyImmediate(q.GetComponent<Collider>());
            q.transform.SetParent(_ovRoot.transform, false);
            q.transform.position = new Vector3(p.x, FloorY + 0.02f, p.z);
            q.transform.localEulerAngles = new Vector3(90f, 0f, 0f);
            q.transform.localScale = new Vector3(CellSize * 0.96f, CellSize * 0.96f, 1f);   // slight gutter -> grid read
            // Sprites/Default (NOT Unlit/Transparent — the latter has no _Color, so a per-cell tint is
            // ignored): it exposes _Color and alpha-blends. Transparent queue (>2000) so the opaque floor +
            // actors draw first — tiles then blend over the floor and are depth-occluded by the actors above.
            var m = new Material(Shader.Find("Sprites/Default")); m.mainTexture = tex; m.renderQueue = 2500;
            var rend = q.GetComponent<Renderer>(); rend.sharedMaterial = m; rend.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            _ovQuads[idx] = q; _ovMats[idx] = m;
        }
        _ovHover = -1;
    }

    void DestroyOverlay()
    {
        if (_ovRoot != null) Object.Destroy(_ovRoot);
        _ovRoot = null; _ovQuads = null; _ovMats = null; _ovBase = null; _ovCols = 0; _ovRows = 0; _ovHover = -1;
    }

    // Repaint every cell's resting tint from the current surface: dark red-brown when impassable OR occupied,
    // faint gold otherwise. Preserves the active hover cell's highlight. No allocation (mutates Material.color).
    void RefreshOverlayColors()
    {
        if (_ovMats == null) return;
        for (int r = 0; r < _ovRows; r++) for (int c = 0; c < _ovCols; c++)
        {
            int idx = r * _ovCols + c;
            if (idx >= _ovMats.Length || _ovMats[idx] == null) continue;
            int key = CellKey(c, r);
            Color baseCol = (_impassable.Contains(key) || _occupied.Contains(key)) ? OvBlockRest : OvWalkRest;
            _ovBase[idx] = baseCol;
            _ovMats[idx].color = (idx == _ovHover) ? HoverColor(c, r) : baseCol;
        }
    }

    // Hover tint: red on a foe cell (attack affordance), brighter gold elsewhere — mirrors the browser.
    Color HoverColor(int c, int r) { return _foeCells.Contains(CellKey(c, r)) ? OvFoeHover : OvWalkHover; }

    // Toggle (G): first turn-on builds the pool lazily and repaints; turn-off just hides the root (kept for a
    // cheap re-show). OFF == zero rendered quads == byte-identical scene.
    void ToggleOverlay()
    {
        _overlayOn = !_overlayOn;
        if (_overlayOn) { EnsureOverlay(); RefreshOverlayColors(); if (_ovRoot != null) _ovRoot.SetActive(true); }
        else if (_ovRoot != null) _ovRoot.SetActive(false);
    }

    // Re-tint on hover from the SAME floor raycast the click uses. Only mutates on a cell change (cheap).
    void UpdateOverlayHover()
    {
        if (_ovQuads == null) return;
        int hover = -1;
        var cam = Camera.main;
        if (cam != null)
        {
            Ray ray = cam.ScreenPointToRay(Input.mousePosition);
            if (Mathf.Abs(ray.direction.y) > 1e-4f)
            {
                float tt = (FloorY - ray.origin.y) / ray.direction.y;
                if (tt >= 0 && WorldToCell(ray.origin + ray.direction * tt, out int c, out int r)) hover = r * _ovCols + c;
            }
        }
        if (hover == _ovHover) return;
        if (_ovHover >= 0 && _ovHover < _ovMats.Length && _ovMats[_ovHover] != null) _ovMats[_ovHover].color = _ovBase[_ovHover];
        _ovHover = hover;
        if (_ovHover >= 0 && _ovHover < _ovMats.Length && _ovMats[_ovHover] != null)
            _ovMats[_ovHover].color = HoverColor(_ovHover % _ovCols, _ovHover / _ovCols);
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
                if (clip.name.ToLower().Contains("idle")) { SampleClipRuntime(go, clip, 0f); posedByClip = true; break; }
                if (!posedByClip) { SampleClipRuntime(go, clip, 0f); posedByClip = true; }
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
        // #anim-combat: remember the moveset fbx (walk/attack clips may live there, not in the model) and the
        // head-top offset for the HP bar (height is the scale target; +margin clears the silhouette).
        _animOf[id] = (aref != null && aref.Length > 2) ? aref[2] : "";
        _topOf[id] = height + 1.4f;
        _cellOf[id] = new[] { cx, cy };
        Debug.Log("[CSC] spawned " + nm + " model=" + fbx + " x" + sc.ToString("F2") + " @cell(" + cx + "," + cy + ") rends=" + rends.Length);
        return go.transform;
    }

    void MakeGroundQuad(string name, Vector3 p, float yOff, float scale, Texture2D tex, Color col, int queue)
    {
        var old = GameObject.Find(name); if (old != null) Object.DestroyImmediate(old);
        var q = GameObject.CreatePrimitive(PrimitiveType.Quad); q.name = name; Object.DestroyImmediate(q.GetComponent<Collider>());
        q.transform.position = new Vector3(p.x, FloorY + yOff, p.z); q.transform.localEulerAngles = new Vector3(90f, 0f, 0f); q.transform.localScale = new Vector3(scale, scale, 1f);
        // #anim-combat TINT FIX: Sprites/Default (NOT Unlit/Transparent — which has no _Color, so the ring's
        // foe-red / ally-cyan tint was silently dropped and every ring rendered white). Sprites/Default
        // exposes _Color and alpha-blends, so the tint now actually renders (the AO blob keeps its white tint
        // over the dark blob texture, unchanged). Same shader the tile overlay + advisory pulse already use.
        var m = new Material(Shader.Find("Sprites/Default")); m.mainTexture = tex; m.color = col; m.renderQueue = queue;
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
        KillWalkGraph(id);   // the stopped glide can't destroy its own graph (#1451-review P2)
        _glide.Remove(id); _cellOf.Remove(id); _fbxOf.Remove(id);
        _animOf.Remove(id); _topOf.Remove(id); RemoveHpBar(id);   // #anim-combat: clear combat/anim state
        _downed.Remove(id); _downRunning.Remove(id); _reviveWanted.Remove(id); _downPose.Remove(id);
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
        // a DOWNED (prone) combatant never walks — snap it if the engine somehow relocates it.
        if (_downed.Contains(id)) { GroundSnap(a, cx, cy); return; }
        if (_glide.TryGetValue(id, out var running) && running != null) StopCoroutine(running);
        KillWalkGraph(id);   // the stopped glide can't destroy its own graph (#1451-review P2)
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

        // resolve a walk animation. ROOT CAUSE of the pre-#anim-combat "actors SLIDE" report: the walk clip
        // WAS found (goblin.fbx carries a humanoid Walk), but it was driven with AnimationClip.SampleAnimation
        // — which CANNOT retarget a Mecanim (humanoid/generic) clip in a BUILT PLAYER (it silently no-ops for
        // non-legacy clips; it only appears to work in-editor). The canonical runtime path is a PlayableGraph
        // (AnimationClipPlayable -> AnimationPlayableOutput -> Animator), which retargets humanoid AND plays
        // generic clips correctly in builds. So: pick the walk clip (own model/moveset, else a humanoid donor
        // for a clipless humanoid rig) and drive it through the graph whenever the actor has an Animator+avatar;
        // SampleAnimation stays only as the legacy/no-Animator fallback.
        AnimationClip walkClip = FindOwnClip(id, "walk", "run");
        var walkAnim = go.GetComponentInChildren<Animator>();
        if (walkClip == null && walkAnim != null && walkAnim.avatar != null && walkAnim.avatar.isHuman) walkClip = DonorWalk();
        UnityEngine.Playables.PlayableGraph walkGraph = default; bool haveGraph = false; bool sampleWalk = false;
        if (walkClip != null)
        {
            if (walkAnim != null && walkAnim.avatar != null) { walkGraph = MakeClipGraph(walkAnim, walkClip, "Walk_" + a.name); haveGraph = true; _walkGraphOf[id] = walkGraph; }
            else sampleWalk = true;   // no Animator/avatar -> legacy rig -> direct curve sample is the only path
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
            if (haveGraph) walkGraph.Evaluate(Time.deltaTime);
            else if (sampleWalk) { float len = walkClip.length > 0.01f ? walkClip.length : 1f; walkClip.SampleAnimation(go, animT % len); }
            MoveActorAndShadows(a, p);
            yield return null;
        }
        // arrive: snap exact, tear down walk, return to a grounded idle facing the camera.
        MoveActorAndShadows(a, endPos);
        if (haveGraph) KillWalkGraph(id);   // registry-tracked destroy (#1451-review P2)
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
                if (clip.name.ToLower().Contains("idle")) { SampleClipRuntime(go, clip, 0f); posed = true; break; }
                if (!posed) { SampleClipRuntime(go, clip, 0f); posed = true; }
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

    // The actor's OWN embedded clip whose name contains any of `names` (walk/run, attack, ...), from the
    // bundle — searching BOTH the MODEL fbx (_fbxOf, e.g. goblin.fbx carries Idle/Walk/Attack) AND the
    // moveset fbx (_animOf, the registry anim_ref, for rigs whose clips live in a separate fbx). Null for
    // baked actors (no _fbxOf) or when no such clip exists -> donor / no-clip fallback.
    AnimationClip FindOwnClip(string id, params string[] names)
    {
        var b = Bundle(); if (b == null) return null;
        string fbx, animRef;
        _fbxOf.TryGetValue(id, out fbx);
        _animOf.TryGetValue(id, out animRef);
        foreach (var src in new[] { fbx, animRef })
        {
            if (string.IsNullOrEmpty(src)) continue;
            foreach (var clip in b.LoadAssetWithSubAssets<AnimationClip>(src))
            {
                if (clip == null || clip.name.StartsWith("__")) continue;
                string ln = clip.name.ToLower();
                foreach (var n in names) if (ln.Contains(n)) return clip;
            }
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
        // #Phase4: advance the advisory fade clock regardless of poll/POST state.
        if (!string.IsNullOrEmpty(_advMsg)) _advT += Time.deltaTime;
        // #Phase3: overlay toggle (G) + hover run independent of the click gate below.
        if (Input.GetKeyDown(KeyCode.G)) ToggleOverlay();
        if (_overlayOn) UpdateOverlayHover();
        // #anim-combat: world-space HP bars follow + billboard their actor; the active-turn combatant's ring
        // pulses. Both run every frame regardless of the click/poll gate.
        UpdateHpBars();
        UpdateTurnPulse();
        if (_busy) return;
        if (Input.GetMouseButtonDown(0)) HandleClick();
    }

    // #Phase4: a short, fading amber note near the top of the screen. IMGUI (no Canvas needed); alpha fades
    // over AdvisoryHold. A drop shadow keeps it legible over the painterly board.
    void OnGUI()
    {
        if (string.IsNullOrEmpty(_advMsg)) return;
        float a = 1f - Mathf.Clamp01(_advT / AdvisoryHold);
        if (a <= 0f) { _advMsg = ""; return; }
        if (_advStyle == null)
            _advStyle = new GUIStyle(GUI.skin.label) { fontSize = 20, fontStyle = FontStyle.Bold, alignment = TextAnchor.MiddleCenter, wordWrap = true };
        float w = Mathf.Min(720f, Screen.width - 40f), h = 64f;
        var rect = new Rect((Screen.width - w) / 2f, Screen.height * 0.14f, w, h);
        var prev = GUI.color;
        GUI.color = new Color(0f, 0f, 0f, a * 0.6f);
        GUI.Label(new Rect(rect.x + 2f, rect.y + 2f, rect.width, rect.height), _advMsg, _advStyle);
        GUI.color = new Color(1f, 0.80f, 0.35f, a);
        GUI.Label(rect, _advMsg, _advStyle);
        GUI.color = prev;
    }

    void ShowAdvisory(string msg) { _advMsg = msg; _advT = 0f; }

    // #Phase4: parse the engine's advisory notes from a raw /move response and surface them. `move_blocked`
    // (a reject the engine evaluated) shows its own reason text; `movement_illegal` (over-budget / Speed-0,
    // "moved anyway") shows a short canned note. Searched at ANY nesting because the combat arbiter wraps the
    // move view. Absent -> silent (today's behavior). Then an amber ring pulse marks the mover's cell.
    void HandleAdvisory(string rawJson)
    {
        object root;
        try { root = Json.Parse(rawJson); } catch { return; }
        var mb = FindDict(root, "move_blocked");
        var mi = FindDict(root, "movement_illegal");
        string msg = null;
        if (mb != null) msg = (mb.ContainsKey("reason") ? mb["reason"] as string : null) ?? "move blocked";
        else if (mi != null) msg = mi.ContainsKey("conditions") ? "can't move (Speed 0) — moved anyway" : "over movement budget — moved anyway";
        if (string.IsNullOrEmpty(msg)) return;
        ShowAdvisory(msg);
        if (_lastPostCell != null) StartCoroutine(AmberPulse(_lastPostCell[0], _lastPostCell[1]));
    }

    // Depth-first search of the parsed JSON tree for the first dict-valued entry under `key`.
    static System.Collections.Generic.Dictionary<string, object> FindDict(object node, string key)
    {
        if (node is System.Collections.Generic.Dictionary<string, object> d)
        {
            if (d.TryGetValue(key, out var v) && v is System.Collections.Generic.Dictionary<string, object> vd) return vd;
            foreach (var kv in d) { var f = FindDict(kv.Value, key); if (f != null) return f; }
        }
        else if (node is System.Collections.Generic.List<object> l)
        {
            foreach (var e in l) { var f = FindDict(e, key); if (f != null) return f; }
        }
        return null;
    }

    // #Phase4: a brief expanding amber ring pulse on the mover's cell — reads "the DM disposed of this move"
    // rather than a silent teleport. Cosmetic, self-destructs; mirrors FlashReject's throwaway-quad idiom.
    IEnumerator AmberPulse(int c, int r)
    {
        Vector3 p = CellToWorld(c, r);
        var q = GameObject.CreatePrimitive(PrimitiveType.Quad); q.name = "AdvisoryPulse"; Object.DestroyImmediate(q.GetComponent<Collider>());
        q.transform.localEulerAngles = new Vector3(90f, 0f, 0f);
        // Sprites/Default so the amber _Color tint actually applies (Unlit/Transparent ignores it); above
        // the tile overlay (queue 2600 > 2500) so the pulse reads on top.
        var m = new Material(Shader.Find("Sprites/Default")); m.mainTexture = RingTex(); m.renderQueue = 2600;
        var rend = q.GetComponent<Renderer>(); rend.sharedMaterial = m; rend.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        float dur = 0.9f, t = 0f;
        while (t < dur)
        {
            t += Time.deltaTime; float u = t / dur; float scale = Mathf.Lerp(2.2f, 3.4f, u);
            q.transform.position = new Vector3(p.x, FloorY + 0.07f, p.z);
            q.transform.localScale = new Vector3(scale, scale, 1f);
            m.color = new Color(1f, 0.72f, 0.20f, Mathf.Clamp01(1f - u));
            yield return null;
        }
        Object.Destroy(q);
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
        int key = CellKey(c, r);
        // W6.2 (#1461) REST MODE: no combat signals -> the click WALKS a party member to the cell via the
        // engine's `walk_to` verb (the walk_to_cell /move intent), NOT the combat move. Same #1441
        // pre-validation as combat — a blocked/occupied cell flashes a red ring instead of a doomed POST
        // (rest_blocked_cells folds standers into `impassable`, so the _impassable check rejects a cell a
        // person stands on too). No known party mover (no rest party token yet) -> reject rather than POST
        // a moverless walk the engine would 400. This whole branch is inert on a combat surface (_restMode
        // false), so the combat attack/move path below is byte-identical.
        if (_restMode)
        {
            if (string.IsNullOrEmpty(_restMoverId) || _impassable.Contains(key) || _occupied.Contains(key)) { StartCoroutine(FlashReject(c, r)); return; }
            StartCoroutine(PostWalk(c, r));
            return;
        }
        // on-turn attack when the clicked cell holds the foe (allowed even though the foe occupies it).
        if (c == _foeX && r == _foeY && _foeId.Length > 0) { StartCoroutine(PostAttack()); return; }
        // #1441 click pre-validation (UX pre-filter ONLY; the engine stays authoritative and independently
        // rejects illegal moves): a click on an impassable (wall/prop) or token-occupied cell flashes a red
        // ring instead of firing a doomed POST.
        if (_impassable.Contains(key) || _occupied.Contains(key)) { StartCoroutine(FlashReject(c, r)); return; }
        StartCoroutine(PostMove(c, r));
    }

    // Brief red ring flash at a rejected cell — immediate "you can't go there" with no server round-trip.
    IEnumerator FlashReject(int c, int r)
    {
        Vector3 p = CellToWorld(c, r);
        var q = GameObject.CreatePrimitive(PrimitiveType.Quad); q.name = "RejectFlash"; Object.DestroyImmediate(q.GetComponent<Collider>());
        q.transform.position = new Vector3(p.x, FloorY + 0.07f, p.z); q.transform.localEulerAngles = new Vector3(90f, 0f, 0f); q.transform.localScale = new Vector3(2.6f, 2.6f, 1f);
        // #anim-combat TINT FIX: Sprites/Default so the animated red reject tint below actually applies
        // (Unlit/Transparent ignores _Color — the flash rendered white).
        var m = new Material(Shader.Find("Sprites/Default")); m.mainTexture = RingTex(); m.renderQueue = 1960;
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
        _lastPostCell = new[] { x, y };   // #Phase4: anchor the advisory pulse on the move's target cell
        yield return Post("{\"kind\":\"move_to_cell\",\"x\":" + x + ",\"y\":" + y + ",\"turn_token\":\"" + _turnToken + "\",\"campaign\":\"" + CampaignId + "\"}");
        _busy = false;
    }
    IEnumerator PostAttack()
    {
        _busy = true;
        yield return Post("{\"kind\":\"attack\",\"target_id\":\"" + _foeId + "\",\"turn_token\":\"" + _turnToken + "\",\"campaign\":\"" + CampaignId + "\"}");
        _busy = false;
    }

    // W6.2 (#1461) REST-MODE walk: POST the `walk_to_cell` intent (the rest-mode twin of move_to_cell)
    // so the engine's `walk_to` paths around walls/props/standers, writes Character.stage_cell, and
    // returns the CONFIRMED route. The engine stays the SOLE WRITER — the client never predicts a path;
    // it re-fetches the surface so the board reflects the engine-written stage_cell (same discipline as
    // the combat re-render). A refusal (unreachable / off-grid the pre-filter missed) toasts its reason.
    IEnumerator PostWalk(int x, int y)
    {
        _busy = true;
        _lastPostCell = new[] { x, y };   // #Phase4: anchor any advisory pulse on the walk's target cell
        string body = "{\"kind\":\"walk_to_cell\",\"character_id\":\"" + _restMoverId + "\",\"x\":" + x + ",\"y\":" + y + ",\"campaign\":\"" + CampaignId + "\"}";
        using (var req = new UnityWebRequest(ViewerUrl + "/move", "POST"))
        {
            req.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(body));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout = 8;
            yield return req.SendWebRequest();
            if (!Ok(req)) { Debug.LogWarning("[CSC] /walk failed: " + req.error + " body=" + req.downloadHandler.text); }
            else
            {
                // walk_to_cell returns {ok, walked, character_id, from, to, path} (NOT the combat surface).
                // A rejected walk ({ok:false, reason}) toasts its reason; a success is reflected by the
                // re-fetch below (the engine wrote stage_cell; we never render a predicted route).
                MoveResp resp = null;
                try { resp = JsonUtility.FromJson<MoveResp>(req.downloadHandler.text); }
                catch (System.Exception e) { Debug.LogWarning("[CSC] walk parse: " + e.Message); }
                if (resp != null && !resp.ok && !string.IsNullOrEmpty(resp.reason)) ShowAdvisory(resp.reason);
            }
        }
        _busy = false;
        yield return Fetch();   // re-render off the engine's fresh surface (stage_cell now updated)
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
            // #Phase4: surface any advisory note (movement_illegal / move_blocked) on BOTH accepted and
            // rejected responses — a short fading toast + amber pulse so a long/blocked move reads clearly.
            HandleAdvisory(req.downloadHandler.text);
        }
    }

    // ---- #anim-combat runtime animation helpers ---------------------------------------------------

    // A transient PlayableGraph that drives one clip through an Animator. This is the RUNTIME-CORRECT way
    // to play a Mecanim (humanoid/generic) clip in a BUILT player — Evaluate(dt) advances + applies the
    // pose (humanoid clips retarget through the avatar). Caller Evaluates per frame and Destroys at the end.
    UnityEngine.Playables.PlayableGraph MakeClipGraph(Animator anim, AnimationClip clip, string tag)
    {
        var g = UnityEngine.Playables.PlayableGraph.Create(tag);
        var cp = UnityEngine.Animations.AnimationClipPlayable.Create(g, clip);
        var op = UnityEngine.Animations.AnimationPlayableOutput.Create(g, "Out", anim);
        UnityEngine.Playables.PlayableOutputExtensions.SetSourcePlayable(op, cp);
        return g;
    }

    // Pose a GameObject to one clip at `time`. Prefers a one-shot PlayableGraph Evaluate through the
    // Animator (the only path that poses a Mecanim clip in a BUILT player — AnimationClip.SampleAnimation
    // silently no-ops for non-legacy clips in a standalone build, which is the walk/idle "freeze" bug).
    // Falls back to SampleAnimation only for a rig with no Animator/avatar (legacy curves write directly).
    void SampleClipRuntime(GameObject go, AnimationClip clip, float time)
    {
        if (go == null || clip == null) return;
        var anim = go.GetComponentInChildren<Animator>();
        if (anim != null && anim.avatar != null)
        {
            var g = UnityEngine.Playables.PlayableGraph.Create("Pose_" + go.name);
            var cp = UnityEngine.Animations.AnimationClipPlayable.Create(g, clip);
            UnityEngine.Playables.PlayableExtensions.SetTime(cp, time);
            var op = UnityEngine.Animations.AnimationPlayableOutput.Create(g, "Out", anim);
            UnityEngine.Playables.PlayableOutputExtensions.SetSourcePlayable(op, cp);
            g.Evaluate(0f); g.Destroy();
        }
        else clip.SampleAnimation(go, time);
    }

    // ---- #anim-combat combat-feel helpers (verb map ported from paint_combat_replay_v1.cs) ---------

    // A world-space damage/heal number that rises + fades over the struck actor (camera-facing).
    void FloatDamage(Vector3 atFeet, string text, Color col)
    {
        var g = new GameObject("DmgNum");
        var tm = g.AddComponent<TextMesh>();
        tm.text = text; tm.fontSize = 90; tm.characterSize = 0.22f; tm.anchor = TextAnchor.MiddleCenter; tm.alignment = TextAlignment.Center; tm.color = col;
        // Unity 6 dropped the builtin Arial; bind the LegacyRuntime font (else the TextMesh renders nothing).
        var font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        if (font == null) font = Resources.GetBuiltinResource<Font>("Arial.ttf");
        var mr = g.GetComponent<MeshRenderer>();
        if (font != null) { tm.font = font; if (mr != null) { mr.sharedMaterial = new Material(font.material); mr.sharedMaterial.renderQueue = 3100; } }
        else if (mr != null && mr.sharedMaterial != null) mr.sharedMaterial.renderQueue = 3100;
        StartCoroutine(FloatNumCo(g, atFeet + new Vector3(0f, 3.7f, 0f), col));
    }
    IEnumerator FloatNumCo(GameObject g, Vector3 start, Color col)
    {
        float t = 0f, dur = 1.1f; var tm = g != null ? g.GetComponent<TextMesh>() : null;
        while (t < dur && g != null)
        {
            t += Time.deltaTime; float u = t / dur;
            g.transform.position = start + new Vector3(0f, u * 1.6f, 0f);
            var cam = Camera.main; if (cam != null) g.transform.rotation = cam.transform.rotation;
            if (tm != null) tm.color = new Color(col.r, col.g, col.b, Mathf.Clamp01(1f - u));
            yield return null;
        }
        if (g != null) Object.Destroy(g);
    }

    // Knockback flinch: a short out-and-back nudge AWAY from the attacker (transform motion; reads as a hit
    // recoil on any rig, matching the replay's knockBack — no clip needed). Rings/AO track via MoveActorAndShadows.
    IEnumerator FlinchCo(Transform a, Vector3 fromPos)
    {
        if (a == null) yield break;
        Vector3 home = a.position;
        Vector3 dir = a.position - fromPos; dir.y = 0f;
        if (dir.sqrMagnitude < 1e-4f) dir = -a.forward;
        dir = dir.normalized;
        float dur = 0.28f, t = 0f;
        while (t < dur && a != null)
        {
            t += Time.deltaTime; float u = t / dur;
            float k = u < 0.4f ? (u / 0.4f) : (1f - (u - 0.4f) / 0.6f);   // peak out at u=0.4, ease back
            MoveActorAndShadows(a, home + dir * (0.5f * k));
            yield return null;
        }
        if (a != null) MoveActorAndShadows(a, home);
    }

    // Attack lunge: face the target, lunge forward + back (out-and-back), and — when the actor HAS an attack
    // clip — play it through the graph on top (goblin.fbx carries Attack; a clipless rig just lunges, the
    // "or lunge fallback" the packet asks for). Returns to a grounded idle facing the camera.
    IEnumerator LungeCo(Transform a, string id, Vector3 towardPos)
    {
        if (a == null) yield break;
        var go = a.gameObject;
        Vector3 home = a.position;
        Vector3 dir = towardPos - a.position; dir.y = 0f;
        if (dir.sqrMagnitude < 1e-4f) yield break;
        dir = dir.normalized;
        float pitchX = go.GetComponentInChildren<SkinnedMeshRenderer>() != null ? 0f : -90f;
        a.rotation = Quaternion.Euler(pitchX, Mathf.Atan2(dir.x, dir.z) * Mathf.Rad2Deg, 0f);
        AnimationClip atk = FindOwnClip(id, "attack", "swing");
        var anim = go.GetComponentInChildren<Animator>();
        UnityEngine.Playables.PlayableGraph g = default; bool hg = false;
        if (atk != null && anim != null && anim.avatar != null) { g = MakeClipGraph(anim, atk, "Atk_" + a.name); hg = true; }
        float dur = 0.42f, t = 0f;
        while (t < dur && a != null)
        {
            t += Time.deltaTime; float u = t / dur;
            float k = u < 0.45f ? (u / 0.45f) : (1f - (u - 0.45f) / 0.55f);
            MoveActorAndShadows(a, home + dir * (0.9f * k));
            if (hg) g.Evaluate(Time.deltaTime);
            yield return null;
        }
        if (a != null) MoveActorAndShadows(a, home);
        if (hg && g.IsValid()) g.Destroy();
        if (a != null)
        {
            PoseIdle(go);
            var cam = Camera.main; float camYaw = cam != null ? cam.transform.eulerAngles.y : 45f;
            a.rotation = Quaternion.Euler(pitchX, camYaw + 180f, 0f);
            MoveActorAndShadows(a, home);
        }
    }

    // Downed — hp<=0 while the token is STILL on the surface. In this engine that means DOWNED, not dead:
    // combat_loop keeps the combatant in the order at current_hp=0 and heals revive it (#1106), so the old
    // terminal despawn + permanent _dead mark made a healed ally invisible forever (#1451-review P1). Now:
    // collapse (own death clip when present, else a topple) + dim ring/AO to 0.35, and REMAIN prone on the
    // field. True removal is the surface-absence fade in ApplySurf; revive is RestoreDowned. A revive that
    // lands mid-fall is honored when the fall ends (never StopCoroutine this — the clip graph must be
    // destroyed HERE, on every exit path).
    IEnumerator DownCo(string id, Transform a)
    {
        _downed.Add(id); _downRunning.Add(id);
        RemoveHpBar(id);
        if (a == null) { _downRunning.Remove(id); yield break; }
        // an in-flight glide would fight the fall for the transform — kill it (and its graph) first.
        if (_glide.TryGetValue(id, out var gco) && gco != null) StopCoroutine(gco);
        _glide.Remove(id); KillWalkGraph(id);
        var go = a.gameObject;
        Vector3 home = a.position; Quaternion startRot = a.rotation;
        _downPose[id] = new DownPose { scale = a.localScale, rot = startRot };
        AnimationClip death = FindOwnClip(id, "death", "dead", "die");
        var anim = go.GetComponentInChildren<Animator>();
        UnityEngine.Playables.PlayableGraph g = default; bool hg = false;
        if (death != null && anim != null && anim.avatar != null) { g = MakeClipGraph(anim, death, "Down_" + a.name); hg = true; }
        float dur = 0.85f, t = 0f;
        while (t < dur && a != null && !_reviveWanted.Contains(id))
        {
            t += Time.deltaTime; float u = t / dur;
            if (hg) g.Evaluate(Time.deltaTime);
            else a.rotation = startRot * Quaternion.Euler(0f, 0f, Mathf.Lerp(0f, 85f, u));   // topple when no clip
            MoveActorAndShadows(a, home + new Vector3(0f, -0.25f * u, 0f));
            float dim = Mathf.Lerp(1f, 0.35f, u);
            FadeSibling(a.name, "_Ring", dim); FadeSibling(a.name, "_AO", dim);
            yield return null;
        }
        if (hg && g.IsValid()) g.Destroy();
        _downRunning.Remove(id);
        if (a != null && _reviveWanted.Contains(id)) { _reviveWanted.Remove(id); RestoreDowned(id, a); }
        // else: stays prone + dimmed until healed (RestoreDowned) or removed from the surface (fade-despawn).
    }

    // Revive (hp back above 0 while still surface-listed — the #1106 heal): stand the actor back up. Root
    // scale/rotation from the captured down-pose, bones re-posed to idle, then a fresh ground-snap on its
    // engine cell (posed bounds differ from prone bounds); ring/AO restored, HP bar re-created.
    void RestoreDowned(string id, Transform a)
    {
        DownPose p; _downPose.TryGetValue(id, out p);
        _downed.Remove(id); _reviveWanted.Remove(id); _downPose.Remove(id);
        if (a == null) return;
        if (p != null) { a.localScale = p.scale; a.rotation = p.rot; }
        PoseIdle(a.gameObject);
        int[] cell; if (_cellOf.TryGetValue(id, out cell)) GroundSnap(a, cell[0], cell[1]);
        FadeSibling(a.name, "_Ring", 1f); FadeSibling(a.name, "_AO", 1f);
        EnsureHpBar(id, a);
        Debug.Log("[CSC] revived Actor_" + id);
    }

    // A downed combatant the engine no longer lists (the true death/removal): brief shrink+sink from the
    // prone pose, then the full Despawn. Caller already cleared _spawned/_downed (no double-fade).
    IEnumerator FadeOutRemoveCo(string id)
    {
        var a = FindActor(id);
        if (a == null) { Despawn(id); yield break; }
        Vector3 s0 = a.localScale; Vector3 p0 = a.position;
        float dur = 0.45f, t = 0f;
        while (t < dur && a != null)
        {
            t += Time.deltaTime; float u = t / dur;
            a.localScale = Vector3.Lerp(s0, s0 * 0.05f, u);
            MoveActorAndShadows(a, p0 + new Vector3(0f, -0.6f * u, 0f));
            float dim = Mathf.Lerp(0.35f, 0f, u);
            FadeSibling(a.name, "_Ring", dim); FadeSibling(a.name, "_AO", dim);
            yield return null;
        }
        Despawn(id);   // removes Actor_<id> + _AO + _Ring, clears per-actor state
    }

    // Deterministically destroy an actor's live walk graph. GlideTo registers its graph here because a
    // StopCoroutine'd glide never reaches its own Destroy — natural arrival, re-glide, despawn and downing
    // all funnel through this instead (#1451-review P2 leak).
    void KillWalkGraph(string id)
    {
        UnityEngine.Playables.PlayableGraph g;
        if (_walkGraphOf.TryGetValue(id, out g)) { if (g.IsValid()) g.Destroy(); _walkGraphOf.Remove(id); }
    }

    // Fade a named ground sibling's material alpha (ring/AO death fade).
    void FadeSibling(string actorName, string suffix, float alpha)
    {
        var s = GameObject.Find(actorName + suffix);
        if (s == null) return;
        var r = s.GetComponent<Renderer>(); if (r == null || r.sharedMaterial == null) return;
        var c = r.sharedMaterial.color; c.a = Mathf.Clamp01(alpha); r.sharedMaterial.color = c;
    }

    // ---- #anim-combat + #1442 world-space HP bars (fed from surface hp; pure consumer) --------------

    // Create the HP bar root (bg + fg quads) for an actor once; UpdateHpBars drives its position/width/billboard.
    void EnsureHpBar(string id, Transform actor)
    {
        if (actor == null) return;
        GameObject root;
        if (_hpBars.TryGetValue(id, out root) && root != null) return;
        root = new GameObject("Actor_" + id + "_HP");
        MakeBarQuad(root, "_bg", new Color(0.08f, 0.03f, 0.03f, 1f), 3080);   // child 0
        MakeBarQuad(root, "_fg", new Color(0.85f, 0.15f, 0.12f, 1f), 3090);   // child 1
        _hpBars[id] = root;
    }
    void MakeBarQuad(GameObject root, string suffix, Color col, int queue)
    {
        var q = GameObject.CreatePrimitive(PrimitiveType.Quad); q.name = root.name + suffix; Object.DestroyImmediate(q.GetComponent<Collider>());
        q.transform.SetParent(root.transform, false); q.transform.localScale = new Vector3(3.2f, 0.35f, 1f);
        var m = new Material(Shader.Find("Unlit/Color")); m.color = col; m.renderQueue = queue;   // Unlit/Color exposes _Color (solid tinted bar)
        var r = q.GetComponent<Renderer>(); r.sharedMaterial = m; r.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
    }
    void RemoveHpBar(string id)
    {
        if (_hpBars.TryGetValue(id, out var root)) { if (root != null) Object.Destroy(root); _hpBars.Remove(id); }
    }

    // Each frame: ride the bar above its actor's head, billboard it to the camera, and set the fill width
    // from the tracked hp fraction (engine truth). Prune bars whose actor is gone or dead.
    void UpdateHpBars()
    {
        if (_hpBars.Count == 0) return;
        var cam = Camera.main;
        System.Collections.Generic.List<string> gone = null;
        foreach (var kv in _hpBars)
        {
            var root = kv.Value; if (root == null) continue;
            var actor = FindActor(kv.Key);
            if (actor == null || _downed.Contains(kv.Key)) { if (gone == null) gone = new System.Collections.Generic.List<string>(); gone.Add(kv.Key); continue; }
            float top; if (!_topOf.TryGetValue(kv.Key, out top)) top = 5.0f;
            root.transform.position = actor.position + new Vector3(0f, top, 0f);
            if (cam != null) root.transform.rotation = cam.transform.rotation;
            int hp, mx; float frac = (_hpMaxOf.TryGetValue(kv.Key, out mx) && mx > 0 && _hpOf.TryGetValue(kv.Key, out hp)) ? Mathf.Clamp01((float)hp / mx) : 1f;
            const float full = 3.2f;
            if (root.transform.childCount >= 2)
            {
                var fg = root.transform.GetChild(1);
                fg.localScale = new Vector3(full * frac, 0.35f, 1f);
                fg.localPosition = new Vector3(-full * (1f - frac) / 2f, 0f, 0f);
            }
        }
        if (gone != null) foreach (var id in gone) RemoveHpBar(id);
    }

    // Active-turn ring pulse: the isCurrent combatant's selection ring breathes (alpha + scale); the prior
    // pulsed ring is reset to rest when the turn moves on.
    void UpdateTurnPulse()
    {
        if (_pulsePrev != _currentId && !string.IsNullOrEmpty(_pulsePrev))
        {
            var prev = GameObject.Find("Actor_" + _pulsePrev + "_Ring");
            if (prev != null) { var pr = prev.GetComponent<Renderer>(); if (pr != null && pr.sharedMaterial != null) { var c = pr.sharedMaterial.color; c.a = 1f; pr.sharedMaterial.color = c; } prev.transform.localScale = new Vector3(2.6f, 2.6f, 1f); }
            _pulsePrev = _currentId;
        }
        _pulsePrev = _currentId;
        if (string.IsNullOrEmpty(_currentId)) return;
        var ring = GameObject.Find("Actor_" + _currentId + "_Ring");
        if (ring == null) return;
        var r = ring.GetComponent<Renderer>(); if (r == null || r.sharedMaterial == null) return;
        float p = 0.5f + 0.5f * Mathf.Sin(Time.time * 4f);
        var col = r.sharedMaterial.color; col.a = Mathf.Lerp(0.55f, 1f, p); r.sharedMaterial.color = col;
        float s = Mathf.Lerp(2.6f, 3.05f, p); ring.transform.localScale = new Vector3(s, s, 1f);
    }
}
