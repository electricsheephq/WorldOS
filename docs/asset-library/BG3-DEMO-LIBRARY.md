# BG3-Style Demo Asset Library (Tripo3D bulk generation)

> Owner-directed 2026-07-21: "design up and go until all tokens used up — sample demo
> town/dungeon/creatures/characters + a larger 4x-square boss." Parallel graphics lane;
> does NOT displace active charter #1386 (Act II close-out).

## Budget (live-verified 2026-07-21)

- Tripo3D balance at start: **4,835 credits** (`GET /v2/openapi/user/balance`).
  (Owner believed ~41k — dashboard-vs-API discrepancy; API is source of truth.)
- Measured costs (`.claude/skills/asset-gen/TRIPO_PIPELINE.md` + `--dry-run`):
  P1 lowpoly gen ~20 cr · v3.1 gen ~40 cr · full humanoid gen→rig→1 retarget ≈ ~100 cr,
  each extra retarget ~10–20 cr · rig-check free.
- Plan: ~3,550 cr roster + ~1,285 cr (27%) retry/variant reserve. Hard stop at balance < 100.

## Style contract (pinned across every prompt)

`stylized hand-painted dark fantasy RPG, muted earthy palette, game-ready low poly`
— original phrasing only (Copyright Guard: never name BG/PoE/Disco as a style source).
Characters: `A-pose, full body, feet on ground`. Model: `P1-20260311` (game/low-poly,
best for characters) for everything except the boss, which gets `v3.1-20260211` for detail.
Output: rigged **FBX** (`spec=mixamo` biped / `tripo` creatures) → Unity import as
`animationType=Generic` (NEVER Humanoid — silently drops clips).

## Wave 1 — Party: 12 BG3 classes in signature races (biped; walk idle run slash; ~130 cr ea)

| asset_id | prompt core |
|---|---|
| pc_fighter_human | human male fighter, plate armor, longsword and kite shield |
| pc_wizard_elf | high elf male wizard, long robe, gnarled staff, spellbook at hip |
| pc_cleric_drow | drow female cleric, chainmail, mace and round shield, holy symbol |
| pc_rogue_tiefling | tiefling male rogue, dark leather armor, dual daggers, hood |
| pc_ranger_elf | wood elf female ranger, hooded cloak, longbow, quiver |
| pc_barbarian_halforc | half-orc male barbarian, fur and hide, greataxe, bare chest |
| pc_paladin_dragonborn | dragonborn male paladin, ornate heavy armor, warhammer |
| pc_warlock_human | human female warlock, dark tattered robes, eldritch staff |
| pc_sorcerer_tiefling | tiefling female sorcerer, draconic scale accents, flowing robe |
| pc_bard_gnome | gnome male bard, colorful doublet, lute on back, rapier |
| pc_druid_elf | elf female druid, leaf-and-bark garb, wooden staff, antler circlet |
| pc_monk_githyanki | githyanki male monk, wrapped forearms, simple gi, bald |

## Wave 2 — Town NPCs (biped; walk idle; ~110 cr ea)

| asset_id | prompt core |
|---|---|
| npc_blacksmith | burly human male blacksmith, leather apron, smithing hammer |
| npc_merchant | stout human male merchant, fine tunic, coin purse, scroll |
| npc_guard | human male town guard, chain shirt, spear, kettle helm |
| npc_beggar | elderly human female beggar, ragged shawl, walking stick |

## Wave 3 — Creatures (Tripo-only rig types; walk idle; ~100–130 cr ea)

| asset_id | prompt core | expected rig_type |
|---|---|---|
| cre_wolf | dire wolf, on all fours, hackles raised | quadruped |
| cre_owlbear | owlbear, bear body owl head, on all fours | quadruped |
| cre_giant_spider | giant spider, eight legs, venomous fangs | octopod |
| cre_intellect_devourer | intellect devourer, walking brain on four clawed legs | quadruped |
| cre_raven | giant raven, wings spread, perched | avian (the "air" unit) |
| cre_goblin | goblin warrior, green skin, scavenged armor, scimitar | biped |
| cre_skeleton | animated skeleton warrior, rusted sword and shield | biped |
| cre_imp | small winged imp, leathery wings, barbed tail | biped |

## Wave 4 — Boss (4×4 squares = Gargantuan; v3.1 detail; walk idle slash; ~150–200 cr)

| asset_id | prompt core |
|---|---|
| boss_young_red_dragon | young red dragon, massive, wings folded, four legs, horns, scales, gargantuan boss |

## Wave 5 — Props: town + dungeon demo set (static P1 lowpoly, no rig; ~20 cr ea)

Town: prop_tavern_table, prop_wooden_chair, prop_barrel, prop_crate, prop_market_stall,
prop_well, prop_lantern_post, prop_hand_cart, prop_signpost, prop_anvil, prop_fountain,
prop_bench.

Dungeon: prop_treasure_chest, prop_sarcophagus, prop_iron_gate, prop_torch_sconce,
prop_stone_pillar, prop_rubble_pile, prop_altar, prop_brazier, prop_wooden_door,
prop_gargoyle_statue, prop_bookshelf, prop_throne, prop_portcullis.

(25 props ≈ 500 cr.)

## Ops
- Driver: `tools/asset_library_batch.py` — queue-driven, checkpointing, resumable,
  stops at balance < 100; one JSONL manifest row per item (task ids, files, credits).
- Binaries (gitignored class): `/Volumes/LEXAR/Codex/worldos-asset-library/<asset_id>/`
  (GLB+FBX+albedo). Box deploy to `Assets/cast|props/<id>/` is a separate step (LFS
  blocked; LEXAR tarball is the save story).
- Registry: rows appended to `data/asset-registry/registry.json` after generation with
  `gen_recipe` = exact prompt + Tripo task ids (regenerable).
- Known limits (live-tested facts): output URLs expire ~5 min (wrapper downloads
  immediately); retarget = ONE preset per call; creatures support fewer clips
  (per-clip failures non-fatal); FBX imports untextured → assign albedo separately.

## Outcome (2026-07-21, issue #1628)

- **68/68 assets generated, zero permanent failures.** 33 rigged (12 party, 6 NPCs, 13 creatures,
  2 bosses) + 35 static props. 655 MB on LEXAR (`/Volumes/LEXAR/Codex/worldos-asset-library/`).
- **Credits: 4,835 → 215** (95.5% spent; 215 held as integration-regen reserve above the 100 floor).
- Added waves 6–7 during the run (ogre 2nd boss, zombie/giant_rat/mimic/gnoll/harpy/doppelganger,
  noble/priest, 9 more props) to honor the owner's "use the tokens up" directive.
- Intellect devourer regen'd once (rig-check `rig_type:"others"` → quadruped-phrased prompt fixed it).
- Avian rigs (raven, harpy) ship rigged-but-clipless: NO avian retarget presets exist on Tripo
  rigging v2.5 (probed 2026-07-21, recorded in TRIPO_PIPELINE.md). Quadruped/octopod = walk only.
- All 68 registered in `data/asset-registry/registry.json` (exact-resolve verified via
  `viewer/asset_registry.py`). Binaries stay off-git per the registry convention; box deploy of
  `rigged.fbx`/`model.glb` → the registry's `Assets/cast|props/<id>/` paths is the follow-up step.
