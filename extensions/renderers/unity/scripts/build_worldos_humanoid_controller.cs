// build_worldos_humanoid_controller.cs — build the SHARED humanoid AnimatorController from the
// Explosive LLC "RPG Character Mecanim Animation Pack" (all clips are Humanoid -> retarget onto ANY
// valid humanoid avatar). This is the #1408 permanent T-pose fix: CombatSurfaceClient assigns this
// controller to every actor whose Animator has a valid humanoid avatar and drives it with a Speed
// float (Locomotion blend: idle/walk/run) + Attack/Hit/Death triggers.
//
// Run on the GEX44 box:  unity-mcp code execute --no-safety-checks -f build_worldos_humanoid_controller.cs
// NO top-level `using` — `code execute` wraps the snippet in a method body where `using` is illegal
// (mirrors the proven build_combat_animator.cs / paint_3d_spike.cs). No LINQ either. Idempotent: the
// controller is cleared + rebuilt clean every run, so re-running never duplicates states/params.
//
// PARAMETER NAMES ARE A CONTRACT with CombatSurfaceClient.cs: Speed (Float), Attack/Hit/Death (Trigger).
var sb = new System.Text.StringBuilder();
string CTRL = "Assets/Animations/WorldOSHumanoid.controller";

// The pack clips (by their FBX filename, sans .FBX). Located via FindAssets so the exact subfolder
// (Unarmed / Relax) doesn't matter. Run-Forward is the natural cell->cell stride at glide speed; the
// Unarmed set has no plain walk, so Relax-Walk-Forward is the slow-walk blend point.
string IDLE = "RPG-Character@Unarmed-Idle";
string WALK = "RPG-Character@Relax-Walk-Forward";
string RUN  = "RPG-Character@Unarmed-Run-Forward";
string ATK  = "RPG-Character@Unarmed-Attack-R1";
string HIT  = "RPG-Character@Unarmed-GetHit-F1";
string DIE  = "RPG-Character@Unarmed-Death1";

UnityEditor.AssetDatabase.Refresh();

// Resolve an AnimationClip sub-asset by its owning FBX filename (FindAssets on the file, then scan its
// sub-assets for the first non-"__" AnimationClip). Robust to which pack subfolder the clip lives in.
System.Func<string, UnityEngine.AnimationClip> clipByName = (fname) => {
    var guids = UnityEditor.AssetDatabase.FindAssets(fname);
    for (int gi = 0; gi < guids.Length; gi++) {
        string p = UnityEditor.AssetDatabase.GUIDToAssetPath(guids[gi]);
        if (string.IsNullOrEmpty(p)) continue;
        // require an exact filename match (FindAssets is a fuzzy contains-match)
        string leaf = System.IO.Path.GetFileNameWithoutExtension(p);
        if (leaf != fname) continue;
        var imp = UnityEditor.AssetImporter.GetAtPath(p) as UnityEditor.ModelImporter;
        if (imp != null && imp.animationType != UnityEditor.ModelImporterAnimationType.Human)
            sb.AppendLine("WARN " + fname + " importer is " + imp.animationType + " not Humanoid (retarget may fail)");
        var assets = UnityEditor.AssetDatabase.LoadAllAssetsAtPath(p);
        for (int ai = 0; ai < assets.Length; ai++) {
            var ac = assets[ai] as UnityEngine.AnimationClip;
            if (ac != null && !ac.name.StartsWith("__")) return ac;
        }
    }
    return null;
};

var idleC = clipByName(IDLE); var walkC = clipByName(WALK); var runC = clipByName(RUN);
var atkC  = clipByName(ATK);  var hitC  = clipByName(HIT);  var dieC = clipByName(DIE);
if (idleC == null) sb.AppendLine("MISSING clip " + IDLE);
if (walkC == null) { sb.AppendLine("MISSING clip " + WALK + " -> using RUN for the walk blend point"); walkC = runC; }
if (runC  == null) sb.AppendLine("MISSING clip " + RUN);
if (atkC  == null) sb.AppendLine("MISSING clip " + ATK);
if (hitC  == null) sb.AppendLine("MISSING clip " + HIT);
if (dieC  == null) sb.AppendLine("MISSING clip " + DIE);

// (Re)create the controller, cleared to a blank slate (idempotent rebuild).
System.IO.Directory.CreateDirectory("Assets/Animations");
var ctrl = UnityEditor.AssetDatabase.LoadAssetAtPath<UnityEditor.Animations.AnimatorController>(CTRL);
if (ctrl == null) ctrl = UnityEditor.Animations.AnimatorController.CreateAnimatorControllerAtPath(CTRL);
var pcopy = ctrl.parameters;
foreach (var p in pcopy) ctrl.RemoveParameter(p);
var sm = ctrl.layers[0].stateMachine;
var scopy = sm.states;
foreach (var cs in scopy) sm.RemoveState(cs.state);

// Parameters (the CombatSurfaceClient contract).
ctrl.AddParameter("Speed",  UnityEngine.AnimatorControllerParameterType.Float);
ctrl.AddParameter("Attack", UnityEngine.AnimatorControllerParameterType.Trigger);
ctrl.AddParameter("Hit",    UnityEngine.AnimatorControllerParameterType.Trigger);
ctrl.AddParameter("Death",  UnityEngine.AnimatorControllerParameterType.Trigger);

// Locomotion: a 1D blend on Speed (idle@0 / walk@1.5 / run@5). CreateBlendTreeInController adds the
// state + tree to layer 0; it auto-adds a "Blend" float param which we repoint to "Speed" and drop.
UnityEditor.Animations.BlendTree tree;
var loco = ctrl.CreateBlendTreeInController("Locomotion", out tree, 0);
tree.blendType = UnityEditor.Animations.BlendTreeType.Simple1D;
tree.blendParameter = "Speed";
tree.useAutomaticThresholds = false;
if (idleC != null) tree.AddChild(idleC, 0f);
if (walkC != null) tree.AddChild(walkC, 1.5f);
if (runC  != null) tree.AddChild(runC, 5f);
// drop the auto-added "Blend" param if CreateBlendTreeInController introduced one.
var pcopy2 = ctrl.parameters;
foreach (var p in pcopy2) if (p.name == "Blend") ctrl.RemoveParameter(p);
sm.defaultState = loco;

// One-shot verb states (Any-State triggered). Attack/Hit return to Locomotion after (near-)full play;
// Death is terminal (holds prone — CombatSurfaceClient's DownCo owns the sink/dim, revive rebinds out).
System.Func<string, UnityEngine.AnimationClip, UnityEditor.Animations.AnimatorState> verb = (nm, clip) => {
    var st = sm.AddState(nm); st.motion = clip;
    var anyT = sm.AddAnyStateTransition(st);
    anyT.AddCondition(UnityEditor.Animations.AnimatorConditionMode.If, 0f, nm);
    anyT.duration = 0.08f; anyT.hasExitTime = false; anyT.canTransitionToSelf = false;
    return st;
};
var atkS = verb("Attack", atkC);
var hitS = verb("Hit",    hitC);
var dieS = verb("Death",  dieC);
foreach (var st in new UnityEditor.Animations.AnimatorState[] { atkS, hitS }) {
    var back = st.AddTransition(loco);
    back.hasExitTime = true; back.exitTime = 0.85f; back.duration = 0.15f;
}
// Death: no back-transition (terminal); the revive path rebinds the Animator to the default state.

UnityEditor.EditorUtility.SetDirty(ctrl);
UnityEditor.AssetDatabase.SaveAssets();
UnityEditor.AssetDatabase.Refresh();
sb.AppendLine("WorldOSHumanoid.controller: states=" + sm.states.Length + " params=" + ctrl.parameters.Length
    + " loco-children=" + tree.children.Length
    + " clips[idle/walk/run/atk/hit/die]="
    + (idleC != null ? "1" : "0") + (walkC != null ? "1" : "0") + (runC != null ? "1" : "0")
    + (atkC != null ? "1" : "0") + (hitC != null ? "1" : "0") + (dieC != null ? "1" : "0"));
return sb.ToString();
