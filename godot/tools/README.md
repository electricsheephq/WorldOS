# GT2 asset pipeline — Meshy → Blender → directional sprite sheet

The non-throwaway "speed now, quality later" art pipeline for the GT2 Godot renderer
(epic #1050). The renderer reads **only** the sprite-sheet manifest (`sheet.json`) + the
render-profile contract — never the pixels — so a sheet produced by ANY source drops in at
the same `scope_key` with **zero renderer change**. Provenance ladder: CC0 placeholder →
Meshy→Blender render → hand/AI paintover.

## Tools (run from the repo root)

1. **`meshy_gen.py`** — generate a 3D character/prop via the Meshy text-to-3D API.
   ```
   python3 godot/tools/meshy_gen.py \
     --prompt "a stylized fantasy human ranger, leather armor, hooded green cloak, T-pose, full body" \
     --out content/worlds/_private/baldurs-gate/sprites/aubree-iso8
   ```
   Submits preview → polls → refine (textured, PBR) → polls → downloads `model.glb` (+
   `thumbnail.png`, `meshy_meta.json`). **API key** is read from `~/.worldos/meshy.key` or
   `$MESHY_API_KEY` — **never** hardcode it or commit it.

2. **`bake_sprites.py`** — Blender headless render of the model to flat 2D, 8 facings.
   ```
   /opt/homebrew/bin/blender --background --python godot/tools/bake_sprites.py -- \
     --model <dir>/model.glb --out <dir>/frames
   ```
   Orthographic camera at the **LOCKED dimetric 2:1 projection** (see `godot/ISO-PROJECTION.md`):
   yaw 45°, elevation auto-calibrated so a unit floor-tile's top face renders 2:1 (~29.5°).
   Rotates the model 0/45/…/315° for the 8 facings (`S,SE,E,NE,N,NW,W,SW`), renders
   idle/walk/attack/cast frames (synthesized motion for a static model), and projects the
   foot point → `anchor.json`.

3. **`pack_sheet.py`** — tile frames into the renderer's layout + emit the manifest.
   ```
   python3 godot/tools/pack_sheet.py --frames <dir>/frames --scope sprite-aubree-iso8 \
     --out content/worlds/_private/baldurs-gate/sprites/aubree-iso8
   ```
   Produces `sheet.png` (rows = 8 facings, cols = 24 = idle4/walk8/attack6/cast6, 128px cells →
   3072×1024) + `sheet.json` (manifest v1, identical shape to the committed placeholder, with
   `source:"meshy-blender-render"`).

## Where the art lives

- **Committed (CC0/owned only):** `godot/assets/characters/<scope>/` holds the **placeholder**
  default (regenerable via `gen_placeholder_sheet.py`).
- **Finals (NOT committed):** Meshy→Blender / AI-paintover outputs go under
  `content/worlds/_private/…` (gitignored, owner-licensed) and are served at runtime via the
  `/image`-style bridge (the served-finals wiring is issue #1063). Record AI provenance in the
  render-profile `ai_disclosure` block (EU disclosure, Steam survey).
