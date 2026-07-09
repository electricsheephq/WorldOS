#!/usr/bin/env bash
# WorldOS PLAYER SMOKE — the deterministic, headless-of-agents post-build check (#1443).
#
# The T3 native-palette gate (qa/ui_playtest_player.sh) needs an LLM player (~$3, several minutes)
# and was BLIND for weeks before anyone noticed (Mission Control Spaces silently broke every
# screenshot/click — see #1443). This script is the standing, FREE (~30-60s, no LLM) substitute
# that should run after EVERY player rebuild: it boots the SAME camp fixture + WorldOSPlayer.app,
# then drives a fixed SCRIPTED sequence through the exact same native-palette primitives
# (qa/native_palette/native_palette_core.js) a blind player agent would use —
#
#   screenshot -> click a known walkable cell (move) -> wait -> screenshot
#              -> click the goblin (on-turn attack)  -> wait -> screenshot
#
# — and asserts, against the ENGINE'S OWN campaign snapshot (ground truth, not the public/player-
# facing surface):
#   1. the mover's grid cell CHANGED (the click's raycast really landed and posted move_to_cell)
#   2. the goblin's current_hp DROPPED (the attack click really landed and the engine resolved it —
#      deterministic because the fixture (qa/seed_gfx_camp_smoke.py) sandboxes force_hit so the
#      attack always connects; damage is still rolled normally, so hp still drops by a real amount)
#   3. MOTION-LIVENESS: consecutive glide frames captured during each action differ (a frozen/black
#      viewport would pass "the engine mutated" but fail this — the #1443 bug's actual symptom was
#      screenshots silently not updating at all)
#
# This is a GATE, not a scored run: no LLM, no scores_ledger row (see docs/RUNBOOK-INDEX.md — free,
# every rebuild, alongside fast_gate). Frames + a smoke_result.json land in the run dir for evidence.
#
# CLICK-TARGET CALIBRATION (read before debugging a FAIL here): click pixels are computed from grid
# cells via the locked dimetric camera projection qa/visual_pregate.py::CameraSpec documents for the
# STRUCTURAL/asset-gen capture pipeline (paint_combat_v1.cs). A live validation run (#1443) proved
# the CROSS-SPACE mechanism end-to-end — a real synthetic click landed on the real window and the
# engine registered a real move — but also surfaced that the LIVE gameplay camera/view (WorldOSPlayer
# runtime, CombatSurfaceClient.cs) may frame the scene differently than that offline capture camera
# (e.g. an establishing/pre-combat shot before the tactical grid view settles), so exact cell->pixel
# math may need on-box recalibration. This is a SEPARATE, orthogonal gap from the #1443 bug (Spaces
# blindness) — this script still fails LOUD with a clear reason rather than silently passing, which
# is the property #1443 needed. If this FAILs on a build known to be otherwise healthy, capture one
# frame by hand (`node qa/native_palette/player_smoke_driver.js` won't do it standalone — use
# native_palette_core.js's captureWindow() directly, no click) and re-derive the projection constants.
#
# Usage:   qa/player_smoke.sh [<runid>]
# Env:     WORLDOS_PLAYER_APP        — path to WorldOSPlayer.app (same search order as the T3 runner)
#          WORLDOS_NPT_WINDOW_OWNER  — CGWindowList owner name (default "WorldOSPlayer")
#          WORLDOS_NPT_HELPER        — path to a prebuilt native_input binary (else compiled fresh)
#          WORLDOS_NPT_FULLSCREEN_FALLBACK — "1" to allow the full-screen+crop fallback capture
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT" || exit 1

NPT_DIR="$ROOT/qa/native_palette"
OWNER="${WORLDOS_NPT_WINDOW_OWNER:-WorldOSPlayer}"
CID="camp_gfxdemo01"

# --- shared boot helpers (#1443 — see qa/ui_playtest_player.sh for the sibling T3 harness) -------
. "$ROOT/qa/lib_native_player_boot.sh"

RUN="${1:-smoke-$(date +%H%M%S)}"
RUNDIR="$ROOT/qa/player_smoke_runs/$RUN"
PLAYERDIR="$RUNDIR/player"
STATE_DIR="$RUNDIR/state"
rm -rf "$RUNDIR" 2>/dev/null
mkdir -p "$PLAYERDIR/screenshots" "$STATE_DIR"

# --- fail-loud permission preflight (same two grants the T3 gate requires) ----------------------
PERMS="$(swift "$NPT_DIR/native_input.swift" checkperms 2>/dev/null)"
if ! echo "$PERMS" | grep -q '"screen_recording":true'; then
  echo "[smoke] FATAL: Screen Recording NOT granted → System Settings ▸ Privacy & Security ▸ Screen Recording" >&2
  echo "        (enable the app running this script, then RESTART it). probe=$PERMS" >&2; exit 5; fi
if ! echo "$PERMS" | grep -q '"accessibility":true'; then
  echo "[smoke] FATAL: Accessibility NOT granted → System Settings ▸ Privacy & Security ▸ Accessibility" >&2
  echo "        (enable the app running this script, then RESTART it). probe=$PERMS" >&2; exit 5; fi
echo "[smoke] permissions OK: $PERMS"

# --- #1456 owner-active guard: never hijack the Mac while the owner is working (before we spend
# anything on seeding/booting the player). FORCE_PLAYER_QA=1 overrides; exit 75 == deferred. -------
owner_active_guard || exit $?

# --- #1443 force-recompile discipline (see ui_playtest_player.sh for the full rationale) ---------
rm -f "$PLAYERDIR/native_input"

APP="$(find_player_app)" || { echo "[smoke] WorldOSPlayer.app not found (set WORLDOS_PLAYER_APP)." >&2; exit 2; }
PLAYER_BIN="$APP/Contents/MacOS/WorldOSPlayer"
[ -x "$PLAYER_BIN" ] || { echo "[smoke] player binary not executable: $PLAYER_BIN" >&2; exit 2; }

PORT="$(pick_port)" || { echo "[smoke] no free port in 8990-8999 — aborting" >&2; exit 3; }
BASE_URL="http://127.0.0.1:$PORT"

# --- seed the DETERMINISTIC (sandbox + force_hit) camp fixture (#1443; see the seed's docstring) --
echo "[smoke] seeding $CID via qa/seed_gfx_camp_smoke.py…"
SEED_JSON="$(WORLDOS_STATE_DIR="$STATE_DIR" uv run --directory servers/engine python "$ROOT/qa/seed_gfx_camp_smoke.py" "$STATE_DIR" 2>"$RUNDIR/seed.err")" \
  || { echo "[smoke] seed failed (see $RUNDIR/seed.err)" >&2; cat "$RUNDIR/seed.err" >&2; exit 4; }
echo "[smoke] seeded: $SEED_JSON"

# --- compute a WALKABLE cell adjacent to the goblin (so the move lands the hero in melee range,
# then the attack click lands on-turn) — derived from the seed's OWN impassable set + cells, so a
# future fixture change can't silently break this into an out-of-range attack. ---------------------
TARGETS_JSON="$(python3 - "$SEED_JSON" <<'PY'
import json, sys
seed = json.loads(sys.argv[1])
hx, hy = seed["hero_cell"]; gx, gy = seed["goblin_cell"]
cols, rows = map(int, seed["grid"].split("x"))
blocked = {tuple(c) for c in seed["impassable"]}
blocked.add((gx, gy))
best, best_dist = None, None
for dx in (-1, 0, 1):
    for dy in (-1, 0, 1):
        if dx == 0 and dy == 0:
            continue
        c, r = gx + dx, gy + dy
        if not (0 <= c < cols and 0 <= r < rows):
            continue
        if (c, r) in blocked or (c, r) == (hx, hy):
            continue
        dist = max(abs(c - hx), abs(r - hy))  # chebyshev — matches the engine's default diagonal_mode
        if best is None or dist < best_dist:
            best, best_dist = (c, r), dist
if best is None:
    print(json.dumps({"ok": False, "reason": "no walkable cell adjacent to the goblin"}))
    sys.exit(1)
print(json.dumps({"ok": True, "walk_cell": list(best), "cols": cols, "rows": rows,
                   "hero_cell": [hx, hy], "goblin_cell": [gx, gy]}))
PY
)" || { echo "[smoke] FATAL: could not derive a walkable cell adjacent to the goblin: $TARGETS_JSON" >&2; exit 4; }
echo "[smoke] targets: $TARGETS_JSON"
WALK_C="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['walk_cell'][0])" "$TARGETS_JSON")"
WALK_R="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['walk_cell'][1])" "$TARGETS_JSON")"
COLS="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['cols'])" "$TARGETS_JSON")"
ROWS="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['rows'])" "$TARGETS_JSON")"
HERO_C="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['hero_cell'][0])" "$TARGETS_JSON")"
HERO_R="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['hero_cell'][1])" "$TARGETS_JSON")"
GOB_C="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['goblin_cell'][0])" "$TARGETS_JSON")"
GOB_R="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['goblin_cell'][1])" "$TARGETS_JSON")"
HERO_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['hero_id'])" "$SEED_JSON")"
GOBLIN_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['goblin_id'])" "$SEED_JSON")"

# --- start engine + viewer. WORLDOS_COMBAT_TEST=1 arms the double-guarded force_hit toggle (the
# OTHER half of the guard is Campaign.is_sandbox, set by the seed) for the grid-combat arbiter that
# runs IN-PROCESS inside this viewer. WORLDOS_PLAYER_MOVES must be a writable path even though a
# move_to_cell/attack never actually appends to it (they're engine-resolved in-process before the
# append path) — the do_POST /move gate refuses ALL moves up-front when it's unset. -----------------
WORLDOS_STATE_DIR="$STATE_DIR" WORLDOS_PLAYER_MOVES="$STATE_DIR/player_moves.jsonl" \
WORLDOS_COMBAT_TEST=1 \
  python3 viewer/server.py "" "$PORT" > "$RUNDIR/viewer.log" 2>&1 &
VIEWER=$!
PLAYER_APP_PID=""
cleanup() {
  kill "$VIEWER" 2>/dev/null
  [ -n "$PLAYER_APP_PID" ] && kill "$PLAYER_APP_PID" 2>/dev/null
  osascript -e 'quit app "WorldOSPlayer"' >/dev/null 2>&1 || true
  # #1466 FIX B: fallback if -logFile capture didn't land — gated on PLAYER_APP_PID so a pre-launch
  # exit (e.g. viewer never came up) never copies a stale prior-run Player.log into this run's dir.
  [ -n "$PLAYER_APP_PID" ] && copy_player_log_fallback "$PLAYERDIR" "$PLAYERDIR/unity_player.log"
}
trap cleanup EXIT INT TERM

ready=0
for _ in $(seq 1 40); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/combat-surface?campaign=$CID" 2>/dev/null)"
  [ "$code" = "200" ] && { ready=1; break; }
  sleep 0.5
done
[ "$ready" = "1" ] || { echo "[smoke] viewer never served /combat-surface at $BASE_URL (see $RUNDIR/viewer.log)" >&2; exit 4; }
echo "[smoke] viewer ready — /combat-surface serving $CID."

# --- #1456 launch WINDOWED, never fullscreen, never re-activate (no focus theft / no Space switch).
# ScreenCaptureKit captures across Spaces, so there is nothing to pin — we just quit any stale
# instance and spawn a fixed-size WINDOWED player in the background. ---------------------------------
osascript -e 'quit app "WorldOSPlayer"' >/dev/null 2>&1 || true
sleep 1
# #1466 FIX B: -logFile below redirects Unity's own stdout/stderr into unity_player.log, so
# player_app.log (the shell redirect just below) is now expected to be sparse/near-empty in the
# common case — it's kept as a belt-and-suspenders capture of anything emitted before Unity's
# logging takes over (e.g. dyld/early native errors). unity_player.log is the primary player log.
PLAYER_WIN_ARGS=(); while IFS= read -r __a; do PLAYER_WIN_ARGS+=("$__a"); done < <(player_windowed_launch_args "$PLAYERDIR/unity_player.log")
WORLDOS_ENGINE_BASE_URL="$BASE_URL" WORLDOS_CAMPAIGN_ID="$CID" "$PLAYER_BIN" "${PLAYER_WIN_ARGS[@]}" \
  > "$RUNDIR/player_app.log" 2>&1 &
PLAYER_APP_PID=$!
echo "[smoke] player app launched WINDOWED (pid $PLAYER_APP_PID) — engine=$BASE_URL campaign=$CID args=${PLAYER_WIN_ARGS[*]}"
# Let Unity open its window, fetch /combat-surface, and settle into the TACTICAL GRID camera
# framing (not whatever establishing/pre-combat view it opens on) before the scripted clicks —
# the click-target math below assumes the locked dimetric combat camera is on screen.
sleep 10

# --- BASELINE snapshot (before the scripted click sequence) ---------------------------------------
snapshot_field() {  # snapshot_field <python-expr-on-'snap'>
  python3 -c "
import json, sys
snap = json.load(open('$STATE_DIR/campaigns/$CID/snapshot.json'))
print($1)
" 2>/dev/null
}
BASE_HERO_XY="$(snapshot_field "next((c['x'],c['y']) for c in snap['combat']['order'] if c['character_id']=='$HERO_ID')")"
BASE_GOBLIN_HP="$(snapshot_field "snap['characters']['$GOBLIN_ID']['current_hp']")"
echo "[smoke] baseline: hero_xy=$BASE_HERO_XY goblin_hp=$BASE_GOBLIN_HP"

# --- the SCRIPTED sequence (no LLM) — same primitives the T3 palette exposes ----------------------
echo "[smoke] driving scripted sequence: move ($HERO_C,$HERO_R)->($WALK_C,$WALK_R), attack ($GOB_C,$GOB_R)…"
DRIVER_JSON="$(node "$NPT_DIR/player_smoke_driver.js" \
  --rundir "$RUNDIR" --owner "$OWNER" --cols "$COLS" --rows "$ROWS" \
  --hero-cell "$HERO_C,$HERO_R" --goblin-cell "$GOB_C,$GOB_R" --walk-cell "$WALK_C,$WALK_R" \
  ${WORLDOS_NPT_HELPER:+--helper "$WORLDOS_NPT_HELPER"} \
  ${WORLDOS_NPT_FULLSCREEN_FALLBACK:+--fullscreen-fallback} \
  2>"$RUNDIR/driver.err")"
DRIVER_RC=$?
echo "[smoke] driver rc=$DRIVER_RC: $DRIVER_JSON"

# Settle beat so the engine-resolved move/attack (already applied server-side the instant the
# client's click POSTed) is reflected in the just-written snapshot before we re-read it.
sleep 1

# --- FINAL snapshot + assertions --------------------------------------------------------------
FINAL_HERO_XY="$(snapshot_field "next((c['x'],c['y']) for c in snap['combat']['order'] if c['character_id']=='$HERO_ID')")"
FINAL_GOBLIN_HP="$(snapshot_field "snap['characters']['$GOBLIN_ID']['current_hp']")"
echo "[smoke] final: hero_xy=$FINAL_HERO_XY goblin_hp=$FINAL_GOBLIN_HP"

GLIDE_MOVE_DISTINCT="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('glide_move_distinct',0))" "$DRIVER_JSON" 2>/dev/null || echo 0)"
GLIDE_ATTACK_DISTINCT="$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('glide_attack_distinct',0))" "$DRIVER_JSON" 2>/dev/null || echo 0)"

PASS=1
REASONS=()
[ "$DRIVER_RC" = "0" ] || { PASS=0; REASONS+=("driver reported a click failure (rc=$DRIVER_RC)"); }
[ -n "$BASE_HERO_XY" ] && [ -n "$FINAL_HERO_XY" ] && [ "$BASE_HERO_XY" != "$FINAL_HERO_XY" ] \
  || { PASS=0; REASONS+=("hero cell did not change (base=$BASE_HERO_XY final=$FINAL_HERO_XY) — move click did not land"); }
[ -n "$BASE_GOBLIN_HP" ] && [ -n "$FINAL_GOBLIN_HP" ] && [ "$FINAL_GOBLIN_HP" -lt "$BASE_GOBLIN_HP" ] 2>/dev/null \
  || { PASS=0; REASONS+=("goblin HP did not drop (base=$BASE_GOBLIN_HP final=$FINAL_GOBLIN_HP) — attack click did not land"); }
[ "${GLIDE_MOVE_DISTINCT:-0}" -ge 2 ] 2>/dev/null \
  || { PASS=0; REASONS+=("move glide frames were not distinct (motion-liveness failed, distinct=$GLIDE_MOVE_DISTINCT)"); }
[ "${GLIDE_ATTACK_DISTINCT:-0}" -ge 2 ] 2>/dev/null \
  || { PASS=0; REASONS+=("attack glide frames were not distinct (motion-liveness failed, distinct=$GLIDE_ATTACK_DISTINCT)"); }

python3 - "$RUNDIR/smoke_result.json" "$PASS" "$BASE_HERO_XY" "$FINAL_HERO_XY" "$BASE_GOBLIN_HP" "$FINAL_GOBLIN_HP" \
  "$GLIDE_MOVE_DISTINCT" "$GLIDE_ATTACK_DISTINCT" "$DRIVER_RC" "$RUN" <<'PY'
import json, sys
out, passed, bhero, fhero, bhp, fhp, gmove, gattack, drc, run = sys.argv[1:11]
json.dump({
    "run": run, "pass": bool(int(passed)),
    "hero_xy": {"base": bhero, "final": fhero},
    "goblin_hp": {"base": bhp, "final": fhp},
    "glide_distinct": {"move": int(gmove or 0), "attack": int(gattack or 0)},
    "driver_rc": int(drc),
}, open(out, "w"), indent=2)
PY

echo "[smoke] driver output + snapshots: $RUNDIR"
if [ "$PASS" = "1" ]; then
  echo "[smoke] PASS — cell changed, goblin HP dropped, glide frames distinct."
  exit 0
else
  echo "[smoke] FAIL:"
  for r in "${REASONS[@]}"; do echo "  - $r"; done
  exit 1
fi
