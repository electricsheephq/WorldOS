# WorldOS Model Tiering Strategy

Status: proposal / to-test. This document captures a takeover whiteboard from 2026-05-31. It does
not change runtime behavior and must not be treated as release evidence.

## Why This Exists

The recent low story/mechanical scores raised a plausible model-routing question: should the DM use a
stronger model for the parts of play where quality is felt, while cheaper/faster models handle test
drivers or research helpers?

Owner recollection says earlier DM runs used Opus and later runs defaulted to Sonnet. Treat that as
owner recollection until backed by tracked repo history or release artifacts. The tracked scripts do
verify the current default:

| Role | Job | Current default | Evidence |
|---|---|---|---|
| DM | product story, rulings, combat staging | `sonnet`, overridable by `WORLDOS_DM_MODEL` / `CLAWDND_DM_MODEL` | `scripts/play.sh`, `scripts/play_party.sh`, `qa/run_duo.sh` |
| AI playtest persona | test driver, not product output | `sonnet` / actor model envs | `qa/ui_playtest_app.sh`, `qa/run_duo.sh` |
| Scorer | story/mechanical judge | `sonnet` today | `qa/score.sh` |

The f5500ac RRI must not be used as proof that model choice is the root cause. That run was partial
and harness-contaminated.

## Proposal

Use the best model where the player feels quality, and keep the test harness stable:

1. Cold open / universe setup: test Opus DM.
2. Combat or high-stakes rulings: test Opus DM.
3. Routine continuation beats: test Sonnet, or a later tiered mode only after a clean baseline.
4. Scorer: hold constant during A/B runs, so the ruler does not move while the DM changes.

The Haiku idea is not “make Haiku the DM.” The stronger proposal is to prototype Haiku as a helper
lane for lore/rules/state research packets, so the DM spends fewer serial tool round-trips and keeps
premium context focused on judgment and prose.

## Test Sequence

Run this only after gate trust is restored:

1. Sonnet baseline: fixed orchestrator, lean off, scorer constant.
2. Opus-DM arm: same scenario and scorer, `WORLDOS_DM_MODEL=opus` or the confirmed current Opus id.
3. Optional tiered arm: cold-open/combat Opus, routine beats Sonnet, behind a default-off flag.
4. Optional helper prototype: Haiku research packet helper for lore/rules/state gathering.

Run backend/persona sweeps on the 32GB VM. Keep the Mac-only built `.app` launch/play smoke on this
Mac or macOS CI.

## Open Questions

- Which exact Opus model id is valid in the current CLI/runtime?
- Is Claude fast mode reachable from headless `claude -p`, or only interactive Claude Code?
- Should the scorer be promoted to Opus later as a one-time re-baseline after the DM A/B?
- Should helper agents route through Codex subagents first, or through OpenClaw/Codex provider
  adapters after the release gate is trustworthy?
