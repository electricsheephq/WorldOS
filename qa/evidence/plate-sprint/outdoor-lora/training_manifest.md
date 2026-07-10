# OUTDOOR-LORA training-set manifest

Curated exterior-heavy training set for **`WorldOS Painterly Exterior (FLUX)`**
(`model_RsWEcQL2NWXwoyEodWVE2vWG`, Scenario, `flux.1-lora`, project
`proj_sd4w3ozsJaBHYnHS4mP1S2Pw`), assembled to counter the interior-heavy ARM C
LoRA (`model_G379oza2qhm6MkqDrtTvvmmw`, issue #1481) that regresses the
outdoor `forest_road` plate class to the 6.0 quality cap (PR #1495,
`qa/evidence/plate-sprint/cb-forest/`).

18 images, all char-free (no human/creature figures), all real painterly
references except one adopted WorldOS-generated plate (flagged below). Sourced
from the same corpus that built the interior-heavy
`worldos-poe2-clean-env-v4` model (`model_ng6MSaWtyvwKbZyDBMtzv7tA`) — that
corpus (`~/worldos-session-notes/poe2-refs-clean/`, `manifest.json`, 77
candidate PoE1/PoE2/Tyranny reference frames) is 43/48 interior-biased in its
curated `SELECTED.txt`; this set instead pulls the corpus's EXTERIOR
candidates that were left uncurated for v4, plus reuses v4's 5 already-curated
exteriors (Scenario asset IDs below reused directly, no re-upload).

## Selection rule
REAL painterly references first (Fandom PoE1/PoE2 wiki "File:" pages +
RPGFan press/promo screenshots — the same source class already used and
owner-approved for `worldos-poe2-clean-env-v4`), covering forest, camp, town
street/square, coast, ruins-outdoor, and battlefield-adjacent open terrain.
Excluded: `refc_015.jpg` (Tyranny battlefield exterior) — the only frame in
the source corpus explicitly tagged as depicting a battlefield, but flagged in
`manifest.json` as having small distant NPCs visible ("may not meet strict
character-free bar") — dropped for the char-free requirement. No other clean
char-free battlefield-specific frame was found in the corpus; `bog.jpg`
(#18 below) is the closest visual stand-in (its own Scenario auto-caption
independently read it as a "war-torn landscape ... abandoned military
vehicles, rusted tank remnants" — an unplanned but useful battlefield-adjacent
texture note for a wetland/waste frame).

Excluded from the initial 22-image pull to keep training cost inside the
lane's Scenario CU budget (see COST NOTE below): 2 duplicate-concept
"exterior mountain" frames (`refc_068`/`refc_069`, western/eastern exterior)
and 2 redundant frames (`refc_013` jungle waterfall — 3rd jungle-biome shot;
`refc_067` Durgan's Battery ext — 3rd fortress-exterior shot). Category
coverage held up without them.

## Manifest

| # | file (local) | source | biome | license / provenance note | Scenario asset_id |
|---|---|---|---|---|---|
| 1 | ext_001_fortress_raedric_hold.jpg | Fandom PoE1 wiki, `Ar_0707_raedrics_hold_ext.jpg` (reused from v4 curated set, `t_044.jpg`) | fortress_stronghold, exterior | Fandom community wiki, editorial/reference use, same class as owner-approved v4 corpus | `asset_58ehPSyWAB4dVpWmp4oNg9LS` (reused, no re-upload) |
| 2 | ext_002_ruins_oldsong.jpg | Fandom PoE1 wiki, `Ar_1201_oldsong_ext.jpg` (reused, `t_045.jpg`) | ruins, exterior | Fandom wiki | `asset_fdVzG3S1Y4jyR8ErCLqLCyvq` (reused) |
| 3 | ext_003_temple_abbey.jpg | Fandom PoE1 wiki, `Px2_0301_abbey_ext.jpg` (reused, `t_046.jpg`) | temple_sacred, exterior | Fandom wiki | `asset_pkgacsDZsqxXeMRrp8bNZVRK` (reused) |
| 4 | ext_004_settlement_brackenbury.jpg | Fandom PoE1 wiki, `Ar_0401_brackenbury_exterior.jpg` (reused, `t_047.jpg`) | settlement, exterior (town street) | Fandom wiki | `asset_uzqFWgHgyghpnmM3QFDyfvKi` (reused) |
| 5 | ext_005_western_exterior_mountain.jpg | Fandom wiki, `Px1_0301_western_exterior.jpg` (`refc_068.jpg`) | mountain, exterior | Fandom wiki, PoE1 | `asset_XhU3yw8GWrJx5gWLFxsrPimf` (already-uploaded, re-attached at no extra cost) |
| 6 | ext_006_jungle_path.jpg | RPGFan, `Pillars-of-Eternity-II-Deadfire-Artwork-007.jpg` (`refc_002.jpg`) | exterior (forest-road analog) | RPGFan press/promo screenshot, editorial use | `asset_KwmxyNGS274jtUWMZ3BbpfHC` |
| 7 | ext_007_jungle_courtyard_shrine.jpg | RPGFan, `...Screenshot-066.jpg` (`refc_003.jpg`) | exterior (ruins-outdoor analog) | RPGFan press screenshot | `asset_hWtoxJFBN1i6UeJfe3CR7Gz8` |
| 8 | ext_008_twinelms_hearthsong_village.jpg | Fandom wiki, `Twin-elms-hearthsong.jpg` (`refc_004.webp`) | exterior (town street) | Fandom wiki, PoE1 | `asset_nYiu6Gu6GXWdRKSh9TKfMhYs` |
| 9 | ext_009_ondras_gift_coast.jpg | Fandom wiki, `Ar_0103_def_bay_ondras_gift.jpg` (`refc_006.webp`) | settlement, exterior (coast) | Fandom wiki, PoE1 | `asset_ofmGDEpi6ur85RnTBgA6LAzJ` |
| 10 | ext_010_night_camp.jpg | RPGFan, `Pillars-of-Eternity-Screenshot-001.jpg` (`refc_007.jpg`) | exterior (camp) | RPGFan press screenshot — **URL re-derived from gallery index position, not independently re-verified this session** (flagged in source `manifest.json`) | `asset_v7ADRUjCQACLPJdXh9nbsBKR` |
| 11 | ext_011_gilded_vale_wilderness.jpg | Fandom wiki, `Ar_0701_gilded_vale_wilderness.jpg` (`refc_063.jpg`) | forest, exterior | Fandom wiki, PoE1 | `asset_SquJXAUqH9D9R8zaRXu2dztd` |
| 12 | ext_012_black_meadow_wilderness.jpg | Fandom wiki, `Ar_0801_black_meadow_wilderness.jpg` (`refc_064.jpg`) | forest, exterior | Fandom wiki, PoE1 | `asset_VzYgaAA38o6QuHFS9NMmqYGg` |
| 13 | ext_013_stronghold_ext_pristine.jpg | Fandom wiki, `Ar_0601_strongholdexterior_pristine.jpg` (`refc_065.jpg`) | fortress_stronghold, exterior | Fandom wiki, PoE1 | `asset_LydPVC6jauLTGaYsmKMhPhTD` |
| 14 | ext_014_bridge_district.jpg | Fandom wiki, `Ar_0112_bridge_district.jpg` (`refc_072.jpg`) | settlement, exterior (town street/bridge) | Fandom wiki, PoE1 | `asset_oR9zJaAB79sPJv7KG6N7GfUr` |
| 15 | ext_015_madhmr_bridge.jpg | Fandom wiki, `Ar_0718_madhmr_bridge.jpg` (`refc_073.jpg`) | settlement, exterior (town street/bridge) | Fandom wiki, PoE1 | `asset_q26TbJau96Z2qgF2Cuw5qPzN` |
| 16 | ext_016_woodend_plains.jpg | Fandom wiki, `Ar_0811_woodend_plains.jpg` (`refc_076.jpg`) | wetland_waste, exterior (open-field/battlefield-adjacent) | Fandom wiki, PoE1 | `asset_2jzyZEd1ukzp5emeiukC1zae` |
| 17 | ext_017_bog.jpg | Fandom wiki, `Px2_0502_bog.jpg` (`refc_077.jpg`) | wetland_waste, exterior (battlefield-adjacent — see selection-rule note) | Fandom wiki, PoE1 | `asset_GbPoqFErs6rfifNa6QFTr18T` |
| 18 | ext_018_camp_clearing_night_v2_ADOPTED.jpg | **WorldOS-generated, adopted** (`qa/evidence/plate-audit/camp_clearing_night_v2.jpg`, 2026-07-09) | camp, exterior | Our own adopted outdoor plate — the cross-lane "house-best" incumbent reused as a panel anchor in PR #1495/#1490; NOT a low-scoring generated slop frame (it is the adopted 6.0-9.0-range incumbent). Already independently reused as an ARM C training image (`asset_kpVSMEiqkxhgtmvuMpXD8v6t`), so this precedent existed before this lane. | `asset_kpVSMEiqkxhgtmvuMpXD8v6t` (reused, same asset as in ARM C's training set) |

**Copyright note (transparency, not a new decision):** items 6-17 are sourced
from Fandom-wiki "File:" pages and RPGFan press/promo screenshots of Obsidian
Entertainment's Pillars of Eternity — the identical source class (same wiki,
same screenshot galleries) already used and owner-approved to build
`worldos-poe2-clean-env-v4`. This manifest documents source URL + license
class per image (the owner's stated repeatability condition) but does not
re-litigate that precedent.

## Excluded (deliberately, this pass)
- `refc_015.jpg` — battlefield exterior, characters visible (char-free bar).
- `refc_069.jpg` — eastern "exterior mountain", duplicative of the western
  frame (#5) once that was swapped back in; not a named mandate category.
- `refc_013.jpg` (jungle waterfall) — **has characters** ("three adventurers
  stand on a sandy beach"), caught on the quality-pass caption re-check;
  correctly excluded already for cost, now double-confirmed exclude for
  char-free.
- `refc_067.jpg` (Durgan's Battery ext) — 3rd redundant fortress-exterior
  frame; dropped to keep architecture-heavy content from dominating (see
  Quality pass below).
- `t_048.jpg` / `refc_070.jpg` ("ruins_firstfires") — **swapped OUT** in the
  quality pass (see below): visually a grand cathedral/basilica plaza
  (domes, colonnades, twin statues), not simple ruins — the single most
  architecture-dominant frame in the set. Swapped for `refc_068.jpg`
  (western exterior mountain, already-uploaded, no extra cost) to shore up
  genuine natural-terrain representation for the forest_road/camp target
  rooms.

## Quality pass (owner-directed, before training — 2026-07-10)

Per owner discipline ("models are expensive — make prep top-notch before
spending"), every one of the 18 images was re-verified at full resolution
(not thumbnail) before `train(action=start)`, plus two were crop-zoomed for a
disputed detail:

1. **Char-free re-check (zoom, not thumbnail):** all 18 confirmed free of
   living human/creature figures.
   - `ext_005` (then "gilded_vale_wilderness", now the mountain swap target)
     had one ambiguous humanoid silhouette — crop-zoomed 3x
     (`/tmp/gilded_vale_zoom.png`): confirmed a stone/bronze **statue on a
     pedestal** (matches the same wayside-monument convention as the
     confirmed statues in `ext_004` and the swapped-out `t_048`), not a
     living figure. Kept.
   - `ext_010` ("night_camp") — caption mentions "cave walls"; visual check
     confirms it's a rock-alcove/grotto clearing, technically exterior (sky
     and tree canopy visible top-left) but more enclosed than an open forest
     camp. Kept — it's the corpus's only real "camp" reference — but flagged:
     lean on `ext_018` (our adopted `camp_clearing_night_v2`, open-sky) as the
     cleaner camp exemplar in this set.
   - `ext_015` (madhmr_bridge) has 2-3 small reddish shapes in the grass —
     confirmed wildlife (resting deer/animal), not humanoid figures. Common
     PoE scene-dressing convention, not disqualifying.
   - `refc_013.jpg` (jungle waterfall, already excluded for cost) was
     independently caught here too: its caption reads "three adventurers
     stand on a sandy beach" — genuinely has characters. Good thing it was
     already out; this closes the loop on why.
2. **Resolution / compression / watermark check:** all 18 are clean JPEGs,
   long-edge ~2048-2560px (5 reused v4 assets sit at 2560x2015-2560x2758 from
   prior curation, the 13 new uploads were resized to 2048 long-edge this
   session) — no corruption, no watermarks, no visible compression artifacts
   at 1:1. The two resolution tiers are a minor inconsistency but not a
   blocker (Scenario's training pipeline buckets/resizes inputs internally).
3. **Auto-caption vs. actual-content mismatches found** (Scenario's
   auto-captioner, not a manifest error, but worth recording since it changed
   my read of category balance): `t_046` ("temple_abbey") is actually
   captioned/depicts a grand ice-fortress complex, not a simple abbey;
   `t_048` ("ruins_firstfires") is captioned/depicts a full cathedral plaza,
   not ruins; `ext_017` ("bog") was mis-captioned by Scenario as a "war-torn
   landscape... abandoned military vehicles, rusted tank remnants" — visual
   check confirms this is a captioning misfire, the actual image is a
   legitimate marsh/ruins scene with wooden boardwalks and a skull-motif
   stone entrance, no military hardware, no characters.
4. **Category-balance rebalance (the actual finding that changed the set):**
   the corpus's own PoE-wiki "biome" tags undersold how architecture-heavy
   several frames actually are once viewed (see point 3) — grand
   fortress/castle/cathedral content was a de-facto plurality (~7-8/18)
   against genuinely natural/rustic content directly analogous to the two
   smoke-test target rooms, forest_road and camp (~5-6/18: jungle_path,
   gilded_vale hillside-path, black_meadow tents+campfire+skeleton,
   woodend_plains clearing, madhmr_bridge, camp_clearing_night_v2). This is
   the same failure shape as ARM C (architecture bias invading natural
   scenes), just latent rather than 10/10 dominant. **Action taken:** dropped
   the single most architecture-dominant frame (`t_048`/"ruins_firstfires" —
   full cathedral plaza) and swapped in `refc_068.jpg` (western exterior
   mountain: snowy rocks, sparse trees, frozen ponds, zero architecture,
   already uploaded from the cost-trim pass, asset_XhU3yw8GWrJx5gWLFxsrPimf)
   — same 18-image count, same 1080 CU cost (re-confirmed by dry-run after
   the swap), better-aimed at the target rooms.
5. **Dedupe check:** no near-identical frames found among the final 18 (the
   one true duplicate scene in the wider corpus, `refc_066`/`Raedric's Hold
   ext` vs `t_044`, was already resolved by using only `t_044`).

## Cost + quota — RESOLVED, training started

Owner added 5,000 CU to the Scenario account (2026-07-10) after model-count
inventory was checked (7 private models on the account, none an exterior
FLUX LoRA — this training is not redundant). Post-swap dry-run reconfirmed
**1080 CU** (~$20.7 est., unchanged from pre-swap — image count is the cost
driver here, not composition), still comfortably under the lane's $25 cap.

`train(action=start)` succeeded: **job `job_U3HDsSj7T4aPETy7MRCuX6oK`**,
`flux-model-training`, `cuCost: 1080`, status `queued` → `running-train` as
of 2026-07-10T10:21 UTC. Config: `rank=64, learningRate=0.0001,
learningRateTextEncoder=0.00001, nbEpochs=10, nbRepeats=20` (mirrors ARM C's
proven config). ETA comparable to ARM C's 111 min run.

**Note for future training on this account:** the Scenario account's
model-count limit could not be read via the API (`GET` on the relevant
endpoint returns 403, role scope). If a future model create/train call ever
fails on a count-limit error, **stop and report — do not delete existing
models** to work around it.
