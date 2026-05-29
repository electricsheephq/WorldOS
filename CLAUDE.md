# WorldOS — working guide (read before editing/running)

WorldOS simulates living, AI-generated worlds and lets you play epic D&D 5e inside them —
shipped as a Claude Code plugin, with a native macOS app. The flagship world is a
post-Baldur's-Gate-3 5e living world. This file is auto-loaded when working in the repo — it
exists so we never again run the wrong UI or work from the wrong checkout (both have bitten us).

> Naming note: the project was renamed `ClawDnD` → `WorldOS` (GitHub repo
> `electricsheephq/WorldOS`; prior owner paths may redirect). The product name is
> now **WorldOS**. Some lower-level identifiers are still being migrated in later PRs and remain
> `clawdnd`-named on purpose: the MCP servers (`clawdnd-engine`, `clawdnd-rules`, `clawdnd-voice`,
> `clawdnd-player`), the `CLAWDND_*` env vars, and the `dev.clawdnd.app` bundle id. Treat those as
> the live wire/contract names until their dedicated rename PRs land — don't "fix" them ad hoc.

## Which checkout am I in?
- **Canonical working checkout: `/Users/lume/ClawDnD-val`** — it tracks GitHub
  `electricsheephq/WorldOS` `main`. Edit, run, commit, and verify here unless a
  task explicitly asks for a fresh Lexar worktree.
- **`/Volumes/LEXAR/repos/ClawDnD-val` and `/Volumes/LEXAR/repos/ClawDnD` are DEPRECATED**
  (the pre-2026-05-28 location). Do not edit or run from them. They're kept only as
  fast-forwarded read-only mirrors and are guarded so an accidental run still serves local code.
- If `git remote -v` shows `electricsheephq/WorldOS` but your `pwd` is under `/Volumes/LEXAR/…`,
  you're on the deprecated mirror unless the task explicitly requested a fresh Lexar worktree.
- The Claude Code **preview tool roots at the LEXAR path**, so don't trust `preview_start`
  blindly — run the viewer yourself (below) from the canonical checkout, or confirm the served
  code is current (root must 302→`/openworlds/`; `chrome.jsx` must have zero `traffic-lights`).

## The UI is OpenWorlds — NOT the root dashboard
- The real, current UI is the **OpenWorlds React SPA at `/openworlds/`**
  (`viewer/openworlds/*.jsx`, in-browser Babel, no build step), served by `viewer/server.py`.
  The native app `dist/WorldOS.app` loads `/openworlds/`. ("OpenWorlds" is the SPA codename —
  a sub-component of WorldOS — and stays.)
- **`http://127.0.0.1:<port>/` (the root) is the LEGACY pre-OpenWorlds dashboard** — it now
  redirects to `/openworlds/`. Never treat the root, `viewer/index.html`, or `dashboard.html`
  as the current UI. Always verify at `/openworlds/`.

## Run / verify the viewer
```sh
CLAWDND_STATE_DIR=<state-dir> CLAWDND_REPO_ROOT="$PWD" \
  python3 "$PWD/viewer/server.py" "" <port>      # argv: server.py [campaign] [port]
# Pass "" for the campaign to just set a port (a bare numeric arg is read as a CAMPAIGN id,
# not the port, and silently binds the default 8765). Then open /openworlds/ — not the root.
```
(The `CLAWDND_*` env vars are the current names; a later PR adds `WORLDOS_*` aliases.)
Headless capture: `qa/owshot.sh <screen-hash> <out.png> <port>` (fresh Chrome profile → no
stale cache). The viewer sends `Cache-Control: no-store` and version-stamps the index scripts,
but a long-lived browser profile can still hold an old copy — use a fresh profile when in doubt.

## Architecture invariants
- **The engine (`servers/engine/`) is the SOLE writer** of `snapshot.json`. The viewer, the
  native app, and the player facade only READ engine read-models and submit intent via
  `POST /move`. Never write play-state directly.
- Three MCP servers (`.mcp.json`): engine, rules, voice.

## Don't repeat these (hard-won)
- **Version-skew:** never `git commit` engine `.py` while a play/QA session is running. A
  long-lived DM engine subprocess running older `models.py` than the snapshot writer fails
  every tool call with pydantic `extra_forbidden`. Commit engine changes BEFORE starting a
  session. Viewer/.jsx, qa/, content/, docs/, skills/, macos/ are session-safe.
- **One checkout:** a stale second checkout silently serving old code is the #1 cause of
  "the fixes aren't there / wrong UI." Keep the LEXAR mirror fast-forwarded after every push.

## Testing characters
Use CANON BG NPCs from the 2076-record pool (e.g. `dal-lightspark`) — they have real ingested
portraits. NOT the 7 BG3 origin heroes (overpowered), and NOT custom PCs. A character with no
ingested face shows a neutral silhouette — never a class/race heraldic crest.

## Tests
Engine: `uv run --directory servers/engine python -m pytest tests/ -q` (single-process, ~15s,
1400+ tests). Viewer: `uv run --directory viewer pytest -q tests/`. Prefer GitHub CI for the
full sweep when the main disk is tight; engine single-process locally is light.

---

## Project map — read order + where everything lives
An agent resuming this project should read in this order, then consult the rest by need:

1. **`CLAUDE.md`** (this file) — checkout/UI/run guardrails + this map. Auto-loaded.
2. **`WorldOS-RUNBOOK.md`** — the compaction-resilience doc: architecture, invariants, dev/QA loops, lessons, work queue. READ FIRST after this.
3. **`WorldOS-NORTH-STAR.md`** — *what "great" means* (the optimization target). The score is a **proxy** for the felt prestige-CRPG session; come here when score and gut disagree.
4. **`qa/SCORECARD.md`** — the running results ledger (every scored run). 
5. Latest **session runbook**: `/Volumes/LEXAR/Codex/session-notes/<date>/clawdnd-*/` (`implementation-notes.html` or `runbook.html`) — the in-flight day-log. Current sprint: `2026-05-27/clawdnd-1.0-autonomous-sprint/runbook.html`.

### How we measure (the fitness function — still the gate)
Defined in **`qa/SCORING.md`**. A run = **1 hard behavioral gate** + **3 LLM lenses (1–5)**:
- **Behavioral gate** (`qa/assert_behavioral.py`) — deterministic PASS/FAIL: dice rolled, clock advanced, party visited ≥2 locations, new NPC met, player moves resolved, no dangling combat/conditions. RED caps the lenses.
- **Mechanical** lens → `qa/rubric.md` (5e/rules/tool-fidelity). **Target ≥ 4.5.**
- **Story-craft / "Tolkien"** lens → `qa/rubric_tolkien.md` (grandeur/character/prose/momentum/theme; act-aware). **Target ≥ 4.3** (enduring North Star 4.5).
- **Combat / "Angry-DM"** lens → `qa/rubric_angry_dm.md` (5e combat fidelity). Drive upward; the engine core is clean, residual is DM adherence + sampling.
- **OpenWorlds app sweep** adds: image-render ≥95% + button-coverage ≥95% + zero console errors (`qa/screen_coverage.py`, `qa/owshot.sh`).
- Scorer: `qa/score.sh` (+ `qa/SCORING.md` for the schemas). **Log every scored run to `qa/SCORECARD.md`.**

### Run a session / sweep
- 2-agent duo (DM + constrained AI player): `qa/run_duo.sh <runid> baldurs-gate qa/play_player_openworlds.txt <beats> <budget>` (player persona = a CANON BG NPC, e.g. Dal Lightspark).
- Companions ensemble: `qa/run_party.sh`. Combat fidelity lane: `qa/run_combat_sprint.sh`. Parallel: `qa/run_parallel.sh`.
- All scored runs land in `qa/SCORECARD.md`.

### The skills the DM/companions run
`skills/dungeon-master/` (`SKILL.md` = beat cycle + non-negotiables; `AGENT.md` = DM identity + 3-act process; `reference/*.md` = combat, storycraft, living-world, living-arcs, quest-generation, death-and-reroll). Plus `skills/companion/`, `skills/campaign-author/`, `skills/world-author/`.

### Design / architecture docs (`docs/`)
`ARCHITECTURE.md` · `OPENWORLDS_NATIVE_APP_ROADMAP.md` · `OPENWORLDS_UI_AUDIT.md` (the page-by-page audit; GitHub epic #242) · `OPENWORLDS_FIDELITY_PLAN.md` · `OPENWORLDS_DESIGN_ASSET_POLICY.md` (official images → gitignored `_private/`, never committed) · `RELEASE_1.0_CHECKLIST.md` · `MONSTER_AUTHORING.md` · `SPARKLE_SETUP.md`.

### Content + ingest
World seeds + lore: `content/worlds/baldurs-gate/` (canon characters, areas, endings, `lore/` + `lore/wiki/`). Wiki-first ingest pipeline: `tools/ingest/` (`wiki_fetch.py` → `wiki_to_{lore,characters,areas}.py`, `wiki_images.py` → gitignored `content/worlds/_private/`). Direction: pull from the BG3/FR wikis, generate little (see memory `clawdnd-wiki-first`).
