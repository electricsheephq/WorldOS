# AI Playtester harness (issue #324)

> Current routing note: this is the blind browser persona diagnostic harness. It is valuable for
> empirical UX friction, but it is not the built-app product gate. For the fast built-app handoff
> gate run `qa/app_handoff_gate.py`; for lower-level native app evidence use `qa/ui_playtest_app.sh`;
> for release truth use the non-partial RRI path in `qa/release_readiness.py`.

A **blind UI/UX test**: an AI "player" drives the real `/openworlds/` browser UI with no
source-code access and reports every bug + UX gap it hits. Different signal from the
code-reading audit — this finds bugs by *trying to play*.

v1 shipped **one persona (The First-Timer)**, Playwright, a single end-to-end run.
**v2 adds the other four personas + a cross-persona scoring aggregator** (issue #324).

## Run it

```sh
qa/ui_playtest.sh <runid> <world> <persona> <beats> <budget>
# the canonical v1 command:
qa/ui_playtest.sh play1 baldurs-gate newbie 30 3.00
```

- `runid`   — names the output dir `qa/ui_playtest_runs/<runid>/` (wiped + recreated).
- `world`   — world id seeded for the session (e.g. `baldurs-gate`).
- `persona` — picks `qa/play_player_browser_<persona>.txt`. The runner dispatches purely by
              this name (line 40), so adding a persona is just adding its brief file.
- `beats`   — soft cap on the number of Player **palette actions**.
- `budget`  — USD cap for the **Player** `claude -p` process.

### The five personas (v2)

Each persona is a self-contained brief over the **same** 8-tool palette; only the player's
mindset + bug-hunting focus differs. They are designed so the bugs they hit *overlap* — a
defect every persona reports is the highest-priority one (see the aggregator below).

| persona | brief | what it hunts |
|---|---|---|
| `newbie` | `play_player_browser_newbie.txt` | first-timer onboarding: can a blind new player reach + sustain play? jargon, dead buttons, getting-stuck. |
| `veteran` | `play_player_browser_veteran.txt` | BG3 affordance gaps: hotbar / action economy, character-sheet depth, equipment paper-doll + compare, tactical combat (movement/spell-area preview), approval/subclass. |
| `adversarial` | `play_player_browser_adversarial.txt` | tries to BREAK it: dead/lying controls, rapid/double-fire, empty/huge/markup input, navigate-away mid-DM-turn, modal traps, state corruption. |
| `narrative` | `play_player_browser_narrative.txt` | story-first / menu-averse: immersion breaks, DM-internal leaks, lifeless NPC dialogue, jargon tabs that pull you out of the fiction. |
| `optimizer` | `play_player_browser_optimizer.txt` | data depth: complete bestiary stat blocks, deep inspectors (full rules text), compare-on-hover, level-up / subclass / re-prepare build planning. |

> The persona briefs are committed (they're the deliverable). Only the per-run **outputs**
> under `qa/ui_playtest_runs/` and `qa/playwright/node_modules/` are gitignored.

Output under `qa/ui_playtest_runs/<runid>/`:

```
meta.json                 run params + player cost
summary.md                human-readable digest (what they tried, where stuck, top bugs, score)
score.json                rubric metrics + pass/fail
bugs.ndjson               one bug per line (schema: qa/ui_playtest_bug_schema.json)
viewer.log                engine+viewer stdout
dm/                       DM agent: chat.jsonl (two-sided), dm.jsonl (full stream), turn.*.jsonl, dm.err
player/
  screenshots/            step-NNN-*.png (one per palette action)
  a11y/                   step-NNN.txt (the screen-reader view at each look)
  actions.ndjson          one line per palette action {seq, action, target, ok, dead, ...}
  console.ndjson          browser console errors/warnings captured passively
  network.ndjson          failed / 4xx / 5xx requests captured passively
  player.jsonl            the Player agent's full stream
  status.json             present iff the player called give_up
```

> Run artifacts are **gitignored** (`/qa/ui_playtest_runs/`) — bulky + regenerable, public repo.

## Architecture (three processes, one run)

```
qa/ui_playtest.sh
  ├─ Engine + Viewer (viewer/server.py)  → serves /openworlds/ on a free port (8990–8999),
  │                                         wired with a move sink + a two-sided chat log.
  │                                         Engine is the SOLE writer; viewer only reads
  │                                         surfaces + accepts /move intents.
  ├─ DM agent (claude -p, full plugin)   → UNCHANGED from qa/run_duo.sh / qa/play_human.sh.
  │                                         Opens the scene, then a background loop resolves
  │                                         each player move posted to the move sink and writes
  │                                         narration to the chat log. The DM never knows
  │                                         there is a UI.
  └─ PLAYER agent (claude -p, palette only) → a blind newbie. Sees ONLY the screen
                                              (screenshot / a11y_tree); acts ONLY via
                                              click / type / key / wait; reports friction via
                                              report_bug / give_up. Drives the real browser.
```

**The play loop is the magic:** the Player doesn't talk to the DM. The Player drives the UI →
the UI POSTs `/move` → the unchanged DM resolves it → narration flows back onto the screen via
`/chat`. The Player re-screenshots to see the result and decides the next action. The harness
validates the whole UI ↔ engine ↔ DM loop *empirically* (it works because the player could play).

Because a plain browser has no native bridge, the harness pre-mints the live game (the DM's
opening turn seats a living canon PC + companion and opens a scene) so the launcher's
**Chronicles** shelf offers **Resume Chronicle**. The newbie discovers and clicks into the game
through the *real* launcher start-flow — which is exactly the flow that exercises #305 (a dead
character must not be offered/seated as a PC; if it is, the newbie reports it).

## The T3 native-window variant (issue #1436 U2 / #1322)

The T3 gate exits on the RENDERED surface, not the browser: a blind AI playtester completes a quest
loop in the standalone Unity **WorldOSPlayer.app** window. `qa/ui_playtest_player.sh` is the browser
harness's native twin — it exposes the **same 9-tool contract** to the player agent but backs it with
macOS primitives instead of Playwright, and scores with the **same** `qa/ui_playtest_score.py`.

```sh
qa/ui_playtest_player.sh --preflight            # check app + deps + swift helper + PERMISSIONS, no run
qa/ui_playtest_player.sh <runid> <beats> <budget> [--force]   # seed → viewer → player app → DM loop → play → score
```

- Boot recipe: the **scene-paired seed** (default `qa/seed_gfx_camp.py`, see the pairing table just
  below) mints `camp_gfxdemo01` → `viewer/server.py` serves `/combat-surface` (move sink + chat
  wired) → `WorldOSPlayer.app` is launched with the env launch-contract `WORLDOS_ENGINE_BASE_URL` /
  `WORLDOS_CAMPAIGN_ID` → the **unchanged** `run_duo` DM loop resolves the player's moves → the
  native palette drives the window → teardown → score.

**SCENE ↔ FIXTURE pairing (#1441 Phase 2 — read this before changing the seed or the baked scene):**
the Unity player build bakes exactly one scene's painted props into its plate, and the seed script's
`scene_grid` impassable set must match that same scene or the painted props render walkable — this is
precisely how the felt bug shipped: the player build baked the `camp_clearing_night` plate (fire pit,
log seat, bedrolls, supply crates, boulders, tree line) while the harness kept seeding the older
crypt-shaped grid (pillars + a sarcophagus) under the same `camp_gfxdemo01` campaign id, so none of the
camp's painted scenery was pathing-solid and actors could stand *in* the campfire or *on* the log seat
(the owner's "stacking on everything" report). `qa/ui_playtest_player.sh` now resolves the seed script
from a `WORLDOS_PLAYER_SCENE` env var (`camp` default, or `crypt`) via a fixed pairing table —
`camp` → `qa/seed_gfx_camp.py`, `crypt` → `qa/seed_gfx_combat.py` — and refuses to run with an explicit
`WORLDOS_PLAYER_SEED_SCRIPT` override that doesn't match the requested scene unless you pass `--force`;
when the player app's baked scene changes again, update the pairing table (or set
`WORLDOS_PLAYER_SCENE=crypt`) rather than swapping the seed's grid contents in place.
- Palette backing (`qa/native_palette/native_palette_server.js`): `screenshot` = `screencapture -l
  <windowid>` (window found via `CGWindowList`); `click(x,y)` = a `CGEvent` click at window-relative
  pixels mapped to global points (via `qa/native_palette/native_input.swift`, or `cliclick` if
  installed); `type`/`key` = synthetic keystrokes; `a11y_tree` = a **pixels-only stub** (the T3
  persona plays from screenshots); `wait`/`report_bug`/`give_up`/`finish` identical to the browser
  palette. Persona brief: `qa/native_palette/play_player_native_t3.txt`.
- **macOS permissions (owner action, FAIL-LOUD):** the palette needs **Screen Recording** (System
  Settings ▸ Privacy & Security ▸ Screen Recording — for the capture AND for `CGWindowList` to see the
  window) and **Accessibility** (▸ Accessibility — for synthetic input). A missing grant aborts the
  run with the exact pane to open; it is never silently skipped. Run `--preflight` to check both.
- One live GUI harness at a time; the palette never touches Eva; the engine stays the sole writer.

## The Player palette (the constrained tool surface)

`qa/playwright/palette_server.js` is an **MCP server** that is the Player's *entire* tool surface
— mirroring the engine's constrained `player_server.py` facade (roles enforced in code, not
prose). Exactly **8 tools**, each backed by a Playwright (Chromium) browser the server controls:

| tool | backing |
|---|---|
| `screenshot()` | `page.screenshot()` (saved PNG) + the a11y text |
| `a11y_tree()` | `locator('body').ariaSnapshot()` — what a screen reader announces* |
| `click(target)` | `getByRole`/`getByText` locator click (visible label/text), reports dead clicks |
| `type(text, target?, submit?)` | fill a field by label/placeholder; optional Enter to take a turn |
| `key(name)` | `keyboard.press` |
| `wait(ms? selector?)` | `waitForTimeout` / `waitForSelector` (capped) |
| `report_bug({severity, screen, expected, actual, …})` | appends one line to `bugs.ndjson` |
| `give_up(reason)` | ends the run (writes `status.json`) |

There is **no** source-code access, **no** engine introspection, **no** filesystem in the palette.

\* The spec named `page.accessibility.snapshot()`. That API was removed in Playwright ≥ 1.5x;
`ariaSnapshot()` is its modern successor and returns the same role/name/value/disabled tree.

The harness also **passively captures** browser console errors and failed/4xx/5xx network
requests and auto-emits them as bugs (`source: "auto"`) — so breakage is caught even when the
persona doesn't notice it.

## Scoring (qa/ui_playtest_score.py)

| metric | meaning |
|---|---|
| `completed_intro_flow` | reached the play screen **and** took ≥1 in-story turn |
| `actions_to_first_beat` | palette actions until the first submitted turn (newbie target ≤ 10) |
| `dead_clicks` | clicks that landed but changed nothing on screen |
| `console_errors` / `network_failures` | passive-capture counts |
| `bug_reports_{critical,major,minor,trivial}` | counts by severity |
| `persona_satisfaction` | 1–10, self-reported if the player stated one, else derived |

A run **passes** when `completed_intro_flow` **and** `critical == 0` **and** `console_errors == 0`
**and** `satisfaction ≥ 6`.

## Aggregating a persona sweep (qa/ui_playtest_aggregate.py)

Run the personas one at a time into sibling dirs, then aggregate them into one release-readiness
picture:

```sh
SWEEP=qa/ui_playtest_runs/sweep-$(date +%Y%m%d)
qa/ui_playtest.sh "$SWEEP/newbie"      baldurs-gate newbie      30 3.00
qa/ui_playtest.sh "$SWEEP/veteran"     baldurs-gate veteran     30 3.00
qa/ui_playtest.sh "$SWEEP/adversarial" baldurs-gate adversarial 30 3.00
qa/ui_playtest.sh "$SWEEP/narrative"   baldurs-gate narrative   30 3.00
qa/ui_playtest.sh "$SWEEP/optimizer"   baldurs-gate optimizer   30 3.00
python3 qa/ui_playtest_aggregate.py "$SWEEP"   # → $SWEEP/RELEASE_SUMMARY.md
```

The aggregator reads each run's `score.json` + `bugs.ndjson` and writes `RELEASE_SUMMARY.md`
(+ `release_summary.json`):

- a **per-persona scorecard** (pass / satisfaction / played / dead-clicks / console-errors / cost);
- **cross-persona findings ranked by how many personas hit each defect**. Bugs are clustered
  across personas by `(screen, category)` + a synonym-normalized token overlap (so "dead Forge
  button" and "Forge tab does nothing" merge). Priority = **breadth of impact**: a defect ALL
  personas hit is **P0**, one persona is **P3** — the natural prioritization from the persona
  strategy. The persona's own severity (critical/major/minor/trivial) breaks ties within a band.
- the by-design **missing-image 404 noise collapsed** into one advisory line (never prioritized).

Release verdict is **READY** only when every persona passed AND there is no P0/P1 cross-persona
defect.

> **Serial only on this host.** A 16 GB host OOMs running concurrent heavy DM sessions, so the
> sweep above runs personas **one at a time** (each is its own engine+DM+player triple). True
> parallelism — a persona panel running side by side — needs a bigger host; on a 16 GB box, keep
> it serial.

## Setup (one time)

Playwright + the MCP SDK live in their own workspace so the repo root stays clean:

```sh
cd qa/playwright
npm install                      # installs playwright + @modelcontextprotocol/sdk + zod
npx playwright install chromium  # ONLY if chromium isn't already cached (it often is)
```

`node_modules/` is gitignored; `package.json` + `package-lock.json` are committed.

### Env knobs (optional)

- `WORLDOS_UIPT_CHANNEL=chrome` — reuse system Chrome instead of bundled Chromium.
- `WORLDOS_DM_MODEL` / `WORLDOS_UIPT_PLAYER_MODEL` — model per agent (DM default `opus`; player `sonnet`).
- `WORLDOS_UIPT_DM_BUDGET` — USD per DM turn (default `1.50`).

## Journey eval — factual VQA (qa/journey_eval.py)

The aesthetic panels measure **beauty-vs-bar**, so a T-posing actor, a wrong-plate bundle, a character
standing inside a painted prop, and a failed door-cross plate swap all reached owner builds *scored
around*. `journey_eval.py` walks the playable loop and asks **factual YES/NO** questions of every frame
(YES = defect); any yes fails the journey and names the frame. It drives the SAME box player as
`qa/player_smoke.sh` (`lib_native_player_boot.sh` boot + the #1466 `WORLDOS_QA_INPUT` cell-click channel).

Three phases (split so the box drive and the LLM VQA run independently):

```sh
# 1. derive the scripted path from a room manifest (+ an optional plan of parley/door/combat cells).
#    One step adjacent to EVERY impassable prop; transitions capture both sides.
python3 qa/journey_eval.py build-script qa/room_manifests/camp_clearing_night_v2.cells.json \
  --plan qa/journey_plans/camp.json -o /tmp/journey/script.json

# 2+3. ON THE BOX: boot the player, drive the script, capture frames, then VQA + verdict end to end.
#    (needs Screen Recording + Accessibility grants + WorldOSPlayer.app, same as player_smoke.sh)
python3 qa/journey_eval.py run qa/room_manifests/camp_clearing_night_v2.cells.json \
  --plan qa/journey_plans/camp.json --campaign camp_gfxdemo01 --rundir qa/journey_runs/camp-1

# VQA-only over an already-captured frames dir (anywhere claude is authed — no box):
python3 qa/journey_eval.py vqa qa/journey_runs/camp-1/frames_manifest.json \
  -o qa/journey_runs/camp-1/journey_verdict.json
```

- **Questions** are versioned + reviewable in `qa/journey_vqa_questions.md` (the harness reads only the
  fenced `json` block; `applies_to: all|transition`). Every question is phrased YES = defect.
- **Scorer**: `qa/vqa_frame.sh` runs one `sonnet` `claude -p` per frame over the image, reusing
  `score.sh`'s auth-isolation (fresh config dir + keychain token + GLM-neutralised env). Env:
  `WORLDOS_VQA_MODEL` (default `sonnet`), `WORLDOS_VQA_TIMEOUT` (default `180`),
  `WORLDOS_VQA_GUARD_ONLY=1` (offline wiring proof, no LLM).
- **Verdict**: `journey_verdict.json` — `passed:false` with the offending frames + flags if ANY yes.
- The aggregation is unit-tested with a stub scorer (`qa/test_journey_eval.py`); the box capture + live
  VQA are exercised on the box.

## Scope notes

- The bugs this finds are **the point** — record them; do **not** fix WorldOS UI bugs from a
  playtest run. File them as issues.
- Constraints honored: the engine (`servers/engine/`) is the **sole writer**; the harness only
  reads viewer surfaces + drives the UI / `/move`. No wire-contract changes (`WORLDOS_*` env,
  `worldos-*` MCP ids, bundle id). No assets / `_private/` committed.
- v2 adds the other four personas (BG3 veteran, adversarial QA, storyteller, min-maxer) + the
  cross-persona scoring aggregator (`qa/ui_playtest_aggregate.py`). The sweep runs **serially**
  on this 16 GB host (concurrent heavy DM runs OOM it); a true parallel panel needs a bigger
  host. See issue #324.
