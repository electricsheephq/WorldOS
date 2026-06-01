# WorldOS — RUNBOOK (READ FIRST on resume)

> **Before anything: confirm you are in the repo root and know the right UI.**
> The current product UI is OpenWorlds at `/openworlds/`; the root dashboard is
> legacy. This runbook is the public project map + read order. Machine-specific
> agent notes such as `CLAUDE.md` are intentionally local-only and gitignored.
>
> **Routing (consolidated 2026-06-02):** current release/gate state lives in
> `WorldOS-OPERATING-GOAL.md` first (its STATE-OF-TRUTH block), then this file, `qa/QA_TOOLS.md`, and
> `qa/SCORECARD.md` (the human score ledger). The former `WorldOS-GUI-RUNBOOK.md` is now merged into
> this file (see "THE GUI / NATIVE-APP LOOP" below); the original is preserved at
> `docs/archive/WorldOS-GUI-RUNBOOK.md`. The detailed historical work-queue that used to live at the
> bottom of this file is now at `docs/archive/RUNBOOK-WORK-QUEUE.md`.
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
> Last updated: 2026-06-02 (consolidation). The last fast handoff proof was on `9545383`, but **code/QA
> commits have since landed above it on `origin/main`** (verified 2026-06-02 — NOT docs-only), so that SHA
> is stale as a "current" baseline; re-prove on the current SHA. Release notes are historical context.
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

## THE GUI / NATIVE-APP LOOP (merged from the former GUI runbook)

> How to test→fix→LOOK the WorldOS GUI on the REAL surface and drive it to a 10/10 release. Born from
> the 2026-05-31 reorientation: the prior loop scored a HEADLESS PROXY served from worktrees WITH NO ART,
> so every visible defect (no palette, no images, no map, unformatted chronicle, phantom companion)
> sailed past. This loop makes that impossible to repeat. The product is the **launchable, played `.app`**;
> a green score on any other surface is a measurement bug, not progress.

### The two surfaces (never confuse them)
- **ITERATE — visible, playable, fast:** the OpenWorlds viewer served **from the local canonical repo**
  `/Users/lume/ClawDnD-val` (which HAS the ~2.9 GB `content/worlds/_private` art) as a LIVE PLAYABLE
  session on **fixed port 8799**. Fix one thing at a time and LOOK here.
- **GATE — truth:** the built `dist/WorldOS.app` via `qa/ui_playtest_app.sh` (part A native #356 +
  part B persona loop). Release is judged here. Same viewer code; adds the native shell. A non-local
  worktree may serve art only via `WORLDOS_ART_REPO_ROOT=/Users/lume/ClawDnD-val`, but prefer the local
  checkout because external-drive file prompts have broken local AI tests.

### Fresh-GUI-agent quick start — the hybrid handoff gate
Before spending budget on long persona runs, run the handoff gate on the current commit. It catches
stale tabs, dead launchers, missing private art, missing actor/actions, failed `/move`, no narration,
console/network errors, provider trace failures, and evidence gaps.
```bash
cd /Users/lume/ClawDnD-val
python3 qa/app_handoff_gate.py --web-beats 5 --built-beats 5 --codex-moves 1 \
  --art-root /Users/lume/ClawDnD-val --scripted-budget 1.00 --codex-budget 3.00 \
  --timeout 90 --codex-timeout 240
```
Writes `/Volumes/LEXAR/Codex/worldos-agent-grade-app-testability/<run-id>/`. Review `handoff.json`
first, then each gate's `app-evidence/manifest.json`, `app-status.*.json`, `session-surface.*.json`,
screenshots, moves, console/network/action logs, provider trace. `handoff_score=100` means the GUI
wiring loop is trustworthy **for implementation velocity** — it is NOT release-ready evidence by itself.

| Command | Use it for | Do not treat it as |
|---|---|---|
| `scripts/play.sh ... 8799` | Fast local LOOK loop on canonical repo with private art | Built-app proof |
| `qa/app_handoff_gate.py` | Fast web + built-app scripted smoke + short Codex playtest | Full release verdict |
| `qa/ui_playtest_app.sh` | Native app harness, native Part A+B evidence, failure buckets | Five-persona sweep by itself |
| `qa/ui_playtest.sh` | Blind browser persona diagnostics (#324) | Built-app product proof |
| `qa/release_readiness.py --handoff-json ...` | RRI rollup + verdict when paired with complete persona evidence | A substitute for missing persona artifacts |

Ports: `8799 /openworlds/` = canonical fast-iteration; `8899` = scripted/dev harness (valid only when
same-port `/app-status` is live); native app uses a dynamic port — read `run.json` or
`/app-status.viewer.port`, never guess. The handoff gate accepts five enabled actions; the RRI
palette-live gate is stricter (≥6 enabled actions on a `can_act:true`, disk-backed surface).

### Stand up the iteration surface (8799, playable, from canonical)
```bash
cd /Users/lume/ClawDnD-val
git rev-parse --short HEAD && git rev-parse --short origin/main   # confirm synced
pkill -f 'viewer/server.py'; pkill -f 'scripts/play.sh'; pkill -f 'play_party.sh'   # NOT the Eva gateway
CLAWDND_PLAY_PORT=8799 nohup bash scripts/play.sh baldurs-gate preview-$(git rev-parse --short HEAD) 8799 > /tmp/wos-8799.log 2>&1 &
```
Open `http://127.0.0.1:8799/openworlds/`. The DM cold-open takes ~30–90s; **wait for a SEATED PC**
(party non-empty), not just `can_act:true` — `can_act` can flip true before the PC is seated.

### LOOK (verify by curl + screenshot — NEVER a single Read; the channel fabricates)
The tool channel intermittently returns fabricated/empty/doubled reads (it has invented a palette-disabled
bug and a scene-404 that were both false). **Ground every load-bearing claim in ≥2 clean reads + an HTTP
code/checksum.**
```bash
curl -s http://127.0.0.1:8799/session-surface | python3 -c 'import json,sys;d=json.load(sys.stdin); \
  print("party",[(p["name"],p.get("kind")) for p in d.get("party",[])]); \
  print("palette",[a["id"] for a in d.get("availableActions",[]) if a.get("available")]); \
  print("can_act",d.get("can_act"))'
# images: curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8799/image?scope=location:loc-lower-city"
```
Per-fix visual checklist: palette buttons present + enabled in the MAIN column · a click resolves a turn ·
portraits/scene/map images 200 · a multi-paragraph DM beat renders as paragraphs · prose streams mid-turn
(`/events` count climbs) · a SOLO session has the PC alone.

### Fix one thing → PR → merge → rebuild → LOOK
1. Confirm the symptom on 8799 with ≥2 clean reads. If it doesn't reproduce, it's a stale/corrupt read —
   do NOT fix it.
2. Builder agent in a **same-disk local worktree off origin/main** when GUI/app tests need art:
   `git -C /Users/lume/ClawDnD-val worktree add -B codex/<slug> /Users/lume/WorldOS-worktrees/wos-<slug> origin/main`.
   Lexar worktrees remain fine for docs/backend/non-GUI slices that do not launch the viewer/app.
3. PR → CI green (incl. `viewer-tests`) → admin-squash-merge → delete branch → prune worktree.
   **Builder PRs sometimes fail to push silently** — always `gh pr view <n>` / `git ls-remote origin <branch>`
   to confirm the branch+PR EXIST before relying on them.
4. `git pull --ff-only` local canonical → restart 8799 → LOOK → record the proof in `qa/SCORECARD.md`.

### Agent-facing app contract
- `GET /app-status` and `GET /__worldos/app-status.json` are **read-only** probes for agents/harnesses:
  build/version, viewer port, state root, provider, private-art-root presence, live campaign/run, move
  sink, active actor, enabled actions, canonical endpoints. They must not mutate state.
- Use `/app-status` before screenshots when diagnosing the built app. A built-app proof that cannot
  produce this status object is a harness/observability failure. `qa/ui_playtest_app.sh` captures launcher
  and minted-provider `app-status` JSON into the native evidence folder. See
  `docs/AGENT_GRADE_APP_TESTABILITY.md` for the full contract.
- Native provider reality check: OpenWorlds native-start honors the macOS app's selected provider (#472);
  if the web UI hasn't loaded app-status it omits `provider` and Swift's `selectedProviderRaw` decides.
  The Codex path has two wrappers — `scripts/play_codex_dm.sh` (the selected provider's DM loop) and
  `scripts/play_codex_actor.sh` (constrained player/companion actor). Do not swap them. A wrapper run is
  not release proof by itself.

### The gate sweep (judged on the built `.app`)
```bash
WORLDOS_ART_REPO_ROOT=/Users/lume/ClawDnD-val \
qa/release_gate.sh --personas newbie,veteran,adversarial,narrative,optimizer --budget 12 --port 8785
```
RRI 10/10 = all 11 gates (OPERATING-GOAL §4) hold on ONE build across the five canonical personas. The
scorer must record required/expected/completed/missing personas + explicit evidence gaps, disk-backed
behavioral, UI-audit, image denominator/source, palette-live evidence, per-run Part B pass status, and
same-build SHA. Append every `--scorecard-row` to `qa/SCORECARD.md`. Only a non-partial,
non-harness-contaminated 10/10 row with no evidence gaps counts as release evidence.

### Support VM lane (heavy sweeps, not Mac-only app truth)
- Target: owner-provided **32GB support VM** (`support-vm-1`); connection/auth details live in local
  operator-only runbooks/evidence, **not** tracked repo docs. Do not assume it is ready for Codex runs
  until credentials/config are intentionally installed and verified.
- Default VM persona lane is Codex DM + Codex UI player (Claude only when explicitly selected). The Codex
  lane needs Codex CLI `>=0.120.0` (per-invocation `codex exec -c mcp_servers.*` overrides).
- Do **not** use it as proof for Mac-only surfaces (`WorldOS.app` build/launch, native #356, built-app UI
  play). Those stay on this Mac or macOS CI.
- VM preflight before any RRI sweep, via the repo-owned writer:
  ```bash
  python3 qa/support_vm_preflight.py --repo /root/worldos-qa/WorldOS --expected-sha <SHA> \
    --provider codex --player-agent codex --art-root /root/worldos-qa/WorldOS \
    --private-art-mode required --artifact-dir /tmp/worldos-support-vm-preflight-<SHA> \
    --artifact-return-target /Volumes/LEXAR/Codex/worldos-support-vm-rri/<SHA>-preflight
  ```
  It is read-only w.r.t. WorldOS state, writes `support_vm_preflight.{json,md}`, redacts secrets, and
  exits non-zero if same-SHA/origin/tool/auth/private-art blockers would make the sweep untrustworthy.
  [UNVERIFIED, carried from the prior runbook] A read-only scout reached `evaos-support` (~32 GB RAM, 16
  CPUs, `uv`/Node/`codex-cli 0.120.0`/Playwright/private art) but its checkout was stale (`4524b3e`),
  batch-mode `git` could not query the HTTPS origin, Codex auth was unproven, and `/Volumes/LEXAR/Codex`
  did not exist on the VM. Approve/sync the VM, prove Codex auth, make `origin/main` queryable, and define
  artifact return before #466.
- Split Mac/VM rollup: pass the Mac proof as
  `--handoff-json /Volumes/LEXAR/Codex/worldos-agent-grade-app-testability/<handoff>/handoff.json`
  alongside VM persona dirs from the **same** SHA. RRI satisfies the native gate from the Mac handoff only
  if all required gates+manifests are same-SHA, clean, private-art-present, gap-free. Mixed-SHA/missing
  artifacts stay `partial` / `harness_contaminated`.

### macOS privacy-prompt triage
A macOS Photos/Music prompt during local proof can be a **test-process attribution artifact**: TCC may
name the frontmost WorldOS app as `responsible` while the actual `accessing` process is a diagnostic
command (`/usr/bin/find`, `codex`). Before filing as a product blocker, inspect:
```bash
/usr/bin/log show --style compact --last 10m \
  --predicate 'eventMessage CONTAINS[c] "dev.clawdnd" OR eventMessage CONTAINS[c] "kTCCServicePhotos" OR eventMessage CONTAINS[c] "kTCCServiceMediaLibrary"'
```
If `AUTHREQ_ATTRIBUTION` shows `accessing=/usr/bin/find` or `=codex`, classify as harness contamination
and rerun without broad filesystem scans while the app is frontmost. If `WorldOSApp`/a WebKit child
directly accesses a protected path, treat it as a release-blocking bug.

### Release (when RRI = 10/10 on a fresh `.app` build)
Bump `.claude-plugin/plugin.json` (current `1.0.3` → `1.0.4`), tag `v1.0.4`, GitHub release + CHANGELOG.
Then MAINTAIN: every PR touching `viewer/ | macos/ | skills/ | servers/engine/` → rebuild + RRI sweep +
SCORECARD row; any regression (a critical bug, a sub-7 persona, sub-threshold score, image <95%, dead
palette) reverts the goal to "fix" and outranks new work.

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

> Moved to `docs/archive/RUNBOOK-WORK-QUEUE.md` during the 2026-06-02 consolidation (it was already
> self-labeled "Historical snapshot, not current authority"). For the live release state read the
> STATE-OF-TRUTH block in `WorldOS-OPERATING-GOAL.md`; for the running score ledger read
> `qa/SCORECARD.md`; for open GitHub work run `gh issue list` / `gh pr list`. Most Quest-Arc and
> combat-fidelity items in that archive are MERGED and closed — it is kept for provenance only.

---

## RESUME CHECKLIST
1. Read this file, then any handed-off local session notes + `qa/SCORECARD.md`.
2. `cd` to the repo checkout or relevant worktree; `git log --oneline -10`.
3. `gh pr list` + `gh issue list` to see open lanes (avoid the desktop-lane PRs above).
4. Pick up the Quest-Arc L3 build / the queued waves; build → focused single-process test →
   PR (no `tail` in `&&`) → merge after checks pass → sync + prune. Log every QA run to SCORECARD.
