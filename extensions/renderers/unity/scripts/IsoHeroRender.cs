using UnityEngine;
using UnityEditor;
using System.Collections.Generic;

// Render the in-scene HERO actor in ISOLATION (no plate, no goblin, no shadow) against a neutral
// background, framed tight, for the OFFLINE Scenario img2img paint-pass (ceiling-proof evidence).
public static class IsoHeroRender
{
    [MenuItem("Tools/WorldOS/CL/Z Render Hero Isolated")]
    public static void RenderHeroIso()
    {
        var root = GameObject.Find("ClosedLoopRoot");
        if (root == null) { Debug.LogError("[ISO] no ClosedLoopRoot"); return; }
        Transform hero = null;
        foreach (var t in root.GetComponentsInChildren<Transform>(true))
            if (t.name == "HeroFighter") { hero = t; break; }
        if (hero == null) { Debug.LogError("[ISO] no HeroFighter"); return; }

        var cam = Camera.main;
        var all = root.GetComponentsInChildren<Renderer>(true);
        var prev = new Dictionary<Renderer, bool>();
        foreach (var r in all) { prev[r] = r.enabled; r.enabled = false; }
        foreach (var r in hero.GetComponentsInChildren<Renderer>(true)) r.enabled = true;

        // frame the hero: temporarily reposition an ISOLATED camera (don't disturb the locked one).
        var isoGO = new GameObject("IsoCam");
        var iso = isoGO.AddComponent<Camera>();
        iso.orthographic = true;
        var hb = new Bounds(hero.position, Vector3.one * 0.1f);
        foreach (var r in hero.GetComponentsInChildren<Renderer>()) hb.Encapsulate(r.bounds);
        // Start from the LOCKED main camera (which correctly frames the room), then DOLLY along the
        // camera's local right/up so the hero's world-center projects to the frame center, and shrink
        // orthoSize to fill. An ortho camera's projection is position-invariant in depth, so sliding it
        // in its own right/up plane just recomposes — the dimetric pitch is preserved.
        iso.transform.position = cam.transform.position;
        iso.transform.rotation = cam.transform.rotation;     // same dimetric pitch
        iso.orthographic = true;
        iso.orthographicSize = Mathf.Max(2.5f, hb.size.y * 0.66f);
        iso.aspect = 768f / 1024f;
        // shift camera so hero center is centered: project hero center to viewport, move by the delta.
        Vector3 vp = iso.WorldToViewportPoint(hb.center);
        Vector3 right = iso.transform.right;
        Vector3 up = iso.transform.up;
        float worldH = iso.orthographicSize * 2f;
        float worldW = worldH * iso.aspect;
        iso.transform.position += right * ((vp.x - 0.5f) * worldW) + up * ((vp.y - 0.5f) * worldH);
        iso.clearFlags = CameraClearFlags.SolidColor;
        iso.backgroundColor = new Color(0.5f, 0.5f, 0.52f, 1f);
        iso.nearClipPlane = 0.1f; iso.farClipPlane = 200f;
        iso.aspect = 768f / 1024f;

        var rt = new RenderTexture(768, 1024, 24, RenderTextureFormat.ARGB32); rt.Create();
        iso.targetTexture = rt;
        iso.Render();
        var t2 = new Texture2D(768, 1024, TextureFormat.RGB24, false);
        RenderTexture.active = rt; t2.ReadPixels(new Rect(0, 0, 768, 1024), 0, 0); t2.Apply(); RenderTexture.active = null;
        System.IO.File.WriteAllBytes("/Volumes/LEXAR/WorldOS-Unity-spike/Assets/painterly/hero_iso.png", t2.EncodeToPNG());
        iso.targetTexture = null; rt.Release();
        float oszLog = iso.orthographicSize;
        Object.DestroyImmediate(isoGO);
        foreach (var r in all) if (prev.ContainsKey(r)) r.enabled = prev[r];
        AssetDatabase.Refresh();
        Debug.Log("[ISO] hero_iso.png written (orthoSize " + oszLog.ToString("F2") + ")");
    }
}
