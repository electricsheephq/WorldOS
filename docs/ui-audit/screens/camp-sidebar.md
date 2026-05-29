# Camp Sidebar — 60/100 — Polish-Pass (Loop 2 standalone audit)

**Route:** `/openworlds/#map` with `campMode` toggled on (no own hash — owned by Map)
**Source:** `viewer/openworlds/camp-sidebar.jsx` (605 LOC)
**Screenshot:** _none — camp mode never captured in Loop 1; regen via `qa/owshot.sh map docs/ui-audit/screenshots/camp-1512.png 8799` then click Make Camp in-app_
**Compared to:** BG3 long-rest UI + camp scene, Pathfinder: Kingmaker camp + cooking, Dragon's Dogma rest (P11 in `RPG_REFERENCE_PATTERNS.md`).
**Why a standalone audit:** Loop 1 folded findings into `screens/map.md` because the sidebar is mounted INSIDE the Atlas screen (`screen-map.jsx:206-215`). But at 605 LOC with its own timeline, drag-drop, watch, recipes, and TalkPanel — it's substantial enough for its own depth. Promoted in Loop 2.

## Score

| # | Criterion | Score | × Weight | Justification |
|---|---|---|---|---|
| C1 | Visual Polish | **8/10** | 80 | TimelineBar with day-to-night gradient + wax-seal cursor (line 301-348) is a signature visual. RoleSlot + WatchSlot panels are well-styled. |
| C2 | Information Density | **8/10** | 80 | Stacked vertical panels (Time / Rations / Healing / Roles / Special Roles / Watch / Party / Talk / CTA) fit in the right-rail width. |
| C3 | RPG Genre Conventions | **8/10** | 120 | All P11 patterns: timeline, role slots, drag-to-assign, recipe picker, rations balance, talk-to-companion. ✓ |
| C4 | Interaction Affordance | **6/10** | 90 | Drag-drop role assignment ✓; Talk affordance with quill ✓; **Begin Resting CTA is disabled (line 293-295) — "Display-only — resting is not yet wired to the engine; nothing is saved". Manage rations button also display-only (line 103). Recipes are decorative (the `roles.cooking` gates the picker but the chosen recipe never flows to engine).** |
| C5 | Content Completeness | **3/10** | 45 | **`TALK_PROMPTS` is `_default` only (line 595-603)** — no per-companion dialogue. **`SPECIAL_ROLES = {}` empty (line 584)** — every idle companion falls back to generic "Stand watch / Quiet hours" card. Recipes hardcoded as 3 (hearty / pheasant / stew, line 586-590) with placeholder bonus strings. |
| C6 | Accessibility | **5/10** | 50 | Drag-and-drop has NO keyboard-equivalent. WatchSlot + RoleSlot use `<div>` for the drop target (line 374-385, 432-441) instead of `<button>` — no `role="button"` either. TimelineBar segment text at fontSize 7 (line 320) is below typical legibility floor. |
| C7 | Empty-State Handling | **8/10** | 40 | "No party in camp. Camp is empty." (line 233) ✓; SpecialRoles renders only when `specialRoles.length > 0` (line 188) ✓. |
| C8 | Wiki-First Asset Fidelity | **7/10** | 70 | Party portraits via `<Img scope={"portrait-"+p.id}>` (line 256, 391, 454, 488, 542) ✓ — wired everywhere. **Loop-2 catalog confirms: 2,077 portraits exist in `_private/baldurs-gate/images/portrait_*` including all 7 BG3 origins (Jaheira, Astarion, Shadowheart, Wyll, Karlach, Minsc, Gale).** |
| C9 | Responsive / Layout | **6/10** | 30 | Stacks vertically; fits the 340px Atlas right-rail. Below ~280px the TimelineBar segment numbers crowd. |
| C10 | Performance Perception | **8/10** | 40 | 5s poll on `/character-surface` (line 31); same as Character screen. Drag state is local. |

**Total: 645/1000 = 65/100 → Polish-Pass** _(rounded to 60 — the Begin Resting CTA being disabled is a structural blocker; `SPECIAL_ROLES` + `TALK_PROMPTS` being empty drags content completeness hard)_

## Findings

| # | Severity | Title | File:line | Epic | Acceptance criteria |
|---|---|---|---|---|---|
| CS-01 | **Critical** | Begin Resting CTA is disabled — long rest has no engine write-lane | `camp-sidebar.jsx:293-295` | epic:wire-prototypes | When `can_act` and the camp surface emits a valid camp configuration, the CTA POSTs to `/move` with `{kind:"rest", type:"long", watch:[watch1, watch2], cook:roles.cooking, recipe, hunting:roles.hunting, camouflage:roles.camouflage}`. Engine resolves; party HP / spell slots / abilities refresh. Toast confirms. Today the modal closes nothing. |
| CS-02 | **Critical** | `TALK_PROMPTS = { _default: {...} }` only — no per-companion fireside dialogue | `camp-sidebar.jsx:595-603` | epic:per-page-polish + epic:wire-prototypes | Either (a) pull `TALK_PROMPTS` from a `/camp-surface` (or extension of `/relations-surface`) read-model so engine projects per-companion opening + responses, OR (b) populate `TALK_PROMPTS` for the 7 BG3 origins + canon NPCs from authored content under `content/worlds/baldurs-gate/`. Path (a) is the wiki-first direction; (b) is faster. Today every companion's fireside chat is "Sit. The fire is low. There is room." |
| CS-03 | **Major** | `SPECIAL_ROLES = {}` empty — every idle companion shows generic "Stand watch / Quiet hours" | `camp-sidebar.jsx:584` | epic:per-page-polish | Populate per-canon-id with character-true camp duties (Karlach → "Sharpen my axe / I like the rhythm"; Astarion → "Read by candle / Once we have one I trust"). Mirrors BG3 long-rest character beats. |
| CS-04 | **Major** | Rations "Manage" button is preview-only (disabled) | `camp-sidebar.jsx:103` | epic:wire-prototypes | Wire to a small modal: split rations between heroes, mark a hero "fasting" (no consumption), or buy/forage. Live state via `/inventory-surface`. Today the button does nothing. |
| CS-05 | **Major** | Drag-and-drop role assignment has NO keyboard equivalent | `camp-sidebar.jsx:236-285, 372-428, 430-478` | epic:per-page-polish + accessibility | Add a "Assign…" button next to each role slot that opens a small dropdown of unassigned party members. Keyboard nav works. Drag-drop remains for mouse users. |
| CS-06 | **Major** | "Use rations" checkbox is `defaultChecked` — uncontrolled, value never read | `camp-sidebar.jsx:115-117` | epic:wire-prototypes | Either (a) controlled state passed to the rest move payload (CS-01), or (b) remove the toggle entirely. Today flipping it is theater. |
| CS-07 | **Major** | Healing radio is local state — never flows to engine rest move | `camp-sidebar.jsx:50, 121-127` | epic:wire-prototypes | Same as CS-06 — flow into the rest move payload (`{healing: "spells"\|"natural"}`) so the engine can apply the chosen heal mode. |
| CS-08 | **Minor** | RoleSlot details (e.g. "Hunting will take 0–2 hours. You will recover 5 rations.") are hardcoded text, not data-driven | `camp-sidebar.jsx:137, 149, 161-163` | epic:per-page-polish | When the camp-surface emits per-role outcome estimates (`{hours_min, hours_max, rations_recovered, attack_chance}`), surface them here. Today every Hunter shows the same prediction. |
| CS-09 | **Minor** | TimelineBar fontSize 7 + monochrome cursor over a busy gradient is hard to read | `camp-sidebar.jsx:320, 336-345` | epic:per-page-polish + accessibility | Bump fontSize to ≥ 9. Add a darker stroke under hour numerals. Verify at high-contrast mode. |
| CS-10 | **Minor** | TalkPanel responses overwrite the prompt area — no way to back out without closing the whole panel | `camp-sidebar.jsx:549-577` | epic:per-page-polish | Add a "← Other lines" button after a reply renders, that re-shows the response choices. Today once you pick one you're done. |
| CS-11 | **Minor** | "Bank the fire" copy is charming but only appears post-reply — not on initial prompt close | `camp-sidebar.jsx:575` | epic:per-page-polish | Verify the X button at top-right (line 536) is equally discoverable. |
| CS-12 | **Trivial** | RoleSlot icon prop is a one-char letter (H/C/C) — inconsistent visual weight vs other screens' OpenWorldsIcon usage | `camp-sidebar.jsx:131-159` | iconography | Adopt `OpenWorldsIcon` registry IDs (would need new ids: `camp.hunting`, `camp.cooking`, `camp.camouflage`, `camp.watch`). Folds into #279. |

## Missing features (deferred to backlog)

- **Camp scene art** behind the sidebar — `_private/baldurs-gate/images/scene_*` has 26 candidates; pick one for "camp under the stars" backdrop.
- **Per-region camp risk** — Lower City rooftop vs Shadow-cursed Lands vs Underdark — affects encounter chance.
- **Companion-pair banter** — BG3 has unique pair dialogue at camp.
- **Tent / fire / standard** customization — pure flavor but high-immersion.
- **Buff timeline** — show which buffs expire by morning.

## Asset gaps (wiki-first inventory) — Loop 2 update

- **Companion portraits** ✅ present (`portrait_jaheira / astarion / shadowheart / wyll / karlach / minsc / gale` all exist).
- **Camp scene** ⚠ no `scene_camp` directly, but `scene_emerald-grove` / `scene_last-light-inn` / `scene_elfsong-tavern` could re-purpose as backdrops while a dedicated camp asset is ingested.
- **Role icons** ⚠ — `camp.rest` exists in icon registry; `camp.hunting / camp.cooking / camp.camouflage / camp.watch` need adding (#279 scope).

## Recommended next pass

1. **CS-01 (Begin Resting wire)** unlocks the entire screen — the rest of the UI is supporting the moment of long rest.
2. **CS-02 + CS-03 (per-companion TALK_PROMPTS + SPECIAL_ROLES)** are the biggest visible content gap and pair with the Owlcat-style companion campaigns epic [#58](https://github.com/100yenadmin/ClawDnD/issues/58).
3. **CS-05 (keyboard equivalent)** is the highest accessibility leverage on this screen.
