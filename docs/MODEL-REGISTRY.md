# MODEL REGISTRY — Scenario models for the WorldOS painterly pipeline

**Status:** authoritative. **Owner audit:** #1553 (M-ALIGN), 2026-07-11. **Machine-readable companion:**
[`qa/model_registry.json`](../qa/model_registry.json) — the allowlist the promotion provenance gate
checks (`tools/library/promote.py`). **Account rule:** never DELETE a Scenario model — deprecate it here.

This registry answers the owner's audit questions with FACTS pulled from Scenario metadata
(`mcp__scenario__model_get` / `asset_get` / `assets_list`), never recall. Every row records: base
model, training-data class, measured results, ROLE, and a verdict.

---

## TL;DR (the decisions this audit forces)

1. **The adopted canonical plates use NO trained WorldOS LoRA.** camp (truegrey), tavern, and the crypt
   incumbent (`crypt_armb_iter3`, panel 8.0) are all **plain `model_bfl-flux-1-dev` + depth-ControlNet
   base → `model_google-gemini-3-1-flash` style pass.** The base geometry lock is flux.1-dev; the paint
   quality is Gemini 3.1. That two-model chain *is* the canonical pipeline. (paint-first era. For the 3D-first KIT chain the edit model is `model_google-gemini-pro-image-editing` — see the 2026-09-02 adoption record at the end of this file.)
2. **The Architectural FLUX LoRA (`model_G379…`) is trained AI-on-AI.** 8 of its 10 training images are
   our OWN generated plates (`camp_clearing_night_v1/v2`, `crypt_dense_v1`, `crypt_firelit_v2`,
   `church_firelit_v1`, `cc2_nave`, `tavern_firelit_v1`, `seedream_undercroft`). A quality ceiling is
   baked in. The only plate that used it (`crypt-rich`) scored **6.5 — an HONEST NEGATIVE below the 8.0
   incumbent.** → **DEPRECATED.**
3. **flux.2-dev cannot drive the registered path.** The owner's highest-rated model
   (`model_J97…`, PoE2 Environments v2, trained on REAL PoE2 refs) is a `flux.2-dev-lora`, and
   `model_bfl-flux-2-dev` exposes **no `controlImage`/`controlModality` parameter** (verified against the
   live model schema 2026-07-11) — only `referenceImages`. So flux.2 is **txt2img/reference-only**: it
   produces the best-looking beauty frames but **cannot be geometry-locked to a greybox today.** →
   **REFERENCE-ONLY**, and the #1546 retrain must stay on a flux.1 / z-image base for the registered path.
4. **The next LoRA (#1546) must train on REAL refs only.** The real-reference sets — v4
   (`model_ng6…`, 26 curated Fandom/ArtStation) and v2 (`model_J97…`, 11 PoE2) — are the seed corpus.
   Never retrain on our own generated plates (that is exactly what made G379 a dead end).

---

## Provenance map — which model made each canonical plate (the owner's exact question)

| Canonical plate | Base | Trained LoRA? | Style pass | Verified from |
|---|---|---|---|---|
| **camp** (truegrey) | `model_bfl-flux-1-dev` depth-CN (seed42 cs0.75) | **NO — plain flux.1-dev** | Gemini 3.1 no-ref | `qa/evidence/true-greybox/findings.json` + loop contract |
| **tavern** (new-tavern) | `model_bfl-flux-1-dev` depth-CN (seed123 cs0.85) | **NO — plain flux.1-dev** | Gemini 3.1 lighting-restage (`asset_2Bc7…`) | `asset_get` on the final; `findings.json` |
| **crypt incumbent** `crypt_armb_iter3` (8.0) | `model_bfl-flux-1-dev` depth-CN (#1514) | **NO — plain flux.1-dev** | Gemini iter2-f structure-lock → Gemini 3.1 iter3 detail-enrich | `qa/evidence/crypt-replicate/findings.json` |
| **crypt-rich** (6.5, honest negative) | `model_bfl-flux-1-dev` depth-CN cs0.7 | **YES — `model_G379…` @0.85** | Gemini 3.1 structure-lock (`asset_ssap7…`) | `asset_get` on base `asset_rYC4…` (loras=[G379]) |

**Answer:** the tavern and camp bases applied **NO trained LoRA — plain `flux.1-dev` + depth ControlNet.**
The RsWE exterior-LoRA camp frames (`asset_byFD…`, cs0.65 scale0.6) are a **separate, unadopted**
outdoor-LoRA exploration, not the shipped camp plate. The only canonical-adjacent plate that used a
trained LoRA is `crypt-rich`, an experiment that **underperformed** the LoRA-free incumbent.

---

## The models (7 trained, private)

| id | name | base / type | training data | ROLE | verdict |
|---|---|---|---|---|---|
| `model_MB22WaRCBLtfhi5R2CRpHoEL` | WorldOS Painterly | z-image LoRA (6) | curated BG/PoE/Disco refs | original z-image img2img painterly LoRA (early recipe default @0.78) | REFERENCE-ONLY |
| `model_H7GSNY6rDYPN85cMdMGQVtsX` | PoE2 Environments (v1) | z-image LoRA (11) | REAL PoE2 refs | first env backdrop LoRA; superseded by v3/v4 | REFERENCE-ONLY |
| `model_J97JPYrKtWNUUujkCgTG4s1W` | PoE2 Environments v2 (Flux2) | flux.2-dev LoRA (11) | **REAL PoE2 refs** (`poe2_001..022.jpg`) | **owner's best OUTPUT quality; BEAUTY REFERENCE only — flux.2 has no ControlNet, so unusable in the registered path.** #1546 seed | REFERENCE-ONLY |
| `model_AxwVTjTcMm8RZieM9ZZi8nNG` | PoE2 Environments v3 | z-image LoRA (19) | REAL PoE2/ArtStation refs | data-scale variant of v1 | REFERENCE-ONLY |
| `model_ng6MSaWtyvwKbZyDBMtzv7tA` | worldos-poe2-clean-env-v4 | z-image LoRA (26) | **REAL curated** (Fandom no-FX + ArtStation) | cleanest real-ref set; **primary #1546 retrain seed** | REFERENCE-ONLY |
| `model_RsWEcQL2NWXwoyEodWVE2vWG` | Painterly Exterior (FLUX) | flux.1 LoRA (18) | MOSTLY REAL (v4 + landscape refs; 1 AI plate) | exterior/outdoor depth-CN LoRA; bake-off control arm | CANDIDATE |
| `model_G379oza2qhm6MkqDrtTvvmmw` | Painterly Architectural (FLUX) | flux.1 LoRA (10) | **AI-ON-AI — 8/10 are our own generated plates** | interior depth-CN LoRA used by `crypt-rich` (6.5) | **DEPRECATED** |

Base / foundation models in the canonical chain (registry-approved, non-LoRA):
`model_bfl-flux-1-dev` (CANONICAL registered base, depth ControlNet) · `model_google-gemini-3-1-flash`
(CANONICAL style pass) · `model_z-image` (early img2img base) · `model_bfl-flux-2-dev` (flux.2 base;
**no ControlNet** today).

---

## Training-set quality (AI-derived vs real-reference)

| model | class | evidence |
|---|---|---|
| G379 Architectural | **AI-ON-AI (flag)** | 8/10 training images are our OWN generated plates (`camp_clearing_night_v1/v2`, `crypt_dense_v1`, `crypt_firelit_v2`, `church_firelit_v1`, `cc2_nave`, `tavern_firelit_v1`, `seedream_undercroft`) + 2 isolated props (brazier, chest) |
| RsWE Exterior | mostly real | 4× `lora-v4-t_*` (v4 real set) + ~13 real landscape/environment refs + 1 AI plate (`camp_clearing_night_v2`) |
| J97 v2 (Flux2) | **REAL** | 11× `poe2_001..022.jpg` real PoE2 screenshots |
| ng6 v4 | **REAL** | 26 curated Fandom-wiki (no in-engine FX) + ArtStation |
| AxwV v3 | **REAL** | 19-ref environment-dominant PoE2/ArtStation set |
| H7GS v1 | **REAL** | same 11 real PoE2 refs as v2 |
| MB22 original | curated-mixed | 6 BG/PoE/Disco-inspired refs |

---

## Bake-off (one fixed crypt greybox, shared seed) — see the PR for embedded frames

Controlled comparison on the true-greybox crypt (depth control `asset_ByYfTs78japedqFecMXFXcwe`, shared
seed 12345, identical prompt + Gemini structure-lock across arms). Evidence in
`qa/evidence/model-audit/bakeoff/` (+ `bakeoff_panel_verdict.json`). Total spend **103 CU** (cap 120).

| Arm | Recipe | Panel median (blind, n=5) | Δ vs real-art control (9.0) | Edge-recall (styled) | Defects | Verdict |
|---|---|---|---|---|---|---|
| **A** | plain `flux.1-dev` depth-CN → Gemini | **6.0** | −3.0 | 0.862 | fake-text 4/5 (hallucinated signature) | REJECT — **confounded by a stochastic text artifact** |
| **B** | + `G379` interior LoRA → Gemini | **7.0** | −2.0 | 0.965 | none | stable — best defect-clean registered arm (this seed) |
| **C** | + `RsWE` exterior LoRA → Gemini *(control arm)* | **6.0** | −3.0 | 0.984 | fake-text 4/5 + monumental drift | REJECT — exterior LoRA wrong domain for interiors |
| **D** | `flux.2-dev` + `v2` (real refs) txt2img *(beauty ref)* | **7.0** | −2.0 | n/a (unlocked comp) | none | **REFERENCE-ONLY — best beauty, but txt2img → unregisterable** |
| control | real shipped PoE2 plate | 9.0 | — | — | — | in-band [6.8, 9.2] → instrument valid |

**Read honestly (single seed, 103 CU):** the control landed in-band so the panel is valid. All registered
arms cluster **6.0–7.0, far below real art (9.0).** The v2-Flux2 beauty ref (D) tied the best styled arm
and was defect-clean — top quality, but **cannot register.** The plain-flux (A) and exterior-LoRA (C) arms
were dragged under the adoption bar by a **hallucinated text/signature artifact** on the Gemini pass (2 of
4 styled arms) — a per-seed defect, **not** proof that the interior LoRA beats plain flux (the LoRA-free
incumbent `crypt_armb_iter3` scores 8.0). Load-bearing conclusions are seed-independent: **G379 is a
dead-end (AI-on-AI), flux.2 can't register, retrain on real refs.** Full data:
[`qa/evidence/model-audit/bakeoff/bakeoff_panel_verdict.json`](../qa/evidence/model-audit/bakeoff/bakeoff_panel_verdict.json).


**flux.2 ControlNet check (Task 4):** `model_schema_get("model_bfl-flux-2-dev")` returns parameters
`{modelId, loras, lorasScale, prompt, referenceImages, numOutputs, numInferenceSteps, width, height,
guidance, seed}` — **no `controlImage`, no `controlModality`, no `controlStrength`.** FLUX.2 does **not**
support depth ControlNet on Scenario today; it does NOT unlock the registered path for the v2 model.

---

## Style-pass bake-off v2 (#1556) — GPT-Image-2 / Kontext / MAI / SeeDream / Ideogram / El Diablo vs Gemini

Owner-surfaced challengers, one style pass each over the SAME fixed crypt base
(`asset_BKn1kiX2c8BaifYj559yKx7Y`, flux.1-dev depth-CN, seed 12345), identical STRUCTURE-LOCK +
ADDITIONS-LOCK prompt on the edit arms; Kontext took the greybox directly (native blockout mode). Blind
5-scorer panel, Gemini as the anchor. **111 / 120 CU.** Machine data:
[`qa/evidence/1556/RANKED.md`](../qa/evidence/1556/RANKED.md) +
[`gate_results.json`](../qa/evidence/1556/gate_results.json) +
[`panel/panel_verdict.json`](../qa/evidence/1556/panel/panel_verdict.json); rows in
`qa/model_registry.json → evaluated_1556`.

| Arm | Recall vs base (≥0.95) | Invented (net-new) | Panel median (n=5) | Cost/img | Verdict |
|---|---|---|---|---|---|
| **Gemini 3.1** (incumbent) | 0.973 ✅ | 1 | **8.0** | 20 CU | **HOLDS** — reproduces its adopted 8.0; faint signature (fake-text 5/5) |
| **MAI 2.5 Edit** | **0.9999 ✅** | **0** | **7.0** | **12 CU** | **CANDIDATE** — defect-clean, artifact-free, cheapest, perfect lock; did NOT beat 8.0 |
| **GPT-Image-2** | 0.988 ✅ | 3 | 6.0 | **45 CU** | REJECT — clean but mid + costliest |
| **El Diablo** (iso-dungeon LoRA) | 0.981 ✅ | 0 | 3.0 | 9 CU | REJECT — under-cooked; walls read as broken tiling |
| **Seedream 5.0 Pro** | 0.997 ✅ | 4 | — | 9 CU | REJECT — INVENTED an arch doorway (ADDITIONS-LOCK violation) |
| **Kontext Blockout→Render** | **0.854 ❌** | 4 | — | 16 CU | REJECT — geometry-aware not geometry-locked; invented a water pool |
| Ideogram V4 | — | — | — | 0 CU | INELIGIBLE — txt2img-only on Scenario (no img2img edit) |

**Verdict: no challenger beats the Gemini 3.1 incumbent (8.0); the pipeline is unchanged.** The panel
ordering was unanimous across all 5 scorers. **MAI Image 2.5 Edit (7.0)** is the notable near-miss — the
only edit arm that is defect-clean, free of Gemini's signature artifact, perfectly structure-locked
(0.9999) and the cheapest (12 CU); registered as an evaluated **CANDIDATE** worth a multi-seed re-run
before any adoption call (owner's). No challenger id is added to the promotion allowlist.

Two honest calibration notes (why the gate thresholds aren't cargo-culted): (1) **Gate 1 is recall vs the
fixed BASE, not the raw greybox** — the adopted gold incumbent scores only **0.878 vs the raw greybox**
with `registration_recall`, so a "≥0.95 vs greybox" bar is unreachable for any painterly plate. (2)
**"invented == 0" is unreachable for the walled crypt** — the base itself and the gold incumbent each flag
7 cells (the #1540 detector was calibrated on the camp open clearing), so the signal is **net-new flags
vs that structural baseline**. Access facts: **GPT-Image-2 is Scenario-only, 45 CU** (the codex CLI has no
image-gen connector — only `-i` vision input); the disguised control was off-subject (jungle exterior) so
instrument validity rests on the anchor this round — full caveat in RANKED.md.

---

## The provenance gate (how this registry is enforced)

- **`qa/plate_loop.py`** stamps the full **model chain** (base + every LoRA + scales + style-pass model)
  into each loop JSON row and panel contract, read from the generator's `scenario_meta.json` (or a
  config-declared `model_chain` for a pre-generated candidate).
- **`tools/library/promote.py`** REFUSES a room candidate whose recorded `model_chain` references any
  model not listed in `qa/model_registry.json`. **Additive:** the gate is a NO-OP when the registry file
  is absent (default-allow) or when a candidate carries no chain (legacy) — it only bites a chain that
  declares an unregistered model. This ends ad-hoc model selection structurally.

**When you train/adopt/deprecate a model:** update BOTH `qa/model_registry.json` (the allowlist the gate
reads) and this file (the human rationale). A DEPRECATED model stays in the allowlist so old plates that
used it remain promotable; the `verdict` is advisory to humans, membership is the gate.

## 2026-09-02 adoption record — the kit-chain edit model (refresh charter #1702, step 7)
`model_google-gemini-pro-image-editing` (Gemini 3.0 Pro image edit) is the CANONICAL edit pass of the **3D-first kit chain**:
geometry → `build_room_kit` → seg-registration ≥ 0.99 → the kit scene's own depth → flux depth-CN base → **structure-holding,
critique-targeted Gemini 3 Pro edit** → `qa/styled_align_check.py` (phase-corr ALIGNED ≤ 1 px) → `qa/object_align_check.py`
(per-object) → two-anchor blind panel. It produced the adopted **kit crypt v1** (#1688, 2026-07-23; panel 7 vs 8, Δ−1.0 in-band,
dx=0) and **kit tavern v1/v2** (#1689, #1690 → #1703). The plates' manifest provenance had carried this model as "pending owner
allowlist"; this record + the `qa/model_registry.json` entry close that. `model_google-gemini-3-1-flash` stays registered as the
paint-first-era style pass (historical plates); it is not used by the kit chain. Known gap: this editor exposes no seed
(determinism), and one edit deleted a table (fixed by the per-object gate + composite) — the 2026-09 Track B head-to-head
(Gemini 4K refs · Qwen Image 3.0 Pro · FLUX.2 seeded) is the measured route to a successor, never a silent swap.
