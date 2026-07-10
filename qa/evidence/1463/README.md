# W6.4 (#1463) — stage manifest + animated layers, merged with the T3 onboarding readability gap

Renderer changes to `extensions/renderers/unity/scripts/CombatSurfaceClient.cs` +
`BuildMacOSPlayer.cs`, plus the T3 player-launch env gate + qa docs. Validated on the GEX44 box
(Unity 6000.5.1f1, `worldos-unity@c383e4e9`), claim under #1386.

## Compile — GREEN
Deployed the two changed scripts to the box (`Assets/CombatSurfaceClient.cs`,
`Assets/Editor/BuildMacOSPlayer.cs`), `chown unity`, `refresh_unity(force, scripts, compile)` →
domain reload completed (WorldOS-autoarm re-ran on fresh load) → **0 console errors, 0 warnings**
(`read_console` errors + CombatSurfaceClient-filtered both empty).

## Stage manifest (task 3) — flicker + glow, LIVE on the box
With a test `StreamingAssets/stage.json` (anchors at the two braziers, `flicker
{amplitude:0.5, speed:6}`) present, play mode logged:

```
[CSC] stage.json loaded: flicker=True lights=2 glowQuads=2
```

- `wt_w6_4_1463_flicker_a.png`, `wt_w6_4_1463_flicker_b.png` — two frames ~1.2s apart; the two
  brazier (fire) point lights are Perlin-flickered around their captured base intensity and the two
  warm glow quads pool on the floor (reused `MakeGroundQuad`). Non-black (5120×2880, downscaled to
  1920 for the repo). The test `stage.json` was removed afterward, restoring the byte-identical scene
  (absent file ⇒ no flicker touch, no glow quads).
- Schema example: `stage.example.json` (kept here, NOT at project root, so builds stay byte-identical
  until a real `stage.json` is deliberately added). `BuildMacOSPlayer.EnsurePackaged` copies it into
  StreamingAssets only when present.

## Onboarding readability (tasks 1+2) — hint layer, name plates, first-turn overlay
`_onboard = WORLDOS_PLAYTEST=1 || WORLDOS_ONBOARD=1`. In this editor capture `onboard=False` (no env)
— so the hint layer + name plates correctly did **not** draw, and the overlay stayed OFF. That is the
byte-identical beauty-capture path. They light up under onboarding:

- **Task 1 hint layer** (`DrawOnboardHint`, IMGUI): a top-of-screen line naming whose turn it is
  (by NAME) + `"click a highlighted tile to move · click a foe to attack"`, faded out over 1.6s after
  the first engine-accepted move/attack/walk (`MarkActed`).
- **Task 2 turn indicator + name plate** (`MakeNameLabel`, world-space TextMesh parented to the
  `#1451` HP-bar root so it rides + billboards for free): each actor's name above the bar; the
  isCurrent combatant's plate tinted gold.
- **First-turn overlay:** `_onboard` forces the walkability overlay ON at Start even outside a
  playtest (the T3 "15 actions to first turn" readability gap).

The live hint-layer + name-plate frames come from the orchestrator's **T3 gate rerun**, which is the
only context that populates them (the player is launched with `WORLDOS_ONBOARD=1` — added to
`qa/ui_playtest_player.sh` — against a live engine surface, and can show the hint fade as motion).

## Note (research tension, flagged not resolved)
`docs/research/2026-07-10-stage-tech-research.md` amendment #5 warns fire-flicker over the *current*
flat-Unlit backdrop can worsen plate/actor cohesion, and would reorder flicker after a lighting-rig
unification. This change keeps flicker **opt-in via `stage.json` presence** (absent ⇒ dormant +
byte-identical), so it ships dark and does not touch beauty captures until a `stage.json` is
deliberately added — but shipping a real `stage.json` should wait on that rig work.
