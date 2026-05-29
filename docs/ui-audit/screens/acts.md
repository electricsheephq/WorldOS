# Acts — 64/100 — Polish-Pass

**Route:** `/openworlds/#acts`
**Source:** `viewer/openworlds/screen-acts.jsx` (282 LOC)
**Screenshot:** `docs/ui-audit/screenshots/acts-1512.png`
**Compared to:** Pathfinder: Kingmaker chapter UI, BG3 Acts I/II/III tracker (P14 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "Two-pane codex layout. Read-only banner up top. 'Acts not tracked yet — campaign director has not compiled act progress' empty state. Wax-seal spine timeline ready to receive data. Mostly empty in this preview, by design."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **8/10** | 80 | Spine timeline with wax-seal nodes + "You are here" pulse + two-pane codex chrome is well-realized. |
| C2 | Information Density | **6/10** | 60 | When data is present, the right pane packs synopsis + Began/Through/Hero Lv + Key choices + Beats. When empty (today), the panes look sparse. |
| C3 | RPG Genre Conventions | **8/10** | 120 | All P14 patterns: vertical timeline, current-act pill, key choices recap, callbacks. |
| C4 | Interaction Affordance | **7/10** | 105 | Spine wax seal + act row both clickable to select ✓. No filter / no jump-to-act keyboard. |
| C5 | Content Completeness | **3/10** | 45 | Acts has NO data in preview — campaign director hasn't compiled. This is the correct, honest behavior per `tracked=false`, but the screen reads thin. |
| C6 | Accessibility | **6/10** | 60 | Spine wax-seal buttons missing `aria-label` (the visible numeral is the only ID). Animation on `current` flicker (line 124) — gate on reduced-motion. |
| C7 | Empty-State Handling | **8/10** | 40 | "Not yet written" for future acts (line 178-188) ✓; "Acts not tracked yet" full-pane (line 166-174) ✓. |
| C8 | Wiki-First Asset Fidelity | **4/10** | 40 | **`act.illustration` + per-act beat sketches + party-at-start portraits all use `<Placeholder>` (line 199, 251, 270)** — no real art ever renders. Should be `<Img scope={"act-"+act.id}>` for illustration and `<Img scope={"portrait-"+p.id}>` for party portraits. |
| C9 | Responsive / Layout | **7/10** | 35 | 1fr/1.2fr split at 1512 reads balanced. Spine paddingLeft 24 + spine width 2 holds at 1280. |
| C10 | Performance Perception | **8/10** | 40 | 7s poll on `/acts-surface` (line 40); empty state until first fetch ✓. |

**Total: 625/1000 = 63/100 → Polish-Pass** _(rounded to 64)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| A-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| A-02 | **Major** | `act.illustration` uses `<Placeholder>` not `<Img>` — even when the engine could emit a scope | `screen-acts.jsx:198-200` | epic:per-scene-art | Replace with `<Img scope={act.imageScope \|\| ("act-"+act.id)} label={"illustration · "+act.illustration}>` so a per-act cover image (e.g., Act-I "Coast Road" landscape) can render. Falls back to Placeholder ✓. |
| A-03 | **Major** | Per-act beat sketches use `<Placeholder>` (line 251) | `screen-acts.jsx:251` | epic:per-scene-art | Replace with `<Img scope={m.imageScope \|\| ("beat-"+(m.id\|\|i))}>` for memorable-beat thumbnails. |
| A-04 | **Major** | "Who walked this act" portraits use `<Placeholder>` | `screen-acts.jsx:268-275` | epic:portraits | Replace with `<Img scope={"portrait-"+p.id}>` mirroring all other party portrait spots. (Paired with launcher L-05 and journal J-02.) |
| A-05 | **Major** | No data ever populates Acts for a fresh save — "campaign director has not compiled" is honest but undercut by the lack of any "what would appear here" example | `screen-acts.jsx:65-70` | epic:per-page-polish | When `!tracked`, show a Sample / Preview of what an Act would look like (a faded "Act I — Coast Road" entry) so a new player understands the value before the director runs. OR add a one-sentence "Acts are compiled once you've played ≥ N beats; check back later." |
| A-06 | **Minor** | Spine wax-seal numeral animation is `flicker` — runs always for current | `screen-acts.jsx:124` | accessibility | Gate animation on `[data-reduced-motion=on] { animation: none; }`. |
| A-07 | **Minor** | "Key choices made" + "Beats and callbacks" sections always rendered (with empty-states when no data) — could be hidden together when both empty | `screen-acts.jsx:212-261` | epic:per-page-polish | When `(act.choices.length + (act.beats\|\|act.memories\|\|surface?.threads\|\|[]).length) === 0`, show a single empty-state instead of two sub-sections. |
| A-08 | **Minor** | Spine node sizes don't differentiate by act significance (climax vs scene) | `screen-acts.jsx:111-125` | epic:per-page-polish | Optional: vary wax-seal node radius (24 / 28 / 32) by `act.weight`. |
| A-09 | **Trivial** | "Read-only" banner is shown inside the spine column — could go above both panes | `screen-acts.jsx:65-70` | epic:per-page-polish | Move the read-only banner to the top of the screen for visibility. |

## Missing features (deferred to backlog)

- **Per-act outcome card** — Pathfinder shows post-act recap with key stats.
- **Companion-in-act ledger** — who was with you during this act.
- **Cinematic reel** — sequence of beat thumbnails as a horizontal scroller.
- **"This act echoes in Act III"** — quest callback explicit graph.
- **Branch markers** — if a choice led to a divergent act, show on spine.

## Asset gaps (wiki-first inventory)

- **Per-act illustration** — 1 cover image per act (sword-coast at dawn, lower-city plaza burning, undercity tunnel, etc.).
- **Beat sketches** — small thematic art per memorable beat.
- **Companion portraits** — already shipped.

## Recommended next pass

1. **A-02 / A-03 / A-04 (replace Placeholders with Img)** is a paired sweep with launcher / journal — finish it once.
2. **A-05 (sample preview when untracked)** lifts the empty state from "is this broken?" to "I see what's coming".
3. **A-07 (collapse empty section pair)** wins back vertical space.
