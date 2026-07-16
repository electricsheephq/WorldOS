// AssetRegistry.cs — Unity twin of viewer/asset_registry.py (gfx M-C, issue #1195).
//
// The renderer NEVER names a literal asset path. It names a SLOT (an assetId
// plus a kind) and asks this registry, which ALWAYS returns a guaranteed
// non-null ref (the real asset OR a default template). This is the asset
// analogue of engine=SOLE-WRITER: swapping/regenerating ANY asset is ZERO
// renderer edits, because the renderer only ever knows slots.
//
// Resolution rule (FIRST hit wins; IDENTICAL to the Python twin):
//     exact -> alias -> defaults[kind] -> defaults["__any__"]   (the "floor")
//
// Guarantees: Resolve() ALWAYS returns a non-null Ref (with defaultUsed and
// resolvedVia set) and NEVER throws (a missing/corrupt registry degrades to an
// in-code hardcoded floor template). VIEW LAYER only — reads, never writes.
//
// JSON parsing: uses the repo's existing MiniJson.cs (extensions/renderers/
// unity/scripts/MiniJson.cs — Parse-only, object -> Dictionary<string,object>).
// No new dependency needed; JsonUtility was NOT used because the registry is a
// map-of-maps (arbitrary asset_id keys) which JsonUtility cannot model.
//
// -------------------------------------------------------------------------
// INTENDED CALL SITE (DO NOT WIRE YET — paint_combat_v1.cs lives on the
// unmerged branch feat/combat-animator-prep, PR #1180). After #1180 merges,
// the renderer's spawn() should resolve by SLOT instead of a literal load:
//
//     // BEFORE (literal path — the anti-pattern this registry removes):
//     // var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(
//     //     "Assets/painterly/models/hero.fbx");
//
//     // AFTER (slot -> registry -> guaranteed non-null ref):
//     AssetRegistry.Ref a = AssetRegistry.Resolve(actor.assetId, actor.kind);
//     var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(a.modelRef);
//     // a.defaultUsed / a.resolvedVia let the renderer log/telemeter misses
//     // (e.g. badge a placeholder) without ever crashing on a missing asset.
//
// Regenerating or swapping hero.fbx => edit registry.json ONLY; spawn() unchanged.
// -------------------------------------------------------------------------

using System;
using System.Collections.Generic;
using System.IO;

public static class AssetRegistry
{
    /// <summary>Resolved asset ref. modelRef/albedoRef/animRef may individually
    /// be null for kinds that don't use them, but the Ref itself is never null.</summary>
    public class Ref
    {
        public string assetId;
        public string kind;
        public string modelRef;
        public string albedoRef;
        public string animRef;
        public string genRecipe;
        public string version;
        public bool defaultUsed;
        public string resolvedVia; // "exact" | "alias" | "default:<kind>" | "floor"
    }

    // Cached parsed registry (loaded lazily, once).
    static bool _loaded;
    static readonly object _lock = new object();
    static Dictionary<string, object> _assets = new Dictionary<string, object>();
    static Dictionary<string, object> _defaults = new Dictionary<string, object>();
    static Dictionary<string, object> _aliases = new Dictionary<string, object>();

    // In-code hardcoded floor — used ONLY if registry.json is missing/corrupt
    // and no usable default exists. Keeps the never-null / never-throw contract.
    static Ref HardcodedFloor(string kind)
    {
        // #1601: the in-code floor (used only when registry.json is missing/corrupt) must be an
        // ANIMATED humanoid, never the clipless Assets/painterly/models/hero.fbx that rendered a
        // sideways T-pose for runtime-spawned rogues. patron_commoner is a rigged humanoid whose
        // idle lives in a separate moveset fbx, so animRef names it. Mirrors asset_registry.py's
        // _HARDCODED_FLOOR and the client's in-code ResolveAsset char default.
        return new Ref
        {
            assetId = "__floor__",
            kind = string.IsNullOrEmpty(kind) ? "character" : kind,
            modelRef = "Assets/chars_v2/patron_commoner/rigged.fbx",
            albedoRef = "Assets/chars_v2/patron_commoner/albedo.jpg",
            animRef = "Assets/chars_v2/patron_commoner/anim_idle.fbx",
            genRecipe = "in-code hardcoded floor (registry.json missing or unreadable)",
            version = "0.0.0",
            defaultUsed = true,
            resolvedVia = "floor",
        };
    }

    /// <summary>Locate data/asset-registry/registry.json. Honors the
    /// WORLDOS_ASSET_REGISTRY env override, else walks up from the Unity project
    /// dir (Application.dataPath's parent at runtime; here we walk from CWD).</summary>
    static string FindRegistryPath()
    {
        string env = Environment.GetEnvironmentVariable("WORLDOS_ASSET_REGISTRY");
        if (!string.IsNullOrEmpty(env) && File.Exists(env)) return env;

        // Walk up from the current directory looking for the repo-root marker.
        // (At Editor runtime the Unity project is a sibling of the WorldOS repo
        // checkout; the env override is the robust path for the box.)
        string cur = Directory.GetCurrentDirectory();
        for (int i = 0; i < 10 && !string.IsNullOrEmpty(cur); i++)
        {
            string candidate = Path.Combine(cur, "data", "asset-registry", "registry.json");
            if (File.Exists(candidate)) return candidate;
            DirectoryInfo parent = Directory.GetParent(cur);
            if (parent == null) break;
            cur = parent.FullName;
        }
        return null;
    }

    static void EnsureLoaded()
    {
        if (_loaded) return;
        lock (_lock)
        {
            if (_loaded) return;
            try
            {
                string path = FindRegistryPath();
                if (!string.IsNullOrEmpty(path))
                {
                    string text = File.ReadAllText(path);
                    var root = MiniJson.Parse(text) as Dictionary<string, object>;
                    if (root != null)
                    {
                        _assets = AsDict(root, "assets");
                        _defaults = AsDict(root, "defaults");
                        _aliases = AsDict(root, "aliases");
                    }
                }
            }
            catch
            {
                // Missing / unreadable / corrupt -> degrade to floor at resolve time.
            }
            _loaded = true;
        }
    }

    /// <summary>Resolve a SLOT to a guaranteed-non-null Ref. NEVER throws.
    /// Rule: exact -> alias -> defaults[kind] -> defaults["__any__"] -> floor.</summary>
    public static Ref Resolve(string assetId, string kind)
    {
        try
        {
            EnsureLoaded();
            string aid = (assetId ?? "").Trim();

            // 1) exact
            if (aid.Length > 0 && _assets.ContainsKey(aid))
                return Row(aid, false, "exact");

            // 2) alias -> asset_id
            if (aid.Length > 0 && _aliases.ContainsKey(aid))
            {
                string target = AsString(_aliases[aid]);
                if (!string.IsNullOrEmpty(target) && _assets.ContainsKey(target))
                    return Row(target, true, "alias");
            }

            // 3) defaults[kind]
            string k = (kind ?? "").Trim();
            if (k.Length > 0 && _defaults.ContainsKey(k))
            {
                string target = AsString(_defaults[k]);
                if (!string.IsNullOrEmpty(target) && _assets.ContainsKey(target))
                    return Row(target, true, "default:" + k);
            }

            // 4) defaults["__any__"]  (the floor)
            if (_defaults.ContainsKey("__any__"))
            {
                string target = AsString(_defaults["__any__"]);
                if (!string.IsNullOrEmpty(target) && _assets.ContainsKey(target))
                    return Row(target, true, "floor");
            }

            // 5) in-code hardcoded floor
            return HardcodedFloor(kind);
        }
        catch
        {
            return HardcodedFloor(kind);
        }
    }

    static Ref Row(string assetId, bool defaultUsed, string resolvedVia)
    {
        var row = _assets.ContainsKey(assetId) ? _assets[assetId] as Dictionary<string, object> : null;
        row = row ?? new Dictionary<string, object>();
        return new Ref
        {
            assetId = assetId,
            kind = AsString(Get(row, "kind")),
            modelRef = AsString(Get(row, "model_ref")),
            albedoRef = AsString(Get(row, "albedo_ref")),
            animRef = AsString(Get(row, "anim_ref")),
            genRecipe = AsString(Get(row, "gen_recipe")),
            version = AsString(Get(row, "version")),
            defaultUsed = defaultUsed,
            resolvedVia = resolvedVia,
        };
    }

    // -- MiniJson dict helpers (MiniJson yields Dictionary<string,object>) ----
    static Dictionary<string, object> AsDict(Dictionary<string, object> parent, string key)
    {
        if (parent != null && parent.ContainsKey(key))
        {
            var d = parent[key] as Dictionary<string, object>;
            if (d != null) return d;
        }
        return new Dictionary<string, object>();
    }

    static object Get(Dictionary<string, object> d, string key)
    {
        return (d != null && d.ContainsKey(key)) ? d[key] : null;
    }

    static string AsString(object o)
    {
        return o == null ? null : o as string;
    }
}
