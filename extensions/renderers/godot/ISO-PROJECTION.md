# WorldOS GT2 — Isometric projection (LOCKED)

> **Single source of truth** for the GT2 Godot renderer's projection. Both the **Blender
> bake pipeline** (`extensions/renderers/godot/tools/bake_sprites.py`) and the **Godot renderer** (`WorldView.gd`,
> `CharacterToken.gd`) cite this file, and every sprite-sheet manifest asserts
> `projection: "dimetric-2to1"` against it. **This is irreversible once finals bake** — art
> and zone→screen geometry must share one projection or they desync. Owner lock **2026-06-21**.

## The lock

| Property | Value | Notes |
|----------|-------|-------|
| **Projection** | **dimetric 2:1** | ~**26.57°** (`atan(0.5)`). The Diablo II / SimCity 2000 / Infinity-Engine "isometric" look (technically dimetric — two axes share a scale). NOT true 30° isometric. |
| **Tile ratio** | width : height = **2 : 1** | A floor "cell" is twice as wide as it is tall on screen. |
| **Facing count** | **8** | |
| **Facing order** | `["S","SE","E","NE","N","NW","W","SW"]` | Index 0 = South (toward camera). Clockwise. Sprite-sheet rows follow this order. |
| **Origin / anchor** | **foot point** | Each token/prop node origin is at the character's feet (the floor-contact point), so `global_position.y` IS the depth key. |
| **Depth sort** | **Godot Y-sort** by foot-y | Greater screen-Y = nearer camera = drawn in front. No manual depth bands. |

## Derived rules (do not re-derive ad hoc)
- **Facing is renderer-DERIVED**, never from the engine (the engine has no facing field and must never gain one — sole-writer invariant). Out of combat: 8-way snap of the screen-space vector between the token's previous and new zone anchor on a `move_to_zone`; reset to `default_facing` on a `travel` (location change). In combat: 8-way snap of actor-zone-anchor → target-zone-anchor (from the Action-Replay envelope `target_fk`).
- **`facing_strategy: "mirror4"`** is allowed: author/render only `S, SW, W, NW, N` and horizontal-flip for `SE, E, NE` — halves the art. The renderer applies the flip; the manifest declares the strategy.
- **Zone → screen** placement is data-driven from `renderer_profiles.godot.backdrop_layout[scope].zone_anchors` (normalized `[x,y]` in 0..1 of the backdrop), computed at the **same** projection the backdrop art was baked/painted at.

## Manifest assertion
Every sprite-sheet manifest carries `"projection": "dimetric-2to1"`. The renderer **refuses** a manifest whose projection id does not match this lock, so a mis-baked sheet fails loudly instead of desyncing silently.
