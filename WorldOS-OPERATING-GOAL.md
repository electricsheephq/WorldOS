# WorldOS — OPERATING GOAL (v1 — re-authored from first principles 2026-05-31; consolidated 2026-06-02)

<!-- ════════════════════════════════════════════════════════════════════════════
     ★ STATE OF TRUTH — READ THIS FIRST, ACT ON THIS ONLY (update every session)
     Post-compaction agents: this block is ground truth. Do NOT reconstruct state
     from scattered docs or old plans; trust this, verify the SHA, then act. Keep
     it to ~10 lines — the PR-narrative lives in docs/archive/OPERATING-GOAL-PR-LOG.md.
     ──────────────────────────────────────────────────────────────────────────
     CURRENT SHA:   <TODO: fill from the measured A/B run — `git -C /Users/lume/ClawDnD-val rev-parse --short origin/main`>
                    Note (verified 2026-06-02): commits ABOVE the old `9545383` app-proof are NOT all
                    docs-only — they include product/QA code (#528 viewer screen-create, the RRI scorer
                    qa/release_readiness.py, ui_playtest_app.sh). So re-prove the app on the CURRENT SHA;
                    do not carry `9545383` forward as if main were docs-only on top of it.
     LAST REAL SCORE: <TODO: fill from the A/B + ledger — the last NON-partial, non-contaminated RRI row,
                    with its build SHA. As of consolidation there is NONE; see qa/SCORECARD.md "RRI
                    reconciliation" for why the 1.8 / ~6.0 / 2.7 numbers are all partial/unmeasured.>
     CANONICAL:     /Users/lume/ClawDnD-val is the synced local app/private-art checkout — build/run/test
                    the Mac app here so macOS does not prompt on Lexar-hosted assets.
     NEXT ACTION:   Do not claim release. Run #466 for a trustworthy clean five-persona RRI on ONE explicit
                    SHA. Pair a same-SHA Mac handoff (`qa/app_handoff_gate.py` → `--handoff-json`) with
                    support-VM persona artifacts, gated by `qa/support_vm_preflight.py` (operator must
                    approve VM repo-sync/auth first; the scouted VM checkout was stale + auth unproven).
                    Keep sprint work UX-first (#467) — see docs/archive/OPERATING-GOAL-PR-LOG.md §10.
     DISCIPLINE:    ≥2 clean reads before any fix (channel fabricates under host load); ONE heavy claude -p
                    stream; never probe-kill; test the BUILT .app, never a proxy; honest scores; never "100% confidence".
     ════════════════════════════════════════════════════════════════════════════ -->

> Read order on resume: the STATE-OF-TRUTH block above → this file → `WorldOS-RUNBOOK.md` (the merged
> HOW-TO + GUI loop) → `qa/SCORECARD.md` (the human score ledger). (NORTH-STAR is the long-game ceiling,
> not needed to act.) Historical PR-narrative + sprint order: `docs/archive/OPERATING-GOAL-PR-LOG.md`.

## Agent Navigation / Current TOC

Use this map before opening older audit docs. It is meant for a fresh agent that needs to know
which file answers which question.

| Need | Read / run |
|---|---|
| Current release truth, last proof, next action | This file, especially the state block above |
| Fast GUI/native app loop + repo architecture, invariants, dev loop | `WorldOS-RUNBOOK.md` (GUI runbook merged in) and `docs/ARCHITECTURE.md` |
| QA command index | `qa/QA_TOOLS.md` |
| App-status, handoff, evidence contract | `docs/AGENT_GRADE_APP_TESTABILITY.md` |
| Score ledger (human narrative) | `qa/SCORECARD.md` (points at the machine-readable `qa/scores.db` once it lands) |
| Historical GUI punch-list | `docs/archive/GUI_WORKBOOK.md` |
| Historical PR-narrative + UX sprint order | `docs/archive/OPERATING-GOAL-PR-LOG.md` |
| Blind browser persona harness | `qa/UI_PLAYTEST.md` |
| Old page-by-page audits and native roadmaps | `docs/OPENWORLDS_UI_AUDIT.md`, `docs/OPENWORLDS_NATIVE_APP_ROADMAP.md`, `docs/ui-audit/` |

Incoming-agent rule: if the goal is to catch broken wiring, stale browser tabs, dead controls,
missing art, missing actor/actions, failed moves, console/network errors, or evidence gaps before
spending budget on long playtests, start with `qa/app_handoff_gate.py` from the GUI runbook. It is
the handoff/velocity gate only. Full non-partial RRI remains the release verdict.

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
> UI-audit log, palette-live source, optional `--handoff-json` Mac app proof, per-run Part B pass status,
> and per-run build SHA. Smoke-sized
> persona sets, mixed-build evidence, failed app-persona loops, missing persona scores, or missing
> artifact denominators can never silently produce a release-ready result.

---

## 5. THE ITERATE LOOP (while the gate fails)

`git fetch` → create/update a **same-disk local worktree off origin/main** for tracked edits
when GUI/app tests need assets (Lexar is evidence/scratch, not the default runtime tree) →
`rm -rf dist/WorldOS.app` in the local app checkout → `qa/ui_playtest_app.sh` × 5 personas against the built `.app` → **score** (5-persona
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
  (`clawdnd-*` / `CLAWDND_*` / `dev.clawdnd.app`). Build/run/test the Mac app from
  `/Users/lume/ClawDnD-val` so private art stays on the local disk and macOS does not prompt on Lexar
  files. Use **same-disk local worktrees** for GUI-affecting tracked edits, Lexar for evidence/snapshots,
  and the 32GB support VM / GitHub CI for heavy tests. `_private/` never committed.

---

## 9. CURRENT STATUS + UX-FIRST SPRINT ORDER

> Moved out of this file during the 2026-06-02 consolidation to keep the operating goal short.
> The live state is the STATE-OF-TRUTH block at the top of this file; the running score ledger is
> `qa/SCORECARD.md`. The historical PR-by-PR narrative (former §9) and the UX-first sprint order
> (former §10) now live in **`docs/archive/OPERATING-GOAL-PR-LOG.md`** for provenance.


*The mechanics are the floor. The launchable, playable, felt prestige session in the built app is the
product. Build toward the North-Star ceiling; use THIS loop to keep the floor under the user's feet
always real.*
