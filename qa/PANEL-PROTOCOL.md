# Blind beauty-panel protocol (THE versioned ruler)

**Why this file exists (2026-07-16):** an ad-hoc rewording of the scorer prompt caused ~1 point of
pure ruler drift — the SAME shipped plates read Δ−3.0/−2.5 under the new wording vs Δ−2.0/−1.0 at
ship time. A Δ-vs-control band is only comparable under the SAME scorer wording. Nobody freehands
the ruler (the paint_room lesson, applied to panels): the wording below is canonical; change it
only with a scorecard entry re-anchoring every shipped reference under the new wording.

## Scorer prompt (canonical wording — v1)

> You are scoring two isometric fantasy-RPG background paintings for hand-painted concept-art
> quality (composition, painterly texture, lighting/chiaroscuro, material read, atmosphere).
> Read image A at {first} and image B at {second} with the Read tool. Score EACH on 0-10
> (pre-rendered PoE2/BG3-class = 8-10; clean but generic = 5-7; flat/AI-artifacted = 0-4).
> They are unrelated images from different projects — judge each on its own merits.
> FORMAT RULES: return ONLY the structured output; score_a = image A, score_b = image B.

Mechanics: 5 independent scorers (sonnet, low effort), A/B order alternated per scorer, medians
reported, structured output (score_a, score_b, one-paragraph notes each).

**Executable ruler:** `qa/panel_workflow.mjs` — run via the Workflow tool with
`{scriptPath: "qa/panel_workflow.mjs", args: {control: <poe2 ref>, rooms: [{id, plate}, ...]}}`
(absolute paths). The scorer wording above is embedded there; edit BOTH together, never one.

## The two-anchor rule

Every panel batch includes:
1. **The PoE2 control** — `/Users/m1/Codex/worldos-refs/poe2_ruins_brazier_integration_01.jpg` (sha256
   `69fbb979a00f0d59685847979af74b9fa882a6f7062c96456fe64a479e26e7fb`, 855,066 B). OFF-REPO BY DESIGN: it is a
   frame of a shipped commercial game and must never be committed; re-pinned 2026-09-02 after the former
   `/Volumes/LEXAR/WorldOS-Unity-spike/refs/` copy died with that drive. `qa/visual_controls_identity.json`
   carries the same path. Pass it to `panel_workflow.mjs` as an ABSOLUTE path. Anchors the TOP of the scale.
2. **At least one SHIPPED plate as a disguised calibration reference** (e.g.
   `plates/shop_v1_registered.png`) — anchors the SHIP BAR. The verdict is "candidate within ~0.5
   of the calibration reference in the SAME run", never the raw Δ band across runs.

## Blinded adjudication (the round-5 lesson)

The agent that authored a cycle must NOT render its gate/panel verdict. Verdicts are rendered by a
fresh agent given ONLY: the instrument outputs (panel medians + calibration reference, err_cells
solve, walk-gate report) and the acceptance bars — no cycle history, no narrative, no knowledge of
schedule pressure ("last cycle before the brake" produced two falsified adoptions on 2026-07-16).
An author who disagrees with a blind verdict escalates with a NEW measurement, never a story.
