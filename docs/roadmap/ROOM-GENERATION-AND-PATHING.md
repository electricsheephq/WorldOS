# Room Generation & Pathing — how WorldOS generates a room (the owner's design question, answered)

> **The question (owner, 2026-06-30):** *"How do we generate a room? Do we layer that? Do we first
> generate the room without items, then send it back to the system to layer items on top? For pathing,
> what does that all look like? … we get to a point where 95% of our rooms look amazing, pathing works."*

This doc is the **answer**, grounded in a proven end-to-end run (not a proposal). The renders that prove
it: `~/worldos-session-notes/renders/greybox_to_painted_ALIGNED.png` (greybox → painted side-by-side) +
`crypt_from_greybox_v1.png`.

## The one-line answer

**One authored `scene_grid` is the SINGLE source for BOTH the painted room AND the pathing.** We render
that scene_grid as a camera-pinned **greybox** (floor + walls + a box per prop, at the combat camera),
then **img2img** the greybox into a painterly room. Because the paint follows the greybox structure, the
painted props land on the same cells the pathing obstacles live on — **aligned by construction, never
decoupled, never reverse-engineered from pixels.**

```
            ┌─────────────────────────────────────────────────────────────┐
            │   scene_grid  (authored geometry: walls + props at cells)     │   ← THE SINGLE SOURCE
            └───────────────┬───────────────────────────────┬─────────────┘
                            │                               │
          export_scene_grid.py                    impassable_cells()  (M-B bridge)
                            │                               │
                    room_geometry.json                combat grid_impassable
                            │                               │
                 build_room_greybox.cs                      │
                            │ (greybox at the contract camera)
                       greybox.png                          │
                            │                               │
              generate_room.py --base-plate                 │
                  (img2img, painterly LoRA)                 │
                            │                               │
                     painted room.png ───────────────────►  the SAME cells
                            │                               │
                        deploy_room.sh                      │
                            │                               │
                  paint_combat_v1.cs renders 3D actors that route around the PAINTED props,
                  because the pathing obstacles ARE those props' cells.
```

## "Do we layer it?" — YES, but the layering is greybox→paint, not room→items

The owner's instinct ("generate the room without items, then layer items on top") is right in spirit, with
one correction learned from the proof: **the items (props) are authored in the geometry FIRST (as the
greybox), and the PAINT is the layer applied on top** — not the other way round. Reasons:

1. **Pathing must be authored, never derived from a painted image.** (Red-team FATAL, recorded in the
   plan: SAM / monocular-depth on a painted plate cannot *guarantee* "never path through a wall.") So the
   geometry — walls + prop footprints — is authored up front in the `scene_grid`. That authored geometry
   IS the pathing.
2. **The greybox carries that geometry into the image.** A box per prop at the contract camera gives the
   img2img a structure to paint over, so the painted prop ends up where the authored cell is.
3. **The paint is the cosmetic layer.** img2img re-skins the greybox into stone/wood/torchlight. It never
   moves the load-bearing structure (see the fidelity knob below).

So the layer stack is: **authored geometry (pathing) → greybox render → painterly paint**. Items are part
of layer 1, not a separate re-feed pass. (A genuine second *content* pass — e.g. scatter clutter/decals on
top — is a future refinement; it would be authored into the scene_grid too, so it stays on the pathing.)

## The fidelity knob (the real design tradeoff)

img2img `strength` trades **structure-fidelity** against **paint-quality**:

- **Low strength (~0.45–0.5):** the greybox dominates; props stay pixel-on-cell; but the paint is thinner
  (closer to a relit greybox). Use for **pathing-critical** rooms where every interior obstacle must align.
- **High strength (~0.7):** richer painterly result; the LoRA preserves the room shape + the *interior*
  props (proven: the central brazier + sarcophagus landed on their cells at 0.7) but **freely invents
  decorative detail at the perimeter** — e.g. it painted pillars into the wall corners. That is SAFE: the
  perimeter is already impassable wall, so invented corner decoration has **zero pathing impact**. What
  must stay aligned is the **walkable-interior** obstacles, and those do.

**Rule of thumb:** keep interior obstacles few and chunky (a box the LoRA won't ignore); let the perimeter
be decorated freely; pick strength by how pathing-critical the room is.

## The pieces (all committed, repeatable)

| Step | Tool | What it does |
|---|---|---|
| author geometry | `scene_grid.py` (`_gen_dungeon`/`_gen_tavern`/… or a hand-authored grid) | walls + props at cells = the pathing |
| export | `qa/export_scene_grid.py` | scene_grid → `room_geometry.json` (cols, rows, walls, props, impassable) |
| greybox | `extensions/renderers/unity/scripts/build_room_greybox.cs` | floor + walls + a box per prop at the **contract camera** → `room_greybox.png` |
| paint | `extensions/renderers/godot/tools/generate_room.py --base-plate <greybox>` | img2img with the painterly LoRA → painted room |
| pathing | `scene_grid.py:impassable_cells()` (M-B bridge) | the SAME scene_grid → combat `grid_impassable` |
| deploy | `qa/deploy_room.sh` | make the painted room the active combat backdrop |
| play | `paint_combat_v1.cs` | 3D actors route around the painted props (= the pathing cells) |

One-command orchestration: **`qa/gen_room_from_scene_grid.sh <campaign> <room_type>`** chains
export → greybox → img2img → deploy.

## "95% of rooms look amazing + pathing works" — where we are

- **Pathing works — proven + structural.** The alignment is by construction (one scene_grid → both), so
  pathing is correct for *any* generated room, not tuned per room. ✅
- **Look amazing — the ≥8 carved-greybox lever is now PROVEN.** Adding carved geometry to the greybox —
  **flagstone grout lines** on the floor, **pilasters/buttresses + a cornice** on the walls — takes the
  img2img from a flat gray room (~6) to a carved-stone PoE2 crypt (~8): painted flagstones with mortar,
  carved columns with capitals, multi-torch warm/cool lighting. **Same prompt, same LoRA, same strength —
  the ONLY change is the greybox geometry.** This is the definitive confirmation that the ≥8 lever is
  carved geometry, NOT the prompt (`extensions/renderers/shared/room_recipes.json:ceiling_2026_06_30`).
  Proof: `~/worldos-session-notes/renders/flat_vs_carved_painted.png` (flat vs carved, side-by-side) +
  `carved_greybox_to_painted.png` + `crypt_carved_v1.png`. `build_room_greybox.cs` emits the carved
  geometry. ✅
  - **Backdrop hygiene:** pick a figure-free variant — at strength ~0.62 the LoRA occasionally paints a
    figure where a prop box is figure-sized; keep props chunky/low (braziers wide-not-tall) and select the
    variant with no painted actor, since the 3D cast layers on top.

## Invariants honored

- **Engine = SOLE WRITER.** `export_scene_grid.py` is read-only on engine state; the greybox renderer +
  img2img are view/asset layers. Pathing obstacles are produced by the engine's `impassable_cells()`.
- **Walkability authored-by-construction, NEVER auto-derived from a painted image.**
- **Modular invariant.** Any room generates from one command + its scene_grid; zero renderer edits per room.
