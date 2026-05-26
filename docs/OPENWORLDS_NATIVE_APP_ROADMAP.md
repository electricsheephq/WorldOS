# OpenWorlds Native App Roadmap

Date: 2026-05-26

## Correction

OpenWorlds is the visible macOS app experience. SwiftUI/AppKit remains in the
product, but only as the native supervisor and bridge for process lifecycle,
provider launch, dependency checks, diagnostics, settings persistence, and
WKWebView integration.

The old SwiftUI Play/Campaigns/Monitor/Providers/Settings/Logs shell is not the
normal product UI. It stays temporarily as a debug/recovery control center while
the OpenWorlds bridge reaches parity.

## State Authority

- Engine and existing player move paths are the only campaign-state writers.
- Viewer exposes browser-safe read models.
- OpenWorlds reads viewer APIs and may submit player intent through `POST /move`.
- The native bridge may start/stop local processes and return app diagnostics.
- OpenWorlds and Swift code must not write `snapshot.json`, `play-state`,
  `qa/state`, inventory, quests, XP, clocks, companion state, or private lore
  directly.

## Surface Map

| Surface | Current state | Backing |
| --- | --- | --- |
| Chronicles | Wired/partial | `/openworlds/campaigns.json`; native start/resume still needs bridge hardening |
| Table | Wired/partial | `/session-surface` plus `/move` for enabled actions |
| Combat | Wired/partial | `/combat-surface` plus `/move` for enabled actions |
| Atlas | Wired/partial | `/atlas-surface` plus `/move` for enabled travel |
| Settings | Native bridge | App preferences, dependency status, provider status, diagnostics |
| Providers | Native bridge | `ProviderAdapters.swift` and `AppProcessService` |
| Logs | Native bridge | supervisor/provider logs from Swift |
| Relations/Camp | Display-only | needs companion/camp read model |
| Inventory/Merchant/Forge | Display-only | needs item/economy read models and engine-owned actions |
| Acts | Display-only | needs campaign-director projection |
| Bestiary/Codex | Display-only | needs player-known lore projection |
| Character/Create/Seed/Dialog | Display-only | prototype UI retained until backed |

## Capability Labels

Every OpenWorlds screen should expose one of these labels:

- `Wired`: backed by viewer/native bridge and usable.
- `Read-only`: backed by real data but no mutation path.
- `Display-only`: prototype UI retained for roadmap/fidelity, not backed.
- `Provider required`: requires Claude/Codex/OpenClaw session.
- `Unavailable`: dependency or native bridge missing.

## Sprint Order

1. Sprint 0: roadmap correction and trailing-slash bug fix.
2. Sprint 1: full-window OpenWorlds host in the macOS app, including the first
   pass at single-frame custom chrome (#136).
3. Sprint 2: `window.ClawDnDNative.request(type, payload)` bridge.
4. Sprint 3: map Settings, Providers, and Logs into OpenWorlds.
5. Sprint 4: make Chronicles the real app home with live/stale run state.
6. Sprint 5+: finish gameplay surfaces in impact order: table, combat, atlas,
   relations/camp, inventory/economy, acts, bestiary/codex.
7. Release trust: add a Sparkle-backed local beta channel (#134) after the local
   `.app`, signing, and bundle identity are stable. This lets owners update the
   native app and bundled OpenWorlds UI without repeated manual rebuild/download
   cycles, while keeping the viewer/engine state directories outside the app
   bundle.

## Sparkle Update Lane

Sparkle is the beta distribution lane for #134. The local beta channel writes to
`/Volumes/LEXAR/Codex/clawdnd-beta-channel` and uses:

- app bundle: `/Volumes/LEXAR/Codex/clawdnd-beta-channel/ClawDnD.app`
- update feed: `file:///Volumes/LEXAR/Codex/clawdnd-beta-channel/appcast.xml`
- release script: `script/package_macos_beta.sh`
- bundle id: `dev.clawdnd.app`
- version/build: `0.3.0` / `2026052601` for `0.3.0-beta.1`
- signing identity: `Developer ID Application: Andrew Ryan (TC6MS3T6NN)`

The Sparkle private key lives only under
`/Volumes/LEXAR/Codex/clawdnd-release-secrets/`. The repo stores only the public
key in `macos/ClawDnDApp/SparklePublicKey.txt`.

Implementation goals:

- Keep app updates separate from campaign state, `play-state`, `qa/state`, and
  private world content.
- Add a stable bundle identifier, signing identity decision, appcast location,
  update cadence, rollback notes, and release-channel naming before enabling
  automatic checks.
- Surface update status inside the OpenWorlds Settings screen through the native
  bridge, not through a second visible SwiftUI settings shell.
- Keep local dev builds working without Sparkle so contributors can still use
  `./script/build_and_run.sh --verify`.
- Package `viewer/openworlds/` into `ClawDnD.app/Contents/Resources/openworlds`
  and launch the repo-backed Python viewer with `CLAWDND_OPENWORLDS_DIR` pointing
  at those bundled assets when available.

## Window Chrome Lane

The visible app should not show duplicate window traffic lights. Track the full
custom-chrome work in #136:

- native titlebar and traffic lights hidden in normal OpenWorlds play;
- OpenWorlds frame becomes the apparent app frame;
- visible OpenWorlds controls later call native close/minimize/zoom through the
  bridge;
- a reliable drag region is added without stealing gameplay/settings clicks;
- Debug Control Center keeps normal native chrome for recovery.

## Native Bridge Contract

Browser API:

```js
window.ClawDnDNative.request(type, payload)
```

Supported request types:

- `appStatus`
- `dependencyStatus`
- `providerStatuses`
- `startViewer`
- `stopViewer`
- `startProviderSession`
- `stopProvider`
- `diagnostics`
- `copyDiagnostics`
- `updaterStatus`
- `checkForUpdates`
- `windowCommand`
- `openFallbackDashboard`

Native replies:

```json
{ "ok": true, "requestId": "uuid", "type": "appStatus", "payload": {} }
```

Errors:

```json
{ "ok": false, "requestId": "uuid", "type": "startProviderSession", "error": "message" }
```

## Validation

Run from a Lexar-backed checkout:

```bash
python3 -m unittest viewer.tests.test_openworlds_static -q
python3 -m py_compile viewer/server.py
swift build --package-path macos/ClawDnDApp
./script/build_and_run.sh --verify
python3 scripts/license_check.py
git diff --check
```

No story/DM content changes and no narrative QA runs are part of this lane.
