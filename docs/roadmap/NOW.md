# NOW — the you-are-here surface

> Update this file at every session close and every charter transition. It is the FIRST thing
> a bootstrapping agent reads after OPERATIONS.md. Keep it under a screen. History belongs in
> git, not here.

_Last updated: 2026-09-02 (UTC) — **REBOOT after 5 weeks dormant + a machine migration: MEASUREMENTS PENDING.**
Charter #1702 (`active-sprint`) is the plan of record. Done so far: renderer restored LOCALLY (Unity 6000.5.6f1 on
the Paris Mac; Stdio MCP bridge; canonical scene decontaminated), #1690 split (#1703 data half MERGED, C# gate half
in flight), engine static gates green (fast_gate 257 / walk_static). NOT yet re-measured: the §9 G1-G4 table (Track A
step 6) — until it lands, every row below is 2026-07-21 state. The GEX44 box and the LEXAR drive are GONE; any path
under `/Volumes/LEXAR` or `/Users/lume` in these docs is historical._

> Depth: `docs/roadmap/PROCEDURAL-SCORECARD.md` (generator-chain narrative) ·
> `docs/ROOM-PIPELINE-RUNBOOK.md` (11-step room pipeline; §11 walk ship gate) ·
> `docs/KIMI-ONBOARDING.md` (Kimi-side routing + the full Task #76 packet).

## Active sprint

- **ACTIVE charter: #1386** (label `active-sprint`) — Act II close-out, "Rendered Felt".
- **Jul-16 generator chain MERGED at tip `fd23e972`** — 5-room spine (#1604–#1609,
  incl. sha-pinned certifications #1607 + ledger walk surface #1608); generate_town /
  dress_focal v2 generator (#1610/#1611/#1621 — 3 non-collinear fire beacons per room);
  instruments #1613–#1616 (walk-gate tri-state, T-pose roster fix, wing hardening).
  Paint runs under an `err_cells ≤ 0.35` hard gate with similarity re-registration.
- **Task #76 next cycle IN FLIGHT** — regen → box render ×3 → paint under the err_cells
  gate → hot-load walk gates → blind-adjudicated panels (~140 Scenario CU; refilled
  2026-07-20). Stage 1 (beacon regen) = PR #1625; #1619 render_recipe = PR #1626;
  doc hygiene = PR #1624. Box claim via #1386 comments; packet in docs/KIMI-ONBOARDING.md §4.
- **Companions:** #1620 experience gates (open).
- **★★ GOVERNING MILESTONE (owner, 2026-07-22): DEMO COMPLETION — PRODUCT-ROADMAP §9.** The owner
  plays "The Crypt Below" end-to-end with zero user-truth defects, proven by the four §9 gates.
  Demo-critical path in §9.1; the harness system (player_cert + executable feature registry +
  known-hole SLA + rebuild-not-patch) in §9.2. The A-series evals below are its instruments.
- **A-SERIES SHIPPED (2026-07-22):** the Adventure Loop — PRODUCT-ROADMAP §4d. Both eval
  modalities live (#1637/#1638 merged); first weakest-link verdicts: #1645 (combat lifecycle),
  #1639 (cast presence), #1647 (user-truth gates epic). The weakest-link verdict routes every
  subsequent sprint (first verdict: behavioral → #1645).

## Live lanes

- **Walk-GREEN rooms:** crypt / tavern / throne_hall live + shop / tavern_snug certified
  (sha-pinned certs in `qa/certifications/`).
- **dwing wing CLOSED:** 0 adopted / 3 honest negatives — instruments adjudicate eyeballs.
- **Release truth:** `qa/RRI.json` = 2.7 partial/harness-contaminated — **NO valid release
  verdict exists.** `dist/` is EMPTY (no built app).
- **Open PRs (notable):** #1624/#1625/#1626 (this wave's drafts) · #1617 (Unity persistence docs) · #1498 (outdoor LoRA — CU now refilled) · #1298 (owner-held) · #1012/#1102/#573 (drafts) · #1622 (dependabot).
- **Next charters queued:** S2 (#1309) → S3 (#1310), both open.

## Blockers

None. (Blocker Law: fresh-probe row in docs/ACTIVE-GOAL.md or it isn't a blocker.)

## Known frictions (not blockers)

- Auto-merge fires normally (the #1389 "hang" was a stale ruler pin — resolved by #1431/#1434);
  `--admin` is emergency-only (declare why in a PR comment + file a follow-up).
- qa-release-gate-tests was RED repo-wide (stale ruler pin) — FIXED by #1431; if it re-reds,
  check SCORING_CONFIG_FILES drift first.
- GitHub LFS push for the box Unity repo blocked on a paid data pack (~$5/mo owner decision);
  local commits + LEXAR tarball are the save story meanwhile.
