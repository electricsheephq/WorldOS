# STYLE-PASS BAKE-OFF v2 — RANKED (#1556, M-ALIGN)

**Challenge:** beat the Gemini 3.1 incumbent style pass on ONE fixed crypt substrate. One arm per
challenger; identical STRUCTURE-LOCK + ADDITIONS-LOCK prompt across the instruction-edit arms; the
Kontext arm takes the greybox directly (its native blockout mode).

- **Fixed base (reused, not regenerated):** `arm_a_base_plain_flux.png` = `asset_BKn1kiX2c8BaifYj559yKx7Y`
  (flux.1-dev depth-CN, NO LoRA, seed 12345, depth control `asset_ByYfTs78japedqFecMXFXcwe`).
- **Greybox (Kontext input):** `crypt_greybox.png` → uploaded `asset_asNMUzqDjm4Es4Q2fdVa75SD`.
- **CU spent: 111 / 120 cap** (Gemini 20 · GPT-Image-2 45 · MAI 12 · Seedream 9 · Kontext 16 · El Diablo 9).

## The ranked table

| Rank | Arm (model) | Gate 1 · recall vs base (≥0.95) | Gate 2 · invented (net-new) | Panel median (blind n=5) | Cost / image | Access route | Verdict |
|---|---|---|---|---|---|---|---|
| **1** | **Gemini 3.1** `model_google-gemini-3-1-flash` *(incumbent anchor)* | 0.973 ✅ | 1 (wall-adjacent) | **8.0** | 20 CU | Scenario | **INCUMBENT HOLDS** — reproduces its documented 8.0; carries a faint signature (fake-text 5/5) |
| **2** | **MAI 2.5 Edit** `model_microsoft-mai-image-2-5-edit` | **0.9999 ✅** | **0** | **7.0** | **12 CU** | Scenario | **STRONGEST CHALLENGER** — defect-clean, artifact-free, perfect lock, cheapest edit; did NOT beat 8.0 |
| **3** | **GPT-Image-2** `model_openai-gpt-image-2` | 0.988 ✅ | 3 (left-wall rubble) | 6.0 | **45 CU** | Scenario only | clean but mid + costliest → not worth it |
| **4** | **El Diablo** `model_a2dvNsgst7PCnpiucRY7bEHW` (flux.1-dev + LoRA) | 0.981 ✅ | 0 | 3.0 | 9 CU | Scenario | UNDER-COOKED — barely transforms; walls read as broken tiling |
| **rej** | **Seedream 5.0 Pro** `model_bytedance-seedream-5-0-pro` | 0.997 ✅ | 4 | — (rejected pre-panel) | 9 CU | Scenario | **REJECT** — beautiful but INVENTED an arched doorway (ADDITIONS-LOCK violation) |
| **rej** | **Flux Kontext Blockout→Render** `model_eJg6GZwd59vg4ycufRLyKsxL` | **0.854 ❌** | 4 | — (fails Gate 1) | 16 CU | Scenario | **REJECT** — does NOT hold geometry (0.854 < 0.95) + invented a glowing water pool |
| **skip** | **Ideogram V4** `model_ideogram-v4` | — | — | — | 0 CU (not run) | Scenario | **INELIGIBLE** — txt2img-only on Scenario (no img2img edit) → cannot do a geometry-locked style pass, same class as flux.2 |
| control | real PoE2:Deadfire plate (disguised, slot image_3) | — | — | 2.0 ⚠ bimodal [1,1,2,8,9] | — | — | off-subject (jungle exterior) → see control caveat |

## Verdict — no switch this PR

**No challenger beats the Gemini 3.1 incumbent (8.0).** The panel ordering was **unanimous across all 5
scorers** (Gemini > MAI > GPT-Image-2 > El Diablo). This PR adds evidence + registry rows ONLY; the
pipeline is unchanged.

**MAI Image 2.5 Edit is the notable result.** At 7.0 it did not exceed 8.0, but it is the ONLY edit arm
that is simultaneously: defect-clean, **free of the fake-text/signature artifact that hits Gemini
(5/5)**, perfectly structure-locked (0.9999 vs base), and the **cheapest edit (12 CU vs Gemini's 20)**.
It is registered as an evaluated **CANDIDATE** — worth a multi-seed re-run before any adoption call, which
remains an orchestrator/owner decision.

## Gate calibration (load-bearing — read before trusting the numbers)

Both gates required honest recalibration against ground truth, documented here so the thresholds aren't
cargo-culted:

1. **Gate 1 is recall vs the fixed BASE, not the raw greybox.** With `qa/plate_overlays.registration_recall`
   the *adopted gold incumbent* (`crypt_armb_iter3`, panel 8.0) scores only **0.878 vs the raw greybox** —
   a literal "≥0.95 vs greybox" bar is unreachable for ANY painterly plate. The ≥0.95 gate is therefore
   measured against the styled BASE (structure preservation of the style step), matching the prior #1553
   bake-off's `edge_recall_styled` column semantics. Recall vs greybox is reported as a secondary drift
   number.
2. **Gate 2 "invented == 0" is unreachable for the crypt; the signal is NET-NEW flags.** The #1540
   `inverse_coherence_flags` detector was calibrated on the CAMP open clearing. On the walled crypt it
   false-positives: **the fixed base itself flags 7 cells and the gold incumbent flags 7** (the back-wall
   / column ornamentation caught in each cell's upward silhouette band). The honest signal is flags
   **beyond the base+incumbent structural baseline** — the `net-new` column above. Seedream's invented
   arch and Kontext's water pool were confirmed VISUALLY (zoom overlays), not just by the counter.

## Access verdicts (the owner's two open questions)

- **GPT-Image-2 access — Scenario vs "~free Codex connector":** the codex CLI (`codex-cli 0.144.0`)
  exposes image only as `-i/--image <FILE>` = attaching images as *vision input* to the coding agent.
  There is **no image-generation/editing endpoint and no image-gen MCP** among its connectors (gitnexus,
  evaos-fleet, github, notion, unity, computer-use). So from this pipeline's automation, **GPT-Image-2 is
  reachable only via Scenario, metered at 45 CU/image** (high quality) — the most expensive arm, and it
  scored mid (6.0). The "~free via the Codex connector" path does not exist for the WorldOS style-pass
  pipeline; the marginal cost of GPT-Image-2 style passes does NOT drop to zero.
- **Flux Kontext "Blockout to Render" — does it hold geometry?** **No, not tightly enough.** Fed the raw
  greybox in its native blockout mode, it scored **0.854 edge-recall vs base — below the 0.95 bar** (it
  reinterprets/regenerates layout rather than pixel-locking it) and it invented a glowing water pool on
  the floor. It is geometry-*aware* but not geometry-*locked*; the img2img-over-registered-base arms
  (MAI 0.9999, GPT 0.988, Gemini 0.973) preserve structure far better. Kontext is not a substitute for
  the depth-CN-base → style-edit chain.

## Control caveat (disclosed)

The disguised in-band control (slot `image_3`) was a real PoE2:Deadfire plate but an **outdoor
jungle-temple exterior**, not a crypt interior. It scored a **bimodal [1,1,2,8,9]** (median 2.0, out of
the [6.8,9.2] band): 3/5 scorers applied the off-brief `not_a_crypt` penalty, 2/5 scored pure craft
(8,9). This is a **control-selection flaw (off-subject), not an instrument failure** — the craft reads
confirm real art reads top-tier, and instrument validity is carried this round by the **subject-matched
Gemini ANCHOR reproducing its documented 8.0 exactly**. A subject-matched crypt control is the fix for
any re-run. The arm ordering is unaffected (unanimous across scorers).

## Artifacts
- `arms/` — the 6 full-res generated arms · `overlays/` — contact sheet + zoom overlays (Seedream arch,
  MAI/GPT verification) + `pr_best_frames.png` · `panel/blind/` — the 5 blinded plates (image_3 control
  uncommitted, copyright) · `panel/panel_mapping.json` · `panel/panel_verdict.json` · `gate_results.json`
  · `gate.py` (the two-gate harness) · `style_prompt_shared.txt` / `kontext_prompt.txt`.
