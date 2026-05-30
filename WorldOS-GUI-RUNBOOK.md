# WorldOS GUI Runbook — the look-and-wire release loop

> How to test→fix→LOOK the WorldOS GUI on the REAL surface and drive it to a 10/10 release.
> Born from the 2026-05-31 reorientation: the prior loop scored a HEADLESS PROXY served from
> WORKTREES WITH NO ART, so every visible defect (no palette, no images, no map, unformatted
> chronicle, phantom companion) sailed past. This runbook makes that impossible to repeat.
> Companions: `WorldOS-OPERATING-GOAL.md` (the gate), `qa/GUI_WORKBOOK.md` (the live punch-list),
> `qa/release_readiness.py` (the RRI scorer), `qa/SCORECARD.md` (the ledger).

## The two surfaces (never confuse them again)
- **ITERATE — visible, playable, fast:** the OpenWorlds viewer served **from the canonical repo**
  `/Users/lume/ClawDnD-val` (which HAS the 2.9 GB `content/worlds/_private` art) as a LIVE PLAYABLE
  session on **fixed port 8799**. This is where you fix one thing at a time and LOOK.
- **GATE — truth:** the built `dist/WorldOS.app` via `qa/ui_playtest_app.sh` (part A native #356 +
  part B persona loop). Release is judged here. Same viewer code; adds the native shell.
- **Why both:** identical viewer. 8799-from-canonical skips the build + guarantees art is present, so
  it's the honest fast loop. The `.app` is the shipped artifact. NEVER iterate on a worktree-served
  viewer (no `_private` → 100% image 404) or a stale build.

## Stand up the iteration surface (8799, playable, from canonical)
```
cd /Users/lume/ClawDnD-val
git checkout main && git pull --ff-only origin main          # current main (has merged fixes)
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
   `git -C /Users/lume/ClawDnD-val worktree add -B fix/<slug> /tmp/wos-<uniq> origin/main`
3. PR → CI green (incl. `viewer-tests`) → admin-squash-merge → delete branch → prune worktree.
   **Builder PRs sometimes fail to push silently** (happened twice this session) — always
   `gh pr view <n>` / `git ls-remote origin <branch>` to confirm the branch+PR EXIST before relying
   on them; if lost, redo the (usually small) change yourself in a clean worktree.
4. `git pull --ff-only` canonical → restart 8799 → LOOK → tick GUI_WORKBOOK with the proof.

## The gate sweep (Phase 3 — judged on the built .app)
```
# build + 5 personas, SEQUENTIAL (clean host for honest latency), each its own run dir:
for p in newbie veteran adversarial narrative optimizer; do
  WOS_APP_PART=AB qa/ui_playtest_app.sh sweep-$p baldurs-gate $p 40 12.00
done
# 3-lens story/mech on a duo transcript: qa/score.sh <md> <state> rubric_tolkien.md score_schema_tolkien.json out.json ; same w/ rubric_angry_dm.md
# behavioral: python3 qa/assert_behavioral.py <run.jsonl> <state.json>  (exit 0=GREEN)
# GUI health:  qa/ui_audit_health.sh --port 8799 --quick --axe --ui-gate
# palette-live: the curl check above (≥6 enabled actions on a can_act surface)
# roll it up:
python3 qa/release_readiness.py --runs sweep-newbie,sweep-veteran,sweep-adversarial,sweep-narrative,sweep-optimizer \
  --story story.json --mech mech.json --behavioral GREEN|RED --ui-audit PASS|FAIL --palette-live true|false \
  --build-sha $(git rev-parse --short HEAD) --scorecard-row
```
RRI 10/10 = all 11 gates hold on ONE build. Append the `--scorecard-row` line to `qa/SCORECARD.md`.

## Release (when RRI = 10/10 on a fresh .app build)
Bump `.claude-plugin/plugin.json` → 1.0.4, tag `v1.0.4`, GitHub release + CHANGELOG. Then MAINTAIN:
every PR touching `viewer/ | macos/ | skills/ | servers/engine/` → rebuild + RRI sweep + SCORECARD row;
any regression (a critical bug, a sub-7 persona, sub-threshold score, image <95%, dead palette)
reverts the goal to "fix" and outranks new work.

## Hard rules (carried from CLAUDE.md + this session's lessons)
- Engine (`servers/engine`) = SOLE writer of campaign state. Don't touch wire contracts
  (`clawdnd-*`/`CLAWDND_*` MCP ids, `dev.clawdnd.app`); you MAY read `WORLDOS_REPO_ROOT`.
- `_private/` (the 2.9 GB art) is **never committed**. Building/serving from canonical is how the
  art is present.
- 16 GB host: tests on **GitHub CI**, never heavy local suites. Parallel agents are fine.
- **Verify, don't trust:** ≥2 clean reads for any claim; the RRI scorer reads disk, not the live
  channel; confirm builder PRs actually pushed.
- The product is the **launchable, played .app**. A green score on any other surface is a
  measurement bug, not progress.
