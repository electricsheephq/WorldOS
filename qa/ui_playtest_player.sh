#!/usr/bin/env bash
# WorldOS T3 NATIVE-PLAYER PLAYTESTER HARNESS (issue #1436 W5c Unit 2 / #1322 the T3 gate).
#
# The browser sibling qa/ui_playtest.sh drives the /openworlds/ BROWSER UI. THIS runner drives the
# standalone Unity **WorldOSPlayer.app** window — the RENDERED surface the T3 gate exits on: a blind
# AI playtester completes a quest loop (walk to an NPC → take a quest → cross rooms → fight → resolve
# → report satisfaction) entirely in the native window, scored by the SAME qa/ui_playtest_score.py.
#
# Four processes, one run (the DM side is UNCHANGED from qa/ui_playtest.sh / qa/run_duo.sh):
#   - Engine + Viewer (viewer/server.py): serves the engine surfaces (/combat-surface, /events) that
#     the player build consumes, wired with a move sink ($MOVES) + a two-sided chat log ($CHAT).
#     Engine is the SOLE writer; the viewer only reads surfaces + accepts /move intents.
#   - The pre-seeded campaign: qa/seed_gfx_combat.py mints camp_gfxdemo01 (hero-vs-goblin GRID combat
#     on the crypt plate) so a LIVE, renderable scene exists the instant the player app connects.
#   - WorldOSPlayer.app: launched with the env launch-contract (WORLDOS_ENGINE_BASE_URL /
#     WORLDOS_CAMPAIGN_ID — PlayerLauncher.swift / CombatSurfaceClient.cs). Renders camp_gfxdemo01 and
#     posts move-intents through the existing /move kinds. Pure consumer; never writes engine state.
#   - DM agent (claude -p, full plugin): the SAME dm_turn helper + background resolver loop as
#     qa/ui_playtest.sh. Grounds on the pre-seeded campaign, opens the scene, then resolves each move
#     the player posts and narrates the next beat. The DM never knows there is a native window.
#   - PLAYER agent (claude -p, ONLY the NATIVE palette MCP — strict): a blind player that SEES only
#     the window (screenshot) and ACTS only via click(x,y)/type/key/wait, reporting friction via
#     report_bug/give_up/finish. NO source, NO engine introspection, NO filesystem. It drives the
#     REAL Unity window; its clicks POST /move; the unchanged DM resolves; the render updates.
#
# ────────────────────────────────────────────────────────────────────────────────────────────────
# macOS PERMISSIONS (REQUIRED — the native palette FAILS LOUD, never silently skips, if either is
# missing; granting them is an OWNER action, not a test outcome):
#   1. Screen Recording  →  System Settings ▸ Privacy & Security ▸ Screen Recording
#        Enable the app that runs THIS script (Terminal / iTerm / your shell), then RESTART it.
#        Needed both to screencapture the player window AND for CGWindowList to see the window at all.
#   2. Accessibility     →  System Settings ▸ Privacy & Security ▸ Accessibility
#        Enable the same app, then RESTART it. Needed for synthetic clicks/keystrokes to land.
# Preflight both without launching a run:   qa/ui_playtest_player.sh --preflight
# ────────────────────────────────────────────────────────────────────────────────────────────────
#
# Usage:   qa/ui_playtest_player.sh <runid> <beats> <budget>
#   <runid>  = names qa/ui_playtest_runs/<runid>/ (wiped + recreated).
#   <beats>  = soft cap on player palette actions.  <budget> = USD cap for the PLAYER agent.
# Env knobs:
#   WORLDOS_PLAYER_APP        — path to WorldOSPlayer.app (default: ~/Applications, /Applications,
#                               then ~/worldos-session-notes/w5a-build/WorldOSPlayer.app).
#   WORLDOS_NPT_WINDOW_OWNER  — CGWindowList owner name (default "WorldOSPlayer").
#   WORLDOS_DM_MODEL / WORLDOS_UIPT_PLAYER_MODEL — per-agent models (DM opus; player sonnet).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1

NPT_DIR="$ROOT/qa/native_palette"
PW_DIR="$ROOT/qa/playwright"                     # the palette shares the MCP SDK installed here
OWNER="${WORLDOS_NPT_WINDOW_OWNER:-WorldOSPlayer}"
CID="camp_gfxdemo01"                             # the id seed_gfx_combat.py + the box renderer pin

# --- locate the installed MCP SDK (worktrees have no node_modules — accept an explicit override or
# the canonical checkout's qa/playwright install, matching native_palette_server.js's resolution). --
find_sdk_node_modules() {
  local c
  for c in "${WORLDOS_NPT_NODE_MODULES:-}" \
           "$PW_DIR/node_modules" \
           "/Users/lume/WorldOS/qa/playwright/node_modules"; do
    [ -n "$c" ] && [ -d "$c/@modelcontextprotocol" ] && { echo "$c"; return 0; }
  done
  return 1
}

# --- locate the player app ---------------------------------------------------
find_player_app() {
  local c
  for c in "${WORLDOS_PLAYER_APP:-}" \
           "$HOME/Applications/WorldOSPlayer.app" \
           "/Applications/WorldOSPlayer.app" \
           "$HOME/worldos-session-notes/w5a-build/WorldOSPlayer.app"; do
    [ -n "$c" ] && [ -d "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}

# --- --preflight: validate prerequisites + permissions, then exit (no run) ---
if [ "${1:-}" = "--preflight" ]; then
  rc=0
  echo "[t3-preflight] checking prerequisites…"
  if APP="$(find_player_app)"; then echo "  ✓ player app: $APP"; else echo "  ✗ WorldOSPlayer.app not found (set WORLDOS_PLAYER_APP)"; rc=1; fi
  if SDK_NM="$(find_sdk_node_modules)"; then echo "  ✓ MCP SDK present ($SDK_NM)"; else echo "  ✗ palette deps missing — (cd qa/playwright && npm install)"; rc=1; fi
  if node "$NPT_DIR/native_palette_server.js" --selfcheck >/tmp/npt_selfcheck.json 2>&1 && grep -q '"ok":true' /tmp/npt_selfcheck.json; then
    echo "  ✓ native palette selfcheck: 9-tool contract OK"; else echo "  ✗ native palette selfcheck FAILED:"; cat /tmp/npt_selfcheck.json; rc=1; fi
  if command -v swiftc >/dev/null 2>&1 && swiftc -typecheck "$NPT_DIR/native_input.swift" 2>/tmp/npt_swift.err; then
    echo "  ✓ swift helper compiles"; else echo "  ✗ swift helper did not compile:"; cat /tmp/npt_swift.err 2>/dev/null; rc=1; fi
  PERMS="$(swift "$NPT_DIR/native_input.swift" checkperms 2>/dev/null)"
  echo "  · permission probe: $PERMS"
  echo "$PERMS" | grep -q '"screen_recording":true' || { echo "  ✗ SCREEN RECORDING missing → System Settings ▸ Privacy & Security ▸ Screen Recording (enable this terminal, restart it)"; rc=1; }
  echo "$PERMS" | grep -q '"accessibility":true'   || { echo "  ✗ ACCESSIBILITY missing → System Settings ▸ Privacy & Security ▸ Accessibility (enable this terminal, restart it)"; rc=1; }
  [ "$rc" = 0 ] && echo "[t3-preflight] ALL GREEN — ready to run the T3 gate." || echo "[t3-preflight] NOT READY (see ✗ above)."
  exit "$rc"
fi

. "$ROOT/qa/lib_beat_driver.sh"  # worldos_env + shared DM helpers (dm_timeout/resolve/chatlog)

RUN="${1:-t3-$(date +%H%M%S)}"
BEATS="${2:-40}"
BUDGET="${3:-4.00}"
DM_MODEL="$(worldos_env DM_MODEL opus)"
PLAYER_MODEL="$(worldos_env UIPT_PLAYER_MODEL sonnet)"
case "$DM_MODEL" in *opus*) _dm_def=12.00 ;; *) _dm_def=1.50 ;; esac
DM_BUDGET="$(worldos_env UIPT_DM_BUDGET "$_dm_def")"
WORLDOS_DM_MODEL="$DM_MODEL"
WORLDOS_ACTOR_MODEL="$PLAYER_MODEL"
# shellcheck source=glm_profile.sh
. "$ROOT/qa/glm_profile.sh"; worldos_apply_glm_profile

PERSONA_FILE="$ROOT/qa/native_palette/play_player_native_t3.txt"
[ -f "$PERSONA_FILE" ] || { echo "[t3] no persona brief at $PERSONA_FILE" >&2; exit 2; }
SDK_NM="$(find_sdk_node_modules)" || {
  echo "[t3] MCP SDK not installed. Run: (cd qa/playwright && npm install)" >&2; exit 2; }
APP="$(find_player_app)" || { echo "[t3] WorldOSPlayer.app not found (set WORLDOS_PLAYER_APP). Run --preflight." >&2; exit 2; }
PLAYER_BIN="$APP/Contents/MacOS/WorldOSPlayer"
[ -x "$PLAYER_BIN" ] || { echo "[t3] player binary not executable: $PLAYER_BIN" >&2; exit 2; }

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

# --- fail-loud permission preflight BEFORE we spend anything ------------------
PERMS="$(swift "$NPT_DIR/native_input.swift" checkperms 2>/dev/null)"
if ! echo "$PERMS" | grep -q '"screen_recording":true'; then
  echo "[t3] FATAL: Screen Recording NOT granted → System Settings ▸ Privacy & Security ▸ Screen Recording" >&2
  echo "       (enable the app running this script, then RESTART it). probe=$PERMS" >&2; exit 5; fi
if ! echo "$PERMS" | grep -q '"accessibility":true'; then
  echo "[t3] FATAL: Accessibility NOT granted → System Settings ▸ Privacy & Security ▸ Accessibility" >&2
  echo "       (enable the app running this script, then RESTART it). probe=$PERMS" >&2; exit 5; fi
echo "[t3] permissions OK: $PERMS"

# --- free port in 8990–8999 --------------------------------------------------
pick_port() { local p; for p in $(seq 8990 8999); do
  if ! (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then echo "$p"; return 0; fi
  exec 3>&- 2>/dev/null || true; done; return 1; }
PORT="$(pick_port)" || { echo "[t3] no free port in 8990-8999 — aborting" >&2; exit 3; }
BASE_URL="http://127.0.0.1:$PORT"

# --- seed the pre-minted combat campaign (engine = sole writer) --------------
# uv --directory cd's into servers/engine, so pass the seed by ABSOLUTE path (per its docstring).
echo "[t3] seeding $CID (hero-vs-goblin grid combat)…"
SEED_JSON="$(WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory servers/engine python "$ROOT/qa/seed_gfx_combat.py" "$STATE_DIR" 2>"$RUNDIR/seed.err")" \
  || { echo "[t3] seed failed (see $RUNDIR/seed.err)" >&2; cat "$RUNDIR/seed.err" >&2; exit 4; }
echo "[t3] seeded: $SEED_JSON"

# --- DM MCP config: re-root every server at THIS repo + scope engine to $STATE_DIR (== ui_playtest.sh).
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
        if os.environ.get("WORLDOS_ENGINE_ALWAYSLOAD", "1") == "1":
            srv["alwaysLoad"] = True
json.dump(cfg, open(out, "w"))
PY

# --- PLAYER MCP config: ONLY the NATIVE palette server (strict, no other tools). The palette treats
# WORLDOS_NPT_RUNDIR as the RUN ROOT (bugs.ndjson + status.json there; screenshots/actions under player/).
python3 - "$NPT_DIR" "$RUNDIR" "$OWNER" "$SDK_NM" "$PLAYER_CFG" <<'PY'
import json, sys
npt_dir, rundir, owner, node_modules, out = sys.argv[1:6]
json.dump({"mcpServers": {"worldos-nativeplayer": {
    "command": "node",
    "args": [f"{npt_dir}/native_palette_server.js"],
    "env": {
        "WORLDOS_NPT_RUNDIR": rundir,
        "WORLDOS_NPT_PERSONA": "t3-native",
        "WORLDOS_NPT_WINDOW_OWNER": owner,
        "WORLDOS_NPT_NODE_MODULES": node_modules,
    },
}}}, open(out, "w"))
PY

echo "[t3] run=$RUN port=$PORT beats=$BEATS budget=\$$BUDGET app=$APP"

# --- start engine + viewer (move sink + chat wired) --------------------------
WORLDOS_STATE_DIR="$STATE_DIR" \
WORLDOS_VIEWER_CHAT="$CHAT" WORLDOS_PLAYER_MOVES="$MOVES" \
  python3 viewer/server.py "" "$PORT" > "$RUNDIR/viewer.log" 2>&1 &
VIEWER=$!
PLAYER_APP_PID=""
cleanup() {
  kill "$VIEWER" 2>/dev/null
  [ -n "${DMLOOP:-}" ] && kill "$DMLOOP" 2>/dev/null
  [ -n "$PLAYER_APP_PID" ] && kill "$PLAYER_APP_PID" 2>/dev/null
  osascript -e 'quit app "WorldOSPlayer"' >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

ready=0
for _ in $(seq 1 40); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/combat-surface?campaign=$CID" 2>/dev/null)"
  [ "$code" = "200" ] && { ready=1; break; }
  sleep 0.5
done
[ "$ready" = "1" ] || { echo "[t3] viewer never served /combat-surface at $BASE_URL (see $RUNDIR/viewer.log)" >&2; exit 4; }
echo "[t3] viewer ready — /combat-surface serving $CID."

# --- launch the native player build with the env launch-contract -------------
# WorldOSPlayer.app reads WORLDOS_ENGINE_BASE_URL + WORLDOS_CAMPAIGN_ID at startup
# (CombatSurfaceClient.Start) and renders the surface. ONE live GUI harness at a time — we quit any
# stale instance first, then launch fresh (the app activates rather than duplicating on relaunch).
osascript -e 'quit app "WorldOSPlayer"' >/dev/null 2>&1 || true
sleep 1
WORLDOS_ENGINE_BASE_URL="$BASE_URL" WORLDOS_CAMPAIGN_ID="$CID" "$PLAYER_BIN" \
  > "$RUNDIR/player_app.log" 2>&1 &
PLAYER_APP_PID=$!
echo "[t3] player app launched (pid $PLAYER_APP_PID) — engine=$BASE_URL campaign=$CID"
# Give the Unity build a beat to open its window + first combat-surface fetch before the agent looks.
sleep 6

# --- DM turn helper + opening + resolver loop (IDENTICAL shape to qa/ui_playtest.sh) ----------------
DSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
DM_BRIEF="$(cat "$ROOT/qa/play_dm_duo.txt")"
WORLDOS_DM_MODEL="$DM_MODEL"
dm_turn() {
  local first="$1" msg="$2" out resume=() beat_timeout rc
  worldos_dm_prebeat_mark "$STATE_DIR"
  msg="$WORLDOS_LIVE_PROGRESS_RULE"$'\n\n'"$msg"
  [ "$first" = "0" ] && resume=(--resume "$DSID") || resume=(--session-id "$DSID")
  beat_timeout="$(worldos_dm_timeout "$first")"
  out="$RUNDIR/dm/turn.$(date +%s%N).jsonl"
  timeout "$beat_timeout" \
    claude -p "$msg" "${resume[@]}" --plugin-dir "$ROOT" --mcp-config "$DM_CFG" --strict-mcp-config \
    --model "$DM_MODEL" --permission-mode bypassPermissions --max-budget-usd "$DM_BUDGET" \
    --output-format stream-json --verbose > "$out" 2>> "$RUNDIR/dm/dm.err"
  rc=$?
  [ "$rc" -ne 0 ] && echo "[t3] DM turn rc=$rc (timeout=${beat_timeout}s) — relying on engine-logged narration fallback" >&2
  cat "$out" >> "$COMBINED"
  worldos_dm_final_text "$out" "$STATE_DIR" "$rc"
}

# The campaign is PRE-SEEDED (camp_gfxdemo01), so — unlike ui_playtest.sh's cold-open — the DM GROUNDS
# on the existing state and opens the scene; it does NOT start_world/re-seat (mirrors mechanism_probe.sh).
echo "[t3] DM opening the scene on the pre-seeded campaign…"
DMSG="$(dm_turn 1 "$DM_BRIEF

You are resuming an IN-PROGRESS session (campaign_id=\"$CID\") — the party + a live combat encounter are ALREADY seeded (a level-4 fighter PC vs a goblin in a firelit crypt on a combat grid). Do NOT start_world or re-seat anyone. FIRST call scene_context(campaign_id=\"$CID\") (or get_state) to re-ground on the party, the combat, and the grid. Then open the scene with real quoted narration and hand the player an open moment + a clear choice. Their actions arrive next as tagged moves; resolve them THROUGH the engine and narrate.")"
worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
if [ -z "$DMSG" ]; then
  echo "[t3] WARN: DM produced no opening (see $RUNDIR/dm/dm.err) — recording a visible failure beat." >&2
  worldos_chatlog_dm_failed
else
  worldos_chatlog_dm "$DMSG"
fi

# --- background DM-resolver loop (IDENTICAL to ui_playtest.sh) ----------------
(
  MCURSOR="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; MCURSOR="${MCURSOR:-0}"
  DMLOOP_BEAT=0
  while true; do
    total="$(wc -l < "$MOVES" 2>/dev/null | tr -d ' ')"; total="${total:-0}"
    if [ "$total" -gt "$MCURSOR" ]; then
      new="$(tail -n +"$((MCURSOR + 1))" "$MOVES" 2>/dev/null)"; MCURSOR="$total"
      PMSG="$(printf '%s' "$new" | jq -rs 'map("[\(.kind)] \(.text // .name // "")") | join("  ")' 2>/dev/null)"
      [ -z "$PMSG" ] && continue
      chatlog player "$PMSG"
      HB_CID="$CID"
      worldos_emit_progress_heartbeat "$HB_CID" 0 "$DMLOOP_BEAT"
      DMLOOP_BEAT=$((DMLOOP_BEAT + 1))
      DMSG="$(dm_turn 0 "The player does:

$PMSG

Resolve it through the engine (roll checks, apply casts/attacks, voice NPCs) and narrate the next beat as a played scene. Hand the moment back to the player.")"
      worldos_resolve_dm_reply "$DMSG" "$STATE_DIR"; DMSG="$WORLDOS_DM_REPLY"
      if [ -z "$DMSG" ]; then worldos_chatlog_dm_failed; else worldos_chatlog_dm "$DMSG"; fi
    else
      sleep 2
    fi
  done
) &
DMLOOP=$!
echo "[t3] DM resolver loop up (pid $DMLOOP)."

# --- PLAYER agent: blind player drives the NATIVE window via the native palette MCP only ----------
PERSONA_BRIEF="$(cat "$PERSONA_FILE")"
PSID="$(python3 -c 'import uuid;print(uuid.uuid4())')"
echo "[t3] player agent starting (native palette, max ~$BEATS actions)…"
PLAYER_OUT="$RUNDIR/player/player.jsonl"
claude -p "$PERSONA_BRIEF

You have a budget of about $BEATS actions for this whole session. Play the quest loop in the rendered window: look (screenshot), move toward the encounter, act, and see the result, reporting friction as you go. When you have either (a) genuinely gotten stuck after reporting it, or (b) completed the loop and seen enough to judge the experience, stop — give a final 1-2 sentence verdict via finish(satisfaction, verdict), or give_up if you are stuck. Start now." \
  --session-id "$PSID" --mcp-config "$PLAYER_CFG" --strict-mcp-config \
  --model "$PLAYER_MODEL" --permission-mode bypassPermissions --max-budget-usd "$BUDGET" \
  --output-format stream-json --verbose > "$PLAYER_OUT" 2>> "$RUNDIR/player/player.err"
PLAYER_RC=$?
echo "[t3] player agent finished (rc=$PLAYER_RC)."

PLAYER_VERDICT="$(jq -rs 'map(select(.type=="result"))[-1].result // ""' "$PLAYER_OUT" 2>/dev/null)"
PLAYER_COST="$(jq -rs '[.[]|select(.type=="result")|.total_cost_usd//0]|add // 0' "$PLAYER_OUT" 2>/dev/null)"

# Stop the DM loop + viewer + player app before scoring.
kill "$DMLOOP" 2>/dev/null; DMLOOP=""
kill "$VIEWER" 2>/dev/null
kill "$PLAYER_APP_PID" 2>/dev/null; osascript -e 'quit app "WorldOSPlayer"' >/dev/null 2>&1 || true

# --- meta.json (state_dir lets the scorer resolve the campaign snapshot for structural_coverage) ---
python3 - "$RUNDIR/meta.json" "$RUN" "$CID" "$PORT" "$BEATS" "$BUDGET" "$PLAYER_COST" "$PLAYER_RC" "$STATE_DIR" <<'PY'
import json, sys, datetime
out, run, cid, port, beats, budget, cost, rc, state_dir = sys.argv[1:10]
json.dump({
    "run": run, "world": cid, "persona": "t3-native", "surface": "native-player",
    "port": int(port), "beats_cap": int(beats), "budget_usd": float(budget),
    "player_cost_usd": round(float(cost or 0), 4), "player_rc": int(rc),
    "state_dir": state_dir,
    "finished_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
}, open(out, "w"), indent=2)
PY

echo "[t3] scoring + summarizing…"
python3 "$ROOT/qa/ui_playtest_score.py" "$RUNDIR" "$PLAYER_VERDICT" 2>> "$RUNDIR/score.err"
SCORE_RC=$?
echo "[t3] done. dir=$RUNDIR"
if [ -f "$RUNDIR/summary.md" ]; then echo "----- summary.md -----"; cat "$RUNDIR/summary.md"; fi
exit "$SCORE_RC"
