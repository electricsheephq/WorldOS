# QA

WorldOS QA is split into fast confidence, app proof, provider proof, and release proof.

## Quick Local Checks

Use focused local checks for fast feedback:

```bash
bash -n scripts/play.sh scripts/play_party.sh scripts/play_codex_dm.sh scripts/play_codex_actor.sh qa/score_codex.sh
python3 -m pytest qa/test_app_handoff_gate.py qa/test_release_readiness.py -q
swift build --package-path macos/WorldOSApp
```

Prefer CI or a high-memory remote QA host for heavy suites and persona sweeps.

## Fast App Confidence

Use localhost/OpenWorlds for rapid inspection:

```text
http://127.0.0.1:8765/openworlds/
http://127.0.0.1:8765/app-status
http://127.0.0.1:8765/session-surface
```

This is the fastest way to check route health, the read model, action availability, and move sink state.

## Built-App Handoff Gate

Use the built-app handoff gate when you need product proof:

```bash
python3 qa/app_handoff_gate.py \
  --web-beats 5 \
  --built-beats 5 \
  --codex-moves 1 \
  --art-root . \
  --scripted-budget 1.00 \
  --codex-budget 3.00 \
  --timeout 90 \
  --codex-timeout 240
```

The handoff gate proves:

- web scripted smoke,
- built `dist/WorldOS.app` scripted smoke,
- short built-app provider playtest,
- `/app-status`,
- `/session-surface`,
- accepted `/move`,
- screenshots, action logs, network/console logs, and provider trace.

A handoff score of `100` is a fast GUI/provider confidence signal. It is not a release verdict.

## Provider Proof

Provider proof must be honest about provider family:

- Anthropic proof: Anthropic DM/player/scorer lane.
- Codex proof: Codex/OpenAI DM/player/scorer lane.
- Mixed proof: benchmark only.

Evidence manifests should include provider family, DM model, player agent/model, scorer provider/model, build SHA, and art status.

## Release Readiness

The Release Readiness Index is the release verdict. It requires one build SHA with complete evidence, including:

- canonical personas,
- behavior gate,
- UI/app evidence,
- image and palette evidence,
- story and mechanical scores,
- runtime/console health,
- built-app playability.

Partial, missing-persona, mixed-SHA, or harness-contaminated results are diagnostic evidence, not a release declaration.

For the command index, read [../qa/QA_TOOLS.md](../qa/QA_TOOLS.md).
