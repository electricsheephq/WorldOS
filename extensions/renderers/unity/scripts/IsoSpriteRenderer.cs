using UnityEngine;
using UnityEditor;
using System.Collections.Generic;
using System.IO;

/// <summary>
/// ARM 1A helper: render each in-scene rigged actor (posed to Idle) from the LOCKED
/// dimetric angle (pitch atan(0.5)=26.565deg) to a transparent-bg high-res PNG, for
/// Scenario painterly img2img stylization. Output: Assets/painterly/sprites/&lt;id&gt;_raw.png.
/// </summary>
public static class IsoSpriteRenderer
{
    [MenuItem("Tools/WorldOS/CL/A0 Yaw Probe (hero)")]
    public static void YawProbe()
    {
        var go = GameObject.Find("HeroFighter");
        if (go == null) { Debug.LogError("[YawProbe] no hero"); return; }
        var prevE = go.transform.localEulerAngles;
        float pitch = Mathf.Rad2Deg * Mathf.Atan(0.5f);
        var allRends = Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None);
        var rs = go.GetComponentsInChildren<Renderer>();
        float[] yaws = { 0f, 90f, 180f, 270f };
        foreach (var yaw in yaws)
        {
            go.transform.localEulerAngles = new Vector3(prevE.x, yaw, prevE.z);
            var b = rs[0].bounds; for (int i = 1; i < rs.Length; i++) b.Encapsulate(rs[i].bounds);
            var camGO = new GameObject("__P"); var cam = camGO.AddComponent<Camera>();
            cam.orthographic = true; cam.orthographicSize = b.size.y * 0.62f;
            cam.clearFlags = CameraClearFlags.SolidColor; cam.backgroundColor = new Color(0.3f, 0.3f, 0.35f, 1f);
            Vector3 cd = Quaternion.Euler(pitch, 0, 0) * Vector3.forward;
            camGO.transform.position = b.center - cd * 50f; camGO.transform.rotation = Quaternion.Euler(pitch, 0, 0);
            var prev = new Dictionary<Renderer, bool>();
            foreach (var rr in allRends) { prev[rr] = rr.enabled; bool mine = false; foreach (var m in rs) if (m == rr) mine = true; if (!mine) rr.enabled = false; }
            var rt = new RenderTexture(256, 384, 24); rt.Create(); cam.aspect = 256f / 384f; cam.targetTexture = rt; cam.Render();
            var pr = RenderTexture.active; RenderTexture.active = rt; var tex = new Texture2D(256, 384, TextureFormat.RGB24, false);
            tex.ReadPixels(new Rect(0, 0, 256, 384), 0, 0); tex.Apply();
            File.WriteAllBytes("/tmp/yaw_" + yaw.ToString("F0") + ".png", tex.EncodeToPNG());
            RenderTexture.active = pr; cam.targetTexture = null; rt.Release(); Object.DestroyImmediate(tex);
            foreach (var rr in allRends) rr.enabled = prev[rr]; Object.DestroyImmediate(camGO);
        }
        go.transform.localEulerAngles = prevE;
        Debug.Log("[YawProbe] wrote /tmp/yaw_{0,90,180,270}.png");
    }

    [MenuItem("Tools/WorldOS/CL/A Render Iso Sprites (raw)")]
    public static void RenderIsoSprites()
    {
        string dir = "Assets/painterly/sprites";
        if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
        float pitch = Mathf.Rad2Deg * Mathf.Atan(0.5f);
        string[] names = { "HeroFighter", "MonsterGoblin" };
        string[] outs = { "hero_idle", "goblin_idle" };

        var allRends = Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None);
        for (int k = 0; k < names.Length; k++)
        {
            var go = GameObject.Find(names[k]);
            if (go == null) { Debug.LogError("[IsoSprite] not found: " + names[k]); continue; }

            // Face the actor TOWARD the camera for a 3/4 FRONT sprite (the in-scene pose
            // yaws ~180 = facing away; the player wants the face). Hero angled camera-left,
            // goblin camera-right so a future composite can angle them at each other.
            var prevEuler = go.transform.localEulerAngles;
            // VERIFIED via yaw-probe: yaw 180 turns the FACE toward the iso cam (yaw 0 = back).
            // Gentle 3/4 offset keeps the face visible while breaking dead face-on symmetry.
            float frontYaw = 180f + ((k == 0) ? -15f : 15f);   // hero right-3/4, goblin left-3/4
            go.transform.localEulerAngles = new Vector3(prevEuler.x, frontYaw, prevEuler.z);

            var rs = go.GetComponentsInChildren<Renderer>();
            var b = rs[0].bounds;
            for (int i = 1; i < rs.Length; i++) b.Encapsulate(rs[i].bounds);

            var camGO = new GameObject("__IsoCam");
            var cam = camGO.AddComponent<Camera>();
            cam.orthographic = true;
            cam.orthographicSize = b.size.y * 0.62f;
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0f, 0f, 0f, 0f);
            cam.nearClipPlane = 0.01f;
            cam.farClipPlane = 200f;
            Vector3 camDir = Quaternion.Euler(pitch, 0f, 0f) * Vector3.forward;
            camGO.transform.position = b.center - camDir * 50f;
            camGO.transform.rotation = Quaternion.Euler(pitch, 0f, 0f);

            // isolate this actor (transparent cutout)
            var prevEnabled = new Dictionary<Renderer, bool>();
            foreach (var rr in allRends)
            {
                prevEnabled[rr] = rr.enabled;
                bool mine = false;
                foreach (var m in rs) if (m == rr) mine = true;
                if (!mine) rr.enabled = false;
            }

            int H = 1024;
            int W = Mathf.Clamp(Mathf.RoundToInt(H * (b.size.x * 1.3f) / (cam.orthographicSize * 2f)), 256, 1024);
            cam.aspect = (float)W / H;
            var rt = new RenderTexture(W, H, 24, RenderTextureFormat.ARGB32);
            rt.antiAliasing = 4;
            rt.Create();
            cam.targetTexture = rt;
            cam.Render();
            var prevRT = RenderTexture.active;
            RenderTexture.active = rt;
            var tex = new Texture2D(W, H, TextureFormat.RGBA32, false);
            tex.ReadPixels(new Rect(0, 0, W, H), 0, 0);
            tex.Apply();
            File.WriteAllBytes(dir + "/" + outs[k] + "_raw.png", tex.EncodeToPNG());
            RenderTexture.active = prevRT;
            cam.targetTexture = null;
            rt.Release();
            Object.DestroyImmediate(tex);

            foreach (var rr in allRends) rr.enabled = prevEnabled[rr];
            Object.DestroyImmediate(camGO);
            go.transform.localEulerAngles = prevEuler;   // restore in-scene pose
            Debug.Log("[IsoSprite] " + outs[k] + " rendered " + W + "x" + H + " boundsH=" + b.size.y.ToString("F2"));
        }
        AssetDatabase.Refresh();
        Debug.Log("[IsoSprite] done -> " + dir);
    }
}
