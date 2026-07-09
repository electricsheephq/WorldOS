# W6.3b (#1470) — `generate_room.py --controlnet` A/B + relight-registration evidence

**Claim proved:** conditioning the base/layout pass on the room greybox (ControlNet depth) drives
paint-vs-grid drift to ~zero **and preserves the greybox framing** (so the W6.0 relight stack can
register its depth/normal sidecars against the plate) **without losing paint quality.**

Room: `camp_clearing_night` (16×12 open-air clearing). Same greybox, seed 42, 1344×768.
Greybox produced headlessly: `seed_gfx_camp_clearing.py` → `export_scene_grid.py` →
`greybox_render_headless.py` (no Unity/box).

| file | what |
|------|------|
| `greybox_control_1344x768.png` / `greybox_control.jpg` | the depth control image (authored geometry) |
| `plate_armA_current.jpg` | Arm A — **current path** (unconditioned img2img, `strength 0.45`) |
| `plate_armB_controlnet.jpg` | Arm B — **`--controlnet depth`** (Pipeline A, `model_bfl-flux-1-dev`, `controlStrength 0.7`) |
| `overlay_armA_current.jpg` / `overlay_armB_controlnet.jpg` | greybox structural **edges (magenta) composited over each plate** — the registration check |
| `meta_armA_current.json` / `meta_armB_controlnet.json` | provenance (job ids, params; no credentials) |
| `build_overlays.py` | deterministic, offline regenerator of the overlays + metric |

## Registration verdict (relight criterion)

**Edge-alignment recall** = fraction of greybox structural-edge pixels that have a painted edge within
3 px (brightness-robust; Arm B is firelit/dark so a luma-silhouette IoU is unreliable):

| arm | edge-recall vs greybox |
|-----|------------------------|
| Arm A — current img2img | **0.686** |
| Arm B — `--controlnet depth` | **0.870** |

- **Arm B (conditioned): framing PRESERVED.** In `overlay_armB_controlnet.jpg` the magenta greybox
  edges land on painted structure — the floor-diamond outline traces the painted floor edge, all four
  tree columns, **both** boulder cubes, the three bedrolls and the crate sit under their authored
  outlines. No outpaint / fill-the-frame drift; depth/normal sidecars register.
- **Arm A (current): DRIFT.** In `overlay_armA_current.jpg` the boulder-cube and bedroll outlines
  hover over **empty painted floor** (props dropped by unconditioned img2img) and the floor's
  lower-right magenta edge falls over black/torn paint (the floor was reshaped/outpainted). Sidecars
  would mis-register against dropped props and the torn floor edge.
- **Paint quality NOT lost:** Arm B is fully painterly (cracked-earth floor, mossy stone columns,
  firelit chiaroscuro) even though the z-image painterly LoRA is dropped on the flux control pass —
  flux.1-dev rejects it (`Allowed model types: flux.1-lora`). The `--layered` Gemini passes (unchanged)
  ride on top in production for further polish; this single-pass A/B already clears the bar.

## Drift checker (`qa/check_plate_drift.py`, #1472, MERGED)

Not run here: its `check` mode needs a per-prop `*.cells.json` fingerprint manifest **and** a
known-good painted baseline plate to template-match against — neither exists for a first-ever camp
ControlNet plate (those are drift-lane fixtures). The edge-alignment registration metric above
substitutes and directly measures the relight-registration criterion #1470 gained. Building the camp
`cells.json` + baseline and running the NCC gate is the natural follow-up on the drift lane.
