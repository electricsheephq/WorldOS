# WorldOS QA Tools Index

This is the command map for agents. It does not replace the release truth in
`WorldOS-OPERATING-GOAL.md`, the GUI loop in `WorldOS-GUI-RUNBOOK.md`, or the evidence ledger `qa/scores_ledger.md` (rendered from `qa/scores_db.py`; `qa/SCORECARD.md` is legacy).

Default local paths:

- App/private-art checkout: `/Users/lume/ClawDnD-val`.
- Evidence root: `/Volumes/LEXAR/Codex`.
- Heavy persona/backend sweeps: GitHub CI or the owner-provided support VM after explicit preflight.

## Fast GUI And Native App Gates

| Tool | Use it for | Writes / reads | Do not use when |
|---|---|---|---|
| `qa/app_handoff_gate.py` | The current fastest handoff gate: web scripted smoke, built `dist/WorldOS.app` scripted smoke, short built-app Codex playtest, and bounded hook checks on one SHA | `/Volumes/LEXAR/Codex/worldos-agent-grade-app-testability/<run-id>/handoff.json` plus gate evidence bundles | You need the final release verdict; run RRI instead |
| `qa/app_smoke_scripted.py` | Deterministic multi-beat scripted smoke against the web/viewer harness | Screenshots, app-status snapshots, moves, `smoke.json` | You need real-provider behavior or native shell proof |
| `qa/ui_playtest_app.sh` | Built-app/native harness with native Part A+B evidence and stable failure buckets | Native app run dir, app-status snapshots, screenshots, move/chat artifacts | You only need a quick static or web smoke |
| `qa/export_app_evidence.py` | Normalize a live app or run dir into a reviewable evidence bundle | `manifest.json`, status/session snapshots, screenshots, traces, logs | You are trying to prove behavior without first running a gate |
| `qa/app_failure_buckets.py` | Classify harness failures into the stable app bucket list | Bucket JSON / shell-readable output | You need product fixes; this only labels failures |
| `qa/app_handoff_hooks.js` | Static/same-port hook probe for core agent-driving controls | Hook-check JSON inside handoff evidence | You need human exploratory testing; this is a bounded locator check |

Copy-paste fast handoff command:

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

Read `handoff.json` first. A `handoff_score` of `100` means the GUI implementation agent has a
trustworthy fast loop for wiring and core controls. It does not mean release-ready.

Stable app failure buckets are:
`no_app`, `no_launcher`, `no_provider`, `no_art`, `no_actor`, `no_actions`, `move_rejected`,
`no_narration`, `console_error`, and `permission_prompt`.

## Release And RRI

| Tool | Use it for | Required evidence | Do not use when |
|---|---|---|---|
| `qa/release_readiness.py` | The Release Readiness Index rollup and only release verdict | Complete same-SHA app/persona evidence, including optional `--handoff-json` Mac proof | You only need fast GUI wiring confidence |
| `qa/release_gate.sh` | Orchestrate the release sweep over the canonical persona set | Built app, persona runs, behavior/UI/image/palette evidence | The support VM or Mac proof preflight is incomplete |
| `qa/support_vm_preflight.py` | Read-only readiness artifact before a support-VM persona sweep | VM identity, repo SHA, selected provider/player lane, `origin/main` queryability, tool/auth/art status, return path, teardown plan | You are trying to fix or sync the VM; get operator approval first |

RRI requires a non-partial five-persona result on one build SHA. A handoff gate can feed the native
app proof through `--handoff-json`, but it cannot fill in missing persona artifacts. The support-VM
preflight must pass before a VM sweep can count toward #466; if `origin/main` is not queryable from
the VM, fix the VM repo credentials/sync lane before running personas.
When a sweep uses remote/persona artifacts plus Mac handoff proof, pass both evidence files into
the RRI rollup path: `qa/release_gate.sh --handoff-json <handoff.json> --support-preflight-json
<support_vm_preflight.json> ...`. Missing or stale support-preflight evidence must stay a failed
release gate, not a warning.
The default VM lane is Codex DM plus Codex UI player; Claude is only a readiness dependency when
`--provider claude` or `--player-agent claude` is selected. The Codex lane requires Codex CLI
`>=0.120.0` for per-invocation MCP server overrides.

## Browser And Persona Diagnostics

| Tool | Use it for | Writes | Do not treat it as |
|---|---|---|---|
| `qa/ui_playtest.sh` | Blind browser persona playtest for #324 | `qa/ui_playtest_runs/<runid>/` | Built-app proof |
| `qa/ui_playtest_aggregate.py` | Combine browser persona findings | Aggregate report/score | Release verdict |
| `qa/ui_playtest_score.py` | Score a browser UI playtest run | `score.json` | Proof that the native shell works |

Use these when you want empirical UX friction from a browser-driven player. They are valuable for
finding broken buttons and confusing flows, but the product is still `dist/WorldOS.app`.

## Story, Mechanics, And Engine Quality

| Tool | Use it for | Notes |
|---|---|---|
| `qa/run_duo.sh` | AI player + DM duo for story/mechanical quality | Heavy local run; prefer narrow or remote execution |
| `qa/run_combat_sprint.sh` | Fast bug-finder for combat fidelity | Good for finding engine/adherence issues, not broad story score |
| `qa/run_party.sh` | Player + companion agents + DM ensemble | Exercises companion and betrayal lanes |
| `qa/score.sh` | Primary story/mechanical/5e scoring | Uses the repo rubrics and schemas |
| `qa/score_openclaw.sh` | Stricter gpt-5.4 cross-check | Cross-check only, not the primary score baseline |

## Evidence Reading Order

For any app run, review in this order:

1. `handoff.json`, `RRI.json`, or gate verdict JSON.
2. `manifest.json` and its `review_entrypoint`.
3. `app-status.initial.json` / `app-status.final.json`.
4. `session-surface.initial.json` / `session-surface.final.json`.
5. Screenshots and accessibility snapshots.
6. `moves.ndjson`, `actions.ndjson`, `console.ndjson`, `network.ndjson`.
7. Provider trace summary and raw trace snippets.

If a screenshot shows a playable page but same-port `/app-status` is missing, classify it as
`no_launcher`. Stale rendered browser tabs are not evidence.
