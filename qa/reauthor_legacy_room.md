# Re-authoring a LEGACY room's geometry to its painted plate

**Doctrine first (#1585, VISION "geometry is ground truth"): the paint is cosmetic.** For a room whose
plate is still generated (Track C3, the kit chain) you fix the *renderer*, never the grid. This method is
for the four **shipped legacy plates** — `camp_clearing`, `tavern_snug`, `shop`, `throne_hall` — whose
paint is frozen and whose declared props drifted 4-9 cells from what the painter drew. There the grid is
DELIBERATELY re-authored to the paint, documented per prop, and re-gated. It is never pixel-chasing: every
moved cell carries a frame that justifies it.

## 0. The one thing that goes wrong: the cell→pixel transform

The 2026-09-02 collision lens fitted an affine map from three anchor clicks. Its row axis came out ~7°
off, which is a whole cell of drift at the grid edge, and it produced findings that a verified transform
refutes (crypt `(11,6)/(11,7)` "inside the crate" — they are clear flagstones; the crate is `(10,6)/(10,7)`).

Use the CONTRACT projection instead — `qa/overlay_collision.py`, which goes cell → world
(`greybox_render_headless.cell_to_world`) → viewport through the frozen `Euler(30,45,0)` ortho camera, the
same math `qa/walk_test.py:world_to_window_px` gates on. Then **prove it on the frame you are about to
read**: `--verify` compares the actor's own `/debug actorVX/actorVY` against the projection's prediction
for the token's cell and fails over 0.25 cell. Measured residuals on the four rooms: 0.005-0.08 cell.

## 1. The loop

1. **Rig.** `qa/qa_sandbox.py up` on `qa/seed_adventure_demo.py` with FREE ports (`--engine-port` /
   `--qa-port`; 8866-8946 are usually taken). `FORCE_PLAYER_QA=1`. Always `down` afterwards.
2. **Overlay.** `/shot`, then
   `qa/overlay_collision.py --frame F --out O --engine … --qa … --outline --verify`.
   `--outline` draws the grid without the red tint so the PAINT stays readable; drop it for a
   "what is blocked" read. Read the PNG.
3. **Locate a painted mass.** A prop's footprint is the floor diamond it STANDS on. Its paint rises
   up-screen from there, so a cell whose centre is covered by tall paint may be the footprint OR the floor
   *behind* it. Do not guess: the ground point of cell (c,r) is exactly where the actor's selection ring
   lands.
4. **Felt-test the ambiguous ones.** Click a currently-walkable candidate, `/shot`, and look at the ring.
   Ring on the counter's front panel ⇒ the counter owns that cell (tavern `(7,5)`, shop `(5,4)`, shop
   `(1,8)`, throne `(9,5)` brazier, throne `(10,6)` throne seat). Ring on clear floor ⇒ leave it walkable
   (crypt `(11,7)`). One probe settles what ten minutes of eyeballing cannot.
5. **Propose, then look before you commit.** Paint the candidate footprints onto the raw frame (one colour
   per prop) and read that image. Adjust once; do not iterate on pixels.
6. **Edit.** Rewrite `props`, then re-derive `walls` and `impassable` from
   `structural_walls ∪ (all prop cells − door_cells)` so the three lists cannot disagree.
   `impassable` is a committed RECORD — the seed reads `walls` + `props`
   (`seed_gfx_town.build_grid_from_geometry`).
7. **Gate.** `uv run --directory servers/engine python <wt>/qa/walk_static.py` repo-wide (door landings,
   orphan pockets, the ortho triple-check), the room tests, then a re-seeded rig +
   `qa/walk_test.py --room <room> --exhaustive` + a felt-click table.
8. **Record.** One row per prop in `qa/evidence/<lane>/<room>.md`: cells before · after · the frame.

## 2. Rules that came out of the four rooms

- **Retire a phantom, never re-point it for its own sake.** `camp crate_c/crate_wall/crate_r/bedroll_l`
  blocked bare firelit dirt; deleting them is the fix. Only re-point when the paint has a real mass that
  went uncollided (`camp bedroll_r` → the rolled bedroll under the lean-to).
- **Fires are props.** A painted fire must be impassable AND carry `campfire_pit` / `brazier` / `hearth` so
  the effects registry can seat VFX on it. Every lit fire in the four rooms accepted the walk before this.
- **A roofline is not a footprint.** `camp shelter` and `throne dais` were declared where the *paint of the
  structure* sits, several cells up-screen from the ground it stands on.
- **Paint outside the grid is not yours to fix here.** `tavern_snug`'s long bench, woodpile and big pillar
  and `shop`'s lower-right bench resolve to r ≈ 9.8-10.9 — outside a 10-row grid. Delete the prop that
  claimed them and note the plate/grid registration for the plate lane; do not stretch the grid.
- **Paint outside the ROOM is yours.** Where the plate paints an exterior the grid still calls floor —
  `throne_hall`'s arcade and stair band (c 12-14) and `shop`'s east stair landing — that band becomes wall.
- **Doors move only onto a painted opening AND a perimeter cell.** `shop`'s painted archway is at the
  interior cell `(10,5)`, which cannot be a door (`check_geometry`: doors are perimeter). The wired seam
  moved to the perimeter cell beside it, `(12,5)`, whose landing `(11,5)` is one step from the arch — and
  the back-wall cell `(6,0)`, which the plate paints as a shelf unit, was sealed into `wall_n_0`. Update
  `qa/seed_adventure_demo.py`'s `ROOMS` door table and `ALLOWED_UNWIRED` in the same commit.
- **Connectivity is a hard constraint on taste.** The tavern's bar reads as an L, but blocking the short
  arm's `(8,5)` orphans the whole area behind the bar — including the shop door's landing. The bar is
  authored as its long arm; the player walks around its open end, which is also how the room reads.
- **Occlusion is a different lane.** A hero standing on legitimate floor *behind* a tall prop looks
  embedded when the `boxes/*.json` sidecar does not mask him (crypt `(13,7)` behind the carved
  sarcophagus). That is a sidecar regeneration (needs the Editor's ExportBoxes) — never "fix" it by
  blocking open floor, which just re-creates the phantom-blocker class this method removes.
