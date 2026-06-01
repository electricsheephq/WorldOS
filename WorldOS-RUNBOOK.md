# WorldOS — RUNBOOK (READ FIRST on resume)

> **Before anything: confirm you are in the repo root and know the right UI.**
> The current product UI is OpenWorlds at `/openworlds/`; the root dashboard is
> legacy. This runbook is the public project map + read order. Machine-specific
> agent notes such as `CLAUDE.md` are intentionally local-only and gitignored.
>
> **Takeover routing, 2026-06-01:** current release/gate state lives in
> `WorldOS-OPERATING-GOAL.md` first, then `WorldOS-GUI-RUNBOOK.md`, then `qa/SCORECARD.md`.
> The work queue later in this file is historical unless it agrees with those sources.
> **Local/VM routing, 2026-06-01:** `/Users/lume/ClawDnD-val` is the synced local app/private-art
> checkout and should be used for GUI/native-app testing. Use `/Volumes/LEXAR/Codex` for evidence,
> snapshots, and logs; do not make Lexar the default GUI runtime tree because external-drive
> permissions can break local AI/browser tests. Heavy backend/persona sweeps belong on GitHub CI or
> the owner-provided 32GB support VM (`support-vm-1`) after remote access and Codex config are
> intentionally installed and verified; connection details are kept outside tracked docs. A read-only
> scout reached the operator endpoint and found `evaos-support` suitable but stale (`4524b3e`, behind
> the `9545383` proof baseline) with Codex auth/config unproven. Mac-only built-app proof remains local/macOS CI.

> **This is the compaction-resilience doc.** If you are an agent resuming this project
> after a context reset, read this top-to-bottom before doing anything. It captures the
> project, architecture, the load-bearing invariants you must not violate, the exact dev
> + QA loops, how to delegate, the hard-won lessons, and the current state + work queue.
>
> The QA results ledger is `qa/SCORECARD.md`; the scoring spec is `qa/SCORING.md`.
> If an operator hands you local session notes or decision records, treat them as
> private working artifacts unless they are intentionally promoted into tracked docs.
>
> Last updated: 2026-06-01T17:07:00+07:00 (`9545383` is the latest same-SHA app-proof build; release notes below are historical context).
>
> **Graphics & game-types roadmap (canonical):** the long-term plan for the kinds of games
> WorldOS can produce (GT0 narrative dashboard → GT1 SNES pixel → GT2 Pillars/BG isometric)
> and the capabilities (C1–C10) that mature Branch A → Branch B lives in
> [`docs/roadmap/WORLDOS-GRAPHICS-ROADMAP.md`](docs/roadmap/WORLDOS-GRAPHICS-ROADMAP.md) —
> the single source of truth, executed via GitHub Milestones + Issues. Invariant: the
> renderer is always a thin client; the engine stays the sole writer of state.
>
> **v1.0.1 ([Release](https://github.com/electricsheephq/WorldOS/releases/tag/v1.0.1)):** Phase-4 action lanes complete — Merchant BUY and Forge Craft now relay structured `/move` intents during live play (Create was already wired to the bridge). The seven BG3 origin heroes carry full `companion_dossier` blocks (wound / wants / fears / values / approval / banter / camp prompts). Native-app reliability: build script prefers stable Developer ID signing when keychain ACL allows; `script/unblock_native_app.sh` is a one-shot helper for security-scanner re-evaluation hangs. `docs/SPARKLE_SETUP.md` is the 7-step path to auto-update on top of Developer ID. Engine **1385/1385 ✓**, viewer **90/90 ✓**, license-check clean. **Open gap (owner-only):** first-run Keychain "Always Allow" click on a Developer-ID-signed app to end "popup every rebuild" — or run `script/unblock_native_app.sh` once.

---

## PROJECT

**WorldOS** — a post-Baldur's-Gate-3, **living-world D&D 5e Claude Code plugin**. You don't
play *against* the AI; you adventure *with* it. An AI Dungeon Master narrates and voices
every NPC; a voiced AI companion adventures alongside you with its own sheet and agency.

- **North star: epic, mature, Baldur's-Gate-caliber STORY** sitting on a **deterministic
  SRD 5.2 engine**. Dice and rules are never hallucinated; the story is generated live.
- **Goal: a universe-system that generates worlds.** Reverse-engineer how BG3 / Skyrim /
  Kingmaker structure story → a seed/engine that can spin up new lore-grounded worlds. A
  2nd world is meant to be near-free once the system is perfected on the first.
- **Source-available commercial product.** BG3 ingestion is **INTERNAL-ONLY** — we ship a **wiki-INDEX + a
  self-serve ingestor**, and distribute **nothing copyrighted**. (Owner steer: P0
  the public `baldurs-gate/` world is unofficial Fan Content, never sold.)
- **BG-ONLY focus.** Baldur's Gate is THE world. Sundered Reach is a deprioritized
  side-option (left in place, no investment). All content + QA target BG. Perfect the
  whole system on BG.
- Code uses the root **WorldOS Source-Available Commercial EULA**; rules data is **CC-BY-4.0 SRD 5.2.1**; universe seeds are unofficial
  free Fan Content with their own `LICENSE.md`.

---

## ARCHITECTURE + FILE MAP

**Three Python MCP servers** (run with `uv`), an AI DM brain (Claude), and a viewer app.

| MCP server | Dir | Role |
|---|---|---|
| `clawdnd-engine` | `servers/engine/` | Authoritative game state: dice, sheets, combat, conditions, XP/leveling, encounters, persistence. **Sole writer of campaign truth.** |
| `clawdnd-rules` | `servers/rules/` | SRD 5.2.1 rules lookup (offline; `dnd5eapi.co` fallback). |
| `clawdnd-voice` | `servers/voice/` | TTS behind a swappable `TtsBackend` (Kokoro default; null backend in QA). |
| `clawdnd-player` | `servers/engine/player_server.py` | **The constrained move FACADE.** An actor acts ONLY through this limited, READ-ONLY-on-state surface; it can't narrate the world or assert outcomes. Parameterized by `CLAWDND_ACTOR_ID` / `CLAWDND_ACTOR_ROLE` so the same surface drives the player or any companion peer agent. |

> **NOTE / spec correction:** there is **no `servers/player/` directory**. The player
> facade is the file `servers/engine/player_server.py`, exposed as the `clawdnd-player`
> MCP server. `.mcp.json` registers the 3 plugin servers (engine/rules/voice); the player
> facade is wired per-run by the QA harness.

### Key engine modules (`servers/engine/`)

| File | What lives here |
|---|---|
| `models.py` | All Pydantic models. `_StrictModel` (`extra="forbid"`) base; `Character`, `Quest`, `Faction`, `CompanionArc/Agenda`, `ArcGate`, `CompanionQuestArc`, `WorldState`, `Campaign`, `SceneDebt`, `Event/ParleyOption/Outcome` (Quest-Arc L3), `PendingOnHitRider`, etc. The contract surface — **additive-only**. |
| `server.py` | The big one (~300KB). All engine MCP tools (`start_world`, `start_combat`, `attack`, `cast_spell`, `next_turn`, `add_quest`, `get_campaign_director`, `roll`, …). |
| `store.py` | **Sole-writer persistence.** `campaign_lock()` (fcntl), `_atomic_write` (tmp + `os.replace`), `save_campaign`, `load_campaign` (with the #165 **tolerant load**: drops unknown TOP-LEVEL keys so old/new snapshots round-trip; sub-model strictness preserved). |
| `player_server.py` | The constrained move facade (the `clawdnd-player` MCP) — see above. |
| `combat.py` | Action economy, attack-vs-AC, damage, conditions, the Multiattack enforcement (#181), turn-skip guard. |
| `companion_arc.py` | The ONE engine-enforced arc system: betrayal/agenda rolls off the `attitude_value` gauge; `CompanionAgenda.decision_flag` (Quest-Arc L2). The reuse template for faction arcs. |
| `companion.py` / `companion_banter.py` | Companion sheets, dossiers, banter. |
| `director.py` + `scene_debt.py` | **Campaign Director (#72):** advisory layer telling the DM what the campaign OWES each beat (scene-debt taxonomy: `hook_untracked`, `quest_stalled`, `npc_introduced_silent`, `thread_no_payoff`, …). Advise-not-dictate; the engine never acts on a debt. |
| `content.py` | World/seed loading, `_resolve_quest_variants` (ending-tied `when:{fact}` + seeded weighted-random `random:<weight>`), ending overlays. |
| `worldsim.py` | `tick()` — standing threads move on their own; `BacklogItem.effect` + `_apply_structured_effect` ripple path. |
| `wander.py` | Typed multi-resolution wandering encounters (combat/skill/social/hazard/boon; ~60% non-combat) staged on travel/camp; folds in `encounter_outlook`. |
| `encounter.py` | CR→XP SIZING math + `_outlook_for_xps` (`must_offer_out` doctrine). |
| `consequences.py` | `schedule()` — scheduled `Consequence`s (rule-of-three callbacks, #185). |
| `questgen.py` / `generator.py` | Hook assembly at seed (deliberately one-shot, rejects re-triggering); campaign generation. |
| `bestiary.py` / `itemcatalog.py` / `lorebook.py` / `srd_tables.py` | Auto-discovered content data layers. |
| `dice.py` | Full notation + the `_MAX_DICE`/`_MAX_SIDES` DoS clamp (#169). |
| `rests.py`, `inventory.py`, `npc.py`, `recap.py`, `travel.py`, `spells.py`, `ledger.py`, `imagegen.py`, `openclaw_image.py` | Supporting subsystems. |

### Other surfaces

- **`viewer/`** — the local dashboard/director's-view (`server.py`, `dashboard.html`,
  `monitor.html`). **`viewer/openworlds/`** — the playable React CRPG app (28 screens:
  `screen-map/combat/camp/dialogue/merchant/forge/bestiary/acts/relations/journal/…`,
  `app.jsx`, `data.js`, `native-bridge.js`). Renders on **live engine read-models** (#161
  wired). **The macOS/OpenWorlds Swift shell is the SIBLING lane — see "Don't collide".**
- **`qa/`** — the QA harness (see THE QA LOOP).
- **`content/worlds/baldurs-gate/`** — the world: `world.json` (regions, factions, cast,
  history, standing threads, `quest_variants`), `areas/`, `characters/`, `endings/`,
  `lore/`, `origins/`, `LICENSE.md`.
- **`skills/`** (`dungeon-master`, `companion`, `campaign-author`, `world-author`),
  **`agents/`** (`companion-agent.md`), **`commands/`** (player slash commands),
  **`data/srd/`** (SRD 5.2.1), **`tools/ingest/`** (wiki → lore corpus).

---

## INVARIANTS (load-bearing — do not violate)

These are the rules that keep the engine deterministic and crash-/compaction-safe. Every
change must respect them.

1. **The engine is the SOLE WRITER of campaign truth.** Campaign state lives on disk as
   `snapshot.json`, written under `campaign_lock` via an **atomic** temp-file + `os.replace`.
   Nothing else mutates state. The player facade (`player_server.py`) is **READ-ONLY** on
   state — it only appends structured *moves* for the DM to resolve.
2. **Additive-by-default.** Empty == today. Old snapshots must round-trip. Models use
   `_StrictModel` (`extra="forbid"`); the #165 **tolerant load** drops only *unknown
   top-level* keys (sub-model strictness intact) so a future non-additive schema change
   can't brick old saves. Every new field defaults to "behaves like today when unset."
   Each feature must be independently removable; low blast radius.
3. **Gates/triggers read ONLY engine-MUTATED values — NEVER fiction.** A gate or trigger
   may key off `flags`, `reputation`, `attitude_value`, `day`, `standing` — values the
   engine itself sets. The engine **cannot judge prose** and must never monitor near-
   constant fiction. (This is *the* constraint from `questgen.py`. It's why quest CONTENT
   stays DM-advisory and only gauge-backed things get engine teeth.)
4. **QA uses null voice / null image and NEVER the Eva / OpenClaw gateway-by-accident.**
   The QA harness runs gateway-free `claude -p` (or a *scoped* gpt-5.4 OpenClaw path with
   isolated `clawdnd-qa*` agents). **NEVER touch Eva** (the owner's live agent): don't
   restart/reconfigure the gateway, don't touch agents `main`/`operations`, no
   `doctor --fix`, no global `mcp set`.
5. **Engine rolls the probability; the DM is TOLD the result.** Wander/betrayal/variant
   rolls happen *in-engine* and surface in the tool return; the DM narrates + routes.
   The DM never rolls dice in its head. "Probability proposes, DM/lore disposes."

---

## THE DEV LOOP (exact)

> **Test-execution policy:** prefer GitHub CI for broad validation. For local
> development, run focused tests first and keep Python test runs single-process unless
> you have explicitly verified your machine can handle parallel workers.

1. **Branch off main in a fresh worktree** (keeps lanes disjoint from the app-testing checkout).
   For GUI/native-app work, prefer same-disk local worktrees under `/Users/lume/WorldOS-worktrees`
   so private-art reads stay on the local disk. Lexar worktrees are fine for docs/backend/non-GUI
   slices that do not launch the app against art. Implement **additively** (honor every invariant above).
2. **Run focused local tests single-process:**
   ```bash
   uv run --directory servers/engine python -m pytest <relpath> -q -p no:xdist
   ```
   `-p no:xdist` (or simply never passing `-n`) is mandatory. Run the focused test file(s)
   for the change; run the full suite (`servers/engine/tests`) before merge.
   - **Warm the venv first** on a fresh worktree (`uv sync` or a `uv run python -c pass`)
     — a cold-start `.venv` race once produced a phantom "Extra inputs not permitted"
     mechanical failure mid-run. The QA runners warm it; do it manually for ad-hoc tests.
3. **Push**, then create the PR:
   ```bash
   gh pr create --title "…" --body "$(cat <<'EOF'
   …
   EOF
   )"
   ```
   **DO NOT pipe `gh pr create` through `tail` inside an `&&` chain** — a transient
   GraphQL blip gets masked, the branch ends up pushed-but-unmerged, and the merge is
   silently skipped. (This bit us on #185.) Check the exit / the returned PR URL.
4. **Merge only after checks pass.** Use the standard PR merge flow once GitHub CI
   and required review gates are green.
5. **Sync + clean up:**
   ```bash
   git pull --ff-only origin main
   git worktree remove --force <worktree>
   git branch -D <branch>
   git worktree prune
   ```

**The whole shape:** worktree off main → implement additive → focused single-process test →
push → `gh pr create` (no `tail` in an `&&` chain) → merge after checks pass →
`git pull --ff-only origin main` → remove worktree + delete branch + prune.

---

## THE QA LOOP

The fitness function = **1 hard behavioral gate** + **3 LLM lenses**. Spec: `qa/SCORING.md`.
**Log every run to `qa/SCORECARD.md`** (the ledger that survives compaction).

**Runners:**
- `qa/run_duo.sh <run> <world> <persona> [beats] [budget]` — AI player + DM duo via
  `claude -p` (gateway-free). Threaded/cached: `--session-id` on beat 1, `--resume` after,
  re-grounding from snapshot each beat (anti-mush). The player gets ONLY the `clawdnd-player`
  facade; the DM gets the full engine+rules+voice (null backends) + the dungeon-master skill.
- `qa/run_combat_sprint.sh <run>` — **the fast BUG-FINDER.** ~1.5–2 min: pre-seeds a fight
  (zero LLM) → ONE DM call for a 3-round combat → behavioral-gate → Angry-DM score.
- `qa/run_duo_openclaw.sh` — the same duo via **gpt-5.4** (OpenClaw gateway, off the claude
  quota; scoped `clawdnd-qa*` agents only; needs `--thinking low`).
- `qa/run_party.sh` — player + up to 3 companion peer AGENTS + DM (exercises recruit/banter/
  the betrayal path; restore this to the cadence to feel-validate Quest-Arc L2).
- `qa/run_parallel.sh` — 2–3 isolated concurrent runs (the velocity model; 2 `claude -p` is fine).

**Scoring — three lenses (1–5 each) + the gate:**
- **Behavioral gate** (`qa/assert_behavioral.py`) — deterministic pass/fail. FATAL on:
  dead/non-progressing scene, world-progression floor (≥6-beat runs: clock advanced +
  ≥2 locations), player narrating the world (facade over-write), silent companion,
  unresolved player `[cast]/[attack]/[check]/[save]`, combat left active / stray monsters /
  dangling conditions. **RED ⇒ all three lenses capped to ≤2.5 / INVALID.**
- **Mechanical** (`rubric.md`) — DM tool-stream vs correctness; hallucinated mechanics are
  the worst defect.
- **Story-craft / "The Loremaster's Eye" (Tolkien)** (`rubric_tolkien.md`) — stingy +
  act-relative; BG3-calibrated. Reads the two-sided play log.
- **5e-fidelity / "The Angry DM"** (`rubric_angry_dm.md`) — adversarial SRD 5.2.1 checklist
  (d20 tests, ~15 action types, all 14 conditions). Reads the DM tool-stream + a behavioral
  scoped-B gate.
- Scorers: `qa/score.sh` (claude — the PRIMARY baseline) or `qa/score_openclaw.sh` (gpt-5.4,
  grades **~1.5 pts HARSHER** — a strict cross-check, NOT the headline).

**Targets (the loop's exit bar):** **story ≥ 4.3, mechanical ≥ 4.5, gate GREEN, 0
critical/high** adversarial defects.

**How to read the lenses (hard-won):**
- The **combat-sprint is a fast BUG-FINDER**, not a score-maximizer. It surfaced **real
  engine bugs** the surfacing work had masked: monster Multiattack (#181), the Round-1
  turn-skip (#183), Guiding-Bolt-on-cast-not-hit (#188). Use it to *find* defects.
- **A single sprint's Angry-DM is COVERAGE-CAPPED (~3)** — one vanilla 3-round fight can't
  exercise the whole 5e surface. The **score climbs via BROADER play + a richer seed**
  (saves/conditions/subclasses/run-to-resolution), not by re-running one short fight.
- **Low mech/Angry-DM on emergent duos is usually a SAMPLING artifact, not an engine
  defect** — both AI player and DM drift to roleplay, so combat is rarely formally run.
  The wandering-encounter system + combat-seeking personas force real fights.

---

## AGENT DELEGATION

Orchestrate via subagents; verify from the top.

1. **Create a fresh worktree** off main for the agent's lane.
2. **Spawn an agent** (Agent tool / `claude -p`) with: a **precise spec**, the
   **single-process-test guardrail** (`-p no:xdist`, never `-n` unless the lane explicitly supports it), and the
   directive **"flag-don't-force-fix if many tests break"** (don't let an agent weaken
   assertions or paper over a real regression to make a suite go green).
3. **Review the diff** — *trust BUT verify*. Read what the agent actually changed; confirm
   it's additive, honors the invariants, and didn't weaken a guardrail test.
4. **Merge** via the dev loop.

**Host-load rule:** API-backed agents (Agent tool, gpt-5.4) **don't strain the host** — fan
out freely. Only **`claude -p` QA is host-heavy** (the duo/sprint spin up engine processes);
**2 concurrent `claude -p` runs is fine**, more risks OOM. Reap orphaned `player_server` /
`server.py` processes between runs.

---

## DISCIPLINE / LESSONS (the expensive ones)

- **Validate BEFORE fixing.** The LLM scorer mis-attributes root cause. The "STR-18 → +5
  attack bug" was a **FALSE alarm** — engine smoke confirmed melee **+6** (mod 4 + prof 2);
  the real issue was DM adherence, not the engine. Reproduce against the engine before you
  "fix" anything a scorer flagged.
- **Surfacing info ≠ the DM using it (the reach-for lesson).** Adding a tool/field the DM
  *could* call doesn't mean it *will*. Two reliable fixes: (a) **fold the value into a
  trigger the DM already hits every turn** (e.g. `turn_brief` on `next_turn` vs only at
  `start_combat`; the Director consulted at beat start → `add_quest` went 0→3), or (b)
  **ENFORCE it in the engine** (the Multiattack economy fix #181; the turn-skip block #183).
- **The combat-sprint → engine-fix → re-measure loop WORKS.** It's how Multiattack,
  turn-skip, and Guiding-Bolt were each isolated and the Angry-DM score moved (2.8 → 3.3
  after #181). Use it deliberately.
- **First-principles for load-bearing decisions.** A public contract / schema / tool API /
  concurrency change with real trade-offs = run the research/decision loop and write a
  decision record, not speculative code.
- **Don't reflexively import a gate from one code path into another** — ask "in which
  domain is this concern real?" first (e.g. apply_damage is *legit* for traps/poison, so a
  blanket "reject apply_damage" rule was wrong).
- **REUSE before rebuild.** The Quest-Arc engine reused the companion stage-machine; #143
  variants reused the shipped `_resolve_quest_variants` resolver. The engine is usually
  ~80–90% there — find the existing primitive.
- **Demo-leak: verify with a repo-wide GREP, not per-screen spot-checks.** "I previewed
  screen X and it looked clean" is a WEAK assertion — a screen's LIVE read-model path can be
  clean while its FALLBACK / prototype data (shown on an empty campaign or a non-wired tab)
  still leaks. The runbook twice over-claimed "zero PF leak" off live spot-checks; a
  `grep -rniE "linzi|cassian|oleg|stag lord|stolen marches|kingmaker|pathfinder…"` over
  `viewer/openworlds/*.{jsx,js}` then found the Kingmaker demo hardcoded in EIGHT screens
  (data.js, bestiary, forge, acts, camp-sidebar, chrome, dialogue, relations, inventory).
  The honest fix everywhere: gate to the live `/*-surface` read-model with a BG-neutral
  empty-state; never a demo fallback; never invent BG content. Grep is the invariant scan.
- **Multi-process UI-wiring bugs: RUN/instrument, don't theorize.** Chasing "why didn't the
  WebView repoint" by reasoning (timing? Swift @State? a 302?) went in circles; the answers
  came from running it — `curl /session-surface` (can_act=true on the live campaign proved
  the engine path), killing the app's read-only viewer to learn which port the WebView was
  on, reading the live state dir. The true root (a campaign-SELECTION race) was none of the
  first three hypotheses. (= the first-principles skill's "Step 1.5 — run the system".)

---

## CURRENT STATE + WORK QUEUE

**Historical snapshot, not current authority:** this queue was written around `ea815fc`
(2026-05-27 cont.3). During the 2026-05-31 takeover, the gate-truth stabilization merged as PR #465,
the UX-first doc sync merged as PR #468, and first-minute click/title chrome proof merged as PR #470.
Local routing sync merged as PR #471, native provider-selection sync merged as PR #472, takeover
state docs synced as PR #473, Codex-DM app observability merged as PR #475, scripted smoke provider merged
as PR #494, stable agent UI hooks merged as PR #495, failure-bucket/RRI split metadata merged as PR #496,
and takeover truth sync merged as PR #498, followed by PR #499 recording current-main built-app proof
PR #500 fixing Codex-DM provider trace cancellations, PR #501 recording that proof in docs, and
PR #504 adding the 100/100 hybrid app handoff gate, PR #505 adding the RRI `--handoff-json`
bridge for Mac app proof, PR #506 syncing docs to that proof, and PR #508 adding the support-VM
preflight artifact gate.
The local app/private-art checkout should stay fast-forwarded to `origin/main`; the only current gate
truth lives in `WorldOS-OPERATING-GOAL.md` + `WorldOS-GUI-RUNBOOK.md` + `qa/SCORECARD.md`. Do not use
this section to decide release state. The next sprint is UX-first (#467):
fast handoff play is proven diagnostically on `9545383`, including private art, Codex DM, an active player,
five enabled actions, one accepted/resolved `/move`, no evidence-manifest gaps, zero failed/error provider
trace events, and a post-merge `handoff_score=100`. With #479 proven and #504/#505 merged, run #466 only after
support-VM routing/auth/config preflight is explicit; this session's read-only check found the local
`support-vm-1` SSH alias did not resolve and the operator-endpoint VM checkout was stale at `4524b3e`.
Then prioritize clickability/chrome, launcher clarity,
live-response feel, and CRPG depth before more hardening/proxy/security work.

**LATEST (2026-05-27 cont.3) — the Quest & Arc engine is COMPLETE + WIRED, all combat
defects closed, and the sibling's draft-PR backlog is fully landed.** Since the queue
below was written: L3 events (#196), faction arcs (#205), the DM-wiring (#203), the North
Star doc (#206), the full combat-fidelity wave (#207/#209/#210/#211/#213→**#215** maneuver
die — all 6 flagged combat defects CLOSED), canon content-fill (**#216** — 4 stumble-into
Events + 2 faction arcs + 2 decision-gated agendas), and **all 10 sibling roadmap-squeeze
PRs** (#190/#192/#199/#191/#197 engine + #204/#201/#202/#198/#187 viewer) merged after a
2-agent read-only triage. **Combat-sprint at a new high: angry-dm 3.7** (combat core "clean
— every number traces to a tool call"); residual is DM adherence (monster reactions #218)
+ narration nits (#219), not engine. distill now surfaces auto-fired repeat-saves +
maneuver damage (#217). IN FLIGHT: a post-content-fill story-lift duo (does story clear
4.3?) + the **2nd-seed generativity spike** (the North Star deliverable-B gate — a thin
ORIGINAL non-BG world, zero engine changes; branch `spike/second-seed-generativity`).
The detailed queue below is now mostly HISTORICAL — read `implementation-notes.html` +
`qa/SCORECARD.md` for the live state.

### Quest & Arc engine (the living-story skeleton — `decision-quest-arc-engine.md`)
- **L1 — rule-of-three** (`Quest.evolves_to` + `callback_in_days`; `complete_quest`
  schedules the follow-on via `consequences.schedule`) — **MERGED #185**.
- **L2 — decision-gated flips** (`CompanionAgenda.decision_flag` adds +0.30 to the
  attitude-gated betrayal roll; the breaking-point guard is checked FIRST so it never fires
  above threshold; warning bands telegraph) — **MERGED #189**.
- **L3 — Event / ParleyOption / Outcome** (the first-class Kingmaker decisional; thin
  wrapper over `_apply_structured_effect`; converges with the parley research; an Event
  Outcome sets a decision_flag → the L2↔L3 seam) — **BUILDING** (design in flight).
- **BUILD-NEXT — faction-growth arcs** (the Skyrim/Kingmaker join→grow→lead): additive
  `Faction` fields (`rank`, `standing`, `joined`, `questline_arc_id`) + generalize the
  companion stage-machine into a faction-owned `Arc` gated on `Faction.reputation`.
  **QUEUED.** (Closes the "rep is tracked but reads nothing" gap.)
- **DM-skill WIRING + canon content-fill** (author Raphael / Flaming-Fist into agendas +
  quest `evolves_to` chains) — **QUEUED**, per wave.
- **DEFER** — kingdom/guild-building continuation (tick the inert `RegionControl`/
  `FactionAsset` primitives). Far-future.

### Other engine / QA threads
- **Campaign Director (#72)** — `director.py` + `scene_debt.py` + 3 advisory tools.
  **MERGED + integrated** (DM consults `get_campaign_director` each beat → `add_quest` gap
  closed in play). #71 (path-compiler) + #73 (predicates) deferred until a world authors an
  `adventure_path`.
- **Combat-fidelity fixes** — Multiattack economy (#181), Round-1 turn-skip ENGINE block
  (#183), Guiding-Bolt-on-hit (#188) all **MERGED**. Residual: broader DM-adherence /
  class-feature coverage (issue **#166**) — push via the reach-for pattern + a richer sprint
  seed, not engine force.
- **Observability (#184)** — snapshot version-stamp (`schema_version` + `engine_sha`) +
  centralized QA findings collector (`qa/collect_findings.py`) — **MERGED**. Possible next
  slice: a real-play event log.
- **#143 variant matrices** — foundation ~90% shipped; remaining = (a) doc the weight
  convention in `content/worlds/README.md` (common 11 / uncommon 6 / rare 2 / very_rare 1
  on a ~20 base), (b) author canon variant CONTENT (free-rollable slots first), (c) ONE
  engine bit: generalize the companion agenda-roll to rare non-companion NPC flips (cap
  ~0.07, 3–7%/scene). **GATED on an owner glance** at the quest-idea boundary before a big
  content push.
- **#141 Parley → AI-player relay** — reopened; the relay was never built. Pairs with
  L3 / the social encounter type.
- **Party-ensemble betrayal validation** — `run_party.sh` not exercised since L2 shipped;
  restore it to validate the decision-flip in play.

### NATIVE DESKTOP APP — the play path (IN SCOPE; was the sibling lane)
The macOS/OpenWorlds Swift shell is now part of this lane (the old "another agent owns it,
stay out" note is retired; those PRs #150/#182/#187/#190-192 are merged). The app
(`macos/WorldOSApp/`, built by `script/build_and_run.sh run` → `dist/WorldOS.app`) is a
WKWebView loading the **live worktree** viewer at `/openworlds/` — so a **viewer/JS change
lands on app relaunch with NO Swift rebuild** (the swift build is a ~0.1s no-op).

**How in-app PLAY works (2026-05-27 cont.26 — the read-only→functional fix):**
- The OpenWorlds launcher Play buttons call the native bridge
  `OpenWorldsNative.request("startProviderSession",{provider?,world,runId,companions})`
  (`screen-launcher.jsx`). `provider` is optional: when the web surface has not loaded app status yet,
  Swift `RootView.startProviderFromBridge` falls back to the macOS app's `selectedProviderRaw` setting.
  Swift then asks `AppProcessService.startProviderSession` to launch the selected provider on a fresh
  port and returns `{url}`; the JS then `window.location.assign(reply.url)` — drive the reload from
  **JS**, not the Swift `webURL` @State (which didn't repoint reliably across the async hop).
- The Claude provider still shells **`scripts/play.sh`** / `scripts/play_party.sh`. `play.sh` IS the play loop:
  it binds a viewer with `CLAWDND_PLAYER_MOVES` +
  `CLAWDND_VIEWER_CHAT` set (→ `_live_play()` true) and runs a `claude -p` DM watching the
  move sink. `POST /move` → sink; `/chat?since=` → DM narration the Session tails.
- The checked-in Codex provider now defaults to `scripts/play_codex_dm.sh`, a DM wrapper that owns
  the live viewer, the engine/rules/voice MCP contract, `chat.jsonl`, and `player_moves.jsonl`.
  Keep `scripts/play_codex_actor.sh` as the constrained player/companion actor helper through
  `player_server.py`; it is not the native provider's DM loop. OpenClaw still requires an explicit
  configured command before it can be treated as a startable provider.
- Agents and app harnesses should read `GET /app-status` before trying to infer state from pixels or
  process lists. It is a read-only contract for the live OpenWorlds surface: provider, run/state roots,
  private-art presence, active campaign/session, move sink, actor, enabled actions, and canonical endpoints.
  The built-app harness captures launcher and minted-provider app-status JSON as release evidence.
- **`can_act = _live_play() AND is_live_view`**, and `is_live_view` requires
  `cid == self.campaign_id`. The viewer launches with an EMPTY campaign id; `_resolve_campaign`
  lazily sets `self.campaign_id` to the **current** campaign (`_pick_campaign`). So the
  ★gotcha★: the SPA must VIEW the live/current campaign — `app.jsx` auto-routes launcher→table
  when `runningProvider` is set, **re-polls `campaigns.json` (4s), and auto-follows the
  `current` campaign** so the surface binds to the DM-minted run, not a stale save the
  one-shot catalog pick had selected. Verify playable: `curl /session-surface` → `can_act:true`.
- **Footguns (all fixed cont.26, watch for regressions):** (1) a Finder/Dock GUI launch gets
  launchd's minimal PATH → `claude`/`uv` not found; `launch_common.sh:clawdnd_augment_path`
  prepends `~/.local/bin`+Homebrew. (2) `play.sh`/`play_party.sh` traps must SEPARATE EXIT
  (cleanup) from INT/TERM (cleanup+`exit`) or SIGTERM resumes the loop (wedged orphan). (3)
  `AppProcessService` does NOT kill its viewer child on app SIGTERM → orphaned viewers accrue
  across relaunches (still open — terminate-on-quit TODO). (4) `play.sh` always starts a
  FRESH campaign (true resume-by-id is a later enhancement).
- **Critical files:** `RootView.swift` (startProviderSession bridge), `ProviderAdapters.swift`
  (shells play.sh, ClaudeProvider.detect), `AppProcessService.swift` (PortFinder), `WebView.swift`
  (bridge user-script `window.ClawDnDNative`), `screen-launcher.jsx` + `app.jsx`, `scripts/play.sh`.

---

## RESUME CHECKLIST
1. Read this file, then any handed-off local session notes + `qa/SCORECARD.md`.
2. `cd` to the repo checkout or relevant worktree; `git log --oneline -10`.
3. `gh pr list` + `gh issue list` to see open lanes (avoid the desktop-lane PRs above).
4. Pick up the Quest-Arc L3 build / the queued waves; build → focused single-process test →
   PR (no `tail` in `&&`) → merge after checks pass → sync + prune. Log every QA run to SCORECARD.
