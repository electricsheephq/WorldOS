# #1433 W5b — magenta shader fix + live CombatSurfaceClient wiring (GEX44 box, 2026-07-08)

Closes the two gaps between the running `WorldOSPlayer.app` (built #1322 / PR #1432) and the T3 gate:
magenta missing-shader blocks, and the un-wired combat client.

## 1) Magenta blocks — root cause + fix
- **Cause:** the `Occluder_*` depth-proxy cubes in `M1CombatV1_canonical.unity` referenced
  `WorldOS/OccluderDepth`, a shader `paint_combat_v1.cs` created AT RUNTIME via
  `UnityEditor.ShaderUtil.CreateShaderAsset(...)` and serialized INLINE into the scene. A runtime-created
  inline shader is not compiled into a standalone player build → its material fell back to the pink error
  shader = the magenta blocks (see `before_first_mac_player_launch_magenta.png`). Editor renders were fine
  (the built-in RP resolves the inline shader in-editor), so it only surfaced in the player.
- **Render pipeline is BUILT-IN** (`m_CustomRenderPipeline: {fileID: 0}`), so actors (`Standard`) /
  backdrop (`Unlit/Texture`) / AO+rings (`Unlit/Transparent`) were never the problem — only the runtime
  occluder shader.
- **Fix (byte-identical editor color output):**
  1. Committed `Assets/OccluderDepth.shader` (`WorldOS/OccluderDepth`) — source byte-for-byte the old
     runtime string (`ColorMask 0`, `ZWrite On`, `Queue Geometry-1`; depth-only, writes no color).
  2. `W5bWireScene` MenuItem reassigned the scene's occluder material from the inline shader to the
     committed asset — the material now serializes `m_Shader: {…, guid: 8c8c25e6…, type: 3}` (asset ref).
  3. Added `WorldOS/OccluderDepth` to Project Settings → Always-Included Shaders (belt-and-braces vs
     variant stripping; count 7 → 8).
  4. `paint_combat_v1.cs` now prefers `Shader.Find("WorldOS/OccluderDepth")` and only falls back to the
     runtime `CreateShaderAsset` string when the asset is absent — so future captures bake a build-safe
     scene AND the editor capture flow stays byte-identical on any box.
- **Build evidence (`shader-inclusion.txt` / `build-report.txt`):** `Importing … Assets/OccluderDepth.shader
  (ShaderImporter)` + `Compiling shader "WorldOS/OccluderDepth"` + `Serialized binary data for shader
  WorldOS/OccluderDepth` — the shader is compiled INTO the player build. Build: `Succeeded`, 0 errors,
  56 pre-existing warnings, 158.4 MB, Universal (x64ARM64).

## 2) CombatSurfaceClient — live wiring
- **Attached** to a `CombatSurfaceClient` GameObject in `M1CombatV1_canonical.unity` (was unattached; W5a
  flagged this). Editor edit-mode captures are unaffected (the client only acts at runtime).
- **Actor resolution:** replaced the stale `GameObject.Find("HeroFighter")` / `Find("MonsterGoblin")` with
  per-token `GameObject.Find("Actor_" + token.id)` — the CURRENT registry naming `paint_combat_v1.cs`
  spawns (`Actor_char_<hash>` for allies, `Actor_<foe id>` for foes). Missing actors are skipped safely.
- **Cell↔world** now mirrors `paint_combat_v1.cs` EXACTLY (grid read from the surface `grid` block;
  `(c-(Cols-1)/2)*2.0`, row-flipped `(Rows-1)/2 - r`; cell 2.0, 14×11 default) — was the stale
  ClosedLoopBuilder 14×10/cell-5 math. Actor is bounds-centered on the cell (matches the baked placement →
  no jump on first poll); `_AO`/`_Core`/`_Ring` siblings follow by the same delta.
- **Input:** click → floor raycast → cell → POST `/move` with the EXISTING kinds only, payloads byte-exact
  to `qa/drive_gfx_combat.py`: `move_to_cell` `{kind,x,y,turn_token,campaign}`; on-turn `attack`
  `{kind,target_id,turn_token,campaign}` when the clicked cell holds the foe. Engine stays the sole writer;
  the client re-renders only engine-confirmed surfaces (`/combat-surface` poll + the `/move` response).
- Reads the engine origin/campaign from `WORLDOS_ENGINE_BASE_URL` / `WORLDOS_CAMPAIGN_ID` (PR #1430
  handoff contract), default unchanged when absent.

## Delivery
- Rebuilt `.app` scp'd to the Mac: `/Users/lume/worldos-session-notes/w5a-build/WorldOSPlayer.app.zip`
  (64.8 MB, overwrite) — the orchestrator smoke-tests the built player locally (macOS build can't run on
  the Linux box). Box project committed for anti-drift.
