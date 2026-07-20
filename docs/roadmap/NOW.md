# NOW — the you-are-here surface

> Update this file at every session close and every charter transition. It is the FIRST thing
> a bootstrapping agent reads after OPERATIONS.md. Keep it under a screen. History belongs in
> git, not here.

_Last updated: 2026-07-20 (UTC) — Jul-16 generator chain merged; Task #76 next cycle pending._

> Depth: `docs/roadmap/PROCEDURAL-SCORECARD.md` (generator-chain narrative) ·
> `docs/ROOM-PIPELINE-RUNBOOK.md` (11-step room pipeline; §11 walk ship gate) ·
> `docs/KIMI-ONBOARDING.md` (Kimi-side routing + the full Task #76 packet).

## Active sprint

- **ACTIVE charter: #1386** (label `active-sprint`) — Act II close-out, "Rendered Felt".
- **Jul-16 generator chain MERGED at tip `fd23e972`** — 5-room spine (#1604–#1609);
  generate_town / dress_focal v2 generator (#1610/#1611/#1621 — 3 non-collinear fire beacons
  per room); instruments #1613–#1616 (walk-gate tri-state, ledger walk surface, sha-pinned
  certifications, T-pose roster fix). Paint runs under an `err_cells ≤ 0.35` hard gate with
  similarity re-registration.
- **Task #76 next-cycle packet PENDING** — regen the wing → box render ×3 → paint under the
  err_cells gate → hot-load walk gates → blind-adjudicated panels (~140 Scenario CU; budget
  refilled 2026-07-20). Box claim via #1386 comments; full packet in docs/KIMI-ONBOARDING.md §4.
- **Companions:** #1619 render_recipe (in flight, zero-CU — kills recipe-authoring bugs, land
  before the repaint) + #1620 experience gates.

## Live lanes

- **Walk-GREEN rooms:** crypt / tavern / throne_hall live + shop / tavern_snug certified
  (sha-pinned certs in `qa/certifications/`).
- **dwing wing CLOSED:** 0 adopted / 3 honest negatives — instruments adjudicate eyeballs.
- **Release truth:** `qa/RRI.json` = 2.7 partial/harness-contaminated — **NO valid release
  verdict exists.** `dist/` is EMPTY (no built app).
- **Open PRs:** #1617 (Unity persistence docs — its OPERATIONS.md sections are not in base yet).
- **Next charters queued:** S2 (#1309) → S3 (#1310), both open.

## Blockers

None. (Blocker Law: fresh-probe row in docs/ACTIVE-GOAL.md or it isn't a blocker.)

## Known frictions (not blockers)

- Auto-merge hangs repo-wide → `gh pr merge --admin --squash` after green+resolved (#1389).
- qa-release-gate-tests was RED repo-wide (stale ruler pin) — FIXED by #1431; if it re-reds,
  check SCORING_CONFIG_FILES drift first.
- GitHub LFS push for the box Unity repo blocked on a paid data pack (~$5/mo owner decision);
  local commits + LEXAR tarball are the save story meanwhile.
