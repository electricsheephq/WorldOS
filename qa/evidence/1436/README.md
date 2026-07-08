# #1436 W5c Unit 1 — player runtime actor spawning (evidence)

Validated on GEX44 (Unity 6000.5.1f1, headed editor via unity-mcp), 2026-07-08.

## Packaging mechanism
StandaloneOSX **AssetBundle** (`StreamingAssets/worldos_actors`) keyed by each asset's EXACT registry
path + `registry.json` copied verbatim to `StreamingAssets/`. Built by `BuildMacOSPlayer.EnsurePackaged()`.
AssetBundle (not Resources) chosen because its load key IS the `Assets/...` path already in the registry,
so the runtime loader passes `registry.model_ref` verbatim — zero path transform, registry invariant
(slot lookup + default fallback, zero renderer edits per asset swap) preserved. Editor render path
(`paint_combat_v1.cs`, `AssetRegistry.cs`) untouched → byte-identical captures.

## Artifacts
- `spawn1436_editortest.png` — editor-mode spawn test: three tokens with NO baked Actor_* GameObject
  (`Goblin`→goblin exact, `Aldric`→alias fighter, `Wren`→alias innkeeper) spawned at runtime through
  `CombatSurfaceClient.SpawnActor` (loaded from the bundle in `StreamingAssets`). All grounded, scaled
  (bind-pose lock), idle-posed, with cyan/red selection rings + AO blobs, firelit on the painterly plate.
- `worldos_actors.manifest` — 26 bundled assets (every registry model_ref/albedo_ref/anim_ref that exists
  on disk; 3 non-existent `@moveset`/audio paths correctly skipped).
- `build-report.txt` — `result=Succeeded totalErrors=0`, Universal `x64ARM64`, scene
  `M1CombatV1_canonical.unity`.

## Editor-mode spawn diagnostics (reflection-invoked SpawnActor)
```
spawn Goblin -> Actor_tf_goblin      pos=(6.9,0.0,-6.1)  feet grounded, rends=1
spawn Aldric -> Actor_tf_aldric      alias->fighter,     feet grounded
spawn Wren   -> Actor_tf_wren        alias->innkeeper,   feet grounded
Actor_tf_goblin_AO / _Ring present (flat ground quads)
```

## Delivered build
`~/worldos-session-notes/w5a-build/WorldOSPlayer.app.zip` (overwritten) — StreamingAssets bundle +
registry verified inside `Contents/Resources/Data/StreamingAssets/`. Orchestrator smokes locally against
a live campaign with foes.
