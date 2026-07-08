// chars_v2_wave2_humanoid_fix.cs — #1412 fix: the wave-2 cast (mage, innkeeper, patron_commoner;
// wave-2 import PR #1410) landed on the box as animationType=Generic, but they are Meshy HUMANOID
// moveset rigs (same family as goblin, which imports as Humanoid / avatar.isHuman=True). Through
// paint_combat_v1.cs they miss the #1411 PlayableGraph idle retarget (which requires isHuman) and
// fail floor-contact/screen-scale/pose-uprightness, while the SAME models render fine when driven
// standalone (the wave-2 import lane bypasses the retarget path).
//
// Fix: flip ModelImporter.animationType Generic(2) -> Humanoid(3) for BOTH rigged.fbx (avatar
// SOURCE, CreateFromThisModel) and anim_idle.fbx (avatar COPIED from rigged.fbx) per actor,
// SaveAndReimport, then verify avatar.isHuman on the reimported rigged.fbx.
//
// Run: unity-mcp code execute --no-safety-checks -f chars_v2_wave2_humanoid_fix.cs
AssetDatabase.Refresh();
string[] chars = { "mage", "innkeeper", "patron_commoner" };
var report = new System.Text.StringBuilder();

foreach (var who in chars)
{
    string dir = "Assets/chars_v2/" + who;
    string riggedPath = dir + "/rigged.fbx";
    string idlePath = dir + "/anim_idle.fbx";

    // 1) rigged.fbx: avatar SOURCE, Humanoid, CreateFromThisModel.
    var riggedImp = AssetImporter.GetAtPath(riggedPath) as ModelImporter;
    if (riggedImp == null) { report.AppendLine(who + ": MISSING rigged.fbx at " + riggedPath); continue; }
    riggedImp.animationType = ModelImporterAnimationType.Human;
    riggedImp.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
    EditorUtility.SetDirty(riggedImp);
    riggedImp.SaveAndReimport();

    // 2) anim_idle.fbx: Humanoid, avatar COPIED from the rigged.fbx's own avatar.
    var idleImp = AssetImporter.GetAtPath(idlePath) as ModelImporter;
    if (idleImp == null) { report.AppendLine(who + ": MISSING anim_idle.fbx at " + idlePath); }
    else
    {
        Avatar srcAvatar = null;
        foreach (var o in AssetDatabase.LoadAllAssetsAtPath(riggedPath))
            if (o is Avatar) { srcAvatar = (Avatar)o; break; }
        idleImp.animationType = ModelImporterAnimationType.Human;
        if (srcAvatar != null)
        {
            idleImp.avatarSetup = ModelImporterAvatarSetup.CopyFromOther;
            idleImp.sourceAvatar = srcAvatar;
        }
        else
        {
            // fallback: create its own avatar rather than silently staying Generic.
            idleImp.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
        }
        EditorUtility.SetDirty(idleImp);
        idleImp.SaveAndReimport();
    }

    // 3) verify avatar.isHuman on the reimported rigged.fbx.
    Avatar verifyAvatar = null;
    foreach (var o in AssetDatabase.LoadAllAssetsAtPath(riggedPath))
        if (o is Avatar) { verifyAvatar = (Avatar)o; break; }
    bool isHuman = verifyAvatar != null && verifyAvatar.isValid && verifyAvatar.isHuman;
    report.AppendLine(who + ": rigged.animationType=" + riggedImp.animationType +
        " avatar.isValid=" + (verifyAvatar != null && verifyAvatar.isValid) +
        " avatar.isHuman=" + isHuman);
}

AssetDatabase.Refresh();
return report.ToString();
