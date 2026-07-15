# FLUX ENDPOINT REGRESSION — root cause (owner asked "figure out why", 2026-07-15)

## Cause: CONFIRMED Scenario Modal-side, NOT our end / not GEX
Systematic bisect (5 probes over ~2.5h, all 9 CU):
- Byte-identical repro of this-morning's WINNING job (job_fxTssiHBxHfkSnf8TtAqjKs5: same depth
  asset_JvC4tSTYsci5Gv53CiMUgsUi, same prompt from its metadata, seed 12345, cs0.85, 1344x768, 28
  steps, g3.5, NO loras) → CLAY, not the painterly base it made at 09:38 UTC. Seed determinism BROKEN.
  ⇒ same inputs, different output = the model DEPLOYMENT changed. Not our params (identical), not the
  box (this is the Scenario API, GEX is uninvolved).
- Params bisect (all exonerated): apron prompt sentence removed → still clay; depth-remap reverted →
  still clay; prompt verbatim vs v3.6 → both clay. Geometry/prompt/remap all innocent.
- BACKEND SPLIT is the tell: model_bfl-flux-1-dev (subProcessor=Modal) regressed to a pale/clay
  prior; the interior-LoRA path model_G379 (subProcessor=Replicate) STILL paints painterly on the
  same depth+seed. ⇒ the Modal deployment of bare flux-1-dev depth-CN changed; Replicate is fine.

## The registration tradeoff (measured)
| path | backend | beauty | registration (brazier blob-solve vs boxes) |
|------|---------|--------|--------------------------------------------|
| bare flux (this AM) | Modal | painterly | 0.05 cell (the gold standard) |
| bare flux (now) | Modal | CLAY (regressed) | — |
| flux + LoRA 0.4 | Replicate | painterly | ~1.4-3.7 cell (LoRA loosens CN) |
| flux + LoRA 0.2 cs0.9 | Replicate | painterly, pale | structure 38px residual, braziers ~1.6 cell |
| greybox → Gemini | — | BEST (panel 7.5) | ~0.8 cell (Gemini recomposes) |

## Recipe map under the outage
- BEAUTY PREVIEW / non-walkable: greybox→Gemini (best) or flux+LoRA Replicate.
- REGISTERED shipping (0.05 cell): needs Modal bare-flux recovery — the original chain. Re-probe.
- The brazier drift in the LoRA path is partly PROMPT-fighting-depth (prompt places braziers "flanking
  the doorway"; the depth's brazier cells differ) — a depth-led prompt would tighten it; queued lever.

## RECOMMENDATION
Worth a note to Scenario: their Modal flux-1-dev depth-CN deployment changed ~2026-07-15 10:00 UTC
(a byte-identical job that worked at 09:38 now returns a different, flat result). Pipeline is otherwise
proven; registered-beauty resumes on Modal recovery. Re-probe: bare flux, depth asset
asset_JvC4tSTYsci5Gv53CiMUgsUi, seed 12345 — painterly return = recovered.
