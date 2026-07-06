using UnityEngine;
using UnityEditor;
using UnityEditor.Animations;
using System.IO;

/// <summary>
/// Animation proof for ARM 1B (winner): wire the Mecanim Idle clip (looping) onto the in-scene
/// hero + goblin via an AnimatorController, then capture two frames at different clip times to
/// prove the rig actually animates (the silhouette/limbs change between frames).
/// </summary>
public static class AnimProof
{
    // Build a looping AnimatorController whose default state plays the FBX's Idle clip.
    static AnimatorController BuildIdleController(string fbxPath, string ctrlPath)
    {
        AnimationClip idle = null;
        foreach (var o in AssetDatabase.LoadAllAssetsAtPath(fbxPath))
        {
            var cl = o as AnimationClip;
            if (cl != null && !cl.name.StartsWith("__preview") && cl.name.Contains("Idle")) { idle = cl; break; }
        }
        if (idle == null) { Debug.LogError("[AnimProof] no Idle clip in " + fbxPath); return null; }
        var ctrl = AnimatorController.CreateAnimatorControllerAtPath(ctrlPath);
        var sm = ctrl.layers[0].stateMachine;
        var st = sm.AddState("Idle");
        st.motion = idle;
        sm.defaultState = st;
        Debug.Log("[AnimProof] controller " + ctrlPath + " -> Idle clip '" + idle.name + "' (loop=" + idle.isLooping + ")");
        return ctrl;
    }

    [MenuItem("Tools/WorldOS/CL/B Wire Mecanim Idle (winner)")]
    public static void WireIdle()
    {
        foreach (var pair in new[] {
            new[]{"HeroFighter",HeroFbx(),"Assets/HeroIdle.controller"},
            new[]{"MonsterGoblin",GobFbx(),"Assets/GoblinIdle.controller"} })
        {
            var go = GameObject.Find(pair[0]);
            if (go == null) { Debug.LogError("[AnimProof] not found: " + pair[0]); continue; }
            // the FBX is the child (wrapper holds placement); the Animator goes on the FBX root
            var fbx = go.transform.childCount > 0 ? go.transform.GetChild(0).gameObject : go;
            var anim = fbx.GetComponent<Animator>();
            if (anim == null) anim = fbx.AddComponent<Animator>();
            var avatar = AssetDatabase.LoadAllAssetsAtPath(pair[1]);
            foreach (var o in avatar) { if (o is Avatar) { anim.avatar = (Avatar)o; break; } }
            anim.runtimeAnimatorController = BuildIdleController(pair[1], pair[2]);
            anim.applyRootMotion = false;
            anim.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            Debug.Log("[AnimProof] wired Animator on " + pair[0] + " (avatar=" + (anim.avatar != null) + ")");
        }
        UnityEditor.SceneManagement.EditorSceneManager.MarkSceneDirty(
            UnityEngine.SceneManagement.SceneManager.GetActiveScene());
    }

    // Edit-mode proof: sample the hero Idle at two times and capture, so the limbs differ.
    [MenuItem("Tools/WorldOS/CL/B2 Capture Idle Frames (proof)")]
    public static void CaptureIdleFrames()
    {
        var cam = Camera.main;
        var hero = GameObject.Find("HeroFighter");
        var gob = GameObject.Find("MonsterGoblin");
        var heroFbx = hero.transform.GetChild(0).gameObject;
        var gobFbx = gob.transform.GetChild(0).gameObject;
        AnimationClip heroIdle = FindClip(HeroFbx(), "Idle");
        AnimationClip gobIdle = FindClip(GobFbx(), "Idle");
        AnimationClip heroWalk = FindClip(HeroFbx(), "Walk");

        float[] times = { 0.15f, 0.55f };   // two distinct Idle phases (loop)
        for (int i = 0; i < times.Length; i++)
        {
            float t = times[i] * (heroIdle != null ? heroIdle.length : 1f);
            if (heroIdle != null) heroIdle.SampleAnimation(heroFbx, t);
            float tg = times[i] * (gobIdle != null ? gobIdle.length : 1f);
            if (gobIdle != null) gobIdle.SampleAnimation(gobFbx, tg);
            Capture(cam, "/tmp/1B_idle_frame" + i + ".png");
        }
        // a Walk frame too (prove Walk clip)
        if (heroWalk != null) heroWalk.SampleAnimation(heroFbx, 0.4f * heroWalk.length);
        Capture(cam, "/tmp/1B_walk_frame.png");
        // restore Idle pose
        if (heroIdle != null) heroIdle.SampleAnimation(heroFbx, 0.4f * heroIdle.length);
        if (gobIdle != null) gobIdle.SampleAnimation(gobFbx, 0.4f * gobIdle.length);
        Debug.Log("[AnimProof] captured /tmp/1B_idle_frame0.png, frame1.png, walk_frame.png");
    }

    static AnimationClip FindClip(string fbx, string name)
    {
        // chars_v3 emits one FBX per clip -> search the whole char folder (ClosedLoopBuilder helper).
        return ClosedLoopBuilder.FindClipInDir(fbx, name);
    }

    // resolve the active mesh-source FBX per character (chars_v3 idle.fbx when UseV3, else chars_v2).
    static string HeroFbx() { return ClosedLoopBuilder.UseV3 ? "Assets/chars_v3/hero/glb/idle.fbx" : "Assets/chars_v2/hero/hero.fbx"; }
    static string GobFbx()  { return ClosedLoopBuilder.UseV3 ? "Assets/chars_v3/goblin/glb/idle.fbx" : "Assets/chars_v2/goblin/goblin.fbx"; }

    static void Capture(Camera cam, string outPath)
    {
        var rt = new RenderTexture(1344, 756, 24, RenderTextureFormat.ARGB32);
        rt.Create();
        var prev = cam.targetTexture;
        cam.targetTexture = rt;
        cam.Render();
        var pa = RenderTexture.active;
        RenderTexture.active = rt;
        var tex = new Texture2D(1344, 756, TextureFormat.RGB24, false);
        tex.ReadPixels(new Rect(0, 0, 1344, 756), 0, 0);
        tex.Apply();
        File.WriteAllBytes(outPath, tex.EncodeToPNG());
        RenderTexture.active = pa;
        cam.targetTexture = prev;
        rt.Release();
        Object.DestroyImmediate(tex);
    }
}
