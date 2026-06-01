# Agent-Grade App Testability Contract

This document defines the architecture contract for milestone
[Agent-Grade App Testability - Built-App Harness & Evidence](https://github.com/electricsheephq/WorldOS/milestone/19),
parent issue [#480](https://github.com/electricsheephq/WorldOS/issues/480).

The goal is narrow: make the shipped `dist/WorldOS.app` testable by an agent with
deterministic status, stable driving hooks, and disk-backed evidence. This does
not change the game architecture: the engine remains the sole writer, the native
app/OpenWorlds viewer remains a thin reader plus `/move` intent submitter, and
the built app remains release truth.

Current command routing lives in `qa/QA_TOOLS.md` and `WorldOS-GUI-RUNBOOK.md`. This file defines
the contract those tools must satisfy.

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

Current v1 minimum, implemented first so agents can stop guessing:

```json
{
  "schema": "worldos.app-status.v1",
  "surface": "openworlds",
  "state_authority": "engine",
  "write_lane": "/move",
  "build": {
    "sha": "string",
    "version": "string"
  },
  "viewer": {
    "port": 8766,
    "repo_root": "string",
    "state_root": "string",
    "provider": "codex|claude|...",
    "chat_path": "string",
    "chat_lines": 0
  },
  "art": {
    "repo_root": "string",
    "private_root": "string",
    "private_root_present": true
  },
  "live": {
    "attached_campaign_id": "string",
    "campaign_id": "string",
    "active_session_id": "string",
    "run_id": "string",
    "moves_path": "string",
    "moves_writable": true,
    "is_live_view": true,
    "can_act": false,
    "actor": {"id": "string", "name": "string", "kind": "player"},
    "enabled_action_ids": ["continue", "say", "do", "check", "save"],
    "enabled_action_count": 5
  },
  "endpoints": {
    "app_status": "/app-status",
    "session_surface": "/session-surface",
    "move": "/move"
  }
}
```

V2 readiness/health expansion for #481/#483:

```json
{
  "readiness": {
    "status": "ready|degraded",
    "ready_for_smoke": false,
    "ready_for_play": false,
    "failure_bucket": "none|no_app|no_launcher|no_provider|no_art|no_actor|no_actions|move_rejected|no_narration|console_error|permission_prompt",
    "failure_detail": "string"
  },
  "health": {
    "same_port_alive": true,
    "route_loaded": true,
    "console_errors": 0,
    "network_failures": 0,
    "provider_ready": false,
    "image_probe_ok": false,
    "failure_bucket": "none|no_app|no_launcher|no_provider|no_art|no_actor|no_actions|move_rejected|no_narration|console_error|permission_prompt",
    "failure_detail": "string"
  }
}
```

Behavioral rules:

- `ready_for_smoke` requires built app launch, OpenWorlds route loaded, status
  fetchable, provider ready, private-art image probe success, a seated living
  player, visible narration, at least one enabled player action, and `/move`
  sink readiness.
- `ready_for_play` is stronger: it also requires the selected provider to be a
  real provider or an explicitly enabled deterministic test provider, and it must
  report no blocking console/network failures.
- `degraded` means the app is observable but not fully playable; include a
  failure bucket. Harnesses that cannot continue safely should stop with the
  appropriate stable failure bucket rather than inventing another status value.
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
  `WORLDOS_ENABLE_SCRIPTED_PROVIDER=1`. If requested without the gate, the app
  refuses to launch it.
- Uses the normal engine/player architecture. It may script the DM response, but
  campaign state is still written only by the engine and player input still
  enters as `/move`.
- Seats a living canon player, emits visible DM narration, exposes enabled
  actions, accepts representative `/move` intents, resolves deterministic
  follow-up turns, writes `scripted-provider/summary.json`, and leaves
  `/session-surface` actionable.
- Is never release proof by itself. It is a wiring smoke for #482/#483/#486.

## Accessibility and Driving Hooks

Agents should prefer semantic accessibility over implementation-specific DOM
shape. `data-worldos-testid` is allowed when role/name is ambiguous or when copy
changes would make tests brittle.

Policy:

- Every agent-driven control has a stable accessible role, name, disabled state,
  and focus behavior.
- Important regions expose stable landmarks or labels: launcher, campaign shelf,
  OpenWorlds root, narration log, active-player panel, action palette, move
  composer, provider/status banner, modal/dialog layer.
- `data-worldos-testid` values are stable public test hooks, not CSS hooks. Do
  not rename or remove one without updating the harness in the same change.
- Prefer generic test ids plus state attributes for repeated controls, for
  example `data-worldos-testid="action-button"` with
  `data-worldos-action-id="say"` rather than embedding volatile label text in
  the test id.
- Narration/progress surfaces use `aria-live` or an equivalent observable update
  marker so an agent can tell whether a turn advanced.
- Hooks must not reveal private art paths, secrets, or internal provider prompts.

Minimum hook set for #484:

- `worldos-launcher`
- `chronicle-start`
- `chronicle-resume`
- `openworlds-root`
- `app-status-banner`
- `error-banner`
- `provider-status`
- `narration-log`
- `active-player`
- `action-palette`
- `action-button` plus `data-action-id`
- `move-input`
- `move-submit`
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

`qa/export_app_evidence.py --run-dir <dir> --out <bundle>` copies a completed
smoke/playtest run into this bundle shape. `--app-status-url <url>` remains
supported for live read-only export from a running app.

Each exported `manifest.json` also carries a normalized
`review_entrypoint` object. That object is the first file a reviewing agent
should open: it repeats the command, repo, branch, commit SHA, dirty state, app
build SHA, provider, gate kind, run id, timestamps, verdict, failure bucket,
art status, and indexed pointers for screenshots, app-status snapshots,
session-surface snapshots, moves, provider trace, console logs, network logs,
and action logs.

Bundles are evidence, not source. Do not commit them. Screenshots may contain
private art and must remain in `/Volumes/LEXAR/Codex` unless the owner explicitly
chooses to publish a redacted excerpt.

## Gate Split

Issue #486 defines three distinct gates. They must not be collapsed into one
score.

## 100/100 Handoff Gate

`qa/app_handoff_gate.py` is the fast hybrid gate for Codex-led GUI work. It is
the command a main implementation agent should run before spending budget on
longer exploratory/persona playtests.

Default local invocation:

```bash
cd /Users/lume/ClawDnD-val
python3 qa/app_handoff_gate.py \
  --web-beats 5 \
  --built-beats 5 \
  --codex-moves 1 \
  --art-root /Users/lume/ClawDnD-val \
  --scripted-budget 1.00 \
  --codex-budget 3.00 \
  --timeout 90 \
  --codex-timeout 240
```

The handoff gate writes
`/Volumes/LEXAR/Codex/worldos-agent-grade-app-testability/<run-id>/handoff.json`
with `schema: worldos.app-handoff.v1`. `handoff_score` is `100` only when every
mandatory gate passes on the same clean commit SHA:

- web deterministic scripted smoke.
- built `dist/WorldOS.app` deterministic scripted smoke.
- built `dist/WorldOS.app` short Codex-provider playtest.
- bounded hook probe for launcher/resume, table actions, free-text move,
  settings provider status, modal/error/status hook presence.
- evidence manifests with no blocking gaps.

This score means the GUI implementation agent has a trustworthy fast loop for
app wiring and core controls. It does not mean the product is release-ready.
Full non-partial five-persona RRI remains the release verdict.

### 1. Deterministic Built-App Smoke

Purpose: fast, repeatable app wiring proof.

Surface: rebuilt `dist/WorldOS.app`, deterministic scripted provider,
app-status v1, stable hooks, five to eight `/move` beats.

Pass requires:

- Native app launches and serves `/openworlds/`.
- `app-status.readiness.ready_for_smoke` is true on the same port that serves
  `/openworlds/`.
- Private art probe succeeds without committing art.
- A living player is seated, narration is visible, actions are enabled, and
  every scripted `/move` beat is accepted and advances narration.
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
