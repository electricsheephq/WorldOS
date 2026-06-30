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
- **Look amazing — driven to ~7.07 via a TEXTURED greybox; ≥8 is now empirically LoRA-bound.** A scored
  loop (`qa/scores_db`, harsh PoE2 panel) climbed the carved-greybox to its ceiling: flat gray greybox
  **4.75** → procedural stone albedo+normal (a textured base, not flat grey) **5.75** → LIT walls (strong
  cool fill so the carved walls don't crush to black) **6.5** → GEOMETRIC masonry coursing on the wall
  faces (the floor painted into flagstones because it has geometric grout boxes; the walls needed the same)
  **7.07**. Denser coursing yielded no gain. The win: 7.07 is reached at **LOW img2img strength (0.5)**, so
  the camera-pin holds and props stay on the authored-pathing cells — authored-pathing-aligned AND
  repeatable from any scene_grid (the old 7.4 firelit plate needed a hand-made painterly base + hand-tuned
  obstacles). **≥8 is LoRA-bound, not geometry-bound:** the LoRA paints focal craft (floor flagstones,
  statue niches, fluted columns, figural relief panels) at PoE2-grade but smooths the broad wall *fields*
  into value-pass regardless of coursing — so ≥8 needs a crisper architectural/stone LoRA (or a dedicated
  wall-field detail pass), per `room_recipes.json:textured_greybox_result_2026_07_01`. Renders:
  `crypt14_walledcourse_v1.png` (7.07) + `m1_combat_textured.png` (live combat) + `LEVER_progression_6up.png`.
  - The earlier rigorous panel had corrected an eyeball over-claim (a flat carved greybox is ~5, not ≥8);
    this textured-greybox loop is the honest, scored path that lifted it to 7.07. There is a real
    **alignment↔quality tradeoff** with a GRAY greybox + this LoRA, which the textured base mitigates:
  - **Low strength (~0.55–0.62):** preserves the carved geometry on-cell (alignment holds) but under-paints
    the gray → washed-out / gray-fog (~5). This is what the aligned-pathing system uses.
  - **High strength (~0.82):** fully repaints into painterly firelit carved stone (**scored 7.0**, washout
    solved, strong warm/cool chiaroscuro) — but **drifts the props off-cell** (breaks alignment), so it's a
    non-aligned **"hero plate"** (`crypt_heroplate_str082_7p0.png`), not the combat backdrop.
  - **The ≥8 ceiling** (carved-stone micro-craft: fluting, chiseled relief, masonry coursing) is **beyond
    this painterly LoRA + img2img from a gray greybox**, exactly as `room_recipes.json:ceiling_2026_06_30`
    predicted. The ≥8-AND-aligned path = a 2-stage **texture-then-relight** (so the base is painterly before
    the low-strength pin) and/or **a crisper architectural model/LoRA**. **The owner deprioritized chasing
    ≥8** ("don't re-loop the prompt") in favor of the SYSTEM below — which IS proven. ◐
  - **Backdrop hygiene:** pick a figure-free variant — the LoRA occasionally paints a figure where a prop
    box is figure-sized; keep props chunky/low and select the variant with no painted actor.

## Invariants honored

- **Engine = SOLE WRITER.** `export_scene_grid.py` is read-only on engine state; the greybox renderer +
  img2img are view/asset layers. Pathing obstacles are produced by the engine's `impassable_cells()`.
- **Walkability authored-by-construction, NEVER auto-derived from a painted image.**
- **Modular invariant.** Any room generates from one command + its scene_grid; zero renderer edits per room.
