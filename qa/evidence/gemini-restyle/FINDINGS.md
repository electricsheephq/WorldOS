# GEMINI-RESTYLE ROUTE — beauty unlock + registration tradeoff (2026-07-15, flux-outage response)

## Context: the flux depth-CN endpoint regressed mid-run (probe-verified, external)
Byte-identical repro of the v3.5 winning flux job now returns clay/vector output; seed determinism
broken ~10:00 UTC; stable + deterministic 1.5h later. The REGISTERED-base half of the pipeline
(flux depth-CN, 0.05-cell registration) is externally blocked. This lane found a route around it.

## The route: greybox → Gemini structure-lock style pass (NO flux)
The Unity greybox render is pixel-perfect registered to the occluder boxes BY CONSTRUCTION. Feeding
it straight to model_google-gemini-3-1-flash (structure-lock + scene-grounding prompt, 2K, thinking
HIGH, seed 123, no referenceImages per the reference-images law) produces a genuinely PoE2-caliber
painterly crypt in ONE model call — carved knight effigy, weathered mossy stone, warm-core/cool-corner
chiaroscuro, frame-filling moody edges. Fixes the v3.5 panel's 3 named defects (carving fidelity,
flat lighting drama, dead-apron vignette).

## Blind panel (partial: 3/5 scorers, 2 lost to a session-limit window — re-run for a full read)
| slot | plate | median |
|------|-------|--------|
| A | greybox→Gemini (v3.6) | 7.5 |
| D | clay-flux→Gemini (v3.5 layout) | 7.8 |
| B | PoE2 real-art control | 9.3 (in-band) |
| C | incumbent crypt_fresh_v1 | 5.5 |
Both Gemini routes BEAT the incumbent by ~2 pts and cleared the v3.5's 6.2. Delta vs a 9.3 control
= −1.5 (just under the −1.2 bar; note the control anchored harsher than the prior panel's 8.8).

## ★ THE HONEST TRADEOFF (why this is not yet the adopted walkable recipe)
Gemini RECOMPOSES: given the greybox it restyles faithfully in style but MOVES props. Blob-solve vs
the v3.6 sidecar (qa/overlay_boxes.py): 3 braziers 0.76–0.80 cells off, tomb shifted, one brazier
2.03 cells. Edge-recall vs its own greybox 0.73 (a registered flux base is 0.90+). So:
- flux depth-CN base: REGISTERED (0.05 cell) but currently CLAY (endpoint broken)
- greybox→Gemini:     BEAUTIFUL (panel 7.5) but ~0.8-cell DRIFT (Gemini recomposes)
The UNIFY-THE-FRAMES thesis is paint==occluders; 0.8-cell drift weakens it (actors clip ~half a
cell). Registration is why the pipeline used flux-depth-CN in the first place.

## Verdict + next levers
- greybox→Gemini is ADOPTED-TRACK for BEAUTY and as the flux-outage fallback, NOT yet for the
  walkable/collision pipeline (registration bar unmet).
- On flux recovery: the original chain (flux-registered base → Gemini) gives BOTH. Re-probe hourly.
- Registration-recovery idea (untested, flux-blocked): greybox→Gemini beauty → low-strength flux
  depth-CN snap-back to the depth structure. Test when the endpoint returns.
- Full 5-scorer panel owed (partial here); a fresh control frame may lift the delta.
