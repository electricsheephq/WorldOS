#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
CANON=/Users/m1/WorldOS; OWNER_REPO=/Users/m1/worldos-owner
TARGET_APP="$HOME/Applications/WorldOSPlayer.app"
STATE="$HOME/Library/Application Support/WorldOS/owner_demo"
AGENTS="$HOME/Library/LaunchAgents"; SESSION=org.worldos.owner-session; PLAYER=org.worldos.owner-player
DM=org.worldos.owner-dm; DM_SCRIPT=qa/agent_play.sh
ENGINE_PORT=8776; QA_PORT=8981; BUILD_SHA=; BUILD_REPORT=; SHA=; STAGE=; RESEED=0; PURGE=0
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
  if [[ ! -f "$s" ]] || ! grep -q 'serve' "$s"; then die "$s has no 'serve' mode: the owner-dm agent would start nothing and every say/do/check would queue forever"; fi; }

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
  if [[ -r "$BUILD_REPORT" && -s "$BUILD_REPORT" ]]; then
    python3 "$ROOT/qa/owner_install_verify.py" build-report "$BUILD_REPORT" || die "build-report.txt beside $app does not record a successful build"
  else
    BUILD_REPORT=; [[ -n "$BUILD_SHA" ]] || die "no successful build-report.txt beside $app and no --build-sha"
  fi
  [[ -f "$ROOT/$DM_SCRIPT" ]] || echo "OWNER INSTALL NOTE: $ROOT/$DM_SCRIPT is absent; install will refuse until the DM loop lands." >&2
  echo "OWNER INSTALL PREFLIGHT GREEN (pins GREEN; KitRoom_=0; crypt+tavern FRESH; build identity ${BUILD_REPORT:+build-report result=Succeeded}${BUILD_REPORT:+ }${BUILD_REPORT:-via --build-sha $BUILD_SHA})"
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
  local domain tmp; domain="gui/$(id -u)"
  launchctl bootstrap "$domain" "$AGENTS/$SESSION.plist" 2>/dev/null || launchctl kickstart -k "$domain/$SESSION"
  await_code engine_code 200 90 "engine /session-surface never reached 200 on :$ENGINE_PORT"
  launchctl bootstrap "$domain" "$AGENTS/$PLAYER.plist" 2>/dev/null || true; launchctl kickstart -k "$domain/$PLAYER"
  # Unity binds :$QA_PORT only once the player is up; a single curl races normal startup.
  await_code player_code 200 60 "player QA listener never answered /debug on :$QA_PORT within 60s"
  launchctl bootstrap "$domain" "$AGENTS/$DM.plist" 2>/dev/null || true; launchctl kickstart -k "$domain/$DM"
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' RETURN
  curl -fsS --max-time 5 "http://127.0.0.1:$ENGINE_PORT/session-surface" >"$tmp/surface.json"
  curl -fsS --max-time 5 -X POST -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$QA_PORT/debug" >"$tmp/debug.json"
  python3 "$ROOT/qa/owner_install_verify.py" consumed --surface "$tmp/surface.json" --debug "$tmp/debug.json" \
    --manifest "$OWNER_REPO/extensions/renderers/unity/plates_manifest.json" || die "the installed player never applied the seeded campaign"
  cat "$tmp/debug.json"; echo
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
  WORLDOS_STATE_DIR="$STATE" uv run --directory "$OWNER_REPO/servers/engine" python "$OWNER_REPO/qa/seed_adventure_demo.py" "$STATE"
  mkdir -p "$AGENTS"; render "$RECEIPT" install "$TARGET_APP" "$WT" "$RECEIPT/install-ledger.json"
  for L in "$SESSION" "$PLAYER" "$DM"; do install -m 0644 "$RECEIPT/$L.plist" "$AGENTS/$L.plist"; done
  start_and_probe;;
restore)
  [[ -d "$APP" && -f "$APP/restore.json" ]] || die "restore needs a receipt dir holding restore.json"
  stop_agents
  [[ ! -d "$APP/WorldOSPlayer.app" ]] || { rm -rf "$TARGET_APP"; ditto "$APP/WorldOSPlayer.app" "$TARGET_APP"; }
  [[ ! -d "$APP/owner_demo" ]] || { rm -rf "$STATE"; ditto "$APP/owner_demo" "$STATE"; }
  PRIOR_WT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("prior_worktree_sha") or "")' "$APP/restore.json")
  [[ -z "$PRIOR_WT" ]] || git -C "$OWNER_REPO" checkout --quiet --detach "$PRIOR_WT"
  # Prefer the plists this receipt REPLACED; fall back to the ones it installed, so a
  # first-ever install still restores to a bootable set rather than to nothing.
  mkdir -p "$AGENTS"
  for L in "$SESSION" "$PLAYER" "$DM"; do
    if [[ -f "$APP/prior-$L.plist" ]]; then install -m 0644 "$APP/prior-$L.plist" "$AGENTS/$L.plist"
    elif [[ -f "$APP/$L.plist" ]]; then install -m 0644 "$APP/$L.plist" "$AGENTS/$L.plist"
    else die "receipt has no $L.plist to restore"; fi
  done
  start_and_probe; echo "RESTORED from $APP (worktree ${PRIOR_WT:-unchanged})";;
refresh)
  [[ -n "$SHA" ]] || die "refresh requires --sha"; stop_agents; WT=$(resolve_worktree "$SHA"); require_dm "$OWNER_REPO"; if ((RESEED)); then RECEIPT=$(receipt_dir); mkdir -p "$RECEIPT"; [[ ! -d "$STATE" ]] || ditto "$STATE" "$RECEIPT/owner_demo"; echo "Restore state: ditto '$RECEIPT/owner_demo' '$STATE'"; WORLDOS_STATE_DIR="$STATE" uv run --directory "$OWNER_REPO/servers/engine" python "$OWNER_REPO/qa/seed_adventure_demo.py" "$STATE"; fi; start_and_probe; echo "REFRESHED $WT";;
status) for L in "$SESSION" "$PLAYER" "$DM"; do launchctl print "gui/$(id -u)/$L" || true; done; echo "engine /session-surface $(engine_code)"; echo "player /debug $(player_code)";;
uninstall) stop_agents; rm -f "$AGENTS/$SESSION.plist" "$AGENTS/$PLAYER.plist" "$AGENTS/$DM.plist"; if ((PURGE)); then rm -rf "$TARGET_APP" "$STATE"; fi; echo "Uninstalled agents; app/state $([[ $PURGE == 1 ]] && echo purged || echo kept).";;
*) usage;; esac
