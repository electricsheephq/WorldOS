# Troubleshooting

## `uv` Or Python Setup Fails

Install `uv` and retry from the repo root:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv --version
```

If a virtualenv crosses filesystems, `uv` may warn about hardlinks. This is usually harmless; set `UV_LINK_MODE=copy` to silence it.

## OpenWorlds Does Not Load

Check the local port:

```bash
curl -fsS http://127.0.0.1:8765/app-status
```

If the port is stale, stop the old process and relaunch:

```bash
scripts/qa_reap.sh
./worldos-play.command
```

## No Active Player Or No Actions

Read:

```text
/app-status
/session-surface
```

Look for:

- `live.can_act`,
- `live.actor`,
- `live.enabled_action_count`,
- `readiness.failure_bucket`.

Common buckets include `no_actor`, `no_actions`, `no_narration`, and `move_rejected`.

## Provider CLI Missing

Install or authenticate the selected provider family.

- Anthropic lane: Claude Code / Claude CLI.
- Codex lane: Codex CLI.

Unselected provider CLIs should not block the selected provider lane.

## Codex Config Drift

If Codex wrappers fail before launch, inspect the active Codex config. A top-level `service_tier` should be absent, `fast`, or `flex`; `default` is stale and should be removed or changed.

## No Narration After A Move

Check:

- provider trace summary,
- chat log,
- `/app-status.health`,
- `/session-surface.recentEvents`,
- console/network logs.

If the move was accepted but narration did not advance, file it as a provider/app playability blocker with the evidence path and build SHA.

## Missing Art

Missing art should degrade to public-safe placeholders unless a test explicitly requires private art. Do not commit private art to fix a missing-image test.

## Native App Will Not Launch

Try a fresh local build:

```bash
script/build_and_run.sh
```

If macOS blocks a local build, use the repo's native app unblocking helper only for local development:

```bash
script/unblock_native_app.sh
```

For release distribution, use signed/notarized builds rather than local bypasses.

## Evidence Looks Green But Scores Are Low

Separate product wiring from quality scoring:

- App handoff proves the built app can play a first move.
- Story/mechanical scoring measures the quality of the session.
- RRI requires both wiring evidence and full persona/scoring evidence.

Do not treat a partial handoff as a release score.
