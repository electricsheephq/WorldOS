---
name: visual-critic
description: >-
  The "Angry-DM for graphics" — WorldOS's recursive self-improving VISUAL feedback loop.
  Use to SCORE/critique any WorldOS render (a frame from the Unity closed-loop pipeline, the
  archived Godot extension, or a still) against the **Pillars of Eternity II: Deadfire** painterly
  bar (BG2 = a tactical-readability cross-check only; Disco Elysium = a dark-pocket/mood cross-check
  only), and to drive the render→pre-gate→panel→fix→re-render loop until a scene CONVERGES to that
  bar instead of plateauing. v2: REFERENCE-ANCHORED (the critic scores the GAP to specific named
  reference frames), a DIVERSE 5-7 lens PANEL (parallel subagents), DETERMINISTIC pre-gates
  (frame-lit + floor-contact + screen-scale + occupancy + motion-liveness, numbers not vibes), and
  REGRESSION-TRACKED in scores_db (surface="visual"). v3 re-anchor: PoE2-as-sole-bar; L4 rewarded
  for a GROUNDED real-3D actor (not a painterly sprite); an L7 MOTION lens scored from a render REEL.
  Invoke whenever you produce a render and need an honest gap-to-bar score + concrete defects +
  machine-actionable fixes WITHOUT burning the main agent's context on pixels. Mirrors the engine's
  story/mech QA loop, for the visual side.
---

# Visual Critic v2 — the reference-anchored convergent visual-feedback loop

## ★ Art-direction bar — Pillars of Eternity II: Deadfire is the SOLE reference (v3 re-anchor)
WorldOS's art direction is **Pillars of Eternity II: Deadfire** — pre-rendered painterly 2D
backgrounds composited with **real-time 3D animated characters**. PoE2 is the bar for EVERY lens.
The old "BG1/2 + PoE + Disco, equal-weight" muddle is dropped: BG2 and Disco are now narrow
CROSS-CHECKS, not co-equal targets.

- **PoE2 = the sole bar (every lens scores the GAP to PoE2).** What to look for, and weave into
  every lens prompt:
  - **Vibrant tropical-gothic palette** — saturated teals/jades, warm sandstone + amber lamplight,
    deep maritime blues; never the desaturated grey-brown "mud" of a cheap render.
  - **Warm/cool contrast in EVERY frame** — a warm key (hearth / lantern / sun) played against a
    cool fill/ambient (sky / shadow / water). If the frame is monochrome-warm or monochrome-cool,
    it is below the bar.
  - **Chroma lives in the lights and magic** — fire, spells, glows, stained glass carry the most
    saturated hues; they are colored light, not white blobs.
  - **Blue-violet shadows, NEVER pure black** — PoE2 shadows are tinted toward the cool ambient
    (blue/violet/teal). A pure #000 shadow is a tell that the deferred ambient is missing.
  - **Layered deferred lighting** — warm key + cool fill at roughly a **3:1** key:fill ratio +
    deferred point lights for fire/spells + **ambient-from-the-plate GI** (the 3D actor must pick
    up bounce color from the painted background, not sit under a neutral studio light).
  - **Painterly BUT architecturally precise plates** — visible brush economy and atmospheric depth,
    yet the perspective/architecture is crisp and correct (not smeared or warped).
  - **Mildly-stylized 3D actors with soft blue contact shadows** — the characters are real 3D,
    slightly stylized (not photoreal), and PLANTED by a soft, cool-tinted contact shadow.
- **BG2 = tactical-readability cross-check ONLY.** Consult BG2EE refs solely for "is walkable vs
  blocked + party formation legible?" (L5). Do NOT score palette / lighting / brushwork against BG2.
- **Disco Elysium = dark-pocket / mood cross-check ONLY.** Consult Disco refs solely for "do dark
  zones still read with chroma in the shadow, and does the frame carry mood?" Do NOT score
  grounding / scale / tactical clarity against Disco.

**Character pivot (load-bearing for L2 + L4):** WorldOS is moving characters from FLAT BILLBOARDS to
**real 3D animated actors**. The critic must therefore CREDIT real-3D grounding and integration — a
flat billboard is now the LOW end of L4, a scene-lit grounded 3D actor is the HIGH end (see L4/L2
below). The v2 critic OVER-penalized billboards as "pasted stickers" (a validated false-negative: it
scored "zero contact shadow" on a frame whose feet-crop clearly showed the shadow). v3 fixes this.

## Why this exists (and why v1 plateaued at 4/10)
Images are expensive in the main agent's context, so image review is delegated to CRITIC
SUBAGENTS that read the render and return **TEXT-ONLY** structured findings. The main agent (the
builder) never loads pixels — it acts on the critic's JSON. That part worked. **What did not
work: the loop converged LOW and stalled (~4/10, "closer but not perfect").** The root causes,
and the v2 fix for each:

| v1 plateau cause | v2 fix |
|---|---|
| **No concrete bar.** The critic scored against its *memory* of "BG/PoE/Disco look," which is vague, drifts, and inflates. There was nothing to measure the GAP against. | **Reference-anchored scoring against the PoE2 bar.** The critic is handed 2-3 specific reference frames (`refs/`, **PoE2-first**) matched to the scene kind, and scores the GAP to *those exact images* per dimension. "Lighting 4/10" becomes "lighting 4/10 — `poe2_tavern` wraps a warm hearth key around the character's right side, rim-lights the silhouette, and tints the shadow blue-violet; here the actor is flat-white front-lit with a pure-black shadow." |
| **Lenses too soft / holistic.** One critic scoring seven blended dimensions averages into a mushy mid-score and never isolates the single worst illusion-breaker. | **Diverse 5-6 lens PANEL.** Independent parallel subagents, each with ONE obsession (registration, occlusion/grounding, light-coherence, character-integration, tactical-readability, painterly-vs-reference). A specialist scores its axis 2-3 points harsher than a generalist. Synthesis picks the single highest-leverage fix. |
| **No algorithmic grounding.** "Pasted on" / "floating" / "wrong scale" are geometric facts the eye estimates and an LLM rounds off — they never became NUMBERS, so they weren't gated or regression-tracked. | **Deterministic pre-gates** (`qa/visual_pregate.py`): frame-lit (luminance mean+variance), per-actor floor-contact-Y delta (cells), screen-scale relative error, occupancy-mask match. A CRITICAL pre-gate SHORT-CIRCUITS the panel — fix the measurable defect first. |
| **fix_actions too vague to move the needle.** "Improve lighting" can't be executed; the next render looked the same; the score didn't move; the loop gave up. | Every defect carries a **parameterized fix_action** (target + action + exact params: hex, scale, cell, shader keyword, prompt delta) AND names which Unity menu / `scenario_gen.py` flag / shader uniform applies it. |
| **No convergence target above the floor, no regression memory.** "≥7.5" with no per-scene baseline meant a scene could oscillate forever and a fix that *helped* could silently *regress* a dimension. | **Convergence + regression.** Stop at overall **≥7.5 AND no CRITICAL/HIGH** (target **≥8** for "caliber"). Log every round to `scores_db` (surface="visual") and run `qa/visual_regression.py` so a round that drops a dimension ≥1.0 or the overall ≥0.7 is flagged REGRESSED vs the scene's canonical baseline frame. |

**Bake-off validated (2026-06-22):** the v2 loop drove a scene from 4→6 in one cycle and caught
real geometric defects (floating actors, wrong scale) as numbers. These lessons are now codified:

> ★ **CAPTURE QUALITY:** render at `super_size=4` (~10k px) BEFORE scoring — LOW-RES captures INFLATE the critic (a real miss: low-res screencaps fooled the panel into scoring degraded images; the owner caught it).
>
> ★ **VERIFY VISUAL CLAIMS vs PIXELS:** a lean single self-critic INFLATES (self-reported 7.27 vs rigorous-panel ~4-5.5; an agent over-claimed "reads as a combat beat" while the pixels showed one figure + a forbidden camera-flip). ALWAYS verify an agent's visual success claim against the actual frame — trust the rigorous reference-anchored 5–6-lens panel, not a lean self-score.

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
to drive a scene to **PoE2 caliber** (BG2/Disco only as the narrow cross-checks above), with the
convergence and regression evidence in the same ledger as story/mech.

## The loop (one cycle)

```plaintext
RENDER  (Unity CL pipeline: Tools/WorldOS/CL/0 → step-4 Scenario → step-5 assemble → screenshot;
         or an explicit archived-Godot extension proof; or a still)  →  /tmp/<scene>-r<N>.png  (+ measured actor boxes from the
         render side, + the scenegrid fixture). For the MOTION lens (L7), ALSO render a REEL of
         N frames (idle / locomotion / attack / hit-react / death) via qa/motion_reel.py.
  │
  ├─① PRE-GATES  qa/visual_pregate.py  (deterministic, <1s, no LLM)
  │     frame-lit · luma-staging-law (G6) · floor-contact-Y · screen-scale · occupancy-mask ·
  │     motion-liveness (G5, reel only)
  │     → if verdict==FLAG (any CRITICAL/HIGH, incl. a G6 FAIL): DO NOT call the panel. Apply the
  │       named deterministic fix (re-ground the actor / rescale / re-light / re-stage for the
  │       dark-pool law / un-freeze the idle), RE-RENDER, restart ①. A G6 WARN does not block —
  │       carry its stats into ④ synthesis alongside the verdict.
  │
  ├─② REFERENCE PICK  choose 2-3 refs/ frames matching scene.kind, **PoE2-first** (PoE2 is the bar;
  │     a BG2 ref is added ONLY for the tactical-readability cross-check, a Disco ref ONLY for the
  │     dark-pocket/mood cross-check): tavern→poe2_tavern (+bg2ee_temple for L5, +disco_cafeteria
  │     for dark-pocket); outdoor→poe2_cliff (+bg2ee_forest for L5); dark→poe2_market
  │     (+disco_office for mood, +bg2ee_cavern for L5). See refs/INDEX.md "How to use in the critic."
  │
  ├─③ PANEL  fan out 6-7 LENS SUBAGENTS in parallel (Agent tool, model opus, run_in_background ok;
  │     L7 MOTION runs only when a reel was rendered). Each gets: the render path (L7 gets the reel
  │     contact-sheet + sidecar), the SAME 2-3 ref paths, the scene_spec, the pre-gate JSON, and
  │     its ONE-lens prompt. Each returns TEXT-only per-lens JSON (gap-to-ref score + defects + fix).
  │
  ├─④ SYNTHESIZE  (this skill, no pixels) → overall (0-10), the merged defect list (CRITICAL first),
  │     and the SINGLE highest-leverage fix_action.
  │
  ├─⑤ LOG  scores_db.add_run(surface="visual", visual_scene, visual_backend, visual_round=N,
  │     visual_overall, visual_dims_json={6 still lenses}, visual_pregate, visual_blocking,
  │     + motion_overall, motion_dims_json={L7 sub-scores}, motion_reel_ref, milestone) ;
  │     then qa/visual_regression.py --candidate <run_id>  (worse-vs-baseline guard, still + motion).
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
- **G6 LUMA-STAGING-LAW** — greyscale histogram stats (Rec.709 luma) vs the measured real-PoE
  staging-law bands (2026-07-01 campaign; same bands as `atelier_luma_gate.py` and
  `generate_room.py`'s `_staging_law_distance` — the source of truth, not re-derived here):
  `near_black` (frac of pixels `L<26`) PASS **0.66-0.85**, WARN 0.50-0.66; `lit` (frac `L>60`) PASS
  **0.02-0.05**, WARN 0.05-0.20; `median_L` PASS **0-15**, WARN 15-40. Outside the WARN band on any
  stat → **FAIL** (mapped to HIGH, short-circuits the panel like the other hard gates); inside WARN
  but outside PASS → **WARN** (mapped to MED — the panel is allowed to run, but **quote the stats
  alongside the verdict**, not just "staging looks off"). Run this on **every candidate BEFORE
  staging a panel** — a FAIL means fix the staging (re-light / re-prompt for the dark-pool law)
  before spending scorer tokens on it. Pillow if present, else a stdlib PNG decode, like G1.
  **Always runs** (needs only the PNG).
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
- **G5 MOTION-LIVENESS** (only when a render REEL is supplied — `qa/motion_reel.py` builds it; when
  no reel is passed, G5 SKIPS == today's behavior) — objective inter-frame deltas over the reel:
  a FROZEN idle (no inter-frame pixel delta across the idle frames — the actor is a static
  billboard, not a living 3D actor) → CRITICAL; NO walk-centroid displacement when the reel's
  metadata says a MOVE occurred (the actor teleports / slides without locomotion) → HIGH. Pure
  numbers (mean abs pixel delta + centroid drift), Pillow-or-stdlib, like G1.

Inputs the render side must emit for G2/G3/G4 (G1 needs only the PNG): the `*.scenegrid.json`
fixture, and per-actor measured boxes `{id, cell:[c,r], feet_px:[x,y], px_height, world_height_ft?}`.
The Unity side knows each actor's spawn cell and can read its rendered screen bounds; emit them
to a sidecar JSON next to the capture. Run:

```bash
python qa/visual_pregate.py --render /tmp/scene-r2.png \
  --scenegrid /Volumes/LEXAR/WorldOS-Unity-spike/fixtures/tavern.scenegrid.json \
  --actors @/tmp/scene-r2.actors.json --json     # exit 2 == FLAG (CRITICAL/HIGH fired)
```

**Hard gate:** any CRITICAL or HIGH result means RE-RENDER before calling the LLM panel — this
includes a G6 FAIL (mapped to HIGH): fix staging first, do not spend scorer tokens on it. A G6 WARN
(mapped to MED) does not block the panel, but the near_black/lit/median_L stats it printed must be
quoted alongside whatever verdict the panel/synthesis produces (numbers, not vibes, all the way
through — never just "staging looks a bit off"). The pre-gate result is deterministic and
reproducible; treat it as a CI-style gate, not a hint.

## ② Reference anchoring (the bar made concrete) — PoE2-first
The critic does NOT score against a remembered look. It scores the GAP to 2-3 SPECIFIC reference
frames it is shown alongside the render, with **PoE2 as the bar** and BG2/Disco only as the narrow
cross-checks (see the §★ art-direction bar at the top). References live at
`/Volumes/LEXAR/WorldOS-Unity-spike/refs/` (13 calibration frames, see `refs/INDEX.md`).
Pick by scene kind (PoE2 ref ALWAYS leads; the bracketed cross-check ref is added only for its
narrow lens):
- **tavern / interior**: `poe2_tavern_interior_combat_02` (the bar: warm hearth key, cool room
  ambient at ~3:1, blue-violet shadows, grounded soft contact shadows on plank floor)
  [+ `bg2ee_temple_combat_lighting_04` for L5 tactical-readability; + `disco_cafeteria_bar_interior_03`
  for the dark-pocket/mood cross-check].
- **outdoor / wilderness**: `poe2_cliff_party_brushwork_03` (the bar) [+ `bg2ee_forest_party_tactical_01`
  for L5 tactical-readability].
- **dark zone / cavern / dungeon**: `poe2_market_interior_lighting_04` (the bar: chroma surviving
  into shadow) [+ `disco_office_interior_lighting_04` for the dark-pocket/mood cross-check;
  + `bg2ee_cavern_darkzone_lighting_03` for L5 blocked-cells-in-shadow readability].
- **best single light-coherence anchor** (for L3, any scene): `poe2_market_interior_lighting_04`
  (warm-key + cool-fill + colored deferred lights, the PoE2 deferred look).
These are INTERNAL calibration references only (not redistributed/reproduced/served, never a
generation input).

## ③ The PANEL — 6-7 diverse lens subagents (parallel)
Spawn each as its own subagent (Agent tool, model **opus** for the quality read, `run_in_background`
ok). Give each the render path, the SAME 2-3 ref paths, the `scene_spec`, the pre-gate JSON, and
its single-lens prompt. Each returns TEXT-only JSON. **A specialist obsessed with one axis scores
it 2-3 points harsher than a generalist — that harshness is the point.** L1–L6 score a STILL; **L7
(MOTION) scores a REEL** and runs only when a reel was rendered.

> ★ **GROUNDING auto-DROP (the billboard false-negative fix).** The deterministic G3 floor-contact
> gate is MEASURED truth; an LLM lens guessing "the actor is a pasted sticker with no contact
> shadow" is a PRIOR. **Rule: if an L2 or L4 lens raises a defect whose claim CONTRADICTS a
> deterministic G3 floor-contact PASS (e.g. "floating / no contact shadow / not grounded" when G3
> says feet-on-floor PASS), DROP that defect entirely BEFORE synthesis — or, if you want to retain
> a paper trail, cap it to LOW and tag it `dropped:contradicts-G3-PASS`. Do NOT merely demote it one
> notch (CRITICAL→HIGH): a HIGH still caps `overall` at synthesis (§④), so a one-notch demotion just
> re-creates the billboard false-negative as a re-render loop.** A G3-contradicted grounding claim
> must NOT feed the synthesis severity caps at all. Pass the G3 result into the L2/L4 prompts and
> tell the lens NOT to claim "floating / ungrounded / no shadow" when G3 PASSed — credit the
> measured grounding first, and only flag a SUBTLER integration tell (shadow too hard-edged, wrong
> shadow color, etc.), which is a separate (non-dropped) defect on its own merits. This is what stops
> the v2 "billboard = sticker" prior from overriding measured grounding.

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

## ★★ CALIBRATION-CONTROL PROTOCOL (MANDATORY since 2026-07-02 — the positive-control finding)
The panel's ABSOLUTE scale is **broken at the top and cannot be trusted**: measured 2026-07-02, blind on the
then-standard instrument, REAL shipped Pillars of Eternity area plates scored **3.0-4.6** and real BG2EE **4.6-5.6**
while our AI plates scored 5.0-6.7 — scorers even confabulated "diffusion-model CG" tells on hand-painted 1998 art.
Two causes, both now banned: (1) the "default skeptical / almost nothing AI-made deserves ≥8" primer suppressed
~0.7 pt and made ≥8 unattainable BY CONSTRUCTION for anything framed as "the candidate"; (2) no panel ever carried
a positive control, so the ceiling was invisible. Rules:
1. **NEVER use an AI-prior primer** ("almost nothing AI-made deserves 8", "default skeptical of AI"). Harshness
   belongs in FLAW-FINDING, not in a scale-suppressing prior. Instruct "score what you SEE — judge the image on
   its craft, not on assumptions about how it was made."
2. **EVERY adoption/verdict panel embeds ≥1 DISGUISED REAL-ART CONTROL** (a shipped plate NOT among the refs,
   presented at comparable resolution/crop/UI-free). The reportable metric is the **DELTA vs the control's
   same-panel score** — "ours ≥ control" = the bar is met. An absolute number from this instrument is NEVER
   citable as a quality verdict (only deltas and flaw lists are).
3. Keep the blind mapping OUTSIDE the panel image directory (scorers Read adjacent files); ≥5 scorers per
   candidate; report median with mean; within-panel comparisons only (cross-panel drift is real, ±1.2).
4. The panel's real value is RELATIVE ranking + concrete flaw diagnosis (both repeatedly proven excellent).
   Treat its flaw lists as the work queue; treat its absolute numbers as instrument-relative only.
5. Complement with the FELT/product track: score the COMPOSED game frame (plate + actors + rings at viewport
   scale) with a "would a player screenshot and share this?" lens — the story-side felt-vs-scores lesson applies
   to graphics identically.
6. **★ HOUSE-STYLE ANCHOR (MANDATORY since 2026-07-08, the camp/market cadence regression)** — a PoE2/BG2
   control alone proves the plate beats a real-game bar; it does NOT prove the plate matches WorldOS's OWN
   established painterly hand. `camp_clearing_night`/`market_square` (backdrop-cadence-20260708) each scored
   6.0, "adopted," against only a disguised PoE2/BG2 control (market's was itself defective that round) plus a
   remembered/cited number for the incumbent-class bar — no in-panel image comparison to the actual best-in-
   class WorldOS plate was ever shown to scorers. Both plates read cartoonish/cel-shaded on later owner review.
   Fix: **every new-room adoption/verdict panel MUST also embed the current best-in-class WorldOS plate for
   that room family** (today: `crypt_dense_v1`, disclosed — not disguised — as "the house best"), with an
   explicit scorer question: *"does the candidate read as the SAME painterly hand / hit the SAME craft bar as
   this house-best reference, or does it look like a different, lesser pipeline?"* A candidate that beats its
   PoE2 control but loses the house-style read is a REGRESSION, not an adoption, regardless of the absolute
   number. This is distinct from L6 (gap to the PoE2 reference) — L6 checks the external bar, this checks
   internal consistency across WorldOS's own generated rooms.

The lenses (one subagent each):
1. **L1 registration / cohesion** — does the painted floor register with the gameplay grid under
   the locked camera? Does the whole frame read as ONE coherent space (no double-perspective, no
   plate seam, no mirrored asymmetry)? Gap to the ref's unified space.
2. **L2 occlusion / grounding** — are actors PLANTED (a visible soft contact shadow, feet meet the
   floor, they pass BEHIND props they should)? **Build on the pre-gate's G3 floor-contact numbers
   FIRST — if G3 PASSed, the actor IS grounded; CREDIT that.** When a contact shadow is visibly
   rendered (check the feet-crop), score it as PRESENT — do NOT report "zero contact shadow" on a
   frame whose feet clearly show one (the v2 validated false-negative). Only then flag the SUBTLER
   tell vs the PoE2 ref: is the contact shadow soft + blue-violet-tinted (PoE2) or hard-edged /
   pure-black / wrong-direction? Subject to the §③ auto-downgrade rule (an L2 CRITICAL that
   contradicts a G3 PASS is downgraded). (No "actor standing on the table.") Gap to how the PoE2
   ref's figures sit in the scene with soft cool contact shadows.
3. **L3 scene-light coherence** — are actors lit BY the scene's key light (warm hearth/lantern key,
   cool fill at ~3:1 per the spec `lighting`), with rim light, matching shadow direction, AND
   PoE2's blue-violet (never pure-black) shadows + ambient-from-plate bounce on the actor? Or flat,
   front-lit, "studio-lit cutout" with a black shadow? This is the dimension that most often tanks —
   gap to `poe2_market` / `poe2_tavern` warm-key wrap + cool fill + colored deferred lights.
4. **L4 character integration** — the decisive "grounded scene-lit REAL-3D actor vs pasted billboard"
   call. **WorldOS targets real 3D animated actors, NOT painterly sprites — so a clean, well-formed
   3D model that is GROUNDED (G3 PASS), scene-lit (picks up the plate's warm key + cool fill +
   ambient bounce, casts a soft blue contact shadow), and mildly stylized to sit in the painterly
   world scores HIGH.** A FLAT BILLBOARD (a 2D cutout that doesn't turn, no real volume, lit
   independently of the scene, hard/absent contact shadow) scores LOW. Do NOT penalize an actor for
   "looking 3D / too clean-edged / not hand-painted" — that is the GOAL now; the old "painted-vs-
   pasted-sprite" framing is REVERSED. Score the GAP to the PoE2 ref's mildly-stylized 3D actors:
   the tells that LOWER the score are now *flatness / billboard-ness / scene-light mismatch /
   missing-or-wrong contact shadow / saturation that ignores the plate's palette*, not "it reads as
   3D." Subject to the §③ auto-downgrade rule (an L4 CRITICAL contradicting a G3 PASS is downgraded).
5. **L5 tactical readability** — is walkable vs blocked legible? Party formation / focal actor
   clear? Do dark zones still read (blocked cells visible in shadow)? Gap to the ref's readable
   tactical space.
6. **L6 painterly-vs-reference** — pure art-direction craft of the BACKDROP plate: brush economy,
   value structure, color harmony (vibrant tropical-gothic PoE2 palette: saturated teals/jades +
   warm sandstone/amber), warm/cool contrast, atmospheric depth, NO decorative-frieze/border
   artifact (a known Scenario failure). Gap to the PoE2 ref's painterly quality.
7. **L7 MOTION** (scored from a render REEL, NOT a single still — runs only when a reel was rendered).
   **This lens is fed the reel contact-sheet PNG + the JSON sidecar** (`qa/motion_reel.py` builds
   both: a grid of the N reel frames in order + per-frame metadata {frame_idx, beat/anim label,
   actor centroid, t_ms, engine event}). Score how ALIVE and WEIGHTED the actor's animation is —
   the real-time-3D half of the PoE2 look — across these sub-dimensions:
   - **idle life** — does the idle breathe/sway, or is it a frozen statue? (a frozen idle is the
     G5 CRITICAL; the lens scores how *natural* a non-frozen idle is.)
   - **locomotion weight** — does the walk/run read with weight, footplant, and believable speed,
     or does the actor slide/skate (centroid moves but legs don't, per the sidecar)?
   - **attack anticipation / impact / follow-through** — a readable wind-up, a crisp impact frame,
     and a settle — or a single popped pose with no arc?
   - **hit-react** — does a struck actor flinch/recoil readably?
   - **death** — does the death animation read as a fall/collapse with weight, not a vanish?
   - **timing-sync to engine events** — do the anim beats line up with the engine events in the
     sidecar (the impact frame at the engine's `attack_lands` t_ms, the recoil at `damage_taken`)?
   - **turn-to-face** — does the actor rotate to face its target/move-direction (proving it is a
     real 3D actor, not a fixed billboard)?
   Return per-sub-dim scores + an L7 motion overall; build on the G5 pre-gate numbers (frozen-idle /
   no-walk-displacement) rather than re-estimating them. Gap to PoE2's mildly-stylized but weighty
   real-time-3D character motion. (See §⑤ — L7 logs to the `motion_*` scores_db columns, separate
   from the L1–L6 `visual_*` columns.)

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
Every round is one `visual` row (see `qa/scores_db_visual_patch.md` for the additive schema). The
L1–L6 STILL scores go in the `visual_*` columns; the L7 MOTION scores go in the additive `motion_*`
columns (`motion_overall` 0-10, `motion_dims_json` = the L7 sub-scores, `motion_reel_ref` = the reel
path/id). **`milestone` is an INDEPENDENT grouping tag (e.g. "M1.0"|"M1.2") — set it on EVERY visual
round, reel or not**, so a milestone's rounds group regardless of whether a reel was scored. Only
the three `motion_*` columns are NULL/omitted on a still-only round (empty == today):
```python
from qa.scores_db import add_run, set_canonical_baseline
add_run(run_id=f"vc-{scene}-r{N}-{sha8}", surface="visual", scorer_model="opus",
        methodology=f"vc-panel-7lens round={N}", build_sha=sha8,
        milestone="M1.2",                       # ALWAYS set — independent of reel availability
        visual_scene=scene_id, visual_backend="unity-cl", visual_round=N,
        visual_overall=overall, visual_dims_json={ "registration":L1, "occlusion_grounding":L2,
          "scene_light_coherence":L3, "character_integration":L4, "tactical_readability":L5,
          "painterly_vs_reference":L6 },
        # L7 MOTION — these three only on a reel round; omit/None on a still-only round:
        motion_overall=L7_overall, motion_reel_ref=reel_contact_sheet_path,
        motion_dims_json={ "idle_life":..., "locomotion_weight":..., "attack_arc":...,
          "hit_react":..., "death":..., "timing_sync":..., "turn_to_face":... },
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

## ⑦ TIER split — backdrop-BINDING vs actor-PLACEHOLDER (the gfx playable-demo gate)
The PoE2 painterly-CRPG is **2D backdrop (the reusable foundation) + 3D placeholder actors**. Score them on
DIFFERENT bars so placeholder actors don't drag the binding backdrop gate, and a good backdrop isn't masked
by rough actors:
- **TIER-1 BACKDROP (the BINDING room gate).** Score the **BACKDROP ALONE** — render the plate quad with NO
  actors/rings/VFX (a backdrop-only capture), or score the source plate PNG directly. GATE: **L6 ≥ 8 AND L1 ≥ 8
  AND detail ≥ 7 AND 0/3 washout AND pathing-map-correct** (you can read walkable floor vs walls/obstacles).
  This is what a *room* must clear before it's "done."
- **TIER-2 ACTOR / EFFECT / MOTION (placeholder-OK during the demo).** Score on the COMPOSITE. GATE: **pre-gates
  G1–G4 PASS** (grounded, in-cell, correct scale) **AND L2/L3/L4 ≥ 5.0** (grounded, scene-lit-enough, reads as
  belonging) **AND the backdrop still ≥ 8**. Actors are basic placeholders (a demo cast + default templates) we
  polish much later — do NOT block a playable demo on actor AA. (Pillar-4 reconciliation: placeholders are the
  PATH; real-art-via-the-proven-workflow is the destination.)
- **Combat-FUN checklist (binary, the felt gate for a playable round):** ☐ turns are visible (whose turn, initiative
  order) ☐ movement pathfinds (routes around painted walls) ☐ every action has feedback (swing/cast + VFX + damage
  number + a sound) ☐ rhythm (you-then-enemy, no dead air) ☐ a win/lose arc. All-yes = "would I play another round."
- **Diminishing-returns brake (don't re-loop a ceiling):** if a backdrop round yields **no material gain** vs the
  prior (or REGRESSES), the **5-round brake FIRES** — record the residual + the structural lever to break the
  ceiling (e.g. a carved-geometry greybox for L6 carved-stone), ADOPT the best plate as done-enough, and MOVE ON.
  A hit ceiling is a legitimate stop, not a failure to keep grinding.

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
- `qa/visual_pregate.py` (deterministic gates G1–G5; G5 = motion-liveness), `qa/motion_reel.py`
  (build the L7 motion reel contact-sheet + JSON sidecar — MODE A engine-state reel / MODE B
  timeline reel), `qa/visual_regression.py` (worse-vs-baseline, still + motion arms),
  `qa/scores_db.py` (the ledger, now includes `visual` surface + `visual_*` + `motion_*` + `milestone`
  columns).
- `asset-gen` (the gen pipeline fix_actions drive), the archived Godot dev skill at
  `extensions/renderers/godot/skills/godot-dev/SKILL.md` when explicitly reopened,
  `worldos-decide` (gate big calls at 95%), the engine story/mech QA loop in `worldos-dev` (the
  analogue this mirrors), Unity-pivot decision at `worldos-session-notes/2026-06-22-unity-pivot/`.
