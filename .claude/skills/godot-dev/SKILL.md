---
name: godot-dev
description: Develop / test / debug the WorldOS GT2 Godot painterly-isometric renderer (the `godot/` project). Use when working on the isometric view — GDScript (`.gd`) or scenes (`.tscn`), the `SurfaceClient`/`WorldView`/`CharacterToken`/`InputController`, the render-profile contract, sprite sheets, click-to-move/Y-sort, or the Godot CI lane. Encodes the thin-client invariants, the dimetric-2:1 projection, how to run/validate locally, and points at the full knowledge base.
---

# WorldOS GT2 Godot renderer — dev skill

The GT2 renderer is a **stateless thin client** over the zone-based WorldOS engine. **Read
`godot/HANDOFF.md` first** — it is the full knowledge base (architecture diagrams, the
isometric/painterly/Pillars research, gotchas, the prioritized backlog). Companions:
`godot/ISO-PROJECTION.md` (the locked projection), `godot/tools/README.md` (asset pipeline).

## The non-negotiables (full list in HANDOFF.md §7)
- Engine = **sole writer**; the renderer owns ZERO state but the `/events` cursor.
- **Ignore all surface `x/y`** — re-derive every screen position from named **zones**.
- **No engine facing field, ever** — facing is renderer-derived (conformance-locked).
- Writes only the **frozen `/move` vocab** (`…/travel/inspect/move_to_zone`).
- Projection is **dimetric 2:1 (~26.57°), irreversible once finals bake** — cite `ISO-PROJECTION.md`.
- Engine snapshot always overrides optimistic UI (never move a token from the click).

## Run / validate (Godot 4.6.3 at `/Applications/Godot.app/Contents/MacOS/Godot`)
- Parse/compile: `godot --headless --path godot --import`
- Logic smoke: `godot --headless --path godot --quit-after 180 -- --smoke-intent`
- Visual proof (window): `godot --path godot --demo-occlusion --quit-after 300` → `/tmp/wos_godot_occlusion_*.png`
- CI: `.github/workflows/godot.yml` (import + conformance + Web/Linux export + screenshot), `godot/**`-triggered, no model keys.

## Making art (see the `asset-gen` skill)
Characters/props/backdrops come from the AI asset toolkit → the Blender bake → the sprite-sheet
manifest. Tripo (rig+animate) and Meshy make 3D characters; Scenario makes painterly backdrops
(non-Eva); finals live gitignored in `content/worlds/_private/.../images/<scope>/`. Invoke the
**`asset-gen`** skill for the job matrix, keys, wrappers, and MCPs.

## Backlog (HANDOFF.md §9)
Next: **#1090** (narration/dialogue in-view) → **#1089** (painterly backdrop, via `asset-gen`/Scenario)
→ **#1063** (serve `_private` finals) → **#1092** (full party) → **#1060** (combat tokens) → **#1091**
(real rigged animation, via `asset-gen`/Tripo). Delivery: #1057–#1059. Epic **#1050**.

## Dev loop
Worktree off `origin/main` → additive change → local Godot validate → PR → squash-merge → prune.
Multi-session repo: never branch-flip the shared checkout; `godot/`-only PRs skip CodeQL.
