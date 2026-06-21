# Getting Started

This guide gets you from a fresh clone to a playable WorldOS session.

## Prerequisites

- macOS or Linux for the Python engine/viewer.
- macOS 13+ plus Xcode Command Line Tools for `WorldOS.app`.
- `git`.
- `uv` for Python runtime management.
- At least one provider login:
  - Claude Code for the Anthropic lane.
  - Codex CLI for the Codex/OpenAI lane.

Install `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Clone

```bash
git clone https://github.com/electricsheephq/WorldOS.git
cd WorldOS
```

## Start OpenWorlds In A Browser

The browser surface is the fastest way to inspect and play the app loop.

```bash
./worldos-play.command
```

or:

```bash
scripts/play.sh sundered-reach
```

Open:

```text
http://127.0.0.1:8765/openworlds/
```

Use the action palette to continue, look around, speak, act, roll checks/saves, travel, and resolve combat when it starts.

## Build The Native App

```bash
script/build_and_run.sh
```

The built app lives at:

```text
dist/WorldOS.app
```

Use the native app when you need to prove the release surface: provider settings, app launch, private/public art loading, native window behavior, and provider process startup.

To develop the Godot isometric renderer or generate art, see `godot/HANDOFF.md` and the `godot-dev` / `asset-gen` skills.

## Pick A Provider

WorldOS supports provider families rather than a single hard-coded model path.

- Anthropic lane: Claude Code / Claude CLI wrappers.
- Codex lane: Codex CLI / OpenAI models.
- OpenClaw lane: experimental/future gateway work.

Read [PROVIDERS.md](PROVIDERS.md) before changing provider commands or model names.

## First Session Checklist

1. Start OpenWorlds or `WorldOS.app`.
2. Confirm the selected provider is ready.
3. Start or resume a world.
4. Confirm visible DM narration.
5. Confirm the active player appears.
6. Confirm at least one action is enabled.
7. Submit one move.
8. Confirm the DM responds and the session remains actionable.

For app proof, `/app-status` and `/session-surface` are the machine-readable truth surfaces. See [APP_TESTING.md](APP_TESTING.md).

## Public And Private Content

Committed content must be original, redistributable, or covered by an appropriate public notice. User-owned content, private art, private screenshots, and raw evidence bundles stay out of git under ignored `_private/` or external artifact paths.

See [docs/images/README.md](images/README.md) for screenshot/image rules.
