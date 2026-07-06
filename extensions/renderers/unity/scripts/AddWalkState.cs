using UnityEngine;
using UnityEditor;
using UnityEditor.Animations;

/// <summary>
/// One-shot editor utility: adds Walk state + IsWalking bool param to HeroAnim_CL
/// and GoblinAnim_CL so GridPathController can drive walk animation.
/// Run via: Tools/WorldOS/Add Walk State to Hero+Goblin
/// </summary>
public static class AddWalkState
{
    [MenuItem("Tools/WorldOS/Add Walk State to Hero+Goblin")]
    public static void Run()
    {
        SetupChar("HeroFighter",     "HeroAnim_CL",   "Assets/chars_v3/hero/glb/walk.fbx");
        SetupChar("MonsterGoblin",   "GoblinAnim_CL", "Assets/chars_v3/goblin/glb/walk.fbx");
    }

    static void SetupChar(string goName, string controllerName, string walkFbxPath)
    {
        var go = GameObject.Find(goName);
        if (go == null) { Debug.LogWarning("[AddWalk] GameObject not found: " + goName); return; }
        var anim = go.GetComponentInChildren<Animator>();
        if (anim == null) { Debug.LogWarning("[AddWalk] No Animator on " + goName); return; }
        var ac = anim.runtimeAnimatorController as AnimatorController;
        if (ac == null) { Debug.LogWarning("[AddWalk] Not an AnimatorController on " + goName); return; }

        // ── 1. Add IsWalking bool param ──────────────────────────────────────
        bool hasParam = false;
        foreach (var p in ac.parameters)
            if (p.name == "IsWalking") { hasParam = true; break; }
        if (!hasParam)
            ac.AddParameter("IsWalking", AnimatorControllerParameterType.Bool);

        // ── 2. Add Walk state ─────────────────────────────────────────────────
        var sm = ac.layers[0].stateMachine;
        AnimatorState walkState = null;
        AnimatorState idleState = null;
        foreach (var cs in sm.states)
        {
            if (cs.state.name == "Walk") walkState = cs.state;
            if (cs.state.name == "Idle") idleState = cs.state;
        }

        if (walkState == null)
        {
            var walkClip = AssetDatabase.LoadAssetAtPath<AnimationClip>(walkFbxPath);
            if (walkClip == null)
            {
                // Try sub-assets
                var objs = AssetDatabase.LoadAllAssetRepresentationsAtPath(walkFbxPath);
                foreach (var o in objs)
                    if (o is AnimationClip c) { walkClip = c; break; }
            }
            walkState = sm.AddState("Walk");
            walkState.motion = walkClip;
            walkState.speed = 1f;
            Debug.Log("[AddWalk] Created Walk state for " + goName +
                      " clip=" + (walkClip != null ? walkClip.name : "NULL"));
        }

        // ── 3. Idle → Walk (IsWalking true) ──────────────────────────────────
        if (idleState != null)
        {
            bool hasIToW = false;
            foreach (var tr in idleState.transitions)
                if (tr.destinationState == walkState) { hasIToW = true; break; }
            if (!hasIToW)
            {
                var t = idleState.AddTransition(walkState);
                t.hasExitTime = false;
                t.duration = 0.10f;
                t.AddCondition(AnimatorConditionMode.If, 0f, "IsWalking");
            }
        }

        // ── 4. Walk → Idle (IsWalking false) ─────────────────────────────────
        if (idleState != null)
        {
            bool hasWToI = false;
            foreach (var tr in walkState.transitions)
                if (tr.destinationState == idleState) { hasWToI = true; break; }
            if (!hasWToI)
            {
                var t = walkState.AddTransition(idleState);
                t.hasExitTime = false;
                t.duration = 0.10f;
                t.AddCondition(AnimatorConditionMode.IfNot, 0f, "IsWalking");
            }
        }

        EditorUtility.SetDirty(ac);
        AssetDatabase.SaveAssets();
        Debug.Log("[AddWalk] Done for " + goName + " — IsWalking param + Walk state wired.");
    }
}
