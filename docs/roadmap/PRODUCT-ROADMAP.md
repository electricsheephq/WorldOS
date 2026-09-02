# WorldOS Product Roadmap — the ladder, the sprints, the versions

> **ACTIVE SPRINT: charter #1386 (Act II close-out — Rendered Felt). Refresh this pointer at every charter transition.**

> **The master navigation doc (v2 — the three Acts).** VISION.md says what the product IS and the
> bar it must clear; this doc says the ORDER we build it in — every sprint from here to the
> Walkable World and beyond, with a binding gate, an ordered issue list, and a version pin, so
> that ANY agent (Claude, Codex, GLM, human) can open this file, find the active sprint, and
> execute a lane without needing this repo's history in its head. (New here? Start at
> `docs/OPERATIONS.md` — the one-page bootstrap.) v1 authored + red-teamed 2026-07-03; **v2 same
> day after the owner expanded the North Star** (full walkable rendered games, the template
> library, the harvest loop — see VISION "The destination"). Owner reviews via PR.

## 0. THE THREE ACTS (the shape of everything below)

- **ACT I — The Demo** (now → GA = v1.1.0): sprints **S1–S10** (§4), unchanged from v1 except that
  the render-delivery decision is now RESOLVED (`RENDER-DELIVERY-DECISION.md`: Unity standalone =
  the demo's delivery; the S2 entry gate is satisfied) and S8's delivery is the Unity standalone +
  app handoff.
- **ACT II — The Walkable World + The Harvest Loop**: the **W-series** (§4b: scene-at-rest → walk
  → talk → living stage → the Unity player tier) and the **HV-series** (§4c: artifact evals →
  extract → promote → reuse → flywheel ops). Interleaves with Act I where parallel-safe — W1, HV1,
  HV2 start immediately. Act II's exit = **T3 is real**: a blind AI playtester completes a quest
  loop IN the rendered surface, on a library-assembled game.
- **ACT III — The Universe Platform** (§6): packs, remaining agent lanes, hosted runtime, creator,
  KOTOR-class universes, the 2D tier, engine-as-platform.

---

## 1. The product thesis

WorldOS is **"Baldur's Gate that writes itself, played through your own AI."** Three concentric
products, built in this order:

1. **The Game** — the BG-universe flagship: the deterministic SRD 5.2 engine + the OpenWorlds
   viewer + the native app + the PoE2 painterly combat demo. The 1.0 ladder ships this.
2. **The Platform** — the same game played through *any* agent (Claude Code today; Codex /
   OpenClaw / Hermes lanes in epic #911) and eventually hosted (milestone 25). The moat: the
   player brings their own agent + credentials, so there is **no central LLM cost** — the
   distribution economics are the platform bet.
3. **The Universe System** — universe creator + template packs (#644) + licensed universes
   (KOTOR #330, VtM #331, Shadowrun #332) + fan showcase (milestone 26). Post-GA revenue
   shape: original-world paid alpha (#712) with BG as the free flagship.

**The meta-product** (name it, because it is working): the measurement culture itself —
control-anchored art panels, story lenses, the behavioral gate, the FELT track. These
instruments are what let autonomous agents build a game to a bar. WorldOS is the game AND the
proof that the method ships games.

## 2. Version map (fixes the semver/rung muddle)

Engine releases stay **semver v1.0.x**; product rungs are **named releases pinned to an engine
version**. The old "v1.1 — The Table" milestone label was misleading (its content ships inside
v1.0.7–v1.0.8) and is retitled **"The Table (story/world systems)"**.

| Engine version | Sprint(s) | Product rung cut at this version |
|---|---|---|
| v1.0.5 | S1 | **SHIPPED 2026-07-08** — S1 evidence gate3d: story 4.2 / mech 4.1 / behavioral GREEN |
| v1.0.6 | S2 + S3 | — (felt demo loop + combat readability) |
| v1.0.7 | S4 | — (The Table I) |
| v1.0.8 | S5 + S6 | — (The Table II + alive/latency) |
| v1.0.9 | S7 | **Player-Ready Beta** (2D-viewer scope — see §4/S7) |
| v1.0.10 | S8 (+S9 parallel) | **1.0 Playable Combat Demo** |
| v1.1.0 | S10 | **1.0 GA** |
| v2.x | post-GA phases | platform / universes / rendered-game (§6) |

## 3. Sprint execution protocol (how ANY agent runs a lane)

- **A sprint = a charter epic issue** (label `sprint-charter`) + its milestone. The charter
  carries: GOAL · BINDING GATE (as *runnable commands + evidence paths*, never vibes) ·
  ORDERED issues with lane labels · INVARIANT checklist · RISKS/owner-gates.
- **Lanes** (issue labels): `lane:engine` · `lane:story` · `lane:renderer` · `lane:app` ·
  `lane:qa` · `lane:art` · `lane:platform`.
- **An issue enters a charter only when dispatch-ready**: spec, acceptance criteria, evidence
  requirements, invariant notes. The charter author upgrades stale issues or they wait.
- **The loop** (unchanged, from VISION "How we build"): worktree off main → additive change →
  single-process pytest + `qa/fast_gate.sh` → PR → review-gated merge → prune. QA tiers per
  `docs/qa/FAST_GATE.md`; never report Tier-0/1 as a release verdict.
- **Anti-variance rule for release gates** (red-team amendment): a story gate is never a single
  duo — the deep 24-beat read must be corroborated by the sweep duo; if they straddle the bar,
  a third duo decides by median. Mech is always a combat-sprint median (n≥3). FELT gates are
  **control-anchored same-panel deltas** (beat the embedded disguised real-art control), never
  raw votes or absolute numbers.
- **Ledger discipline**: every scored run → `qa/scores_db.py`; every panel embeds a disguised
  real-art control; identity mappings live outside panel dirs.

## 4. The sprints

### S1 — Engagement Engined *(ACTIVE — closes the v1.0.5 campaign)*
- **Goal:** the engine's relationship/quest/camp machinery is *driven*, not narrated — the
  measured rri-a1-duo2 defect class (behavioral RED: 0 quest/relationship tool calls in 22
  beats) is closed by every-beat obligation cues.
- **Issues:** #1286 (✅ PR #1299) · #1288 (✅) · #1287 + #1285 (PR #1300) · #1160 WS3a rebase ·
  merge-queue closeout (#1282 #1283 #1293 #1294 #1295 #1297 #1298).
- **Gate (runnable):** re-freeze SHA → fresh 24-beat Opus duo: story ≥4.3 **and** behavioral
  GREEN (structural_completeness passes), corroborated per §3 anti-variance rule → 3× combat
  sprint median → VM 5-persona sweep → Mac native part-A → `qa/release_readiness.py` rollup →
  **cut v1.0.5** (CHANGELOG batch + tag + pre-release + evidence bundle).
- **Lane:** `lane:story` + `lane:qa`.

### S2 — The Felt Demo Loop *(graphics; milestone 31)*
- **ENTRY GATE (red-team amendment — do this FIRST):** the **render-delivery decision doc**
  (issue filed as the S2-entry blocker): how does the Unity render reach the player's screen —
  embedded Unity build in the app / render-service streaming frames into OpenWorlds / native
  Unity player? First-principles decision doc (options, data, diagram, debate, decision), NOT
  code. Today's path is one-way box→QA-frames; S2's packaging depends on the answer even
  though the Animator/VFX logic itself is delivery-agnostic.
- **Goal:** the demo's moment-to-moment feel — animation, VFX, death, motion — driven by real
  engine events.
- **Issues:** `/events` Action-Replay → Animator/VFX wiring (verb→clip map, `anim_hint`,
  DOTween `lastPath` glide, damage/VFX at engine cells) · death/defeat visual resolution
  (topple/fade) · #1296 edge-blending (Beautify composite grade / silhouette feather) ·
  #1284 prop-cell grounding · #1281 re-validation on a REAL church-located combat ·
  L7 motion-reel lens first run · ghoul combat-clip completion.
- **Gate:** combat-FUN checklist 6/6 with evidence frames (VISION §Graphics) + FELT re-panel
  where the hero frame **meets/beats the embedded real-art control same-panel** (votes are a
  secondary signal only).
- **Lane:** `lane:renderer` + `lane:art`.

### S3 — Combat Readability *(viewer; milestone 28 UI cluster; parallel-safe with S2)*
- **Goal:** the combat UI communicates state like a real CRPG on the 2D OpenWorlds surface —
  independent of the render-delivery answer.
- **Issues:** #595 condition badges + initiative row · #596 reaction pip / OA banner /
  concentration warning · #597 target-bind indicator · #599 dodge/dash/disengage/hide/shove
  verbs · #219 crit attribution + point-blank nits · #827 character-sheet button.
- **Gate (runnable, taxonomy fixed up front):** a blind AI playtester (`qa/ui_playtest.sh`,
  combat-seeking persona) completes a GUI combat with **zero bugs in the defined
  confusion-taxonomy**: dead-control · state-mismatch (UI disagrees with engine values) ·
  unexplained-wait >10s · mislabeled-affordance. Taxonomy classes tagged in `bugs.ndjson`.
- **Lane:** `lane:app` + `lane:engine`.

### S4 — The Table I: Rest & Memory *(milestone 29 retitled; sequel to S1)*
- **Goal:** camp/rest is a real subsystem and the world visibly remembers choices — the two
  cheapest-highest-leverage Table pillars, aimed at the engagement WARNs S1's measurement
  surfaced (camp never fired, world under-peopled).
- **Issues:** #611 camp_supplies + make_camp/forage · #609 honest camp clock · #612 + #613 +
  #614 relationship/reputation change-logs + panel · #616 ArcGate ladder · #775 retrieval
  surface revival (+#803 prerequisites) · **#753 latency BUDGET definition (pulled forward —
  red-team amendment: it is the documented #1 give-up driver; defining + instrumenting is
  cheap and it must be measurable before Beta gates on it).**
- **Gate:** a scored 24-beat duo where camp fires ≥1×, the change-logs populate, a companion
  arc-gate progresses, and the S1-era engagement WARNs (camp/world_peopled/faction) are clear —
  story ≥4.3 held per §3 rule.
- **Lane:** `lane:story` + `lane:engine`.

### S5 — The Table II: Agendas & Rhythm
- **Goal:** companions push back; sessions have rhythm; failure becomes story.
- **Issues:** #837 companion agendas (**includes the engine-side `proactive_beat` flag,
  write+read in the same change** — red-team amendment: "companion-initiated" must be an
  engine-mutated value, never fiction-read) · #838 session rhythm (recap/cliffhanger/epilogue) ·
  #839 fail-forward complications · #836 consequence surface · #141 parley relay · #608 parley
  echo.
- **Gate:** a scored duo with ≥1 engine-flagged proactive companion beat + recap/cliffhanger
  present + **tolkien `dramatic_momentum` dim ≥4.3** (an existing rubric dimension — red-team's
  "invented metric" objection was wrong on this one point; it printed 4.2 in tonight's
  scorecard).
- **Lane:** `lane:story`.

### S6 — Alive: Live Composition
- **Goal:** the screen always shows motion — #835 keystone (stream the DM's scene as written),
  wired against the #753 budget defined in S4; #561 effort tiers.
- **Gate:** measured perceived-latency inside the budget on the GUI loop (from
  `duration_api_ms` + streaming-visible evidence); no dead wait beyond budget anywhere in a
  full playtest.
- **Lane:** `lane:engine` + `lane:app`.

### S7 — Player-Ready Beta *(cuts the Beta rung, on ~v1.0.9)*
- **SCOPE (red-team amendment, explicit):** Beta's "no broken moment" bar applies to the
  **shipped 2D OpenWorlds surface in the built .app**. The Unity render is NOT in scope for
  Beta — render-in-app is a Demo-1.0 (S8) gate. This resolves the ladder contradiction; VISION
  is patched to say so.
- **Issues:** #640 multi-campaign freeze (P0) · #307/#308/#310 playtest fixes · #288/#284
  responsive · #279/#174 icon registry · #503/#502 chronicle/narration polish · onboarding
  self-teach pass · "how a player plays" run-flow doc (per surface: app / CLI / bring-your-own-
  agent) · **latency budget met** (from S4/S6).
- **Gate:** VISION's Beta rung verbatim on the scoped surface: built .app, a no-prior-knowledge
  dogfood arc with no broken moment, honest felt session; 5-persona sweep green at the beta bar.
- **Lane:** `lane:app`.

### S8 — Demo Assembly *(cuts 1.0 Playable Combat Demo, on ~v1.0.10)*
- **Goal:** assemble the demo per the render-delivery decision (made at S2 entry): a modular
  multi-room dungeon, playable in-app, on placeholders, backdrop scorecard passing per room.
- **Issues:** render-in-app integration (per the decision) · demo dungeon authoring (4–6
  linked room-units with door-cell graph: crypt/church/tavern/bosshall + 2 new) · monster wave
  2 (cast to ~10, registry-complete) · day/night in-app · #441 blind traversal + combat build
  gate · backdrop scorecard run per demo room.
- **Gate:** VISION's 1.0 rung verbatim: playable modular combat scene in-app on the PoE2 stack,
  placeholders OK, backdrop scorecard PASS per room, combat-FUN 6/6 **in-app**, FELT
  control-anchored parity on the hero frame.
- **Lane:** `lane:renderer` + `lane:app` + `lane:art`.

### S9 — Atelier: Craft Above the Controls *(milestone 33; parallel with S7/S8)*
- **Issues:** #1241 Atelier kit (3D modular base + albedo LoRA paint-over) · #1243
  tile-ControlNet finisher · #1217 multi-room composition craft · #1218 stray-item control.
- **Gate:** adopted plates beat their real-art controls by ≥+1 median on ≥2 consecutive panels;
  FELT control-parity on hero frames.
- **Provider-fallback note (red-team amendment):** Scenario + Gemini are single-vendor
  dependencies for the plate pipeline; the registry-by-slot invariant makes output swaps cheap,
  but the *generation* lane needs a named fallback (ComfyUI/SDXL on GEX44 — already
  provisioned for #1243) documented in the graphics roadmap.
- **Lane:** `lane:art`.

### S10 — GA Assembly *(cuts 1.0 GA = v1.1.0)*
- **Issues:** placeholder→real art registry swap (through the S2/S9-proven pipelines) · #151
  notarized distribution · #134 Sparkle updates · #591 parity meta-check · **#643 Scoring v2
  as the GA-grade instrument** (RRI saturation + satisfaction provenance fixed BEFORE the GA
  verdict is measured on it) · #1137 save-compat CI guard · #1122 security triage (also gates
  the hosted-runtime track) · multi-agent GA slice (see platform track).
- **Gate:** VISION's GA rung: Demo workflow on real art + Beta story/world bar + notarized +
  parity + **the bring-your-own-agent surface documented and the Claude Code lane verified
  end-to-end** (red-team amendment: GA-block ONE proven provider lane, not the 5-lane epic).
- **Lane:** all.

## 4b. THE W-SERIES — The Walkable World (Act II; each = 1–2 overnight bursts)

> Every W sprint ships its EVAL FIRST (decision-by-eval) + a **text-tier byte-identity test**
> (VISION invariant: the text tier always plays). Exploration ground truth: scene_grid already
> carries walkable cells AND populated `spawns` (per-kind generators, scene_grid.py:254–660) —
> spawns is read today by the layout-validator (scene_grid.py:974, spawn cells vs blocked
> cells) and by the Unity closed-loop renderer (ClosedLoopBuilder.cs:126-129, party/foe cell
> placement); no CONSUMER reads it for rendering/projection in the live viewer/product surface
> yet; move_to_coords exists combat-gated;
> the viewer already paints walkability. The gaps are ungatings + one new render mode.

> **Act II execution state (living; last trued 2026-07-08):**
> **Sprint 1 (charter #1328, CLOSED)** — W1 #1330 ✅ · HV1 #1331 ✅ · HV2 #1329 ✅ · Tier-1.5 probe
> harness #1336 ✅. QA-economics v2 doctrine merged (#1340, docs/OPERATIONS.md).
> **Sprint 2 (charter #1337, CLOSED)** — W1–W4 (#1330/#1341/#1344/…) and HV1–HV5 (#1331/#1329/#1338/
> #1342/…) are MERGED; W5 (#1322, the Unity player tier) remains open, not yet started.
> **ACTIVE charter = #1386** ("Act II close-out — Rendered Felt"): ordered lane is #1284 actor
> grounding v2 → the rendered rest-scene demo (canon fixture, grounded actors, W1 stage block,
> FELT panel vs the PoE2 anchor) → W5a Unity player build (#1322) → HV follow-ups (#1378 cross_door
> re-stage, HV extractor quality pass 2). Entry gate satisfied: v1.0.5 released, GEX44 box
> reachable, render-delivery decision #1302 CLOSED. Closing #1386 pulls the next charter: S2
> (#1309, entry gate satisfied) queues after.
> **v1.0.5 RELEASED 2026-07-08** — S1 evidence gate: gate3d story 4.2 / mech 4.1 / behavioral GREEN.

- **W1 — "Scene at Rest"** *(✅ SHIPPED — PR #1330, incl. the felt_rest_panel instrument)*. Additive `stage` block (`mode: rest|combat` +
  rest tokens) in `build_combat_surface` (viewer/server.py:3376; optionally aliased as
  /scene-surface) — party + present NPCs (`Character.location_id == current`) PROJECTED onto
  `scene_grid.spawns` (add `npc:<id>` spawn keys in the generators). Projection only — zero new
  persisted state. Renderer scene-at-rest mode (idle clips; assets exist). **EVAL (build first):**
  FELT-style rest-scene panel — "does the tavern-with-innkeeper read as a game?" (disguised
  real-game controls, same calibration law). Known risk: `spawns` sits inside `_layout_hash`
  (scene_grid.py:158) → one-time Tier-2 art-cache invalidation, accepted.
- **W2 — "Walk"** *(engine half ✅ SHIPPED — PR #1341; UI half split → #1350, unblocked by #1303's
  glide PR #1345)*. New additive
  `walk_to` verb BESIDE move_to_coords (servers/engine/server.py:4583 — the combat gate stays
  untouched), reusing `combat_grid.shortest_path:221` via ONE shared blocked-set function (never
  fork pathing). Writes
  additive `Character.stage_cell`. Emits Action-Replay walk beats via /events
  (viewer/server.py:9181) so the Animator glides them. Walkmask click-to-move in rest mode
  (screen-combat.jsx pattern exists); door-cell click → `cross_door` walk-through. **Rule:** the
  renderer glides only engine-confirmed paths — no client prediction. **EVAL:** scripted
  click-walk replay (path legality, glide renders, text-tier identity).
- **W3 — "Talk"** *(engine+surface half ✅ SHIPPED — PR #1344; real seams: viewer read-model
  `build_parley_surface` ~viewer/server.py:6472 + engine `generate_parley_options(approach=)`;
  UI/staging half remains)*. Parley surface gains additive stage metadata (NPC stage
  cell, attitude); click-NPC → approach-to-talk (walk_to adjacent, then parley); dialogue rendered
  at the actor (2D reuses screen-dialogue.jsx). **EVAL:** blind panel + a behavioral check that
  the DM receives IDENTICAL parley moves as the text tier.
- **W4 — "The Living Stage"** *(coordinate with S8)*. `servers/engine/server.py:4039`
  (`start_combat`) additive param seeds combatant cells from `stage_cell` (default = today);
  `servers/engine/server.py:6976` (`end_combat`) writes survivors back; day/night from the
  campaign clock (surfaced per #1307). **EVAL gate = NO TELEPORT:** combat
  entry cells == last rest cells; exit cells == combat end cells. Full explore→talk→fight→loot→
  move-on loop playable.
- **W5 — "The Unity Player tier"** *(depends W1–W4; formalizes the render-delivery ruling)*.
  macOS player build of extensions/renderers/unity, launched beside the app (mirror
  native-bridge.js handoff); input = existing POST /move kinds ONLY. **EVAL = the T3 gate:** a
  blind AI playtester completes a quest loop IN the rendered surface (extend the GUI harness).

## 4d. THE A-SERIES — The Adventure Loop (the organizing spine from 2026-07-21; plan-approved)

> The question this series answers: the subsystems all exist and gate individually — rooms walk,
> the DM plays, panels score, the harvest promotes. The A-series composes them into ONE evaluated
> PLAYABLE LOOP (a Diablo-1-grade quest) and turns its eval into the routing instrument for
> everything else: **each cycle, the weakest dimension gets the next sprint.** S8 (Demo Assembly)
> is absorbed by this series — its gate becomes A-G's gate.

- **A0 — Compose** *(two units, parallel)*: `get_quests` full-read RPC (engine additive; get_state
  lists active only) + `qa/seed_adventure_demo.py` — the one-call fixture: camp ↔ tavern_snug
  (Keeper/giver) ↔ shop (merchant), camp ↔ crypt (goblins) ↔ throne_hall (Goblin Boss); add_quest
  4-objective arc; reward staged; full static stack at seed. Every room class is already
  walk-green certified — composition, zero new geometry.
- **A-T — The text-arc eval** *(parallel with A-G)*: `qa/run_adventure.sh` (duo-derivative,
  arc-directed persona, **20-beat budget** (raised from 15 on 2026-09-02 — a control run with the July DM model completed at beat 19 while 15 was knife-edge; the bar is unchanged: N≥3, completion ≥ 0.67, behavioral GREEN; DM and player model ids are pinned and recorded per row), completion short-circuit) + `qa/quest_progress.py`
  (per-beat get_quests polling → quest_trace.json: reached_giver / quest_accepted /
  entered_dungeon / boss_dead / reward_received / quest_completed) + `qa/adventure_eval.py`
  (N runs via the run_parallel pattern → completion_rate · beats/wall-time · stuck (dead beats +
  stage-gap outliers) · engagement · 3 lenses · behavioral gate → scores_db surface="adventure"
  + a WEAKEST-LINK verdict line). Ruler: new ac_-family config list per HV1's
  scoring_config_version rule.
- **A-G — The walked eval** *(parallel with A-T; absorbs S8's gate)*: `qa/adventure_walk.py`
  drives the SANDBOX player through the arc route on the :8972 channel (walk_test door-graph
  machinery + journey_eval VQA per stage + ui_playtest-style stuck/dead-click accounting), feeding
  the same aggregator (modality column). Prereq: ONE box build batching the #1616 T-pose
  registry-sync + any #76-adopted plates. Gate (from S8, upgraded): the full quest loop completes
  walked, in-app semantics, tri-state gates green, per-room backdrop scorecard PASS.
- **A2 — The flywheel protocol**: each autonomous run = 1 full adventure eval (N≥3 arc + ≥1
  walked) + 1 improvement cycle on the weakest dimension, then re-eval. The two-anchor panel
  ruler RATCHETS: when a flagship room is hand-elevated past the calibration reference, the
  reference upgrades and pulls the bulk tier on the next cycle. Variation breadth (bar variants,
  dungeon variants) = re-running Loop 0 (the room pipeline) per seed — the `library/` is the
  accumulation of gate-passed artifacts (HV3 promote is its sole writer).
- **A3 — Proceduralization gate**: only when the A-eval holds green across N seeds do we
  parameterize — adventure templates × universe skins (the DM pulls a world), background
  generation on library cache-miss via the StreamingAssets HOT-LOAD mechanism (camOrtho-proven;
  box builds are ship-time only) with the never-T-pose floor as the immediate stand-in.
  Proceduralizing an ungated loop generates infinite mediocrity; gating first generates infinite
  shippable.

## 4c. THE HV-SERIES — The Harvest Loop (Act II; the flywheel)

> The mechanism: every scored QA run is ALSO a harvest candidate — no new run types. Content flows
> snapshot → extract → eval-gated promote → `library/` → reused by world-gen/questgen → measured
> as a trend ("less AI dependence" becomes a number). All promotion is eval-gated; the content
> analogue of the real-art-control law is **disguised hand-authored canon as panel controls**.

- **HV1 — "Artifact Evals FIRST"** *(✅ SHIPPED — PR #1331; instrument DISCRIMINATES: canon controls
  2.9–4.2 vs thin extract 2.05)*. `qa/artifact_score.py` + per-class rubrics
  (quest / npc-or-villain / location / encounter; plates already have visual-critic). Controls =
  hand-authored canon (world.json quest_variants / npc_roster dossiers / wiki-canon areas) pushed
  through the SAME artifact schema. Storage: additive `artifacts` TABLE in qa/scores.db — new
  ruler family `ac_…` via a NEW file-list in scoring_config_version.py (NEVER append to
  SCORING_CONFIG_FILES — that silently re-versions `sc_`). Ships a thin snapshot reader so it runs
  on EXISTING finished campaigns immediately.
- **HV2 — "Extract"** *(✅ SHIPPED — PR #1329; 165 artifacts extracted from 4 campaigns; its merged
  schema is canonical)*. *(original spec: first commit = the schema handshake
  `data/library/artifact_schema.json`)*. `qa/export_campaign_artifacts.py` (sibling of
  export_scene_grid.py; reuses distill.py's transcript reader for dialogue snippets + attitude
  arcs) → `qa/artifacts_out/<campaign>/{quests,npcs,locations,encounters}/*.json` with provenance
  {campaign_id, run_id, world, sha, scores}. Strictly read-only on play-state.
- **HV3 — "Promote"** *(✅ SHIPPED — PR #1338; first live promotion batch in flight)*. `tools/library/promote.py`: nominations
  (qa/nominations.jsonl) → artifact panels → threshold gate (overall ≥4.0, no dim <3.0,
  control-valid → `stable`; `canonical` = human curation only) → **`library/`** (pack-shaped,
  #644-forward-compatible: pack.json {name, version, license, provenance} +
  quests/npcs/locations/encounters/rooms). Rooms UNIFY the proto-library: entries REFERENCE
  room_recipes keys + registry asset_ids — promote.py NEVER edits either. promote.py = sole
  writer of library/; a library-lint (no unscored stable entries; provenance+license required).
  **Bootstrap ruling (nomination-queue circular dep):** nothing upstream of HV3 produces
  `qa/nominations.jsonl` — HV5 (#1327, `qa/closeout.py` auto-nominator) does, and it depends ON
  HV3. For the FIRST batch the queue is hand-authored (one JSON line per `artifact_id`, sourced
  from HV2's `qa/artifacts_out/<campaign>/**/*.json`); promote.py invents NO nomination heuristic —
  that logic lives solely in HV5's closeout auto-nominator. See docs/OPERATIONS.md "HV3 promotion".
- **HV4 — "Reuse"** *(needs HV3)*. questgen._derive_hooks gains a library candidate source
  (tier-weighted, DEFAULT-OFF — default seed path stays byte-identical, guarded by the existing
  test_seed_world_default_is_unchanged test); world.json additive `library_packs:[...]`; new
  engine tool `lookup_library` (mirrors the wiki-first canon pattern); library rooms ship as
  registry aliases (zero renderer edits by contract). **EVAL:** A/B duo library-first vs pure-gen
  — lens parity-or-better + latency/token reduction + feature_engagement confirms library content
  is ENGAGED, not decorative.
- **HV5 — "Flywheel ops"** *(slice 1 ✅ SHIPPED — PR #1342: closeout auto-nomination via
  qa/nominate.py; nightly batch scoring, weekly curation, library_metrics + backdrop cadence
  remain)*. qa/closeout.py auto-NOMINATES artifacts from every
  scored run (story threshold = STORY_BAR, qa/closeout.py, currently 4.3; quest completed; NPC
  turn floor N=3) — artifact scoring runs in a
  nightly batch, never inline (duo latency untouched). Weekly curation batch. **Backdrop cadence:
  2 environments a night, panel-gated, weekly curation → ~100 environments in ~10 weeks** on the
  proven GEX44 pipeline. `library_metrics` table (size by class/tier, Σreuse_count, promotion
  pass-rate, %library-sourced beats) — the flywheel's own eval: the "less AI dependence" trend.

**Act II additive-invariant register** (what a skeptic refutes per stage): HV1 `sc_`/`lc_` hashes
unchanged · HV2 zero writes under play-state · HV3 room_recipes/registry byte-identical ·
HV4 default seed path identical with library_packs absent · HV5 closeout append-only + duo
wall-clock unchanged · every W sprint: text-tier byte-identity + no new writers.

**Act II sequencing:** NOW: W1 ∥ W2-engine ∥ HV1 ∥ HV2 (schema handshake first) → HV3 →
HV5-hooks + backdrop cadence → W3 → HV4 (+A/B) → W4 (with S8) → W5 → **T3 gate**.

## 5. The platform track (parallel, after S7)
1. **#911 multi-agent plugin** — GA slice = docs + Claude lane verified (S10); Codex CLI lane
   next (the cheapest second runtime), then OpenClaw/Hermes post-GA.
2. **Milestone 25 hosted runtime** — strictly behind #1122 security triage; the
   runtime-host decision (#706) is a first-principles doc. Economics note: hosted sessions pay
   the DM cost-per-beat centrally — the bring-your-own-agent default (#911) is the hedge; a
   hosted tier prices against measured $/beat from the ledger.
3. **Milestone 26 creator + showcase** — post-GA; #711 licensing policy is an owner/legal gate.
4. **#32 Discord / #31 multiplayer** — vNext after hosted runtime proves session economics.

## 6. ACT III — The Universe Platform (post-GA/post-Act-II trajectory)
- **"Any Agent, Anywhere":** all #911 lanes + hosted runtime + Discord distribution.
- **"The Universe System":** creator (#713), template packs (#644 — **the HV library IS the pack
  content**: pack.json is the HV3 schema shipped externally), licensed universes v2.1–v2.4
  (#330–#332: KOTOR-class recreations run on the same engine + a themed library), fan showcase
  policy (#711), paid-alpha original world (#712).
- **"Engine as Platform":** release the engine so others plug in their own renderers (the
  pure-consumer surface contract makes Unity/RPG-Maker/2D plugs identical in shape); the 2D
  pixel-art tier (#1145) graduates from filed option to the reference third-party-style renderer.
- The old "Phase C — Rendered Game" (#645) is **DELIVERED BY the W-series** — #645 closes as
  superseded when W5's T3 gate passes. #1045 Unreal fidelity tier stays deferred until the Unity
  path caps.

## 7. Owner Gate Register (human-gated items — clear these AHEAD of need)
| Item | Needed by | What the owner must do |
|---|---|---|
| Apple Developer cert + notarization creds | S10 start | provision cert into Keychain (#151) |
| Sparkle signing key | S10 start | generate/store key (#134) |
| Licensing: Owlcat/OpenWorlds reference assets | before S8 art ships | #122 audit sign-off |
| Fan-showcase / user-content policy | milestone 26 | #711 policy call |
| Asset-Store purchases (if S2/S9 need packs) | as flagged | purchase + import to box project |
| GPU-box capacity/renewal | continuous | GEX44 lease + Unity seat |
| Paid-alpha pricing | Phase B | business call (#712) |

## 8. Standing gaps register (ALL FILED — the numbers)
**#1302** render-delivery DECISION (S2-entry blocker) · **#1303** Action-Replay→Animator/VFX
wiring (incl. death topple/fade) · **#1304** L7 motion-reel first run · **#1305** monster wave 2 +
ghoul clips · **#1306** demo dungeon authoring (room-unit graph) · **#1307** `time_of_day` on
`/combat-surface` · **#1308** run-level duo resume (constructive half of #1285).
**Charters live:** **#1309** (S2 — The Felt Demo Loop, incl. the FELT control-anchored gate) ·
**#1310** (S3 — Combat Readability, incl. the confusion-bug taxonomy). Later charters (S4+) are
authored from §4 when their predecessor's gate passes — same template.

## 9. ★ DEMO COMPLETION — THE GOVERNING MILESTONE (owner-set 2026-07-22)

**The milestone:** the owner plays "The Crypt Below" (adventure_demo_v1) end-to-end in the WorldOS
player, unassisted: camp hub → Keeper Maera (visible, quest accepted) → crypt (visible goblins,
combat runs AND CLOSES with XP) → throne (visible boss, fight completes) → return → reward →
quest_completed — with ZERO user-truth defects. Demo completion proves the system can build the rest.

**Proven by four gates (checkable, never narrative):**
- G1 — the certification gates that EXIST run green against the INSTALLED build, verified by
  build identity: the app self-reports its build stamp via the /app-status contract (WorldOS-GUI-RUNBOOK §app-status; viewer/server.py), and the gate evidence records the
  SAME stamp — a mismatch is a G1 FAIL (the certified-build ≠ installed-build class, #1651).
  Today that means walk_static (CI) + the paint-coherence gate + the A-T/A-G evals run against the
  installed pair; G1 UPGRADES to the full `player_cert` suite when §9.2 lands (a proof clause may
  only reference gates that exist).
- G2 — arc-duo eval: completion at bar with behavioral GREEN (surface=adventure, av_ ruler).
- G3 — walked-arc eval GREEN over the FULL arc route INCLUDING the return-for-reward leg back to
  the giver (navigation + cast presence + VQA stages at every leg).
- G4 — owner playthrough observes ZERO user-truth defects of ANY severity (walk-through, invisible
  actor, dead door, spawn-in-furniture, stuck UI panel) and files zero new P1s of any class (the
  residual unknown-unknowns absorber).

### 9.1 Demo-critical path (dependency-ordered; ⊘ = independent of the pipeline fork)
1. ⊘ #1645 combat lifecycle (M) — DM closes fights (action economy, end_combat, XP, time-advance).
2. ⊘ #1639 cast presence (M) — rest surface emits NPC + live-monster tokens; client renders them.
3. ⊘ #1522 parley-panel lifecycle (S) — CloseParley() from the location-change path (sits on the
   demo's FIRST beat: Maera parley → door-cross). UI/panel lifecycle is a NAMED demo property.
4. ⊘ #1647 wave 1 (S-M) — coherence-aware spawns/arrivals (instrument merged; relocation in
   flight); silhouette fix + door hotspots (#1649); #1584 spawn test wired into CI.
5. ⊘ ONE box build carrying the client fixes → sandbox gates → owner install (install gate = §9.2).
6. Camp HUB (fork-dependent): regen geometry is GREEN; ships as greybox-composite / #1642-lit /
   3D-first per the spike outcome.
7. ⊘ #1642 alive plates (M) — normal pass + light composite (batch with build 5 when ready).
8. #83 THE 3D SPIKE — decides room construction FORWARD; not demo-blocking. SPIKE EXIT CRITERIA
   (red-team): per-actor silhouette-per-submesh + spawn-centroid assertions over the NEW crypt
   roster — the 3D re-author must not reintroduce either decayed class.

### 9.2 THE HARNESS SYSTEM (the enforcement redesign, red-team-amended)
- **`qa/player_cert`** — CHARTERED WORK (L), not an aspiration: fold walk_test + adventure_walk +
  journey_eval + the user-truth stages + a combat-lifecycle probe into ONE tri-state command.
  SPLIT (red-team F1): a CI-RUNNABLE static/headless half (every PR) and a BOX-HOSTED live half
  with a NAMED trigger — scheduled box session + owning runbook step + a version-stamp the owner
  app self-reports on launch, diffed against the latest cert run (drift is loud, not silent).
  ROSTER-COMPLETE (F2): live properties iterate the FULL actor roster; any roster addition is a
  trigger event re-running the applicable property set against the new member.
  BUILD ORDER (red-team sequencing): the two shared assertion PRIMITIVES first — silhouette-per-
  submesh and spawn-centroid — consumed by the #83 spike immediately, aggregated by player_cert.
- **`qa/features.json` + lint** — the EXECUTABLE feature registry: every shipped capability row
  binds to a gate assertion id; CI fails on unbound rows or orphan gates. Docs inform; only red
  CI enforces (archaeology case 6: a written runbook rule recurred anyway).
- **Known-hole SLA** — player-feelable holes cannot defer past the next box build; every deferral
  names the gate that guards it meanwhile.
- **Rebuild-not-patch** — rooms failing registration/coherence are REGENERATED through the current
  chain, never hand-patched; the cert-required lint (#1644) keeps retired plates unshippable.

### 9.3 Ruler discipline (restated)
Two-anchor calibrated panels; av_ ruler for adventure aggregates; blind adjudication wherever an
author would judge their own work; honest negatives are progress and get scorecard rows.
