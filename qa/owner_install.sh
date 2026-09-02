#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
CANON=/Users/m1/WorldOS; OWNER_REPO=/Users/m1/worldos-owner
TARGET_APP="$HOME/Applications/WorldOSPlayer.app"
STATE="$HOME/Library/Application Support/WorldOS/owner_demo"
AGENTS="$HOME/Library/LaunchAgents"; SESSION=org.worldos.owner-session; PLAYER=org.worldos.owner-player
ENGINE_PORT=8776; QA_PORT=8981; BUILD_SHA=; BUILD_REPORT=; SHA=; STAGE=; RESEED=0; PURGE=0
die(){ printf 'OWNER INSTALL REFUSED: %s\n' "$*" >&2; exit 1; }
usage(){ echo "usage: $0 preflight|dry-run|install [app] [--stage dir] [--sha sha] [--build-sha sha] | refresh --sha sha [--reseed] | status | uninstall [--purge]"; exit 2; }

preflight(){
  local app=$1 out kit room
  [[ -d "$app" && -x "$app/Contents/MacOS/WorldOSPlayer" ]] || die "player app/binary missing: $app"
  [[ -f "$app/Contents/Resources/Data/level0" ]] || die "level0 missing"
  if ! out=$(python3 "$ROOT/qa/packaged_pins.py" "$app" --repo "$ROOT" 2>&1); then die "packaged pins RED/ERROR: $out"; fi
  [[ "$out" == *"PINS GREEN"* ]] || die "packaged pins did not report GREEN"
  if ! kit=$(strings "$app/Contents/Resources/Data/level0" | { grep -c 'KitRoom_' || [[ $? == 1 ]]; }); then die "level0 KitRoom_ scan ERROR"; fi
  [[ "$kit" == 0 ]] || die "level0 contains $kit KitRoom_ string(s)"
  for room in crypt tavern; do python3 "$ROOT/qa/room_pipeline.py" --room "$room" --check-cert || die "$room certification is not FRESH"; done
  BUILD_REPORT="$(dirname "$app")/build-report.txt"
  [[ -n "$BUILD_SHA" || ( -s "$BUILD_REPORT" && -r "$BUILD_REPORT" ) ]] || die "no readable build-report.txt and no --build-sha"
  [[ -s "$BUILD_REPORT" && -r "$BUILD_REPORT" ]] || BUILD_REPORT=
  echo "OWNER INSTALL PREFLIGHT GREEN (pins GREEN; KitRoom_=0; crypt+tavern FRESH; build identity recorded)"
}

render(){
  local out=$1 mode=$2 src=$3 wt=$4 ledger=$5 uv
  uv=$(command -v uv) || die "uv not found"
  [[ "$uv" == /* ]] || die "uv did not resolve to an absolute path"
  python3 "$ROOT/qa/owner_install_plists.py" --output "$out" --repo "$OWNER_REPO" --app "$TARGET_APP" --source-app "$src" --state "$STATE" --uv "$uv" --ledger "$ledger" --mode "$mode" --build-sha "$BUILD_SHA" --build-report "$BUILD_REPORT" --worktree-sha "$wt" --engine-port "$ENGINE_PORT" --qa-port "$QA_PORT"
}

stop_agents(){
  local domain engine qa i; domain="gui/$(id -u)"
  launchctl bootout "$domain/$PLAYER" >/dev/null 2>&1 || true; launchctl bootout "$domain/$SESSION" >/dev/null 2>&1 || true
  for ((i=0; i<20; i++)); do engine=$(curl -s -o /dev/null -w '%{http_code}' --max-time 1 "http://127.0.0.1:$ENGINE_PORT/health" || true); qa=$(curl -s -o /dev/null -w '%{http_code}' --max-time 1 -X POST -d '{}' "http://127.0.0.1:$QA_PORT/debug" || true); [[ "$engine" != 200 && "$qa" != 200 ]] && return; sleep .5; done
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

start_and_probe(){
  local domain code surface debug; domain="gui/$(id -u)"; code=
  launchctl bootstrap "$domain" "$AGENTS/$SESSION.plist"
  launchctl kickstart -k "$domain/$SESSION"
  for _ in {1..90}; do code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 1 "http://127.0.0.1:$ENGINE_PORT/health" || true); [[ "$code" == 200 ]] && break; sleep 1; done
  [[ "$code" == 200 ]] || die "engine /health never reached 200"
  launchctl bootstrap "$domain" "$AGENTS/$PLAYER.plist"
  launchctl kickstart -k "$domain/$PLAYER"
  surface=$(curl -fsS --max-time 5 "http://127.0.0.1:$ENGINE_PORT/combat-surface")
  python3 -c 'import json,sys; d=json.load(sys.stdin); n=(d.get("location") or {}).get("name"); assert d.get("campaign_id")=="adventure_demo_v1" and n; print("scene:",n)' <<<"$surface"
  debug=$(curl -fsS --max-time 5 -X POST -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$QA_PORT/debug")
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert "camOrtho" in d; print(json.dumps(d,sort_keys=True))' <<<"$debug"
}

MODE=${1:-}; [[ -n "$MODE" ]] || usage; shift
APP=
if [[ "$MODE" =~ ^(preflight|dry-run|install)$ ]]; then [[ $# -gt 0 ]] || usage; APP=$1; shift; fi
while (($#)); do case $1 in --stage) STAGE=${2:-}; shift 2;; --sha) SHA=${2:-}; shift 2;; --build-sha) BUILD_SHA=${2:-}; shift 2;; --reseed) RESEED=1; shift;; --purge) PURGE=1; shift;; *) usage;; esac; done

case $MODE in
preflight) preflight "$APP";;
dry-run) [[ -n "$STAGE" && "$STAGE" == /* ]] || die "dry-run requires absolute --stage"; preflight "$APP"; SHA=${SHA:-$(git -C "$ROOT" rev-parse origin/main)}; mkdir -p "$STAGE"; render "$STAGE" dry-run "$APP" "$SHA" "$STAGE/install-ledger.json"; echo "DRY RUN VERIFIED: $STAGE/install-ledger.json";;
install)
  preflight "$APP"; SHA=${SHA:-origin/main}; RECEIPT="/Users/m1/Codex/session-notes/$(date -u +%F)/worldos-refresh/artifacts/owner-install/backup-$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$RECEIPT"; RESTORE="'$ROOT/qa/owner_install.sh' uninstall --purge"
  if [[ -d "$TARGET_APP" ]]; then ditto "$TARGET_APP" "$RECEIPT/WorldOSPlayer.app"; RESTORE+="; ditto '$RECEIPT/WorldOSPlayer.app' '$TARGET_APP'"; fi
  if [[ -d "$STATE" ]]; then ditto "$STATE" "$RECEIPT/owner_demo"; RESTORE+="; ditto '$RECEIPT/owner_demo' '$STATE'"; fi
  if [[ -f "$AGENTS/$SESSION.plist" ]]; then ditto "$AGENTS/$SESSION.plist" "$RECEIPT/$SESSION.plist"; RESTORE+="; install -m 0644 '$RECEIPT/$SESSION.plist' '$AGENTS/$SESSION.plist'"; fi
  if [[ -f "$AGENTS/$PLAYER.plist" ]]; then ditto "$AGENTS/$PLAYER.plist" "$RECEIPT/$PLAYER.plist"; RESTORE+="; install -m 0644 '$RECEIPT/$PLAYER.plist' '$AGENTS/$PLAYER.plist'"; fi
  echo "Restore: $RESTORE"; stop_agents
  mkdir -p "$(dirname "$TARGET_APP")"; ditto "$APP" "$TARGET_APP"; xattr -dr com.apple.quarantine "$TARGET_APP"; codesign --force --deep --sign - "$TARGET_APP"; echo "Installed with ad-hoc signing; this demo build is unnotarized (accepted)."
  WT=$(resolve_worktree "$SHA"); WORLDOS_STATE_DIR="$STATE" uv run --directory "$OWNER_REPO/servers/engine" python "$OWNER_REPO/qa/seed_adventure_demo.py" "$STATE"
  mkdir -p "$AGENTS"; render "$RECEIPT" install "$TARGET_APP" "$WT" "$RECEIPT/install-ledger.json"; install -m 0644 "$RECEIPT/$SESSION.plist" "$AGENTS/$SESSION.plist"; install -m 0644 "$RECEIPT/$PLAYER.plist" "$AGENTS/$PLAYER.plist"; start_and_probe;;
refresh)
  [[ -n "$SHA" ]] || die "refresh requires --sha"; stop_agents; WT=$(resolve_worktree "$SHA"); if ((RESEED)); then RECEIPT="/Users/m1/Codex/session-notes/$(date -u +%F)/worldos-refresh/artifacts/owner-install/backup-$(date -u +%Y%m%dT%H%M%SZ)"; mkdir -p "$RECEIPT"; [[ ! -d "$STATE" ]] || ditto "$STATE" "$RECEIPT/owner_demo"; echo "Restore state: ditto '$RECEIPT/owner_demo' '$STATE'"; WORLDOS_STATE_DIR="$STATE" uv run --directory "$OWNER_REPO/servers/engine" python "$OWNER_REPO/qa/seed_adventure_demo.py" "$STATE"; fi; start_and_probe; echo "REFRESHED $WT";;
status) launchctl print "gui/$(id -u)/$SESSION" || true; launchctl print "gui/$(id -u)/$PLAYER" || true; curl -sS -o /dev/null -w "engine /health %{http_code}\n" --max-time 2 "http://127.0.0.1:$ENGINE_PORT/health" || true; curl -sS --max-time 2 -X POST -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$QA_PORT/debug" || true;;
uninstall) stop_agents; rm -f "$AGENTS/$SESSION.plist" "$AGENTS/$PLAYER.plist"; if ((PURGE)); then rm -rf "$TARGET_APP" "$STATE"; fi; echo "Uninstalled agents; app/state $([[ $PURGE == 1 ]] && echo purged || echo kept).";;
*) usage;; esac
