---
name: visual-critic
description: >-
  The "Angry-DM for graphics" — WorldOS's recursive self-improving VISUAL feedback loop.
  Use to SCORE/critique any WorldOS render (a frame from the Unity closed-loop pipeline, the
  Godot prototype, or a still) against the BG1/2 + Pillars of Eternity + Disco Elysium painterly
  bar, and to drive the render→pre-gate→panel→fix→re-render loop until a scene CONVERGES to that
  bar instead of plateauing. v2: REFERENCE-ANCHORED (the critic scores the GAP to specific named
  reference frames), a DIVERSE 5-6 lens PANEL (parallel subagents), DETERMINISTIC pre-gates
  (frame-lit + floor-contact + screen-scale + occupancy, numbers not vibes), and REGRESSION-TRACKED
  in scores_db (surface="visual"). Invoke whenever you produce a render and need an honest
  gap-to-bar score + concrete defects + machine-actionable fixes WITHOUT burning the main agent's
  context on pixels. Mirrors the engine's story/mech QA loop, for the visual side.
---

# Visual Critic v2 — the reference-anchored convergent visual-feedback loop

## Why this exists (and why v1 plateaued at 4/10)
Images are expensive in the main agent's context, so image review is delegated to CRITIC
SUBAGENTS that read the render and return **TEXT-ONLY** structured findings. The main agent (the
builder) never loads pixels — it acts on the critic's JSON. That part worked. **What did not
work: the loop converged LOW and stalled (~4/10, "closer but not perfect").** The root causes,
and the v2 fix for each:

| v1 plateau cause | v2 fix |
|---|---|
| **No concrete bar.** The critic scored against its *memory* of "BG/PoE/Disco look," which is vague, drifts, and inflates. There was nothing to measure the GAP against. | **Reference-anchored scoring.** The critic is handed 2-3 specific reference frames (`refs/`) matched to the scene kind, and scores the GAP to *those exact images* per dimension. "Lighting 4/10" becomes "lighting 4/10 — `poe2_tavern` wraps a warm hearth key around the character's right side and rim-lights the silhouette; here the actor is flat-white front-lit." |
| **Lenses too soft / holistic.** One critic scoring seven blended dimensions averages into a mushy mid-score and never isolates the single worst illusion-breaker. | **Diverse 5-6 lens PANEL.** Independent parallel subagents, each with ONE obsession (registration, occlusion/grounding, light-coherence, character-integration, tactical-readability, painterly-vs-reference). A specialist scores its axis 2-3 points harsher than a generalist. Synthesis picks the single highest-leverage fix. |
| **No algorithmic grounding.** "Pasted on" / "floating" / "wrong scale" are geometric facts the eye estimates and an LLM rounds off — they never became NUMBERS, so they weren't gated or regression-tracked. | **Deterministic pre-gates** (`qa/visual_pregate.py`): frame-lit (luminance mean+variance), per-actor floor-contact-Y delta (cells), screen-scale relative error, occupancy-mask match. A CRITICAL pre-gate SHORT-CIRCUITS the panel — fix the measurable defect first. |
| **fix_actions too vague to move the needle.** "Improve lighting" can't be executed; the next render looked the same; the score didn't move; the loop gave up. | Every defect carries a **parameterized fix_action** (target + action + exact params: hex, scale, cell, shader keyword, prompt delta) AND names which Unity menu / `scenario_gen.py` flag / shader uniform applies it. |
| **No convergence target above the floor, no regression memory.** "≥7.5" with no per-scene baseline meant a scene could oscillate forever and a fix that *helped* could silently *regress* a dimension. | **Convergence + regression.** Stop at overall **≥7.5 AND no CRITICAL/HIGH** (target **≥8** for "caliber"). Log every round to `scores_db` (surface="visual") and run `qa/visual_regression.py` so a round that drops a dimension ≥1.0 or the overall ≥0.7 is flagged REGRESSED vs the scene's canonical baseline frame. |

**Bake-off validated (2026-06-22):** the v2 loop drove a scene from 4→6 in one cycle and caught
real geometric defects (floating actors, wrong scale) as numbers. These two lessons are now
codified:

1. **Camera fix is permanent** — `qa/visual_pregate.py` uses `up=cross(fwd,right)` for the
   dimetric camera basis. The old `right×fwd` negated the up vector and flipped depth→screen-Y,
   making far cells project *below* near cells. The fix was validated ≤1px vs Unity's
   `WorldToViewportPoint` ground truth. Do NOT change the cross-product order.

2. **LLM panel `overall` is ADVISORY / trend, not a hard gate.** The 5-6 lens panel has ±1.5
   per-score variance across runs on the same frame; a single-run `overall` can swing by that
   much without any real change. **Rule: average ≥2 runs per scored round before citing the
   panel `overall` as a quality conclusion; never gate FATAL on a single-run `overall`.** The
   DETERMINISTIC pre-gate (G1-G4 in `qa/visual_pregate.py`) is the hard gate — it is
   reproducible, numbers not vibes, and short-circuits the panel when it fires.

The goal is **"build the system that builds the system"** — a loop the orchestrator runs itself
to drive a scene to BG/PoE/Disco caliber, with the convergence and regression evidence in the
same ledger as story/mech.

## The loop (one cycle)
```
RENDER  (Unity CL pipeline: Tools/WorldOS/CL/0 → step-4 Scenario → step-5 assemble → screenshot;
         or Godot --demo; or a still)  →  /tmp/<scene>-r<N>.png  (+ measured actor boxes from the
         render side, + the scenegrid fixture)
  │
  ├─① PRE-GATES  qa/visual_pregate.py  (deterministic, <1s, no LLM)
  │     frame-lit · floor-contact-Y · screen-scale · occupancy-mask
  │     → if verdict==FLAG (any CRITICAL/HIGH): DO NOT call the panel. Apply the named
  │       deterministic fix (re-ground the actor / rescale / re-light), RE-RENDER, restart ①.
  │
  ├─② REFERENCE PICK  choose 2-3 refs/ frames matching scene.kind (tavern→poe2_tavern +
  │     bg2ee_temple_combat + disco_cafeteria; outdoor→bg2ee_forest + poe2_cliff; dark→bg2ee_cavern
  │     + poe2_market). See refs/INDEX.md "How to use in the critic."
  │
  ├─③ PANEL  fan out 5-6 LENS SUBAGENTS in parallel (Agent tool, model opus, run_in_background ok).
  │     Each gets: the render path, the SAME 2-3 ref paths, the scene_spec, the pre-gate JSON, and
  │     its ONE-lens prompt. Each returns TEXT-only per-lens JSON (gap-to-ref score + defects + fix).
  │
  ├─④ SYNTHESIZE  (this skill, no pixels) → overall (0-10), the merged defect list (CRITICAL first),
  │     and the SINGLE highest-leverage fix_action.
  │
  ├─⑤ LOG  scores_db.add_run(surface="visual", visual_scene, visual_backend, visual_round=N,
  │     visual_overall, visual_dims_json={6 lenses}, visual_pregate, visual_blocking) ;
  │     then qa/visual_regression.py --candidate <run_id>  (worse-vs-baseline guard).
  │
  └─⑥ if overall ≥7.5 AND no CRITICAL/HIGH (target ≥8): CONVERGED → set_canonical_baseline once,
        stop. else: apply fix_action[0], RE-RENDER, repeat. Cap N=5 rounds, then report the
        residual + the blocking defect + the best-round frame.
```

## ① Deterministic pre-gates (run FIRST, every round) — `qa/visual_pregate.py`
Cheap numeric checks that catch what eyes (and LLMs) miss. A CRITICAL pre-gate short-circuits the
LLM panel — there is no point asking five subagents to admire brushwork while the hero's feet
float 0.4 cells above the stone. The exact checks (thresholds are the module's tunable constants):

- **G1 FRAME-LIT** — downscaled luminance mean + variance.
  `mean < 0.06` → CRITICAL "effectively black" (guards the URP-decal / `-batchmode -nographics` /
  missing-plate black-render bug); `mean > 0.97` → CRITICAL "blown out"; `variance < 0.0015` →
  HIGH "flat / no content." Pillow if present, else a stdlib PNG decode. **Always runs.**
- **G3 FLOOR-CONTACT** — per actor, project its cell's floor plane (y=0) to screen-Y under the
  *locked dimetric camera* (orthoSize 18, pitch atan(0.5)=26.565°, pos (0,40.25,-55.5), aspect
  1344/756 — the `CameraSpec.LOCKED` mirror of `ClosedLoopBuilder.LockCamera`), compare to the
  actor's *measured* screen-feet-Y, express the delta in FLOOR-CELL units.
  `feet > 0.45 cells above floor` → CRITICAL "floating"; `> 0.20` → HIGH; `> 0.45 below` (sunk
  into floor / clipping a prop's base) → CRITICAL; `> 0.20 below` → HIGH. **This is the #1
  "pasted-on" tell, now a number.**
- **G4 SCREEN-SCALE** — per actor, expected pixel height = the actor's world height (`ACTOR_TARGET_H`
  5.2u ≈ 6ft, override per kind) projected at its cell depth; compare to measured pixel height.
  relative error `> 0.32` → HIGH "wrong scale for its world depth"; `> 0.18` → MED. Catches the
  "giant goblin / doll-sized hero" scale break that reads as pasted-on.
- **G2 OCCUPANCY** (only when the render draws a walkable/blocked overlay, e.g. tactical mode) —
  compare the rendered per-cell tint to the SceneGrid walk-mask; `>22%` cells mismatch → HIGH
  "tactical space unreadable"; `>10%` → MED.

Inputs the render side must emit for G2/G3/G4 (G1 needs only the PNG): the `*.scenegrid.json`
fixture, and per-actor measured boxes `{id, cell:[c,r], feet_px:[x,y], px_height, world_height_ft?}`.
The Unity side knows each actor's spawn cell and can read its rendered screen bounds; emit them
to a sidecar JSON next to the capture. Run:
```bash
python qa/visual_pregate.py --render /tmp/scene-r2.png \
  --scenegrid /Volumes/LEXAR/WorldOS-Unity-spike/fixtures/tavern.scenegrid.json \
  --actors @/tmp/scene-r2.actors.json --json     # exit 2 == FLAG (CRITICAL/HIGH fired)
```

**Hard gate:** any CRITICAL or HIGH result means RE-RENDER before calling the LLM panel.
The pre-gate result is deterministic and reproducible; treat it as a CI-style gate, not a hint.

## ② Reference anchoring (the bar made concrete)
The critic does NOT score against a remembered "BG look." It scores the GAP to 2-3 SPECIFIC
reference frames it is shown alongside the render. References live at
`/Volumes/LEXAR/WorldOS-Unity-spike/refs/` (13 calibration frames, see `refs/INDEX.md`).
Pick by scene kind (the INDEX's "How to use in the critic" table):
- **tavern / interior**: `poe2_tavern_interior_combat_02` (prime tavern: warm hearth key, cool
  room ambient, grounded contact shadows on plank floor) + `bg2ee_temple_combat_lighting_04` +
  `disco_cafeteria_bar_interior_03`.
- **outdoor / wilderness**: `bg2ee_forest_party_tactical_01` + `poe2_cliff_party_brushwork_03`.
- **dark zone / cavern / dungeon**: `bg2ee_cavern_darkzone_lighting_03` + `poe2_market_interior_lighting_04`.
- **best single light-coherence anchors** (for the light lens, any scene): `disco_office_interior_lighting_04`,
  `poe2_market_interior_lighting_04`.
These are INTERNAL calibration references only (not redistributed/reproduced/served, never a
generation input).

## ③ The PANEL — 5-6 diverse lens subagents (parallel)
Spawn each as its own subagent (Agent tool, model **opus** for the quality read, `run_in_background`
ok). Give each the render path, the SAME 2-3 ref paths, the `scene_spec`, the pre-gate JSON, and
its single-lens prompt. Each returns TEXT-only JSON. **A specialist obsessed with one axis scores
it 2-3 points harsher than a generalist — that harshness is the point.**

Common preamble (prepend to every lens prompt):
> You are ONE lens of a harsh visual-critic PANEL for WorldOS. Score ONLY your assigned dimension —
> ignore the others (a sibling lens owns them). You are given: the RENDER `{{render_png}}`; 2-3
> REFERENCE frames `{{ref_pngs}}` that define the bar (these are the target — score the render's
> GAP to THEM, not to a generic memory); the scene spec `{{scene_spec}}`; and the deterministic
> pre-gate JSON `{{pregate_json}}` (already-measured facts — do not re-estimate them, build on them).
> Be specific and unflattering; vague praise is worthless. For your dimension return TEXT-ONLY JSON:
> `{"lens":"<id>","score":N,"gap_to_ref":"<which ref, what it does that the render doesn't>",
> "defects":[{"id":"...","severity":"CRITICAL|HIGH|MED|LOW","what":"...","why_breaks_illusion":"...",
> "fix_action":{"target":"sprite|actor_mat|backdrop|grid|occluder|shadow|camera|lighting|scene_spec|plate_prompt",
> "action":"regen|reprompt|rescale|recolor|reposition|shader_param|mask|relight|other",
> "detail":"exact + actionable, with params","applies_via":"<Unity menu / scenario_gen flag / shader uniform>"}}]}`.
> Score 0-10 as the GAP to the refs: 9-10 = indistinguishable from the reference bar; 7-8 = clearly
> the same world, minor tells; 5-6 = reads as a game but visibly below the bar; 3-4 = the illusion
> is breaking on your axis; 0-2 = your axis is broken. Do NOT grade on a curve or soften.

The lenses (one subagent each):
1. **L1 registration / cohesion** — does the painted floor register with the gameplay grid under
   the locked camera? Does the whole frame read as ONE coherent space (no double-perspective, no
   plate seam, no mirrored asymmetry)? Gap to the ref's unified space.
2. **L2 occlusion / grounding** — are actors PLANTED (contact shadow reads, feet meet the floor,
   they pass BEHIND props they should)? Build on the pre-gate's floor-contact numbers. Gap to how
   the ref's figures sit in the scene. (No "actor standing on the table.")
3. **L3 scene-light coherence** — are actors lit BY the scene's key light (warm hearth right / cool
   fill left per the spec `lighting`), with rim light and matching shadow direction? Or flat,
   front-lit, "studio-lit cutout"? This is the dimension that most often tanks — gap to
   `poe2_tavern` / `disco_office` warm-key wrap + rim.
4. **L4 character integration** — the decisive "painted vs pasted-3D" call. Brushwork/edge/texture/
   palette match between actor and backdrop; does the actor look hand-painted into the plate or
   like a clean 3D model composited on top? Gap to the ref's figures (which are 2D sprites painted
   to match). Name the specific tells (too-clean edges, too-high specular, saturation mismatch,
   no atmospheric desaturation with depth).
5. **L5 tactical readability** — is walkable vs blocked legible? Party formation / focal actor
   clear? Do dark zones still read (blocked cells visible in shadow)? Gap to the ref's readable
   tactical space.
6. **L6 painterly-vs-reference** — pure art-direction craft of the BACKDROP plate: brush economy,
   value structure, color harmony, atmospheric depth, NO decorative-frieze/border artifact (a
   known Scenario failure). Gap to the ref's painterly quality.

## ④ Synthesis (this skill, no pixels)
Merge the six lens JSONs:
- `overall` = a defect-weighted blend, NOT a flat mean: start from the mean of the six lens
  scores, then CAP it — overall cannot exceed `min(lens) + 1.5` (one broken axis breaks the frame),
  and any open CRITICAL caps overall at ≤4, any open HIGH caps it at ≤6. (A frame with five 8s and
  one 3 is not a 7 — it's a ~4.5, because the 3 is what the eye snags on.)
- **Variance caveat (MANDATORY):** the panel has ±1.5 per-lens natural variance on the same frame.
  Do NOT cite a single-run `overall` as a final verdict. **Average ≥2 panel runs before reporting
  a quality conclusion.** The DETERMINISTIC pre-gate is the hard gate; the LLM `overall` is a
  direction indicator / trend, not a pass/fail number.
- Build the merged `defects[]`, CRITICAL→HIGH→MED→LOW, de-duped across lenses (the same flat-light
  defect named by L3 and L4 is ONE defect, severity = the harsher).
- `highest_leverage_fix` = the fix_action that resolves the most-severe / most-cross-lens defect.
  Prefer a fix that multiple lenses flagged (it moves multiple dimensions at once).

## ⑤ Log + regression-track — `scores_db` (surface="visual")
Every round is one `visual` row (see `qa/scores_db_visual_patch.md` for the additive schema):
```python
from qa.scores_db import add_run, set_canonical_baseline
add_run(run_id=f"vc-{scene}-r{N}-{sha8}", surface="visual", scorer_model="opus",
        methodology=f"vc-panel-6lens round={N}", build_sha=sha8,
        visual_scene=scene_id, visual_backend="unity-cl", visual_round=N,
        visual_overall=overall, visual_dims_json={ "registration":L1, "occlusion_grounding":L2,
          "scene_light_coherence":L3, "character_integration":L4, "tactical_readability":L5,
          "painterly_vs_reference":L6 },
        visual_pregate=pregate_verdict, visual_blocking="<open CRITICAL/HIGH ids>",
        source_path=render_png, notes="<refs used + caveats>")
```
Then guard against backslide:
```bash
python qa/visual_regression.py --candidate vc-<scene>-r<N>-<sha8> --json   # exit 2 == REGRESSED
```
`qa/visual_regression.py` compares this round to the scene's CANONICAL baseline frame (keyed on
`visual_scene` + `visual_backend`): overall drop >0.7, any dim drop ≥1.0, or a NEW CRITICAL/HIGH
defect the baseline didn't have → REGRESSED. A fix that helps one axis but quietly regresses
another is caught here, not weeks later.

## ⑥ Convergence
Stop when **overall ≥7.5 AND zero open CRITICAL/HIGH** (the loop's floor); push to **≥8** for
"BG/PoE/Disco caliber." On the first frame to cross the bar for a scene, call
`set_canonical_baseline(<run_id>)` so every later round of that scene regresses against it. Cap at
**N=5 rounds**; if not converged, report the residual overall, the single blocking defect, and the
best-scoring round's frame path (do not silently loop forever).

**Convergence evidence:** because panel overall is advisory (see §④ variance caveat), claim
convergence only when: (a) the pre-gate is PASS, AND (b) TWO consecutive panel runs both score
≥7.5 overall, AND (c) no CRITICAL/HIGH defects remain. Do not claim convergence on a single run.

## Build-the-system workflows (this critic is the gate inside each)
Filler-first: ONE hero + ONE monster animating well before any roster.
- **gen-character / gen-enemy**: Scenario/Meshy/PixelLab → import → animate → render on the
  reference backdrop → panel (L2 grounding + L3 light + L4 integration + L6 craft) → iterate to bar.
- **gen-scene**: author/lay-out an iso scene (`*.scenegrid.json`) → painterly plate (the CL
  pipeline step-4 Scenario canny) → place a probe actor → pre-gate + panel → iterate.
- **assemble-encounter**: scene + hero + monster + the engine combat grid → render a beat →
  full panel → iterate. (The CL pipeline at `/Volumes/LEXAR/WorldOS-Unity-spike/CLOSED-LOOP-PIPELINE.md`
  is the canonical Unity render path; step-6 there IS this critic gate.)

## Anti-patterns
- Main agent Reading render PNGs for review (defeats the context-offload — always delegate to lenses).
- Calling the LLM panel BEFORE the pre-gate (wastes 6 opus calls admiring a black frame or a floating
  actor — pre-gate first, fix CRITICAL deterministics, then critique).
- Scoring against a remembered "BG look" instead of the shown reference frames (re-introduces the v1
  drift/inflation that caused the plateau — ALWAYS pass refs and score the GAP to them).
- A flat-mean `overall` that lets five good axes hide one broken one (use the §④ capping rule).
- Vague fix_actions ("improve lighting") — demand target + action + params + `applies_via`.
- Treating one good still as "done" — log every round, set the baseline, watch `visual_regression`.
- **Gating FATAL on a single-run `overall`** — the ±1.5 variance means a single score is a direction
  indicator, not a verdict. Average ≥2 runs before concluding quality. The deterministic pre-gate
  (G1-G4) is the only single-run hard gate.

## Cross-refs
- References + per-dimension map: `/Volumes/LEXAR/WorldOS-Unity-spike/refs/INDEX.md`.
- Render pipeline: `/Volumes/LEXAR/WorldOS-Unity-spike/CLOSED-LOOP-PIPELINE.md` (the CL menu + Scenario step).
- `qa/visual_pregate.py` (deterministic gates), `qa/visual_regression.py` (worse-vs-baseline),
  `qa/scores_db.py` (the ledger, now includes `visual` surface + `visual_*` columns).
- `asset-gen` (the gen pipeline fix_actions drive), `godot-dev` (the Godot render path),
  `worldos-decide` (gate big calls at 95%), the engine story/mech QA loop in `worldos-dev` (the
  analogue this mirrors), Unity-pivot decision at `worldos-session-notes/2026-06-22-unity-pivot/`.
