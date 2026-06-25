# WorldOS Agent Instructions

## Codex Desktop Local-Resource Policy

- Treat `/Users/lume/WorldOS` as the canonical local Mac app/private-art checkout for WorldOS GUI and native-app testing.
- Use `/Volumes/LEXAR/Codex` for Codex artifacts, scratch files, screenshots, reports, and downloaded CI/VM artifacts.
- Use same-disk local worktrees for GUI/native-app edits that must launch against private art. Lexar worktrees are fine for docs, backend-only, and non-GUI slices that do not launch the app against private art.
- Before running install, build, or test commands, verify `pwd`. If a GUI/native app run is not in `/Users/lume/WorldOS` or a same-disk worktree with `WORLDOS_ART_REPO_ROOT=/Users/lume/WorldOS`, explain why.
- Prefer GitHub Actions or the 32GB support VM for heavyweight validation, full suites, matrix tests, long integration tests, and persona sweeps.
- Run local tests only for fast feedback, local-only reproduction, validating unpushed edits, or Mac-only `.app` proof. Use the narrowest focused command first.
- Do not launch multiple heavyweight local suites or persona sweeps in parallel on this Mac.
- If local test work causes memory pressure, stop it, report the command/path, and switch to a narrower check, GitHub CI, or the support VM.

## WorldOS Takeover Truth

- Read `WorldOS-OPERATING-GOAL.md` first, then `WorldOS-GUI-RUNBOOK.md`, `WorldOS-RUNBOOK.md`, and `qa/SCORECARD.md`.
- The product is the launchable, playable `dist/WorldOS.app`. Wrapper/config/test-only progress does not count as product progress unless it directly unlocks built-app gameplay evidence.
- Engine remains sole writer of campaign state. GUI/native app remains a thin reader plus `/move` intent submitter.
- Built-app proof must include visible narration, private art, an active player, enabled actions, accepted `/move`, and `/session-surface` showing the live campaign as actionable.
- The current `qa/RRI.json` from `f5500ac` is partial/harness-contaminated evidence, not a release verdict.
- Release evidence requires the RRI contract in `qa/release_readiness.py`: expected/completed/missing personas, disk-backed scores, behavior/UI/image/palette evidence, same build SHA, and non-partial status.

## Support VM

- Target VM: owner-provided 32GB support VM, `support-vm-1`.
- Connection/auth details are operator-only and should stay outside tracked repo docs.
- Use the support VM for heavy backend/persona sweeps only after Codex CLI credentials/config are intentionally installed and verified there.
- VM preflight must record VM identity, repo checkout path, branch/SHA, Codex CLI version, auth/profile status, `uv`, Node/npm/Playwright availability, private-art status or explicit backend-only/no-art classification, env vars, budget/concurrency cap, teardown commands, and artifact return path under `/Volumes/LEXAR/Codex`.
- The VM cannot prove Mac-only surfaces. `WorldOS.app` build/launch, native #356, and built-app UI play evidence stay on this Mac or macOS CI.
- VM artifacts can feed RRI only when `run.json`, `score.json`, `session_surface.final.json`, network/image evidence, palette-live evidence, and build SHA are explicit. Otherwise the result remains partial/harness-contaminated.

## GEX44 GPU host (evaos-gpu-gex44-1)

- Internal GPU compute host `evaos-gpu-gex44-1` (Hetzner GEX44, RTX 4000 SFF Ada / 64 GB / Ubuntu 24.04) — NOT a customer VM. Supabase source of truth is `fleet_nodes` with `role = gpu_compute`; do not create or use a `gpu_vms` inventory table for this host.
- Operator access is operator-only (outside tracked docs): key `~/.openclaw/secrets/evaos-gpu-gex44-1-key`, connection refs in `~/.openclaw/secrets/gex44.env`.
- **Provisioning is COMPLETE** (verified on-box: the heavy part-B sweep lane, CUDA/local-AI, and the Unity 6000.5.1f1 + Unity-MCP render loop are all proven). GEX44 is now the **preferred** heavy-sweep + Unity/visual-renderer host (the 32 GB support VM is the fallback). Operational details + the connect/capture recipes live in `WorldOS-GUI-RUNBOOK.md` → "GPU-VM lane".
- No customer data, no customer-VM bootstrap, no live Eva/customer runtime use on this host.

## GitHub And Reviews

- Use branch prefix `codex/` for new branches unless instructed otherwise.
- Keep PRs draft until the evidence is honest enough for review.
- If a PR is part of the work, do not end while required checks, review-bot status, or current actionable review threads are unresolved unless the user explicitly asks to pause.
- Keep up with CodeRabbit and GitHub review threads. Verify each comment against the code, fix valid issues, and rerun focused validation before pushing.
- Treat generic warning-only bot suggestions as non-blocking unless they identify a real defect or the repository enforces them.

