# WorldOS GUI Runbook — the look-and-wire release loop

> How to test→fix→LOOK the WorldOS GUI on the REAL surface and drive it to a 10/10 release.
> Born from the 2026-05-31 reorientation: the prior loop scored a HEADLESS PROXY served from
> WORKTREES WITH NO ART, so every visible defect (no palette, no images, no map, unformatted
> chronicle, phantom companion) sailed past. This runbook makes that impossible to repeat.
> Companions: `WorldOS-OPERATING-GOAL.md` (the gate), `qa/GUI_WORKBOOK.md` (the live punch-list),
> `qa/release_readiness.py` (the RRI scorer), `qa/SCORECARD.md` (the ledger).
>
> Takeover note, 2026-05-31: `/Users/lume/ClawDnD-val` is currently the private-art/live-app checkout,
> not the place for tracked takeover edits. Use Lexar worktrees for changes; intentionally fast-forward
> canonical only when the owner chooses to move the live app/art checkout.

## The two surfaces (never confuse them again)
- **ITERATE — visible, playable, fast:** the OpenWorlds viewer served **from the canonical repo**
  `/Users/lume/ClawDnD-val` (which HAS the 2.9 GB `content/worlds/_private` art) as a LIVE PLAYABLE
  session on **fixed port 8799**. This is where you fix one thing at a time and LOOK.
- **GATE — truth:** the built `dist/WorldOS.app` via `qa/ui_playtest_app.sh` (part A native #356 +
  part B persona loop). Release is judged here. Same viewer code; adds the native shell.
- **Why both:** identical viewer. 8799-from-canonical skips the build + guarantees art is present, so
  it's the honest fast loop. The `.app` is the shipped artifact. A Lexar worktree may serve private art
  only when `WORLDOS_ART_REPO_ROOT=/Users/lume/ClawDnD-val` points at the canonical private-art checkout.
  The native app has a separate Private art repo path setting, and `script/build_and_run.sh` also writes
  the art root into `Info.plist` as `WorldOSArtRepoRoot` so LaunchServices env loss cannot hide missing art.

## Stand up the iteration surface (8799, playable, from canonical)
```
cd /Users/lume/ClawDnD-val
# Do not auto-pull during takeover; this checkout holds private art and the live app.
# Verify intentionally before moving it:
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
```
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
2. Builder agent in a **worktree off origin/main** (never branch-op canonical):
   `git -C /Users/lume/ClawDnD-val worktree add -B codex/<slug> /Volumes/LEXAR/repos/wos-<slug> origin/main`
3. PR → CI green (incl. `viewer-tests`) → admin-squash-merge → delete branch → prune worktree.
   **Builder PRs sometimes fail to push silently** (happened twice this session) — always
   `gh pr view <n>` / `git ls-remote origin <branch>` to confirm the branch+PR EXIST before relying
   on them; if lost, redo the (usually small) change yourself in a clean worktree.
4. `git pull --ff-only` canonical → restart 8799 → LOOK → tick GUI_WORKBOOK with the proof.

## The gate sweep (Phase 3 — judged on the built .app)
```
qa/release_gate.sh --personas newbie,veteran,adversarial,narrative,optimizer --budget 12
```
RRI 10/10 = all 11 gates hold on ONE build across the canonical five personas
(`newbie,veteran,adversarial,narrative,optimizer`). The scorer must record
required/expected/completed/missing personas plus explicit evidence gaps, disk-backed behavioral,
UI audit, image denominator/source, palette-live evidence, per-run Part B pass status, and same-build
SHA evidence.
The runtime safety gate includes both critical bug reports and raw console/page errors from the
palette run.
Append the `--scorecard-row` line to `qa/SCORECARD.md` only when the run is not partial/harness-contaminated
and has no evidence gaps.

Non-disruptive Mac smoke during takeover:
```
WORLDOS_NO_STOP_EXISTING=1 \
WORLDOS_ART_REPO_ROOT=/Users/lume/ClawDnD-val \
WORLDOS_PREFER_LAUNCH_ROOTS=1 \
script/build_and_run.sh --verify
```
This proves the worktree-built bundle launches without killing the canonical app. It is only a smoke:
release truth still requires `qa/ui_playtest_app.sh` Part A+B and the full RRI sweep.

## Release (when RRI = 10/10 on a fresh .app build)
Bump `.claude-plugin/plugin.json` → 1.0.4, tag `v1.0.4`, GitHub release + CHANGELOG. Then MAINTAIN:
every PR touching `viewer/ | macos/ | skills/ | servers/engine/` → rebuild + RRI sweep + SCORECARD row;
any regression (a critical bug, a sub-7 persona, sub-threshold score, image <95%, dead palette)
reverts the goal to "fix" and outranks new work.

## Hard rules (carried from CLAUDE.md + this session's lessons)
- Engine (`servers/engine`) = SOLE writer of campaign state. Don't touch wire contracts
  (`clawdnd-*`/`CLAWDND_*` MCP ids, `dev.clawdnd.app`); you MAY read `WORLDOS_ART_REPO_ROOT`.
- `_private/` (the 2.9 GB art) is **never committed**. Building/serving from canonical is how the
  art is present; Lexar worktrees can read it via `WORLDOS_ART_REPO_ROOT=/Users/lume/ClawDnD-val`.
- 16 GB host: tests on **GitHub CI / 32GB VM**, never heavy local suites. Parallel read-only agents are
  fine; do not launch multiple heavyweight persona sweeps locally.
- **Verify, don't trust:** ≥2 clean reads for any claim; the RRI scorer reads disk, not the live
  channel; confirm builder PRs actually pushed.
- The product is the **launchable, played .app**. A green score on any other surface is a
  measurement bug, not progress.
