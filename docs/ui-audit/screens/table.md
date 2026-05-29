# Session (Table) — 70/100 — Polish-Pass

**Route:** `/openworlds/#table`
**Source:** `viewer/openworlds/screen-table.jsx` (580 LOC)
**Screenshot:** `docs/ui-audit/screenshots/table-1512.png`
**Compared to:** BG3 main play UI (dialog + portrait strip + log), Kingmaker adventure log + party HUD (P5/P11 patterns in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "Genuinely good 3-column CRPG table layout. Scene + recap + scrim + log + active-quest sidebar all on pattern. The Caelar portrait is a silhouette and the right column is mostly empty in this preview state — but the bones are strong."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **8/10** | 80 | Scene plate + readability scrim + day pill + scene caption is the best-realized scene block in the app. Drop-cap chronicle entries land. |
| C2 | Information Density | **8/10** | 80 | 260px / 1fr / 280px split works at 1440+; party + center scene + log + right sidebar (quests + stash + encounter) reads at a glance. |
| C3 | RPG Genre Conventions | **8/10** | 120 | Tabletop Chronicle log uses Narration/Action/Dialog/Roll entry kinds (line 483-538) — on-pattern. **GM Advisory panel (line 311-332) is a unique strength, not a gap.** |
| C4 | Interaction Affordance | **8/10** | 120 | d20/d12/d8/d6 quick-roll buttons (line 289-292) + Declare input wired to `postMove` (line 164-174) + action bar gated on `actionById("do")?.available`. ✓ |
| C5 | Content Completeness | **6/10** | 90 | Active Quests panel shows "No active quests" in this preview; Quick Stash "Inventory empty"; Encounter panel "Choose what to risk" placeholder. None of these are bugs — but the screen reads sparse without live data. Bar a longer playthrough screenshot. |
| C6 | Accessibility | **7/10** | 70 | Scrim over scene caption ✓ readable; d20-buttons icons are decorative (need aria-labels); chat input has placeholder ✓; the `LogEntry` text color contrast in narration kind should be audited at high-contrast mode. |
| C7 | Empty-State Handling | **8/10** | 40 | Every panel has a `<div className="body-sm muted">` empty-state line ("No moves yet" / "No actions … until a campaign snapshot loads" / "No active combat round"). ✓ |
| C8 | Wiki-First Asset Fidelity | **6/10** | 60 | Scene `<Img scope={scene.imageScope}>` ✓; PartyRow portraits use `<Img scope={"portrait-"+p.id}>` (line 439) ✓ — but Caelar is custom-PC silhouette (correct fallback). Quick Stash uses `IconPlate glyph={...}` (line 362) — relies on icon registry. |
| C9 | Responsive / Layout | **6/10** | 30 | At 1280 the 260+1fr+280 = ~720px center is tight. Below 1280, the right column should collapse to a tab strip. Not tested. |
| C10 | Performance Perception | **8/10** | 40 | 3 separate polls (session-surface / journal-surface / chat) on a 5s clock (line 95-128); visibility-gated ✓. No console error storm. |

**Total: 730/1000 = 73/100 → Polish-Pass** _(rounded to 70 per the table scoring rule)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| T-01 | **Critical** | Title-bar text overlaps nav-rail at top-left | `chrome.jsx:415-432` | epic:per-page-polish | (Cross-cutting; see L-01 in `screens/launcher.md`.) |
| T-02 | **Major** | Caelar portrait is a silhouette across PartyRow + hero card | `screen-table.jsx:439`, `chrome.jsx:225-235` | epic:portraits | When the PC is custom-created (`bindHero`), the portrait pipeline writes `portrait-<id>.png` to the served image store. Until then, silhouette is the correct fallback (per `clawdnd-canonical-setup`). This is a wait-on-EPIC-A item. |
| T-03 | **Major** | Recent-Events stream mixes engine-recent + chat-tail + local log in display order | `screen-table.jsx:44` | epic:per-page-polish | `visibleLog = [...recentEvents, ...chatBeats, ...log]` — concatenation, not time-merged. Should sort by an authoritative timestamp so a player who acts mid-poll doesn't see their own action above older DM prose. Add `entry.at` (engine seq#) and stable-sort by it before rendering. |
| T-04 | **Major** | Encounter panel actions show full label even when disabled — disabled_reason in `hand muted` is small | `screen-table.jsx:381-389` | epic:per-page-polish | When `!a.available`, the row prefixes the label with the disabled glyph (`inventory.locked`) ✓ but the disabled_reason copy is `hand muted` (line 384) — bump to a `body-xs` warning tone for legibility. Verify against actual disabled actions during a paused combat. |
| T-05 | **Minor** | GM Advisory shows ONLY the first debt (line 313-327) — total debt count is visible but extras hidden | `screen-table.jsx:311-332` | epic:per-page-polish | Either render up to 3 debts (mirroring `screen-journal.jsx`) or add a "+N more debts" chip that opens the Journal Advisory section. |
| T-06 | **Minor** | Active Quests panel doesn't show region/quest-art | `screen-table.jsx:334-356` | epic:per-scene-art | When a quest has `q.region`, show a one-line eyebrow `quest.region` above the title; later add `quest.imageScope` for a 24×24 quest sigil. |
| T-07 | **Minor** | Round Order header always shows under Encounter even out of combat | `screen-table.jsx:398-417` | epic:per-page-polish | Hide the "Round Order" SectionTitle when `roundOrder.length === 0` AND the empty-state message — show neither, save vertical space for the encounter description. |
| T-08 | **Minor** | "Read-only: <reason>" placeholder text in input is the only signal that play isn't live | `screen-table.jsx:300` | epic:per-page-polish | When `!canAct`, also dim the d20/d12/d8/d6 buttons (they already pass `disabled={!actionById("check")?.available}` ✓ but no tooltip). Add a `title="…disabled_reason…"` on each. |
| T-09 | **Minor** | "Travel" / "Parley" / "Camp" buttons inside scene plate are tone="dark" with no hover affordance | `screen-table.jsx:265-267` | epic:per-page-polish | Add a hover background to the dark buttons over the scene image; current default `.btn.dark` is hard to read over the BG cityscape. |
| T-10 | **Trivial** | Pill `tone="royal"` for dayLabel may low-contrast vs scrim at sunset/dusk scene art | `screen-table.jsx:254` | epic:per-page-polish | Verify the day pill's gold-glow text-shadow holds against the warm dusk scrim of the BG cityscape. |

## Missing features (deferred to backlog)

- **Per-turn cue chip strip** — BG3 surfaces "Sneak Attack available" / "Concentration on Bless" beside the active hero. The Command surface (`commandCenter.cues`, line 254-281 in `screen-combat.jsx`) has the data; surface it on the Session table too when a combat is active.
- **DM TTS / voice toggle in the chronicle log** — Kokoro voice integration exists in the engine (`servers/voice/`); no per-narration play button in the chronicle.
- **Quest completion / failure inline toast** — only the engine recentEvents stream surfaces these.

## Asset gaps (wiki-first inventory)

- **Scene art per location** — the masthead `Img scope={scene.imageScope}` (line 234) renders when present, else `<Placeholder>`. Confirm `_private/baldurs-gate/scenes/<location>.jpg` exists for each BG3 district (Lower City, Upper City, Outer City, Wyrm's Crossing, Reithwin, Candlekeep, Elturel, …).
- **Quest sigils** — 16×16 or 24×24 per quest, derived from quest tag (rescue=hand, investigate=eye, fetch=parcel, slay=sword).

## Recommended next pass

1. **T-03 (sort log entries by engine seq#)** is the most felt bug — your own action appearing in the wrong chronological position breaks immersion.
2. **T-04 (encounter disabled_reason legibility)** + **T-08 (d20 buttons title)** together make the Read-only mode self-explanatory.
3. **T-07 (hide Round Order out of combat)** wins back vertical real estate.
