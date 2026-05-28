# Changelog

All notable changes to ClawDnD are documented here.
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format.
ClawDnD's code is MIT; world seeds are licensed separately (see `content/worlds/README.md`).

---

## [Unreleased]

Nothing queued yet.

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
- **Repo can be checked out anywhere.** Verified by cloning into `~/ClawDnD-val` on
  internal disk in parallel with the LEXAR worktree; the build + the test suites + the
  whole OpenWorlds screen set all run identically from either location.

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
slash commands, README, MIT license and third-party notices.
