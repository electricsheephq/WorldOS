# Decision: the adopted plate-generation recipe (PLATE RECIPE DECISION)

**Date:** 2026-07-10 (plate sprint, Phase 3 codification) · **Status:** DECIDED — supersedes the
implicit model_z-image img2img default. Machine-readable params recorded at
`extensions/renderers/shared/room_recipes.json:registered_pipeline_2026_07_10` (params only — no
room's `canonical_plate`/active pointer changes here). Cross-linked from
`docs/research/2026-07-10-stage-tech-research.md` (REJECTED-APPROACHES REGISTER) and
`docs/RUNBOOK-INDEX.md` (room/backdrop gen row).

## Adopted pipeline for registered plates

1. **flux.1-dev + depth-ControlNet base** from the room greybox (registration by construction).
2. **Style pass:** Gemini instruction-edit (`model_google-gemini-3-1-flash`) with STRUCTURE-LOCK +
   explicit DIMETRIC-LOCK prompt clauses.
3. **Registration gate:** edge-recall >=0.95 for hard-edge/masonry rooms; for organic rooms edge-recall
   is ADVISORY (content-blind and class-dependent, issue #1491) — use the greybox-edge overlay as
   primary evidence.
4. **5-scorer blind panel:** disguised in-band real-art control (validity band 6.8-9.2; out-of-band =>
   advisory, re-run once) + house anchor; best-of-N (N>=3) generations per iteration (measured
   run-to-run variance).

## THE REFERENCE-IMAGES LAW

Gemini `referenceImages` hijack CONTENT toward the reference, not just style. A reference is safe ONLY
if its composition already matches the room greybox (e.g., an anchor minted FROM that same greybox).
For rooms with no greybox-aligned anchor: use NO `referenceImages` — text style description + scene-content
grounding instead (camp evidence: no-ref registration 0.9439 vs same-room-ref 0.81-0.84, PR #1492).

The crypt's reference-based win was the degenerate case: its grid was authored FROM the incumbent
plate, so hijack-toward-reference == registration.

## Outdoor class status

Capped ~6.0 by available style sources. Rejected, with unpark conditions:

| Approach | Why rejected | Unpark condition |
|---|---|---|
| z-image layered anchors for outdoor | 6.0 quality ceiling (PR #1490) | Better outdoor layered recipes or references |
| Same-room `referenceImages` as style anchor | Content hijack (PR #1492) | None — see THE REFERENCE-IMAGES LAW above |
| Interior-trained ARM C LoRA (`model_G379oza2qhm6MkqDrtTvvmmw`) as outdoor anchor-minter | Imposes crypt interiors on outdoor greyboxes, recall 0.45 (PR #1495) | Retrain with an outdoor-heavy set (est <=$12, OWNER SPEND GATE). The LoRA IS validated for char-free interior/architectural one-pass style at low control strength |
| WOSRelight on shared greybox sidecars | Vertical banding on warm high-contrast plates (PR #1488, 5/5 last) | Per-plate sidecars. Relight is unnecessary when plates arrive warm+firelit from the style pass |
| ARM A two-stage z-image style pass over flux base | Style-vs-registration structurally capped ~5.5 (PR #1487 tooling retained) | None known |

## Crypt adoption honesty note

The adopted plate (`library/rooms/room_crypt_armb_iter3_styled_20260710`) is the incumbent's
composition re-registered (NCC 0.93-0.96, lighting-only diffs) — the win is REGISTRATION (0.9903),
which unlocks coherent occluders/collision/set-interaction; visual novelty is low and disclosed.

## Open questions

- **#1491** — the registration metric (edge-recall vs greybox) is content-blind and class-dependent:
  it rewards busy imagery over faithful organic reinterpretation, so it's ADVISORY-only for organic/
  outdoor rooms above. A per-room-class registration strategy (`structure_class` field + a content-aware
  structural metric such as NCC/SSIM to the registered base) is the proposed fix, not yet implemented.
- **#1493** — `qa/scores.db` (committed binary sqlite) and `qa/scores_ledger.md` (rendered artifact)
  are not merge-safe across parallel PLATE SPRINT PR lanes; this PR intentionally does NOT touch
  `qa/scores.db`. An append-only per-PR journal rendered post-merge on main is the leading fix option.
- **PR #1496** (probe-placement) recalibrated `SARCOPHAGUS_CELLS` in `qa/seed_gfx_combat.py` and noted
  that NO `qa/room_manifests/*.cells.json` manifest exists for hand-authored QA seeds, so the #1462
  plate-drift gate (wired default-on for canonical rooms by this PR, see `generate_room.py
  --drift-gate`) never runs against them. Fixture manifests for hand-authored seeds is an open
  follow-up alongside #1491/#1493.
