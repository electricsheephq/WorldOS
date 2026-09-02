# WorldOS Agent Instructions

## Codex Desktop Local-Resource Policy

- Treat `/Users/m1/WorldOS` as the canonical local Mac app/private-art checkout for WorldOS GUI and native-app testing.
- Use `/Users/m1/Codex` for Codex artifacts, scratch files, screenshots, reports, and downloaded CI/VM artifacts.
- Use same-disk local worktrees for GUI/native-app edits that must launch against private art. Lexar worktrees are fine for docs, backend-only, and non-GUI slices that do not launch the app against private art.
- Before running install, build, or test commands, verify `pwd`. If a GUI/native app run is not in `/Users/m1/WorldOS` or a same-disk worktree with `WORLDOS_ART_REPO_ROOT=/Users/m1/WorldOS`, explain why.
- Prefer GitHub Actions or the 32GB support VM for heavyweight validation, full suites, matrix tests, long integration tests, and persona sweeps.
- Run local tests only for fast feedback, local-only reproduction, validating unpushed edits, or Mac-only `.app` proof. Use the narrowest focused command first.
- Do not launch multiple heavyweight local suites or persona sweeps in parallel on this Mac.
- If local test work causes memory pressure, stop it, report the command/path, and switch to a narrower check, GitHub CI, or the support VM.

## WorldOS Takeover Truth

- Read `docs/OPERATIONS.md` FIRST (the bootstrap), then `docs/roadmap/NOW.md` → `docs/ACTIVE-GOAL.md` → `docs/roadmap/PRODUCT-ROADMAP.md` → the `active-sprint`-labeled charter issue. Runbooks (`WorldOS-RUNBOOK.md`, `WorldOS-GUI-RUNBOOK.md`) are reference, not the entry point.
- The product is the launchable, playable `dist/WorldOS.app`. Wrapper/config/test-only progress does not count as product progress unless it directly unlocks built-app gameplay evidence.
- Engine remains sole writer of campaign state. GUI/native app remains a thin reader plus `/move` intent submitter.
- Built-app proof must include visible narration, private art, an active player, enabled actions, accepted `/move`, and `/session-surface` showing the live campaign as actionable.
- The current `qa/RRI.json` from `f5500ac` is partial/harness-contaminated evidence, not a release verdict.
- Release evidence requires the RRI contract in `qa/release_readiness.py`: expected/completed/missing personas, disk-backed scores, behavior/UI/image/palette evidence, same build SHA, and non-partial status.

## Support VM

- Target VM: owner-provided 32GB support VM, `support-vm-1`.
- Connection/auth details are operator-only and should stay outside tracked repo docs.
- Use the support VM for heavy backend/persona sweeps only after Codex CLI credentials/config are intentionally installed and verified there.
- VM preflight must record VM identity, repo checkout path, branch/SHA, Codex CLI version, auth/profile status, `uv`, Node/npm/Playwright availability, private-art status or explicit backend-only/no-art classification, env vars, budget/concurrency cap, teardown commands, and artifact return path under `/Users/m1/Codex`.
- The VM cannot prove Mac-only surfaces. `WorldOS.app` build/launch, native #356, and built-app UI play evidence stay on this Mac or macOS CI.
- VM artifacts can feed RRI only when `run.json`, `score.json`, `session_surface.final.json`, network/image evidence, palette-live evidence, and build SHA are explicit. Otherwise the result remains partial/harness-contaminated.

## Unity / GPU host status (GEX44 retired)

GEX44 was retired 2026-08-06; do not SSH, rsync, build, capture, or save against `46.4.26.123`,
`/home/unity`, or the retired `gex44-unity-host` tooling. Unity 6000.5.6f1 runs at
`/Users/m1/worldos-unity` (mirror `/Users/m1/Codex/worldos-unity-mirror`) via
`extensions/renderers/unity/tools/mcp_stdio_exec.py`; build with the WorldOS macOS menu command and run
QA through `qa/qa_sandbox.py`. Heavy backend/persona sweeps use `support-vm-1`.

## Shared Owned-Repo Policy

- Use `100yenadmin/codex-operating-kit` for the shared issue/epic/milestone/sprint policy, PR review-thread lifecycle, and release changelog standard.
- For meaningful GitHub work, create or reuse an issue before implementation, link PRs to the issue, and update the issue/tracker before handoff, merge, or pause.
- Before merge, release, or readiness claims, query current-head review threads and separate resolvable review threads from top-level bot comments and check annotations.
- P0-P2 current actionable review threads block merge/release unless fixed, proven false-positive, or explicitly escalated. P3/advisory threads still need terminal disposition.
- Releases, prereleases, and release-affecting PRs must lead with human-readable user/operator outcomes and keep proof, evidence, artifact identity, and rollback details in a compact verification tail.
- Keep WorldOS-specific RRI, persona proof, built-app, GPU/VM, private-art, and release-readiness gates in this repo's runbooks. The shared kit supplies the common operating spine only.

## GitHub And Reviews

- Use branch prefix `codex/` for new branches unless instructed otherwise.
- Keep PRs draft until the evidence is honest enough for review.
- If a PR is part of the work, do not end while required checks, review-bot status, or current actionable review threads are unresolved unless the user explicitly asks to pause.
- Keep up with CodeRabbit and GitHub review threads. Verify each comment against the code, fix valid issues, and rerun focused validation before pushing.
- Treat generic warning-only bot suggestions as non-blocking unless they identify a real defect or the repository enforces them.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **WorldOS** (23838 symbols, 48173 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/WorldOS/context` | Codebase overview, check index freshness |
| `gitnexus://repo/WorldOS/clusters` | All functional areas |
| `gitnexus://repo/WorldOS/processes` | All execution flows |
| `gitnexus://repo/WorldOS/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
