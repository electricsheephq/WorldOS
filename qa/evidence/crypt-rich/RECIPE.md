# CRYPT-RICH recipe (reproducibility)

Lane: beat the crypt incumbent (crypt_armb_iter3, panel 8.0) HONESTLY via the geometry-richness
principle (epic #1508, PR #1528 lesson). The #1528 attempt capped at 7.1 because the greybox was the
sparse 3-prop true-greybox; this lane AUTHORS a denser greybox so the paint has ornament volumes to carve.

## 1. Geometry (denser)
tools/author_room_geometry.py crypt_rich -> qa/evidence/crypt-rich/crypt_rich_geometry.json
- Canonical layout kept EXACTLY: pillars (3,3)/(3,4)+(8,9)/(9,9); full 12-cell coffin cols3-7 x rows6-8;
  door zones (6,0)+(13,4) marked/kept clear. +11 ornament volumes (reliefs/niches, engaged column,
  torch brackets, rubble, broken slabs, skull pile, spilled urn). Walkable topology flood-fill CONNECTED.
- Manifest derived: qa/room_manifests/crypt_rich.cells.json
- Greybox: CUTAWAY wall_height=5 (enclosed-room convention) so all interior volumes are visible to the
  depth-ControlNet controlImage. qa/evidence/crypt-rich/crypt_rich_greybox.png (1344x768).

## 2. Registered base — flux depth-ControlNet + interior LoRA (Scenario)
model_bfl-flux-1-dev, controlModality=depth, controlStrength=0.7, controlImage=<cutaway greybox>,
loras=[model_G379oza2qhm6MkqDrtTvvmmw] lorasScale=[0.85], 1344x768, numOutputs=3, steps=28, guidance=3.5,
seed=12345. Adopted base = generation #2 (asset_rYC4EXay2XdSjACLmPFNuLgj) -> candidate_flux.png.
Prompt: qa/evidence/crypt-rich/base_prompt.txt

## 3. Style pass — Gemini 3.1 structure-lock enrichment (Scenario)
model_google-gemini-3-1-flash, referenceImages=[<candidate_flux base asset>] (the ONLY reference — it is
minted from the greybox so it is greybox-aligned, the sanctioned case; NO external style anchor per the
reference-images LAW), aspectRatio=auto, resolution=2K, thinkingLevel=HIGH, numOutputs=3, seed=123.
Prompt (STRUCTURE-LOCK + scene-content grounding, no dimetric-lock — camera pinned by the base ref):
  qa/evidence/crypt-rich/style_pass_prompt.txt
Adopted candidate = generation #3 (asset_ssap7oAn9ZvywsBFTuL9t94i) resized to 1344x768 ->
candidate_final.png (gemini/gem_3.png).

## Gates
- visual_pregate.py (the promotion HARD FLOOR): PASS (frame-lit + occupancy + floor-contact).
- check_grid_paint_coherence.py: INCOHERENT (diagnostic only — the 8.0 incumbent ALSO fails this strict
  absolute gate identically: painterly-softening + tall-silhouette-vs-footprint divergence; promote.py's
  visual gate keys on visual_pregate + the blind-panel delta, NOT this gate).

## CU spent: base 26 + tighter A/B 27+27 + Gemini 60 = 140 CU (cap 400). No training.
