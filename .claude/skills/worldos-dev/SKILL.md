---
name: worldos-dev
description: Develop, test, and QA the WorldOS plugin (the living-world D&D 5e engine). Use when implementing an engine/content/QA change, running or scoring a playtest, delegating a build to a subagent, or resuming the project after a compaction. Encodes the exact dev loop (worktree → additive change → single-process LEXAR pytest → PR → squash-admin merge → prune), the QA loop (duo / combat-sprint runners + the 3 lenses + behavioral gate), and the load-bearing engine invariants. Read WorldOS-RUNBOOK.md first for full context.
---

# WorldOS Dev

You build and harden **WorldOS** — a post-BG3 living-world D&D 5e Claude Code plugin. North
star: **epic Baldur's-Gate-caliber STORY on a deterministic SRD 5.2 engine**; goal: a
universe-system that generates worlds. FREE product, BG-only. **Read `WorldOS-RUNBOOK.md`
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
Test policy: **GitHub-CI-first; local only on LEXAR; SINGLE-PROCESS** (main disk ~4.3 GB
free — parallel workers OOM the host). This repo is on LEXAR, so local single-process is OK.

1. **Worktree off main** (keeps the engine/content/QA lane disjoint from the sibling
   macOS/OpenWorlds lane). Implement **additively** (honor every invariant).
2. **Single-process test** (warm the venv first on a fresh worktree):
   ```bash
   uv run --directory servers/engine python -m pytest <relpath> -q -p no:xdist
   ```
   **NEVER pass `-n` / use xdist** — parallel workers OOM the host. Run focused files for
   the change; run the full `servers/engine/tests` before merge.
3. **Push + open the PR** with a HEREDOC body:
   ```bash
   gh pr create --title "…" --body "$(cat <<'EOF'
   …
   EOF
   )"
   ```
   **DO NOT pipe `gh pr create` through `tail` inside an `&&` chain** — it masks a transient
   failure and silently skips the merge (bit us on #185). Verify the returned PR URL.
4. **Merge (local-gate):** `gh pr merge --squash --admin` — GitHub Actions is degraded
   repo-wide; we gate on local single-process pytest + `license_check`, CI reconciles post-merge.
5. **Sync + clean:**
   ```bash
   git pull --ff-only origin main
   git worktree remove --force <worktree> && git branch -D <branch> && git worktree prune
   ```

## THE QA LOOP
Spec: `qa/SCORING.md`. **Log every run to `qa/SCORECARD.md`.** Targets: **story ≥ 4.3,
mechanical ≥ 4.5, gate GREEN, 0 critical/high.**

Runners (from repo root):
- `qa/run_duo.sh <run> <world> <persona> [beats] [budget]` — AI player + DM duo (gateway-free
  `claude -p`; threaded `--session-id`/`--resume`, re-grounds from snapshot each beat).
- `qa/run_combat_sprint.sh <run>` — **the fast BUG-FINDER** (~2 min, pre-seeded fight, one DM
  call, Angry-DM-scored). Use to *find* engine defects.
- `qa/run_party.sh` — player + companion peer agents + DM (the betrayal path / Quest-Arc L2).
- `qa/run_duo_openclaw.sh` — gpt-5.4 path (scoped `clawdnd-qa*` agents, `--thinking low`).

Scoring: **behavioral gate** (`qa/assert_behavioral.py`, deterministic; RED ⇒ all lenses
capped ≤2.5) + **3 LLM lenses** — Mechanical (`rubric.md`), Story-craft/Tolkien
(`rubric_tolkien.md`), 5e-fidelity/Angry-DM (`rubric_angry_dm.md`). Scorer `qa/score.sh`
(claude, PRIMARY) or `qa/score_openclaw.sh` (gpt-5.4, grades ~1.5 harsher — cross-check only).

QA truths:
- The combat-sprint **finds bugs**; a single sprint's Angry-DM is **coverage-capped ~3**.
  The score climbs via **broader play + a richer seed**, not re-running one short fight.
- Low mech/Angry-DM on emergent duos is usually a **sampling artifact** (players drift to
  roleplay), not an engine defect — force fights via wander + combat-seeking personas.

## DELEGATION
Create worktree → spawn agent with a **precise spec** + the **single-process-test guardrail**
+ **"flag-don't-force-fix if many tests break"** → **review the diff (trust BUT verify;
confirm additive + invariant-safe + no weakened guardrail tests)** → merge via the dev loop.
API agents (Agent tool, gpt-5.4) don't strain the host — fan out freely; only `claude -p` QA
is host-heavy (**2 concurrent is fine**). Reap orphaned `player_server`/`server.py` procs.

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
