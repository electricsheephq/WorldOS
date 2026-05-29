# Quest Journal — 72/100 — Polish-Pass

**Route:** `/openworlds/#journal`
**Source:** `viewer/openworlds/screen-journal.jsx` (365 LOC)
**Screenshot:** `docs/ui-audit/screenshots/journal-1512.png`
**Compared to:** BG3 quest log + map markers, Pathfinder: Kingmaker journal (P6 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "Two-page spread is gorgeous. Active/Past/Rumors tabs. One quest visible (The Fate of the Emerald Grove). Wax seal flourish. Objectives panel sparse for this preview. GM Advisory pulled into left rail when present."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **9/10** | 90 | Two-page spread + spine gradient + corner ornaments + drop-cap quest entry + wax seal — best-realized "codex page" in the app. |
| C2 | Information Density | **8/10** | 80 | 300px left + spread right gives enough room for prose + objectives + threads + chronicler. |
| C3 | RPG Genre Conventions | **9/10** | 135 | All P6 patterns + rule-of-three evolution badge (line 197-205) + Threads & Callbacks (line 295-322) — unique strengths. |
| C4 | Interaction Affordance | **7/10** | 105 | Quest click selects ✓; "Show on map" → `onNavigate("map")` ✓; **"Bookmark" button has no onClick (line 355)** — non-functional. |
| C5 | Content Completeness | **6/10** | 90 | One quest visible (The Fate of the Emerald Grove) — fine. Objectives panel empty in preview (no `quest.objectives` populated). Past + Rumors tabs not exercised. |
| C6 | Accessibility | **7/10** | 70 | Tabs styled but no `role="tab"` / `aria-selected`. Drop-cap class uses `::first-letter` — verify reader compatibility. Wax-seal "OPEN WORLDS" decoration has no aria-hidden. |
| C7 | Empty-State Handling | **9/10** | 45 | Honest empty per tab (line 119-123): "No active quests in the chronicle yet" / "Nothing has been resolved or failed yet" / "No rumors or untracked hooks." ✓. |
| C8 | Wiki-First Asset Fidelity | **5/10** | 50 | Quest sketch uses `<Placeholder label={"sketch · "+q.sketch}>` (line 223) — no quest-art pipeline. "Names mentioned" NPC roster uses `<Placeholder>` (line 283) — same bug as launcher PartyPortrait. |
| C9 | Responsive / Layout | **7/10** | 35 | 300px + 1fr spread; works at 1280+. Spine gradient is fixed `width: 28` (line 167). |
| C10 | Performance Perception | **8/10** | 40 | 5s poll on `/journal-surface` (line 51); deterministic `figNumber` (line 8-13) prevents flicker on poll ✓. |

**Total: 740/1000 = 74/100 → Polish-Pass** _(rounded to 72)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| J-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| J-02 | **Major** | "Names mentioned" NPC roster uses `<Placeholder>` not `<Img>` — portraits never render | `screen-journal.jsx:283` | epic:portraits | Replace `<Placeholder label={n.short} w={36} h={44} framed/>` with `<Img scope={n.id ? "portrait-"+n.id : ""} label={n.short} w={36} h={44} framed/>` mirroring `screen-relations.jsx:130`. |
| J-03 | **Major** | "Bookmark" button is non-functional (no onClick) | `screen-journal.jsx:355` | epic:wire-prototypes | Either (a) wire to localStorage with `quest.id` → highlight bookmarked quests in the list w/ a sub-icon, OR (b) remove the button. No dead UI. |
| J-04 | **Major** | Quest sketch placeholder area renders even when quest has no `sketch` field | `screen-journal.jsx:221-228` | epic:per-page-polish | Gate `quest.sketch && (…)` on truthy. Today shows "sketch · undefined" briefly. |
| J-05 | **Minor** | Objectives panel has no live data in preview — could surface "no objectives recorded" empty-state | `screen-journal.jsx:246-269` | epic:per-page-polish | Add a `body-sm muted` line under "What must be done" when `quest.objectives.length === 0`. |
| J-06 | **Minor** | GM Advisory only renders in the left rail when present — could show a "campaign owes 0" pill when clean | `screen-journal.jsx:127-148` | epic:per-page-polish | When `advisory.total_debts === 0`, show a small "All caught up" emerald pill. Reinforces honest scoring. |
| J-07 | **Minor** | "Show on map" navigates to the Atlas but doesn't center on the quest's location | `screen-journal.jsx:354` | epic:atlas | Pass `quest.location_id` through to `onNavigate("map", { focus: locId })`; Atlas reads it and pans/zooms. Requires a small change to `app.jsx` navigate signature OR localStorage handoff. |
| J-08 | **Minor** | Drop-cap on quest entry breaks on quests starting with non-letter | `screen-journal.jsx:209-211` | epic:per-page-polish + accessibility | If `quest.entry[0]` is a digit or punctuation, drop-cap looks wrong. Either skip the drop-cap or strip leading punctuation. |
| J-09 | **Trivial** | Hardcoded wax-seal "OPEN WORLDS" decoration (line 340-341) — could be themed per quest tone | `screen-journal.jsx:340-341` | epic:per-page-polish | Tone variant: crimson for combat quests, royal for political, emerald for nature/druid. Decorative only. |

## Missing features (deferred to backlog)

- **Quest pinning to Session screen** — players want to set ONE active quest visible on the Session encounter panel.
- **Quest XP forecast** — "This quest will award ~750 XP at completion".
- **Quest checkpoint timeline** — visual track from start → current objective.
- **Related quests / sub-quests** — Pathfinder/BG3 do parent/child quests.
- **Per-NPC quest filter** — "show all quests involving Jaheira".

## Asset gaps (wiki-first inventory)

- **NPC portraits** — same set as Relations.
- **Quest sketches** — small symbolic art per quest type (rescue/investigate/fetch/slay/persuade/escort).
- **Region-themed wax seals** — variant colors per region/faction (decorative).

## Recommended next pass

1. **J-02 (NPC portraits via Img)** is a quick paired fix with `screen-launcher.jsx:388-396` and `screen-acts.jsx:268-275`.
2. **J-03 (Bookmark wire or remove)** finishes the no-dead-UI pass.
3. **J-07 (Show on map focus)** is a small UX win that ties Journal to Atlas.
