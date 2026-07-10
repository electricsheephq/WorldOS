# journey_eval.py — FIRST LIVE RUN (SHIP-CAMP session, 2026-07-11)

The deferred instrument-B run from PR #1506 (`qa/journey_eval.py`), executed for the first time
end-to-end against a live player build. This run rides the same session that shipped the adopted
true-greybox camp plate (PR #1518/#1519, `camp_clearing_night_truegrey_v1.png`) into the player
build — a fresh `WorldOSPlayer.app` (macOS Universal, `M1CombatV1_canonical` scene) was built on
GEX44 and driven locally.

**Per the task charter: this is a first-run validation of the INSTRUMENT itself. Defects found are
the deliverable — none were fixed in-lane.**

## Setup
- Campaign: `walkslice_smoke01` (`qa/seed_gfx_walkslice.py`, seeded fresh) — crypt (NPC "Mira the
  Keeper" + a doorway to camp) linked to `camp_clearing_night` (the freshly-shipped truegrey plate).
- Manifest: `qa/room_manifests/crypt_dense_v1.cells.json` (recipe_key=`crypt`, matches
  `seed_gfx_combat._build_crypt_grid`'s live prop cells exactly — the geometry the deployed
  `crypt_armb_iter3_v1.png` plate is painted around) — drives the auto prop-approach steps.
- Plan: **new** `qa/journey_plans/walkslice.json` (this run authored it — no walkslice plan existed
  before; `qa/journey_plans/camp.json` only covers the door/parley-less `camp_gfxdemo01` fixture).
  `start_cell=[3,5]`, `parley_cell=[3,6]` (Mira), `door_cell=[6,0]` (crypt->camp), `combat_cell=[9,7]`
  (a best-effort guess at the goblin's camp render cell from `camp_grid.spawns.npcs` — `spawn_monster`
  has no explicit cell param, see finding 2 below).
- 7 scripted steps -> 9 frames (3 prop approaches + parley + door-cross pre/post + combat-entry
  pre/post), all captured via SCK, 6/6 click steps landed (`clicks_ok=6`).
- VQA: `qa/vqa_frame.sh`, one `sonnet` `claude -p` pass per frame, `qa/journey_vqa_questions.md` v2
  (on_prop / t_pose / floating / missing_or_cloned / broken_backdrop + the harness-computed
  transition_backdrop_unchanged pair check).

## Verdict: **FAIL** — 1 of 9 frames flagged
`journey_verdict.json`: `passed=false`, `frames_checked=9`, `frames_with_defects=1`.

- **`combat_entry/post` -> `transition_backdrop_unchanged`** (`08_combat_entry_post.png`): the
  pre/post frames of the combat-entry click are near-identical — the scene did not visibly change
  the way a door-cross legitimately did (`door_cross` pair passed clean, `transition_backdrop_unchanged=false`).
  No goblin, health bar, or combat UI is visible in either combat_entry frame; Aldric is simply
  standing near the log pile. This is either (a) the `combat_cell` guess `[9,7]` missing the
  goblin's actual render position, or (b) the goblin failing to render/be clickable in the camp —
  the run cannot distinguish these; flagging both as follow-up, not fixing here.

All other automated flags were clean across every frame (no on-prop, t-pose, floating,
missing/cloned-actor, or broken-backdrop calls) — including the crypt props (sarcophagus, both
pillars) and the shipped camp plate's establishing shots (`06_door_cross_post.png`,
`07_combat_entry_pre.png`), which render the new truegrey camp cleanly: campfire, log seat, crate
stack, and ruin wall all read as a single coherent painted scene with Aldric grounded beside it.

## Additional manual observation (NOT asked by the tracked VQA questions — flagging for awareness only)
Eyeballing the frames directly (beyond the automated flags): the **"Mira the Keeper" parley dialog
opened at `04_parley_step.png` is still visibly open in `05_door_cross_pre.png`, `06_door_cross_post.png`
(the post-transition CAMP frame), and `07_combat_entry_pre.png`** — it persists across the door-cross
room transition instead of being dismissed, and only disappears by `08_combat_entry_post.png`. This
journey's script never issued an explicit "Leave" click before crossing the door (the plan has no
dialog-dismiss step), so this may be a plan-authoring gap rather than a product defect — but a modal
surviving a backdrop swap underneath it is worth a human look. Not filed as an issue in-lane per the
"first run = report, don't fix" charter; noting it here for the follow-up owner.

## Files
- `frames/` — all 9 captured PNGs (SCK captures, 1280x800 windowed).
- `frames_manifest.json` — the capture manifest (paths, step/kind/side, capture_ok, hashes).
- `journey_script.json` — the derived 7-step script (manifest + plan -> steps).
- `journey_verdict.json` — the VQA verdict (per-frame flags + aggregated defects).
- `qa/journey_plans/walkslice.json` (repo root, not under evidence/) — the new plan this run authored.

## Environment
- Player build: GEX44 box (`root@46.4.26.123`, Unity 6000.5.1f1, `Tools/WorldOS/Build/macOS Player
  (Universal)`), 0 compile errors, 1 pre-existing warning. Delivered to
  `~/worldos-session-notes/w5a-build/WorldOSPlayer.app.zip` on this Mac.
- Capture host: local Mac (native_palette / ScreenCaptureKit) — the actual host `qa/player_smoke.sh`
  / `qa/journey_eval.py capture` target (macOS-only: Swift perms probe, `osascript`, CGWindowList).
  GEX44 is the Unity editor/build box only; it does not run the macOS player.
- `FORCE_PLAYER_QA=1` was set for this explicitly-requested run (the owner-active guard's HID-idle
  check read a recent input timestamp from this same interactive session; the QA click channel is an
  in-process HTTP listener, not a synthetic OS click, so it does not contend with foreground input).
