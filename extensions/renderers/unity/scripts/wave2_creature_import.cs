// wave2_creature_import.cs — #1305 wave-2: import ONE Tripo creature (giant_spider [octopod] or
// dire_rat [quadruped]) as animationType=Generic (Tripo bone names don't auto-map to Unity's Humanoid
// avatar; Generic preserves the retargeted clips, per TRIPO_PIPELINE.md / wave-1 ghoul precedent).
// Parameterized via CREATURE_NAME below — edit + re-run per creature (keeps each heavy FBX import as
// its own bounded op, per the gex44-unity-host skill's "one op at a time" discipline for large Tripo
// FBX files).
// Run: unity-mcp code execute --no-safety-checks -f wave2_creature_import.cs
AssetDatabase.Refresh();
string who = "__CREATURE_NAME__";
string dir = "Assets/chars_v2/" + who;
var report = new System.Text.StringBuilder();

string riggedPath = dir + "/rigged.fbx";
var riggedImp = AssetImporter.GetAtPath(riggedPath) as ModelImporter;
if (riggedImp == null) { return who + ": MISSING rigged.fbx at " + riggedPath; }
riggedImp.animationType = ModelImporterAnimationType.Generic;
EditorUtility.SetDirty(riggedImp);
riggedImp.SaveAndReimport();

string walkPath = dir + "/anim_walk.fbx";
int clipsDone = 0;
if (System.IO.File.Exists(walkPath)) {
    var walkImp = AssetImporter.GetAtPath(walkPath) as ModelImporter;
    if (walkImp != null) {
        walkImp.animationType = ModelImporterAnimationType.Generic;
        EditorUtility.SetDirty(walkImp);
        walkImp.SaveAndReimport();
        clipsDone++;
    }
}

report.AppendLine(who + ": rigged.animationType=" + riggedImp.animationType + " clipsImported=" + clipsDone);
return report.ToString();
