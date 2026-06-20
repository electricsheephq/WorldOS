# #461 — Grid / Coordinate Authority (design + PR ladder)

Status: PR-1 landed (movement spine). Additive, opt-in, no default behavior change.

## The additive model

The engine gains a SECOND positional model — a coordinate grid — that coexists with
the existing zone/theater model. Neither replaces the other:

- **Theater-of-the-mind** (no zones, no grid) — today's default; unchanged.
- **Zones** (S2.7) — named regions + adjacency; `move_to_zone` / `combatants_in_zone`
  stay LITERALLY unchanged (signature + docstring + behavior). A permanent first-class
  fallback.
- **Grid** (#461) — `(x, y)` cells, `grid_cell_size` ft each. Engaged ONLY by `set_grid`
  (the sole tool that flips `Combat.grid_enabled`).

All new state is sub-model fields with TODAY's-behavior defaults:

- `Combat`: `grid_enabled=False`, `grid_width=0`, `grid_height=0`, `grid_cell_size=5`,
  `diagonal_mode='chebyshev'`.
- `Combatant`: `x=None`, `y=None`, `moved_cells_this_turn=0`, `dashed=False`.

`grid_enabled=False` ⇒ zone/theater combat is BYTE-FOR-BYTE today's behavior (the grid
code paths are all guarded on the flag; `_combat_view` emits zero key delta off-grid).
No new TOP-LEVEL Campaign key, so `store.py`'s tolerant-load top-level strip is untouched
and old snapshots round-trip. A half-set `x`/`y` pair WARNs (never raises) so a tolerant
load can't crash on a malformed snapshot.

## PR ladder

| PR | Scope |
|----|-------|
| **PR-1** (this) | Movement spine: grid model + `set_grid` / `place_combatant_at_coords` / `move_to_coords`; Chebyshev distance; speed→cells budget; Dash; open-floor reachability; reach-leave opportunity-attack predicate (with the two SRD gates the zone loop omits — one Reaction/round, can't-see); measured-melee advisory in `attack()`; `_combat_view` grid block. |
| **PR-1.5** | The two renderer-facing read-only tools `measure_range` and `grid_reachable` (deferred from PR-1 purely for tool-schema budget — they are for the renderer, not core combat). Land alongside a deliberate schema-budget reclamation. |
| PR-2 | Area-of-effect templates + line-of-effect (`affected_tile_coords`). |
| PR-3 | Terrain (difficult terrain cost, blockers) + line-of-sight / cover. |
| PR-4 | Creature size + reach (>1-cell tokens, reach weapons). |
| PR-5 | Ranged-weapon range gating (normal/long range, disadvantage at long). |
| PR-6 | Movement modes (fly/swim/climb/burrow, half-cost climb). |
| PR-7 | Flanking (advantage from opposite-side allies — optional rule). |
| PR-8 | `five_ten_five` diagonal mode (the variant 5e diagonal rule; the model field is already reserved). |

## Additivity + fallback guarantee

- The grid never blocks. Over-budget, out-of-range, Speed-0, out-of-bounds, occupied —
  all surface as ADVISORY notes in the return (`movement_illegal`, `warnings`,
  `range_warning`), mirroring the zone model's posture. The DM adjudicates.
- The engine stays the SOLE WRITER. The read-only renderer helpers (PR-1.5) never call
  `save_campaign` or mutate state.
- Zone/theater is a permanent fallback — a DM can run any fight without ever touching the
  grid, and a grid fight degrades gracefully (unplaced combatants threaten/cost nothing).

## Downstream surface contract (renderer)

PR-1 carries position on the wire; the renderer consumes it in later increments:

- **render-profile v2**: `positioning: "grid"` (alongside the existing zone profile).
- **`/combat-surface`**: per-combatant `grid_x` / `grid_y`, plus `movement_available`
  (from `grid_reachable`, PR-1.5) and `affected_tile_coords` (AoE, PR-2).
- `_combat_view` already emits a `grid` block (`width`/`height`/`cell_size`/
  `diagonal_mode`) and per-order-entry `x`/`y` when on-grid — the minimal positional feed.
