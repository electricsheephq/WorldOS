using UnityEngine;
using UnityEditor;
using System.IO;

public static class RTCapture
{
    [MenuItem("Tools/RT Capture Frames")]
    public static void CaptureFrames()
    {
        var camGO = GameObject.Find("Main Camera");
        if (camGO == null) { Debug.LogError("No Main Camera"); return; }
        var cam = camGO.GetComponent<Camera>();
        var heroGO = GameObject.Find("HeroFighter");
        var heroAnim = heroGO != null ? heroGO.GetComponentInChildren<Animator>() : null;
        var gobGO = GameObject.Find("MonsterGoblin");
        var gobAnim = gobGO != null ? gobGO.GetComponentInChildren<Animator>() : null;
        int w = 1280, h = 720;
        var rt = new RenderTexture(w, h, 24, RenderTextureFormat.ARGB32);
        rt.antiAliasing = 1;
        rt.Create();
        var origTarget = cam.targetTexture;
        cam.targetTexture = rt;
        float[] nTs = { 0.04f, 0.20f, 0.37f, 0.54f, 0.71f, 0.87f };
        for (int i = 0; i < nTs.Length; i++)
        {
            float nt = nTs[i];
            if (heroAnim != null) { heroAnim.Play("Attack", -1, nt); heroAnim.Update(0f); }
            if (gobAnim != null) { gobAnim.Play("Attack", -1, (nt + 0.15f) % 1f); gobAnim.Update(0f); }
            cam.Render();
            RenderTexture.active = rt;
            var tex = new Texture2D(w, h, TextureFormat.RGB24, false);
            tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
            tex.Apply();
            RenderTexture.active = null;
            string path = "/Volumes/LEXAR/WorldOS-Unity-spike/Captures/rt_f" + (i+1).ToString("D2") + ".png";
            File.WriteAllBytes(path, tex.EncodeToPNG());
            Object.DestroyImmediate(tex);
            Debug.Log("RT captured frame " + (i+1) + " nT=" + nt);
        }
        cam.targetTexture = origTarget;
        rt.Release();
        Object.DestroyImmediate(rt);
        Debug.Log("RT capture DONE");
    }
}
