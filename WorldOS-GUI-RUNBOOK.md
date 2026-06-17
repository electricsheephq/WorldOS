# WorldOS GUI Runbook — the look-and-wire release loop

> How to test→fix→LOOK the WorldOS GUI on the REAL surface and drive it to a 10/10 release.
> Born from the 2026-05-31 reorientation: the prior loop scored a HEADLESS PROXY served from
> WORKTREES WITH NO ART, so every visible defect (no palette, no images, no map, unformatted
> chronicle, phantom companion) sailed past. This runbook makes that impossible to repeat.
> Companions: `WorldOS-OPERATING-GOAL.md` (current truth), `qa/QA_TOOLS.md` (QA command index),
> `docs/AGENT_GRADE_APP_TESTABILITY.md` (app-status/evidence contract), `qa/GUI_WORKBOOK.md`
> (historical punch-list), `qa/release_readiness.py` (the RRI scorer), `qa/SCORECARD.md` (the ledger).
>
> Takeover routing, 2026-06-01: `/Users/lume/WorldOS` is the synced local app/private-art checkout
> and the default place to build/run/test the GUI and native app. The latest same-SHA app proof is
> `da05101` from the 2026-06-07 current-main handoff rerun; later commits may sit above that proof
> without becoming new product proof. Verify `origin/main` before acting, and rerun the handoff gate
> before pairing newer persona artifacts.
> Lexar is for evidence/snapshots/logs, not the default runtime tree, because macOS permission prompts
> can break AI/browser tests when assets live on the external drive. For tracked GUI edits, prefer a
> same-disk local worktree; use Lexar worktrees only for non-GUI slices that will not launch against art.

## Fresh GUI Agent Quick Start

Before a main implementation agent spends time on long persona runs, run the hybrid handoff gate on
the current commit. It catches stale tabs, dead launchers, missing private art, missing actor/actions,
failed `/move`, no narration, console/network errors, provider trace failures, and evidence gaps.

```bash
cd /Users/lume/WorldOS
python3 qa/app_handoff_gate.py \
  --web-beats 5 \
  --built-beats 5 \
  --codex-moves 1 \
  --art-root /Users/lume/WorldOS \
  --scripted-budget 1.00 \
  --codex-budget 3.00 \
  --timeout 90 \
  --codex-timeout 240
```

The run writes `/Volumes/LEXAR/Codex/worldos-agent-grade-app-testability/<run-id>/`. Review
`handoff.json` first, then each gate's `app-evidence/manifest.json`, `app-status.*.json`,
`session-surface.*.json`, screenshots, moves, console/network/action logs, and provider trace summary.
`handoff_score=100` means the GUI wiring loop is trustworthy for implementation velocity. It is not
release-ready evidence by itself.

| Command | Use it for | Do not treat it as |
|---|---|---|
| `scripts/play.sh ... 8799` | Fast local LOOK loop on the canonical repo with private art | Built-app proof |
| `qa/app_handoff_gate.py` | Fast web + built-app scripted smoke + short built-app Codex provider playtest | Full release verdict |
| `qa/ui_playtest_app.sh` | Lower-level native app harness, native Part A+B evidence, failure buckets | Complete five-persona sweep by itself |
| `qa/ui_playtest.sh` | Blind browser persona diagnostics for #324 | Built-app product proof |
| `qa/release_readiness.py --handoff-json ...` | RRI rollup and release verdict when paired with complete persona evidence | A substitute for missing persona artifacts |

| Port / route | Meaning | Guardrail |
|---|---|---|
| `8799 /openworlds/` | Canonical fast iteration surface from `/Users/lume/WorldOS` | Use for LOOK, then rebuild/prove the app |
| `8899 /openworlds/` | Scripted/dev harness default | Valid only when same-port `/app-status` is live |
| `8765` or dynamic app ports | Native app spawned viewer | Read `run.json` or `/app-status.viewer.port`; do not guess |
| `8990-8999` | Browser persona harness range | Diagnostic browser evidence unless paired with app proof |

Handoff requires five enabled actions today because scripted/Codex smoke proves the main play loop.
Release RRI's palette-live gate is stricter: it still requires at least six enabled actions on a
`can_act:true` surface with disk-backed evidence.

## The two surfaces (never confuse them again)
- **ITERATE — visible, playable, fast:** the OpenWorlds viewer served **from the local canonical repo**
  `/Users/lume/WorldOS` (which HAS the 2.9 GB `content/worlds/_private` art) as a LIVE PLAYABLE
  session on **fixed port 8799**. This is where you fix one thing at a time and LOOK.
- **GATE — truth:** the built `dist/WorldOS.app` via `qa/ui_playtest_app.sh` (part A native #356 +
  part B persona loop). Release is judged here. Same viewer code; adds the native shell.
- **Why both:** identical viewer. 8799-from-local skips the build + guarantees art is present, so
  it's the honest fast loop. The `.app` is the shipped artifact. A non-local worktree may serve private art
  only when `WORLDOS_ART_REPO_ROOT=/Users/lume/WorldOS` points at the local private-art checkout, but
  use that as a fallback rather than the default because external-drive file prompts have broken local AI tests.
  The native app has a separate Private art repo path setting, and `script/build_and_run.sh` also writes
  the art root into `Info.plist` as `WorldOSArtRepoRoot` so LaunchServices env loss cannot hide missing art.

## Native provider reality check

- OpenWorlds native-start surfaces now honor the macOS app's selected provider (#472). If the web UI has
  not loaded app status yet, it omits `provider` and lets Swift's `selectedProviderRaw` setting decide.
- The Codex path now has two wrappers: `scripts/play_codex_dm.sh` for the selected provider's DM loop,
  and `scripts/play_codex_actor.sh` for constrained player/companion actor work. Do not swap them.
- Provider configuration is model-family aware. Settings and `/app-status` expose provider family,
  auth surface, DM model, QA player model, QA scorer model, command override, readiness, and detected
  CLI path. Claude keeps `opus`/`sonnet` defaults; Codex keeps `gpt-5.5` defaults. A missing unselected
  provider is not a product blocker.
- Same-family proof is required for provider claims: Anthropic provider/player/scorer for Anthropic
  proof, Codex/OpenAI provider/player/scorer for Codex proof. Mixed Sonnet/GPT runs are cross-provider
  benchmarks only, not release evidence.
- App evidence manifests must include `provider_family`, `dm_model`, `player_agent`, `player_model`,
  `scorer_provider`, and `scorer_model`. Missing provider/model fields make the handoff/RRI result
  partial until the evidence is rerun.
- Do not treat the wrapper as release proof by itself. The 2026-06-01T04:39:09+07:00 pre-merge built-app proof
  (`/Volumes/LEXAR/Codex/worldos-built-app-playtest/codex-app-headproof-20260601T043909/`) showed the Codex-DM
  path could mint a live native session, load private BG art, seat Alfira, show narration, expose five enabled
  actions, accept and resolve a `/move`, leave `/session-surface` actionable, and produce a provider trace
  with zero errors/failed tool calls on PR #475 app-code commit `8bd833f`.
- The post-#475 merged-main built-app proof
  (`/Volumes/LEXAR/Codex/worldos-built-app-playtest/post475-main-app-proof-20260601T051230/`, build `32ca561`)
  was player-playable, but provider trace noise persisted. It is historical playable evidence, superseded for
  #479 closure by the `f7ab6d7` merged-main proof below. Release still requires the full non-partial RRI gate.
- The current-main built-app proof
  (`/Volumes/LEXAR/Codex/worldos-built-app-playtest/codex-current-main-proof-20260531T234242Z/`, build `19c3fd0`)
  again proved product wiring: private art present, Codex provider, Alfira active, visible narration, five
  enabled actions, writable `/move`, one accepted move, chat roles `dm, player, dm`, and `/session-surface`
  still actionable. The provider trace still had three failed/cancelled engine tool calls, so it is historical
  non-clean evidence, superseded for #479 closure by the `f7ab6d7` proof below.
- The #479 trace-clean branch proof
  (`/Volumes/LEXAR/Codex/worldos-built-app-playtest/codex-479-traceclean-nodup-proof-20260601T003002Z/`, app-code
  `b081092`) reran the built app with private art, Codex provider, Alfira active, five enabled actions,
  a writable `/move`, one accepted/resolved player move, chat roles `dm, player, dm`, and `/session-surface`
  still actionable. `app-evidence/manifest.json` had no gaps and `provider-errors.after-move.json` reported
  zero parse errors plus zero failed/error tool calls. Native accessibility review showed exactly one opening
  narration row and one follow-up narration row, confirming engine-logged `/chat` rows resolve turns without
  duplicating visible prose.
- The merged-main #479 proof
  (`/Volumes/LEXAR/Codex/worldos-built-app-playtest/codex-main-f7ab6d7-proof-20260601T010058Z/`, build
  `f7ab6d7`) repeated the proof on `main`: private art present, Codex provider, Alfira active, five enabled
  actions, writable `/move`, one accepted/resolved player move, chat roles `dm, player, dm`, and
  `/session-surface` still actionable. `app-evidence/manifest.json` had no gaps and
  `provider-errors.after-move.json` reported zero parse errors plus zero failed/error tool calls. Native
  accessibility review showed the chronicle with one opening narration row and one follow-up narration row,
  not duplicate chat/event prose. This closes the #479 diagnostic blocker, but release still requires #466's
  full non-partial RRI gate.
- The current-main handoff gate
  (`/Volumes/LEXAR/Codex/worldos-agent-grade-app-testability/handoff-20260607-da05101-current-main-clean/`,
  build `da05101`) is the current fastest GUI trust proof. It scored `handoff_score=100` with web-scripted
  smoke 5 moves, built `dist/WorldOS.app` scripted smoke 5 moves, and built `dist/WorldOS.app`
  Codex-provider playtest 1 move. All three evidence manifests passed with zero gaps, private art present,
  screenshots, app-status/session-surface snapshots, move logs, provider trace, console/network/action logs,
  and failure bucket fields. The Codex trace summary reported `trace_exists=true`, `line_count=350`, and
  `failed_or_error_count=0`. `validate_handoff_json(..., "da05101")` returned `valid=True`, `gaps=0`.
  The first canonical checkout attempt
  (`/Volumes/LEXAR/Codex/worldos-agent-grade-app-testability/handoff-20260607-da05101-current-main/`)
  passed the product gates but is not accepted as release evidence because unrelated local untracked files
  made the checkout dirty. The clean same-disk worktree proof above supersedes the `9545383`, `fd9dba5`,
  and `4a0efe1` handoffs as current proof. If #466 persona artifacts are produced from a newer SHA, rerun
  the Mac handoff on that same SHA before RRI rollup. It is the fast GUI velocity gate, not the release verdict.
- The post-#508 handoff gate
  (`/Volumes/LEXAR/Codex/worldos-agent-grade-app-testability/handoff-20260601T100304Z-9545383/`, build
  `9545383`) was the prior fastest GUI trust proof. It scored `handoff_score=100` with web-scripted smoke
  5 moves, built `dist/WorldOS.app` scripted smoke 5 moves, and built `dist/WorldOS.app` Codex-provider
  playtest 1 move. All three evidence manifests passed with zero gaps, private art present, screenshots,
  app-status/session-surface snapshots, move logs, provider trace, console/network/action logs, and failure
  bucket fields. The Codex trace summary reported `trace_exists=true`, `line_count=80`, and
  `failed_or_error_count=0`. `validate_handoff_json(..., "9545383")` returned `valid=True`, `gaps=0`.
  This is now historical and superseded by the `da05101` current-main handoff above.

## Agent-facing app contract

- `GET /app-status` and `GET /__worldos/app-status.json` are read-only probes for agents and harnesses.
  They report build/version, viewer port, state root, provider, private-art root presence, live campaign/run,
  move sink, active actor, enabled actions, and canonical endpoints. They must not mutate campaign state.
- Use `/app-status` before screenshots when diagnosing the built app. It answers: "am I on the real live
  campaign, can the player act, where is the move sink, and is private art configured?"
- `qa/ui_playtest_app.sh` captures launcher and minted-provider `app-status` JSON into the native evidence
  folder. A built-app proof that cannot produce this status object is a harness/product observability failure.
- Agent-grade testing progress as of `da05101`: #481 app-status is closed, #482 deterministic scripted
  provider is merged, #483 failure buckets are merged, #484 stable accessibility/DOM hooks are merged,
  #504's hybrid handoff gate is merged and green on `main`, #505's RRI handoff bridge is merged, and
  #508's support-VM preflight artifact gate is merged. #485 evidence bundle completion and #486 gate-split
  follow-through are closed in GitHub; #466 remains the release gate and #467 remains the UX-first sprint.
  A scripted `:8899` harness surface can prove app
  observability, but it is not built-app release proof unless it came from `dist/WorldOS.app` /
  `qa/ui_playtest_app.sh`.

## Stand up the iteration surface (8799, playable, from canonical)
```bash
cd /Users/lume/WorldOS
# This is the intended local app checkout. Verify it is synced before testing:
git rev-parse --short HEAD && git rev-parse --short origin/main
pkill -f 'viewer/server.py'; pkill -f 'scripts/play.sh'; pkill -f 'play_party.sh'   # NOT node:18789 (Eva gateway)
CLAWDND_PLAY_PORT=8799 nohup bash scripts/play.sh baldurs-gate preview-$(git rev-parse --short HEAD) 8799 > /tmp/wos-8799.log 2>&1 &
# play.sh sets CLAWDND_PLAYER_MOVES → can_act:true (the move sink = the palette is live)
```
Open `http://127.0.0.1:8799/openworlds/`. The DM cold-open takes ~30–90s; **wait for a SEATED PC**
(party non-empty), not just `can_act:true` — `can_act` can flip true before the PC is seated.

Ad-hoc harness ports such as `8899` are allowed for agent-grade smoke/debugging only when `/app-status`
identifies the build SHA, provider, repo root, art root, move sink, and readiness. Do not confuse a healthy
`:8899` scripted-provider surface with a current built `.app` proof, and do not assume the port will still
be alive after the harness tears down.

If the in-app browser is available, point it at the discovered live port from `qa/ui_playtest_app.sh`
`run.json` or `/app-status.viewer.port` instead of guessing `:8899`. Browser evidence is diagnostic unless
the port came from the built app launch path and the proof bundle also contains `/app-status`,
`/session-surface`, move/chat/provider artifacts, and a built-app screenshot.

## LOOK (verify by curl + screenshot — NEVER a single Read; the channel fabricates)
The tool channel intermittently returns fabricated/empty/doubled reads (this session it invented a
`kind=pc` palette-disabled bug and a scene-404 that were both false). **Ground every load-bearing
claim in ≥2 clean reads + a checksum/HTTP code.**
```bash
curl -s http://127.0.0.1:8799/session-surface | python3 -c 'import json,sys;d=json.load(sys.stdin); \
  print("party",[ (p["name"],p.get("kind")) for p in d.get("party",[])]); \
  print("palette",[a["id"] for a in d.get("availableActions",[]) if a.get("available")]); \
  print("can_act",d.get("can_act"))'
# images: curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8799/image?scope=location:loc-lower-city"
```
Per-fix visual checklist: palette buttons present + enabled in the MAIN column · a click resolves a
turn · portraits/scene/map images 200 · a multi-paragraph DM beat renders as paragraphs · prose
streams mid-turn (`/events` count climbs during the turn) · a SOLO session has the PC alone.

## Fix one thing → PR → merge → rebuild → LOOK (the loop)
1. Confirm the symptom on 8799 with ≥2 clean reads. If it doesn't reproduce, it's a stale/corrupt
   read — do NOT fix it (log to GUI_WORKBOOK "evaporated").
2. Builder agent in a **same-disk local worktree off origin/main** when GUI/app tests need art:
   `git -C /Users/lume/WorldOS worktree add -B codex/<slug> /Users/lume/WorldOS-worktrees/wos-<slug> origin/main`
   Lexar worktrees remain fine for docs/backend/non-GUI slices that do not launch the viewer/app.
3. PR → CI green (incl. `viewer-tests`) → admin-squash-merge → delete branch → prune worktree.
   **Builder PRs sometimes fail to push silently** (happened twice this session) — always
   `gh pr view <n>` / `git ls-remote origin <branch>` to confirm the branch+PR EXIST before relying
   on them; if lost, redo the (usually small) change yourself in a clean worktree.
4. `git pull --ff-only` local canonical → restart 8799 → LOOK → tick GUI_WORKBOOK with the proof.

## The gate sweep (Phase 3 — judged on the built .app)
```bash
WORLDOS_ART_REPO_ROOT=/Users/lume/WorldOS \
qa/release_gate.sh --personas newbie,veteran,adversarial,narrative,optimizer --budget 12 --port 8785
```
RRI 10/10 = all 11 gates hold on ONE build across the canonical five personas
(`newbie,veteran,adversarial,narrative,optimizer`). The scorer must record
required/expected/completed/missing personas plus explicit evidence gaps, disk-backed behavioral,
UI audit, image denominator/source, palette-live evidence, per-run Part B pass status, and same-build
SHA evidence.
The runtime safety gate includes both critical bug reports and raw console/page errors from the
palette run.
Append every `--scorecard-row` line to the ledger (`qa/scores_db.py` → `qa/scores_ledger.md`; `qa/SCORECARD.md`
is legacy) as diagnostic release evidence. Only a non-partial, non-harness-contaminated 10/10 row with no
evidence gaps can count as release evidence.

### Reading the sweep — honest satisfaction (finish vs give_up vs derived)
- **Two clean endings, don't collapse them (#574):** `finish(satisfaction, verdict)` = a SATISFIED end
  (`gave_up=false` + a self-reported 1–10 — **the ONLY path that clears G3 ≥7**); `give_up` = a genuine BLOCK
  (dead control / error / DM stalled with no narration).
- **The budget is STORY BEATS, not wall-clock.** A persona's clicks/types/WAITS within a beat are free; a rich
  beat is ~100–126s of DM *reasoning* (the spinner / streaming narration is PROGRESS, not a hang — #571). A slow
  beat is not a give-up.
- **Every persona MUST end via `finish(satisfaction, verdict)`.** If it just stops, the scorer DERIVES sat
  (`8 − friction`, capped < 7 once any friction lands), so the gate reads artificially low. A `gave_up=false` +
  `arc=true` + *derived* 5–6 run = "persona didn't self-report," NOT a dissatisfied player (2026-06-02 VM sweep:
  4/5 derived — a self-report-coverage artifact, not a quality verdict). Read `satisfaction_source`; a derived
  G3-miss is inconclusive.
- **PARTIAL ≠ product RED.** A host-memory crater (only persona-1 scored) or a hung/incomplete rollup (e.g. the
  duo step stalls) is harness-contaminated — re-run via the VM lane; never cite a PARTIAL RRI as a quality score.

## macOS privacy prompt triage

During local proof runs, a macOS Photos/Music prompt can be a **test-process attribution artifact**:
TCC may name the frontmost WorldOS app as `responsible` even when the actual `accessing` process is a
diagnostic command such as `/usr/bin/find` or `codex`. Before filing this as a product blocker, inspect
the attribution:

```bash
/usr/bin/log show --style compact --last 10m \
  --predicate 'eventMessage CONTAINS[c] "dev.clawdnd" OR eventMessage CONTAINS[c] "kTCCServicePhotos" OR eventMessage CONTAINS[c] "kTCCServiceMediaLibrary"'
```

If `AUTHREQ_ATTRIBUTION` shows `accessing=/usr/bin/find` or `accessing=codex`, classify it as harness
contamination and rerun proof without broad filesystem scans while the app is frontmost. If it shows
`WorldOSApp` or a WebKit child process directly accessing a protected Photos/Music path, treat it as a
release-blocking product bug.

Non-disruptive Mac smoke during takeover:
```bash
WORLDOS_NO_STOP_EXISTING=1 \
WORLDOS_ART_REPO_ROOT=/Users/lume/WorldOS \
WORLDOS_PREFER_LAUNCH_ROOTS=1 \
script/build_and_run.sh --verify
```
This proves the local/worktree-built bundle launches without killing an existing app. It is only a smoke:
release truth still requires `qa/ui_playtest_app.sh` Part A+B and the full RRI sweep.

## Support VM lane (heavy sweeps, not Mac-only app truth)

- Target: owner-provided **32GB support VM** (`support-vm-1`); connection/auth details live in local
  operator-only runbooks/evidence, not tracked repo docs.
- Do not assume it is ready for Codex runs until credentials/config are intentionally installed and verified.
  The default support-VM persona lane is Codex DM plus Codex UI player; Claude is only required when
  the preflight is run with `--provider claude` or `--player-agent claude`. The Codex lane requires
  Codex CLI `>=0.120.0` because it uses per-invocation `codex exec -c mcp_servers.*` overrides rather
  than mutating `CODEX_HOME` with `codex mcp add`.
- Use it for heavy backend/persona release sweeps and parallel QA once configured.
- Do **not** use it as proof for Mac-only surfaces: `WorldOS.app` build/launch, native #356, and built-app
  UI play evidence stay on this Mac or macOS CI.
- VM preflight before any RRI sweep: record VM identity, repo checkout path, branch/SHA, Codex CLI version,
  GitHub `origin/main` queryability, auth/profile status, `uv`, Node/npm/Playwright/Chromium availability,
  private-art availability or explicit backend-only/no-art classification, env vars, budget/concurrency cap,
  teardown commands, and the artifact return path under `/Volumes/LEXAR/Codex`. Use the repo-owned preflight
  artifact writer before #466:
  ```bash
  python3 qa/support_vm_preflight.py \
    --repo /root/worldos-qa/WorldOS \
    --expected-sha da05101 \
    --provider codex \
    --player-agent codex \
    --art-root /root/worldos-qa/WorldOS \
    --private-art-mode required \
    --artifact-dir /tmp/worldos-support-vm-preflight-da05101 \
    --artifact-return-target /Volumes/LEXAR/Codex/worldos-support-vm-rri/da05101-preflight
  ```
  The script is read-only with respect to WorldOS state; it writes `support_vm_preflight.json` and
  `support_vm_preflight.md`, redacts secrets, and exits non-zero if same-SHA/origin/tool/auth/private-art
  blockers would make the RRI sweep untrustworthy. Its generated persona commands must include both
  `WOS_APP_SELECTED_PROVIDER` and `WOS_APP_PLAYER_AGENT`; otherwise the VM sweep lane is not defined
  tightly enough to count toward #466.
- **⚠ `IS_SANDBOX=1` is MANDATORY for the claude lane (the VM runs as root):** `claude -p
  --permission-mode bypassPermissions` is REFUSED as root → silent empty-turn abort. `sweep_v2.sh` sets it;
  a standalone `run_duo.sh`/`play.sh` needs `IS_SANDBOX=1 bash qa/...`. (This — not say()-into-void — was the
  duo's beat-0 blocker, 2026-06-03.) Art is at `content/worlds/_private/baldurs-gate/images`, NOT top-level
  `_private`. **The full ONE-COMMAND part-B sweep + exact step-by-step is in the `worldos-dev` skill →
  "VM GATE SWEEP — exact procedure"; keep that section and this one in sync as the harness changes.**
- **VM status UPDATE (2026-06-03 — supersedes the stale 2026-06-01 scout below):** the VM is READY — git-fetch
  from origin WORKS now (the old "could not read Username" failure is resolved), claude 2.1.158 is authed,
  codex 0.120.0 present, art present, ~28 GB free. The 2026-06-01 "stale `4524b3e` / sync-failed / Lexar-absent"
  blockers no longer hold; the heavy part-B `sweep_v2.sh` lane is runnable.
- **VM status UPDATE (2026-06-07 — current `da05101` staging):** the repo-owned preflight artifact at
  `/Volumes/LEXAR/Codex/worldos-support-vm-rri/da05101-preflight/` returned `verdict=blocked`, so no
  personas were run. The VM could query GitHub `origin/main` as `da05101`, but its local repo HEAD/local
  `origin/main` were still `e5c0a5f`; Codex CLI auth/profile was not proven; and #466 release-RRI readiness
  requires rerunning with `--private-art-mode required`. Sync/fetch the VM checkout, prove Codex auth/profile,
  rerun the preflight with required art, then run the five-persona sweep only if that artifact says ready.
- Read-only VM scout (2026-06-01): an operator-only endpoint note can reach `evaos-support` without printing
  the endpoint. Capacity/tooling look suitable for heavy sweeps: ~32 GB RAM, 16 CPUs, ~537 GB free disk, `git`,
  `python3`, `uv 0.11.17`, Node `v22.22.1`, npm `10.9.4`, `codex-cli 0.120.0`, Playwright modules, and private
  art. The VM WorldOS checkout at `/root/worldos-qa/WorldOS` is clean but stale at `4524b3e` and behind
  the `9545383` proof baseline; `git` cannot query/sync the HTTPS origin in batch mode; Codex auth/config is
  not proven; `/Volumes/LEXAR/Codex` does not exist on the VM. Before #466, approve/sync the VM checkout, prove
  Codex auth, make `origin/main` queryable from the VM, set a remote staging path, and copy artifacts back to
  local Lexar.
- RRI rollup rule: Mac/local evidence supplies native Part A and built-app screenshots; VM artifacts can supply
  persona, behavior, image/network, palette-live, and score evidence only when `run.json`, `score.json`,
  `session_surface.final.json`, `network.ndjson`, and build SHA are present. Missing or mixed-SHA artifacts
  must remain `partial` / `harness_contaminated`.
- Split Mac/VM rollup command shape: pass the Mac proof into RRI as
  `--handoff-json /Volumes/LEXAR/Codex/worldos-agent-grade-app-testability/handoff-20260607-da05101-current-main-clean/handoff.json`
  alongside VM persona run dirs from the same `da05101` SHA. RRI should satisfy the native gate from the
  Mac handoff bundle only if all required handoff gates and manifests are same-SHA, clean,
  private-art-present, and gap-free. If the VM runs a newer SHA, rerun `qa/app_handoff_gate.py` on that
  newer SHA first.

## Release (when RRI = 10/10 on a fresh .app build)
Bump `.claude-plugin/plugin.json` → 1.0.4, tag `v1.0.4`, GitHub release + CHANGELOG. Then MAINTAIN:
every PR touching `viewer/ | macos/ | skills/ | servers/engine/` → rebuild + RRI sweep + SCORECARD row;
any regression (a critical bug, a sub-7 persona, sub-threshold score, image <95%, dead palette)
reverts the goal to "fix" and outranks new work.

## Hard rules (carried from CLAUDE.md + this session's lessons)
- Engine (`servers/engine`) = SOLE writer of campaign state. Don't touch wire contracts
  (`clawdnd-*`/`CLAWDND_*` MCP ids, `dev.worldos.app`); you MAY read `WORLDOS_ART_REPO_ROOT`.
- `_private/` (the 2.9 GB art) is **never committed**. Building/serving from the local checkout is how the
  art is present; worktrees can read it via `WORLDOS_ART_REPO_ROOT=/Users/lume/WorldOS` when needed.
- 16 GB Mac: tests on **GitHub CI / 32GB support VM** for heavyweight sweeps, never heavy local suites. Parallel read-only agents are
  fine; do not launch multiple heavyweight persona sweeps locally.
- **Verify, don't trust:** ≥2 clean reads for any claim; the RRI scorer reads disk, not the live
  channel; confirm builder PRs actually pushed.
- The product is the **launchable, played .app**. A green score on any other surface is a
  measurement bug, not progress.
