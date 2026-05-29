---
name: worldos-dev
description: Develop, test, and QA the WorldOS plugin (the living-world D&D 5e engine). Use when implementing an engine/content/QA change, running or scoring a playtest, running the blind AI-playtester GUI harness, delegating a build to a subagent, or resuming the project after a compaction. Encodes the exact dev loop (worktree → additive change → focused single-process pytest → PR → merge → prune), the story/mech QA loop (duo / combat-sprint runners + the 3 lenses + behavioral gate), the GUI QA loop (the #324 Playwright AI-playtester harness — Option B), the overnight-loop discipline (agent liveness, host-capacity cap, salvage, shared-checkout safety), and the load-bearing engine invariants. Read WorldOS-RUNBOOK.md first for full context.
---

# WorldOS Dev

You build and harden **WorldOS** — a post-BG3 living-world D&D 5e Claude Code plugin. North
star: **epic Baldur's-Gate-caliber STORY on a deterministic SRD 5.2 engine**; goal: a
universe-system that generates worlds. Source-available commercial product, BG-focused. **Read `WorldOS-RUNBOOK.md`
(repo root) for the full project/architecture/state.** This skill is the operational loop.

## Load-bearing INVARIANTS (never violate)
1. **Engine = SOLE WRITER.** State is `snapshot.json` written under `campaign_lock` via
   atomic temp-file + `os.replace` (`servers/engine/store.py`). The player facade
   (`servers/engine/player_server.py`, the `clawdnd-player` MCP) is READ-ONLY on state —
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
4. **Merge only after checks pass.** Use the repository's normal PR merge path and treat
   local `license_check` + focused pytest as pre-push confidence, not a replacement for CI.
5. **Sync + clean:**
   ```bash
   git pull --ff-only origin main
   git worktree remove --force <worktree> && git branch -D <branch> && git worktree prune
   ```

## THE QA LOOP
Spec: `qa/SCORING.md`. **Log every run to `qa/SCORECARD.md`.** Targets: **story ≥ 4.3,
mechanical ≥ 4.5, gate GREEN, 0 critical/high.**

**Story/mechanical runners** (from repo root):
- `qa/run_duo.sh <run> <world> <persona> [beats] [budget]` — AI player + DM duo (gateway-free
  `claude -p`; threaded `--session-id`/`--resume`, re-grounds from snapshot each beat).
- `qa/run_combat_sprint.sh <run>` — **the fast BUG-FINDER** (~2 min, pre-seeded fight, one DM
  call, Angry-DM-scored). Use to *find* engine defects.
- `qa/run_party.sh` — player + companion peer agents + DM (the betrayal path / Quest-Arc L2).
- `qa/run_duo_openclaw.sh` — gpt-5.4 path (scoped `clawdnd-qa*` agents, `--thinking low`).

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

The restricted **8-tool palette** (`qa/playwright/palette_server.js`, an MCP server backing a
Chromium browser — mirrors the engine's role-enforced `player_server.py` facade; NO source/engine/
filesystem access): `screenshot · a11y_tree · click · type · key · wait · report_bug · give_up`.
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
  branch.** `/Users/lume/ClawDnD-val` may be on another session's branch (e.g. loop-8). Branch-flips
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
- **First-principles for load-bearing decisions** (public contract/schema/tool API) — write
  a decision doc, not speculative code.
- **REUSE before rebuild** — the engine is usually ~80–90% there (Quest-Arc reused the
  companion stage-machine; #143 reused `_resolve_quest_variants`). Find the existing primitive.
- **Don't collide with the macOS/OpenWorlds sibling lane** (their open PRs: #150/#182/#187 +
  drafts #190/#191/#192). Stay in the engine/content/QA lane.
