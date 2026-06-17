# Providers

WorldOS is provider-agnostic at the game contract level. The engine owns campaign state; providers drive the DM loop by calling engine/rules tools and narrating results.

## Provider Families

| Family | Runtime | Typical role | Requires the other family? |
|---|---|---|---|
| Anthropic | Claude Code / Claude CLI | Opus/Sonnet DM, player, and scoring lanes | No |
| Codex/OpenAI | Codex CLI | GPT DM, player/test agent, and scoring lanes | No |
| OpenClaw | Gateway/plugin adapters | Experimental provider research | No, but not the first release dependency |

Same-family proof means the DM, test/player agent, and scorer all come from the same provider family. Mixed scoring is useful for benchmarking, but it is not same-family product proof.

## Engine Contract

Provider changes must not change these rules:

- The engine is the only campaign-state writer.
- OpenWorlds and `WorldOS.app` are read-model surfaces plus `/move` intent submitters.
- Provider tools may read state, ask rules questions, resolve rolls through engine tools, and narrate.
- Providers must not write snapshots or bypass `/move`.

## Anthropic Lane

The Anthropic lane uses the existing Claude wrappers and should remain stable when Codex features change.

Common scripts:

```bash
scripts/play.sh
scripts/play_party.sh
qa/score.sh
```

Use this lane when:

- The user has Anthropic/Claude access.
- You need Opus/Sonnet comparison evidence.
- You are validating that Codex changes did not break the existing Claude path.

## Codex/OpenAI Lane

The Codex lane uses Codex CLI and GPT models without requiring Anthropic auth.

Common scripts:

```bash
scripts/play_codex_dm.sh
scripts/play_codex_actor.sh
qa/score_codex.sh
```

Codex CLI config notes:

- A top-level `service_tier` of `fast` or `flex` is accepted.
- `service_tier = "default"` is considered config drift and should be removed or changed.
- The app can launch Codex with an isolated `CODEX_HOME` for QA without affecting the user's normal CLI profile.

## OpenClaw Lane

OpenClaw remains experimental for provider/plugin research. Do not build or depend on gateway plugin work until the native Codex lane is green enough to justify it.

## Model Selection

Defaults are intentionally conservative:

- Anthropic lane: Opus DM where the Claude path selects it; Sonnet player/scorer where configured.
- Codex lane: GPT-5.5 DM/player/scorer unless changed.

Advanced QA settings for player/test/scorer models should stay separate from normal gameplay provider settings.

## Environment Names

WorldOS is moving toward `WORLDOS_*` names while retaining legacy `WORLDOS_*` aliases for compatibility. Provider launchers should set both names when they bridge old and new scripts.

Do not remove legacy names in a provider change unless the migration is explicit and tested.

## Troubleshooting

- Provider CLI missing: install or authenticate the selected provider family; unselected providers may be absent.
- No narration: inspect provider trace and `/app-status.readiness.failure_bucket`.
- Move accepted but no response: inspect `/session-surface`, chat logs, and provider process status.
- Mixed-family evidence: label it as benchmark evidence, not same-family proof.

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
