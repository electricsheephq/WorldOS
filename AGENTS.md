# WorldOS — what this repo is
WorldOS is a living-world D&D 5e engine: a deterministic Python engine is the SOLE WRITER of world state; the player's own AI agent is the DM; renderers (the Unity player, the web viewer, the text tier) are pure consumers. Destination: a fully rendered CRPG at Pillars of Eternity II quality, built AND playtested by agents.
Five rules every agent follows: (1) the engine is the sole writer — no renderer or harness mutates state; (2) decision-by-eval — no claim without its instrument (gate exit codes are the verdict, the author never judges their own run, blind adjudication for panels and playtests); (3) geometry is ground truth — collision/occlusion come from the grid + boxes sidecar, paint is cosmetic, every room ships walk-certified with a sha-pinned cert; (4) the agent plays first — every build gets an agent playthrough (sandbox player + /click /shot /debug + the viewer) before any owner ask; the owner is the escalation at the 80/20 wall; (5) pixels before credit — nothing rendered is done until a frame of the RUNNING build was looked at.
Bootstrap order: docs/OPERATIONS.md → docs/roadmap/NOW.md → docs/ACTIVE-GOAL.md → docs/roadmap/PRODUCT-ROADMAP.md → the `active-sprint` charter issue → docs/RUNBOOK-INDEX.md. Ports: owner engine 8776 / QA 8981; sandbox 8866 / 8972; NEVER 8766 (not WorldOS). Canonical repo checkout /Users/m1/WorldOS; the Unity renderer PROJECT lives at /Users/m1/worldos-unity (local Unity 6000.5.6f1 — the GEX44 box is retired).

# WorldOS Agent Instructions

## Codex Desktop Local-Resource Policy

- Treat `/Users/m1/WorldOS` as the canonical local Mac app/private-art checkout for WorldOS GUI and native-app testing.
- Use `/Users/m1/Codex` for Codex artifacts, scratch files, screenshots, reports, and downloaded CI/VM artifacts.
- Use same-disk local worktrees for GUI/native-app edits that must launch against private art. Lexar worktrees are fine for docs, backend-only, and non-GUI slices that do not launch the app against private art.
- Before running install, build, or test commands, verify `pwd`. If a GUI/native app run is not in `/Users/m1/WorldOS` or a same-disk worktree with `WORLDOS_ART_REPO_ROOT=/Users/m1/WorldOS`, explain why.
- Prefer the local Mac (14 cores / 64 GB) for heavyweight validation, full suites, matrix tests, long integration tests, and persona sweeps; use GitHub Actions when a remote runner is better suited. `support-vm-1` is a customer box, not the default heavy-sweep host.
- Run local tests only for fast feedback, local-only reproduction, validating unpushed edits, or Mac-only `.app` proof. Use the narrowest focused command first.
- Do not launch multiple heavyweight local suites or persona sweeps in parallel on this Mac.
- If local test work causes memory pressure, stop it, report the command/path, and switch to a narrower check or GitHub CI.

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
- Use `support-vm-1` only for explicitly customer-box or remote-runner work after its credentials/config are intentionally installed and verified there; local heavy QA remains the default.
- VM preflight must record VM identity, repo checkout path, branch/SHA, Codex CLI version, auth/profile status, `uv`, Node/npm/Playwright availability, private-art status or explicit backend-only/no-art classification, env vars, budget/concurrency cap, teardown commands, and artifact return path under `/Users/m1/Codex`.
- The VM cannot prove Mac-only surfaces. `WorldOS.app` build/launch, native #356, and built-app UI play evidence stay on this Mac or macOS CI.
- VM artifacts can feed RRI only when `run.json`, `score.json`, `session_surface.final.json`, network/image evidence, palette-live evidence, and build SHA are explicit. Otherwise the result remains partial/harness-contaminated.

## GEX44 GPU host (HISTORICAL — retired 2026-08-06)

The former GEX44 GPU host was the preferred heavy-sweep and Unity/visual-renderer lane before it was
discarded on 2026-08-06. This section is retained only as retirement context: do not SSH, rsync,
build, capture, or save against `46.4.26.123`, `/home/unity`, or the retired `gex44-unity-host` tooling.
Unity 6000.5.6f1 runs at
`/Users/m1/worldos-unity` (mirror `/Users/m1/Codex/worldos-unity-mirror`) via
`extensions/renderers/unity/tools/mcp_stdio_exec.py`; build with the WorldOS macOS menu command and run
QA through `qa/qa_sandbox.py`. Local Mac heavy QA is primary; `support-vm-1` is a customer box.

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

## GitNexus (disabled)

GitNexus is disabled on this machine (owner, 2026-10-06): no MCP server, no skills, no nightly index refresh.
Use `rg` and file reads. Never run `gitnexus analyze` or `npx gitnexus` here.
