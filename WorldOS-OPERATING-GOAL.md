# WorldOS — OPERATING GOAL (v1 — re-authored from first principles 2026-05-31)

<!-- ════════════════════════════════════════════════════════════════════════════
     ★ STATE OF TRUTH — READ THIS FIRST, ACT ON THIS ONLY (update every session)
     Post-compaction agents: this 6-line block is ground truth. Do NOT reconstruct
     state from scattered docs or old plans; trust this, verify the sha, then act.
     ──────────────────────────────────────────────────────────────────────────
     AS OF:        2026-05-31 takeover stabilization
     ORIGIN TIP:   82aeaf2 (verified by git fetch on 2026-05-31)
     CANONICAL:    /Users/lume/ClawDnD-val is the private-art/live-app checkout; observed at f5500ac
                   and behind origin/main by 3 commits before takeover. Do not fast-forward it
                   until the owner intentionally chooses to move the private-art checkout.
     WORKTREE:     tracked takeover edits happen from a Lexar worktree off refreshed origin/main.
     LAST MEASURED GATE BUILD:
                   f5500ac produced qa/RRI.json = 2.7/10, but this is PARTIAL /
                   HARNESS-CONTAMINATED evidence: only newbie wrote score.json; the other
                   personas failed around port/backend harness setup; behavioral/UI/palette/image
                   evidence was not a valid five-persona release verdict.
     LAST VALID RELEASE GATE:
                   none after the RRI contract hardening. A release verdict requires expected
                   persona count, disk-backed palette/image/behavioral evidence, and built .app play.
     NEXT ACTION:  Stabilize gate truth first → rerun a clean gate on the 32GB VM for backend/personas
                   plus Mac/macOS CI built-app verification → then fix the trustworthy failure list.
     DISCIPLINE:   ≥2 clean reads before any fix (channel fabricates under host load); ONE heavy claude -p stream;
                   never probe-kill; test the BUILT .app, never a proxy; honest scores; never "100% confidence".
     ════════════════════════════════════════════════════════════════════════════ -->

> Read order on resume: the STATE-OF-TRUTH block above → this file → `WorldOS-GUI-RUNBOOK.md` →
> `WorldOS-RUNBOOK.md` → `qa/SCORECARD.md`. (NORTH-STAR is the long-game ceiling, not needed to act.)

---

## 0. FIRST PRINCIPLES — what are we actually trying to do?

WorldOS is **a game** — a post-BG3 D&D 5e living world shipped as a native macOS app (`dist/WorldOS.app`).
A game has exactly **one real test: can a real person launch it and play a satisfying session without
hitting something that feels broken?** Engine depth, art, mechanics, scorecards are all **proxies**.
The product is the *launchable, playable, felt session*. **We optimize the product, never a proxy.**

> *Why this doc exists:* for ~8h on 2026-05-30 the loop reported GREEN on a fitness function that
> measured the WRONG surface — the harness booted its OWN playable viewer while the shipped `.app`
> launched read-only. A green score on a surface the user can't reach is a **measurement bug, not
> progress.** This doc makes that impossible to repeat.

---

## 1. THE OPERATING GOAL

**Drive `dist/WorldOS.app` to release-ready for a fresh player — and keep it there as the codebase moves.**

**Release-ready =** a user with **NO prior WorldOS knowledge** launches the freshly-rebuilt `.app`,
picks a playable canon NPC, and plays a complete **8-beat Baldur's Gate session** — the five phases each
**genuinely firing** — **without ever saying "this feels broken."**

---

## 2. WHAT "COMPLETION" LOOKS LIKE — the 8-beat session, made concrete

Done = all five phases actually happen on the built app — not narrated, not faked:

1. **PARLEY** — the player talks with NPCs/companions; choices land; dialogue is tracked.
2. **COMBAT** — a real **engine-resolved** fight: the DM calls `start_combat` + `spawn_monster`; attack
   rolls and rounds resolve **through the engine tools**, NOT prose and NOT a skill-check substitute.
   *(arc4 lesson: 11 turns with ZERO combat calls = NOT complete.)*
3. **TRAVEL** — the party moves to a new location (the scene/clock actually changes).
4. **REST** — camp **restores resources + advances the clock**.
5. **TRAVEL** — the party sets out again toward the next beat.

Verified from the engine tool-trace **and** the player-facing chronicle, **on the built `.app`.**

---

## 3. P0 — THE NON-NEGOTIABLE: test the BUILT `.app`, never a proxy

The fitness function is `qa/ui_playtest_app.sh` running the **native part-A+B §8.2 harness** against
`dist/WorldOS.app`:
- **Part A** = the native CGEvent-click gate (proves read-only→playable routing #356 on the real app window).
- **Part B** = the persona play loop on the byte-identical `play_party.sh` backend + the app's OpenWorlds viewer.

NEVER a self-booted playable preview, a dev port, or the stale `:8765`. **Unit tests + part-B-only runs
MISS native-surface criticals** — the #405 overlay-wedge (no escape; `dismiss()` unwired; green only
because a test stubbed `useEffect`) proved this. The built, played app is the only evidence that counts.

---

## 4. THE PASS GATE — Release Readiness Index (RRI 10/10, all 11 gates on the SAME fresh build)

Computed by `qa/release_readiness.py` (hard-gate floor — a missed gate caps the score, never hidden
by an average). **RRI 10/10 = every gate holds on one fresh build:**

1. **Native #356 gate** PASS (`ui_playtest_app.sh` part A on the built `.app`).
2. **Arc completes** — ≥1 persona finishes all 5 phases (§2): combat **through the engine**, travel
   moves the party, rest restores + advances the clock. `completed_intro_flow = true`.
3. **Cross-persona satisfaction ≥ 7/10** averaged (newbie/veteran/adversarial/narrative/optimizer).
   *(LATENCY lives here: if impatient personas quit on slow turns, this fails → context-leaning lever.)*
4. **No persona gives up** (`gave_up = false`).
5. **Zero critical runtime/console bugs** across all 5 personas — incl. no JS console/page errors,
   no wedge / no-escape / dead-end.
6. **Story-craft ≥ 4.3** (Tolkien lens, `score.sh` + `rubric_tolkien.md`).
7. **Mechanical ≥ 4.5** (Angry-DM lens, `score.sh` + `rubric_angry_dm.md`).
8. **Behavioral GREEN** (`qa/assert_behavioral.py`: clock advanced, ≥2 locations, combat fired, no role-bleed).
9. **`qa/ui_audit_health.sh --quick --axe --ui-gate` → 0 regressions** (a11y + per-screen render).
10. **Image-render rate ≥ 95%** (NEW — from `network.ndjson`; "no images" was THE owner-visible defect).
11. **Palette-live** (NEW — ≥6 enabled action buttons on a `can_act:true` surface; the "tools missing" defect).

> Gates 1, 10, 11 were added 2026-05-31 after the GUI reorientation — the prior gate could pass while the
> player saw no images and no clickable tools. RRI makes the *visible* product part of the gate.
> The release DECISION is RRI = 10/10 (all gates); RRI < 10 lists exactly which gates failed.
> The RRI output contract also records `required_release_personas`, `expected_personas`,
> `completed_personas`, `missing_personas`, `missing_release_personas`, `partial`,
> `harness_contaminated`, explicit `evidence_gaps`, image source/denominator, behavioral evidence path,
> UI-audit log, palette-live source, per-run Part B pass status, and per-run build SHA. Smoke-sized
> persona sets, mixed-build evidence, failed app-persona loops, missing persona scores, or missing
> artifact denominators can never silently produce a release-ready result.

---

## 5. THE ITERATE LOOP (while the gate fails)

`git fetch` → create/update a **Lexar worktree off origin/main** (never branch-op the private-art
checkout) → `rm -rf dist/WorldOS.app` in the intended app checkout → `qa/ui_playtest_app.sh` × 5 personas against the built `.app` → **score** (5-persona
satisfaction + 3 lenses + behavioral + axe) → **file GitHub issues** tied to `{build_sha, version_tag}`
with `file:line` + acceptance criteria → **delegate code to builder subagents** (worktree → PR →
CI-green incl. `viewer-tests` → squash-merge) → **rebuild → re-playtest.**
**An issue closes ONLY when the NEXT build's playtest no longer reproduces it.**

---

## 6. THE MAINTAIN LOOP (once the gate holds)

Baseline `{build_sha, version_tag, scores}`. Every PR touching `viewer/`, `macos/`, `skills/`, or
`servers/engine/` → rebuild + 5-persona playtest + a SCORECARD row + `ui_audit_health`. Any regression
(a critical bug, a sub-7 persona, an axe regression, a sub-threshold score) **reverts the goal to "fix"**
and outranks new work. Cut a version bump (v1.0.x) each time the gate holds on a fresh build.

---

## 7. MY ROLE — release-verifier orchestrator

I playtest the built app, file issues, **delegate product-code fixes to builder subagents**, merge
CI-green PRs, plan sprints toward version bumps, and **verify every fix on a subsequent build before
closing.** I orchestrate + verify; I do not hand-write product code by default. Owner = orienter + final
verifier; can revert the goal to "fix" anytime.

---

## 8. DISCIPLINE (load-bearing invariants)

- The **BUILT, PLAYED `.app` is ground truth.** The harness serves the app, never the reverse.
- **Never** claim "100% confidence" / "audit complete." A merged PR is a hypothesis; a non-reproducing
  NEXT build is the evidence. Close issues only on next-build non-reproduction. **Honest scores only.**
- Engine (`servers/engine`) = **SOLE writer** of campaign state. Don't touch wire contracts
  (`clawdnd-*` / `CLAWDND_*` / `dev.clawdnd.app`). Build/edit from **Lexar worktrees off origin/main**.
  Treat `/Users/lume/ClawDnD-val` as the canonical private-art/live-app checkout until intentionally
  moved. 16GB host → **GitHub CI / 32GB VM** for heavy tests, never local heavy workers. `_private/`
  never committed.

---

## 9. CURRENT STATUS (2026-05-31 — takeover context, NOT a release verdict)

- Repo truth is being stabilized from a Lexar worktree at `origin/main` (`82aeaf2`). The private-art
  checkout at `/Users/lume/ClawDnD-val` was observed at `f5500ac` and behind by 3 commits; keep it
  intact until the owner chooses to fast-forward it.
- The `f5500ac` RRI (`2.7/10`) is preserved as partial evidence only. It proves the gate/harness was
  not trustworthy enough for release scoring: one persona completed, others lacked `score.json`, and
  image/palette/behavioral/UI audit sources were either missing or harness-contaminated.
- The stabilization lane is: make docs and RRI output truthful → make the gate fail on missing personas
  and record exact evidence paths → fix high-confidence playability blockers → run the full backend/persona
  sweep on the 32GB VM and the Mac-only built-app smoke on this Mac or macOS CI.
- Known product blockers to verify after harness trust is restored: built app playability, narration
  stall/disabled-control behavior under real latency, image/private-art root routing, and legacy
  `/dashboard` references that can mislead agents away from `/openworlds/`.

---

*The mechanics are the floor. The launchable, playable, felt prestige session in the built app is the
product. Build toward the North-Star ceiling; use THIS loop to keep the floor under the user's feet
always real.*
