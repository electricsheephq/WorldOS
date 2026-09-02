# Town-layout generator (Track C1) — DECISION RECORD 2026-09-02

**Status:** decided (orchestrator, Fable) after a 3-design / 6-lens red-team workflow (wf_880e2090; digest at
`session-notes/2026-09-02/worldos-refresh/artifacts/trackC/c1-design/DIGEST.md`). Implementation is queued BEHIND the
agent G4 pass on the demo (charter #1702); nothing here spends CU.

## What C1 is
The bridge from a seeded DISTRICT PLAN (hub square + N districts + town gate + the crypt entrance) to the input contract of
`tools/generate_town.py` (rooms / doorways / props in generator coordinates), so every district becomes a camera-sized
room-unit (14×11 at ortho 10.5224) joined by `cross_door` with MASKED boundaries — the 2026-07-12 tiled-space ruling: towns are a
LAYOUT problem, never a widened frame.

## Decision: design A (shape-grammar on a fixed lattice), bound by the red-team
1. **Substrate = a uniform lattice of 12×9 plots at pitch 13×10.** The one-cell gutter IS the wall/street/gutter and doubles as both
   neighbours' crop margin, so every door lands on the crop perimeter at match distance 0 (the snap never fires) and the frozen
   14×11 / 10.5224 camera contract holds town-wide. Rectangular footprints are mandatory (so `_stamp_room`'s wall replacement is a no-op).
2. **Grammar** `TOWN → HUB · DISTRICT{2..4} · GATE · CRYPT_MOUTH` with path-addressed blake2b hashing per decision (adding a district
   never re-rolls the others). **First town = 6–10 rooms** (BG1's nine-district city is the reference), not 200 — the grammar's 5–7
   arity is a feature at this scale; the 200-room claims of all three designs were unbuilt and are not a requirement.
3. **Class templates** (hub square, tavern, market, gate, crypt mouth, residential, temple, alley) with a reserved CLEAR CROSS +
   OUTER-BAND prop rule — BUT the rule must be made compositional: the red-team orphaned cells with two individually legal props
   (196/196 rooms red). So the repair loop is REAL (bounded 3 passes: shift/drop the offending prop, re-run check_geometry) and every
   template ships with a brute-force unit test over its placement options (the 1785-config sweep becomes CI).
4. **Exits**: the town gate is emitted as a level-exit seam (`room_b: ""`) and recorded in the seed's `allowed_unwired` — the travel map
   (W3 of #1640) attaches there. The `unwired_exits` key the design read from `<town>_world.json` does not exist; the seed adapter reads
   the plan file instead.
5. **Per-room class/material/district** ride as additive inert fields on `rooms[]` and a post-pass stamps `geo["material"]` per room
   (generate_town's single `--material` stays as the default).
6. **W1 (#1646 overworld schematic)** is the gate-slot oracle only for v1 (which lattice edge faces the road); the settlement→plan
   adapter is a later 40-line unit. B's and C's alternatives are retained in the digest as fallbacks (B if a non-lattice embedding is
   ever needed; C's isolation lemma as a test idea).

## Prerequisites — contract defects that hit ALL three designs (fix BEFORE C1 lands; each is small)
- `_resolve_kind` substring order mis-resolves `barrel→bar`, `merchants_cart→cart` → longest-match / word-boundary resolution + a test.
- `generate_town` writes `<town>_world.json` + `_plates_fragment.json` BEFORE printing gate errors → a red run leaves green-looking
  files; write only after validation (or delete on error).
- `walk_static.validate_repo` only sees the hard-coded `GEOMETRY_OF` + `qa/room_geometries/` → generated towns are invisible to CI;
  add a discoverable `qa/room_geometries/towns/<town>/` (or a manifest) so "walk_static clean" is not vacuous for towns.
- Landing-cell prop drops are SILENT (3.1 % of props in a fuzz, all pillars) → drop must log + count, and the dressing bars re-run after.

## The paint question decides the C2 order (spike BEFORE templates)
Every design ships enclosed walled courtyards until C2 exists (`build_room_unified.cs:332-340` destroys a room with no wall_run), and
50 % of doors fall on the camera-side edge (rendered as a 0.55u parapet notch by the kit builder, or behind a full 5.0u wall by the
unified builder). So: **panel ONE hub/market plate through the kit chain as a walled plaza first.** In-band → proceed with the
templates; out of band → C2's `outdoor:true` mode + a camera-side door-frame treatment (ranked ABOVE exterior prefab coverage) come
first. Report dead-end ratio and hub↔crypt door-crossing diameter as gate metrics on every generated town.

## Acceptance for C1 v1 (all machine-checkable)
`generate_town` exit 0 on 40/40 seeds for the 6–10-room grammar · byte-identical re-runs · zero auto-injected dressing ids ·
check_geometry + dressing bars + reciprocity + connectivity green per room · engine seed loads with all doors wired except the gate seam ·
the A-T text arc completes over the town graph at the 20-beat ruler · the walked arc (adventure_walk) crosses hub→tavern→crypt→throne→return.
Economics note (red-team, all designs): C1 removes gate-red iteration but not the per-plate cost (~5 CU + the 1-of-3 adoption yield);
the kit chain per district is the shipping surface (charter #1702), so plate count = district count, budgeted per town.
