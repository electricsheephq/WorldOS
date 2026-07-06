# WorldOS Test-Selection Matrix — what to run for which change

The practical "you changed **X** → run **A / B / C**" rubric (fast → slow), plus the operating
principle for how we run tests. Owner-directed (2026-06-21). Companion to `docs/qa/FAST_GATE.md`
(the fast-gate design) and `qa/SCORING.md` (the lenses + the ledger).

## Operating principle — keep a GLM-5.2 run in flight, always
**A GLM-5.2 background run should almost always be running in parallel with dev work.** GLM-5.2 (z.ai,
Anthropic-compatible endpoint) is **off the Anthropic session budget**, reliable to ~Opus story caliber,
and a full run takes **hours** — so there is no reason to fire a run and then sit idle. The pattern:
1. Push a GLM run out (a smoke / arc / duo testing what you just changed).
2. **Work in parallel** on something that doesn't depend on it — combat, graphics, tooling (these don't
   need the same sweeps).
3. When the run returns (~2 h), triage → log to `scores_db` → the **next** run goes out. Never "run a
   sweep then stop working."

Combat is **engine-driven** now (the v2.0 competence ladder), so GLM's old combat-truncation risk is
largely gone — GLM is a fine DM for nearly all QA.

## The test levels (fast → slow)
| Level | Command | Cost / time | What it proves |
|---|---|---|---|
| **Tier-0 fast gate** | `bash qa/fast_gate.sh` | free, ~seconds | Deterministic engine/seat-path/combat-resolution regressions (CI on every change). |
| **Combat smoke (0-LLM)** | `uv run --directory servers/engine python ../../qa/combat_smoke.py --seed N [--fast]` | free, ~30 s | Engine mechanics + the **engine-AI competence** (heal/cast/abilities fire). Exit 0 = no regression. |
| **Beat smoke** | `qa/run_duo.sh <run> <world> qa/play_player_duo.txt 6 8.00` (GLM) | off-budget, ~20 m | DM craft / UX / narration on a short duo. Iteration signal, not a gate. |
| **Arc smoke** | `qa/run_arc_smoke.sh <run> <adventure> 4` (set the model — defaults Sonnet!) | ~5 m | Does a companion arc ENGAGE (met + approval moves off 0)? |
| **Combat-sprint median** | `qa/run_combat_sprint.sh <run>` ×3 → median of `*.angrydm.json` | Opus ~5/run | The HONEST mechanical gate (pre-seeded, deterministic coverage). Baseline 3.7. |
| **3-act full run** | `qa/run_duo.sh` with the embergloom prompt, ≥24 beats (Opus or GLM) | hours | Story + companion + combat over a full authored arc. |
| **5-persona VM sweep + RRI** | the support-VM lane (`qa/vm/sweep_v2.sh`) — see `WorldOS-GUI-RUNBOOK.md` | hours, VM | The release index (story/mech/sat per persona + the 11 RRI gates). **RELEASE-only.** |

## What to run for which change
| You changed… | Run (fast → slow) |
|---|---|
| **engine combat mechanics / the combat AI** (`combat_loop`, `combat_ai`, attack/cast/riders) | `fast_gate` → **`combat_smoke.py`** → combat-sprint median ×3 |
| **DM craft / prompt / narration / UX** | beat smoke (`run_duo` ~6 b, **GLM**) |
| **story / arcs / companions / quests** | **arc smoke** → a 3-act full run (`run_arc_smoke`, then a ≥24-beat run; **GLM/Opus, never the Sonnet default**) |
| **scoring / rubrics / gates** | `fast_gate` + `qa/test_lens_variance.py` + `test_score_determinism` → **re-version the ruler** (`scoring_config_version.py --label/--lens`, stamp `sc_`/`lc_` + CHANGELOG) |
| **viewer / OpenWorlds UI** | `qa/ui_playtest.sh` (blind persona) + the viewer tests; for combat UI, `qa/preview_combat.sh` + watch `#battle` |
| **Unity/current visual renderer** | GPU-VM Unity/visual-critic proof for renderer work; keep Linux CI to deterministic SceneGrid/render-contract tests unless a PR intentionally changes renderer-host code. |
| **Godot reference/extension material** | Optional/manual only: `godot --headless --path extensions/renderers/godot --import` + the archived conformance/screenshot lane when explicitly touching `extensions/renderers/godot/`. Do not make it a required merge gate. |
| **a RELEASE** (not just a tag) | the FULL round: `combat_smoke` + beat smoke + a 3-act run + **5-persona VM sweep + RRI** + the Mac native part-A |

## Tags vs releases
- **Tag liberally** — at every clean milestone (a feature lands + its level-appropriate tests pass).
  A tag is a checkpoint; it does NOT require the full sweep.
- **Release rarely** — a real release (`gh release`, GA framing) requires the **full round** above,
  including a non-partial **5-persona VM sweep + RRI** and the Mac native part-A at one SHA. Only then
  is a CHANGELOG `[Unreleased]` band promoted to a numbered GA.

## Logging
Every scored run → `qa/scores_db.py add_run(...)` (NEVER hand-edit `scores_ledger.md`). Mark
contaminated/partial rows with `*`. Compare only within the same `sc_`/`lc_` ruler.
