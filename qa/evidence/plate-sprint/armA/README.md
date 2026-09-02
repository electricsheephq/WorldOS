# PLATE SPRINT — ARM A evidence (crypt, iteration 1)

**ARM A** = two-stage *registered base + style pass*: a flux `--controlnet depth` base (edge-recall
~1.0 vs the greybox) followed by a z-image + painterly-LoRA img2img **style pass** at low strength,
drift-gated by the registration gate. Reachable today, zero new capability.

## Configs (style strength sweep, controlnet depth @ 0.7, seed 42)

| config | style strength | registration (edge-recall vs greybox) | gate | panel median (5 blind sonnet scorers) |
|---|---|---|---|---|
| str025 | 0.25 | 0.9655 | **PASS** | 4.5 |
| **str035** | **0.35** | **0.9701** | **PASS** | **5.5 (best)** |
| str045 | 0.45 | 0.9043 | FAIL | — |
| str045→retry | 0.40 | 0.9399 | FAIL | 5.0 (beauty data point) |
| incumbent `crypt_dense_v1` | — | — | — | 8.0 |
| PoE2 control | — | — | — | 9.5 (above band; scale loose at top) |

Base pass alone (flux controlnet, no style) = **0.9996** registration.

## Convergence

Target (coordinator-corrected): **registration ≥ 0.95 AND panel median ≥ 7.0**; comparator = incumbent 8.0.

- Best ARM A candidate **str035**: registration **0.9701 PASS**, panel median **5.5**.
- Δ vs ceiling (7.0) = **−1.5**;  Δ vs incumbent (8.0) = **−2.5**.
- **NOT CONVERGED** on beauty (registered, but under the 7.0 bar).

## Finding

A **structural** ceiling, not a tuning miss: style strength trades directly against registration. The
strength that would add dramatic chiaroscuro (≥0.45) breaks the ≥0.95 lock (0.90/0.94), while the
strengths that register (0.25/0.35) under-style to a flat, underdressed look (~4.5–5.5). All 5 scorers
named **flat value structure / no hot key-light pool** as the dominant flaw — NOT the ARM D
decorative-frieze repetition (flagged only as a minor secondary tell; the low-strength img2img over a
registered base cannot recompose into ARM D's free-Gemini friezes). Iterating the strength sweep will
interpolate 4.5–5.5, never cross 7.0 — so the loop stops with the best per the max-4-iteration rule.

The registered+styled deliverable works; the beauty gap is what ARM B (Gemini style-edit, already 8.0)
and ARM C (flux-compatible painterly LoRA) exist to close.

## Files
- `panel_verdict.json` — canonical combined 5-image blind panel (ARM D shape): scores, medians, means, control note.
- `verdict_str025.json` / `verdict_str035.json` / `verdict_str045_retry040.json` — per-config A/B/C verdicts ingested via `plate_loop --panel-verdict`.
- `cfg_str0{25,35,45}.json` — the plate_loop configs.
- `contact_sheet.png` — the 3 candidates + incumbent + control at a glance.
- Full plates + logs: `~/worldos-session-notes/plate-sprint/armA/iter1/`; gallery: `~/worldos-session-notes/plate-sprint/gallery.html`.
