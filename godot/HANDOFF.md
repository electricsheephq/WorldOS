# GT2 Godot painterly-isometric renderer — HANDOFF / STATUS

> Source of truth for the GT2 Godot renderer (epic **#1050**, milestone *Graphics M5 — GT2
> Godot painterly-isometric renderer*). Read this + `ISO-PROJECTION.md` + `tools/README.md` +
> `docs/roadmap/contracts/render-profile.md` before continuing. Last updated 2026-06-21.

## TL;DR

The **foundation is done and CI-gated; the renderer is NOT yet feature-complete or shipped.**
A vertical slice works: a directional character (real Meshy→Blender art) on live engine state,
click-to-move on the frozen `/move` vocab, correct Y-sort occlusion — validated locally + in CI.
The "looks-like-Pillars, fully-playable, shipped-to-web+native" end state is still backlog.

## What is DONE (merged to main, CI-green)

| # | What |
|---|---|
| #1051 | `renderer_profiles.godot` contract block + `ISO-PROJECTION.md` (dimetric 2:1 LOCKED) + "no engine facing" conformance lock |
| #1052 | `godot/` project + `Config` + `SurfaceClient` thin-client transport + fixtures |
| #1053 | `WorldView` — backdrop plane + renderer-owned **procedural walkmask** + deterministic zone markers |
| #1054 | directional `CharacterToken` + sprite-sheet manifest v1 + CC0 placeholder art |
| #1055 | click-to-move (`move_to_zone`/`travel`/`inspect`) + `FacingResolver` + Y-sort occlusion |
| #1056 | `.github/workflows/godot.yml` — import + conformance + export(Web single-thread + Linux) + screenshot artifact |
| #1062 | Meshy→Blender asset pipeline (`tools/meshy_gen.py` + `bake_sprites.py` + `pack_sheet.py`) |

## What is OPEN (the remaining backlog, prioritized)

**Visual/playability (do these to "look + play like Pillars"):**
- **#1090 — surface narration + dialogue in the view** ← *biggest gap; the story is currently invisible in the Godot view.*
- **#1089 — painterly backdrop pipeline** (Meshy 3D env → Blender render-down; backdrop is a procedural gradient today).
- **#1063 — serve `_private` finals via `/sprite?scope=`** (so the Meshy character loads in real play, not just local screenshots).
- **#1092 — render the full party + present NPCs** in exploration (only `party[0]` shows today).
- **#1060 — combat & zone token rendering** via the Action-Replay envelope.
- **#1091 — real *rigged* animation** (today's bake uses *synthesized* bob/lunge motion, not skeletal).
- **#1093 — camera (pan/zoom/follow) + audio/ambience + input polish.**

**Delivery (export is validated in CI; actual serving/packaging is not built):**
- **#1057 — serve the Godot HTML5 export at `/godot/*`** (single-threaded, Brotli) alongside `/openworlds`.
- **#1058 — standalone macOS `.app`** + `play.sh --client godot`.
- **#1059 — Mac app launches the Godot native client + renderer Picker** (recommend a first-principles pass first).

**Tactics / hygiene:**
- **#1061 — optional cell-grid (#461) tie-in** for gridded combat (branch:b).
- **#1064 — `license_check.py` gate** for committed `godot/assets` (needs an "owner-generated/Meshy" tier alongside CC0).

## ⚠ Diagram vs. reality (the architecture diagram is the PLAN; these parts diverge)

1. **"TileMapLayer — isometric terrain"**: there is **no `TileMapLayer`/`TileSet`**. The floor is a
   **procedural perspective-trapezoid walkmask** (`WorldView._rebuild_floor`). This is *correct* —
   positioning is **zone**-based, not tile-based — so the diagram over-promises a tilemap. Decision:
   keep the zone-walkmask model; tile terrain is only needed if a future game-type wants free-tile movement.
2. **"Background plane — painterly image"**: the node exists and loads via `/image?scope=`, but **no
   painterly art is generated** → procedural gradient fallback. Real backdrops = #1089.
3. **"Web export / Native desktop"**: the CI lane **validates the exports build**, but nothing serves
   or packages them yet (#1057-1059).

## How to run / validate (local)

- Godot **4.6.3** at `/Applications/Godot.app/Contents/MacOS/Godot`. Blender **5.1.2** at `/opt/homebrew/bin/blender`.
- Parse/compile gate: `godot --headless --path godot --import`
- Logic smoke: `godot --headless --path godot --quit-after 180 -- --smoke-intent` (prints the move-intent + derived facing; asserts frozen vocab)
- Visual proof (real window): `godot --path godot --demo-occlusion --quit-after 300` → `/tmp/wos_godot_occlusion_{behind,front}.png`
- CI: `.github/workflows/godot.yml` runs all of the above + the Web/Linux export on every `godot/**` change (deterministic, no model keys).

## Asset pipeline + art posture

- Regenerate the character: `python3 godot/tools/meshy_gen.py --prompt "..." --out <dir>` → `blender --background --python godot/tools/bake_sprites.py -- --model <dir>/model.glb --out <dir>/frames` → `python3 godot/tools/pack_sheet.py --frames <dir>/frames --scope <scope> --out <dir>`. See `tools/README.md`.
- **Meshy API key:** `~/.worldos/meshy.key` (mode 600, OUTSIDE the repo) or `$MESHY_API_KEY`. *(Key was pasted in a chat transcript 2026-06-21 → rotate when convenient.)*
- **Art posture (enforced):** committed tree (`godot/assets/`) holds **CC0/owned placeholders ONLY**.
  Meshy/AI/Blender **finals** live gitignored in `content/worlds/_private/<world>/images/<scope>/`,
  served at runtime — **never committed**. ⚠ Generate finals into the **canonical** repo's `_private`,
  NOT a worktree's (a worktree's gitignored `_private` is **pruned on `git worktree remove`** — this
  ate the first ranger). The current ranger is regenerated at
  `content/worlds/_private/baldurs-gate/images/sprite-aubree-iso8/`.
- **Backgrounds & Eva caveat:** the engine's only wired image provider (`WORLDOS_IMAGE_PROVIDER=openclaw`)
  rides **Eva's OpenClaw gateway + Codex OAuth** — do NOT drive it autonomously (the "never touch Eva"
  invariant). Use the **Meshy→Blender** route for backdrops (#1089), or add a direct gpt-image key.

## Load-bearing invariants (do not violate)

- Engine = **sole writer**; the renderer is a **thin client** (owns zero state but the `/events` cursor).
- **Ignore all surface `position.x/y`** (`positionAuthority:'derived'`); re-derive every screen position from named **zones**.
- **No engine facing field, ever** — facing is 100% renderer-derived (locked by a conformance assertion).
- Writes only the **frozen `/move` vocabulary** (`say/do/check/save/combat/attack/cast/use_item/clarify/travel/inspect/move_to_zone`).
- Projection is **irreversible once finals bake** — everything cites `ISO-PROJECTION.md` (dimetric 2:1).

## Dev loop

Worktree off `origin/main` → additive change → local Godot validation (above) → PR → squash-merge →
prune. `godot/`-only PRs: CodeQL skips, the deterministic lanes + `godot.yml` gate. The repo is
multi-session — always work in a worktree, never branch-flip the shared checkout.

## Key files

`godot/project.godot` · `godot/autoload/{Config,SurfaceClient,ImageResolver,FacingResolver,RenderProfile}.gd` ·
`godot/scenes/{Main,WorldView,CharacterToken,PropActor,InputController,Hud,FxLayer}.gd` ·
`godot/tools/{meshy_gen,bake_sprites,pack_sheet}.py` · `godot/ISO-PROJECTION.md` ·
`godot/assets/characters/aubree/{sheet.png,sheet.json}` (CC0 placeholder) ·
`docs/roadmap/contracts/{render-profile.md,render-profile.schema.json,move-intents.md,action-replay-envelope.md}` ·
`viewer/openworlds/render/surface-client.js` (the JS thin-client this mirrors).
