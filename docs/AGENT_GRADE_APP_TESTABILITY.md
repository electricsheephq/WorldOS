# Agent-Grade App Testability Contract

This document defines the architecture contract for milestone
[Agent-Grade App Testability - Built-App Harness & Evidence](https://github.com/electricsheephq/WorldOS/milestone/19),
parent issue [#480](https://github.com/electricsheephq/WorldOS/issues/480).

The goal is narrow: make the shipped `dist/WorldOS.app` testable by an agent with
deterministic status, stable driving hooks, and disk-backed evidence. This does
not change the game architecture: the engine remains the sole writer, the native
app/OpenWorlds viewer remains a thin reader plus `/move` intent submitter, and
the built app remains release truth.

## Related Work

- [#324](https://github.com/electricsheephq/WorldOS/issues/324): AI playtester harness with five personas.
- [#466](https://github.com/electricsheephq/WorldOS/issues/466): clean non-partial five-persona RRI.
- [#467](https://github.com/electricsheephq/WorldOS/issues/467): UX-first release readiness.
- [#479](https://github.com/electricsheephq/WorldOS/issues/479): real-provider tool-contract noise.
- [#481](https://github.com/electricsheephq/WorldOS/issues/481): expose `app-status` v1.
- [#482](https://github.com/electricsheephq/WorldOS/issues/482): deterministic scripted DM provider.
- [#483](https://github.com/electricsheephq/WorldOS/issues/483): crisp built-app harness failure buckets.
- [#484](https://github.com/electricsheephq/WorldOS/issues/484): stable accessibility and agent-driving hooks.
- [#485](https://github.com/electricsheephq/WorldOS/issues/485): one evidence bundle per app playtest.
- [#486](https://github.com/electricsheephq/WorldOS/issues/486): split smoke, provider playtest, and RRI gates.

## App-Status v1

`app-status` v1 is a read-only behavior contract for the live OpenWorlds
surface. The built-app harness must be able to fetch it without causing any
campaign mutation. Missing or malformed status is a harness failure, not an
implicit pass.

Preferred surface: `GET /app-status` from the viewer process serving the built
app. Native bridge implementations may also mirror the same object, but the JSON
shape and semantics must stay identical.

Required top-level fields for v1:

```json
{
  "ok": true,
  "schema": "worldos.app-status.v1",
  "surface": "openworlds",
  "state_authority": "engine",
  "write_lane": "/move",
  "build": {
    "sha": "string",
    "version": "string"
  },
  "viewer": {
    "port": 8765,
    "repo_root": "string",
    "state_root": "string",
    "provider": "claude|codex|openclaw|scripted|",
    "transcript_path": "string",
    "chat_path": "string",
    "chat_lines": 0
  },
  "art": {
    "repo_root": "string",
    "private_root": "string",
    "private_root_present": false
  },
  "live": {
    "attached_campaign_id": "string",
    "campaign_id": "string",
    "active_session_id": "string",
    "run_id": "string",
    "moves_path": "string",
    "moves_writable": false,
    "is_live_view": false,
    "can_act": false,
    "actor": {
      "id": "string",
      "name": "string",
      "kind": "player"
    },
    "enabled_action_ids": ["continue", "say", "do", "check", "save"],
    "enabled_action_count": 5
  },
  "endpoints": {
    "app_status": "/app-status",
    "session_surface": "/session-surface",
    "campaign_catalog": "/openworlds/campaigns.json",
    "move": "/move",
    "chat": "/chat",
    "activity": "/activity",
    "image": "/image?scope=<scope>"
  }
}
```

Behavioral rules:

- The route is a probe, not a scorer. Readiness/failure buckets belong in the
  harness transition output built from app-status plus session-surface, console,
  network, and screenshot evidence.
- Deterministic smoke readiness requires built app launch, OpenWorlds route
  loaded, status fetchable, private-art presence, a seated living player, visible
  narration, at least one enabled player action, and `/move` sink readiness.
- Real-provider play readiness is stronger: the selected provider must be a real
  provider or an explicitly enabled deterministic test provider, and it must
  report no blocking console/network failures.
- Status must never expose private art file contents, secrets, model keys, or
  operator-only VM details. Paths may be omitted or redacted when not needed for
  diagnosis.

## Deterministic Smoke Provider

The deterministic smoke provider is a dev/test-only DM provider for built-app
smoke. It exists to prove app wiring deterministically before spending budget on
real providers.

Contract:

- Provider id: `scripted`.
- No network calls, model calls, randomness without a recorded seed, or external
  auth.
- Enabled only when an explicit dev/test gate is set, for example
  `WORLDOS_ENABLE_SCRIPTED_PROVIDER=1`. If requested without the gate, normal
  provider UI remains limited to real providers and the app refuses to run it.
- Uses the normal engine/player architecture. It may script the DM response, but
  campaign state is still written only by the engine and player input still
  enters as `/move`.
- Seats a living canon player, emits visible DM narration, exposes enabled
  actions, accepts one representative `/move`, resolves a deterministic follow-up
  turn, and leaves `/session-surface` actionable.
- Is never release proof by itself. It is a wiring smoke for #482/#483/#486.

## Accessibility and Driving Hooks

Agents should prefer semantic accessibility over implementation-specific DOM
shape. `data-testid` is allowed when role/name is ambiguous or when copy changes
would make tests brittle.

Policy:

- Every agent-driven control has a stable accessible role, name, disabled state,
  and focus behavior.
- Important regions expose stable landmarks or labels: launcher, campaign shelf,
  OpenWorlds root, narration log, active-player panel, action palette, move
  composer, provider/status banner, modal/dialog layer.
- `data-testid` values are stable public test hooks, not CSS hooks. Do not rename
  or remove one without updating the harness in the same change.
- Prefer generic test ids plus state attributes for repeated controls, for
  example `data-testid="action-button"` with `data-action-id="say"` rather than
  embedding volatile label text in the test id.
- Narration/progress surfaces use `aria-live` or an equivalent observable update
  marker so an agent can tell whether a turn advanced.
- Hooks must not reveal private art paths, secrets, or internal provider prompts.

Minimum hook set for #484:

- `worldos-launcher`
- `chronicle-start`
- `chronicle-resume`
- `openworlds-root`
- `app-status-banner`
- `narration-log`
- `active-player`
- `action-palette`
- `action-button` plus `data-action-id`
- `move-composer`
- `turn-progress`

## Evidence Bundle

Every built-app smoke or playtest writes one bundle under:

`/Volumes/LEXAR/Codex/worldos-agent-grade-app-testability/<run-id>/`

The run id should include date/time, short SHA, provider, and gate kind. The
harness should write to a temporary directory first and atomically promote the
completed bundle so partial runs are recognizable.

Required contents for #485:

- `manifest.json`: schema version, run id, gate kind, repo, branch, commit SHA,
  dirty-state flag, app SHA/version, provider id, world/persona, command,
  start/end timestamps, artifact root, and final verdict.
- `app-status.initial.json` and `app-status.final.json`.
- `session-surface.initial.json` and `session-surface.final.json` when a session
  was reached.
- `screenshots/`: launch, first playable surface, after first move, and final
  state at minimum.
- `a11y/`: matching accessibility snapshots for the same moments.
- `console.ndjson` and `network.ndjson`: passive browser/app failures.
- `actions.ndjson`: agent or harness actions with result, timestamp, and target.
- `moves.ndjson`: submitted `/move` intents and accept/reject result.
- `provider_trace.ndjson`: provider-level steps, redacted for secrets.
- `run.log`: harness stdout/stderr.
- `summary.md`: human-readable verdict with failure bucket and next action.
- Gate-specific score output: `smoke.json`, `provider_playtest.json`, or
  `RRI.json`.

Bundles are evidence, not source. Do not commit them. Screenshots may contain
private art and must remain in `/Volumes/LEXAR/Codex` unless the owner explicitly
chooses to publish a redacted excerpt.

## Gate Split

Issue #486 defines three distinct gates. They must not be collapsed into one
score.

### 1. Deterministic Built-App Smoke

Purpose: fast, repeatable app wiring proof.

Surface: rebuilt `dist/WorldOS.app`, deterministic smoke provider, app-status
v1, stable hooks, one `/move`.

Pass requires:

- Native app launches and serves `/openworlds/`.
- `app-status` is fetchable from the launcher and minted live viewer and reports
  `schema: "worldos.app-status.v1"` plus live facts needed by the harness.
- Private art probe succeeds without committing art.
- A living player is seated, narration is visible, actions are enabled, and one
  `/move` is accepted and resolved.
- Evidence bundle exists with no missing required files.

This gate catches wiring failures early. It does not prove provider quality,
latency, story quality, or release readiness.

### 2. Short Real-Provider Playtest

Purpose: prove the real selected provider can drive the built app without
contract noise that breaks the player-facing loop.

Surface: rebuilt `dist/WorldOS.app`, real provider such as Codex/Claude, short
budget, one to two player moves, evidence bundle.

Pass requires deterministic smoke already passing, plus:

- Provider starts without auth/setup cancellation loops.
- First playable turn appears on the built app.
- At least one real `/move` is accepted and resolved.
- Provider trace has no blocking validation/safety cancellations.

This is where #479 belongs. It is diagnostic release evidence, not a replacement
for the five-persona RRI.

### 3. Full Non-Partial Five-Persona RRI

Purpose: release-grade verdict.

Surface: one rebuilt app SHA, the #324 persona set (`newbie`, `veteran`,
`adversarial`, `narrative`, `optimizer`), disk-backed evidence, and
`qa/release_readiness.py`.

Pass requires #466 conditions:

- All expected personas complete or are explicitly reported missing.
- No partial or harness-contaminated verdict.
- Same build SHA across evidence.
- Disk-backed behavior, UI, image, palette, console/network, score, and
  session-surface evidence.
- RRI returns non-partial release-ready status.

This is the only release gate. The UX-first roadmap in #467 should use its
failures as product evidence, not as a reason to build more proxy machinery.

## Non-Goals

- No engine write-path changes.
- No new gameplay mechanics, content, renderer branch, or UI redesign.
- No committed private art or generated evidence bundles.
- No replacement for #324 or #466.
- No treating dev viewer, proxy port, deterministic provider, or one-turn
  provider proof as release truth.
- No broad local persona sweeps on the 16 GB Mac when CI or the support VM is the
  safer validation surface.

## Invariants

- Engine is the sole writer of campaign state.
- OpenWorlds/native app is a thin reader plus `/move` intent submitter.
- `dist/WorldOS.app` is the product surface and release truth.
- Private art stays outside git; evidence routes to `/Volumes/LEXAR/Codex`.
- Deterministic test providers are gated and never silently active in normal
  player mode.
- Harness failures use crisp reason buckets; missing evidence never counts as a
  pass.
