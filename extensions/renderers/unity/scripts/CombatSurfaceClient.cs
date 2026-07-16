using System.Collections;
using System.Collections.Concurrent;   // #1466: thread-safe hand-off from the QA HTTP thread to Update()
using System.Net;                      // #1466: localhost HttpListener for the QA input channel
using System.Threading;                // #1466: the QA listener runs off the Unity main thread
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
    // #1466 FIX A: hard wall-clock ceiling for ONE /combat-surface fetch. UnityWebRequest.timeout is
    // unreliable (a hung connect never fires it), so an explicit elapsed-time watchdog Aborts a stuck
    // request instead — the T3 wedge (t3-gate-1454) where the FIRST fetch hung forever and bricked the
    // whole PollLoop. 8s > PollInterval, so a healthy poll never trips it.
    public float FetchTimeout = 8f;

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
    Texture2D _blobT, _ringT, _pipT;          // procedural AO blob + contact decal + feet-pip, built once, shared
    Texture2D _decalFoe, _decalParty;         // RING-V2: per-team #1524 contact-decal textures (faction hue baked in)
    Material _silFoe, _silParty; bool _silMatMissing; // #1545: per-team walk-behind silhouette materials (ZTest Greater)
    AnimationClip _donorIdle; bool _donorTried; // goblin.fbx embedded Idle, for clipless-humanoid retarget

    // RING-V2 warm-floor port (#1515 fix 2 / #1524, from CohesionProbe.cs): a hearth-INDEPENDENT warm floor
    // blended into the actor grounding so shadows/decals sit in the plate's warm palette instead of neutral
    // grey. Exact ported values — do not retune here (0 = fully cool, 1 = fully warm; the ported weight is 0.35).
    static readonly Color WarmAmb = new Color(0.55f, 0.35f, 0.18f);   // CohesionProbe _warmAmb (lit-ground warm band)
    const float WARM_AMBIENT_FLOOR = 0.35f;                           // CohesionProbe WARM_AMBIENT_FLOOR

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
    // #1544 REST-WALK ROUTE: the engine-confirmed route of the most recent REST `walk_to_cell`. The rest
    // walk verb routes AROUND props (combat_grid.shortest_path) but the engine stores that route ONLY in the
    // /move RESPONSE `path` (server.py:5171 envelope_path) — it never writes combat.last_move_path, so the
    // surface's `lastPath` (viewer/server.py:3865 reads combat.last_move_path) is empty in rest and the glide
    // fell back to a STRAIGHT LINE that visually clipped prop cells ("walking through tables"). PostWalk now
    // parses that response `path` into this field so GlideTo follows the engine polyline cell-by-cell, exactly
    // as combat does. Pure consumer (reads a field the engine already returns); cleared once the matching
    // glide consumes it, so a later poll never reuses a stale walk route.
    readonly System.Collections.Generic.List<int[]> _walkPath = new System.Collections.Generic.List<int[]>();

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

    // WALKABLE-SLICE-V1 (item 1): the surface's authored DOORWAY cells (viewer/server.py _combat_doors,
    // `doors:[{cell:[c,r], to, toName, multi}]`) — the M-E room-transition affordance. Parsed alongside
    // impassable/lastPath/occluders in ParseSurfaceExtras. A rest-mode click on a door cell WALKS the lead
    // PC onto it then POSTs `cross_door` (item 3). Keyed by CellKey for an O(1) HandleCell lookup; the
    // dest-room NAME rides the value for an onboarding toast. Empty on a combat surface / a doorless room.
    readonly System.Collections.Generic.Dictionary<int, string> _doorTo = new System.Collections.Generic.Dictionary<int, string>();
    // Owner playtest #4 (B) DOOR DISCOVERABILITY: door cells rendered no affordance, so the owner could not
    // find how to change rooms. A persistent, gently PULSING gold glow quad + a floating "To <Room>" label
    // now mark each door cell (built from _doorTo whenever the set changes; a room swap rebuilds them). Pure
    // presentation over the same authored door cells the click path already crosses — no engine change.
    GameObject _doorRoot;                 // parent of the glow quads + labels (one-call teardown/rebuild)
    System.Collections.Generic.List<Material> _doorGlowMats;  // per-door glow material (per-frame alpha pulse)
    string _doorSig = "\0";               // signature of the door set the affordance was last built for
    // WALKABLE-SLICE-V1 (item 2): rest-mode NPC talk-targets by cell (rest_role:"npc" stage tokens). A
    // click on an NPC's cell POSTs `parley_approach` (the engine walks the lead PC adjacent + opens the
    // parley) instead of a walk. cellKey -> npc id. Rebuilt each ParseSurfaceExtras from the stage block.
    readonly System.Collections.Generic.Dictionary<int, string> _npcAtCell = new System.Collections.Generic.Dictionary<int, string>();

    // WALKABLE-SLICE-V1 (Option A): the rest-mode STAGE cast (party + present NPCs), parsed from the surface
    // `stage` block into the SAME Tok shape combat tokens use, so ApplySurf renders them through the ONE
    // spawn/reposition/despawn+glide pipeline — the owner must SEE his character stand, walk, and cross a
    // door in rest, not just in combat. Empty in combat (top-level `tokens` drive it there); the shared PC
    // ids mean a rest<->combat transition GLIDES the same actor instead of blinking a new one.
    readonly System.Collections.Generic.List<Tok> _stageCast = new System.Collections.Generic.List<Tok>();

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
    // #1482-review: discriminates a name-plate-ONLY root (EnsureNamePlate; hp-hidden foe) from a full
    // HP-bar root (EnsureHpBar) inside the shared _hpBars dict, so EnsureHpBar can UPGRADE a plate-only
    // root in place instead of early-returning on it when a foe's HP later becomes known.
    readonly System.Collections.Generic.HashSet<string> _namePlateOnly = new System.Collections.Generic.HashSet<string>();
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
    // #idle-persist: per-actor PERSISTENT idle graph. A resting actor's idle is a LIVE graph the Update loop
    // evaluates every frame (a one-shot pose cannot hold a skinned pose — see PlayIdleGraph). Torn down by
    // KillIdleGraph on glide/attack start and on despawn.
    readonly System.Collections.Generic.Dictionary<string, UnityEngine.Playables.PlayableGraph> _idleGraphOf = new System.Collections.Generic.Dictionary<string, UnityEngine.Playables.PlayableGraph>();
    // #anim-pack (RPG Character Mecanim pack — the PERMANENT #1408 T-pose/roster fix): the SHARED humanoid
    // AnimatorController (idle/walk/run + attack/hit/death states, driven by a Speed float + Attack/Hit/Death
    // triggers) retargeted onto any actor whose Animator has a VALID humanoid avatar. A REAL controller keeps
    // the Animator evaluating every frame, so it REPLACES the per-frame idle/walk/attack PlayableGraph
    // workaround for humanoids; the graph paths (PlayIdleGraph/MakeClipGraph/donor retarget) stay as the
    // FALLBACK for non-humanoid / clipless / controller-absent rigs — byte-identical to pre-#anim-pack when the
    // controller can't load or the avatar isn't humanoid. _ctrlDriven tracks who the controller currently
    // drives (every glide/attack/hit/death/idle branch keys off it). Parameter names must match the controller
    // built by build_worldos_humanoid_controller.cs.
    const string HumanoidControllerPath = "Assets/Animations/WorldOSHumanoid.controller";
    RuntimeAnimatorController _humanoidCtrl; bool _humanoidCtrlTried;
    readonly System.Collections.Generic.HashSet<string> _ctrlDriven = new System.Collections.Generic.HashSet<string>();
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
    // #1482: the foe token ids this surface (rebuilt each ApplySurf alongside _foeCells) — lets the name
    // plate read a foe's plate in a hostile tint so a first-timer can tell target from ally.
    readonly System.Collections.Generic.HashSet<string> _foeIds = new System.Collections.Generic.HashSet<string>();
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
    public float AdvisoryHold = 2.5f;   // #1482: seconds the on-screen note holds before it finishes fading (was 3.2 — match other transient text)
    string _advMsg = "";
    float _advT = 0f;                    // fade clock; alpha = 1 - advT/AdvisoryHold
    GUIStyle _advStyle;
    int[] _lastPostCell = null;          // the cell the last /move POST targeted (for the pulse anchor)

    // W6.4 (#1463) ONBOARDING READABILITY (the T3 gap — readability, not plumbing). A brief on-screen hint
    // layer names whose turn it is (by NAME) + the one-line affordance, fading out after the first
    // engine-accepted action; a world-space NAME PLATE rides each actor's HP-bar root (#1451 machinery, task
    // 2) with the isCurrent combatant's plate tinted gold (the turn indicator); and the walkability overlay
    // is forced ON for the first turn. ALL gated on _onboard = WORLDOS_PLAYTEST=1 (playtests) OR
    // WORLDOS_ONBOARD=1 (the app host's real player session). Absent BOTH (beauty captures) -> byte-identical
    // (no hint, no plate, overlay OFF), preserving the existing WORLDOS_PLAYTEST default-off pattern.
    [Header("Onboarding readability (#1463 W6.4)")]
    bool _onboard = false;
    bool _acted = false;                 // set on the first engine-accepted move/attack/walk -> the hint fades
    float _actedT = 0f;                  // fade clock after the first action
    const float HintFadeDur = 1.6f;
    string _currentName = "";            // display name of the isCurrent combatant (whose turn), for the hint
    GUIStyle _hintStyle, _hintSubStyle;
    readonly System.Collections.Generic.Dictionary<string, string> _nameOf = new System.Collections.Generic.Dictionary<string, string>();

    // W6.4 (#1463) STAGE MANIFEST (the #1463 core): an OPTIONAL StreamingAssets/stage.json
    // ({fire_anchors:[[x,z]], flicker:{amplitude,speed}}) that animates the shipped scene — a Perlin
    // light-flicker on the scene fire (brazier) lights and a warm procedural glow quad at each anchor, pulsed
    // by the same noise field so lights + floor pool breathe together like flame. ABSENT FILE = byte-identical
    // (no flicker touch, no glow quads) — a pure additive stage layer. The base intensities are captured so
    // the flicker is a revertible multiplier. Copied into the build by BuildMacOSPlayer.EnsurePackaged when
    // present. (Reuses MakeGroundQuad for the glow pool + the per-frame Update block, per the packet.)
    [Header("Stage manifest (#1463 W6.4; optional StreamingAssets/stage.json)")]
    bool _flickerActive = false;
    float _flickAmp = 0.35f, _flickSpeed = 6f;
    Light[] _fireLights;                 // scene brazier (fire) lights driven by the flicker
    float[] _fireBaseIntensity;          // captured base intensity -> flicker is a revertible multiplier
    float[] _fireSeed;                   // per-light Perlin seed offset -> independent flame flicker
    System.Collections.Generic.List<GameObject> _glowQuads;   // warm glow quads at fire_anchors
    System.Collections.Generic.List<Material> _glowMats;      // their materials (alpha pulsed each frame)
    float[] _glowSeed;                   // per-quad Perlin seed
    Texture2D _glowT;                    // warm radial glow texture, built once

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

    // WALKABLE-SLICE-V1 (item 6) RUNTIME PLATE REGISTRY (W5e; docs/roadmap/W5E-PLATE-REGISTRY-DECISION.md):
    // ONE persistent scene; the backdrop plate is resolved AT RUNTIME by the engine location id via a
    // StreamingAssets manifest (plates_manifest.json: {plates:{<slug>:{plate, planeSize?, cameraPin?}}}).
    // On a surface location change (a cross_door relocates the party -> the re-fetched surface carries the
    // new location.id), a brief fade + Texture2D.LoadImage swaps the plate on the "PaintedBackdrop" material
    // (the object paint_combat_v1.cs bakes) and re-sizes the camera-child quad to the new plate aspect.
    // Walkable/impassable/occluder truth stays ENGINE-side (already on the surface); the manifest is pure
    // presentation. ABSENT manifest / unknown location => no swap (byte-identical single-plate behavior).
    // (#W5e item 6; optional StreamingAssets/plates_manifest.json)
    // VFX-ANCHORS: an OPTIONAL per-plate "effects" array anchors animated presentation VFX (an animated
    // campfire over the painted firepit; embers/fireflies later) to grid cells. Each spec is {type, cell:[c,r],
    // scale?, y?}; `type` resolves through effects_registry.json (type -> prefab asset path in the actor
    // bundle) so content authors reference abstract types, not asset paths. Pure presentation — no engine/
    // gameplay contact; ABSENT effects (or absent registry / bundle) => nothing spawns (byte-identical).
    class EffectSpec { public string type; public int[] cell; public float scale = 1f; public float y = 0f; }
    class PlateEntry { public string plate; public float[] planeSize; public float ortho = -1f, pitch = float.NaN, yaw = float.NaN;
                       public System.Collections.Generic.List<EffectSpec> effects;
                       // UNIFY-THE-FRAMES (owner-ratified 2026-07-15): optional per-plate `boxes` file
                       // (StreamingAssets-relative, emitted by build_room_unified.cs) — the EXACT world-space
                       // box list the plate's depth conditioning was rendered from. When present, the depth-
                       // proxy occluders are built from THESE boxes verbatim (RebuildOccluders), so what masks
                       // an actor at runtime is byte-derived from what shaped the paint. Absent => legacy
                       // per-cell footprint proxies (byte-identical prior behavior).
                       public string boxesPath; }
    // world-space occluder boxes of the ACTIVE plate ({center,size} rows, kind!=floor), null => legacy path.
    System.Collections.Generic.List<float[]> _plateBoxes;
    string _plateBoxesLocId = "\0";  // the location _plateBoxes reflects (sentinel => unset); guards the stale-leak (#1575)
    bool _truthOverlay = System.Environment.GetEnvironmentVariable("WORLDOS_TRUTH_OVERLAY") == "1"; // G-key engine-truth overlay (playtest-#9 instrument); env=1 starts ON (QA/proof runs)
    Material _overlayMat;                                     // GL lines material (Hidden/Internal-Colored)

    // ---- UNIFY-THE-FRAMES truth overlay ---------------------------------------------------------
    // GL-immediate wireframes drawn AFTER the scene (Built-in RP): every grid cell's floor diamond
    // colored by live engine state (gold=walkable, red=impassable, cyan=door), plus magenta wireframes
    // of the active occluder volumes. Pure read of state the client already holds (_impassable /
    // _doorTo / Cols / Rows / _plateBoxes) — no engine call, no state write.
    Material EnsureOverlayMaterial()
    {
        if (_overlayMat != null) return _overlayMat;
        var sh = Shader.Find("Hidden/Internal-Colored");
        if (sh == null) return null;
        _overlayMat = new Material(sh) { hideFlags = HideFlags.HideAndDontSave };
        _overlayMat.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
        _overlayMat.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
        _overlayMat.SetInt("_Cull", (int)UnityEngine.Rendering.CullMode.Off);
        _overlayMat.SetInt("_ZTest", (int)UnityEngine.Rendering.CompareFunction.Always);
        _overlayMat.SetInt("_ZWrite", 0);
        return _overlayMat;
    }

    void OnRenderObject()
    {
        if (!_truthOverlay || Cols <= 0 || Rows <= 0) return;
        if (Camera.current != Camera.main) return;           // gameplay camera only (never scene/preview/probes)
        var m = EnsureOverlayMaterial(); if (m == null) return;
        m.SetPass(0);
        GL.PushMatrix();
        GL.Begin(GL.LINES);
        float y = FloorY + 0.03f;
        for (int r = 0; r < Rows; r++)
            for (int c = 0; c < Cols; c++)
            {
                int key = CellKey(c, r);
                Color col = _doorTo.ContainsKey(key) ? new Color(0.2f, 0.9f, 1f, 0.9f)
                          : _impassable.Contains(key) ? new Color(1f, 0.25f, 0.2f, 0.85f)
                          : new Color(1f, 0.85f, 0.3f, 0.35f);
                GL.Color(col);
                var w = CellToWorld(c, r);
                float h = CellSize * 0.5f;
                Vector3 a = new Vector3(w.x - h, y, w.z), b = new Vector3(w.x, y, w.z + h);
                Vector3 d2 = new Vector3(w.x + h, y, w.z), e = new Vector3(w.x, y, w.z - h);
                GL.Vertex(a); GL.Vertex(b); GL.Vertex(b); GL.Vertex(d2);
                GL.Vertex(d2); GL.Vertex(e); GL.Vertex(e); GL.Vertex(a);
            }
        if (_plateBoxes != null)
        {
            GL.Color(new Color(1f, 0.3f, 1f, 0.9f));
            foreach (var b in _plateBoxes)
            {
                Vector3 cn = new Vector3(b[0], b[1], b[2]);
                Vector3 hs = new Vector3(b[3] / 2f, b[4] / 2f, b[5] / 2f);
                // 4 vertical edges of the box
                for (int i = 0; i < 4; i++)
                {
                    float sx = (i == 0 || i == 3) ? -1f : 1f, sz = (i < 2) ? -1f : 1f;
                    GL.Vertex(cn + new Vector3(sx * hs.x, -hs.y, sz * hs.z)); GL.Vertex(cn + new Vector3(sx * hs.x, hs.y, sz * hs.z));
                }
                // top + bottom rectangles
                for (int lvl = -1; lvl <= 1; lvl += 2)
                {
                    Vector3 p1 = cn + new Vector3(-hs.x, lvl * hs.y, -hs.z), p2 = cn + new Vector3(hs.x, lvl * hs.y, -hs.z);
                    Vector3 p3 = cn + new Vector3(hs.x, lvl * hs.y, hs.z), p4 = cn + new Vector3(-hs.x, lvl * hs.y, hs.z);
                    GL.Vertex(p1); GL.Vertex(p2); GL.Vertex(p2); GL.Vertex(p3);
                    GL.Vertex(p3); GL.Vertex(p4); GL.Vertex(p4); GL.Vertex(p1);
                }
            }
        }
        GL.End();
        GL.PopMatrix();
    }
    System.Collections.Generic.Dictionary<string, PlateEntry> _plateManifest;
    System.Collections.Generic.Dictionary<string, string> _effectRegistry;   // type -> prefab asset path (effects_registry.json)
    System.Collections.Generic.List<GameObject> _effectInstances;            // live spawned effect instances (despawned on plate swap)
    GameObject _effectsRoot;                                                  // parent of the spawned effect instances (one-call teardown)
    string _locId = "";          // the surface's current location.id (parsed every ParseSurfaceExtras)
    string _plateLocId = "\0";   // the location the CURRENT backdrop plate was applied for (sentinel => unset)
    bool _plateSwapping = false; // guards against a re-entrant swap while a fade is mid-flight
    // #1544 TRANSITION POLISH: a SHARED black cover raised OPAQUE the instant a room change is detected (top
    // of ApplyJson, BEFORE ApplySurf/RebuildOccluders mutate the scene), so the destination room's occluder
    // proxies + repositioned cast are built BEHIND black — the owner never sees the un-textured greybox flash
    // or the black-gap disconnect the old fade-IN exposed. Held through the (synchronous) plate load, faded
    // OUT only once the destination plate is applied AND the new-room surface has been consumed. Destroyed at
    // the end of the reveal (fields nulled) so each transition builds a fresh cover.
    GameObject _plateCover; Material _plateCoverMat;

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
        // #1483 (runtime backstop; DEDUPE with #1477 at merge — same one-line fix): a macOS player with
        // Run-In-Background OFF PAUSES its whole loop (Update, coroutines, input, the /combat-surface poll)
        // whenever it is not the FOREGROUND app. The no-hijack launch (#1456) never activates the window, so
        // between the brief activate->click->restore taps the player FREEZES — the smoke's walk glide never
        // animates (motion-liveness fails) and the 2nd click reads a STALE surface (foe target unresolved ->
        // an attack falls through to a move). runInBackground=true keeps the loop ticking so QA input lands.
        // Harmless/standard for an interactive player; beauty captures are unaffected (rendered content is
        // identical — this only governs ticking while unfocused). #1477 also bakes this into PlayerSettings.
        Application.runInBackground = true;

        // Additive config resolution (#1322 W5a): the standalone player build has no Inspector to
        // hand-edit, so the app host (NSWorkspace launch w/ configuration.environment, mirroring
        // native-bridge.js) hands the engine origin + campaign through the PROCESS ENVIRONMENT.
        // Absent env vars ⇒ today's Inspector-set defaults, byte-identical to pre-#1322 behavior.
        string envUrl = System.Environment.GetEnvironmentVariable("WORLDOS_ENGINE_BASE_URL");
        if (!string.IsNullOrEmpty(envUrl)) ViewerUrl = envUrl;
        string envCampaign = System.Environment.GetEnvironmentVariable("WORLDOS_CAMPAIGN_ID");
        if (!string.IsNullOrEmpty(envCampaign)) CampaignId = envCampaign;

        // #Phase3 / owner playtest #4 (C): the walkability overlay is a QA/debug grid, NOT a normal-play
        // affordance — the owner found a default-ON overlay confusing (and, with the adopted crypt plate's
        // prop drift, visibly misaligned with the painted room). It now defaults OFF in normal play and turns
        // ON only under an explicit QA env var (WORLDOS_WALK_OVERLAY=1) or a playtest (WORLDOS_PLAYTEST=1). G
        // still toggles it live (ToggleOverlay). Beauty captures + a normal onboarding session (no QA var)
        // render a byte-identical, overlay-free scene. Built lazily on the first surface (needs grid extents).
        _overlayOn = System.Environment.GetEnvironmentVariable("WORLDOS_WALK_OVERLAY") == "1"
                  || System.Environment.GetEnvironmentVariable("WORLDOS_PLAYTEST") == "1";

        // #1463: onboarding readability (the T3 gap) — the whose-turn/affordance hint, name plates, and the
        // door affordance (B). On for player sessions: a playtest OR the app host's WORLDOS_ONBOARD launch.
        // Owner playtest #4 (C): onboarding NO LONGER force-enables the walkability overlay — a first-timer
        // gets the hint + the glowing doorway (B), not a full grid over the board. Absent both env vars
        // (beauty captures) stays byte-identical.
        _onboard = _overlayOn || System.Environment.GetEnvironmentVariable("WORLDOS_ONBOARD") == "1";

        // #1463: load the OPTIONAL stage manifest (fire flicker + glow anchors). Absent -> byte-identical.
        LoadStageManifest();
        // WALKABLE-SLICE-V1 (item 6): load the OPTIONAL plate registry (per-location backdrop swap). Absent
        // -> no swap, the scene's baked plate stands (byte-identical to pre-W5e).
        LoadPlateManifest();
        // VFX-ANCHORS: load the OPTIONAL effects registry (type -> prefab asset path). Absent -> no effects
        // resolve -> nothing spawns (byte-identical). Paired with the per-plate `effects` array above.
        LoadEffectsRegistry();
        // Option A (item 3): hide the scene's baked mannequins — the client now renders its own cast in rest
        // AND combat, so a baked actor would double-render / linger T-posed beside the live cast.
        HideBakedCast();

        Debug.Log("[CSC] start: campaign=" + CampaignId + " url=" + ViewerUrl + " overlay=" + _overlayOn + " onboard=" + _onboard);
        StartCoroutine(PollLoop());

        // #1466 QA INPUT CHANNEL (env-gated, OFF by default = byte-identical player). OS-synthetic mouse
        // never reaches a no-activation background Unity window (Input never sees it — HID/postToPid/
        // brief-activation all REFUTED; see docs/research register + #1466), so player QA had no way to
        // exercise the click path. When WORLDOS_QA_INPUT=1 we open a LOCALHOST-only HttpListener that
        // accepts a normalized viewport coord and feeds it through the SAME HandleClickAt raycast->cell->
        // pre-validation->POST path a human click takes — the client does everything else identically.
        if (System.Environment.GetEnvironmentVariable("WORLDOS_QA_INPUT") == "1") StartQaInput();
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
        // #1466 FIX A: the poll loop must SURVIVE any single bad fetch — one hung/failed request must
        // never end the loop (the T3 wedge: a forever-hung first Fetch left the loop dead and no surface
        // was ever applied). Fetch below is now self-limiting (watchdog + Abort), so it always returns
        // and the NEXT tick recovers; this loop is unconditional-forever by construction.
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
        // #1466 FIX A (T3 wedge): drive the request with an EXPLICIT elapsed-time watchdog — req.timeout=6
        // is an unreliable backstop (a hung connect can never fire it, wedging this coroutine AND PollLoop
        // forever: Player.log dies at "[CSC] start" then 10min of silence, zero surfaces applied). If the
        // request has not completed within FetchTimeout we Abort() it and bail so the next 1.5s poll tick
        // recovers. A per-fetch outcome line (ok/status/timeout) makes the wedge observable in Player.log.
        // ADDITIVE + byte-identical on the happy path: a prompt response falls straight through to ApplyJson.
        using (var req = UnityWebRequest.Get(ViewerUrl + "/combat-surface?campaign=" + CampaignId))
        {
            req.timeout = 6;   // built-in backstop retained; the watchdog below is the real guard
            var op = req.SendWebRequest();
            float t0 = Time.realtimeSinceStartup;
            while (!op.isDone)
            {
                if (Time.realtimeSinceStartup - t0 > FetchTimeout)
                {
                    req.Abort();
                    Debug.LogWarning("[CSC] fetch TIMEOUT (aborted after " + FetchTimeout.ToString("0.#") + "s) — PollLoop will retry next tick");
                    yield break;   // `using` disposes the aborted request; PollLoop continues
                }
                yield return null;
            }
            if (Ok(req)) { Debug.Log("[CSC] fetch ok (" + req.responseCode + ")"); _dbgSurf++; ApplyJson(req.downloadHandler.text); }
            else Debug.LogWarning("[CSC] fetch FAILED status=" + req.responseCode + " err=" + req.error);
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
        // #1544 TRANSITION POLISH: if this surface changed the room (a cross_door relocation) and the plate
        // manifest has an entry for the new location, raise the OPAQUE black cover NOW — BEFORE ApplySurf +
        // RebuildOccluders rebuild the destination room's cast and (invisible depth-only) occluder proxies.
        // The old fade-IN in SwapPlateCo let that rebuild flash over the OLD plate for ~0.18s (the owner's
        // "raw grey proxy boxes then a disconnected black frame"). Covering FIRST hides the whole rebuild +
        // plate-load window; MaybeSwapPlate then applies the destination plate behind the cover and fades back
        // in only once the plate is applied AND this new-room surface has been consumed (which happens here).
        if (PlateSwapPending()) RaisePlateCover();
        ApplySurf(s);
        // W6.1 (#1460): (re)build the invisible occluder proxies AFTER ApplySurf has applied this surface's
        // grid extents (CellToWorld depends on Cols/Rows). No-ops unless the occluder set/location changed.
        RebuildOccluders();
        // WALKABLE-SLICE-V1 (item 6): swap the backdrop plate when the surface's location changed and the
        // manifest has an entry for it (a cross_door into a new room). No-op otherwise (byte-identical).
        MaybeSwapPlate();
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
                _npcAtCell.Clear();                                            // WALKABLE-SLICE-V1 (item 2): rebuilt from this stage
                _stageCast.Clear();                                            // Option A: rebuilt from this stage
                if (_restMode && stage.ContainsKey("tokens") && stage["tokens"] is System.Collections.Generic.List<object> stoks)
                {
                    foreach (var e in stoks)
                    {
                        var tk = e as System.Collections.Generic.Dictionary<string, object>; if (tk == null) continue;
                        string role = tk.ContainsKey("rest_role") ? tk["rest_role"] as string : "";
                        string id = tk.ContainsKey("id") ? tk["id"] as string : "";
                        // Option A: render EVERY present stage token (party + npc) as a client actor, through the
                        // same spawn/pose/ground pipeline combat tokens use. Stage tokens carry name/team/kind
                        // (server _emit) so SpawnActor resolves the right model; rest cast are all team "ally".
                        if (!string.IsNullOrEmpty(id) && tk.ContainsKey("x") && tk.ContainsKey("y"))
                        {
                            _stageCast.Add(new Tok {
                                id = id,
                                name = tk.ContainsKey("name") ? tk["name"] as string : id,
                                team = tk.ContainsKey("team") ? tk["team"] as string : "ally",
                                x = System.Convert.ToInt32(tk["x"]), y = System.Convert.ToInt32(tk["y"]),
                                isCurrent = false, hp = 1, hpMax = 1,
                            });
                        }
                        // WALKABLE-SLICE-V1 (item 2): map every present NPC (rest_role:"npc") to its cell so a
                        // click there opens a parley_approach instead of a walk (browser: screen-combat.jsx:373).
                        if (role == "npc" && !string.IsNullOrEmpty(id) && tk.ContainsKey("x") && tk.ContainsKey("y"))
                            _npcAtCell[CellKey(System.Convert.ToInt32(tk["x"]), System.Convert.ToInt32(tk["y"]))] = id;
                        if (role != "party") continue;                        // party tokens walk; npc tokens are talk-targets
                        if (!string.IsNullOrEmpty(id) && string.IsNullOrEmpty(_restMoverId)) _restMoverId = id; // first party token = deterministic lead PC
                    }
                }
            }
            // WALKABLE-SLICE-V1 (item 1): parse the authored doorway cells (server _combat_doors). Guarded on
            // ContainsKey (mirrors impassable/occluders) so a /move response without `doors` leaves the prior
            // set intact; a /combat-surface poll always carries the key ([] when the room has no doors).
            if (root.ContainsKey("doors"))
            {
                _doorTo.Clear();
                var doors = root["doors"] as System.Collections.Generic.List<object>;
                if (doors != null) foreach (var de in doors)
                {
                    var dd = de as System.Collections.Generic.Dictionary<string, object>; if (dd == null) continue;
                    var cell = dd.ContainsKey("cell") ? dd["cell"] as System.Collections.Generic.List<object> : null;
                    if (cell == null || cell.Count < 2) continue;
                    string toName = dd.ContainsKey("toName") ? dd["toName"] as string : "";
                    _doorTo[CellKey(System.Convert.ToInt32(cell[0]), System.Convert.ToInt32(cell[1]))] = toName ?? "";
                }
            }
            // WALKABLE-SLICE-V1 (item 6): the surface's current location.id, unconditionally (drives the
            // runtime plate swap in MaybeSwapPlate). Guarded on presence so a /move response without the
            // block (e.g. walk_to_cell) leaves the last-known location intact.
            if (root.ContainsKey("location") && root["location"] is System.Collections.Generic.Dictionary<string, object> loc && loc.ContainsKey("id"))
                _locId = (loc["id"] as string) ?? _locId;
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
        // UNIFY-THE-FRAMES: the plate-box sidecar takes priority BEFORE the legacy empty-set early-out —
        // a room can have zero legacy occluder props yet a full box sidecar (walls always ship in it);
        // gating the sidecar behind _occRaw would silently drop every wall volume (codex review, #1575).
        // STALE-LEAK GUARD (adversarial-invariant-verify, #1575): _plateBoxes is only cleared inside
        // ApplyPlate, so entering a room with no manifest entry (or a missing plate file) leaves the
        // PREVIOUS room's boxes live — RebuildOccluders would then place the old room's walls at the old
        // room's world coords in the new room and discard the new room's real footprint occluders. Only
        // trust the sidecar when it was applied FOR the current location (_plateLocId == _locId).
        bool boxesForThisRoom = _plateBoxes != null && _plateBoxes.Count > 0 && _plateBoxesLocId == _locId;
        if ((_occRaw == null || _occRaw.Count == 0) && !boxesForThisRoom)
        { Debug.Log("[CSC] occluders: 0 (cleared)"); return; }

        var mat = EnsureOccluderMaterial();
        if (mat == null) return;                             // shader missing -> skip (never a visible box)
        _occRoot = new GameObject("OccluderProxies");
        // When the active plate ships its box sidecar, the proxies are THOSE boxes verbatim — the
        // identical volumes the plate's depth conditioning was rendered from, so runtime masking and
        // painted geometry cannot disagree (walls included, which the footprint path never covered —
        // the playtest-#9 walk-through-wall class). Legacy footprint path below is untouched.
        if (boxesForThisRoom)
        {
            int bn = 0;
            foreach (var b in _plateBoxes)
            {
                var bx = GameObject.CreatePrimitive(PrimitiveType.Cube);
                bx.name = "Occluder_box_" + bn;
                Destroy(bx.GetComponent<Collider>());
                bx.transform.SetParent(_occRoot.transform, true);
                bx.transform.position = new Vector3(b[0], b[1], b[2]);
                bx.transform.localScale = new Vector3(b[3], b[4], b[5]);
                var bxr = bx.GetComponent<Renderer>();
                bxr.sharedMaterial = mat;
                bxr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
                bxr.receiveShadows = false;
                bn++;
            }
            Debug.Log("[CSC] occluders: " + bn + " UNIFIED plate-box volumes (loc=" + _occLocId + ")");
            return;
        }
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
        // Option A: combat surfaces carry the cast in top-level `tokens`; a REST surface carries it under
        // `stage` (parsed to _stageCast). Render whichever is active through the SAME spawn/reposition/
        // despawn+glide pipeline, so the party is visible and walkable in rest and a rest<->combat transition
        // (shared PC ids) glides the same actor instead of blinking a new one.
        bool isCombat = s.tokens.Length > 0;
        var cast = isCombat ? s.tokens : (_restMode ? _stageCast.ToArray() : System.Array.Empty<Tok>());
        // #1441: rebuild the occupied-cell set (every token's cell) for client-side click pre-validation.
        // #Phase3: also rebuild the foe-cell set so the overlay hover reads red on an attackable cell.
        _occupied.Clear(); _foeCells.Clear(); _foeIds.Clear();
        foreach (var t in cast) if (t != null) { int k = CellKey(t.x, t.y); _occupied.Add(k); if (t.team == "foe") { _foeCells.Add(k); if (!string.IsNullOrEmpty(t.id)) _foeIds.Add(t.id); } }
        var present = new System.Collections.Generic.HashSet<string>();
        foreach (var t in cast)
        {
            // #anim-combat P1 fix: a surface-listed token is ALWAYS live to the client — hp<=0 while listed
            // means DOWNED (prone on the field, revivable), never a skip. Removal from the surface is the
            // only terminal signal (the stale path below).
            if (t == null) continue;
            if (!string.IsNullOrEmpty(t.id)) { present.Add(t.id); _nameOf[t.id] = string.IsNullOrEmpty(t.name) ? t.id : t.name; } // #1463: name plate source
            // #1582: remember the first cast token as the QA channel's actor-of-interest (the party
            // PC in rest mode) so /debug can report its viewport position for the walkability gate.
            if (string.IsNullOrEmpty(_qaActorId) && !string.IsNullOrEmpty(t.id)) _qaActorId = t.id;
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
        if (isCombat) ApplyCombat(s);
        else { _currentId = ""; _currentName = ""; RestNamePlates(cast); }   // Option A: rest cast — name plates, no HP/turn
        // #Phase3: keep the overlay in sync with the new surface — rebuild the quad pool if the grid
        // extents changed (rest rooms are non-14x11), then repaint per-cell tints for the new occupancy.
        if (_overlayOn) { EnsureOverlay(); RefreshOverlayColors(); }
        // owner playtest #4 (B): (re)build the door affordance for this surface's door set (rebuilds only on a
        // change — a room swap; a doorless/combat surface tears it down). Unconditional: the glowing doorway is
        // a normal-play affordance, not a QA overlay.
        EnsureDoorAffordance();
    }

    // Option A: the rest cast gets a #1484 name plate (no HP bar) under onboarding/playtest — mirrors combat's
    // gated plates so beauty captures stay clean. UpdateHpBars rides + prunes them when the actor despawns.
    void RestNamePlates(Tok[] cast)
    {
        if (!_onboard) return;
        foreach (var t in cast) if (t != null && !string.IsNullOrEmpty(t.id)) EnsureNamePlate(t.id, FindActor(t.id));
    }

    // Option A (item 3): when the client renders its OWN cast (rest AND combat), hide the scene's BAKED
    // mannequins so there is no double-render / stale T-posed actor beside the live cast. Runtime-only —
    // SetActive(false) never touches the scene asset, so editor-only workflows that rely on the baked cast
    // are preserved. Runs once at Start, before any runtime spawn, so every Actor_* present is baked.
    void HideBakedCast()
    {
        var hide = new System.Collections.Generic.HashSet<GameObject>();
        // Every baked character MESH -> hide its whole root object. At Start (before any runtime spawn) every
        // SkinnedMeshRenderer in the scene is baked scaffolding (paint_combat_v1's FIGHTER/Goblin3D/pose
        // dummies P___bind_-1 / P_Idle_* / P_Walk_*), so this catches them regardless of name.
        foreach (var smr in GameObject.FindObjectsOfType<SkinnedMeshRenderer>())
        { var t = smr.transform; while (t.parent != null) t = t.parent; hide.Add(t.gameObject); }
        // Baked selection decals (AO blobs + rings) live as their own roots (SoloAO/SoloRing/HeroAO), plus any
        // prior Actor_* — match them by name so no stale ring is left under the live cast.
        foreach (var tr in GameObject.FindObjectsOfType<Transform>())
        {
            if (tr.parent != null) continue;   // roots only
            string n = tr.name;
            if (n.StartsWith("Actor_") || n.StartsWith("Solo") || n.StartsWith("Hero")
                || n.EndsWith("AO") || n.EndsWith("Ring") || n.EndsWith("Core")) hide.Add(tr.gameObject);
        }
        foreach (var go in hide) if (go != this.gameObject) go.SetActive(false);
    }

    // #anim-combat: the ported verb map, driven off the surface hp fields + isCurrent (engine truth only).
    void ApplyCombat(Surf s)
    {
        // active-turn combatant (attacker anchor + ring-pulse target).
        _currentId = "";
        foreach (var t in s.tokens) if (t != null && t.isCurrent && !string.IsNullOrEmpty(t.id)) { _currentId = t.id; break; }
        // #1463: the display name of the active combatant, for the onboarding hint's "whose turn" line.
        _currentName = ""; if (!string.IsNullOrEmpty(_currentId)) _nameOf.TryGetValue(_currentId, out _currentName);
        Transform attacker = string.IsNullOrEmpty(_currentId) ? null : FindActor(_currentId);

        foreach (var t in s.tokens)
        {
            if (t == null || string.IsNullOrEmpty(t.id)) continue;
            if (t.hpMax <= 0)
            {
                // #1482: foes hide their HP (viewer gates hp/hpMax on hp_known — a D&D DM-screen posture), so
                // they never enter the HP-bar path and never got a name plate — the reason a first-timer took
                // 14 blind clicks to find the foe. Give any hp-less token a name-plate-ONLY root (mirrors the
                // hero plate on the same billboarded HP-bar root UpdateHpBars positions each frame, minus the
                // HP quads). Onboard-only, so beauty captures stay byte-identical.
                if (_onboard) EnsureNamePlate(t.id, FindActor(t.id));
                continue;
            }
            int newHp = t.hp;
            int prevHp; bool hadPrev = _hpOf.TryGetValue(t.id, out prevHp);
            _hpMaxOf[t.id] = t.hpMax;

            if (hadPrev && newHp < prevHp && !_downed.Contains(t.id))
            {
                Transform tgt = FindActor(t.id);
                if (tgt != null)
                {
                    FloatDamage(tgt.position, "-" + (prevHp - newHp), new Color(1f, 0.95f, 0.45f, 1f));
                    if (newHp > 0)
                    {
                        // #anim-pack: fire the controller's Hit reaction (the transform knockback flinch still
                        // runs on top for any rig). Controller-absent actors just flinch, as before.
                        if (_ctrlDriven.Contains(t.id)) { var han = tgt.GetComponentInChildren<Animator>(); if (han != null) han.SetTrigger("Hit"); }
                        StartCoroutine(FlinchCo(tgt, attacker != null ? attacker.position : tgt.position - tgt.forward));
                    }
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
        // #1482-review: name-plate-only roots (hp-hidden foes) never enter _hpOf (they `continue` above),
        // so the prune above misses them — a foe that leaves the surface while still hp-hidden left its
        // plate floating forever over a stale actor (baked actors are never despawned; see ApplySurf's
        // despawn-on-removal note). Prune those separately off the same surface-presence check.
        if (_namePlateOnly.Count > 0)
        {
            var goneNamePlates = new System.Collections.Generic.List<string>();
            foreach (var id in _namePlateOnly) { bool here = false; foreach (var t in s.tokens) if (t != null && t.id == id) { here = true; break; } if (!here) goneNamePlates.Add(id); }
            foreach (var id in goneNamePlates) RemoveHpBar(id);
        }
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

    // ---- owner playtest #4 (B): DOOR AFFORDANCE (pulsing gold glow + floating "To <Room>" label) --------
    // Door cells previously rendered NOTHING, so the owner could not find how to change rooms. These mark the
    // authored door cells (already in _doorTo) with an unmissable glow + destination label. Presentation-only
    // over the same cells the click path already crosses (HandleCell -> cross_door); no engine/contract change.

    static readonly Color DoorGlowCol = new Color(1f, 0.80f, 0.30f, 0.55f);  // warm gold (door affordance)

    // A short fingerprint of the door set, so EnsureDoorAffordance rebuilds only on an actual change (a room
    // swap), not every poll — mirroring the occluder/overlay "rebuild-on-change" idiom.
    string DoorSignature()
    {
        if (_doorTo.Count == 0) return "";
        var keys = new System.Collections.Generic.List<int>(_doorTo.Keys); keys.Sort();
        var sb = new System.Text.StringBuilder();
        foreach (var k in keys) sb.Append(k).Append('=').Append(_doorTo[k]).Append(';');
        return sb.ToString();
    }

    // Called every ApplySurf: (re)build the door glow/labels only when the door set changed. A doorless or
    // combat surface (empty _doorTo) tears the affordance down.
    void EnsureDoorAffordance()
    {
        string sig = DoorSignature();
        if (sig == _doorSig) return;
        _doorSig = sig;
        BuildDoorAffordance();
    }

    void BuildDoorAffordance()
    {
        if (_doorRoot != null) { Object.Destroy(_doorRoot); _doorRoot = null; }
        _doorGlowMats = null;
        if (_doorTo.Count == 0) return;
        _doorRoot = new GameObject("DoorAffordance");
        _doorGlowMats = new System.Collections.Generic.List<Material>();
        foreach (var kv in _doorTo)
        {
            int c = kv.Key / 10000, r = kv.Key % 10000;
            Vector3 p = CellToWorld(c, r);
            // gold glow quad on the floor (queue 1949 -> just under the actor AO/ring, like the stage glow).
            var q = GameObject.CreatePrimitive(PrimitiveType.Quad); q.name = "DoorGlow_" + c + "_" + r;
            Object.DestroyImmediate(q.GetComponent<Collider>());
            q.transform.SetParent(_doorRoot.transform, false);
            q.transform.position = new Vector3(p.x, FloorY + 0.03f, p.z);
            q.transform.localEulerAngles = new Vector3(90f, 0f, 0f);
            q.transform.localScale = new Vector3(CellSize * 1.7f, CellSize * 1.7f, 1f);
            var m = new Material(Shader.Find("Sprites/Default")); m.mainTexture = GlowTex();
            m.color = DoorGlowCol; m.renderQueue = 1949;
            var rend = q.GetComponent<Renderer>(); rend.sharedMaterial = m; rend.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            _doorGlowMats.Add(m);
            // floating "To <Room>" label above the doorway (camera-facing TextMesh; UpdateDoorGlow billboards it).
            var g = new GameObject("DoorLabel_" + c + "_" + r);
            g.transform.SetParent(_doorRoot.transform, false);
            g.transform.position = new Vector3(p.x, FloorY + 3.2f, p.z);
            var tm = g.AddComponent<TextMesh>();
            tm.text = "To " + Prettify(kv.Value); tm.fontSize = 64; tm.characterSize = 0.16f;
            tm.anchor = TextAnchor.LowerCenter; tm.alignment = TextAlignment.Center; tm.color = new Color(1f, 0.87f, 0.45f, 1f);
            var font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            if (font == null) font = Resources.GetBuiltinResource<Font>("Arial.ttf");
            var mr = g.GetComponent<MeshRenderer>();
            if (font != null) { tm.font = font; if (mr != null) { mr.sharedMaterial = new Material(font.material); mr.sharedMaterial.renderQueue = 3096; } }
            else if (mr != null && mr.sharedMaterial != null) mr.sharedMaterial.renderQueue = 3096;
        }
    }

    // Prettify a destination room name/id for the label: split on _/-, Title-Case each word. An already-pretty
    // toName ("Campfire Clearing") passes through; a raw id ("camp_clearing_night") -> "Camp Clearing Night".
    static string Prettify(string raw)
    {
        if (string.IsNullOrEmpty(raw)) return "the next room";
        var parts = raw.Replace('_', ' ').Replace('-', ' ').Split(new[] { ' ' }, System.StringSplitOptions.RemoveEmptyEntries);
        var sb = new System.Text.StringBuilder();
        foreach (var w in parts) { if (sb.Length > 0) sb.Append(' '); sb.Append(char.ToUpper(w[0])); if (w.Length > 1) sb.Append(w.Substring(1)); }
        return sb.Length > 0 ? sb.ToString() : "the next room";
    }

    // Per-frame gentle pulse of the door glow(s) + billboard each label to the camera. Cheap; no-op with no
    // doors. Called from Update.
    void UpdateDoorGlow()
    {
        if (_doorRoot == null) return;
        if (_doorGlowMats != null)
        {
            float pulse = 0.30f + 0.32f * (0.5f + 0.5f * Mathf.Sin(Time.time * 3.0f));  // ~0.30..0.62 alpha
            for (int i = 0; i < _doorGlowMats.Count; i++)
                if (_doorGlowMats[i] != null) { var col = DoorGlowCol; col.a = pulse; _doorGlowMats[i].color = col; }
        }
        var cam = Camera.main;
        if (cam != null)
            foreach (Transform t in _doorRoot.transform)
                if (t.GetComponent<TextMesh>() != null) t.rotation = cam.transform.rotation;
    }

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

    // #anim-pack: the shared humanoid controller, loaded ONCE from the actor bundle by HumanoidControllerPath.
    // Baked into worldos_actors (with its pack clip dependencies) by BuildMacOSPlayer.EnsurePackaged. Absent
    // (an un-repackaged/old bundle) -> null -> every actor uses the per-frame graph fallback (byte-identical).
    RuntimeAnimatorController HumanoidController()
    {
        if (_humanoidCtrlTried) return _humanoidCtrl;
        _humanoidCtrlTried = true;
        _humanoidCtrl = LoadAsset<RuntimeAnimatorController>(HumanoidControllerPath);
        Debug.Log("[CSC] humanoid controller " + (_humanoidCtrl != null ? "loaded" : "absent") + " @" + HumanoidControllerPath);
        return _humanoidCtrl;
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

    // Returns [model_ref, albedo_ref, anim_ref]. Mirrors paint_combat_v1.cs's resolveAsset, with the
    // #1601 divergence below: the in-code CHARACTER default (monster still -> goblin) is an ANIMATED
    // humanoid, NOT the clipless hero.fbx. hero.fbx is non-humanoid with no resolvable idle, so a token
    // that fell to this floor (a runtime-spawned rogue whose name matches no asset/alias) rendered a
    // sideways T-pose. patron_commoner is the generic rigged humanoid the rest of the cast already spawn
    // correctly; its idle lives in a SEPARATE moveset fbx, so the char floor must ALSO name that anim_ref
    // or FindOwnClip finds no idle. goblin.fbx carries its OWN embedded Idle, so the monster floor keeps
    // an empty anim_ref. (This in-code floor only fires when registry.json is absent/corrupt; the normal
    // path resolves defaults.character -> template_human, which #1601 also re-pointed at patron_commoner.)
    // #1423 albedo nuance: only substitute the template albedo when this token fell through to a default;
    // a real resolved row with an empty albedo means "own material".
    string[] ResolveAsset(string slug, string kind)
    {
        LoadRegistry();
        string fbxDef = kind == "monster" ? "Assets/chars_v2/goblin/goblin.fbx" : "Assets/chars_v2/patron_commoner/rigged.fbx";
        string albDef = kind == "monster" ? "Assets/chars_v2/goblin/albedo.png" : "Assets/chars_v2/patron_commoner/albedo.jpg";
        string animDef = kind == "monster" ? "" : "Assets/chars_v2/patron_commoner/anim_idle.fbx";
        if (_regAssets == null) return new[] { fbxDef, albDef, animDef };
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
        return new[] { fbxDef, albDef, animDef };
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
        // RING-V2 warm-floor port: the grounding disc RGB is lerped from the old cool-neutral (0.02,0.02,0.03,
        // b>r — reads cool/grey over the warm plate) toward WarmAmb by WARM_AMBIENT_FLOOR, so the AO shadow
        // sits in the plate's palette. Same Lerp(cool, warm, FLOOR) operation #1524 used for actor ambient.
        _blobT = new Texture2D(256, 256, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp };
        Color aoRgb = Color.Lerp(new Color(0.02f, 0.02f, 0.03f), WarmAmb, WARM_AMBIENT_FLOOR);
        var px = new Color[256 * 256]; float c = 127.5f;
        for (int y = 0; y < 256; y++) for (int x = 0; x < 256; x++) { float d = Mathf.Clamp01(Mathf.Sqrt((x - c) * (x - c) + (y - c) * (y - c)) / c); px[y * 256 + x] = new Color(aoRgb.r, aoRgb.g, aoRgb.b, Mathf.Clamp01(Mathf.Pow(1f - d, 0.9f))); }
        _blobT.SetPixels(px); _blobT.Apply();
        return _blobT;
    }

    // Hollow ring ellipse — retained for the cosmetic cell pulses (AmberPulse / FlashReject), NOT the actor
    // grounding (RING-V2 moved actor grounding to ContactDecalTex + the feet-pip).
    Texture2D RingTex()
    {
        if (_ringT != null) return _ringT;
        _ringT = new Texture2D(256, 256, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp };
        var px = new Color[256 * 256]; float c = 127.5f;
        for (int y = 0; y < 256; y++) for (int x = 0; x < 256; x++) { float d = Mathf.Sqrt((x - c) * (x - c) + (y - c) * (y - c)) / c; float a = (d > 0.78f && d < 0.93f) ? 1f : 0f; px[y * 256 + x] = new Color(1f, 1f, 1f, a); }
        _ringT.SetPixels(px); _ringT.Apply();
        return _ringT;
    }

    // RING-V2 (#1524 port): the subtle faction CONTACT DECAL that replaces the bright UI ellipse as the
    // actor's grounding+selection read (the ellipse cost the #1515 cohesion panel 2.0 alone — scorers read
    // it as a game-engine selection ring). Exact port of CohesionProbe.ContactDecalTex: a dark warm grounding
    // disc + a low-saturation faction rim baked into the texture (faction semantics survive), alpha-blended so
    // it darkens the plate like a muted PoE2 selection shadow. Per-team so the baked hue survives a white tint.
    // Values are the merged #1524 evidence — do not retune.
    Texture2D ContactDecalTex(bool foe)
    {
        if (foe && _decalFoe != null) return _decalFoe;
        if (!foe && _decalParty != null) return _decalParty;
        const int N = 256;
        Color faction = foe ? new Color(0.55f, 0.24f, 0.19f) : new Color(0.34f, 0.52f, 0.60f);
        Color core = new Color(0.045f, 0.035f, 0.03f);   // #1524 warm near-black grounding core
        var t = new Texture2D(N, N, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp };
        var px = new Color[N * N];
        for (int y = 0; y < N; y++) for (int x = 0; x < N; x++)
        {
            float dx = (x - N / 2f) / (N / 2f), dy = (y - N / 2f) / (N / 2f);
            float d = Mathf.Sqrt(dx * dx + dy * dy);
            float discA = Mathf.Pow(Mathf.Clamp01(1f - d / 0.98f), 1.7f) * 0.5f;   // soft grounding shadow
            float ringA = Mathf.Exp(-Mathf.Pow((d - 0.72f) / 0.13f, 2f)) * 0.55f;  // muted faction contact rim
            float w = ringA / (ringA + discA + 1e-4f);
            Color rgb = Color.Lerp(core, faction, w);
            float a = d > 1f ? 0f : Mathf.Clamp01(discA + ringA);
            px[y * N + x] = new Color(rgb.r, rgb.g, rgb.b, a);
        }
        t.SetPixels(px); t.Apply();
        if (foe) _decalFoe = t; else _decalParty = t;
        return t;
    }

    // RING-V2: the small team-colored FEET-PIP — the PRIMARY team read (the subtle contact decal above is
    // grounding+selection). A soft filled dot (white; the team hue comes from the quad's material tint),
    // solid to ~0.6r then a soft falloff to the edge so it reads as a crisp pip, not a ring.
    Texture2D PipTex()
    {
        if (_pipT != null) return _pipT;
        _pipT = new Texture2D(256, 256, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp };
        var px = new Color[256 * 256]; float c = 127.5f;
        for (int y = 0; y < 256; y++) for (int x = 0; x < 256; x++) { float d = Mathf.Sqrt((x - c) * (x - c) + (y - c) * (y - c)) / c; float a = 1f - Mathf.SmoothStep(0.6f, 1f, d); px[y * 256 + x] = new Color(1f, 1f, 1f, Mathf.Clamp01(a)); }
        _pipT.SetPixels(px); _pipT.Apply();
        return _pipT;
    }

    // #1545 walk-behind silhouette: the per-team material for the second (ZTest Greater) pass added to every
    // actor renderer at spawn. Where a depth-proxy box (WorldOS/OccluderDepth, ZWrite On) has overdrawn the
    // actor, the actor's front-face fragments are FARTHER than the buffered proxy depth, so ZTest Greater fires
    // and paints a flat team-tinted silhouette (~0.45 alpha) instead of the actor vanishing (BG2/PoE convention;
    // #1545 + the owner's "character disappears near doors" report). Unoccluded actors: front-face depth equals
    // their own opaque depth -> Greater fails -> nothing drawn. Mirrors EnsureOccluderMaterial's missing-shader
    // graceful skip (shader must be in Always-Included Shaders to resolve in the player build).
    Material EnsureSilhouetteMaterial(bool foe)
    {
        if (foe && _silFoe != null) return _silFoe;
        if (!foe && _silParty != null) return _silParty;
        if (_silMatMissing) return null;
        var sh = Shader.Find("WorldOS/ActorSilhouette");
        if (sh == null) { Debug.LogWarning("[CSC] silhouette: WorldOS/ActorSilhouette not found (add to Always-Included Shaders); walk-behind mask disabled."); _silMatMissing = true; return null; }
        // Reuse the existing runtime team colors at the #1545 ~0.45 mask alpha (no new hue invented).
        var m = new Material(sh) { color = foe ? new Color(1f, 0.13f, 0.10f, 0.45f) : new Color(0.4f, 0.95f, 1f, 0.45f) };
        m.renderQueue = 3000;   // Transparent: draws after all opaque geometry + proxy depth is laid
        if (foe) _silFoe = m; else _silParty = m;
        return m;
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

        // #idle-fix: register the model + moveset fbx BEFORE the idle pose so the idle resolver (FindOwnClip)
        // can search this actor's OWN clips in EITHER source — a rigged char whose idle lives in a separate
        // anim_*.fbx (registry anim_ref) poses correctly instead of falling through to the donor/T-pose.
        _fbxOf[id] = fbx;
        _animOf[id] = (aref != null && aref.Length > 2) ? aref[2] : "";

        string nm = "Actor_" + id;
        var existing = GameObject.Find(nm); if (existing != null) Object.DestroyImmediate(existing);
        var go = (GameObject)Object.Instantiate(prefab); go.name = nm;

        var cam = Camera.main;
        float camYaw = cam != null ? cam.transform.eulerAngles.y : 45f;
        // #1397 pitch guard: a skinned Meshy Y-up rig needs pitch 0; only a static non-skinned mesh keeps
        // the legacy -90 Z-up stand-up. Set before posing (depends only on rig type).
        float pitchX = go.GetComponentInChildren<SkinnedMeshRenderer>() != null ? 0f : -90f;
        go.transform.rotation = Quaternion.Euler(pitchX, camYaw + 180f, 0f);

        // #idle-persist: keep the Animator updating its skinned pose even when briefly off-screen/unfocused —
        // AnimatorCullingMode.CullUpdateTransforms freezes the skinned mesh at its last dispatched pose when
        // the actor isn't in the active render, which is exactly the "T-pose on capture" symptom. AlwaysAnimate
        // guarantees the persistent idle graph's pose is dispatched every frame in the player's render loop.
        var animC = go.GetComponentInChildren<Animator>();
        if (animC != null) animC.cullingMode = AnimatorCullingMode.AlwaysAnimate;

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

        // #anim-pack: a VALID humanoid avatar retargets the shared RPG-pack controller (idle/walk/run +
        // attack/hit/death, driven by Speed + triggers). A real controller keeps the Animator evaluating every
        // frame, so the bind-pose revert the per-frame idle graph worked around (#1408/#idle-persist) cannot
        // happen — this is the permanent T-pose fix. Assigned AFTER the bind-pose scale lock so the controller's
        // idle first frame can't inflate the height; animC.Update(0f) settles that idle before the grounding
        // Measure below. Non-humanoid / clipless / controller-absent actors fall through to the persistent idle
        // graph (byte-identical to pre-#anim-pack).
        bool ctrlDriven = false;
        // #anim-pack: require isHuman AND isValid — an avatar flagged humanoid but INVALID (a broken bone
        // map) cannot retarget the humanoid clips and would silently T-pose (the exact #1408 failure). This
        // mirrors anim_pack_avatar_gate.cs's (isHuman && isValid) accept criterion; an invalid rig falls
        // through to the per-frame graph fallback below.
        if (animC != null && animC.avatar != null && animC.avatar.isHuman && animC.avatar.isValid)
        {
            var hc = HumanoidController();
            if (hc != null) { animC.runtimeAnimatorController = hc; animC.applyRootMotion = false; animC.Update(0f); ctrlDriven = true; _ctrlDriven.Add(id); }
        }
        // Pose to a neutral idle for the VISUAL now that scale is locked (fallback path). #idle-persist: start a
        // PERSISTENT idle graph (Update evaluates it each frame) — a one-shot Evaluate cannot hold a SKINNED
        // pose (a disabled Animator freezes GPU skinning at bind; an enabled controllerless Animator reverts to
        // bind), so the idle must be a live graph like the walk glide. Resolve the clip: own idle (model OR
        // moveset), else the model's first clip, else the goblin donor idle retargeted onto a clipless rig.
        if (!ctrlDriven) PlayIdleGraph(go, id, ResolveIdleClip(id, fbx, go));

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

        // #1545 walk-behind silhouette: append the ZTest-Greater team material as a SECOND pass on each actor
        // renderer, so an actor overdrawn by a depth-proxy box renders a flat tinted silhouette instead of
        // vanishing. Added after the albedo assignment so it's the last material. No-ops (skips) if the shader
        // isn't in the build (EnsureSilhouetteMaterial warns once).
        // NOTE (single-submesh assumption): Unity maps the extra material to the renderer's LAST submesh only,
        // so this fully covers actors whose renderer has one submesh — which is every current actor (the albedo
        // path above assigns a single sharedMaterial per renderer, and the box run confirmed rends=1 / one
        // material each). A future MULTI-submesh actor (separate body/clothing/weapon materials) would silhouette
        // only its last submesh; the general fix is a dedicated per-submesh silhouette renderer (deferred — it
        // does not trigger with current assets and needs box re-validation).
        var sil = EnsureSilhouetteMaterial(foe);
        if (sil != null)
            foreach (var r in rends)
            {
                var ms = new System.Collections.Generic.List<Material>(r.sharedMaterials);
                ms.Add(sil); r.sharedMaterials = ms.ToArray();
            }

        // RING-V2 ground siblings, laid flat on the floor. `_AO` = warm-tinted grounding blob; `_Ring` = the
        // #1524 subtle faction contact decal (2.6->2.3, the merged evidence size) that carries grounding +
        // selection (UpdateTurnPulse breathes it on the active turn); `_Pip` = the small team-colored feet-pip,
        // the PRIMARY team read (replaces the old bright ellipse). MoveActorAndShadows drags all three with the feet.
        MakeGroundQuad(nm + "_AO", p, 0.04f, 2.0f, BlobTex(), Color.white, 1950);
        MakeGroundQuad(nm + "_Ring", p, 0.06f, 2.3f, ContactDecalTex(foe), new Color(1f, 1f, 1f, 0.72f), 1955);
        MakeGroundQuad(nm + "_Pip", p, 0.07f, 0.7f, PipTex(), foe ? new Color(1f, 0.13f, 0.10f, 1f) : new Color(0.4f, 0.95f, 1f, 1f), 1958);

        _spawned.Add(id);
        // #1441/#anim-combat: _fbxOf + _animOf were registered up-front (before the idle pose). Seed the cell
        // (so the first poll doesn't spuriously glide a just-spawned actor already on its engine cell) and the
        // head-top offset for the HP/name plate (height is the scale target; +margin clears the silhouette).
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
        foreach (var suf in new[] { "", "_AO", "_Core", "_Ring", "_Pip" })
        {
            var g = GameObject.Find("Actor_" + id + suf);
            if (g != null) Object.Destroy(g);
        }
        _spawned.Remove(id);
        // #1441: tear down any in-flight glide + per-actor state so a re-spawn starts clean.
        if (_glide.TryGetValue(id, out var co) && co != null) StopCoroutine(co);
        KillWalkGraph(id);   // the stopped glide can't destroy its own graph (#1451-review P2)
        KillIdleGraph(id);   // #idle-persist: tear down the persistent idle graph too
        _glide.Remove(id); _cellOf.Remove(id); _fbxOf.Remove(id);
        _animOf.Remove(id); _topOf.Remove(id); RemoveHpBar(id);   // #anim-combat: clear combat/anim state
        _downed.Remove(id); _downRunning.Remove(id); _reviveWanted.Remove(id); _downPose.Remove(id);
        _ctrlDriven.Remove(id);   // #anim-pack: forget controller-driven state so a re-spawn re-resolves the avatar
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
            // #idle-fix: a FIRST-SIGHTED actor that did not come through SpawnActor (a baked Actor_<id>, or
            // one relocated by the engine before any glide) was only GROUNDED here — never idle-POSED — so a
            // clipless humanoid rendered its raw bind (T) pose. Pose idle FIRST, then ground the posed bounds
            // (feet -> FloorY), mirroring SpawnActor's pose->ground order so the actor also can't float.
            PoseIdle(a.gameObject);
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

    // #idle-fix (WALKABLE-SLICE-V1 follow-up): after a runtime plate swap (a location change), SETTLE every
    // cast member to a grounded idle on the NEW plate. A cross-room location change repositions the whole
    // cast at once; a glide that is still in flight (or interrupted by the rapid re-fetch a cross_door
    // triggers) can leave a clipless humanoid frozen in its bind (T) pose and/or floating off the floor.
    // Stop any in-flight glide, then re-pose idle and re-ground (feet -> FloorY) — the SAME pose->ground the
    // glide arrival uses. Presentation-only; a DOWNED (prone) combatant is left as-is. Called from ApplyPlate.
    void SettleCastIdleGrounded()
    {
        foreach (var kv in _cellOf)
        {
            var a = FindActor(kv.Key); if (a == null) continue;
            if (_downed.Contains(kv.Key)) continue;                 // a downed combatant stays prone
            if (_glide.TryGetValue(kv.Key, out var g) && g != null) { StopCoroutine(g); _glide[kv.Key] = null; }
            KillWalkGraph(kv.Key);                                   // the stopped glide can't destroy its own graph
            PoseIdle(a.gameObject);
            GroundSnap(a, kv.Value[0], kv.Value[1]);
        }
    }

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
        foreach (var suf in new[] { "_AO", "_Core", "_Ring", "_Pip" })
        {
            var g = GameObject.Find(a.name + suf);
            if (g != null) g.transform.position += delta;
        }
    }

    // #1544: the engine-confirmed polyline to follow for a from->to move, or null for a straight line. Combat
    // moves ride the surface `_lastPath` (combat.last_move_path); rest walks ride `_walkPath` (the walk
    // response `path`, which never reaches the surface's lastPath — see _walkPath). A path only applies when
    // its endpoints match THIS move, so a stale route is never followed. Returns the SAME list instance so the
    // caller can tell which channel matched (rest routes are single-use and cleared on consumption).
    System.Collections.Generic.List<int[]> MatchingEnginePath(int fromCx, int fromCy, int cx, int cy)
    {
        if (PathMatches(_lastPath, fromCx, fromCy, cx, cy)) return _lastPath;
        if (PathMatches(_walkPath, fromCx, fromCy, cx, cy)) return _walkPath;
        return null;
    }
    static bool PathMatches(System.Collections.Generic.List<int[]> p, int fromCx, int fromCy, int cx, int cy)
    {
        return p != null && p.Count >= 2
            && p[0][0] == fromCx && p[0][1] == fromCy
            && p[p.Count - 1][0] == cx && p[p.Count - 1][1] == cy;
    }

    // #1441 GLIDE: tween the actor cell->cell at GlideSpeed, playing a walk clip while moving and
    // returning to idle at rest. Follows the ENGINE-CONFIRMED polyline (combat lastPath or rest _walkPath)
    // when it matches this move (start==path[0] && target==path[-1]); otherwise a straight-line fallback.
    // Rings/AO follow every frame. Presentation-only: only ever called with an engine-confirmed target.
    IEnumerator GlideTo(Transform a, string id, int fromCx, int fromCy, int cx, int cy)
    {
        var go = a.gameObject;
        float pitchX = go.GetComponentInChildren<SkinnedMeshRenderer>() != null ? 0f : -90f;

        // Build the world-space route. Default: straight line start->target. If the engine confirmed a route
        // for THIS move (combat `lastPath`, or the rest walk's `_walkPath` — #1544), follow its cells (each
        // grounded to feet on FloorY via the same offset) so the walk detours around props like the engine did.
        Vector3 startPos = a.position;
        Vector3 endPos = GroundedPivot(a, cx, cy);
        var route = new System.Collections.Generic.List<Vector3> { startPos };
        var engPath = MatchingEnginePath(fromCx, fromCy, cx, cy);
        if (engPath != null)
        {
            // ground offset that maps the from-cell's CellToWorld to the actor's current grounded pivot,
            // reused for every intermediate cell so the whole walk stays foot-planted on the flat floor.
            Vector3 fromCellW = CellToWorld(fromCx, fromCy);
            Vector3 gOff = new Vector3(startPos.x - fromCellW.x, startPos.y - fromCellW.y, startPos.z - fromCellW.z);
            for (int i = 1; i < engPath.Count; i++) route.Add(CellToWorld(engPath[i][0], engPath[i][1]) + gOff);
            // A rest walk route is single-use — clear it once consumed so a later same-endpoints glide can't
            // reuse a stale polyline (the combat `_lastPath` is refreshed every surface, so it self-corrects).
            if (engPath == _walkPath) _walkPath.Clear();
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
        var walkAnim = go.GetComponentInChildren<Animator>();
        bool cd = _ctrlDriven.Contains(id);   // #anim-pack: controller-driven -> drive Speed, not a walk graph
        AnimationClip walkClip = null;
        UnityEngine.Playables.PlayableGraph walkGraph = default; bool haveGraph = false; bool sampleWalk = false;
        if (cd)
        {
            // #anim-pack: the shared controller's Locomotion blend plays walk/run off the Speed float; the
            // Animator self-updates in the player loop, so there is NO per-frame graph Evaluate here. Speed is
            // set to the glide's planar rate so the blend picks a stride matching the cell->cell tween.
            if (walkAnim != null) walkAnim.SetFloat("Speed", GlideSpeed);
        }
        else
        {
            walkClip = FindOwnClip(id, "walk", "run");
            if (walkClip == null && walkAnim != null && walkAnim.avatar != null && walkAnim.avatar.isHuman) walkClip = DonorWalk();
            KillIdleGraph(id);   // #idle-persist: stop the idle graph so it doesn't fight the walk graph over the Animator
            if (walkClip != null)
            {
                if (walkAnim != null && walkAnim.avatar != null) { walkGraph = MakeClipGraph(walkAnim, walkClip, "Walk_" + a.name); haveGraph = true; _walkGraphOf[id] = walkGraph; }
                else sampleWalk = true;   // no Animator/avatar -> legacy rig -> direct curve sample is the only path
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
            if (haveGraph) walkGraph.Evaluate(Time.deltaTime);
            else if (sampleWalk) { float len = walkClip.length > 0.01f ? walkClip.length : 1f; walkClip.SampleAnimation(go, animT % len); }
            MoveActorAndShadows(a, p);
            yield return null;
        }
        // arrive: snap exact, tear down walk, return to a grounded idle facing the camera.
        MoveActorAndShadows(a, endPos);
        if (haveGraph) KillWalkGraph(id);   // registry-tracked destroy (#1451-review P2)
        if (cd) { if (walkAnim != null) walkAnim.SetFloat("Speed", 0f); }   // #anim-pack: Speed 0 -> controller's Idle
        else PoseIdle(go);
        var cam = Camera.main; float camYaw = cam != null ? cam.transform.eulerAngles.y : 45f;
        a.rotation = Quaternion.Euler(pitchX, camYaw + 180f, 0f);
        MoveActorAndShadows(a, GroundedPivot(a, cx, cy));   // re-ground the final idle pose (idempotent)
        _glide.Remove(id);
    }

    // Pose `go` to a neutral idle by (re)starting its PERSISTENT idle graph — the same idle SpawnActor
    // establishes, so a glide/attack returns to it at rest. See PlayIdleGraph for why a live graph (not a
    // one-shot pose) is required to hold a skinned pose.
    void PoseIdle(GameObject go)
    {
        if (go == null) return;
        string nm = go.name;
        string id = nm.StartsWith("Actor_") ? nm.Substring(6) : nm;
        // #anim-pack: a controller-driven humanoid returns to Idle via the controller — Rebind clears any
        // terminal Death / one-shot state (this is the revive path's stand-up), Update(0f) settles the pose,
        // and Speed 0 selects Idle in the Locomotion blend. The glide/attack arrival paths set Speed directly
        // (no Rebind), so this heavier reset only runs on first-sight/settle/revive.
        if (_ctrlDriven.Contains(id))
        {
            var an = go.GetComponentInChildren<Animator>();
            if (an != null) { an.Rebind(); an.Update(0f); an.SetFloat("Speed", 0f); }
            return;
        }
        string fbx; _fbxOf.TryGetValue(id, out fbx);
        PlayIdleGraph(go, id, ResolveIdleClip(id, fbx, go));
    }

    // Resolve the best neutral-idle clip for an actor: its OWN idle (model fbx OR separate moveset, dual-source
    // FindOwnClip), else the model's first embedded clip, else the goblin donor idle retargeted onto a clipless
    // HUMANOID rig. Null only for a legacy no-clip rig.
    AnimationClip ResolveIdleClip(string id, string fbx, GameObject go)
    {
        var clip = FindOwnClip(id, "idle");
        if (clip == null)
        {
            var b = Bundle();
            if (b != null && !string.IsNullOrEmpty(fbx))
                foreach (var c in b.LoadAssetWithSubAssets<AnimationClip>(fbx))
                {
                    if (c == null || c.name.StartsWith("__")) continue;
                    clip = c; break;   // first non-__ model clip
                }
        }
        if (clip == null)
        {
            var anim = go != null ? go.GetComponentInChildren<Animator>() : null;
            if (anim != null && anim.avatar != null && anim.avatar.isHuman) clip = DonorIdle();
        }
        return clip;
    }

    // #idle-persist ROOT-CAUSE FIX (bone- + BakeMesh-verified on the box): a one-shot Evaluate CANNOT hold a
    // SKINNED pose — a runtime actor's Animator has an avatar but no controller, so on the next frame either it
    // reverts to the bind (T/A) pose (if left enabled) or its GPU skinning freezes at bind (if disabled). The
    // walk glide renders correctly because it drives a LIVE graph every frame; idle must do the same. This
    // starts (or restarts) a persistent idle graph the Update loop evaluates each frame — the clip loops, so
    // the actor breathes. GlideTo/LungeCo KillIdleGraph before driving walk/attack, then PoseIdle restarts it.
    void PlayIdleGraph(GameObject go, string id, AnimationClip clip)
    {
        if (go == null || clip == null || string.IsNullOrEmpty(id)) return;
        var anim = go.GetComponentInChildren<Animator>();
        if (anim == null || anim.avatar == null) { SampleClipRuntime(go, clip, 0f); return; }   // legacy rig
        KillIdleGraph(id);
        anim.enabled = true; anim.Rebind();   // clear stale binding so the fresh graph output applies
        var g = UnityEngine.Playables.PlayableGraph.Create("Idle_" + go.name);
        var cp = UnityEngine.Animations.AnimationClipPlayable.Create(g, clip);
        var op = UnityEngine.Animations.AnimationPlayableOutput.Create(g, "Out", anim);
        UnityEngine.Playables.PlayableOutputExtensions.SetSourcePlayable(op, cp);
        UnityEngine.Playables.PlayableExtensions.SetTime(cp, clip.length * 0.5f);   // start settled, not at bind-like frame 0
        g.Evaluate(0f);
        _idleGraphOf[id] = g;
    }
    void KillIdleGraph(string id)
    {
        if (_idleGraphOf.TryGetValue(id, out var g)) { if (g.IsValid()) g.Destroy(); _idleGraphOf.Remove(id); }
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
        // UNIFY-THE-FRAMES truth overlay (playtest-#9 instrument): G toggles engine-truth rendering over
        // the plate — every cell's floor diamond colored by its live state (walkable/impassable/door) +
        // wireframes of the active occluder volumes. The permanent "is paint lying?" check: no alignment
        // claim ships without a screenshot of this overlay agreeing with the paint.
        // gated: QA channel enabled, env-armed, or a debug build — never a bare release toggle
        // (evaos review on #1575: an unguarded G in a shipped player is a griefable debug surface).
        if ((_qaClicks != null || Debug.isDebugBuild || System.Environment.GetEnvironmentVariable("WORLDOS_TRUTH_OVERLAY") != null)
            && Input.GetKeyDown(KeyCode.G)) { _truthOverlay = !_truthOverlay; Debug.Log("[CSC] truth overlay " + (_truthOverlay ? "ON" : "OFF")); }
        // #idle-persist: drive every resting actor's persistent idle graph so its skinned pose renders (and
        // breathes) each frame — the walk/attack coroutines Evaluate their own graphs, so a gliding/attacking
        // actor has no idle graph here (KillIdleGraph removed it) until PoseIdle restarts it at rest.
        if (_idleGraphOf.Count > 0)
            foreach (var kv in _idleGraphOf) if (kv.Value.IsValid()) kv.Value.Evaluate(Time.deltaTime);
        // #Phase4: advance the advisory fade clock regardless of poll/POST state.
        if (!string.IsNullOrEmpty(_advMsg)) _advT += Time.deltaTime;
        // #1463: advance the onboarding-hint fade clock after the first action; drive the stage flicker.
        if (_acted) _actedT += Time.deltaTime;
        if (_flickerActive || _glowMats != null) UpdateStageFlicker();
        // owner playtest #4 (B): pulse the door glow(s) + billboard their labels.
        UpdateDoorGlow();
        // #Phase3: overlay toggle (G) + hover run independent of the click gate below.
        if (Input.GetKeyDown(KeyCode.G)) ToggleOverlay();
        if (_overlayOn) UpdateOverlayHover();
        // WALKABLE-SLICE-V1 (item 5): the parley panel captures input while open — Esc closes it (a world
        // click is gated out below so the party never walks underneath the panel).
        if (_parleyOpen) { if (Input.GetKeyDown(KeyCode.Escape)) CloseParley(); }
        // WALKABLE-SLICE-V1 (item 4): F starts a fight in place from rest mode (onboarding affordance).
        else if (_restMode && !_busy && Input.GetKeyDown(KeyCode.F)) StartCoroutine(PostStartCombat());
        // #anim-combat: world-space HP bars follow + billboard their actor; the active-turn combatant's ring
        // pulses. Both run every frame regardless of the click/poll gate.
        UpdateHpBars();
        UpdateTurnPulse();
        if (_busy) return;
        // #1466: drain at most one queued QA click per frame on the MAIN thread (Camera/raycast/coroutines
        // AND Screen.width/height are main-thread only). Same _busy gate as a real click. A cell request
        // runs the shared HandleCell validation+POST; a viewport request runs the full raycast first.
        if (_qaClicks != null)
        {
            _screenW = Screen.width; _screenH = Screen.height;   // cache for the off-thread /health
            // #1583: cache the camera pose + origin viewport for the off-thread /debug walkability probe
            // (Camera is main-thread only). Runs every QA frame so /debug is fresh even before any click.
            var _dcam = Camera.main;
            if (_dcam != null)
            {
                _dbgCamOrtho = _dcam.orthographic ? _dcam.orthographicSize : -1f;
                var _de = _dcam.transform.eulerAngles; _dbgCamRx = _de.x; _dbgCamRy = _de.y; _dbgCamRz = _de.z;
                var _dp = _dcam.transform.position; _dbgCamPx = _dp.x; _dbgCamPy = _dp.y; _dbgCamPz = _dp.z;
                var _dov = _dcam.WorldToViewportPoint(Vector3.zero); _dbgOriginVX = _dov.x; _dbgOriginVY = _dov.y;
                _dbgCamValid = true;
                // #1582: occluder count (0 == the latent zero-occluder path), plate-room match, and
                // the party actor's own viewport point (the client-side visual-registration signal).
                _dbgOccCount = _occRoot != null ? _occRoot.transform.childCount : 0;
                _dbgPlateLocMatch = (_plateBoxesLocId == _locId);
                if (!string.IsNullOrEmpty(_qaActorId))
                {
                    Transform _da = FindActor(_qaActorId);
                    if (_da != null)
                    {
                        var _dav = _dcam.WorldToViewportPoint(_da.position);
                        _dbgActorVX = _dav.x; _dbgActorVY = _dav.y; _dbgActorValid = true;
                    }
                    else _dbgActorValid = false;
                }
            }
            // /shot: capture the app's OWN framebuffer (countdown lets the request thread return first).
            if (_qaShot > 0 && --_qaShot == 0)
                ScreenCapture.CaptureScreenshot(_qaShotPathNext ?? _qaShotPath);   // #1582: numbered file
            if (_qaClicks.TryDequeue(out var qc))
            {
                _dbgDeq++;
                if (qc.cell) HandleCell(qc.c, qc.r);
                else HandleClickAt(new Vector3(qc.vx * Screen.width, qc.vy * Screen.height, 0f));
                return;
            }
        }
        if (Input.GetMouseButtonDown(0) && !_parleyOpen) HandleClick();   // WALKABLE-SLICE-V1: parley panel eats world clicks
    }

    // #Phase4: a short, fading amber note near the top of the screen. IMGUI (no Canvas needed); alpha fades
    // over AdvisoryHold. A drop shadow keeps it legible over the painterly board.
    void OnGUI()
    {
        DrawOnboardHint();   // #1463 (task 1): whose-turn + affordance hint; fades after the first action
        DrawParleyPanel();   // WALKABLE-SLICE-V1 (item 5): the in-player parley panel when talking to an NPC
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

    // A real mouse click -> the SAME coordinate-level handler the QA input channel (#1466) drives, so
    // the raycast->cell->pre-validation->POST product path is identical whether a human or the T3 gate
    // clicks. Input.mousePosition is screen pixels, bottom-left origin (what ScreenPointToRay wants).
    void HandleClick() { HandleClickAt(Input.mousePosition); }

    // Minimal input: raycast the click onto the floor plane -> cell -> POST the existing /move kinds
    // ONLY (move_to_cell, or an on-turn attack when the clicked cell holds the foe). Payload mirrors
    // the viewer driver (qa/drive_gfx_combat.py) exactly. `screenPoint` is screen pixels (bottom-left
    // origin); the QA channel converts its normalized viewport coord to this via Screen.width/height.
    void HandleClickAt(Vector3 screenPoint)
    {
        var cam = Camera.main; if (cam == null) return;
        Ray ray = cam.ScreenPointToRay(screenPoint);
        if (Mathf.Abs(ray.direction.y) < 1e-4f) return;
        float tt = (FloorY - ray.origin.y) / ray.direction.y; if (tt < 0) return;
        Vector3 hit = ray.origin + ray.direction * tt;
        if (!WorldToCell(hit, out int c, out int r)) return;
        HandleCell(c, r);
    }

    // The cell-level half of a click: identical rest-vs-combat pre-validation + POST whether the cell
    // came from a mouse raycast (HandleClickAt) or the QA input channel's cell path (#1466). This is the
    // "SAME HandleClick validation path" the QA channel runs — NOT a DoMove/DoAttack shortcut: it still
    // honors _restMode, the mover check, and the #1441 impassable/occupied pre-filter (FlashReject).
    void HandleCell(int c, int r)
    {
        int key = CellKey(c, r);
        _dbgActed++;
        _dbgLast = "cell(" + c + "," + r + ") rest=" + _restMode + " foe=(" + _foeX + "," + _foeY + ")'" + _foeId + "' imp=" + (_impassable.Contains(key) ? "Y" : "n") + " occ=" + (_occupied.Contains(key) ? "Y" : "n");
        // W6.2 (#1461) REST MODE: no combat signals -> the click WALKS a party member to the cell via the
        // engine's `walk_to` verb (the walk_to_cell /move intent), NOT the combat move. Same #1441
        // pre-validation as combat — a blocked/occupied cell flashes a red ring instead of a doomed POST
        // (rest_blocked_cells folds standers into `impassable`, so the _impassable check rejects a cell a
        // person stands on too). No known party mover (no rest party token yet) -> reject rather than POST
        // a moverless walk the engine would 400. This whole branch is inert on a combat surface (_restMode
        // false), so the combat attack/move path below is byte-identical.
        if (_restMode)
        {
            // WALKABLE-SLICE-V1 (item 2): a click on a present NPC's cell TALKS (parley_approach) — the engine
            // walks the lead PC adjacent and opens the parley; the in-player panel (item 5) renders it. Checked
            // BEFORE the occupied gate because an NPC cell is `occupied` (it holds the NPC token).
            if (_npcAtCell.TryGetValue(key, out string npcId) && !string.IsNullOrEmpty(npcId)) { StartCoroutine(PostParley(npcId)); return; }
            // WALKABLE-SLICE-V1 (item 3): a click on an authored doorway cell CROSSES it — walk the lead PC onto
            // the doorway then POST cross_door (relocates the party to the linked room; item 6 swaps the plate on
            // the re-fetched surface). Checked BEFORE the walkability gate so a door on the room edge still crosses.
            if (_doorTo.ContainsKey(key)) { StartCoroutine(PostCrossDoor(c, r)); return; }
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

    // ---- #1466 QA input channel ------------------------------------------------------------------
    // Localhost HttpListener that turns a click request into a synthetic click fed through the SAME
    // validation+POST path a human click takes. OFF unless WORLDOS_QA_INPUT=1 (byte-identical player
    // otherwise). Port from WORLDOS_QA_INPUT_PORT (default 8971), mirroring the WORLDOS_ENGINE_BASE_URL
    // env-contract style. Two request shapes on POST /click (queued; resolved next frame on the main thread):
    //   {"c":9,"r":8}        -> HandleCell(c,r)  — the ROBUST path QA uses. Skips only the raycast; still
    //                           runs the rest-vs-combat + #1441 impassable/occupied pre-validation + POST.
    //                           No pixel/titlebar/aspect calibration to get wrong (the SCK capture includes
    //                           the macOS titlebar, so a captured-pixel viewport never matched Unity's).
    //   {"vx":0..1,"vy":0..1} -> HandleClickAt  — viewport coord (BOTTOM-LEFT origin), runs the full raycast
    //                           too; kept as real product-fidelity input for a correctly-calibrated caller.
    //   GET /health          -> {"ok":true}
    [System.Serializable] class QaClick { public int c = -1; public int r = -1; public float vx = float.NaN; public float vy = float.NaN; }
    struct QaCmd { public bool cell; public int c; public int r; public float vx; public float vy; }
    volatile int _qaShot;                                     // /shot countdown -> main-thread framebuffer capture
    // NOT a field initializer: Application.persistentDataPath throws if read during type/MonoBehaviour
    // construction (adversarial-invariant-verify, #1575). Assigned once in StartQaInput (main thread)
    // BEFORE the listener thread starts, so the off-thread responder reads an already-set value.
    string _qaShotPath;
    ConcurrentQueue<QaCmd> _qaClicks;
    HttpListener _qaListener;
    Thread _qaThread;
    volatile bool _qaStop;
    // Unity's render dims, cached on the MAIN thread (Screen.* is main-thread only) so the off-thread
    // /health handler can report them. A pixel-space caller (the T3 palette) needs Screen.height to undo
    // the macOS titlebar the SCK capture includes (captured height != Screen.height).
    volatile int _screenW, _screenH;
    // #1466 diagnostics: a no-activation player's Player.log is buffered and unreliable, so the channel
    // exposes its own counters via GET /debug (enqueued/dequeued/acted + the last branch HandleCell took
    // + surface-derived state). Single-writer per field in practice; volatile is enough for a QA probe.
    volatile int _dbgEnq, _dbgDeq, _dbgActed, _dbgSurf;
    volatile string _dbgLast = "none";
    // #1583 walkability gate: the /debug channel also reports the runtime CAMERA POSE + the viewport
    // position of world origin, so qa/walk_test.py can assert Camera.main == build_room_unified's
    // contract rig (Euler(30,45,0), pos=-(fwd)*80, aim at origin, pinned ortho). Cached on the MAIN
    // thread each QA frame (Camera is main-thread only), read by the off-thread /debug responder. The
    // valid-flag means an un-cached field is OMITTED from the JSON (walk_test then reads 'camera pose
    // unavailable' and fails LOUD) — never emitted as a misleading 0 that would read as a wrong ortho.
    volatile bool _dbgCamValid;
    volatile float _dbgCamOrtho, _dbgCamRx, _dbgCamRy, _dbgCamRz, _dbgCamPx, _dbgCamPy, _dbgCamPz, _dbgOriginVX, _dbgOriginVY;
    // #1582 walkability-gate fattening (sidecar review): occluder count (0 == the latent
    // zero-occluder path — an instant red for the walk gate), whether the loaded plate boxes belong
    // to the CURRENT room, and the party actor's own viewport position (closes the visual-
    // registration loop without pixel-diff when available). Cached main-thread like the camera pose.
    volatile int _dbgOccCount = -1;
    volatile bool _dbgPlateLocMatch;
    volatile bool _dbgActorValid;
    volatile float _dbgActorVX, _dbgActorVY;
    volatile string _qaActorId = "";          // first cast token id (set on ApplyJson, main thread)
    int _qaShotCounter;                        // monotonic /shot id -> numbered files, no overwrite races
    volatile string _qaShotPathNext;           // the path the NEXT countdown capture writes

    void StartQaInput()
    {
        int port = 8971;
        string p = System.Environment.GetEnvironmentVariable("WORLDOS_QA_INPUT_PORT");
        if (!string.IsNullOrEmpty(p) && int.TryParse(p, out int pv) && pv > 0) port = pv;
        // main thread — safe to read persistentDataPath here (never in a field initializer, #1575)
        _qaShotPath = System.IO.Path.Combine(Application.persistentDataPath, "wos_shot.png");
        _qaClicks = new ConcurrentQueue<QaCmd>();
        try
        {
            _qaListener = new HttpListener();
            _qaListener.Prefixes.Add("http://127.0.0.1:" + port + "/");
            _qaListener.Start();
        }
        catch (System.Exception e)
        { Debug.LogWarning("[CSC] QA input listener failed to bind :" + port + " — " + e.Message); _qaListener = null; return; }
        _qaThread = new Thread(QaListenLoop) { IsBackground = true, Name = "CSC-QAInput" };
        _qaThread.Start();
        Debug.Log("[CSC] QA input channel LISTENING on http://127.0.0.1:" + port + "/click (cell {c,r} or viewport {vx,vy} -> HandleCell/HandleClickAt)");
    }

    // Runs OFF the Unity main thread: it may ONLY do pure/thread-safe work (parse + Mathf.Clamp01 + queue).
    // NO Unity API here (Screen/Camera/coroutines) — those happen when Update() drains the queue.
    void QaListenLoop()
    {
        while (!_qaStop && _qaListener != null && _qaListener.IsListening)
        {
            HttpListenerContext ctx;
            try { ctx = _qaListener.GetContext(); }
            catch { break; }   // listener stopped/disposed -> exit the loop cleanly
            try
            {
                string body = "";
                if (ctx.Request.HasEntityBody)
                    using (var sr = new System.IO.StreamReader(ctx.Request.InputStream, ctx.Request.ContentEncoding)) body = sr.ReadToEnd();
                string resp = "{\"ok\":false}";
                if (ctx.Request.Url.AbsolutePath == "/click" && ctx.Request.HttpMethod == "POST")
                {
                    var q = JsonUtility.FromJson<QaClick>(body);
                    if (q != null && q.c >= 0 && q.r >= 0)
                    { _qaClicks.Enqueue(new QaCmd { cell = true, c = q.c, r = q.r }); _dbgEnq++; resp = "{\"ok\":true}"; }
                    else if (q != null && !float.IsNaN(q.vx) && !float.IsNaN(q.vy))
                    { _qaClicks.Enqueue(new QaCmd { cell = false, vx = Mathf.Clamp01(q.vx), vy = Mathf.Clamp01(q.vy) }); _dbgEnq++; resp = "{\"ok\":true}"; }
                }
                else if (ctx.Request.Url.AbsolutePath == "/shot" && ctx.Request.HttpMethod == "POST")
                {
                    // UNIFY-THE-FRAMES QA verb: in-app framebuffer capture on the MAIN thread next frame —
                    // no desktop/SCK capture, no titlebar calibration, no privacy exposure of other windows.
                    // POST-only (parity with /click); writes under persistentDataPath, never a shared /tmp
                    // fixed path (symlink hardening — evaos review on #1575).
                    // #1582: NUMBERED files (wos_shot_<id>.png) — a fixed overwritten path races at sweep
                    // scale (half-written/stale copies). A new numbered file appears only when written, so
                    // callers poll its existence+size — race-free by construction. Response carries path+id.
                    int _sid = System.Threading.Interlocked.Increment(ref _qaShotCounter);
                    string _spath = _qaShotPath.Replace("wos_shot.png", "wos_shot_" + _sid + ".png");
                    _qaShotPathNext = _spath;
                    _qaShot = 2;
                    resp = "{\"ok\":true,\"path\":\"" + _spath.Replace("\\", "/") + "\",\"id\":" + _sid + "}";
                }
                else if (ctx.Request.Url.AbsolutePath == "/health")
                    resp = "{\"ok\":true,\"screenW\":" + _screenW + ",\"screenH\":" + _screenH + "}";
                else if (ctx.Request.Url.AbsolutePath == "/debug")
                {
                    var sb = new System.Text.StringBuilder();
                    sb.Append("{\"ok\":true,\"enq\":").Append(_dbgEnq).Append(",\"deq\":").Append(_dbgDeq)
                      .Append(",\"acted\":").Append(_dbgActed).Append(",\"surf\":").Append(_dbgSurf)
                      .Append(",\"busy\":").Append(_busy ? "true" : "false")
                      .Append(",\"last\":\"").Append(_dbgLast).Append("\"");
                    if (_dbgCamValid)  // #1583: camera pose for qa/walk_test.py (omitted on an old build)
                    {
                        var ic = System.Globalization.CultureInfo.InvariantCulture;
                        sb.Append(",\"camOrtho\":").Append(_dbgCamOrtho.ToString("0.####", ic))
                          .Append(",\"camRx\":").Append(_dbgCamRx.ToString("0.####", ic))
                          .Append(",\"camRy\":").Append(_dbgCamRy.ToString("0.####", ic))
                          .Append(",\"camRz\":").Append(_dbgCamRz.ToString("0.####", ic))
                          .Append(",\"camPx\":").Append(_dbgCamPx.ToString("0.####", ic))
                          .Append(",\"camPy\":").Append(_dbgCamPy.ToString("0.####", ic))
                          .Append(",\"camPz\":").Append(_dbgCamPz.ToString("0.####", ic))
                          .Append(",\"originVX\":").Append(_dbgOriginVX.ToString("0.#####", ic))
                          .Append(",\"originVY\":").Append(_dbgOriginVY.ToString("0.#####", ic));
                        // #1582 fattening: occluder count (0 == the latent zero-occluder path),
                        // plate-room match, and the party actor's viewport position when known.
                        sb.Append(",\"occCount\":").Append(_dbgOccCount)
                          .Append(",\"plateLocMatch\":").Append(_dbgPlateLocMatch ? "true" : "false");
                        if (_dbgActorValid)
                            sb.Append(",\"actorVX\":").Append(_dbgActorVX.ToString("0.#####", ic))
                              .Append(",\"actorVY\":").Append(_dbgActorVY.ToString("0.#####", ic));
                    }
                    sb.Append("}");
                    resp = sb.ToString();
                }
                byte[] buf = System.Text.Encoding.UTF8.GetBytes(resp);
                ctx.Response.ContentType = "application/json";
                ctx.Response.ContentLength64 = buf.Length;
                ctx.Response.OutputStream.Write(buf, 0, buf.Length);
                ctx.Response.OutputStream.Close();
            }
            catch { /* one bad request must never kill the QA loop */ }
        }
    }

    void StopQaInput()
    {
        _qaStop = true;
        try { if (_qaListener != null) { _qaListener.Stop(); _qaListener.Close(); } } catch { }
        _qaListener = null;
    }

    void OnDestroy() { StopQaInput(); }
    void OnApplicationQuit() { StopQaInput(); }

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
    // #1461: in REST mode a programmatic move is a WALK (walk_to_cell), not the combat move_to_cell —
    // so a headless/QA driver exercises the same rest lane a mouse click does (a move_to_cell at rest is
    // engine-rejected as a combat move). Combat mode is unchanged (PostMove).
    public void DoMove(int x, int y) { if (!_busy) StartCoroutine(_restMode ? PostWalk(x, y) : PostMove(x, y)); }
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
            // #1466: the SAME elapsed-time watchdog the GET path uses — a hung /move POST would otherwise
            // wait forever on SendWebRequest with _busy=true, and PollLoop only fetches when !_busy, so the
            // renderer could never recover. The watchdog guarantees this coroutine terminates and _busy is
            // released below on EVERY path (success, reject, or timeout/abort).
            var op = req.SendWebRequest();
            float t0 = Time.realtimeSinceStartup;
            bool timedOut = false;
            while (!op.isDone)
            {
                if (Time.realtimeSinceStartup - t0 > FetchTimeout) { req.Abort(); timedOut = true; break; }
                yield return null;
            }
            if (timedOut) Debug.LogWarning("[CSC] /walk TIMEOUT (aborted after " + FetchTimeout.ToString("0.#") + "s) — PollLoop will retry");
            else if (!Ok(req)) Debug.LogWarning("[CSC] /walk failed: " + req.error + " body=" + req.downloadHandler.text);
            else
            {
                // walk_to_cell returns {ok, walked, character_id, from, to, path} (NOT the combat surface).
                // A rejected walk ({ok:false, reason}) toasts its reason; a success is reflected by the
                // re-fetch below (the engine wrote stage_cell; we never render a predicted route).
                MoveResp resp = null;
                try { resp = JsonUtility.FromJson<MoveResp>(req.downloadHandler.text); }
                catch (System.Exception e) { Debug.LogWarning("[CSC] walk parse: " + e.Message); }
                if (resp != null && !resp.ok && !string.IsNullOrEmpty(resp.reason)) ShowAdvisory(resp.reason);
                else
                {
                    // #1544: capture the engine-confirmed walk route from the response `path` BEFORE the
                    // re-fetch below. The surface never carries this route in rest (its `lastPath` reads
                    // combat.last_move_path, which walk_to_cell doesn't write), so without this the glide
                    // straight-lined through prop cells. GlideTo (via MatchingEnginePath) now follows it.
                    ParseWalkPath(req.downloadHandler.text);
                    MarkActed();   // #1463: an accepted walk retires the onboarding hint
                }
            }
        }
        _busy = false;   // always released (the watchdog guarantees the loop above terminates)
        yield return Fetch();   // re-render off the engine's fresh surface (stage_cell now updated)
    }

    // #1544: parse a rest `walk_to_cell` response's engine-confirmed `path` ([[x,y],... incl. the from-cell)
    // into _walkPath so the imminent glide follows the routed polyline instead of straight-lining through
    // props. Uses the runtime Json map parser (JsonUtility can't model a list-of-[x,y]), mirroring
    // ParseSurfaceExtras' lastPath parse. Absent/corrupt `path` leaves _walkPath empty (straight-line
    // fallback, engine still authoritative). Cleared first so a rejected/pathless walk can't reuse a stale one.
    void ParseWalkPath(string json)
    {
        _walkPath.Clear();
        try
        {
            var root = Json.Parse(json) as System.Collections.Generic.Dictionary<string, object>;
            if (root == null || !root.ContainsKey("path")) return;
            var lp = root["path"] as System.Collections.Generic.List<object>;
            if (lp == null) return;
            foreach (var ce in lp) { var cell = ce as System.Collections.Generic.List<object>; if (cell == null || cell.Count < 2) continue; _walkPath.Add(new[] { System.Convert.ToInt32(cell[0]), System.Convert.ToInt32(cell[1]) }); }
        }
        catch (System.Exception e) { Debug.LogWarning("[CSC] walk-path parse: " + e.Message); }
    }

    // WALKABLE-SLICE-V1: a minimal POST /move for the rest-mode intents (walk-to-door, cross_door,
    // parley_approach, start_combat) whose responses are NOT the combat surface (so they don't re-render
    // inline — the caller re-fetches). Mirrors PostWalk's elapsed-time watchdog so a hung POST can never
    // wedge the poll loop. `onReject(reason)` (optional) fires with the engine's reason on an {ok:false}.
    IEnumerator PostSimple(string body, string tag, System.Action<string> onReject = null)
    {
        using (var req = new UnityWebRequest(ViewerUrl + "/move", "POST"))
        {
            req.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(body));
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout = 8;
            var op = req.SendWebRequest();
            float t0 = Time.realtimeSinceStartup;
            bool timedOut = false;
            while (!op.isDone)
            {
                if (Time.realtimeSinceStartup - t0 > FetchTimeout) { req.Abort(); timedOut = true; break; }
                yield return null;
            }
            if (timedOut) Debug.LogWarning("[CSC] /" + tag + " TIMEOUT (aborted) — PollLoop will retry");
            else if (!Ok(req)) Debug.LogWarning("[CSC] /" + tag + " failed: " + req.error + " body=" + req.downloadHandler.text);
            else
            {
                MoveResp resp = null;
                try { resp = JsonUtility.FromJson<MoveResp>(req.downloadHandler.text); }
                catch (System.Exception e) { Debug.LogWarning("[CSC] " + tag + " parse: " + e.Message); }
                if (resp != null && !resp.ok && !string.IsNullOrEmpty(resp.reason)) { if (onReject != null) onReject(resp.reason); }
            }
        }
    }

    // WALKABLE-SLICE-V1 (item 3): cross an authored doorway. Walk the lead PC onto the doorway first
    // (engine paths there; best-effort), then POST cross_door which relocates the party to the linked
    // room (engine-gated: rejects cleanly if combat is unresolved / it isn't a real doorway). Mirrors the
    // browser onRestDoorWalk:360 -> crossDoor:167. The re-fetch renders the NEW room and item 6 swaps the plate.
    IEnumerator PostCrossDoor(int x, int y)
    {
        _busy = true;
        if (!string.IsNullOrEmpty(_restMoverId))
            yield return PostSimple("{\"kind\":\"walk_to_cell\",\"character_id\":\"" + _restMoverId + "\",\"x\":" + x + ",\"y\":" + y + ",\"campaign\":\"" + CampaignId + "\"}", "walk-to-door");
        string reason = null;
        yield return PostSimple("{\"kind\":\"cross_door\",\"x\":" + x + ",\"y\":" + y + ",\"campaign\":\"" + CampaignId + "\"}", "cross_door", r => reason = r);
        if (!string.IsNullOrEmpty(reason)) ShowAdvisory(reason);
        else MarkActed();
        _busy = false;
        yield return Fetch();   // re-render the new room's surface (plate swap resolves here, item 6)
    }

    // WALKABLE-SLICE-V1 (item 2): talk to an NPC. POST parley_approach (the engine walks the lead PC
    // adjacent + opens the parley in-process), re-fetch so the walked tokens glide to the confirmed cells,
    // then open the in-player parley panel bound to this NPC (item 5). Browser: screen-combat.jsx:373.
    IEnumerator PostParley(string npcId)
    {
        _busy = true;
        string reason = null;
        yield return PostSimple("{\"kind\":\"parley_approach\",\"target_id\":\"" + npcId + "\",\"character_id\":\"" + _restMoverId + "\",\"campaign\":\"" + CampaignId + "\"}", "parley_approach", r => reason = r);
        _busy = false;
        if (!string.IsNullOrEmpty(reason)) { ShowAdvisory(reason); yield break; }
        MarkActed();
        yield return Fetch();          // glide the walked tokens to the engine-confirmed cells
        OpenParley(npcId);             // item 5: bind + fetch /parley-surface, show the panel
    }

    // WALKABLE-SLICE-V1 (item 4): start a fight in place from rest mode. POST a start_combat intent (the
    // additive viewer resolver picks the combatants — party + present NPCs — and seeds initiative where
    // they rested; the engine stays SOLE WRITER). The next surface arrives combat-mode, which the player
    // already consumes. Triggered by the F key in rest mode (see Update). Engine untouched.
    IEnumerator PostStartCombat()
    {
        _busy = true;
        string reason = null;
        yield return PostSimple("{\"kind\":\"start_combat\",\"campaign\":\"" + CampaignId + "\"}", "start_combat", r => reason = r);
        _busy = false;
        if (!string.IsNullOrEmpty(reason)) ShowAdvisory(reason);
        else MarkActed();
        yield return Fetch();          // re-render as the combat surface
    }

    // ---- WALKABLE-SLICE-V1 (item 5) in-player parley panel ------------------------------------------
    // A minimal parchment HUD panel that opens on talking to an NPC (item 2). Consumes
    // GET /parley-surface?npc=<id> for the speaker header, and offers a reply field posted via the same
    // /move `say` lane the browser dialogue uses. Pure consumer; scrolling text + one input, parchment-
    // styled to match the onboarding HUD. Closed with Esc or the Leave button.
    string _parleyNpc = "";
    bool _parleyOpen = false;
    string _parleyHeader = "";
    string _parleyBody = "";
    string _parleyReply = "";
    Vector2 _parleyScroll;
    GUIStyle _parleyTitleStyle, _parleyBodyStyle;
    Texture2D _parchTex;

    void OpenParley(string npcId)
    {
        _parleyNpc = npcId;
        _parleyOpen = true;
        _parleyReply = "";
        _parleyScroll = Vector2.zero;
        // Speaker name from the surface's name cache (populated in ApplySurf); the fetch enriches the header.
        string nm; _parleyHeader = (_nameOf.TryGetValue(npcId, out nm) && !string.IsNullOrEmpty(nm)) ? nm : npcId;
        _parleyBody = "…";
        StartCoroutine(LoadParleySurface(npcId));
    }

    void CloseParley() { _parleyOpen = false; _parleyNpc = ""; }

    // Fetch GET /parley-surface?npc=<id> and read a display header + any prompt defensively (the parley
    // surface is a skill/DC menu, not a chat log — so this reads a name/header if present and otherwise
    // keeps the cached name). A failed fetch leaves the cached name + a generic invitation; never fatal.
    IEnumerator LoadParleySurface(string npcId)
    {
        string url = ViewerUrl + "/parley-surface?campaign=" + CampaignId + "&npc=" + UnityWebRequest.EscapeURL(npcId);
        using (var req = UnityWebRequest.Get(url))
        {
            req.timeout = 8;
            yield return req.SendWebRequest();
            if (!Ok(req)) { _parleyBody = "You approach " + _parleyHeader + "."; yield break; }
            try
            {
                var root = Json.Parse(req.downloadHandler.text) as System.Collections.Generic.Dictionary<string, object>;
                if (root != null)
                {
                    // header/name: /parley-surface carries `npc` = {id, name, attitude, disposition, ...}
                    // (verified against build_parley_surface). `actor` is the PC NAME string (not a dict) and
                    // `free_form` is a BOOL flag (not a prompt), so neither carries a header/opening line —
                    // read npc.name, else keep the cached stage-token name.
                    var npc = root.ContainsKey("npc") ? root["npc"] as System.Collections.Generic.Dictionary<string, object> : null;
                    string hn = npc != null && npc.ContainsKey("name") ? npc["name"] as string : null;
                    if (!string.IsNullOrEmpty(hn)) _parleyHeader = hn;
                    // disposition tints the opening line a touch; the free-form conversation happens via the
                    // reply field (POST /move say). No streamed opening line on the surface -> a generic invite.
                    string disp = npc != null && npc.ContainsKey("disposition") ? npc["disposition"] as string : "";
                    _parleyBody = "You approach " + _parleyHeader
                        + (string.IsNullOrEmpty(disp) ? "" : " (" + disp + ")") + ". What do you say?";
                }
            }
            catch (System.Exception e) { Debug.LogWarning("[CSC] parley-surface parse: " + e.Message); _parleyBody = "You approach " + _parleyHeader + "."; }
        }
    }

    // Speak the typed reply via the existing /move `say` lane (the engine advances the conversation). The
    // panel stays open; the DM's reply is not streamed here (out of scope for the slice) — a "…" sent state.
    IEnumerator SpeakParley(string text)
    {
        if (string.IsNullOrWhiteSpace(text)) yield break;
        _busy = true;
        yield return PostSimple("{\"kind\":\"say\",\"text\":\"" + JsonEsc(text) + "\",\"campaign\":\"" + CampaignId + "\"}", "say");
        _busy = false;
        _parleyReply = "";
        _parleyBody = "You said: “" + text + "”";
    }

    // Minimal JSON string escaping for a typed reply (quotes/backslashes/newlines) — the reply is the only
    // user-authored string this client POSTs; every other body is machine-built with safe values.
    static string JsonEsc(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        var b = new System.Text.StringBuilder(s.Length + 8);
        foreach (char ch in s)
        {
            switch (ch)
            {
                case '"': b.Append("\\\""); break;
                case '\\': b.Append("\\\\"); break;
                case '\n': b.Append("\\n"); break;
                case '\r': b.Append("\\r"); break;
                case '\t': b.Append("\\t"); break;
                default: if (ch < 0x20) b.Append(' '); else b.Append(ch); break;
            }
        }
        return b.ToString();
    }

    // A soft parchment fill for the panel background, built once (opaque warm paper).
    Texture2D ParchTex()
    {
        if (_parchTex != null) return _parchTex;
        _parchTex = new Texture2D(4, 4, TextureFormat.RGBA32, false);
        var px = new Color[16]; for (int i = 0; i < 16; i++) px[i] = new Color(0.16f, 0.13f, 0.09f, 0.94f);
        _parchTex.SetPixels(px); _parchTex.Apply();
        return _parchTex;
    }

    // Draw the parley panel (called from OnGUI when open). Parchment card, scrolling body text, a reply
    // input field (Enter speaks), and a Leave button. IMGUI so no Canvas is needed (matches the HUD idiom).
    void DrawParleyPanel()
    {
        if (!_parleyOpen) return;
        if (_parleyTitleStyle == null)
        {
            _parleyTitleStyle = new GUIStyle(GUI.skin.label) { fontSize = 22, fontStyle = FontStyle.Bold, wordWrap = true, normal = { textColor = new Color(1f, 0.90f, 0.62f) } };
            _parleyBodyStyle = new GUIStyle(GUI.skin.label) { fontSize = 16, wordWrap = true, normal = { textColor = new Color(0.95f, 0.94f, 0.88f) } };
        }
        float w = Mathf.Min(560f, Screen.width - 40f), h = Mathf.Min(320f, Screen.height - 60f);
        var panel = new Rect(Screen.width - w - 20f, Screen.height - h - 20f, w, h);
        GUI.DrawTexture(panel, ParchTex());
        GUILayout.BeginArea(new Rect(panel.x + 16f, panel.y + 14f, panel.width - 32f, panel.height - 28f));
        GUILayout.Label(_parleyHeader, _parleyTitleStyle);
        _parleyScroll = GUILayout.BeginScrollView(_parleyScroll, GUILayout.Height(h - 130f));
        GUILayout.Label(_parleyBody, _parleyBodyStyle);
        GUILayout.EndScrollView();
        GUILayout.Space(6f);
        // Enter in the reply field speaks; the field is named so we can detect the keystroke.
        var e = Event.current;
        GUI.SetNextControlName("ParleyReply");
        _parleyReply = GUILayout.TextField(_parleyReply, GUILayout.Height(24f));
        if (e.type == EventType.KeyDown && (e.keyCode == KeyCode.Return || e.keyCode == KeyCode.KeypadEnter)
            && GUI.GetNameOfFocusedControl() == "ParleyReply" && !string.IsNullOrWhiteSpace(_parleyReply))
        { StartCoroutine(SpeakParley(_parleyReply)); e.Use(); }
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Speak", GUILayout.Width(90f)) && !string.IsNullOrWhiteSpace(_parleyReply)) StartCoroutine(SpeakParley(_parleyReply));
        GUILayout.FlexibleSpace();
        if (GUILayout.Button("Leave", GUILayout.Width(90f))) CloseParley();
        GUILayout.EndHorizontal();
        GUILayout.EndArea();
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
            if (resp != null && resp.ok) MarkActed();   // #1463: the first accepted move/attack retires the hint
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
        // #idle-persist: a continuous graph (walk/attack) drives the Animator via Evaluate(dt) every frame,
        // so the Animator must be ENABLED for the whole run. At-rest actors are frozen (Animator disabled +
        // stale binding, see SampleClipRuntime) to hold their idle; enable + Rebind here so this fresh graph's
        // output actually applies (an un-rebound Animator silently no-ops the output). The caller Evaluates the
        // first frame before any yield, so the Rebind's transient bind pose never renders.
        if (anim != null) { anim.enabled = true; anim.Rebind(); }
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
            // One-shot pose (legacy/edge path; the persistent idle uses PlayIdleGraph). Enable + Rebind so the
            // fresh graph output applies; sample a settled frame (frame 0 of many idle clips sits at ~bind).
            // NOTE: do NOT disable the Animator afterward — a disabled Animator freezes the SKINNED render at
            // bind even though the bone transforms move (verified via BakeMesh on the box).
            anim.enabled = true;
            anim.Rebind();
            float sampleT = time > 0.001f ? time : clip.length * 0.5f;
            var g = UnityEngine.Playables.PlayableGraph.Create("Pose_" + go.name);
            var cp = UnityEngine.Animations.AnimationClipPlayable.Create(g, clip);
            UnityEngine.Playables.PlayableExtensions.SetTime(cp, sampleT);
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
        float t = 0f, dur = 1.5f; var tm = g != null ? g.GetComponent<TextMesh>() : null;   // #1482: damage-pop lifetime ~1.5s (rise+fade)
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
        var anim = go.GetComponentInChildren<Animator>();
        bool cd = _ctrlDriven.Contains(id);   // #anim-pack: controller plays the Attack state, then auto-returns
        UnityEngine.Playables.PlayableGraph g = default; bool hg = false;
        if (cd) { if (anim != null) anim.SetTrigger("Attack"); }
        else
        {
            AnimationClip atk = FindOwnClip(id, "attack", "swing");
            KillIdleGraph(id);   // #idle-persist: stop the idle graph while the attack clip drives the Animator
            if (atk != null && anim != null && anim.avatar != null) { g = MakeClipGraph(anim, atk, "Atk_" + a.name); hg = true; }
        }
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
            if (!cd) PoseIdle(go);   // #anim-pack: the controller returns to Locomotion via the Attack->exit transition
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
        KillIdleGraph(id);   // #idle-persist: a downed combatant is prone, not idle — stop the idle graph so it can't stand it back up
        var go = a.gameObject;
        Vector3 home = a.position; Quaternion startRot = a.rotation;
        _downPose[id] = new DownPose { scale = a.localScale, rot = startRot };
        var anim = go.GetComponentInChildren<Animator>();
        bool cd = _ctrlDriven.Contains(id);   // #anim-pack: controller plays the Death state (terminal)
        UnityEngine.Playables.PlayableGraph g = default; bool hg = false;
        if (cd) { if (anim != null) anim.SetTrigger("Death"); }
        else
        {
            AnimationClip death = FindOwnClip(id, "death", "dead", "die");
            if (death != null && anim != null && anim.avatar != null) { g = MakeClipGraph(anim, death, "Down_" + a.name); hg = true; }
        }
        float dur = 0.85f, t = 0f;
        while (t < dur && a != null && !_reviveWanted.Contains(id))
        {
            t += Time.deltaTime; float u = t / dur;
            if (hg) g.Evaluate(Time.deltaTime);
            else if (!cd) a.rotation = startRot * Quaternion.Euler(0f, 0f, Mathf.Lerp(0f, 85f, u));   // topple when no clip (controller plays Death)
            MoveActorAndShadows(a, home + new Vector3(0f, -0.25f * u, 0f));
            float dim = Mathf.Lerp(1f, 0.35f, u);
            FadeSibling(a.name, "_Ring", dim); FadeSibling(a.name, "_AO", dim); FadeSibling(a.name, "_Pip", dim);
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
        FadeSibling(a.name, "_Ring", 0.72f); FadeSibling(a.name, "_AO", 1f); FadeSibling(a.name, "_Pip", 1f);
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
            FadeSibling(a.name, "_Ring", dim); FadeSibling(a.name, "_AO", dim); FadeSibling(a.name, "_Pip", dim);
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
    // #1482-review: also the UPGRADE path — a name-plate-only root (EnsureNamePlate) whose foe's HP has since
    // become known gets the bg/fg quads added in place, instead of staying plate-only forever.
    void EnsureHpBar(string id, Transform actor)
    {
        if (actor == null) return;
        GameObject root;
        if (_hpBars.TryGetValue(id, out root) && root != null)
        {
            if (_namePlateOnly.Contains(id))
            {
                MakeBarQuad(root, "_bg", new Color(0.08f, 0.03f, 0.03f, 1f), 3080);
                MakeBarQuad(root, "_fg", new Color(0.85f, 0.15f, 0.12f, 1f), 3090);
                // UpdateHpBars indexes the fg quad at child 1 — reorder so bg/fg precede the name label
                // EnsureNamePlate already parented (it was the sole/first child until now).
                var bg = GameObject.Find(root.name + "_bg"); if (bg != null) bg.transform.SetSiblingIndex(0);
                var fg = GameObject.Find(root.name + "_fg"); if (fg != null) fg.transform.SetSiblingIndex(1);
                _namePlateOnly.Remove(id);
            }
            return;
        }
        root = new GameObject("Actor_" + id + "_HP");
        MakeBarQuad(root, "_bg", new Color(0.08f, 0.03f, 0.03f, 1f), 3080);   // child 0
        MakeBarQuad(root, "_fg", new Color(0.85f, 0.15f, 0.12f, 1f), 3090);   // child 1
        // #1463 (task 2): onboarding name plate — a world-space TextMesh riding just above the bar, parented
        // to the SAME HP-bar root the #1451 machinery positions + billboards each frame (so it tracks + faces
        // the camera for free). Only under onboarding, so beauty captures stay byte-identical.
        if (_onboard) MakeNameLabel(root, id);
        _hpBars[id] = root;
    }
    // #1482: a name-plate-ONLY root for a token with no known HP (foes hide their HP, so they never enter the
    // HP-bar path). Reuses the _hpBars dict + UpdateHpBars' per-frame position/billboard/prune (it carries the
    // plate above the actor's head for free), but adds NO HP quads — so UpdateHpBars' `childCount >= 2` fill
    // update is skipped and only the name label rides. Idempotent; onboard-only via the call site. Marked in
    // _namePlateOnly so EnsureHpBar can UPGRADE this root in place if the foe's HP later becomes known, and so
    // ApplyCombat's surface-presence prune can clear it once the foe leaves the surface.
    void EnsureNamePlate(string id, Transform actor)
    {
        if (actor == null) return;
        if (_hpBars.TryGetValue(id, out var root) && root != null) return;
        root = new GameObject("Actor_" + id + "_HP");
        MakeNameLabel(root, id);
        _hpBars[id] = root;
        _namePlateOnly.Add(id);
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
        _namePlateOnly.Remove(id);
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
            // #1463 (task 2): the turn indicator — the isCurrent combatant's name plate reads gold. #1482: a
            // foe (not on turn) reads hostile red so it registers as a TARGET vs an ally's parchment-white
            // (the bar's bg/fg quads carry no TextMesh, so this finds the name label).
            if (_onboard)
            {
                var nameTm = root.GetComponentInChildren<TextMesh>();
                if (nameTm != null)
                    nameTm.color = (kv.Key == _currentId) ? new Color(1f, 0.85f, 0.4f, 1f)
                                 : _foeIds.Contains(kv.Key) ? new Color(1f, 0.46f, 0.40f, 0.95f)
                                 : new Color(0.96f, 0.92f, 0.78f, 0.85f);
            }
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

    // Active-turn selection pulse: the isCurrent combatant's contact decal (`_Ring`) breathes (alpha + scale)
    // to mark the selection state (RING-V2: the feet-pip carries the always-on team read); the prior pulsed
    // decal resets to rest when the turn moves on. Rest/selected alphas 0.72/0.98 and base scale 2.3 track the
    // #1524 contact-decal evidence values (down from the old 2.6 UI ring).
    void UpdateTurnPulse()
    {
        if (_pulsePrev != _currentId && !string.IsNullOrEmpty(_pulsePrev))
        {
            var prev = GameObject.Find("Actor_" + _pulsePrev + "_Ring");
            if (prev != null) { var pr = prev.GetComponent<Renderer>(); if (pr != null && pr.sharedMaterial != null) { var c = pr.sharedMaterial.color; c.a = 0.72f; pr.sharedMaterial.color = c; } prev.transform.localScale = new Vector3(2.3f, 2.3f, 1f); }
            _pulsePrev = _currentId;
        }
        _pulsePrev = _currentId;
        if (string.IsNullOrEmpty(_currentId)) return;
        var ring = GameObject.Find("Actor_" + _currentId + "_Ring");
        if (ring == null) return;
        var r = ring.GetComponent<Renderer>(); if (r == null || r.sharedMaterial == null) return;
        float p = 0.5f + 0.5f * Mathf.Sin(Time.time * 4f);
        var col = r.sharedMaterial.color; col.a = Mathf.Lerp(0.72f, 0.98f, p); r.sharedMaterial.color = col;
        float s = Mathf.Lerp(2.3f, 2.7f, p); ring.transform.localScale = new Vector3(s, s, 1f);
    }

    // ---- #1463 W6.4 onboarding hint layer (task 1) --------------------------------------------------

    // The onboarding hint — whose turn (by NAME) + a one-line affordance, near the top of the screen. Full
    // strength until the first engine-accepted action, then fades over HintFadeDur and never returns. Inert
    // unless _onboard (beauty captures draw nothing). IMGUI (no Canvas), matching the advisory idiom above.
    void DrawOnboardHint()
    {
        if (!_onboard) return;
        float a = _acted ? (1f - Mathf.Clamp01(_actedT / HintFadeDur)) : 1f;
        if (a <= 0f) return;
        if (_hintStyle == null)
        {
            _hintStyle = new GUIStyle(GUI.skin.label) { fontSize = 22, fontStyle = FontStyle.Bold, alignment = TextAnchor.MiddleCenter, wordWrap = true };
            _hintSubStyle = new GUIStyle(GUI.skin.label) { fontSize = 16, alignment = TextAnchor.MiddleCenter, wordWrap = true };
        }
        string turn = _restMode ? "Explore the room" : (string.IsNullOrEmpty(_currentName) ? "Your turn" : _currentName + "'s turn");
        // owner playtest #4 (B): in rest mode with a door in view, tell the first-timer HOW to change rooms
        // (the owner could not find the doorway). Overlay is now default-off, so the rest hint no longer
        // references "highlighted tiles"; it points at the click-to-move + the glowing doorway instead.
        string hint = _restMode
            ? (_doorTo.Count > 0 ? "Click to move · click the glowing doorway to travel" : "Click a tile to move")
            : "Click a highlighted tile to move · click a foe to attack";
        float w = Mathf.Min(760f, Screen.width - 40f);
        var r1 = new Rect((Screen.width - w) / 2f, Screen.height * 0.03f, w, 34f);
        var r2 = new Rect((Screen.width - w) / 2f, Screen.height * 0.03f + 32f, w, 26f);
        var prev = GUI.color;
        GUI.color = new Color(0f, 0f, 0f, a * 0.6f);   // drop shadow for legibility over the painterly board
        GUI.Label(new Rect(r1.x + 2f, r1.y + 2f, r1.width, r1.height), turn, _hintStyle);
        GUI.Label(new Rect(r2.x + 2f, r2.y + 2f, r2.width, r2.height), hint, _hintSubStyle);
        GUI.color = new Color(1f, 0.90f, 0.62f, a);
        GUI.Label(r1, turn, _hintStyle);
        GUI.color = new Color(0.95f, 0.95f, 0.92f, a);
        GUI.Label(r2, hint, _hintSubStyle);
        GUI.color = prev;
    }

    // The first engine-accepted action retires the onboarding hint (it fades out and stays gone).
    void MarkActed() { if (_onboard && !_acted) { _acted = true; _actedT = 0f; } }

    // #1463 (task 2): a small camera-facing name plate parented to the HP-bar root (UpdateHpBars' per-frame
    // position + billboard carry it for free). LegacyRuntime font (Unity 6 dropped builtin Arial), matching
    // FloatDamage's TextMesh idiom. UpdateHpBars tints the isCurrent combatant's plate gold (the turn indicator).
    void MakeNameLabel(GameObject root, string id)
    {
        string nm; if (!_nameOf.TryGetValue(id, out nm) || string.IsNullOrEmpty(nm)) nm = id;
        var g = new GameObject(root.name + "_name");
        g.transform.SetParent(root.transform, false);
        g.transform.localPosition = new Vector3(0f, 0.7f, 0f);
        var tm = g.AddComponent<TextMesh>();
        tm.text = nm; tm.fontSize = 64; tm.characterSize = 0.16f; tm.anchor = TextAnchor.LowerCenter; tm.alignment = TextAlignment.Center; tm.color = new Color(0.96f, 0.92f, 0.78f, 1f);
        var font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
        if (font == null) font = Resources.GetBuiltinResource<Font>("Arial.ttf");
        var mr = g.GetComponent<MeshRenderer>();
        if (font != null) { tm.font = font; if (mr != null) { mr.sharedMaterial = new Material(font.material); mr.sharedMaterial.renderQueue = 3095; } }
        else if (mr != null && mr.sharedMaterial != null) mr.sharedMaterial.renderQueue = 3095;
    }

    // ---- #1463 W6.4 stage manifest (task 3; optional StreamingAssets/stage.json) --------------------

    // Parse the OPTIONAL StreamingAssets/stage.json. Absent/corrupt -> no flicker, no glow quads
    // (byte-identical to pre-#1463). Resolves the scene fire lights (Brazier* point lights baked by
    // paint_combat_v1.cs) for the Perlin flicker and spawns a warm glow quad at each fire_anchor via
    // MakeGroundQuad. Uses the same runtime Json parser registry.json + the surface extras already use.
    void LoadStageManifest()
    {
        try
        {
            string p = System.IO.Path.Combine(Application.streamingAssetsPath, "stage.json");
            if (!System.IO.File.Exists(p)) { Debug.Log("[CSC] no stage.json (byte-identical scene)"); return; }
            var root = Json.Parse(System.IO.File.ReadAllText(p)) as System.Collections.Generic.Dictionary<string, object>;
            if (root == null) return;

            // flicker block -> Perlin drive of the scene fire lights (captured base intensity = revertible).
            if (root.ContainsKey("flicker") && root["flicker"] is System.Collections.Generic.Dictionary<string, object> fl)
            {
                if (fl.ContainsKey("amplitude")) _flickAmp = System.Convert.ToSingle(fl["amplitude"]);
                if (fl.ContainsKey("speed")) _flickSpeed = System.Convert.ToSingle(fl["speed"]);
                var lights = new System.Collections.Generic.List<Light>();
                foreach (var lt in GameObject.FindObjectsOfType<Light>())
                    if (lt != null && lt.type == LightType.Point && lt.name.StartsWith("Brazier")) lights.Add(lt);
                if (lights.Count > 0)
                {
                    _fireLights = lights.ToArray();
                    _fireBaseIntensity = new float[_fireLights.Length];
                    _fireSeed = new float[_fireLights.Length];
                    for (int i = 0; i < _fireLights.Length; i++) { _fireBaseIntensity[i] = _fireLights[i].intensity; _fireSeed[i] = i * 13.37f; }
                    _flickerActive = true;
                }
            }

            // fire_anchors -> a warm glow quad on the floor at each [x,z] WORLD position (reuse MakeGroundQuad;
            // queue 1948 sits just under the actor AO/ring so the fire pool reads under the cast).
            if (root.ContainsKey("fire_anchors") && root["fire_anchors"] is System.Collections.Generic.List<object> anchors && anchors.Count > 0)
            {
                _glowQuads = new System.Collections.Generic.List<GameObject>();
                _glowMats = new System.Collections.Generic.List<Material>();
                var seeds = new System.Collections.Generic.List<float>();
                int gi = 0;
                foreach (var ae in anchors)
                {
                    var arr = ae as System.Collections.Generic.List<object>; if (arr == null || arr.Count < 2) { gi++; continue; }
                    float ax = System.Convert.ToSingle(arr[0]), az = System.Convert.ToSingle(arr[1]);
                    MakeGroundQuad("StageGlow_" + gi, new Vector3(ax, FloorY, az), 0.03f, 6.5f, GlowTex(), new Color(1f, 0.55f, 0.2f, 0.5f), 1948);
                    var g = GameObject.Find("StageGlow_" + gi);
                    if (g != null) { var r = g.GetComponent<Renderer>(); _glowQuads.Add(g); _glowMats.Add(r != null ? r.sharedMaterial : null); seeds.Add(gi * 7.7f); }
                    gi++;
                }
                _glowSeed = seeds.ToArray();
            }
            Debug.Log("[CSC] stage.json loaded: flicker=" + _flickerActive + " lights=" + (_fireLights != null ? _fireLights.Length : 0) + " glowQuads=" + (_glowQuads != null ? _glowQuads.Count : 0));
        }
        catch (System.Exception e) { Debug.LogWarning("[CSC] stage.json parse: " + e.Message); }
    }

    // A soft warm radial glow, brightest at center -> transparent at the rim (the fire pool on the floor).
    Texture2D GlowTex()
    {
        if (_glowT != null) return _glowT;
        _glowT = new Texture2D(128, 128, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp };
        var px = new Color[128 * 128]; float c = 63.5f;
        for (int y = 0; y < 128; y++) for (int x = 0; x < 128; x++)
        {
            float d = Mathf.Clamp01(Mathf.Sqrt((x - c) * (x - c) + (y - c) * (y - c)) / c);
            px[y * 128 + x] = new Color(1f, 1f, 1f, Mathf.Clamp01(Mathf.Pow(1f - d, 1.8f)));
        }
        _glowT.SetPixels(px); _glowT.Apply();
        return _glowT;
    }

    // #1463: drive the fire flicker each frame (the per-frame Update block, per the packet). A single Perlin
    // field (per-source seed offset) modulates each brazier's intensity around its captured base and each glow
    // quad's alpha, so lights + floor pool breathe together like flame. Pure presentation; reverts to base.
    void UpdateStageFlicker()
    {
        float tt = Time.time * _flickSpeed;
        if (_flickerActive && _fireLights != null)
            for (int i = 0; i < _fireLights.Length; i++)
            {
                if (_fireLights[i] == null) continue;
                float n = Mathf.PerlinNoise(_fireSeed[i], tt) - 0.5f;                 // -0.5..0.5
                _fireLights[i].intensity = _fireBaseIntensity[i] * (1f + _flickAmp * 2f * n);
            }
        if (_glowMats != null)
            for (int i = 0; i < _glowMats.Count; i++)
            {
                if (_glowMats[i] == null) continue;
                float seed = (_glowSeed != null && i < _glowSeed.Length) ? _glowSeed[i] : i;
                float n = Mathf.PerlinNoise(seed, tt) - 0.5f;
                var c = _glowMats[i].color; c.a = Mathf.Clamp01(0.5f * (1f + _flickAmp * 2f * n)); _glowMats[i].color = c;
            }
    }

    // ---- WALKABLE-SLICE-V1 (item 6) runtime plate registry --------------------------------------------

    // Parse the OPTIONAL StreamingAssets/plates_manifest.json ({version, plates:{<slug>:{plate, planeSize?,
    // cameraPin?}}}). Absent/corrupt -> _plateManifest null -> no swap ever runs (byte-identical to the
    // baked-single-plate scene). Uses the same runtime Json parser registry.json/stage.json use.
    void LoadPlateManifest()
    {
        try
        {
            string p = System.IO.Path.Combine(Application.streamingAssetsPath, "plates_manifest.json");
            if (!System.IO.File.Exists(p)) { Debug.Log("[CSC] no plates_manifest.json (baked single plate)"); return; }
            var root = Json.Parse(System.IO.File.ReadAllText(p)) as System.Collections.Generic.Dictionary<string, object>;
            var plates = (root != null && root.ContainsKey("plates")) ? root["plates"] as System.Collections.Generic.Dictionary<string, object> : null;
            if (plates == null) { Debug.LogWarning("[CSC] plates_manifest.json has no `plates` map"); return; }
            _plateManifest = new System.Collections.Generic.Dictionary<string, PlateEntry>();
            foreach (var kv in plates)
            {
                var row = kv.Value as System.Collections.Generic.Dictionary<string, object>; if (row == null) continue;
                var pe = new PlateEntry();
                pe.plate = row.ContainsKey("plate") ? row["plate"] as string : null;
                if (string.IsNullOrEmpty(pe.plate)) continue;
                if (row.ContainsKey("planeSize") && row["planeSize"] is System.Collections.Generic.List<object> ps && ps.Count >= 2)
                    pe.planeSize = new[] { System.Convert.ToSingle(ps[0]), System.Convert.ToSingle(ps[1]) };
                if (row.ContainsKey("cameraPin") && row["cameraPin"] is System.Collections.Generic.Dictionary<string, object> cp)
                {
                    if (cp.ContainsKey("ortho")) pe.ortho = System.Convert.ToSingle(cp["ortho"]);
                    if (cp.ContainsKey("pitch")) pe.pitch = System.Convert.ToSingle(cp["pitch"]);
                    if (cp.ContainsKey("yaw")) pe.yaw = System.Convert.ToSingle(cp["yaw"]);
                }
                // VFX-ANCHORS: OPTIONAL `effects`:[{type, cell:[c,r], scale?, y?}] anchored VFX for this plate.
                if (row.ContainsKey("boxes") && row["boxes"] is string bp && !string.IsNullOrEmpty(bp))
                    pe.boxesPath = bp;                       // UNIFY-THE-FRAMES: occluder boxes sidecar
                if (row.ContainsKey("effects") && row["effects"] is System.Collections.Generic.List<object> fx)
                {
                    pe.effects = new System.Collections.Generic.List<EffectSpec>();
                    foreach (var fe in fx)
                    {
                        var er = fe as System.Collections.Generic.Dictionary<string, object>; if (er == null) continue;
                        var es = new EffectSpec();
                        es.type = er.ContainsKey("type") ? er["type"] as string : null;
                        if (er.ContainsKey("cell") && er["cell"] is System.Collections.Generic.List<object> cl && cl.Count >= 2)
                            es.cell = new[] { System.Convert.ToInt32(cl[0]), System.Convert.ToInt32(cl[1]) };
                        if (er.ContainsKey("scale")) es.scale = System.Convert.ToSingle(er["scale"]);
                        if (er.ContainsKey("y")) es.y = System.Convert.ToSingle(er["y"]);
                        if (!string.IsNullOrEmpty(es.type) && es.cell != null) pe.effects.Add(es);
                    }
                }
                _plateManifest[kv.Key] = pe;
            }
            Debug.Log("[CSC] plates_manifest.json loaded: " + _plateManifest.Count + " plate(s)");
        }
        catch (System.Exception e) { Debug.LogWarning("[CSC] plates_manifest parse: " + e.Message); }
    }

    // Swap the backdrop plate when the surface's location changed since the last-applied plate AND the
    // manifest has an entry for it. Called from ApplyJson after ApplySurf. No manifest / same location /
    // unknown location -> no-op (the scene's current plate stands). Guarded against a re-entrant mid-fade swap.
    void MaybeSwapPlate()
    {
        if (!PlateSwapPending()) return;
        // _plateSwapping (set synchronously as SwapPlateCo's first line) is the re-entrancy guard; _plateLocId
        // is committed only on a SUCCESSFUL apply (below) so a missing plate file retries on the next poll.
        StartCoroutine(SwapPlateCo(_locId, _plateManifest[_locId]));
    }

    // #1544: the plate-swap gate, shared by ApplyJson's pre-mutation cover raise and MaybeSwapPlate so the
    // cover is raised for EXACTLY the surfaces that swap the plate. True only when a manifest is loaded, no
    // swap is already mid-flight, the surface carries a location, and that location differs from the one the
    // current backdrop plate was applied for AND has a manifest entry (an unknown room keeps its plate — no
    // pointless fade). Same-room occluder/prop changes don't trip it, so the cover is a room-change affordance.
    bool PlateSwapPending()
    {
        return _plateManifest != null && !_plateSwapping && !string.IsNullOrEmpty(_locId)
            && _locId != _plateLocId && _plateManifest.ContainsKey(_locId);
    }

    // #1544: raise the shared black cover to FULLY OPAQUE this frame (idempotent). Called BEFORE any visible
    // scene mutation on a room change so the destination-room rebuild happens behind black — no fade-IN, so no
    // window where the un-textured proxies show through. Parented to the camera in FRONT of the backdrop
    // (backdrop local z=160; cover nearer at z=120, oversized to blanket the ortho frustum). SwapPlateCo fades
    // it out and destroys it once the destination plate is applied.
    void RaisePlateCover()
    {
        var cam = Camera.main; if (cam == null) return;
        if (_plateCover == null)
        {
            _plateCover = GameObject.CreatePrimitive(PrimitiveType.Quad); _plateCover.name = "PlateFade";
            Object.DestroyImmediate(_plateCover.GetComponent<Collider>());
            _plateCover.transform.SetParent(cam.transform, false);
            _plateCover.transform.localPosition = new Vector3(0f, 0f, 120f);
            float ch = (cam.orthographic ? cam.orthographicSize * 2f : 30f) * 1.4f;
            _plateCover.transform.localScale = new Vector3(ch * 2f, ch, 1f);
            _plateCoverMat = new Material(Shader.Find("Sprites/Default")); _plateCoverMat.renderQueue = 4000;
            var cr = _plateCover.GetComponent<Renderer>(); cr.sharedMaterial = _plateCoverMat;
            cr.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
        }
        _plateCoverMat.color = new Color(0f, 0f, 0f, 1f);   // opaque immediately — never a greybox-exposing fade-IN
    }

    // #1544: LoadImage swap on the PaintedBackdrop material + re-size the camera-child quad to the new plate
    // aspect + optional camera pin, then fade the (already-opaque) cover back OUT. The cover was raised OPAQUE
    // by ApplyJson (RaisePlateCover) BEFORE the scene was rebuilt, so the whole rebuild + plate load stays
    // behind black; the reveal only starts once the destination plate is applied AND this new-room surface has
    // been consumed — no greybox flash, no black-gap disconnect. A missing plate file / backdrop object leaves
    // the current plate untouched (a logged warning, never a broken frame).
    IEnumerator SwapPlateCo(string slug, PlateEntry entry)
    {
        _plateSwapping = true;
        // Ensure the cover is up (defensive — e.g. a first-load swap where ApplyJson's raise was skipped
        // because Camera.main resolved late); a no-op when ApplyJson already raised it.
        RaisePlateCover();

        if (ApplyPlate(entry)) _plateLocId = slug;   // commit only on success -> a missing file retries next poll

        if (_plateCover != null && _plateCoverMat != null)
        {
            for (float t = 0f; t < 0.18f; t += Time.deltaTime) { _plateCoverMat.color = new Color(0f, 0f, 0f, 1f - Mathf.Clamp01(t / 0.18f)); yield return null; }
            Object.Destroy(_plateCover); _plateCover = null; _plateCoverMat = null;
        }
        _plateSwapping = false;
    }

    // Load the plate PNG (StreamingAssets/<entry.plate>) via Texture2D.LoadImage and assign it to the
    // "PaintedBackdrop" material (the camera-child quad paint_combat_v1.cs bakes). Re-sizes the quad to the
    // new plate aspect exactly as the bake does (oh = 2*orthoSize, ow = oh*aspect) unless the manifest pins
    // an explicit planeSize; applies an optional camera pin (ortho/pitch/yaw), reproducing the bake's rig.
    bool ApplyPlate(PlateEntry entry)
    {
        if (entry == null || string.IsNullOrEmpty(entry.plate)) return false;
        string path = System.IO.Path.Combine(Application.streamingAssetsPath, entry.plate);
        if (!System.IO.File.Exists(path)) { Debug.LogWarning("[CSC] plate file missing: " + path + " (keeping current plate)"); return false; }
        Texture2D tex = null;
        try
        {
            var bytes = System.IO.File.ReadAllBytes(path);
            tex = new Texture2D(2, 2, TextureFormat.RGBA32, false) { wrapMode = TextureWrapMode.Clamp };
            if (!tex.LoadImage(bytes)) { Debug.LogWarning("[CSC] plate decode failed: " + path); return false; }
        }
        catch (System.Exception e) { Debug.LogWarning("[CSC] plate load: " + e.Message); return false; }

        var bd = GameObject.Find("PaintedBackdrop");
        if (bd == null) { Debug.LogWarning("[CSC] PaintedBackdrop not found — cannot swap plate"); return false; }
        var rend = bd.GetComponent<Renderer>(); if (rend == null) return false;
        rend.material.mainTexture = tex;                 // instance material -> per-scene, never edits a shared asset

        var cam = Camera.main;
        // camera pin FIRST (so orthographicSize is current when we derive the plane height).
        if (cam != null)
        {
            if (entry.ortho > 0f)
            {
                cam.orthographicSize = entry.ortho;
                // ★ CAMERA-RIG FIX (#1583, epic #1581): reproduce build_room_unified's FULL rig, not just
                // ortho. The plate was painted by a camera at Euler(30,45,0), pulled back 80 units, AIMING AT
                // WORLD ORIGIN. Actors AND occluders are world-placed (CellToWorld / box centers) and projected
                // through THIS camera. The pre-fix code only re-set rotation/position when the manifest carried
                // pitch+yaw — but the shipped pins carry only `ortho`, so the runtime camera kept its
                // baked/previous-room POSITION and EVERYTHING projected offset from the plate: walk-on-tomb,
                // walk-through-wall, and no pillar occlusion (the 2026-07-15 walkability failure — all one
                // projection mismatch). Set the contract rig UNCONDITIONALLY whenever ortho is pinned, so every
                // room is self-healing and no future room can regress by omitting a pin. pitch/yaw default to the
                // frozen dimetric contract (CANONICAL.md: elevation 30, yaw 45); a manifest may still override.
                float pitch = float.IsNaN(entry.pitch) ? 30f : entry.pitch;
                float yaw = float.IsNaN(entry.yaw) ? 45f : entry.yaw;
                Quaternion rot = Quaternion.Euler(pitch, yaw, 0f);
                cam.transform.rotation = rot;
                cam.transform.position = -(rot * Vector3.forward) * 80f;   // aim at world origin (room center)
            }
        }
        // re-size the backdrop quad: explicit planeSize wins, else derive from the plate aspect + ortho (the bake).
        float oh, ow;
        if (entry.planeSize != null && entry.planeSize.Length >= 2) { ow = entry.planeSize[0]; oh = entry.planeSize[1]; }
        else { oh = (cam != null && cam.orthographic) ? cam.orthographicSize * 2f : bd.transform.localScale.y; ow = oh * ((float)tex.width / tex.height); }
        var ls = bd.transform.localScale;
        bd.transform.localScale = new Vector3(ow, oh, ls.z == 0f ? 1f : ls.z);
        Debug.Log("[CSC] plate swapped -> " + entry.plate + " (" + tex.width + "x" + tex.height + ", loc=" + _locId + ")");
        // UNIFY-THE-FRAMES: load this plate's occluder-box sidecar (world-space {center,size} rows from
        // build_room_unified.cs — the same boxes the depth conditioning was rendered from). Parsed once per
        // swap; RebuildOccluders prefers these over per-cell footprint proxies. Reset the occluder signature
        // so the next ApplyJson rebuild fires even when the surface's occluder set is unchanged.
        _plateBoxes = null;
        _plateBoxesLocId = _locId;  // whatever the load yields (boxes or none), it reflects THIS room
        if (!string.IsNullOrEmpty(entry.boxesPath))
        {
            // SECURITY (adversarial-invariant-verify, #1575): a manifest is untrusted data. IsPathRooted
            // only rejects ABSOLUTE paths — it does NOT stop `..` traversal, and Path.Combine does not
            // normalize, so `"../../../etc/passwd"` would escape StreamingAssets. Reject rooted paths AND
            // any path that, once resolved, does not stay under streamingAssetsPath. The WHOLE block is
            // also wrapped in a broad catch so a malformed value (invalid path chars -> ArgumentException,
            // which the old narrow filter missed) can never abort SwapPlateCo and wedge _plateSwapping.
            try
            {
                string root = System.IO.Path.GetFullPath(Application.streamingAssetsPath);
                string bpath = System.IO.Path.GetFullPath(System.IO.Path.Combine(root, entry.boxesPath));
                bool contained = bpath.StartsWith(root + System.IO.Path.DirectorySeparatorChar,
                                                  System.StringComparison.Ordinal) || bpath == root;
                if (System.IO.Path.IsPathRooted(entry.boxesPath) || !contained)
                    throw new System.Security.SecurityException(
                        "plate boxes path escapes StreamingAssets: " + entry.boxesPath);
                if (System.IO.File.Exists(bpath))
                {
                    var broot = Json.Parse(System.IO.File.ReadAllText(bpath)) as System.Collections.Generic.Dictionary<string, object>;
                    object blistO = null;
                    var blist = (broot != null && broot.TryGetValue("boxes", out blistO)) ? blistO as System.Collections.Generic.List<object> : null;
                    if (blist != null)
                    {
                        _plateBoxes = new System.Collections.Generic.List<float[]>();
                        foreach (var bo in blist)
                        {
                            var bd2 = bo as System.Collections.Generic.Dictionary<string, object>; if (bd2 == null) continue;
                            object v;
                            string bkind = bd2.TryGetValue("kind", out v) ? v as string : "";
                            if (bkind == "floor") continue;  // the floor never occludes an actor
                            var bc = bd2.TryGetValue("center", out v) ? v as System.Collections.Generic.List<object> : null;
                            var bs = bd2.TryGetValue("size", out v) ? v as System.Collections.Generic.List<object> : null;
                            if (bc == null || bs == null || bc.Count < 3 || bs.Count < 3) continue;
                            _plateBoxes.Add(new[] {
                                System.Convert.ToSingle(bc[0]), System.Convert.ToSingle(bc[1]), System.Convert.ToSingle(bc[2]),
                                System.Convert.ToSingle(bs[0]), System.Convert.ToSingle(bs[1]), System.Convert.ToSingle(bs[2]) });
                        }
                        Debug.Log("[CSC] plate boxes loaded: " + _plateBoxes.Count + " occluder volumes (" + entry.boxesPath + ")");
                    }
                }
                else Debug.LogWarning("[CSC] plate boxes file missing (falling back to footprint proxies)");
            }
            // Broad catch on purpose: ANY sidecar failure (traversal, invalid chars, IO, malformed JSON)
            // degrades to the footprint proxies and MUST NOT throw out of the swap coroutine.
            catch (System.Exception e)
            { Debug.LogWarning("[CSC] plate boxes load rejected/failed: " + e.Message); _plateBoxes = null; }
        }
        // rebuild NOW (not next poll): the swap happens behind the black cover; the proxies must match
        // the revealed plate immediately, never one poll late (codex review on #1575).
        _occSigBuilt = null;
        RebuildOccluders();
        // VFX-ANCHORS: despawn the prior plate's effect instances and spawn this plate's anchored VFX (an
        // animated fire over the painted firepit, etc.). No `effects` / no registry / no bundle => a clean
        // despawn + nothing spawned (byte-identical to the pre-VFX plate).
        SpawnPlateEffects(entry);
        // #idle-fix: the location just changed — settle the whole cast to a grounded idle on the new plate so
        // no actor renders a bind (T) pose or floats after the cross-room reposition (the taste-pass defect).
        SettleCastIdleGrounded();
        return true;
    }

    // ---- VFX-ANCHORS: per-plate anchored presentation effects (fire / embers / fireflies) ----------------

    // Parse the OPTIONAL StreamingAssets/effects_registry.json ({version, effects:{<type>:{prefab}}}) into a
    // flat type -> prefab-asset-path map. The prefab paths key into the SAME worldos_actors AssetBundle the
    // build bakes them into (BuildMacOSPlayer.EnsurePackaged). Absent/corrupt -> _effectRegistry null -> no
    // effect ever resolves (byte-identical). Same runtime Json parser registry.json/stage.json use.
    void LoadEffectsRegistry()
    {
        try
        {
            string p = System.IO.Path.Combine(Application.streamingAssetsPath, "effects_registry.json");
            if (!System.IO.File.Exists(p)) { Debug.Log("[CSC] no effects_registry.json (no anchored VFX)"); return; }
            var root = Json.Parse(System.IO.File.ReadAllText(p)) as System.Collections.Generic.Dictionary<string, object>;
            var eff = (root != null && root.ContainsKey("effects")) ? root["effects"] as System.Collections.Generic.Dictionary<string, object> : null;
            if (eff == null) { Debug.LogWarning("[CSC] effects_registry.json has no `effects` map"); return; }
            _effectRegistry = new System.Collections.Generic.Dictionary<string, string>();
            foreach (var kv in eff)
            {
                var row = kv.Value as System.Collections.Generic.Dictionary<string, object>; if (row == null) continue;
                string prefab = row.ContainsKey("prefab") ? row["prefab"] as string : null;
                if (!string.IsNullOrEmpty(prefab)) _effectRegistry[kv.Key] = prefab;
            }
            Debug.Log("[CSC] effects_registry.json loaded: " + _effectRegistry.Count + " effect type(s)");
        }
        catch (System.Exception e) { Debug.LogWarning("[CSC] effects_registry parse: " + e.Message); }
    }

    // Despawn the prior plate's effect instances and spawn this plate's `effects` anchored at each cell's
    // CellToWorld position (+ y). Each spec's `type` resolves through the registry to a prefab loaded from
    // the actor bundle; a missing type / prefab / bundle is a logged skip (never a broken frame). Hovl SG
    // materials are re-pointed to Legacy Particles so they render under the box's Built-in RP (see #1515).
    void SpawnPlateEffects(PlateEntry entry)
    {
        // despawn prior instances first (unconditional — a plate with no effects clears the old ones).
        if (_effectInstances != null)
        {
            foreach (var go in _effectInstances) if (go != null) Object.Destroy(go);
            _effectInstances.Clear();
        }
        if (entry == null || entry.effects == null || entry.effects.Count == 0) return;
        if (_effectRegistry == null) { Debug.LogWarning("[CSC] plate has effects but no effects_registry.json — skipped"); return; }
        if (_effectsRoot == null) { _effectsRoot = new GameObject("_EffectsRoot"); }
        if (_effectInstances == null) _effectInstances = new System.Collections.Generic.List<GameObject>();

        foreach (var es in entry.effects)
        {
            if (es == null || string.IsNullOrEmpty(es.type) || es.cell == null || es.cell.Length < 2) continue;
            if (!_effectRegistry.TryGetValue(es.type, out var prefabPath) || string.IsNullOrEmpty(prefabPath))
            { Debug.LogWarning("[CSC] effect type '" + es.type + "' not in registry — skipped"); continue; }
            var prefab = LoadAsset<GameObject>(prefabPath);
            if (prefab == null) { Debug.LogWarning("[CSC] effect prefab missing in bundle: " + prefabPath + " (type " + es.type + ")"); continue; }
            var inst = (GameObject)Object.Instantiate(prefab);
            inst.name = "_FX_" + es.type + "_" + es.cell[0] + "_" + es.cell[1];
            inst.transform.SetParent(_effectsRoot.transform, false);
            inst.transform.position = CellToWorld(es.cell[0], es.cell[1]) + new Vector3(0f, es.y, 0f);
            if (es.scale > 0f && System.Math.Abs(es.scale - 1f) > 1e-4f) inst.transform.localScale *= es.scale;
            RepointHovlMaterials(inst);
            WarmParticleSystems(inst);
            _effectInstances.Add(inst);
            Debug.Log("[CSC] spawned effect '" + es.type + "' @cell[" + es.cell[0] + "," + es.cell[1] + "] y=" + es.y + " scale=" + es.scale);
        }
    }

    // Runtime port of M15SpendGateProbe.FixHovlVFX (#1515) Built-in-RP branch: the box renders under Built-in
    // RP, where Hovl's Shader-Graph particle materials (Shader Graphs/HS_*, HS_*) draw 0 px. Re-point them to
    // Unity's Legacy Particles shader (Additive for fire/energy; Alpha Blended for distortion), preserving the
    // emissive texture + tint. No-op under URP/HDRP (the SG shaders render there) and for non-Hovl materials.
    static void RepointHovlMaterials(GameObject inst)
    {
        if (UnityEngine.Rendering.GraphicsSettings.currentRenderPipeline != null) return;   // URP/HDRP: SG renders
        var addBlend = Shader.Find("Legacy Shaders/Particles/Additive");
        var alphaBlend = Shader.Find("Legacy Shaders/Particles/Alpha Blended");
        if (addBlend == null) { Debug.LogWarning("[CSC] Legacy Shaders/Particles/Additive not found — Hovl VFX may render 0px"); return; }
        int fixedN = 0;
        foreach (var r in inst.GetComponentsInChildren<Renderer>(true))
        {
            var src = r.sharedMaterial; if (src == null || src.shader == null) continue;
            string sn = src.shader.name;
            if (!(sn.Contains("HS_") || sn.StartsWith("Shader Graphs/HS") || sn.StartsWith("Hovl"))) continue;
            bool distort = sn.Contains("Distort");
            var tgt = new Material(distort && alphaBlend != null ? alphaBlend : addBlend);
            Texture tex = null;
            foreach (var pn in new[] { "_MainTex", "_BaseMap", "_BaseColorMap" }) if (src.HasProperty(pn) && src.GetTexture(pn) != null) { tex = src.GetTexture(pn); break; }
            if (tex != null) tgt.mainTexture = tex;
            foreach (var pn in new[] { "_TintColor", "_BaseColor", "_Color" }) if (src.HasProperty(pn)) { tgt.color = src.GetColor(pn); break; }
            r.material = tgt;   // per-instance material — never edits the shared bundle asset
            fixedN++;
        }
        if (fixedN > 0) Debug.Log("[CSC] Built-in RP: re-pointed " + fixedN + " Hovl SG material(s) -> Legacy Particles");
    }

    // Tick freshly-instantiated ParticleSystems forward so a persistent VFX (a looping campfire) shows flame
    // in the first captured frame instead of its empty t=0 bind state (mirrors M15SpendGateProbe's warm-up).
    static void WarmParticleSystems(GameObject inst)
    {
        foreach (var ps in inst.GetComponentsInChildren<ParticleSystem>(true))
        {
            ps.Simulate(2f, true, true);
            ps.Play(true);
        }
    }
}
