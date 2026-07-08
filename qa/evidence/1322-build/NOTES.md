# #1322 W5a — macOS player build evidence (GEX44 box, 2026-07-08)

## Build
- Scene: `Assets/Scenes/M1CombatV1_canonical.unity` (current-best LIVE combat scene per CANONICAL.md's
  #1418 close-out; `TavernTier1.unity` is explicitly DEPRECATED there — not used).
- Target: `StandaloneOSX`, architecture Universal (`x64ARM64` — confirmed via `file` on the built
  executable: Mach-O universal binary, x86_64 + arm64 slices both present).
- Company/Product/BundleId: `worldos` / `WorldOSPlayer` / `com.worldos.WorldOSPlayer` (was
  `DefaultCompany` / `WorldOS-Unity-spike` / `com.DefaultCompany.WorldOS-Unity-spike`).
- Result: **Succeeded**, 0 errors, 56 warnings (pre-existing obsolete-API warnings across the
  project, none from this change), 158,429,215 bytes, build time 1m9s.
- Built via `Tools/WorldOS/Build/macOS Player (Universal)` (`BuildMacOSPlayer.cs`), executed inside the
  already-running headed editor (`execute_menu_item`) — NOT via a separate `-batchmode` process
  (BOX.md #1196: `-batchmode` silently kills the box's live MCP HTTP server).

## Build-readiness gaps found + disposition
1. **EditorBuildSettings.scenes was EMPTY** (`m_Scenes: []`) — a player build would have shipped with
   no scene. FIXED (build-readiness config, not a code hack): `BuildMacOSPlayer.cs` registers
   `M1CombatV1_canonical.unity` as scene 0.
2. **4 legacy editor-only scripts sat outside `Assets/Editor/`** and referenced `UnityEditor`/`[MenuItem]`
   with no guard — would break player compilation (`CS0234`-class failures once actually hit; a
   companion `CS0266` surfaced instead first from an unrelated obsolete-API mismatch in this build's
   own new script, see below). FIXED with `#if UNITY_EDITOR` / `#endif` wraps (additive, editor
   behavior unchanged, excluded from the player assembly):
   - `Assets/AnimFrameCapture.cs`
   - `Assets/IntegrationBuilder.cs`
   - `Assets/TavernTier1Builder.cs`
   - `Assets/SetupPainterlyScene.cs`
   (`CombatSurfaceDemo.cs`, the other `[MenuItem]`+`UnityEditor` script, was already correctly placed
   under `Assets/Editor/` — no action needed.)
3. **Own build-script bug** (not pre-existing): first `BuildMacOSPlayer.cs` draft used the obsolete
   `UnityEditor.OSXStandalone.MacOSArchitecture` enum, which no longer implicitly converts to
   `UnityEditor.OSXStandalone.UserBuildSettings.architecture`'s type
   (`UnityEditor.Build.OSArchitecture` in Unity 6000.5.1f1) — `CS0266`. Fixed in the committed script.
4. **STRUCTURAL — reported, not hacked:** `CombatSurfaceClient.cs` (the sole runtime MonoBehaviour that
   polls `/combat-surface` + POSTs `/move`) is not attached to any GameObject in any scene, and its
   `Start()` does `GameObject.Find("HeroFighter")` / `Find("MonsterGoblin")` — names that don't exist
   in the current asset-registry-driven pipeline (actors are named `Actor_char_<hash>` at runtime, see
   `paint_combat_v1.cs`). Wiring live `/combat-surface` polling + input into the player scene is W5c
   scope per the issue's design packet ("raycast→cell→POST input MonoBehaviour"); this build ships the
   scene as a static composed frame (no live polling yet). Flagging so W5c doesn't rediscover this.

## Config mechanism (coordinator contract, PR #1430)
`CombatSurfaceClient.Start()` now resolves the engine origin from the process environment BEFORE
falling back to the existing hardcoded default (byte-identical when absent):
- `WORLDOS_ENGINE_BASE_URL` → `ViewerUrl` (default unchanged: `http://127.0.0.1:8765`)
- `WORLDOS_CAMPAIGN_ID` → `CampaignId` (default unchanged: `""`)
No Info.plist/config-file mechanism was added per the coordinator's explicit instruction (env var via
`NSWorkspace` `configuration.environment` at launch is sufficient; no plist/config file needed).

## Artifacts
- `build-report.txt` — Unity `BuildReport.summary` dump.
- `bundle-listing.txt` — full `.app` bundle file tree + `file` output on the main executable (confirms
  universal binary).
- `build-log-tail.txt` — `[BuildMacOSPlayer]` log lines from `Logs/Editor.log`.
- Delivered artifact (not committed — binary): `~/worldos-session-notes/w5a-build/WorldOSPlayer.app.zip`
  on the Mac (scp'd from the box's `BuildOutput/`), 64,872,162 bytes zipped.
