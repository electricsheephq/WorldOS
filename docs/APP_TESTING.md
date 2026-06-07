# App Testing

Agents and humans should test WorldOS through observable app contracts, not fragile desktop guessing.

## Fast Loop: Localhost

Use localhost for quick UI and state inspection:

```text
/openworlds/
/app-status
/session-surface
```

This verifies the web/OpenWorlds read model, route health, current campaign/run, active actor, enabled actions, and `/move` sink readiness.

## Release Surface: Built App

Use `dist/WorldOS.app` when the question is native-product proof:

- Swift app launch,
- app preferences,
- provider selection,
- configured `CODEX_HOME` or provider command,
- native window transition,
- built app screenshots,
- provider process launch from the app.

Do not count localhost-only proof as native proof.

## `/app-status`

`/app-status` is the app/harness truth probe. It is read-only and must not mutate campaign state.

Important fields:

- build SHA/version,
- selected provider and provider family,
- auth surface,
- DM/player/scorer model metadata,
- art status,
- campaign/run/session ids,
- active actor,
- enabled actions,
- readiness and failure bucket,
- canonical endpoints.

## `/session-surface`

`/session-surface` is the player-action truth surface. It should answer:

- Is this live?
- Can the player act?
- Who is the active player?
- Which actions are enabled?
- What is the current scene/narration?
- What `/move` payloads are valid?

## Scripted Provider

The scripted provider is a deterministic test provider. Use it to prove app wiring before spending model time.

It is not release-proof by itself. It should still exercise the same app/engine shape: visible narration, enabled actions, accepted moves, and actionable final state.

## Evidence Bundles

Evidence bundles should include:

- manifest,
- app-status snapshots,
- session-surface snapshots,
- screenshots,
- accessibility snapshots,
- moves and action logs,
- console/network logs,
- provider trace summary,
- build SHA and dirty-state metadata.

Raw evidence bundles may contain private art or local paths. Do not commit them unless they are deliberately sanitized and documented.

## Browser Versus Native App

Use the browser for speed. Use the built app for release-surface proof.

That distinction prevents agents from wasting time opening the full app for ordinary DOM/status checks while still preserving the hard rule that the shipped app must be proven before release.
