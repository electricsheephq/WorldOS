# OpenWorlds UI/UX Scoring Rubric — v1.0

> Deterministic per-screen rubric used by the UI/UX audit (issue #242 Phase 5).
> The score is an instrument, not the goal: it tells the implementation agent
> where to spend the next hour. Every per-screen audit file under
> `docs/ui-audit/screens/<screen>.md` carries this table filled in with score +
> one-line justification per criterion.
>
> This is the visual + interaction layer scorecard. The play-fidelity scorecard
> (gate + 3 lenses) lives in `qa/SCORING.md` and is unrelated.

## Method
- Each criterion is graded **0–10** (integer); a per-screen weighted total /100 is reported.
- Per criterion: **0–2** broken / absent, **3–4** stub or honest empty-state, **5–6** functional but rough, **7–8** polished + on-genre, **9–10** "this is the template the rest of the app should match" (Relations / Parley currently sit here).
- Scoring is **honest** — a beautiful blank state still scores low on Content Completeness; a broken-but-data-bound surface scores low on Interaction Affordance even if the data is real.
- Comparisons are made to **Baldur's Gate 3** (visual + companion + dialogue UX), **Pathfinder: Kingmaker / Wrath of the Righteous** (party + map + dialogue + inventory UX), and the **D&D 5e SRD presentation conventions** (Roll20 / DNDBeyond character sheet).
- Reference patterns library: `docs/ui-audit/RPG_REFERENCE_PATTERNS.md`. Use it; never paste a competitor screenshot into the repo (see `docs/OPENWORLDS_DESIGN_ASSET_POLICY.md`).

## The ten criteria

| # | Criterion | What it measures | Weight |
|---|---|---|---|
| C1 | **Visual Polish** | Typography hierarchy, spacing/rhythm, ornamental consistency (parchment + corner filigree), iconography, hover/active affordances, no overlapping/clipped/illegible elements. | 10 |
| C2 | **Information Density** | Right amount of info per real-estate. CRPG screens (sheet, inventory, bestiary) should be dense and scannable; chat/parley should be calm and focused. Penalize wasted whitespace AND visual cramming. | 10 |
| C3 | **RPG Genre Conventions** | Matches what a Pathfinder/Kingmaker/BG3 player expects to find on this screen: stat-block layout, initiative tracker, faction reputation bar, quest-card structure, dialogue branch indents, etc. The right vocabulary in the right places. | 15 |
| C4 | **Interaction Affordance** | Every visible button does something OR is honestly hidden/disabled with a tooltip explaining why. No "dead buttons" without `can_act` gating + label. Hover/focus/active states are legible. Keyboard shortcuts where they make sense. | 15 |
| C5 | **Content Completeness** | Are all expected fields/data populated? Empty fields (Bestiary HD/AC/Speed/Senses), missing portraits, "0 spells prepared" with no browse path, "(none)" walls. Includes the wiki-first ingest gap. | 15 |
| C6 | **Accessibility** | Reduced-motion / high-contrast respected; UI-scale honored; keyboard nav reaches every actionable control; ARIA roles + labels on icon-only buttons; color contrast on text-over-image (the Session recap-on-scrim bug is the canonical fail). | 10 |
| C7 | **Empty-State Handling** | When there is no data, does the screen say so honestly + helpfully (Relations "no faction standings yet") OR does it leak demo/Pathfinder filler (Forge ledger, World Seed "Linzi")? | 5 |
| C8 | **Wiki-First Asset Fidelity** | Per `project_worldos_wiki_first` direction: assets ingested from BG3/FR wikis (portraits, item icons, creature art, location scenes) where the canon exists. Penalize placeholder tiles when a canon image exists in `content/worlds/_private/`. | 10 |
| C9 | **Responsive / Layout** | Viewports tested: 1280×800 (laptop default), 1440×900 (desktop), 1920×1080 (full HD). Honors `--ui-scale`. The fidelity-plan viewports are the gate. Mobile is out-of-scope for 1.0 graphic release. | 5 |
| C10 | **Performance Perception** | Render time of the screen; no console errors / 404 storms on `/image?scope=…`; debounced inputs; smooth tab/screen switch; no layout-shift after data binds. Measured via `preview_network` (failed-only) + `preview_console_logs` (error level). | 5 |

Total weight = 100. A screen ≥ 80/100 is **release-ready**; 60–79 is **polish-pass**; 40–59 is **finish work**; < 40 is **blocker** (must lift before 1.0 graphic release).

## Score → severity → milestone mapping

| Total | Disposition | Milestone (proposed) |
|---|---|---|
| 80–100 | Release-ready — small polish issues only (Trivial / Minor) | Polish-Pass Backlog |
| 60–79 | Polish-Pass: 1–3 Major findings, no Critical | Graphics Release 1.0 — Polish Wave |
| 40–59 | Finish-work: ≥ 1 Critical or ≥ 3 Major findings | Graphics Release 1.0 — Finish Wave |
| < 40 | Blocker — must lift to ≥ 60 before 1.0 ships | Graphics Release 1.0 — Blocker Wave |

## Finding severity (used in issue labels)

| Severity | Definition | SLA before 1.0 |
|---|---|---|
| **Critical** | Blocks play, looks broken to a new player, OR shatters the prestige-CRPG framing (e.g. dead action bar, unreadable text on image, demo-leak in production). | Must fix |
| **Major** | Visible degradation a Pathfinder/BG3 player would call out in the first 60 seconds (missing portraits where the pipeline supports them, empty stat block in Bestiary, broken Spellbook). | Must fix unless owner defers |
| **Minor** | Polish gap — affordance/iconography/copy nit; doesn't block, but a release-quality screen wouldn't ship with it. | Should fix |
| **Trivial** | Pure preference / non-load-bearing copy or pixel tweak. | Nice-to-have |

## Cross-cutting tags (used as extra issue labels)

- `screen:<id>` — locator (one of the 16 screens listed in chrome.jsx NAV_GROUPS).
- `epic:portraits` — rolls up to EPIC A.
- `epic:atlas` — rolls up to EPIC B.
- `epic:playable` — rolls up to EPIC C.
- `epic:per-scene-art` — rolls up to EPIC D.
- `epic:demo-leak` — rolls up to EPIC E.
- `epic:wire-prototypes` — rolls up to EPIC F.
- `epic:per-page-polish` — rolls up to EPIC G.
- `asset-gap` — needs `tools/ingest/wiki_images.py` or local-only `_private` art.
- `accessibility` — C6.
- `keyboard` — keyboard-nav specific.
- `copy` — copywriting / on-screen text.
- `iconography` — icon registry (rolls into #174).
- `severity:critical` / `severity:major` / `severity:minor` / `severity:trivial`.

## Per-screen audit file template

Every audit file under `docs/ui-audit/screens/<screen>.md` MUST have this shape:

```
# <Screen Title> — <total>/100 — <disposition>

**Route:** `/openworlds/#<hash>`
**Source:** `viewer/openworlds/screen-<id>.jsx` (LOC)
**Screenshot:** `docs/ui-audit/screenshots/<id>-1440.png`
**Compared to:** <Pathfinder analog>, <BG3 analog>

## Score
| # | Criterion | Score | Weighted | Justification (one line) |
|---|---|---|---|---|

## Findings
| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|

## Missing features (deferred to backlog)

## Asset gaps (wiki-first inventory)

## Recommended next pass
```

That shape is the **contract** the implementation agent reads. Every finding row must be (a) anchored to file:line, (b) acceptance-criteria-bearing, and (c) ready to file as a GitHub issue with no further translation.
