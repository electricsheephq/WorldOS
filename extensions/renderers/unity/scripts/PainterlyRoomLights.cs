using UnityEngine;

/// <summary>
/// LIVE-ROOM fire globals (#1793 Day 3, "the room is the scene").
///
/// The WorldOS/PainterlyRoom shader takes its point-light term from three SHADER GLOBALS
/// (_WOSFirePos[4] / _WOSFireColor[4] / _WOSFireCount) rather than from Unity's own light loop:
/// Unity demotes ForcePixel lights past `pixelLightCount` to VERTEX lights, and the demoted cool key
/// leaked a blue wash across the crypt, so the vertex-light term was removed from the shader on
/// purpose (Day 2). In the EDITOR those globals are set by the knob script; in the PLAYER nothing
/// sets them, so a baked live room would render with `_WOSFireCount == 0` (flat, fire-less).
///
/// This component is baked onto the live-room prefab root by
/// `Tools/WorldOS/Kit/Bake Live Room Prefab` (build_room_kit.cs) with the values CAPTURED FROM THE
/// SCENE at bake time, so the player never depends on light-discovery order — or on lights at all.
/// The serialized fields are the source of truth; `Resources/LiveRooms/&lt;roomId&gt;.json` is a
/// human-readable fallback for a prefab baked before the fields existed / edited by hand.
///
/// Runtime assembly ONLY — no UnityEditor usage (this ships inside the player).
/// </summary>
[DisallowMultipleComponent]
public class PainterlyRoomLights : MonoBehaviour
{
    // Shader global names — must match extensions/renderers/unity/shaders/PainterlyRoom.shader.
    public const string FirePosGlobal = "_WOSFirePos";
    public const string FireColorGlobal = "_WOSFireColor";
    public const string FireCountGlobal = "_WOSFireCount";
    // The shader declares float4 _WOSFirePos[4] / _WOSFireColor[4]. SetGlobalVectorArray fixes the
    // array size on first use, so ALWAYS submit exactly this many elements (padding with zeros) —
    // a short array would silently resize the global and mismatch the declaration.
    public const int MaxFires = 4;

    [Tooltip("Room id this prefab was baked for (also the Resources/LiveRooms/<id>.json fallback key).")]
    public string roomId = "";
    [Tooltip("xyz = world position of the fire, w = falloff range.")]
    public Vector4[] firePos;
    [Tooltip("rgb = light colour x gain (the painted fire's contribution), a unused.")]
    public Vector4[] fireColor;
    [Tooltip("How many entries of the arrays above are live (0 => the shader's fire term is off).")]
    public int fireCount;

    /// <summary>JSON sidecar shape (JsonUtility — no parser dependency in the runtime assembly).</summary>
    [System.Serializable]
    public class FireGlobals
    {
        public string id;
        public int count;
        public Vector4[] pos;
        public Vector4[] color;
        public CameraGrade grade;
    }

    // ---- camera grade (#1793 Day 3b) --------------------------------------------------------------
    // The blind beauty panel judged a GRADED editor frame: the crypt's room-of-record capture goes
    // through a Beautify component that lives only in the editor scene's memory. The built player must
    // reproduce that grade or it ships a different picture than the one that passed the panel.
    // The values are BAKED (read off the editor camera when present) and applied by the client when a
    // live room is instantiated, so the grade travels with the room rather than with the scene.
    [System.Serializable]
    public class CameraGrade
    {
        public bool bloom = true;
        public float bloomIntensity = 1.1f;
        public float bloomThreshold = 0.9f;
        public float contrast = 1.14f;
        public float saturate = 0.05f;
        public float brightness = 0.98f;
        public float sharpen = 2.5f;
        public bool vignetting = true;
        public float vignettingFade = 0.55f;
        public float dither = 0.02f;
    }

    [Tooltip("Post grade the room was judged under; applied to Camera.main when the room is instantiated.")]
    public CameraGrade grade = new CameraGrade();

    // Beautify ships as a third-party asset compiled into Assembly-CSharp. Reflection (not a direct
    // reference) so this file — and everything that calls it — still COMPILES in a project where the
    // asset is absent, and so a missing grade degrades to "ungraded", never to a build error.
    public const string BeautifyTypeName = "BeautifyEffect.Beautify, Assembly-CSharp";
    static System.Type _beautify; static bool _beautifyResolved;
    public static System.Type BeautifyType()
    {
        if (_beautifyResolved) return _beautify;
        _beautifyResolved = true;
        _beautify = System.Type.GetType(BeautifyTypeName);
        if (_beautify == null)
            foreach (var asm in System.AppDomain.CurrentDomain.GetAssemblies())
            { _beautify = asm.GetType("BeautifyEffect.Beautify"); if (_beautify != null) break; }
        return _beautify;
    }

    static bool SetMember(object target, string name, object value)
    {
        var t = target.GetType();
        var pi = t.GetProperty(name);
        if (pi != null && pi.CanWrite) { pi.SetValue(target, System.Convert.ChangeType(value, pi.PropertyType), null); return true; }
        var fi = t.GetField(name);
        if (fi != null) { fi.SetValue(target, System.Convert.ChangeType(value, fi.FieldType)); return true; }
        return false;
    }

    static bool TryGetMember(object target, string name, out object value)
    {
        value = null;
        var t = target.GetType();
        var pi = t.GetProperty(name);
        if (pi != null && pi.CanRead) { value = pi.GetValue(target, null); return true; }
        var fi = t.GetField(name);
        if (fi != null) { value = fi.GetValue(target); return true; }
        return false;
    }

    /// <summary>Get-or-add the Beautify component on `cam` and push `g` onto it. False when the asset is
    /// absent (or the camera is null) — the caller then reports an UNGRADED player rather than pretending.</summary>
    public static bool ApplyGrade(Camera cam, CameraGrade g)
    {
        if (cam == null || g == null) return false;
        var t = BeautifyType(); if (t == null) return false;
        var comp = cam.GetComponent(t) ?? cam.gameObject.AddComponent(t);
        if (comp == null) return false;
        SetMember(comp, "bloom", g.bloom);
        SetMember(comp, "bloomIntensity", g.bloomIntensity);
        SetMember(comp, "bloomThreshold", g.bloomThreshold);
        SetMember(comp, "contrast", g.contrast);
        SetMember(comp, "saturate", g.saturate);
        SetMember(comp, "brightness", g.brightness);
        SetMember(comp, "sharpen", g.sharpen);
        SetMember(comp, "vignetting", g.vignetting);
        SetMember(comp, "vignettingFade", g.vignettingFade);
        SetMember(comp, "dither", g.dither);
        var beh = comp as Behaviour; if (beh != null) beh.enabled = true;
        return true;
    }

    /// <summary>Switch the grade off (leaving a live room). Never removes the component — a second
    /// enable must not re-run the asset's Awake/OnEnable cost mid-session.</summary>
    public static void DisableGrade(Camera cam)
    {
        if (cam == null) return;
        var t = BeautifyType(); if (t == null) return;
        var beh = cam.GetComponent(t) as Behaviour;
        if (beh != null) beh.enabled = false;
    }

    /// <summary>Read the LIVE grade off a camera (bake time). False => nothing read; `into` is untouched.</summary>
    public static bool ReadGrade(Camera cam, CameraGrade into)
    {
        if (cam == null || into == null) return false;
        var t = BeautifyType(); if (t == null) return false;
        var comp = cam.GetComponent(t); if (comp == null) return false;
        object v;
        if (TryGetMember(comp, "bloom", out v)) into.bloom = System.Convert.ToBoolean(v);
        if (TryGetMember(comp, "bloomIntensity", out v)) into.bloomIntensity = System.Convert.ToSingle(v);
        if (TryGetMember(comp, "bloomThreshold", out v)) into.bloomThreshold = System.Convert.ToSingle(v);
        if (TryGetMember(comp, "contrast", out v)) into.contrast = System.Convert.ToSingle(v);
        if (TryGetMember(comp, "saturate", out v)) into.saturate = System.Convert.ToSingle(v);
        if (TryGetMember(comp, "brightness", out v)) into.brightness = System.Convert.ToSingle(v);
        if (TryGetMember(comp, "sharpen", out v)) into.sharpen = System.Convert.ToSingle(v);
        if (TryGetMember(comp, "vignetting", out v)) into.vignetting = System.Convert.ToBoolean(v);
        if (TryGetMember(comp, "vignettingFade", out v)) into.vignettingFade = System.Convert.ToSingle(v);
        if (TryGetMember(comp, "dither", out v)) into.dither = System.Convert.ToSingle(v);
        return true;
    }

    void OnEnable()
    {
        if (fireCount <= 0 || firePos == null || fireColor == null) LoadFromResources();
        Apply();
    }

    // A room that is torn down (plate swap / room change) must not leave its fires lighting the next
    // room: zeroing the COUNT is enough (the shader's loop breaks at f >= _WOSFireCount).
    void OnDisable() { Shader.SetGlobalFloat(FireCountGlobal, 0f); }

    /// <summary>Push the baked globals to the shader. Public so a caller can re-arm them after
    /// anything else (a capture rig, another room) has overwritten the globals.</summary>
    public void Apply()
    {
        var pos = new Vector4[MaxFires];
        var col = new Vector4[MaxFires];
        int n = Mathf.Clamp(fireCount, 0, MaxFires);
        for (int i = 0; i < n; i++)
        {
            if (firePos != null && i < firePos.Length) pos[i] = firePos[i];
            if (fireColor != null && i < fireColor.Length) col[i] = fireColor[i];
        }
        Shader.SetGlobalVectorArray(FirePosGlobal, pos);
        Shader.SetGlobalVectorArray(FireColorGlobal, col);
        Shader.SetGlobalFloat(FireCountGlobal, n);
    }

    // Fallback only (see the class doc): Resources/LiveRooms/<roomId>.json beside the prefab.
    void LoadFromResources()
    {
        if (string.IsNullOrEmpty(roomId)) return;
        var ta = Resources.Load<TextAsset>("LiveRooms/" + roomId);
        if (ta == null) { Debug.LogWarning("[PainterlyRoomLights] no baked fires and no Resources/LiveRooms/" + roomId + ".json"); return; }
        try
        {
            var g = JsonUtility.FromJson<FireGlobals>(ta.text);
            if (g == null) return;
            firePos = g.pos; fireColor = g.color; fireCount = g.count;
            if (g.grade != null) grade = g.grade;
        }
        catch (System.Exception e) { Debug.LogWarning("[PainterlyRoomLights] " + roomId + ".json parse: " + e.Message); }
    }
}
