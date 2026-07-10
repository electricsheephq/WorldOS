# W6.0 relight A/B — iteration 3 (#1469, post-#1479 resequence)

`#1479` (rerun) FIXED input registration (edge-recall **1.000**) but B (relit) still LOST to A (flat),
**2.0 vs 3.0**, on a new control-independent defect: WOSRelight crushed every away-facing vertical
normal (pillars, sarcophagus, walls) to **solid black** under the lone cool key — every scorer named
"black-block voids". The verdict prescribed two coupled fixes; iter3 lands both and re-runs the A/B.

## Changes under test

1. **Shader** — `extensions/renderers/unity/shaders/WOSRelight.shader`, additive, all new uniforms
   **default 0 (byte-compatible when unset)**:
   - `_AmbLift` — ambient **FLOOR** (`lit = max(lit, _AmbLift)`); no vertical normal renders below a
     readable painted value. Mirrors `PainterlyActor._AmbientLift`. RungR feeds **0.80**.
   - `_WarmBounceCol` / `_WarmBounce` — warm floor/hearth **bounce wrap** for side/down-facing
     verticals (N.y~0 → full warmth). RungR feeds plate `_warmAmb` @ 0.25.
   - `_Hearth` / `_HearthCol` — dedicated **hearth POINT fill** at the plate fire anchor, in the
     greybox view space, z-pinned mid-depth (robust to the P-vs-ViewNormal z-convention). RungR feeds
     `_key * 0.6`.
2. **RungR** — `extensions/renderers/unity/scripts/CohesionProbe.cs`: `RungR` + `LoadSidecar` brought
   into the repo (were box-only) and extended to feed the three new uniforms. Reproducible.

Both files synced to the box (`Assets/painterly/shaders/WOSRelight.shader`,
`Assets/Editor/CohesionProbe.cs`, chown unity); clean compile (0 errors).

## The plate: firelit `--layered` was ATTEMPTED and REJECTED (a key finding)

The verdict wanted a FIRELIT conditioned plate via `generate_room.py --controlnet depth --layered`
so RungB samples a *warm* key. That was run (seed 42, mirroring #1479's base pass byte-for-byte). But
the `--layered` premise — "firelit rides the locked geometry, preserving registration" — is
**empirically false**:

| pass | edge-recall vs greybox structural edges |
|------|------|
| pass1 ControlNet base | **1.000** |
| pass2 Gemini detail/populate | 0.873 |
| pass3 Gemini staging (final) | **0.708** |

The Gemini instruction-edit passes have no strength knob and **recomposed** the geometry — the staging
pass invented a central staircase and shifted the left half off the greybox (`overlay_conditioned_crypt.jpg`;
the final firelit plate is `plate_conditioned_crypt.png` — gorgeous but 0.708). A 0.708-registered plate
would re-misalign the WOSRelight sidecars — the original #1469 failure. **"Firelit AND 1:1-registered"
is unattainable via this pipeline.** So the A/B was run on the **registered (cool) base** plate
(`crypt_rerun_conditioned`, recall 1.000 — the #1479 plate held CONSTANT), isolating **only** the
shader change: a clean before/after.

## Result

- **Control valid:** C (PoE2, disguised) median **8.0**, in the 6.8–9.2 band. Numbers trustworthy.
- **Panel:** A(flat) = **2.0**, B(relit) = **2.0**, C(control) = **8.0**.
- **The #1479 LOSS is RESOLVED — but no decisive WIN.** B and A are **tied** at 2.0, yet all 5 scorers
  **ranked B > A**. In-frame the ambient floor **eliminated the black-block voids** (pillars/sarcophagus/
  walls read as painted stone, not black — `B_relit_iter3.jpg`). B went from LOSING (2.0<3.0) to
  TIED/marginally-preferred. It doesn't *beat* A because the registered plate is cool/flat → RungB
  sampled a **cool** key (DDE0FF), so the relight adds only mild cool shaping, no warm cohesion. Both
  arms sit ~6 pts below the control — a dull-cool-plate + dark-empty-room confound, not a relight defect.

## Recommendation — STOP the relight-shader loop

Merge the shader + RungR change (additive, byte-compatible, resolves the void failure, no-harm,
5/5 preferred). **Do not port relight to the shipped path yet.** The remaining gap is a **plate-pipeline**
problem, and the two prescribed fixes are now **mutually exclusive**: 1:1 registration (single-pass
ControlNet, *cool*) **or** a warm firelit plate (`--layered`, registration 0.708), never both. Further
relight progress is blocked on a NEW work item — a warm firelit plate at ~1.0 registration (a ControlNet
base that keeps the warm painterly grade, or feeding warmth via the light RIG on a neutral registered
plate) — which is out of scope of "relight iteration". Per diminishing returns (3 rounds, no win, no
lever left for a 4th relight-shader round), **stop here**.

Regenerate the registration overlay + recall metric: `python3 build_overlays.py`.
