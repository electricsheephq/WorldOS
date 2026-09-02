# WorldOS owner install (this Mac)

This kit installs the ad-hoc-signed, unnotarized demo player at `~/Applications/WorldOSPlayer.app`. It serves `adventure_demo_v1` from the one pinned checkout `/Users/m1/worldos-owner`, with engine port 8776 and player QA port 8981. It never uses 8766, 8971, 8866, or 8972.

## Flow

1. `qa/owner_install.sh preflight /absolute/WorldOSPlayer.app` runs all refuse-on-red gates without writing.
2. `qa/owner_install.sh dry-run /absolute/WorldOSPlayer.app --stage /absolute/evidence/stage` writes only two plists plus `install-ledger.json` under the stage directory.
3. The owner runs `qa/owner_install.sh install /absolute/WorldOSPlayer.app [--sha COMMIT] [--build-sha COMMIT]`. It backs up existing app/state, installs and ad-hoc signs the app, pins `/Users/m1/worldos-owner`, seeds `owner_demo`, installs the two LaunchAgents, starts the session first, waits for `/health` 200, then starts and probes the player. Do not run this from a QA lane.

Refuse-on-red means packaged pins GREEN, zero `KitRoom_` strings in `level0`, FRESH crypt and tavern certifications, and either sibling `build-report.txt` or `--build-sha`. Any RED or ERROR exits 1 before a write.

## Refresh and removal

- `qa/owner_install.sh refresh --sha COMMIT [--reseed]` stops both agents, verifies the new commit is a fast-forward of the pinned checkout, moves it detached, optionally reseeds, then repeats the ordered start and probes.
- `qa/owner_install.sh status` reads LaunchAgent and explicit-port health/debug state.
- `qa/owner_install.sh uninstall` removes agents/plists but keeps app and state. `--purge` also removes the exact installed app and owner state paths.

## Rollback

Install prints the timestamped backup path and exact restore command. Backups and ledgers live under `/Users/m1/Codex/session-notes/<UTC-date>/worldos-refresh/artifacts/owner-install/`. Stop both agents before restoring app/state, then bootstrap and start the session before the player.

## Traps checklist

- [ ] T1: after `ditto`, clear quarantine and ad-hoc codesign before launch.
- [ ] T2: never blind-start both; session must reach health 200 before player kickstart.
- [ ] T3: reseed and serve from the same pinned checkout; refresh must be fast-forward safe.
- [ ] T4: QA probes use `127.0.0.1`, never `localhost`.
- [ ] T5: numbered `wos_shot_*.png` is current; `wos_shot.png` may be stale.
- [ ] T6: never edit the installed app; hot-load only a copy.
- [ ] T7: never use `osascript quit`; stop only the exact LaunchAgent/PID.
- [ ] T8: pass explicit ports and verify the serving identity; “UP” alone is insufficient.
- [ ] T9: require the build's always-included silhouette/occluder shaders before shipping.

Dry-run proof is `installer-dry-run-verified`: gates, plist rendering, and the named app were checked. It does not prove live install behavior, installed-build G1, or owner-play G4.
