# Changelog

All notable changes to WorldOS (formerly ClawDnD) are documented here.
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.
WorldOS is source-available commercial software; world seeds are licensed separately
(see `LICENSE`, `ROYALTY-ADDENDUM.md`, `COMMERCIAL-LICENSE.md`, and
`content/worlds/README.md`).

---

## [Unreleased]

- Gameplay toward the RRI bar (story ≥4.3, mechanical ≥4.5) — the GA work, now built on the
  honest, un-gamed measurement that 1.0.5-rc1 established.

---

## [1.0.5-rc1] — 2026-06-19

**System-hardening RC — NOT a GA.** The RRI gameplay gates (story ≥4.3, mechanical ≥4.5,
5-persona satisfaction, …) are not yet met; this RC hardens the *measurement* and the *model
architecture* so the gameplay work that follows is built on honest, un-gamed signal.

### Honest measurement — the behavioral gate stops false-capping good play
- Every-beat tools (`record_decision` etc.) coerce string/comma-string list args at the Pydantic
  layer, so a model passing `approval_tags="x"` is coerced, not rejected — killing the
  model-agnostic `no_rejected_tool_calls` RED-cap (#1027).
- `party_traveled` is now WARN below 8 beats (a deep single-scene vignette is not a stuck DM),
  FATAL only for substantial runs; `combat_not_left_active` distinguishes a truncated/resumed
  fight (WARN) from a genuine abandon (FATAL) (#1030). Adversarially verified — no integrity gate
  weakened. The ~30% "GLM cap rate" was these self-inflicted false-caps (they capped Claude too);
  a same-SHA 1-v-1 on the fixed engine now shows **0/5 RED**.

### GLM as a cheap batch-QA engine — clean model-switching
- One model choice flows coherently: GLM is a no-op for Claude; Claude-mode defensively unsets
  any stray GLM env (switch-back is always clean, no leak); a mixed-model guard; product play
  forced clean-Claude; the scorer always isolated-Claude (#1026/#1028). Measured: GLM 5.2 is
  **comparable quality** (within ~0.2 of Claude — higher on mechanical/angry, ~0.2 lower on
  story); its true cost is **latency** (3–4× cold-opens) → cheap overnight/VM sweeps, never the
  release gate (Claude stays the quality bar).

### Timing instrumentation + iteration tooling
- Per-tool-call + per-kind (combat/social/cold-open) timing → sidecar → `latency_rollup` →
  `scores.db` columns + the `story_readout` TIMING stamp (#1006/#1007/#1016/#1020). Beats are
  decode-bound; tool-exec is ~1–4%.
- `qa/run_arc_smoke.sh` — companion-arc iteration smoke (asserts approval moved / the arc engaged)
  (#1029).
- CodeQL advanced setup: the Swift autobuild scoped to `macos/**` + weekly (#1015).

### Felt-world machinery (story / relationship / acts — live-tested)
- Story-engagement feedback loop (auto-seeded approval vocabulary, feature-engagement coverage
  scorer, companion-quest orphan cue) (#1017–#1024); acts-engine runtime + felt-shape scorer
  (#1001/#1002); weighted approval + diminishing returns + inter-companion stance (#1003–#1005).
- Docs current: MODEL-TIERING (the GLM lane + the honest 1-v-1 numbers), SCORING (gate severity as
  honest measurement, *not* score-gaming; the coercion contract; timing columns), the runbooks (#1031).

- Licensing: WorldOS Source-Available Commercial EULA v1.0 + `ROYALTY-ADDENDUM.md`,
  `COMMERCIAL-LICENSE.md`, `CLA.md`, `CONTRIBUTING.md`, a PR CLA template. Prior MIT grants for
  older copies remain preserved in `LICENSE`.

---

## [1.0.4-rc5] — 2026-06-17

**The living relationship engine — companions, camp, and quests that actually engage, proven
end-to-end on two golden spines.** A read-the-played-story pass found that the craft was
BG3-caliber but the engine's companion/quest machinery *never engaged in real play* — companions
were narrated, not gauged; quests resolved in prose but never evolved; and nothing scored that as
failure, so it looked healthy. rc5 closes that loop and proves it live.

- **The window — a readable adventure + a structural-coverage stamp (#956, #958).** An
  authored-adventure run mode (`CLAWDND_ADVENTURE_ID` → `start_adventure`) + `qa/story_readout.py`
  renders any playtest as the story a human reads (DM prose + rolls + companion/combat moments)
  plus a one-line coverage stamp (recruit? camp? approval-moved? quest-resolved+evolved?). The
  harness emitted `score.json`, never the story; now you can see it.
- **The relationship-engine cues (#961).** `persist_beat` returns an **obligations digest every
  beat** — a frozen companion ("regard hasn't moved — tag a values-choice or camp"), an overdue
  camp, a resolvable/stalled quest, a quest with no echo — folding the obligation into the tool the
  DM calls every beat (the proven "surfacing isn't using; fold it into the every-beat trigger"
  lesson). `scene_context.durable` mirrors it; a SKILL step-6b mandates acting on it. Plus a FATAL
  `structural_completeness` gate that caps a substantial system-skipping run.
- **The full-circle scorecard (#963).** The persona scorecards + sweep + ledger now **track and
  show** acts / recruit / camp / approval / quest-resolve+evolve, computed from engine ground
  truth — so a system-skipping run can no longer look healthy to an implementation agent. Penalty
  (the gate) + tracking, sharing one coverage helper so they can't drift.
- **Authored relationship content (#962, #964).** Brother Toll (Embergloom) and **a whole new
  Baldur's Gate golden spine, "The Ledger of Mercy"** (a debt-bondage almonry, a true-believer
  antagonist, Sergeant Ondine Marsh) got full `companion_dossier` approval vocabularies and
  multi-gate `CompanionArc`s — including a real **betrayal fork** on Ondine. The gauge the
  machinery moves. Both spines validate clean; the engine maps it via `Character(**data)`.
- **Harness robustness (#965).** `run_duo` survives transient API 500s (classified retry +
  backoff; a 500 had killed an overnight run); the readout approval stamp counts the `persist_beat`
  path; a `camp_scene_skipped` cue (the DM rested but skipped the camp conversation).
- **PROVEN end-to-end.** Two full-depth 24-beat validations: companion approval **climbed**
  (Toll 0→47, Ondine 0→80), **arc gates unlocked**, camp ran, the **companion's personal quest
  and the main quest both resolved + evolved**, the acts progressed to a real climax, and the
  `structural_completeness` gate **PASSED** — with **story-craft 4.8** on both (the bar is 4.3),
  the highest yet. Every system that was dead (all `·`) in every prior run is alive.

Additive throughout; the engine remains the sole writer. Pre-release.

---

## [1.0.4-rc4] — 2026-06-17

**Felt quality + measurement fidelity.** rc4 lands the min-maxer planning layer, story-craft
directives, and a major QA-harness reliability pass that gives the measurement gates fidelity —
so future quality changes can be validated honestly.

- **The planning/theorycraft layer (#951)** — closing the build-optimizer persona's gaps, all
  wire-up of existing engine machinery (the features were ~90% built, just not reachable):
  - **Half-caster slot trust** — the Spells tab now shows the caster tier + a derived
    progression note ("Half-caster — 4th-level slots unlock at L13", computed from the engine's
    own SRD table). An SRD-correct L10 Paladin's 4/3/2 slots are no longer misread as "missing".
  - **Market priced-first** — the merchant catalogue surfaces priced, buyable gear ahead of the
    priceless magic-item long tail (it was alphabetical → looked empty).
  - **Camp spell-prep** — resting at camp now surfaces the existing prepare-spells modal per
    prepared caster (it was only reachable from the character sheet).
  - **Feat browser** — a new read-only `feats()` engine tool + a browsable feat picker replaces
    the blind free-text feat box in the level-up flow.
- **Story-craft directives (#960)** — three craft moves the scorer kept naming on otherwise-
  excellent sessions: a running clock must be *felt* (not just named); never re-narrate the
  player's own action back; planted texture must recur and pay off.
- **QA-harness reliability (#966) — gate fidelity + #623 recovery.** Adversarially verified (a
  gate-weakening was caught and closed before merge):
  - A persona whose **player process crashes** is retried, then classified harness-inconclusive
    (re-measure) — no longer laundered into a product-quality failure.
  - **`party_traveled`** no longer RED-caps a *complete single-scene drama* (clock advanced AND a
    quest resolved AND ≥8 beats) like a frozen stall — while a genuine stall stays RED.
  - The **Mac-handoff** native-mint deadline now derives from (and outlasts) the DM cold-open's
    own timeout, with a liveness-bounded grace — fixing flaky handoff legs.
  - **#623**: an empty pre-beat mark on a continuing beat no longer fails-open and stamps
    recycled prose "genuine"; a deadline-killed routine beat no longer escalates the retry into a
    ~15-minute hang.

---

## [1.0.4-rc3] — 2026-06-16

**The autonomy unblock — popup P0 fixes + an honest Beta baseline.** rc3 removes the macOS
prompts that blocked every shell-launched GUI run (and any future user's first launch), then
captures a clean same-SHA 5-persona measurement to steer the road to Beta.

- **No more keychain/codesign prompt on every build (#945).** `script/build_and_run.sh` now
  ad-hoc signs by default and never auto-searches the keychain for a signing identity. A bare
  `security find-identity` scanned *every* keychain in the macOS search list — including an
  unrelated product's signing keychain on a removable volume — and fired a keychain-password
  *and* removable-volume prompt on each build. Stable-cdhash signing is now opt-in via
  `WORLDOS_SIGN_IDENTITY`.
- **No more removable-volume prompt + viewer-not-ready on shell launch (#946).** The `.app`
  merged its full inherited environment into every child it spawns, so a foreign env var pointing
  at a removable volume (`GBRAIN_SKILLS_DIR=/Volumes/…`, exported by `~/.zshenv` and inherited via
  `open -n`) made the viewer/provider enumerate the volume → a modal TCC prompt that can't be
  answered headlessly and stalled viewer startup ("Viewer did not become ready"). One root cause,
  both symptoms. `EnvironmentBootstrap.withoutRemovableVolumeLeaks()` now strips `/Volumes`-prefixed
  inherited vars before spawning children (the app's own roots survive via the post-strip overlay);
  the QA harness mirrors the filter. Verified popup-free end-to-end on the canonical `.app`.
- **Honest Beta baseline.** A clean same-SHA 5-persona VM sweep: newbie **8**, veteran **7**,
  adversarial **7**, narrative **8** — Beta-quality for four of five personas. The engine
  re-validated **SRD-correct** (a reported "critical" Lv-10 Paladin spell-slot bug is a
  half-caster-rules false alarm — the engine knows `L10 Paladin → L3 slots`). The RRI single
  number stays measurement-noisy (a `party_traveled` behavioral false-cap when an emergent session
  stays in one scene). The one real feature gap surfaced by the build-optimizer persona is the
  **planning/theorycraft layer** (spell-preparation UI, market catalogue, level-up planner) — the
  next milestone toward Beta.

---

## [1.0.4-rc2] — 2026-06-16

**The felt bundle — battle, atlas, and a companion soul.** Building on rc1's playable
foundation, rc2 lands the first wave of felt-quality work plus a measurement-integrity fix
that had been silently tanking every score.

- **Companions react to your choices (#940, #941).** A new keyword-only `approval_tags` on
  `record_decision` (and `persist_beat`'s decision leg) moves a present companion's approval
  when a choice matches their content-authored `approval_likes`/`approval_dislikes` — the DM
  tags the *cause*, the engine owns the *number* (gauge-not-fiction). Surfaced on
  `scene_context.durable.companions` every beat. All seven Baldur's Gate origins
  (incl. the four rostered ones — Shadowheart, Astarion, Karlach, Wyll) carry a shared
  snake-case approval vocabulary, so one tag can ripple across the party and turn arc gates.
  Brings the previously-dead likes/dislikes read to life.
- **Combat *looks* like a battle (#937).** The combat screen now renders the location scene
  art as a backdrop (reusing the proven `location:<id>` image scope) and shows condition
  chips on tokens — on top of the already-complete SRD 5.2 turn engine.
- **A coherent world map (#939).** Authored geographic axial-hex coordinates for the Baldur's
  Gate seed locations (+ wired area-`hex` through ingestion + `map_kind: hex`), replacing the
  force-sim scatter with a readable, hub-centered atlas.
- **The behavioral-gate false-cap is dead (#938).** `persist_beat` now tolerates a bare/empty
  `campaign_id` (resolves the active campaign) instead of a fatal rejection — a single bare
  model-slip was RED-capping every quality lens to 2.5 on otherwise-coherent sessions.
  Validated live: a re-run flipped behavioral RED→GREEN.
- **The seated hero stays correct (#935).** The live active-hero correction routes through the
  authoritative `kind=player` resolver (not `party[0]`), so a companion never displaces the PC
  mid-session.

Additive throughout; old snapshots round-trip; the engine remains the sole writer. Pre-release.

---

## [1.0.4-rc1] — 2026-06-16

**Player-Ready Beta candidate — the first genuinely independent, resumable game.**
Cut as a save-checkpoint: WorldOS can now be cold-opened, played, quit, and **resumed**
as a real independent game on the production path (`play.sh` + the OpenWorlds viewer —
the exact path the native `.app` runs).

- **State isolation + Resume re-attach (#933):** the app saves to an isolated per-user
  state dir (`~/.worldos/state`); the launcher catalog surfaces layered saves as
  resumable; `play.sh` re-attaches the saved campaign via `WORLDOS_RESUME_CAMPAIGN`
  (move sink preserved, no fresh cold-open). Fresh launch truncates; resume appends —
  proven by `test_play_state_isolation_resume.sh`, `test_resume_reattach.py`, and
  `test_launcher_catalog_layering.py`.
- **Active hero is the authoritative `kind=player` actor (#932, #935):** the seated PC is
  the engine's actor (`surface.actor` → first `kind=player` PC → `party[0]`), never a
  companion the engine happened to order first — on both the initial seed (#932) and the
  live-update correction (#935). Fixes the "ACTIVE ASTARION / Lvl 1 Adventurer" mis-seat.
- **Scripted provider hermetic vs injected state dir (#934):** the QA scripted provider
  pins both state-dir env names + chooses a free port, so the part-A native gate mints a
  live `can_act:true` surface under the app's `WORLDOS_STATE_DIR` injection.

Proven end-to-end (real claude DM, headless production path): cold-open seated a proper
PC (Rolan, Tiefling Wizard 3 Evoker), live surface, a real player move resolved, save
persisted, quit → catalog shows it resumable → resume re-attached the same campaign live.
The scripted part-A native gate PASSES on the real built `.app`.

Known: the literal `.app` GUI cold-open fires a one-time per-build macOS Keychain prompt
(ad-hoc signature); `claude setup-token` → `~/.worldos/claude-token` makes it popup-free.

---

## [1.0.3] — 2026-05-29

**Renamed ClawDnD → WorldOS.** The product is now **WorldOS** — "simulate living,
AI-generated worlds and play epic D&D 5e inside them." Shipped as four phased,
CI-green PRs so nothing broke mid-flight:

- **Identity (#296):** plugin id `clawdnd` → **`worldos`**; the macOS app is now
  `WorldOSApp` / `WorldOS.app`; author `electricsheephq`; repo moved to
  `github.com/electricsheephq/WorldOS` (prior owner paths may redirect).
  README, public docs, and the dev skill (`worldos-dev`) rebranded.
- **Code + docs (#299):** ClawDnD → WorldOS across source and documentation; **zero
  ClawDnD references remain in `servers/engine`**.
- **Env compatibility (#300, non-breaking):** a shared resolver now prefers
  `WORLDOS_<X>` and falls back to the legacy `CLAWDND_<X>` (one-time deprecation
  warning); `~/.worldos` preferred with `~/.clawdnd` fallback. **Both names still
  work** — no migration required.
- **Intentionally unchanged wire contracts:** the MCP server ids
  (`clawdnd-engine/rules/voice/player`) and the `dev.clawdnd.app` bundle id are kept
  as live contracts and will migrate in their own dedicated releases.

Also folded in since 1.0.2: OpenWorlds UI-audit fixes — platform-aware title bar
(#260), Browse-spellbook CTA + modal (#268), the 12-slot canonical paper-doll (#271),
the camp rest action (#282), and the canon-NPC reverse-picker (roster surface) —
**axe-core 0 across 18 screens**. Player-bind correctness: the DM/test flow now
SELECTS a real canon NPC as the PC (`load_canon_character(kind="player")`) and never
invents one, with the `kind="player"` ⇒ in-party engine invariant (#162) closing the
player-outside-party gate.

Engine **1511** + viewer **146** + voice 17 + rules 16 + axe 0 + license-check green;
SwiftPM build clean.

---

## [1.0.2] — 2026-05-29

**OpenWorlds UI graphics-release burndown — every screen cleaner, every image present,
zero accessibility violations.** Drives the page-by-page UI audit (epic #242, Phase 5) into
the product:

- **Accessibility: axe-core 11 → 0** across all 16 screens. Scrollable framed panels + inner
  scroll regions (session log, combat log, relations detail panes) are now keyboard-focusable
  (#291); the World Seed notes textarea gets a label (#292). Health-check `--axe` baseline locked at 0.
- **Item art that was silently broken now renders.** Character / Forge / Merchant built their
  `item-<slug>` image scopes via an undefined `window.slug` → every item 404'd to a placeholder
  despite the art existing. Defined it once + wired the Character equipped block (#270).
- **Title-bar no longer overlaps the nav rail on any screen** (#260) — platform-aware (keeps the
  76px inset for the macOS native traffic lights; harmless in browser).
- **Demo-world leaks removed:** Bestiary "THE MARCHES" → "the Sword Coast" + empty stat fields
  hidden (#262/#263); Forge Workshop-Ledger seed entries dropped for an honest empty-state
  (#264). Creation Plane race/class/portrait-gallery art wired (#265).
- **Engine play-loop fixes:** a `kind="player"` character is now always in the party (invariant
  — fixes the player-in-party gate); `update_character` accepts the intuitive `skills`/`expertise`
  aliases; auto-hit spells documented to resolve via `cast_spell` not `attack`.
- **Proactive QA gates:** `no_rejected_tool_calls` + `xp_awarded_on_progression` (FATAL — lock the
  version-skew + milestone-XP classes) and `caster_has_spellbook` + `quest_objectives_progress`
  (WARN). Combat-fidelity cues validated (sprint 3.0 → 3.7).

Engine 1435 + viewer 86 + axe 0 + license-check green. Native app builds clean + codesign valid.

---

## [1.0.1] — 2026-05-28

**Phase-4 action lanes + native-app reliability + the seven canon dossiers.** Wires the
last two display-only prototype screens (Merchant, Forge) into the live `/move` sink so a
running session can actually transact through them; seeds machine-usable
`companion_dossier` blocks on the seven BG3 origin heroes (the engine's living-world
systems — banter, approval, arc gates, camp prompts — now have a real anchor for the
canon cast); and ships a stack of native-app reliability + unblock work for a class of
host-environment hangs (security-software file scanning on freshly-rebuilt ad-hoc
signatures).

### Added

- **Merchant — BUY relays a structured `do` move on /move when `can_act`** (Phase-4
  action lane). Reads `surface.can_act` + `campaign_id` from `/character-surface` and
  POSTs `{kind:"do", text:"I buy X and Y from <merchant> for N gp, haggled Z% off"}` so
  the DM resolves the purchase via the engine's `buy_item` tool. The honest "(preview)"
  / "(live)" label flips with `can_act`.
- **Forge — Craft relays a structured `check` move on /move when `can_act`** (Phase-4
  action lane). The /character-surface fetch was already in place for the live-party
  crafter selector; now the craft button posts `{kind:"check", skill, dc, name, text}`
  during a live session. The Workshop Ledger records "relayed (DC X)" entries; the DM
  rolls + narrates. Banner + capability badge flip "Live" / "Display-only preview"
  honestly.
- **Companion dossiers for the seven BG3 origin heroes** (Astarion / Gale / Karlach /
  Lae'zel / Shadowheart / Wyll / Halsin). Each carries a terse machine-usable
  `companion_dossier` with `wound`, `wants`, `fears`, `values`, `approval_likes` /
  `approval_dislikes`, `banter_tags`, `camp_prompts` — lore-faithful tags, never long
  copied prose (per the licensing guard). The engine's living-world systems now have
  real anchors for the canon cast instead of `note` strings buried in prompts. Also
  reaffirms `playable=false` + `role="hero"` so the seven legends are encounterable
  NPCs of the post-BG3 Faerûn, never roll-able player characters.
- **URL-hash screen deep-link** (`/openworlds/#character`, `#battle`, `#parley`, etc.,
  with aliases `battle→combat`, `parley→dialogue`, `market→merchant`,
  `chronicles→launcher`, `stash→inventory`). Real feature (linkable / bookmarkable
  screens) and the foundation of the autonomous headless-Chrome QA workflow.
- **`script/unblock_native_app.sh`** — one-shot helper that reaps stale processes, kills
  NordVPN's Shield + privileged helper (one sudo prompt; they auto-restart), rebuilds
  the app, opens it, and polls for the viewer to bind. Designed for fast recovery when
  a freshly-rebuilt ad-hoc-signed binary trips a security scanner re-evaluation.

### Changed

- **Build script now prefers a Developer ID signature** when one is in the keychain
  (falls back to ad-hoc otherwise). A Developer ID signature has the SAME cdhash across
  rebuilds AND is generally pre-trusted by security software, so subsequent rebuilds
  don't re-trip the file scanner. First run prompts the standard macOS Keychain
  "Always Allow" dialog; after one click, every future rebuild signs silently. This is
  the foundation Sparkle auto-update would build on.
- **App viewer-subprocess launch hardened.** Passes an ABSOLUTE script path
  (`<repo>/viewer/server.py`) and sets the working directory to an internal-disk temp
  dir (`NSTemporaryDirectory`) instead of the repo URL. Python's interpreter init no
  longer calls `getcwd()` on an external/removable volume — a kernel-level enumeration
  that some security scanners hang on `open$NOCANCEL`. `server.py` resolves all of its
  assets from `__file__`, so the cwd change is transparent to it.
- **Repo can be checked out anywhere.** Verified by cloning into a clean checkout in
  parallel with another worktree; the build + the test suites + the whole OpenWorlds
  screen set all run identically from either location.

### Fixed

- Four pre-existing failing engine tests (`test_list_canon_characters_playable_filter`,
  `test_start_character_pickup_rejects_hero_accepts_minor`,
  `test_canon_character_record_carries_a_dossier`,
  `test_load_canon_character_populates_dossier`) — root cause was the seven origin-hero
  JSON files missing `playable` / `role` flags AND the new `companion_dossier` block.
  Engine suite is now **1385 / 1385 passing**; viewer suite stays at 90 / 90.

---

## [1.0.0] — 2026-05-27

**The Living-World Engine — release milestone.** This release rounds off the deterministic
5e engine, completes the Quest & Arc generative layer, ships a second original CC-BY world
(*The Tidal Commonwealth*), and makes the OpenWorlds native macOS app a playable read-model
with image rendering. 1.0 is a local/personal build; public distribution and notarization
are deferred to 1.0.1.

### Added

**Combat engine (SRD 5.2)**

- Monster **Multiattack** — enforced in the attack economy; authoritative to-hit surfaced
  at `start_combat`.
- Monster **Parry** defensive reactions — auto-applied when the reaction flips a hit to a
  miss; DM narrates the deflection.
- **Grapple / shove / escape** resolver (SRD 5.2 save-based).
- **Surprise-attack** affordance on `start_combat` + combat-init doctrine.
- **Battle Master maneuver die** — rolled into the triggering attack's damage.
- **Multi-component attack damage** and Multiattack composition hardened.
- End-of-turn **repeat saves** auto-enforced so save-ends conditions never lock forever.
- **Guiding Bolt** advantage rider auto-granted and consumed on hit (not on cast).
- `turn_brief` surfaced on every `next_turn` call; Round-1 turn-skip guard enforced.
- Crit narration cites the actual source, not a blanket "nat 20".

**Spellcasting**

- All 339 SRD spells handled by `cast_spell`; slot management, upcasting, concentration
  binding, and save-vs-attack resolution.

**Quest & Arc engine (three-layer)**

- **Layer 1 — Rule-of-three quest evolution**: `Quest.evolves_to` + `callback_in_days`
  schedules follow-on consequences so no setup is left dangling.
- **Layer 2 — Decision-gated companion flips**: player choices accumulate weight; a
  betrayal roll fires only above the attitude threshold, telegraphed by warning bands.
- **Layer 3 — First-class Events**: `ParleyOption` / `Outcome` pairs carry deterministic
  engine outcomes (flags, reputation shifts, scheduled consequences) wired to the DM's
  beat loop.
- **Faction-growth arcs**: the join → grow → lead loop (`faction_arc.py`).
- Living-story trio wired into the DM skill and seeded with canon exemplar content.
- Betrayal modelled as a **rising probability roll** gated by attitude value (not a hard
  threshold).

**Living-world layer**

- `world_tick` background world-sim: standing threads advance on the day clock
  (auto-fired by `travel_to` / `downtime`), one beat at a time.
- `lookup_lore` FTS over per-world `lore/` corpus; authored pages (tier 0) outrank
  ingested wiki pages (tier 1); `era` / chronology surfaced per hit.
- `recall` / `recall_npc` / `recall_decisions` campaign-memory ledger (FTS5 over
  snapshot + session logs); derived, rebuildable, deletable.
- Campaign Director + Scene-Debt advisory wired into the beat loop; `add_quest` went
  from rare to automatic.
- Typed multi-resolution **wandering encounters** (combat / skill / social / hazard / boon).
- **Parley scaffold** + `encounter_outlook` + balancing doctrine.
- `record_decision` / `adjust_reputation` / `add_consequence` (time-deferred consequences).
- `add_location` live world-building with orphan/duplicate warnings.
- **Campaign calendar** display projection.
- Tolerant store load — unknown top-level keys dropped so old saves survive future schema
  changes.
- Settlement pressure read-model skeleton; worldgraph atlas graph metadata skeleton;
  bestiary authored monster metadata skeleton; private compendium sidecar scaffold.

**Worlds shipped**

- *The Sundered Reach* (original, CC-BY-4.0) — the default world, six authored lore pages.
- *Unofficial Baldur's Gate 3+ Universe Seed* (free, unofficial Fan Content — Wizards Fan
  Content Policy + Larian; never sold) — 248-page ingested wiki corpus, 5 navigable
  post-BG3 areas, mid-tier monster pack, canon BG events / faction arcs / companion seams.
- *The Tidal Commonwealth* (original, CC-BY-4.0) — a second original world as a
  generativity spike; geographic texture (Saltmere, Ironhull, Vethis); playable depth.
- **Base-world companion arcs** — a companion who can turn, functional in any world seed.

**OpenWorlds native macOS app**

- SwiftUI shell (`macos/ClawDnDApp/`) — `script/build_and_run.sh` builds and launches an
  ad-hoc-signed app bundle.
- **Image render bridge** (`/image` endpoint) — generated and ingested art shown in Atlas,
  Parley, inventory (item icons), character/table portrait, and scene-art screens.
- Five OpenWorlds screens wired to live engine read-models: map/atlas, party, quests,
  event feed, combat command center.
- Companion camp beat history, acts chronicle, betrayal-warning, and quest-evolution
  callbacks surfaced in read-models.
- CapabilityBadge preview banners on display-only screens.

**Voice**

- Kokoro TTS (local, free, multi-voice) default; ElevenLabs and null backends swappable
  via `CLAWDND_TTS_BACKEND`.
- Per-character `voice_id` mapping; reliable text-only fallback.
- STT seam in place (`SttBackend` interface); no live backend yet — you type your turn.

**Play surface**

- Interactive play dashboard (`scripts/play.sh` / `clawdnd-play.command`): acts through
  Say / Do / Continue / dice / combat buttons and click-to-travel; DM resolves each move
  via the engine and renders the next beat live. Safety-capped (per-turn budget, session
  ceiling, turn cap).
- `clawdnd-dashboard.command` — read-only director's view for watching a running game.
- Desktop shortcut installed by `scripts/install-desktop-shortcut.sh`.
- Beat-aware DM loop with midpoint/climax runbooks scaling to the play cap.

**QA**

- Behavioral gate (`assert_behavioral.py`) — deterministic PASS/FAIL on structural
  integrity (turns taken, world progressed, player didn't narrate the world, companions
  spoke, combat closed cleanly, no dangling conditions).
- Tolkien story-craft lens (≈ 4.1–4.2 / 5, prestige fantasy) + mechanical lens
  (target ≥ 4.5) + 5e-fidelity Angry-DM adversarial lens.
- Fast combat-sprint lane + scoped behavioral gate.
- Parallel retry-hardened QA harness; GPT-5.4 cross-check scorer.
- `qa/SCORECARD.md` running ledger; `qa/SCORING.md` rubric.
- ~353 engine tests (pytest) green in CI; 17 QA distill tests.

### Changed

- DM skill upgraded to "Generating a world live" mode: beat cycle, per-turn Director
  consultation, storytelling craft bar (antagonist warmth-first, felt menace, the
  unforgettable beat).
- Bestiary multi-directory first-wins layout (content-pack foundation).
- Engine schema hardened (`extra="forbid"` on all Pydantic v2 models).
- Quest variants resolved once at world-gen from `world.json` seed (`quest_variants`
  weighted matrices with documented rarity bands).
- Viewer shifted from read-only to interactive play surface.

### Fixed

- Multiattack enforcement: engine *refuses* the wrong move in the attack economy.
- Battle Master maneuver die was previously omitted from attack damage.
- Multi-component attack damage accumulation.
- End-of-turn repeat saves for save-ends conditions (e.g. Hold Person).
- Guiding Bolt advantage rider applied on hit, not on cast.
- PC turn-skip guard enforced in `next_turn`.
- Dice count/sides clamped to stop pathological rolls hanging the engine.
- Store tolerant-load so unknown top-level keys never brick an old save.
- Crit narration now cites the actual crit source.

---

## [0.2.0] — Living-World Generative Engine

The pivot: the AI DM now **generates an epic story live inside a persistent,
canon-anchored world**, rather than only running pre-authored modules.

- **World seeds** — `start_world(world_id)` seeds a campaign from a world bible
  (`content/worlds/<id>/world.json`).
- **On-demand lore** — `lookup_lore` (FTS over a per-world `lore/` corpus).
- **Campaign memory** — `recall` / `recall_npc` / `recall_decisions`.
- **Background world-sim** — `world_tick` ticks standing threads on the day clock.
- **Wiki-ingestion pipeline** — `tools/ingest/` builds lore corpora from MediaWiki/Fandom
  (CC-BY-SA); ships a 248-page Baldur's Gate corpus.
- **Authored-scene craft** — `get_scene`, `add_location`, `record_decision`,
  `adjust_reputation`.
- **Companion** — `companion_advise` + party-deliberation loop.
- **Read-only play-view** (`viewer/`) — map / party / quests / event feed.
- **Front door** — `/world-list`, `/world-play`, `/world-new` + `world-author` skill.
- **Worlds shipped** — *The Sundered Reach* (CC-BY-4.0) and the *Unofficial BG3+ Universe
  Seed* (free Fan Content).
- QA harness with Tolkien story-craft lens.

## [0.1.0] — Tier-1 Feature-Complete

Three MCP servers (engine, rules, voice). Dice (advantage/crit, roll ledger), characters
and leveling, combat and action economy, all-339-spell casting, rests, inventory/economy,
NPC memory and check-gated social, exploration/travel, encounters, multi-session
persistence and "Previously on…" recaps, time-deferred consequences, a multi-act arc
generator. Bundled SRD 5.2.1 (CC-BY-4.0) and bestiary. Original adventures ("The Cellar
Rats", "The Embergloom Pact"). Voice (Kokoro / ElevenLabs / null) and STT seam. Player
slash commands, README, then-current licensing, and third-party notices.
