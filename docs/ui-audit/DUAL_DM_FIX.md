# Dual-DM Fix: `qa/ui_playtest.sh` ↔ `scripts/play_party.sh`

**Loop-9, gotcha #1.** This document captures the architecture problem and the
exact patch needed to fix it.

## TL;DR

`qa/ui_playtest.sh` and `scripts/play_party.sh` (via its solo fallback into
`scripts/play.sh`) **both** spawn a DM agent (`claude -p`) wired to the **same
viewer move sink** (`$MOVES = $STATE_DIR/player_moves.jsonl`). If a user ever
runs them side by side over the same state dir — or if a future composition
ever chains them — the two DMs race on the same move tail, double-resolve
every move, and corrupt narration. Worse: the DMs hold two **separate** Claude
sessions on the same campaign, so the engine sees writes from two voices that
never coordinate.

The fix is to **separate roles by ownership**:

- `play_party.sh` (or `play.sh` underneath) **owns the viewer + the DM agent**.
- `ui_playtest.sh` **strips down to just the Playwright Player** that drives
  the *already-running* viewer.

When the two scripts compose, there is exactly one DM resolving moves from
exactly one sink.

---

## Current architecture (file:line)

### `qa/ui_playtest.sh` — owns viewer + DM + Player (THREE roles)

| Role | Lines | Notes |
|------|-------|-------|
| Viewer (engine + `/move` + `/chat`) | `qa/ui_playtest.sh:114-130` | `python3 viewer/server.py "" "$PORT"` wired with `CLAWDND_VIEWER_CHAT=$CHAT` + `CLAWDND_PLAYER_MOVES=$MOVES`. Port picked from 8990–8999. |
| **DM agent (`claude -p`, full plugin)** | `qa/ui_playtest.sh:71-90, 132-145, 152-157` | Builds `$DM_CFG` rooted at `$ROOT/servers/*` + engine state dir = `$STATE_DIR`. `dm_turn()` runs `claude -p ... --plugin-dir "$ROOT" --mcp-config "$DM_CFG"`. The "opening" turn at L153 seats a PC + companion. |
| **DM-resolver background loop** | `qa/ui_playtest.sh:159-183` | A `( ... ) &` subshell tailing `$MOVES`. Each new line → `dm_turn 0 "The player does: ..."` → `chatlog dm ...` → writes back to `$CHAT`. PID held in `$DMLOOP`. |
| Playwright Player (`claude -p` with palette MCP only) | `qa/ui_playtest.sh:92-109, 185-201` | The `$PLAYER_CFG` MCP server (`palette_server.js`) is the agent's *only* tool surface. URL handed in via env (`CLAWDND_UIPT_URL`). |

The two **DM**-shaped artifacts to remove from `ui_playtest.sh` are:

1. The DM MCP-config build (`L71-90`).
2. The `dm_turn` helper and DM-resolver loop (`L132-183`), including the DM
   "opening turn" at `L152-157` that seats the PC + companion.

(The DM scratch dir at `$RUNDIR/dm/` and the `mkdir -p ... "$RUNDIR/dm"` at
`L52`, `MOVES` + `CHAT` + `COMBINED` files at `L53-55`, and the `DM_BUDGET`
env knob at `L39` all go with them.)

### `scripts/play_party.sh` — owns viewer + DM + companions (the "right side")

| Role | Lines | Notes |
|------|-------|-------|
| Solo fallback (delegates to `scripts/play.sh`) | `scripts/play_party.sh:69-85` | When no companion-spec is given, `exec scripts/play.sh "${ARGS[@]}"`. **The solo path IS just `play.sh`** — same DM, same viewer, same move sink. |
| Viewer supervisor | `scripts/play_party.sh:291-311` (party) and `scripts/play.sh:208-229` (solo) | Long-running supervised viewer at port 8765 (or `$CLAWDND_PLAY_PORT`, or `clawdnd_choose_port`). Same `CLAWDND_PLAYER_MOVES=$MOVES` + `CLAWDND_VIEWER_CHAT=$CHAT` wiring. PID file: `$STATE_DIR/.viewer.pid`. |
| DM agent (full plugin) | `scripts/play_party.sh:113-128` (config), `215-233` (turn), `331-340` (opening) — and `scripts/play.sh:70-85, 183-192, 251-273` for solo. | Same `--plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config --model "$CLAWDND_DM_MODEL"` invocation. The opening turn seats a PC + companion live. |
| Human-paced beat loop | `scripts/play_party.sh:393-450` (party), `scripts/play.sh:286-326` (solo) | Tails `$MOVES`. New line → companion turns (party only) → `turn dm ... "The player does: ..."` → narrates back to `$CHAT`. |
| Idle ceiling | `scripts/play_party.sh:399-403, 444-447` (party only — solo `play.sh` has none, see L286-326) | Party loop stops after `CLAWDND_PLAY_MAX_IDLE` (default 1800s) with no human move; solo loop spins on `sleep 2` forever until Ctrl-C. |

### The collision

If both scripts run against the same campaign:

1. **Two viewers compete for the port** — `play_party.sh` picks 8765,
   `ui_playtest.sh` picks 8990; if a user manually points them at the same
   state dir, both viewers serve different ports but **the same `MOVES` file**.
2. **Two DMs race on `$MOVES`** — both have a `MCURSOR` tailing the same JSONL
   file, both run `dm_turn 0 "The player does: ..."`. The first one to read
   `$MOVES` past its `MCURSOR` wins; the loser silently double-resolves the
   same move with stale narration.
3. **Two Claude sessions on one campaign** — each DM has its own `$DSID`
   (`uuidgen`). They write engine state via the same `clawdnd-engine` MCP but
   never see each other's `--resume` tape, so the engine gets contradictory
   `apply_check` / `apply_attack` / scene-narration calls.
4. **Twice the cost** — both DMs bill against budgets the user thought was
   one ceiling.

This isn't a theoretical race. It's the **intended** flow of `ui_playtest.sh`
*today* — the harness is self-contained — and the user has explicitly asked
for `play_party.sh` to be the play surface a playtester drives. The two
collide because each owns the DM independently.

---

## Proposed architecture

```
scripts/play_party.sh   (or play.sh underneath)
   └── owns:
        • viewer (supervised) on a chosen $PORT
        • DM agent (one claude -p, one session id, one $DSID)
        • $MOVES + $CHAT sinks under play-state/$RUN/
        • the human-paced beat loop tailing $MOVES
        • a "rundir handshake" file: play-state/$RUN/.handshake.json
          { "port": 8765, "url": "http://127.0.0.1:8765/openworlds/",
            "state_dir": "...", "moves": "...", "chat": "...",
            "campaign_id": "...", "dm_session_id": "..." }

qa/ui_playtest.sh
   └── owns:
        • the Playwright Player agent ONLY
        • bugs.ndjson, screenshots/, a11y/, console/network logs
        • scoring + summary

   └── reads (does NOT spawn):
        • the URL from --attach-to <rundir>/.handshake.json
        • the PORT/CHAT for friction signals only
```

The Player agent uses the **same** palette MCP server, but its
`CLAWDND_UIPT_URL` env var is sourced from the handshake JSON written by
`play_party.sh` (or `play.sh`) — not from a port `ui_playtest.sh` picked
itself.

### Why a handshake JSON (not a fixed env var)?

Two reasons:

1. **Per-run isolation.** `play_party.sh` already builds per-run state dirs
   under `play-state/$RUN/`. A handshake JSON dropped there means a
   playtester can attach to a *specific* live game without stomping on
   another one (the persona sweep at `qa/UI_PLAYTEST.md:139-145` runs five
   personas sequentially — each Player attaches to *its own* live game).
2. **Survives a viewer respawn.** `play_party.sh`'s supervisor restarts the
   viewer if it dies (`L294-302`). A handshake JSON written ONCE by the
   parent script (not the supervisor) tells the Player which port the parent
   chose, even if the underlying viewer process changed PID.

The handshake JSON is the **only** new contract surface.

---

## The exact patch

### 1. `scripts/play_party.sh` + `scripts/play.sh` — emit the handshake

After the port is chosen and the campaign is pre-seeded, write the handshake
JSON. This is additive — the existing flows do not read it; only the new
detached Player path will.

**Add to `scripts/play.sh` after `L43` (port chosen) and before `L57`
(state dir created):**

```bash
# --- attachment handshake -----------------------------------------------------
# After the port is chosen and STATE_DIR exists, drop a handshake JSON that an
# external Player (qa/ui_playtest.sh --attach-to) can read to attach to THIS
# live game without spawning its own viewer or DM. Written once; the viewer
# supervisor (L208-229) does not touch it.
write_handshake() {
  python3 - "$STATE_DIR/.handshake.json" "$PORT" "$STATE_DIR" "$MOVES" "$CHAT" "$DSID" <<'PY'
import json, sys
out, port, state, moves, chat, dsid = sys.argv[1:7]
json.dump({
    "schema": "clawdnd.play.handshake.v1",
    "port": int(port),
    "url": f"http://127.0.0.1:{port}/openworlds/",
    "state_dir": state,
    "moves": moves,
    "chat": chat,
    "dm_session_id": dsid,
    "campaign_id": None,  # filled by viewer once campaign exists
}, open(out, "w"), indent=2)
PY
}
# (Called after $DSID is generated; see below.)
```

Then call `write_handshake` right after `DSID="$(uuidgen ...)"` at `L87`:

```bash
DSID="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')"
write_handshake
```

And **mirror this in `scripts/play_party.sh`**:
- Insert the same `write_handshake` helper after `L102` (state dir created)
  but reading `STATE_DIR`, `PORT`, `MOVES`, `CHAT`, `DSID`.
- Call `write_handshake` right after `DSID="$(uuidgen ...)"` at `L206`.

The handshake JSON is dropped under `play-state/$RUN/.handshake.json`. (It is
gitignored — `play-state/` already is.)

### 2. `qa/ui_playtest.sh` — add `--attach-to` and strip the DM

**Add a new flag** (default OFF — keeps existing standalone behavior
during migration):

```bash
ATTACH_RUNDIR="${UIPT_ATTACH_TO:-}"   # path to play-state/$RUN/ with .handshake.json
# Allow CLI override: qa/ui_playtest.sh --attach-to play-state/play-1234 newbie newbie 30 3.00
if [ "${1:-}" = "--attach-to" ]; then
  ATTACH_RUNDIR="$2"; shift 2
fi
```

**When `$ATTACH_RUNDIR` is set, skip ALL DM scaffolding:**

```bash
if [ -n "$ATTACH_RUNDIR" ]; then
  # Attach mode: read PORT + URL from the running parent's handshake.
  HANDSHAKE="$ATTACH_RUNDIR/.handshake.json"
  [ -f "$HANDSHAKE" ] || { echo "[uipt] --attach-to dir has no .handshake.json: $HANDSHAKE" >&2; exit 5; }
  # Wait up to 60s for the handshake to be readable AND the URL to answer 200.
  for _ in $(seq 1 60); do
    PORT="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("port",""))' "$HANDSHAKE" 2>/dev/null)"
    [ -n "$PORT" ] && [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/openworlds/" 2>/dev/null)" = "200" ] && break
    sleep 1
  done
  URL="http://127.0.0.1:$PORT/openworlds/"
  # No viewer to spawn, no DM, no resolver loop, no $DSID, no $DM_CFG.
  echo "[uipt] attach mode: parent run=$ATTACH_RUNDIR port=$PORT"
else
  # ... existing standalone path: pick_port, spawn viewer, build DM_CFG, run dm_turn, spawn DM loop ...
fi
```

**Remove (in attach mode) the lines:**

- `L52` `mkdir -p ... "$RUNDIR/dm"` — no DM scratch dir needed.
- `L53-55` `MOVES` / `CHAT` / `COMBINED` file init — these belong to the
  parent now.
- `L56` `DM_CFG=` — no DM config.
- `L60-68` `pick_port` + `PORT=$(pick_port)` — the parent picked the port.
- `L71-90` DM MCP config builder — gone.
- `L114-130` viewer spawn + ready loop — replaced with the handshake wait above.
- `L132-145` `dm_turn()` — gone.
- `L147-157` DM opening turn — gone. (The parent's DM already opened the
  scene; the Player discovers the live game via the launcher.)
- `L159-183` DM-resolver loop subshell + `DMLOOP=$!` — gone. The parent's
  beat loop resolves moves.
- `L207-209` `kill "$DMLOOP"`/`kill "$VIEWER"` — gone.
- `cleanup()` at `L119` — collapses to a no-op in attach mode.

**Keep unchanged:**

- The Player MCP config build (`L92-109`).
- The Player agent invocation (`L185-201`).
- The verdict + cost extraction (`L203-205`).
- `meta.json` + scoring (`L211-225`).

### 3. Optional: scrape the parent's `chat.jsonl` for the Player verdict

Today the harness scores `console_errors` + `network_failures` purely from
the palette-captured logs. In attach mode the Player still sees the same
screen, so this works unchanged. If the scorer needs DM-side narration
context (e.g., for `completed_intro_flow`), point it at the parent's chat:

```bash
[ -n "$ATTACH_RUNDIR" ] && CHAT_FOR_SCORE="$ATTACH_RUNDIR/chat.jsonl" \
                       || CHAT_FOR_SCORE="$CHAT"
python3 "$ROOT/qa/ui_playtest_score.py" "$RUNDIR" "$PLAYER_VERDICT" "$CHAT_FOR_SCORE"
```

(Today's scorer signature is `(rundir, verdict)`. Adding the chat path is a
follow-up to land in `ui_playtest_score.py` — out of scope for this fix
unless the score needs it.)

### 4. Optional: smoke-test convenience script

Add `qa/ui_playtest_attached.sh` (one-shot composition):

```bash
#!/usr/bin/env bash
# Launch play_party.sh in background, wait for its handshake, then run
# ui_playtest.sh --attach-to <that dir>. One DM, one viewer, one Player.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="${1:-play-$(date +%H%M%S)}"
PARENT="$ROOT/play-state/$RUN"
mkdir -p "$PARENT"
CLAWDND_PLAY_MAX_TURNS=40 CLAWDND_PLAY_MAX_IDLE=300 \
  "$ROOT/scripts/play_party.sh" "${2:-baldurs-gate}" "$RUN" &
PARENT_PID=$!
trap 'kill $PARENT_PID 2>/dev/null' EXIT
# Wait for handshake (max 60s).
for _ in $(seq 1 60); do [ -f "$PARENT/.handshake.json" ] && break; sleep 1; done
[ -f "$PARENT/.handshake.json" ] || { echo "[smoke] parent never wrote handshake" >&2; exit 1; }
exec "$ROOT/qa/ui_playtest.sh" --attach-to "$PARENT" "${3:-attached-$RUN}" \
                               "${4:-baldurs-gate}" "${5:-newbie}" "${6:-30}" "${7:-3.00}"
```

This is a developer convenience, not a contract.

---

## Acceptance criteria

A smoke test that proves **no dual DM** is running at any time:

```bash
# Run the composed flow.
qa/ui_playtest_attached.sh smoke-1 baldurs-gate newbie 10 1.00 &
SMOKE=$!

# Sample every 2s for the lifetime of the run. Count concurrent DM processes.
MAX_DMS=0
while kill -0 "$SMOKE" 2>/dev/null; do
  # A DM process = claude -p with --plugin-dir AND --mcp-config dm.mcp.json.
  N=$(pgrep -f 'claude -p.*--plugin-dir.*--mcp-config.*dm\.mcp\.json' | wc -l | tr -d ' ')
  [ "$N" -gt "$MAX_DMS" ] && MAX_DMS=$N
  sleep 2
done
echo "[smoke] max concurrent DM processes: $MAX_DMS (expected ≤ 1)"
[ "$MAX_DMS" -le 1 ] || { echo "DUAL DM DETECTED" >&2; exit 1; }
```

**Pass criteria:**

1. `MAX_DMS ≤ 1` for the full duration of the run.
2. Exactly **one** `.handshake.json` exists under `play-state/`.
3. The Player's `$RUNDIR/bugs.ndjson` records at least one screenshot and at
   least one click (proves the Player actually drove the live game).
4. The parent `play-state/$RUN/chat.jsonl` shows DM narration entries with
   strictly increasing `\n`-separated timestamps in the embedded JSON — no
   duplicate "DM" lines for the same player move (which would prove the
   double-resolve race didn't happen).
5. Neither the parent nor the Player exits with code ≠ 0 caused by the
   handshake path (handshake-readable: 0 is the only correct outcome).

Manual smoke (a 5-minute check):

```bash
# Term 1
scripts/play_party.sh baldurs-gate manual-1 8765

# Term 2 (after Term 1 says "DM opened the scene")
qa/ui_playtest.sh --attach-to play-state/manual-1 manual-1 baldurs-gate newbie 10 1.00

# Verify:
#   1. ONE viewer process: pgrep -f 'viewer/server.py' | wc -l   → 1
#   2. ONE DM process:     pgrep -f 'claude -p.*dm\.mcp\.json' | wc -l → 1
#   3. The Player's bugs.ndjson and screenshots/ are populated
#   4. The DM in Term 1 narrates results of the Player's clicks
```

---

## Migration plan

Backwards-compatible rollout in three steps:

### Phase 1 (this PR) — additive

- Add `--attach-to` flag and the attach-mode branch to `qa/ui_playtest.sh`.
- Add `write_handshake` to `scripts/play.sh` + `scripts/play_party.sh`.
- **Existing standalone `ui_playtest.sh` invocations continue to work
  unchanged** — every script that calls it today (the sweep at
  `qa/UI_PLAYTEST.md:139-145`, any user habit) is untouched.
- Land `qa/ui_playtest_attached.sh` as the documented composition.

### Phase 2 (next PR) — flip the default in the sweep

- Update the persona sweep in `qa/UI_PLAYTEST.md:139-145` and any orchestrator
  (`qa/ui_playtest_aggregate.py`?) to launch ONE `play_party.sh` per sweep
  and have all five personas attach to it via `--attach-to`. Five Players,
  one DM, one viewer — five times the data for one-fifth the DM cost.
- Add a deprecation banner to standalone mode: when `ui_playtest.sh` runs
  without `--attach-to`, print `[uipt] WARN: standalone mode will be removed
  in a future release; pass --attach-to <rundir>`.

### Phase 3 (cleanup) — remove standalone

- After two releases with the deprecation banner, delete the
  standalone DM scaffolding from `ui_playtest.sh` (L52-55, L71-90, L114-130,
  L132-183, L207-209). `--attach-to` becomes mandatory.
- This is the patch that physically removes the dual-DM code path.

### Migration safety

- The handshake file is JSON with a `schema` field
  (`clawdnd.play.handshake.v1`). Future versions bump the schema; the
  attach-mode Player refuses to attach to an unknown schema.
- The Player's wait-for-handshake loop has a 60s ceiling — if the parent
  never wrote one (old `play.sh` from before phase 1), the Player exits
  cleanly with `exit 5` (new exit code, distinct from today's `2/3/4`).
- A user who runs `ui_playtest.sh` standalone (no `--attach-to`) during
  phase 1 gets today's behavior, byte-for-byte. There is **no flag day**.

---

## Out of scope (intentionally)

- **Per-persona Player isolation in attach mode.** Five Players attaching to
  one viewer-DM each drive the same browser session sequentially in the
  current sweep. Running them concurrently against one DM means five Players
  POSTing `/move` simultaneously — the parent's beat loop would interleave
  them. That's a future change to the parent's loop (batched-resolve), not
  this fix.
- **Replacing the harness's pre-mint of a PC/companion.** Today
  `ui_playtest.sh:152-157` runs a DM "opening turn" so the launcher's
  Chronicles shelf has a Resume option. In attach mode, the parent's DM has
  already done this (and seated a PC the parent's user / wizard authored).
  The Player discovers the game via the launcher just as before — but it's
  the parent's PC, not one the harness minted. This is a *better* signal
  (it's the real flow), but if a Player needs a guaranteed-canonical seed PC
  for #305, the parent must be told what to seat. That's a parent-side
  contract, not part of this fix.
- **Multiple parents on one machine.** Two parallel `play_party.sh` runs
  with distinct `$RUN` names get distinct `play-state/$RUN/.handshake.json`
  files; the Player chooses one by passing `--attach-to`. If a user runs two
  parents on the same port (e.g., both default 8765 and the second hits
  `clawdnd_choose_port`'s fallback), the second's handshake correctly
  records its actual chosen port. No new code needed.

---

## Open questions for the main agent

1. **Should attach-mode `ui_playtest.sh` re-write `meta.json` to record the
   parent's `$RUN` id?** Probably yes — the persona sweep aggregator should
   know the Player attached to a shared parent. Suggest adding
   `"attached_to": "<parent-run-id>"` to `meta.json` when `ATTACH_RUNDIR` is
   set.
2. **The current scoring rubric expects `console_errors` from the same
   browser the DM "knows about"** — in attach mode the parent's
   `chat.jsonl` is the DM's narration record. Is that the right source for
   `completed_intro_flow`? If yes, plumb `--chat-source <parent-chat>` into
   `ui_playtest_score.py`. (Today the score doesn't read the chat, so this
   may be a no-op.)
3. **Does the Eva / wizard authored-hero flow at `play.sh:100-180` interact
   with attach-mode?** I assumed yes: if `CLAWDND_PLAY_HERO` is set, the
   parent pre-seeds an authored PC, and the Player discovers + plays it.
   No code change needed — but worth a one-line note in the handshake JSON
   (`"hero_authored": true`) so the Player's persona brief can adapt
   ("you're playing this author's hero" vs "you're picking from
   Chronicles").
