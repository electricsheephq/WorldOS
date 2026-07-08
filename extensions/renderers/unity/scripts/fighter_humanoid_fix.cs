// fighter_humanoid_fix.cs — #1418 item (a): the FIGHTER (Aldric, the party PC) T-poses in the composed
// rest scene. Root cause (two stacked bugs, both fixed by this pass):
//
//  1) registry.json had NO "aldric" alias, so the token's slugified name ("aldric") never matched the
//     "fighter" asset key, silently falling through to defaults["character"]=template_human -> the
//     stale placeholder Assets/painterly/models/hero.fbx (Generic, avatarSetup=NoAvatar, 0 embedded
//     clips, no SkinnedMeshRenderer at all -- confirmed via Probe1418Inspect.cs on this box). Same class
//     of bug as #1412's missing "patron"->"patron_commoner" alias. Fixed in registry.json (this PR).
//  2) The REAL current-generation skinned Aldric asset already deployed to the box on 2026-07-01
//     (Assets/cast/fighter/fighter.fbx: SkinnedMeshRenderer, 24 bones, rootBone=Hips, its OWN embedded
//     "Idle" clip) shipped as animationType=Generic / avatarSetup=NoAvatar (confirmed via Probe1418Inspect.cs:
//     "NO Animator/avatar at all" -- matches paint_combat_replay_v1.cs's #1397 probe comment). With no
//     Animator, paint_combat_v1.cs's spawn() can't reach EITHER posing path: not the donor-retarget branch
//     (`_anim!=null && _anim.avatar!=null && _anim.avatar.isHuman` -- no Animator to test), so the actor was
//     left in its completely unposed FBX bind pose (T-pose). Registry.json now points "fighter" straight at
//     this file (no separate anim_ref -- the Idle clip is embedded in the model itself, read by spawn()'s
//     poseClipPath fallback the same way goblin.fbx's own embedded Idle already works).
//
// Fix (this script): flip ModelImporter.animationType Generic(2) -> Humanoid(3), avatarSetup =
// CreateFromThisModel, SaveAndReimport, verify avatar.isHuman -- IDENTICAL pattern to
// chars_v2_wave2_humanoid_fix.cs (#1412), extended to this asset ("donor-avatar approach" from #1418's
// suggested fix directions: give the actor its OWN valid Humanoid avatar so it can pose itself from its
// embedded Idle clip; the existing donor-retarget branch also becomes reachable as a safety net for any
// FUTURE clipless cast member once this asset has a real avatar).
//
// Run: create_script -> refresh_unity(compile) -> execute_menu_item("Tools/WorldOS/Fix1418 Fighter Humanoid Import") -> read_console -> delete_script.
AssetDatabase.Refresh();
string path = "Assets/cast/fighter/fighter.fbx";
var report = new System.Text.StringBuilder();
var mi = AssetImporter.GetAtPath(path) as ModelImporter;
if (mi == null) { report.AppendLine("MISSING fighter.fbx at " + path); return report.ToString(); }
mi.animationType = ModelImporterAnimationType.Human;
mi.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
EditorUtility.SetDirty(mi);
mi.SaveAndReimport();

Avatar verifyAvatar = null;
int nClips = 0;
foreach (var o in AssetDatabase.LoadAllAssetsAtPath(path))
{
    if (o is Avatar a) verifyAvatar = a;
    if (o is AnimationClip) nClips++;
}
bool isHuman = verifyAvatar != null && verifyAvatar.isValid && verifyAvatar.isHuman;
report.AppendLine("fighter.fbx: animationType=" + mi.animationType +
    " avatar.isValid=" + (verifyAvatar != null && verifyAvatar.isValid) +
    " avatar.isHuman=" + isHuman + " nClips(incl.__preview__)=" + nClips);

AssetDatabase.Refresh();
return report.ToString();
