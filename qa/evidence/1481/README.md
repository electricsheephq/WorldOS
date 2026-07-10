# #1481 — relight A/B on the ARM-B winning styled plate (the relight-blocker unblocking test)

The #1469/#1480 relight verdict left ONE open lever: the relight tied (never beat) the flat plate only
because the sole ~1.0-registered plate was **cool** (single-pass ControlNet base → RungB sampled a cool
key `DDE0FF` → mild cool shaping, no warm cohesion). #1481 supplies the missing lever — a **warm, firelit
crypt plate at ~1.0 registration** (the plate-sprint **ARM-B iter3** winner) — and re-runs the *same*
committed A/B with only the plate changed.

## Plate under test
`plate-sprint/armB/iter3/style/prep_iter3_plate.png` → box `Assets/painterly/backdrops/styled_crypt_iter3_ab.png`.
- Registration **0.9903** edge-recall vs the crypt greybox (≥0.95 gate PASS).
- Plate-only panel median **8.0** = TIES the incumbent house-anchor `crypt_dense_v1`.
- On load, `Analyze()` sampled a **WARM** key `#FFC16A` (vs iter3's cool `DDE0FF`) — the intended variable
  is isolated: the warm+registered plate now feeds warm relight.

## Method (isolates ONLY the backdrop shader)
`M1CombatV1_canonical`, in-memory (never saved; Reset discards). Plate swapped onto `PaintedBackdrop`;
camp occluders + combat cast disabled (33 objs); Analyze cache force-invalidated. Baseline cast
`Actor_hero@(6,6)` + `Actor_goblin@(9,5)` via Cohesion Probe **0b**.
- **A (flat):** 0b + **RungB** actor rig + flat `Unlit/Texture` backdrop.
- **B (relit):** **RungR** (= idempotent RungB + `WOS/Relight` backdrop w/ the #1480 uniforms: `_AmbLift`
  0.80, `_WarmBounce` 0.25, hearth point-fill @ plate fire anchor, crypt `room_greybox_{depth,normal}.png`
  sidecars).

Only the backdrop shader differs A→B. `super_size 2` (5120×2880), both non-black. See `AB_compare.jpg`.

## Result — the relight LOSES on the warm plate (control-independent)

| arm | median | rank |
|-----|--------|------|
| **A_flat** (styled plate, flat) | **7.5** | 4/5 A>C, 1/5 C>A |
| **B_relit** (#1480 relight) | **2.5** | **5/5 LAST** |
| C_control (PoE2 real art) | 6.5 | — |

> **Control is INVALID this panel** (C=6.5 < the 6.8–9.2 floor → `control_valid:false`). So the panel's
> *absolute* numbers are NOT citable as a passed validity bar, and this is NOT a control-parity or
> styled-plate adoption claim. The load-bearing finding below is **control-independent**.

- **PRIMARY (control-independent) — the relight REGRESSES hard.** B=2.5, **unanimous last**, on a NEW
  **vertical-banding artifact** every
  scorer named. Root cause: the crude *shared greybox* depth/normal sidecars (2 flat walls + 2 pillars)
  relighting a far richer, high-contrast warm plate (carved pillars, sarcophagus, arch) print the greybox
  seams through as stripe banding — invisible on iter3's low-contrast **cool** plate (same shader+sidecars,
  no banding), glaring here. The relight's premise (a cool/flat plate needs geometric relighting for warmth)
  is **invalidated** by a plate that arrives warm+firelit: it can only darken and stamp seams. Only the
  backdrop shader changed A→B, so the 5.0-point **unanimous** drop is attributable to the relight alone —
  no reliance on the (invalid) control.
- **SECONDARY (control-soft, advisory).** A_flat's high absolute (7.5, above C, A>C on 4/5) is *consistent*
  with the warm styled plate integrating well — but with `control_valid:false` this is **not** a parity
  claim. The styled plate's actual house-anchor evidence is a **separate** panel: in its plate-sprint
  ARM-B iter3 panel the plate scored **8.0 = ties the incumbent `crypt_dense_v1`** (that panel, not this
  one, is the plate-adoption gate).

## Recommendation — STOP the relight loop
Do **not** port relight to the shipped path. `WOSRelight` is additive / all-uniforms-default-0 /
byte-compatible-when-unset and nothing shipped lights it, so the #1480 merge stays safe-dormant — nothing to
gate off. **Basis = the control-independent relight regression above, not this panel's absolute numbers.**
The styled-plate pipeline remains the cohesion path, but its **adoption** rests on the plate-sprint anchor
panel (plate tied `crypt_dense_v1` at 8.0), not on this control-invalid panel — before any promotion, re-run
a FELT/visual panel with an **in-band control + the house anchor**. If relight is ever revisited it needs a
**per-plate** depth/normal sidecar (from that plate's own conditioning geometry), never the crude shared
greybox — that mismatch is the binding banding defect. Diminishing returns: 4 A/B rounds, no shader lever
left; the relight is done.

Ledger: recorded as run `1481-relight-on-styled-plate-panel-1` (surface=visual) in `qa/scores.db` /
`qa/scores_ledger.md`.

Blind mapping (recorded for audit): `img_alpha=A_flat, img_beta=B_relit, img_gamma=C_control`.
Durable full-res captures on box: `Captures-Durable/1481_{A_flat,B_relit}.png`.
