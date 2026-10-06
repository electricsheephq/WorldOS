# #1628 BG3-Style Demo Asset Library — GEX44 Deploy Package

**68 Tripo3D assets** (33 rigged + animated, 35 static props) for the WorldOS PoE2-style demo:
12 BG3-class party members in signature races, 6 town NPCs, 13 creatures, 2 bosses
(young red dragon 4×4 Gargantuan + ogre chieftain), 35 town/dungeon props.
Source issue: electricsheephq/WorldOS#1628 · code branch: `codex/1628-bg3-demo-asset-library` (PR #1635).

## Package layout (this tarball)

```
Assets/cast/<id>/    33 rigged assets: <id>.fbx (rigged), anim_<clip>.fbx, albedo.jpg|png, <id>_mat (made on box)
Assets/props/<id>/   35 static props: <id>.fbx (Blender-converted Mac-side), albedo.jpg|png (+ source .glb)
TripoLibraryImport.cs  Unity editor script — configures all 68 importers in one menu click
deploy_manifest.json   asset_id -> kind / unity_dir / model / clips / albedo
generation_manifest.jsonl  full Tripo provenance: prompts, gen/rig task ids, per-file bytes
```

## Deploy on GEX44 (`evaos-gpu-gex44-1`, Unity project `/home/unity/worldos-unity`)

1. **Copy + untar** into the Unity project root (paths in the tarball already mirror `Assets/`):
   ```
   scp -o ControlPath=/tmp/gex44-cm.sock worldos-asset-library-deploy.tar.gz root@<gex44>:/tmp/
   ssh -o ControlPath=/tmp/gex44-cm.sock root@<gex44> \
     'tar xzf /tmp/worldos-asset-library-deploy.tar.gz -C /home/unity/worldos-unity && \
      chown -R unity:unity /home/unity/worldos-unity/Assets/cast /home/unity/worldos-unity/Assets/props'
   ```
   (ControlMaster socket per WorldOS-GUI-RUNBOOK §GPU-VM lane; host in `~/.openclaw/secrets/gex44.env`.)
2. **Install the import script**: copy `TripoLibraryImport.cs` to
   `/home/unity/worldos-unity/Assets/Editor/` (any Editor folder), `chown unity:unity`.
3. **In the Editor** (launch via `/home/unity/launch_editor.sh`, DISPLAY=:0):
   menu **Tools → WorldOS → Tripo Library → Configure #1628 library**.
   Expect one log line: `[TripoLib] configured 68 models, ~70 clips, 68 albedo materials`.
4. **Registry is already wired**: PR #1635 adds all 68 rows to `data/asset-registry/registry.json`
   (exact-resolve verified). The renderer resolves `asset_id` → these paths; bind `<id>_mat.mat`
   (or `albedo.jpg` directly) at spawn — Tripo FBX imports untextured by design.

## Hard-won constraints (do NOT rediscover)

- **Generic, never Humanoid**: Tripo bone names don't map to Unity's Humanoid avatar; a Humanoid
  import **silently drops all clips**. The import script enforces `Generic/NoAvatar`.
- **FBX imports untextured**: albedo was pre-extracted from each source GLB Mac-side
  (`extract_glb_albedo.py`); the UVs match the rigged FBX because both derive from the same GLB.
- **Clip coverage by rig type**: bipeds walk/idle/run/slash (party) or walk/idle (NPCs);
  quadruped/hexapod/octopod **walk only**; **avian (raven, harpy) rigged but CLIPLESS** — Tripo
  has zero working avian retarget presets (probed exhaustively 2026-07-21, error 1004).
  Use avian units perched/static or hand-animate later.
- **Props are FBX** (converted Mac-side via Blender — the box has none); source `.glb` included
  as fallback if the project has glTFast.
- The two v3.1 bosses are heavy (dragon 178 MB, ogre 324 MB on disk) — first import will take minutes.

## Verification gate (post-deploy)

1. Import log line above (counts match).
2. Pick `pc_fighter_human`: Animation window shows Idle/Walk/Run/Slash takes; drag into a test
   scene, assign `pc_fighter_human_mat` — textured, T-pose-free (bind pose reads correctly).
3. `boss_young_red_dragon` walk clip plays on its Generic rig.
4. `viewer/asset_registry.py` resolve check (Mac-side): `pc_fighter_human`/`prop_throne` resolve
   `via exact` (already verified pre-handoff).
