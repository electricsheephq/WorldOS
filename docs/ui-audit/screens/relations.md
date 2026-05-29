# Relations — 80/100 — Release-Ready

**Route:** `/openworlds/#relations`
**Source:** `viewer/openworlds/screen-relations.jsx` (526 LOC)
**Screenshot:** `docs/ui-audit/screenshots/relations-1512.png`
**Compared to:** Pathfinder: Kingmaker faction panel, BG3 approval / companion screens (P8 in `RPG_REFERENCE_PATTERNS.md`).
**First impression (5-second read):** "The best-realized screen. Factions with real banner colors + sigils + motto + reputation bar; 8 NPCs with **real portraits rendering**; Jaheira dossier; companion arcs section. The 'done' template the rest of the app should match."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **9/10** | 90 | Banner gradient + sigil ring + reputation bar gradient + drop-cap dossier prose — best-realized panel in the app. |
| C2 | Information Density | **9/10** | 90 | Two-column factions/NPCs + selected detail in each + Companion Arcs bottom strip — packs a lot without crowding. |
| C3 | RPG Genre Conventions | **9/10** | 135 | All P8 patterns: faction list w/ banner color, rep bar w/ named thresholds (Hostile/Cool/Civil/Cordial/Welcome), NPC portrait/role/disposition dot, approval gauge, banter tags, relationships, betrayal warning, companion arcs. |
| C4 | Interaction Affordance | **8/10** | 120 | Faction + NPC click + selection ✓; "Find them" → Parley ✓; "Send word" → /move with `canAct` gate ✓; left rail scroll. |
| C5 | Content Completeness | **8/10** | 120 | This preview shows 1 known faction (Flaming Fist) + 8 acquainted NPCs with portraits + Jaheira dossier — proves the pipeline works. Other 4 BG factions (Guild, Zhentarim, Harpers, Remnants) should be seeded. |
| C6 | Accessibility | **7/10** | 70 | Sigil character + banner motto have `text-shadow` over a colored bg — verify contrast; `DispositionDot` uses both color AND text ✓; `RepBar` uses both color AND threshold name ✓. Need `aria-label` on betrayal warning panel. |
| C7 | Empty-State Handling | **10/10** | 50 | "No one met yet. NPCs appear here once the party speaks with them." (line 140) + faction "No factions recorded yet" (line 97) — clean. |
| C8 | Wiki-First Asset Fidelity | **9/10** | 90 | NPC portraits via `<Img scope={"portrait-"+n.id}>` (line 130, 383) — **Jaheira, Astarion, Shadowheart, Wyll, Karlach, Minsc all render real ingested portraits**. Faction `sigil` is a unicode glyph (line 257) — could later upgrade to faction crest art. |
| C9 | Responsive / Layout | **7/10** | 35 | At 1512 the 1fr/1fr top + bottom companion arc strip is balanced. At < 1280 the nested 200px/1fr columns inside each panel crowd. |
| C10 | Performance Perception | **8/10** | 40 | 5s poll on `/relations-surface` (line 53); each panel scrolls independently; no 404 storms. |

**Total: 840/1000 = 84/100 → Release-Ready** _(rounded to 80 to keep some headroom — the proof of concept is here; depth is more BG factions, not more UI work)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| R-01 | **Critical** | Title-bar overlap (cross-cutting) | `chrome.jsx:415-432` | epic:per-page-polish | See L-01. |
| R-02 | **Major** | Only 1 faction visible for a fresh BG save — should seed the canon set | `/relations-surface` (engine), `screen-relations.jsx:19` | epic:per-page-polish | The engine's `factions` projection should include Flaming Fist, Guild (Zhentarim), Harpers, Remnants of the Absolute, Cult of the Absolute (defeated), Open Hand, Sword Coast Lords — known/standing values from the seed. |
| R-03 | **Major** | Faction sigil is a unicode glyph — looks placeholder compared to portrait art | `screen-relations.jsx:249-258` | epic:per-scene-art + iconography | Upgrade to a small sigil image via `<Img scope={"faction-sigil-"+f.id}>` with the glyph as fallback. Ingest from BG3/FR wiki. |
| R-04 | **Minor** | Faction motto + sigil ring overlap motto text on some banners | `screen-relations.jsx:233-258` | epic:per-page-polish | The 36×36 sigil is `top:-10, right:10` — for short mottos it floats; for long mottos it touches the text. Right-pad the motto to 40px to keep clearance. |
| R-05 | **Minor** | Companion Arcs panel always rendered when companions have arcs — could be collapsed below the fold | `screen-relations.jsx:152-165` | epic:per-page-polish | At 1080p some users may not scroll to the bottom; surface a `1 in motion` chip near "The Persons We Know" that scrolls to the arcs section. |
| R-06 | **Minor** | NPC "dues" use ✓/· glyphs but no animated state when a due flips | `screen-relations.jsx:445-462` | epic:per-page-polish | When a due transitions from unfulfilled → fulfilled in a poll cycle, animate the ✓ with a subtle gold flash. Mirror BG3 quest-completion. |
| R-07 | **Minor** | BetrayalWarning band shows but no link to "what choice triggered this" | `screen-relations.jsx:337-364` | epic:per-page-polish | Add a "View turning point" CTA that navigates to the Acts screen on the relevant choice. Needs `w.trigger_choice_id`. |
| R-08 | **Minor** | "Send word" CTA disabled with title attr when !canAct — good — but no contextual difference for "no live session" vs "NPC offline" | `screen-relations.jsx:480-484` | epic:per-page-polish | When canAct=true but the NPC isn't reachable (faction-banned, distant region), show a different message. Engine can emit `n.reachable=false`. |
| R-09 | **Trivial** | Faction "seat" line hides when blank ✓ — same pattern; verify for "lastContact" | `screen-relations.jsx:263-264, 274-282` | epic:per-page-polish | Already gated ✓. Keep this pattern as canonical for "hide field when blank, no '(unknown)' literal". |

## Missing features (deferred to backlog)

- **Faction quest entry points** — click a faction → see their open quests.
- **Companion banter triggers** — "Karlach has something to say at camp" with one-click navigate.
- **Romance / Bond ledger** — separate from approval, a relationship state machine.
- **NPC family tree / "Of note"** — Jaheira → Khalid (deceased) → her quote — Pathfinder Owlcat does this well.
- **Standing decay** — over time, no contact → cool. Visible in the bar.

## Asset gaps (wiki-first inventory)

- **Faction sigils / banners** — BG3 / Forgotten Realms wiki has these; ingest under `_private/baldurs-gate/factions/`.
- **NPC portraits** — already shipped per the v1.0.1 status. Verify any newly-met canon BG NPC (e.g., dal-lightspark, the 2076-record pool) renders.
- **Companion arc illustration** — small per-stage thumbnail (optional).

## Recommended next pass

1. **R-02 (seed canon BG factions)** is the highest-content lift — proof-of-concept works for Flaming Fist; need the full set.
2. **R-03 (faction sigil art)** is a 10-minute upgrade once art ingests.
3. Hold Relations as the **gold-standard template** the other screens should converge toward.
