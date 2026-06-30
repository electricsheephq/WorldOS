// build_combat_animator.cs — import the moveset (Generic) + build the CombatActor Animator controller.
// Run on the GEX44 box: unity-mcp code execute --no-safety-checks -f build_combat_animator.cs
// NO top-level `using` — `code execute` wraps the snippet in a method body where `using` is illegal
// (the proven paint_3d_spike.cs works precisely because it fully-qualifies everything). No LINQ either.
// Expects the moveset clips at Assets/painterly/models/moveset/anim_<name>.fbx (imported as Generic).
var sb = new System.Text.StringBuilder();
string DIR = "Assets/painterly/models/moveset/";
string CTRL = "Assets/Animations/CombatActor.controller";
// walk/run deferred (no .glb to Blender-clean); combat needs idle/attack/cast/block/dodge/hit/death.
var moves = new string[] { "idle", "attack", "cast", "block", "dodge", "hit", "death" };

UnityEditor.AssetDatabase.Refresh();

// 1) import each clip FBX as Generic — Humanoid SILENTLY DROPS Meshy/Tripo clips (load-bearing gotcha).
int imported = 0;
foreach (var m in moves) {
    string fbx = DIR + "anim_" + m + ".fbx";
    var imp = UnityEditor.AssetImporter.GetAtPath(fbx) as UnityEditor.ModelImporter;
    if (imp == null) { sb.AppendLine("MISSING " + fbx); continue; }
    if (imp.animationType != UnityEditor.ModelImporterAnimationType.Generic) {
        imp.animationType = UnityEditor.ModelImporterAnimationType.Generic;
        imp.SaveAndReimport();
    }
    imported++;
}
UnityEditor.AssetDatabase.Refresh();

// 2) build the controller: states + a trigger per verb + Any-State transitions; idempotent (rebuilds clean).
System.IO.Directory.CreateDirectory("Assets/Animations");
var ctrl = UnityEditor.AssetDatabase.LoadAssetAtPath<UnityEditor.Animations.AnimatorController>(CTRL);
if (ctrl == null) ctrl = UnityEditor.Animations.AnimatorController.CreateAnimatorControllerAtPath(CTRL);
var paramsCopy = ctrl.parameters;
foreach (var p in paramsCopy) ctrl.RemoveParameter(p.name);
var sm = ctrl.layers[0].stateMachine;
var statesCopy = sm.states;
foreach (var cs in statesCopy) sm.RemoveState(cs.state);

System.Func<string, UnityEngine.AnimationClip> clipOf = (m) => {
    var assets = UnityEditor.AssetDatabase.LoadAllAssetsAtPath(DIR + "anim_" + m + ".fbx");
    foreach (var a in assets) {
        var ac = a as UnityEngine.AnimationClip;
        if (ac != null && !ac.name.StartsWith("__")) return ac;
    }
    return null;
};

var states = new System.Collections.Generic.Dictionary<string, UnityEditor.Animations.AnimatorState>();
foreach (var m in moves) {
    string trig = "to" + char.ToUpper(m[0]) + m.Substring(1);
    ctrl.AddParameter(trig, UnityEngine.AnimatorControllerParameterType.Trigger);
    var st = sm.AddState(m);
    st.motion = clipOf(m);
    states[m] = st;
    if (st.motion == null) sb.AppendLine("WARN no clip in anim_" + m + ".fbx");
}
sm.defaultState = states["idle"];

foreach (var m in moves) {
    if (m == "idle") continue;
    string trig = "to" + char.ToUpper(m[0]) + m.Substring(1);
    var anyT = sm.AddAnyStateTransition(states[m]);
    anyT.AddCondition(UnityEditor.Animations.AnimatorConditionMode.If, 0, trig);
    anyT.duration = 0.10f; anyT.canTransitionToSelf = false;
    if (m != "death") {  // one-shot verbs return to idle; death holds (terminal)
        var back = states[m].AddTransition(states["idle"]);
        back.hasExitTime = true; back.exitTime = 0.9f; back.duration = 0.25f;
    }
}
UnityEditor.AssetDatabase.SaveAssets();
sb.AppendLine("CombatActor.controller: imported=" + imported + "/7 states=" + sm.states.Length + " params=" + ctrl.parameters.Length);
return sb.ToString();
