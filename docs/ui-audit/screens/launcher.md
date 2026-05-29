# Chronicles (Launcher) — 64/100 — Polish-Pass

**Route:** `/openworlds/#launcher`
**Source:** `viewer/openworlds/screen-launcher.jsx` (514 LOC)
**Screenshot:** `docs/ui-audit/screenshots/launcher-1512.png`
**Compared to:** BG3 main menu, Kingmaker chapter screen, Skyrim load-game (P13 in `RPG_REFERENCE_PATTERNS.md`)
**First impression (5-second read):** "Beautiful masthead, but every save thumb is a 'seal' placeholder and the title bar is overlapping the nav rail."

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **6/10** | 60 | Hero plate + drop-cap recap is beautiful; **title-bar text wraps over nav-rail glyphs at the top-left** (chrome.jsx:422 `paddingLeft: 76` collides with nav-rail icons at this viewport). |
| C2 | Information Density | **7/10** | 70 | 1.15fr/1fr split reads well; stat strip (LAST SAT / SESSIONS / HEROES / REGION) is well-paced. |
| C3 | RPG Genre Conventions | **7/10** | 105 | Hero plate, chronicle list, party portrait row, recap — all on-pattern. Missing: per-save scene thumbnail (BG3 saves the last scene as PNG). |
| C4 | Interaction Affordance | **7/10** | 105 | `Resume Chronicle` CTA is clear; `Roster` + `Journal` quick links are good. **AI GM segmented radio in `NewCampaignModal` has no onChange — non-functional (line 450).** |
| C5 | Content Completeness | **5/10** | 75 | Three campaign rows all show identical title "Unofficial Baldur's Gate 3+ Universe Seed" — no differentiating campaign-specific title/recap/region. |
| C6 | Accessibility | **6/10** | 60 | Buttons have hover transitions; `Forge a new hero` button missing `aria-label`; the masthead text "OPEN WORLDS · A TABLETOP, REAWAKENED" text-shadows over a busy image (line 91-100) — contrast risk. |
| C7 | Empty-State Handling | **9/10** | 45 | "No campaigns yet ✦ Start your first adventure" empty-state is honest + on-brand (line 106-120). |
| C8 | Wiki-First Asset Fidelity | **3/10** | 30 | `CampaignRow` uses `<Placeholder label="seal" w={56} h={56} />` (line 371) — should be `<Img scope={c.imageScope || c.coverScope}/>` so each chronicle gets its own scene art. **PartyPortrait helper (line 388-396) uses `<Placeholder>` directly, never reaches Img — dead code path or stale.** Right-pane party portraits DO use `<Img scope={"portrait-"+p.id}>` (line 249) ✓. |
| C9 | Responsive / Layout | **7/10** | 35 | Works at 1440 and 1512. At < 1280 the 1.15fr/1fr split collapses — not tested. |
| C10 | Performance Perception | **8/10** | 40 | One-time `/openworlds/campaigns.json` poll every 4s (app.jsx:117); no obvious 404 storm in console. |

**Total: 625/1000 = 64/100 → Polish-Pass**

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| L-01 | **Critical** | Title-bar text wraps over the nav-rail glyphs at top-left | `chrome.jsx:415-432`, `app.jsx:259-267` | epic:per-page-polish | At 1280px+ the title text "Open Worlds · <campaign> · <location>" never overlaps the nav-rail. Either (a) start `padding-left` greater than nav-rail width, or (b) move the campaign/location text to the right of the title bar. Verified at 1280/1440/1920. |
| L-02 | **Major** | Chronicle rows use `seal` placeholder, no per-save thumbnail | `screen-launcher.jsx:371` | epic:per-scene-art | `CampaignRow` thumbnail uses `<Img scope={c.thumbScope \|\| c.imageScope}/>` with a graceful Placeholder fallback. New surface field `campaigns[].thumbScope` (or reuse `imageScope`) — engine writes the last `scene.imageScope` to it on save. |
| L-03 | **Major** | All visible chronicle rows show identical title | `screen-launcher.jsx:349-386` + `/openworlds/campaigns.json` | epic:per-page-polish | Each campaign card shows a distinct title OR distinct (date + region) subline — if many saves are the same campaign, group/badge them. The current "Unofficial Baldur's Gate 3+ Universe Seed" repeated × 3 reads as a bug. |
| L-04 | **Major** | New-Campaign modal's "AI Game Master" radio is non-functional | `screen-launcher.jsx:449-451` | epic:wire-prototypes | Wire the AI GM SegRadio (`Permissive` / `Standard` / `Strict`) to `useState` + carry into the `onCreate` payload. OR remove the field if not yet supported. No dead UI. |
| L-05 | **Major** | PartyPortrait helper uses `<Placeholder>` not `<Img>` | `screen-launcher.jsx:388-396` | epic:portraits | Replace `<Placeholder label={portrait.short}>` with `<Img scope={portrait.id ? "portrait-"+portrait.id : ""} label={portrait.short}>`. Dead component if not referenced anywhere — confirm + delete, OR fix in case it's reused. |
| L-06 | **Minor** | Masthead tagline contrast over busy image | `screen-launcher.jsx:91-100` | epic:per-page-polish + accessibility | Add a darkening scrim or text-shadow strong enough to meet WCAG AA over any masthead scene. Verify against the Lower City + Candlekeep cover art. |
| L-07 | **Minor** | "Forge a new hero" and "Begin a new chronicle" buttons are visually identical | `screen-launcher.jsx:124-167` | epic:per-page-polish | Differentiate primary (begin chronicle) vs secondary (forge hero, which doesn't even start a campaign) — different icon, different tone, or move "Forge a new hero" into a smaller secondary slot. Today they look like duplicates. |
| L-08 | **Minor** | Eyebrow color `var(--crimson)` lacks a defined hover state for the chronicle-row CTA | `screen-launcher.jsx:282` | epic:per-page-polish | Brass ghost buttons (`Roster`, `Journal`) need a clearer hover/focus state. Audit btn-ghost in styles.css. |
| L-09 | **Minor** | Native-bridge "Summoning the Dungeon Master…" never resolves in browser preview | `screen-launcher.jsx:26-62` | epic:per-page-polish | When `OpenWorldsNative.hasBridge()` is false (preview), the button label says "Resume Chronicle" but the click just `onNavigate("table")` — fine. Add a small "preview" hint near the CTA so a preview-only tester knows resume won't actually summon. |
| L-10 | **Trivial** | "Light the lantern" is a delightful but undocumented copy choice for the modal Submit | `screen-launcher.jsx:455` | epic:per-page-polish | Keep, or align with the "Bind the hero" / "Sow the change" naming pattern. Add to a copy style guide. |

## Missing features (deferred to backlog)

- **Cloud/sync indicator** — no per-save "cloud / local / latest" badge. (Owner deprioritized — local 1.0.)
- **Recently-played sort** — campaigns appear in catalog order, no explicit "most recent on top".
- **Search / filter chronicles** — works when ≤ 5 saves, breaks at 50.
- **Per-save thumbnail capture** — see L-02 (engine writes last `imageScope` on save).

## Asset gaps (wiki-first inventory)

- **Chronicle cover scenes** — need `_private/<world>/scenes/cover-<campaign>.jpg` OR reuse `location:<location_id>` as the chronicle thumbnail.
- **Hero (Caelar) portrait** — Caelar is a custom test PC; per `clawdnd-canonical-setup` memory, a faceless custom PC must show the silhouette, never a class crest. Behavior is correct ✓. Provide a portrait pipeline for created characters (gateway-gen vs default; see EPIC A discussion in `#242`).

## Recommended next pass

1. Land **L-01 (title-bar overlap)** — recurs on every screen.
2. Land **L-04 (AI GM radio dead)** + **L-05 (PartyPortrait helper)** — small, removes dead UI.
3. **L-02 (per-save thumbnail)** is the biggest visible-quality lift on this screen — needs engine surface change.
