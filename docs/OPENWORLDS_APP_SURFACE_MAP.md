# OpenWorlds App Surface Map

Date: 2026-05-26

This document is the app-side handoff map for the OpenWorlds macOS shell. It
describes what is wired today, what is display-only, and which GitHub issue owns
the next implementation. The native app remains a supervisor and read/action
surface; the engine/viewer remain the game-state authority.

## Capability Legend

- **Wired**: usable against current viewer/native bridge APIs.
- **Read-only**: backed by real data, but no mutation path yet.
- **Partial**: some real backing exists, but the screen still contains local or
  prototype-only behavior.
- **Display-only**: retained for design fidelity and roadmap shape, not backed
  by ClawDnD data yet.
- **Engine-needed**: needs a new engine/viewer read model or engine-owned action.
- **Bridge-needed**: needs native supervisor bridge work.

## Product Shell

| Area | Current state | Evidence | Owner issue | Next action |
| --- | --- | --- | --- | --- |
| Native app host | Wired | `RootView` launches viewer and hosts `/openworlds/` in `WKWebView` | #82, #113 | Keep as product shell; leave old SwiftUI shell as debug only |
| Bundled OpenWorlds assets | Wired | App packages `viewer/openworlds` and sets `CLAWDND_OPENWORLDS_DIR` | #134 | Keep UI assets app-bundled; engine/viewer stay repo-backed |
| Sparkle local beta | Partial | `UpdaterService`, `checkForUpdates`, local appcast | #134 | Manual update smoke; notarization remains separate |
| Window controls | Partial | OpenWorlds traffic buttons call `windowCommand`; AppKit drag strip exists | #136 | Manual close/minimize/zoom/drag smoke; tune drag hit box if needed |
| Native settings/provider/log bridge | Partial | Settings screen calls `appStatus`, provider actions, diagnostics, Sparkle | #132, #133 | Move remaining Swift debug-only controls into OpenWorlds or mark unavailable |

## OpenWorlds Screens

| Screen | Current state | Backing today | Owner issue | Handoff notes |
| --- | --- | --- | --- | --- |
| Chronicles / launcher | Partial | `/openworlds/campaigns.json`; local selected campaign state | #114 | Resume/view navigates to Table. New chronicle modal currently creates local-only demo rows and must be replaced with native/provider start flow before treating it as real. |
| Session / table | Wired/partial | `/session-surface`; enabled actions post `/move` | #115 | Best real gameplay surface today. Continue enriching read model fields rather than writing local state. |
| Battle / combat | Wired/partial | `/combat-surface`; action bar posts `/move` | #116 | Tactical board is engine-owned projection. Improve legality, event cards, targeting, and refresh behavior through viewer read models. |
| Atlas / map | Wired/partial | `/atlas-surface`; available travel posts `/move` | #117 | Strategic map is partially real. Camp/rest UI is still mostly informational until rest/camp actions are engine-owned. |
| Settings / native app | Partial | Native bridge app status, dependencies, providers, Sparkle | #132, #134 | `ClawDnD` section is real. Audio/display/gameplay/controls/accessibility/saves are display-only until preferences are bridged. |
| Quest journal | Display-only/read-model-needed | `state.quests` demo/store fallback only | #120 or new journal projection issue | Should consume player-known quest projection from viewer, not static seed data. |
| Character / heroes | Display-only/engine-needed | `state.party` only | #80/#81 if build planner, or new character projection issue | Needs character sheet projection, level-up/build planner actions, rest-prep through engine. |
| Inventory / stash | Display-only/engine-needed | `state.stash` only; equip/use/drop are local toasts | #119 | Replace local interactions with engine-owned item action model. |
| Merchant / market | Display-only/engine-needed | static `MERCHANTS`, local cart/coins | #119 | Needs economy/merchant read models and buy/sell/haggle player-intent actions. |
| Forge | Display-only/engine-needed | static recipe list and local random roll | #119 | Must not keep local crafting resolution; needs engine-owned craft preview/action. |
| Relations / camp | Display-only/engine-needed | static factions/NPCs | #118 | Needs companion/camp/faction/NPC disposition projection and player-known filtering. |
| Bestiary / codex | Display-only/engine-needed | static `BESTIARY`, `PEOPLE`, `LORE` | #121 | Must consume player-known lore only; never expose hidden/private lore. |
| Acts | Display-only/engine-needed | static `ACTS` | #120 | Needs campaign-director projection and player-visible chronology/payoff markers. |
| Dialogue / parley | Display-only/provider-needed | static `DIALOGUE`; local choice tree | #118 or provider dialogue issue | Needs provider/engine dialogue read model; choices should post player intent, not mutate local branch only. |
| Creation Plane | Display-only/engine-needed | local character-creation wizard | #79/#81 or new onboarding issue | Can remain design reference until character creation/start-game contract exists. |
| World Seed | Display-only/engine-needed | local seed settings and toasts | #114/#79 | Needs read-only campaign seed projection first; destructive reseed must be unavailable until engine-owned. |

## Highest-Value App Follow-Ups

1. **Finish manual beta smoke for PR #150**: verify OpenWorlds loads inside the
   packaged app, Sparkle reads the local appcast, traffic buttons control the
   real native window, and the drag strip behaves well.
2. **Replace local-only launcher creation**: `NewCampaignModal` must call the
   native/provider start flow or be marked `Display-only`.
3. **Bridge Settings preferences**: persist real native prefs for repo path,
   port, state dir, provider commands, budgets, voice backend, and update state.
4. **Complete Session/Combat/Atlas before new surfaces**: these already have
   viewer read models and `/move` lanes, so they compound fastest.
5. **Keep all inventory/crafting/merchant actions disabled until engine-owned**:
   the current UI is visually useful but must not resolve item/economy state in
   browser-local code.
6. **Add notarization as a separate release-trust issue**: current beta is
   Developer ID signed but intentionally not notarized.

## Non-Negotiable Boundary

The macOS app and OpenWorlds browser code may supervise, display, and submit
player intent. They must not directly write `snapshot.json`, `play-state`,
`qa/state`, inventory, quests, XP, clocks, companion state, or private lore.

