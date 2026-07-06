# FELT REST-SCENE panel — "does this read as a real game AT REST?" (W1 / #1318)

> The visual eval instrument for **Scene at Rest** (Act II W1). A location renders AT REST —
> party + present NPCs placed on the grid in idle poses, no combat — and this panel asks ONE
> question the scores can't fake: **does a composed rest frame read as a shipped CRPG at rest,
> or as a tech demo with actors standing on a board?** It is the visual sibling of the story
> FELT lens (`qa/rubric_tolkien.md`): a rest tavern should feel *inhabited* — the innkeeper is
> THERE before anyone draws a sword.

This is a PROTOCOL + a thin logger. The actual panel runs LATER, on real rendered frames (the
renderer's rest mode, step 4 of #1318). Build the instrument first so the rung is decided by
EVAL, not by vibes.

---

## The one law you cannot break: the disguised positive control
This panel inherits the **CALIBRATION-CONTROL LAW** proven 2026-07-02 (see
`.claude/skills/visual-critic/SKILL.md` §CALIBRATION-CONTROL PROTOCOL, and memory
`feedback_visual_panel_scoring_variance`): our panel's ABSOLUTE scale is broken at the top and
**cannot be cited as a quality verdict**. Real shipped CRPG art (BG3 / Pillars of Eternity /
BG2EE) scored 3.0–5.6 on our own instrument; scorers even confabulated "diffusion-model tells"
on hand-painted 1998 art. So:

1. **No AI-prior primers.** The scorer prompt NEVER says "AI render", "diffusion", "candidate",
   or "almost nothing deserves ≥8". Those primers cost ~0.7 pt and made ≥8 unattainable BY
   CONSTRUCTION. The scorer is told only: *"rate how much each frame reads as a real,
   commercially-shipped CRPG town/interior at rest."*
2. **Every panel embeds ≥1 DISGUISED REAL-GAME CONTROL** — a genuine shipped rest scene (a BG3
   tavern-at-rest screenshot, a Pillars town-at-rest frame) NOT among any refs, cropped UI-free
   to comparable resolution, shuffled in among our frames under a neutral id (`frame_03`).
3. **The reportable metric is the DELTA vs the control's same-panel score**, never an absolute.
   `ours_median − control_median ≥ 0` ⇒ *"reads as a real game at rest"* — the W1 bar is MET.
   An absolute number from this instrument is NEVER a quality verdict; only the delta + the
   flaw list are.
4. **Blind mapping lives OUTSIDE the panel image dir** (scorers Read adjacent files). ≥5 blind
   scorers per panel; report **median with mean**; within-panel comparisons only (cross-panel
   drift is real, ±1.2). Eyeball the frames yourself 0-for-5 before trusting any panel.

---

## What a rest frame is scored ON (5 lenses, 0–10 each)
The rest scene is PURE PRESENTATION of engine state, so the lenses are about *placement +
inhabitation + coherence*, not combat readability:

| id | lens | the "real game at rest" tell |
|----|------|------------------------------|
| `placement_plausibility` | Are the party + NPCs standing where PEOPLE stand — near the bar, by the hearth, at a stall — not on a bare tactical grid in a firing line? | figures cluster at meaningful anchors, not evenly spaced on cells |
| `inhabitation` | Does the room read as LIVED-IN — the innkeeper present, the place peopled — vs an empty stage with the party dropped in? | ≥1 non-party NPC visibly present and belonging |
| `idle_life` | Do the actors read as at ease (idle pose, weight settled) rather than combat-ready / T-posed / frozen mid-stride? | relaxed idle silhouettes, varied facing |
| `scene_light_coherence` | Do the actors sit inside the plate's light (warm hearth key, cool fill) as ONE painting, not lit by a different sun? | rim/key direction matches the plate mood |
| `grounding_integration` | Feet on the floor plane, correct depth scale, no "pasted-on" float — the #1 illusion-breaker. | feet contact stone; scale reads with depth |

Deterministic pre-gates (`qa/visual_pregate.py`: frame-lit, floor-contact-Y, screen-scale) run
FIRST and short-circuit the panel on a CRITICAL — there is no point asking 5 scorers to admire a
peopled tavern while the innkeeper's feet float half a cell above the floor.

---

## Panel composition (the two calibration frames #1318 names)
Build ONE calibration panel with BOTH rest scenes + their disguised controls:

- **tavern-with-innkeeper** — a rest tavern, party by the entrance, the innkeeper at the bar
  anchor. Disguised control: a real BG3/Pillars tavern-at-rest crop.
- **church-with-priest** — a rest church/interior, party mid-floor, a priest at the altar/anchor.
  Disguised control: a real shipped chapel/interior-at-rest crop.

Each panel: our frame(s) + ≥1 disguised control, shuffled, neutral ids, mapping stored outside
the image dir. ≥5 blind scorers, ≥2 panel runs averaged (±1.5 per-lens variance).

---

## Logging (existing schema — surface="visual", zero migration)
Log every panel round to `scores_db` via the existing visual-critic lane (`qa/scores_db.py`,
surface="visual"): `visual_overall` (holistic rest read), `visual_dims_json` (the 5 lenses
above), `visual_scene` (`rest:tavern-innkeeper` / `rest:church-priest`), `visual_backend`
(`unity-cl`), `visual_round`, `visual_pregate` (the pre-gate verdict). The control's row is
logged too (scene id suffixed `:control`) so the delta is queryable from the ledger, not just
the panel report. `qa/felt_rest_panel.py` is the thin logger/summarizer that wraps this.

## The W1 binding gate (charter #1328)
> rest-scene panel scored (control-anchored, logged scores_db) + text-tier byte-identity test
> green + no combat-mode regression.

The panel PASSES W1 when, on ≥2 averaged runs of the calibration panel, **each rest scene's
median ≥ its disguised control's median** (delta ≥ 0), with no open CRITICAL pre-gate. Report
the delta + the flaw list; never cite the absolute.
