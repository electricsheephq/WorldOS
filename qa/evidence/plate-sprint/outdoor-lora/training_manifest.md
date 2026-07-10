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
| 5 | ext_005_ruins_firstfires.jpg | Fandom PoE1 wiki, `Ar_0201_first_fires_exterior.jpg` (reused, `t_048.jpg`) | ruins, exterior | Fandom wiki | `asset_dWCdqiENwCyq9EdzDDQCfUTK` (reused) |
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
- `refc_068.jpg` / `refc_069.jpg` — western/eastern "exterior mountain",
  dropped to trim Scenario CU cost (see COST NOTE); not a named mandate
  category, and duplicative of each other.
- `refc_013.jpg` (jungle waterfall) / `refc_067.jpg` (Durgan's Battery ext) —
  3rd redundant frame in their respective sub-categories (jungle, fortress
  ext); dropped for the same cost reason.

## COST NOTE — Scenario plan CU limit hit (training blocked, needs owner)

Dry-run cost estimate for this 18-image `flux.1-lora` config: **1080 CU**
(confirmed linear at 60 CU/image against the 22-image dry run of 1320 CU).
Extrapolating from ARM C's actual spend (627 CU logged as "~<=$12"), 1080 CU
is comfortably under the lane's **$25 cap** (~$20.7 est.) with headroom left
for the smoke-test anchor mints.

**However, `train(action=start)` returned HTTP 429**:
`PlanLimitReachedError` — `actionName: "train-model"`, `actionLimit: 10000`,
`actionValue: 9754`, `limitScope: "team"`. The Scenario team plan's
train-model CU allowance is **97.5% consumed** (246 CU of headroom left,
against 1080 CU needed) — this is a hard account-level quota, not a
dollar-cost gate, and it is **not something I can resolve by trimming the
dataset further** (246 CU is not enough for any viable `flux.1-lora` run at
the mandate's 18-30 image floor).

Model `model_RsWEcQL2NWXwoyEodWVE2vWG` ("WorldOS Painterly Exterior (FLUX)")
is created with all 18 training images attached and ready — training has NOT
been started. This needs the owner to either (a) purchase additional Scenario
CU / raise the team's `train-model` plan limit, or (b) confirm this is a
recurring-period quota that will reset, before this lane can proceed to the
train → smoke-test phase.
