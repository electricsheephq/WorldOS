// wave2_monster_import.cs — #1305 wave-2 monster cast: import the 4 Meshy HUMANOID moveset actors
// (zombie, bandit, cult_leader, animated_armor) as animationType=Humanoid DIRECTLY (per the #1412
// lesson: Meshy rigs are Humanoid-classifiable; a Generic import silently drops the retarget path),
// mirroring chars_v2_wave2_humanoid_fix.cs's proven pattern but applied to ALL 9 moveset clips, not
// just anim_idle.fbx. rigged.fbx = avatar SOURCE (CreateFromThisModel); every anim_*.fbx = avatar
// COPIED from rigged.fbx's own avatar (CopyFromOther) so all clips + rigged share one retargetable
// Humanoid avatar.
// Run: unity-mcp code execute --no-safety-checks -f wave2_monster_import.cs
AssetDatabase.Refresh();
string[] chars = { "zombie", "bandit", "cult_leader", "animated_armor" };
string[] clips = { "idle", "walk", "run", "attack", "cast", "block", "dodge", "hit", "death" };
var report = new System.Text.StringBuilder();

foreach (var who in chars)
{
    string dir = "Assets/chars_v2/" + who;
    string riggedPath = dir + "/rigged.fbx";

    var riggedImp = AssetImporter.GetAtPath(riggedPath) as ModelImporter;
    if (riggedImp == null) { report.AppendLine(who + ": MISSING rigged.fbx at " + riggedPath); continue; }
    riggedImp.animationType = ModelImporterAnimationType.Human;
    riggedImp.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
    EditorUtility.SetDirty(riggedImp);
    riggedImp.SaveAndReimport();

    Avatar srcAvatar = null;
    foreach (var o in AssetDatabase.LoadAllAssetsAtPath(riggedPath))
        if (o is Avatar) { srcAvatar = (Avatar)o; break; }

    int clipsDone = 0;
    foreach (var clip in clips)
    {
        string clipPath = dir + "/anim_" + clip + ".fbx";
        if (!System.IO.File.Exists(clipPath)) continue;
        var clipImp = AssetImporter.GetAtPath(clipPath) as ModelImporter;
        if (clipImp == null) { report.AppendLine(who + ": not a model: " + clipPath); continue; }
        clipImp.animationType = ModelImporterAnimationType.Human;
        if (srcAvatar != null) { clipImp.avatarSetup = ModelImporterAvatarSetup.CopyFromOther; clipImp.sourceAvatar = srcAvatar; }
        else { clipImp.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel; }
        EditorUtility.SetDirty(clipImp);
        clipImp.SaveAndReimport();
        clipsDone++;
    }

    Avatar verifyAvatar = null;
    foreach (var o in AssetDatabase.LoadAllAssetsAtPath(riggedPath))
        if (o is Avatar) { verifyAvatar = (Avatar)o; break; }
    bool isHuman = verifyAvatar != null && verifyAvatar.isValid && verifyAvatar.isHuman;
    report.AppendLine(who + ": rigged.animationType=" + riggedImp.animationType +
        " avatar.isValid=" + (verifyAvatar != null && verifyAvatar.isValid) +
        " avatar.isHuman=" + isHuman + " clipsImported=" + clipsDone + "/" + clips.Length);
}
return report.ToString();
