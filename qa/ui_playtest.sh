#!/usr/bin/env bash
# WorldOS AI PLAYTESTER HARNESS v1 (issue #324) — a blind UI/UX test.
#
# Three processes, one run:
#   - Engine + Viewer (viewer/server.py): serves the REAL /openworlds/ UI on a free
#     port, wired with a move sink ($MOVES) + a two-sided chat log ($CHAT). Engine is
#     the SOLE writer; the viewer only reads surfaces + accepts /move intents.
#   - DM agent (claude -p, full plugin + dungeon-master skill + engine MCP): UNCHANGED
#     from qa/run_duo.sh / qa/play_human.sh. It opens the scene, then a background loop
#     resolves each player move the UI posts to $MOVES and writes narration to $CHAT,
#     which the UI polls via /chat. The DM never knows there is a UI.
#   - PLAYER agent (claude -p with ONLY the Playwright palette MCP — strict): a blind
#     newbie who SEES only the screen (screenshot / a11y_tree) and ACTS only via
#     click / type / key / wait, reporting friction via report_bug / give_up. NO source,
#     NO engine introspection, NO filesystem. It drives the real browser; its clicks
#     POST /move; the unchanged DM resolves; narration flows back onto the screen.
#
# Usage:   qa/ui_playtest.sh <runid> <world> <persona> <beats> <budget>
# Target:  qa/ui_playtest.sh play1 baldurs-gate newbie 30 3.00
#   <beats>  = max player palette ACTIONS before we stop the run (a soft cap; the player
#             may also give_up earlier). <budget> = USD cap for the PLAYER agent process.
#
# Produces under qa/ui_playtest_runs/<runid>/:
#   player/screenshots/*.png, player/a11y/*.txt, bugs.ndjson, actions.ndjson,
#   console.ndjson, network.ndjson, score.json, summary.md, meta.json
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1
. "$ROOT/qa/lib_beat_driver.sh"  # worldos_env + shared helpers

RUN="${1:-play-$(date +%H%M%S)}"
WORLD="${2:-baldurs-gate}"
PERSONA="${3:-newbie}"
BEATS="${4:-30}"          # max player palette actions (soft cap)
BUDGET="${5:-3.00}"       # USD cap for the PLAYER agent
PW_DIR="$ROOT/qa/playwright"
PW_CHANNEL="$(worldos_env UIPT_CHANNEL "")"   # "" = bundled chromium; "chrome" = system Chrome
DM_MODEL="$(worldos_env DM_MODEL opus)"
PLAYER_MODEL="$(worldos_env UIPT_PLAYER_MODEL sonnet)"
# Per-DM-turn budget scales to the model: the Opus cold-open world-build needs ~$12; the Sonnet-tuned
# $1.50 cap trips error_max_budget_usd on the Opus cold-open (the PC never seats). Sonnet unchanged.
case "$DM_MODEL" in *opus*) _uipt_dm_def=12.00 ;; *) _uipt_dm_def=1.50 ;; esac
DM_BUDGET="$(worldos_env UIPT_DM_BUDGET "$_uipt_dm_def")"        # per DM turn (model-aware)
# GLM-only settings profile (no-op for Claude). Sourced after model vars resolve, before any
# timeout/budget/retry knob is consumed. This harness spawns the DM/player itself (it does NOT
# delegate to run_duo.sh), so it must apply the profile directly. The profile keys off
# WORLDOS_DM_MODEL/WORLDOS_ACTOR_MODEL, so export the role models under those names first (the
# late WORLDOS_DM_MODEL="$DM_MODEL" near dm_turn re-asserts the same value, idempotently).
# See qa/glm_profile.sh.
WORLDOS_DM_MODEL="$DM_MODEL"
WORLDOS_ACTOR_MODEL="$PLAYER_MODEL"
# shellcheck source=glm_profile.sh
. "$ROOT/qa/glm_profile.sh"
worldos_apply_glm_profile
PERSONA_FILE="$ROOT/qa/play_player_browser_${PERSONA}.txt"

[ -f "$PERSONA_FILE" ] || { echo "[uipt] no persona brief at $PERSONA_FILE" >&2; exit 2; }
[ -d "$PW_DIR/node_modules/playwright" ] || {
  echo "[uipt] Playwright not installed. Run: (cd qa/playwright && npm install && npx playwright install chromium)" >&2
  exit 2
}

RUNDIR="$ROOT/qa/ui_playtest_runs/$RUN"
PLAYERDIR="$RUNDIR/player"
STATE_DIR="$RUNDIR/state"
rm -rf "$RUNDIR" 2>/dev/null
mkdir -p "$PLAYERDIR/screenshots" "$PLAYERDIR/a11y" "$STATE_DIR" "$RUNDIR/dm"
MOVES="$STATE_DIR/player_moves.jsonl"; : > "$MOVES"
CHAT="$RUNDIR/dm/chat.jsonl"; : > "$CHAT"
COMBINED="$RUNDIR/dm/dm.jsonl"; : > "$COMBINED"
DM_CFG="$STATE_DIR/dm.mcp.json"
PLAYER_CFG="$STATE_DIR/player.mcp.json"

# --- free port in 8990–8999 (fail if all busy) -------------------------------
pick_port() {
  local p
  for p in $(seq 8990 8999); do
    if ! (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then echo "$p"; return 0; fi
    exec 3>&- 2>/dev/null || true
  done
  return 1
}
PORT="$(pick_port)" || { echo "[uipt] no free port in 8990-8999 — aborting" >&2; exit 3; }
URL="http://127.0.0.1:$PORT/openworlds/"

# --- DM MCP config: re-root every server at THIS repo + scope engine to $STATE_DIR.
# (Identical to run_duo.sh — guards the version-skew that RED-caps a run if the DM
#  engine runs older models.py than the snapshot writer.)
python3 - "$ROOT/qa/qa.mcp.example.json" "$STATE_DIR" "$DM_CFG" "$ROOT" <<'PY'
import json, os, sys
cfg_path, state, out, root = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
cfg = json.load(open(cfg_path))
for name, srv in cfg.get("mcpServers", {}).items():
    args = srv.get("args", [])
    if "--directory" in args:
        i = args.index("--directory"); raw = args[i + 1].rstrip("/")
        if raw.startswith("./"): raw = raw[2:]
        if "/servers/" in raw: pkg = raw.rsplit("/servers/", 1)[1]
        elif raw.startswith("servers/"): pkg = raw[len("servers/"):]
        else: pkg = raw
        args[i + 1] = f"{root}/servers/{pkg}"
    if name == "worldos-engine":
        srv.setdefault("env", {})["WORLDOS_STATE_DIR"] = state
        # Dogfood FIDELITY (parity with scripts/play.sh:142 + qa/run_duo.sh): PIN the engine tools
        # (un-defer) so the DM stops burning a ~9s ToolSearch round-trip re-discovering the engine MCP
        # tools EVERY move — production has alwaysLoad default-on, so without this the dogfood overstates
        # per-move latency vs the real player surface. Set WORLDOS_ENGINE_ALWAYSLOAD=0 for the deferred
        # baseline (the latency A/B arm). This generation block is LOCAL to this QA runner (it re-roots
        # qa/qa.mcp.example.json into the run's own $DM_CFG) — NOT shared with the production play.sh gen.
        if os.environ.get("WORLDOS_ENGINE_ALWAYSLOAD", "1") == "1":
            srv["alwaysLoad"] = True
json.dump(cfg, open(out, "w"))
PY

# --- PLAYER MCP config: ONLY the Playwright palette server (strict, no other tools).
# This is the constrained surface — the player sees ONLY the screen. The palette treats
# WORLDOS_UIPT_RUNDIR as the RUN ROOT (bugs.ndjson + status.json land there; screenshots/
# a11y/action/console/network logs go under player/), so pass $RUNDIR, not $PLAYERDIR.
python3 - "$PW_DIR" "$URL" "$RUNDIR" "$PW_CHANNEL" "$PERSONA" "$PLAYER_CFG" <<'PY'
import json, sys
pw_dir, url, rundir, channel, persona, out = sys.argv[1:7]
json.dump({"mcpServers": {"worldos-uiplayer": {
    "command": "node",
    "args": [f"{pw_dir}/palette_server.js"],
    "env": {
        "WORLDOS_UIPT_URL": url,
        "WORLDOS_UIPT_RUNDIR": rundir,
        "WORLDOS_UIPT_CHANNEL": channel,
        "WORLDOS_UIPT_PERSONA": persona,
    },
}}}, open(out, "w"))
PY

echo "[uipt] run=$RUN world=$WORLD persona=$PERSONA port=$PORT beats=$BEATS budget=\$$BUDGET"
echo "[uipt] url=$URL"

# --- start engine + viewer (move sink + chat wired) --------------------------
WORLDOS_STATE_DIR="$STATE_DIR" \
WORLDOS_VIEWER_CHAT="$CHAT" WORLDOS_PLAYER_MOVES="$MOVES" \
  python3 viewer/server.py "" "$PORT" > "$RUNDIR/viewer.log" 2>&1 &
VIEWER=$!
cleanup() { kill "$VIEWER" 2>/dev/null; [ -n "${DMLOOP:-}" ] && kill "$DMLOOP" 2>/dev/null; }
trap cleanup EXIT INT TERM

# Wait for the viewer to answer /openworlds/ (200).
ready=0
for _ in $(seq 1 40); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "$URL" 2>/dev/null)"
  [ "$code" = "200" ] && { ready=1; break; }
  sleep 0.5
done
[ "$ready" = "1" ] || { echo "[uipt] viewer never became ready at $URL (see $RUNDIR/viewer.log)" >&2; exit 4; }
echo "[uipt] viewer ready."

# --- DM turn helper (claude -p, full plugin, resumed) ------------------------
DSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
DM_BRIEF="$(cat "$ROOT/qa/play_dm_duo.txt")"
# chatlog is the SHARED lib implementation (qa/lib_beat_driver.sh, reads ambient $CHAT at call
# time). SYN-01/F12-7: a local 2-arg override here used to shadow it AFTER sourcing the lib,
# silently discarding worldos_chatlog_dm's {"fallback_recovered":true} honesty stamp — never
# re-define chatlog in a runner.
# #745 (the newbie mid-stream-stall give-up): the GUI-sweep DM driver MUST bound every beat exactly
# like scripts/play.sh's dm_turn — previously this helper ran `claude -p` with NO `timeout`, NO retry,
# and NO fallback, so a DM turn that streamed partial prose via /events and then FROZE mid-generation
# hung FOREVER: dm_turn never returned → `chatlog dm` (the turn-END /chat line) never fired → the turn
# never RESOLVED on the client, leaving the player on the (now-fixed-but-slower) client stall path with
# no backend recovery at all. Here we (1) wall-clock the beat with `timeout` (tiered off the cold-open
# `first` signal via the shared worldos_dm_timeout; a frozen process is KILLED at the deadline so the
# turn returns), and (2) if the killed/failed beat left empty result text, the CALLER stitches the
# engine-logged narration tail as a fallback reply (worldos_resolve_dm_reply, which also flags the
# recovery — #749c) so the dm chat row always carries a real turn-END line → the client's pending
# clears + the bar re-enables. WORLDOS_DM_MODEL lets the
# timeout helper pick the opus cold-open tier. Bash 3.2-safe (timeout(1) from coreutils; ${arr[@]+…}).
WORLDOS_DM_MODEL="$DM_MODEL"
dm_turn() {
  local first="$1" msg="$2" out resume=() beat_timeout rc
  # SYN-01: pre-beat log-tail mark (once per beat — this driver is single-attempt) so the
  # caller's resolve can tell a GENUINE #357 recovery from RECYCLED pre-beat prose.
  worldos_dm_prebeat_mark "$STATE_DIR"
  # #623 dogfood FIDELITY: prepend the live-progress rule (the ONE shared WORLDOS_LIVE_PROGRESS_RULE
  # in qa/lib_beat_driver.sh — parity with scripts/play.sh:288 + scripts/play_party.sh + the codex DM)
  # so the DM logs an EARLY /events narration beat. Its ABSENCE here mirrored the SOLO-path #623 bug:
  # the dogfood DM emitted nothing to /events until the full ~82s beat completed, so the viewer stayed
  # blank and the playtest OVERSTATED perceived latency vs production. This is the MODEL-COOPERATIVE
  # half; the caller ALSO emits the model-INDEPENDENT heartbeat (worldos_emit_progress_heartbeat) per
  # move before the resolve, exactly like scripts/play.sh:620. Additive — engine stays the sole writer.
  msg="$WORLDOS_LIVE_PROGRESS_RULE"$'\n\n'"$msg"
  [ "$first" = "0" ] && resume=(--resume "$DSID") || resume=(--session-id "$DSID")
  beat_timeout="$(worldos_dm_timeout "$first")"
  out="$RUNDIR/dm/turn.$(date +%s%N).jsonl"
  timeout "$beat_timeout" \
    claude -p "$msg" "${resume[@]}" --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
    --model "$DM_MODEL" --permission-mode bypassPermissions --max-budget-usd "$DM_BUDGET" \
    --output-format stream-json --verbose > "$out" 2>> "$RUNDIR/dm/dm.err"
  rc=$?
  [ "$rc" -ne 0 ] && echo "[uipt] DM turn rc=$rc (timeout=${beat_timeout}s) — relying on engine-logged narration fallback" >&2
  cat "$out" >> "$COMBINED"
  # Echo the beat's final result text via the SYN-01 shared classification front door: it notes
  # $out for the caller's worldos_resolve_dm_reply and echoes NOTHING on an error-class result
  # (a 401's "result" text is the API's error string, never a reply). The #357 fallback (recover
  # the engine-logged narration tail when a killed/failed beat left this empty) is applied by
  # the CALLER via worldos_resolve_dm_reply — a direct call, because dm_turn runs in a command
  # substitution where the #749c fallback_recovered flag (a global) could never escape the subshell.
  worldos_dm_final_text "$out" "$STATE_DIR" "$rc"
}

# --- DM opens the scene so a LIVE, playable game exists (the launcher's Chronicles
# shelf will then offer "Resume Chronicle", and /chat carries the opening narration).
# Mirrors play_human.sh: seat a level-3 PC + a companion, open a personal scene. We do
# NOT hardcode a specific canon PC (so this exercises #305: a dead character must not be
# seated/offered). The newbie will discover + click into this game via the real launcher.
echo "[uipt] DM opening the scene…"
DMSG="$(dm_turn 1 "$DM_BRIEF

Begin a SOLO session for a brand-new human player in this world: start_world(\"$WORLD\"), start_session, seat a fitting level-3 PLAYER CHARACTER (a LIVING canon figure via load_canon_character(kind=\"player\", add_to_party=true) — NEVER a dead/fallen character; apply sensible skills/spells), and bring in ONE roster companion the player meets in the scene. Then open a human-scale, personal scene with real quoted dialogue and hand the player an open moment + a clear choice. Their actions will arrive next as tagged moves.")"
# #357/#749c: recover the engine-logged narration tail when the turn died with no result text;
# a recovered reply stamps fallback_recovered:true on the dm chat row (worldos_chatlog_dm).
worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
# SYN-01: an empty resolved reply is a FAILED beat. The old masking default ("The scene is
# set. What do you do?") pretended a scene existed; record the wrapper-authored VISIBLE failure
# row instead — it is still a real turn-END dm row, so the client's pending state clears.
if [ -z "$DMSG" ]; then
  echo "[uipt] WARN: DM produced no opening (see $RUNDIR/dm/dm.err) — recording a visible failure beat; the player may land in a thin scene." >&2
  worldos_chatlog_dm_failed
else
  worldos_chatlog_dm "$DMSG"
fi

# --- background DM-resolver loop: tail $MOVES, resolve each new move, append narration
# to $CHAT (the UI shows it via /chat). Identical shape to play_human.sh's loop. Runs
# until the harness kills it (player done / beats hit / budget out).
(
  MCURSOR="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; MCURSOR="${MCURSOR:-0}"
  # #623 dogfood FIDELITY: a 0-based continuing-beat index so the per-move heartbeat ROTATES its
  # teaser (worldos_emit_progress_heartbeat, below) exactly as scripts/play.sh:620 rotates off DM_TURNS.
  DMLOOP_BEAT=0
  while true; do
    total="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; total="${total:-0}"
    if [ "$total" -gt "$MCURSOR" ]; then
      new="$(tail -n +"$((MCURSOR + 1))" "$MOVES" 2>/dev/null)"; MCURSOR="$total"
      PMSG="$(printf '%s' "$new" | jq -rs 'map("[\(.kind)] \(.text // .name // "")") | join("  ")' 2>/dev/null)"
      [ -z "$PMSG" ] && continue
      chatlog player "$PMSG"
      # #623 dogfood FIDELITY: emit the IMMEDIATE, model-INDEPENDENT progress heartbeat NOW — right
      # after the player's move is echoed and BEFORE the long DM think — so /events has a wrapper
      # `narration` row within ~1s and the OpenWorlds viewer flips its spinner to "the scene is
      # arriving above" (app.jsx isWrapperProgressLine → notePendingProgress), exactly as production
      # (scripts/play.sh:620). Without this the dogfood's chronicle stayed BLANK until the final
      # persist (~+82s), making the GUI playtest OVERSTATE perceived latency vs the real player surface.
      # This lane has no CAMPAIGN_ID var, so derive the LIVE id the way play.sh does (the engine-
      # authoritative most-recently-played save; #640 — never a blind first-dir pick when the engine
      # can answer), falling back to the first subdir only if the engine can't. A blank id no-ops the
      # helper (best-effort; never fails a beat) — so the derivation is the crux. Engine stays the SOLE
      # writer (the heartbeat routes through log_engine_narration). first=0 + DMLOOP_BEAT → rotates.
      HB_CID="$(worldos_live_campaign_id "$ROOT" "$STATE_DIR" "$WORLD")"
      [ -z "$HB_CID" ] && HB_CID="$(find "$STATE_DIR/campaigns" -mindepth 1 -maxdepth 1 -type d -exec basename {} \; 2>/dev/null | head -n1)"
      worldos_emit_progress_heartbeat "$HB_CID" 0 "$DMLOOP_BEAT"
      DMLOOP_BEAT=$((DMLOOP_BEAT + 1))
      DMSG="$(dm_turn 0 "The player does:

$PMSG

Resolve it through the engine (roll checks, apply casts/attacks, voice NPCs) and narrate the next beat as a played scene. Hand the moment back to the player.")"
      # #357/#749c: same recovery + honesty stamp as the opening turn (direct call, see dm_turn).
      # SYN-01: an empty resolved reply is a FAILED beat — the visible failure row replaces the
      # old "..." masking default (still a turn-END dm row, so the client's pending clears).
      worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
      if [ -z "$DMSG" ]; then
        worldos_chatlog_dm_failed
      else
        worldos_chatlog_dm "$DMSG"
      fi
    else
      sleep 2
    fi
  done
) &
DMLOOP=$!
echo "[uipt] DM resolver loop up (pid $DMLOOP)."

# --- PLAYER agent: blind newbie drives the real browser via the palette MCP only.
# A single claude -p session: the persona brief + the action budget. The palette tools
# log every screenshot/click/bug under $PLAYERDIR. We cap the run with a turn ceiling in
# the prompt AND the agent's own --max-budget-usd; the palette also short-circuits on
# give_up (writes status.json).
PERSONA_BRIEF="$(cat "$PERSONA_FILE")"
PSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
echo "[uipt] player agent starting (persona=$PERSONA, max ~$BEATS actions)…"
PLAYER_OUT="$RUNDIR/player/player.jsonl"
claude -p "$PERSONA_BRIEF

You have a budget of about $BEATS actions for this whole session. Spend them trying to start and play the story, reporting friction as you go. When you have either (a) genuinely gotten stuck after reporting it, or (b) played a few real turns and seen enough to judge the experience, you may stop — give a final 1-2 sentence verdict and, if you got stuck, call give_up. Start now." \
  --session-id "$PSID" --mcp-config "$PLAYER_CFG" --strict-mcp-config \
  --model "$PLAYER_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
  --output-format stream-json --verbose > "$PLAYER_OUT" 2>> "$RUNDIR/player/player.err"
PLAYER_RC=$?
echo "[uipt] player agent finished (rc=$PLAYER_RC)."

# Final reply text (the player's verdict) for the summary.
PLAYER_VERDICT="$(jq -rs 'map(select(.type=="result"))[-1].result // ""' "$PLAYER_OUT" 2>/dev/null)"
PLAYER_COST="$(jq -rs '[.[]|select(.type=="result")|.total_cost_usd//0]|add // 0' "$PLAYER_OUT" 2>/dev/null)"

# Stop the DM loop + viewer before scoring.
kill "$DMLOOP" 2>/dev/null; DMLOOP=""
kill "$VIEWER" 2>/dev/null

# --- meta.json ---------------------------------------------------------------
python3 - "$RUNDIR/meta.json" "$RUN" "$WORLD" "$PERSONA" "$PORT" "$BEATS" "$BUDGET" "$PLAYER_COST" "$PLAYER_RC" <<'PY'
import json, sys, datetime
out, run, world, persona, port, beats, budget, cost, rc = sys.argv[1:10]
json.dump({
    "run": run, "world": world, "persona": persona, "port": int(port),
    "beats_cap": int(beats), "budget_usd": float(budget),
    "player_cost_usd": round(float(cost or 0), 4), "player_rc": int(rc),
    "finished_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
}, open(out, "w"), indent=2)
PY

# --- score + summary ---------------------------------------------------------
echo "[uipt] scoring + summarizing…"
python3 "$ROOT/qa/ui_playtest_score.py" "$RUNDIR" "$PLAYER_VERDICT" 2>> "$RUNDIR/score.err"
SCORE_RC=$?

echo "[uipt] done. dir=$RUNDIR"
if [ -f "$RUNDIR/summary.md" ]; then
  echo "----- summary.md -----"; cat "$RUNDIR/summary.md"
fi
exit "$SCORE_RC"
