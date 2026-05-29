# OpenWorlds UI/UX — Master Tracker (Phase 5 deep audit, 2026-05-29)

> **Source-of-truth index** for the page-by-page UI/UX audit landed under `docs/ui-audit/`.
> Updated by the implementation agent as issues land. Mirror of `#242` Phase 5 status.
>
> **Audit method:** live walkthrough of every screen at `127.0.0.1:8799/openworlds/`, viewports
> 1440×900 and 1512×982; sources read for every `viewer/openworlds/screen-*.jsx`; findings anchored
> to file:line; severity rubric in `SCORING_RUBRIC.md`. Comparable patterns in
> `RPG_REFERENCE_PATTERNS.md` (Pathfinder/Kingmaker/BG3/DNDBeyond).
> **Read order on resume:** this file → `SCORING_RUBRIC.md` → the per-screen doc you're working on.

## Loop 2 update (2026-05-29 cont.)

After Loop 1 closed at 70-75% confidence, Loop 2 attacked the named gaps. Material findings:

- **Asset catalog re-calibration — [#281](https://github.com/100yenadmin/ClawDnD/issues/281).** `content/worlds/_private/baldurs-gate/images/` holds 2,359 art dirs: 2,077 portraits (incl. all 7 BG3 origins), 140 items, 77 creatures, 26 scenes, 12 classes (full PHB), 11 races, 10 maps, 6 factions. **Most Loop-1 "asset-gap" findings are UI wire-up, not ingest.** #265 / #267 / #270 / #273 / Map M-01 / Bestiary creature-slug all re-frame as "you have the art; wire `<Img scope=…>` to it".
- **Shared infrastructure verified clean.** `data.js` is the empty `INITIAL_STATE` (line 14-22 explicitly forbids demo content); `tooltip.jsx` + `toast.jsx` solid; `icon-registry.jsx` has 12 icons + 21 aliases, room to grow (#279 still on-point); `index.html` uses local-vendored React/ReactDOM/Babel + local-vendored fonts via `vendor/google-fonts.css` (asset policy satisfied — no CDN).
- **Server-route names validated against `viewer/server.py:5180-5407`.** All `/<screen>-surface` references in the audit docs match real handlers. No `/merchant-surface`, `/seed-surface`, `/persons-surface`, or `/lore-surface` routes exist — confirms MK-03, S-02, BE-04 as correctly scoped "needs engine route".
- **Camp Sidebar promoted to standalone audit — `screens/camp-sidebar.md` (60/100, Polish-Pass).** 12 findings; CS-01 (Begin Resting wire) [#282](https://github.com/100yenadmin/ClawDnD/issues/282) + CS-02 (per-companion fireside) [#283](https://github.com/100yenadmin/ClawDnD/issues/283) filed.
- **Responsive system — [#284](https://github.com/100yenadmin/ClawDnD/issues/284).** `.stack-on-narrow` class defined in `styles.css:906` but **never applied** in any screen JSX. 3-column screens crowd below 1200px instead of collapsing.
- **GitHub routing audit.** Verified milestones + labels for #244–#279; cleaned up the `epic` label leak on sub-issues (#260, #261, #265, #273, #277) and the misleading `screen:launcher` on cross-cutting #260.

**Loop 2 confidence: ~85%.** Remaining open gaps (would lift to 95% in Loop 3): interactive coverage (click-through Create wizard / palette switcher / a11y modes), multi-viewport capture (1366 / 1920), live-session UX (`canAct=true` state), native macOS app verification.

## Loop 3 update (2026-05-29 cont.)

Attacked Loop-2 closeout gaps. Confidence rises **to ~90%.**

- **Multi-viewport responsive verified.** Headless Chrome captures at 1366×768 (the laptop floor) + 1920×1080. Confirms #284 (.stack-on-narrow unused): at 1366 the **Table** + **Character** + **Inventory** 3-column screens crowd; Combat + Create + Map read OK. Filed as [#288](https://github.com/100yenadmin/ClawDnD/issues/288).
- **State validation against a populated campaign.** `/atlas-surface` ships 23 known_locations + 45 edges; `/relations-surface` ships 5 BG factions + 11 canon NPCs (Jaheira / Minsc and Boo / Shadowheart / Wyll / Karlach / The Emperor / Withers / Astarion / …); `/character-surface` ships Astarion @ Rogue L5 HP 29/29 AC 14. **The engine DOES seed the canon when given a populated campaign.** Loop-1's framing of #261 + #273 ("under-populated") was actually a fresh-state observation. Re-framed as [#289](https://github.com/100yenadmin/ClawDnD/issues/289) — the ask is now "ensure fresh-save initial seed", not "build the seed projection".
- **NEW bug** — `/character-surface` ships `feats` and `classFeatures` arrays with **identical content** (per SRD 5.2: `feats` = optional ASI-feats taken at L4/8/12/16/19; `classFeatures` = auto-by-level — disjoint sets). UI renders the same 10 entries twice with two different labels. Filed [#286](https://github.com/100yenadmin/ClawDnD/issues/286).
- **NEW finding** — Astarion ships `abilities: []` + `raceTraits: []` but the UI renders the "Special Abilities" + "Lineage > traits" section headers without gating → empty panes next to the populated Feats panel. Filed [#287](https://github.com/100yenadmin/ClawDnD/issues/287).
- **Generativity proof.** Current location is `Aldenmoor Estate — Study` — a generated canon-grounded location ("Councillor Riven Aldenmoor's Upper City estate" with a fire, Harper signet letter, district relief maps on the wall). Validates the `ClawDnD-NORTH-STAR.md` Part 1B generativity principle end-to-end.
- **Asset re-calibration [#281](https://github.com/100yenadmin/ClawDnD/issues/281) confirmed again.** Astarion's portrait renders from `_private/baldurs-gate/images/portrait_astarion/`. Atlas backdrop renders the Sword Coast map. Wiki-first ingest → engine → `<Img>` pipeline works with real data.

### Loop 3 honest scope notes

- **Live-session UX still invisible.** All 18 saved campaigns have `can_act: false` — no live DM is attached to any save. The DM-attached state (live action bar enabled, live combat unfolding, dice rolling) would require launching a real `startProviderSession`. Out of scope for this artifact-only audit. The UI's two states (read-only vs live) are documented from JSX source-reading; in-flight observation deferred to operator-driven testing.
- **Native macOS app verification still pending.** The title-bar fix (#260) would need a `script/build_and_run.sh` build + window-frame inspection. Out of scope.

### Loop 3 sub-issues (#286–#289)

| Issue | Title | Sev |
|---|---|---|
| [#286](https://github.com/100yenadmin/ClawDnD/issues/286) | `feats` and `classFeatures` carry duplicate content | Major |
| [#287](https://github.com/100yenadmin/ClawDnD/issues/287) | Character: hide empty Special Abilities + Lineage section headers | Minor |
| [#288](https://github.com/100yenadmin/ClawDnD/issues/288) | 1366×768 crowding on Table + Character + Inventory | Major |
| [#289](https://github.com/100yenadmin/ClawDnD/issues/289) | Atlas/Relations fresh-save seed (reframes #261 + #273) | Major |

**Loop 3 confidence: ~90%.** Remaining 10% lives in: live-session UX (operator-launched DM required), native-app verification (build required), exhaustive interaction coverage (palette/a11y/keyboard-shortcut walk-through).

## Loop 4 update (2026-05-29 cont.)

Pushed against the asymptote. Confidence rises **to ~95%.** See [`docs/ui-audit/MAINTAIN.md`](MAINTAIN.md) for the honest "100% on a moving target" framing.

- **Live-session UX (evidence-at-rest).** Inspected `/Volumes/LEXAR/Codex/clawdnd-artproof-state/campaigns/camp_54fd704d985b/snapshot.json` — the saved engine state for the canonical BG campaign. **`combat.active = true`** at rest with full schema: `round=1`, `turn_index=2`, `order=[char_149e22788290 init 13 reaction_used=true, char_3d2a73f8f833 init 7, char_d710006ae7e0 init 6]`, `action_used=true`, `action_attacks_made=1`, `bonus_action_used=false`, `surge_actions=0`, `zones=[]`. **The live-combat state shape is documented.** When a DM provider attaches and the action bar / initiative tracker / battle log render against this state, the UI's `screen-combat.jsx:75-93` consumers (`tokens / initiative / actionBar / zones / battleLog / encounter / commandCenter / economy`) all have observable shapes to validate against. Live-session UX is no longer structurally invisible — only the operator-driven walk-through remains.
- **Native macOS app — title-bar interaction clarified.** Inspected `macos/ClawDnDApp/Sources/ClawDnDApp/Views/RootView.swift:348-398`. The Swift `OpenWorldsChromeHostView` enables `titlebarAppearsTransparent + fullSizeContentView` AND `unhides` all three standard window buttons (close/min/zoom). The `paddingLeft: 76` in `chrome.jsx:422` exists **specifically to clear the macOS native traffic lights**. #260's acceptance criteria need to be **platform-aware**: keep 76px in native, drop/shrink in browser. Filed as a clarification comment on #260.
- **Interactive coverage code-verified.** Keyboard map (`app.jsx:197-220`): 17 shortcuts (t/x/p/m/c/i/f/r/j/b/a/$/w/n/s/,/?). Palette (`styles.css`): 2 alt-palette blocks (`cool`, `dark`) + default warm. A11y (`styles.css:849-892`): `[data-reduced-motion=on]` universal animation-duration zeroing + `[data-contrast=high]` panel/btn/window/parchment chrome overrides + `--ui-scale` via `zoom` on `.window`. Create wizard (`screen-create.jsx:19-27, 133-139, 572-576`): 7 steps + 5e-canonical point-buy `abilityCost = {8:0, 9:1, 10:2, 11:3, 12:4, 13:5, 14:7, 15:9}`. All wiring matches the audit's framework reading.
- **`dist/ClawDnD.app` exists locally.** The native app has been built. Operator can run it for the visual walk that Loop 4 can't do artifact-only.
- **Maintain loop landed.** `qa/ui_audit_health.sh` runs 30 structural checks (viewer reachable, data.js empty, icon-registry baseline, demo-leak grep with JSX-comment-aware regex, surface-route 200s, server.py route literals, responsive + a11y CSS, asset-catalog size ≥ 2000, headless capture pipeline). **All 30 PASS at Loop 4 baseline.** Re-run on every PR touching `viewer/openworlds/`, `viewer/server.py`, `content/worlds/`, or `styles.css`. Doc: [`MAINTAIN.md`](MAINTAIN.md).

**Loop 4 confidence: ~95%.** The remaining ~5% is the asymptote: operator-driven live-DM walk, native-app run, axe-core scan. After those (one operator session, one Swift build, one CLI invocation), confidence → ~99%. The final 1% is the software-is-mutable constant — handled by re-running `qa/ui_audit_health.sh` on every PR.

### Loop 4 deliverables

| Artifact | Purpose |
|---|---|
| `qa/ui_audit_health.sh` | 30-check structural sweep; PASS = audit findings still valid |
| `docs/ui-audit/MAINTAIN.md` | How to keep the audit current; what 100% really means |
| Comment on [#260](https://github.com/100yenadmin/ClawDnD/issues/260) | Platform-aware title-bar fix (keep 76px for macOS traffic lights, drop in browser) |

---

## Scoreboard

| # | Screen | Score | Disposition | Epic | Audit doc | Screenshot |
|---|---|---|---|---|---|---|
| 1 | Relations | **80** | Release-Ready | [#244](https://github.com/100yenadmin/ClawDnD/issues/244) | [relations.md](screens/relations.md) | [png](screenshots/relations-1512.png) |
| 2 | Parley (Dialogue) | **78** | Polish-Pass | [#248](https://github.com/100yenadmin/ClawDnD/issues/248) | [dialogue.md](screens/dialogue.md) | [png](screenshots/dialogue-1512.png) |
| 3 | Atlas (Map) | **72** | Polish-Pass | [#249](https://github.com/100yenadmin/ClawDnD/issues/249) | [map.md](screens/map.md) | [png](screenshots/map-1512.png) |
| 4 | Quest Journal | **72** | Polish-Pass | [#253](https://github.com/100yenadmin/ClawDnD/issues/253) | [journal.md](screens/journal.md) | [png](screenshots/journal-1512.png) |
| 5 | Session (Table) | **70** | Polish-Pass | [#246](https://github.com/100yenadmin/ClawDnD/issues/246) | [table.md](screens/table.md) | [png](screenshots/table-1512.png) |
| 6 | Market (Merchant) | **70** | Polish-Pass | [#256](https://github.com/100yenadmin/ClawDnD/issues/256) | [merchant.md](screens/merchant.md) | [png](screenshots/merchant-1512.png) |
| 7 | Heroes (Character) | **68** | Polish-Pass | [#250](https://github.com/100yenadmin/ClawDnD/issues/250) | [character.md](screens/character.md) | [png](screenshots/character-1512.png) |
| 8 | Setting (Settings) | **68** | Polish-Pass | [#259](https://github.com/100yenadmin/ClawDnD/issues/259) | [settings.md](screens/settings.md) | [png](screenshots/settings-1512.png) |
| 9 | Battle (Combat) | **66** | Polish-Pass | [#247](https://github.com/100yenadmin/ClawDnD/issues/247) | [combat.md](screens/combat.md) | [png](screenshots/combat-1512.png) |
| 10 | Stash (Inventory) | **66** | Polish-Pass | [#251](https://github.com/100yenadmin/ClawDnD/issues/251) | [inventory.md](screens/inventory.md) | [png](screenshots/inventory-1512.png) |
| 11 | Chronicles (Launcher) | **64** | Polish-Pass | [#245](https://github.com/100yenadmin/ClawDnD/issues/245) | [launcher.md](screens/launcher.md) | [png](screenshots/launcher-1512.png) |
| 12 | Acts | **64** | Polish-Pass | [#255](https://github.com/100yenadmin/ClawDnD/issues/255) | [acts.md](screens/acts.md) | [png](screenshots/acts-1512.png) |
| 13 | Forge | **62** | Polish-Pass | [#252](https://github.com/100yenadmin/ClawDnD/issues/252) | [forge.md](screens/forge.md) | [png](screenshots/forge-1512.png) |
| 14 | Creation Plane (Create) | **60** | Polish-Pass | [#257](https://github.com/100yenadmin/ClawDnD/issues/257) | [create.md](screens/create.md) | [png](screenshots/create-1512.png) |
| 15 | Codex (Bestiary) | **56** | Finish-Wave | [#254](https://github.com/100yenadmin/ClawDnD/issues/254) | [bestiary.md](screens/bestiary.md) | [png](screenshots/bestiary-1512.png) |
| 16 | World Seed | **50** | Finish-Wave | [#258](https://github.com/100yenadmin/ClawDnD/issues/258) | [seed.md](screens/seed.md) | [png](screenshots/seed-1512.png) |
| 17* | Camp Sidebar (Loop 2) | **60** | Polish-Pass | (rolls up under #249) | [camp-sidebar.md](screens/camp-sidebar.md) | _capture in Map campMode_ |

**Average: 66.2/100** (across 17 audited surfaces). Bottom-up: 9 screens lift through Polish; 2 through Finish; 1 Release-Ready. \*Camp Sidebar is mounted inside Atlas (campMode) and rolls under #249; standalone audit added in Loop 2.

---

## Cross-cutting sub-issues filed (#260–#279)

> These are the leverage points. Closing the cross-cutting items lifts multiple screens at once.

| Issue | Title | Sev | Epics | Milestone |
|---|---|---|---|---|
| [#260](https://github.com/100yenadmin/ClawDnD/issues/260) | Title-bar text overlaps nav-rail on every screen | Critical | per-page-polish | Polish Wave |
| [#261](https://github.com/100yenadmin/ClawDnD/issues/261) | Atlas — seed the BG nav graph (Lower City / Upper City / …) | Critical | atlas | Polish Wave |
| [#262](https://github.com/100yenadmin/ClawDnD/issues/262) | Bestiary 'THE MARCHES' → Sword Coast (demo-leak) | Critical | demo-leak | Finish Wave |
| [#263](https://github.com/100yenadmin/ClawDnD/issues/263) | Bestiary intel-tier stat block or hide-when-blank | Critical | per-page-polish + wire-prototypes | Finish Wave |
| [#264](https://github.com/100yenadmin/ClawDnD/issues/264) | Forge Workshop Ledger demo leak (the scribe / the smith / a companion) | Critical | demo-leak | Polish Wave |
| [#265](https://github.com/100yenadmin/ClawDnD/issues/265) | Creation Plane race + class + portrait gallery art | Critical | portraits + per-scene-art | Polish Wave |
| [#266](https://github.com/100yenadmin/ClawDnD/issues/266) | World Seed write-lane decision + de-fake hardcoded values | Critical | wire-prototypes + demo-leak | Finish Wave |
| [#267](https://github.com/100yenadmin/ClawDnD/issues/267) | Combat foe portraits + battle scene backdrop | Critical | portraits + per-scene-art | Polish Wave |
| [#268](https://github.com/100yenadmin/ClawDnD/issues/268) | Character Spellbook browse path | Critical | per-page-polish | Polish Wave |
| [#269](https://github.com/100yenadmin/ClawDnD/issues/269) | Merchant id mismatch (`gate-sundries` vs `talli`) | Critical | per-page-polish | Polish Wave |
| [#270](https://github.com/100yenadmin/ClawDnD/issues/270) | Img-not-Placeholder sweep across 8 screens | Major | portraits + per-scene-art | Polish Wave |
| [#271](https://github.com/100yenadmin/ClawDnD/issues/271) | Inventory paper-doll + full slot set | Major | per-page-polish | Polish Wave |
| [#272](https://github.com/100yenadmin/ClawDnD/issues/272) | Inventory compare-on-hover | Major | per-page-polish | Polish Wave |
| [#273](https://github.com/100yenadmin/ClawDnD/issues/273) | Relations canon BG factions seed | Major | per-page-polish | Polish Wave |
| [#274](https://github.com/100yenadmin/ClawDnD/issues/274) | Session log time-merge sort | Major | per-page-polish | Polish Wave |
| [#275](https://github.com/100yenadmin/ClawDnD/issues/275) | Atlas watermark dim | Major | per-page-polish | Polish Wave |
| [#276](https://github.com/100yenadmin/ClawDnD/issues/276) | Parley free-form text input | Major | per-page-polish + wire-prototypes | Polish Wave |
| [#277](https://github.com/100yenadmin/ClawDnD/issues/277) | Create Family + Biography wiring | Major | wire-prototypes | Polish Wave |
| [#278](https://github.com/100yenadmin/ClawDnD/issues/278) | Settings Export chronicle wire | Major | wire-prototypes | Polish Wave |
| [#279](https://github.com/100yenadmin/ClawDnD/issues/279) | Iconography: OpenWorldsIcon registry usage across screens | Major | per-page-polish + iconography | Polish Wave |
| [#281](https://github.com/100yenadmin/ClawDnD/issues/281) | **Asset catalog re-calibration: art exists for nearly every gap** | Major | portraits + per-scene-art | Polish Wave |
| [#282](https://github.com/100yenadmin/ClawDnD/issues/282) | Camp Sidebar: 'Begin Resting' CTA needs engine write-lane | Critical | wire-prototypes | Polish Wave |
| [#283](https://github.com/100yenadmin/ClawDnD/issues/283) | Camp Sidebar: per-companion fireside TALK_PROMPTS | Critical | per-page-polish + wire-prototypes | Polish Wave |
| [#284](https://github.com/100yenadmin/ClawDnD/issues/284) | Responsive: .stack-on-narrow defined but unused; screens crowd <1200px | Major | per-page-polish | Polish Wave |

---

## Recommended work order (highest leverage first)

> **Owner steer + design-led items first.** Then sweep cross-cutting items. Then per-screen.

### Wave 0 — owner / design decisions (1–2 days)

1. **#266 — World Seed write-lane DECISION**: keep as view-only OR build a `/seed-surface` write lane. The audit doc proposes both paths; the lower-effort path is "view-only". Pick before any S- work starts.
2. **#263 — Bestiary intel-tier OR hide-when-blank**: same pick-one shape — Approach A or B in the doc.
3. **#265 — portrait pipeline approach**: gateway-gen for created PCs, OR default class/race art, OR both? (Audit doc lays out trade-offs.) This drops out of EPIC A in #242.
4. **ST-02 — which Settings preview controls ship by 1.0?** See `docs/ui-audit/screens/settings.md`.

### Wave 1 — cross-cutting sweeps (highest leverage)

5. **#260 (title-bar overlap)** — single change lifts every screen.
6. **#270 (Img-not-Placeholder sweep)** — 8 screens lift at once. **Per Loop-2 #281: art ALREADY EXISTS** for every spot in the table — pure UI work, no ingest blocking.
7. **#261 (BG nav graph seed)** — Atlas elevates from 72 → 80+.
8. **#273 (BG factions seed)** — Relations stays at 80+ with full content. **Per #281: all 6 BG faction sigils already ingested.**
9. **#284 (apply .stack-on-narrow or refactor responsive)** — lifts C9 scores on 8+ screens at the < 1200px breakpoint.

### Wave 2 — per-screen criticals

9. **#262 + #263 (Bestiary)** — lifts 56 → 75+.
10. **#266 (Seed)** — lifts 50 → 70+ on the view-only path.
11. **#265 (Create races/classes/portraits)** — lifts 60 → 80+.
12. **#267 (Combat foes + backdrop)** — lifts 66 → 80+.
13. **#268 (Spellbook browse)** — lifts Character toward Release-Ready.
14. **#264 (Forge ledger demo-leak)** + **#269 (Merchant id mismatch)** — quick wins, finish EPIC E.

### Wave 3 — per-screen majors

15. **#271 + #272 (Inventory paper-doll + compare)** — major BG3-parity lift.
16. **#274 (log sort)** — felt fix on Session.
17. **#275 (watermark)** + **#276 (Parley free-form)** + **#277 (Create wiring)** + **#278 (Export)** + **#279 (iconography)** — polish sweep.

### Wave 4 — per-screen minors + trivials

18. Implementation agent files individual tickets per per-screen audit doc Minor/Trivial rows as work is picked up. The audit docs hold the source of truth — don't pre-file 60+ Minor tickets.

### Wave 5 — Camp (Loop 2 lane)

19. **#282 (Begin Resting wire)** — the single most felt missing wire on Atlas; unlocks the whole camp sidebar.
20. **#283 (per-companion fireside)** — content-first; pairs with #58 Owlcat-style companion campaigns.
21. Remaining CS-* findings in `screens/camp-sidebar.md` filed as Wave-3-style polish.

---

## Milestones

| Milestone | # | Status | Issues |
|---|---|---|---|
| Graphics Release 1.0 — Blocker Wave | [#8](https://github.com/100yenadmin/ClawDnD/milestone/8) | Reserved (empty today) | — |
| Graphics Release 1.0 — Finish Wave | [#9](https://github.com/100yenadmin/ClawDnD/milestone/9) | Open | Bestiary (#254 #262 #263), Seed (#258 #266) |
| Graphics Release 1.0 — Polish Wave | [#10](https://github.com/100yenadmin/ClawDnD/milestone/10) | Open | 14 epics + 16 cross-cutting subs |
| Graphics Release 1.0 — Backlog / Post-1.0 | [#11](https://github.com/100yenadmin/ClawDnD/milestone/11) | Open | Trivials + missing features |

---

## Labels added (taxonomy)

- `ui-audit` — every issue from this audit cycle.
- `severity:critical` / `severity:major` / `severity:minor` / `severity:trivial` — per the rubric.
- `epic:portraits` (A) / `epic:atlas` (B) / `epic:playable` (C) / `epic:per-scene-art` (D) / `epic:demo-leak` (E) / `epic:wire-prototypes` (F) / `epic:per-page-polish` (G).
- `screen:<id>` × 16 (launcher / table / combat / dialogue / map / character / inventory / forge / relations / journal / bestiary / acts / merchant / create / seed / settings).
- `asset-gap` — needs wiki ingest or `_private` art.
- `accessibility` — a11y findings (often paired with `severity:minor`).
- `iconography` — `OpenWorldsIcon` registry usage.
- `copy` — on-screen text.

---

## How an implementation agent picks up work

1. **Read this file + `SCORING_RUBRIC.md` + the target screen's audit doc.** That's 5 minutes of orientation.
2. **Pick a row from the per-screen audit's "Findings" table.** Each row carries severity / file:line / epic tag / acceptance criteria.
3. **Search GitHub** for an existing issue (`<screen> <row-id>` like `launcher L-04`). File if missing.
4. **Implement.** Headless verify via `qa/owshot.sh <screen> docs/ui-audit/screenshots/<screen>-<viewport>.png 8799` (fresh Chrome profile per port — no cache). Visually verify in the macOS app via `script/build_and_run.sh`.
5. **Update the per-screen audit doc**: strike through the finding row OR add a "✅ shipped <commit-sha>" suffix.
6. **Re-score the screen** when 3+ findings of any severity have landed; bump the score table at the top of the doc.

---

## Files in this audit

```
docs/ui-audit/
├── MASTER_TRACKER.md        ← you are here
├── SCORING_RUBRIC.md         ← 10-criterion rubric + severity + per-screen template
├── RPG_REFERENCE_PATTERNS.md ← P1–P16 patterns from Pathfinder/Kingmaker/BG3/DNDBeyond
├── screens/
│   ├── launcher.md  table.md     combat.md     dialogue.md
│   ├── map.md       character.md inventory.md  forge.md
│   ├── relations.md journal.md   bestiary.md   acts.md
│   └── merchant.md  create.md    seed.md       settings.md
├── screenshots/    ← gitignored — see screenshots/README.md for how to regenerate
│   └── README.md   (the only file in this dir that's committed)
└── code-map/        ← reserved for per-screen code-anchor notes (empty today)
```

> **Why screenshots aren't committed.** The audit screenshots reproduce rendered
> output from `content/worlds/_private/` (BG3 portraits, Sword Coast map, BG city
> scene art). Per `docs/OPENWORLDS_DESIGN_ASSET_POLICY.md` and the existing
> `.gitignore` rule for `_private/`, that © content stays local. Derivative
> screenshots that embed it follow the same rule. The per-screen audit docs reference
> `docs/ui-audit/screenshots/<screen>-1512.png` paths — those resolve locally after
> a one-line regen loop (see `screenshots/README.md`).

---

## Session reference

This audit was produced 2026-05-29 in one session as part of issue [#242](https://github.com/100yenadmin/ClawDnD/issues/242) Phase 5. Session notes live at `/Volumes/LEXAR/Codex/session-notes/2026-05-29/openworlds-ui-ux-review/implementation-notes.html` (per the user-level guidance to keep a per-session scratchpad for substantial / state-changing sessions).

The audit was authored against the canonical checkout `/Users/lume/ClawDnD-val` (per `clawdnd-canonical-setup` memory), git rev `227ff3227010820b2df2e5692dc77505ef7f980e`. The viewer was served via both `/Volumes/LEXAR/repos/ClawDnD-val` (port 8799, content-identical mirror) and `/Users/lume/ClawDnD-val` (port 8795); both were verified at the same revision.

The audit does NOT touch product code — see the goal directive. The implementation agent is the appropriate owner for any code changes; this audit hands them everything they need to start.
