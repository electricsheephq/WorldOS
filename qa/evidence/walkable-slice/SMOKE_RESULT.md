# WALKABLE-SLICE-V1 — smoke result

**Verdict: PASS.** The owner-playable loop works end-to-end — validated at the engine/viewer seam
(ground truth) AND rendered visually in the live Unity player on the GEX44 box.

## Loop steps (per-step pass/fail)

| Step | Mechanism | Result |
|------|-----------|--------|
| doors[] on surface | `_combat_doors` -> surface | PASS (`[{cell:[6,0], to:camp_clearing_night}]`) |
| rest walk | `walk_to_cell` | PASS (`ok, walked, to=[6,7]`) |
| NPC parley | `parley_approach` (Mira) | PASS (`ok`, engine walks + opens parley) |
| parley-surface schema | GET `/parley-surface?npc=` | PASS (`npc.name` read; client fixed to real schema) |
| door cross INTO camp | `cross_door(6,0)` | PASS (`ok, crossed=[6,0]` -> location `camp_clearing_night`) |
| **runtime plate swap** | poll detects location change | **PASS (backdrop crypt.png -> camp_clearing_night_v2.png; see frames)** |
| start_combat (fight in place) | `start_combat` | PASS (`combatants=[Aldric, Goblin]`) |
| attack resolves | on-turn `attack` (force_hit) | PASS (goblin hp 10 -> 1) |

## Real location ids
- crypt: `crypt`  · camp: `camp_clearing_night` (pinned stable in the seed; == the plates_manifest.json keys).

## Item-4 combatant-selection sanity verdict
**Correct.** `start_combat` selected party **+ present foe** (Aldric + Goblin Warrior); the NPC Mira
correctly did NOT join (she is in the crypt, not the camp). Required a fix to `_resolve_start_combat`
to include `kind == "monster"` (was `{npc, companion, player}` only — would have excluded the foe).

## parley-surface schema (verified, client read fixed)
`/parley-surface` returns `npc = {id, name, attitude, disposition, ...}` (a dict), `actor` = PC NAME
string, `free_form` = bool flag. The client now reads `npc.name` for the header (was reading a
non-existent `npc_name` / treating `actor`/`free_form` as dicts).

## Frames (frames/)
- `frame1_crypt.png` — crypt plate (rest), plates/crypt.png swapped in by the registry.
- `frame2_camp_after_cross.png` — **camp plate after cross_door: the plate visibly swapped.**
- `frame3_camp_combat.png` — combat in camp (goblin spawned at runtime, ally/foe rings).
- `frame4_camp_attack.png` — attack resolved (goblin HP bar depleted).

Captured on GEX44 Unity 6000.5.1f1, live player (`CombatSurfaceClient`) polling the WorldOS viewer
over an SSH reverse tunnel, direct-camera render (super_size 2). Plate assets on the box: crypt =
`crypt_firelit_v2.png` staged as `plates/crypt.png`; camp = `camp_clearing_night_v1.png` staged as
`plates/camp_clearing_night_v2.png` (v2 art not on the box; v1 stands in — the SWAP mechanism is what
this proves). Re-run the seam half via `drive_walkslice_seam.py` against a booted viewer.
