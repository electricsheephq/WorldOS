using UnityEngine;
using UnityEditor;
using System.IO;

/// <summary>
/// Configure the chars_v3 (Meshy hi-tier: meshy-6, 300K->140K remeshed, 4K hd_texture,
/// remove_lighting) rigged FBXs for Unity: import as HUMANOID, name each animation FBX's single
/// take to Idle/Walk/Attack, set Idle+Walk to loop, copy the avatar from the rigged FBX so all
/// clips share one Humanoid avatar (retargetable). The Meshy rig+animation API emits SEPARATE FBX
/// files per clip (rigged/idle/walk/attack) — unlike chars_v2's single multi-take hero.fbx — so the
/// scene code now searches the whole char folder for a named clip (see ClosedLoopBuilder.FindClipInDir).
/// </summary>
public static class CharsV3Import
{
    static readonly string[] Chars = { "hero", "goblin" };

    [MenuItem("Tools/WorldOS/CL/V3 Configure chars_v3 FBX (Humanoid + clips)")]
    public static void Configure()
    {
        foreach (var who in Chars)
        {
            string dir = "Assets/chars_v3/" + who + "/glb";
            // 1) rigged.fbx = the avatar SOURCE (Create From This Model), Humanoid.
            ConfigureFbx(dir + "/rigged.fbx", null, false, true, null);
            // 2) animation FBXs: Humanoid avatar COPIED from rigged.fbx; rename the take.
            string riggedAvatar = dir + "/rigged.fbx";
            ConfigureFbx(dir + "/idle.fbx",   "Idle",   true,  false, riggedAvatar);
            ConfigureFbx(dir + "/walk.fbx",   "Walk",   true,  false, riggedAvatar);
            ConfigureFbx(dir + "/attack.fbx", "Attack", false, false, riggedAvatar);
        }
        AssetDatabase.Refresh();
        Debug.Log("[V3] Configured chars_v3 FBX importers (Humanoid + named clips). Reimporting...");
    }

    static void ConfigureFbx(string path, string clipName, bool loop, bool createAvatar, string copyAvatarFrom)
    {
        if (!File.Exists(path)) { Debug.LogWarning("[V3] missing " + path); return; }
        var imp = AssetImporter.GetAtPath(path) as ModelImporter;
        if (imp == null) { Debug.LogWarning("[V3] not a model: " + path); return; }

        imp.animationType = ModelImporterAnimationType.Human;
        imp.importAnimation = true;
        imp.importBlendShapes = false;
        imp.materialImportMode = ModelImporterMaterialImportMode.ImportStandard;
        imp.materialLocation = ModelImporterMaterialLocation.InPrefab;

        if (createAvatar)
        {
            imp.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
        }
        else if (!string.IsNullOrEmpty(copyAvatarFrom))
        {
            var src = AssetDatabase.LoadAssetAtPath<Avatar>(copyAvatarFrom);
            if (src == null)
            {
                foreach (var o in AssetDatabase.LoadAllAssetsAtPath(copyAvatarFrom))
                    if (o is Avatar) { src = (Avatar)o; break; }
            }
            if (src != null)
            {
                imp.avatarSetup = ModelImporterAvatarSetup.CopyFromOther;
                imp.sourceAvatar = src;
            }
        }

        // name the single take + loop
        if (!string.IsNullOrEmpty(clipName))
        {
            var clips = imp.defaultClipAnimations;
            if (clips != null && clips.Length > 0)
            {
                clips[0].name = clipName;
                clips[0].loopTime = loop;
                imp.clipAnimations = clips;
            }
        }
        EditorUtility.SetDirty(imp);
        imp.SaveAndReimport();
        Debug.Log("[V3] configured " + path + (clipName != null ? " clip=" + clipName + " loop=" + loop : " (avatar src)"));
    }
}
