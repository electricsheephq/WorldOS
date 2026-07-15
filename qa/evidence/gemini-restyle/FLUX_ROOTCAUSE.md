# "FLUX ENDPOINT REGRESSION" — CORRECTED POSTMORTEM: it was OUR call, not Scenario (2026-07-15)

## Final verdict (owner called it; proven byte-identical)
Scenario's flux-1-dev deployment NEVER changed. A repro with the correct parameter slot reproduces
the morning's winning v3.5 base BYTE-FOR-BYTE (asset_nLpb == job_Hdt/job_2rD-fixed md5 05a32d314fca,
1,014,723 bytes). Full determinism. The earlier "Modal regression" sections below the fold were a
MISDIAGNOSIS — retract the "report to Scenario" recommendation.

## The actual root cause: a parameter-SLOT swap introduced by context compaction
model_bfl-flux-1-dev has TWO image inputs:
- `controlImage` → ControlNet conditioning (what the registered pipeline uses)
- `image`        → img2img reference (strength-based; controlModality/controlStrength IGNORED)
The morning's winning calls passed `controlImage`. After the mid-session compaction, the call format
was reconstructed from a summary as `image:` — so EVERY subsequent "flux" draw (tavern cycle-1 bases,
all five crypt v3.6 draws, every "re-probe") was actually IMG2IMG OVER A GRAYSCALE DEPTH MAP:
pale/washed palette (the depth map's), loose ~1-cell structure (img2img, no CN), thread artifacts,
"deterministic failure" (same wrong slot every time). The "byte-identical repro" that indicted the
backend was never byte-identical — the recorded job inputs differ (winning job: controlImage; failing
repro: image). LESSON: repro claims must diff the SERVICE-RECORDED inputs, not the intended call.

## What this invalidates / keeps
- INVALIDATED: "Modal regression", "registration degraded server-side", the LoRA/Replicate
  routing theory (the LoRA path differed simply because those calls also mis-slotted differently),
  and the afternoon's tavern + crypt-v3.6 paint verdicts (all wrong-slot; re-run correctly).
- KEPT (independent evidence): the tavern cue-mass rule (v5 held 4/4 tables even mis-slotted);
  molded-kind vocabulary; the greybox→Gemini beauty route (real, panel 7.5, ~0.8-cell recompose);
  best-of-N selection (the PROMOTED recipe's own precedent, cs0.7 best-of-3).

## The systemic fix (the owner's structural demand)
No freehand model calls, ever: qa/paint_room.py is now THE painter — prompts/params come from
room_recipes.json entries, the API slot is pinned (`controlImage`), every job's recorded input is
logged next to its output. A fresh/compacted agent runs a COMMAND, not a remembered call shape.
