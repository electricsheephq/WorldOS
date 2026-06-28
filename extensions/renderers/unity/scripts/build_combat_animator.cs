// build_combat_animator.cs — import the 9-clip Meshy moveset (as Generic) + build the CombatActor Animator.
// Run on the GEX44 box: unity-mcp code execute --no-safety-checks -f build_combat_animator.cs
// Expects the moveset clips deployed at Assets/painterly/models/moveset/anim_<name>.fbx
// (from `meshy_gen.py --rig-from-task <hero> --moveset`). Built-in pipeline; runs via the CoplayDev bridge.
// Source patterns: Besty0728 AnimatorSkills (see unity-editor-patterns-m1-combat.md). CANONICAL.md discipline.
using System.Linq;
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;

var sb = new System.Text.StringBuilder();
const string DIR = "Assets/painterly/models/moveset/";
const string CTRL = "Assets/Animations/CombatActor.controller";
var moves = new[] { "idle", "walk", "run", "attack", "cast", "block", "dodge", "hit", "death" };

// 1) import each clip FBX as Generic — Humanoid SILENTLY DROPS Meshy/Tripo clips (load-bearing gotcha).
int imported = 0;
foreach (var m in moves) {
    string fbx = DIR + "anim_" + m + ".fbx";
    var imp = AssetImporter.GetAtPath(fbx) as ModelImporter;
    if (imp == null) { sb.AppendLine("MISSING " + fbx); continue; }
    if (imp.animationType != ModelImporterAnimationType.Generic) { imp.animationType = ModelImporterAnimationType.Generic; imp.SaveAndReimport(); }
    imported++;
}
AssetDatabase.Refresh();

// 2) build the controller: 9 states + a trigger per verb + Any-State transitions; idempotent (rebuilds clean).
System.IO.Directory.CreateDirectory("Assets/Animations");
var ctrl = AssetDatabase.LoadAssetAtPath<AnimatorController>(CTRL) ?? AnimatorController.CreateAnimatorControllerAtPath(CTRL);
foreach (var p in ctrl.parameters.ToArray()) ctrl.RemoveParameter(p.name);
var sm = ctrl.layers[0].stateMachine;
foreach (var cs in sm.states.ToArray()) sm.RemoveState(cs.state);

System.Func<string, AnimationClip> clipOf = (m) =>
    AssetDatabase.LoadAllAssetsAtPath(DIR + "anim_" + m + ".fbx").OfType<AnimationClip>().FirstOrDefault(c => !c.name.StartsWith("__"));

var states = new Dictionary<string, AnimatorState>();
foreach (var m in moves) {
    string trig = "to" + char.ToUpper(m[0]) + m.Substring(1);
    ctrl.AddParameter(trig, AnimatorControllerParameterType.Trigger);
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
    anyT.AddCondition(AnimatorConditionMode.If, 0, trig);
    anyT.duration = 0.10f; anyT.canTransitionToSelf = false;
    // one-shot verbs return to idle; locomotion + death hold
    if (m != "walk" && m != "run" && m != "death") {
        var back = states[m].AddTransition(states["idle"]);
        back.hasExitTime = true; back.exitTime = 0.9f; back.duration = 0.25f;
    }
}
AssetDatabase.SaveAssets();
sb.AppendLine("CombatActor.controller: imported=" + imported + "/9 states=" + sm.states.Length + " params=" + ctrl.parameters.Length);
return sb.ToString();
