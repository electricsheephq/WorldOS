// WorldOsAutoArmBridge.cs — headless auto-arm for the CoplayDev MCP-For-Unity HTTP bridge.
// WHY: a fresh editor does NOT arm itself (the AutoStartOnLoad pref defaults false AND issue #1121
// means the pref check in InitializeOnLoad runs before EditorPrefs is initialized, so it never fires).
// This script bypasses the pref entirely: on every domain reload, it polls (via EditorApplication.update,
// so services + the local server are ready) and calls MCPServiceLocator.Bridge.StartAsync() ONCE —
// the exact method the panel's "Connect" button uses. Reflection avoids an asmdef reference.
// Place under Assets/Editor/. Idempotent + self-detaching. Remove if/when CoplayDev #1121 lands upstream.
using System;
using System.Reflection;
using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class WorldOsAutoArmBridge
{
    static double _deadline = 0;
    static bool _done = false;

    static WorldOsAutoArmBridge()
    {
        EditorApplication.update += Tick;
    }

    static void Tick()
    {
        if (_done) { EditorApplication.update -= Tick; return; }
        if (_deadline == 0) _deadline = EditorApplication.timeSinceStartup + 45.0;
        if (EditorApplication.timeSinceStartup > _deadline) { _done = true; EditorApplication.update -= Tick; return; }

        try
        {
            Type locator = null;
            foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
            {
                var t = a.GetType("MCPForUnity.Editor.Services.MCPServiceLocator");
                if (t != null) { locator = t; break; }
            }
            if (locator == null) return; // plugin assembly not loaded yet; keep polling

            var bridge = locator.GetProperty("Bridge", BindingFlags.Public | BindingFlags.Static)?.GetValue(null);
            if (bridge == null) return;

            bool running = (bool)bridge.GetType().GetProperty("IsRunning").GetValue(bridge);
            if (running) { Debug.Log("[WorldOS-autoarm] bridge already armed"); _done = true; return; }

            bridge.GetType().GetMethod("StartAsync").Invoke(bridge, null); // fire-and-forget Task<bool>
            Debug.Log("[WorldOS-autoarm] StartAsync invoked -> arming HTTP bridge");
            _done = true; // arm once; if it fails, fall back to manual Connect
        }
        catch (Exception e)
        {
            Debug.LogWarning("[WorldOS-autoarm] " + e.Message);
        }
    }
}
