# RUNBOOK-INDEX — the run-type registry

> **Every run type in the system has a row here.** Each row names its runner, cost tier, required
> evidence, scores surface, and owning skill/runbook. All rows are bound by the **Universal Run
> Contract** (docs/OPERATIONS.md): HEALTH → EVIDENCE → SCORE → VERDICT → POINTER. Adding a new run
> type = adding a row + wiring the five steps. Changing a runner = updating its row (bump
> Last-verified). Dispatch packets MUST name the run type's row.
>
> _Last full verification sweep: 2026-07-08._ Manual-append bucket auto-wired #1414 (2026-07-08):
> duo/sprint/sweep/app-gate/RRI now self-persist via `qa/scores_persist.py` (fail-loud).

## Play / engine QA

| Run type | Runner | Tier/cost | Required evidence | Scores surface | Owner doc/skill |
|---|---|---|---|---|---|
| fast_gate | `qa/fast_gate.sh` | free, ~30s, EVERY change | pass log (iteration-only, never release evidence) | — (by design) | worldos-dev |
| mechanism probe | `qa/mechanism_probe.sh` | ~$1, cue-adjacent PRs | fixture transcript + deterministic ACTED/IGNORED | engine-duo (auto-append) | OPERATIONS QA-econ v2 |
| combat sprint | `qa/run_combat_sprint.sh` | ~$1.50, combat-adjacent | transcript + mech score + behavioral | engine-duo (auto-append #1414) | worldos-dev |
| story/mech duo | `qa/run_duo.sh` | ~$6, BATCH/release only, solo-tenant | transcript + 3 lenses + behavioral + infra-health note | engine-duo (auto-append #1414; CONTAMINATED marker on QUOTA/INFRA abort) | worldos-dev + watcher contract |
| 5-persona sweep | `qa/vm/sweep_v2.sh` (canonical: evaos-support VM) | ~$12, milestone | RRI json + persona score.json ×5 | GUI-headless-proxy (auto-append #1414, per persona) | WorldOS-GUI-RUNBOOK |
| app gate (native) | `qa/ui_playtest_app.sh` | ~$7, release | run.json + score.json + handoff.json | GUI-built-app (auto-append #1414, from score.json) | worldos-dev VM-GATE §; ⚠ `ui_playtest.sh` variant tests a non-shippable port |
| release rollup | `qa/release_gate.sh` → `release_readiness.py` | wraps the above | RRI verdict json + closeout block | rows from parts (RRI row itself auto-appends #1414 whenever --out is written) | OPERATIONS |

## Visual / render

| Run type | Runner | Tier/cost | Required evidence | Scores surface | Owner doc/skill |
|---|---|---|---|---|---|
| visual pre-gate | `qa/visual_pregate.py` | free, every render | verdict numbers + manifest path (gate, not history) | — (gate) | visual-critic skill |
| visual-critic panel | `qa/felt_rest_panel.py` / panel protocol | ~$2/scene | frames in qa/evidence/<n>/ + per-lens scores | visual (auto via felt_rest_panel; ⚠ other paths verify) | visual-critic skill |
| box render/capture | gex44-unity-host skill + manage_camera | free (GPU) | PNG captures (super_size 2-4) → feeds pre-gate/panel | — (feeds visual) | BOX.md + gex44-unity-host; ⚠ motion_reel.py capture hook is a TODO stub |
| demo reel | `qa/demo_reel.py` | free | frame series + GIF (the pixels rule artifact) | — (visibility) | OPERATIONS pixels rule |

## Content / harvest loop

| Run type | Runner | Tier/cost | Required evidence | Scores surface | Owner doc/skill |
|---|---|---|---|---|---|
| artifact eval panel (HV1) | `qa/artifact_calibration_panel.py` | ~$1.50/class | panel report + control-band verdict | artifacts table (⚠ not in scores_ledger view) | SCORING.md |
| extraction (HV2) | `qa/export_campaign_artifacts.py` | free | artifact JSONs → qa/artifacts_out/ | — (feeds HV1/HV3) | OPERATIONS harvest § |
| promotion (HV3) | `tools/library/promote.py` + library_lint | free (+score-if-unscored) | library/ entries (tier, provenance, license) | artifacts table (⚠ not in ledger view) | OPERATIONS promotion runbook |
| library metrics (HV5) | `qa/library_metrics.py` | free | snapshot row (size/tier/reuse) | library_metrics table (⚠ not in ledger view) | OPERATIONS |

## Generation / assets

| Run type | Runner | Tier/cost | Required evidence | Scores surface | Owner doc/skill |
|---|---|---|---|---|---|
| room/backdrop gen | `qa/export_scene_grid.py` → `qa/gen_room_from_scene_grid.sh` → `qa/deploy_room.sh` (live Unity lane; ⚠ `--layered` naming lives on the quarantined Godot tool) | Scenario CUs | plate PNG + pin-check + control-anchored panel (MUST include a disclosed WorldOS house-style anchor — e.g. crypt_dense_v1 — alongside the disguised PoE2/BG2 control; a PoE2-only or absolute-score reading is NOT sufficient for adoption, see plate-style-regression #2026-07-08) + room_recipes entry + library/rooms promotion | visual (panel) | asset-gen skill |
| character/asset gen | asset-gen skill (Meshy/Tripo/Scenario/PixelLab) | ~5-25 CU/asset | registry entry (gen_recipe) + grounded upright render per actor + evidence commit | ⚠ NONE today — gate via pre-gate + panel on first composed use | asset-gen skill |

## Known wiring gaps (tracked)
1. ~~**Manual-append bucket** (duo, sprint, sweep, app gate, RRI)~~ — CLOSED #1414: each runner now
   self-persists via `qa/scores_persist.py` (fail-loud; CONTAMINATED marker on a QUOTA/INFRA abort).
2. ~~**Ledger unification**: artifacts + library_metrics tables don't render into scores_ledger.md.~~
   — CLOSED #1415: `scores_db.py --render` now folds an "Artifact panels" roll-up (per-panel_id,
   per-class median + ±1.2 control-band verdict) and a chronological "Library metrics" trend into
   `qa/scores_ledger.md` itself, alongside the runs table; each section is cleanly omitted when its
   store is empty.
3. Half-wired, #1415 update: `felt_rest_panel`'s non-rest write path was VERIFIED — the row was
   never skipped (add_run fires unconditionally per frame), but the REST_LENSES-only dims filter
   silently dropped a non-rest panel's per-lens scores; fixed to pass through arbitrary dims for a
   non-`rest:` scene. `motion_reel.py`'s Unity-capture hook is now wired against the documented
   `manage_camera` pattern (env-gated on `WORLDOS_UNITY_MCP_URL`, mockable via `mcp_call=`) but its
   live `:8080/mcp` round-trip is UNVERIFIED on this lane (no GEX44 box access) — validation queues
   behind the next box session. Still open: the engine-fetch half of motion_reel (TODO hook,
   separate scope) and the stale `--layered` naming.
