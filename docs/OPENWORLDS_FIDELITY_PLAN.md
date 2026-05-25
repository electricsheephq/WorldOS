# OpenWorlds Fidelity Rollout Plan

This document supersedes the SwiftUI repaint direction explored in PR #123.
OpenWorlds should be integrated as an exact web surface first, then wired to
ClawDnD read models screen by screen.

## Decision

The OpenWorlds export is the visual contract. The macOS app should supervise
local services and host the product surface in `WKWebView`; it should not
recreate the OpenWorlds UI in SwiftUI unless a future native component can meet
screenshot-level parity.

Primary visual/reference source files:

- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/Open Worlds.html`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/styles.css`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/app.jsx`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/chrome.jsx`
- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/screen-*.jsx`

Prototype/demo data requiring rewrite or explicit non-canonical labeling:

- `/Volumes/LEXAR/Codex/openworlds-design-2026-05-25/openworlds/data.js`

## Architecture

- SwiftUI owns native process supervision, provider launch/status, settings,
  logs, dependency checks, diagnostics, and packaging.
- `viewer/server.py` serves the exact OpenWorlds web surface under
  `/openworlds/` so the UI and viewer APIs are same-origin.
- `/dashboard` remains a fallback/debug route until the OpenWorlds surface is
  stable.
- The browser may read viewer APIs and post player intent to `/move`.
- The browser must never write `snapshot.json`, `play-state`, `qa/state`,
  inventory, quests, XP, world clocks, companion state, or private notes.

## Rollout

1. Fidelity and asset contract.
   - Add this plan and the asset policy.
   - Keep PR #123 draft and mark it superseded.
   - Confirm no reference images or private content are staged.

2. Viewer-hosted exact OpenWorlds surface.
   - Create a cleaned, audited `viewer/openworlds/` bundle from the primary
     export.
   - Normalize `Open Worlds.html` to `index.html`.
   - Use locally vendored React/ReactDOM/Babel for the first exact-fidelity
     sprint, with notices.
   - Remove live CDN calls and Open Design host chrome.
   - Rewrite or label prototype `data.js` content as non-canonical demo data.
   - The PR body must list copied source files, dependency license notices,
     removed CDN calls, removed sandbox/tweak code, and any rewritten prototype
     copy or data.
   - Serve `/openworlds/`, `/openworlds/<asset>`, and
     `/openworlds/config.json`.

3. Native app opens OpenWorlds.
   - Have the Play surface start the viewer and load `/openworlds/`.
   - Keep SwiftUI diagnostics and settings available.
   - Abandon the SwiftUI OpenWorlds repaint from PR #123.

4. Chronicles launcher data binding.
   - Replace prototype campaign rows with read-only campaign summaries.
   - Show live/stale status, world, day, location, and provider/run metadata
     where available.

5. Session/Table read model.
   - Add filtered `/session-surface`.
   - Feed the Table screen with scene, party, conditions, quests, recent events,
     and available actions.
   - Exclude hidden fields such as `notes`, `dm_notes`, sealed companion agenda,
     and private lore.

6. Gameplay surfaces.
   - Combat board, atlas, relations/camp, inventory/merchant/forge, acts, and
     bestiary/codex proceed as read-model surfaces before adding backed player
     actions.

## PR #123 Disposition

PR #123 must remain unmerged. After this plan lands, add a final PR comment on
#123 linking to the fidelity contract PR and close #123 as superseded once the
first viewer-hosted OpenWorlds surface PR is open.

If the viewer-hosted web-surface path fails, reopen the architecture decision in
a new issue or PR. Do not revive the SwiftUI repaint unless it includes
screenshot-level parity evidence against the exported OpenWorlds reference.

## Visual Gates

Before a visual PR is marked ready:

- Capture exported reference and ClawDnD candidate screenshots at `1366x768`,
  `1440x900`, and `1920x1080`.
- Add `1024x768` and mobile-like widths when the PR claims responsive web
  parity; otherwise record those viewports as deferred.
- Confirm the candidate preserves the OpenWorlds window frame, nav rail,
  parchment surface, typography, spacing, hover/active affordances, and screen
  routing.
- Reject obvious SwiftUI repaint drift.

## Validation

Focused local checks:

```bash
cd <repo-root>
pwd
python3 -m py_compile viewer/server.py
python3 -m unittest discover -s viewer/tests -q
swift build --package-path macos/ClawDnDApp
./script/build_and_run.sh --verify
python3 scripts/license_check.py
git diff --check
```

Use GitHub CI for broad engine, rules, voice, and license validation. Docs-only
PRs are expected to run the normal CI and license-check jobs; the macOS Swift
workflow only runs for macOS, script, or workflow changes unless manually
dispatched. Use CodeRabbit and read-only adversarial agents before merge.
