---
name: worldos-dev
description: Develop, test, and QA the WorldOS plugin (the living-world D&D 5e engine). Use when implementing an engine/content/QA change, running or scoring a playtest, running the blind AI-playtester GUI harness, delegating a build to a subagent, or resuming the project after a compaction. Encodes the exact dev loop (worktree → additive change → focused single-process pytest → PR → merge → prune), the story/mech QA loop (duo / combat-sprint runners + the 3 lenses + behavioral gate), the GUI QA loop (the #324 Playwright AI-playtester harness — Option B), the overnight-loop discipline (agent liveness, host-capacity cap, salvage, shared-checkout safety), and the load-bearing engine invariants. Read WorldOS-RUNBOOK.md first for full context.
---

# WorldOS Dev

You build and harden **WorldOS** — a post-BG3 living-world D&D 5e Claude Code plugin. North
star: **epic Baldur's-Gate-caliber STORY on a deterministic SRD 5.2 engine**; goal: a
universe-system that generates worlds. Source-available commercial product, BG-focused. **Read `WorldOS-RUNBOOK.md`
(repo root) for the full project/architecture/state.** This skill is the operational loop.

**Graphics:** the **Unity/current-renderer lane** + deterministic SceneGrid/visual-critic checks.
Godot GT2 is archived as reference/extension material under `extensions/renderers/godot/`.
**AI asset generation** (3D chars, rigging, painterly backdrops, sprites) → the `asset-gen` skill.

> **⚠ HEAVY QA SWEEPS (5-persona / RRI) → USE THE SUPPORT VM, NOT LOCAL.** The 16 GB Mac OOMs mid-sweep
> (proven 2026-06-02: cratered to 147 M free, personas 2–5 backends never minted, shipped a junk PARTIAL RRI 2.7).
> Documented lane: **`WorldOS-GUI-RUNBOOK.md` § "Support VM lane (heavy sweeps)"** + memory note
> `project_worldos_vm_sweep_lane.md`. The Mac runs the **macOS-only** native Part-A (#356 `.app` launcher +
> built-app screenshots); the **32 GB evaos-support VM** (`root@178.104.123.213`, repo `/root/worldos-qa/WorldOS`,
> harness `sweep_v2.sh`) runs the heavy **part-B** 5-persona sweep against the **real OpenWorlds browser GUI**
> (server.py + playwright/chromium — NOT a degraded proxy). RRI rolls up Mac `--handoff-json` + same-SHA VM dirs.
> **On resume after a compaction, re-read that runbook section FIRST** — do not rebuild the QA plan from memory
> or fight local RAM. For per-beat DM latency, see the `worldos-latency-forensics` skill.

## VM GATE SWEEP — exact procedure (heavy part-B; the thing you keep re-deriving)
**The full gate is ONE command on the 32 GB VM. Update THIS section when the harness changes** — it is
the source of truth so a cold post-compaction agent never re-learns it (it cost a real diagnosis 2026-06-03).

**The VM** (`evaos-support`): `root@178.104.123.213`, key `~/.openclaw/secrets/cloud-deploy-key` (key-only
root; wrong-user probes trip fail2ban — always `root`). Repo `/root/worldos-qa/WorldOS`; harness
`/root/worldos-qa/sweep_v2.sh`; results `/root/worldos-qa/results/`. 30 GB / 16 CPU. Has claude (authed) +
codex(≥0.120) + uv + node + playwright/chromium + art. **No swift** → the native Part-A `.app` gate stays Mac.

**⚠ `IS_SANDBOX=1` IS MANDATORY (the VM is root).** `claude -p --permission-mode bypassPermissions`
(→ `--dangerously-skip-permissions`) is REFUSED as root → empty `.result` → silent abort ("player produced
no intro — aborting"). `sweep_v2.sh` exports it itself; a STANDALONE `run_duo.sh`/`play.sh` on the VM needs
`IS_SANDBOX=1 bash qa/...`. (A direct `claude -p` WITHOUT the bypass flag works as root → proves auth is
fine; only bypass+root is guarded.) This — NOT say()-into-void — was the duo's beat-0 blocker (2026-06-03).

**Art lives at `content/worlds/_private/baldurs-gate/images`** (~2360 dirs / 3 GB, gitignored, present on the
VM) — NOT top-level `_private`. Don't false-alarm on `ls WorldOS/_private`.

```bash
KEY=~/.openclaw/secrets/cloud-deploy-key; VM=root@178.104.123.213
# 1) SCOUT (read-only): reachable? SHA? git-fetch works? claude authed? RAM?
ssh -i $KEY $VM 'cd /root/worldos-qa/WorldOS && git rev-parse --short HEAD && free -g|grep Mem && claude --version'
# 2) FF the VM to the SHA under test (origin/main)
ssh -i $KEY $VM 'cd /root/worldos-qa/WorldOS && git fetch origin -q && git checkout main -q && git pull --ff-only'
# 3) SMOKE first (confirm scoring works before the ~$30-60 batch): 2-beat duo, ~10 min
ssh -i $KEY $VM 'cd /root/worldos-qa/WorldOS && IS_SANDBOX=1 nohup bash qa/run_duo.sh smoke baldurs-gate qa/play_player_duo.txt 2 0.80 >/tmp/smoke.log 2>&1 &'
# 4) FULL part-B (canary newbie → 4 personas PARALLEL → duo G5 → behavioral → ui_audit → RRI), ~60-90 min:
ssh -i $KEY $VM 'nohup bash /root/worldos-qa/sweep_v2.sh >/root/worldos-qa/results/sweep.out 2>&1 &'
# 5) READ results
ssh -i $KEY $VM 'cd /root/worldos-qa/results && cat sweep2.log && for f in score-*.json; do echo "== $f =="; \
  python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print({k:d.get(k) for k in [\"persona_satisfaction\",\"gave_up\",\"bug_reports_critical\",\"completed_intro_flow\",\"satisfaction_source\"]})" "$f"; done'
```
**Maps to the gates:** `score-<persona>.json` ×5 → G2 (`bug_reports_critical`=0) + G3 (`persona_satisfaction`≥7
**self-reported** AND `gave_up`=false) + G1 (`completed_intro_flow`) · `duo-tolkien.json`/`duo-angrydm.json` →
G5 (story ≥4.3 / mech ≥4.5) · `behavioral.log` → G5 behavioral · `ui_audit.log` → G4 (axe 0).
**Honest-score guard:** a DERIVED sat (verify `satisfaction_source`) of 5–6 is INCONCLUSIVE, not "bad" (see
the GUI QA LOOP note) — only `self-reported` counts toward G3.

**Rollup → RRI (#466):** the Mac runs native Part-A on the BUILT `.app` (`qa/app_handoff_gate.py` →
handoff.json) at the **SAME SHA**; `qa/release_readiness.py --handoff-json <mac.json> <vm score dirs>`
combines them. Mixed-SHA / missing `run.json`/`score.json`/`network.ndjson` ⇒ `partial`/`harness_contaminated`
(never a clean release). Copy VM `results/` back under `/Users/m1/Codex/...` for the rollup + ledger.

### RRI evidence-path contract (don't false-mask the gate)
**`release_readiness.py` requires EVIDENCE-PATH flags, not just value flags** — a gate that has the
*value* but not a *path that EXISTS* is an `evidence_gap`, which RED-caps the gate and **understates the
RRI**. A VM part-B-only sweep that passes only value flags reads a FALSE-LOW number (real 2026-06-05: an
honest **~4.5/11** masked as **1.8/11**). Each gate needs BOTH halves (see `release_readiness.py` ~709–731):
- **behavioral:** `--behavioral GREEN` **AND** `--behavioral-path <file-that-exists>`.
- **ui_audit:** `--ui-audit PASS` **AND** `--ui-audit-log <file-that-exists>`.
- **palette_live:** `--palette-live true` **AND** `--palette-source <file-or-label>` (a label is fine;
  only a path-looking value is existence-checked).
- **native_gate:** needs `--handoff-json <mac-handoff>` (the Mac Part-A) — **structurally absent on a
  VM-only sweep**, so that gate is *expected* to be a gap until the Mac part-A rolls in at the same SHA.
  Don't read its absence as a regression; read it as "Part-A not yet joined."

**The other half of the same bug — behavioral read the WRONG path → false RED.** The sweep computed
behavioral from `$DCOMB=qa/transcripts/vm2-duo.combined.jsonl`, which doesn't exist (empty ⇒ defaulted
RED) — even though `run_duo.sh` runs `assert_behavioral.py` itself and **prints `behavioral=GREEN` in its
own log**. **Fix (in `qa/vm/sweep_v2.sh`): read behavioral from the duo-log verdict, and pass the three
evidence-path flags:**
```bash
behav=RED; grep -q 'behavioral=GREEN' "$RES/duo.log" && behav=GREEN   # NOT the nonexistent combined.jsonl
python3 qa/release_readiness.py --runs … --story … --mech … \
  --behavioral $behav     --behavioral-path "$RES/duo.log" \
  --ui-audit  $audit      --ui-audit-log    "$RES/ui_audit.log" \
  --palette-live true     --palette-source  "$RES/ui_audit.log" \
  --build-sha "$SHA" --out "$RES/RRI.json" --scorecard-row
```
That single change took the VM sweep's RRI from **1.8 → 4.5** with no behavior/score change — it was
pure harness-evidence plumbing. The corrected reference harness is checked in at `qa/vm/sweep_v2.sh`.

## Load-bearing INVARIANTS (never violate)
1. **Engine = SOLE WRITER.** State is `snapshot.json` written under `campaign_lock` via
   atomic temp-file + `os.replace` (`servers/engine/store.py`). The player facade
   (`servers/engine/player_server.py`, the `worldos-player` MCP) is READ-ONLY on state —
   it only appends structured *moves* the DM resolves.
2. **Additive-by-default.** Empty == today; old snapshots round-trip. Models are
   `_StrictModel` (`extra="forbid"`); the tolerant load (#165) drops only unknown
   *top-level* keys. Every new field defaults to today's behavior; each feature is
   independently removable.
3. **Gates/triggers read ONLY engine-mutated values** (`flags`, `reputation`,
   `attitude_value`, `day`, `standing`) — **NEVER fiction.** The engine can't judge prose.
   Quest CONTENT stays DM-advisory; only gauge-backed things get engine teeth.
4. **Engine rolls; the DM is TOLD the result.** Wander/betrayal/variant rolls happen
   in-engine, surface in the tool return; the DM narrates. "Probability proposes, DM disposes."
5. **QA is gateway-free / null-backend, and NEVER touches Eva** (the owner's live OpenClaw
   agent): no gateway restart/reconfigure, no `doctor --fix`, no global `mcp set`, don't
   touch agents `main`/`operations`.

## PLANNING A NEW SUBSYSTEM (research → design → batch → execute)
For a NEW feature/subsystem (not a one-line fix), don't jump to code — this arc works:
1. **Clarify** — if the request is vague, ask 2-3 questions that LOCK the load-bearing constraints
   (delivery target, art/tech direction, scope) BEFORE researching. Wrong constraint → wrong research.
2. **External research FIRST** (`deep-research`) — the outside question (libraries, techniques, prior
   art) — before touching the repo.
3. **Repo design SECOND** (parallel `Explore`/`Plan` agents or a design workflow) — find the
   integration SEAM + what ALREADY EXISTS (the engine is usually 80-90% there — reuse before rebuild).
4. **Verify contested claims against SOURCE before writing the plan** — agents and your own research
   are wrong ~half the time on specifics; read the load-bearing file yourself to resolve disagreements
   before committing to a plan (see [[feedback_validate_adversarial_subagent_findings]]).
5. **Plan → file the epic + child issues in ONE batch script** (not one-at-a-time), labeled per the
   repo's convention → execute each as a worktree PR via the dev loop below. Hand off with the
   `sprint-handoff-doc` skill.

## THE DEV LOOP (exact)
Test policy: **GitHub-CI-first for broad validation; focused local tests for fast feedback.**
Keep Python tests single-process unless the lane explicitly supports parallel execution.

1. **Worktree off main** (keeps the engine/content/QA lane disjoint from the sibling
   macOS/OpenWorlds lane). Implement **additively** (honor every invariant).
2. **Single-process test** (warm the venv first on a fresh worktree):
   ```bash
   uv run --directory servers/engine python -m pytest <relpath> -q -p no:xdist
   ```
   Run focused files for the change; rely on GitHub CI for broad validation unless the
   full local suite is explicitly needed.
3. **Push + open the PR** with a HEREDOC body:
   ```bash
   gh pr create --title "…" --body "$(cat <<'EOF'
   …
   EOF
   )"
   ```
   **DO NOT pipe `gh pr create` through `tail` inside an `&&` chain** — it masks a transient
   failure and silently skips the merge (bit us on #185). Verify the returned PR URL.
4. **Merge through the protected path (see ## THE MERGE GATE).** Default
   `gh pr merge <#> --squash --auto` — this **enables auto-merge and returns immediately** (it
   does NOT block); GitHub squash-merges later once the 5 required `ci.yml` checks + conversation
   resolution pass. **Poll `gh pr view <#> --json state,mergeCommit` until `state==MERGED` before
   step 5** — never sync/clean a still-open PR (stale `main` + premature branch delete). Local
   `license_check` + focused pytest are pre-push confidence, NOT a CI replacement. `--admin` is
   **emergency-only** (declare why in a PR comment + open a follow-up issue) — it bypasses branch
   protection. *(main is branch-protected as of 2026-06-21 — Guardrail + admin-override;
   `allow_auto_merge` + `delete_branch_on_merge` are on.)*
5. **Sync + clean:**
   ```bash
   git pull --ff-only origin main
   git worktree remove --force <worktree> && git branch -D <branch> && git worktree prune
   ```

### Multi-session / worktree gotchas (learned the hard way — these cost real time)
- **`fetch`-not-`pull` leaves the canonical checkout STALE.** Building every PR in worktrees off
  `origin/main` + merging on GitHub means `/Users/m1/WorldOS` is only ever `fetch`ed — its working
  tree silently falls behind and files look "not there." After a merge batch:
  `git -C /Users/m1/WorldOS pull --ff-only`.
- **Merge race.** With `--auto` a parallel PR landing mid-flight just re-queues your merge; with
  `--admin` (emergency-only) it can fail "base branch was modified" and your LOCAL branch may get
  cleaned even though the PR stayed OPEN. Verify with `gh pr view <n> --json state` and **retry** on
  any still-open PR.
- **A worktree's gitignored files are DESTROYED on `git worktree remove`.** Generated/uncommitted
  output written into a worktree's `_private` (or any gitignored path) is gone on cleanup — write
  durable generated artifacts into the **canonical** repo, not a worktree.
- **Secret-scan the diff before every commit** — `git diff --cached | grep -iE '<key-prefixes>'` must
  be empty. Keys live in `~/.worldos/*.key` (mode 600), never the tree.
- **CI-only-validatable changes:** let `--auto` hold the merge until CI concludes (or `gh run watch`).
  Godot extension/archive-only PRs are optional/manual proof only and are not branch-protection gates;
  the protected path still waits for the 5 required Woodpecker checks.
- **New-subsystem skills:** **`asset-gen`** (Meshy/Tripo/Scenario/PixelLab art),
  **`wire-external-api-service`** (integrate a new API/MCP), **`sprint-handoff-doc`** (hand off a sprint).

## THE MERGE GATE — CI + CodeRabbit (don't push-and-abandon)
**Shepherd every PR to merge; never open-and-walk-away.** Stay engaged (or hand to a report-only
watcher) until it lands. **Orphaned PRs** (whose driving agent died/abandoned them) surface daily in
the open **`stuck-pr-report`** issue — when idle or resuming, check it and adopt one. Merge only when ALL hold:
1. **CI green AND present** — all 5 required `ci.yml` contexts (`test`, `viewer-tests`,
   `qa-release-gate-tests`, `server-contracts`, `license-check`) have a check-run on THIS head and
   are SUCCESS. A *missing* context reads as no-status, not pass (strict=false + a job added to
   ci.yml after your last push ⇒ re-push to regenerate it).
2. **CodeRabbit reviewed & clear** (or degraded-mode handled). The reliable "done" signal is a
   **review object whose `commit_id == head SHA`**, NOT the early `summarize by coderabbit.ai`
   comment and NOT the commit status (a rate-limited run shows a green status with zero review).
   Nitpicks hide in the review body's `<details>` blocks — don't trust "Actionable comments: N" alone.
3. **All review threads genuinely addressed** (required_conversation_resolution is on). Resolve a
   thread only after FIX / FILE / WON'T-FIX / FALSE-POSITIVE — never resolve-without-addressing.
4. **No human `CHANGES_REQUESTED`** outstanding + stale-base rebase check (strict=false lets a green
   stale PR merge; rebase if main moved in a semantically-conflicting way).

**Nit-triage** — judge each finding by **PX-relevance** (does it change what a player sees/hears/does,
or corrupt save state?). "actionable = 0" = *zero unaddressed PX-relevant correctness findings*, not
zero total comments. FIX-in-PR if P≤2 & PX-relevant & in-scope; FILE a `Post-1.0 / vNext` issue (reply
linking it, then resolve) if real-but-out-of-scope; WON'T-FIX (one batched reply) for P3/P4 cosmetics;
FALSE-POSITIVE if the bot misread. **Only P0 + un-concurred P1 PX-relevant findings block the merge;
nits NEVER block.** Before applying any bot suggestion touching engine/snapshot/facade/schema, run the
invariant-conflict check (sole-writer / additive / gates-read-engine / `extra=forbid`) — a suggestion
that breaks an invariant is a FALSE-POSITIVE, not a fix.

**Degraded mode (CodeRabbit rate-limited / credit-exhausted / silent):** retry once with `@coderabbitai
review`; if still no review-object, substitute a review (`adversarial-pr-sweep` or your own adversarial
diff read), label `needs-coderabbit-rereview`, proceed — NEVER treat a green status as "clear". Bound
the wait. **Trivial lane:** lint/docs/comment-only diffs merge on CI-green + your own review. **Headless
sink:** with no reachable human, label `blocked`+`needs-human`, leave threads unresolved, exit cleanly.
**Cache cost is NEVER a reason to merge early.**

**Multi-round → delegate to a report-only watcher** (`pr-review-loop`): poll in segmented ≤270s chunks
(keep the cache warm — a completion-only `sleep 600` just defers a cold read), triage + reply, and
RETURN `{ci_status, coderabbit_review_status, actionable_comments[], unresolved_threads}`. The MAIN
thread owns the final 95% merge decision; the watcher does not merge.

## QA STRATEGY — pick the TIER (don't run the 90-min sweep to iterate)
**Match the test to the change + your confidence — run a MIX, not all-or-nothing.** The full 5-persona
sweep is the MILESTONE verdict, not the inner loop. Design + the honest signal accounting (the
adversarial critique that proved naive mid-arc-snapshot seeding is a false-confidence trap — it bakes
in the post-fix state and skips the seat-path/cold-open/free-play surfaces our bugs live in):
`docs/qa/FAST_GATE.md`.

| Tier | Run | Cost | When |
|---|---|---|---|
| **0 — deterministic** | `qa/fast_gate.sh` | **$0, ~2s** | EVERY engine/content/data/viewer change (and in CI). 186 engine tests + the end-to-end seat-path skill guard → the structural / seat-path / rest / travel / combat-resolution regression classes, caught free + instant. |
| **1.5 — mechanism probe** | `qa/mechanism_probe.sh <name> <fixture> [beats=3] [budget=1.00]` | ~$1, ~10 min | A CUE-mechanism question ("does obligation cue X fire + does the DM ACT on it?"). Seeds a deterministic trigger fixture (`qa/probe_fixtures/<fixture>.py`) + drives 2–3 REAL DM beats, then a DETERMINISTIC verdict (ACTED/IGNORED/CUE_ABSENT — no LLM lens). First fixture: `wrap_window_active_quest`. **ITERATION-ONLY** — the seed skips cold-open/seat-path/free-play surfaces; never a release verdict. |
| **1 — fast LLM loop** | `qa/fast_probe.sh [persona]` | ~$2–3, ~20 min | DM-craft / UX / satisfaction changes. ONE persona ROTATED by iteration (`newbie→…→optimizer`) + a 6-beat duo (≥6 keeps the behavioral floors armed). Iteration signal ONLY. |
| **2 — milestone sweep** | the `## VM GATE SWEEP` procedure above (VM part-B + Mac part-A) | ~$10, ~90 min | ONLY when 0+1 pass + before a version bump → RRI → #466. Also the periodic recalibration of the cheap tiers vs the full RRI. |

Rules: Tier 0 on **every** change (free + instant — most regressions we ship live here, e.g. the
skill-case +3-not-+6 crit). Tier 1 when you touched DM/UX and need a satisfaction/quality read —
**rotate the persona** so variance sweeps over 5 loops (one fixed optimizer misses the veteran/
adversarial fails). Tier 2 sparingly. **NEVER report a Tier-0/1 result as a release verdict.** After
any tier, log the outcome (tier, build SHA, result, delta) to the ledger so progress is observable.

## THE QA LOOP
Spec: `qa/SCORING.md`. **The ledger is `qa/scores_db.py` (SQLite `scores.db`) → rendered to
`qa/scores_ledger.md` (`add_run(...)` / `--render`); `qa/SCORECARD.md` is LEGACY narrative.** Append
every scored run via `add_run(...)` — NEVER hand-edit `scores_ledger.md`. Load-bearing columns:
**surface** (engine-duo / GUI-built-app / GUI-headless-proxy / smoke-only) + **dm_model** + **actor_model**
+ **scorer** — this is what prevents cross-surface/cross-model score confusion; mark contaminated rows
(RED-capped / PARTIAL / harness) with `*` so an RRI-2.7-class number is never cited as a clean quality score.
Targets: **story ≥ 4.3, mechanical ≥ 4.5, gate GREEN, 0 critical/high.**

**Story/mechanical runners** (from repo root):
- `qa/run_duo.sh <run> <world> <persona> [beats] [budget]` — AI player + DM duo (gateway-free
  `claude -p`; threaded `--session-id`/`--resume`, re-grounds from snapshot each beat).
- `qa/run_combat_sprint.sh <run>` — **the fast BUG-FINDER** (~2 min, pre-seeded fight, one DM
  call, Angry-DM-scored). Use to *find* engine defects.
- `qa/run_party.sh` — player + companion peer agents + DM (the betrayal path / Quest-Arc L2).
- `qa/run_duo_openclaw.sh` — gpt-5.4 path (scoped `worldos-qa*` agents, `--thinking low`).

Scoring (story/mech runs): **behavioral gate** (`qa/assert_behavioral.py`, deterministic; RED ⇒
all lenses capped ≤2.5) + **3 LLM lenses** — Mechanical (`rubric.md`), Story-craft/Tolkien
(`rubric_tolkien.md`), 5e-fidelity/Angry-DM (`rubric_angry_dm.md`). Scorer `qa/score.sh`
(claude, PRIMARY) or `qa/score_openclaw.sh` (gpt-5.4, grades ~1.5 harsher — cross-check only).

QA truths:
- The combat-sprint **finds bugs**; a single sprint's Angry-DM is **coverage-capped ~3**.
  The score climbs via **broader play + a richer seed**, not re-running one short fight.
- Low mech/Angry-DM on emergent duos is usually a **sampling artifact** (players drift to
  roleplay), not an engine defect — force fights via wander + combat-seeking personas.
- **Seat a LIVING canon PC** — `load_canon_character(name, kind="player")` of a mid-tier
  *alive* NPC (NEVER invent a PC, NEVER the 7 origins, NEVER a dead one — #305: Dal Lightspark
  is canonically dead, so the ow-v103-reval scores are suspect and OWE a re-run on a living PC).
  `qa/seed_canon_fixture.py` seeds the rich living-PC fixture used by the GUI/playtest surfaces.

## THE GUI QA LOOP — AI playtester harness (#324, Option B)
**The GUI-quality strategy is now EMPIRICAL play, not screen-by-screen audit grinding.** Owner
pivot (2026-05-30, `docs/ui-audit/STRATEGY.md`): GUI usability ≈ 2/10, the gap is **wiring not
visuals**, and the way to find what's actually broken is to **let a blind AI try to play it**.
Don't build more screens, don't hand-tune CSS per screen, don't grind all 17 to perfect — run
the harness, fix what it empirically hits.

`qa/ui_playtest.sh <run> <world> <persona> <beats> <budget>` (doc: `qa/UI_PLAYTEST.md`):
```bash
qa/ui_playtest.sh play1 baldurs-gate newbie 30 3.00   # the canonical v1 command
```
Three processes, one run: **Engine + Viewer** (serves the real `/openworlds/` UI, SOLE writer +
a `/move` sink + two-sided `/chat`) · **DM agent** (`claude -p`, UNCHANGED from `run_duo.sh` — it
never knows there's a UI; a bg loop resolves each posted move) · **PLAYER agent** (`claude -p`
with ONLY the Playwright palette MCP — a blind persona; sees only screenshot/a11y, acts only via
the palette). The Player drives the browser → clicks POST `/move` → the DM resolves → narration
flows back onto the screen; the loop is validated **because the player could play it**.

The restricted **9-tool palette** (`qa/playwright/palette_server.js`, an MCP server backing a
Chromium browser — mirrors the engine's role-enforced `player_server.py` facade; NO source/engine/
filesystem access): `screenshot · a11y_tree · click · type · key · wait · report_bug · give_up · finish`.
**`finish(satisfaction, verdict)` = a SATISFIED end** → `gave_up=false` + a self-reported 1–10 score (the
scorer PREFERS `status.satisfaction` over the brittle verdict-regex); **`give_up` = a genuine BLOCK** (dead
control / error / DM stalled). Don't collapse them — #574: a satisfied player who just stopped was mis-scored
`gave_up=true` + a derived low sat (7→2). Goal-gate G3 = sat ≥ 7 self-reported AND `gave_up=false`.
**Reading a DERIVED sat (load-bearing — verify `satisfaction_source`):** most personas DON'T call
`finish()` with a number (4/5 in the 2026-06-02 VM sweep), so the scorer DERIVES sat = `8 − friction`
(crit=1 ⇒ 6) — which **can never clear G3 (≥7)**. So a run with `gave_up=false` + `arc=true` + `crit≤1`
+ a *derived* 5–6 is a **persona-didn't-self-report** signal, NOT a dissatisfied player; only
`satisfaction_source: self-reported` counts toward G3, and a derived G3-miss is **inconclusive** (re-run a
persona that finishes). (`self-reported` can also come from the mandated `Satisfaction: N/10` verdict line,
not only `finish()`.) The fix is to make personas reliably self-report, not to read a derived 5–6 as "bad".
(Use `locator.ariaSnapshot()` — `page.accessibility.snapshot()` was removed in Playwright ≥1.5x.)
It **passively** auto-emits console errors + 4xx/5xx as `source:"auto"` bugs.

Output `qa/ui_playtest_runs/<run>/` (**gitignored** — bulky, regenerable, public repo): per-action
`screenshots/` + `a11y/` + `actions.ndjson`, `bugs.ndjson` (schema `qa/ui_playtest_bug_schema.json`),
`summary.md`, `score.json`. Scored by `qa/ui_playtest_score.py` — passes when `completed_intro_flow`
**and** `critical==0` **and** `console_errors==0` **and** `satisfaction≥6`.

Setup (one time, owner-authorized): `(cd qa/playwright && npm install && npx playwright install
chromium)` — chromium-only ~92MB. (The repo's "4.3 GB disk" caution was STALE; ~24 GB free as of
#325. Still: the harness is heavy — see the OVERNIGHT-LOOP cap below.)

**Strategy / sequence:** run the harness vs current `origin/main` BEFORE fixing → the surfaced
bugs become the issue queue (ground truth for "what's broken") → triage by severity, fix P0/P1
play-loop blockers first → **v2 = the other 4 personas** in STRATEGY.md (BG3-veteran, adversarial-QA,
narrative-player, build-optimizer) + parallel + a scoring aggregator → then Option C (Vite+TS+shadcn,
one screen/wk). Bugs hit by ALL personas are P0; by 1 persona, P3 (natural prioritization).

## DELEGATION
Create worktree → spawn agent with a **precise spec** + the **single-process-test guardrail**
+ **"flag-don't-force-fix if many tests break"** → **review the diff (trust BUT verify;
confirm additive + invariant-safe + no weakened guardrail tests)** → merge via the dev loop.
API agents (Agent tool, gpt-5.4) don't strain the host — fan out freely; **heavy `claude -p`
runs (duo / combat-sprint / `ui_playtest.sh`) are host-heavy** — see the OVERNIGHT-LOOP cap below.
Reap orphaned `player_server`/`server.py`/headless-chromium procs after every run.

**Builder-spec contract (paste-ready).** Every delegated build gets: `isolation: worktree` off
`origin/main`; **additive / default-off** (empty == today; old snapshots round-trip); **sole-writer**
(state only via the existing locked verbs); **flag-don't-force-fix** (stop + report if a guard fights
the change — don't weaken it); focused single-process pytest (`-p no:xdist`); **restore `qa/scores.db`**
if a test mutated it; mind the **120 KB tool-schema budget** (no new MCP tool/params; docstrings count —
a verbose docstring once failed CI by 10 bytes, so put detail in the runtime error, not the schema).
And it must **REPORT BACK**: PR # + URL, a **before/after demo**, a per-file summary, the additivity +
sole-writer proof, the pytest result, a **confidence %**, and the open risks.

**Verify before merge — don't trust green.** A builder's own suite (even 3,000+ tests) proves the
happy path, not the *invariant*. For any load-bearing additive PR, run the **`adversarial-invariant-verify`**
skill (fan out skeptics at the named invariants — additive/byte-identical, sole-writer, guard-can't-leak,
math-correct — in a review worktree) before merging. This caught a non-byte-identical dice path + a
TEST-toggle leak that a 3,092-test green suite missed. (Liveness + salvage of a dead builder: see
OVERNIGHT-LOOP below.)

## OVERNIGHT-LOOP DISCIPLINE (the autonomous-session lessons — these cost real time)
Encoded from the 2026-05-29/30 overnight burndown. They are the failure modes that silently kill
an unattended loop.
- **Verify agent LIVENESS — "no completion notification" ≠ running.** Heavy agents die silently
  on OOM / the 5-hour rate-limit (two died at ~00:54 and were wrongly assumed live). Check the
  agent's `*.jsonl` mtime (is it still being written?) and/or whether its PR exists — don't infer
  "running" from the absence of a result.
- **Cap heavy agents on the 16 GB host: 1 heavy + 1 light, never a swarm.** ~4-5 concurrent heavy
  `claude -p` runs OOM'd the box and silenced DMs mid-run. Revalidation/scoring runs go
  **sequentially, one heavy DM at a time**, for clean (non-degraded) scores. `gpt-5.4` API agents
  are fine to fan out — only the local `claude -p` lane is the constraint.
- **The salvage pattern — a dead agent's STAGED work is recoverable.** When an agent dies mid-run,
  inspect its worktree: if the staged diff is complete, commit it + CI-validate + open the PR
  yourself (recovered #305 → #329 this way; nothing lost). Don't re-do work a corpse already finished.
- **NEVER `git checkout` / branch-op the SHARED canonical checkout while a parallel session holds a
  branch.** `/Users/m1/WorldOS` may be on another session's branch (e.g. loop-8). Branch-flips
  there corrupt the sibling session. Do EVERY repo change via a **worktree agent off `origin/main`**
  (`git worktree add -b <branch> <path> origin/main`). If you must touch the shared checkout's main,
  do it as ONE atomic Bash (`git checkout main && test branch==main && add && commit && push`) — never
  trust the working-tree branch between separate tool calls (background `spawn_task`s flip it too).
- **A sub-agent told "read-only" may still leave live edits in the shared tree** — `git status` after
  every agent run; adopt+finish good orphaned WIP rather than waste it (but fix its incomplete tests).
- **The host's #1 OOM cause is NOT the project's agents — it's the code-index / notion MCP-server
  triplet leak.** The ~12 long-running procs an owner sees are GitNexus/notion servers (documented
  leak); **restart Codex to clear them**. Reap genuinely-wedged WorldOS procs, but don't chase the
  MCP triplets as if they were your QA agents.

## DISCIPLINE (the expensive lessons)
- **Validate before fixing** — the LLM scorer mis-attributes root cause (the "STR-18 attack
  bug" was a FALSE alarm; engine was correct at +6). Reproduce against the engine first.
- **Surfacing info ≠ the DM using it** — fold the value into a trigger the DM already hits
  every turn (e.g. `turn_brief` on `next_turn`; Director at beat start), or ENFORCE in the
  engine (Multiattack #181, turn-skip #183).
- **QA must EXERCISE the flag before you trust an A/B** — `qa/run_duo.sh` once `--resume`d the full
  transcript and IGNORED `WORLDOS_LEAN_BEATS`, so every prior "lean" A/B was a non-lean artifact (a
  short run just has a smaller transcript that reads as "context dropped"). Before trusting a flag A/B
  (lean / effort / `alwaysLoad`), READ the runner you invoked and confirm its DM turn actually consumes
  the flag. A flag BUILT but never exercised by QA is DEAD CODE — expect latent bugs the moment it runs
  for real (#538 recent_narration + #543 scene_context AttributeError surfaced only after #549 wired lean).
- **DM-beat latency is model REASONING, not the GUI/harness** — measured ~100–126s/beat at `--effort
  medium` ≈ original Opus-high; it's `duration_api_ms` (surface-independent), not the viewer/Playwright.
  Don't chase a harness perf bug. Levers + the SDK measurement method are in the `worldos-latency-forensics`
  skill: `alwaysLoad` un-defers engine tools (cold-open 248→176s, neutral), effort is the trading lever
  (owner's quality call, A/B first), model choice is OPEN (see `docs/MODEL-TIERING-STRATEGY.md`).
- **First-principles for load-bearing decisions** (public contract/schema/tool API) — write
  a decision doc, not speculative code.
- **REUSE before rebuild** — the engine is usually ~80–90% there (Quest-Arc reused the
  companion stage-machine; #143 reused `_resolve_quest_variants`). Find the existing primitive.
- **Don't collide with the macOS/OpenWorlds sibling lane** (their open PRs: #150/#182/#187 +
  drafts #190/#191/#192). Stay in the engine/content/QA lane.

## THE ROOM PAINT LOOP (unified pipeline — added 2026-07-15)
One command per room; never freehand a Scenario call: `python3 qa/paint_room.py <class> --depth <png>`
(prompts/params pinned in qa/unified_paint_recipes.json; controlImage slot pinned — see the
slot-bug postmortem in qa/evidence/gemini-restyle/FLUX_ROOTCAUSE.md). The full loop, gates, cycle
log, and defect classes: docs/roadmap/PROCEDURAL-SCORECARD.md + docs/ROOM-PIPELINE-RUNBOOK.md.
Town generation: tools/generate_town.py → qa/seed_gfx_town.py (reciprocal cross_door contract).
Verification instruments: qa/select_best_draw.py, qa/overlay_boxes.py (--solve, --composite).
