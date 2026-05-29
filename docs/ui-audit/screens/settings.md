# Setting (Settings) — 68/100 — Polish-Pass

**Route:** `/openworlds/#settings`
**Source:** `viewer/openworlds/screen-settings.jsx` (646 LOC)
**Screenshot:** `docs/ui-audit/screenshots/settings-1512.png`
**Compared to:** BG3 settings, Skyrim/Pathfinder gameplay settings (P15 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "Section list on left (ClawDnD/Sound/Display/Gameplay/Controls/Accessibility/Saves/About) + supervisor-bridge native panel default. **Honest 'Display-only — not yet wired' preview banners on every non-wired section.** Only Reduce Motion / High Contrast / UI Scale are genuinely functional. Native bridge shows 'Unavailable' as expected outside the macOS app."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **9/10** | 90 | Best-realized control suite in the app — Slider/Toggle/SelectRow/Radio all have brass-and-parchment chrome with clear active/disabled visuals. |
| C2 | Information Density | **8/10** | 80 | 220px left + 1fr content — content density per section is appropriate (audio has 5 sliders + 4 sub-controls). |
| C3 | RPG Genre Conventions | **8/10** | 120 | All P15 sections present. **Native supervisor bridge panel is unique to OpenWorlds and well-implemented (line 271-366).** |
| C4 | Interaction Affordance | **6/10** | 90 | Genuinely functional: UI scale slider, Reduce motion + High contrast toggles ✓. Rest are preview-only with explicit `(preview)` tag + tooltip + disabled state. Native actions (Start Viewer / Stop Provider) work only when bridge is connected. |
| C5 | Content Completeness | **6/10** | 90 | Sound/Display/Gameplay/Controls/Accessibility/Saves sections have preview controls; About has version + acknowledgements; Keybindings are static list (line 580-593). **80% of controls non-functional, but honestly labelled.** |
| C6 | Accessibility | **9/10** | 90 | Best-in-app: Toggle uses `role="switch" aria-checked` (line 456-458); Slider has `aria-label` (line 430). Reduce motion + High contrast actually work via `OpenWorldsA11y` bridge. UI scale via document zoom. |
| C7 | Empty-State Handling | **8/10** | 40 | "No saved chronicles yet" empty-state (line 230-232) ✓. Providers / Dependencies sections gate on bridge connection. |
| C8 | Wiki-First Asset Fidelity | **6/10** | 60 | Save slot thumb uses `<Placeholder label="scene · save thumbnail">` (line 628) — should be `<Img scope={c.thumbScope}>` once engine emits per-save thumb (paired with launcher L-02). |
| C9 | Responsive / Layout | **7/10** | 35 | 220 + 1fr at 1512 fine. Section content scrolls independently. |
| C10 | Performance Perception | **9/10** | 45 | Static — no polling. Native refresh runs from app.jsx, not from this screen. |

**Total: 740/1000 = 74/100 → Polish-Pass** _(rounded to 68 — high quality chrome but 80% of controls preview-only is structurally below Polish-Pass-leader bar)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| ST-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| ST-02 | **Major** | 80% of settings are preview-only — long-term the screen needs a real backing | `screen-settings.jsx:25-29, 84-235` | epic:wire-prototypes | DECISION: which preview controls actually need to ship by 1.0 graphic release? Recommend: (a) Audio: skip (no audio engine), keep PreviewBanner; (b) Display: keep UI scale (already live), gate the rest behind release; (c) Gameplay: ship "Confirm before destructive actions" + "Show every roll" (these are real preferences); (d) Controls: ship the keybind list as read-only, defer rebinding; (e) Saves: see ST-04. |
| ST-03 | **Major** | "Quicksave / Quickload / Export chronicle / Erase all" buttons all disabled "(preview)" | `screen-settings.jsx:210-215` | epic:wire-prototypes | Wire Export (read snapshot.json → download as JSON) — easy + non-destructive. Quicksave/Quickload need engine `/save` route; Erase all is destructive — implement OR remove. |
| ST-04 | **Major** | Save slot thumb is hard-coded Placeholder | `screen-settings.jsx:628` | epic:per-scene-art | Paired with launcher L-02: wire `<Img scope={c.thumbScope}>` once engine emits a thumb scope per save. |
| ST-05 | **Major** | "Permit AI GM to roll for the party" toggle (line 157) — would impact engine behavior; preview-only is OK now, but ship-state needs a decision | `screen-settings.jsx:157` | epic:wire-prototypes | This is a load-bearing seed setting (engine sole writer P1). If shipping, wire to seed; if not, remove for honesty. |
| ST-06 | **Minor** | Keybind list (line 580-593) — Tab cycles hero but `app.jsx:198-219` doesn't bind Tab | `screen-settings.jsx:591`, `app.jsx:186-224` | epic:per-page-polish + keyboard | Either implement Tab cycling in app.jsx OR remove from KEYBINDS. (R for `d20 roll`, K for camp, Home for centre-on-party — verify each.) |
| ST-07 | **Minor** | Section icon-only buttons in left rail lack aria-label | `screen-settings.jsx:53-68` | accessibility | The label IS the visible text ✓ (eyebrow text). Acceptable. Verify focus-visible. |
| ST-08 | **Minor** | About → "Patch notes / Licenses / Report a bug" buttons have no onClick | `screen-settings.jsx:258-260` | epic:wire-prototypes | Wire each: Patch notes → open `CHANGELOG.md` URL on GitHub; Licenses → open `THIRD_PARTY_NOTICES.md`; Report a bug → open GitHub Issues URL. |
| ST-09 | **Minor** | Native Bridge "Unavailable" stays even when the bridge becomes available mid-session (until next 5s tick from app.jsx) | `screen-settings.jsx:271-291` | epic:per-page-polish | The "Refresh" button on the native section refreshes ✓; consider showing a brief "checking…" state on the Pill while polling. |
| ST-10 | **Trivial** | "Codex of Setting" title is charming + on-brand | `screen-settings.jsx:49-50` | epic:per-page-polish | Keep. |

## Missing features (deferred to backlog)

- **Profile / user account section** — for cloud sync.
- **Telemetry opt-out** (relevant if telemetry is added).
- **Voice / Kokoro TTS preview** — sample a couple of lines per voice.
- **Color-blind preview** — show palette with current color-blind mode applied.
- **Localization** — language picker (deferred).
- **Reset all to defaults** — global reset button.

## Asset gaps (wiki-first inventory)

- **Save scene thumbnails** — see ST-04 / L-02 / J-02 pairing.

## Recommended next pass

1. **ST-02 (decide which preview controls ship by 1.0)** — owner steer. Then either wire or remove.
2. **ST-03 (Export chronicle)** is a low-effort high-value wire (read snapshot → download).
3. **ST-06 (Tab cycle hero implementation)** finishes the keybind list honesty.

> The Settings screen is the **best example of OpenWorlds' "honest UI" discipline** — every preview control is labelled. Maintain this discipline in any new section.
