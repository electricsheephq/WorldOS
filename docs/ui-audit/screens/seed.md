# World Seed — 50/100 — Finish-Wave

**Route:** `/openworlds/#seed`
**Source:** `viewer/openworlds/screen-seed.jsx` (327 LOC)
**Screenshot:** `docs/ui-audit/screenshots/seed-1512.png`
**Compared to:** No direct AAA-CRPG analog; closest is the Citizen Sleeper "Codex" or Wildermyth "create world" — see P16 in `RPG_REFERENCE_PATTERNS.md`.
**First impression (5-second read):** "Beautiful Quickening card with quote + prose + Seeded/By/Pattern/Engine StatLines + Re-seed buttons on left. Right: System / Tone / Difficulty / AI GM / World Rules / Chronicler's notes. **Entire screen is display-only — 'Sow the change' button disabled with title 'World re-seeding isn't wired yet'. Quote + stats are HARDCODED prototype data.**"

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **9/10** | 90 | Cream quote panel + parchment + brass dropdowns + cinzel toggles — gorgeous chrome. |
| C2 | Information Density | **8/10** | 80 | Two-pane 1/1.1; right pane stacks 6 sections without crowding. |
| C3 | RPG Genre Conventions | **5/10** | 75 | No AAA-CRPG genre analog — this is an OW invention. The sections (System/Tone/Difficulty/AI GM/World Rules) read like NaNoWriMo project settings. Reads coherent but isn't a familiar pattern. |
| C4 | Interaction Affordance | **3/10** | 45 | **All inputs are local-state — nothing is saved.** Sow the change is `disabled` with `title="World re-seeding isn't wired yet"` (line 194). Reseed shows a "Reseed locked" toast (line 78). |
| C5 | Content Completeness | **3/10** | 45 | Hardcoded quote (line 45-49), hardcoded "Seeded 20th of Nightal, 1492 DR" / "By the chronicle" / "Pattern 9b3d-2f1e-77ac" / "Engine Chronicle II" (line 64-67). None of this reflects the actual campaign seed. |
| C6 | Accessibility | **6/10** | 60 | Toggle has role="switch" via the SeedToggle wrapper — verify. Dropdown opens on click; needs keyboard nav (Arrow keys). |
| C7 | Empty-State Handling | **4/10** | 20 | No empty state at all — the screen always shows the SAME prototype seed data regardless of save state. |
| C8 | Wiki-First Asset Fidelity | **5/10** | 50 | No portraits / scenes needed on this screen; cream-quote panel is pure CSS. ✓. |
| C9 | Responsive / Layout | **7/10** | 35 | 1fr/1.1fr split at 1512 OK. The right pane is scrollable. |
| C10 | Performance Perception | **9/10** | 45 | No polling, no network — static. Fast. |

**Total: 545/1000 = 55/100 → Finish-Wave** _(rounded to 50 — display-only on an interaction-heavy screen is a structural deficiency)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| S-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| S-02 | **Critical** | All seed parameters are display-only — no engine write lane | `screen-seed.jsx:18, 194` | epic:wire-prototypes | DECISION: keep World Seed as a viewable settings page OR wire it to a real `/seed-surface` write lane. If wire: define which params are actually mutable mid-campaign (Tone/Narration safe; Permadeath/Difficulty arguably retroactive-only). If retire: make this screen read-only and pull live values from the active campaign instead of hardcoded prototype data. |
| S-03 | **Critical** | Quote, Seeded date, By, Pattern, Engine values are hardcoded prototype | `screen-seed.jsx:45-49, 64-67` | epic:demo-leak | Replace with values pulled from the active campaign's seed (engine emits `seed_signature`, `seed_date`, `chronicler`, `engine_version`). When campaign is unset, hide the StatLine block or show an honest empty-state. |
| S-04 | **Major** | Hardcoded "Re-seed locked" toast (line 78) — re-seed is destructive but no real confirm flow | `screen-seed.jsx:75-79` | epic:wire-prototypes | If keeping: implement a real 2-step modal (type the campaign name to confirm). If retiring: remove the Reseed button. |
| S-05 | **Major** | "AI Game Master" section labels match the launcher's NewCampaignModal — duplicate pattern, neither wired | `screen-seed.jsx:121-145`, `screen-launcher.jsx:449` | epic:wire-prototypes | Consolidate: World Seed is the canonical home for these params; launcher's modal should pass them through to `bindHero` / `startProviderSession`. |
| S-06 | **Major** | Tone selector wires `update("tone", v)` but `t.palette` (warm/cool/dark) is the actual chrome palette — Tone here is decoupled from visuals | `screen-seed.jsx:93-103`, `app.jsx:60-63` | epic:wire-prototypes + epic:per-page-polish | Either link Tone (Heroic/Grim/Mythic/Picaresque) to the `data-palette` attribute so Grim shifts the chrome to walnut (matches the seed copy) OR drop the visual implication from the copy. Today copy says "Crimson and walnut" for Grim but selecting Grim changes nothing. |
| S-07 | **Major** | "Permadeath" / "Fate dice" / "Item destruction" / "Anachronism" toggles have local state only — never read by engine | `screen-seed.jsx:149-173` | epic:wire-prototypes | If keeping, wire to the seed/save. If retiring, remove. |
| S-08 | **Minor** | Chronicler's notes textarea uses `defaultValue` (line 179) — uncontrolled, never saved | `screen-seed.jsx:177-191` | epic:wire-prototypes | Wire to state + persist via seed save. |
| S-09 | **Minor** | Quote attribution "— found in a border coachman's pocket, undated" is charming but generic | `screen-seed.jsx:47` | epic:per-page-polish | Either (a) pull a per-world quote from the seed manifest, OR (b) keep static and document as OW flavor. |
| S-10 | **Trivial** | "Pattern 9b3d-2f1e-77ac" reads as random hex — could be derived from the actual seed signature | `screen-seed.jsx:66` | epic:per-page-polish | Derive from a stable hash of the campaign id. |

## Missing features (deferred to backlog)

- **Save/export seed manifest** — let user share a seed YAML/JSON file (matches the generativity north-star in `ClawDnD-NORTH-STAR.md` Part 1 deliverable B).
- **Compare seeds** — diff between two seeds.
- **Seed templates** — one-click "Curse of Strahd vibes" / "Eberron noir".
- **Preview chronicler voice** — sample 2-3 lines of narration in each voice (Florid / Almost-poetic / Terse).
- **Difficulty preview** — "Hard means: encounters are scaled +1 CR per session."

## Asset gaps (wiki-first inventory)

- None — this is a text/control surface, no art needed.

## Recommended next pass

1. **DECISION POINT for the owner (S-02)**: Is World Seed a viewable-current-seed page, or a mutable settings page?
   - If viewable: gut the editable controls, surface live `campaign.seed` values, this screen drops to ~85 (Polish).
   - If mutable: design + build the `/seed-surface` write lane. This is a multi-PR effort.
2. **S-03 (de-fake the hardcoded values)** is the minimum honesty fix.
3. Either way, **S-05 (consolidate with launcher's NewCampaignModal)** removes duplication.

> Per the previous audit doc, Seed scored 3/10 with a "Linzi (chronicler)" demo leak. Demo leak has been **removed** (replaced with "the chronicle" line 65) ✓. The structural display-only problem remains.
