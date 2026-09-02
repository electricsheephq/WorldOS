#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
CANON=/Users/m1/WorldOS; OWNER_REPO=/Users/m1/worldos-owner
TARGET_APP="$HOME/Applications/WorldOSPlayer.app"
STATE="$HOME/Library/Application Support/WorldOS/owner_demo"
AGENTS="$HOME/Library/LaunchAgents"; SESSION=org.worldos.owner-session; PLAYER=org.worldos.owner-player
DM=org.worldos.owner-dm; DM_SCRIPT=qa/agent_play.sh
ENGINE_PORT=8776; QA_PORT=8981; BUILD_SHA=; BUILD_REPORT=; SHA=; STAGE=; RESEED=0; PURGE=0
DM_RUN="$STATE/agent_play_runs/owner"; DM_HEARTBEAT="$DM_RUN/serve.heartbeat"
DM_LOG="$STATE/owner-dm.log"; DM_ERR="$STATE/owner-dm.err.log"
START_PROBE_TMP=
die(){ printf 'OWNER INSTALL REFUSED: %s\n' "$*" >&2; exit 1; }
usage(){ echo "usage: $0 preflight|dry-run|install [app] [--stage dir] [--sha sha] [--build-sha sha] | refresh --sha sha [--reseed] | restore <receipt-dir> | status | uninstall [--purge]"; exit 2; }

# viewer/server.py has NO /health route — do_GET 404s anything it does not name — so
# readiness is the cheapest surface it DOES serve. /session-surface answers 200 with a
# JSON body (campaign_id "" until a campaign attaches), which is exactly "the engine is up".
engine_code(){ curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$ENGINE_PORT/session-surface" || true; }
player_code(){ curl -s -o /dev/null -w '%{http_code}' --max-time 2 -X POST -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$QA_PORT/debug" || true; }
await_code(){ local fn=$1 want=$2 secs=$3 what=$4 code='' i; for ((i=0; i<secs; i++)); do code=$("$fn"); [[ "$code" == "$want" ]] && return 0; sleep 1; done; die "$what (last HTTP '$code')"; }
# The two LaunchAgents serve pixels only; `say`/`do`/`check` are appended to the move sink
# for a DM to answer, so without qa/agent_play.sh serve the demo cannot be played at all.
require_dm(){ local s="$1/$DM_SCRIPT"
  if [[ ! -f "$s" ]] || ! grep -qE '(^|[^[:alnum:]_])serve([^[:alnum:]_]|$)' "$s"; then die "$s has no 'serve' mode: the owner-dm agent would start nothing and every say/do/check would queue forever"; fi; }

ledger_consumption(){
  local ledger=$1 result=$2 elapsed=$3 detail=$4
  [[ -n "$ledger" && -f "$ledger" ]] || return 0
  python3 "$ROOT/qa/owner_install_verify.py" ledger "$ledger" --result "$result" \
    --elapsed "$elapsed" --detail "$detail" || echo "OWNER INSTALL NOTE: could not update $ledger" >&2
}

reset_dm_run(){
  local state=$1 stamp run chat moved=0
  stamp=$(date -u +%Y%m%dT%H%M%SZ); run="$state/agent_play_runs/owner"; chat="$state/chat.jsonl"
  if [[ -e "$run" ]]; then
    [[ ! -e "$state/agent_play_runs/owner.archived-$stamp" ]] || die "DM archive timestamp collision: $stamp"
    mv "$run" "$state/agent_play_runs/owner.archived-$stamp"; moved=1
  fi
  if [[ -e "$chat" ]]; then
    [[ ! -e "$state/chat.archived-$stamp.jsonl" ]] || die "chat archive timestamp collision: $stamp"
    mv "$chat" "$state/chat.archived-$stamp.jsonl"; moved=1
  fi
  ((moved == 0)) || echo "Archived prior DM run/chat at timestamp $stamp; reseed will start fresh."
}

await_dm(){
  local secs=$1 i log
  for ((i=0; i<secs; i++)); do [[ -f "$DM_HEARTBEAT" ]] && return 0; sleep 1; done
  echo "OWNER INSTALL DM LOG TAIL (heartbeat missing):" >&2
  for log in "$DM_LOG" "$DM_ERR"; do
    [[ ! -f "$log" ]] || { echo "--- ${log##*/} ---" >&2; tail -n 20 "$log" >&2; }
  done
  die "DM never reached serving state (no $DM_HEARTBEAT within ${secs}s)"
}

await_consumed(){
  local surface=$1 debug=$2 secs=$3 ledger=${4:-} i msg detail
  curl -fsS --max-time 5 "http://127.0.0.1:$ENGINE_PORT/session-surface" >"$surface"
  for ((i=0; i<=secs; i++)); do
    curl -fsS --max-time 5 -X POST -H 'Content-Type: application/json' -d '{}' \
      "http://127.0.0.1:$QA_PORT/debug" >"$debug" 2>/dev/null || printf '{}\n' >"$debug"
    if python3 "$ROOT/qa/owner_install_verify.py" player-ready "$debug" >/dev/null 2>&1; then
      if msg=$(python3 "$ROOT/qa/owner_install_verify.py" consumed --surface "$surface" --debug "$debug" \
          --manifest "$OWNER_REPO/extensions/renderers/unity/plates_manifest.json" 2>&1); then
        ledger_consumption "$ledger" GREEN "$i" "$msg"
        printf '%s (elapsed=%ss)\n' "$msg" "$i"; cat "$debug"; echo; return 0
      fi
      ledger_consumption "$ledger" REFUSED "$i" "$msg"; die "$msg"
    fi
    ((i == secs)) || sleep 1
  done
  detail=$(cat "$debug"); ledger_consumption "$ledger" REFUSED "$secs" "last /debug: $detail"
  echo "OWNER INSTALL last /debug after ${secs}s: $detail" >&2
  die "player never reached surf>0 with plateLocMatch=true within ${secs}s"
}

preflight(){
  local app=$1 out kit room
  [[ -d "$app" && -x "$app/Contents/MacOS/WorldOSPlayer" ]] || die "player app/binary missing: $app"
  [[ -f "$app/Contents/Resources/Data/level0" ]] || die "level0 missing"
  if ! out=$(python3 "$ROOT/qa/packaged_pins.py" "$app" --repo "$ROOT" 2>&1); then die "packaged pins RED/ERROR: $out"; fi
  [[ "$out" == *"PINS GREEN"* ]] || die "packaged pins did not report GREEN"
  if ! kit=$(strings "$app/Contents/Resources/Data/level0" | { grep -c 'KitRoom_' || [[ $? == 1 ]]; }); then die "level0 KitRoom_ scan ERROR"; fi
  [[ "$kit" == 0 ]] || die "level0 contains $kit KitRoom_ string(s)"
  for room in crypt tavern; do python3 "$ROOT/qa/room_pipeline.py" --room "$room" --check-cert || die "$room certification is not FRESH"; done
  # Unity's StampFailedReport writes a nonempty result=Failed report beside a stale .app,
  # so "readable and nonempty" is not build identity — require the stamped success.
  BUILD_REPORT="$(dirname "$app")/build-report.txt"
  [[ -r "$BUILD_REPORT" && -s "$BUILD_REPORT" ]] || die "build-report.txt is required beside $app (a --build-sha cannot prove the built shaders)"
  python3 "$ROOT/qa/owner_install_verify.py" build-report "$BUILD_REPORT" || die "build-report.txt beside $app does not record a successful build with both required shaders"
  [[ -f "$ROOT/$DM_SCRIPT" ]] || echo "OWNER INSTALL NOTE: $ROOT/$DM_SCRIPT is absent; install will refuse until the DM loop lands." >&2
  echo "OWNER INSTALL PREFLIGHT GREEN (pins GREEN; KitRoom_=0; crypt+tavern FRESH; build identity build-report result=Succeeded $BUILD_REPORT)"
}

receipt_dir(){ echo "/Users/m1/Codex/session-notes/$(date -u +%F)/worldos-refresh/artifacts/owner-install/backup-$(date -u +%Y%m%dT%H%M%SZ)"; }

render(){
  local out=$1 mode=$2 src=$3 wt=$4 ledger=$5 uv
  uv=$(command -v uv) || die "uv not found"
  [[ "$uv" == /* ]] || die "uv did not resolve to an absolute path"
  python3 "$ROOT/qa/owner_install_plists.py" --output "$out" --repo "$OWNER_REPO" --app "$TARGET_APP" --source-app "$src" --state "$STATE" --uv "$uv" --ledger "$ledger" --mode "$mode" --build-sha "$BUILD_SHA" --build-report "$BUILD_REPORT" --worktree-sha "$wt" --engine-port "$ENGINE_PORT" --qa-port "$QA_PORT"
}

# Never trust the bootout echo: poll until BOTH listeners are actually gone, because a
# viewer still accepting /move can interleave a move with the reseed or land one after
# the backup was taken. Every caller runs this BEFORE touching the app or the state.
stop_agents(){
  local domain label i; domain="gui/$(id -u)"
  for label in "$DM" "$PLAYER" "$SESSION"; do launchctl bootout "$domain/$label" >/dev/null 2>&1 || true; done
  for ((i=0; i<20; i++)); do
    [[ "$(engine_code)" != 200 && "$(player_code)" != 200 ]] && return 0
    sleep .5
  done
  die "engine/player still answers after bootout"
}

resolve_worktree(){
  local wanted=$1 resolved old
  git -C "$CANON" fetch origin; resolved=$(git -C "$CANON" rev-parse "${wanted}^{commit}")
  if [[ ! -d "$OWNER_REPO/.git" && ! -f "$OWNER_REPO/.git" ]]; then git -C "$CANON" worktree add --detach "$OWNER_REPO" "$resolved" >/dev/null
  else
    [[ -z "$(git -C "$OWNER_REPO" status --porcelain)" ]] || die "$OWNER_REPO is dirty"
    old=$(git -C "$OWNER_REPO" rev-parse HEAD); git -C "$CANON" merge-base --is-ancestor "$old" "$resolved" || die "refresh is not fast-forward safe"
    git -C "$OWNER_REPO" checkout --quiet --detach "$resolved"
  fi
  git -C "$OWNER_REPO" rev-parse HEAD
}

# Session FIRST, then the player, then the DM — only the session plist sets RunAtLoad, so
# `bootstrap` starts exactly one process and nothing races the engine (#1612's two-kick trap).
start_and_probe(){
  local domain tmp ledger=${1:-}; domain="gui/$(id -u)"
  tmp=$(mktemp -d); START_PROBE_TMP=$tmp; trap 'rm -rf "$START_PROBE_TMP"' EXIT
  launchctl bootstrap "$domain" "$AGENTS/$SESSION.plist" 2>/dev/null || launchctl kickstart -k "$domain/$SESSION"
  await_code engine_code 200 90 "engine /session-surface never reached 200 on :$ENGINE_PORT"
  launchctl bootstrap "$domain" "$AGENTS/$PLAYER.plist" 2>/dev/null || true; launchctl kickstart -k "$domain/$PLAYER"
  # Unity binds :$QA_PORT only once the player is up; a single curl races normal startup.
  await_code player_code 200 60 "player QA listener never answered /debug on :$QA_PORT within 60s"
  rm -f "$DM_HEARTBEAT"                    # a prior serve must not satisfy this start
  launchctl bootstrap "$domain" "$AGENTS/$DM.plist" 2>/dev/null || true; launchctl kickstart -k "$domain/$DM"
  await_dm 60
  await_consumed "$tmp/surface.json" "$tmp/debug.json" 180 "$ledger"
  trap - EXIT; rm -rf "$tmp"; START_PROBE_TMP=
}

MODE=${1:-}; [[ -n "$MODE" ]] || usage; shift
APP=
if [[ "$MODE" =~ ^(preflight|dry-run|install|restore)$ ]]; then [[ $# -gt 0 ]] || usage; APP=$1; shift; fi
while (($#)); do case $1 in --stage) STAGE=${2:-}; shift 2;; --sha) SHA=${2:-}; shift 2;; --build-sha) BUILD_SHA=${2:-}; shift 2;; --reseed) RESEED=1; shift;; --purge) PURGE=1; shift;; *) usage;; esac; done

case $MODE in
preflight) preflight "$APP";;
dry-run) [[ -n "$STAGE" && "$STAGE" == /* ]] || die "dry-run requires absolute --stage"; preflight "$APP"; SHA=${SHA:-$(git -C "$ROOT" rev-parse origin/main)}; mkdir -p "$STAGE"; render "$STAGE" dry-run "$APP" "$SHA" "$STAGE/install-ledger.json"; echo "DRY RUN VERIFIED: $STAGE/install-ledger.json";;
install)
  preflight "$APP"; SHA=${SHA:-origin/main}; RECEIPT=$(receipt_dir); mkdir -p "$RECEIPT"
  # Stop FIRST: a live viewer must not accept a move while the app is replaced, the state
  # reseeded, or the backup taken. Only then snapshot app + state + ALL THREE plists +
  # the worktree sha, so `restore` can hand back a RUNNING installation, not just files.
  stop_agents
  PRIOR_WT=; [[ ! -d "$OWNER_REPO/.git" && ! -f "$OWNER_REPO/.git" ]] || PRIOR_WT=$(git -C "$OWNER_REPO" rev-parse HEAD)
  [[ ! -d "$TARGET_APP" ]] || ditto "$TARGET_APP" "$RECEIPT/WorldOSPlayer.app"
  [[ ! -d "$STATE" ]] || ditto "$STATE" "$RECEIPT/owner_demo"
  for L in "$SESSION" "$PLAYER" "$DM"; do [[ ! -f "$AGENTS/$L.plist" ]] || ditto "$AGENTS/$L.plist" "$RECEIPT/prior-$L.plist"; done
  python3 -c 'import json,sys; json.dump({"prior_worktree_sha": sys.argv[1] or None, "app": sys.argv[2], "state": sys.argv[3]}, open(sys.argv[4], "w"), indent=2)' "$PRIOR_WT" "$TARGET_APP" "$STATE" "$RECEIPT/restore.json"
  echo "Rollback (one line, yields a running installation): '$ROOT/qa/owner_install.sh' restore '$RECEIPT'"
  WT=$(resolve_worktree "$SHA"); require_dm "$OWNER_REPO"
  mkdir -p "$(dirname "$TARGET_APP")"; ditto "$APP" "$TARGET_APP"; xattr -dr com.apple.quarantine "$TARGET_APP"; codesign --force --deep --sign - "$TARGET_APP"; echo "Installed with ad-hoc signing; this demo build is unnotarized (accepted)."
  reset_dm_run "$STATE"
  WORLDOS_STATE_DIR="$STATE" uv run --directory "$OWNER_REPO/servers/engine" python "$OWNER_REPO/qa/seed_adventure_demo.py" "$STATE"
  mkdir -p "$AGENTS"; render "$RECEIPT" install "$TARGET_APP" "$WT" "$RECEIPT/install-ledger.json"
  for L in "$SESSION" "$PLAYER" "$DM"; do install -m 0644 "$RECEIPT/$L.plist" "$AGENTS/$L.plist"; done
  start_and_probe "$RECEIPT/install-ledger.json";;
restore)
  RECEIPT=$APP  # `restore` takes the receipt dir in the positional slot, not an app
  [[ -d "$RECEIPT" && -f "$RECEIPT/restore.json" ]] || die "restore needs a receipt dir holding restore.json"
  stop_agents
  [[ ! -d "$RECEIPT/WorldOSPlayer.app" ]] || { rm -rf "$TARGET_APP"; ditto "$RECEIPT/WorldOSPlayer.app" "$TARGET_APP"; }
  [[ ! -d "$RECEIPT/owner_demo" ]] || { rm -rf "$STATE"; ditto "$RECEIPT/owner_demo" "$STATE"; }
  PRIOR_WT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("prior_worktree_sha") or "")' "$RECEIPT/restore.json")
  if [[ -n "$PRIOR_WT" ]]; then
    [[ -e "$OWNER_REPO/.git" ]] || git -C "$CANON" worktree add --detach "$OWNER_REPO" "$PRIOR_WT" >/dev/null
    git -C "$OWNER_REPO" checkout --quiet --detach "$PRIOR_WT"
  fi
  # Prefer the plists this receipt REPLACED; fall back to the ones it installed, so a
  # first-ever install still restores to a bootable set rather than to nothing.
  mkdir -p "$AGENTS"
  for L in "$SESSION" "$PLAYER" "$DM"; do
    if [[ -f "$RECEIPT/prior-$L.plist" ]]; then install -m 0644 "$RECEIPT/prior-$L.plist" "$AGENTS/$L.plist"
    elif [[ -f "$RECEIPT/$L.plist" ]]; then install -m 0644 "$RECEIPT/$L.plist" "$AGENTS/$L.plist"
    else die "receipt has no $L.plist to restore"; fi
  done
  start_and_probe; echo "RESTORED from $RECEIPT (worktree ${PRIOR_WT:-unchanged})";;
refresh)
  [[ -n "$SHA" ]] || die "refresh requires --sha"; stop_agents; WT=$(resolve_worktree "$SHA"); require_dm "$OWNER_REPO"; if ((RESEED)); then RECEIPT=$(receipt_dir); mkdir -p "$RECEIPT"; [[ ! -d "$STATE" ]] || ditto "$STATE" "$RECEIPT/owner_demo"; echo "Restore state: ditto '$RECEIPT/owner_demo' '$STATE'"; reset_dm_run "$STATE"; WORLDOS_STATE_DIR="$STATE" uv run --directory "$OWNER_REPO/servers/engine" python "$OWNER_REPO/qa/seed_adventure_demo.py" "$STATE"; fi; start_and_probe; echo "REFRESHED $WT";;
status) for L in "$SESSION" "$PLAYER" "$DM"; do launchctl print "gui/$(id -u)/$L" || true; done; echo "engine /session-surface $(engine_code)"; echo "player /debug $(player_code)"; echo "dm heartbeat $([[ -f "$DM_HEARTBEAT" ]] && echo PRESENT || echo MISSING) ($DM_HEARTBEAT)";;
uninstall) stop_agents; rm -f "$AGENTS/$SESSION.plist" "$AGENTS/$PLAYER.plist" "$AGENTS/$DM.plist"; if ((PURGE)); then rm -rf "$TARGET_APP" "$STATE"; fi; echo "Uninstalled agents; app/state $([[ $PURGE == 1 ]] && echo purged || echo kept).";;
*) usage;; esac
