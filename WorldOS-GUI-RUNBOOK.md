# WorldOS GUI Runbook — the look-and-wire release loop

> How to test→fix→LOOK the WorldOS GUI on the REAL surface and drive it to a 10/10 release.
> Born from the 2026-05-31 reorientation: the prior loop scored a HEADLESS PROXY served from
> WORKTREES WITH NO ART, so every visible defect (no palette, no images, no map, unformatted
> chronicle, phantom companion) sailed past. This runbook makes that impossible to repeat.
> Companions: `WorldOS-OPERATING-GOAL.md` (the gate), `qa/GUI_WORKBOOK.md` (the live punch-list),
> `qa/release_readiness.py` (the RRI scorer), `qa/SCORECARD.md` (the ledger).
>
> Takeover routing, 2026-05-31: `/Users/lume/ClawDnD-val` is the synced local app/private-art checkout
> (`6e03da4 == origin/main` after #473) and the default place to build/run/test the GUI and native app.
> Lexar is for evidence/snapshots/logs, not the default runtime tree, because macOS permission prompts
> can break AI/browser tests when assets live on the external drive. For tracked GUI edits, prefer a
> same-disk local worktree; use Lexar worktrees only for non-GUI slices that will not launch against art.

## The two surfaces (never confuse them again)
- **ITERATE — visible, playable, fast:** the OpenWorlds viewer served **from the local canonical repo**
  `/Users/lume/ClawDnD-val` (which HAS the 2.9 GB `content/worlds/_private` art) as a LIVE PLAYABLE
  session on **fixed port 8799**. This is where you fix one thing at a time and LOOK.
- **GATE — truth:** the built `dist/WorldOS.app` via `qa/ui_playtest_app.sh` (part A native #356 +
  part B persona loop). Release is judged here. Same viewer code; adds the native shell.
- **Why both:** identical viewer. 8799-from-local skips the build + guarantees art is present, so
  it's the honest fast loop. The `.app` is the shipped artifact. A non-local worktree may serve private art
  only when `WORLDOS_ART_REPO_ROOT=/Users/lume/ClawDnD-val` points at the local private-art checkout, but
  use that as a fallback rather than the default because external-drive file prompts have broken local AI tests.
  The native app has a separate Private art repo path setting, and `script/build_and_run.sh` also writes
  the art root into `Info.plist` as `WorldOSArtRepoRoot` so LaunchServices env loss cannot hide missing art.

## Native provider reality check

- OpenWorlds native-start surfaces now honor the macOS app's selected provider (#472). If the web UI has
  not loaded app status yet, it omits `provider` and lets Swift's `selectedProviderRaw` setting decide.
- The Codex path now has two wrappers: `scripts/play_codex_dm.sh` for the selected provider's DM loop,
  and `scripts/play_codex_actor.sh` for constrained player/companion actor work. Do not swap them.
- Do not treat the wrapper as release proof by itself. The 2026-06-01T03:40:26+07:00 local built-app proof
  (`/Volumes/LEXAR/Codex/worldos-built-app-playtest/codex-app-final-20260601T033714/`) shows the Codex-DM
  path can mint a live native session, load private BG art, seat Alfira, show narration, expose five enabled
  actions, accept and resolve a `/move`, and leave `/session-surface` actionable on exact PR #475 head
  `500c379`. Release still requires the full non-partial RRI gate, and #479 remains the next provider-noise
  fix before trusting RRI latency/playability evidence.

## Stand up the iteration surface (8799, playable, from canonical)
```bash
cd /Users/lume/ClawDnD-val
# This is the intended local app checkout. Verify it is synced before testing:
git rev-parse --short HEAD && git rev-parse --short origin/main
pkill -f 'viewer/server.py'; pkill -f 'scripts/play.sh'; pkill -f 'play_party.sh'   # NOT node:18789 (Eva gateway)
CLAWDND_PLAY_PORT=8799 nohup bash scripts/play.sh baldurs-gate preview-$(git rev-parse --short HEAD) 8799 > /tmp/wos-8799.log 2>&1 &
# play.sh sets CLAWDND_PLAYER_MOVES → can_act:true (the move sink = the palette is live)
```
Open `http://127.0.0.1:8799/openworlds/`. The DM cold-open takes ~30–90s; **wait for a SEATED PC**
(party non-empty), not just `can_act:true` — `can_act` can flip true before the PC is seated.

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
   `git -C /Users/lume/ClawDnD-val worktree add -B codex/<slug> /Users/lume/WorldOS-worktrees/wos-<slug> origin/main`
   Lexar worktrees remain fine for docs/backend/non-GUI slices that do not launch the viewer/app.
3. PR → CI green (incl. `viewer-tests`) → admin-squash-merge → delete branch → prune worktree.
   **Builder PRs sometimes fail to push silently** (happened twice this session) — always
   `gh pr view <n>` / `git ls-remote origin <branch>` to confirm the branch+PR EXIST before relying
   on them; if lost, redo the (usually small) change yourself in a clean worktree.
4. `git pull --ff-only` local canonical → restart 8799 → LOOK → tick GUI_WORKBOOK with the proof.

## The gate sweep (Phase 3 — judged on the built .app)
```bash
qa/release_gate.sh --personas newbie,veteran,adversarial,narrative,optimizer --budget 12
```
RRI 10/10 = all 11 gates hold on ONE build across the canonical five personas
(`newbie,veteran,adversarial,narrative,optimizer`). The scorer must record
required/expected/completed/missing personas plus explicit evidence gaps, disk-backed behavioral,
UI audit, image denominator/source, palette-live evidence, per-run Part B pass status, and same-build
SHA evidence.
The runtime safety gate includes both critical bug reports and raw console/page errors from the
palette run.
Append every `--scorecard-row` line to `qa/SCORECARD.md` as diagnostic release evidence. Only a
non-partial, non-harness-contaminated 10/10 row with no evidence gaps can count as release evidence.

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
WORLDOS_ART_REPO_ROOT=/Users/lume/ClawDnD-val \
WORLDOS_PREFER_LAUNCH_ROOTS=1 \
script/build_and_run.sh --verify
```
This proves the local/worktree-built bundle launches without killing an existing app. It is only a smoke:
release truth still requires `qa/ui_playtest_app.sh` Part A+B and the full RRI sweep.

## Support VM lane (heavy sweeps, not Mac-only app truth)

- Target: owner-provided **32GB support VM** (`support-vm-1`); connection/auth details live in local
  operator-only runbooks/evidence, not tracked repo docs.
- Do not assume it is ready for Codex runs until credentials/config are intentionally installed and verified.
- Use it for heavy backend/persona release sweeps and parallel QA once configured.
- Do **not** use it as proof for Mac-only surfaces: `WorldOS.app` build/launch, native #356, and built-app
  UI play evidence stay on this Mac or macOS CI.
- VM preflight before any RRI sweep: record VM identity, repo checkout path, branch/SHA, Codex CLI version,
  auth/profile status, `uv`, Node/npm/Playwright availability, private-art availability or explicit
  backend-only/no-art classification, env vars, budget/concurrency cap, teardown commands, and the artifact
  return path under `/Volumes/LEXAR/Codex`.
- RRI rollup rule: Mac/local evidence supplies native Part A and built-app screenshots; VM artifacts can supply
  persona, behavior, image/network, palette-live, and score evidence only when `run.json`, `score.json`,
  `session_surface.final.json`, `network.ndjson`, and build SHA are present. Missing or mixed-SHA artifacts
  must remain `partial` / `harness_contaminated`.

## Release (when RRI = 10/10 on a fresh .app build)
Bump `.claude-plugin/plugin.json` → 1.0.4, tag `v1.0.4`, GitHub release + CHANGELOG. Then MAINTAIN:
every PR touching `viewer/ | macos/ | skills/ | servers/engine/` → rebuild + RRI sweep + SCORECARD row;
any regression (a critical bug, a sub-7 persona, sub-threshold score, image <95%, dead palette)
reverts the goal to "fix" and outranks new work.

## Hard rules (carried from CLAUDE.md + this session's lessons)
- Engine (`servers/engine`) = SOLE writer of campaign state. Don't touch wire contracts
  (`clawdnd-*`/`CLAWDND_*` MCP ids, `dev.clawdnd.app`); you MAY read `WORLDOS_ART_REPO_ROOT`.
- `_private/` (the 2.9 GB art) is **never committed**. Building/serving from the local checkout is how the
  art is present; worktrees can read it via `WORLDOS_ART_REPO_ROOT=/Users/lume/ClawDnD-val` when needed.
- 16 GB Mac: tests on **GitHub CI / 32GB support VM** for heavyweight sweeps, never heavy local suites. Parallel read-only agents are
  fine; do not launch multiple heavyweight persona sweeps locally.
- **Verify, don't trust:** ≥2 clean reads for any claim; the RRI scorer reads disk, not the live
  channel; confirm builder PRs actually pushed.
- The product is the **launchable, played .app**. A green score on any other surface is a
  measurement bug, not progress.
