# RECALL — owner playtest #7 vs journey-eval run 1 (the legal-path blind spot, #1523)

This table is the honest instrument record for `journey_eval.py` v1's first live run
(`journey_verdict.json`, this directory; see `NOTES.md` for the full run report). It compares what a
**human playtest** (owner, playtest #7 — `qa/evidence/camp-tune/findings.json`) found by hand against
what the **automated journey-eval instrument** caught on its first live run, so the instrument's real
recall — not its intended recall — is on record before anyone cites a clean journey-eval run as proof
a room has no missing-footprint defects.

## Why this comparison exists

`journey_eval.py` walks a scripted path of clicks the ENGINE considers legal, then asks factual VQA
questions of the resulting frames. That method has a structural hole named in #1523: it **cannot
click onto a cell the engine already refuses** (an unwalkable/impassable cell), so it can never
observe the specific defect class "the engine accepted a move onto a cell whose paint reads as a
solid object" — a missing-footprint bug is invisible to a run that only walks legal paths. Playtest
#7 found exactly that class by hand, on the same room (`camp_clearing_night_truegrey_v1.png`), before
any of it was fixed. The recall table below is the receipt.

## Owner playtest #7 defects (human eyeball, pre-fix) — `qa/evidence/camp-tune/findings.json`

| # | Defect | Class | Fixed by (author_room_geometry / derive_room_manifest re-authoring) |
|---|--------|-------|---|
| 1 | **Woodpile** walkable (should block) | missing footprint | `FIREWOOD_CELLS` extended onto the painted log mass |
| 2 | **Crate stack** (left) walkable on boxes + a phantom blocked cell with no paint under it | missing footprint + phantom occlusion | `CRATE_L_CELLS` re-authored to the 3 actually-painted boxes; phantom `POST_CELLS` retired |
| 3 | **Hut/shelter** (top-center) walk-through | missing footprint | `SHELTER_CELLS` re-authored to the posts + back wall/canvas |
| 4 | Top-right **trees / camp exit**: over-covering occlusion hid the player near the exit | occlusion-hull bug (not a footprint miss) | `wall_br` split into 3 short runs — hull shrank 48 cells → 7 cells apiece |
| — | **Fire-pit blocking** | — | Confirmed CORRECT already — not a defect; no fix needed |

Note: item 4 (trees/exit) is an *occlusion* bug (the silhouette hull over-covered open ground), not a
missing-footprint bug like 1-3 — it's included here because it's part of the same playtest-#7 punch
list the owner named, but it's a different mechanism (see `docs/ROOM-PIPELINE-RUNBOOK.md` step 2,
"footprint-vs-occlusion is the distinction CAMP-TUNE's defect #5 turned on").

## What journey-eval run 1 caught (`journey_verdict.json`, PR #1520, ship-camp-1 session)

The run walked the crypt→parley→door-cross→combat-entry loop over `walkslice_smoke01` (a different
campaign/manifest than camp-tune's fixture, but the same class of question — does the legal path look
right). 9 frames captured, 6/6 click steps landed clean.

| Finding | How caught | Class |
|---|---|---|
| `combat_entry/post` → `transition_backdrop_unchanged` (1 of 9 frames flagged) | Automated VQA (transition pair-check) | Combat-entry not visibly registering (goblin cell guess miss, or a render/click gap) — NOT a footprint defect |
| "Mira the Keeper" parley dialog persists across the door-cross transition | Manual eyeball, NOT one of the tracked VQA questions | UI/dialog-lifecycle gap, flagged for awareness only, not filed in-lane |

**All automated on-prop / t-pose / floating / missing-or-cloned / broken-backdrop flags were clean
across every frame** — including direct approaches to the crypt's sarcophagus and both pillars.

## The recall number

**0 of the 4 playtest-#7 missing-footprint-class defects (woodpile, crate stack, hut, trees/exit)
would have been catchable by journey-eval v1's methodology, even on this exact run's room** — not
because they were re-introduced (camp-tune's fixes were already merged by the time this run executed),
but because journey-eval v1 structurally cannot produce the observation "the engine accepted a click
onto a painted-solid cell." It only asks factual questions of frames reached via LEGAL clicks; a
missing-footprint bug is, by definition, a cell the engine treats as legal that shouldn't be. Confirmed
directly in #1523's own framing: *"currently 0/4 by the legal-path run."*

**What journey-eval v1 IS good at (this run's real catch):** transition-integrity questions (did the
backdrop plausibly change on a room-crossing action) and, per the manual-eyeball note, surfacing
UI-lifecycle oddities a VQA question set didn't anticipate — genuinely different defect classes than
playtest #7's, not a subset or superset of it.

## The fix in flight

**journey_eval v2 (#1523)** adds the missing adversarial phase: click every painted-prop candidate
region (from the manifest's footprint+occlusion sets, plus a coarse grid sweep of high-texture
regions) and flag whenever the engine ACCEPTS the move onto a cell whose paint reads as a solid
object — the mechanism that would have caught all 3 of playtest #7's missing-footprint defects (1-3
above) automatically, pre-ship, instead of relying on the owner's eyes. Recall target: 4/4 (or a
justified partial) against this same playtest-#7 punch list, re-run once v2 lands.

## Standing implication for anyone shipping a room

See `docs/OPERATIONS.md` "Journey-eval + the coherence gate — standing instruments": until v2 lands,
a clean journey-eval v1 run is evidence the LEGAL path looks right — it is not evidence the room has
no missing-footprint defects. Run `qa/check_grid_paint_coherence.py` (the absolute grid↔paint gate)
and, ideally, a human pass over the manifest's prop list against the painted plate before treating a
room as free of this defect class.
