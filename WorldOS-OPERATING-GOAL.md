# WorldOS — OPERATING GOAL (v1 — re-authored from first principles 2026-05-31)

<!-- ════════════════════════════════════════════════════════════════════════════
     ★ STATE OF TRUTH — READ THIS FIRST, ACT ON THIS ONLY (update every session)
     Post-compaction agents: this 6-line block is ground truth. Do NOT reconstruct
     state from scattered docs or old plans; trust this, verify the sha, then act.
     ──────────────────────────────────────────────────────────────────────────
     AS OF:        2026-06-01T16:00:00+07:00 #505 RRI bridge merged + current-SHA handoff gate passed
     MAIN BASELINE:
                   fd9dba5 (PRs #475, #494, #495, #496, #498, #499, #500, #501, #504,
                   and #505 merged; verified `/Users/lume/ClawDnD-val` was fast-forwarded after #505).
                   Re-verify current `origin/main` before acting.
     CANONICAL:    /Users/lume/ClawDnD-val is now the synced local app/private-art checkout and
                   the default place to build/run/test the Mac app. Keep GUI/runtime tests on this
                   local disk so macOS does not prompt on Lexar-hosted assets.
     WORKTREES:    For tracked edits, prefer same-disk local worktrees when GUI/app tests need assets.
                   Use /Volumes/LEXAR/Codex for evidence/snapshots and Lexar worktrees only for
                   non-GUI/doc/backend slices that do not launch the app against private art.
     SUPPORT VM:   32GB owner-provided support VM (`support-vm-1`). Connection/auth details live
                   in local operator-only evidence/runbooks, not tracked repo docs. Use it for
                   heavy backend/persona sweeps only after Codex/config/credentials are intentionally
                   installed. Mac-built `.app` smoke/play proof stays on this Mac or macOS CI.
                   Current local preflight note: `ssh -G support-vm-1` resolves only to the alias;
                   `ssh -o BatchMode=yes support-vm-1 ...` could not resolve the hostname in this
                   Codex Desktop session. A read-only operator-endpoint scout reached `evaos-support`
                   (~32 GB RAM, 16 CPUs) with WorldOS at `/root/worldos-qa/WorldOS`, but that checkout
                   was stale (`4524b3e`) and now behind `fd9dba5`; Codex auth/config was not
                   proven. Restore/verify operator routing, fast-forward the VM repo, verify Codex auth,
                   and define artifact return before running #466 there.
     LAST MEASURED GATE BUILD:
                   f5500ac produced qa/RRI.json = 2.7/10, but this is PARTIAL /
                   HARNESS-CONTAMINATED evidence: only newbie wrote score.json; the other
                   personas failed around port/backend harness setup; behavioral/UI/palette/image
                   evidence was not a valid five-persona release verdict.
     LAST BUILT-APP PLAY PROOF:
                   Last merged-main handoff proof is `fd9dba5`
                   (`/Volumes/LEXAR/Codex/worldos-agent-grade-app-testability/handoff-20260601T085319Z-fd9dba5/`):
                   `qa/app_handoff_gate.py` scored `handoff_score=100` with web-scripted smoke
                   5 moves, built `dist/WorldOS.app` scripted smoke 5 moves, and built
                   `dist/WorldOS.app` Codex-provider playtest 1 move. Private BG art was present,
                   visible narration and five enabled actions were present, `/move` resolved, all
                   three manifests had zero evidence gaps, and the Codex provider trace reported
                   `trace_exists=true`, `line_count=177`, and
                   `failed_or_error_count=0`.
                   The prior `4a0efe1` 100/100 handoff remains preserved but is superseded as the
                   latest merged-main app proof by this `fd9dba5` post-#505 run.
                   Prior trace-clean real-provider built-app proof on merged main is `f7ab6d7`
                   (`codex-main-f7ab6d7-proof-20260601T010058Z`); it remains useful diagnostic
                   #479 evidence but is superseded as the latest merged-main app proof by the
                   `fd9dba5` handoff gate.
     LAST VALID RELEASE GATE:
                   none after the RRI contract hardening. A release verdict requires expected
                   persona count, disk-backed palette/image/behavioral evidence, and built .app play.
     NEXT ACTION:  #479 is closed; #504 gives a fast GUI velocity gate; #505 lets RRI consume
                   Mac handoff proof through `--handoff-json`.
                   Do not claim release. Run #466 for a trustworthy clean RRI failure list/result:
                   use the `fd9dba5` handoff JSON for Mac/local built `.app` proof while the 32GB support VM
                   runs heavy backend/persona sweeps after explicit VM routing/auth/config
                   preflight. If the VM route is still unavailable, record that as the blocker and
                   file/fix repo-side RRI harness gaps only if found. Continue #485/#486 for
                   evidence export and gate split follow-through; #481/#482/#483/#484 are closed.
                   Keep sprint work UX-first (#467): first-turn playability, clickability/chrome,
                   launcher clarity, live-response feel, and CRPG depth before more hardening/proxy/security work.
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

## 9. CURRENT STATUS (2026-06-01T16:00:00+07:00 — #505 RRI bridge merged + current-SHA handoff gate passed)

- Repo truth stabilization merged in PR #465, UX-first doc sync merged in PR #468, first-minute
  click/title chrome proof merged in PR #470, local/Lexar/support-VM routing merged in PR #471,
  native provider selection merged in PR #472, and takeover docs synced in PR #473.
  The takeover foundation then merged: PR #475 added the Codex-DM built-app provider path,
  `/app-status`, evidence export baseline, and docs; PR #494 added the dev-gated scripted provider;
  PR #495 added stable OpenWorlds accessibility / `data-worldos-testid` hooks; PR #496 added app
  playtest failure buckets plus RRI gate-split metadata. PR #498 synced takeover docs after those
  merges. PR #499 then recorded the current-main built-app proof, PR #500 fixed the Codex-DM
  provider trace cancellations, PR #501 recorded that proof in the runbooks/scorecard, and PR #504
  added the hybrid 100/100 app handoff gate. PR #505 then hardened the RRI bridge so Mac handoff
  evidence can be supplied with `--handoff-json` while support-VM persona artifacts supply the heavy
  sweep. The local app/private-art checkout `/Users/lume/ClawDnD-val` was fast-forwarded to
  `fd9dba5 == origin/main` after #505.
- The stale local pre-sync artifacts were preserved before the fast-forward at
  `/Volumes/LEXAR/Codex/worldos-local-checkout-snapshot-20260531T223923` and in `stash@{0}`
  (`pre-sync local takeover docs 2026-05-31`). Treat those as evidence, not current release truth.
- The `f5500ac` RRI (`2.7/10`) is preserved as partial evidence only. It proves the gate/harness was
  not trustworthy enough for release scoring: one persona completed, others lacked `score.json`, and
  image/palette/behavioral/UI audit sources were either missing or harness-contaminated.
- Built-app launch smoke on `cad2e00` rendered OpenWorlds with private art, but the first Resume/Play
  click still forced Claude and failed on Claude auth. PR #472 fixed that web/native selection bug.
  PR #475 then added a separate Codex DM wrapper and points the native Codex adapter at it,
  while keeping the older Codex actor wrapper as a constrained player/move-facade helper.
- Early Codex-DM local built-app evidence is preserved: private BG art loaded,
  Alfira seated as `player`, visible DM narration, enabled actions, a real player move appended to
  `player_moves.jsonl`, and a post-move DM response with `can_act:true` in `/session-surface`.
  Evidence is in `/Volumes/LEXAR/Codex/worldos-built-app-proof/`:
  `session-surface-racefix-after-dm-response-20260601T012410.json`,
  `worldos-racefix-first-turn-20260601T012110.png`, and
  `worldos-racefix-dm-response-dismissed-permission-20260601T012516.png`.
- A Photos/Music permission prompt seen during this proof was traced through unified logs to
  macOS TCC attribution contamination: `responsible=dev.clawdnd.app`, but the actual accessor was
  `/usr/bin/find` launched by the test/diagnostic environment. Treat that screenshot prompt as harness
  contamination unless a clean run shows `WorldOSApp`/WebKit itself accessing a protected library path.
- The next gate evidence step is issue #466: a clean non-partial five-persona RRI from `fd9dba5` or newer.
  Heavy backend/persona sweeps belong on the owner-provided 32GB support VM (`support-vm-1`) once auth/config
  are intentionally installed there; connection details are kept outside tracked docs. In this Codex Desktop
  session the local SSH alias for `support-vm-1` did not resolve; a read-only operator-endpoint scout reached
  the VM and confirmed `evaos-support` has ~32 GB RAM, 16 CPUs, `git`, `python3`, `uv`, Node/npm, Codex CLI,
  Playwright, and private art, but its WorldOS checkout is `4524b3e` and now behind `fd9dba5`; Codex
  auth/config was not proven. Restore/verify VM routing, fast-forward the VM checkout, verify auth, and define
  artifact return before the heavy sweep. Mac-only built-app launch/play proof stays on this Mac or macOS CI.
- Built-app diagnostic evidence exists, but release truth is still absent. The PR #475 pre-merge app-code
  proof `8bd833f` (`codex-app-headproof-20260601T043909`) was trace-clean. The post-merge main proof
  `32ca561` (`post475-main-app-proof-20260601T051230`) was playable with private art, Alfira, five enabled
  actions, and a resolved `/move`, but provider trace noise persisted. The current-main `19c3fd0` proof
  (`codex-current-main-proof-20260531T234242Z`) repeated the product pass on the actual built app:
  private art root present, Codex provider, live campaign/run, writable `/move`, Alfira active, five enabled
  actions, visible narration, one accepted player move, chat roles `dm, player, dm`, and
  `/session-surface` still live/actionable. Its provider trace still recorded 3 failed/cancelled tool calls
  (`log_event`, `log_event`, `persist_beat`). The follow-up #479 branch proof `b081092`
  (`codex-479-traceclean-nodup-proof-20260601T003002Z`) reran the built `WorldOS.app` with private art,
  accepted and resolved a real `/move`, kept `/session-surface` live/actionable, exported
  `app-evidence/manifest.json` with no gaps, and produced a provider trace summary with zero parse errors
  and zero failed/error tool calls. Native accessibility review also showed the chronicle rendered the opening
  and follow-up once each after suppressing engine-logged `/chat` duplicates. PR #500 merged that fix, and the
  merged-main proof `f7ab6d7` (`codex-main-f7ab6d7-proof-20260601T010058Z`) repeated the built-app run:
  private BG art present, Codex provider, Alfira active, five enabled actions, writable `/move`, one accepted
  player move, chat roles `dm, player, dm`, `/session-surface` still live/actionable, native after-move
  screenshot archived, `app-evidence/manifest.json` with no gaps, and `provider-errors.after-move.json`
  reporting zero parse errors plus zero failed/error tool calls. This is sufficient to close #479 as a
  merged-main diagnostic; it is still not an RRI release verdict.
- The post-#505 current-main handoff gate `handoff-20260601T085319Z-fd9dba5` then reproved the fast GUI
  velocity loop on the current `main`: web-scripted smoke 5 moves, built-app scripted smoke 5 moves,
  built-app Codex playtest 1 move, private art present, active player, five enabled actions, zero evidence
  gaps across all three manifests, and Codex trace `failed_or_error_count=0` with `line_count=177`.
  `qa.release_readiness.validate_handoff_json(..., "fd9dba5")` returned `valid=True` and `gaps=0`.
  This supersedes the `4a0efe1` handoff as current app-wiring proof, but it remains diagnostic and cannot
  replace the full five-persona RRI.
- The agent-grade testability layer now has real code merged: `GET /app-status` exposes the live run,
  campaign, provider, private-art presence, move sink, actor, enabled actions, readiness, and failure buckets
  without mutating state; the scripted provider can prove wiring behind a dev/test gate; and stable a11y/DOM
  hooks make the UI more driveable. A current-session `:8899` probe briefly showed `080497e`, scripted
  provider, private art root at `/Users/lume/ClawDnD-val`, `can_act:true`, five enabled actions,
  `ready_for_smoke:true`, and no reported console/network failures; a later read found the port already
  down. Browser-based checks should use the live port discovered from `run.json` or `/app-status`, and if
  a browser session cannot reach local URLs, fall back to `/app-status`, `/session-surface`, app screenshots,
  and exported evidence. Treat fixed ad-hoc ports as transient harness/observability evidence only, never
  built `.app` proof.
- Product direction is now UX-first (#467). Do not turn the next sprint into more gate hardening, proxy adapters,
  transport/security work, UGC/legal, or renderer branches unless #466 proves they block the player-facing
  session. The game must feel launchable, clickable, responsive, and deep before it needs more machinery.
- Highest-confidence UX risks to verify/fix next: broader click hit areas (#309) after #470's
  shared-chrome proof; built-app title/chrome truth (#306); launcher clarity/stale campaigns (#358);
  per-beat latency/live response (#393); portrait/gallery blockers (#379); and CRPG depth on
  Heroes/Battle/Inventory (#308/#318/#310, with #462/#463 folded into Battle readability as presentation containment).

---

## 10. UX-FIRST SPRINT ORDER (after takeover stabilization)

Use the gate as evidence, not as the roadmap. The next sprint should optimize the felt session:

1. **Stretch first-turn proof into a short built-app playtest.** PARTIAL. PR #475, #500, and follow-up proofs show
   a fresh player can launch,
   choose/start/resume, reach the Table, submit multiple `/move`s, and see narration resolve without
   critical console/runtime errors. Current main now has trace-clean real-provider evidence for #479.
   Evidence must be built-app
   screenshots plus `/app-status`,
   `/session-surface`, move/chat/provider artifacts, not a proxy preview.
2. **Fix the "this is not clickable" feeling.** Close #309 only when clicking any visible tab/button
   background works with mouse and keyboard. Pair with visual truth for #306 so the title/day/chrome no
   longer look broken at common widths.
3. **Make the launcher feel like a real game shelf.** Remove stale/scratch campaign noise (#358), make
   bridge/no-bridge state honest, and ensure each chronicle looks distinct enough to choose.
4. **Make slow turns feel alive.** Verify #393/#394-style streaming in a real built-app run; if text does
   not appear within the first 15-30 seconds of a turn, prioritize streaming/proof-of-life UX over more
   backend hardening.
5. **Add CRPG depth where players look for it.** Heroes spellbook/manage-spells (#308), Battle readability
   (#318, including #462/#463 token containment/alignment as presentation truth), Inventory/paper-doll
   feel (#310), and portrait/race gallery continuity (#379) beat proxy/security work until the session
   feels like a game.

---

*The mechanics are the floor. The launchable, playable, felt prestige session in the built app is the
product. Build toward the North-Star ceiling; use THIS loop to keep the floor under the user's feet
always real.*
