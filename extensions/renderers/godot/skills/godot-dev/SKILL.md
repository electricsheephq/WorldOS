---
name: godot-dev
description: Develop / test / debug the WorldOS GT2 Godot painterly-isometric reference/extension renderer (the `extensions/renderers/godot/` archive). Use only for explicit Godot extension work or a renderer-migration issue that reopens Godot; current isometric/visual-renderer work routes through the Unity/current-renderer and visual-critic lane instead. Encodes the thin-client invariants, the dimetric-2:1 projection, how to run/validate locally, and points at the full knowledge base.
---

# WorldOS GT2 Godot reference renderer — dev skill

The GT2 renderer is a **stateless thin client** over the zone-based WorldOS engine, kept as
reference/extension material while the current renderer lane is Unity/current-renderer plus
deterministic SceneGrid/visual-critic checks. **Read `extensions/renderers/godot/HANDOFF.md` first** — it is the full
Godot knowledge base (architecture diagrams, the isometric/painterly/Pillars research, gotchas,
the prioritized backlog). Companions: `extensions/renderers/godot/ISO-PROJECTION.md` (the locked projection),
`extensions/renderers/godot/tools/README.md` (asset pipeline).

## The non-negotiables (full list in HANDOFF.md §7)
- Engine = **sole writer**; the renderer owns ZERO state but the `/events` cursor.
- **Ignore all surface `x/y`** — re-derive every screen position from named **zones**.
- **No engine facing field, ever** — facing is renderer-derived (conformance-locked).
- Writes only the **frozen `/move` vocab** (`…/travel/inspect/move_to_zone`).
- Projection is **dimetric 2:1 (~26.57°), irreversible once finals bake** — cite `ISO-PROJECTION.md`.
- Engine snapshot always overrides optimistic UI (never move a token from the click).

## Run / validate (Godot 4.6.3 at `/Applications/Godot.app/Contents/MacOS/Godot`)
- Parse/compile: `godot --headless --path extensions/renderers/godot --import`
- Logic smoke: `godot --headless --path extensions/renderers/godot --quit-after 180 -- --smoke-intent`
- Visual proof (window): `godot --path extensions/renderers/godot --demo-occlusion --quit-after 300` → `/tmp/wos_godot_occlusion_*.png`
- CI archive: `extensions/renderers/godot/ci/github-actions-godot.yml` (import + conformance + Web/Linux export + screenshot), no model keys.

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
Only use this loop for explicit `extensions/renderers/godot/` reference/extension work. Worktree off `origin/main` →
additive change → local Godot validate → PR → squash-merge → prune. Multi-session repo: never
branch-flip the shared checkout; Godot extension-only PRs are optional/manual proof and not a required branch-protection lane.

## Gotchas (cost real time — see HANDOFF.md §8)
- **`export_presets.cfg` is in Godot's default `.gitignore`, but `godot --headless --export` fails
  "no preset named X" without it.** Remove it from `.gitignore` and commit minimal Web + Linux
  presets. Validate a preset locally *without* templates by checking the error is *"export template
  not found"* (preset OK) vs *"no preset named X"* (preset wrong).
- **Asset bake output is gitignored — destroyed on `git worktree remove`.** Run the bake
  (`meshy_gen`/`tripo_gen` → `bake_sprites.py` → `pack_sheet.py`) so finals land in the **canonical**
  repo's `content/worlds/_private/.../images/<scope>/`, never inside a `WorldOS-worktrees/wt-*/` worktree.
- Art generation → the **`asset-gen`** skill; integrating a new gen service → **`wire-external-api-service`**.
