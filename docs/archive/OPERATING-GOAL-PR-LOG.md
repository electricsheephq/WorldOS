# WorldOS — OPERATING-GOAL archived PR-log + sprint order (§9/§10)

> **ARCHIVED 2026-06-02** during the canonical-doc consolidation. This is the rambling §9 "CURRENT
> STATUS" PR narrative and §10 "UX-FIRST SPRINT ORDER" that previously lived at the bottom of
> `WorldOS-OPERATING-GOAL.md`. It was moved out so the operating goal stays a short GOAL + RRI gate +
> a single STATE block.
>
> **Historical context only — does NOT override current truth.** For the live release state, read the
> STATE block in `WorldOS-OPERATING-GOAL.md`. For the score ledger read `qa/SCORECARD.md`. SHAs below
> were current as of 2026-06-01; many code/QA commits have since landed on `origin/main` above the
> `9545383` proof (verified 2026-06-02: the post-`9545383` tips are NOT all docs-only — see the
> consolidation note in the PR description).

---

## 9. CURRENT STATUS (2026-06-01T17:49:00+07:00 — latest same-SHA app proof was 9545383)

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
  sweep. PR #506 then synced these docs to the `fd9dba5` proof without changing product code. PR #508 added the
  support-VM preflight artifact gate. The local app/private-art checkout `/Users/lume/ClawDnD-val` was
  fast-forwarded to `9545383`, and the Mac app handoff was rerun on that exact SHA.
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
- The next gate evidence step is issue #466: a clean non-partial five-persona RRI from one explicit SHA.
  The easiest current path is a support-VM sweep pinned to a release-candidate SHA, gated first by
  `qa/support_vm_preflight.py`, and paired with the same-SHA handoff JSON. If the
  sweep runs on a newer `origin/main` tip, rerun the Mac handoff on that same SHA first.
  Heavy backend/persona sweeps belong on the owner-provided 32GB support VM (`support-vm-1`) once auth/config
  are intentionally installed there; connection details are kept outside tracked docs. In an earlier Codex Desktop
  session a read-only operator-endpoint scout reached the VM and confirmed `evaos-support` has ~32 GB RAM,
  16 CPUs, `git`, `python3`, `uv`, Node/npm, Codex CLI, Playwright, and private art, but its WorldOS checkout
  was `4524b3e` and behind the `9545383` proof baseline; GitHub origin query/sync failed in batch mode; Codex
  auth/config was not proven. Get operator approval for VM repo sync/auth setup, verify `origin/main` is
  queryable from the VM, and define artifact return before the heavy sweep. Mac-only built-app launch/play
  proof stays on this Mac or macOS CI.
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
- The post-#505 product-code handoff gate `handoff-20260601T085319Z-fd9dba5` then reproved the fast GUI
  velocity loop: web-scripted smoke 5 moves, built-app scripted smoke 5 moves,
  built-app Codex playtest 1 move, private art present, active player, five enabled actions, zero evidence
  gaps across all three manifests, and Codex trace `failed_or_error_count=0` with `line_count=177`.
  `qa.release_readiness.validate_handoff_json(..., "fd9dba5")` returned `valid=True` and `gaps=0`.
- The post-#508 handoff gate `handoff-20260601T100304Z-9545383` then reproved the same fast GUI
  velocity loop on product build `9545383`: web-scripted smoke 5 moves, built-app scripted smoke 5 moves,
  built-app Codex playtest 1 move, private art present, active player, five enabled actions, zero evidence
  gaps across all three manifests, and Codex trace `failed_or_error_count=0` with `line_count=80`.
  `qa.release_readiness.validate_handoff_json(..., "9545383")` returned `valid=True` and `gaps=0`.
  This superseded the `fd9dba5` handoff as app-wiring proof. It remained diagnostic and cannot
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
