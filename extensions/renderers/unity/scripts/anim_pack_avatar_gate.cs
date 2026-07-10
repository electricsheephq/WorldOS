// anim_pack_avatar_gate.cs — #1408 avatar gate. For every registry character/monster MODEL fbx, test
// whether its avatar is a VALID humanoid (the precondition for the RPG-pack controller to retarget). A
// broken/generic avatar is re-imported as Humanoid with auto-mapping (CreateFromThisModel) and re-tested.
// Genuinely-unfixable rigs (auto-map can't resolve the required bones) are reported NEEDS_REMODEL — the
// runtime keeps them on the per-frame graph fallback and the registry marks them needs_remodel (no hand-rig).
//
// Run on the GEX44 box:  unity-mcp code execute --no-safety-checks -f anim_pack_avatar_gate.cs
// NO top-level `using`; no LINQ. Prints one MODEL line per model for the caller to fold into registry.json.
var sb = new System.Text.StringBuilder();

// Quadrupeds/vermin are non-humanoid BY DESIGN — never force them to Humanoid (they keep the fallback).
var skipBiped = new System.Collections.Generic.HashSet<string>();
skipBiped.Add("giant_spider"); skipBiped.Add("dire_rat");

// Read the project-root registry.json (the copy EnsurePackaged reads) and collect character/monster models.
string projRoot = System.IO.Directory.GetParent(UnityEngine.Application.dataPath).FullName;
string regPath = System.IO.Path.Combine(projRoot, "registry.json");
if (!System.IO.File.Exists(regPath)) { return "NO registry.json at " + regPath; }
var root = MiniJson.Parse(System.IO.File.ReadAllText(regPath)) as System.Collections.Generic.Dictionary<string, object>;
var assets = (root != null && root.ContainsKey("assets")) ? root["assets"] as System.Collections.Generic.Dictionary<string, object> : null;
if (assets == null) { return "NO assets block in registry.json"; }

// Load an FBX's embedded Avatar sub-asset (null if none).
System.Func<string, UnityEngine.Avatar> avatarOf = (path) => {
    var all = UnityEditor.AssetDatabase.LoadAllAssetsAtPath(path);
    for (int i = 0; i < all.Length; i++) { var av = all[i] as UnityEngine.Avatar; if (av != null) return av; }
    return null;
};

var seen = new System.Collections.Generic.HashSet<string>();
foreach (var kv in assets) {
    var row = kv.Value as System.Collections.Generic.Dictionary<string, object>; if (row == null) continue;
    string kind = row.ContainsKey("kind") ? row["kind"] as string : "";
    if (kind != "character" && kind != "monster") continue;
    string model = row.ContainsKey("model_ref") ? row["model_ref"] as string : null;
    if (string.IsNullOrEmpty(model) || !model.ToLower().EndsWith(".fbx")) continue;
    if (seen.Contains(model)) continue; seen.Add(model);
    string slug = kv.Key;

    if (string.IsNullOrEmpty(UnityEditor.AssetDatabase.AssetPathToGUID(model))) { sb.AppendLine("MODEL " + slug + " " + model + " -> MISSING_ON_DISK"); continue; }
    if (skipBiped.Contains(slug)) { sb.AppendLine("MODEL " + slug + " " + model + " -> SKIP_NONBIPED"); continue; }

    var imp = UnityEditor.AssetImporter.GetAtPath(model) as UnityEditor.ModelImporter;
    if (imp == null) { sb.AppendLine("MODEL " + slug + " " + model + " -> NO_MODEL_IMPORTER"); continue; }

    var av0 = avatarOf(model);
    bool h0 = av0 != null && av0.isHuman; bool v0 = av0 != null && av0.isValid;
    string before = "H" + (h0 ? "1" : "0") + "V" + (v0 ? "1" : "0");

    if (h0 && v0) { sb.AppendLine("MODEL " + slug + " " + model + " before=" + before + " -> ALREADY_HUMANOID"); continue; }

    // Attempt the fix: re-import as Humanoid with auto-mapping from this model's own skeleton. Capture the
    // original importer settings first so a FAILED fix restores them (never leave a half-changed import that
    // couldn't become a valid humanoid — the runtime keeps such a rig on the per-frame fallback either way).
    var origType = imp.animationType;
    var origSetup = imp.avatarSetup;
    imp.animationType = UnityEditor.ModelImporterAnimationType.Human;
    imp.avatarSetup = UnityEditor.ModelImporterAvatarSetup.CreateFromThisModel;
    imp.SaveAndReimport();
    UnityEditor.AssetDatabase.ImportAsset(model, UnityEditor.ImportAssetOptions.ForceUpdate);

    var av1 = avatarOf(model);
    bool h1 = av1 != null && av1.isHuman; bool v1 = av1 != null && av1.isValid;
    string after = "H" + (h1 ? "1" : "0") + "V" + (v1 ? "1" : "0");
    bool fixedOk = h1 && v1;
    if (!fixedOk)
    {
        // restore the original import settings so a needs-remodel rig is left exactly as it was found.
        imp.animationType = origType;
        imp.avatarSetup = origSetup;
        imp.SaveAndReimport();
        UnityEditor.AssetDatabase.ImportAsset(model, UnityEditor.ImportAssetOptions.ForceUpdate);
    }
    string verdict = fixedOk ? "FIXED" : "NEEDS_REMODEL";
    sb.AppendLine("MODEL " + slug + " " + model + " before=" + before + " after=" + after + " -> " + verdict);
}
UnityEditor.AssetDatabase.SaveAssets();
return sb.ToString();
