# SWEEP-PRECISION evidence

The occlusion-exemption fix to `qa/journey_visual_sweep.py`'s inverse-coherence check (#1552 follow-up).
Full walkslice-world sweep, scratch viewer, before vs after this fix — same code base commit
(`fc1a24a9`, includes #1564 CAMP-CELLS + #1565 FRESH-CRYPT), only the detector logic differs.

`before/report.json` — the pre-fix sweep (footprint-only exemption, as it shipped through #1552).
`after/report.json` — the post-fix sweep (footprint + authored-occlusion exemption, this PR).

## All-rooms CLEAN% — before vs after

| room | plate | CLEAN% before | flags before | CLEAN% after | flags after | occlusion-exempted |
|---|---|---|---|---|---|---|
| crypt | `crypt_fresh_v1.png` (#1565's canonical crypt, painted over the sparser un-reconciled walkslice grid, #1559) | **77.9%** | 21/94 | **90.5%** | 9/94 | 12 |
| camp_clearing_night | `camp_clearing_night_truegrey_v1.png` | 95.6% | 6/134 | 95.6% | 6/134 | 0 (see note) |
| tavern | `tavern_fit2_v1.png` | 100.0% | 0/43 | 100.0% | 0/43 | 0 (already clean) |
| **overall** | | **90.1%** | 27 findings | **94.5%** | 15 findings | |

**crypt is the headline case this PR closes.** `crypt_fresh_v1.png` is #1565's richly-authored 20-prop
crypt, adopted as the canonical `crypt` plate — but the *live* walkslice engine grid hasn't been
re-authored with fresh-crypt's full prop set yet (`qa/evidence/crypt-fresh/WALKSLICE-RECONCILIATION.md`,
#1559, explicitly deferred). So today's live crypt room only authors 3 props (`pillar_l`, `pillar_r`,
`sarcophagus` — the sparse incumbent geometry), while the plate paints all 20. Before this fix, every
cell the plate's extra silhouette fell on was flagged as "invented furniture" (21 flags, all of them
really just the plate legitimately being denser than the grid). After this fix, `resolve_occlusion_cells`
matches each of those 3 *live* props by id+footprint against the committed manifest corpus
(`qa/room_manifests/*.cells.json`) and exempts the 12 cells that genuinely fall inside their authored
occlusion bands (`pillar_l`/`pillar_r` resolve via `crypt_dense_v1.cells.json`, `sarcophagus` via the same
file's 35-cell computed silhouette — see `after/report.json`'s `rooms[].occlusion_notes`).

**The 9 flags that remain are correctly NOT suppressed.** They belong to the separate, already-tracked
walkslice-reconciliation gap (#1559) — plate content with no authored prop backing it in the live grid at
all. This fix only ever exempts a cell inside a prop's *authored* occlusion; it never blanket-suppresses
a room just because it's dense. The negative control in `qa/test_journey_visual_sweep.py`
(`test_red_first_genuinely_invented_tavern_furniture_still_flags`) proves the same discipline on
committed, unrelated assets: the old `tavern_truegrey` plate, cross-referenced against the later
`tavern_fit2` manifest (a stress case — a manifest generation the live room never used), still flags
4 of 5 baseline cells because no authored prop anywhere covers them.

**camp shows 0 exempted, but for two DIFFERENT reasons, both visible in `occlusion_notes`, neither a
defect in this fix:**
1. 16 of camp's 22 live props resolve occlusion cleanly (`camp_truegrey.cells.json` /
   `camp_clearing_night_v2.cells.json`); the other 6 (`camp_sack`, `firewood_tail`, `gear_stones`,
   `ruin_rubble1`, `ruin_rubble2`, `shelter_post_r`) are PR #1564's brand-new camp props (CAMP-CELLS,
   merged the same day as #1565) — the committed manifest predates them, so `resolve_occlusion_cells`
   correctly logs "no manifest prop matches" and falls back to no exemption (a manifest-regen follow-up).
2. Camp's 6 flagged cells (`(3,10) (4,10) (9,5) (9,6) (9,7) (9,8)`) are NOT footprint-vs-occlusion at
   all — #1564's own commit message identifies them as the detector's silhouette band sweeping UP-AND-
   BACK on screen into a NEIGHBOURING object (the fire, a deliberately-walkable bedroll mat) rather than
   anything painted at the flagged cell's own floor position, i.e. an ALREADY-TRIAGED false-positive
   class this PR does not touch (a different defect from the one #1552/#1565 raised). Camp's CLEAN%
   is correctly unchanged (95.6% both runs) — no regression, no silent improvement claimed.

## Red-first unit proof (`qa/test_journey_visual_sweep.py`)

* `test_red_first_fresh_crypt_dense_room_flags_drop_after_occlusion_exemption` — #1565's fresh crypt
  scored against its OWN derived manifest (`qa/room_manifests/crypt_fresh.cells.json`, all 20 props
  matching by id+footprint): 15 baseline flags -> **0** after the fix, all 15 moved to `exempted`.
* `test_red_first_genuinely_invented_tavern_furniture_still_flags` — the negative control described
  above: 5 baseline flags -> 4 still flagged, 1 exempted (the shared `hearth` prop's silhouette band).

Reproduce: `python3 -m pytest qa/test_journey_visual_sweep.py -q` (13 pre-existing + 9 new = 22 tests,
pure functions, no engine/HTTP).

Reproduce the live sweep: `python3 qa/journey_visual_sweep.py run --state-dir <scratch> --rundir <dir>
--port <port ≥8767, never 8766>`.
