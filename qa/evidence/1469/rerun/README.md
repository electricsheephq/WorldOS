# W6.0 relight A/B RE-RUN (#1469, post-#1470 resequence)

The original #1469 A/B (PR #1473, `qa/evidence/1469/`) FAILED on **input registration**: the shipped
crypt plate (`crypt_dense_v1`) was a full-frame img2img OUTPAINT, but the WOSRelight depth/normal
sidecars frame a centered diamond on black -> the geometry-driven light landed as a floating,
misaligned rectangular band. The verdict resequenced #1470 (ControlNet conditioning) onto the critical
path: a conditioned plate inherits the greybox framing **by construction**, making the same sidecars
register 1:1. This directory re-runs the exact A/B on such a conditioned plate.

## Pipeline (mirrors #1470 armB, single-pass)

`generate_room.py --controlnet depth --room crypt` with the crypt greybox render (1344x768, the same
framing as the `Captures-Durable/room_greybox_{depth,normal}.png` sidecars) as the depth control image
(`model_bfl-flux-1-dev`, controlStrength 0.7, seed 42). Deployed to the GEX44 box PaintedBackdrop;
baseline cast + RungR relight via the Cohesion Probe menus; captured at super_size 2.

| file | what |
|------|------|
| `plate_conditioned_crypt.png` | the #1470-conditioned crypt plate (ControlNet depth) |
| `greybox_control_1344x768.png` / `crypt_greybox_depth.png` | the greybox control + the relight's depth sidecar |
| `overlay_conditioned_crypt.jpg` | greybox structural edges (magenta) over the plate — the registration check |
| `A_flat_conditioned.jpg` | Arm A — flat Unlit/Texture conditioned plate + cast (shipped look) |
| `B_relit_conditioned.jpg` | Arm B — WOSRelight (RungR) on the backdrop quad |
| `panel_verdict.json` | 5-scorer blind integration panel (A/B + PoE2 control C) |
| `build_overlays.py` | deterministic offline regenerator of the overlay + edge-recall metric |

## Result

- **Registration: FIXED.** Edge-alignment recall **1.000** vs both the greybox structural edges and the
  depth sidecar (camp armB was 0.870). The relit shading now lands ON the painted pillars/sarcophagus.
- **Panel:** A(flat)=**3.0**, B(relit)=**2.0**, C(control)=**8.0** (control VALID, in the 6.8-9.2 band).
- **B still lost to A — but for a NEW reason.** WOSRelight crushes all vertical geometry (pillars,
  sarcophagus, walls) to **solid black** on this low-key/cool conditioned plate (every scorer named
  "black-block voids"). Root cause is relight parameterization + a flat single-pass plate (cool key
  sampled, no warm fire pool), NOT registration.

## Recommendation

Registration blocker resolved; do **not** port relight to the shipped path yet. Next iteration:
(1) raise the WOSRelight ambient/bounce floor + add the hearth point-light fill so vertical normals
never go black; (2) regenerate the conditioned plate `--layered` (firelit rides on the locked geometry,
preserving registration) so it has a warm fire pool + painterly polish. Then re-run this exact A/B.
