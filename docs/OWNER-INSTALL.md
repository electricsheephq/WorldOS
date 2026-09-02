# WorldOS owner install (this Mac)

This kit installs the ad-hoc-signed, unnotarized demo player at `~/Applications/WorldOSPlayer.app`. It serves `adventure_demo_v1` from the one pinned checkout `/Users/m1/worldos-owner`, with engine port 8776 and player QA port 8981. It never uses 8766, 8971, 8866, or 8972.

Three LaunchAgents, not two. `org.worldos.owner-session` is the viewer/engine, `org.worldos.owner-player` is the Unity player, and `org.worldos.owner-dm` runs `qa/agent_play.sh serve --run owner --engine http://127.0.0.1:8776 --state <state> --campaign adventure_demo_v1` — the DM beat loop. The viewer resolves only grid, doorway, parley-approach and combat intents in process; `say`, `do`, `check` and `save` are appended to `WORLDOS_PLAYER_MOVES` for a DM to answer, so without the third agent the owner's dialogue queues forever and the quest cannot progress. `install` and `refresh` refuse while that script has no `serve` mode. The loop itself landed in #1750.

Two seams that must not drift:

- `agent_play.sh serve` derives its chat path as `<state-dir>/chat.jsonl`, so the session agent sets `WORLDOS_VIEWER_CHAT` to exactly that file. A viewer writing `chat.json` leaves the DM tailing a file nobody writes and every owner line goes unanswered.
- `WORLDOS_AGENT_PLAY_ROOT` points the run dir at `<state-dir>/agent_play_runs` instead of its `<repo>/qa/agent_play_runs` default. That run dir holds the durable chat cursor, so it belongs beside the state the receipt backs up, not inside the pinned checkout that `refresh --sha` moves.

## Flow

1. `qa/owner_install.sh preflight /absolute/WorldOSPlayer.app` runs all refuse-on-red gates without writing.
2. `qa/owner_install.sh dry-run /absolute/WorldOSPlayer.app --stage /absolute/evidence/stage` writes only the three plists plus `install-ledger.json` under the stage directory.
3. The owner runs `qa/owner_install.sh install /absolute/WorldOSPlayer.app [--sha COMMIT] [--build-sha COMMIT]`. It **stops the agents first**, then backs up app, state, all three plists and the pinned worktree sha, installs and ad-hoc signs the app, pins `/Users/m1/worldos-owner`, seeds `owner_demo`, installs the LaunchAgents, and starts them in order. Do not run this from a QA lane.

Stopping before any write is load-bearing: a viewer still accepting `POST /move` can interleave a move with the reseed, or land one after the backup was taken.

### Start order

`viewer/server.py` has no `/health` route — `do_GET` 404s anything it does not name — so readiness is `GET /session-surface` returning 200. Only the session plist sets `RunAtLoad`; the player and DM are started by the installer, because a player that boots beside the engine self-exits against an unavailable engine (#1612).

1. bootstrap `owner-session`, poll `/session-surface` for 200 (90 s).
2. kickstart `owner-player`, poll `POST /debug` on 8981 for 200 (60 s) — Unity binds that port only once the player is up, so a single curl races normal startup latency.
3. remove any stale DM heartbeat, kickstart `owner-dm`, and require its new
   `<state>/agent_play_runs/owner/serve.heartbeat` within 60 s. The serve loop refreshes it every
   poll; refusal prints the tail of `<state>/owner-dm{,.err}.log` so auth/model-open failures are visible.
4. prove the campaign was **consumed**, not merely served: after `/debug` first answers, poll for up
   to 180 s until `surf > 0` and `plateLocMatch == true`, then require `/session-surface`
   `campaign_id == adventure_demo_v1` and `camOrtho` equal to that location's `cameraPin.ortho` in
   `extensions/renderers/unity/plates_manifest.json`. The result and elapsed seconds are written to
   the install ledger.

Refuse-on-red means packaged pins GREEN, zero `KitRoom_` strings in `level0`, FRESH crypt and tavern certifications, and a build identity that is either `--build-sha` or a sibling `build-report.txt` **stamped `result=Succeeded`**. `BuildMacOSPlayer.StampFailedReport` writes a nonempty `result=Failed` report beside a possibly stale app, so nonempty is not identity. Any RED or ERROR exits 1 before a write.

## Refresh and removal

- `qa/owner_install.sh refresh --sha COMMIT [--reseed]` stops all three agents, verifies the new commit is a fast-forward of the pinned checkout, moves it detached, optionally reseeds, then repeats the ordered start and the consumption proof. Every reseed archives the prior `agent_play_runs/owner` directory and `chat.jsonl` with one UTC timestamp before starting a fresh DM context/cursor.
- `qa/owner_install.sh status` prints each LaunchAgent, the explicit-port `/session-surface` and `/debug` codes, and DM heartbeat presence/path.
- `qa/owner_install.sh uninstall` removes all three agents/plists but keeps app and state. `--purge` also removes the exact installed app and owner state paths.

## Rollback

Install prints ONE copy-pasteable line that yields a **running** installation:

```
qa/owner_install.sh restore /Users/m1/Codex/session-notes/<UTC-date>/worldos-refresh/artifacts/owner-install/backup-<TS>
```

`restore` stops the agents, puts back the app and the state dir, checks the pinned worktree back out at the sha recorded in `restore.json`, reinstalls the plists the receipt replaced (falling back to the ones it installed, so a first-ever install still restores to a bootable set), then runs the same ordered start and consumption proof. Backups and ledgers live under `/Users/m1/Codex/session-notes/<UTC-date>/worldos-refresh/artifacts/owner-install/`.

## Traps checklist

- [ ] T1: after `ditto`, clear quarantine and ad-hoc codesign before launch.
- [ ] T2: never blind-start both; the session must answer `/session-surface` 200 before the player is kickstarted, and the player's 8981 listener must answer before it is probed.
- [ ] T3: reseed and serve from the same pinned checkout; refresh must be fast-forward safe.
- [ ] T4: QA probes use `127.0.0.1`, never `localhost`.
- [ ] T5: numbered `wos_shot_*.png` is current; `wos_shot.png` may be stale.
- [ ] T6: never edit the installed app; hot-load only a copy.
- [ ] T7: never use `osascript quit`; stop only the exact LaunchAgent/PID.
- [ ] T8: pass explicit ports and verify the serving identity; “UP” alone is insufficient.
- [ ] T9: require the build's always-included silhouette/occluder shaders before shipping.
- [ ] T10: a nonempty `build-report.txt` is not a successful build — require `result=Succeeded`.
- [ ] T11: the session needs `WORLDOS_ART_REPO_ROOT=/Users/m1/WorldOS`; the gitignored `_private` art exists only in the canonical checkout, so the owner worktree as art root reports every image missing.
- [ ] T12: two agents render pixels but answer nothing — `org.worldos.owner-dm` is what consumes `WORLDOS_PLAYER_MOVES`.
- [ ] T13: a listening player is not yet a consuming player — allow the first surface up to 180 s, then ledger the final result.

Dry-run proof is `installer-dry-run-verified`: gates, plist rendering, and the named app were checked. It does not prove live install behavior, installed-build G1, or owner-play G4.
