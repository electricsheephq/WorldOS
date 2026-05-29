# Parley (Dialogue) — 78/100 — Polish-Pass

**Route:** `/openworlds/#dialogue` (alias `#parley`)
**Source:** `viewer/openworlds/screen-dialogue.jsx` (295 LOC)
**Screenshot:** `docs/ui-audit/screenshots/dialogue-1512.png`
**Compared to:** BG3 dialog UI (portrait + numbered branches + DC chips), Pathfinder: Kingmaker conversation (P5 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "Owner-favorite. Scene backdrop + EASY/MED/HARD difficulty + 5 skill-slot rows with proficient/expertise pills + DC chips + free-form path + approach history. Caelar silhouette + 'How does Caelar approach this?' framing. This is the template the rest of the app should match."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **9/10** | 90 | Vignette + dual candle glows + the brass-framed conversation panel — best chrome in the app. |
| C2 | Information Density | **9/10** | 90 | 5 skill rows + free-form + DC pill + modifier + difficulty + history sidebar — dense without crowding. |
| C3 | RPG Genre Conventions | **9/10** | 135 | All P5 patterns: portrait + alignment + numbered branches + DC + skill + free-form + history. **The "These are SLOTS, the DM voices the line" framing is a deliberate, well-done OpenWorlds invention.** |
| C4 | Interaction Affordance | **9/10** | 135 | Hover state on each skill row (line 222-230) ✓; click posts `{kind:"check", skill, dc}` via /move ✓; free-form posts `{kind:"say"}` ✓; difficulty selector wires through to `/parley-surface?difficulty=` (line 27). |
| C5 | Content Completeness | **7/10** | 105 | 5 skills present + free-form — well-covered. Difficulty is per-attempt. Could surface anchor NPC's `disposition` / `attitude_value` in the panel (currently shown only in Relations). |
| C6 | Accessibility | **7/10** | 70 | DC tooltip on the "DC" label (line 166-168) ✓; Difficulty buttons have `title` (line 170); skill rows are `<button>` ✓. Candle glow animation needs reduced-motion gate. |
| C7 | Empty-State Handling | **10/10** | 50 | `ParleyEmpty` (line 75-91) — "When the party sits down to talk, this is where the lead speaker's approaches appear …" — best-in-class empty state. Honest + on-brand + instructive. |
| C8 | Wiki-First Asset Fidelity | **7/10** | 70 | Scene backdrop via `<Img scope={surface.imageScope \|\| "location:"+location_id}>` ✓; actor portrait via `<Img scope={"portrait-"+surface.actor_id}>` ✓. Caelar silhouette is correct fallback. |
| C9 | Responsive / Layout | **7/10** | 35 | At 1512 the 160px portrait / 1fr body grid is balanced. Below 1100 the portrait column would crowd. |
| C10 | Performance Perception | **8/10** | 40 | 5s poll re-runs whenever `difficulty` changes ✓ (re-runs `/parley-surface` with new DCs). No 404 storm. |

**Total: 820/1000 = 82/100 → Release-Ready** _(rounded to 78 as a conservative call: portrait fallback path + reduced-motion gap)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| D-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| D-02 | **Major** | Difficulty buttons read as global settings without the tooltip | `screen-dialogue.jsx:169-176` | epic:per-page-polish | Add an inline subline `Approach difficulty for this attempt` next to the DC label so the per-attempt scope is visible without hovering the tiny "DC" eyebrow. (Owner's prior audit-doc note about the toggle reading like a setting is partly addressed by the tooltip but still merits a stronger inline label.) |
| D-03 | **Major** | Free-form path doesn't yield a clear DM-side outcome | `screen-dialogue.jsx:116-126` | epic:wire-prototypes | After clicking "Speak freely", a follow-up text input should let the player type the line (not just send `${actorName} speaks their own way`). Wire to a free-form input + Send button. The current `kind:"say"` move is a stub — surface a real say-text field. |
| D-04 | **Major** | "Read-only" toast is unexplained when canAct=false | `screen-dialogue.jsx:103, 118` | epic:per-page-polish | Replace `"This view can't land moves. The DM voices the chosen approach."` with `"This is a preview — no live DM is attached. Open a chronicle to converse."` and add a CTA to navigate to the launcher. |
| D-05 | **Minor** | Approach history is right-side rail at fixed `top: 70, width: 240` — overlaps difficulty buttons at smaller widths | `screen-dialogue.jsx:266-288` | epic:per-page-polish | Move history beneath the difficulty controls OR make it collapsible. At 1280 it overlaps. |
| D-06 | **Minor** | Approaches taken history doesn't persist across reloads | `screen-dialogue.jsx:21` | epic:per-page-polish | `history` is `useState` only. For a real "I tried Athletics and failed earlier" surface, source from the engine's parley_log (or a `recent_checks` projection). |
| D-07 | **Minor** | No NPC disposition / attitude_value surfaced in the parley panel | `screen-dialogue.jsx:192-198` | epic:per-page-polish | Pull `surface.npc?.disposition` and `attitude_value` to display a small "Cool / Cordial / Welcome" pill near the actor name — matches `screen-relations.jsx` `DispositionDot`. Same data, surfaced where it changes player choice. |
| D-08 | **Minor** | Difficulty tooltips redundant with the DC label tooltip | `screen-dialogue.jsx:170` | epic:per-page-polish | Consolidate: a single Tooltip on the parent "DC" eyebrow covering all three buttons; remove the per-button `title`. |
| D-09 | **Minor** | Candle glow animation runs even with reduced-motion preference | `screen-dialogue.jsx:142-143` | accessibility | Gate `.candleglow` animation on `[data-reduced-motion=on]` (already set by `OpenWorldsA11y`). Style: `[data-reduced-motion=on] .candleglow { animation: none; }`. |
| D-10 | **Trivial** | "Speak freely — your own words" eyebrow uses ✦ glyph; consistency with other "free-form" entry points | `screen-dialogue.jsx:256` | epic:per-page-polish | The Session screen's "Forge a new hero" also uses ♕/✦. Pick one glyph for "free-form" across the app for muscle memory. |

## Missing features (deferred to backlog)

- **Multi-skill approach** ("Persuasion w/ Insight assist") — Tides of Numenera pattern.
- **NPC reaction preview** — "Voicing this will likely shift Jaheira -5 approval" warning.
- **Branching dialogue tree** — multi-step parley (not single-shot).
- **Quote-history archive** — past parleys with each NPC, browsable.

## Asset gaps (wiki-first inventory)

- **Per-NPC parley backdrop** — same `location:` scope is reused; could add `scene-parley-<location>` for dedicated indoor compositions.
- **Companion portraits** for the 7 BG3 origin heroes — already shipped in `_private/baldurs-gate/portraits/` per the Phase-4 owner status comment. Verify Caelar (custom PC) shows silhouette, not crest, when tested live.

## Recommended next pass

1. **D-03 (free-form text input)** unlocks real role-play; the current free-form path is a stub.
2. **D-02 (difficulty inline label)** finishes the prior owner-noted "reads like a setting" concern.
3. **D-07 (disposition pill)** is a 10-minute win that ties Parley back to Relations.

> Per the previous audit doc, Parley scored 7/10 — this round it's the highest-scoring screen alongside Relations. Hold this as the **template** for the rest.
